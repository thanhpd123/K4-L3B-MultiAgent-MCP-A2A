"""Smoke test: run solve_case on a few cases and validate output schema."""
import asyncio
import json
from pathlib import Path

from student_agent.cases import load_case_set
from student_agent.config import Settings
from student_agent.contracts import Contracts
from student_agent.mcp_gateway import connect_gateway
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case


async def main() -> None:
    root = Path(".")
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    case_set = load_case_set(root)

    for case_id in ["L3B_CASE_001", "L3B_CASE_009", "L3B_CASE_010"]:
        case = case_set.cases[case_id]
        trace = TraceWriter(root / "traces" / "smoke.jsonl", contracts)
        async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as g:
            output = await solve_case(case, g, trace)
        try:
            contracts.validate_output(output, f"smoke/{case_id}")
            print("OK", case_id)
        except Exception as exc:  # noqa: BLE001
            print("SCHEMA FAIL", case_id, exc)
            print(json.dumps(output, ensure_ascii=False, indent=2))
            continue
        # print a compact view
        print(json.dumps({
            "assessment": output["assessment"],
            "shipment": output["shipment_analysis"],
            "payment": output["payment_analysis"],
            "financial": output["financial_resolution"],
            "entities": output["affected_entities"],
            "conflicts": output["data_conflicts"],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
