//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! A network that delays, duplicates and loses messages, in virtual time, with the models of the agents on it. Shared by the chaos
//! and the fuzz tests.
#![allow(dead_code)]

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap};

use indexmap::IndexMap;
use ioc_concord::agent::Agent;
use ioc_concord::state::RunStatus;
use ioc_concord::store::MemoryStore;
use ioc_concord::tasks::Task;
use ioc_concord::wire::{InitParams, ScoreIn};
use serde_json::json;

use crate::common::{number, Rng, SCell, SRef};

pub const RUN: &str = "11111111-1111-4111-8111-111111111111";
pub const RUN2: &str = "22222222-2222-4222-8222-222222222222";
pub const NAMES: [&str; 5] = ["cost", "security", "legal", "ops", "ux"];
pub const TIMEOUT: i64 = 30;
pub const RETRIES: i64 = 3;

/// A source of UUIDs that count, with 16 random bits after the prefix.
fn uuids(prefix: String, rng: SRef<Rng>) -> impl FnMut() -> String {
    let mut n = 0u64;
    move || {
        n += 1;
        let bits = rng.borrow_mut().next_u64() >> 48;
        format!("{prefix}-{bits:04x}-4000-8000-{n:012x}")
    }
}

struct Item {
    when: f64,
    n: u64,
    kind: &'static str,
    who: String,
    data: String,
    wake: f64,
}

impl PartialEq for Item {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
impl Eq for Item {}
impl PartialOrd for Item {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Item {
    /// The earliest first (a max-heap, so reversed).
    fn cmp(&self, other: &Self) -> Ordering {
        other.when.total_cmp(&self.when).then(other.n.cmp(&self.n))
    }
}

/// The knobs of the network.
#[derive(Clone)]
pub struct Opts {
    pub loss: f64,
    pub dup: f64,
    pub delay: f64,
    pub restart: f64,
    pub crash: BTreeMap<String, f64>,
    pub watcher: bool,
    pub timeout: i64,
    pub retries: i64,
    /// agent -> seconds it takes to answer a task, on top of the usual
    pub slow: BTreeMap<String, f64>,
    /// agents that hear, or speak, another version
    pub rewrite_in: Vec<String>,
    pub rewrite_out: Vec<String>,
}

impl Default for Opts {
    fn default() -> Self {
        Opts {
            loss: 0.0,
            dup: 0.0,
            delay: 5.0,
            restart: 0.0,
            crash: BTreeMap::new(),
            watcher: false,
            timeout: TIMEOUT,
            retries: RETRIES,
            slow: BTreeMap::new(),
            rewrite_in: Vec::new(),
            rewrite_out: Vec::new(),
        }
    }
}

/// The network, the clock and the models of one run.
pub struct Chaos {
    pub seed: u64,
    rng: SRef<Rng>,
    opts: Opts,
    /// agent -> the texts that reached it
    pub delivered: BTreeMap<String, Vec<String>>,
    pub now: SCell<f64>,
    queue: BinaryHeap<Item>,
    counter: u64,
    pub names: Vec<String>,
    stores: BTreeMap<String, MemoryStore>,
    pub agents: IndexMap<String, Agent>,
    /// (time, author, text)
    pub log: Vec<(f64, String, String)>,
    thinking: BTreeSet<String>,
    ticking: BTreeSet<(String, u64)>,
    pub initiator: String,
    pub budget: i64,
    pub retries: i64,
    params: InitParams,
    pub max_active: usize,
}

impl Chaos {
    pub fn new(seed: u64, n: usize, budget: i64, tau: &str, opts: Opts) -> Chaos {
        let rng = SRef::new(Rng::new(seed.wrapping_add(0x6e65_7400)));
        let now = SCell::new(0.0);
        let names: Vec<String> = NAMES[..n].iter().map(|s| s.to_string()).collect();
        let (mut stores, mut agents) = (BTreeMap::new(), IndexMap::new());
        let mut everyone = names.clone();
        if opts.watcher {
            everyone.push("alice".to_string());
        }
        for (i, a) in everyone.iter().enumerate() {
            let store = MemoryStore::new();
            let clock = now.clone();
            let agent = Agent::builder(a)
                .store(store.clone())
                .clock(move || clock.get())
                .uuid_fn(uuids(format!("{:08x}", i + 1), rng.clone()))
                .build()
                .expect("agent");
            stores.insert(a.clone(), store);
            agents.insert(a.clone(), agent);
        }
        let initiator = if opts.watcher { "alice".to_string() } else { names[0].clone() };
        let params = InitParams::default().satisfaction_threshold(tau).budget(budget).timeout(opts.timeout).retries(opts.retries);
        Chaos {
            seed,
            rng,
            retries: opts.retries,
            opts,
            delivered: BTreeMap::new(),
            now,
            queue: BinaryHeap::new(),
            counter: 0,
            names,
            stores,
            agents,
            log: Vec::new(),
            thinking: BTreeSet::new(),
            ticking: BTreeSet::new(),
            initiator,
            budget,
            params,
            max_active: 0,
        }
    }

    // --- the machinery ---
    fn uniform(&self, lo: f64, hi: f64) -> f64 {
        lo + (hi - lo) * self.rng.borrow_mut().unit()
    }

    fn chance(&self, p: f64) -> bool {
        self.rng.borrow_mut().unit() < p
    }

    fn at(&mut self, delay: f64, kind: &'static str, who: &str, data: &str) {
        self.counter += 1;
        self.queue.push(Item {
            when: self.now.get() + delay,
            n: self.counter,
            kind,
            who: who.to_string(),
            data: data.to_string(),
            wake: 0.0,
        });
    }

    pub fn alive(&self, who: &str) -> bool {
        self.opts.crash.get(who).copied().unwrap_or(f64::INFINITY) > self.now.get()
    }

    /// What a step of `who` sends goes on the network; its next tick and its next thought are scheduled.
    fn after(&mut self, who: &str, send: Vec<String>) {
        for text in send {
            let text =
                if self.opts.rewrite_out.iter().any(|a| a == who) { text.replace("version=\"0.3.0\"", "version=\"0.4.0\"") } else { text };
            self.log.push((self.now.get(), who.to_string(), text.clone()));
            let everyone: Vec<String> = self.agents.keys().cloned().collect();
            for to in everyone {
                let copies = 1 + usize::from(self.chance(self.opts.dup));
                for _ in 0..copies {
                    if !self.chance(self.opts.loss) {
                        let heard = if self.opts.rewrite_in.contains(&to) {
                            text.replace("version=\"0.3.0\"", "version=\"0.4.0\"")
                        } else {
                            text.clone()
                        };
                        let delay = self.uniform(0.0, self.opts.delay);
                        self.at(delay, "deliver", &to, &heard);
                    }
                }
            }
        }
        self.plan(who);
    }

    fn plan(&mut self, who: &str) {
        let (due, wake) = {
            let a = &self.agents[who];
            (!a.due().is_empty(), a.next_wakeup())
        };
        if due && !self.thinking.contains(who) {
            self.thinking.insert(who.to_string());
            let delay = self.uniform(0.0, 2.0) + self.opts.slow.get(who).copied().unwrap_or(0.0);
            self.at(delay, "think", who, "");
        }
        if let Some(wake) = wake {
            if self.ticking.insert((who.to_string(), wake.to_bits())) {
                self.counter += 1;
                self.queue.push(Item {
                    when: wake.max(self.now.get()),
                    n: self.counter,
                    kind: "tick",
                    who: who.to_string(),
                    data: String::new(),
                    wake,
                });
            }
        }
    }

    fn maybe_restart(&mut self, who: &str) {
        if self.chance(self.opts.restart) {
            let clock = self.now.clone();
            let prefix = format!("{:08x}", self.rng.borrow_mut().next_u64() >> 32);
            let agent = Agent::builder(who)
                .store(self.stores[who].clone())
                .clock(move || clock.get())
                .uuid_fn(uuids(prefix, self.rng.clone()))
                .build()
                .expect("a restart from the saved state");
            self.agents.insert(who.to_string(), agent);
        }
    }

    fn answer(&mut self, who: &str) {
        let seed = self.seed;
        let tasks = self.agents[who].due();
        for t in tasks {
            let a = self.agents.get_mut(who).unwrap();
            let rid = t.run_id().to_string();
            let step = match &t {
                Task::InitReply(_) => {
                    let (rej, idl) = (number(seed, &[who, "rej"], 0.0, 40.0), number(seed, &[who, "idl"], 60.0, 100.0));
                    a.submit_init_reply(
                        &rid,
                        rej,
                        idl,
                        &format!("plan of {who}"),
                        "because",
                        number(seed, &[who, "seed"], 0.0, 100.0),
                        "good",
                        None,
                    )
                }
                Task::Score(t) => {
                    let scores: Vec<ScoreIn> = t
                        .candidates
                        .iter()
                        .map(|c| {
                            let lo = if c.id.starts_with('r') { 45.0 } else { 0.0 };
                            ScoreIn::new(&c.id, number(seed, &[who, &c.id], lo, 100.0), "my view")
                        })
                        .collect();
                    a.submit_score(&rid, &scores, None)
                }
                Task::Repair(t) => a.submit_repair(
                    &rid,
                    &format!("fix {} by {who}", t.round),
                    "because",
                    number(seed, &[who, "repair", &t.round.to_string()], 60.0, 100.0),
                    "better",
                    None,
                ),
                other => panic!("a task of a kind the test does not know: {other:?}"),
            }
            .expect("the answer is valid");
            self.after(who, step.send);
        }
    }

    pub fn run(&mut self) -> &mut Self {
        self.run_with(None)
    }

    pub fn run_with(&mut self, initiators: Option<Vec<(String, String)>>) -> &mut Self {
        let initiators = initiators.unwrap_or_else(|| vec![(self.initiator.clone(), RUN.to_string())]);
        for (who, run_id) in initiators {
            let spec = json!({"type": "string", "description": "a plan"});
            let names = self.names.clone();
            let step = self
                .agents
                .get_mut(&who)
                .unwrap()
                .start(&names, "pick one", "nothing", "all agree", &spec, Some(&self.params), Some(&run_id), None)
                .expect("start");
            self.after(&who, step.send);
        }
        let mut steps = 0;
        while let Some(item) = self.queue.pop() {
            self.now.set(item.when);
            assert!(self.now.get() < 1e5, "the run does not end");
            steps += 1;
            assert!(steps < 200_000);
            let who = item.who.clone();
            if !self.alive(&who) {
                continue;
            }
            match item.kind {
                "deliver" => {
                    self.maybe_restart(&who);
                    self.delivered.entry(who.clone()).or_default().push(item.data.clone());
                    let step = self.agents.get_mut(&who).unwrap().receive(&item.data, None).expect("receive");
                    self.after(&who, step.send);
                }
                "tick" => {
                    self.ticking.remove(&(who.clone(), item.wake.to_bits()));
                    self.maybe_restart(&who);
                    let step = self.agents.get_mut(&who).unwrap().tick(None).expect("tick");
                    self.after(&who, step.send);
                }
                _ => {
                    self.thinking.remove(&who);
                    self.maybe_restart(&who);
                    self.answer(&who);
                }
            }
            let active = self.agents[&who].state.runs.values().filter(|r| r.status == RunStatus::Active).count();
            self.max_active = self.max_active.max(active);
            assert!(active <= 1, "{who} is in {active} runs at once");
            if steps % 50 == 0 {
                serde_json::to_string(&self.agents[&who].state).expect("the state is JSON");
            }
        }
        self
    }
}

/// The size of the network of the seed, its budget and its threshold.
pub fn setup(seed: u64) -> (usize, i64, &'static str) {
    (2 + (seed % 3) as usize, 1 + (seed % 4) as i64, ["0.5", "0.6", "0.8"][(seed % 3) as usize])
}
