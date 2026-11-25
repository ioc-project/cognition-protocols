#!/usr/bin/env python3
#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""Optional Shared Meaning Curator (SMC) support for the ACCORD initiator.

Two things, both off unless ``smc.json`` sits in the skill directory (the parent
of ``scripts/``, found relative to this file, never the working directory):

* before the protocol: ask SMC what it already knows about the task and hand
  back its ``final_response`` text, to be added to the mission as extra context;
* after the lock: send the locked contract to SMC as one OTEL-style span, from a
  detached process, so the tool call never waits for the (slow) extraction.

Standard library only. Nothing here raises into the protocol: every public
function returns a small status dict, and failures stay in that dict.

``smc.json``::

    {
      "base_url": "http://localhost:9004",
      "workspace_id": "<uuid>",
      "mas_id": "<uuid>",
      "retrieval": {"enabled": true, "timeout_s": 15, "max_chars": 1500},
      "ingest":    {"enabled": true, "timeout_s": 300}
    }

Run as a script this file is the detached ingest worker:
``smc_client.py <payload-file> <status-file>``. It is not an ACCORD subcommand.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_NAME = "smc.json"
# <skill>/scripts/smc_client.py -> <skill>/smc.json. Tests point this elsewhere.
CONFIG_PATH = Path(__file__).resolve().parent.parent / CONFIG_NAME

EXTRACTION_PATH = "/api/knowledge-mgmt/extraction"
EVIDENCE_PATH = "/api/knowledge-mgmt/reasoning/evidence"

DEFAULT_RETRIEVAL_TIMEOUT_S = 15.0
DEFAULT_INGEST_TIMEOUT_S = 300.0
DEFAULT_MAX_CHARS = 1500
MAX_INTENT_CHARS = 2000
MAX_RECORD_CHARS = 12000
# What SMC says when its graph holds nothing relevant. Never pass this on as context.
NO_EVIDENCE_TEXT = "the evidence does not support an answer"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def load_config() -> tuple[dict | None, str | None]:
    """Return ``(config, error)``.

    ``(None, None)``: no smc.json, SMC is off and the caller must behave exactly
    as it does without this module. ``(None, "...")``: the file exists but is
    unusable; SMC stays off and the reason is reported.
    """
    path = Path(CONFIG_PATH)
    if not path.is_file():
        return None, None
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"cannot read {path.name}: {exc}"
    if not isinstance(raw, dict):
        return None, f"{path.name} must hold a JSON object"
    base = str(raw.get("base_url") or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None, "base_url must start with http:// or https://"
    ws, mas = str(raw.get("workspace_id") or "").strip(), str(raw.get("mas_id") or "").strip()
    if not ws or not mas:
        return None, "workspace_id and mas_id are both required"
    ret, ing = raw.get("retrieval") or {}, raw.get("ingest") or {}
    if not isinstance(ret, dict) or not isinstance(ing, dict):
        return None, "retrieval and ingest must be JSON objects"
    try:
        cfg = {
            "base_url": base, "workspace_id": ws, "mas_id": mas,
            "retrieval": {
                "enabled": bool(ret.get("enabled", True)),
                "timeout_s": float(ret.get("timeout_s", DEFAULT_RETRIEVAL_TIMEOUT_S)),
                "max_chars": int(ret.get("max_chars", DEFAULT_MAX_CHARS)),
            },
            "ingest": {
                "enabled": bool(ing.get("enabled", True)),
                "timeout_s": float(ing.get("timeout_s", DEFAULT_INGEST_TIMEOUT_S)),
            },
        }
    except (TypeError, ValueError) as exc:
        return None, f"bad number in {path.name}: {exc}"
    return cfg, None


def _post(url: str, body: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - scheme checked in load_config
        return json.loads(resp.read().decode("utf-8"))


def _header(cfg: dict) -> dict:
    return {"workspace_id": cfg["workspace_id"], "mas_id": cfg["mas_id"]}


def _failure(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, TimeoutError) or (
            isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)):
        return "timeout", "request timed out"
    if isinstance(exc, urllib.error.HTTPError):
        return "error", f"HTTP {exc.code}"
    return "error", f"{type(exc).__name__}: {exc}"[:300]


# --------------------------------------------------------------------------
# Retrieval (before the protocol)
# --------------------------------------------------------------------------


def retrieve(cfg: dict, task: str) -> dict:
    """Ask SMC about ``task``. Result keys: status, latency_ms, injected, context, ...

    ``status`` is ``ok`` (context returned), ``no_evidence`` (SMC had nothing
    relevant), ``timeout`` or ``error``. Only ``ok`` carries a non-empty ``context``.
    """
    started = time.monotonic()
    out: dict[str, Any] = {"status": "error", "injected": False, "context": ""}
    try:
        body = {
            "header": _header(cfg),
            "request_id": uuid.uuid4().hex,
            "payload": {
                "intent": task[:MAX_INTENT_CHARS],
                "metadata": {"query_type": "Semantic Graph Traversal"},
            },
        }
        resp = _post(cfg["base_url"] + EVIDENCE_PATH, body, cfg["retrieval"]["timeout_s"])
        out["response_id"] = resp.get("response_id")
        tokens = (resp.get("meta") or {}).get("tokens")
        if isinstance(tokens, dict):
            out["tokens"] = tokens.get("total")
        evidence_status, text = "", ""
        for rec in resp.get("records") or []:
            ev = ((rec or {}).get("content") or {}).get("evidence") or {}
            status = str(ev.get("status") or "")
            final = str(ev.get("final_response") or "").strip()
            evidence_status = evidence_status or status
            if status == "sufficient" and final and NO_EVIDENCE_TEXT not in final.lower():
                evidence_status, text = status, final
                break
        out["evidence_status"] = evidence_status
        if text:
            cap = cfg["retrieval"]["max_chars"]
            out.update(status="ok", injected=True, context=text[:cap], truncated=len(text) > cap)
        else:
            out["status"] = "no_evidence"
    except Exception as exc:  # noqa: BLE001 - fail open, whatever went wrong
        out["status"], out["error"] = _failure(exc)
    out["latency_ms"] = round((time.monotonic() - started) * 1000)
    return out


# --------------------------------------------------------------------------
# Ingest (after the lock)
# --------------------------------------------------------------------------


def _clip(text: Any, limit: int = 600) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def render_contract(task: str, contract: dict) -> str:
    """A readable account of a locked contract.

    SMC's extractor reads the span's prompt and completion text, so this is what
    it turns into concepts and relations. Plain sentences, no JSON.
    """
    lines = [
        "ACCORD intent lock: the specialist agents agreed on what the mission means before acting.",
        f"Mission: {_clip(task, 1500)}",
        f"Lock status: {contract.get('status', '')}",
    ]
    roster = contract.get("roster") or []
    if roster:
        lines.append("Specialists:")
        for c in roster:
            caps = "; ".join(_clip(x, 120) for x in (c.get("capabilities") or [])[:6])
            lines.append(f"- {c.get('identity')}: roles {', '.join(c.get('roles') or [])}. Can: {caps}.")
    frame = contract.get("problem_frame") or []
    if frame:
        lines.append("Agreed problem frame (support = how many specialists raised it independently):")
        for a in frame:
            tag = "single specialist, unverified" if a.get("flagged_singleton") else f"support {a.get('support')}"
            lines.append(f"- [{a.get('id')}, {tag}] {_clip(a.get('text'))}")
    glossary = contract.get("glossary") or []
    if glossary:
        lines.append("Terms the team defined:")
        for g in glossary:
            note = "defined differently by different specialists" if g.get("status") == "flagged" else "agreed"
            defs = "; ".join(f"{v.get('agent')}: {_clip(v.get('definition'), 200)}"
                             for v in (g.get("variants") or [])[:4])
            lines.append(f"- {g.get('term')} ({note}) {defs}".rstrip())
    vo = contract.get("verifiable_objective") or {}
    owners = vo.get("owners") or {}
    if owners:
        lines.append("Who owns which knowledge:")
        lines += [f"- {_clip(item, 200)} is owned by {who}" for item, who in owners.items()]
    for u in vo.get("uncovered") or []:
        lines.append(f"- Knowledge nobody held: {_clip(u, 300)}")
    checks = vo.get("checks") or []
    if checks:
        lines.append("Checks the result must pass:")
        lines += [f"- {_clip(c.get('atom'), 300)} -> pass when: {_clip(c.get('criterion'), 300)} "
                  f"(assigned to {c.get('assigned_to')})" for c in checks]
    flagged = contract.get("flagged_items") or []
    if flagged:
        lines.append("Flagged items: " + "; ".join(_clip(f, 200) for f in flagged))
    text = "\n".join(lines)
    return text if len(text) <= MAX_RECORD_CHARS else text[: MAX_RECORD_CHARS - 1] + "…"


def build_ingest_body(cfg: dict, task: str, contract: dict, request_id: str) -> dict:
    digest = hashlib.sha256(request_id.encode()).hexdigest()
    span = {
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "TraceId": digest[:32],
        "SpanId": digest[32:48],
        "ParentSpanId": "",
        "SpanName": "accord.intent_lock",
        "SpanKind": "Server",
        "ServiceName": "accord-initiator",
        "Duration": 1_000_000_000,
        "SpanAttributes": {
            "agent_id": "accord_initiator",
            "gen_ai.prompt.0.role": "system",
            "gen_ai.prompt.0.content": (
                "You are the ACCORD initiator. You convene specialist agents, merge their problem "
                "frames and lock a verified intent contract before any action is taken."),
            "gen_ai.prompt.1.role": "user",
            "gen_ai.prompt.1.content": _clip(task, 1500),
            "gen_ai.completion.0.content": render_contract(task, contract),
        },
    }
    return {
        "header": _header(cfg),
        "request_id": request_id,
        "payload": {"metadata": {"format": "observe-sdk-otel"}, "data": [span]},
    }


def request_id_for(contract: dict) -> str:
    blob = json.dumps(contract, sort_keys=True, separators=(",", ":"), default=str)
    return "accord-" + hashlib.sha256(blob.encode()).hexdigest()[:24]


def _write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def spawn_ingest(cfg: dict, task: str, contract: dict, runtime_dir: Path, stem: str) -> dict:
    """Start the ingest in a detached process and return at once.

    The child's standard streams go to /dev/null and it gets its own session, so
    the calling tool does not wait for it. It writes ``<stem>.smc_ingest.json``
    when done. Never raises.
    """
    try:
        request_id = request_id_for(contract)
        runtime_dir = Path(runtime_dir).resolve()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        payload_path = runtime_dir / f"{stem}.smc_payload.json"
        status_path = runtime_dir / f"{stem}.smc_ingest.json"
        job = {
            "url": cfg["base_url"] + EXTRACTION_PATH,
            "timeout_s": cfg["ingest"]["timeout_s"],
            "request_id": request_id,
            "body": build_ingest_body(cfg, task, contract, request_id),
        }
        _write_json(status_path, {"status": "running", "request_id": request_id})
        payload_path.write_text(json.dumps(job))
        os.chmod(payload_path, 0o600)
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, str(Path(__file__).resolve()), str(payload_path), str(status_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True,
        )
        return {"status": "started", "request_id": request_id, "pid": proc.pid,
                "status_file": str(status_path)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}


def run_ingest_job(payload_path: Path, status_path: Path) -> None:
    """Worker body: do the POST, record the outcome, never raise."""
    started = time.monotonic()
    result: dict[str, Any] = {"status": "error"}
    try:
        job = json.loads(Path(payload_path).read_text())
        result["request_id"] = job.get("request_id")
        resp = _post(job["url"], job["body"], float(job["timeout_s"]))
        meta = resp.get("metadata") or {}
        result.update(
            status="ok",
            concepts=len(resp.get("concepts") or []),
            relations=len(resp.get("relations") or []),
            persisted=meta.get("persisted"),
            response_id=resp.get("response_id"),
        )
        if resp.get("error"):
            result.update(status="error", error=_clip(resp.get("error"), 300))
    except Exception as exc:  # noqa: BLE001
        result["status"], result["error"] = _failure(exc)
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    try:
        _write_json(Path(status_path), result)
    except Exception:  # noqa: BLE001
        pass
    try:
        Path(payload_path).unlink()
    except OSError:
        pass


if __name__ == "__main__":
    if len(sys.argv) == 3:
        run_ingest_job(Path(sys.argv[1]), Path(sys.argv[2]))
    sys.exit(0)
