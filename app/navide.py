import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from common import AppError


def plan_payload(document, manifest, workspace_path):
    if not Path(workspace_path).is_absolute():
        raise AppError("External Navide caller requires an absolute --workspace-path.")
    source = document["source"]
    marker = "MOCK fixture" if source["mock"] else "Meta output / human review"
    frames = {f["id"]: f for f in manifest["frames"]}
    todos = []
    for step in document["steps"]:
        refs = [{k: frames[ref][k] for k in ("id", "file", "source_frame", "pts", "sample_time_ms")} for ref in step["source_frame_refs"]]
        content = (
            f"[{marker}] {step['id']} | {step['start_ms']}–{step['end_ms']} ms\n"
            + json.dumps(step["concept"], ensure_ascii=False) + "\n"
            + "Source: " + json.dumps(source, ensure_ascii=False) + "\n"
            + f"PTS time_base={manifest['time_base']}; timeline_origin_ms={manifest['timeline_origin_ms']}\n"
            + "Frames: " + json.dumps(refs, ensure_ascii=False) + "\n"
            + "待人工檢視：由外層判斷實體/軟體操作與執行權限；此待辦不會自動派工或控制設備。"
        )
        todos.append({"id": step["id"], "content": content, "status": "pending"})
    return {"name": f"[{marker}] 影片操作概念 {source['video_sha256'][:12]}",
            "overview": f"[{marker}] 影片 → 五欄操作概念 → pending 待辦。來源 {source['video_name']}，SHA256 {source['video_sha256']}。僅供人檢視，未派工、未執行、未完成 Agentic 驗收。",
            "stage": "draft", "todos": todos, "workspace_path": str(workspace_path)}


async def submit_plan(payload, timeout_seconds=30):
    if not Path(payload.get("workspace_path", "")).is_absolute():
        raise AppError("External Navide caller requires an absolute workspace_path.")
    url = os.environ.get("NAVIDE_MCP_URL")
    if not url:
        return {"status": "exported_not_sent", "reason": "NAVIDE_MCP_URL is not configured; JSON export only."}
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise AppError("NAVIDE_MCP_URL is invalid; URL withheld.")
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:
        raise AppError("Install requirements-mcp.txt before sending to Navide.") from None
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        async with asyncio.timeout(timeout_seconds):
            async with streamablehttp_client(url, timeout=timeout_seconds, sse_read_timeout=timeout_seconds) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("plan_create", payload)
        if result.isError:
            raise AppError("Navide plan_create returned an error; no automatic retry.")
        data = getattr(result, "structuredContent", None)
        if not isinstance(data, dict):
            data = None
            for item in result.content:
                if getattr(item, "type", None) == "text":
                    try:
                        candidate = json.loads(item.text)
                    except ValueError:
                        continue
                    if isinstance(candidate, dict) and "rel_path" in candidate:
                        data = candidate
                        break
        if not isinstance(data, dict) or data.get("ok") is False or not all(isinstance(data.get(k), str) and data[k] for k in ("rel_path", "name", "stage")):
            raise AppError("Navide receipt is missing rel_path/name/stage; creation unknown. Do not retry blindly.")
        return {"status": "submitted", **{k: data[k] for k in ("rel_path", "name", "stage")}}
    except AppError:
        raise
    except Exception:
        raise AppError("Navide request failed or timed out; creation unknown. No automatic retry; inspect Navide before retrying.") from None
    finally:
        logging.disable(previous_logging)
