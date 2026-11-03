#!/usr/bin/env python3
"""Minimal local web UI for the Meta #6 video CLI (stdlib only).

Wraps app/cli.py without touching core logic. Binds 127.0.0.1 only.
Usage: python3 server.py [--port 18766]
"""
import argparse
import hashlib
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

APP_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
DOTENV = APP_DIR / ".env"


def dotenv_value(name, path=None):
    """Read one VAR from app/.env. The value is never logged or displayed."""
    try:
        text = (path or DOTENV).read_text()
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        m = re.match(r"^%s\s*=\s*(.*)$" % re.escape(name), line)
        if m:
            val = m.group(1).strip().strip("'\"")
            if val:
                return val
    return ""


def dotenv_key(path=None):
    """Read META_API_KEY from app/.env. The value is never logged or displayed."""
    return dotenv_value("META_API_KEY", path)


def effective_meta_key():
    return os.environ.get("META_API_KEY", "").strip() or dotenv_key()
RUNS_DIR = APP_DIR / "runs"
UPLOAD_DIR = RUNS_DIR / "web-uploads"
BUDGET_FILE = RUNS_DIR / "web-ui-budget.json"
EVIDENCE_DIR = APP_DIR / "evidence"
MAX_UPLOAD = 100 * 1024 * 1024
DEFAULT_MAX_CALLS = 20
DEFAULT_MAX_USD = 1.00
DEFAULT_MODEL = "muse-spark-1.2"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,80}$")
FRAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.(jpg|jpeg|png)$")


def find_workspace():
    d = APP_DIR
    for _ in range(6):
        if (d / ".agent-team").is_dir():
            return d
        d = d.parent
    return APP_DIR.parent


WORKSPACE = find_workspace()
# Built-in clips for the MCP/programmatic API only; the web UI offers upload only.
EXAMPLES = {
    "m1": ("AIST bimanual: put 2 washers on a shaft (14s)",
           "../materials-survey/research/samples/0001_task_video.mp4",
           "AIST Bimanual Manipulation Dataset · CC BY 4.0"),
    "synthetic": ("Synthetic clip (pipeline check only)", "runs/synthetic-fixture.mp4",
                  "Self-made test card · for pipeline check"),
}


_CSRF_TOKEN = secrets.token_urlsafe(32)
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def _host_of(value):
    m = re.match(r"^(?:https?://)?([^/:]+)", (value or "").strip().lower())
    return m.group(1) if m else ""


def _loopback_host(headers):
    return _host_of(headers.get("Host", "")) in LOCAL_HOSTS


def settings_request_allowed(headers):
    """POST: loopback Host + same-origin proof + (CSRF checked by caller)."""
    if not _loopback_host(headers):
        return False
    for h in ("Origin", "Referer"):
        v = headers.get(h, "")
        if v:
            return _host_of(v) in LOCAL_HOSTS
    return False


def masked_last4():
    key = effective_meta_key()
    return ("****" + key[-4:]) if key else ""


def write_dotenv_key(value):
    """Write META_API_KEY to app/.env (0600), keeping unrelated lines."""
    value = (value or "").strip()
    if not value or len(value) > 500:
        raise ValueError("empty or oversized key")
    lines = []
    if DOTENV.is_file():
        lines = [ln for ln in DOTENV.read_text().splitlines()
                 if not re.match(r"^\s*(export\s+)?META_API_KEY\s*=", ln)]
    lines.append(f"META_API_KEY={value}")
    fd = os.open(str(DOTENV), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, ("\n".join(lines) + "\n").encode())
    finally:
        os.close(fd)
    os.chmod(str(DOTENV), 0o600)


def clear_dotenv_key():
    if not DOTENV.is_file():
        return False
    lines = DOTENV.read_text().splitlines()
    kept = [ln for ln in lines if not re.match(r"^\s*(export\s+)?META_API_KEY\s*=", ln)]
    if len(kept) == len(lines):
        return False
    DOTENV.write_text("\n".join(kept) + ("\n" if kept else ""))
    os.chmod(str(DOTENV), 0o600)
    return True


def esc(s):
    return html.escape(str(s), quote=True)


def clean_path_text(s):
    """Strip absolute local paths before display."""
    s = str(s)
    s = s.replace(str(APP_DIR), "<app>")
    s = s.replace(str(WORKSPACE), "<workspace>")
    s = re.sub(r"/Users/[^/\s]+", "<user>", s)
    return s


def render_markdown(md, run_id):
    """Tiny renderer: headings, lists, images (jailed), code, bold, paragraphs."""
    out = []
    in_code, in_list = False, False
    for line in md.splitlines():
        s = line.rstrip()
        if s.strip().startswith("```"):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<pre>" if not in_code else "</pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(esc(line))
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            lvl = len(m.group(1))
            title = m.group(2)
            if lvl == 1:
                title = title.replace("操作手冊", "Operation manual").replace("組裝檢查表", "Assembly checklist")
            sm = re.match(r"^(\d+)\.\s+step-", title)
            sm2 = re.match(r"^step-0*(\d+)", title)
            if lvl == 3 and sm:
                out.append(f"<h3 id=\"manual-step-{sm.group(1)}\" data-step=\"{sm.group(1)}\" class=\"syncable\">{esc(title)}</h3>")
            elif lvl == 2 and sm2:
                out.append(f"<h2 id=\"manual-step-{sm2.group(1)}\" data-step=\"{sm2.group(1)}\" class=\"syncable\">{esc(title)}</h2>")
            else:
                out.append(f"<h{lvl}>{esc(title)}</h{lvl}>")
            continue
        if re.match(r"^[-*]\s+", s):
            if not in_list:
                out.append("<ul>")
                in_list = True
            item = re.sub(r"^[-*]\s+", "", s)
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(item))
            out.append(f"<li>{item}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not s.strip():
            continue

        def img_sub(mo):
            alt, src = esc(mo.group(1)), mo.group(2)
            name = Path(src).name
            if FRAME_RE.match(name):
                return f'<img alt="{alt}" src="/frames/{run_id}/{name}" loading="lazy">'
            return esc(alt)

        s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", img_sub, esc(s))
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        out.append(f"<p>{s}</p>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</pre>")
    return "\n".join(out)


def parse_multipart(body, boundary):
    """Return (fields, files). files: {name: (filename, bytes)}."""
    fields, files = {}, {}
    for part in body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        data = data[: -len(b"\r\n")] if data.endswith(b"\r\n") else data
        hm = re.search(rb'name="([^"]+)"(?:;\s*filename="([^"]*)")?', head)
        if not hm:
            continue
        name = hm.group(1).decode()
        if hm.group(2) is not None:
            files[name] = (hm.group(2).decode(), data)
        else:
            fields[name] = data.decode(errors="replace")
    return fields, files


def frame_file_for(run_dir, ref_id):
    for cand in sorted((run_dir / "frames").glob(f"*{ref_id}.jpg")):
        if FRAME_RE.match(cand.name):
            return cand.name
    return None


def safe_run_dir(run_id):
    if not RUN_ID_RE.match(run_id or ""):
        return None
    for base in (RUNS_DIR, EVIDENCE_DIR):
        try:
            broot = base.resolve()
        except OSError:
            continue
        d = (base / run_id).resolve()
        if broot not in d.parents and d != broot:
            continue
        if d.is_dir():
            return d
    return None


def list_runs(include_mock=False):
    runs = []
    if not RUNS_DIR.is_dir():
        return runs
    for child in sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not child.is_dir():
            continue
        if child.name.startswith(("_", ".")):
            continue
        ops = child / "operations.json"
        if ops.is_file():
            try:
                doc = json.loads(ops.read_text())
                src = doc.get("source", {})
                if src.get("mock") and not include_mock:
                    continue
                runs.append({
                    "id": child.name,
                    "steps": str(len(doc.get("steps", []))),
                    "provider": src.get("provider", "?"),
                    "mock": bool(src.get("mock", False)),
                    "video": src.get("video_name", "?"),
                    "mtime": datetime.fromtimestamp(child.stat().st_mtime).strftime("%m-%d %H:%M"),
                })
                continue
            except (ValueError, OSError):
                pass
        if (child / "checklist.md").is_file() or (child / "report.md").is_file():
            runs.append({
                "id": child.name, "steps": "—", "provider": "loop/log",
                "mock": False, "video": "—",
                "mtime": datetime.fromtimestamp(child.stat().st_mtime).strftime("%m-%d %H:%M"),
            })
    return runs


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_budget():
    try:
        b = json.loads(BUDGET_FILE.read_text())
    except (ValueError, OSError):
        return {"date": _today(), "paid_calls": 0, "spend_usd": 0.0}
    if b.get("date") and b["date"] != _today():
        return {"date": _today(), "paid_calls": 0, "spend_usd": 0.0}
    b.setdefault("date", _today())
    return b


def save_budget(b):
    RUNS_DIR.mkdir(exist_ok=True)
    b.setdefault("date", _today())
    BUDGET_FILE.write_text(json.dumps(b, indent=2))


def get_limits():
    """Daily paid limits + model. .env/env override the open-source default."""
    calls, usd = DEFAULT_MAX_CALLS, DEFAULT_MAX_USD
    for var, kind in (("WEB_MAX_CALLS_PER_DAY", int), ("WEB_MAX_USD_PER_DAY", float)):
        raw = dotenv_value(var) or os.environ.get(var, "")
        try:
            v = kind(raw)
            if v > 0:
                if kind is int:
                    calls = v
                else:
                    usd = v
        except (ValueError, TypeError):
            pass
    return {"max_calls": calls, "max_usd": usd, "model": DEFAULT_MODEL}


PROGRESS_STAGES = ["Checking video", "Extracting frames", "Sending to Muse Spark",
                   "Model analyzing", "Validating steps", "Writing manual", "Done"]
SENDING_WINDOW_S = 3.0


def derive_stage_index(out_dir, provider, elapsed):
    """Map output files (+provider/elapsed) to a PROGRESS_STAGES index.

    The CLI is a black box (no progress stdout), so "Sending" vs "Analyzing"
    is split by time: a meta run still waiting for provider-result.json past
    SENDING_WINDOW_S is assumed to be analyzing, not uploading.
    """
    if (out_dir / "operations.json").is_file():
        return 5
    if (out_dir / "provider-result.json").is_file():
        return 4
    if (out_dir / "frames.json").is_file():
        if provider == "meta" and elapsed >= SENDING_WINDOW_S:
            return 3
        return 2
    frames_dir = out_dir / "frames"
    try:
        has_jpg = any(frames_dir.glob("*.jpg"))
    except OSError:
        has_jpg = False
    return 1 if has_jpg else 0


def jpeg_size(path):
    """Read WxH from a JPEG SOF marker. "" when unknown. Read-only."""
    try:
        data = Path(path).read_bytes()[:65536]
    except OSError:
        return ""
    i = 2
    while i + 8 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xC0, 0xC2):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return f"{w}×{h}" if w and h else ""
        if m == 0x01 or 0xD0 <= m <= 0xD9:
            i += 2
            continue
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        if ln < 2:
            return ""
        i += 2 + ln
    return ""


def scrub_video_name(raw):
    return re.sub(r"[\\/]", "_", Path(raw or "video").name)[:80]


def mock_fixture_matches(video_path):
    """Mock wraps a hand-written fixture for the synthetic clip only.

    Refuse any other video: running the fixture against a real clip would
    mislabel it (e.g. a robot video "analyzed" as color bars). The CLI core
    is untouched; the wrapper blocks before any run directory is created.
    """
    try:
        ref = RUNS_DIR / "synthetic-fixture.mp4"
        if not ref.is_file():
            return False
        h1, h2 = hashlib.sha256(), hashlib.sha256()
        with open(video_path, "rb") as f:
            for ch in iter(lambda: f.read(65536), b""):
                h1.update(ch)
        with open(ref, "rb") as f:
            for ch in iter(lambda: f.read(65536), b""):
                h2.update(ch)
        return h1.hexdigest() == h2.hexdigest()
    except OSError:
        return False


def lite_validation_checks(out_dir):
    """Re-verify operations.json against frames.json with stdlib only.

    Mirrors validation.py's checks (schema shape, source match, time order,
    5 non-empty fields, frame refs incl. file presence). Returns
    ({name: bool}, steps) or ({}, 0) when the files are not readable yet.
    """
    try:
        ops = json.loads((out_dir / "operations.json").read_text())
        manifest = json.loads((out_dir / "frames.json").read_text())
    except (OSError, ValueError):
        return {}, 0
    steps = ops.get("steps", [])
    if not isinstance(steps, list) or not steps:
        return {}, 0
    if not all(isinstance(s, dict) and isinstance(s.get("concept"), dict) for s in steps):
        return ({"schema": False, "source": False, "time order": False,
                 "5 fields": False, "frame refs": False}, len(steps))
    concept_keys = {"object", "action", "target", "pre", "done"}
    schema_ok = (isinstance(ops.get("source"), dict)
                 and all(isinstance(s, dict) and {"id", "start_ms", "end_ms",
                         "concept", "source_frame_refs"} <= set(s)
                         and isinstance(s.get("concept"), dict)
                         and concept_keys <= set(s["concept"])
                         for s in steps))
    source_ok = ops.get("source") == manifest.get("source")
    try:
        duration = float(manifest["source"]["duration_ms"])
    except (KeyError, TypeError, ValueError):
        duration = -1
    ordered, prev = True, 0
    for s in steps:
        try:
            st, en = float(s["start_ms"]), float(s["end_ms"])
        except (TypeError, ValueError):
            ordered = False
            break
        if not prev <= st < en <= duration:
            ordered = False
            break
        prev = en
    fields_ok = all(isinstance(v, str) and v.strip()
                    for s in steps for v in s["concept"].values())
    frames = {f.get("id"): f for f in manifest.get("frames", [])}
    refs_ok = True
    for s in steps:
        refs = s.get("source_frame_refs", [])
        if len(set(refs)) != len(refs):
            refs_ok = False
            break
        for r in refs:
            f = frames.get(r)
            if f is None:
                refs_ok = False
                break
            try:
                t = float(f.get("sample_time_ms", -1))
                st, en = float(s["start_ms"]), float(s["end_ms"])
            except (TypeError, ValueError):
                refs_ok = False
                break
            if not st <= t < en:
                refs_ok = False
                break
            if not (out_dir / str(f.get("file", ""))).is_file():
                refs_ok = False
                break
    return ({"schema": schema_ok, "source": source_ok, "time order": ordered,
             "5 fields": fields_ok, "frame refs": refs_ok}, len(steps))


def review_flags(out_dir):
    """Non-blocking review hints. Even-split steps may be guessed, not seen."""
    try:
        ops = json.loads((out_dir / "operations.json").read_text())
    except (OSError, ValueError):
        return []
    steps = ops.get("steps", [])
    if not isinstance(steps, list):
        return []
    lens = []
    for s in steps:
        try:
            lens.append(float(s["end_ms"]) - float(s["start_ms"]))
        except (TypeError, ValueError, KeyError):
            return []
    if len(lens) >= 3 and max(lens) > 0 and (max(lens) - min(lens)) / max(lens) < 0.05:
        return [f"Even split: all {len(lens)} steps within 5% — verify boundaries by hand."]
    return []


def job_detail(out_dir):
    """Cheap read-only facts for the progress page. Missing files -> absent keys."""
    d = {}
    frames_dir = out_dir / "frames"
    try:
        files = sorted(p.name for p in frames_dir.glob("*.jpg")) if frames_dir.is_dir() else []
    except OSError:
        files = []
    d["frame_files"] = files[:200]
    d["frame_count"] = len(files)
    try:
        manifest = json.loads((out_dir / "frames.json").read_text())
    except (OSError, ValueError):
        return d
    src = manifest.get("source", {})
    try:
        d["duration_s"] = round(float(manifest.get("source_duration_ms", 0)) / 1000, 1)
    except (TypeError, ValueError):
        pass
    sha = str(src.get("video_sha256", ""))
    if sha:
        d["sha12"] = sha[:12]
    if manifest.get("sample_interval_ms") is not None:
        d["interval_ms"] = manifest["sample_interval_ms"]
    times = []
    for f in manifest.get("frames", []):
        try:
            times.append(round(float(f["sample_time_ms"]) / 1000, 1))
        except (KeyError, TypeError, ValueError):
            pass
    d["sample_times_s"] = times[:6]
    d["sample_total"] = len(manifest.get("frames", []))
    if files:
        d["resolution"] = jpeg_size(frames_dir / files[0])
    checks, n = lite_validation_checks(out_dir)
    if checks:
        d["validation"] = checks
        d["steps"] = n
    flags = review_flags(out_dir)
    if flags:
        d["review_flags"] = flags
    return d

jobs = {}
jobs_lock = threading.Lock()

CSS = """<style>
@font-face{font-family:"Plex Sans";font-weight:400;font-display:swap;src:url("/static/fonts/plex-sans-400.woff2") format("woff2")}
@font-face{font-family:"Plex Sans";font-weight:600;font-display:swap;src:url("/static/fonts/plex-sans-600.woff2") format("woff2")}
@font-face{font-family:"Plex Sans";font-weight:700;font-display:swap;src:url("/static/fonts/plex-sans-700.woff2") format("woff2")}
@font-face{font-family:"Plex Mono";font-weight:400;font-display:swap;src:url("/static/fonts/plex-mono-400.woff2") format("woff2")}
html,body{overflow-x:clip}
body{font-family:"Plex Sans",system-ui,-apple-system,BlinkMacSystemFont,sans-serif;margin:0;background:#f1f3f1;color:#1a2420;font-size:15px;line-height:1.55}
::selection{background:#cfe3d8}
main{max-width:1180px;margin:0 auto;padding:28px 16px 48px;min-width:0}
.topnav{display:flex;justify-content:space-between;align-items:baseline;margin:0 0 4px;font-size:14px}
.brand{font-weight:700;font-size:15px;color:#1a2420}
input[type=password]{font-size:15px;padding:9px 12px;border:1px solid #9aa6b2;border-radius:6px;width:100%;max-width:420px;box-sizing:border-box}
input[type=text]{font-size:15px;padding:9px 12px;border:1px solid #9aa6b2;border-radius:6px;width:100%;max-width:420px;box-sizing:border-box}
input[type=number]{font-size:15px;padding:9px 12px;border:1px solid #9aa6b2;border-radius:6px;width:100%;max-width:200px;box-sizing:border-box}

h1{font-size:32px;line-height:1.2;margin:0 0 6px;letter-spacing:-0.01em}
h2{font-size:19px;margin:22px 0 8px}h3{font-size:16px;margin:16px 0 6px}
.sub{color:#3d4a44;margin:0 0 16px;font-size:16px}
.step .ev{color:#3d4a44}
.hintline{font-size:14px;color:#3d4a44;margin:4px 0}
.warnline{font-size:14px;color:#8a4d00;font-weight:600;margin:4px 0}
.hero{display:grid;grid-template-columns:1.15fr .85fr;gap:20px;align-items:center;margin:2px 0 18px}
@media(max-width:900px){.hero{grid-template-columns:1fr}}
.hero h1{font-size:30px}
.beforeafter{display:grid;grid-template-columns:1fr auto 1.3fr;gap:12px;align-items:center;margin-top:12px}
.hero .beforeafter{margin-top:0}
@media(max-width:700px){.beforeafter{gap:8px}.basteps img{width:56px;height:42px}.basteps li{font-size:12.5px}.ba figcaption{font-size:11px}}
.ba{margin:0}
.ba img{width:100%;border-radius:8px;border:1px solid #d8dee6;display:block}
.ba figcaption{font-size:12.5px;color:#3d4a44;margin-top:6px}
.balead{font-weight:700;margin:0 0 6px}
.basteps{list-style:none;margin:0;padding:0}
.basteps li{display:flex;gap:10px;align-items:center;margin:8px 0;font-size:13.5px;min-width:0}
.basteps img{width:88px;height:66px;object-fit:cover;border-radius:4px;border:1px solid #d8dee6;flex:none}
.basrc{font-size:12.5px;color:#3d4a44;margin:8px 0 0}
details.advlog{margin-top:12px}
.keyline{margin-top:12px;border-top:1px solid #d8dee6;padding-top:8px}
details.advlog summary{cursor:pointer;font-weight:600;color:#3d4a44}
.hero .lede{margin:0}
.hero .beforeafter{min-width:0}

.pipe{display:flex;align-items:stretch;gap:0;margin:10px 0 4px;flex-wrap:wrap}
.node{flex:1 1 0;min-width:150px;background:#ffffff;border:1px solid #d8dee6;border-radius:8px;padding:10px 14px}
.node b{display:block;font-size:14px}.node span{font-size:12.5px;color:#3d4a44}
.arrow{align-self:center;padding:0 10px;font-size:20px;color:#0b5c4c;font-weight:700}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 0;padding:0;list-style:none}
.chips li{font-size:12.5px;font-weight:600;background:#ffffff;border:1px solid #d8dee6;border-radius:99px;padding:3px 12px;color:#3d4a44}
.card{background:#ffffff;border:1px solid #d8dee6;border-radius:8px;padding:16px 18px;margin:14px 0;min-width:0}
.card.scroll{overflow-x:auto}
.errpage{max-width:660px}
.getback-lede{font-weight:700;margin:2px 0 6px;font-size:15px}
.gbgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:8px 0}
@media(max-width:700px){.gbgrid{grid-template-columns:1fr 1fr}}
.gb{background:#f4faf7;border:1px solid #d8dee6;border-radius:8px;padding:10px;font-size:13px;color:#3d4a44;min-width:0}
.gb .gbn{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:99px;background:#0b5c4c;color:#fff;font-size:12px;font-weight:700;margin-right:6px}
.gb b{display:block;color:#1a2420;margin:6px 0 2px;font-size:13.5px}
.fitnote,.limitnote{font-size:13.5px;color:#3d4a44;margin:8px 0 0}
.drop{display:block;border:2px dashed #9aa6b2;border-radius:10px;padding:22px 18px;text-align:center;background:#fafcfb;margin-top:12px;cursor:pointer}
.drop.over{border-color:#0b5c4c;background:#e3efe9}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.dropicon{display:block;font-size:30px;color:#0b5c4c;line-height:1}
.dropmsg{display:block;margin:8px 0 10px;font-size:14px;color:#3d4a44}
.filebtnlike{display:inline-block;font-size:15px;font-weight:600;padding:8px 22px;background:#ffffff;color:#0b5c4c;border:2px solid #0b5c4c;border-radius:6px}
.filename{font-size:14px;margin:10px 0 0;overflow-wrap:break-word}

button.small{font-size:13px;padding:6px 14px;margin-top:8px}
button.textbtn{background:none;border:none;padding:0;margin:0;color:#0b5c4c;font:inherit;font-size:inherit;cursor:pointer;text-decoration:underline}
label.f{display:block;margin:12px 0 4px;font-weight:600}
.selwrap{position:relative;display:block}
.selwrap select{appearance:none;-webkit-appearance:none;font-size:15px;padding:9px 34px 9px 12px;border:1px solid #9aa6b2;border-radius:6px;background:#fff;color:#1a2420;width:100%;cursor:pointer}
.selwrap::after{content:"▾";position:absolute;right:12px;top:50%;transform:translateY(-50%);color:#3d4a44;pointer-events:none}
input[type=checkbox]{accent-color:#0b5c4c;width:17px;height:17px;vertical-align:-3px}
button{font-size:15px;font-weight:600;padding:10px 22px;background:#0b5c4c;color:#ffffff;border:none;border-radius:6px;cursor:pointer;margin-top:14px;transition:background 120ms}
button:hover{background:#08463b}button:disabled{background:#9aa6b2;cursor:default}
button.big{font-size:17px;padding:13px 30px}
button.ghost{background:#fff;color:#0b5c4c;border:2px solid #0b5c4c;padding:6px 14px;font-size:14px}
button.ghost:hover{background:#e3efe9}
.btnrow{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
:focus-visible{outline:3px solid #0b5c4c;outline-offset:2px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.cols>*{min-width:0}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
@media(max-width:640px){main{padding:16px 12px}.card{padding:12px 14px}h1{font-size:26px}.node{min-width:100%;margin-bottom:8px}.arrow{transform:rotate(90deg);padding:2px 0}}
.manual img{max-width:100%;border:1px solid #d8dee6;border-radius:4px;margin:6px 0}
.manual pre,.pane pre,.card pre{background:#edefec;padding:10px;overflow:auto;font-size:13px;color:#1a2420;max-width:100%}
.manual p{overflow-wrap:break-word;line-height:1.55}
.manual h1{font-size:1.25rem;line-height:1.3;margin:12px 0 8px;overflow-wrap:break-word}
.manual h2{font-size:1.15rem;line-height:1.35;margin:14px 0 6px;overflow-wrap:break-word}
.manual h3{font-size:1.1rem;line-height:1.35;margin:12px 0 6px;overflow-wrap:break-word}
.manual h3.syncable,.manual h2.syncable{cursor:pointer;border-radius:4px;padding:2px 6px;margin-left:-6px}
.manual h3.syncable:hover,.manual h2.syncable:hover{background:#e3efe9}
.manual h3.sel,.manual h2.sel{background:#e3efe9;outline:2px solid #0b5c4c}
.pane{max-height:640px;overflow:auto}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:560px}
th,td{border:1px solid #d8dee6;padding:6px 10px;text-align:left}
th{background:#edefec;color:#1a2420}
.step{display:flex;gap:12px;border:1px solid #d8dee6;border-radius:8px;padding:10px;margin:10px 0;cursor:pointer;transition:border-color 120ms,background 120ms}
.step:hover{border-color:#0b5c4c}
.step.sel{border-color:#0b5c4c;background:#e3efe9}
.step img{width:132px;height:99px;object-fit:cover;border-radius:4px;border:1px solid #d8dee6;flex:none}
.step .t b{font-size:15px}.step .t{font-size:13.5px;color:#3d4a44;min-width:0}
.step .t code{font-size:12px}
.step details{margin-top:6px}
.step pre{font-size:12px;max-height:220px}
.badge{display:inline-block;font-size:12px;font-weight:700;border-radius:99px;padding:2px 10px;margin-right:6px}
.badge.live{background:#d3e7d8;color:#166534}.badge.sample{background:#fbdfb9;color:#8a4d00}
.badge.n{background:#e3efe9;color:#0b5c4c}
.stages{list-style:none;margin:6px 0 0;padding:0}
.stages li{position:relative;display:flex;gap:12px;align-items:flex-start;padding:10px 12px;border-radius:8px;overflow:hidden}
.stages li+li{margin-top:4px}
.stages .sic{flex:none;width:26px;height:26px;border-radius:99px;border:2px solid #9aa6b2;color:#3d4a44;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;background:#fff}
.stages li.done .sic{background:#0b5c4c;border-color:#0b5c4c;color:#fff}
.stages li.active{background:#e3efe9}
.stages li.active .sic{background:#0b5c4c;border-color:#0b5c4c;color:#fff;animation:pulse 1.2s ease-in-out infinite}
.stages li.active::after{content:"";position:absolute;left:0;top:0;bottom:0;width:34%;background:linear-gradient(100deg,transparent,rgba(11,92,76,.18),transparent);animation:sweep 1.6s linear infinite}
.stages li.fail{background:#fdeceb}
.stages li.fail .sic{background:#b42318;border-color:#b42318;color:#fff}
.stages .sbody{min-width:0}
.stages .sbody b{font-size:15px}
.stages .sdetail{margin:2px 0 0;font-size:13px;color:#3d4a44;overflow-wrap:break-word}
.stages li.wait{opacity:.75}
@keyframes pulse{50%{opacity:.45}}
@keyframes sweep{from{transform:translateX(-110%)}to{transform:translateX(320%)}}
@keyframes pop{from{opacity:0;transform:translateY(10px)}}
@keyframes scanx{from{transform:translateX(-120%)}to{transform:translateX(520%)}}
@keyframes flyin{from{opacity:0;transform:translateX(26px)}}
.film{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.film img{width:96px;height:72px;object-fit:cover;border-radius:4px;border:1px solid #d8dee6;animation:pop .3s ease-out}
.filmmore{font-size:12.5px;color:#3d4a44;align-self:center}
.scanwrap{position:relative;overflow:hidden;border-radius:8px;background:#1a2420;color:#fff;margin-top:10px;padding:10px 14px}
.scanwrap p{position:relative;margin:0;font-size:14px}
.scanbeam{position:absolute;top:0;bottom:0;left:0;width:22%;background:linear-gradient(100deg,transparent,rgba(125,211,192,.55),transparent);animation:scanx 2s linear infinite}
.donesteps{display:flex;flex-direction:column;gap:8px;margin:8px 0}
.dstep{background:#f4faf7;border:1px solid #0b5c4c;border-radius:8px;padding:8px 12px;font-size:14px;animation:flyin .35s ease-out backwards}
a.btnlink{display:inline-block;font-size:15px;font-weight:700;padding:10px 22px;background:#0b5c4c;color:#fff!important;border-radius:6px;text-decoration:none;margin-top:6px}
.elapsedline{font-size:14px;color:#3d4a44}

.errlinks{margin:10px 0;font-size:15px}
#alog{max-height:220px;overflow:auto}
dialog{border:1px solid #d8dee6;border-radius:10px;padding:20px;max-width:min(520px,calc(100vw - 32px));width:520px;box-sizing:border-box}
dialog::backdrop{background:rgba(26,36,32,.5)}
.dlgclose{margin:0;text-align:right}
.dlgclose button{margin:0;padding:4px 12px}
dialog h2{margin:0 0 8px}
.tl{list-style:none;margin:6px 0 0;padding:0 0 0 4px}
.tl li{position:relative;padding:0 0 14px 26px;cursor:pointer;border-radius:6px}
.tl li::before{content:"";position:absolute;left:7px;top:22px;bottom:-2px;width:2px;background:#d8dee6}
.tl li:last-child::before{display:none}
.tl li::after{content:"";position:absolute;left:2px;top:6px;width:10px;height:10px;border-radius:99px;background:#fff;border:3px solid #0b5c4c}
.tl li:hover::after{background:#0b5c4c}
.tl li.sel{background:#e3efe9}
.tl img{width:64px;height:48px;object-fit:cover;border-radius:4px;border:1px solid #d8dee6;vertical-align:middle;margin-right:8px}
.tl .tt{font-size:13.5px}.tl .tt b{font-size:14px}.tl code{font-size:12px}
details.provenance{margin-top:10px}
details.provenance summary{cursor:pointer;font-weight:600}
.ok{color:#166534;font-weight:700}.warn{color:#8a4d00;font-weight:700}.err{color:#b42318;font-weight:700}
.mono{font-family:"Plex Mono",ui-monospace,SFMono-Regular,monospace;font-size:13px}
a{color:#0b5c4c}
footer{margin-top:28px;color:#3d4a44;font-size:13px}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
</style>"""

HOME_TMPL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Watch Once — video to manual + robot JSON</title>{css}</head>
<body><main>
<p class="topnav"><span class="brand">Watch Once</span><button type="button" class="textbtn" data-opendlg>Settings</button></p>
<div class="hero">
<div class="lede">
<h1>Turn a how-to video into a manual for people and step data for robots.</h1>
<p class="sub">Upload a short video &rarr; we split it into timed steps &rarr; read the manual or export it.</p>
<p><a class="btnlink" href="#pick">Upload a video</a></p>
</div>
{herocompare}
</div>
<div class="card" id="pick"><h2>1. Pick a video</h2>
<form method="post" action="/analyze" enctype="multipart/form-data">
<p class="getback-lede">Upload a how-to video. Get back:</p>
<div class="gbgrid">
<div class="gb"><span class="gbn">1</span><b>Human manual</b><span>step-by-step with key frames</span></div>
<div class="gb"><span class="gbn">2</span><b>Robot-ready step JSON</b><span>object / action / target / pre / done + timestamps</span></div>
<div class="gb"><span class="gbn">3</span><b>Exports</b><span>LeRobot annotations, VLA instructions, planner steps</span></div>
<div class="gb"><span class="gbn">4</span><b>Navide task payload</b><span>generated for review only, not auto-submitted</span></div>
</div>
<p class="fitnote">Best for: one person or machine doing one thing, 10–60s, steady camera, clear actions.</p>
<p class="limitnote">Limits: step splitting is unstable — the same clip may split into 2 or 4 steps. Always review results by hand.</p>
<label class="drop" id="drop" for="filein">
<input type="file" name="video" id="filein" class="vh" accept=".mp4,video/mp4">
<span class="dropicon" aria-hidden="true">⇪</span>
<span class="dropmsg">Drop an MP4 here or click to choose · max 100 MB · stays on this machine</span>
<span class="filebtnlike">Choose file</span>
</label>
<p class="filename" id="fname" hidden></p>
<p><button type="button" class="ghost small" id="rmfile" hidden>Remove</button></p>
<label class="f" for="hintin">What is in the clip? (optional, naming help only)</label>
<div><input type="text" id="hintin" name="task_hint" maxlength="200" autocomplete="off" placeholder="e.g. washers on a shaft"></div>
{cta}
<div class="keyline">
{key_status}
</div>
</form></div>
<div class="card"><h2>How it works</h2>
<div class="pipe" aria-hidden="true">
<div class="node"><b>1 · Video</b><span>Upload MP4 to analyze</span></div><div class="arrow">&rarr;</div>
<div class="node"><b>2 · Steps</b><span>AI splits motion into timed steps</span></div><div class="arrow">&rarr;</div>
<div class="node"><b>3 · Manual + JSON</b><span>Read it, or feed it to a robot</span></div>
</div>
<ul class="chips"><li>Runs 100% on this machine</li><li>Meta Muse Spark, paid by approval</li><li>Every claim traceable to frames</li></ul>
</div>
<div class="card scroll"><h2>2. History</h2>{history}</div>
<footer>Local app · CLI untouched · Meta Muse Spark inside</footer>
</main>
<dialog id="settingsDlg" aria-labelledby="dlgTitle">
<form method="dialog" class="dlgclose"><button type="submit" aria-label="Close">×</button></form>
<h2 id="dlgTitle">Settings</h2>
<p class="mono" id="dlgStatus"></p>
<p class="err" id="dlgErr"></p>
<form id="dlgKeyForm" method="post" action="/settings/save">
<input type="hidden" name="csrf" value="{csrf}">
<label class="f" for="dk">Meta API key (stored in app/.env, file mode 0600)</label>
<div><input type="password" id="dk" name="key" autocomplete="off" placeholder="paste key, save takes effect immediately"></div>
<div class="btnrow"><button type="submit">Save key</button></div>
</form>
<form id="dlgClearForm" method="post" action="/settings/clear" style="margin-top:8px">
<input type="hidden" name="csrf" value="{csrf}">
<button type="submit" class="ghost">Clear key from .env</button>
</form>
</dialog>
<script>
var fi=document.getElementById('filein'),
dz=document.getElementById('drop'),fn=document.getElementById('fname'),rm=document.getElementById('rmfile');
function show(){{if(!fi.files.length){{fn.hidden=true;rm.hidden=true;return;}}
var f=fi.files[0];fn.textContent=f.name+' · '+(f.size/1048576).toFixed(1)+' MB';fn.hidden=false;rm.hidden=false;}}
fi.addEventListener('change',show);
rm.addEventListener('click',function(){{fi.value='';show();}});
['dragenter','dragover'].forEach(function(ev){{dz.addEventListener(ev,function(e){{e.preventDefault();dz.classList.add('over');}});}});
['dragleave','drop'].forEach(function(ev){{dz.addEventListener(ev,function(e){{e.preventDefault();dz.classList.remove('over');}});}});
dz.addEventListener('drop',function(e){{if(e.dataTransfer&&e.dataTransfer.files.length){{try{{fi.files=e.dataTransfer.files;}}catch(err){{}}show();}}}});
show();
var dlg=document.getElementById('settingsDlg'),lastTrig=null;
function syncDlg(){{document.getElementById('dlgStatus').innerHTML=document.getElementById('keywrap').innerHTML;
  document.getElementById('dlgErr').textContent='';}}
if(dlg&&dlg.showModal){{
document.addEventListener('click',function(e){{
  var el=e.target.closest?e.target.closest('[data-opendlg]'):null;
  if(!el)return;
  e.preventDefault();lastTrig=el;syncDlg();dlg.showModal();}});
dlg.addEventListener('close',function(){{if(lastTrig&&document.contains(lastTrig))lastTrig.focus();}});
if(location.hash==='#settings'){{syncDlg();dlg.showModal();}}
async function postDlg(f,ok){{
  var r=await fetch(f.action,{{method:'POST',body:new URLSearchParams(new FormData(f))}});
  if(r.ok){{ok();dlg.close();}}else{{document.getElementById('dlgErr').textContent='Save failed (HTTP '+r.status+').';}}}}
function keepBudget(){{var m=document.getElementById('keywrap').innerHTML.match(/ · Budget used today:[^<]*/);return m?m[0]:'';}}
document.getElementById('dlgKeyForm').addEventListener('submit',function(e){{e.preventDefault();
  var kv=document.getElementById('dk').value;
  postDlg(e.target,function(){{var l4=kv.slice(-4);
    document.getElementById('keywrap').innerHTML='Meta API key: <b class="ok">configured · ****'+l4+'</b> <button type="button" class="textbtn" data-opendlg>Set up</button>'+keepBudget();
    document.getElementById('dk').value='';}});}});
document.getElementById('dlgClearForm').addEventListener('submit',function(e){{e.preventDefault();
  postDlg(e.target,function(){{document.getElementById('keywrap').innerHTML='Meta API key: <b class="warn">not configured</b> <button type="button" class="textbtn" data-opendlg>Set up</button>'+keepBudget();}});}});
}}
</script>
</body></html>"""

JOB_TMPL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Analyzing {job}</title>{css}</head>
<body><main><p><a href="/">&larr; Home</a></p><h1 id="title">Analyzing&hellip;</h1>
<p class="sub mono">{job}</p>
<p class="elapsedline" id="provline">Provider: &hellip;</p>
<p class="elapsedline">Elapsed <b id="elapsed">0.0s</b></p>
<div class="card"><ol class="stages" id="stages">
<li class="wait"><span class="sic">1</span><div class="sbody"><b>Checking video</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">2</span><div class="sbody"><b>Extracting frames</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">3</span><div class="sbody"><b>Sending to Muse Spark</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">4</span><div class="sbody"><b>Model analyzing</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">5</span><div class="sbody"><b>Validating steps</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">6</span><div class="sbody"><b>Writing manual</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">7</span><div class="sbody"><b>Done</b><p class="sdetail"></p></div></li>
</ol></div>
<div class="card" id="filmcard" hidden><h2>Frames</h2><div class="film" id="film"></div>
<div class="scanwrap" id="scanwrap" hidden><div class="scanbeam"></div><p>Muse Spark is watching the video&hellip;</p></div></div>
<div class="card" id="donecard" hidden><h2>Done</h2><div class="donesteps" id="donesteps"></div><p id="donesum"></p><p><a id="openresult" class="btnlink" href="#">Open result</a></p></div>
<p id="err" class="err"></p>
<p class="errlinks" id="errlinks" hidden><a href="/">&larr; Back</a> &middot; <a href="/#pick">Try again</a></p>
<details class="advlog"><summary>Activity log</summary><pre id="alog" class="mono"></pre></details>
</main>
<script>
var NAMES=["Checking video","Extracting frames","Sending to Muse Spark","Model analyzing","Validating steps","Writing manual","Done"];
var IDX={{}};NAMES.forEach(function(n,i){{IDX[n]=i;}});
var shown=0,t0=Date.now(),finale=false,filmSeen={{}};
function tick(){{document.getElementById("elapsed").textContent=((Date.now()-t0)/1000).toFixed(1)+"s";}}
setInterval(tick,200);
function detailFor(k,j,prov){{var d=j.detail||{{}};
  if(k===0){{var p=[];if(d.duration_s!=null)p.push(d.duration_s+" s");if(d.resolution)p.push(d.resolution);if(d.sha12)p.push("sha "+d.sha12);
    return (j.video_name||"video")+(p.length?" · "+p.join(" · "):" · reading file…");}}
  if(k===1){{if(d.sample_total)return "Sampled "+d.sample_total+" frames"+(d.sample_times_s&&d.sample_times_s.length?" at "+d.sample_times_s.join(", ")+" … s":"");
    if(d.frame_count)return d.frame_count+" frames so far";return "Waiting for frames…";}}
  if(k===2){{return prov==="meta"?"Uploading original MP4 to api.meta.ai ("+(j.model||"Muse Spark")+")":"Using the built-in fixture (no model call)";}}
  if(k===3){{return prov==="meta"?"Muse Spark is watching the video and splitting it into steps":"Applying the built-in fixture (no model call)";}}
  if(k===4){{if(!d.validation)return "Checking…";
    var v=Object.keys(d.validation).map(function(n){{return n+" "+(d.validation[n]?"✓":"✗");}}).join(" · ")+(d.steps!=null?" · "+d.steps+" steps":"");
    if(d.review_flags&&d.review_flags.length)v+=" · ⚠ "+d.review_flags.join("; ");
    return v;}}
  if(k===5){{return d.steps!=null?"Manual + robot JSON ready — exports on the result page":"Writing…";}}
  if(j.state==="done"){{var s=(d.steps!=null?d.steps+" steps":"done")+" · "+((Date.now()-t0)/1000).toFixed(1)+"s";
    if(j.cost_usd!=null)s+=" · $"+Number(j.cost_usd).toFixed(4);return s;}}
  return "";}}
function render(j,prov){{
  var lis=document.querySelectorAll("#stages li"),d=j.detail||{{}};
  lis.forEach(function(li,k){{
    var det=li.querySelector(".sdetail"),sic=li.querySelector(".sic");
    det.textContent=detailFor(k,j,prov);
    if(j.state==="failed"&&k===shown){{li.className="fail";sic.textContent="✗";}}
    else if(k<shown||j.state==="done"&&k<=shown){{li.className="done";sic.textContent="✓";}}
    else if(k===shown){{li.className="active";sic.textContent=String(k+1);}}
    else{{li.className="wait";sic.textContent=String(k+1);}}
  }});
  var files=d.frame_files||[],run=j.run_id||"",film=document.getElementById("film");
  if(files.length&&run){{document.getElementById("filmcard").hidden=false;
    files.slice(0,30).forEach(function(fn){{
      if(filmSeen[fn])return;filmSeen[fn]=1;
      var im=document.createElement("img");im.alt=fn;im.loading="lazy";
      im.src="/frames/"+encodeURIComponent(run)+"/"+encodeURIComponent(fn);
      film.appendChild(im);}});
    if(files.length>30&&!document.getElementById("filmmore")){{
      var m=document.createElement("span");m.id="filmmore";m.className="filmmore";
      m.textContent="+"+(files.length-30)+" more";film.appendChild(m);}}
  }}
  document.getElementById("scanwrap").hidden=!(prov==="meta"&&shown===3&&j.state!=="done"&&j.state!=="failed");
  document.getElementById("alog").textContent=(j.events||[]).map(function(e){{return "+"+e.t+"s "+e.msg;}}).join("\\n");
  var pl=document.getElementById("provline");
  pl.childNodes[0].textContent="Provider: "+(prov==="meta"?("meta · "+(j.model||"Muse Spark")):"built-in sample ");
  if(j.state==="done"&&shown>=6){{
    document.getElementById("title").textContent="Done";
    document.getElementById("donecard").hidden=false;
    var box=document.getElementById("donesteps");
    if(!box.childNodes.length){{(j.steps_done||[]).forEach(function(s,i){{
      var dv=document.createElement("div");dv.className="dstep";dv.style.animationDelay=(i*0.12)+"s";
      dv.textContent=s.id+" · "+s.span+" — "+s.action;box.appendChild(dv);}});}}
    var sum=(j.steps_done||[]).length+" steps · "+((Date.now()-t0)/1000).toFixed(1)+"s";
    if(j.cost_usd!=null)sum+=" · cost $"+Number(j.cost_usd).toFixed(4)+" (meta)";
    document.getElementById("donesum").textContent=sum;
    document.getElementById("openresult").href="/result/"+encodeURIComponent(run);
  }}
}}
function fail(msg){{
  document.getElementById("title").textContent="Failed";
  document.getElementById("err").textContent="FAILED: "+msg;
  document.getElementById("errlinks").hidden=false;
  var lis=document.querySelectorAll("#stages li");
  if(lis[shown]){{lis[shown].className="fail";lis[shown].querySelector(".sic").textContent="✗";}}
}}
async function poll(){{
  var j;try{{const r=await fetch("/api/job/{job}");j=await r.json();}}catch(e){{setTimeout(poll,1500);return;}}
  if(!j||j.error){{fail("Unknown job.");return;}}
  var prov=j.provider||"meta";
  if(j.started_at)t0=j.started_at*1000;
  var target=j.state==="done"?6:(IDX[j.stage]!=null?IDX[j.stage]:0);
  shown=target;
  render(j,prov);
  if(j.state==="failed"){{fail(j.error||"Failed");return;}}
  if(j.state==="done"&&shown>=6&&!finale){{finale=true;
    setTimeout(function(){{location.href="/result/"+encodeURIComponent(j.run_id);}},1200);return;}}
  setTimeout(poll,800);
}}
window.onload=poll;
</script>
</body></html>"""

RESULT_TMPL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Result {run}</title>{css}</head>
<body><main>
<p><a href="/">&larr; Home</a></p>
<h1>Result: {run}</h1>
<p>{badges}</p>
{hintline}
{flagline}
<p class="sub">{meta}</p>
<div class="cols">
<div class="card manual pane"><h2>Human manual</h2>
<ol class="tl">{timeline}</ol>
<details class="provenance"><summary>Provenance &amp; source notes</summary>{provelog}</details>
<details open><summary>Full manual</summary>{manual}</details></div>
<div class="card pane"><h2>Robot operation JSON</h2>{cards}
<details><summary>Full operations.json</summary><pre>{ops}</pre></details></div>
</div>
<div class="card"><h2>Export for robots / VLA</h2>
<p class="sub">Format-aligned input-ready annotations. No robot states or actions included; never run on a real robot or VLA.</p>
<p><a href="/export/{run}/lerobot">LeRobot annotation</a> · <a href="/export/{run}/vla">VLA sequence</a> · <a href="/export/{run}/plan">Plan steps</a></p>
{export_preview}
</div>
<div class="card"><h2>Send to Navide</h2>
<p>{navide_summary}</p>
<form method="post" action="/api/navide/{run}">
<button type="submit">Generate plan_create payload</button></form>
{payload}
{verdict}
</div>
</main>
<script>
function clearSel(){{document.querySelectorAll(".sel").forEach(function(e){{e.classList.remove("sel");}});}}
function jump(n){{
  clearSel();
  var a=document.querySelector('[data-step="'+n+'"].step'),b=document.getElementById("manual-step-"+n);
  var t=document.querySelector('.tl li[data-step="'+n+'"]');
  var reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if(a){{a.classList.add("sel");a.scrollIntoView({{behavior:reduce?"auto":"smooth",block:"nearest"}});}}
  if(b){{b.classList.add("sel");b.scrollIntoView({{behavior:reduce?"auto":"smooth",block:"nearest"}});}}
  if(t){{t.classList.add("sel");}}
}}
document.querySelectorAll("[data-step].step,.manual h3.syncable,.manual h2.syncable,.tl li[data-step]").forEach(function(e){{
  e.addEventListener("click",function(ev){{if(ev.target.closest("details,button,a"))return;jump(e.getAttribute("data-step"));}});
}});
document.querySelectorAll("[data-copy]").forEach(function(btn){{
  btn.addEventListener("click",function(ev){{ev.stopPropagation();
    var txt=document.getElementById(btn.getAttribute("data-copy")).textContent;
    function done(){{btn.textContent="Copied";setTimeout(function(){{btn.textContent="Copy JSON";}},1200);}}
    if(navigator.clipboard&&navigator.clipboard.writeText){{navigator.clipboard.writeText(txt).then(done,done);}}
    else{{var ta=document.createElement("textarea");ta.value=txt;document.body.appendChild(ta);ta.select();try{{document.execCommand("copy");}}catch(e){{}}document.body.removeChild(ta);done();}}
  }});
}});
</script>
</body></html>"""


def _push_event(job_id, elapsed, msg):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is not None and len(job.get("events", [])) < 100:
            job.setdefault("events", []).append({"t": round(elapsed, 1), "msg": msg})


def _watch_job(job_id, out_dir, provider, started_at, proc_done):
    """Poll output files every 0.5s; the CLI prints no progress itself."""
    last_idx = -1
    while not proc_done.is_set():
        elapsed = time.time() - started_at
        try:
            idx = derive_stage_index(out_dir, provider, elapsed)
            detail = job_detail(out_dir)
        except OSError:
            idx, detail = last_idx if last_idx >= 0 else 0, {}
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            if idx != last_idx:
                last_idx = idx
                job["stage"] = PROGRESS_STAGES[idx]
                if len(job.get("events", [])) < 100:
                    job.setdefault("events", []).append(
                        {"t": round(elapsed, 1), "msg": f"Stage: {PROGRESS_STAGES[idx]}"})
            job["detail"] = detail
        time.sleep(0.5)


def run_cli_job(job_id, video_path, provider, task_hint=""):
    out_name = f"web-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = RUNS_DIR / out_name
    started_at = time.time()
    with jobs_lock:
        jobs[job_id].update(state="running", stage=PROGRESS_STAGES[0], run_id=out_name,
                            started_at=started_at)
        video_name = jobs[job_id].get("video_name", "video")
    _push_event(job_id, 0.0, f"Job started · {provider} · {video_name}")
    cmd = [sys.executable, "-B", "cli.py", str(video_path),
           "--workspace-path", str(WORKSPACE), "--provider", provider,
           "--out", f"runs/{out_name}"]
    if provider == "meta":
        cmd.append("--confirm-paid-api")
        cmd += ["--model", get_limits()["model"]]
        hint = " ".join(str(task_hint or "").split())[:200]
        if hint:
            cmd += ["--task-hint", hint]
    try:
        env = None
        if provider == "meta" and not os.environ.get("META_API_KEY", "").strip():
            file_key = dotenv_key()
            if file_key:
                env = dict(os.environ, META_API_KEY=file_key)
        proc = subprocess.Popen(cmd, cwd=str(APP_DIR), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env)
        proc_done = threading.Event()
        watcher = threading.Thread(target=_watch_job,
                                   args=(job_id, out_dir, provider, started_at, proc_done),
                                   daemon=True)
        watcher.start()
        log_lines = []
        while True:
            line = proc.stdout.readline()
            if line:
                log_lines.append(clean_path_text(line.rstrip()))
                with jobs_lock:
                    jobs[job_id]["log_tail"] = "\n".join(log_lines[-12:])
            if line == "" and proc.poll() is not None:
                break
        proc_done.set()
        watcher.join(timeout=5)
        rc = proc.returncode
        err = "" if rc == 0 else clean_path_text("\n".join(log_lines[-6:]) or f"exit {rc}")
        cost = None
        steps_done = []
        if provider == "meta" and rc == 0:
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
            except (ValueError, OSError, KeyError):
                err = (err + " (usage unreadable)").strip()
                rc = 1
        if rc == 0:
            try:
                ops = json.loads((out_dir / "operations.json").read_text())
                for s in ops.get("steps", [])[:20]:
                    if not isinstance(s, dict):
                        continue
                    try:
                        st = float(s.get("start_ms", 0))
                        en = float(s.get("end_ms", 0))
                    except (TypeError, ValueError):
                        st, en = 0, 0
                    concept = s.get("concept")
                    action = concept.get("action", "?") if isinstance(concept, dict) else "?"
                    steps_done.append({
                        "id": str(s.get("id", "?")),
                        "span": f"{st / 1000:g}–{en / 1000:g}s",
                        "action": str(action)})
            except (OSError, ValueError):
                pass
        elapsed = time.time() - started_at
        with jobs_lock:
            jobs[job_id].update(state="done" if rc == 0 else "failed", error=err,
                                stage="Done" if rc == 0 else "Failed",
                                detail=job_detail(out_dir), cost_usd=cost,
                                steps_done=steps_done)
        if rc == 0:
            _push_event(job_id, elapsed, f"Done in {elapsed:.1f}s · {len(steps_done)} steps")
        else:
            _push_event(job_id, elapsed, f"Failed: {(err[:120] or 'unknown error')}")
    except (OSError, ValueError) as exc:
        with jobs_lock:
            jobs[job_id].update(state="failed", error=clean_path_text(exc), stage="Failed")
        _push_event(job_id, time.time() - started_at, "Failed: worker error")


SHOWCASE_RUN = "web-20261103T111002Z"


def beforeafter_html():
    """Real before/after from the M1 live run; empty string when unavailable."""
    d = safe_run_dir(SHOWCASE_RUN)
    if d is None:
        return ""
    try:
        ops = json.loads((d / "operations.json").read_text())
        steps = ops.get("steps", [])
        if not steps:
            return ""
        thumbs = []
        for s in steps[:3]:
            refs = s.get("source_frame_refs", [])
            if not refs:
                return ""
            fn = "meta-" + refs[0] + ".jpg"
            if not (d / "frames" / fn).is_file():
                return ""
            thumbs.append((fn, s.get("id", "?"),
                           f"{s.get('start_ms', 0) / 1000:g}–{s.get('end_ms', 0) / 1000:g}s",
                           s.get("concept", {}).get("action", "?")))
        lis = "".join(
            f'<li><img alt="{esc(sid)}" src="/frames/{SHOWCASE_RUN}/{esc(fn)}" loading="lazy">'
            f'<span><b>{esc(sid)} · {esc(span)}</b><br>{esc(action)}</span></li>'
            for fn, sid, span, action in thumbs)
        model = ""
        try:
            prov = json.loads((d / "provider-result.json").read_text())
            model = prov.get("metadata", {}).get("model", "") or prov.get("model", "")
        except (OSError, ValueError):
            pass
        model_disp = {"muse-spark-1.2": "Muse Spark 1.2"}.get(model, "Muse Spark")
        return (
            '<div class="beforeafter">'
            f'<figure class="ba"><img alt="Input frame" src="/frames/{SHOWCASE_RUN}/{esc(thumbs[0][0])}" loading="lazy">'
            "<figcaption>Before: one frame in</figcaption></figure>"
            '<div class="arrow">&rarr;</div>'
            '<div class="baout"><p class="balead">After: timed steps + manual</p>'
            f'<ul class="basteps">{lis}</ul>'
            f'<p class="basrc">Example output from a real {model_disp} run (prompt v2)</p></div></div>')
    except (OSError, ValueError):
        return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "WatchOnce/1.0"

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; font-src 'self'; connect-src 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_html(self, body, code=200):
        if not body.lstrip().startswith("<!doctype"):
            body = (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                    f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                    f"<title>WatchOnce</title>{CSS}</head><body><main>"
                    f"<p><a href=\"/\">&larr; Home</a></p><div class=\"card errpage\">{body}"
                    f"<p class=\"sub\">Nothing was submitted or changed. "
                    f"Go back and try again with an MP4 upload.</p>"
                    f"<p><a class=\"cta\" href=\"/#pick\">Back to upload</a></p></div>"
                    f"</main></body></html>")
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        if path == "/":
            return self.page_home()
        if path == "/history":
            return self.page_home()
        if path == "/settings":
            self.send_response(303)
            self.send_header("Location", "/#settings")
            self.end_headers()
            return
        if path.startswith("/job/"):
            job = path[len("/job/"):]
            with jobs_lock:
                if job not in jobs:
                    return self.send_html("<h1>Unknown job</h1>", 404)
            return self.send_html(JOB_TMPL.format(css=CSS, job=esc(job)))
        if path.startswith("/api/job/"):
            job = path[len("/api/job/"):]
            with jobs_lock:
                info = dict(jobs.get(job, {}))
            if not info:
                return self.send_json({"error": "unknown job"}, 404)
            return self.send_json(info)
        if path == "/api/budget":
            return self.send_json(load_budget())
        if path.startswith("/result/"):
            return self.page_result(path[len("/result/"):])
        if path.startswith("/export/"):
            rest = path[len("/export/"):].split("/")
            if len(rest) != 2:
                return self.send_html("<h1>Not found</h1>", 404)
            run_id, kind = unquote(rest[0]), rest[1]
            d = safe_run_dir(run_id)
            if d is None or kind not in ("lerobot", "vla", "plan"):
                return self.send_html("<h1>Not found</h1>", 404)
            try:
                sys.path.insert(0, str(APP_DIR / "export"))
                import exporter
                payload = exporter.export_run(d, kind, d.name)
            except (ValueError, OSError):
                return self.send_html("<h1>Export unavailable for this run</h1>", 404)
            data = json.dumps(payload, ensure_ascii=False, indent=1).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", f"attachment; filename={d.name}-{kind}.json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if path.startswith("/frames/"):
            rest = path[len("/frames/"):].split("/", 1)
            if len(rest) != 2:
                return self.send_html("<h1>Not found</h1>", 404)
            run_id, name = rest
            d = safe_run_dir(run_id)
            if d is None or not FRAME_RE.match(name):
                return self.send_html("<h1>Not found</h1>", 404)
            for cand in (d / "frames" / name, d / name):
                try:
                    rel = cand.resolve()
                except OSError:
                    continue
                if d.resolve() in rel.parents and rel.is_file():
                    data = rel.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(data)
                    return
            return self.send_html("<h1>Not found</h1>", 404)
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            if not re.match(r"^(fonts/)?[A-Za-z0-9_.\-]+\.(woff2|jpg|jpeg|png)$", rel):
                return self.send_html("<h1>Not found</h1>", 404)
            try:
                f = (STATIC_DIR / rel).resolve()
            except OSError:
                return self.send_html("<h1>Not found</h1>", 404)
            if STATIC_DIR.resolve() not in f.parents or not f.is_file():
                return self.send_html("<h1>Not found</h1>", 404)
            ctype = ("font/woff2" if f.suffix == ".woff2"
                     else "image/jpeg" if f.suffix in (".jpg", ".jpeg") else "image/png")
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        return self.send_html("<h1>Not found</h1>", 404)

    def page_home(self):
        runs = list_runs()
        rows = "".join(
            f'<tr><td><a href="/result/{esc(r["id"])}">{esc(r["id"])}</a></td>'
            f'<td>{esc(r["video"])}</td><td>{esc(r["provider"])}</td>'
            f'<td>{r["steps"]}</td><td>{esc(r["mtime"])}</td></tr>'
            for r in runs)
        history = (f"<table><tr><th>Run</th><th>Video</th><th>Provider</th><th>Steps</th><th>Time</th></tr>{rows}</table>"
                   if rows else '<p>No runs yet — upload a video above to analyze it.</p>')
        b = load_budget()
        lim = get_limits()
        budget = (f"Budget used today: {int(b.get('paid_calls', 0))}/{lim['max_calls']} calls, "
                  f"${float(b.get('spend_usd', 0.0)):.4f} / ${lim['max_usd']:.2f}")
        herocompare = beforeafter_html()
        last4 = masked_last4()
        if last4:
            key_status = ('<div id="keywrap">'
                          f'<p class="mono">Meta API key: <b class="ok">configured · {esc(last4)}</b> '
                          '<button type="button" class="textbtn" data-opendlg>Set up</button>'
                          f' · {budget}</p></div>')
        else:
            key_status = ('<div id="keywrap">'
                          '<p class="mono">Meta API key: <b class="warn">not configured</b> '
                          '<button type="button" class="textbtn" data-opendlg>Set up</button>'
                          f' · {budget}</p>'
                          '<p class="sub">To enable live Meta inference, run '
                          '<code>export META_API_KEY=…</code> before starting the server, copy '
                          '<code>.env.example</code> to <code>.env</code> in app/ and fill it in, '
                          'or set it in the <button type="button" class="textbtn" data-opendlg>Settings dialog</button>. '
                          'The key never leaves this machine and is never shown here.</p></div>')
        if last4:
            cta = ('<label class="f"><input type="checkbox" name="confirm" value="yes"> '
                   'This analysis uses your Meta API credit (about US$0.01 for a 14s clip; '
                   'depends on video length).</label>'
                   '<div class="btnrow"><button type="submit" class="big">Analyze</button></div>')
        else:
            cta = ('<div class="btnrow"><button type="button" class="big" data-opendlg>'
                   'Set up your Meta API key to analyze &rarr;</button></div>')
        self.send_html(HOME_TMPL.format(css=CSS, history=history, herocompare=herocompare,
                                        key_status=key_status, cta=cta, showcase=SHOWCASE_RUN,
                                        csrf=_CSRF_TOKEN))

    def page_log_run(self, d):
        left_src = "(no readable artifact)"
        for cand in ("checklist.md", "report.md", "failure-report.md"):
            if (d / cand).is_file():
                left_src = clean_path_text((d / cand).read_text())
                break
        left = render_markdown(left_src, d.name)
        summary = {}
        for jf in ("round-report.json", "round-status.json", "verdict.json",
                   "provider-result.json", "program-checks.json"):
            if (d / jf).is_file():
                try:
                    summary[jf] = json.loads((d / jf).read_text())
                except ValueError:
                    pass
        right = esc(clean_path_text(json.dumps(summary, indent=1, ensure_ascii=False)[:12000]))
        body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Run {esc(d.name)}</title>{CSS}</head>
<body><main><p><a href="/">&larr; Home</a></p><h1>Run: {esc(d.name)}</h1>
<p class="sub">Loop / log run (no validated operations.json).</p>
<div class="cols"><div class="card manual pane"><h2>Report</h2>{left}</div>
<div class="card pane"><h2>Summary JSON</h2><pre>{right}</pre></div></div>
</main></body></html>"""
        self.send_html(body)

    def page_result(self, run_id):
        d = safe_run_dir(unquote(run_id))
        if d is None:
            return self.send_html("<h1>Run not found</h1>", 404)
        try:
            doc = json.loads((d / "operations.json").read_text()) if (d / "operations.json").is_file() else None
        except ValueError:
            doc = None
        if doc is None:
            return self.page_log_run(d)
        src = doc.get("source", {})
        manual_src = (d / "manual.md").read_text() if (d / "manual.md").is_file() else "(no manual)"
        mlines = manual_src.splitlines()
        cut = next((k for k, ln in enumerate(mlines)
                    if re.match(r"^#{2,3}\s+(step-0*\d|\d+\.\s+step-)", ln)), None)
        if cut is None:
            head_md, body_md = "", manual_src
        else:
            head_md, body_md = "\n".join(mlines[:cut]), "\n".join(mlines[cut:])
        provelog = render_markdown(head_md, d.name) if head_md.strip() else "<p>No provenance block.</p>"
        manual = render_markdown(body_md, d.name)
        ops = esc(clean_path_text(json.dumps(doc, indent=1, ensure_ascii=False)[:20000]))
        steps = doc.get("steps", [])
        is_mock = bool(src.get("mock"))
        badges = (f"<span class=\"badge {'sample' if is_mock else 'live'}\">"
                  f"{'SAMPLE' if is_mock else 'META'}</span>"
                  f"<span class=\"badge n\">{len(steps)} steps</span>")
        meta = esc(src.get('video_name', '?'))
        if is_mock:
            meta += " — sample output from the built-in fixture (no model call)"
        try:
            prov = json.loads((d / "provider-result.json").read_text())
            hint = (prov.get("metadata", {}) or {}).get("task_hint") or ""
        except (OSError, ValueError):
            hint = ""
        hintline = (f'<p class="hintline">Task hint used: {esc(hint[:200])}</p>' if hint else "")
        flagline = "".join(f'<p class="warnline">⚠ {esc(f)}</p>' for f in review_flags(d))
        cards = []
        for i, s in enumerate(steps, 1):
            c = s.get("concept", {})
            refs = s.get("source_frame_refs", [])
            img = ""
            if refs:
                fn = frame_file_for(d, refs[0])
                if fn:
                    img = f"<img alt=\"step {i} frame\" src=\"/frames/{esc(d.name)}/{esc(fn)}\" loading=\"lazy\">"
            cards.append(
                f"<div class=\"step\" data-step=\"{i}\">{img}<div class=\"t\">"
                f"<b>Step {i} · {esc(c.get('action', '?')[:60])}</b><br>"
                f"{esc(c.get('object', '?')[:80])} &rarr; {esc(c.get('target', '?')[:80])}<br>"
                f"<small class=\"ev\">Evidence: {esc(str(s.get('evidence') or 'not recorded — run predates the evidence field')[:140])}</small><br>"
                f"<code>{s.get('start_ms', '?')}–{s.get('end_ms', '?')} ms</code> "
                f"<button type=\"button\" class=\"ghost\" data-copy=\"stepjson-{i}\">Copy JSON</button>"
                f"<details><summary>Step JSON</summary><pre id=\"stepjson-{i}\">"
                f"{esc(clean_path_text(json.dumps(s, ensure_ascii=False)[:3000]))}</pre></details>"
                f"</div></div>")
        cards_html = "\n".join(cards)
        tl = []
        for i, s in enumerate(steps, 1):
            c = s.get("concept", {})
            refs = s.get("source_frame_refs", [])
            img = ""
            if refs:
                fn = frame_file_for(d, refs[0])
                if fn:
                    img = (f"<img alt=\"\" src=\"/frames/{esc(d.name)}/{esc(fn)}\" loading=\"lazy\">")
            plain = (c.get("done") or c.get("action") or "?").strip()
            if len(plain) > 110:
                plain = plain[:107].rsplit(" ", 1)[0] + "…"
            tl.append(
                f"<li data-step=\"{i}\">{img}<span class=\"tt\">"
                f"<b>Step {i}</b> <code>{s.get('start_ms', '?')}–{s.get('end_ms', '?')} ms</code><br>"
                f"{esc(plain)}</span></li>")
        timeline = "\n".join(tl)
        nav_path = d / "navide-plan.json"
        sub_path = d / "submission.json"
        if nav_path.is_file():
            nav = json.loads(nav_path.read_text())
            if isinstance(nav.get("workspace_path"), str):
                nav["workspace_path"] = "<workspace>"
            pend = sum(1 for t in nav.get("todos", []) if t.get("status") == "pending")
            navide_summary = (f"Payload <b>{esc(nav.get('name', '?'))}</b>: "
                              f"{len(nav.get('todos', []))} todos, {pend} pending, stage {esc(nav.get('stage', '?'))}.")
            payload = f"<pre>{esc(clean_path_text(json.dumps(nav, indent=1, ensure_ascii=False)[:6000]))}</pre>"
        else:
            navide_summary, payload = "No navide-plan.json in this run.", ""
        verdict = ""
        if (d / "verdict.json").is_file():
            try:
                v = json.loads((d / "verdict.json").read_text())
                verdict = f"<h3>Recorded verification: {esc(v.get('verdict', '?'))}</h3>"
            except ValueError:
                pass
        if sub_path.is_file():
            try:
                sub = json.loads(sub_path.read_text())
                verdict += f"<p>Submission status: <b>{esc(sub.get('status', '?'))}</b> (payload generated, not live-connected).</p>"
            except ValueError:
                pass
        export_preview = ""
        try:
            sys.path.insert(0, str(APP_DIR / "export"))
            import exporter
            previews = []
            for kind, label in (("lerobot", "LeRobot"), ("vla", "VLA"), ("plan", "Plan")):
                p = exporter.export_run(d, kind, d.name)
                previews.append(f"<details><summary>{label}</summary><pre>{esc(json.dumps(p, ensure_ascii=False)[:1500])}</pre></details>")
            export_preview = "\n".join(previews)
        except (ValueError, OSError) as exc:
            export_preview = f"<p>Export unavailable: {esc(clean_path_text(exc))}</p>"
        self.send_html(RESULT_TMPL.format(css=CSS, run=esc(d.name), meta=meta, manual=manual,
                                          ops=ops, navide_summary=navide_summary, payload=payload, verdict=verdict,
                                          export_preview=export_preview, badges=badges, cards=cards_html,
                                          timeline=timeline, provelog=provelog, hintline=hintline,
                                          flagline=flagline))

    def do_POST(self):
        parts = urlsplit(self.path)
        if parts.path == "/analyze":
            return self.post_analyze()
        if parts.path.startswith("/api/navide/"):
            return self.post_navide(parts.path[len("/api/navide/"):])
        if parts.path in ("/settings/save", "/settings/clear"):
            return self.post_settings(parts.path)
        return self.send_html("<h1>Not found</h1>", 404)

    def post_settings(self, path):
        if not settings_request_allowed(self.headers):
            return self.send_html("<h1>Settings is loopback-only</h1>", 403)
        ctype = self.headers.get("Content-Type", "")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_html("<h1>Bad form</h1>", 400)
        if length > 8192:
            return self.send_html("<h1>Form too large</h1>", 400)
        body = self.rfile.read(length)
        fields = {}
        if ctype.startswith("application/x-www-form-urlencoded"):
            fields = {k: v[0] for k, v in parse_qs(body.decode("latin1")).items() if v}
        else:
            m = re.search(r"boundary=([^;]+)", ctype)
            if m:
                fields, _ = parse_multipart(body, m.group(1).strip().encode())
        if not fields or not secrets.compare_digest(fields.get("csrf", ""), _CSRF_TOKEN):
            return self.send_html("<h1>Bad form token</h1>", 403)
        if path == "/settings/save":
            try:
                write_dotenv_key(fields.get("key", ""))
            except ValueError:
                return self.send_html("<h1>Empty key not saved</h1>", 400)
        else:
            clear_dotenv_key()
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def post_analyze(self):
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=([^;]+)", ctype)
        if not m:
            return self.send_html("<h1>Bad upload</h1>", 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_html("<h1>Bad upload</h1>", 400)
        if length > MAX_UPLOAD + 1024:
            return self.send_html("<h1>File too large (max 100 MB)</h1>", 400)
        body = self.rfile.read(length)
        fields, files = parse_multipart(body, m.group(1).strip().encode())
        provider = fields.get("provider", "meta")
        if provider not in ("mock", "meta"):
            return self.send_html("<h1>Bad provider</h1>", 400)
        video = None
        if "video" in files and files["video"][0]:
            fname, data = files["video"]
            if not fname.lower().endswith(".mp4"):
                return self.send_html("<h1>Only .mp4 accepted</h1>", 400)
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", Path(fname).name)[:60]
            video = UPLOAD_DIR / f"{int(time.time())}_{safe}"
            video.write_bytes(data)
        else:
            return self.send_html("<h1>Upload a file</h1>", 400)
        if provider == "mock" and not mock_fixture_matches(video):
            return self.send_html("<h1>Fixture does not match this video (mock supports only the synthetic clip)</h1>", 400)
        if provider == "meta":
            if fields.get("confirm") != "yes":
                return self.send_html("<h1>Paid API needs explicit confirmation</h1>", 400)
            b = load_budget()
            lim = get_limits()
            if int(b.get("paid_calls", 0)) >= lim["max_calls"] or \
                    float(b.get("spend_usd", 0.0)) >= lim["max_usd"]:
                return self.send_html(
                    f"<h1>Daily budget exhausted ({lim['max_calls']} calls / ${lim['max_usd']:.2f})</h1>", 400)
            if not effective_meta_key():
                return self.send_html("<h1>Server has no META_API_KEY: export it before starting, "
                                      "put it in app/.env, or set it on the Settings page</h1>", 400)
        job_id = f"job-{int(time.time() * 1000)}"
        raw_name = files["video"][0]
        with jobs_lock:
            jobs[job_id] = {"state": "queued", "stage": "Queued", "run_id": "", "log_tail": "",
                            "error": "", "provider": provider,
                            "model": get_limits()["model"] if provider == "meta" else "",
                            "video_name": scrub_video_name(raw_name),
                            "started_at": time.time(), "events": [], "detail": {},
                            "cost_usd": None, "steps_done": []}
        hint = " ".join(str(fields.get("task_hint", "") or "").split())[:200]
        th = threading.Thread(target=run_cli_job, args=(job_id, video, provider, hint), daemon=True)
        th.start()
        self.send_response(303)
        self.send_header("Location", f"/job/{job_id}")
        self.end_headers()

    def post_navide(self, run_id):
        d = safe_run_dir(unquote(run_id))
        if d is None or not (d / "navide-plan.json").is_file():
            return self.send_json({"error": "no payload"}, 404)
        try:
            nav = json.loads((d / "navide-plan.json").read_text())
            sub = json.loads((d / "submission.json").read_text()) if (d / "submission.json").is_file() else {}
        except ValueError:
            return self.send_json({"error": "unreadable payload"}, 500)
        out = {"connected": False,
               "label": "payload generated, not live-connected",
               "plan_name": nav.get("name"), "stage": nav.get("stage"),
               "todos": [{"id": t.get("id"), "status": t.get("status")} for t in nav.get("todos", [])],
               "submission_status": sub.get("status")}
        if (d / "verdict.json").is_file():
            try:
                out["recorded_verdict"] = json.loads((d / "verdict.json").read_text()).get("verdict")
            except ValueError:
                pass
        return self.send_json(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18766)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    (Path(__file__).resolve().parent / "server.pid").write_text(str(os.getpid()))
    print(f"WatchOnce on http://127.0.0.1:{args.port}/  (pid {os.getpid()})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

