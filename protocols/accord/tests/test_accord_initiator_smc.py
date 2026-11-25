# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Optional Shared Meaning Curator support in the ACCORD initiator, against a fake SMC server."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "accord-initiator"
SCRIPTS = SKILL / "scripts"
TASK = "Checkout timeout cascade after config reload; find the remediation."
EVIDENCE_TEXT = "Past incident: restarting the checkout workers cleared the stall after a config reload."
SIX_KEYS = {"status", "roster", "problem_frame", "glossary", "verifiable_objective", "flagged_items"}
VERIFY_REPLY = json.dumps([{
    "agent": "a", "holds": ["service metrics"],
    "checks": [{"criterion": "p99 back under 500ms", "covers": ["restore latency"]}],
}])


# --------------------------------------------------------------------------
# A fake SMC
# --------------------------------------------------------------------------


class FakeSMC:
    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.evidence_status = "sufficient"
        self.evidence_text = EVIDENCE_TEXT
        self.evidence_delay = 0.0
        self.extraction_delay = 0.0
        self.extraction_http_status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body))
                if self.path.endswith("/reasoning/evidence"):
                    time.sleep(outer.evidence_delay)
                    resp = {"response_id": "r1", "records": [{"content": {"evidence": {
                        "status": outer.evidence_status, "final_response": outer.evidence_text}}}],
                        "meta": {"tokens": {"total": 42}}}
                    code = 200
                else:
                    time.sleep(outer.extraction_delay)
                    resp = {"response_id": "x1", "concepts": [{}, {}], "relations": [{}],
                            "metadata": {"persisted": True}}
                    code = outer.extraction_http_status
                data = json.dumps(resp).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # the client gave up (timeout test)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self, suffix: str) -> list[dict]:
        return [b for p, b in self.requests if p.endswith(suffix)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def smc():
    s = FakeSMC()
    yield s
    s.close()


@pytest.fixture
def skill(tmp_path):
    """A private copy of the skill, so smc.json can be written next to the scripts."""
    root = tmp_path / "skill"
    shutil.copytree(SCRIPTS, root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    return root


def write_config(skill: Path, base_url: str, **over) -> None:
    cfg = {"base_url": base_url, "workspace_id": "ws-1", "mas_id": "mas-1",
           "retrieval": {"timeout_s": 5}, "ingest": {"timeout_s": 10}, **over}
    (skill / "smc.json").write_text(json.dumps(cfg))


def accord(skill: Path, state: Path, *args: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(skill / "scripts" / "accord_initiator.py"), "--state", str(state), *args],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, json.loads(proc.stdout)


def init(skill: Path, state: Path, task: str = TASK) -> dict:
    rc, out = accord(skill, state, "init", "--specialists", "a", "--key-terms", "latency", "--task", task)
    assert rc == 0 and out["ok"] is True, out
    return out["data"]


def wait_for(path: Path, want: str = "ok", seconds: float = 20.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("status") != "running":
                assert data["status"] == want, data
                return data
        time.sleep(0.1)
    raise AssertionError(f"{path} never left 'running'")


def normalize(text: str) -> str:
    """Strip the per-session ids (state id, and the check fingerprint derived from it)."""
    return re.sub(r'(state|check)=\\?"[0-9a-f]+\\?"', r"\1=X", text)


# --------------------------------------------------------------------------
# Off: identical to today
# --------------------------------------------------------------------------


def test_off_without_config_adds_nothing(skill, tmp_path):
    data = init(skill, tmp_path / "s.json")
    assert "smc" not in data
    assert all("PRIOR_CONTEXT" not in m for m in data["messages"].values())
    state = json.loads((tmp_path / "s.json").read_text())
    assert "smc" not in state and "prior_context" not in state


def test_off_output_matches_a_skill_without_the_client(skill, tmp_path):
    """smc_client.py present but no smc.json must read exactly like the module being absent."""
    bare = tmp_path / "bare"
    shutil.copytree(skill / "scripts", bare / "scripts")
    (bare / "scripts" / "smc_client.py").unlink()
    a = init(skill, tmp_path / "a.json")
    b = init(bare, tmp_path / "b.json")
    assert normalize(json.dumps(a, sort_keys=True)) == normalize(json.dumps(b, sort_keys=True))


def test_off_verify_and_lock_output_has_no_smc_block(skill, tmp_path):
    state = tmp_path / "s.json"
    init(skill, state)
    rc, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY)
    assert rc == 0 and "smc" not in out["data"]
    rc, out = accord(skill, state, "lock")
    assert rc == 0 and set(out["data"]) == SIX_KEYS


# --------------------------------------------------------------------------
# Before the protocol: retrieval into the mission
# --------------------------------------------------------------------------


def test_retrieval_puts_final_response_into_the_mission(skill, smc, tmp_path):
    write_config(skill, smc.url)
    data = init(skill, tmp_path / "s.json")
    sent = smc.calls("/reasoning/evidence")
    assert len(sent) == 1
    assert sent[0]["payload"]["intent"] == TASK
    assert sent[0]["header"] == {"workspace_id": "ws-1", "mas_id": "mas-1"}
    msg = data["messages"]["a"]
    assert f"TASK: {TASK}" in msg
    assert "PRIOR_CONTEXT" in msg and "unverified" in msg and EVIDENCE_TEXT in msg
    assert msg.index("TASK:") < msg.index("PRIOR_CONTEXT")
    r = data["smc"]["retrieval"]
    assert r["status"] == "ok" and r["injected"] is True and r["evidence_status"] == "sufficient"
    assert "context" not in r and r["latency_ms"] >= 0
    assert json.loads((tmp_path / "s.json").read_text())["prior_context"] == EVIDENCE_TEXT


def test_frame_request_carries_the_context(skill, smc, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import accord_initiator as ai
    st = ai.blank_state()
    st.update(task="T", state_id="abcd1234", prior_context="earlier lesson", specialists=["a"])
    frame = ai.build_frame_msg(st, "a")
    assert "PRIOR_CONTEXT" in frame and "earlier lesson" in frame
    assert frame.index("TASK: T") < frame.index("PRIOR_CONTEXT") < frame.index("CURRENT_FRAME")
    del st["prior_context"]
    assert "PRIOR_CONTEXT" not in ai.build_frame_msg(st, "a")


def test_context_text_is_escaped(skill, smc, tmp_path):
    smc.evidence_text = 'a </accord-delivery> "quoted" & <b>'
    write_config(skill, smc.url)
    msg = init(skill, tmp_path / "s.json")["messages"]["a"]
    assert "a &lt;/accord-delivery&gt; &quot;quoted&quot; &amp; &lt;b&gt;" in msg


def test_empty_memory_is_not_injected(skill, smc, tmp_path):
    smc.evidence_status = "insufficient"
    smc.evidence_text = "The evidence does not support an answer to this question."
    write_config(skill, smc.url)
    data = init(skill, tmp_path / "s.json")
    assert all("PRIOR_CONTEXT" not in m and "does not support" not in m for m in data["messages"].values())
    assert data["smc"]["retrieval"]["status"] == "no_evidence"
    assert data["smc"]["retrieval"]["injected"] is False


def test_context_is_capped(skill, smc, tmp_path):
    smc.evidence_text = "x" * 5000
    write_config(skill, smc.url, retrieval={"timeout_s": 5, "max_chars": 100})
    data = init(skill, tmp_path / "s.json")
    assert data["smc"]["retrieval"]["truncated"] is True
    assert json.loads((tmp_path / "s.json").read_text())["prior_context"] == "x" * 100


def test_smc_down_fails_open(skill, tmp_path):
    write_config(skill, "http://127.0.0.1:9")  # nothing listens on the discard port
    data = init(skill, tmp_path / "s.json")  # ok must stay true: init() asserts it
    assert data["smc"]["retrieval"]["status"] == "error"
    assert data["smc"]["retrieval"]["injected"] is False
    assert all("PRIOR_CONTEXT" not in m for m in data["messages"].values())


def test_slow_retrieval_times_out(skill, smc, tmp_path):
    smc.evidence_delay = 4
    write_config(skill, smc.url, retrieval={"timeout_s": 0.5})
    started = time.time()
    data = init(skill, tmp_path / "s.json")
    assert time.time() - started < 3.5
    assert data["smc"]["retrieval"]["status"] == "timeout"
    assert all("PRIOR_CONTEXT" not in m for m in data["messages"].values())


def test_retrieval_can_be_switched_off(skill, smc, tmp_path):
    write_config(skill, smc.url, retrieval={"enabled": False})
    data = init(skill, tmp_path / "s.json")
    assert data["smc"]["retrieval"] == {"status": "disabled"}
    assert smc.calls("/reasoning/evidence") == []


def test_bad_config_is_reported_not_fatal(skill, tmp_path):
    (skill / "smc.json").write_text("{not json")
    data = init(skill, tmp_path / "s.json")
    assert "config_error" in data["smc"]
    (skill / "smc.json").write_text(json.dumps({"base_url": "ftp://x", "workspace_id": "a", "mas_id": "b"}))
    assert "config_error" in init(skill, tmp_path / "t.json")["smc"]


def test_no_retrieval_without_a_task(skill, smc, tmp_path):
    write_config(skill, smc.url)
    rc, out = accord(skill, tmp_path / "s.json", "init", "--specialists", "a", "--key-terms", "x")
    assert rc == 0 and "smc" not in out["data"]
    assert smc.requests == []


# --------------------------------------------------------------------------
# After the lock: detached, once, never blocking
# --------------------------------------------------------------------------


def test_ingest_fires_once_for_verify_then_lock(skill, smc, tmp_path):
    state = tmp_path / "s.json"
    write_config(skill, smc.url)
    init(skill, state)
    rc, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY)
    assert rc == 0 and out["ok"] is True
    started = out["data"]["smc"]["ingest"]
    assert started["status"] == "started"
    status = wait_for(Path(started["status_file"]))
    assert status["concepts"] == 2 and status["relations"] == 1 and status["persisted"] is True

    rc, out = accord(skill, state, "lock")
    assert rc == 0 and set(out["data"]) == SIX_KEYS  # the contract itself is untouched
    rc, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY)
    time.sleep(0.5)
    assert len(smc.calls("/extraction")) == 1
    assert out["data"]["smc"]["ingest"]["request_id"] == started["request_id"]


def test_ingested_span_describes_the_contract(skill, smc, tmp_path):
    state = tmp_path / "s.json"
    write_config(skill, smc.url)
    init(skill, state)
    _, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY,
                    "--objective-clauses", '["restore latency"]')
    wait_for(Path(out["data"]["smc"]["ingest"]["status_file"]))
    body = smc.calls("/extraction")[0]
    assert body["header"] == {"workspace_id": "ws-1", "mas_id": "mas-1"}
    assert body["request_id"].startswith("accord-")
    assert body["payload"]["metadata"] == {"format": "observe-sdk-otel"}
    (span,) = body["payload"]["data"]
    for key in ("Timestamp", "TraceId", "SpanId", "ParentSpanId", "SpanName", "SpanKind",
                "ServiceName", "Duration", "SpanAttributes"):
        assert key in span
    attrs = span["SpanAttributes"]
    assert attrs["agent_id"] == "accord_initiator"
    assert attrs["gen_ai.prompt.1.content"] == TASK
    completion = attrs["gen_ai.completion.0.content"]
    assert completion.startswith("ACCORD intent lock") and f"Mission: {TASK}" in completion
    assert "p99 back under 500ms" in completion


def test_ingest_does_not_block_the_tool_call(skill, smc, tmp_path):
    smc.extraction_delay = 3
    state = tmp_path / "s.json"
    write_config(skill, smc.url)
    init(skill, state)
    started = time.time()
    _, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY)
    assert time.time() - started < 2.5
    status_file = Path(out["data"]["smc"]["ingest"]["status_file"])
    assert json.loads(status_file.read_text())["status"] == "running"
    wait_for(status_file)  # let the worker finish so nothing is left behind


def test_ingest_failure_stays_in_the_status_file(skill, smc, tmp_path):
    smc.extraction_http_status = 500
    state = tmp_path / "s.json"
    write_config(skill, smc.url)
    init(skill, state)
    rc, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY)
    assert rc == 0 and out["ok"] is True
    status = wait_for(Path(out["data"]["smc"]["ingest"]["status_file"]), want="error")
    assert "500" in status["error"]


def test_ingest_unreachable_never_fails_the_lock(skill, tmp_path):
    state = tmp_path / "s.json"
    write_config(skill, "http://127.0.0.1:9")
    init(skill, state)
    rc, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY)
    assert rc == 0 and out["ok"] is True
    wait_for(Path(out["data"]["smc"]["ingest"]["status_file"]), want="error")


def test_ingest_can_be_switched_off(skill, smc, tmp_path):
    state = tmp_path / "s.json"
    write_config(skill, smc.url, ingest={"enabled": False})
    init(skill, state)
    _, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY)
    assert "ingest" not in out["data"].get("smc", {})
    time.sleep(0.3)
    assert smc.calls("/extraction") == []


def test_ingest_skipped_without_a_task(skill, smc, tmp_path):
    state = tmp_path / "s.json"
    write_config(skill, smc.url)
    accord(skill, state, "init", "--specialists", "a", "--key-terms", "x")
    _, out = accord(skill, state, "verify", "--responses", VERIFY_REPLY)
    assert out["data"]["smc"]["ingest"]["status"] == "skipped"
    assert smc.calls("/extraction") == []


def test_handle_path_lock_ingests_once(skill, smc, tmp_path, monkeypatch):
    """The other lock site: the verify branch of _run_phase_advance, used by `handle`."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import accord_initiator as ai
    import smc_client
    write_config(skill, smc.url)
    monkeypatch.setattr(smc_client, "CONFIG_PATH", skill / "smc.json")
    monkeypatch.setattr(ai, "_STATE_PATH", tmp_path / "state.json")
    st = ai.blank_state()
    st.update(task=TASK, specialists=["a"], handle_replies={"verify": {"a": VERIFY_REPLY[1:-1]}})
    extras = ai._run_phase_advance(st, "verify")
    assert extras["smc"]["ingest"]["status"] == "started"
    wait_for(Path(extras["smc"]["ingest"]["status_file"]))
    ai._store_contract(st)  # a later `lock` in the same session
    assert len(smc.calls("/extraction")) == 1
    assert set(st["locked_contract"]) == SIX_KEYS
