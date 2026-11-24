//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the agent tests share: a channel on which every message reaches every agent, the documents the tests start from, and small
//! readers of what an agent sends.
#![allow(dead_code)]

use std::collections::HashSet;

use indexmap::IndexMap;
use ioc_concord::agent::{Agent, Step};
use ioc_concord::tasks::Task;
use ioc_concord::wire::{build_init, parse_message, peek, Body, CancelBody, ErrorBody, InitParams, Kind, Message, ResendBody, ScoreIn};
use ioc_concord::xmlutil::with_check;
use serde_json::{json, Value};

pub use super::common::{uuids, SCell, RUN};

pub const OTHER: &str = "22222222-2222-4222-8222-222222222222";
pub const U44: &str = "44444444-4444-4444-8444-444444444444";

pub fn spec() -> Value {
    json!({"type": "string", "description": "a plan"})
}

/// `{"timeout": 10, "retries": 2}`
pub fn params() -> InitParams {
    InitParams::default().timeout(10).retries(2)
}

/// The init of `alice` that the tests start from; each setter changes one part of it.
pub struct InitDoc {
    participants: Vec<String>,
    run_id: String,
    params: Option<InitParams>,
    from: String,
    uid: String,
}

pub fn init_doc() -> String {
    InitDoc::new().build()
}

impl InitDoc {
    pub fn new() -> Self {
        InitDoc {
            participants: vec!["cost".into(), "security".into()],
            run_id: RUN.into(),
            params: None,
            from: "alice".into(),
            uid: uuids("a0a0a0a0")(),
        }
    }

    pub fn names(mut self, names: &[&str]) -> Self {
        self.participants = names.iter().map(|n| n.to_string()).collect();
        self
    }

    pub fn run(mut self, run_id: &str) -> Self {
        self.run_id = run_id.into();
        self
    }

    pub fn params(mut self, params: InitParams) -> Self {
        self.params = Some(params);
        self
    }

    pub fn from(mut self, from: &str) -> Self {
        self.from = from.into();
        self
    }

    pub fn uid(mut self, uid: &str) -> Self {
        self.uid = uid.into();
        self
    }

    pub fn build(&self) -> String {
        build_init(
            &self.from,
            &self.participants,
            "pick one",
            "nothing",
            "all agree",
            &spec(),
            self.params.as_ref(),
            Some(&self.run_id),
            Some(&self.uid),
        )
        .expect("a valid init")
    }
}

/// `doc` with `old` replaced by `new` and its check computed again: a message that is intact but not what an honest author sends.
pub fn forge(doc: &str, old: &str, new: &str) -> String {
    assert!(doc.contains(old), "{old} is not in the document");
    let changed = doc.replace(old, new);
    let check = regex::Regex::new(r#" check="[^"]*""#).unwrap();
    with_check(&check.replace(&changed, "")).unwrap()
}

/// A channel on which every message reaches every agent, the sender included (a broadcast).
/// Whether the channel loses the text (first) on its way to the agent (second).
pub type Loss = Box<dyn Fn(&str, &str) -> bool>;

pub struct Net {
    /// The clock of every agent, moved by the test.
    pub now: SCell<f64>,
    pub agents: IndexMap<String, Agent>,
    pub log: Vec<String>,
    /// Whether the channel loses `text` on its way to the agent `name`.
    pub lose: Loss,
    /// Agents that hear nothing, say nothing and are never ticked.
    pub dead: HashSet<String>,
}

impl Net {
    pub fn new(names: &[&str]) -> Self {
        let now = SCell::new(0.0);
        let agents = names
            .iter()
            .enumerate()
            .map(|(i, n)| {
                let clock = now.clone();
                let agent = Agent::builder(n).uuid_fn(uuids(&format!("{:08x}", i + 1))).clock(move || clock.get()).build().expect("agent");
                (n.to_string(), agent)
            })
            .collect();
        Net { now, agents, log: Vec::new(), lose: Box::new(|_, _| false), dead: HashSet::new() }
    }

    /// Deliver what `step` sends to every agent, then what the agents send in answer, in the order it was sent.
    pub fn broadcast(&mut self, step: &Step) {
        self.deliver(step.send.clone());
    }

    pub fn deliver(&mut self, first: Vec<String>) {
        let mut queue: std::collections::VecDeque<String> = first.into();
        while let Some(text) = queue.pop_front() {
            self.log.push(text.clone());
            for (name, a) in self.agents.iter_mut() {
                if self.dead.contains(name) || (self.lose)(&text, name) {
                    continue;
                }
                queue.extend(a.receive(&text, None).expect("receive").send);
            }
        }
    }

    /// Start the run with the init `doc`.
    pub fn start(&mut self, doc: &str) {
        self.deliver(vec![doc.to_string()]);
    }

    pub fn answer_init(&mut self, name: &str) {
        let a = &mut self.agents[name];
        let Some(Task::InitReply(task)) = a.due().into_iter().find(|t| matches!(t, Task::InitReply(_))) else {
            panic!("no init reply owed")
        };
        let step = a.submit_init_reply(&task.run_id, 0, 100, &format!("plan of {name}"), "because", 80, "I like it", None).unwrap();
        self.broadcast(&step);
    }

    /// Rate every candidate the agent owes a score for; `raw` is `(candidate id, raw score)`.
    pub fn answer_score(&mut self, name: &str, raw: &[(&str, i32)]) {
        let a = &mut self.agents[name];
        let Some(Task::Score(task)) = a.due().into_iter().find(|t| matches!(t, Task::Score(_))) else { panic!("no score owed") };
        let scores: Vec<ScoreIn> = task
            .candidates
            .iter()
            .map(|c| {
                ScoreIn::new(&c.id, raw.iter().find(|(id, _)| *id == c.id).unwrap_or_else(|| panic!("no score for {}", c.id)).1, "my view")
            })
            .collect();
        let step = a.submit_score(&task.run_id, &scores, None).unwrap();
        self.broadcast(&step);
    }
}

pub fn happy_with(cost_on_security: i32, security_on_cost: i32) -> Net {
    let mut net = Net::new(&["cost", "security"]);
    net.start(&init_doc());
    net.answer_init("cost");
    net.answer_init("security");
    net.answer_score("cost", &[("seed-security", cost_on_security)]);
    net.answer_score("security", &[("seed-cost", security_on_cost)]);
    net
}

pub fn happy() -> Net {
    happy_with(70, 75)
}

// --- readers ----------------------------------------------------------------------

pub fn msg(text: &str) -> Message {
    parse_message(text, None).expect("a valid message")
}

pub fn kinds(step: &Step) -> Vec<Kind> {
    step.send.iter().map(|t| msg(t).kind()).collect()
}

pub fn names(step: &Step) -> Vec<&str> {
    step.events.iter().map(|e| e.event.as_str()).collect()
}

/// The `<concord-error>` messages in what `step` sends.
pub fn errors(step: &Step) -> Vec<(Message, ErrorBody)> {
    step.send.iter().filter(|t| peek(t).unwrap().tag == "concord-error").map(|t| error_body(msg(t))).collect()
}

/// The only message of `step`, an error.
pub fn error_of(step: &Step) -> (Message, ErrorBody) {
    assert_eq!(step.send.len(), 1, "one message: {:?}", step.send);
    error_body(msg(&step.send[0]))
}

pub fn error_body(m: Message) -> (Message, ErrorBody) {
    match m.body.clone() {
        Body::Error(e) => (m, e),
        other => panic!("not an error: {other:?}"),
    }
}

pub fn resend_body(m: &Message) -> &ResendBody {
    match &m.body {
        Body::Resend(r) => r,
        other => panic!("not a resend: {other:?}"),
    }
}

pub fn cancel_body(m: &Message) -> &CancelBody {
    match &m.body {
        Body::Cancel(c) => c,
        other => panic!("not a cancel: {other:?}"),
    }
}

/// The `from` of every message of the log of the given kind (`score`, `resend`, ...).
pub fn counts(net: &Net, kind: &str) -> Vec<String> {
    net.log.iter().map(|t| peek(t).unwrap()).filter(|p| p.tag == format!("concord-{kind}")).map(|p| p.attrs["from"].clone()).collect()
}

/// Every agent that is not dead ticks at `now`, and what it sends is broadcast.
pub fn tick_all(net: &mut Net, now: f64) {
    net.now.set(now);
    let who: Vec<String> = net.agents.keys().cloned().collect();
    for name in who {
        if net.dead.contains(&name) {
            continue;
        }
        let step = net.agents[&name].tick(Some(now)).expect("tick");
        net.broadcast(&step);
    }
}

/// Let time pass, every agent ticking, until nobody has a timer left.
pub fn settle(net: &mut Net, start: f64) -> f64 {
    let (every, mut now) = (10.0, start);
    for _ in 0..40 {
        if net.agents.iter().all(|(n, a)| net.dead.contains(n) || a.next_wakeup().is_none()) {
            return now;
        }
        now += every;
        tick_all(net, now);
    }
    panic!("the timers never end");
}
