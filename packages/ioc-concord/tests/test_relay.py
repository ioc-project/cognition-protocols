#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The centre of a star: the relay on its own (as imported and as bundled), an agent that has one, and whole runs through a star."""
import json

import pytest
from test_agent import RUN, MemStore, forge, init_doc, uuids

from ioc_concord import relay as library_relay
from ioc_concord import wire as W
from ioc_concord.relay import Relay

OTHER = "22222222-2222-4222-8222-222222222222"


def score(run_id=RUN, who="cost", uid="55555555-5555-4555-8555-555555555555"):
    """A score message of `who` for a run: always the same text for the same arguments, and never of an agent for its own candidate."""
    return W.build_score(who, run_id, [("seed-cost" if who == "security" else "seed-security", 5, "r")], uid=uid)


# --- the relay on its own -------------------------------------------------------------------

def test_the_init_of_a_run_is_passed_on_and_makes_the_run_known(relay):
    r = relay.Relay()
    init = init_doc()
    assert r.receive(init) == [init] and list(r.state["runs"]) == [RUN]
    assert r.receive(score()) == [score()]                                  # a message of a run it knows
    assert r.receive(score(OTHER)) == []                                    # and one of a run it has never seen goes nowhere
    assert list(r.state["runs"]) == [RUN]


def test_an_init_inside_a_resend_makes_the_run_known_too(relay):
    r = relay.Relay()
    wrapper = W.build_resend("alice", RUN, init_doc())
    assert r.receive(wrapper) == [wrapper] and list(r.state["runs"]) == [RUN]
    unknown = relay.Relay()
    resent_score = W.build_resend("cost", RUN, score())
    assert unknown.receive(resent_score) == [] and unknown.state["runs"] == {}   # a resend of anything else does not
    assert r.receive(resent_score) == [resent_score]


def test_the_message_is_passed_on_exactly_as_it_came(relay):
    r = relay.Relay()
    text = "Here it is:\n```xml\n" + init_doc() + "\n```\n"
    assert r.receive(text) == [text]
    assert r.receive("  " + score() + "  \n") == ["  " + score() + "  \n"]


@pytest.mark.parametrize("text", ["", "hello", "<other/>", "<concord-score from='a'>never closed", "<concord-init", "{}", "<<>>"])
def test_what_is_not_a_concord_message_is_not_passed_on(relay, text):
    r = relay.Relay()
    assert r.receive(text) == [] and r.state["runs"] == {}


def test_a_run_id_that_is_not_a_uuid_names_no_run(relay):
    r = relay.Relay()
    odd = init_doc().replace(RUN, "RUNID")
    assert r.receive(odd) == [] and r.state["runs"] == {}
    no_run_id = init_doc().replace(f' run_id="{RUN}"', "")
    assert r.receive(no_run_id) == []


def test_it_reads_only_the_name_and_the_run_and_so_it_does_not_look_at_versions_checks_or_senders(relay):
    r = relay.Relay()
    other_version = init_doc().replace('version="0.3.0"', 'version="9.9.9"')
    assert r.receive(other_version) == [other_version]                      # an init of a version it does not speak makes the run known
    damaged = score().replace("5.0", "6.0")
    assert r.receive(damaged) == [damaged]                                  # a check that fails
    stranger = score(who="mallory")
    assert r.receive(stranger) == [stranger]                                # a sender that is not in the run
    future = f'<concord-future version="0.4.0" from="cost" run_id="{RUN}" uuid="33333333-3333-4333-8333-333333333333"/>'
    assert r.receive(future) == [future]                                    # a message it has never heard of


def test_a_run_stays_known_until_it_is_forgotten_whatever_happens_to_it(relay):
    r = relay.Relay()
    r.receive(init_doc())
    r.receive(init_doc(run_id=OTHER))
    r.forget(RUN)
    assert r.receive(score()) == [] and r.receive(score(OTHER)) == [score(OTHER)]
    r.forget(RUN)                                                           # a run it does not know: nothing happens
    assert list(r.state["runs"]) == [OTHER]


def test_learn_remembers_the_run_of_an_init_without_passing_anything_on(relay):
    r = relay.Relay()
    assert r.learn(score()) is False and r.state["runs"] == {}
    assert r.learn(init_doc()) is True and list(r.state["runs"]) == [RUN]
    assert r.should_pass_on(score()) is True and r.should_pass_on(score(OTHER)) is False


def test_the_state_is_kept_and_a_restarted_relay_knows_the_same_runs(relay):
    store = MemStore(relay)
    r = relay.Relay(store=store)
    r.receive(init_doc())
    r.receive(W.build_resend("alice", OTHER, init_doc(run_id=OTHER)))
    again = relay.Relay(store=store)
    assert sorted(again.state["runs"]) == sorted([RUN, OTHER]) and again.receive(score(OTHER)) == [score(OTHER)]
    assert json.loads(json.dumps(again.state)) == again.state


def test_it_saves_only_when_a_run_is_learned_or_forgotten(relay):
    saved = []

    class Counting(MemStore):
        def save(self, state):
            saved.append(1)
            super().save(state)

    r = relay.Relay(store=Counting(relay))
    for text in (score(), "hello", init_doc(), init_doc(), score(), W.build_resend("alice", RUN, init_doc())):
        r.receive(text)
    assert len(saved) == 1
    r.forget(OTHER)
    assert len(saved) == 1
    r.forget(RUN)
    assert len(saved) == 2


@pytest.mark.parametrize("text", ['{"runs": 5}', "[]", "null", "5", '{"other": {}}'])
def test_a_saved_state_that_is_not_a_relay_state_is_refused(relay, text):
    store = MemStore(relay)
    store.text = text
    with pytest.raises(relay.StateError, match="holds no runs"):
        relay.Relay(store=store)


def test_a_state_that_cannot_be_read_does_not_make_the_relay_forget_its_runs(relay):
    store = MemStore(relay)
    r = relay.Relay(store=store)
    r.receive(init_doc())

    def unreadable():
        raise relay.StateError("state file is not valid JSON")
    store.load = unreadable
    with pytest.raises(relay.StateError, match="not valid JSON"):
        relay.Relay(store=store)                                            # not an empty relay: the saved runs are not thrown away
    assert json.loads(store.text)["runs"] == {RUN: True}
    assert relay.Relay(store=MemStore(relay)).state == {"runs": {}}         # nothing saved at all: a fresh start


def test_a_run_id_in_capitals_is_not_a_uuid_and_names_no_run(relay):
    r = relay.Relay()
    lower = "abcdefab-abcd-4abc-8abc-abcdefabcdef"
    init = init_doc(run_id=lower)
    assert r.receive(init.replace(lower, lower.upper())) == [] and r.state["runs"] == {}
    assert r.receive(init) == [init] and list(r.state["runs"]) == [lower]


# --- an agent that has a relay -----------------------------------------------------------------

def centre(agent, store=None, **kw):
    return agent.Agent("hub", relay=Relay(store=store), uuid_fn=uuids("cccccccc"), **kw)


def test_an_agent_without_a_relay_never_says_to_pass_anything_on(agent):
    a = agent.Agent("cost")
    for text in (init_doc(), score(), W.build_resend("alice", RUN, init_doc(run_id=OTHER))):
        assert a.receive(text).forward == []


def test_an_agent_with_a_relay_says_which_messages_to_pass_on_and_still_does_what_an_agent_does(agent):
    a = centre(agent)
    init = init_doc(["hub", "security"])
    step = a.receive(init)
    assert step.forward == [init] and [e["event"] for e in step.events] == ["joined"] and a.due()[0]["kind"] == "init-reply"
    assert a.receive(score(OTHER)).forward == []                            # a run it does not know


def test_a_message_that_the_agent_drops_is_passed_on_anyway(agent):
    a = centre(agent)
    init = init_doc(["hub", "security"])
    a.receive(init)
    cases = {
        "a duplicate": (init, "duplicate"),
        "a sender that is not in the run": (score(who="mallory"), "sender is not in the run"),
        "another version": (W.build_score("security", RUN, [("seed-hub", 5, "r")]).replace('version="0.3.0"', 'version="0.2.0"'), "version"),
        "a damaged message": (W.build_init_reply("security", RUN, 0, 100, "p", "r", 5, "w").replace("<raw>5.0", "<raw>6.0"), "check"),
        "an invalid message": (forge(W.build_score("security", RUN, [("seed-hub", 5, "r")]), "<raw>5.0", "<raw>500.0"), None),
    }
    for label, (text, reason) in cases.items():
        step = a.receive(text)
        assert step.forward == [text], label
        assert step.events[0]["event"] in ("ignored",) and (reason is None or step.events[0]["reason"] == reason), label


def test_a_message_of_an_ended_or_aborted_run_is_still_passed_on(agent):
    a = centre(agent)
    a.receive(init_doc(["hub", "security"]))
    a.state["runs"][RUN]["status"] = "ended"
    late = W.build_score("security", RUN, [("seed-hub", 5, "r")])
    step = a.receive(late)
    assert step.forward == [late] and step.events[0]["reason"] == "run over"
    a.state["runs"][RUN]["status"] = "aborted"
    later = W.build_score("security", RUN, [("seed-hub", 6, "r")], uid="66666666-6666-4666-8666-666666666666")
    assert a.receive(later).forward == [later]


def test_a_centre_that_opens_a_run_knows_it_and_passes_on_what_comes_back(agent):
    a = centre(agent, store=MemStore(library_relay))               # the store of the relay is the library's: it is the library's Relay
    step = a.start(["hub", "cost"], "pick one", "nothing", "all agree", {"type": "string", "description": "d"}, run_id=RUN)
    assert step.forward == [] and len(step.send) == 1 and list(a.relay.state["runs"]) == [RUN]
    reply = W.build_init_reply("cost", RUN, 0, 100, "plan", "because", 80, "good")
    assert a.receive(reply).forward == [reply]


def test_what_the_agent_sends_itself_is_never_in_forward_and_submissions_forward_nothing(agent):
    a = centre(agent)
    a.receive(init_doc(["hub", "security"]))
    step = a.submit_init_reply(RUN, 0, 100, "plan", "because", 80, "good")
    assert step.send and step.forward == []
    assert a.tick(10 ** 9).forward == []


# --- whole runs through a star ----------------------------------------------------------------------

class Star:
    """Spokes that have one link each, to the centre: a Relay alone, or an Agent that has one. A spoke sends to the centre only. The centre
    passes what its relay says to every spoke but the one it came from, and sends its own messages to every spoke."""

    def __init__(self, spokes, hub):
        self.spokes, self.hub, self.queue, self.log = spokes, hub, [], []

    def from_spoke(self, name, step):
        self.queue += [(name, text) for text in step.send]

    def from_hub(self, step):
        for text in step.send:
            self.deliver(text, None)

    def deliver(self, text, origin):
        for name, a in self.spokes.items():
            if name != origin:
                self.from_spoke(name, a.receive(text))

    def pump(self):
        while self.queue:
            origin, text = self.queue.pop(0)
            self.log.append((origin, text))
            if hasattr(self.hub, "should_pass_on"):                         # a centre that is only a relay
                forward, own = self.hub.receive(text), []
            else:
                step = self.hub.receive(text)
                forward, own = step.forward, step.send
            for out in forward:
                self.deliver(out, origin)
            for out in own:
                self.deliver(out, None)

    def drive(self):
        """Answer every task of every agent until none is left."""
        for _ in range(60):
            todo = [(n, a, t) for n, a in self.agents().items() for t in a.due()]
            if not todo:
                return
            for name, a, task in todo:
                if task["kind"] == "init-reply":
                    step = a.submit_init_reply(task["run_id"], 0, 100, f"plan of {name}", "because", 80, "good")
                elif task["kind"] == "score":
                    step = a.submit_score(task["run_id"], [(c["id"], 70, "fine") for c in task["candidates"]])
                else:
                    step = a.submit_repair(task["run_id"], "better", "because", 90, "ok")
                (self.from_hub if a is self.hub else lambda s, name=name: self.from_spoke(name, s))(step)
            self.pump()
        raise AssertionError("the run does not end")

    def agents(self):
        return {**self.spokes, **({"hub": self.hub} if not hasattr(self.hub, "should_pass_on") else {})}


def spokes(agent, *names):
    return {n: agent.Agent(n, uuid_fn=uuids(f"{i:08x}")) for i, n in enumerate(names, 1)}


def test_a_run_goes_through_a_star_whose_centre_is_only_a_relay(agent):
    hub = Relay()
    star = Star(spokes(agent, "alice", "cost", "security"), hub)
    star.from_spoke("alice", star.spokes["alice"].start(["cost", "security"], "pick one", "nothing", "all agree",
                                                          {"type": "string", "description": "d"}, run_id=RUN))
    star.pump()
    star.drive()
    reports = {n: a.report() for n, a in star.spokes.items()}
    assert {n: (r["status"], r["outcome"]) for n, r in reports.items()} == {
        "alice": ("unverified", "SUCCESS"), "cost": ("confirmed", "SUCCESS"), "security": ("confirmed", "SUCCESS")}
    assert reports["cost"]["winner"] == reports["alice"]["winner"] and list(hub.state["runs"]) == [RUN]


def test_a_run_goes_through_a_star_whose_centre_is_a_participant_and_the_reporter(agent):
    hub = centre(agent)
    star = Star(spokes(agent, "alice", "cost", "security"), hub)
    star.from_spoke("alice", star.spokes["alice"].start(["hub", "cost", "security"], "pick one", "nothing", "all agree",
                                                          {"type": "string", "description": "d"}, run_id=RUN))
    star.pump()
    star.drive()
    assert hub.report()["status"] == "confirmed" and hub.report()["outcome"] == "SUCCESS"
    assert {n: a.report()["status"] for n, a in star.spokes.items()} == {"alice": "unverified", "cost": "confirmed", "security": "confirmed"}


def test_a_run_goes_through_a_star_whose_centre_opens_it_and_takes_part(agent):
    hub = centre(agent)
    star = Star(spokes(agent, "cost", "security"), hub)
    star.from_hub(hub.start(["hub", "cost", "security"], "pick one", "nothing", "all agree", {"type": "string", "description": "d"}, run_id=RUN))
    star.pump()
    star.drive()
    assert {n: a.report()["status"] for n, a in star.agents().items()} == {"hub": "confirmed", "cost": "confirmed", "security": "confirmed"}


def test_a_relay_that_restarts_in_the_middle_of_a_run_keeps_passing_it_on(agent):
    store = MemStore(library_relay)
    star = Star(spokes(agent, "alice", "cost", "security"), Relay(store=store))
    star.from_spoke("alice", star.spokes["alice"].start(["cost", "security"], "pick one", "nothing", "all agree",
                                                          {"type": "string", "description": "d"}, run_id=RUN))
    star.pump()
    star.hub = Relay(store=store)                                           # the centre restarts: only the saved run ids remain
    star.drive()
    assert {n: a.report()["status"] for n, a in star.spokes.items()} == {"alice": "unverified", "cost": "confirmed", "security": "confirmed"}


def test_errors_and_resends_after_the_end_still_reach_the_spokes_and_two_runs_share_the_star(agent):
    star = Star(spokes(agent, "alice", "cost", "security"), Relay())
    star.from_spoke("alice", star.spokes["alice"].start(["cost", "security"], "pick one", "nothing", "all agree",
                                                          {"type": "string", "description": "d"}, run_id=RUN))
    star.pump()
    star.drive()
    ask = W.build_error("security", RUN, "cost", "init-reply", "missing", uid="44444444-4444-4444-8444-444444444444")
    star.from_spoke("security", type("S", (), {"send": [ask]})())
    star.pump()
    resends = [t for o, t in star.log if o == "cost" and W.peek(t)["tag"] == "concord-resend"]
    assert resends and W.parse_message(resends[-1])["reply_to"] == "44444444-4444-4444-8444-444444444444"
    # a second run goes through the same centre; messages of a run it never saw go nowhere
    star.from_spoke("alice", star.spokes["alice"].start(["cost", "security"], "again", "nothing", "all agree",
                                                          {"type": "string", "description": "d"}, run_id=OTHER))
    star.pump()
    star.drive()
    assert star.spokes["alice"].report(OTHER)["status"] == "unverified" and star.spokes["cost"].report(OTHER)["status"] == "confirmed"
    before = len(star.log)
    star.from_spoke("security", type("S", (), {"send": [score("33333333-3333-4333-8333-333333333333", "security")]})())
    star.pump()
    assert len(star.log) == before + 1 and not any(t == star.log[-1][1] for o, t in star.log[:-1])      # it reached the centre and went no further
