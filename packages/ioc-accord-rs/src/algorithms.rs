//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The deterministic merges of the phases: the roster, the clusters of the frame and their coverage, the glossary, the owners of the
//! knowledge, the acceptance checks, and the contract they make.

use std::collections::{BTreeSet, HashSet};

use indexmap::IndexMap;
use serde_json::{Map, Value};

use crate::errors::{AccordError, Result};
use crate::ingest::Grounding;
use crate::protocol::{DEFAULT_VERIFIER_ID, GROUP_THRESHOLD, MAX_UNCOVERED_HOLDINGS, SIM_CLARIFY, SIM_MERGE};
use crate::py;
use crate::similarity::similarity;

/// The roster: the first card of each identity.
pub fn union_roster(cards: Vec<Value>) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for c in cards {
        if !out.iter().any(|o| o.get("identity") == c.get("identity")) {
            out.push(c);
        }
    }
    out
}

/// The values once each, in the order they first appear, with Python's equality (`dict.fromkeys`).
fn unique(values: impl IntoIterator<Item = Value>) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for v in values {
        if !out.iter().any(|o| py::eq(o, &v)) {
            out.push(v);
        }
    }
    out
}

/// The species of the frame: the observations (`{"text", "agent", "kind"}`) clustered by similarity to the first observation of each
/// cluster, in order. A species is `{"id": "s<n>", "text", "type", "support", "agents"}`, its support the number of distinct agents.
pub fn group_atoms(observations: &[Value]) -> Result<Vec<Value>> {
    let mut clusters: Vec<Vec<&Value>> = Vec::new();
    for obs in observations {
        let text = py::as_str(py::item(obs, "text")?, "lower")?;
        let mut placed = false;
        for cl in clusters.iter_mut() {
            let first = py::as_str(py::item(cl[0], "text")?, "lower")?;
            if similarity(text, first) >= GROUP_THRESHOLD {
                cl.push(obs);
                placed = true;
                break;
            }
        }
        if !placed {
            clusters.push(vec![obs]);
        }
    }
    let mut species = Vec::new();
    for (i, cl) in clusters.iter().enumerate() {
        let mut agents = Vec::new();
        for o in cl {
            let agent = py::item(o, "agent")?;
            if !py::hashable(agent) {
                let t = py::type_name(agent);
                return Err(AccordError::type_error(format!("cannot use '{t}' as a dict key (unhashable type: '{t}')")));
            }
            agents.push(agent.clone());
        }
        let agents = unique(agents);
        let mut s = Map::new();
        s.insert("id".into(), Value::String(format!("s{}", i + 1)));
        s.insert("text".into(), py::item(cl[0], "text")?.clone());
        s.insert("type".into(), py::item(cl[0], "kind")?.clone());
        s.insert("support".into(), agents.len().into());
        s.insert("agents".into(), Value::Array(agents));
        species.push(Value::Object(s));
    }
    Ok(species)
}

/// Good-Turing estimate of the probability mass of the atoms nobody has named yet: the share of singletons in the observations; 1 when
/// there are none.
pub fn good_turing_missing_mass(support: &[i64]) -> f64 {
    let n: i64 = support.iter().sum();
    if n <= 0 {
        return 1.0;
    }
    support.iter().filter(|s| **s == 1).count() as f64 / n as f64
}

/// Chao1 estimate of the number of atoms, the unseen ones included: `S + f1² / 2f2`, or `S + f1(f1 - 1) / 2` without doubletons.
pub fn chao1(support: &[i64]) -> f64 {
    let s_obs = support.len() as i64;
    let f1 = support.iter().filter(|s| **s == 1).count() as i64;
    let f2 = support.iter().filter(|s| **s == 2).count() as i64;
    if s_obs == 0 {
        return 0.0;
    }
    let extra = if f2 != 0 { (f1 * f1) as f64 / (2.0 * f2 as f64) } else { (f1 * (f1 - 1).max(0)) as f64 / 2.0 };
    s_obs as f64 + extra
}

/// The glossary, and the pairs of definitions to CLARIFY.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Glossary {
    /// Per term, in the order the terms first appear: `{"term", "status": "agreed", "definition", "agents"}` when its definitions are
    /// one cluster, `{"term", "status": "flagged", "variants": [{"agent", "definition"}]}` when they are several.
    pub entries: Vec<Value>,
    /// The pairs of definitions from different clusters whose similarity is in `[SIM_CLARIFY, SIM_MERGE)`:
    /// `{"term", "agents", "units", "sim"}`, `sim` rounded to 3 places.
    pub ambiguous: Vec<Value>,
}

/// The glossary of the definitions: per term, the definitions clustered by similarity to the first of each cluster.
pub fn build_glossary(terms: &[Grounding]) -> Glossary {
    let mut by_term: IndexMap<&str, Vec<(&str, &str)>> = IndexMap::new();
    for g in terms {
        by_term.entry(&g.term).or_default().push((&g.unit, &g.agent));
    }
    let mut out = Glossary::default();
    for (term, items) in by_term {
        let mut clusters: Vec<Vec<usize>> = Vec::new();
        for (i, (unit, _)) in items.iter().enumerate() {
            match clusters.iter_mut().find(|cl| similarity(unit, items[cl[0]].0) >= SIM_MERGE) {
                Some(cl) => cl.push(i),
                None => clusters.push(vec![i]),
            }
        }
        let mut e = Map::new();
        e.insert("term".into(), Value::String(term.to_string()));
        if clusters.len() == 1 {
            let agents = unique(items.iter().map(|(_, a)| Value::String(a.to_string())));
            e.insert("status".into(), "agreed".into());
            e.insert("definition".into(), Value::String(items[0].0.to_string()));
            e.insert("agents".into(), Value::Array(agents));
            out.entries.push(Value::Object(e));
            continue;
        }
        e.insert("status".into(), "flagged".into());
        let variants = clusters
            .iter()
            .flatten()
            .map(|&i| {
                let mut v = Map::new();
                v.insert("agent".into(), Value::String(items[i].1.to_string()));
                v.insert("definition".into(), Value::String(items[i].0.to_string()));
                Value::Object(v)
            })
            .collect();
        e.insert("variants".into(), Value::Array(variants));
        out.entries.push(Value::Object(e));
        for x in 0..clusters.len() {
            for y in x + 1..clusters.len() {
                for &i in &clusters[x] {
                    for &j in &clusters[y] {
                        let s = similarity(items[i].0, items[j].0);
                        if (SIM_CLARIFY..SIM_MERGE).contains(&s) {
                            let mut p = Map::new();
                            p.insert("term".into(), Value::String(term.to_string()));
                            p.insert("agents".into(), serde_json::json!([items[i].1, items[j].1]));
                            p.insert("units".into(), serde_json::json!([items[i].0, items[j].0]));
                            p.insert("sim".into(), py::round(s, 3).into());
                            out.ambiguous.push(Value::Object(p));
                        }
                    }
                }
            }
        }
    }
    out
}

/// Who owns what, and what nobody owns.
pub type Ownership = (IndexMap<String, Vec<Value>>, Vec<Value>);

/// Greedy set cover of the universe: again and again, the agent that holds the most items still uncovered (the first of them on a tie)
/// owns those. Returns every agent's items (an agent's repeated items count, and are owned, twice) and the items left uncovered, in the
/// order of the universe.
///
/// The universe is a list of JSON values, as the caller supplied it; only a string can be held, so another value stays uncovered, and a
/// list or an object is refused as Python's `set()` refuses it.
pub fn greedy_set_cover(universe: &[Value], covers: &IndexMap<String, Vec<String>>) -> Result<Ownership> {
    if let Some(v) = universe.iter().find(|v| !py::hashable(v)) {
        let t = py::type_name(v);
        return Err(AccordError::type_error(format!("cannot use '{t}' as a set element (unhashable type: '{t}')")));
    }
    let mut remaining: HashSet<&str> = universe.iter().filter_map(Value::as_str).collect();
    let others_remain = universe.iter().any(|v| !v.is_string());
    let mut owned: IndexMap<String, Vec<Value>> = covers.keys().map(|a| (a.clone(), Vec::new())).collect();
    while !remaining.is_empty() || others_remain {
        let (mut best, mut gain): (&str, Vec<&str>) = ("", Vec::new());
        for (agent, items) in covers {
            let g: Vec<&str> = items.iter().map(String::as_str).filter(|i| remaining.contains(i)).collect();
            if g.len() > gain.len() {
                (best, gain) = (agent, g);
            }
        }
        if best.is_empty() {
            break;
        }
        for i in &gain {
            remaining.remove(i);
        }
        owned[best].extend(gain.into_iter().map(|i| Value::String(i.to_string())));
    }
    let uncovered = universe.iter().filter(|v| v.as_str().is_none_or(|s| remaining.contains(s))).cloned().collect();
    Ok((owned, uncovered))
}

/// The owners of the knowledge: the greedy set cover by every agent but the verifier, which never owns knowledge. Of the uncovered
/// items, up to `MAX_UNCOVERED_HOLDINGS` stay uncovered; the ones before them go to the first agent that can own knowledge.
pub fn assign_holdings(universe: &[Value], covers: &IndexMap<String, Vec<String>>) -> Result<Ownership> {
    let filtered: IndexMap<String, Vec<String>> =
        covers.iter().filter(|(a, _)| py::strip(a).to_lowercase() != DEFAULT_VERIFIER_ID).map(|(a, v)| (a.clone(), v.clone())).collect();
    let (mut owned, mut uncovered) = greedy_set_cover(universe, &filtered)?;
    for a in covers.keys() {
        owned.entry(a.clone()).or_default();
    }
    if let Some(first) = filtered.keys().next() {
        while uncovered.len() > MAX_UNCOVERED_HOLDINGS {
            let item = uncovered.remove(0);
            owned[first].push(item);
        }
    }
    Ok((owned, uncovered))
}

/// The value at an index of the clauses, as Python indexes them: a list's item, a string's character; a dict has no such key.
fn clause_at(clauses: &Value, index: usize) -> Result<Value> {
    match clauses {
        Value::Array(a) => Ok(a[index].clone()),
        Value::String(s) => Ok(Value::String(s.chars().nth(index).map(String::from).unwrap_or_default())),
        Value::Object(_) => Err(AccordError::python("KeyError", index)),
        other => Err(AccordError::type_error(format!("'{}' object is not subscriptable", py::type_name(other)))),
    }
}

/// The clauses (by index) a check covers: a clause listed in its `covers`, or contained, stripped and lowercased, in one of them.
pub fn covered_clauses(check: &Value, clauses: &Value) -> Result<BTreeSet<usize>> {
    let empty = Value::Array(Vec::new());
    let covers = py::get(check, "covers")?.unwrap_or(&empty);
    let mut out = BTreeSet::new();
    for (i, cl) in py::iter(clauses)?.iter().enumerate() {
        let mut hit = py::contains(covers, cl)?;
        if !hit {
            for cv in py::iter(covers)? {
                let needle = py::strip_str(cl)?.to_lowercase();
                let hay = py::strip_str(&cv)?.to_lowercase();
                if hay.contains(&needle) {
                    hit = true;
                    break;
                }
            }
        }
        if hit {
            out.insert(i);
        }
    }
    Ok(out)
}

/// The acceptance checks: a greedy set cover of the objective clauses by the proposed checks, the first check that covers the most
/// clauses still uncovered each time. A check is `{"atom", "criterion", "assigned_to", "covers"}`, its atom the first clause it
/// discharges.
///
/// `clauses` is the value as the caller supplied it: a list of clause texts, iterated with Python's rules otherwise.
pub fn assign_checks(checks_raw: &[Value], clauses: &Value) -> Result<Vec<Value>> {
    let mut uncovered: BTreeSet<usize> = (0..py::len(clauses)?).collect();
    let mut remaining: Vec<&Value> = checks_raw.iter().collect();
    let mut selected = Vec::new();
    while !uncovered.is_empty() && !remaining.is_empty() {
        // max(): the first of the best
        let mut best: Option<(usize, usize)> = None;
        for (idx, c) in remaining.iter().enumerate() {
            let n = covered_clauses(c, clauses)?.intersection(&uncovered).count();
            if best.is_none_or(|(_, m)| n > m) {
                best = Some((idx, n));
            }
        }
        let Some((idx, _)) = best else { break };
        let check = remaining[idx];
        let gain: BTreeSet<usize> = covered_clauses(check, clauses)?.intersection(&uncovered).copied().collect();
        let Some(&first) = gain.iter().next() else { break };
        uncovered.retain(|i| !gain.contains(i));
        remaining.remove(idx);
        let get = |key: &str, default: Value| -> Result<Value> { Ok(py::get(check, key)?.cloned().unwrap_or(default)) };
        let mut s = Map::new();
        // the atom is the objective clause the check discharges, not a prefix of its criterion
        s.insert("atom".into(), clause_at(clauses, first)?);
        s.insert("criterion".into(), get("criterion", Value::String(String::new()))?);
        s.insert("assigned_to".into(), get("agent", Value::String(String::new()))?);
        s.insert("covers".into(), get("covers", Value::Array(Vec::new()))?);
        selected.push(Value::Object(s));
    }
    Ok(selected)
}

fn list<'a>(st: &'a Value, key: &str) -> Result<&'a Vec<Value>> {
    match py::item(st, key)? {
        Value::Array(a) => Ok(a),
        other => Err(AccordError::type_error(format!("'{}' object is not a list", py::type_name(other)))),
    }
}

/// The locked intent contract of a state: the roster, the problem frame, the glossary, the verifiable objective and every flagged item
/// (the defects recorded so far, a fragile frame, the flagged terms, the uncovered knowledge and the coverage warnings).
pub fn assemble_lock(st: &Value) -> Result<Value> {
    let frame = list(st, "frame")?;
    let glossary = py::item(st, "glossary")?;
    let mut defects: Vec<Value> = py::iter(py::item(st, "defects")?)?;
    let mut singletons = true;
    for sp in frame {
        singletons &= py::eq(py::item(sp, "support")?, &Value::from(1));
    }
    if frame.len() > 3 && singletons {
        defects.push(Value::String(format!("fragile_frame: all {} atoms are unverified singletons", frame.len())));
    }
    for g in py::iter(glossary)? {
        if py::eq(py::item(&g, "status")?, &Value::from("flagged")) {
            defects.push(Value::String(format!("ambiguous_term: {}", py::str_of(py::item(&g, "term")?))));
        }
    }
    let uncovered = py::item(st, "uncovered")?;
    for u in py::iter(uncovered)? {
        defects.push(Value::String(format!("uncovered_atom: {}", py::str_of(&u))));
    }
    defects.extend(py::list_or_empty(st.get("coverage_warnings"))?);

    let mut owners = Map::new();
    let owned = py::item(st, "owned")?;
    let Value::Object(owned) = owned else {
        return Err(py::attribute_error(owned, "items"));
    };
    for (agent, items) in owned {
        for item in py::iter(items)? {
            if !py::hashable(&item) {
                let t = py::type_name(&item);
                return Err(AccordError::type_error(format!("cannot use '{t}' as a dict key (unhashable type: '{t}')")));
            }
            owners.insert(py::key_text(&item), Value::String(agent.clone()));
        }
    }

    let mut problem_frame = Vec::new();
    for s in frame {
        let mut p = Map::new();
        p.insert("id".into(), py::item(s, "id")?.clone());
        p.insert("text".into(), py::item(s, "text")?.clone());
        let support = py::item(s, "support")?;
        p.insert("support".into(), support.clone());
        p.insert("flagged_singleton".into(), Value::Bool(py::eq(support, &Value::from(1))));
        problem_frame.push(Value::Object(p));
    }

    let mut checks = Vec::new();
    for c in py::iter(py::item(st, "checks")?)? {
        let atom = py::get(&c, "atom")?;
        if !(atom.is_some_and(py::truthy) && py::get(&c, "criterion")?.is_some_and(py::truthy)) {
            continue;
        }
        let mut k = Map::new();
        k.insert("atom".into(), py::item(&c, "atom")?.clone());
        k.insert("criterion".into(), py::item(&c, "criterion")?.clone());
        k.insert("assigned_to".into(), py::item(&c, "assigned_to")?.clone());
        checks.push(Value::Object(k));
    }

    let mut objective = Map::new();
    objective.insert("owners".into(), Value::Object(owners));
    objective.insert("uncovered".into(), uncovered.clone());
    objective.insert("checks".into(), Value::Array(checks));

    let mut contract = Map::new();
    contract.insert("status".into(), "INTENT-LOCKED".into());
    contract.insert("roster".into(), py::item(st, "roster")?.clone());
    contract.insert("problem_frame".into(), Value::Array(problem_frame));
    contract.insert("glossary".into(), glossary.clone());
    contract.insert("verifiable_objective".into(), Value::Object(objective));
    contract.insert("flagged_items".into(), Value::Array(defects));
    Ok(Value::Object(contract))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn obs(text: &str, agent: &str) -> Value {
        json!({"text": text, "agent": agent, "kind": "term"})
    }

    #[test]
    fn the_roster_keeps_the_first_card_of_an_identity() {
        let r = union_roster(vec![json!({"identity": "a", "n": 1}), json!({"identity": "b"}), json!({"identity": "a", "n": 2})]);
        assert_eq!(r, vec![json!({"identity": "a", "n": 1}), json!({"identity": "b"})]);
    }

    #[test]
    fn atoms_cluster_on_the_first_of_each_cluster() {
        let o = vec![obs("error rate", "a"), obs("Error-rate!", "b"), obs("latency p95", "a"), obs("error rate", "a")];
        let s = group_atoms(&o).unwrap();
        assert_eq!(s.len(), 2);
        assert_eq!(s[0], json!({"id": "s1", "text": "error rate", "type": "term", "support": 2, "agents": ["a", "b"]}));
        assert_eq!(s[1]["support"], json!(1));
    }

    #[test]
    fn coverage_estimates() {
        assert_eq!(good_turing_missing_mass(&[]), 1.0);
        assert_eq!(good_turing_missing_mass(&[1, 1, 2]), 0.5);
        assert_eq!(chao1(&[]), 0.0);
        assert_eq!(chao1(&[1, 1, 2]), 3.0 + 4.0 / 2.0);
        assert_eq!(chao1(&[1, 1, 1]), 3.0 + 3.0);
        assert_eq!(chao1(&[3]), 1.0);
    }

    #[test]
    fn glossary_agrees_flags_and_finds_near_matches() {
        let g = |t: &str, u: &str, a: &str| Grounding { term: t.into(), unit: u.into(), agent: a.into() };
        let out = build_glossary(&[
            g("latency", "milliseconds", "a"),
            g("latency", "milliseconds", "b"),
            g("rate", "errors per minute", "a"),
            g("rate", "errors per second", "b"),
            g("rate", "zzz", "c"),
        ]);
        assert_eq!(out.entries[0], json!({"term": "latency", "status": "agreed", "definition": "milliseconds", "agents": ["a", "b"]}));
        assert_eq!(out.entries[1]["status"], json!("flagged"));
        let s = similarity("errors per minute", "errors per second");
        if (SIM_CLARIFY..SIM_MERGE).contains(&s) {
            assert_eq!(out.ambiguous[0]["sim"], json!(py::round(s, 3)));
        }
    }

    #[test]
    fn set_cover_takes_the_largest_gain_first() {
        let covers: IndexMap<String, Vec<String>> =
            [("a".into(), vec!["x".into()]), ("b".into(), vec!["x".into(), "y".into()])].into_iter().collect();
        let (owned, uncovered) = greedy_set_cover(&[json!("x"), json!("y"), json!("z"), json!(3)], &covers).unwrap();
        assert_eq!(owned["a"], Vec::<Value>::new());
        assert_eq!(owned["b"], vec![json!("x"), json!("y")]);
        assert_eq!(uncovered, vec![json!("z"), json!(3)]);
        assert!(greedy_set_cover(&[json!([1])], &covers).is_err());
    }

    #[test]
    fn the_verifier_owns_nothing_and_extra_gaps_go_to_the_first_agent() {
        let covers: IndexMap<String, Vec<String>> =
            [(" Verifier ".into(), vec!["x".into()]), ("a".into(), vec![]), ("b".into(), vec!["y".into()])].into_iter().collect();
        let (owned, uncovered) = assign_holdings(&[json!("x"), json!("y"), json!("z"), json!("w")], &covers).unwrap();
        assert_eq!(owned.keys().collect::<Vec<_>>(), vec!["a", "b", " Verifier "]);
        assert_eq!(owned["a"], vec![json!("x"), json!("z")]);
        assert_eq!(uncovered, vec![json!("w")]);
    }

    #[test]
    fn checks_cover_clauses_greedily() {
        let clauses = json!(["p95 under 200ms", "error rate below 1%"]);
        let checks = vec![
            json!({"criterion": "c1", "covers": ["P95 under 200ms in prod"], "agent": "a"}),
            json!({"criterion": "c2", "covers": ["p95 under 200ms", "error rate below 1%"], "agent": "b"}),
        ];
        let sel = assign_checks(&checks, &clauses).unwrap();
        assert_eq!(sel.len(), 1);
        assert_eq!(sel[0]["assigned_to"], json!("b"));
        assert_eq!(sel[0]["atom"], json!("p95 under 200ms"));
        assert_eq!(covered_clauses(&checks[0], &clauses).unwrap(), BTreeSet::from([0]));
    }
}
