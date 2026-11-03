#!/usr/bin/env python3
"""WatchOnce MCP server (stdio): agent-callable wrapper over the frozen CLI.

Tools: list_examples, analyze_video, get_result, export, list_runs,
send_to_navide. Wraps app/cli.py without touching core logic; paid Meta
calls share the web UI budget file (3 calls / US$0.10).

Run: .venv/bin/python mcp_server.py   (needs requirements-mcp.txt)
Config example: mcp.example.json
"""
import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR / "web"))
sys.path.insert(0, str(APP_DIR / "export"))

from server import (  # noqa: E402
    EXAMPLES,
    MAX_PAID_CALLS,
    MAX_PAID_USD,
    RUNS_DIR,
    clean_path_text,
    effective_meta_key,
    list_runs,
    load_budget,
    safe_run_dir,
    save_budget,
)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
except ImportError:
    sys.stderr.write("mcp_server needs the MCP SDK: .venv/bin/python -m pip install -r requirements-mcp.txt\n")
    raise

server = Server("watch-once")

TOOLS = [
    ("list_examples", "List built-in example videos.",
     {"type": "object", "properties": {}}),
    ("analyze_video", "Analyze a video into manual + operation JSON. Default mock (free).",
     {"type": "object",
      "properties": {
          "path": {"type": "string", "description": "MP4 under app/ (example or path, not both)"},
          "example": {"type": "string", "description": "Built-in example id"},
          "provider": {"type": "string", "enum": ["mock", "meta"], "default": "mock"},
          "confirm_paid": {"type": "boolean", "default": False,
                           "description": "Required true for provider=meta"},
      }}),
    ("get_result", "Read a finished run: manual + operations + state.",
     {"type": "object",
      "properties": {"run_id": {"type": "string"}},
      "required": ["run_id"]}),
    ("export", "Export a run for robots/VLA/planners (annotation layer only).",
     {"type": "object",
      "properties": {"run_id": {"type": "string"},
                     "format": {"type": "string", "enum": ["lerobot", "vla", "plan"]}},
      "required": ["run_id", "format"]}),
    ("list_runs", "List runs with validated operations.json.",
     {"type": "object", "properties": {}}),
    ("send_to_navide", "Show the Navide plan payload for a run (not live-connected).",
     {"type": "object",
      "properties": {"run_id": {"type": "string"}},
      "required": ["run_id"]}),
]


def err_text(exc):
    return clean_path_text(f"{type(exc).__name__}: {exc}")


def text_result(obj):
    return [types.TextContent(type="text",
                              text=json.dumps(obj, ensure_ascii=False)[:30000])]


def resolve_video(path, example):
    if path and example:
        raise ValueError("pass either path or example, not both")
    if example:
        if example not in EXAMPLES:
            raise ValueError(f"unknown example (want one of {sorted(EXAMPLES)})")
        v = (APP_DIR / EXAMPLES[example][1]).resolve()
        if not v.is_file():
            raise ValueError("example file missing on this machine")
        return v
    if not path:
        raise ValueError("pass path or example")
    v = (APP_DIR / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
    if APP_DIR.resolve() not in v.parents and v != APP_DIR.resolve():
        raise ValueError("path must be under app/")
    if v.suffix.lower() != ".mp4" or not v.is_file():
        raise ValueError("path must be an existing .mp4 under app/")
    return v


def run_cli_blocking(video, provider):
    out_name = f"mcp-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = RUNS_DIR / out_name
    cmd = [sys.executable, "-B", "cli.py", str(video),
           "--workspace-path", str(APP_DIR.parent.parent), "--provider", provider,
           "--out", f"runs/{out_name}"]
    env = None
    if provider == "meta":
        cmd.append("--confirm-paid-api")
        if not os.environ.get("META_API_KEY", "").strip():
            key = effective_meta_key()
            if key:
                env = dict(os.environ, META_API_KEY=key)
    try:
        proc = subprocess.run(cmd, cwd=str(APP_DIR), capture_output=True, text=True,
                              timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return {"run_id": out_name, "state": "failed", "error": "CLI timed out after 600s"}
    if proc.returncode != 0:
        tail = clean_path_text((proc.stdout + proc.stderr)[-800:])
        return {"run_id": out_name, "state": "failed", "error": tail or "exit %d" % proc.returncode}
    result = {"run_id": out_name, "state": "done"}
    if provider == "meta":
        try:
            prov = json.loads((out_dir / "provider-result.json").read_text())
            usage = prov.get("metadata", {}).get("usage", prov.get("usage", {}))
            inp = int(usage.get("input_tokens", 0))
            outp = int(usage.get("output_tokens", 0))
            cost = inp * 1.25 / 1e6 + outp * 4.25 / 1e6
            b = load_budget()
            b["paid_calls"] = int(b.get("paid_calls", 0)) + 1
            b["spend_usd"] = round(float(b.get("spend_usd", 0.0)) + cost, 8)
            save_budget(b)
            result["usage"] = {"input_tokens": inp, "output_tokens": outp}
            result["cost_usd"] = round(cost, 8)
        except (ValueError, OSError, KeyError):
            result["state"] = "failed"
            result["error"] = "usage unreadable"
    return result


@server.list_tools()
async def handle_list_tools():
    return [types.Tool(name=n, description=d, inputSchema=s) for n, d, s in TOOLS]


@server.call_tool()
async def handle_call_tool(name, arguments):
    args = arguments or {}
    try:
        if name == "list_examples":
            return text_result([{"id": k, "label": v[0]} for k, v in EXAMPLES.items()])
        if name == "list_runs":
            return text_result(list_runs())
        if name == "analyze_video":
            provider = args.get("provider", "mock")
            if provider not in ("mock", "meta"):
                raise ValueError("provider must be mock or meta")
            video = resolve_video(args.get("path", ""), args.get("example", ""))
            if provider == "meta":
                if args.get("confirm_paid") is not True:
                    raise ValueError("provider=meta needs confirm_paid=true")
                b = load_budget()
                if int(b.get("paid_calls", 0)) >= MAX_PAID_CALLS or \
                        float(b.get("spend_usd", 0.0)) >= MAX_PAID_USD:
                    raise ValueError("paid budget exhausted (3 calls / $0.10)")
                if not effective_meta_key():
                    raise ValueError("no META_API_KEY (export it or use app/.env)")
            return text_result(await asyncio.to_thread(run_cli_blocking, video, provider))
        if name == "get_result":
            d = safe_run_dir(args.get("run_id", ""))
            if d is None:
                raise ValueError("unknown run_id")
            ops_p = d / "operations.json"
            if not ops_p.is_file():
                return text_result({"run_id": d.name, "state": "no-operations",
                                    "note": "log/loop run without validated operations.json"})
            doc = json.loads(ops_p.read_text())
            manual = (d / "manual.md").read_text() if (d / "manual.md").is_file() else ""
            ops_txt = json.dumps(doc, ensure_ascii=False)
            return text_result({"run_id": d.name, "state": "done",
                                "steps": len(doc.get("steps", [])),
                                "manual": manual[:20000], "manual_truncated": len(manual) > 20000,
                                "operations": ops_txt[:20000],
                                "operations_truncated": len(ops_txt) > 20000})
        if name == "export":
            import exporter
            d = safe_run_dir(args.get("run_id", ""))
            if d is None:
                raise ValueError("unknown run_id")
            kind = args.get("format", "")
            if kind not in ("lerobot", "vla", "plan"):
                raise ValueError("format must be lerobot, vla, or plan")
            try:
                payload = exporter.export_run(d, kind, d.name)
            except (ValueError, OSError) as exc:
                raise ValueError(f"export unavailable: {exc}") from None
            return text_result(payload)
        if name == "send_to_navide":
            d = safe_run_dir(args.get("run_id", ""))
            if d is None or not (d / "navide-plan.json").is_file():
                raise ValueError("no payload for this run")
            nav = json.loads((d / "navide-plan.json").read_text())
            if isinstance(nav.get("workspace_path"), str):
                nav["workspace_path"] = "<workspace>"
            sub = {}
            if (d / "submission.json").is_file():
                try:
                    sub = json.loads((d / "submission.json").read_text())
                except ValueError:
                    pass
            out = {"connected": False, "label": "payload generated, not live-connected",
                   "plan_name": nav.get("name"), "stage": nav.get("stage"),
                   "todos": [{"id": t.get("id"), "status": t.get("status")}
                             for t in nav.get("todos", [])],
                   "submission_status": sub.get("status")}
            return text_result(out)
        raise ValueError(f"unknown tool: {name}")
    except (ValueError, OSError) as exc:
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=err_text(exc))])


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
