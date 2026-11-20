//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The order of checks, and what follows from each message: the part of the node that works on one run at a time, apart from the
//! agent that owns the state, the store and the clock.

use crate::engine::{add_anchor, add_proposal, add_ratings, evaluate, parse_candidate_id, EvalState, Evaluation, Rating};
use crate::errors::{ConcordError, Result};
use crate::event::{EventKind, HeldReason, IgnoredReason, Source};
use crate::liveness::{owed_batches, sync_timers};
use crate::protocol::{Outcome, PROTOCOL_VERSION};
use crate::state::{item_key, new_run, Abort, AbortNote, AgentState, Reported, Role, Run, RunStatus};
use crate::wire::{
    build_cancel, build_error, build_resend, build_result, check_ok, locate, parse_message, peek, Body, Kind, Message, Peek, DATA_TAGS,
    ERROR, INIT, RESEND,
};

use super::Step;

/// What the node needs to act on a run, apart from the state: who it is, and where UUIDs come from.
pub(super) struct Node<'a> {
    pub(super) me: &'a str,
    pub(super) new_uuid: &'a mut dyn FnMut() -> String,
}

// ----------------------------------------------------------------------------------------------------------------------------
// the order of checks, and what follows from each message
// ----------------------------------------------------------------------------------------------------------------------------

impl Node<'_> {
    pub(super) fn uid(&mut self) -> String {
        (self.new_uuid)()
    }

    pub(super) fn handle(&mut self, state: &mut AgentState, text: &str, step: &mut Step) -> Result<()> {
        let Some(info) = locate(text).ok().and_then(|doc| peek(doc).map(|info| (doc, info))) else {
            step.note(EventKind::ignored(IgnoredReason::NotConcord, Source::default()));
            return Ok(());
        };
        let (doc, info) = info;
        let tag = info.tag.as_str();
        let (sender, run_id, uid) = (info.attr("from"), info.attr("run_id"), info.attr("uuid"));
        let known = run_id.and_then(|r| state.runs.get(r));
        let place = Source::of(tag).from_attr(sender).run_id_attr(run_id);
        // 1. the run is known, the sender belongs to it and is not this agent
        if sender == Some(self.me) {
            step.note(EventKind::ignored(IgnoredReason::OwnMessage, place));
            return Ok(());
        }
        if known.is_none() && tag != INIT && tag != RESEND {
            step.note(EventKind::ignored(IgnoredReason::UnknownRun, place));
            return Ok(());
        }
        if let Some(run) = known {
            if !sender.is_some_and(|s| run.is_member(s)) {
                step.note(EventKind::ignored(IgnoredReason::SenderNotInRun, place));
                return Ok(());
            }
            // 2. the uuid is not one already accepted
            if uid.is_some_and(|u| run.uuids.contains_key(u)) {
                step.note(EventKind::ignored(IgnoredReason::Duplicate, place));
                return Ok(());
            }
            // a version refusal of the init is read whatever its version: the initiator relies on its root attributes and its text
            if tag == ERROR
                && info.attr("reason") == Some("version")
                && info.attr("what") == Some("init")
                && info.attr("to") == Some(self.me)
                && run.initiator == self.me
                && sender.is_some_and(|s| run.participants.iter().any(|p| p == s))
            {
                let run = state.runs.get_mut(run_id.unwrap_or_default()).expect("the run is known");
                if self.on_version_refusal(run, &info, step)? {
                    if let Some(uid) = uid {
                        run.keep_uuid(uid);
                    }
                }
                return Ok(());
            }
        }
        // 3 to 6, for the message itself and, in a resend, for the message inside
        let keep = if tag == RESEND { self.unwrap(state, doc, &info, step)? } else { self.accept(state, doc, &info, false, step)? };
        if keep {
            // the init may have started the run
            if let Some(run) = state.runs.get_mut(run_id.unwrap_or_default()) {
                if let Some(uid) = uid {
                    run.keep_uuid(uid);
                }
                self.advance(run, step)?;
            }
        }
        Ok(())
    }

    /// A `<concord-resend>`: steps 2 and 3 are for the wrapper (it has no check), and 3 to 5 for the message inside.
    pub(super) fn unwrap(&mut self, state: &mut AgentState, doc: &str, info: &Peek, step: &mut Step) -> Result<bool> {
        let place = Source::of(RESEND).from_attr(info.attr("from")).run_id_attr(info.attr("run_id"));
        if info.attr("version") != Some(PROTOCOL_VERSION) {
            step.note(EventKind::wrong_version(place, info.attr("version")));
            if let Some(inner) = info.inner.as_ref().filter(|i| i.tag == INIT) {
                self.refuse_version(inner, step);
            }
            return Ok(false);
        }
        let msg = match parse_message(doc, None) {
            Ok(msg) => msg,
            Err(exc) => {
                step.note(EventKind::invalid(place, exc.to_string()));
                return Ok(false);
            }
        };
        if let Some(why) = root_differs(info, &msg) {
            step.note(EventKind::invalid(place, why));
            return Ok(false);
        }
        let Body::Resend(body) = &msg.body else { return Ok(false) };
        let inner = peek(&body.inner);
        let Some(inner) = inner.filter(|i| i.attr("from") == info.attr("from") && i.attr("run_id") == info.attr("run_id")) else {
            step.note(EventKind::invalid(place, "the message inside is not the sender's own, of this run"));
            return Ok(false);
        };
        self.accept(state, &body.inner, &inner, true, step)
    }

    /// Steps 3 to 6 for `doc`. Returns whether its uuid is to be kept: it was processed.
    pub(super) fn accept(&mut self, state: &mut AgentState, doc: &str, info: &Peek, resent: bool, step: &mut Step) -> Result<bool> {
        let tag = info.tag.as_str();
        let run_id = info.attr("run_id").unwrap_or_default();
        let place = Source::of(tag).from_attr(info.attr("from")).run_id_attr(info.attr("run_id"));
        let known = state.runs.contains_key(run_id);
        if !known && tag != INIT {
            // a message of a run it does not know, such as one carried by a resend
            step.note(EventKind::ignored(IgnoredReason::UnknownRun, place));
            return Ok(false);
        }
        if info.attr("version") != Some(PROTOCOL_VERSION) {
            step.note(EventKind::wrong_version(place, info.attr("version")));
            if tag == INIT {
                self.refuse_version(info, step);
            }
            return Ok(false);
        }
        if DATA_TAGS.contains(&tag) && !check_ok(doc) {
            step.note(EventKind::ignored(IgnoredReason::Check, place));
            if tag != INIT {
                if let Some(run) = state.runs.get_mut(run_id) {
                    self.ask_damaged(run, info, step)?;
                }
            }
            return Ok(false);
        }
        let schema = state.runs.get(run_id).map(|r| r.schema.clone());
        let msg = match parse_message(doc, schema.as_ref()) {
            Ok(msg) => msg,
            Err(exc) => {
                step.note(EventKind::invalid(place, exc.to_string()));
                if tag == INIT && !known {
                    self.error(step, info.attr("from"), info.attr("run_id"), "init", "invalid", None, None, &exc.to_string());
                }
                return Ok(false);
            }
        };
        if let Some(why) = root_differs(info, &msg) {
            step.note(EventKind::invalid(place, why));
            return Ok(false);
        }
        if !known {
            return self.join(state, &msg, doc, step);
        }
        let run = state.runs.get_mut(run_id).expect("the run is known");
        if msg.kind() == Kind::Init {
            return self.known_init(run, &msg, resent, step);
        }
        match self.record(run, &msg, doc, step)? {
            Some(kept) => Ok(kept),
            None => {
                // it cannot be judged before the run state is complete
                let uid = info.attr("uuid").unwrap_or_default();
                if !run.held.contains_key(uid) {
                    if run.held.len() >= held_limit(run) {
                        step.note(EventKind::ignored(IgnoredReason::TooManyHeld, Source::of(msg.kind().as_str()).from(&msg.from)));
                        return Ok(false);
                    }
                    run.held.insert(uid.to_string(), doc.to_string());
                }
                step.note(EventKind::Held {
                    kind: msg.kind().as_str().to_string(),
                    reason: HeldReason::RunStateIncomplete,
                    from: msg.from.clone(),
                });
                let keys = self.lacking_keys(run);
                self.ask_keys(run, &keys, step)?;
                Ok(false)
            }
        }
    }

    /// An init of another version: the agent does not join, and tells the author which version it speaks.
    pub(super) fn refuse_version(&mut self, info: &Peek, step: &mut Step) {
        let theirs = info.attr("version").map_or_else(|| "this init has no version".to_string(), |v| format!("this init is version {v}"));
        self.error(
            step,
            info.attr("from"),
            info.attr("run_id"),
            "init",
            "version",
            None,
            None,
            &format!("I speak version {PROTOCOL_VERSION}; {theirs}."),
        );
    }

    /// Add a `<concord-error>` to the messages to send, unless it cannot be built (an unreadable author or run id). Returns whether it
    /// was added.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn error(
        &mut self,
        step: &mut Step,
        to: Option<&str>,
        run_id: Option<&str>,
        what: &str,
        reason: &str,
        candidate: Option<&str>,
        reference: Option<&str>,
        text: &str,
    ) -> bool {
        let uid = self.uid();
        match build_error(self.me, run_id.unwrap_or_default(), to.unwrap_or_default(), what, reason, candidate, reference, text, Some(&uid))
        {
            Ok(built) => {
                step.send.push(built);
                step.note(EventKind::SentError {
                    kind: "error".to_string(),
                    to: to.map(str::to_string),
                    what: what.to_string(),
                    error: reason.to_string(),
                    run_id: run_id.map(str::to_string),
                });
                true
            }
            Err(exc) => {
                step.note(EventKind::CannotAnswer { detail: exc.to_string() });
                false
            }
        }
    }

    pub(super) fn join(&mut self, state: &mut AgentState, msg: &Message, doc: &str, step: &mut Step) -> Result<bool> {
        let Body::Init(init) = &msg.body else { return Ok(false) };
        let place = Source::of(INIT).from(&msg.from).run_id(&msg.run_id);
        if !init.participants.iter().any(|p| p == self.me) {
            step.note(EventKind::ignored(IgnoredReason::NotParticipant, place));
            return Ok(false);
        }
        if let Some(mine) = state.active_run().map(|r| r.run_id.clone()) {
            let waits = self.waits_for_replies(&state.runs[&mine]);
            if waits && mine > msg.run_id {
                // two runs opened at about the same time: the one with the lower run id continues, and this agent joins it
                let run = state.runs.get_mut(&mine).expect("the run is active");
                self.abort(run, step, "superseded", None, Some(&msg.run_id))?;
            } else {
                step.note(EventKind::ignored(IgnoredReason::Busy, place));
                self.error(step, Some(&msg.from), Some(&msg.run_id), "init", "busy", None, None, "I am in another run.");
                return Ok(false);
            }
        }
        self.open_run(state, msg, doc)?;
        step.note(EventKind::Joined { initiator: msg.from.clone(), participants: init.participants.clone(), run_id: msg.run_id.clone() });
        Ok(true)
    }

    /// Make the run of the parsed init `init` (the text `doc`) the active one. A run id that the agent knows is never reused: it would
    /// replace the state of that run.
    pub(super) fn open_run<'s>(&self, state: &'s mut AgentState, init: &Message, doc: &str) -> Result<&'s mut Run> {
        let Body::Init(body) = &init.body else { return Err(ConcordError::state("open_run: not an init")) };
        if state.runs.contains_key(&init.run_id) {
            return Err(ConcordError::state(format!("the agent knows the run {} already", init.run_id)));
        }
        state.active = Some(init.run_id.clone());
        let run = new_run(self.me, &init.from, &init.run_id, &init.uuid, body, doc)?;
        Ok(state.runs.entry(init.run_id.clone()).or_insert(run))
    }

    /// Whether this agent opened `run` and still waits for init replies.
    pub(super) fn waits_for_replies(&self, run: &Run) -> bool {
        run.initiator == self.me && run.engine.anchors.len() < run.engine.agents.len()
    }

    /// A participant refuses the init because of its protocol version: the initiator ends its run `ABORTED` with the reason
    /// "version" and sends the cancel, so that the participants that already joined end it too.
    pub(super) fn on_version_refusal(&mut self, run: &mut Run, info: &Peek, step: &mut Step) -> Result<bool> {
        let place = Source::of("error").from_attr(info.attr("from")).error("version").run_id(&run.run_id);
        if run.status != RunStatus::Active {
            step.note(EventKind::ignored(IgnoredReason::RunOver, place));
            return Ok(false);
        }
        step.note(EventKind::Refused { source: place, text: info.text.clone() });
        self.abort(run, step, "version", None, None)?;
        // which agent refused, and what it said
        if let Some(abort) = run.abort.as_mut() {
            abort.note = Some(AbortNote { agent: info.attr("from").unwrap_or_default().to_string(), text: info.text.clone() });
        }
        Ok(true)
    }

    /// Record the valid message `msg` (the text `doc`) of a known run. Returns whether to keep its uuid, or None when it cannot be
    /// judged yet (see `accept`).
    pub(super) fn record(&mut self, run: &mut Run, msg: &Message, doc: &str, step: &mut Step) -> Result<Option<bool>> {
        let place = Source::of(msg.kind().as_str()).from(&msg.from).run_id(&run.run_id);
        match &msg.body {
            Body::Error(e) => return self.record_error(run, msg, e, step).map(Some),
            Body::Cancel(c) => return self.record_cancel(run, msg, c, step).map(Some),
            _ => {}
        }
        if run.status != RunStatus::Active {
            step.note(EventKind::ignored(IgnoredReason::RunOver, place));
            return Ok(Some(false));
        }
        if run.role == Role::Initiator {
            return self.record_as_initiator(run, msg, doc, step).map(Some);
        }
        if let Body::Result(r) = &msg.body {
            return self.record_result(run, msg, r, doc, step);
        }
        let by_participant = run.participants.contains(&msg.from);
        match &msg.body {
            Body::Repair(p) if by_participant => self.record_repair(run, msg, p, doc, step),
            Body::InitReply(r) if by_participant => self.record_init_reply(run, msg, r, doc, step).map(Some),
            Body::Score(s) if by_participant => self.record_score(run, msg, s, doc, step).map(Some),
            _ => {
                step.note(EventKind::ignored(IgnoredReason::NotHandled, place));
                Ok(Some(false))
            }
        }
    }

    /// An initiator that is not a participant records only the init replies, and the repairs, which it does not judge, and takes the
    /// result of the reporter: that is how it learns how the run ended.
    pub(super) fn record_as_initiator(&mut self, run: &mut Run, msg: &Message, doc: &str, step: &mut Step) -> Result<bool> {
        let by_participant = run.participants.contains(&msg.from);
        match &msg.body {
            Body::Result(r) => self.observe_result(run, msg, r, doc, step),
            Body::InitReply(r) if by_participant => self.record_init_reply(run, msg, r, doc, step),
            Body::Repair(p) if by_participant => self.observe_repair(run, msg, p, doc, step),
            _ => {
                step.note(EventKind::ignored(
                    IgnoredReason::NotForInitiator,
                    Source::of(msg.kind().as_str()).from(&msg.from).run_id(&run.run_id),
                ));
                Ok(false)
            }
        }
    }

    /// An init for a run the agent knows is ignored, unless it comes inside a resend from the initiator: it is waiting for init
    /// replies, and a participant that has sent its own sends it again.
    pub(super) fn known_init(&mut self, run: &mut Run, msg: &Message, resent: bool, step: &mut Step) -> Result<bool> {
        let key = item_key("init-reply", self.me, None);
        let stored = run.sent.get(&key).cloned();
        let Some(stored) = stored.filter(|_| resent && msg.from == run.initiator) else {
            step.note(EventKind::ignored(IgnoredReason::RunAlreadyKnown, Source::of("init").from(&msg.from).run_id(&run.run_id)));
            return Ok(false);
        };
        let uid = self.uid();
        step.send.push(build_resend(self.me, &run.run_id, &stored, None, Some(&uid))?);
        step.note(EventKind::Resent { kind: "init-reply".to_string(), to: None, run_id: run.run_id.clone() });
        Ok(true)
    }

    /// An error is answered by its addressee, with the stored original word for word, even after the run is over. Only a mismatch is
    /// read by every agent.
    pub(super) fn record_error(&mut self, run: &mut Run, msg: &Message, e: &crate::wire::ErrorBody, step: &mut Step) -> Result<bool> {
        let place = Source::of("error").from(&msg.from).error(&e.reason).what(&e.what);
        if e.reason == "mismatch" {
            return Ok(self.record_mismatch(run, msg, e, step));
        }
        if !["missing", "check"].contains(&e.reason.as_str()) || e.to != self.me || e.what == "init" {
            step.note(EventKind::ignored(IgnoredReason::NoAnswerExpected, place));
            return Ok(false);
        }
        let stored = if e.reason == "check" {
            e.reference.as_ref().and_then(|r| run.sent_by_uuid.get(r))
        } else {
            run.sent.get(&item_key(&e.what, self.me, e.candidate.as_deref()))
        };
        let Some(stored) = stored.cloned() else {
            step.note(EventKind::NothingToSend { source: place });
            return Ok(true);
        };
        let uid = self.uid();
        step.send.push(build_resend(self.me, &run.run_id, &stored, Some(&msg.uuid), Some(&uid))?);
        step.note(EventKind::Resent { kind: e.what.clone(), to: Some(msg.from.clone()), run_id: run.run_id.clone() });
        Ok(true)
    }

    /// A cancel ends the run for this agent, whatever its stage, and is passed on once.
    pub(super) fn record_cancel(&mut self, run: &mut Run, msg: &Message, c: &crate::wire::CancelBody, step: &mut Step) -> Result<bool> {
        let place = Source::of("cancel").from(&msg.from).cancel(&c.reason);
        if let Some(agent) = c.agent.as_ref().filter(|a| !run.participants.contains(a)) {
            step.note(EventKind::invalid(place, format!("{agent} is not a participant of the run")));
            return Ok(false);
        }
        self.abort(run, step, &c.reason, c.agent.as_deref(), c.superseded_by.as_deref())?;
        Ok(true)
    }

    /// End the run `ABORTED` with `reason`, and send the cancel once. An agent whose run has ended with an outcome keeps it: its
    /// report is disputed. An aborted run sends no result.
    pub(super) fn abort(
        &mut self,
        run: &mut Run,
        step: &mut Step,
        reason: &str,
        agent: Option<&str>,
        superseded_by: Option<&str>,
    ) -> Result<()> {
        match run.status {
            RunStatus::Active => {
                run.status = RunStatus::Aborted;
                run.outcome = Some(Outcome::Aborted);
                run.abort = Some(Abort {
                    reason: reason.to_string(),
                    agent: agent.map(str::to_string),
                    superseded_by: superseded_by.map(str::to_string),
                    note: None,
                });
                run.timers.clear();
                run.held.clear();
                step.note(EventKind::Aborted {
                    run_id: run.run_id.clone(),
                    reason: reason.to_string(),
                    agent: agent.map(str::to_string),
                    superseded_by: superseded_by.map(str::to_string),
                });
            }
            RunStatus::Ended => {
                run.disputed = true;
                step.note(EventKind::Disputed {
                    run_id: run.run_id.clone(),
                    reason: reason.to_string(),
                    agent: agent.map(str::to_string),
                    superseded_by: superseded_by.map(str::to_string),
                });
            }
            RunStatus::Aborted => {}
        }
        if !run.cancel_sent {
            run.cancel_sent = true;
            let uid = self.uid();
            step.send.push(build_cancel(self.me, &run.run_id, reason, superseded_by, agent, Some(&uid))?);
            step.note(EventKind::SentCancel {
                kind: "cancel".to_string(),
                run_id: run.run_id.clone(),
                reason: reason.to_string(),
                agent: agent.map(str::to_string),
                superseded_by: superseded_by.map(str::to_string),
            });
        }
        Ok(())
    }

    /// Whether the message of the item `key` has been recorded, or sent, already: the first one stands.
    pub(super) fn already(run: &Run, key: &str, step: &mut Step, place: Source) -> bool {
        if !run.received.contains_key(key) && !run.sent.contains_key(key) {
            return false;
        }
        step.note(EventKind::ignored(IgnoredReason::AlreadyRecorded, place));
        true
    }

    /// An initiator that does not negotiate keeps the repairs it hears, without judging them, so that its report can name the winner. A
    /// repair id carries its author, so a repair that the participants ignore cannot take the place of a valid one.
    pub(super) fn observe_repair(
        &mut self,
        run: &mut Run,
        msg: &Message,
        p: &crate::wire::Proposal,
        doc: &str,
        step: &mut Step,
    ) -> Result<bool> {
        let place = Source::of("repair").from(&msg.from).candidate(&p.id);
        let key = item_key("repair", &msg.from, Some(&p.id));
        if Self::already(run, &key, step, place.clone()) {
            return Ok(true);
        }
        add_proposal(&mut run.engine, &msg.from, &p.id, &p.candidate, &p.rationale, p.raw, &p.reasoning)?;
        run.received.insert(key, doc.to_string());
        step.note(EventKind::recorded(place));
        Ok(true)
    }

    pub(super) fn record_init_reply(
        &mut self,
        run: &mut Run,
        msg: &Message,
        r: &crate::wire::InitReplyBody,
        doc: &str,
        step: &mut Step,
    ) -> Result<bool> {
        let (author, key, p) = (&msg.from, item_key("init-reply", &msg.from, None), &r.proposal);
        if Self::already(run, &key, step, Source::of("init-reply").from(author)) {
            return Ok(true);
        }
        let added = add_anchor(&mut run.engine, author, r.raw_reject, r.raw_ideal)
            .and_then(|_| add_proposal(&mut run.engine, author, &p.id, &p.candidate, &p.rationale, p.raw, &p.reasoning));
        if let Err(exc) = added {
            step.note(EventKind::invalid(Source::of("init-reply").from(author), exc.to_string()));
            return Ok(false);
        }
        run.received.insert(key, doc.to_string());
        step.note(EventKind::recorded(Source::of("init-reply").from(author)));
        Ok(true)
    }

    pub(super) fn record_score(
        &mut self,
        run: &mut Run,
        msg: &Message,
        s: &crate::wire::ScoreBody,
        doc: &str,
        step: &mut Step,
    ) -> Result<bool> {
        let author = &msg.from;
        let ratings: Vec<Rating> =
            s.scores.iter().map(|e| Rating { id: e.id.clone(), raw: e.raw, reasoning: e.reasoning.clone() }).collect();
        let done = match add_ratings(&mut run.engine, author, &ratings) {
            Ok(done) => done,
            Err(exc) => {
                step.note(EventKind::invalid(Source::of("score").from(author), exc.to_string()));
                return Ok(false);
            }
        };
        for cid in &done.recorded {
            run.received.insert(item_key("score", author, Some(cid)), doc.to_string());
        }
        let ignored: Vec<&String> = s.ignored.iter().chain(&done.ignored).collect();
        if !ignored.is_empty() {
            step.note(EventKind::EntriesIgnored {
                kind: "score".to_string(),
                candidates: ignored.into_iter().cloned().collect(),
                from: author.clone(),
            });
        }
        // this agent never proposed it: not a gap
        let (mine, unknown): (Vec<&String>, Vec<&String>) =
            done.unknown.iter().partition(|cid| parse_candidate_id(cid).is_some_and(|(_, who)| who == self.me));
        if !mine.is_empty() {
            step.note(EventKind::EntriesIgnored {
                kind: "score".to_string(),
                candidates: mine.into_iter().cloned().collect(),
                from: author.clone(),
            });
        }
        if !unknown.is_empty() {
            step.note(EventKind::Gap {
                kind: "score".to_string(),
                candidates: unknown.iter().map(|cid| (*cid).clone()).collect(),
                from: author.clone(),
            });
            let mut keys = Vec::new();
            for cid in unknown {
                let Some((round, proposer)) = parse_candidate_id(cid) else { continue };
                if run.participants.iter().any(|p| p == proposer) {
                    if !run.expected.contains(cid) {
                        run.expected.push(cid.clone());
                    }
                    keys.push(if round == 0 { format!("init-reply|{proposer}") } else { format!("repair|{proposer}|{round}") });
                }
            }
            self.ask_keys(run, &keys, step)?;
        }
        step.note(EventKind::scores_recorded(author, &done.recorded));
        Ok(true)
    }

    /// A repair counts only from the bottleneck of the round, and only when one is owed. A copy of the run state that is not complete
    /// cannot tell: None says the repair is to be held (see `accept`) and judged again later.
    pub(super) fn record_repair(
        &mut self,
        run: &mut Run,
        msg: &Message,
        p: &crate::wire::Proposal,
        doc: &str,
        step: &mut Step,
    ) -> Result<Option<bool>> {
        let author = &msg.from;
        let place = Source::of("repair").from(author).candidate(&p.id);
        let key = item_key("repair", author, Some(&p.id));
        if Self::already(run, &key, step, place.clone()) {
            return Ok(Some(true));
        }
        let ev = evaluate(&run.engine);
        if ev.state == EvalState::Wait {
            return Ok(None);
        }
        if ev.state != EvalState::Repair || ev.bottleneck.as_ref() != Some(author) || Some(p.round) != ev.round {
            step.note(EventKind::ignored(IgnoredReason::NoRepairOwed, place));
            return Ok(Some(false));
        }
        if let Err(exc) = add_proposal(&mut run.engine, author, &p.id, &p.candidate, &p.rationale, p.raw, &p.reasoning) {
            step.note(EventKind::invalid(place, exc.to_string()));
            return Ok(Some(false));
        }
        run.received.insert(key, doc.to_string());
        step.note(EventKind::recorded(place));
        Ok(Some(true))
    }

    /// Whether the result `msg` is to be taken: only the result of the reporter counts, and the first one stands. Returns None when it
    /// is to be taken, else what the caller returns (false: ignored, true: already held).
    pub(super) fn result_to_take(&self, run: &Run, msg: &Message, step: &mut Step) -> Option<bool> {
        let place = Source::of("result").from(&msg.from);
        if msg.from != run.participants[0] {
            step.note(EventKind::ignored(IgnoredReason::NotReporter, place));
            return Some(false);
        }
        Self::already(run, &item_key("result", &msg.from, None), step, place).then_some(true)
    }

    pub(super) fn take_result(&self, run: &mut Run, msg: &Message, r: &crate::wire::ResultBody, doc: &str, step: &mut Step) {
        run.reported = Some(Reported { outcome: r.outcome, winning_candidate_id: r.winning_candidate_id.clone() });
        run.received.insert(item_key("result", &msg.from, None), doc.to_string());
        step.note(EventKind::Recorded {
            source: Source::of("result").from(&msg.from),
            candidates: None,
            outcome: Some(r.outcome),
            winner: Some(r.winning_candidate_id.clone()),
        });
    }

    /// The result counts only from the reporter, and its winner must be a candidate of the run. Until this agent has computed its own
    /// outcome it cannot say whether the winner exists: None says the result is to be held (see `accept`).
    pub(super) fn record_result(
        &mut self,
        run: &mut Run,
        msg: &Message,
        r: &crate::wire::ResultBody,
        doc: &str,
        step: &mut Step,
    ) -> Result<Option<bool>> {
        if let Some(taken) = self.result_to_take(run, msg, step) {
            return Ok(Some(taken));
        }
        if !run.engine.pool.iter().any(|c| c.id == r.winning_candidate_id) {
            if run.outcome.is_none() {
                return Ok(None);
            }
            step.note(EventKind::invalid(
                Source::of("result").from(&msg.from),
                format!("{} is not a candidate of the run", r.winning_candidate_id),
            ));
            return Ok(Some(false));
        }
        self.take_result(run, msg, r, doc, step);
        Ok(Some(true))
    }

    /// The result as an initiator that is not a participant takes it: from the reporter, as it is, since it holds no copy of the run
    /// state to judge the winner by. It ends the run for the initiator.
    pub(super) fn observe_result(
        &mut self,
        run: &mut Run,
        msg: &Message,
        r: &crate::wire::ResultBody,
        doc: &str,
        step: &mut Step,
    ) -> Result<bool> {
        if let Some(taken) = self.result_to_take(run, msg, step) {
            return Ok(taken);
        }
        self.take_result(run, msg, r, doc, step);
        run.status = RunStatus::Ended;
        run.outcome = Some(r.outcome);
        run.timers.clear();
        step.note(EventKind::Ended { outcome: Some(r.outcome), run_id: run.run_id.clone() });
        Ok(true)
    }

    /// A mismatch error is read by every agent, also after its run has ended: the run is in doubt.
    pub(super) fn record_mismatch(&mut self, run: &mut Run, msg: &Message, e: &crate::wire::ErrorBody, step: &mut Step) -> bool {
        let place = Source::of("error").from(&msg.from).about("mismatch");
        if !run.participants.contains(&msg.from) || e.to != run.participants[0] {
            step.note(EventKind::ignored(IgnoredReason::NotAboutReporterResult, place));
            return false;
        }
        run.mismatches.received.push(msg.from.clone());
        step.note(EventKind::MismatchReceived { source: place });
        true
    }

    // ------------------------------------------------------------------
    // what follows
    // ------------------------------------------------------------------

    /// Evaluate the engine over the state, note the outcome when this agent has computed it, and take the step that follows: the
    /// reporter sends its result, any other participant compares the result it holds with its outcome.
    pub(super) fn advance(&mut self, run: &mut Run, step: &mut Step) -> Result<()> {
        if run.status != RunStatus::Active {
            return Ok(());
        }
        if run.role == Role::Initiator {
            self.sync_timers(run, step, None);
            return Ok(());
        }
        self.replay_held(run, step)?;
        let ev = evaluate(&run.engine); // once: the steps below read it, and none of them changes the engine
        if ev.state == EvalState::Done && run.outcome.is_none() {
            run.outcome = ev.outcome;
            step.note(EventKind::ComputedOutcome { outcome: ev.outcome, winner: ev.x_star.clone(), run_id: run.run_id.clone() });
        } else if ev.state == EvalState::Repair {
            step.note(EventKind::RepairOwed { bottleneck: ev.bottleneck.clone(), round: ev.round, run_id: run.run_id.clone() });
        }
        if run.outcome.is_some() && run.status == RunStatus::Active {
            self.confirm(run, &ev, step)?;
        }
        self.sync_timers(run, step, Some(&ev));
        Ok(())
    }

    /// Judge again the repairs and results that were held, now that the run state may be complete.
    pub(super) fn replay_held(&mut self, run: &mut Run, step: &mut Step) -> Result<()> {
        let mut moved = true;
        while moved && !run.held.is_empty() {
            moved = false;
            let held: Vec<(String, String)> = run.held.iter().map(|(u, d)| (u.clone(), d.clone())).collect();
            for (uid, doc) in held {
                let msg = parse_message(&doc, Some(&run.schema))?;
                let Some(kept) = self.record(run, &msg, &doc, step)? else { continue };
                run.held.shift_remove(&uid);
                moved = true;
                if kept {
                    run.keep_uuid(&uid);
                }
            }
        }
        Ok(())
    }

    /// The reporter ends its run by sending its result; another participant ends it when it holds that result, and a result that
    /// differs from its own outcome makes it send a mismatch error. A result never changes the outcome.
    pub(super) fn confirm(&mut self, run: &mut Run, ev: &Evaluation, step: &mut Step) -> Result<()> {
        let (Some(outcome), Some(x_star)) = (ev.outcome, ev.x_star.as_deref()) else { return Ok(()) };
        let reporter = run.participants[0].clone();
        if self.me == reporter {
            let uid = self.uid();
            let text = build_result(self.me, &run.run_id, outcome, x_star, Some(&uid))?;
            run.keep_sent(&item_key("result", self.me, None), &text)?;
            step.send.push(text);
            step.note(EventKind::Sent { kind: "result".to_string(), run_id: run.run_id.clone() });
        } else if let Some(reported) = run.reported.clone() {
            if (reported.outcome, reported.winning_candidate_id.as_str()) != (outcome, x_star) {
                let uid = self.uid();
                let text = build_error(
                    self.me,
                    &run.run_id,
                    &reporter,
                    "result",
                    "mismatch",
                    None,
                    None,
                    &format!(
                        "My outcome is {outcome} with {x_star}; the result says {} with {}.",
                        reported.outcome, reported.winning_candidate_id
                    ),
                    Some(&uid),
                )?;
                run.mismatches.sent += 1;
                run.mismatches.last = Some(text.clone());
                step.send.push(text);
                step.note(EventKind::Mismatch {
                    run_id: run.run_id.clone(),
                    mine: (outcome, x_star.to_string()),
                    theirs: (reported.outcome, reported.winning_candidate_id),
                });
            }
        } else {
            return Ok(());
        }
        run.status = RunStatus::Ended;
        step.note(EventKind::Ended { outcome: run.outcome, run_id: run.run_id.clone() });
        Ok(())
    }

    // ------------------------------------------------------------------
    // the messages owed, and the timers
    // ------------------------------------------------------------------

    /// The evaluation of the engine over `run`; an initiator that does not negotiate has none, and nothing reads it.
    pub(super) fn evaluation(run: &Run) -> Option<Evaluation> {
        (run.role != Role::Initiator).then(|| evaluate(&run.engine))
    }

    /// Start the timer of every batch that has become owed, and drop the timer of every batch that is complete (see liveness.rs). The
    /// timers are in the state, so a restart keeps them.
    pub(super) fn sync_timers(&mut self, run: &mut Run, step: &Step, ev: Option<&Evaluation>) {
        let computed;
        let ev = match ev {
            Some(ev) => Some(ev),
            None => {
                computed = Self::evaluation(run);
                computed.as_ref()
            }
        };
        sync_timers(run, self.me, step.now, ev);
    }

    /// Ask the author of the batch for the first message it lacks, and restart the timer. It counts against `retries`, once the
    /// request has been sent. With `reference` the request is `check`: the document of that uuid arrived damaged. An initiator whose
    /// timer expires for an init reply sends the init again, in a resend, since an agent that never received it cannot answer an
    /// error. Returns whether a request was sent.
    pub(super) fn request(&mut self, run: &mut Run, key: &str, step: &mut Step, reference: Option<&str>, from_timer: bool) -> Result<bool> {
        let t = run.timers[key].clone();
        if reference.is_none() && from_timer && t.what == "init-reply" && run.initiator == self.me {
            let init = run
                .sent
                .get(&item_key("init", self.me, None))
                .ok_or_else(|| ConcordError::state("the initiator holds no init to send again"))?;
            let uid = self.uid();
            step.send.push(build_resend(self.me, &run.run_id, init, None, Some(&uid))?);
        } else if let Some(reference) = reference {
            if !self.error(step, Some(&t.author), Some(&run.run_id), &t.what, "check", None, Some(reference), "") {
                return Ok(false);
            }
        } else if !self.error(step, Some(&t.author), Some(&run.run_id), &t.what, "missing", t.candidate.as_deref(), None, "") {
            return Ok(false);
        }
        let timeout = run.params.timeout as f64;
        let t = run.timers.get_mut(key).expect("the timer is there");
        t.requests += 1;
        t.deadline = step.now + timeout;
        step.note(EventKind::Asked {
            author: t.author.clone(),
            what: t.what.clone(),
            candidate: t.candidate.clone(),
            request: t.requests,
            run_id: run.run_id.clone(),
        });
        Ok(true)
    }

    /// A document arrived damaged. If this agent is owed that kind of message from its author, ask for it again, naming the uuid of
    /// the damaged document; the request counts against `retries` with the others for that batch. A document that is not owed (it is
    /// early, or the agent holds it already) is not asked for: the timer will ask when it is owed.
    pub(super) fn ask_damaged(&mut self, run: &mut Run, info: &Peek, step: &mut Step) -> Result<()> {
        let author = info.attr("from");
        let kind = Kind::from_tag(&info.tag).map(Kind::as_str);
        self.sync_timers(run, step, None);
        let mut keys: Vec<(f64, String)> = run.timers.iter().map(|(k, t)| (t.deadline, k.clone())).collect();
        keys.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        for (_, key) in keys {
            let t = &run.timers[&key];
            if Some(t.author.as_str()) == author
                && Some(t.what.as_str()) == kind
                && i64::from(t.requests) < run.params.retries
                && self.request(run, &key, step, info.attr("uuid"), false)?
            {
                return Ok(());
            }
        }
        Ok(())
    }

    pub(super) fn lacking_keys(&self, run: &Run) -> Vec<String> {
        let ev = Self::evaluation(run);
        owed_batches(run, self.me, ev.as_ref()).into_iter().filter(|(_, o)| o.what != "result").map(|(k, _)| k).collect()
    }

    /// Ask at once for the batches of `keys`, since a message that has just arrived proves they exist, without waiting for their
    /// timers. A batch is asked this way once; the timer repeats the request. Each request counts against `retries`.
    pub(super) fn ask_keys(&mut self, run: &mut Run, keys: &[String], step: &mut Step) -> Result<()> {
        self.sync_timers(run, step, None);
        for key in keys {
            let retries = run.params.retries;
            let Some(t) = run.timers.get_mut(key) else { continue };
            if !t.asked && i64::from(t.requests) < retries {
                t.asked = true;
                self.request(run, key, step, None, false)?;
            }
        }
        Ok(())
    }

    /// What a submitted message did: it is sent, and the run moves on.
    pub(super) fn sent(&mut self, run: &mut Run, text: &str, kind: &str, mut step: Step) -> Result<Step> {
        step.send.push(text.to_string());
        step.note(EventKind::Sent { kind: kind.to_string(), run_id: run.run_id.clone() });
        self.advance(run, &mut step)?;
        Ok(step)
    }
}

/// How many messages a run may hold at once while its state is incomplete: one for each message a run can need (every participant, every
/// repair round), and never more than `HELD_CAP`, whatever budget the initiator wrote.
fn held_limit(run: &Run) -> usize {
    const HELD_CAP: usize = 10_000;
    let rounds = usize::try_from(run.params.budget).unwrap_or(usize::MAX).saturating_add(1);
    run.participants.len().saturating_mul(rounds).min(HELD_CAP)
}

/// Why the two readings of the root of a message, `peek` (by its text) and `parse_message` (as XML), do not agree, or None when they do.
/// The agent decides what to do with a message from the first and records the second, so a message that the two read differently, as
/// `from="mallory" a=' from="alice"'` is, would be filed under one sender and recorded as another's.
fn root_differs(info: &Peek, msg: &Message) -> Option<String> {
    let read = [
        ("version", Some(msg.version.as_str())),
        ("from", Some(msg.from.as_str())),
        ("run_id", Some(msg.run_id.as_str())),
        ("uuid", Some(msg.uuid.as_str())),
        ("check", msg.check.as_deref()),
    ];
    read.iter()
        .find(|(name, parsed)| info.attr(name) != *parsed)
        .map(|(name, _)| format!("the attribute {name} of the root is not read the same way by the two readers of a message"))
}
