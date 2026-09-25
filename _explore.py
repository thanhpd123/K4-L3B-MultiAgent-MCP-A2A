"""Exploration helper (not part of the submission)."""
import asyncio
import json
from pathlib import Path

from student_agent.cases import load_case_set
from student_agent.config import Settings
from student_agent.contracts import Contracts
from student_agent.mcp_gateway import connect_gateway


async def main() -> None:
    root = Path(".")
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    case_set = load_case_set(root)
    case_id = "L3B_CASE_001"
    case = case_set.cases[case_id]
    print(json.dumps(case, ensure_ascii=False, indent=2))

    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as g:
        for tool, args in [
            ("get_order", {"order_id": case["customer_request"]["claimed_order_id"]}),
            ("get_order_items", {"order_id": case["customer_request"]["claimed_order_id"]}),
            ("get_order_payments", {"order_id": case["customer_request"]["claimed_order_id"]}),
            ("get_payment_timeline", {"order_id": case["customer_request"]["claimed_order_id"]}),
            ("get_refund_timeline", {"order_id": case["customer_request"]["claimed_order_id"]}),
            ("get_shipment_summary", {"order_id": case["customer_request"]["claimed_order_id"]}),
            ("get_sellers", {"order_id": case["customer_request"]["claimed_order_id"]}),
            ("get_product_context", {"order_id": case["customer_request"]["claimed_order_id"]}),
            ("get_customer_history", {"customer_unique_id": case["customer_unique_id_hint"]}),
            ("get_policy", {"policy_version": case["policy_version"]}),
        ]:
            try:
                ev = await g.call(tool, case_id=case_id, **args)
                print("=" * 80)
                print("TOOL:", tool, args)
                print(json.dumps(ev, ensure_ascii=False, indent=2))
            except Exception as exc:  # noqa: BLE001
                print("=" * 80)
                print("TOOL:", tool, args)
                print("ERROR:", type(exc).__name__, exc)


if __name__ == "__main__":
    asyncio.run(main())
