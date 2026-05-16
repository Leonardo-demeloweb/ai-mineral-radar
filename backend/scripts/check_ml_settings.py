"""Diagnostico: ML Commons settings + connectors existentes."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from mcp_servers.common.config import mcp_settings


async def main():
    async with httpx.AsyncClient(
        base_url=mcp_settings.opensearch_endpoint,
        auth=(mcp_settings.opensearch_user, mcp_settings.opensearch_password),
        verify=True, timeout=30,
    ) as c:

        # --- ML Commons settings ---
        r = await c.get("/_cluster/settings?include_defaults=true&filter_path=**.ml_commons")
        ml = r.json().get("defaults", {}).get("plugins", {}).get("ml_commons", {})
        print("=== ML Commons settings ===")
        for k, v in sorted(ml.items()):
            print(f"  {k}: {v}")

        # --- Connectors existentes ---
        body = json.dumps({"query": {"match_all": {}}, "size": 10})
        r2 = await c.post(
            "/_plugins/_ml/connectors/_search",
            content=body.encode(),
            headers={"Content-Type": "application/json"},
        )
        hits = r2.json().get("hits", {}).get("hits", [])
        print(f"\n=== Connectors existentes: {len(hits)} ===")
        for h in hits:
            src = h.get("_source", {})
            cid = h.get("_id")
            print(f"  id={cid}  name={src.get('name')}  protocol={src.get('protocol')}")

        # --- Models existentes ---
        r3 = await c.post(
            "/_plugins/_ml/models/_search",
            content=body.encode(),
            headers={"Content-Type": "application/json"},
        )
        mhits = r3.json().get("hits", {}).get("hits", [])
        print(f"\n=== Models existentes: {len(mhits)} ===")
        for h in mhits:
            src = h.get("_source", {})
            mid = h.get("_id")
            print(f"  id={mid}  name={src.get('name')}  state={src.get('model_state')}  func={src.get('function_name')}")


asyncio.run(main())
