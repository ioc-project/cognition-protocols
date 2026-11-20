//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The `ioc-concord` command (feature `cli`), one command at a time: what each prints, its exit code and what it keeps. Whole runs are
//! replayed from the transcripts of the skill scripts in `transcripts.rs`.
#![cfg(feature = "cli")]

use std::path::{Path, PathBuf};
use std::time::Instant;

use ioc_concord::agent::Agent;
use ioc_concord::cli::{run, Context, Output, StringInput};
use ioc_concord::store::FileStore;
use ioc_concord::wire::InitParams;
use serde_json::{json, Value};

/// A folder of its own for a test, removed when the test ends.
struct Dir(PathBuf);

impl Dir {
    fn new(name: &str) -> Dir {
        let path = std::env::temp_dir().join(format!("concord-cli-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        Dir(path)
    }

    /// `ioc-concord <args>`, in this folder, with `stdin` (None: the input is closed).
    fn call(&self, args: &[&str], stdin: Option<&str>) -> Output {
        let args: Vec<String> = args.iter().map(|a| (*a).to_string()).collect();
        let input = stdin.map_or_else(StringInput::closed, StringInput::new);
        run(&args, &mut Context::new(&self.0, input))
    }

    fn rec(&self, me: &str, command: &[&str], stdin: Option<&str>) -> Output {
        self.call(&[&["receiver", "--me", me], command].concat(), stdin)
    }

    fn ini(&self, me: &str, command: &[&str], stdin: Option<&str>) -> Output {
        self.call(&[&["initiator", "--me", me], command].concat(), stdin)
    }

    fn path(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

const REASONS: &str = "It meets my role criteria on cost, risk and delivery time, as stated in my briefing.";

fn spec() -> Value {
    json!({"type": "string", "description": "an option"})
}

/// The init of a run opened by `ini` for `participants`.
fn init_text(timeout: i64, retries: i64, participants: &[&str]) -> String {
    let params = InitParams { timeout: Some(timeout), retries: Some(retries), ..InitParams::default() };
    let mut ini = Agent::new("ini").unwrap();
    ini.start(participants, "pick one", "nothing is decided", "everyone agrees", &spec(), Some(&params), None, None).unwrap().send.remove(0)
}

/// The text of the first section `title` of an output.
fn section<'a>(out: &'a str, title: &str) -> &'a str {
    let start = out.find(&format!("--- {title}")).unwrap_or_else(|| panic!("no {title} in {out}"));
    let body = &out[start..];
    let body = &body[body.find('\n').unwrap() + 1..];
    let end = [body.find("\n--- "), body.find("\nNEXT:")].into_iter().flatten().min().unwrap_or(body.len());
    &body[..end]
}

fn answer(candidate: &str) -> String {
    json!({"raw_reject": 10, "raw_ideal": 90, "candidate": candidate, "rationale": REASONS, "raw": 85, "reasoning": REASONS}).to_string()
}

// --- the command line ------------------------------------------------------------------------------------------------------------------

#[test]
fn a_command_line_that_is_wrong_is_an_error_that_says_what_to_do() {
    let d = Dir::new("usage");
    let out = d.call(&["receiver", "nonsense"], None);
    assert_eq!(out.code, 2);
    assert_eq!(
        out.stdout,
        "STATUS: ERROR\nargument command: invalid choice: 'nonsense' (choose from 'receive', 'answer', 'tick', 'wait', 'task', 'status', \
'report')\nNEXT: Run `uvx ioc-concord receiver --help`.\n"
    );
    let out = d.call(&["initiator", "--me", "a", "start"], None);
    assert!(out.stdout.contains("the following arguments are required: --participants, --context, --x-reject, --x-ideal"));
    assert!(out.stdout.ends_with("NEXT: Run `uvx ioc-concord initiator start --help`.\n"));
    let out = d.call(&["initiator", "--me", "a", "status"], None);
    assert!(out.stdout.contains("invalid choice: 'status', maybe you meant 'start'?"), "{}", out.stdout);
    let out = d.call(&["nobody"], None);
    assert!(out.code == 2 && out.stdout.starts_with("STATUS: ERROR\n"));
    assert_eq!(d.call(&[], None).code, 0);
}

#[test]
fn the_help_says_how_to_use_each_command() {
    let d = Dir::new("help");
    let out = d.call(&["initiator", "--help"], None);
    assert_eq!(out.code, 0);
    assert!(out.stdout.starts_with("usage: uvx ioc-concord initiator [-h] --me ME") && out.stdout.contains("open the run"));
    let out = d.call(&["receiver", "-h"], None);
    assert!(out.code == 0 && out.stdout.contains("pass your answer"));
    let out = d.call(&["initiator", "--me", "x", "start", "--help"], None);
    let words = out.stdout.split_whitespace().collect::<Vec<_>>().join(" ");
    assert!(words.contains("--timeout SECONDS") && words.contains("(default 300)"));
    for (_, text) in ioc_concord::guidance::START_PARAMS {
        let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
        assert!(words.contains(&text), "the help of start has the wording of the library: {text}");
    }
    assert!(d.call(&["receiver", "--me", "x", "wait", "--help"], None).stdout.contains("--poll POLL"));
}

#[test]
fn an_agent_id_that_is_not_one_is_refused() {
    let d = Dir::new("me");
    let out = d.rec("not an id", &["report"], None);
    assert_eq!(out.code, 2);
    assert!(out.stdout.starts_with("STATUS: ERROR\n--me 'not an id' is not an agent id: 1 to 64 ASCII letters, digits, _ or -\n"));
}

// --- the commands both roles have ----------------------------------------------------------------------------------------------------

#[test]
fn a_command_without_a_run_says_how_one_starts() {
    let d = Dir::new("norun");
    for command in ["tick", "report", "wait"] {
        let out = d.rec("a", &[command], None);
        assert_eq!(out.code, 2);
        assert_eq!(
            out.stdout,
            "STATUS: ERROR\nthere is no run for a in concord-state-a.json\nNEXT: A run starts with the message that opens it: pass that \
message to `uvx ioc-concord receiver --me a receive`.\n"
        );
    }
}

#[test]
fn the_message_that_opens_the_run_gives_a_task_with_the_brief_once() {
    let d = Dir::new("brief");
    let out = d.rec("a", &["receive"], Some(&init_text(60, 3, &["a", "b"]))).stdout;
    assert!(out.starts_with("STATUS: ANSWER\n--- BRIEF\n"), "{out}");
    let at = |s: &str| out.find(s).unwrap();
    assert!(
        at("--- BRIEF") < at("--- READ")
            && at("--- READ") < at("--- TASK")
            && at("--- TASK") < at("--- WAKE")
            && at("--- WAKE") < at("\nNEXT:")
    );
    assert!(out.contains("ANSWER FORMAT: write one JSON object") && out.contains("`uvx ioc-concord receiver --me a answer`"));
    let again = d.rec("a", &["receive"], Some("not a concord message")).stdout;
    assert!(!again.contains("--- BRIEF") && again.contains("--- NOTE\nnot used: not a CONCORD message"));
    let side: Value = serde_json::from_str(&std::fs::read_to_string(d.path("concord-script-a.json")).unwrap()).unwrap();
    assert_eq!(side["briefed"].as_array().unwrap().len(), 1);
    assert!(d.path("concord-state-a.json").exists() && d.path("concord-state-a.json.lock").exists());
}

#[test]
fn standard_input_is_required_where_a_command_reads_it() {
    let d = Dir::new("stdin");
    let out = d.rec("a", &["receive"], Some("  \n"));
    assert!(out.code == 2 && out.stdout.contains("receive got an empty standard input"));
    let out = d.rec("a", &["receive"], None);
    assert_eq!(out.code, 2);
    assert!(out.stdout.starts_with("STATUS: ERROR\nreceive reads standard input and there is none\nNEXT: Pipe the text in"));
}

#[test]
fn the_report_of_a_run_that_is_not_over_says_so_and_has_a_timer() {
    let d = Dir::new("notover");
    d.rec("a", &["receive"], Some(&init_text(60, 3, &["a", "b"])));
    let out = d.rec("a", &["report"], None);
    assert!(out.code == 0 && out.stdout.starts_with("STATUS: WAIT\n--- NOT OVER\nThe run is not over"), "{}", out.stdout);
    assert!(out.stdout.contains("--- WAKE\n") && out.stdout.contains(" s)\n") && !out.stdout.contains("--- END"));
}

#[test]
fn wait_returns_what_the_tick_produced_and_then_says_that_the_run_is_over() {
    let d = Dir::new("wait");
    d.rec("a", &["receive"], Some(&init_text(1, 1, &["a", "b"])));
    d.rec("a", &["answer"], Some(&answer("X")));
    let started = Instant::now();
    let out = d.rec("a", &["wait", "--poll", "0.1"], None).stdout;
    assert!(started.elapsed().as_secs_f64() > 0.5);
    assert!(out.starts_with("STATUS: SEND\n--- SEND to: b, ini\n<concord-error ") && out.contains("reason=\"missing\""), "{out}");
    let out = d.rec("a", &["wait", "--poll", "0.1"], None).stdout; // after `retries` requests it gives up on b
    assert!(out.starts_with("STATUS: SEND") && out.contains("<concord-cancel ") && out.contains("--- END\nThe run was aborted"), "{out}");
    for _ in 0..2 {
        let out = d.rec("a", &["wait"], None);
        assert!(
            out.code == 0 && out.stdout.starts_with("STATUS: DONE\nNEXT: The run is over, so there is nothing to wait for: do not start")
        );
    }
    assert!(d.rec("a", &["report"], None).stdout.starts_with("STATUS: DONE\n--- END\nThe run was aborted"));
}

#[test]
fn wait_returns_at_once_when_no_timer_runs() {
    let d = Dir::new("notimer");
    let mut solo = Agent::builder("solo").store(FileStore::new(d.path("concord-state-solo.json"))).build().unwrap();
    let params = InitParams { timeout: Some(1000), ..InitParams::default() };
    let init = solo.start(&["x", "y"], "c", "w", "b", &spec(), Some(&params), None, None).unwrap().send.remove(0);
    let run_id = solo.state.active.clone().unwrap();
    for name in ["x", "y"] {
        let mut peer = Agent::new(name).unwrap();
        let _ = peer.receive(&init, None).unwrap();
        let reply = peer.submit_init_reply(&run_id, 10, 90, &format!("option {name}"), REASONS, 80, REASONS, None).unwrap().send.remove(0);
        let _ = solo.receive(&reply, None).unwrap();
    }
    let started = Instant::now();
    let out = d.ini("solo", &["wait"], None);
    assert!(started.elapsed().as_secs() < 2);
    assert!(out.code == 0 && out.stdout.starts_with("STATUS: WAIT\nNEXT: No timer runs") && !out.stdout.contains("--- WAKE"));
}

#[test]
fn a_state_file_that_is_not_json_is_a_fault_with_a_way_back() {
    let d = Dir::new("badstate");
    std::fs::write(d.path("concord-state-a.json"), "{not json").unwrap();
    let out = d.rec("a", &["receive"], Some(&init_text(60, 3, &["a", "b"])));
    assert_eq!(out.code, 2);
    assert_eq!(
        out.stdout,
        "STATUS: ERROR\nstate file concord-state-a.json is not valid JSON (Expecting property name enclosed in double quotes: line 1 \
column 2 (char 1))\nNEXT: Nothing was changed. Fix what the message says (the file or the argument it names) and run the same command \
again. If it keeps failing, stop and say why.\n"
    );
}

#[test]
fn the_state_dir_is_given_on_every_command_that_next_prints() {
    let d = Dir::new("statedir");
    let out = d.rec("a", &["--state-dir", "my state/", "receive"], Some(&init_text(60, 3, &["a", "b"]))).stdout;
    assert!(out.contains("`uvx ioc-concord receiver --me a --state-dir 'my state' answer`"), "{out}");
    assert!(d.path("my state/concord-state-a.json").exists());
    let out = d.rec("a", &["--state-dir", "./", "report"], None).stdout;
    assert!(out.contains("`uvx ioc-concord receiver --me a receive`") && out.contains("there is no run for a in concord-state-a.json"));
}

#[test]
fn messages_that_arrive_at_the_same_time_are_all_recorded() {
    let d = Dir::new("lock");
    let agents: Vec<String> = (0..6).map(|i| format!("a{i}")).collect();
    let names: Vec<&str> = agents.iter().map(String::as_str).collect();
    let start = d.ini(
        "ini",
        &[&["start", "--participants", &names.join(","), "--context", "c", "--x-reject", "r", "--x-ideal", "i"][..], &["--format", "f"]]
            .concat(),
        None,
    );
    let init = section(&start.stdout, "SEND to:").to_string();
    let mut replies = Vec::new();
    for (n, a) in names.iter().enumerate() {
        d.rec(a, &["receive"], Some(&init));
        let out = d.rec(a, &["answer"], Some(&answer(&format!("option {n}")))).stdout;
        replies.push(section(&out, "SEND to:").to_string());
    }
    std::thread::scope(|scope| {
        for reply in &replies {
            let d = &d;
            scope.spawn(move || assert_eq!(d.ini("ini", &["receive"], Some(reply)).code, 0));
        }
    });
    let state: Value = serde_json::from_str(&std::fs::read_to_string(d.path("concord-state-ini.json")).unwrap()).unwrap();
    let run = state["runs"].as_object().unwrap().values().next().unwrap();
    assert_eq!(run["engine"]["anchors"].as_object().unwrap().len(), agents.len());
}

// --- the initiator ---------------------------------------------------------------------------------------------------------------------

fn start(d: &Dir, me: &str, extra: &[&str], before: &[&str]) -> Output {
    let base = ["start", "--participants", "a,b", "--context", "c", "--x-reject", "r", "--x-ideal", "i"];
    d.call(&[&["initiator", "--me", me], before, &base[..], extra].concat(), None)
}

#[test]
fn start_needs_exactly_one_way_to_say_what_an_option_is() {
    let d = Dir::new("start");
    let out = start(&d, "ini", &[], &[]);
    assert!(out.code == 2 && out.stdout.contains("you gave none"));
    let out = start(&d, "ini", &["--option", "X", "--format", "f"], &[]);
    assert!(out.stdout.contains("you gave --option, --format"));
    let out = start(&d, "ini", &["--schema", "{not json"], &[]);
    assert!(out
        .stdout
        .contains("--schema is not valid JSON (Expecting property name enclosed in double quotes: line 1 column 2 (char 1))"));
    let out = start(&d, "ini", &["--format", "f", "--satisfaction-threshold", "2"], &[]);
    assert!(out.stdout.contains("the run is not valid: satisfaction_threshold must be above 0 and at most 1"));
    assert!(
        !d.path("concord-state-ini.json").exists()
            || !std::fs::read_to_string(d.path("concord-state-ini.json")).unwrap().contains("run_id")
    );
    let out = start(&d, "ini", &["--option", "@alice", "--option", "@bob"], &[]);
    assert!(out.code == 0 && out.stdout.contains(r#"{"enum": ["@alice", "@bob"]}"#), "{}", out.stdout);
    assert!(out.stdout.starts_with("STATUS: SEND\n--- SEND to: a, b\n<concord-init "), "{}", out.stdout);
    let out = start(&d, "ini", &["--format", "f"], &[]);
    assert!(out.code == 2 && out.stdout.contains("ini is in a run already"));
}

#[test]
fn a_text_argument_can_be_a_file() {
    let d = Dir::new("files");
    std::fs::write(d.path("ctx.txt"), "décision → 日本語\r\n").unwrap();
    let base = ["start", "--participants", "a,b", "--x-reject", "r", "--x-ideal", "i", "--format", "f", "--context"];
    let out = d.ini("i1", &[&base[..], &["@ctx.txt"]].concat(), None);
    assert!(out.code == 0 && out.stdout.contains("<context>décision → 日本語</context>"), "{}", out.stdout);
    std::fs::write(d.path("bad.txt"), b"d\xe9cision").unwrap();
    let out = d.ini("i2", &[&base[..], &["@bad.txt"]].concat(), None);
    assert!(out.code == 2 && out.stdout.contains("bad.txt is not UTF-8 text"));
    let out = d.ini("i3", &[&base[..], &["@@handle"]].concat(), None);
    assert!(out.code == 0 && out.stdout.contains("<context>@handle</context>"));
    let out = d.ini("i4", &[&base[..], &["@nofile.txt"]].concat(), None);
    assert!(
        out.code == 2 && out.stdout.contains("cannot read nofile.txt: No such file or directory") && out.stdout.contains("written with @@")
    );
}

#[test]
fn an_initiator_that_takes_part_is_handed_over_to_the_receiver() {
    let d = Dir::new("takespart");
    let out =
        d.ini("ini", &["start", "--participants", "ini,b", "--context", "c", "--x-reject", "r", "--x-ideal", "i", "--format", "f"], None);
    assert!(out.code == 0 && out.stdout.starts_with("STATUS: SEND\n--- SEND to: b\n"));
    assert!(out.stdout.contains("run `uvx ioc-concord receiver --me ini task` to see what you owe first"));
    let out = d.ini("ini", &["receive"], Some("anything"));
    assert_eq!(out.code, 2);
    assert!(out.stdout.contains("you take part in this run (ini is one of its participants)"));
    assert!(out.stdout.contains("`uvx ioc-concord receiver --me ini <the same command>`"));
    let out = d.rec("ini", &["task"], None);
    assert!(out.code == 0 && out.stdout.starts_with("STATUS: ANSWER\n--- BRIEF\n"));
}

#[test]
fn the_centre_of_a_star_forwards_to_the_other_spokes() {
    let d = Dir::new("star");
    let out = start(&d, "ini", &["--format", "f"], &["--relay", "a,c"]);
    assert!(out.code == 2 && out.stdout.contains("b is a participant but not one of the spokes (--relay a,c)"));
    let out = start(&d, "ini", &["--format", "f"], &["--relay", "a,b"]);
    assert!(out.code == 0 && out.stdout.contains("`uvx ioc-concord initiator --me ini --relay a,b forward --from <the spoke>`"));
    let init = section(&out.stdout, "SEND to: a, b").to_string();
    let out = d.ini("ini", &["--relay", "a,b", "forward", "--from", "z"], Some(&init));
    assert!(out.code == 2 && out.stdout.contains("z is not one of the spokes (--relay a,b)"));
    let out = d.ini("ini", &["--relay", "a,b", "forward", "--from", "a"], Some(&init));
    assert!(out.code == 0 && out.stdout.starts_with("STATUS: SEND\n--- FORWARD to: b\n<concord-init "), "{}", out.stdout);
    assert!(out.stdout.ends_with("Then pass the same message to `uvx ioc-concord initiator --me ini --relay a,b receive`.\n"));
    let out = d.ini("ini", &["--relay", "a,b", "forward"], Some("hello"));
    assert!(out.stdout.starts_with("STATUS: WAIT\n--- NOTE\nnot passed on: it is not a message of a run this centre knows\n"));
    let out = d.ini("ini", &["forward"], Some("hello"));
    assert!(out.code == 2 && out.stdout.contains("forward is for the centre of a star and needs --relay"));
    assert!(d.path("concord-relay-ini.json").exists());
}

// --- the receiver ------------------------------------------------------------------------------------------------------------------------

#[test]
fn an_answer_that_is_refused_says_why_and_the_same_refusal_three_times_stops() {
    let d = Dir::new("refused");
    let out = d.rec("a", &["answer"], Some("{}"));
    assert!(out.code == 2 && out.stdout.contains("refused: nothing is due from you now"));
    d.rec("a", &["receive"], Some(&init_text(60, 3, &["a", "b"])));
    let out = d.rec("a", &["answer"], Some("{not json")).stdout;
    assert!(out.contains("refused: the answer is not valid JSON (Expecting property name enclosed in double quotes: line 1 column 2 (char 1)): pass one JSON object. Nothing was sent."));
    let out = d.rec("a", &["answer"], Some("[1, 2]")).stdout;
    assert!(out.contains("`answer` must be an object, not a list"));
    let out = d.rec("a", &["answer"], Some(r#"{"raw_reject": 10}"#)).stdout;
    assert!(out.contains("You sent: raw_reject. Expected: raw_reject, raw_ideal, candidate, rationale, raw, reasoning."));
    let short =
        json!({"raw_reject": 10, "raw_ideal": 90, "candidate": "X", "rationale": "short", "raw": 85, "reasoning": "short"}).to_string();
    let nexts: Vec<String> = (0..4).map(|_| d.rec("a", &["answer"], Some(&short)).stdout).collect();
    assert!(!nexts[1].contains("NEXT: Stop.") && nexts[1].contains("You have been refused the same way 2 times in a row"));
    assert!(nexts[2].contains("NEXT: Stop. The same refusal came 3 times in a row") && nexts[3].contains("came 4 times"));
    let out = d.rec("a", &["answer"], Some(&answer("X")));
    assert!(out.code == 0 && out.stdout.starts_with("STATUS: SEND\n--- SEND to: b, ini\n<concord-init-reply "), "{}", out.stdout);
    let side: Value = serde_json::from_str(&std::fs::read_to_string(d.path("concord-script-a.json")).unwrap()).unwrap();
    assert_eq!(side["refusal"], Value::Null);
}

#[test]
fn a_model_that_changes_its_bad_answer_each_time_is_stopped_after_six() {
    let d = Dir::new("six");
    d.rec("a", &["receive"], Some(&init_text(60, 3, &["a", "b"])));
    let mut last = String::new();
    for v in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6] {
        let a = json!({"raw_reject": v, "raw_ideal": v + 0.05, "candidate": "x", "rationale": REASONS, "raw": v, "reasoning": REASONS});
        last = d.rec("a", &["answer"], Some(&a.to_string())).stdout;
    }
    assert!(last.contains("NEXT: Stop. This task has been refused 6 times in a row"), "{last}");
}

#[test]
fn task_and_status_change_nothing() {
    let d = Dir::new("status");
    for command in ["task", "status"] {
        assert_eq!(d.rec("a", &[command], None).code, 2);
    }
    d.rec("a", &["receive"], Some(&init_text(60, 3, &["a", "b"])));
    let state = std::fs::read_to_string(d.path("concord-state-a.json")).unwrap();
    let out = d.rec("a", &["task"], None).stdout;
    assert!(out.starts_with("STATUS: ANSWER\n--- TASK\n"), "the brief was shown already: {out}");
    let out = d.rec("a", &["status"], None).stdout;
    assert!(out.starts_with("STATUS: WAIT\n--- STATE\nrun_id: ") && out.contains("\nrole: participant\nstatus: active\noutcome: None\n"));
    assert!(out.contains("\nscores: {}\nbottleneck: None\nanchors in: none\nlacking: []\ntimer "), "{out}");
    assert_eq!(std::fs::read_to_string(d.path("concord-state-a.json")).unwrap(), state);
}

#[test]
fn the_state_files_are_where_the_scripts_kept_them() {
    let d = Dir::new("files2");
    d.rec("a", &["--state-dir", "s", "receive"], Some(&init_text(60, 3, &["a", "b"])));
    let mut names: Vec<String> = std::fs::read_dir(d.path("s")).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
    names.sort();
    assert_eq!(names, ["concord-script-a.json", "concord-state-a.json", "concord-state-a.json.lock"]);
    let state: Value = serde_json::from_str(&std::fs::read_to_string(Path::new(&d.path("s/concord-state-a.json"))).unwrap()).unwrap();
    assert_eq!(state["me"], "a");
    assert!(state.get("session").is_none());
}
