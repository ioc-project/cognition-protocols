//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
#![cfg(feature = "cli")]
//! The `ioc-concord` command: a whole run through its arguments and its output, as an agent's tool would drive it.

use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};

use ioc_concord::cli::{run, Context, Output, StringInput};
use regex::Regex;

const REASONS: &str = "it holds because the facts of my role support it, and nothing known to me contradicts it";

struct Dir(PathBuf);

impl Dir {
    fn new(name: &str) -> Dir {
        let dir = std::env::temp_dir().join(format!("ioc-concord-cli-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Dir(dir)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn go(dir: &Path, role: &str, args: &[&str], stdin: &str) -> Output {
    let mut ctx = Context {
        cwd: dir.to_path_buf(),
        state_dir: Some(dir.join("state")),
        min_nudge: 0.0,
        input: Box::new(StringInput(stdin.to_string())),
    };
    let mut argv = vec![role.to_string()];
    argv.extend(args.iter().map(|a| a.to_string()));
    run(&argv, &mut ctx)
}

fn status_of(out: &Output) -> &str {
    out.stdout.lines().next().unwrap_or("").strip_prefix("STATUS: ").unwrap_or("")
}

/// What follows the status; for a `SEND` of the initiator, what follows its `--- to:` line.
fn body(out: &Output) -> String {
    let text = out.stdout.split_once('\n').map_or("", |(_, rest)| rest);
    if text.starts_with("NEXT:") {
        return text.splitn(3, '\n').nth(2).unwrap_or("").to_string();
    }
    text.to_string()
}

#[test]
fn an_error_is_a_status_with_what_to_do() {
    let dir = Dir::new("errors");
    let none = dir.0.join("none.json").display().to_string();
    let out = go(&dir.0, "initiator", &["--state", &none, "handle"], "");
    assert!(out.code == 2 && status_of(&out) == "ERROR" && out.stdout.contains("NEXT:"), "{}", out.stdout);
    let out = go(&dir.0, "receiver", &["handle"], "x");
    assert!(out.code == 2 && status_of(&out) == "ERROR" && out.stdout.contains("--me"), "{}", out.stdout);
    let out = go(&dir.0, "initiator", &["--state", &none, "mark-missing", "a", "seed-a"], "");
    assert!(out.code == 2 && out.stdout.contains("mark-missing"), "{}", out.stdout);
    let out = go(&dir.0, "nothing", &[], "");
    assert!(out.code == 2 && status_of(&out) == "ERROR", "{}", out.stdout);
}

#[test]
fn a_receiver_that_is_given_no_message_sends_nothing() {
    let dir = Dir::new("none");
    let out = go(&dir.0, "receiver", &["--me", "cost", "handle"], "hello");
    assert!(out.code == 0 && status_of(&out) == "NONE", "{}", out.stdout);
}

#[test]
fn help_is_printed_with_exit_code_zero() {
    let dir = Dir::new("help");
    for args in [&["initiator", "--help"][..], &["receiver", "--help"][..], &["--help"][..]] {
        let mut ctx = Context { cwd: dir.0.clone(), state_dir: None, min_nudge: 0.0, input: Box::new(StringInput(String::new())) };
        let out = run(&args.iter().map(|a| a.to_string()).collect::<Vec<_>>(), &mut ctx);
        assert_eq!(out.code, 0);
        assert!(out.stdout.contains("ioc-concord") || out.stdout.contains("usage"), "{}", out.stdout);
    }
}

/// What each agent answers, as a model would: its two anchors, its seed, its raw score for each candidate (by its text), and what it
/// proposes when it is asked for a repair, in turn.
#[derive(Default)]
struct Policy {
    anchors: HashMap<&'static str, (u32, u32)>,
    seeds: HashMap<&'static str, &'static str>,
    scores: HashMap<(&'static str, &'static str), u32>,
    repairs: HashMap<&'static str, VecDeque<&'static str>>,
}

impl Policy {
    fn new(anchors: &[(&'static str, u32, u32)], seeds: &[(&'static str, &'static str)]) -> Policy {
        Policy {
            anchors: anchors.iter().map(|(a, r, i)| (*a, (*r, *i))).collect(),
            seeds: seeds.iter().copied().collect(),
            ..Policy::default()
        }
    }

    fn score(mut self, agent: &'static str, candidate: &'static str, raw: u32) -> Policy {
        self.scores.insert((agent, candidate), raw);
        self
    }

    fn repair(mut self, agent: &'static str, candidates: &[&'static str]) -> Policy {
        self.repairs.insert(agent, candidates.iter().copied().collect());
        self
    }
}

/// A run of agents that answer by a policy, with the texts of the candidates by their ids as the replies named them.
struct Run {
    dir: Dir,
    state: String,
    policy: Policy,
    texts: HashMap<String, String>,
}

impl Run {
    fn open(name: &str, policy: Policy, init: &[&str]) -> Run {
        let dir = Dir::new(name);
        let state = dir.0.join("run.json").display().to_string();
        let mut args = vec!["--state", &state, "init"];
        args.extend_from_slice(init);
        let out = go(&dir.0, "initiator", &args, "");
        assert_eq!(out.code, 0, "{}", out.stdout);
        let state = Regex::new(r"(?m)^State: (.*)$").unwrap().captures(&out.stdout).unwrap()[1].to_string();
        Run { dir, state, policy, texts: HashMap::new() }
    }

    fn initiator(&self, args: &[&str], stdin: &str) -> Output {
        let mut argv = vec!["--state", self.state.as_str()];
        argv.extend_from_slice(args);
        go(&self.dir.0, "initiator", &argv, stdin)
    }

    fn remember(&mut self, text: &str) {
        let proposal = Regex::new(r#"<proposal id="([^"]+)">\s*<candidate>([^<]*)</candidate>"#).unwrap();
        for cap in proposal.captures_iter(text) {
            self.texts.insert(cap[1].to_string(), cap[2].to_string());
        }
    }

    /// The flags of an answer to this task.
    fn flags(&mut self, me: &'static str, task: &str) -> Vec<String> {
        let mut flags: Vec<String> = Vec::new();
        let mut proposed = None;
        if task.contains("--reject N --ideal N") {
            let (reject, ideal) = self.policy.anchors[me];
            flags.extend(["--reject".to_string(), reject.to_string(), "--ideal".to_string(), ideal.to_string()]);
            proposed = Some(self.policy.seeds[me]);
        } else if task.contains("--candidate") {
            proposed = self.policy.repairs.get_mut(me).and_then(VecDeque::pop_front);
        }
        if let Some(candidate) = proposed {
            flags.extend(["--candidate", candidate, "--rationale", REASONS].map(String::from));
        }
        for cap in Regex::new(r"--score (\S+) N").unwrap().captures_iter(task) {
            let id = cap[1].to_string();
            let text = self.texts.get(&id).map(String::as_str).or(proposed).unwrap_or("");
            let raw = self.policy.scores.get(&(me, text)).copied().unwrap_or(50);
            flags.extend(["--score".to_string(), id, raw.to_string(), REASONS.to_string()]);
        }
        flags
    }

    /// What the receiver of `me` prints when it is given `text` and then answers what it is asked, to the end.
    fn deliver(&mut self, me: &'static str, text: &str) -> Output {
        let mut out = go(&self.dir.0, "receiver", &["--me", me, "handle"], text);
        assert_eq!(out.code, 0, "{}", out.stdout);
        while status_of(&out) == "ASK" {
            let flags = self.flags(me, &out.stdout);
            let mut args = vec!["--me", me, "answer"];
            args.extend(flags.iter().map(String::as_str));
            out = go(&self.dir.0, "receiver", &args, "");
            assert_eq!(out.code, 0, "{}", out.stdout);
            assert!(matches!(status_of(&out), "SEND" | "ASK"), "{}", out.stdout);
        }
        out
    }

    /// Carry the messages between the initiator and the agents until none is left to send: what the initiator sends goes to every agent,
    /// what they answer goes back to it and, with `broadcast` (no relay), to the other agents too.
    fn run(&mut self, agents: &[&'static str], broadcast: bool) {
        let out = self.initiator(&["handle"], "");
        assert_eq!(status_of(&out), "SEND", "{}", out.stdout);
        let mut inbox: Vec<(&str, String)> = vec![("initiator", body(&out))];
        for _ in 0..40 {
            let mut replies: Vec<(&str, String)> = Vec::new();
            for me in agents {
                for (_, text) in inbox.iter().filter(|(from, _)| from != me) {
                    let out = self.deliver(me, text);
                    if status_of(&out) == "SEND" {
                        let reply = body(&out);
                        self.remember(&reply);
                        replies.push((me, reply));
                    }
                }
            }
            if replies.is_empty() {
                return;
            }
            let joined = replies.iter().map(|(_, r)| r.as_str()).collect::<Vec<_>>().join("\n");
            let out = self.initiator(&["handle"], &joined);
            assert_eq!(out.code, 0, "{}", out.stdout);
            inbox = if broadcast { replies } else { Vec::new() };
            if status_of(&out) == "SEND" {
                inbox.push(("initiator", body(&out)));
            }
            if inbox.is_empty() {
                return;
            }
        }
        panic!("the run did not end: {}", self.status());
    }

    fn status(&self) -> String {
        self.initiator(&["status"], "").stdout
    }
}

const ENUM: &str = r#"{"enum": ["X", "Y", "Z"]}"#;
const ANCHORS: [(&str, u32, u32); 2] = [("a", 10, 90), ("b", 20, 80)];

#[test]
fn a_candidate_that_clears_everyone_at_the_score_ends_the_run_with_success() {
    let policy =
        Policy::new(&ANCHORS, &[("a", "X"), ("b", "Y")]).score("a", "X", 90).score("a", "Y", 50).score("b", "X", 75).score("b", "Y", 70);
    let mut run = Run::open(
        "score",
        policy,
        &["--agents", "a,b", "--context", "pick one", "--x-reject", "none", "--x-ideal", "best", "--schema", ENUM],
    );
    run.run(&["a", "b"], false);
    let status = run.status();
    assert!(status.contains("SUCCESS"), "{status}");
    assert!(status.contains('X'), "{status}");
}

#[test]
fn the_least_satisfied_agent_repairs_and_the_run_ends_with_the_candidate_it_proposed() {
    let policy = Policy::new(&ANCHORS, &[("a", "X"), ("b", "Y")])
        .score("a", "X", 90)
        .score("a", "Y", 20)
        .score("a", "Z", 85)
        .score("b", "X", 25)
        .score("b", "Y", 80)
        .score("b", "Z", 75)
        .repair("a", &["Z"])
        .repair("b", &["Z"]);
    let mut run = Run::open(
        "repair",
        policy,
        &["--agents", "a,b", "--context", "pick one", "--x-reject", "none", "--x-ideal", "best", "--schema", ENUM],
    );
    run.run(&["a", "b"], false);
    let status = run.status();
    assert!(status.contains("SUCCESS") && status.contains('Z'), "{status}");
}

#[test]
fn a_run_whose_budget_is_spent_without_a_candidate_for_everyone_ends_with_best_effort() {
    let policy = Policy::new(&ANCHORS, &[("a", "X"), ("b", "Y")])
        .score("a", "X", 90)
        .score("a", "Y", 20)
        .score("a", "Z", 30)
        .score("b", "X", 25)
        .score("b", "Y", 80)
        .score("b", "Z", 30)
        .repair("a", &["Z", "Z"])
        .repair("b", &["Z", "Z"]);
    let mut run = Run::open(
        "best-effort",
        policy,
        &["--agents", "a,b", "--context", "pick one", "--x-reject", "none", "--x-ideal", "best", "--schema", ENUM, "--B", "2"],
    );
    run.run(&["a", "b"], false);
    let status = run.status();
    assert!(status.contains("BEST_EFFORT"), "{status}");
}

const THREE: [(&str, u32, u32); 3] = [("a", 10, 90), ("b", 20, 80), ("c", 0, 100)];

#[test]
fn three_agents_without_a_relay_end_with_the_candidate_that_clears_them_all() {
    let policy = Policy::new(&THREE, &[("a", "X"), ("b", "Y"), ("c", "Z")])
        .score("a", "X", 90)
        .score("a", "Y", 60)
        .score("a", "Z", 40)
        .score("b", "X", 70)
        .score("b", "Y", 80)
        .score("b", "Z", 50)
        .score("c", "X", 80)
        .score("c", "Y", 60)
        .score("c", "Z", 90);
    let mut run = Run::open(
        "three",
        policy,
        &["--no-relay", "--agents", "a,b,c", "--context", "pick one", "--x-reject", "none", "--x-ideal", "best", "--schema", ENUM],
    );
    run.run(&["a", "b", "c"], true);
    let status = run.status();
    assert!(status.contains("SUCCESS") && status.contains('X'), "{status}");
}

const BASE: [&str; 8] = ["--agents", "a,b", "--context", "c", "--x-reject", "r", "--x-ideal", "i"];

fn init_args(schema: &str) -> Vec<&str> {
    let mut args = vec!["init"];
    args.extend(BASE);
    args.extend(["--schema", schema]);
    args
}

fn open(name: &str, schema: &str) -> Run {
    Run::open(name, Policy::default(), &init_args(schema)[1..])
}

/// What the receiver of `a` prints when it answers the first task with this candidate and anchors, and rates its seed `50`.
fn first_answer(run: &Run, candidate: &str) -> Output {
    go(
        &run.dir.0,
        "receiver",
        &[
            "--me",
            "a",
            "answer",
            "--reject",
            "10",
            "--ideal",
            "90",
            "--candidate",
            candidate,
            "--rationale",
            REASONS,
            "--score",
            "seed-a",
            "50",
            REASONS,
        ],
        "",
    )
}

#[test]
fn an_open_format_with_a_pattern_is_enforced_and_a_run_ends_with_a_plan_that_fits_it() {
    let schema = r#"{"type": "string", "description": "a plan name", "pattern": "^plan-[a-z]+$", "maxLength": 20}"#;
    let policy = Policy::new(&ANCHORS, &[("a", "plan-alpha"), ("b", "plan-beta")])
        .score("a", "plan-alpha", 90)
        .score("a", "plan-beta", 60)
        .score("b", "plan-alpha", 70)
        .score("b", "plan-beta", 80);
    let mut run = Run::open("pattern", policy, &init_args(schema)[1..]);
    run.run(&["a", "b"], false);
    let status = run.status();
    assert!(status.contains("SUCCESS") && status.contains("plan-"), "{status}");
}

#[test]
fn a_seed_that_does_not_fit_the_schema_is_refused_with_why_and_asked_again() {
    let cases = [
        (r#"{"enum": ["X", "Y"]}"#, "Q", "exactly one of the listed options", "X"),
        (r#"{"type": "string", "description": "d", "pattern": "^ok-[0-9]+$"}"#, "nope", "must match the regular expression", "ok-12"),
        (r#"{"type": "string", "description": "d", "minLength": 3, "maxLength": 6}"#, "ab", "at least 3 characters", "abcd"),
        (r#"{"type": "string", "description": "d", "minLength": 3, "maxLength": 6}"#, "abcdefg", "at most 6 characters", "abcd"),
    ];
    for (schema, bad, reason, good) in cases {
        let run = open("schema", schema);
        let first = body(&run.initiator(&["handle"], ""));
        assert_eq!(status_of(&go(&run.dir.0, "receiver", &["--me", "a", "handle"], &first)), "ASK");
        let out = first_answer(&run, bad);
        assert!(out.code == 0 && status_of(&out) == "ASK", "{}", out.stdout);
        assert!(
            out.stdout.contains("refused:") && out.stdout.contains(reason) && out.stdout.contains("Nothing was sent"),
            "{}",
            out.stdout
        );
        let out = first_answer(&run, good);
        assert!(out.code == 0 && status_of(&out) == "SEND", "{}", out.stdout);
    }
}

#[test]
fn a_seed_refused_again_and_again_is_told_how_many_times() {
    let run = open("again", r#"{"enum": ["X", "Y"]}"#);
    let first = body(&run.initiator(&["handle"], ""));
    go(&run.dir.0, "receiver", &["--me", "a", "handle"], &first);
    first_answer(&run, "Q");
    let out = first_answer(&run, "R");
    assert!(out.stdout.contains("refused the same way 2 times"), "{}", out.stdout);
}

#[test]
fn an_answer_that_is_not_usable_is_refused_with_why_and_nothing_is_sent() {
    let run = open("answers", ENUM);
    let first = body(&run.initiator(&["handle"], ""));
    assert_eq!(status_of(&go(&run.dir.0, "receiver", &["--me", "a", "handle"], &first)), "ASK");
    let answer = |args: &[&str]| {
        let mut argv = vec!["--me", "a", "answer"];
        argv.extend_from_slice(args);
        go(&run.dir.0, "receiver", &argv, "")
    };
    let out = answer(&[]);
    assert!(status_of(&out) == "ASK" && out.stdout.contains("You gave no answer"), "{}", out.stdout);
    let out = answer(&["--reject", "10", "--ideal", "90"]);
    assert!(out.stdout.contains("refused:") && out.stdout.contains("--candidate is missing"), "{}", out.stdout);
    let complete = |reject: &str, ideal: &str, score: &str| {
        answer(&["--reject", reject, "--ideal", ideal, "--candidate", "X", "--rationale", REASONS, "--score", "seed-a", score, REASONS])
    };
    for (out, why) in [
        (complete("90", "10", "50"), "must be strictly greater than raw_reject"),
        (complete("50", "50", "50"), "must be strictly greater than raw_reject"),
        (complete("x", "90", "50"), "raw_reject must be a number from 0 to 100"),
        (complete("10", "90", "high"), "raw must be a number from 0 to 100"),
    ] {
        assert!(out.code == 0 && status_of(&out) == "ASK" && out.stdout.contains("refused:") && out.stdout.contains(why), "{}", out.stdout);
    }
    assert_eq!(status_of(&complete("10", "90", "50")), "SEND");
}

#[test]
fn an_init_that_cannot_be_used_is_an_error_that_says_why() {
    let dir = Dir::new("init-errors");
    for (schema, why) in [
        ("not json", "--schema is not JSON"),
        (r#"{"enum": ["X"]}"#, "at least 2 non-empty strings"),
        (r#"{"type": "string", "description": "d", "pattern": "("}"#, "not in the supported subset"),
        (r#"{"type": "string", "description": "d", "color": 1}"#, "keywords that are not enforced here: color"),
        (r#"{"type": "string", "description": "d", "minLength": 5, "maxLength": 2}"#, "greater than"),
    ] {
        let out = go(&dir.0, "initiator", &init_args(schema), "");
        assert!(out.code == 2 && status_of(&out) == "ERROR" && out.stdout.contains(why), "{schema}: {}", out.stdout);
    }
    let out = go(&dir.0, "initiator", &["init"], "");
    assert!(out.code == 2 && out.stdout.contains("--agents"), "{}", out.stdout);
    let out = go(&dir.0, "initiator", &["frobnicate"], "");
    assert!(out.code == 2 && out.stdout.contains("unrecognized subcommand"), "{}", out.stdout);
}

#[test]
fn what_is_not_a_concord_message_is_recorded_or_ignored_and_never_an_error() {
    let run = open("prose", ENUM);
    let first = body(&run.initiator(&["handle"], ""));
    let out = run.initiator(&["handle"], "this is not a concord reply");
    assert!(out.code == 0 && status_of(&out) == "NONE" && !out.stdout.contains("recorded"), "{}", out.stdout);
    let out = run.initiator(&["handle"], "<concord-init-reply from=\"a\"><anchors>");
    assert!(out.code == 0 && status_of(&out) == "NONE" && out.stdout.contains("recorded"), "{}", out.stdout);
    let out = go(&run.dir.0, "receiver", &["--me", "a", "handle"], "plain words, no concord document");
    assert!(out.code == 0 && status_of(&out) == "NONE" && out.stdout.contains("no CONCORD message"), "{}", out.stdout);
    let out = run.initiator(&["handle", "--resend"], "");
    assert!(status_of(&out) == "SEND" && out.stdout.contains("<concord-init"), "{}", out.stdout);
    assert!(first.contains("<concord-init"));
}

#[test]
fn the_receiver_takes_handle_for_granted_after_its_options_and_never_reads_the_input_as_a_file() {
    let dir = Dir::new("implicit-handle");
    for args in
        [&["--me", "a", "plain words"][..], &["--me", "a", "--from", "b"], &["--me", "due"], &["--me", "a", "--state-id", "x", "words"]]
    {
        let out = go(&dir.0, "receiver", args, "");
        assert!(out.code == 0 && status_of(&out) == "NONE", "{args:?}: {}", out.stdout);
    }
    let out = go(&dir.0, "receiver", &["--me", "a", "handle"], "@/nonexistent/concord-file");
    assert!(out.code == 0 && status_of(&out) == "NONE", "{}", out.stdout);
    let out = go(&dir.0, "receiver", &["--me", "a", "handle", "@/nonexistent/concord-file"], "");
    assert!(out.code == 2 && out.stdout.contains("cannot read"), "{}", out.stdout);
}

#[test]
fn a_watchdog_timeout_that_is_not_a_number_of_seconds_is_a_usage_error() {
    let run = open("bad-timeout", ENUM);
    for timeout in ["nan", "inf", "-1"] {
        let out = run.initiator(&["watchdog", "--timeout", timeout], "");
        assert!(out.code == 2 && status_of(&out) == "ERROR", "{timeout}: {}", out.stdout);
    }
}

#[test]
fn a_usage_error_names_the_help_and_does_not_repeat_the_word_error() {
    let dir = Dir::new("usage");
    let out = go(&dir.0, "initiator", &["frobnicate"], "");
    assert!(out.stdout.contains("usage error: unrecognized") && out.stdout.contains("--help"), "{}", out.stdout);
}

#[test]
fn two_ratings_of_the_same_candidate_are_refused() {
    let run = open("twice", ENUM);
    let first = body(&run.initiator(&["handle"], ""));
    let ask = go(&run.dir.0, "receiver", &["--me", "a", "handle"], &first);
    assert_eq!(status_of(&ask), "ASK", "{}", ask.stdout);
    let out = go(&run.dir.0, "receiver", &["--me", "a", "answer", "--score", "x", "1", "why", "--score", "x", "2", "why"], "");
    assert!(status_of(&out) == "ASK" && out.stdout.contains("given twice"), "{}", out.stdout);
}

#[test]
fn a_negative_number_is_a_value_and_is_refused_for_its_range_not_as_a_flag() {
    let run = open("negative", ENUM);
    let first = body(&run.initiator(&["handle"], ""));
    go(&run.dir.0, "receiver", &["--me", "a", "handle"], &first);
    let out = go(&run.dir.0, "receiver", &["--me", "a", "answer", "--reject", "-5", "--ideal", "150"], "");
    assert!(status_of(&out) == "ASK" && !out.stdout.contains("usage error"), "{}", out.stdout);
    let out = run.initiator(&["watchdog", "--timeout", "-1"], "");
    assert!(out.stdout.contains("--timeout must be"), "{}", out.stdout);
}

#[test]
fn the_errors_of_the_initiator_give_the_hints_of_the_initiator() {
    let dir = Dir::new("hints");
    let out = go(&dir.0, "initiator", &["--state", "none.json", "handle"], "");
    assert!(out.stdout.contains("NEXT:") && out.stdout.contains("state path `init` printed"), "{}", out.stdout);
    let out = go(&dir.0, "initiator", &["frobnicate"], "");
    assert!(out.stdout.contains("You need two commands only: `init` once"), "{}", out.stdout);
    let out = go(&dir.0, "receiver", &["--me", "a", "answer", "--bogus"], "");
    assert!(out.stdout.contains("`--me <you> handle`"), "{}", out.stdout);
}

#[test]
fn a_message_that_is_cut_or_of_another_run_is_said_to_be_not_used() {
    let run = open("cut", ENUM);
    let first = body(&run.initiator(&["handle"], ""));
    let cut = &first[..first.len() * 2 / 3];
    let out = go(&run.dir.0, "receiver", &["--me", "a", "handle"], cut);
    assert!(status_of(&out) == "NONE" && out.stdout.contains("nothing was recorded"), "{}", out.stdout);
    let out = run.initiator(&["handle"], "<concord-init-reply from=\"a\"><anchors>");
    assert!(out.stdout.contains("not recorded"), "{}", out.stdout);
}

#[test]
fn the_watchdog_asks_after_its_nudges_even_when_a_nudge_moves_the_timers() {
    let run = open("watchdog", ENUM);
    run.initiator(&["handle"], "");
    let out = run.initiator(&["watchdog", "--timeout", "0.2", "--max-nudges", "1"], "");
    assert_eq!(status_of(&out), "SEND", "{}", out.stdout);
    let out = run.initiator(&["watchdog", "--timeout", "0.2", "--max-nudges", "1"], "");
    assert_eq!(status_of(&out), "ASK", "{}", out.stdout);
}
