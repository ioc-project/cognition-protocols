//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The messages the initiator sends: the request of each phase and the execution request, and the delivery check that binds a reply to
//! the message it answers.
//!
//! A phase message starts with a delivery header, `<accord-delivery state="<state id>" check="<check>"></accord-delivery>`, then a blank
//! line and the body. The check is `reply_check` of the body; the specialist echoes the header on its reply, so a reply that was
//! summarized, rewritten or invented in its place no longer carries the right check.

use std::sync::LazyLock;

use ioc_protocol::check::{short_digest, without};
use ioc_protocol::message::{Encode, MessageError};
use regex::Regex;
use serde_json::Value;

use crate::errors::{AccordError, Result};
use crate::py;
use crate::pyjson::{canonical_json, dumps_pretty};

/// The check of a delivery line: `check="<hex>"` in an `<accord-delivery>` start tag (the last one of the tag).
pub static CHECK_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"<accord-delivery\b[^>]*\bcheck="([0-9a-f]+)""#).unwrap());

/// The check a text carries on its delivery line, if any.
pub fn find_check(text: &str) -> Option<&str> {
    CHECK_RE.captures(text).and_then(|c| c.get(1)).map(|m| m.as_str())
}

/// Text escaped for an XML attribute or content: `&`, `<`, `>` and `"` (`ioc_protocol::xml::esc_attr`).
pub use ioc_protocol::xml::esc_attr as esc;

/// The fingerprint of a phase message, blind to whitespace: the first 8 hex digits of the SHA-256 of the text without its whitespace.
///
/// It binds a reply to the message that was actually sent; it does not prove the reply's own body is untouched, which nothing the
/// initiator holds could establish.
pub fn reply_check(text: &str) -> String {
    // re.sub(r"\s+", "", text): Python's whitespace
    short_digest(&without(text, py::is_space))
}

fn text_of<'a>(st: &'a Value, key: &str) -> Result<&'a str> {
    py::as_str(py::item(st, key)?, "replace")
}

/// A phase message of a session: the body, and the state id its delivery header names.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Delivery<'a> {
    pub state_id: &'a str,
    pub body: &'a str,
}

impl Delivery<'_> {
    /// The delivery header: the state id and the check of the body.
    pub fn header(&self) -> String {
        format!("<accord-delivery state=\"{}\" check=\"{}\"></accord-delivery>", esc(self.state_id), reply_check(self.body))
    }
}

/// The text of a phase message: its delivery header, a blank line, then the body it fingerprints.
impl Encode for Delivery<'_> {
    fn encode(&self) -> std::result::Result<String, MessageError> {
        Ok(format!("{}\n\n{}", self.header(), self.body))
    }
}

/// The delivery header of a body.
pub fn header(st: &Value, body: &str) -> Result<String> {
    Ok(Delivery { state_id: text_of(st, "state_id")?, body }.header())
}

/// A phase message: its delivery header, then the body it fingerprints.
pub fn wrap_msg(st: &Value, body: &str) -> Result<String> {
    Ok(Delivery { state_id: text_of(st, "state_id")?, body }.encode()?)
}

/// `", ".join(values)`: every value has to be a string.
fn join_strs(values: &Value, sep: &str) -> Result<String> {
    let items = py::iter(values)?;
    let mut out = Vec::with_capacity(items.len());
    for (i, v) in items.iter().enumerate() {
        match v.as_str() {
            Some(s) => out.push(s),
            None => {
                return Err(AccordError::type_error(format!("sequence item {i}: expected str instance, {} found", py::type_name(v))));
            }
        }
    }
    Ok(out.join(sep))
}

/// CONVENE: the task and the specialists.
pub fn build_convene_msg(st: &Value) -> Result<String> {
    let specialists = join_strs(py::item(st, "specialists")?, ", ")?;
    let body = format!(
        "CONVENE_REQUEST\n\nActivate your `accord-receiver` skill and follow its CONVENE_REQUEST handler.\n\nTASK: {}\n\nSPECIALISTS: {specialists}",
        esc(text_of(st, "task")?)
    );
    wrap_msg(st, &body)
}

/// FRAME: the task, the current frame and the atoms this agent has in it.
pub fn build_frame_msg(st: &Value, agent: &str) -> Result<String> {
    let frame = py::item(st, "frame")?;
    let current = if py::truthy(frame) { dumps_pretty(frame) } else { "[]".to_string() };
    let me = Value::String(agent.to_string());
    let mut previous = Vec::new();
    for obs in py::iter(py::item(st, "obs")?)? {
        if py::get(&obs, "agent")?.is_some_and(|a| py::eq(a, &me)) {
            previous.push(py::item(&obs, "text")?.clone());
        }
    }
    let mut body = format!(
        "FRAME_REQUEST\n\nActivate your `accord-receiver` skill and follow its FRAME_REQUEST handler.\n\nTASK: {}\n\nCURRENT_FRAME: {current}",
        esc(text_of(st, "task")?)
    );
    if !previous.is_empty() {
        body.push_str(&format!("\n\nYOUR_PREVIOUS_ATOMS: {}", dumps_pretty(&Value::Array(previous))));
    }
    wrap_msg(st, &body)
}

/// GROUND: the key terms.
pub fn build_ground_msg(st: &Value) -> Result<String> {
    let body = format!(
        "GROUND_REQUEST\n\nActivate your `accord-receiver` skill and follow its GROUND_REQUEST handler.\n\nKEY_TERMS: {}",
        dumps_pretty(py::item(st, "key_terms")?)
    );
    wrap_msg(st, &body)
}

/// CLARIFY: the ambiguous pairs and the glossary so far. Every specialist gets the same message.
pub fn build_clarify_msg(st: &Value, _agent: &str, pairs: &Value) -> Result<String> {
    let body = format!(
        "CLARIFY_REQUEST\n\nActivate your `accord-receiver` skill and follow its CLARIFY_REQUEST handler.\n\nAMBIGUOUS_PAIR: {}\n\nDEFINITIONS_SO_FAR: {}",
        dumps_pretty(pairs),
        dumps_pretty(py::item(st, "glossary")?)
    );
    wrap_msg(st, &body)
}

/// VERIFY: the required knowledge, the objective clauses and the problem frame.
pub fn build_verify_msg(st: &Value) -> Result<String> {
    let body = format!(
        "VERIFY_REQUEST\n\nActivate your `accord-receiver` skill and follow its VERIFY_REQUEST handler.\n\nREQUIRED_KNOWLEDGE: {}\n\nOBJECTIVE_CLAUSES: {}\n\nPROBLEM_FRAME: {}",
        dumps_pretty(py::item(st, "required_knowledge")?),
        dumps_pretty(py::item(st, "objective_clauses")?),
        dumps_pretty(py::item(st, "frame")?)
    );
    wrap_msg(st, &body)
}

/// The EXECUTION_REQUEST of an agent: the locked contract as canonical JSON, and the assignment. The message is the same for every
/// agent; `_agent` is kept for the script's signature.
pub fn render_execution_message(_agent: &str, contract: &Value, assignment: &str) -> String {
    format!(
        "EXECUTION_REQUEST\n\n\
         Activate your `accord-receiver` skill and follow its EXECUTION_REQUEST\n\
         handler before starting the assignment. Report values in the units fixed by the\n\
         contract Glossary, complete the acceptance checks assigned to you in the\n\
         Verifiable Objective, and begin your reply with `LOCK_STATUS: RECEIVED`.\n\n\
         LOCKED_INTENT_CONTRACT: {}\n\n\
         ASSIGNMENT: {assignment}",
        canonical_json(contract)
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_check_ignores_whitespace() {
        assert_eq!(reply_check("a b\n\tc\u{1c}"), reply_check("abc"));
        assert_eq!(reply_check(""), "e3b0c442");
    }

    #[test]
    fn the_check_is_read_from_the_delivery_tag() {
        assert_eq!(find_check("x <accord-delivery state=\"s\" check=\"ab12\"></accord-delivery>"), Some("ab12"));
        assert_eq!(find_check("<accord-delivery check=\"aa\" check=\"bb\">"), Some("bb"));
        assert_eq!(find_check("<accord-deliveryx check=\"aa\">"), None);
        // `\b` holds between `-` and `c`: Python reads this one too
        assert_eq!(find_check("<accord-delivery data-check=\"aa\">"), Some("aa"));
        assert_eq!(find_check("<accord-delivery check=\"AB\">"), None);
    }

    #[test]
    fn a_message_is_its_header_then_its_body() {
        let st = json!({"state_id": "s&1", "task": "<t>", "specialists": ["a", "b"]});
        let msg = build_convene_msg(&st).unwrap();
        let (head, body) = msg.split_once("\n\n").unwrap();
        assert_eq!(head, format!("<accord-delivery state=\"s&amp;1\" check=\"{}\"></accord-delivery>", reply_check(body)));
        assert!(body.ends_with("TASK: &lt;t&gt;\n\nSPECIALISTS: a, b"));
        let bad = json!({"state_id": "s", "task": "t", "specialists": ["a", 1]});
        assert_eq!(build_convene_msg(&bad).unwrap_err().message(), "TypeError: sequence item 1: expected str instance, int found");
    }

    #[test]
    fn the_execution_message_carries_the_canonical_contract() {
        let msg = render_execution_message("a", &json!({"z": 1, "a": "é"}), "do it");
        assert!(msg.ends_with("LOCKED_INTENT_CONTRACT: {\"a\":\"é\",\"z\":1}\n\nASSIGNMENT: do it"));
        assert!(msg.starts_with("EXECUTION_REQUEST\n\nActivate your `accord-receiver` skill and follow its EXECUTION_REQUEST\nhandler"));
    }
}
