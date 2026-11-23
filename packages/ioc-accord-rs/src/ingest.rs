//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! Reading the specialists' replies: raw model text or JSON, turned into what each phase merges.
//!
//! The readers are lenient on purpose: a reply that is not a JSON object is skipped, a field of the wrong type is read with Python's
//! rules (`str()` of a number, the characters of a string), and only what Python could not do at all fails, with Python's error.

use indexmap::IndexMap;
use serde_json::{Map, Value};

use crate::errors::Result;
use crate::protocol::MAX_FRAME_ATOMS;
use crate::py;
use crate::pyjson::loads;

/// A reply without the Markdown fence around it: an opening "```" or "```json" and the whitespace after it, and a closing "```".
pub fn strip_fences(text: &str) -> String {
    // re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    let mut s = py::strip(text);
    if let Some(rest) = s.strip_prefix("```") {
        let rest = rest.strip_prefix("json").unwrap_or(rest);
        s = rest.trim_start_matches(py::is_space);
    }
    // re.sub(r"\n?```\s*$", "", stripped.strip()): the text is stripped, so the match can only be its last characters
    let s = py::strip(s);
    let s = s.strip_suffix("\n```").or_else(|| s.strip_suffix("```")).unwrap_or(s);
    s.to_string()
}

/// `re.search(r"\{.*\}", text, re.DOTALL)`: from the first `{` to the last `}` after it.
fn braced(text: &str) -> Option<&str> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    (end > start).then(|| &text[start..=end])
}

/// The replies that are JSON objects. A string is read as JSON once its fence is stripped, or else from its first `{` to its last `}`,
/// which tolerates prose around the object; anything else that is not an object is skipped.
pub fn parse_responses(raw: &[Value]) -> Vec<Map<String, Value>> {
    let mut parsed = Vec::new();
    for r in raw {
        let value = match r {
            Value::String(s) => {
                let text = strip_fences(s);
                match loads(&text) {
                    Ok(v) => Some(v),
                    Err(_) => braced(&text).and_then(|t| loads(t).ok()),
                }
            }
            other => Some(other.clone()),
        };
        if let Some(Value::Object(o)) = value {
            parsed.push(o);
        }
    }
    parsed
}

/// The agent cards of the CONVENE replies, in the shape of the roster: `identity`, `roles`, `capabilities`, `accessScopes`,
/// `holdingsSummary`. A reply that names no agent is skipped.
pub fn ingest_roster(raw: &[Value]) -> Result<Vec<Value>> {
    let mut cards = Vec::new();
    for d in parse_responses(raw) {
        let identity = py::str_or([d.get("agent"), d.get("identity")]);
        let identity = py::strip(&identity);
        if identity.is_empty() {
            continue;
        }
        let mut card = Map::new();
        card.insert("identity".into(), Value::String(identity.to_string()));
        card.insert("roles".into(), Value::Array(py::list_or_empty(d.get("roles"))?));
        card.insert("capabilities".into(), Value::Array(py::list_or_empty(d.get("capabilities"))?));
        card.insert("accessScopes".into(), Value::Array(py::list_or_empty(py::or([d.get("access_scopes"), d.get("accessScopes")]))?));
        card.insert("holdingsSummary".into(), Value::String(py::str_or([d.get("holdings_summary"), d.get("holdingsSummary")])));
        cards.push(Value::Object(card));
    }
    Ok(cards)
}

/// A FRAME round, read.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct FrameRound {
    /// The latest atoms of each agent (`{"text", "agent", "kind"}`), the first `MAX_FRAME_ATOMS` of them, in the order the agents
    /// first answered.
    pub by_agent: IndexMap<String, Vec<Value>>,
    /// The agents whose list was cut: `{"agent", "submitted", "kept", "dropped"}`.
    pub truncated: Vec<Value>,
    /// Whether any reply carried an `atoms` list.
    pub saw_atoms: bool,
}

/// The atoms of a FRAME round, per agent. A reply with an `atoms` list adds to that agent's atoms of this round; the merge then
/// replaces the agent's atoms of the earlier rounds with them, so an empty list withdraws every atom.
pub fn ingest_frame_round(raw: &[Value]) -> FrameRound {
    let mut round = FrameRound::default();
    let mut submitted: IndexMap<String, usize> = IndexMap::new();
    for d in parse_responses(raw) {
        let Some(Value::Array(atoms)) = d.get("atoms") else { continue };
        round.saw_atoms = true;
        let agent = py::strip(&py::str_or([d.get("agent")])).to_string();
        let mut parsed = Vec::new();
        for atom in atoms {
            let Value::Object(atom) = atom else { continue };
            let text = py::strip(&py::str_or([atom.get("text")])).to_string();
            if text.is_empty() {
                continue;
            }
            let mut obs = Map::new();
            obs.insert("text".into(), Value::String(text));
            obs.insert("agent".into(), Value::String(agent.clone()));
            obs.insert("kind".into(), Value::String(py::or([atom.get("type")]).map(py::str_of).unwrap_or_else(|| "term".into())));
            parsed.push(Value::Object(obs));
        }
        *submitted.entry(agent.clone()).or_default() += parsed.len();
        round.by_agent.entry(agent).or_default().extend(parsed);
    }
    for (agent, atoms) in round.by_agent.iter_mut() {
        let total = submitted.get(agent).copied().unwrap_or(0);
        if total > MAX_FRAME_ATOMS {
            let mut t = Map::new();
            t.insert("agent".into(), Value::String(agent.clone()));
            t.insert("submitted".into(), total.into());
            t.insert("kept".into(), MAX_FRAME_ATOMS.into());
            t.insert("dropped".into(), (total - MAX_FRAME_ATOMS).into());
            round.truncated.push(Value::Object(t));
            atoms.truncate(MAX_FRAME_ATOMS);
        }
    }
    round
}

/// A definition of a key term by an agent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Grounding {
    pub term: String,
    pub unit: String,
    pub agent: String,
}

/// The definitions of the key terms in GROUND and CLARIFY replies. A reply can give them as a `units` map, a `groundings` list of
/// `{"term", "unit"}`, or, in CLARIFY, as one `term` with an `extended_definition` (or `unit`, or `definition`). An agent defines a term
/// once: the `units` map first, then the first grounding.
pub fn ingest_glossary(raw: &[Value], key_terms: &[String]) -> Result<Vec<Grounding>> {
    let mut out: Vec<Grounding> = Vec::new();
    for d in parse_responses(raw) {
        let agent = py::strip(&py::str_or([d.get("agent")])).to_string();
        let empty = Value::Object(Map::new());
        let units = py::or([d.get("units")]).unwrap_or(&empty);
        for term in key_terms {
            let unit = py::str_or([py::get(units, term)?]);
            let unit = py::strip(&unit);
            if !unit.is_empty() {
                out.push(Grounding { term: term.clone(), unit: unit.to_string(), agent: agent.clone() });
            }
        }
        let body = Value::Object(d.clone());
        let mut groundings = py::list_or_empty(d.get("groundings"))?;
        groundings.push(body);
        for g in &groundings {
            let Value::Object(g) = g else { continue };
            let term = py::strip(&py::str_or([g.get("term")])).to_string();
            let unit = py::str_or([g.get("unit"), g.get("extended_definition"), g.get("definition")]);
            let unit = py::strip(&unit);
            if key_terms.contains(&term) && !unit.is_empty() && !out.iter().any(|t| t.term == term && t.agent == agent) {
                out.push(Grounding { term, unit: unit.to_string(), agent: agent.clone() });
            }
        }
    }
    Ok(out)
}

/// VERIFY replies, read.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct VerifyReplies {
    /// What each agent holds (its latest reply), blanks dropped, repeats kept.
    pub covers: IndexMap<String, Vec<String>>,
    /// The proposed checks, each the object as the agent wrote it with `agent` set to the agent.
    pub checks: Vec<Value>,
    /// Every item held by some agent, once, in the order they first appear.
    pub all_holds: Vec<String>,
}

/// The holdings and proposed checks of VERIFY replies. A reply that names no agent is skipped.
pub fn ingest_verify(raw: &[Value]) -> Result<VerifyReplies> {
    let mut out = VerifyReplies::default();
    for d in parse_responses(raw) {
        let agent = py::strip(&py::str_or([d.get("agent")])).to_string();
        if agent.is_empty() {
            continue;
        }
        let items: Vec<String> =
            py::list_or_empty(d.get("holds"))?.iter().map(|i| py::strip(&py::str_of(i)).to_string()).filter(|i| !i.is_empty()).collect();
        for i in &items {
            if !out.all_holds.contains(i) {
                out.all_holds.push(i.clone());
            }
        }
        out.covers.insert(agent.clone(), items);
        for c in py::list_or_empty(py::or([d.get("checks"), d.get("proposed_checks")]))? {
            if let Value::Object(mut c) = c {
                c.insert("agent".into(), Value::String(agent.clone()));
                out.checks.push(Value::Object(c));
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn fences_and_prose_around_an_object_are_tolerated() {
        assert_eq!(strip_fences("  ```json\n{\"a\": 1}\n```  "), "{\"a\": 1}");
        assert_eq!(strip_fences("```\n[1]```"), "[1]");
        assert_eq!(strip_fences("x````"), "x`");
        let raw = vec![
            json!("```json\n{\"agent\": \"a\"}\n```"),
            json!("Sure! Here it is: {\"agent\": \"b\", \"x\": {\"y\": 1}} hope it helps"),
            json!("no json here"),
            json!("} {"),
            json!({"agent": "c"}),
            json!([1, 2]),
            json!("[1]"),
        ];
        let agents: Vec<_> = parse_responses(&raw).iter().map(|d| d["agent"].clone()).collect();
        assert_eq!(agents, vec![json!("a"), json!("b"), json!("c")]);
    }

    #[test]
    fn roster_cards_take_either_spelling() {
        let raw = vec![
            json!({"identity": " x ", "roles": ["r"], "accessScopes": ["s"], "holdingsSummary": 5}),
            json!({"agent": "y", "roles": "ab", "capabilities": {"k": 1}}),
            json!({"roles": ["nameless"]}),
        ];
        let cards = ingest_roster(&raw).unwrap();
        assert_eq!(cards.len(), 2);
        assert_eq!(cards[0], json!({"identity": "x", "roles": ["r"], "capabilities": [], "accessScopes": ["s"], "holdingsSummary": "5"}));
        assert_eq!(cards[1]["roles"], json!(["a", "b"]));
        assert_eq!(cards[1]["capabilities"], json!(["k"]));
        let err = ingest_roster(&[json!({"agent": "z", "roles": 3})]).unwrap_err();
        assert_eq!(err.message(), "TypeError: 'int' object is not iterable");
    }

    #[test]
    fn a_frame_round_keeps_the_first_atoms_of_each_agent() {
        let atoms: Vec<Value> = (0..8).map(|i| json!({"text": format!("t{i}"), "type": "risk"})).collect();
        let raw = vec![json!({"agent": "a", "atoms": atoms}), json!({"agent": "b", "atoms": [{"text": " "}, "x", {"text": "u"}]})];
        let round = ingest_frame_round(&raw);
        assert!(round.saw_atoms);
        assert_eq!(round.by_agent["a"].len(), MAX_FRAME_ATOMS);
        assert_eq!(round.by_agent["b"], vec![json!({"text": "u", "agent": "b", "kind": "term"})]);
        assert_eq!(round.truncated, vec![json!({"agent": "a", "submitted": 8, "kept": 6, "dropped": 2})]);
        assert!(!ingest_frame_round(&[json!({"agent": "a"})]).saw_atoms);
    }

    #[test]
    fn glossary_reads_units_groundings_and_clarify_replies() {
        let terms = vec!["latency".to_string(), "error_rate".to_string()];
        let raw = vec![
            json!({"agent": "a", "units": {"latency": "ms"}, "groundings": [{"term": "latency", "unit": "s"}, {"term": "error_rate", "unit": "%"}]}),
            json!({"agent": "b", "term": "latency", "extended_definition": "p95 in ms"}),
        ];
        let got = ingest_glossary(&raw, &terms).unwrap();
        let triples: Vec<_> = got.iter().map(|g| (g.term.as_str(), g.unit.as_str(), g.agent.as_str())).collect();
        assert_eq!(triples, vec![("latency", "ms", "a"), ("error_rate", "%", "a"), ("latency", "p95 in ms", "b")]);
        let err = ingest_glossary(&[json!({"agent": "a", "units": ["x"]})], &terms).unwrap_err();
        assert_eq!(err.message(), "AttributeError: 'list' object has no attribute 'get'");
        assert!(ingest_glossary(&[json!({"agent": "a", "units": ["x"]})], &[]).unwrap().is_empty());
    }

    #[test]
    fn verify_replies_collect_holdings_and_checks() {
        let raw = vec![
            json!({"agent": "a", "holds": ["k1", " ", "k2", "k1", 7], "checks": [{"criterion": "c", "agent": "spoof"}, "x"]}),
            json!({"agent": "b", "holds": ["k2", "k3"], "proposed_checks": [{"criterion": "d"}]}),
            json!({"holds": ["nameless"]}),
        ];
        let v = ingest_verify(&raw).unwrap();
        assert_eq!(v.covers["a"], vec!["k1", "k2", "k1", "7"]);
        assert_eq!(v.all_holds, vec!["k1", "k2", "7", "k3"]);
        assert_eq!(v.checks, vec![json!({"criterion": "c", "agent": "a"}), json!({"criterion": "d", "agent": "b"})]);
    }
}
