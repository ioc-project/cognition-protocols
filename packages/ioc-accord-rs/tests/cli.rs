//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
#![cfg(feature = "cli")]
//! The `ioc-accord` command: whole sessions through its arguments, its output and its state file, as an orchestrator drives the script;
//! the errors, the input, the run keys and replies that arrive together; and the transcripts of `fixtures/cli.json`, recorded from the
//! script, replayed byte for byte.

use std::path::{Path, PathBuf};

use ioc_accord::cli::{run, Context, Output, StringInput};
use ioc_accord::pyjson::dumps_pretty;
use ioc_accord::session::Session;
use ioc_accord::state::resolve_state_path;
use serde_json::{json, Value};

const STATE: &str = ".accord/state.json";

struct Dir(PathBuf);

impl Dir {
    fn new(name: &str) -> Dir {
        let dir = std::env::temp_dir().join(format!("ioc-accord-cli-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Dir(dir)
    }

    fn path(&self) -> &Path {
        &self.0
    }

    fn read(&self, name: &str) -> String {
        std::fs::read_to_string(self.0.join(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
    }

    fn write(&self, name: &str, text: &str) {
        let path = self.0.join(name);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn context(dir: &Path, stdin: &str) -> Context {
    Context { cwd: dir.to_path_buf(), state: None, run_key: None, state_dir: None, input: Box::new(StringInput(stdin.to_string())) }
}

fn go(dir: &Path, args: &[&str], stdin: &str) -> Output {
    run(&args.iter().map(|a| a.to_string()).collect::<Vec<_>>(), &mut context(dir, stdin))
}

/// What a command printed, read back; its exit code must be `code`.
fn json_of(out: &Output, code: i32) -> Value {
    assert_eq!(out.code, code, "{}{}", out.stdout, out.stderr);
    assert!(out.stdout.ends_with("}\n"), "{}", out.stdout);
    serde_json::from_str(&out.stdout).unwrap()
}

fn ok(out: &Output) -> Value {
    let v = json_of(out, 0);
    assert_eq!(v["ok"], true);
    v["data"].clone()
}

fn error(out: &Output) -> String {
    let v = json_of(out, 1);
    assert_eq!((&v["ok"], &v["status"]), (&json!(false), &json!("ERROR")), "{v}");
    assert!(v["next"].as_str().is_some_and(|n| !n.is_empty()));
    v["error"].as_str().unwrap().to_string()
}

fn session(dir: &Dir, state: &str) -> Session {
    Session::load(&dir.read(state)).unwrap()
}

/// The reply of an agent to the message of its phase: the delivery line its message carried, then its answer.
fn reply(dir: &Dir, state: &str, agent: &str, body: Value) -> String {
    let s = session(dir, state);
    let phase = s.phase_key().unwrap();
    let check = s.state()["issued_checks"][format!("{phase}:{agent}")].as_str().unwrap().to_string();
    format!("<accord-delivery state=\"{}\" check=\"{check}\"></accord-delivery>\n{body}", s.state()["state_id"].as_str().unwrap())
}

/// The answer of an agent in each phase.
fn answer(phase: &str, agent: &str) -> Value {
    match phase.split('_').next().unwrap() {
        "convene" => json!({"agent": agent, "roles": [format!("role-{agent}")], "capabilities": ["query"]}),
        "frame" => json!({"agent": agent, "atoms": [{"text": "p95 latency of checkout"}, {"text": format!("{agent} constraint")}]}),
        "ground" => json!({"agent": agent, "units": {"latency": "milliseconds at p95"}}),
        "verify" => {
            json!({"agent": agent, "holds": [format!("{agent}-k")], "checks": [{"criterion": "load test", "covers": ["p95 under 200ms"]}]})
        }
        other => panic!("no answer for {other}"),
    }
}

#[test]
fn a_self_driving_session_runs_from_init_to_the_execution_messages() {
    let dir = Dir::new("self_driving");
    let init = ok(&go(dir.path(), &["init", "--specialists", "ops,dev", "--key-terms", "latency", "--task", "cut p95"], ""));
    assert_eq!((&init["status"], &init["phase"]), (&json!("SEND"), &json!("convene")));
    assert_eq!(init["awaiting"], json!(["ops", "dev"]));

    let mut last = Value::Null;
    for _ in 0..12 {
        let phase = session(&dir, STATE).phase_key().unwrap();
        if phase == "locked" {
            break;
        }
        for agent in ["ops", "dev"] {
            let text = reply(&dir, STATE, agent, answer(&phase, agent));
            let mut args = vec!["handle", "--agent", agent, "--reply", &text];
            if phase == "verify" {
                args.extend(["--required-knowledge", "[\"ops-k\", \"dev-k\", \"gap\"]", "--objective-clauses", "[\"p95 under 200ms\"]"]);
            }
            last = ok(&go(dir.path(), &args, ""));
        }
    }
    assert_eq!(last["status"], "LOCKED");
    let contract = &last["locked_intent_contract"];
    assert_eq!(contract["verifiable_objective"]["owners"], json!({"ops-k": "ops", "dev-k": "dev"}));
    assert_eq!(last["gaps"], json!(["gap"]));

    let exec = ok(&go(dir.path(), &["execution-message", "--agent", "dev", "--assignment-file", "-"], "  ship it\n"));
    assert!(exec["message"].as_str().unwrap().ends_with("ASSIGNMENT: ship it"));
    let status = ok(&go(dir.path(), &["status"], ""));
    assert_eq!((&status["locked"], &status["roster"], &status["frame_rounds"]), (&json!(true), &json!(2), &json!(2)));
    // the state file is the script's text, with no final line break
    assert_eq!(dir.read(STATE), session(&dir, STATE).dump());
}

#[test]
fn a_step_by_step_session_takes_its_replies_from_files_stdin_and_inline() {
    let dir = Dir::new("step_by_step");
    let data = ok(&go(dir.path(), &["init", "--specialists", "a,b", "--key-terms", "latency", "--action-tool", "deploy"], ""));
    assert_eq!(data["next_command"], "roster");
    dir.write("replies/roster.json", r#"[{"agent": "a", "roles": ["sre"]}, "```json\n{\"agent\": \"b\", \"roles\": [\"SRE\"]}\n```"]"#);
    let data = ok(&go(dir.path(), &["roster", "--responses", "replies/roster.json"], ""));
    assert_eq!(data["flagged_items"], json!(["duplicate exclusive role 'sre' claimed by ['a', 'b']"]));
    let frame = r#"[{"agent": "a", "atoms": [{"text": "latency too high"}]}, {"agent": "b", "atoms": [{"text": "Latency too high!"}]}]"#;
    let data = ok(&go(dir.path(), &["frame", "--responses", "-", "--current-frame", "[]"], frame));
    assert_eq!((&data["round"], &data["next_command"]), (&json!(1), &json!("frame")));
    let data = ok(&go(dir.path(), &["frame", "--responses", frame], ""));
    assert_eq!((&data["round"], &data["atoms"][0]["support"]), (&json!(2), &json!(2)));
    let data = ok(&go(dir.path(), &["glossary", "--responses", r#"[{"agent": "a", "units": {"latency": "ms"}}]"#], ""));
    assert_eq!(data["glossary"][0]["status"], "agreed");
    let verify = r#"[{"agent": "a", "holds": ["k"], "checks": [{"criterion": "c", "covers": ["fast"]}]}]"#;
    let data = ok(&go(dir.path(), &["verify", "--responses", verify, "--objective-clauses", "[\"fast\"]"], ""));
    assert_eq!(data["next"]["command"], "execution-message");
    assert_eq!(data["locked_intent_contract"]["status"], "INTENT-LOCKED");
    assert_eq!(data["unverifiable_clauses"], json!([]));
    ok(&go(dir.path(), &["lock"], ""));
    dir.write("task.txt", "\r\n  Build it\r\n");
    let data = ok(&go(dir.path(), &["execution-message", "--agent", "b", "--assignment-file", "task.txt"], ""));
    assert!(data["message"].as_str().unwrap().ends_with("ASSIGNMENT: Build it"));
}

#[test]
fn an_error_is_one_json_object_with_what_to_do_and_changes_nothing() {
    let dir = Dir::new("errors");
    let e = error(&go(dir.path(), &["handle", "--agent", "a", "--reply", "x"], ""));
    assert_eq!(e, "no state file at .accord/state.json — run init first");
    let e = error(&go(dir.path(), &["--state", "a//b/./s.json", "handle", "--agent", "a", "--reply", "x"], ""));
    assert_eq!(e, "no state file at a/b/s.json — run init first");

    ok(&go(dir.path(), &["init", "--specialists", "a"], ""));
    let before = dir.read(STATE);
    let e = error(&go(dir.path(), &["roster", "--responses", "{bad"], ""));
    assert_eq!(e, "invalid JSON in inline JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)");
    let e = error(&go(dir.path(), &["roster", "--responses", "nope.json"], ""));
    assert_eq!(e, "FileNotFoundError: [Errno 2] No such file or directory: 'nope.json'");
    let e = error(&go(dir.path(), &["frame", "--responses", "[\"prose\"]"], ""));
    assert!(e.starts_with("frame: none of the responses carried an 'atoms' list"), "{e}");
    let e = error(&go(dir.path(), &["execution-message", "--agent", "a", "--assignment-file", "nope.txt"], ""));
    assert_eq!(e, "execution-message: no locked contract exists");
    let e = error(&go(dir.path(), &["handle", "--agent", "a", "--reply", "x"], ""));
    assert_eq!(e, "handle: no task in state — run init --task first");
    let out = go(dir.path(), &["roster", "--responses", "[1,]"], "");
    let v = json_of(&out, 1);
    assert_eq!(v["phase"], "roster");
    assert!(v["next"].as_str().unwrap().starts_with("That argument is not JSON"));
    assert_eq!(dir.read(STATE), before, "a command that failed saves nothing");

    // a state that is not JSON: every command but `handle` fails as the script does when it cannot load it, with nothing on stdout
    dir.write(STATE, "{oops");
    let out = go(dir.path(), &["status"], "");
    assert!(out.code == 1 && out.stdout.is_empty() && out.stderr.contains("JSONDecodeError"), "{out:?}");
    let e = error(&go(dir.path(), &["handle", "--agent", "a", "--reply", "x"], ""));
    assert_eq!(e, "JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)");
}

#[test]
fn a_command_line_the_parser_refuses_is_a_usage_error() {
    let dir = Dir::new("usage");
    for args in [&[][..], &["bogus"], &["init"], &["roster"], &["handle", "--agent", "a"], &["init", "--specialists", "a", "--state", "x"]]
    {
        let out = go(dir.path(), args, "");
        let v = json_of(&out, 2);
        assert_eq!((&v["ok"], &v["phase"], &v["status"]), (&json!(false), &json!("usage"), &json!("ERROR")), "{args:?}");
        let e = v["error"].as_str().unwrap();
        assert!(e.starts_with("usage error: ") && !e.contains('\n'), "{e}");
        assert!(v["next"].as_str().unwrap().starts_with("Run `--help`"));
    }
    assert!(!dir.path().join(".accord").exists(), "a usage error touches nothing");
    for args in [&["--help"][..], &["handle", "--help"]] {
        let out = go(dir.path(), args, "");
        assert!(out.code == 0 && out.stdout.contains("Usage: ioc-accord"), "{}", out.stdout);
    }
    // abbreviations, `=`, a repeated option (the last one wins) and a value that starts with a dash, as argparse takes them
    let data = ok(&go(dir.path(), &["init", "--spec", "x", "--specialists=a,b", "--task", "- t"], ""));
    assert_eq!(data["specialists"], json!(["a", "b"]));
    assert_eq!(session(&dir, STATE).state()["task"], "- t");
}

#[test]
fn stdin_is_read_once() {
    let dir = Dir::new("stdin");
    ok(&go(dir.path(), &["init", "--specialists", "a"], ""));
    let data = ok(&go(dir.path(), &["roster", "--responses", "-"], "[{\"agent\": \"a\", \"roles\": [\"x\"]}]"));
    assert_eq!(data["roster"][0]["identity"], "a");
    // the first `-` takes all of it, the second reads nothing
    let e = error(&go(dir.path(), &["verify", "--responses", "-", "--required-knowledge", "-"], "[]"));
    assert_eq!(e, "invalid JSON in -: Expecting value: line 1 column 1 (char 0)");
}

#[test]
fn a_run_key_isolates_the_state_file() {
    let dir = Dir::new("run_key");
    let keyed = |key: &str, state_dir: Option<&str>| {
        let mut ctx = context(dir.path(), "");
        ctx.run_key = Some(key.to_string());
        ctx.state_dir = state_dir.map(str::to_string);
        ctx
    };
    let args = |list: &[&str]| list.iter().map(|a| a.to_string()).collect::<Vec<_>>();
    ok(&run(&args(&["init", "--specialists", "a", "--task", "t"]), &mut keyed("k1", None)));
    ok(&run(&args(&["init", "--specialists", "b"]), &mut keyed("k2", Some("runs/keyed"))));
    let one = resolve_state_path(STATE, Some("k1"), None);
    let two = resolve_state_path(STATE, Some("k2"), Some("runs/keyed"));
    assert!(one.starts_with(".accord/state-") && two.starts_with("runs/keyed/state-"), "{one} {two}");
    assert_eq!(session(&dir, &one).state()["specialists"], json!(["a"]));
    assert_eq!(session(&dir, &two).state()["specialists"], json!(["b"]));
    assert!(!dir.path().join(STATE).exists());
    // the same key finds the same session; an empty key is none
    let status = ok(&run(&args(&["status"]), &mut keyed("k1", None)));
    assert_eq!(status["specialists"], json!(["a"]));
    let e = error(&run(&args(&["handle", "--agent", "a", "--reply", "x"]), &mut keyed("", Some("ignored"))));
    assert_eq!(e, "no state file at .accord/state.json — run init first");
    // ACCORD_STATE names the state file when --state does not
    let mut ctx = context(dir.path(), "");
    ctx.state = Some("custom/s.json".into());
    ok(&run(&args(&["init", "--specialists", "c"]), &mut ctx));
    assert_eq!(session(&dir, "custom/s.json").state()["specialists"], json!(["c"]));
}

#[test]
fn replies_that_arrive_together_are_all_recorded() {
    let dir = Dir::new("concurrent");
    let agents: Vec<String> = (0..8).map(|i| format!("ag{i}")).collect();
    ok(&go(dir.path(), &["init", "--specialists", &agents.join(","), "--task", "t"], ""));
    let replies: Vec<(String, String)> = agents.iter().map(|a| (a.clone(), reply(&dir, STATE, a, answer("convene", a)))).collect();
    let root = dir.path().to_path_buf();
    let threads: Vec<_> = replies
        .into_iter()
        .map(|(agent, text)| {
            let root = root.clone();
            std::thread::spawn(move || go(&root, &["handle", "--agent", &agent, "--reply", &text], ""))
        })
        .collect();
    let outs: Vec<Value> = threads.into_iter().map(|t| ok(&t.join().unwrap())).collect();
    let advanced = outs.iter().filter(|o| o["phase"] == "frame_0").count();
    assert_eq!(advanced, 1, "the last reply closes CONVENE, once");
    let s = session(&dir, STATE);
    assert_eq!(s.state()["roster"].as_array().unwrap().len(), 8);
    assert_eq!(s.state()["handle_replies"]["convene"].as_object().unwrap().len(), 8);
}

#[test]
fn a_refusal_keeps_its_count() {
    let dir = Dir::new("refusal");
    ok(&go(dir.path(), &["init", "--specialists", "a,b", "--task", "t"], ""));
    let body = answer("convene", "a").to_string();
    let e = error(&go(dir.path(), &["handle", "--agent", "a", "--reply", &body], ""));
    assert!(e.contains("carried no `<accord-delivery>` check line"), "{e}");
    assert_eq!(session(&dir, STATE).state()["check_fail"], json!({"convene:a": 1}), "the refusal is saved");
    // a wrong check is refused without a count
    let wrong = format!("<accord-delivery state=\"x\" check=\"00000000\"></accord-delivery>\n{body}");
    let e = error(&go(dir.path(), &["handle", "--agent", "a", "--reply", &wrong], ""));
    assert!(e.contains("does not match its check"), "{e}");
    // the second attempt without the line is recorded, with a defect
    let data = ok(&go(dir.path(), &["handle", "--agent", "a", "--reply", &body], ""));
    assert_eq!(data["still_waiting_this_phase"], json!(["b"]));
    let s = session(&dir, STATE);
    assert_eq!(s.state()["check_fail"], json!({}));
    assert_eq!(s.state()["defects"][0]["kind"], "unverified_reply");
}

// ----------------------------------------------------------------------------------------------------------------------------------
// The transcripts of the script
// ----------------------------------------------------------------------------------------------------------------------------------

/// `atoms_by_agent` of `frame` in name order: the script builds it from a set, in Python's hash order.
fn sort_atoms(stdout: &str) -> String {
    let Ok(mut v) = serde_json::from_str::<Value>(stdout) else { return stdout.to_string() };
    if v["phase"] != "frame" || v["ok"] != true {
        return stdout.to_string();
    }
    let Some(map) = v["data"]["atoms_by_agent"].as_object_mut() else { return stdout.to_string() };
    map.sort_keys();
    format!("{}\n", dumps_pretty(&v))
}

/// Replay one recorded sequence; the differences, all of them.
fn replay(case: &Value) -> Vec<String> {
    let name = case["name"].as_str().unwrap();
    let dir = Dir::new(&format!("golden-{name}"));
    let state = case["state"].as_str().unwrap();
    let env = |k: &str| case["env"][k].as_str().map(str::to_string);
    let mut sid = String::from("{SID}");
    let mut failures = Vec::new();
    for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
        let fill = |s: &str| s.replace("{SID}", &sid);
        for (file, content) in step["files"].as_object().unwrap() {
            let path = dir.path().join(file);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            match content {
                Value::String(text) => std::fs::write(&path, fill(text)).unwrap(),
                other if other["dir"] == true => std::fs::create_dir_all(&path).unwrap(),
                other => std::fs::write(&path, hex(other["hex"].as_str().unwrap())).unwrap(),
            }
        }
        let args: Vec<String> = step["args"].as_array().unwrap().iter().map(|a| fill(a.as_str().unwrap())).collect();
        let stdin = step["stdin"].as_str().map(fill).unwrap_or_default();
        let mut ctx = Context {
            cwd: dir.path().to_path_buf(),
            state: env("ACCORD_STATE"),
            run_key: env("ACCORD_RUN_KEY"),
            state_dir: env("ACCORD_STATE_DIR"),
            input: Box::new(StringInput(stdin)),
        };
        let out = run(&args, &mut ctx);
        if let Ok(text) = std::fs::read_to_string(dir.path().join(state)) {
            if let Some(id) = serde_json::from_str::<Value>(&text).ok().and_then(|v| v["state_id"].as_str().map(str::to_string)) {
                if !id.is_empty() {
                    sid = id;
                }
            }
        }
        let back = |s: &str| if sid == "{SID}" { s.to_string() } else { s.replace(&sid, "{SID}") };
        let at = format!("{name} step {i} {args:?}");
        if out.code != step["code"].as_i64().unwrap() as i32 {
            failures.push(format!("{at}: exit code {} (expected {})\n{}{}", out.code, step["code"], out.stdout, out.stderr));
        }
        if step["code"] == 2 {
            let v: Value = serde_json::from_str(&out.stdout).unwrap_or(Value::Null);
            if v["phase"] != "usage" || !v["error"].as_str().is_some_and(|e| e.starts_with("usage error: ")) {
                failures.push(format!("{at}: not a usage error\n{}", out.stdout));
            }
        }
        if let Some(want) = step["stdout"].as_str() {
            let got = back(&sort_atoms(&out.stdout));
            if got != want {
                failures.push(format!("{at}: stdout\n  rust:   {got:?}\n  python: {want:?}"));
            }
        }
        if let Some(want) = step["state"].as_str() {
            let got = std::fs::read_to_string(dir.path().join(state)).map(|s| back(&s)).unwrap_or_default();
            if got != want {
                failures.push(format!("{at}: state file\n  rust:   {got:?}\n  python: {want:?}"));
            }
        }
    }
    failures
}

fn hex(text: &str) -> Vec<u8> {
    (0..text.len()).step_by(2).map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap()).collect()
}

#[test]
fn the_recorded_transcripts_of_the_script_replay_byte_for_byte() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/cli.json");
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let steps: usize = cases.iter().map(|c| c["steps"].as_array().unwrap().len()).sum();
    assert!(cases.len() >= 8 && steps >= 100, "only {} transcripts, {steps} steps", cases.len());
    let failures: Vec<String> = cases.iter().flat_map(replay).collect();
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}
