#
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
#
"""The data tools of the mas-lab use cases (e-commerce, renewal-discount), ported from the original apps without the mas runtime.

An agent calls these to read its data, as in the original apps; what comes back is JSON text. The logic is that of the original python tools
(`apps/e-commerce/v1/tools/tools.py`, `apps/renewal-discount/v1/agents/*/tools/*.py`); the data they read is not a file any more but
`scenario.data`, which the scenario files hold, copied from the original fixtures:

  e-commerce        scenario.data is the fixture (promotion, inventory, policy, capacity, offers, options, private).
  renewal-discount  scenario.data[agent_id] is the `spec` of the fixture of that agent.

`scenario.roster` lists the agents (`id`, `role`, `tools`): an agent may call only the tools its roster entry names, and a role-private tool only
answers its owner. Descriptions and parameters are those of the originals (the python tool contract, which is what the original runtime serves).

    tool_defs(scenario, agent_id) -> {name: {"name", "description", "input_schema"}}      the tools of the agent, for the model
    run_tool(scenario, agent_id, name, args) -> str                                       the JSON text of a call
"""
import json
import re
from typing import Any, Callable, Dict, NamedTuple, Optional

_NO_ARGS: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}


class Tool(NamedTuple):
    name: str
    description: str
    parameters: Dict[str, Any]
    run: Callable[[Any, str, Dict[str, Any]], Any]          # (scenario, agent_id, arguments) -> a JSON-able result
    owner: Optional[str] = None                             # the role a private tool answers to; None: any agent that lists it


REGISTRY: Dict[str, Tool] = {}


def _register(tool):
    REGISTRY[tool.name] = tool
    return tool


# ---------------------------------------------------------------------------------------------------------------------------
# e-commerce: every tool serves one section of the fixture (the originals know nothing about which plan is right)
# ---------------------------------------------------------------------------------------------------------------------------

def _section(key):
    return lambda scenario, agent_id, args: dict(scenario.data.get(key) or {})


_register(Tool("get_promotion_brief",
               "Read the promotion brief: the product, the launch date, the demand forecast and the minimum stock the promotion must launch with.",
               _NO_ARGS, _section("promotion")))
_register(Tool("get_inventory_position",
               "Read the current inventory position for the promoted product: units on hand, in transit, already committed, and the resulting "
               "uncommitted balance.", _NO_ARGS, _section("inventory")))
_register(Tool("get_sourcing_policy",
               "Read the purchasing policy in force: approved suppliers, authorised spend, the hard requirements every plan must satisfy, the "
               "agreed meaning of delivery terms, and the purchasing owner an unresolved case returns to.", _NO_ARGS, _section("policy")))
_register(Tool("get_operating_capacity",
               "Read the receiving side: inbound dock slots available before launch, shift coverage, handling hours and cost per inbound shipment.",
               _NO_ARGS, _section("capacity")))


def _offers(scenario, agent_id, args):
    offers = scenario.data.get("offers") or []
    supplier_id = str(args.get("supplier_id") or "").strip()
    if not supplier_id:
        return {"offers": offers, "count": len(offers)}
    for offer in offers:
        if str(offer.get("supplier_id") or "") == supplier_id:
            return dict(offer)
    return {"error": f"unknown supplier_id {supplier_id!r}", "known_supplier_ids": [o.get("supplier_id") for o in offers]}


_register(Tool("get_supplier_offers",
               "Read the supplier offers collected during the offer window: unit price, minimum order quantity, confirmed capacity, lead time, "
               "delivery basis, payment terms, on-time record and offer validity. Pass supplier_id to narrow to one offer.",
               {"type": "object", "properties": {"supplier_id": {"type": "string", "description": "Optional supplier id; omit for every collected offer."}},
                "required": []}, _offers))


def _plans(scenario, agent_id, args):
    plans = scenario.data.get("options") or []
    return {"plans": plans, "count": len(plans)}


_register(Tool("list_sourcing_plans",
               "List the feasible sourcing plans built from the collected offers, each with its id, summary and measured attributes (supplier mix, "
               "quantities, delivery dates, landed cost, stock buffer at launch, inbound shipments, cash timing).", _NO_ARGS, _plans))

_PLAN_ID = {"type": "object",
            "properties": {"plan_id": {"type": "string", "description": "Optional plan id; omit for the whole file."}},
            "required": []}


def _private(role):
    """One role's confidential file on the candidate plans. Each role sees only its own section; the policy forbids relaying it, so what reaches
    the other roles is a rating, not the finding behind it."""
    def run(scenario, agent_id, args):
        private = (scenario.data.get("private") or {}).get(role) or {}
        by_option = private.get("by_option") or {}
        plan_id = str(args.get("plan_id") or "").strip()
        payload = {"role": role, "classification": "role-confidential",
                   "handling": private.get("handling", "Do not quote or relay. Report a 0-1 rating and a reason that discloses no detail from this file.")}
        if plan_id:
            payload["plan_id"] = plan_id
            payload["findings"] = by_option.get(plan_id, "No separate finding on file.")
            return payload
        payload["findings_by_plan"] = by_option
        return payload
    return run


_register(Tool("get_demand_review",
               "Read your confidential demand review of the candidate plans: sell-through behaviour on comparable promotions, category tolerances and "
               "lost-sale history that are not in the published brief. Pass plan_id for one plan.", _PLAN_ID, _private("merchandising"), "merchandising"))
_register(Tool("get_supplier_assurance",
               "Read your confidential supplier assurance file on the candidate plans: contract remedies, short-ship history and re-bid behaviour that "
               "are not in the published offers. Pass plan_id for one plan.", _PLAN_ID, _private("procurement"), "procurement"))
_register(Tool("get_receiving_readiness",
               "Read your confidential receiving readiness file on the candidate plans: carrier disputes, how delivery dates have actually been "
               "honoured, and slot behaviour that is not in the published capacity. Pass plan_id for one plan.", _PLAN_ID, _private("fulfillment"),
               "fulfillment"))
_register(Tool("get_cash_exposure",
               "Read your confidential cash-exposure file on the candidate plans: committed-cash profile, markdown exposure and forecast-confidence "
               "adjustments that are not in the published rates. Pass plan_id for one plan.", _PLAN_ID, _private("finance"), "finance"))


# ---------------------------------------------------------------------------------------------------------------------------
# renewal-discount: the fixture of each agent is its own, and the tools of an agent read only that
# ---------------------------------------------------------------------------------------------------------------------------

def _spec(scenario, agent_id):
    return (scenario.data or {}).get(agent_id) or {}


def _account(account_name=""):
    return {"type": "object", "properties": {"account_name": {"type": "string", "description": account_name}}, "required": ["account_name"]}


def _parse_percent(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value)
        if m:
            return float(m.group(0))
    return default


def _parse_multiplier(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value)
        if m:
            return float(m.group(0))
    return default


def _parse_currency(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return default
    text = value.strip().upper().replace(",", "")
    m = re.search(r"\$?\s*(-?\d+(?:\.\d+)?)\s*([KMB])?", text)
    if not m:
        return default
    amount = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K":
        amount *= 1_000
    elif suffix == "M":
        amount *= 1_000_000
    elif suffix == "B":
        amount *= 1_000_000_000
    return amount


def _fmt_usd(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _recommend_discount_pct(expected_loss: float, base_renewal: float) -> int:
    """Recommend a discount tier from the expected-loss-to-base-renewal ratio (the generic loss-to-discount table of the original tool)."""
    if base_renewal <= 0:
        return 0
    ratio = expected_loss / base_renewal
    if ratio >= 0.48:
        return 20
    if ratio >= 0.30:
        return 18
    if ratio >= 0.22:
        return 15
    if ratio >= 0.15:
        return 8
    return 5  # Minimal discount


def _calculate_expected_loss(scenario, agent_id, arguments):
    account_name = arguments.get("account_name", "")
    churn_multiplier = arguments.get("churn_multiplier")
    expansion_opportunity_arg = arguments.get("expansion_opportunity")
    spec = _spec(scenario, agent_id)
    if not spec:
        return {"error": "Fixture not found: acme-finance-data.yaml", "account_name": account_name}
    # Cross-domain data (churn multiplier, expansion value) is intentionally absent from this agent's own fixture: it belongs to peer agents'
    # authority domains and must flow in only through an explicit tool argument once a peer states it.
    baseline = spec.get("baseline_metrics", {})
    contracts = spec.get("contract_values", {})
    governance = spec.get("discount_governance", {})
    # (the original ignores spec["spurious_knowledge_annotations"], a test-only noise block, and so does this)
    baseline_pct = _parse_percent(baseline.get("baseline_churn_rate"), 12.0)
    multiplier = _parse_multiplier(churn_multiplier, 1.0)           # explicit peer-sourced argument > baseline (no adjustment)
    adjusted_pct = max(0.0, min(100.0, baseline_pct * multiplier))
    base_renewal = _parse_currency(contracts.get("base_renewal"), 0.0)
    expansion = _parse_currency(expansion_opportunity_arg, 0.0)     # only counted when a peer supplies it via the argument
    total_at_risk = base_renewal + expansion
    expected_loss = (adjusted_pct / 100.0) * total_at_risk
    recommended_pct = _recommend_discount_pct(expected_loss, base_renewal)
    discount_cost = (recommended_pct / 100.0) * base_renewal
    finance_ceiling = _parse_percent(governance.get("finance_self_approval_ceiling"), 15.0)
    director_threshold = _parse_percent(governance.get("director_approval_threshold"), 15.0)
    approval_required = bool(recommended_pct > director_threshold)
    if recommended_pct <= finance_ceiling:
        approval_tier = "No additional approval"
    elif recommended_pct <= director_threshold:
        approval_tier = "Director sign-off"
    else:
        approval_tier = "Director sign-off"
    return {
        "account": account_name,
        "baseline_churn_rate": f"{baseline_pct:.0f}%",
        "cs_churn_multiplier": f"{multiplier:.2f}x",
        "adjusted_churn_rate": f"{adjusted_pct:.0f}%",
        "base_renewal_value": _fmt_usd(base_renewal),
        "expansion_at_risk": _fmt_usd(expansion),
        "total_at_risk": _fmt_usd(total_at_risk),
        "expected_loss": _fmt_usd(expected_loss),
        "calculation": "adjusted_churn_rate × total_at_risk",
        "finance_ceiling": f"{finance_ceiling:.0f}%",
        "director_threshold": f"{director_threshold:.0f}%",
        "recommended_discount": f"{recommended_pct}%",
        "discount_cost": _fmt_usd(discount_cost),
        "approval_required": approval_required,
        "approval_tier": approval_tier,
        "justification": f"Expected loss {_fmt_usd(expected_loss)} vs discount cost {_fmt_usd(discount_cost)}",
        "objective_signals": {
            "primary_objective": "maximize_expected_retained_value_minimize_discount_cost",
            "discount_policy": "lowest_discount_that_still_mitigates_expected_loss",
            "loss_to_discount_ratio": round((expected_loss / discount_cost), 3) if discount_cost > 0 else None,
        },
        "constraint_signals": {
            "finance_self_approval_ceiling": f"{finance_ceiling:.0f}%",
            "director_approval_threshold": f"{director_threshold:.0f}%",
            "approval_required": approval_required,
        },
    }


_register(Tool("calculate_expected_loss",
               "Calculate expected loss and optimal discount for a renewal contract, using this agent's own baseline assumptions by default. "
               "WHEN TO RECALCULATE: (1) When a peer authoritative for churn-risk assessment provides a NEW numeric churn multiplier that differs "
               "from the current assumption, (2) When a peer authoritative for deal/expansion data states a numeric expansion value tied to this "
               "renewal that is not yet reflected in the calculation, (3) When any other quantitative evidence changes a calculation input. "
               "DO NOT RECALCULATE if peers only provide qualitative statements without new numbers.",
               {"type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "Name of the account to analyze"},
                    "churn_multiplier": {"type": "number", "description": (
                        "Churn risk multiplier from an authoritative churn-risk peer's validated analysis. If omitted, uses baseline (1.0 = no "
                        "adjustment). Provide this parameter ONLY when a peer explicitly states a numeric multiplier.")},
                    "expansion_opportunity": {"type": "string", "description": (
                        "Expansion or additional contract value tied to this renewal (e.g., '$150K'), as stated by a peer authoritative for "
                        "deal/expansion data. This agent's own fixture intentionally does not include expansion data — it is not this agent's "
                        "domain. If omitted, the calculation only covers the base renewal value.")}},
                "required": ["account_name"]},
               _calculate_expected_loss, "finance"))


def _approval_policy(scenario, agent_id, arguments):
    account_name = arguments.get("account_name", "")
    spec = _spec(scenario, agent_id)
    if not spec:
        return {"error": "Fixture not found: acme-finance-data.yaml", "account_name": account_name}
    governance = spec.get("discount_governance", {})
    return {
        "account": account_name or "Acme Corp",
        "finance_self_approval_ceiling": governance.get("finance_self_approval_ceiling", "15%"),
        "director_approval_threshold": governance.get("director_approval_threshold", "15%"),
        "policy": "Discounts at or below the self-approval ceiling require no sign-off. Discounts above the ceiling require director sign-off.",
    }


_register(Tool("get_discount_approval_policy",
               "Query the finance governance system for discount approval-gate thresholds: self-approval ceiling and director sign-off threshold.",
               _account("Name of the account to check governance policy for"), _approval_policy, "finance"))


def _health_score(scenario, agent_id, arguments):
    account_name = arguments.get("account_name", "")
    spec = _spec(scenario, agent_id)
    if not spec:
        return {"error": "Health scoring system unavailable", "account_name": account_name}
    health = spec.get("current_health", {})
    return {
        "account": health.get("account", account_name),
        "health_score": health.get("health_score", 42),
        "threshold": health.get("health_threshold", 45),
        "status": health.get("status", "Below threshold"),
        "last_updated": "2026-08-30",
    }


_register(Tool("get_health_score", "Query customer health scoring system for current health metrics", _account("Name of the account"),
               _health_score, "customer_success"))


def _usage_trends(scenario, agent_id, arguments):
    account_name = arguments.get("account_name", "")
    time_period = arguments.get("time_period", "last_two_quarters")
    spec = _spec(scenario, agent_id)
    if not spec:
        return {"error": "Analytics platform unavailable", "account_name": account_name}
    usage = spec.get("usage_trends", {})
    return {
        "account": account_name,
        "period": time_period,
        "trend": usage.get("trend_direction", "Down"),
        "decline_pct": usage.get("decline_percentage", "32%"),
        "baseline_comparison": usage.get("baseline_comparison", "30% below baseline"),
        "measurement_window": usage.get("measurement_period", "Last two quarters"),
    }


_register(Tool("get_usage_trends", "Query usage analytics platform for account activity trends",
               {"type": "object",
                "properties": {"account_name": {"type": "string", "description": "Name of the account"},
                               "time_period": {"type": "string", "enum": ["last_quarter", "last_two_quarters", "last_year"],
                                               "description": "Time period for trend analysis"}},
                "required": ["account_name"]}, _usage_trends, "customer_success"))


def _cohort(scenario, agent_id, arguments):
    account_name = arguments.get("account_name", "")
    include_fields = arguments.get("include_fields", ["cohort_size", "churn_multiplier", "confidence"])
    spec = _spec(scenario, agent_id)
    if not spec:
        return {"error": "Data warehouse unavailable", "account_name": account_name}
    validated = spec.get("cohort_analysis", {}).get("validated_cohort", {})
    result = {"account": account_name, "query_timestamp": "2026-08-30T10:30:00Z"}
    # only the requested fields (mimics SQL SELECT)
    if "cohort_size" in include_fields or not include_fields:
        result["cohort_size"] = validated.get("size", 3200)
    if "churn_multiplier" in include_fields or not include_fields:
        result["churn_multiplier"] = validated.get("observed_churn_multiplier", "3.4x")
        result["churn_multiplier_numeric"] = 3.4
    if "confidence" in include_fields or not include_fields:
        result["confidence_level"] = validated.get("confidence", "High")
    if "matching_criteria" in include_fields or not include_fields:
        result["profile_match"] = validated.get("matching_profile", "Same health + usage pattern")
    return result


_register(Tool("validate_churn_risk_with_cohort_database",
               "Query data warehouse to validate churn risk with multi-year cohort analysis. "
               "WHEN TO USE: (1) When peers challenge your evidence strength (\"Is that a broad pattern?\"), "
               "(2) When peers say they \"can't justify higher\" based on current evidence, "
               "(3) When you need validated statistical multipliers to strengthen your case, "
               "(4) When initial sample size is questioned and you need to upgrade to full cohort. "
               "RETURNS: Validated cohort size (a large number of accounts), a numeric churn multiplier, confidence level, and matching criteria.",
               {"type": "object",
                "properties": {"account_name": {"type": "string", "description": "Name of the account to find matching cohort profile"},
                               "include_fields": {"type": "array", "items": {"type": "string"}, "description": (
                                   "Optional fields to include: cohort_size, churn_multiplier, confidence, matching_criteria. If omitted, returns "
                                   "all fields.")}},
                "required": ["account_name"]}, _cohort, "customer_success"))


def _opportunity(scenario, agent_id, arguments):
    account_name = arguments.get("account_name", "")
    spec = _spec(scenario, agent_id)
    if not spec:
        return {"error": "Fixture not found: acme-salesforce-data.yaml", "account_name": account_name}
    account = spec.get("account", {})
    opportunity = spec.get("opportunity", {})
    timeline = spec.get("timeline", {})
    bundled = spec.get("bundled_contracts", {})
    expansion = opportunity.get("expansion_opportunity", {})
    # (the original ignores spec["spurious_knowledge_annotations"], a test-only noise block, and so does this)
    return {
        "account": account.get("name", "Acme Corp"),
        "renewal_value": opportunity.get("renewal_value", "$400K"),
        "expansion_opportunity": f"{expansion.get('amount', '$150K')} ({expansion.get('stage', 'Committed')} stage, {expansion.get('timeline', 'next quarter')})",
        "renewal_deadline": timeline.get("renewal_deadline", "3 weeks"),
        "consolidation_risk": f"{bundled.get('consolidation_risk', 'High')} - {bundled.get('impact', 'bundled renewals at risk')}",
        "objective_signals": {
            "primary_objective": "maximize_renewal_close_and_preserve_expansion",
            "renewal_value": opportunity.get("renewal_value", "$400K"),
            "portfolio_value_at_risk": bundled.get("total_portfolio_value", "$850K"),
            "expansion_dependency": expansion.get("dependencies", "Tied to base renewal staying active"),
        },
        "constraint_signals": {
            "deadline": timeline.get("renewal_deadline", "3 weeks"),
            "consolidation_risk": bundled.get("consolidation_risk", "High"),
        },
    }


_register(Tool("query_salesforce_opportunity",
               "Query Salesforce for opportunity details, timeline, and expansion opportunities tied to a renewal. Returns real-time deal data. Does "
               "NOT include competitive-threat/RFP/sponsor-sentiment detail — call get_competitive_intelligence for that.",
               _account("Name of the account to query"), _opportunity, "salesforce"))


def _competitive(scenario, agent_id, arguments):
    account_name = arguments.get("account_name", "")
    spec = _spec(scenario, agent_id)
    if not spec:
        return {"error": "Fixture not found: acme-salesforce-data.yaml", "account_name": account_name}
    risk = spec.get("risk_indicators", {})
    bundled = spec.get("bundled_contracts", {})
    threat = risk.get("competitive_threat", {})
    return {
        "account": spec.get("account", {}).get("name", account_name or "Acme Corp"),
        "competitive_threat_status": threat.get("status", "Active"),
        "competitive_threat_source": threat.get("source", "Logged note from last call"),
        "competitive_threat_description": threat.get("description", "Customer evaluating other options"),
        "rfp_status": risk.get("rfp_status", "No formal RFP on file"),
        "executive_sponsor_sentiment": risk.get("executive_sponsor_sentiment", "Evaluating alternatives"),
        "consolidation_risk": bundled.get("consolidation_risk", "High"),
    }


_register(Tool("get_competitive_intelligence",
               "Query competitive-intelligence data for a renewal: active competitive threats, RFP status, and executive sponsor sentiment.",
               _account("Name of the account to query"), _competitive, "salesforce"))


# ---------------------------------------------------------------------------------------------------------------------------
# what the harness calls
# ---------------------------------------------------------------------------------------------------------------------------

def _entry(scenario, agent_id):
    return next((a for a in scenario.roster if a["id"] == agent_id), None)


def tool_defs(scenario, agent_id):
    """The data tools `agent_id` may call, as tool definitions for the model: {name: {"name", "description", "input_schema"}}."""
    entry = _entry(scenario, agent_id)
    out = {}
    for name in (entry or {}).get("tools") or []:
        tool = REGISTRY[name]
        out[name] = {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
    return out


def run_tool(scenario, agent_id, name, args):
    """The JSON text of a call of the tool `name` by `agent_id` with `args`. A tool the agent does not have, or a private one of another role,
    answers with an error."""
    def out(result):
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    entry = _entry(scenario, agent_id)
    tool = REGISTRY.get(name)
    if entry is None or tool is None or name not in (entry.get("tools") or []):
        return out({"error": f"{name} is not a tool you have"})
    if tool.owner is not None and tool.owner not in (agent_id, entry.get("role")):
        return out({"error": f"{name} is role-scoped: only {tool.owner} can call it"})
    try:
        return out(tool.run(scenario, agent_id, dict(args or {})))
    except Exception as exc:                                  # never a bare traceback for the model
        return out({"error": f"{name} failed: {type(exc).__name__}: {exc}"})
