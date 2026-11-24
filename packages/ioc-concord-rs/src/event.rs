//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! The events of an agent: what happened during a call, as data a log can read.
//!
//! Each kind of event is a variant of `EventKind`, with typed fields, so that the names, the reasons and the fields are checked when
//! the code is compiled. An event serializes to a flat JSON object: its name under `event`, then its fields.

use serde::Serialize;

use crate::protocol::Outcome;
use crate::state::Role;

/// Where a message came from: the part of an event that says which message it is about. A field that is not set is not serialized.
///
/// `from` and `run_id` are the attributes of a message that may be missing: set from an attribute (`from_attr`, `run_id_attr`) they
/// serialize as `null` when it is missing.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Source {
    /// The kind of the message.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    /// The sender; `Some(None)` is a message with no sender.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<Option<String>>,
    /// The run; `Some(None)` is a message with no run id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<Option<String>>,
    /// The candidate a repair proposes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub candidate: Option<String>,
    /// The reason of an error message.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// What an error message is about.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub what: Option<String>,
    /// What a message is about, such as a mismatch.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub about: Option<String>,
    /// The reason of a cancel message.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cancel: Option<String>,
}

impl Source {
    /// A source for a message of this kind.
    pub fn of(kind: &str) -> Self {
        Source { kind: Some(kind.to_string()), ..Source::default() }
    }

    /// The sender.
    #[must_use]
    pub fn from(mut self, from: &str) -> Self {
        self.from = Some(Some(from.to_string()));
        self
    }

    /// The sender as the attribute of a message says it, which may be missing (then `null`).
    #[must_use]
    pub fn from_attr(mut self, from: Option<&str>) -> Self {
        self.from = Some(from.map(str::to_string));
        self
    }

    /// The run.
    #[must_use]
    pub fn run_id(mut self, run_id: &str) -> Self {
        self.run_id = Some(Some(run_id.to_string()));
        self
    }

    /// The run as the attribute of a message says it, which may be missing (then `null`).
    #[must_use]
    pub fn run_id_attr(mut self, run_id: Option<&str>) -> Self {
        self.run_id = Some(run_id.map(str::to_string));
        self
    }

    /// The candidate of a repair.
    #[must_use]
    pub fn candidate(mut self, candidate: &str) -> Self {
        self.candidate = Some(candidate.to_string());
        self
    }

    /// The reason of an error message.
    #[must_use]
    pub fn error(mut self, error: &str) -> Self {
        self.error = Some(error.to_string());
        self
    }

    /// What an error message is about.
    #[must_use]
    pub fn what(mut self, what: &str) -> Self {
        self.what = Some(what.to_string());
        self
    }

    /// What a message is about.
    #[must_use]
    pub fn about(mut self, about: &str) -> Self {
        self.about = Some(about.to_string());
        self
    }

    /// The reason of a cancel message.
    #[must_use]
    pub fn cancel(mut self, cancel: &str) -> Self {
        self.cancel = Some(cancel.to_string());
        self
    }
}

/// Why a message is ignored.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum IgnoredReason {
    /// The text is not a CONCORD message.
    #[serde(rename = "not a CONCORD message")]
    NotConcord,
    /// The agent is the sender.
    #[serde(rename = "own message")]
    OwnMessage,
    /// The run is not known.
    #[serde(rename = "unknown run")]
    UnknownRun,
    /// The sender does not belong to the run.
    #[serde(rename = "sender is not in the run")]
    SenderNotInRun,
    /// The uuid was accepted already.
    #[serde(rename = "duplicate")]
    Duplicate,
    /// The protocol version is not the agent's.
    #[serde(rename = "version")]
    Version,
    /// The integrity check failed.
    #[serde(rename = "check")]
    Check,
    /// The message is not valid.
    #[serde(rename = "invalid")]
    Invalid,
    /// An init for a run the agent knows.
    #[serde(rename = "run already known")]
    RunAlreadyKnown,
    /// The agent holds as many messages as it may.
    #[serde(rename = "too many messages held")]
    TooManyHeld,
    /// The agent is not a participant of the run the init opens.
    #[serde(rename = "not a participant")]
    NotParticipant,
    /// The agent is in another run.
    #[serde(rename = "busy")]
    Busy,
    /// The run is over.
    #[serde(rename = "run over")]
    RunOver,
    /// The initiator does not take this kind of message.
    #[serde(rename = "not for the initiator")]
    NotForInitiator,
    /// The message is not one the agent handles.
    #[serde(rename = "not handled")]
    NotHandled,
    /// The error asks for nothing from this agent.
    #[serde(rename = "no answer expected")]
    NoAnswerExpected,
    /// The item has been recorded or sent already.
    #[serde(rename = "already recorded")]
    AlreadyRecorded,
    /// The repair is not owed by its author.
    #[serde(rename = "no repair owed from this agent")]
    NoRepairOwed,
    /// The result is not from the reporter.
    #[serde(rename = "not the reporter")]
    NotReporter,
    /// The mismatch is not about the result of the reporter.
    #[serde(rename = "not about the result of the reporter")]
    NotAboutReporterResult,
}

/// Why a message is held.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum HeldReason {
    /// The copy of the run state is not complete: the message cannot be judged yet.
    #[serde(rename = "the run state is incomplete")]
    RunStateIncomplete,
}

/// What happened, with its details. The name of the event is the `event` field of the serialized form.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "event")]
pub enum EventKind {
    /// The agent opened a run.
    #[serde(rename = "started")]
    Started {
        /// The participants.
        participants: Vec<String>,
        /// The run.
        run_id: String,
        /// What the agent is in the run.
        role: Role,
    },
    /// A message was ignored.
    #[serde(rename = "ignored")]
    Ignored {
        /// Why.
        reason: IgnoredReason,
        /// Which message.
        #[serde(flatten)]
        source: Source,
        /// The version of the message, for a refusal of its version; `Some(None)` when it has none.
        #[serde(skip_serializing_if = "Option::is_none")]
        version: Option<Option<String>>,
        /// What was wrong with the message.
        #[serde(skip_serializing_if = "Option::is_none")]
        detail: Option<String>,
    },
    /// A message was kept to be judged later.
    #[serde(rename = "held")]
    Held {
        /// The kind of the message.
        kind: String,
        /// Why.
        reason: HeldReason,
        /// The sender.
        from: String,
    },
    /// An error message was sent.
    #[serde(rename = "sent")]
    SentError {
        /// Always `error`.
        kind: String,
        /// The addressee.
        to: Option<String>,
        /// What the error is about.
        what: String,
        /// The reason of the error.
        error: String,
        /// The run.
        run_id: Option<String>,
    },
    /// A cancel message was sent.
    #[serde(rename = "sent")]
    SentCancel {
        /// Always `cancel`.
        kind: String,
        /// The run.
        run_id: String,
        /// Why the run is cancelled.
        reason: String,
        /// The agent the cancel blames.
        agent: Option<String>,
        /// The run that replaces this one.
        superseded_by: Option<String>,
    },
    /// Another message was sent.
    #[serde(rename = "sent")]
    Sent {
        /// The kind of the message.
        kind: String,
        /// The run.
        run_id: String,
    },
    /// An error could not be answered.
    #[serde(rename = "cannot answer")]
    CannotAnswer {
        /// Why.
        detail: String,
    },
    /// The agent joined a run.
    #[serde(rename = "joined")]
    Joined {
        /// The initiator.
        initiator: String,
        /// The participants.
        participants: Vec<String>,
        /// The run.
        run_id: String,
    },
    /// A participant refused the init because of its version.
    #[serde(rename = "refused")]
    Refused {
        /// Which message.
        #[serde(flatten)]
        source: Source,
        /// What the refusal says.
        text: Option<String>,
    },
    /// The run was aborted.
    #[serde(rename = "aborted")]
    Aborted {
        /// The run.
        run_id: String,
        /// Why.
        reason: String,
        /// The agent the cancel blames.
        agent: Option<String>,
        /// The run that replaces this one.
        superseded_by: Option<String>,
    },
    /// A cancel came after the run had ended with an outcome: its report is disputed.
    #[serde(rename = "disputed")]
    Disputed {
        /// The run.
        run_id: String,
        /// Why.
        reason: String,
        /// The agent the cancel blames.
        agent: Option<String>,
        /// The run that replaces this one.
        superseded_by: Option<String>,
    },
    /// A stored message was sent again.
    #[serde(rename = "resent")]
    Resent {
        /// The kind of the message.
        kind: String,
        /// The agent that asked for it.
        #[serde(skip_serializing_if = "Option::is_none")]
        to: Option<String>,
        /// The run.
        run_id: String,
    },
    /// An agent asked for a message that this agent does not hold.
    #[serde(rename = "nothing to send")]
    NothingToSend {
        /// Which message.
        #[serde(flatten)]
        source: Source,
    },
    /// A message was recorded.
    #[serde(rename = "recorded")]
    Recorded {
        /// Which message.
        #[serde(flatten)]
        source: Source,
        /// The candidates a score is for.
        #[serde(skip_serializing_if = "Option::is_none")]
        candidates: Option<Vec<String>>,
        /// The outcome a result reports.
        #[serde(skip_serializing_if = "Option::is_none")]
        outcome: Option<Outcome>,
        /// The winner a result reports.
        #[serde(skip_serializing_if = "Option::is_none")]
        winner: Option<String>,
    },
    /// The entries of a score for candidates that cannot be rated were ignored.
    #[serde(rename = "entries ignored")]
    EntriesIgnored {
        /// The kind of the message.
        kind: String,
        /// The candidates.
        candidates: Vec<String>,
        /// The author.
        from: String,
    },
    /// A score rates candidates that the agent does not know.
    #[serde(rename = "gap")]
    Gap {
        /// The kind of the message.
        kind: String,
        /// The candidates.
        candidates: Vec<String>,
        /// The author.
        from: String,
    },
    /// The agent computed the outcome of the run.
    #[serde(rename = "outcome")]
    ComputedOutcome {
        /// The outcome.
        outcome: Option<Outcome>,
        /// The winning candidate.
        winner: Option<String>,
        /// The run.
        run_id: String,
    },
    /// A repair is owed.
    #[serde(rename = "repair owed")]
    RepairOwed {
        /// The participant that must propose.
        bottleneck: Option<String>,
        /// The round of the repair.
        round: Option<u32>,
        /// The run.
        run_id: String,
    },
    /// The result of the reporter differs from the agent's outcome.
    #[serde(rename = "mismatch")]
    Mismatch {
        /// The run.
        run_id: String,
        /// The outcome and the winner of the agent.
        mine: (Outcome, String),
        /// The outcome and the winner of the result.
        theirs: (Outcome, String),
    },
    /// A mismatch error was received.
    #[serde(rename = "mismatch received")]
    MismatchReceived {
        /// Which message.
        #[serde(flatten)]
        source: Source,
    },
    /// The run ended for the agent.
    #[serde(rename = "ended")]
    Ended {
        /// The outcome.
        outcome: Option<Outcome>,
        /// The run.
        run_id: String,
    },
    /// The agent asked for a message it lacks.
    #[serde(rename = "asked")]
    Asked {
        /// The author of the message.
        author: String,
        /// The kind of the message.
        what: String,
        /// The candidate the message is for.
        candidate: Option<String>,
        /// How many requests have been sent for this batch.
        request: u32,
        /// The run.
        run_id: String,
    },
}

impl EventKind {
    /// A message was ignored, for this reason.
    pub fn ignored(reason: IgnoredReason, source: Source) -> Self {
        EventKind::Ignored { reason, source, version: None, detail: None }
    }

    /// A message was ignored as invalid, for this reason.
    pub fn invalid(source: Source, detail: impl Into<String>) -> Self {
        EventKind::Ignored { reason: IgnoredReason::Invalid, source, version: None, detail: Some(detail.into()) }
    }

    /// A message of another version was ignored.
    pub fn wrong_version(source: Source, version: Option<&str>) -> Self {
        EventKind::Ignored { reason: IgnoredReason::Version, source, version: Some(version.map(str::to_string)), detail: None }
    }

    /// A message was recorded.
    pub fn recorded(source: Source) -> Self {
        EventKind::Recorded { source, candidates: None, outcome: None, winner: None }
    }

    /// The scores of these candidates were recorded.
    pub fn scores_recorded(from: &str, candidates: &[String]) -> Self {
        EventKind::Recorded { source: Source::of("score").from(from), candidates: Some(candidates.to_vec()), outcome: None, winner: None }
    }
}
