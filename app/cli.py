#!/usr/bin/env python3
"""Local video -> reviewed-shape concepts -> pending Navide payload."""

import argparse
import asyncio
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys

from common import AppError, read_json, write_json
from navide import plan_payload, submit_plan
from providers import MetaProvider, MockProvider
from validation import validate_operations
from video import extract

APP_DIR = Path(__file__).resolve().parent


def manual(document, manifest):
    source = document["source"]
    marker = "MOCK — 手寫 fixture，未辨識影片或旁白" if source["mock"] else "META — 模型輸出，待人工核對"
    lines = [f"# 操作手冊 [{marker}]", "", f"來源：{html.escape(source['video_name'])}",
             f"SHA256：`{source['video_sha256']}`；來源時長：{source['duration_ms']} ms", "",
             "這份手冊由同一份操作概念 JSON 產生。未接 SAM；圖像是原片抽幀，未生成追蹤或重畫結果。",
             "時間區間是觀察範圍；end_ms 不代表已證實的精確完成時間，done 仍須人工核對。",
             "由外層人工判斷物理/軟體步驟及執行權限；本次沒有 agent 派工或設備控制。", ""]
    frames = {f["id"]: f for f in manifest["frames"]}
    for step in document["steps"]:
        lines.extend([f"## {step['id']} · {step['start_ms']}–{step['end_ms']} ms", ""])
        for key, value in step["concept"].items():
            lines.append(f"- **{key}**：{html.escape(value)}")
        lines.append(f"- **evidence**：{html.escape(step.get('evidence', ''))}")
        lines.append("")
        for ref in step["source_frame_refs"]:
            frame = frames[ref]
            lines.extend([f"![{marker}: {ref}]({frame['file']})", "",
                          f"`{ref}` → decoder frame {frame['source_frame']}, PTS {frame['pts']} "
                          f"(time_base {manifest['time_base']}), sample {frame['sample_time_ms']} ms", ""])
    return "\n".join(lines)


def run(args):
    if not Path(args.workspace_path).is_absolute():
        raise AppError("--workspace-path must be an absolute workspace root.")
    provider = (MetaProvider(args.model, args.confirm_paid_api, args.prompt_version, args.task_hint) if args.provider == "meta"
                else MockProvider(args.mock_response or APP_DIR / "fixtures/synthetic-response.json"))
    if args.provider == "meta" and args.mock_response:
        raise AppError("--mock-response is only valid with --provider mock.")
    out = Path(args.out).resolve() if args.out else APP_DIR / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not out.is_relative_to(APP_DIR):
        raise AppError("Output must remain inside this app directory.")
    if out.exists():
        raise AppError("Output directory already exists; choose a new directory (no overwrite/resend).")
    out.mkdir(parents=True)
    source = {"provider": args.provider, "mock": args.provider == "mock"}
    try:
        schema = read_json(APP_DIR / "operation.schema.json")
        manifest = extract(Path(args.video).resolve(), out, args.provider, args.sample_interval_ms)
        source = manifest["source"]
        write_json(out / "frames.json", manifest)
        document, metadata = provider.generate(Path(args.video).resolve(), manifest, schema)
        write_json(out / "provider-result.json", {"source": source, "metadata": metadata, "response": document})
        validate_operations(document, schema, manifest, out)
        payload = plan_payload(document, manifest, args.workspace_path)
        # No task payload or manual exists before both schema and semantic validation pass.
        write_json(out / "operations.json", document)
        (out / "manual.md").write_text(manual(document, manifest), encoding="utf-8")
        write_json(out / "navide-plan.json", payload)
        receipt = {"status": "exported_not_sent", "reason": "JSON export only; --send-navide was not requested."}
        if args.send_navide:
            write_json(out / "submission.json", {"source": source, "status": "attempting_once",
                       "notice": "A timeout may still create a plan; inspect Navide before any manual retry."})
            receipt = asyncio.run(submit_plan(payload))
        write_json(out / "submission.json", {"source": source, **receipt})
        print(json.dumps({"mock": source["mock"], "provider": args.provider, "output_dir": str(out),
                          "navide_status": receipt["status"], "notice": receipt.get("reason", "Plan created; all todos pending; no dispatch.")}, ensure_ascii=False))
        return out
    except AppError as exc:
        write_json(out / "failure.json", {"source": source, "status": "failed", "error": str(exc), "provider_metadata": exc.details})
        raise


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("video", help="Local original MP4, <=60 seconds")
    result.add_argument("--workspace-path", required=True, help="Absolute Navide workspace root")
    result.add_argument("--provider", choices=("mock", "meta"), default="mock")
    result.add_argument("--model", choices=("muse-spark-1.2", "muse-spark-1.3"), default="muse-spark-1.2")
    result.add_argument("--confirm-paid-api", action="store_true", help="Explicitly permit a paid original-MP4 upload and inference")
    result.add_argument("--prompt-version", choices=("v1", "v2"), default="v2")
    result.add_argument("--task-hint", default="", help="Short operator hint, naming help only (<=200 chars)")
    result.add_argument("--mock-response", help='JSON fixture with only {"steps": [...]}')
    result.add_argument("--sample-interval-ms", type=int, default=1000)
    result.add_argument("--out", help="New output directory inside app/; default runs/<UTC timestamp>")
    result.add_argument("--send-navide", action="store_true", help="One plan_create attempt using only explicit NAVIDE_MCP_URL")
    return result


def main():
    try:
        run(parser().parse_args())
    except AppError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("ERROR: Local processing failed; no remote error or credential details displayed.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
