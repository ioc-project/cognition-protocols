#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The deterministic merges, one per phase: the roster (CONVENE), the clustered problem frame and its coverage estimators
(FRAME), the glossary and the pairs that need clarifying (GROUND / CLARIFY), the knowledge and check assignment by set
cover (VERIFY), and the contract they add up to (LOCK).

Nothing here talks to an agent or touches a file: a phase merge takes what was said and returns what it means, so the
same code runs under the skill script, under a test, and under any other integration.

The thresholds below are the protocol's only tunables. `SIM_CLARIFY` is the lower bound of the near-match band
[SIM_CLARIFY, SIM_MERGE): cross-cluster pairs that land in it are sent to CLARIFY. Set `SIM_CLARIFY = SIM_MERGE` to empty
the band and skip CLARIFY entirely -- glossary pairs then either agree or stay flagged."""
from ioc_accord.ingest import ingest_roster, ingest_verify
from ioc_accord.similarity import sim

GROUP_THRESHOLD = 0.72
SIM_MERGE = 0.72
SIM_CLARIFY = 0.55
MAX_UNCOVERED_HOLDINGS = 1
MAX_FRAME_ROUNDS = 2
DEFAULT_VERIFIER_ID = "verifier"


def union_roster(cards: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for c in cards:
        out.setdefault(c["identity"], c)
    return list(out.values())


def group_atoms(observations: list[dict]) -> list[dict]:
    """Cluster similar atoms; support = distinct contributing agents."""
    clusters: list[list[dict]] = []
    for obs in observations:
        for cl in clusters:
            if sim(obs["text"], cl[0]["text"]) >= GROUP_THRESHOLD:
                cl.append(obs)
                break
        else:
            clusters.append([obs])
    species = []
    for i, cl in enumerate(clusters, start=1):
        agents = list(dict.fromkeys(o["agent"] for o in cl))
        species.append({
            "id": f"s{i}",
            "text": cl[0]["text"],
            "type": cl[0]["kind"],
            "support": len(agents),
            "agents": agents,
        })
    return species


def good_turing_missing_mass(support: list[int]) -> float:
    n = sum(support)
    return 1.0 if n <= 0 else sum(1 for s in support if s == 1) / n


def chao1(support: list[int]) -> float:
    s_obs = len(support)
    f1 = sum(1 for s in support if s == 1)
    f2 = sum(1 for s in support if s == 2)
    if s_obs == 0:
        return 0.0
    return s_obs + (f1 * f1 / (2.0 * f2) if f2 else f1 * max(f1 - 1, 0) / 2.0)


def build_glossary(terms: list[tuple[str, str, str]]) -> tuple[list[dict], list[dict]]:
    """Per term, cluster agents' units by similarity.

    One cluster -> ``agreed``. Several -> ``flagged`` with per-agent variants.
    Cross-cluster pairs scoring in [SIM_CLARIFY, SIM_MERGE) are ``ambiguous``
    and should go through CLARIFY.
    """
    by_term: dict[str, list[tuple[str, str]]] = {}
    for term, unit, agent in terms:
        by_term.setdefault(term, []).append((unit, agent))

    entries: list[dict] = []
    ambiguous: list[dict] = []
    for term, items in by_term.items():
        clusters: list[list[int]] = []
        for i, (unit, _) in enumerate(items):
            for cl in clusters:
                if sim(unit, items[cl[0]][0]) >= SIM_MERGE:
                    cl.append(i)
                    break
            else:
                clusters.append([i])
        if len(clusters) == 1:
            agents = list(dict.fromkeys(a for _, a in items))
            entries.append({"term": term, "status": "agreed",
                            "definition": items[0][0], "agents": agents})
            continue
        entries.append({
            "term": term, "status": "flagged",
            "variants": [{"agent": items[i][1], "definition": items[i][0]}
                         for cl in clusters for i in cl],
        })
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                for i in clusters[x]:
                    for j in clusters[y]:
                        s = sim(items[i][0], items[j][0])
                        if SIM_CLARIFY <= s < SIM_MERGE:
                            ambiguous.append({
                                "term": term,
                                "agents": [items[i][1], items[j][1]],
                                "units": [items[i][0], items[j][0]],
                                "sim": round(s, 3),
                            })
    return entries, ambiguous


def greedy_set_cover(universe: list[str], covers: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    remaining = set(universe)
    owned: dict[str, list[str]] = {a: [] for a in covers}
    while remaining:
        best, gain = "", []
        for agent, items in covers.items():
            g = [i for i in items if i in remaining]
            if len(g) > len(gain):
                best, gain = agent, g
        if not best:
            break
        owned[best].extend(gain)
        remaining.difference_update(gain)
    return owned, [i for i in universe if i in remaining]


def assign_holdings(universe: list[str], covers: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    """Set-cover knowledge ownership; the verifier never owns knowledge."""
    filtered = {a: list(v) for a, v in covers.items() if a.strip().lower() != DEFAULT_VERIFIER_ID}
    owned, uncovered = greedy_set_cover(universe, filtered)
    for a in covers:
        owned.setdefault(a, [])
    # Tolerate up to MAX_UNCOVERED_HOLDINGS gaps; assign extras to the first agent.
    while len(uncovered) > MAX_UNCOVERED_HOLDINGS and filtered:
        owned[next(iter(filtered))].append(uncovered.pop(0))
    return owned, uncovered


def _covered_clauses(check: dict, clauses: list[str]) -> set[int]:
    covers = check.get("covers", [])
    return {
        i for i, cl in enumerate(clauses)
        if cl in covers or any(cl.strip().lower() in cv.strip().lower() for cv in covers)
    }


def assign_checks(checks_raw: list[dict], clauses: list[str]) -> list[dict]:
    """Greedy set-cover selection of acceptance checks over objective clauses."""
    uncovered = set(range(len(clauses)))
    remaining = list(checks_raw)
    selected = []
    while uncovered and remaining:
        best = max(remaining, key=lambda c: len(uncovered & _covered_clauses(c, clauses)))
        gain = uncovered & _covered_clauses(best, clauses)
        if not gain:
            break
        uncovered -= gain
        remaining.remove(best)
        selected.append({
            # The atom is the objective clause this check discharges, not a
            # prefix of the criterion -- slicing the criterion produced
            # mid-word fragments that made the contract unreadable.
            "atom": clauses[min(gain)],
            "criterion": best.get("criterion", ""),
            "assigned_to": best.get("agent", ""),
            "covers": best.get("covers", []),
        })
    return selected


def assemble_lock(st: dict) -> dict:
    frame, glossary = st["frame"], st["glossary"]
    defects = list(st["defects"])
    if len(frame) > 3 and all(sp["support"] == 1 for sp in frame):
        defects.append(f"fragile_frame: all {len(frame)} atoms are unverified singletons")
    defects += [f"ambiguous_term: {g['term']}" for g in glossary if g["status"] == "flagged"]
    defects += [f"uncovered_atom: {u}" for u in st["uncovered"]]
    defects += list(st.get("coverage_warnings") or [])
    owners = {item: agent for agent, items in st["owned"].items() for item in items}
    return {
        "status": "INTENT-LOCKED",
        "roster": st["roster"],
        "problem_frame": [{"id": s["id"], "text": s["text"], "support": s["support"],
                           "flagged_singleton": s["support"] == 1} for s in frame],
        "glossary": glossary,
        "verifiable_objective": {
            "owners": owners,
            "uncovered": st["uncovered"],
            "checks": [{"atom": c["atom"], "criterion": c["criterion"],
                        "assigned_to": c["assigned_to"]} for c in st["checks"]
                       if c.get("atom") and c.get("criterion")],
        },
        "flagged_items": defects,
    }


def store_contract(st: dict) -> dict:
    """Assemble the contract and keep it in the session state, so later phases can hand out the same one."""
    contract = assemble_lock(st)
    st["locked_contract"] = contract
    return contract


def merge_roster(st: dict, raw: list) -> list[dict]:
    """CONVENE: the union of the identity cards, and the duplicate exclusive roles they reveal."""
    st["roster"] = union_roster(ingest_roster(raw))
    owners: dict[str, list[str]] = {}
    for c in st["roster"]:
        for role in c["roles"]:
            owners.setdefault(role.strip().lower(), []).append(c["identity"])
    st["defects"] = [f"duplicate exclusive role '{r}' claimed by {o}"
                     for r, o in owners.items() if len(o) > 1]
    return st["roster"]


def record_frame_round(st: dict, by_agent: dict[str, list[dict]]) -> list[dict]:
    """FRAME: an agent that submitted this round replaces its own atoms, then the whole pool is clustered again.

    `by_agent` comes from `ingest.ingest_frame_round`, so a caller can refuse an unreadable round before any of this
    happens.
    """
    submitted = set(by_agent)
    st["obs"] = [obs for obs in st["obs"] if obs["agent"] not in submitted]
    for atoms in by_agent.values():
        st["obs"].extend(atoms)
    st["frame_rounds"] += 1
    st["frame"] = group_atoms(st["obs"]) if st["obs"] else []
    return st["frame"]


def record_glossary(st: dict, terms: list[tuple[str, str, str]]) -> tuple[list[dict], list[dict]]:
    """GROUND and CLARIFY: -> (entries, ambiguous).

    CLARIFY re-grounds only the terms it was asked about, so every term this round did not speak to is kept instead of
    replacing the whole glossary.
    """
    entries, ambiguous = build_glossary(terms)
    revised = {e["term"] for e in entries}
    st["glossary"] = [e for e in st["glossary"] if e["term"] not in revised] + entries
    return entries, ambiguous


def record_verify(st: dict, raw: list, required: list[str], clauses: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """VERIFY: who owns which knowledge item, and which checks discharge the objective. -> (covers, universe).

    `universe` falls back to what the specialists happened to report when no required knowledge was given, in which
    case no gap can ever appear: say so in `coverage_warnings` before locking.
    """
    covers, checks_raw, all_holds = ingest_verify(raw)
    universe = required or all_holds
    st["owned"], st["uncovered"] = assign_holdings(universe, covers)
    st["checks"] = assign_checks(checks_raw, clauses)
    return covers, universe
