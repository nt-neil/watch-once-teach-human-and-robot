import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from common import AppError


NAVIDE_STRINGS = {
    "en": {"concepts": "Video operation concepts",
           "overview": "Video → five-field operation concepts → pending todos. Source {name}, SHA256 {sha}. For human review only; not dispatched, not executed, no agentic acceptance.",
           "review": "Needs human review: the outer layer judges physical/software operation and permission; this todo never auto-dispatches or controls equipment."},
    "zh-Hant": {"concepts": "影片操作概念",
           "overview": "影片 → 五欄操作概念 → pending 待辦。來源 {name}，SHA256 {sha}。僅供人檢視，未派工、未執行、未完成 Agentic 驗收。",
           "review": "待人工檢視：由外層判斷實體/軟體操作與執行權限；此待辦不會自動派工或控制設備。"},
    "zh-Hans": {"concepts": "视频操作概念",
           "overview": "视频 → 五栏操作概念 → pending 待办。来源 {name}，SHA256 {sha}。仅供人检视，未派工、未执行、未完成 Agentic 验收。",
           "review": "待人工检视：由外层判断实体/软件操作与执行权限；此待办不会自动派工或控制设备。"},
    "ja": {"concepts": "動画操作コンセプト",
           "overview": "動画 → 5フィールド操作コンセプト → pending タスク。ソース {name}、SHA256 {sha}。人手レビュー専用。未割当・未実行、エージェント検収なし。",
           "review": "人手レビュー要：物理/ソフトウェア操作と権限は外層が判断。このタスクは自動割当も機器制御もしない。"},
    "es": {"concepts": "Conceptos de operación del video",
           "overview": "Video → conceptos de operación de cinco campos → tareas pendientes. Fuente {name}, SHA256 {sha}. Solo revisión humana; sin asignar, sin ejecutar, sin aceptación agéntica.",
           "review": "Requiere revisión humana: la capa externa juzga la operación física/software y los permisos; esta tarea nunca se autoasigna ni controla equipos."},
}


def plan_payload(document, manifest, workspace_path, lang="en"):
    if not Path(workspace_path).is_absolute():
        raise AppError("External Navide caller requires an absolute --workspace-path.")
    t = NAVIDE_STRINGS.get(lang, NAVIDE_STRINGS["en"])
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
            + t["review"]
        )
        todos.append({"id": step["id"], "content": content, "status": "pending"})
    return {"name": f"[{marker}] {t['concepts']} {source['video_sha256'][:12]}",
            "overview": f"[{marker}] " + t["overview"].format(name=source['video_name'], sha=source['video_sha256']),
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
