"""Raw MCP JSON-RPC probe over streamable HTTP."""
import asyncio
import json

import httpx2

ENDPOINT = "https://day09-competition.34-142-201-239.sslip.io/mcp"
KEY = "sk-team-hNNAuG5Z30IohvZMW61KCw5w5ID2m_1oeZ_hB759yn4"


async def main() -> None:
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    async with httpx2.AsyncClient(timeout=60.0) as client:
        # initialize
        r = await client.post(ENDPOINT, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "probe", "version": "0"}},
        })
        print("INIT status", r.status_code)
        print("INIT headers", dict(r.headers))
        print("INIT body", r.text[:2000])
        mcp_session_id = r.headers.get("mcp-session-id")
        print("mcp-session-id:", mcp_session_id)

        # tools/call directly (without initialized notification)
        r2 = await client.post(ENDPOINT, headers={**headers, "mcp-session-id": mcp_session_id} if mcp_session_id else headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_policy",
                       "arguments": {"case_id": "L3B_CASE_001", "policy_version": "EC_POLICY_V2"}},
        })
        print("CALL status", r2.status_code)
        print("CALL body", r2.text[:3000])


if __name__ == "__main__":
    asyncio.run(main())
