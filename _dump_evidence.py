"""Compact evidence dump: one representative case per primary topic."""
import asyncio
import json
from pathlib import Path

from student_agent.cases import load_case_set
from student_agent.config import Settings
from student_agent.contracts import Contracts
from student_agent.mcp_gateway import connect_gateway


def compact(ev):
    if isinstance(ev, dict) and "error" in ev:
        return ev
    return {"ref": ev.get("evidence_ref"), "domain": ev.get("domain"), "data": ev.get("data")}


async def main() -> None:
    root = Path(".")
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    case_set = load_case_set(root)

    by_topic = {}
    for case_id, case in case_set.cases.items():
        claims = case["customer_request"]["claims"]
        primary = claims[0]["topic"] if claims else None
        if primary and primary not in by_topic:
            by_topic[primary] = case_id

    out_path = Path("_evidence_dump.json")
    results = {}
    if out_path.exists():
        results = json.loads(out_path.read_text(encoding="utf-8"))

    for topic, case_id in by_topic.items():
        if case_id in results:
            print("skip", case_id)
            continue
        case = case_set.cases[case_id]
        claimed = case["customer_request"]["claimed_order_id"]
        hint = case["customer_unique_id_hint"]
        rec = {"case": case, "tools": {}}
        try:
            async with connect_gateway(
                settings.mcp_endpoint, settings.team_api_key, contracts
            ) as g:
                async def call(tool, **args):
                    key = f"{tool}:{json.dumps(args, sort_keys=True)}"
                    try:
                        ev = await g.call(tool, case_id=case_id, **args)
                        rec["tools"][key] = compact(ev)
                    except Exception as exc:  # noqa: BLE001
                        rec["tools"][key] = {
                            "error": type(exc).__name__, "message": str(exc)
                        }

                await call("get_policy", policy_version=case["policy_version"])
                await call("get_order", order_id=claimed)
                await call("get_order_items", order_id=claimed)
                await call("get_order_payments", order_id=claimed)
                await call("get_payment_timeline", order_id=claimed)
                await call("get_refund_timeline", order_id=claimed)
                await call("get_shipment_summary", order_id=claimed)
                await call("get_sellers", order_id=claimed)
                await call("get_product_context", order_id=claimed)
                await call("get_customer_history", customer_unique_id=hint)
        except Exception as exc:  # noqa: BLE001
            rec["connection_error"] = f"{type(exc).__name__}: {exc}"
        results[case_id] = rec
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("done", case_id, topic)

    print("TOTAL", len(results))


if __name__ == "__main__":
    asyncio.run(main())
