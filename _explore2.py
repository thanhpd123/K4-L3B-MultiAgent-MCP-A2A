"""Raw tool-call exploration."""
import asyncio
from pathlib import Path

from student_agent.config import Settings
from student_agent.contracts import Contracts
from student_agent.mcp_gateway import connect_gateway


async def main() -> None:
    root = Path(".")
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as g:
        result = await g._session.call_tool(
            "get_policy", arguments={"case_id": "L3B_CASE_001", "policy_version": "EC_POLICY_V2"}
        )
        print("is_error:", getattr(result, "is_error", None))
        print("result_type:", getattr(result, "result_type", None))
        for block in result.content:
            print("BLOCK type:", type(block).__name__)
            print("BLOCK:", repr(block))
        print("structured_content:", repr(getattr(result, "structured_content", None)))


if __name__ == "__main__":
    asyncio.run(main())
