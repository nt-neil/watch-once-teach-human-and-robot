import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
RUN = "web-20261103T083626Z"
def show(title, res):
    t = res.content[0].text
    print(f"\n> call_tool {title}")
    try:
        d = json.loads(t)
    except Exception:
        print(t[:400]); return
    if isinstance(d, dict):
        for k in ("operations", "manual"): 
            if k in d: d[k] = d[k][:160] + " …"
    print(json.dumps(d, ensure_ascii=False, indent=1)[:900])
async def main():
    p = StdioServerParameters(command=".venv/bin/python", args=["-B", "mcp_server.py"])
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("$ mcp client → watch-once (stdio)")
            print("tools:", ", ".join(t.name for t in tools.tools))
            show("list_examples {}", await s.call_tool("list_examples", {}))
            show(f'get_result {{"run_id":"{RUN}"}}', await s.call_tool("get_result", {"run_id": RUN}))
            show(f'export {{"run_id":"{RUN}","format":"vla"}}', await s.call_tool("export", {"run_id": RUN, "format": "vla"}))
asyncio.run(main())
