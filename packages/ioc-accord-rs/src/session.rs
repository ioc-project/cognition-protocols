//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! A session of the initiator: its state, and the commands of the script, each a method that reads and changes it.
//!
//! Two ways to drive a session, as in the script:
//!
//! * self-driving: `init` with a task, then `handle` for every reply that arrives. The phase is worked out from the state, each reply is
//!   checked against the message it answers, and when every specialist has answered a phase its merge runs and the messages of the next
//!   phase come back.
//! * phase by phase: `roster`, `frame` (once per round), `glossary` (again after CLARIFY), `verify`, then `lock` and
//!   `execution_message`, each given the replies of its phase.
//!
//! A command that fails leaves the session as it was, except a refusal of `handle` (`AccordError::is_refusal`), which keeps the state as
//! the refusal left it: the attempt it counted has to survive it. The library does no I/O: the caller loads the state, runs one command
//! and saves `dump()` when it changed, holding whatever lock it needs across the three.
//!
//! The functions under the methods (`cmd_handle`, `current_action`, ...) are the script's, over a state given as a `serde_json::Value`.

use indexmap::IndexMap;
use serde::Serialize;
use serde_json::{json, Map, Value};

use crate::algorithms::{
    assemble_lock, assign_checks, assign_holdings, build_glossary, chao1, covered_clauses, good_turing_missing_mass, group_atoms,
    union_roster,
};
use crate::errors::{AccordError, Result};
use crate::ingest::{ingest_frame_round, ingest_glossary, ingest_roster, ingest_verify, parse_responses, FrameRound};
use crate::messages::{
    build_clarify_msg, build_convene_msg, build_frame_msg, build_ground_msg, build_verify_msg, find_check, render_execution_message,
};
use crate::protocol::MAX_FRAME_ROUNDS;
use crate::py;
use crate::state::{blank_state, dump_state, load_state, new_state_id, with_defaults};

/// The hint `init` gives with the messages of a self-driving session.
pub const HANDLE_HINT: &str =
    "Use `accord_initiator.py handle --agent <id> --reply <text>` for each incoming reply. No separate phase commands needed.";

/// The arguments of `init`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct InitArgs {
    /// The specialist ids, comma-separated.
    pub specialists: String,
    /// The key terms of GROUND, comma-separated.
    pub key_terms: String,
    /// The name of the action tool or step of EXECUTION.
    pub action_tool: String,
    /// The task: with one, the session is self-driving, and `init` returns the CONVENE messages.
    pub task: String,
    /// The id of the session (`state` on the delivery line); None draws one (`state::new_state_id`).
    pub state_id: Option<String>,
}

/// What the initiator does next: the phase, who still has to receive its message, and the message of each.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Action {
    pub phase: String,
    pub recipients: Vec<String>,
    pub messages: IndexMap<String, String>,
}

/// A session: its state, and the commands.
#[derive(Debug, Clone, PartialEq)]
pub struct Session {
    state: Value,
}

impl Default for Session {
    fn default() -> Self {
        Session { state: blank_state() }
    }
}

impl Session {
    /// A session with the blank state, as the script has it when the state file does not exist.
    pub fn new() -> Self {
        Self::default()
    }

    /// A session from the text of its state file.
    pub fn load(text: &str) -> Result<Self> {
        Ok(Session { state: load_state(text)? })
    }

    /// A session from a state, completed with the default of every key it lacks.
    pub fn from_state(state: Value) -> Result<Self> {
        Ok(Session { state: with_defaults(state)? })
    }

    /// The state.
    pub fn state(&self) -> &Value {
        &self.state
    }

    /// The state, to keep.
    pub fn into_state(self) -> Value {
        self.state
    }

    /// The text of the state file, byte for byte what the script writes.
    pub fn dump(&self) -> String {
        dump_state(&self.state)
    }

    /// The current phase: `convene`, `frame_<round>`, `ground`, `clarify`, `verify` or `locked`.
    pub fn phase_key(&self) -> Result<String> {
        infer_phase_key(&self.state)
    }

    /// Who still has to receive the message of the current phase, and the messages.
    pub fn current_action(&self) -> Result<Action> {
        current_action(&self.state)
    }

    /// Run `f` on a copy of the state; keep the copy when it succeeds, or when it fails with a refusal and `keep_refusal`.
    fn transact<T>(&mut self, keep_refusal: bool, f: impl FnOnce(&mut Value) -> Result<T>) -> Result<T> {
        let mut st = self.state.clone();
        let out = f(&mut st);
        match &out {
            Ok(_) => self.state = st,
            Err(e) if keep_refusal && e.is_refusal() => self.state = st,
            Err(_) => {}
        }
        out
    }

    /// `init`: start a new session (the state is reset).
    pub fn init(&mut self, args: &InitArgs) -> Result<Value> {
        let (st, result) = cmd_init(args)?;
        self.state = st;
        Ok(result)
    }

    /// `roster`: the CONVENE merge of the replies (a JSON array of strings or objects).
    pub fn roster(&mut self, responses: &Value) -> Result<Value> {
        self.transact(false, |st| cmd_roster(st, responses))
    }

    /// `frame`: the merge of one FRAME round.
    pub fn frame(&mut self, responses: &Value) -> Result<Value> {
        self.transact(false, |st| cmd_frame(st, responses))
    }

    /// `glossary`: the GROUND merge, also after CLARIFY. `key_terms` (comma-separated) overrides the key terms of `init`.
    pub fn glossary(&mut self, responses: &Value, key_terms: Option<&str>) -> Result<Value> {
        self.transact(false, |st| cmd_glossary(st, responses, key_terms))
    }

    /// `verify`: the VERIFY merge, which locks the contract. None for an argument the command was not given.
    pub fn verify(&mut self, responses: &Value, required_knowledge: Option<&Value>, objective_clauses: Option<&Value>) -> Result<Value> {
        self.transact(false, |st| cmd_verify(st, responses, required_knowledge, objective_clauses))
    }

    /// `lock`: the contract assembled from the state, stored.
    pub fn lock(&mut self) -> Result<Value> {
        self.transact(false, store_contract)
    }

    /// `execution-message`: the EXECUTION_REQUEST of an agent, for the assignment (its text, stripped here).
    pub fn execution_message(&self, agent: &str, assignment: &str) -> Result<Value> {
        cmd_execution_message(&self.state, agent, assignment)
    }

    /// `status`: where the session stands.
    pub fn status(&self) -> Result<Value> {
        cmd_status(&self.state)
    }

    /// `handle`: record one reply of an agent, and advance the phase when every specialist has replied. `required_knowledge` and
    /// `objective_clauses`, when given (and not JSON `null`), are stored first, for VERIFY.
    pub fn handle(
        &mut self,
        agent: &str,
        reply: &str,
        required_knowledge: Option<Value>,
        objective_clauses: Option<Value>,
    ) -> Result<Value> {
        self.transact(true, |st| {
            if let Some(rk) = required_knowledge.filter(|v| !v.is_null()) {
                set(st, "required_knowledge", rk);
            }
            if let Some(oc) = objective_clauses.filter(|v| !v.is_null()) {
                set(st, "objective_clauses", oc);
            }
            cmd_handle(st, agent, reply)
        })
    }
}

// ----------------------------------------------------------------------------------------------------------------------------------
// Access to the state
// ----------------------------------------------------------------------------------------------------------------------------------

fn set(st: &mut Value, key: &str, value: Value) {
    if let Value::Object(o) = st {
        o.insert(key.to_string(), value);
    }
}

/// `st.get(key)` (the state is always an object).
fn get<'a>(st: &'a Value, key: &str) -> Option<&'a Value> {
    st.get(key)
}

/// `st[key]` as a dict to change.
fn dict_mut<'a>(st: &'a mut Value, key: &str) -> Result<&'a mut Map<String, Value>> {
    let v = match st {
        Value::Object(o) => o.get_mut(key).ok_or_else(|| AccordError::python("KeyError", py::repr_str(key)))?,
        other => return Err(AccordError::type_error(format!("'{}' object is not subscriptable", py::type_name(other)))),
    };
    match v {
        Value::Object(o) => Ok(o),
        Value::Array(_) | Value::String(_) => {
            Err(AccordError::type_error(format!("{} indices must be integers or slices, not str", py::type_name(v))))
        }
        other => Err(AccordError::type_error(format!("'{}' object does not support item assignment", py::type_name(other)))),
    }
}

/// The strings of a list of the state (`specialists`, `key_terms`); what is not a string is left out, as no reply can match it.
fn strings(st: &Value, key: &str) -> Result<Vec<String>> {
    Ok(py::iter(py::item(st, key)?)?.into_iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
}

fn len_of(st: &Value, key: &str) -> Result<Value> {
    Ok(py::len(py::item(st, key)?)?.into())
}

fn merge(into: &mut Map<String, Value>, from: Map<String, Value>) {
    for (k, v) in from {
        into.insert(k, v);
    }
}

fn object(v: Value) -> Map<String, Value> {
    match v {
        Value::Object(o) => o,
        _ => Map::new(),
    }
}

/// `[v.strip() for v in value.split(",") if v.strip()]`.
pub fn csv(value: &str) -> Vec<String> {
    value.split(',').map(py::strip).filter(|v| !v.is_empty()).map(str::to_string).collect()
}

/// The fields every phase command adds: the next command, and that nothing may run outside the protocol yet.
pub fn protocol_control(next_command: &str) -> Map<String, Value> {
    object(json!({
        "next_command": next_command,
        "protocol_status": "IN_PROGRESS",
        "locked_contract_exists": false,
        "normal_execution_allowed": false,
        "app_execution_allowed": false,
    }))
}

// ----------------------------------------------------------------------------------------------------------------------------------
// The self-driving session
// ----------------------------------------------------------------------------------------------------------------------------------

/// The current phase, from the merged data: no roster is CONVENE, then the FRAME rounds, no glossary is GROUND, ambiguous pairs are
/// CLARIFY, no contract is VERIFY.
pub fn infer_phase_key(st: &Value) -> Result<String> {
    if !py::truthy(py::item(st, "roster")?) {
        return Ok("convene".into());
    }
    let rounds = py::item(st, "frame_rounds")?;
    let below = match rounds {
        Value::Number(n) => n.as_f64().is_some_and(|r| r < MAX_FRAME_ROUNDS as f64),
        Value::Bool(b) => (*b as i64) < MAX_FRAME_ROUNDS,
        other => {
            return Err(AccordError::type_error(format!("'<' not supported between instances of '{}' and 'int'", py::type_name(other))));
        }
    };
    if below {
        return Ok(format!("frame_{}", py::str_of(rounds)));
    }
    if !py::truthy(py::item(st, "glossary")?) {
        return Ok("ground".into());
    }
    if get(st, "clarify_terms").is_some_and(py::truthy) {
        return Ok("clarify".into());
    }
    if !get(st, "locked_contract").is_some_and(py::truthy) {
        return Ok("verify".into());
    }
    Ok("locked".into())
}

/// The phase, who still has to receive its message (the specialists that have not replied to it), and the message of each.
pub fn current_action(st: &Value) -> Result<Action> {
    if get(st, "locked_contract").is_some_and(py::truthy) {
        return Ok(Action { phase: "locked".into(), ..Action::default() });
    }
    let phase = infer_phase_key(st)?;
    let specialists = strings(st, "specialists")?;
    let replies = py::item(st, "handle_replies")?;
    let empty = Value::Object(Map::new());
    let replied = py::get(replies, &phase)?.unwrap_or(&empty);
    let mut pending = Vec::new();
    for a in specialists {
        if !py::contains(replied, &Value::String(a.clone()))? {
            pending.push(a);
        }
    }
    let mut messages = IndexMap::new();
    let kind = phase.split('_').next().unwrap_or("");
    match (phase.as_str(), kind) {
        ("convene", _) | ("ground", _) | ("verify", _) => {
            let msg = match phase.as_str() {
                "convene" => build_convene_msg(st)?,
                "ground" => build_ground_msg(st)?,
                _ => build_verify_msg(st)?,
            };
            for a in &pending {
                messages.insert(a.clone(), msg.clone());
            }
        }
        (_, "frame") if phase.starts_with("frame_") => {
            for a in &pending {
                messages.insert(a.clone(), build_frame_msg(st, a)?);
            }
        }
        ("clarify", _) => {
            let empty = Value::Array(Vec::new());
            let pairs = get(st, "clarify_terms").unwrap_or(&empty);
            for a in &pending {
                messages.insert(a.clone(), build_clarify_msg(st, a, pairs)?);
            }
        }
        _ => return Ok(Action { phase, ..Action::default() }),
    }
    Ok(Action { phase, recipients: pending, messages })
}

/// Whether the message of a phase was already issued to an agent.
pub fn is_issued(st: &Value, phase: &str, agent: &str) -> Result<bool> {
    Ok(py::get(py::item(st, "issued")?, &format!("{phase}:{agent}"))?.is_some_and(py::truthy))
}

/// Record that the message of a phase was issued to the agents, and the check each message carries.
pub fn mark_issued(st: &mut Value, phase: &str, agents: &[String], messages: Option<&IndexMap<String, String>>) -> Result<()> {
    for a in agents {
        let key = format!("{phase}:{a}");
        dict_mut(st, "issued")?.insert(key.clone(), Value::Bool(true));
        if let Some(msg) = messages.filter(|m| !m.is_empty()).and_then(|m| m.get(a)) {
            if let Some(check) = find_check(msg) {
                dict_mut(st, "issued_checks")?.insert(key, Value::String(check.to_string()));
            }
        }
    }
    Ok(())
}

/// What a reply to each kind of phase has to look like, for the refusal.
pub fn phase_shape(kind: &str) -> Option<&'static str> {
    Some(match kind {
        "convene" => "an agent card: `agent`, plus `roles`, `capabilities`, `access_scopes` and `holdings_summary`",
        "frame" => "an `agent` and an `atoms` list of {\"text\": ..., \"type\": ...} objects (`[]` withdraws every atom)",
        "ground" => "an `agent` and a `units` map carrying a unit for a term in KEY_TERMS",
        "clarify" => "an `agent`, the `term`, and an `extended_definition` for it",
        "verify" => "an `agent`, its `holds` list and its proposed `checks`",
        _ => return None,
    })
}

/// Refuse a reply this phase's merge would take nothing from.
///
/// The bar is the reply's shape, not how much it says: the keys that make it an answer to this phase have to be there, and may be
/// empty. `{"agent": a, "holds": [], "checks": []}` is a complete VERIFY reply from an agent that holds nothing, while
/// `{"agent": a, "summary": "..."}` is not a reply at all.
pub fn validate_reply(st: &Value, phase: &str, agent: &str, reply: &str) -> Result<()> {
    let kind = phase.split('_').next().unwrap_or("");
    let Some(shape) = phase_shape(kind) else { return Ok(()) };
    let refuse = |why: &str| {
        Err(AccordError::new(format!("handle: the reply from {agent} {why}. {} needs {shape}. Nothing was recorded.", kind.to_uppercase())))
    };
    let raw = [Value::String(reply.to_string())];
    let parsed = parse_responses(&raw);
    let Some(body) = parsed.first() else { return refuse("is not a JSON object") };
    let named = py::str_or([body.get("agent"), body.get("identity")]);
    let named = !py::strip(&named).is_empty();
    const CARD_KEYS: [&str; 6] = ["roles", "capabilities", "access_scopes", "accessScopes", "holdings_summary", "holdingsSummary"];
    const VERIFY_KEYS: [&str; 3] = ["holds", "checks", "proposed_checks"];
    match kind {
        "convene" => {
            if !named {
                return refuse("names no agent");
            }
            if !CARD_KEYS.iter().any(|k| body.contains_key(*k)) {
                return refuse("carries none of the agent-card fields");
            }
        }
        "frame" => {
            if !matches!(body.get("atoms"), Some(Value::Array(_))) {
                return refuse("carries no `atoms` list");
            }
        }
        "ground" | "clarify" => {
            if ingest_glossary(&raw, &strings(st, "key_terms")?)?.is_empty() {
                return refuse("grounds none of the key terms");
            }
        }
        "verify" => {
            if !named {
                return refuse("names no agent");
            }
            if !VERIFY_KEYS.iter().any(|k| body.contains_key(*k)) {
                return refuse("carries neither `holds` nor `checks`");
            }
        }
        _ => {}
    }
    Ok(())
}

/// Refuse a reply that does not carry the check of the message that was sent.
///
/// * A wrong check is always refused: the text belongs to some other message. Nothing is recorded.
/// * A missing check is refused once (a refusal: the count of the attempt is kept), then recorded anyway with an `unverified_reply`
///   defect, since a receiver that never echoes the line must not stall the session.
///
/// Nothing is checked when no check was stamped for the agent (a session started before checks existed).
pub fn verify_reply_check(st: &mut Value, phase: &str, agent: &str, reply: &str) -> Result<()> {
    let key = format!("{phase}:{agent}");
    let expected = py::get(py::item(st, "issued_checks")?, &key)?.cloned();
    let Some(expected) = expected.filter(py::truthy) else { return Ok(()) };
    let found = find_check(reply);
    if let Some(found) = found {
        if expected.as_str() == Some(found) {
            dict_mut(st, "check_fail")?.shift_remove(&key);
            return Ok(());
        }
        return Err(AccordError::new(format!(
            "handle: the reply from {agent} does not match its check (expected {}, got {found}). Nothing was recorded.",
            py::str_of(&expected)
        )));
    }
    let fails = dict_mut(st, "check_fail")?;
    let count = match fails.get(&key) {
        None => 1,
        Some(Value::Number(n)) if n.as_i64().is_some() => n.as_i64().unwrap_or(0) + 1,
        Some(Value::Bool(b)) => *b as i64 + 1,
        Some(other) => {
            return Err(AccordError::type_error(format!("unsupported operand type(s) for +: '{}' and 'int'", py::type_name(other))));
        }
    };
    fails.insert(key.clone(), count.into());
    if count < 2 {
        return Err(AccordError::refusal(format!(
            "handle: the reply from {agent} carried no `<accord-delivery>` check line. Nothing was recorded."
        )));
    }
    fails.shift_remove(&key);
    let defect = json!({
        "kind": "unverified_reply", "agent": agent, "phase": phase,
        "detail": "recorded on a second attempt without a matching delivery check",
    });
    match py::item(st, "defects")? {
        Value::Array(_) => {
            if let Some(Value::Array(d)) = st.get_mut("defects") {
                d.push(defect);
            }
            Ok(())
        }
        other => Err(py::attribute_error(other, "append")),
    }
}

/// The defects of a roster: a role (stripped, lowercased) claimed by more than one agent.
fn roster_defects(roster: &[Value]) -> Result<Vec<Value>> {
    let mut owners: IndexMap<String, Vec<Value>> = IndexMap::new();
    for c in roster {
        for role in py::iter(py::item(c, "roles")?)? {
            let role = py::strip_str(&role)?.to_lowercase();
            owners.entry(role).or_default().push(py::item(c, "identity")?.clone());
        }
    }
    Ok(owners
        .into_iter()
        .filter(|(_, o)| o.len() > 1)
        .map(|(r, o)| Value::String(format!("duplicate exclusive role '{r}' claimed by {}", py::repr(&Value::Array(o)))))
        .collect())
}

/// The roster of the CONVENE replies, and its defects, into the state.
fn merge_roster(st: &mut Value, raw: &[Value]) -> Result<()> {
    let roster = union_roster(ingest_roster(raw)?);
    let defects = roster_defects(&roster)?;
    set(st, "roster", Value::Array(roster));
    set(st, "defects", Value::Array(defects));
    Ok(())
}

/// A FRAME round into the state: each agent that answered has its atoms replaced, the round is counted and the atoms are clustered
/// again.
fn merge_frame(st: &mut Value, round: &FrameRound) -> Result<()> {
    let mut obs = Vec::new();
    for o in py::iter(py::item(st, "obs")?)? {
        let agent = py::item(&o, "agent")?;
        if !agent.as_str().is_some_and(|a| round.by_agent.contains_key(a)) {
            obs.push(o);
        }
    }
    for atoms in round.by_agent.values() {
        obs.extend(atoms.iter().cloned());
    }
    let rounds = match py::item(st, "frame_rounds")? {
        Value::Number(n) if n.as_i64().is_some() => Value::from(n.as_i64().unwrap_or(0) + 1),
        Value::Number(n) => Value::from(n.as_f64().unwrap_or(0.0) + 1.0),
        Value::Bool(b) => Value::from(*b as i64 + 1),
        other => {
            return Err(AccordError::type_error(format!("unsupported operand type(s) for +=: '{}' and 'int'", py::type_name(other))));
        }
    };
    let frame = if obs.is_empty() { Vec::new() } else { group_atoms(&obs)? };
    set(st, "obs", Value::Array(obs));
    set(st, "frame_rounds", rounds);
    set(st, "frame", Value::Array(frame));
    Ok(())
}

/// The entries of a glossary merge into the glossary of the state: a term they speak to is replaced, every other term is kept.
fn merge_glossary(st: &mut Value, entries: &[Value]) -> Result<()> {
    let revised: Vec<&Value> = entries.iter().map(|e| &e["term"]).collect();
    let mut glossary = Vec::new();
    for e in py::iter(py::item(st, "glossary")?)? {
        let term = py::item(&e, "term")?;
        if !revised.iter().any(|r| py::eq(r, term)) {
            glossary.push(e);
        }
    }
    glossary.extend(entries.iter().cloned());
    set(st, "glossary", Value::Array(glossary));
    Ok(())
}

/// Run the merge of a phase every specialist has answered; the state changes, and the fields it adds to the result come back.
pub fn run_phase_advance(st: &mut Value, phase_key: &str) -> Result<Map<String, Value>> {
    let replies = py::item(st, "handle_replies")?;
    let raw: Vec<Value> = match py::get(replies, phase_key)? {
        Some(Value::Object(m)) => m.values().cloned().collect(),
        Some(other) => return Err(py::attribute_error(other, "values")),
        None => Vec::new(),
    };

    if phase_key == "convene" {
        merge_roster(st, &raw)?;
        return Ok(object(json!({"roster": st["roster"], "flagged_items": st["defects"]})));
    }

    if phase_key.starts_with("frame_") {
        let round = ingest_frame_round(&raw);
        merge_frame(st, &round)?;
        return Ok(object(json!({"frame": st["frame"], "round": st["frame_rounds"], "truncated": round.truncated})));
    }

    if phase_key == "ground" || phase_key == "clarify" {
        let tuples = ingest_glossary(&raw, &strings(st, "key_terms")?)?;
        let glossary = build_glossary(&tuples);
        merge_glossary(st, &glossary.entries)?;
        let ambiguous = Value::Array(glossary.ambiguous);
        if phase_key == "ground" {
            if py::truthy(&ambiguous) {
                set(st, "clarify_terms", ambiguous.clone());
            }
            return Ok(object(json!({"glossary": st["glossary"], "ambiguous": ambiguous})));
        }
        // empty once the definitions are resolved
        set(st, "clarify_terms", ambiguous.clone());
        return Ok(object(json!({"glossary": st["glossary"], "remaining_ambiguous": ambiguous})));
    }

    if phase_key == "verify" {
        let empty = Value::Array(Vec::new());
        let required = py::or([get(st, "required_knowledge")]).unwrap_or(&empty).clone();
        let clauses = py::or([get(st, "objective_clauses")]).unwrap_or(&empty).clone();
        let replies = ingest_verify(&raw)?;
        let universe = universe_of(&required, &replies.all_holds)?;
        let (owned, uncovered) = assign_holdings(&universe, &replies.covers)?;
        set(st, "owned", owned_value(owned));
        set(st, "uncovered", Value::Array(uncovered));
        let checks = assign_checks(&replies.checks, &clauses)?;
        set(st, "checks", Value::Array(checks));
        let contract = assemble_lock(st)?;
        set(st, "locked_contract", contract.clone());
        return Ok(object(json!({"locked_intent_contract": contract, "gaps": st["uncovered"]})));
    }

    Ok(Map::new())
}

/// `required or all_holds`.
fn universe_of(required: &Value, all_holds: &[String]) -> Result<Vec<Value>> {
    if py::truthy(required) {
        py::iter(required)
    } else {
        Ok(all_holds.iter().map(|s| Value::String(s.clone())).collect())
    }
}

fn owned_value(owned: IndexMap<String, Vec<Value>>) -> Value {
    Value::Object(owned.into_iter().map(|(k, v)| (k, Value::Array(v))).collect())
}

/// `handle`: record one agent's reply, and advance the phase when all replies are in.
///
/// The reply is checked against the message it answers (`verify_reply_check`), then its shape (`validate_reply`, unless it is empty:
/// an empty reply is recorded as the agent having answered). When every specialist has replied the merge runs, and the messages of the
/// next phase that were not issued yet come back (`status` `SEND`, or `NONE` when there are none); `LOCKED` once the contract is.
pub fn cmd_handle(st: &mut Value, agent: &str, reply: &str) -> Result<Value> {
    let agent = py::strip(agent);
    let reply = py::strip(reply);
    if !get(st, "task").is_some_and(py::truthy) {
        return Err(AccordError::new("handle: no task in state — run init --task first"));
    }
    if get(st, "locked_contract").is_some_and(py::truthy) {
        return Ok(json!({"status": "LOCKED", "phase": "locked", "awaiting": [], "messages": {}}));
    }

    let phase_key = infer_phase_key(st)?;
    verify_reply_check(st, &phase_key, agent, reply)?;
    if !reply.is_empty() {
        validate_reply(st, &phase_key, agent, reply)?;
    }

    // record the reply: an empty one too, the agent has answered
    let replies = dict_mut(st, "handle_replies")?;
    let phase_replies = replies.entry(phase_key.clone()).or_insert_with(|| Value::Object(Map::new()));
    let Value::Object(phase_replies) = phase_replies else {
        return Err(AccordError::type_error(format!("'{}' object does not support item assignment", py::type_name(phase_replies))));
    };
    phase_replies.insert(agent.to_string(), Value::String(reply.to_string()));
    let answered: Vec<String> = phase_replies.keys().cloned().collect();
    let pending_after: Vec<String> = strings(st, "specialists")?.into_iter().filter(|a| !answered.contains(a)).collect();

    let extras = if pending_after.is_empty() { run_phase_advance(st, &phase_key)? } else { Map::new() };

    let action = current_action(st)?;
    let mut messages = IndexMap::new();
    if !action.messages.is_empty() {
        // only the messages not issued yet in this phase
        for (recipient, msg) in &action.messages {
            if !is_issued(st, &action.phase, recipient)? {
                messages.insert(recipient.clone(), msg.clone());
            }
        }
        let issued: Vec<String> = messages.keys().cloned().collect();
        mark_issued(st, &action.phase, &issued, Some(&messages))?;
    }

    let mut out = if action.phase == "locked" {
        object(json!({
            "status": "LOCKED", "phase": "locked", "awaiting": [], "messages": {},
            "locked_intent_contract": get(st, "locked_contract").cloned().unwrap_or(Value::Null),
        }))
    } else {
        object(json!({
            "status": if messages.is_empty() { "NONE" } else { "SEND" },
            "phase": action.phase,
            "awaiting": action.recipients,
            "messages": messages,
            "still_waiting_this_phase": pending_after,
        }))
    };
    merge(&mut out, extras);
    Ok(Value::Object(out))
}

// ----------------------------------------------------------------------------------------------------------------------------------
// The commands
// ----------------------------------------------------------------------------------------------------------------------------------

/// `init`: the state of a new session, and the result. With a task, the CONVENE messages are issued and returned.
pub fn cmd_init(args: &InitArgs) -> Result<(Value, Value)> {
    let mut st = blank_state();
    set(&mut st, "specialists", json!(csv(&args.specialists)));
    set(&mut st, "key_terms", json!(csv(&args.key_terms)));
    set(&mut st, "action_tool", json!(args.action_tool));
    let task = py::strip(&args.task);
    if !task.is_empty() {
        set(&mut st, "task", json!(task));
        set(&mut st, "state_id", json!(args.state_id.clone().unwrap_or_else(new_state_id)));
    }
    let mut result = object(json!({
        "specialists": st["specialists"], "key_terms": st["key_terms"], "action_tool": st["action_tool"],
    }));
    merge(&mut result, protocol_control("roster"));
    if !task.is_empty() {
        let action = current_action(&st)?;
        mark_issued(&mut st, &action.phase, &action.recipients, Some(&action.messages))?;
        result.insert("phase".into(), json!(action.phase));
        result.insert("awaiting".into(), json!(action.recipients));
        result.insert("messages".into(), json!(action.messages));
        result.insert("status".into(), json!("SEND"));
        result.insert("hint".into(), json!(HANDLE_HINT));
    }
    Ok((st, Value::Object(result)))
}

/// `roster`: the Phase 0 CONVENE merge.
pub fn cmd_roster(st: &mut Value, responses: &Value) -> Result<Value> {
    merge_roster(st, &py::iter(responses)?)?;
    let mut out = object(json!({"roster": st["roster"], "flagged_items": st["defects"]}));
    merge(&mut out, protocol_control("frame"));
    Ok(Value::Object(out))
}

/// `frame`: the Phase 1 FRAME merge, once per round, with the coverage estimates of the frame.
pub fn cmd_frame(st: &mut Value, responses: &Value) -> Result<Value> {
    let round = ingest_frame_round(&py::iter(responses)?);
    if py::truthy(responses) && !round.saw_atoms {
        return Err(AccordError::new(
            "frame: none of the responses carried an 'atoms' list of {\"text\": ...} objects. Refusing to record an unparseable round.",
        ));
    }
    merge_frame(st, &round)?;
    // The script starts this map from a set of the agents, so its order is Python's hash order; here it is the order of the replies.
    let mut atoms_by_agent: IndexMap<String, Vec<Value>> = round.by_agent.keys().map(|a| (a.clone(), Vec::new())).collect();
    let frame = st["frame"].as_array().cloned().unwrap_or_default();
    for o in py::iter(&st["obs"])? {
        atoms_by_agent.entry(py::str_of(py::item(&o, "agent")?)).or_default().push(py::item(&o, "text")?.clone());
    }
    let support: Vec<i64> = frame.iter().map(|s| s["support"].as_i64().unwrap_or(0)).collect();
    let mm = good_turing_missing_mass(&support);
    let done = st["frame_rounds"].as_f64().is_some_and(|r| r >= MAX_FRAME_ROUNDS as f64);
    let atoms: Vec<Value> = frame
        .iter()
        .map(|s| {
            let mut a = s.as_object().cloned().unwrap_or_default();
            let single = py::eq(&s["support"], &json!(1));
            a.insert("flagged_singleton".into(), json!(single));
            Value::Object(a)
        })
        .collect();
    let mut out = object(json!({
        "atoms": atoms,
        "new_this_round": round.by_agent.values().map(Vec::len).sum::<usize>(),
        "round": st["frame_rounds"],
    }));
    merge(&mut out, protocol_control(if done { "glossary" } else { "frame" }));
    merge(
        &mut out,
        object(json!({
            "atoms_by_agent": atoms_by_agent,
            "truncated": round.truncated,
            "coverage": {
                "good_turing_missing_mass": py::round(mm, 4),
                "chao1": py::round(chao1(&support), 4),
                "coverage": py::round(f64::max(0.0, 1.0 - mm), 4),
            },
        })),
    );
    Ok(Value::Object(out))
}

/// `glossary`: the Phase 2 GROUND merge, also used after CLARIFY. Terms the replies do not speak to are kept.
pub fn cmd_glossary(st: &mut Value, responses: &Value, key_terms: Option<&str>) -> Result<Value> {
    let mut terms = csv(key_terms.unwrap_or(""));
    if terms.is_empty() {
        terms = strings(st, "key_terms")?;
    }
    let tuples = ingest_glossary(&py::iter(responses)?, &terms)?;
    if py::truthy(responses) && tuples.is_empty() {
        let listed = if terms.is_empty() { "no key terms registered".to_string() } else { terms.join(", ") };
        return Err(AccordError::new(format!(
            "glossary: none of the responses carried a definition for a key term ({listed}). Expected a 'units' map, a 'groundings' \
             list, or 'term' with 'extended_definition'. Refusing to overwrite the existing glossary."
        )));
    }
    let glossary = build_glossary(&tuples);
    merge_glossary(st, &glossary.entries)?;
    let next = if glossary.ambiguous.is_empty() { "verify" } else { "glossary" };
    let mut out = object(json!({"glossary": st["glossary"], "ambiguous": glossary.ambiguous}));
    merge(&mut out, protocol_control(next));
    Ok(Value::Object(out))
}

/// `verify`: the Phase 3 VERIFY merge: the owners of the knowledge, the acceptance checks, and the locked contract.
///
/// Without the required knowledge the knowledge map is what the specialists reported, so no gap can appear; without the objective
/// clauses no clause can be unverifiable. The contract says so (`coverage_untested`), and a later `verify` with them clears it.
pub fn cmd_verify(
    st: &mut Value,
    responses: &Value,
    required_knowledge: Option<&Value>,
    objective_clauses: Option<&Value>,
) -> Result<Value> {
    let empty = Value::Array(Vec::new());
    let required = required_knowledge.unwrap_or(&empty);
    let clauses = objective_clauses.unwrap_or(&empty);
    let replies = ingest_verify(&py::iter(responses)?)?;
    let mut warnings = Vec::new();
    if !py::truthy(required) {
        warnings.push(json!("coverage_untested: verify ran without --required-knowledge, so knowledge gaps could not be detected"));
    }
    if !py::truthy(clauses) {
        warnings.push(json!("coverage_untested: verify ran without --objective-clauses, so unverifiable objectives could not be detected"));
    }
    set(st, "coverage_warnings", Value::Array(warnings));
    let universe = universe_of(required, &replies.all_holds)?;
    let (owned, uncovered) = assign_holdings(&universe, &replies.covers)?;
    set(st, "owned", owned_value(owned));
    set(st, "uncovered", Value::Array(uncovered));
    let mut held_by: IndexMap<&str, Vec<&str>> = IndexMap::new();
    for (agent, items) in &replies.covers {
        for item in items {
            held_by.entry(item).or_default().push(agent);
        }
    }
    let checks = assign_checks(&replies.checks, clauses)?;
    set(st, "checks", Value::Array(checks.clone()));
    let mut covered = std::collections::BTreeSet::new();
    for c in &checks {
        covered.extend(covered_clauses(c, clauses)?);
    }
    let mut knowledge_map = Vec::new();
    for i in &universe {
        let holders = i.as_str().and_then(|s| held_by.get(s)).cloned().unwrap_or_default();
        knowledge_map.push(json!({"item": i, "held_by": holders, "gap": py::contains(&st["uncovered"], i)?}));
    }
    let unverifiable: Vec<Value> =
        py::iter(clauses)?.into_iter().enumerate().filter(|(i, _)| !covered.contains(i)).map(|(_, c)| c).collect();
    let gaps = st["uncovered"].clone();
    let contract = store_contract(st)?;
    Ok(json!({
        "knowledge_map": knowledge_map,
        "checks": checks,
        "gaps": gaps,
        "unverifiable_clauses": unverifiable,
        "locked_intent_contract": contract,
        "next": next_step(st)?,
    }))
}

/// What comes after VERIFY, in the payload of `verify`: flagged items do not end the protocol, the execution messages come next.
pub fn next_step(st: &Value) -> Result<Value> {
    let tool = py::item(st, "action_tool")?;
    let tool = if py::truthy(tool) { py::str_of(tool) } else { "the action tool".to_string() };
    Ok(json!({
        "command": "execution-message",
        "agents": py::iter(py::item(st, "specialists")?)?,
        "note": format!(
            "flagged_items do not end the protocol: render one execution message per agent, delegate data.message unchanged, then \
             call {tool} once the replies arrive"
        ),
    }))
}

/// The contract assembled from the state, stored as the locked contract.
pub fn store_contract(st: &mut Value) -> Result<Value> {
    let contract = assemble_lock(st)?;
    set(st, "locked_contract", contract.clone());
    Ok(contract)
}

/// `execution-message`: the EXECUTION_REQUEST of an agent, once the contract is INTENT-LOCKED. The assignment is stripped.
pub fn cmd_execution_message(st: &Value, agent: &str, assignment: &str) -> Result<Value> {
    let contract = match get(st, "locked_contract") {
        Some(c) if py::truthy(c) => c,
        _ => return Err(AccordError::new("execution-message: no locked contract exists")),
    };
    let status = py::get(contract, "status")?;
    if !status.is_some_and(|s| s == "INTENT-LOCKED") {
        return Err(AccordError::new("execution-message: contract is not INTENT-LOCKED"));
    }
    Ok(json!({"agent": agent, "message": render_execution_message(agent, contract, py::strip(assignment))}))
}

/// `status`: where the session stands.
pub fn cmd_status(st: &Value) -> Result<Value> {
    let locked = py::truthy(py::item(st, "owned")?) || py::truthy(py::item(st, "checks")?);
    Ok(json!({
        "specialists": py::item(st, "specialists")?, "key_terms": py::item(st, "key_terms")?,
        "action_tool": py::item(st, "action_tool")?, "roster": len_of(st, "roster")?,
        "frame_atoms": len_of(st, "frame")?, "frame_rounds": py::item(st, "frame_rounds")?,
        "glossary_terms": len_of(st, "glossary")?, "checks": len_of(st, "checks")?,
        "locked": locked,
    }))
}
