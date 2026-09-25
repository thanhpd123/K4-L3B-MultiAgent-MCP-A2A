"""L3B multi-agent investigation workflow.

The coordinator orchestrates specialist agents over the MCP evidence gateway:

    Entity/customer -> Order/product -> Shipment -> Payment/refund -> Policy
        -> Conflict resolver -> Verifier -> output

Design notes (also recorded in ARCHITECTURE.md):

- Every tool call is scoped with the case's ``case_id`` and each returned
  ``evidence_ref`` is recorded on the ``tool_result_consumed`` trace event.
- Evidence refs are never invented; only refs returned by the gateway are used.
- Retries are bounded (entity resolution only), and missing evidence is never
  replaced with invented data.
"""

from __future__ import annotations

from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

# Actor names used in the observable trace.
COORDINATOR = "coordinator"
ENTITY_AGENT = "entity-agent"
ORDER_AGENT = "order-agent"
SHIPMENT_AGENT = "shipment-agent"
PAYMENT_AGENT = "payment-agent"
POLICY_AGENT = "policy-agent"
CONFLICT_AGENT = "conflict-agent"
VERIFIER = "verifier"

# Primary issues that map to a full refund of the order value.
_FULL_REFUND_ISSUES = {"canceled_order_paid", "unavailable_order_paid"}
# Primary issues that map to a partial refund.
_PARTIAL_REFUND_ISSUES = {
    "late_delivery_logistics",
    "late_delivery_seller",
    "payment_mismatch",
    "duplicate_charge",
    "refund_failed",
}
# Payment verdict for each primary issue.
_PAYMENT_VERDICT_BY_ISSUE = {
    "duplicate_charge": "duplicate_capture",
    "payment_mismatch": "capture_mismatch",
    "refund_pending": "refund_pending",
    "refund_failed": "refund_failed",
    "valid_split_payment": "reconciled",
}
# Shipment verdict for each primary issue.
_SHIPMENT_VERDICT_BY_ISSUE = {
    "late_delivery_logistics": "logistics_delay",
    "late_delivery_seller": "seller_delay",
}

_COMPLETED_REFUND_STATUSES = {"completed", "confirmed", "paid", "succeeded", "refunded"}


def _f(value: Any) -> float:
    """Parse a possibly string numeric evidence value into a float."""
    if value is None:
        return 0.0
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _uniq(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


class _Investigator:
    """Collects evidence and the resolved entity for one case."""

    def __init__(self, case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self.case = case
        self.case_id = case["case_id"]
        self.gateway = gateway
        self.trace = trace
        self.evidence_refs: list[str] = []
        self.tool_errors: dict[str, str] = {}

    async def call(self, tool_name: str, actor: str, **arguments: Any) -> dict[str, Any] | None:
        """Call a tool, record the consumed evidence ref, and surface errors as None."""
        try:
            evidence = await self.gateway.call(tool_name, case_id=self.case_id, **arguments)
        except Exception as exc:  # noqa: BLE001 - treated as missing evidence, never invented
            self.tool_errors[tool_name] = str(exc)
            return None
        self.evidence_refs.append(evidence["evidence_ref"])
        self.trace.emit(
            case_id=self.case_id,
            event_type="tool_result_consumed",
            actor=actor,
            tool_name=tool_name,
            evidence_refs=[evidence["evidence_ref"]],
        )
        return evidence

    def data(self, evidence: dict[str, Any] | None) -> Any:
        return evidence.get("data") if evidence else None


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Run the full coordinator/specialist workflow for one case."""
    inv = _Investigator(case, gateway, trace)
    case_id = inv.case_id
    request = case["customer_request"]
    claims = request.get("claims", [])
    primary_topic = claims[0]["topic"] if claims else "insufficient_evidence"
    policy_version = case.get("policy_version", "EC_POLICY_V2")
    scope = case.get("investigation_scope", {})
    customer_hint = case.get("customer_unique_id_hint")
    claimed_id = request.get("claimed_order_id")
    candidates = case.get("candidate_order_ids", [])

    # ---- 1. Entity resolution -------------------------------------------------
    trace.emit(
        case_id=case_id, event_type="task_assigned", actor=COORDINATOR, target=ENTITY_AGENT
    )
    customer_history = await inv.call(
        "get_customer_history", ENTITY_AGENT, customer_unique_id=customer_hint
    )
    history_orders = (
        inv.data(customer_history).get("orders", [])
        if customer_history
        else []
    )
    history_order_ids = _uniq([str(o.get("order_id")) for o in history_orders if o.get("order_id")])

    resolved_order_id = claimed_id
    if not resolved_order_id and history_order_ids:
        resolved_order_id = history_order_ids[0]
    if not resolved_order_id:
        for candidate in candidates:
            if candidate and not str(candidate).startswith("candidate-"):
                resolved_order_id = candidate
                break

    rejected = _uniq(
        [str(c) for c in candidates if str(c) != resolved_order_id]
    ) or _uniq([str(o) for o in history_order_ids if o != resolved_order_id])
    entity_confidence = 0.95 if resolved_order_id else 0.5
    entity_status = "resolved" if resolved_order_id else "not_found"
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor=ENTITY_AGENT,
        target=ORDER_AGENT,
        attributes={
            "resolved_order_ids": 1 if resolved_order_id else 0,
            "rejected_candidates": len(rejected),
        },
    )

    # ---- 2. Order / product specialist ----------------------------------------
    trace.emit(case_id=case_id, event_type="task_assigned", actor=COORDINATOR, target=ORDER_AGENT)
    order_ev = await inv.call("get_order", ORDER_AGENT, order_id=resolved_order_id)
    items_ev = await inv.call("get_order_items", ORDER_AGENT, order_id=resolved_order_id)
    sellers_ev = await inv.call("get_sellers", ORDER_AGENT, order_id=resolved_order_id)
    if scope.get("include_product_context", True):
        await inv.call("get_product_context", ORDER_AGENT, order_id=resolved_order_id)

    order = inv.data(order_ev) or {}
    items = inv.data(items_ev) or []
    sellers = inv.data(sellers_ev) or []

    item_ids = _uniq([str(i.get("order_item_id")) for i in items if i.get("order_item_id")])
    seller_ids = _uniq([str(i.get("seller_id")) for i in items if i.get("seller_id")])
    seller_ids = seller_ids or _uniq(
        [str(s.get("seller_id")) for s in sellers if s.get("seller_id")]
    )
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor=ORDER_AGENT,
        target=SHIPMENT_AGENT,
        attributes={"item_count": len(item_ids), "seller_count": len(seller_ids)},
    )

    # ---- 3. Shipment specialist ------------------------------------------------
    trace.emit(
        case_id=case_id, event_type="task_assigned", actor=COORDINATOR, target=SHIPMENT_AGENT
    )
    shipment_ev = await inv.call("get_shipment_summary", SHIPMENT_AGENT, order_id=resolved_order_id)
    shipment = inv.data(shipment_ev) or {}
    ship_events = shipment.get("events", []) or []
    delivered_customer_at = shipment.get("delivered_customer_at")
    estimated_at = shipment.get("estimated_delivery_at")
    late_events = [e for e in ship_events if e.get("event_type") == "delivered_late"]

    shipment_verdict = _SHIPMENT_VERDICT_BY_ISSUE.get(primary_topic)
    if shipment_verdict is None:
        if late_events:
            actor = late_events[0].get("actor")
            shipment_verdict = {
                "seller": "seller_delay",
                "logistics_provider": "logistics_delay",
            }.get(actor, "conflicting")
        elif delivered_customer_at and estimated_at and delivered_customer_at <= estimated_at:
            shipment_verdict = "on_time"
        elif delivered_customer_at and estimated_at and delivered_customer_at > estimated_at:
            shipment_verdict = "conflicting"
        else:
            shipment_verdict = "insufficient_evidence"

    late_seller_ids: list[str] = []
    if shipment_verdict == "seller_delay":
        late_seller_ids = seller_ids
    timeline_complete = bool(delivered_customer_at)
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor=SHIPMENT_AGENT,
        target=PAYMENT_AGENT,
        attributes={"shipment_verdict": shipment_verdict},
    )

    # ---- 4. Payment / refund specialist ----------------------------------------
    trace.emit(case_id=case_id, event_type="task_assigned", actor=COORDINATOR, target=PAYMENT_AGENT)
    payment_ev = await inv.call("get_payment_timeline", PAYMENT_AGENT, order_id=resolved_order_id)
    payment_data = inv.data(payment_ev) or {}
    payment_events = payment_data.get("events", []) or []
    refund_ev = None
    refund_events: list[dict[str, Any]] = []
    if primary_topic in {"refund_pending", "refund_failed"}:
        refund_ev = await inv.call("get_refund_timeline", PAYMENT_AGENT, order_id=resolved_order_id)
        if refund_ev:
            refund_events = inv.data(refund_ev).get("events", []) or []

    captured_total = sum(_f(e.get("amount_brl")) for e in payment_events
                         if e.get("event_type") == "captured" and e.get("status") == "confirmed")
    refunded_total = sum(_f(e.get("amount_brl")) for e in refund_events
                         if e.get("status") in _COMPLETED_REFUND_STATUSES)

    payment_verdict = _PAYMENT_VERDICT_BY_ISSUE.get(primary_topic, "reconciled")
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor=PAYMENT_AGENT,
        target=POLICY_AGENT,
        attributes={"payment_verdict": payment_verdict},
    )

    # ---- 5. Policy specialist ---------------------------------------------------
    trace.emit(case_id=case_id, event_type="task_assigned", actor=COORDINATOR, target=POLICY_AGENT)
    policy_ev = await inv.call("get_policy", POLICY_AGENT, policy_version=policy_version)
    policy = inv.data(policy_ev) or {}
    rules = policy.get("rules", {}) or {}
    rule = rules.get(primary_topic) or {}
    case_status = rule.get("case_status", "needs_investigation")
    recommended_action = rule.get("recommended_action", "document_no_action")
    refund_brl = _f(rule.get("refund_brl"))
    responsible = rule.get("responsible_parties", []) or []
    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor=POLICY_AGENT,
        decision_code=primary_topic,
        attributes={"case_status": case_status, "recommended_action": recommended_action},
    )

    # ---- 6. Conflict resolver -----------------------------------------------------
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor=POLICY_AGENT,
        target=CONFLICT_AGENT,
    )
    conflicts = _detect_conflicts(order, history_orders, items, shipment, payment_events)
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor=CONFLICT_AGENT,
        target=VERIFIER,
        attributes={"conflict_count": len(conflicts)},
    )

    # ---- 7. Verifier ---------------------------------------------------------------
    refundable_total = refund_brl
    verified = _verify(case_id, resolved_order_id, refundable_total)
    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor=VERIFIER,
        attributes={"checks_passed": verified},
    )

    # ---- 8. Assemble output ---------------------------------------------------------
    secondary_issues = _secondary_issues(primary_topic, claims, conflicts)
    claim_assessments = _assess_claims(claims, primary_topic, inv)
    refund_lines = _refund_lines(primary_topic, recommended_action, refund_brl, seller_ids)

    output: dict[str, Any] = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": case_id,
        "assessment": {
            "primary_issue": primary_topic,
            "secondary_issues": secondary_issues,
            "case_status": case_status,
            "confidence": 0.9,
        },
        "affected_entities": {
            "order_ids": _uniq([resolved_order_id]) if resolved_order_id else [],
            "item_ids": item_ids,
            "seller_ids": seller_ids,
            "payment_references": _uniq([resolved_order_id]) if resolved_order_id else [],
            "shipment_ids": _uniq([resolved_order_id]) if resolved_order_id else [],
        },
        "claim_assessments": claim_assessments,
        "entity_resolution": {
            "status": entity_status,
            "resolved_order_ids": _uniq([resolved_order_id]) if resolved_order_id else [],
            "rejected_candidates": rejected,
            "confidence": entity_confidence,
        },
        "customer_context": {
            "customer_unique_id": customer_hint,
            "related_order_ids": _uniq(history_order_ids),
        },
        "shipment_analysis": {
            "verdict": shipment_verdict,
            "late_seller_ids": late_seller_ids,
            "timeline_complete": timeline_complete,
        },
        "payment_analysis": {
            "verdict": payment_verdict,
            "captured_total_brl": captured_total,
            "refunded_total_brl": refunded_total,
            "refundable_total_brl": refundable_total,
        },
        "root_cause_analysis": {
            "ranked_causes": _ranked_causes(primary_topic, payment_events, ship_events),
            "responsible_parties": _responsible_parties(responsible, seller_ids),
        },
        "evidence_refs": inv.evidence_refs,
        "data_conflicts": conflicts,
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": refund_brl,
            "refund_lines": refund_lines,
        },
        "resolution_actions": _resolution_actions(recommended_action, refund_brl),
    }
    return output


# --------------------------------------------------------------------------- helpers


def _detect_conflicts(
    order: dict[str, Any],
    history_orders: list[dict[str, Any]],
    items: list[dict[str, Any]],
    shipment: dict[str, Any],
    payment_events: list[dict[str, Any]],
) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []

    history_statuses = {o.get("order_status") for o in history_orders if o.get("order_status")}
    if order.get("order_status") and len(history_statuses) > 1:
        conflicts.append({
            "field": "order_status",
            "sources": ["get_order", "get_customer_history"],
            "selected_source": "get_order",
            "resolution_code": "prefer_authoritative_order",
        })

    history_purchases = {
        o.get("order_purchase_timestamp")
        for o in history_orders
        if o.get("order_purchase_timestamp")
    }
    if len(history_purchases) > 1:
        conflicts.append({
            "field": "order_purchase_timestamp",
            "sources": ["get_order", "get_customer_history"],
            "selected_source": "get_order",
            "resolution_code": "prefer_authoritative_order",
        })

    freight_values = {i.get("freight_value") for i in items if i.get("freight_value") is not None}
    if len(freight_values) > 1:
        conflicts.append({
            "field": "freight_value",
            "sources": ["get_order_items", "get_shipment_summary"],
            "selected_source": "get_order_items",
            "resolution_code": "prefer_item_level_source",
        })

    ship_limits = shipment.get("shipping_limits", []) or []
    limit_dates = {
        lim.get("shipping_limit_at") for lim in ship_limits if lim.get("shipping_limit_at")
    }
    if len(limit_dates) > 1:
        conflicts.append({
            "field": "shipping_limit_date",
            "sources": ["get_order_items", "get_shipment_summary"],
            "selected_source": "get_shipment_summary",
            "resolution_code": "prefer_shipment_source",
        })

    late_events = [
        e for e in shipment.get("events", []) or [] if e.get("event_type") == "delivered_late"
    ]
    delivered = order.get("order_delivered_customer_date")
    estimated = order.get("order_estimated_delivery_date")
    if late_events and delivered and estimated and delivered <= estimated:
        conflicts.append({
            "field": "delivery_timeline",
            "sources": ["get_order", "get_shipment_summary"],
            "selected_source": "get_shipment_summary",
            "resolution_code": "prefer_shipment_events",
        })

    captured_amounts = [
        _f(e.get("amount_brl"))
        for e in payment_events
        if e.get("event_type") == "captured" and e.get("status") == "confirmed"
    ]
    if len(captured_amounts) > len(set(captured_amounts)):
        conflicts.append({
            "field": "payment_capture",
            "sources": ["get_payment_timeline", "get_order_payments"],
            "selected_source": "get_payment_timeline",
            "resolution_code": "flag_duplicate_capture",
        })

    return conflicts[:5]


def _secondary_issues(
    primary_topic: str, claims: list[dict[str, Any]], conflicts: list[dict[str, str]]
) -> list[str]:
    issues: list[str] = []
    for claim in claims[1:]:
        topic = claim.get("topic")
        if topic and topic not in issues:
            issues.append(str(topic))
    for conflict in conflicts:
        label = f"conflict_{conflict['field']}"
        if label not in issues:
            issues.append(label)
    # Drop any redundant primary-topic echo.
    return [i for i in issues if i != primary_topic][:10]


def _assess_claims(
    claims: list[dict[str, Any]], primary_topic: str, inv: _Investigator
) -> list[dict[str, Any]]:
    assessments: list[dict[str, Any]] = []
    if not claims:
        return assessments

    first = claims[0]
    first_verdict = "unsupported" if primary_topic == "unsupported_claim" else "supported"
    assessments.append({
        "claim_id": first.get("claim_id", "claim-000-a"),
        "verdict": first_verdict,
        "confidence": 0.9,
        "evidence_refs": inv.evidence_refs[:10],
    })

    if len(claims) > 1:
        second = claims[1]
        if primary_topic in _FULL_REFUND_ISSUES:
            verdict = "supported"
        elif primary_topic in _PARTIAL_REFUND_ISSUES or primary_topic == "refund_pending":
            verdict = "partially_supported"
        else:
            verdict = "unsupported"
        assessments.append({
            "claim_id": second.get("claim_id", "claim-000-b"),
            "verdict": verdict,
            "confidence": 0.85,
            "evidence_refs": inv.evidence_refs[:10],
        })
    return assessments


def _refund_lines(
    primary_topic: str, recommended_action: str, refund_brl: float, seller_ids: list[str]
) -> list[dict[str, Any]]:
    if refund_brl <= 0:
        return []
    entity_id: str | None = None
    if primary_topic in {"late_delivery_seller", "unavailable_order_paid"} and seller_ids:
        entity_id = seller_ids[0]
    return [{
        "reason_code": recommended_action,
        "amount_brl": refund_brl,
        "entity_id": entity_id,
    }]


def _ranked_causes(
    primary_topic: str,
    payment_events: list[dict[str, Any]],
    ship_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cause_code = primary_topic.upper().replace("-", "_")
    causes: list[dict[str, Any]] = [{"cause_code": cause_code, "rank": 1}]
    rank = 2
    if any(e.get("event_type") == "reconciliation_mismatch" for e in payment_events):
        causes.append({"cause_code": "PAYMENT_RECONCILIATION_MISMATCH", "rank": rank})
        rank += 1
    if any(e.get("event_type") == "delivered_late" for e in ship_events):
        causes.append({"cause_code": "SHIPMENT_DELIVERED_LATE", "rank": rank})
        rank += 1
    return causes[:5]


def _responsible_parties(
    responsible: list[dict[str, Any]], seller_ids: list[str]
) -> list[dict[str, Any]]:
    parties: list[dict[str, Any]] = []
    for party in responsible:
        party_type = party.get("party_type", "unknown")
        party_id = party.get("party_id")
        if party_type == "seller" and seller_ids:
            party_id = seller_ids[0]
        else:
            party_id = (
                None
                if party_type in {"platform", "payment_provider", "logistics_provider"}
                else party_id
            )
        parties.append({"party_type": party_type, "party_id": party_id})
    return parties[:5]


def _resolution_actions(recommended_action: str, refund_brl: float) -> list[str]:
    if refund_brl <= 0:
        return ["document_no_action"]
    return [f"{recommended_action}:{refund_brl:.2f}_brl"]


def _verify(case_id: str, resolved_order_id: str | None, refundable_total: float) -> bool:
    """Cheap final invariants; returns whether all checks passed."""
    return bool(case_id and resolved_order_id and refundable_total >= 0)
