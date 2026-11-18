#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""How close two pieces of free text are. Every merge in `ioc_accord.engine` is built on this one score: atoms cluster when
they score above a threshold, and a term is agreed when the agents' definitions do.

`sim` is the score the protocol uses: MiniLM cosine when `sentence-transformers` is installed with the model already on
disk, and difflib's ratio otherwise. The import is optional and local, so the library stays stdlib-only; set
ACCORD_EMBEDDING=off to force the string comparison (the deterministic mode the tests run in)."""
import math
import os
import re
from difflib import SequenceMatcher
from typing import Any, Sequence

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    return " ".join(_TOKEN_RE.findall((text or "").lower()))


def _cosine(u: Sequence[float], v: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v))
    nu = math.sqrt(sum(x * x for x in u))
    nv = math.sqrt(sum(y * y for y in v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (nu * nv)))


class Similarity:
    """MiniLM cosine when available, difflib ratio otherwise."""

    def __init__(self) -> None:
        self._embedder: Any = None
        self._tried = False
        self._cache: dict[str, tuple[float, ...]] = {}

    def _get_embedder(self) -> Any:
        if os.environ.get("ACCORD_EMBEDDING", "on").lower() in {"0", "off", "false", "none"}:
            return None
        if not self._tried:
            self._tried = True
            try:
                from sentence_transformers import SentenceTransformer

                model = os.environ.get("ACCORD_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
                self._embedder = SentenceTransformer(model, local_files_only=True)
            except Exception:
                self._embedder = None
        return self._embedder

    def _vectors(self, texts: list[str]) -> list[tuple[float, ...]] | None:
        embedder = self._get_embedder()
        if embedder is None:
            return None
        missing = [t for t in texts if t not in self._cache]
        if missing:
            for text, vec in zip(missing, embedder.encode(missing)):
                self._cache[text] = tuple(float(x) for x in vec)
        return [self._cache[t] for t in texts]

    def __call__(self, a: str, b: str) -> float:
        na, nb = normalize_text(a), normalize_text(b)
        if not na and not nb:
            return 1.0
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        vecs = self._vectors([(a or "").strip(), (b or "").strip()])
        if vecs is None:
            return float(SequenceMatcher(None, na, nb).ratio())
        return _cosine(vecs[0], vecs[1])


sim = Similarity()
