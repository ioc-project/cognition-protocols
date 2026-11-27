//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What `--help` prints: the help of the skill scripts' command line, as argparse formats it at 80 columns, with the names of this
//! command. Each `--help` of a script, its usage line and its wording, is here as it printed it; argparse would wrap it to the width
//! of the terminal, this is always the layout of 80 columns.

/// The help of `role` (`initiator` or `receiver`), or of one of its commands.
pub(crate) fn help(role: &str, command: Option<&str>) -> &'static str {
    match (role, command) {
        ("initiator", None) => {
            r#"usage: uvx ioc-concord initiator [-h] --me ME [--state-dir STATE_DIR]
                                 [--relay RELAY]
                                 {start,receive,tick,wait,report,forward} ...

CONCORD initiator: opens a run, and follows it to its end. Every call prints STATUS, sections and a NEXT line: do what NEXT says.

start     open the run: prints the first message to send (use `start --help` before the first run)
receive   pass a message of the run that arrived, exactly as received, on standard input (one per call)
tick      time has passed: asks again for what is late, or gives up on a silent agent
wait      blocks until the next timer is due, then ticks (run it in the background, and again each time it returns)
report    how the run ended, or that it has not
forward   (with --relay, for the centre of a star) the messages to pass on to the other spokes

You do not rate or propose anything: the agents do. If you are one of the participants, `start` says so and hands you over to the receiver
script, which you use from then on.

The state of the agent is kept in a file, `concord-state-<me>.json`, in --state-dir. No network.

positional arguments:
  {start,receive,tick,wait,report,forward}
    start               open the run
    receive             pass a message that arrived, as received, on standard
                        input
    tick                time has passed: ask again for what is late, or give
                        up on a silent agent
    wait                block until the next timer is due, then tick (run it
                        in the background)
    report              how the run stands or ended
    forward             for the centre of a star: what to pass on to the other
                        spokes

options:
  -h, --help            show this help message and exit
  --me ME               your agent id, as in the run
  --state-dir STATE_DIR
                        where the state of the agent is kept (default: the
                        current directory)
  --relay RELAY         the centre of a star only: the names of the spokes,
                        comma separated, on every call (see forward)
"#
        }
        ("initiator", Some("start")) => {
            r#"usage: uvx ioc-concord initiator --me ME start [-h]
                                               --participants PARTICIPANTS
                                               --context CONTEXT
                                               --x-reject X_REJECT
                                               --x-ideal X_IDEAL
                                               [--option OPTION]
                                               [--format TEXT] [--schema JSON]
                                               [--satisfaction-threshold T]
                                               [--timeout SECONDS]
                                               [--retries N] [--budget B]

open the run

options:
  -h, --help            show this help message and exit
  --participants PARTICIPANTS
                        The ids of the agents whose satisfaction counts,
                        exactly as written in the task. The order matters: the
                        first one reports the outcome and wins ties, so list
                        yourself first. Comma separated.
  --context CONTEXT     The problem to decide, as every participant must
                        understand it. Or @path of a file (a text that starts
                        with @ is written with @@).
  --x-reject X_REJECT   The objective, never the means: the state the context
                        is trying to produce, as a plain before/after. This is
                        the worst outcome: never a proposal, and no word about
                        how it is reached. If an anchor could pass as
                        something an agent might propose, rewrite it one level
                        up. Or @path of a file (a text that starts with @ is
                        written with @@).
  --x-ideal X_IDEAL     The objective, never the means: the best outcome, as a
                        plain before/after in the same terms. Never a
                        proposal, and no word about how it is reached. Or
                        @path of a file (a text that starts with @ is written
                        with @@).
  --option OPTION       The options of the task, each copied exactly as given.
                        Never write the list yourself. Use it once for each
                        possible answer, and only when the task gives you a
                        closed list of them (West City, East Town, ...): the
                        agents then have to propose exactly one of them, and
                        nothing else is accepted. With no such list, use
                        --format. A list you write yourself shuts out better
                        answers.
  --format TEXT         the open format, when there is no closed list of
                        possible answers: say in words what an answer looks
                        like (a plan, with its actions, each with its service
                        and version, for example). The agents propose their
                        own, and may propose combinations. Or @path of a file
                        (a text that starts with @ is written with @@).
  --schema JSON         the candidate schema, as a JSON Schema (a `pattern` or
                        `minLength` / `maxLength` may be in it), when neither
                        --option nor --format says what you need
  --satisfaction-threshold T
                        The standard the outcome has to meet, a number above 0
                        and at most 1, not a level you are sure is reachable
                        (0.6 is a starting point). If it sits below what the
                        agents already reach on their own proposals, the run
                        ends with no negotiation at all. It never changes
                        during the run.
  --timeout SECONDS     how long an agent waits for a message it is owed
                        before it asks again (default 300).
  --retries N           how many times an agent asks again before it aborts
                        the run (default 3)
  --budget B            the most repair rounds before the run ends BEST_EFFORT
                        (default 8)
"#
        }
        ("initiator", Some("receive")) => {
            r#"usage: uvx ioc-concord initiator --me ME receive [-h]

pass a message that arrived, as received, on standard input

options:
  -h, --help  show this help message and exit
"#
        }
        ("initiator", Some("tick")) => {
            r#"usage: uvx ioc-concord initiator --me ME tick [-h]

time has passed: ask again for what is late, or give up on a silent agent

options:
  -h, --help  show this help message and exit
"#
        }
        ("initiator", Some("wait")) => {
            r#"usage: uvx ioc-concord initiator --me ME wait [-h] [--poll POLL]

block until the next timer is due, then tick (run it in the background)

options:
  -h, --help   show this help message and exit
  --poll POLL  seconds between two looks at the state while waiting (default
               5)
"#
        }
        ("initiator", Some("report")) => {
            r#"usage: uvx ioc-concord initiator --me ME report [-h]

how the run stands or ended

options:
  -h, --help  show this help message and exit
"#
        }
        ("initiator", Some("forward")) => {
            r#"usage: uvx ioc-concord initiator --me ME forward [-h] [--from ORIGIN]

for the centre of a star: what to pass on to the other spokes

options:
  -h, --help     show this help message and exit
  --from ORIGIN  the spoke the message came from, if it came from one
"#
        }
        ("receiver", None) => {
            r#"usage: uvx ioc-concord receiver [-h] --me ME [--state-dir STATE_DIR]
                                {receive,answer,tick,wait,task,status,report} ...

CONCORD receiver: takes part in a run opened by another agent. Every call prints STATUS, sections and a NEXT line: do what NEXT says.

receive   pass a message of the run that arrived, exactly as received, on standard input (one per call)
answer    pass your answer to the task it printed, as one JSON object on standard input
tick      time has passed: asks again for what is late, or gives up on a silent agent
wait      blocks until the next timer is due, then ticks (run it in the background, and again each time it returns)
task      what is due from you now, and when to come back (use it when you do not know, or to see the first task of a run you opened)
status    where the run stands (for debugging)
report    how the run ended, or that it has not

The state of the agent is kept in a file, `concord-state-<me>.json`, in --state-dir. No network.

positional arguments:
  {receive,answer,tick,wait,task,status,report}
    receive             pass a message that arrived, as received, on standard
                        input
    answer              pass your answer to the task, as one JSON object on
                        standard input
    tick                time has passed: ask again for what is late, or give
                        up on a silent agent
    wait                block until the next timer is due, then tick (run it
                        in the background)
    task                what is due from you now, and when to come back
    status              where the run stands, for debugging
    report              how the run stands or ended

options:
  -h, --help            show this help message and exit
  --me ME               your agent id, as in the run
  --state-dir STATE_DIR
                        where the state of the agent is kept (default: the
                        current directory)
"#
        }
        ("receiver", Some("receive")) => {
            r#"usage: uvx ioc-concord receiver --me ME receive [-h]

pass a message that arrived, as received, on standard input

options:
  -h, --help  show this help message and exit
"#
        }
        ("receiver", Some("answer")) => {
            r#"usage: uvx ioc-concord receiver --me ME answer [-h]

pass your answer to the task, as one JSON object on standard input

options:
  -h, --help  show this help message and exit
"#
        }
        ("receiver", Some("tick")) => {
            r#"usage: uvx ioc-concord receiver --me ME tick [-h]

time has passed: ask again for what is late, or give up on a silent agent

options:
  -h, --help  show this help message and exit
"#
        }
        ("receiver", Some("wait")) => {
            r#"usage: uvx ioc-concord receiver --me ME wait [-h] [--poll POLL]

block until the next timer is due, then tick (run it in the background)

options:
  -h, --help   show this help message and exit
  --poll POLL  seconds between two looks at the state while waiting (default
               5)
"#
        }
        ("receiver", Some("task")) => {
            r#"usage: uvx ioc-concord receiver --me ME task [-h]

what is due from you now, and when to come back

options:
  -h, --help  show this help message and exit
"#
        }
        ("receiver", Some("status")) => {
            r#"usage: uvx ioc-concord receiver --me ME status [-h]

where the run stands, for debugging

options:
  -h, --help  show this help message and exit
"#
        }
        ("receiver", Some("report")) => {
            r#"usage: uvx ioc-concord receiver --me ME report [-h]

how the run stands or ended

options:
  -h, --help  show this help message and exit
"#
        }
        _ => "",
    }
}
