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
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit

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

LANGS = (("en", "English"), ("zh-Hant", "繁體中文"), ("zh-Hans", "简体中文"),
         ("ja", "日本語"), ("es", "Español"))
I18N_DIR = Path(__file__).resolve().parent / "i18n"
_I18N_CACHE = {}


def load_lang(code):
    """UI strings for code, falling back to English per key. Default en."""
    if code not in _I18N_CACHE:
        try:
            data = json.loads((I18N_DIR / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _I18N_CACHE[code] = data if isinstance(data, dict) else {}
    if code == "en":
        return _I18N_CACHE["en"]
    base = load_lang("en")
    merged = dict(base)
    merged.update(_I18N_CACHE[code])
    return merged


def request_lang(path):
    try:
        qs = parse_qsl(urlsplit(path).query)
    except ValueError:
        return "en"
    for k, v in qs:
        if k == "lang" and any(v == c for c, _ in LANGS):
            return v
    return "en"


GITHUB_URL = "https://github.com/nt-neil/watch-once-teach-human-and-robot"
GITHUB_SVG = ('<svg height="16" width="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
              '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 '
              '0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 '
              '1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 '
              '0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 '
              '.27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 '
              '3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 '
              '8c0-4.42-3.58-8-8-8z"/></svg>')


def github_link(t):
    label = esc(t["github"])
    return (f'<a class="ghlink" href="{GITHUB_URL}" target="_blank" rel="noopener" aria-label="{label}">'
            f"{GITHUB_SVG}<span class=\"ghtext\">{label}</span></a>")


def lang_switcher(cur, path):
    base = urlsplit(path).path or "/"
    links = []
    opts = []
    for code, label in LANGS:
        if code == cur:
            links.append(f'<span class="langcur" aria-current="true">{esc(label)}</span>')
        else:
            links.append(f'<a class="langlink" href="{esc(base)}?lang={code}" data-lang="{code}">{esc(label)}</a>')
        sel = " selected" if code == cur else ""
        opts.append(f'<option value="{code}"{sel}>{esc(label)}</option>')
    return ('<span class="langsw">' + " · ".join(links) + "</span>"
            '<span class="langcap"><span class="langglobe" aria-hidden="true">'
            '<svg height="14" width="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">'
            '<circle cx="8" cy="8" r="6.5"/><path d="M1.5 8h13M8 1.5c-4.5 4.2-4.5 8.8 0 13 4.5-4.2 4.5-8.8 0-13z"/>'
            "</svg></span>"
            f'<select class="langsel" aria-label="Language" data-langbase="{esc(base)}">'
            + "".join(opts) + "</select></span>")


LANG_JS = """<script>
(function(){try{
var q=new URLSearchParams(location.search);
if(!q.get('lang')){try{var l=localStorage.getItem('wolang');
if(l){q.set('lang',l);location.search=q.toString();return;}}catch(e){}}
document.querySelectorAll('[data-lang]').forEach(function(a){
a.addEventListener('click',function(){
try{localStorage.setItem('wolang',a.getAttribute('data-lang'));}catch(e){}});});
document.querySelectorAll('.langsel').forEach(function(s){
s.addEventListener('change',function(){
var v=s.value;try{localStorage.setItem('wolang',v);}catch(e){}
location.href=s.getAttribute('data-langbase')+'?lang='+encodeURIComponent(v);});});
}catch(e){}})();
</script>"""
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


def mid_ref(refs):
    """Middle cited frame: more representative than the step's first frame."""
    return refs[len(refs) // 2]


def _s(ms):
    """Display helper: milliseconds -> seconds string, one decimal. Display only."""
    try:
        return f"{float(ms) / 1000:.1f}"
    except (TypeError, ValueError):
        return "?"


def _tcaps(st_ms, en_ms):
    """Display helper: shared time capsule for both columns. Display only."""
    return f"<code class=\"tcaps\">{_s(st_ms)}–{_s(en_ms)} s</code>"


def _ellipsize(text, limit):
    """Display helper: append … only when the text was cut. Display only."""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _display_name(raw):
    """Display helper: strip the upload timestamp prefix and -upload suffix. Display only."""
    name = scrub_video_name(raw)
    name = re.sub(r"^\d+_", "", name)
    return re.sub(r"-upload(?=\.[^.]+$|$)", "", name)


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


NOT_VIDEO_MSG = "This is not a video file, or the file is damaged."
CONVERT_FAIL_MSG = "Could not convert this {fmt} file. Try exporting it as MP4 first."


class UploadError(ValueError):
    """Plain-language upload failure shown on the error page."""


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_video(path):
    """Return {video_codec, audio_codec|None, duration, vfr, format} or None.

    Extension-agnostic: any file ffprobe can read with a video stream counts.
    """
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                            "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
        info = json.loads(r.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if r.returncode != 0:
        return None
    vstreams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not vstreams:
        return None
    v = vstreams[0]
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    try:
        dur = float(info.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur <= 0:
        try:
            nb = int(v.get("nb_frames", 0) or 0)
        except (TypeError, ValueError):
            nb = 0
        if nb <= 1:
            return None  # still image, not a video
        dur = 0.04 * nb
    vfr = v.get("avg_frame_rate") not in (None, "0/0") and v.get("r_frame_rate") not in (None, "0/0") \
        and v.get("avg_frame_rate") != v.get("r_frame_rate")
    return {"video_codec": str(v.get("codec_name", "?")).lower(),
            "audio_codec": (str(a.get("codec_name", "?")).lower() if a else None),
            "duration": dur, "vfr": bool(vfr),
            "format": str(info.get("format", {}).get("format_name", "?")).split(",")[0]}


def _needs_transcode(path, probed):
    if Path(path).suffix.lower() != ".mp4":
        return True
    if probed["video_codec"] != "h264":
        return True
    if probed["audio_codec"] not in (None, "aac"):
        return True
    return probed["vfr"]


def prepare_upload(src):
    """Return (mp4_path, provenance). Anything not H.264+AAC MP4 is transcoded.

    Raises UploadError with a plain-language message for non-video or failed
    conversion.
    """
    src = Path(src)
    probed = probe_video(src)
    if probed is None:
        raise UploadError(NOT_VIDEO_MSG)
    if not _needs_transcode(src, probed):
        digest = _sha256_file(src)
        return src, {"original_name": src.name, "original_sha256": digest,
                     "transcoded_sha256": digest, "note": "original mp4, no transcode",
                     "source": f"{probed['format'].upper()} ({probed['video_codec'].upper()})"}
    dst = src.with_name(src.stem + "-upload.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
           "-c:v", "libx264", "-preset", "veryfast"]
    if probed["vfr"]:
        cmd += ["-r", "30"]
    if probed["audio_codec"] is not None:
        cmd += ["-c:a", "aac"]
    cmd += ["-movflags", "+faststart", str(dst)]
    fmt = probed["format"].upper()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        raise UploadError(CONVERT_FAIL_MSG.format(fmt=fmt))
    if r.returncode != 0 or not dst.is_file() or probe_video(dst) is None:
        try:
            dst.unlink()
        except OSError:
            pass
        raise UploadError(CONVERT_FAIL_MSG.format(fmt=fmt))
    prov = {"original_name": src.name, "original_sha256": _sha256_file(src),
            "transcoded_sha256": _sha256_file(dst), "note": "transcoded for upload",
            "source": f"{fmt} ({probed['video_codec'].upper()})"}
    try:
        src.unlink()
    except OSError:
        pass
    return dst, prov


def plain_cli_error(raw):
    """Map technical CLI failures to plain language for the progress page."""
    t = raw or ""
    if "HTTP 401" in t:
        return "Meta rejected the API key (HTTP 401). Open Settings and check the key."
    if "HTTP 402" in t or "HTTP 429" in t:
        return "Meta refused the request (%s). Check billing/quota and try again later." % (
            "402" if "402" in t else "429")
    if "timed out" in t:
        return "Meta took too long to answer (timeout). Nothing was retried; try again later."
    if "Meta request failed" in t:
        return "Could not reach Meta (network error). Check your connection and try again."
    if "outside its step time range" in t or "nonexistent source frame" in t:
        return "The model's steps didn't line up with the video frames. Please try again."
    if "failed schema validation" in t or "non-empty strings" in t or "non-overlapping" in t:
        return "The model's answer had a formatting problem. Please try again."
    return t


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
.langsw{font-size:13px;color:#5a6560}.langlink{color:#155fc0;text-decoration:none}.langcur{font-weight:700;color:#1c2420}
.ghlink{color:#155fc0;text-decoration:none;font-size:13px;white-space:nowrap}.ghlink svg{vertical-align:-3px;margin-right:4px}.ghlink:hover{text-decoration:underline}
.langsel{display:inline-block;font-size:13px;max-width:110px;appearance:none;-webkit-appearance:none;border:1px solid #d8dee6;border-radius:99px;padding:3px 24px 3px 10px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='5'%3E%3Cpath d='M1 1l3 3 3-3' fill='none' stroke='%233d4a44' stroke-width='1.5'/%3E%3C/svg%3E") no-repeat right 9px center;color:#3d4a44;cursor:pointer}
.langcap{position:relative;display:inline-flex;align-items:center}
.langcap .langglobe{position:absolute;left:9px;pointer-events:none}
.langglobe{display:inline-flex;color:#3d4a44;vertical-align:-2px;margin-right:2px}
.langcap .langsel{padding-left:27px}
.langsw{display:none}
.brand{white-space:nowrap}
.topnav{gap:8px;flex-wrap:nowrap}.topright{display:flex;gap:8px;align-items:center;white-space:nowrap}
@media(max-width:480px){.ghlink .ghtext{display:none}.ghlink svg{margin-right:0}
.topnav{padding:8px 0}.topnav .brand{padding:8px 0}.langsel{max-width:none}}
.nrbadge{font-size:12px;font-weight:600;color:#8a4d00;background:#fff3e0;border:1px solid #e0a63f;border-radius:10px;padding:1px 8px;white-space:nowrap}
.hero{display:grid;grid-template-columns:1.15fr .85fr;gap:24px;align-items:center;margin:36px 0 20px}
@media(max-width:900px){.hero{grid-template-columns:1fr}}
.hero h1{font-size:56px;font-weight:700;letter-spacing:-0.02em;line-height:1.05}
@media(max-width:640px){.hero h1{font-size:36px}}
.beforeafter{display:grid;grid-template-columns:1fr auto 1.3fr;gap:12px;align-items:center;margin-top:12px;background:#ffffff;border:1px solid #d8dee6;border-radius:12px;padding:16px;box-shadow:0 8px 28px rgba(26,36,32,.10)}
.hero .beforeafter{margin-top:0}
@media(max-width:700px){.beforeafter{gap:8px}.basteps img{width:56px;height:42px}.basteps li{font-size:12.5px}.ba figcaption{font-size:11px}}
.ba{margin:0}
.ba img{width:100%;border-radius:8px;border:1px solid #d8dee6;display:block}
.ba figcaption{font-size:12.5px;color:#3d4a44;margin-top:6px}
.balead{font-weight:700;margin:0 0 6px}
.basteps{list-style:none;margin:0;padding:0}
.basteps li{display:flex;gap:10px;align-items:center;margin:8px 0;font-size:13.5px;min-width:0;animation:ba-in .5s ease-out backwards}
.basteps li:nth-child(2){animation-delay:.25s}.basteps li:nth-child(3){animation-delay:.5s}
.basteps li:nth-child(2) .batime{animation:ba-glow .4s ease-out .7s backwards}
.basteps li:nth-child(3) .batime{animation:ba-glow .4s ease-out .9s backwards}
.basteps li:nth-child(1) .batime{animation:ba-glow .4s ease-out .45s backwards}
@keyframes ba-in{from{opacity:0}}
@keyframes ba-glow{from{background:#0b5c4c;color:#fff}}
.basteps li b{white-space:nowrap}
.basteps li .batime{display:inline-block;background:#e3efe9;color:#0b5c4c;border-radius:99px;padding:0 8px;font-size:12px;white-space:nowrap}
.basteps img{width:88px;height:66px;object-fit:cover;border-radius:4px;border:1px solid #d8dee6;flex:none}
.basrc{font-size:14px;font-weight:600;color:#1a2420;margin:10px 0 0}
details.advlog{margin-top:12px}
.keyline{margin-top:12px;border-top:1px solid #d8dee6;padding-top:8px}
details.advlog summary{cursor:pointer;font-weight:600;color:#3d4a44}
.hero .lede{margin:20px 0 0}
.hero .lede .sub{font-size:20px;color:#5a6560;text-wrap:balance;max-width:40ch}
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
.gb{background:#f4faf7;border:1px solid #d8dee6;border-radius:8px;padding:12px;font-size:13px;color:#3d4a44;min-width:0}
.gb .gbn{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:99px;background:#0b5c4c;color:#fff;font-size:12px;font-weight:700;margin-right:6px}
.gb b{display:block;color:#1a2420;margin:6px 0 2px;font-size:13.5px}
.fitnote,.limitnote{font-size:13.5px;color:#3d4a44;margin:8px 0 0}
.fitnote{background:#eef6f1;border:1px solid #bcd4c9;border-radius:8px;padding:8px 12px}
.fiticon{color:#0b5c4c;font-weight:700;margin-right:2px}
.limitnote{background:#fff3e0;border:1px solid #e0a63f;border-radius:8px;padding:8px 12px;color:#5a3a00}
.limiticon{font-weight:700;margin-right:2px}
.drop{display:block;border:3px dashed #9aa6b2;border-radius:14px;padding:40px 20px;text-align:center;background:linear-gradient(180deg,#f2f8f4,#fafcfb);margin-top:12px;cursor:pointer;transition:border-color 150ms,background 150ms,box-shadow 150ms}
.drop:hover{animation:dropulse 1.6s ease-in-out infinite}
@keyframes dropulse{50%{box-shadow:0 0 0 6px rgba(11,92,76,.14)}}
.drop:hover{border-color:#0b5c4c;background:#f0f7f3;box-shadow:0 4px 18px rgba(11,92,76,.12)}
.drop.over{border-color:#0b5c4c;background:#e3efe9;box-shadow:0 4px 22px rgba(11,92,76,.20)}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.dropicon{display:block;font-size:44px;color:#0b5c4c;line-height:1}
.dropmsg{display:block;margin:8px 0 10px;font-size:14px;color:#3d4a44}
.filebtnlike{display:inline-block;font-size:15px;font-weight:600;padding:8px 22px;background:#ffffff;color:#0b5c4c;border:2px solid #0b5c4c;border-radius:6px}
.filename{font-size:14px;overflow-wrap:break-word;min-width:0}
.filecaprow{margin:10px 0 0}
.preview{margin-top:10px}
.preview video{width:100%;max-height:360px;object-fit:contain;border:1px solid #d8dee6;border-radius:8px;background:#1a2420;display:block}
#previewVideo[hidden]{display:none}
.previewmeta{font-size:13.5px;color:#3d4a44;margin:6px 0 0}
.previewnote{font-size:13.5px;color:#3d4a44;margin:6px 0 0}
.filecap{display:none;align-items:center;gap:10px;background:#eef6f1;border:1px solid #bcd4c9;border-radius:99px;padding:6px 8px 6px 14px;max-width:100%}
.filecaprow:has(#fname:not([hidden])) .filecap{display:inline-flex}
.filecapicon{display:inline-flex;color:#0b5c4c;flex:none}
.filecap #rmfile{margin-top:0;flex:none}
.tcaps{white-space:nowrap}
.hl{background:linear-gradient(transparent 58%,#cfe3d8 58%,#cfe3d8 94%,transparent 94%);padding:0 1px}

button.small{font-size:13px;padding:6px 14px;margin-top:8px}
button.textbtn{background:none;border:none;padding:0;margin:0;color:#0b5c4c;font:inherit;font-size:inherit;cursor:pointer;text-decoration:underline}
label.f{display:block;margin:12px 0 4px;font-weight:400;font-size:14px}
label.f .hint{color:#3d4a44}
.keycaps{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px;font-size:13px}
.keycap{display:inline-block;background:#e3efe9;color:#1a2420;border:1px solid #bcd4c9;border-radius:99px;padding:3px 12px;white-space:nowrap}
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
:focus-visible{outline:3px solid #0b5c4c;outline-offset:3px;border-radius:4px;box-shadow:0 0 0 5px rgba(11,92,76,.18)}
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
.step{display:flex;gap:12px;border:1px solid #d8dee6;border-radius:8px;padding:10px 12px;margin:8px 0;cursor:pointer;transition:border-color 120ms,background 120ms;line-height:1.4}
.step .t br{content:"";display:block;margin-top:2px}
.step .t button.ghost{margin-top:2px;padding:4px 12px;font-size:13px}
.step details{margin-top:4px}
.step:hover{border-color:#0b5c4c}
.step.sel{border-color:#0b5c4c;background:#e3efe9}
.step img{width:132px;height:99px;object-fit:cover;border-radius:4px;border:1px solid #d8dee6;flex:none}
.step .t b{font-size:15px}.step .t{font-size:13.5px;color:#3d4a44;min-width:0}
.step .t code{font-size:12px}
.step pre{font-size:12px;max-height:220px}
.badge{display:inline-block;font-size:12px;font-weight:700;border-radius:99px;padding:2px 10px;margin-right:6px}
.badge.live{background:#d3e7d8;color:#166534}.badge.sample{background:#fbdfb9;color:#8a4d00}
.badge.n{background:#e3efe9;color:#0b5c4c}
.stages{list-style:none;margin:6px 0 0;padding:0 0 0 4px}
.stages li{position:relative;display:flex;gap:12px;align-items:flex-start;padding:10px 12px;border-radius:8px;overflow:hidden}
.stages li+li{margin-top:4px}
.stages li::before{content:"";position:absolute;left:24px;top:40px;bottom:-6px;width:3px;background:#d8dee6;border-radius:2px}
.stages li:last-child::before{display:none}
.stages li.done::before{background:#0b5c4c}
.stages li.active::before{background:linear-gradient(#0b5c4c,#d8dee6)}
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
.stages li.wait{opacity:.55}
.stages li.wait .sic{border-style:dashed}
.stages li.active{box-shadow:0 2px 12px rgba(11,92,76,.18)}
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
a.cta{display:inline-block;font-size:15px;font-weight:700;padding:10px 22px;background:#0b5c4c;color:#fff!important;border-radius:6px;text-decoration:none}
.errlinks a{display:inline-block;font-size:14px;font-weight:700;padding:8px 18px;border-radius:6px;text-decoration:none;margin-right:8px;background:#0b5c4c;color:#fff!important}
.errlinks a:last-child{background:#fff;color:#0b5c4c!important;border:2px solid #0b5c4c}
.elapsedline{font-size:14px;color:#3d4a44}
.jobmeta{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 12px}
.jobcap{display:inline-block;font-size:13px;background:#eef6f1;border:1px solid #bcd4c9;border-radius:99px;padding:3px 12px;color:#1a2420}

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
.tl img{width:96px;height:72px;object-fit:cover;border-radius:6px;border:1px solid #d8dee6;vertical-align:middle;margin-right:10px}
.tl .tt{font-size:14px}.tl .tt b{font-size:16px}
.tl code{font-size:12px;background:#e3efe9;color:#0b5c4c;border-radius:99px;padding:1px 10px;white-space:nowrap}
.tl li::after{width:12px;height:12px;left:1px}
.step .ev{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
details.provenance{margin-top:10px}
details.provenance summary{cursor:pointer;font-weight:600}
.ok{color:#166534;font-weight:700}.warn{color:#8a4d00;font-weight:700}.err{color:#b42318;font-weight:700}
.mono{font-family:"Plex Mono",ui-monospace,SFMono-Regular,monospace;font-size:13px}
a{color:#0b5c4c}
footer{margin-top:28px;color:#3d4a44;font-size:13px}
.runid{font-size:12.5px;color:#5a6560;margin:-2px 0 8px;overflow-wrap:break-word}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
</style>"""

HOME_TMPL = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{t[title_home]}</title>{css}</head>
<body><main>
<p class="topnav"><span class="brand">{t[brand]}</span><span class="topright">{langsw} · {ghlink} · <button type="button" class="textbtn" data-opendlg>{t[settings]}</button></span></p>
<div class="hero">
<div class="lede">
<h1>{t[hero_title_html]}</h1>
<p class="sub">{t[hero_sub]}</p>
<p><a class="btnlink" href="#pick">{t[hero_cta]}</a></p>
</div>
{herocompare}
</div>
<div class="card" id="pick"><h2>{t[pick_h2]}</h2>
<form method="post" action="/analyze" enctype="multipart/form-data">
<p class="getback-lede">{t[getback_lede]}</p>
<div class="gbgrid">
<div class="gb"><span class="gbn">1</span><b>{t[gb1t]}</b><span>{t[gb1s]}</span></div>
<div class="gb"><span class="gbn">2</span><b>{t[gb2t]}</b><span>{t[gb2s]}</span></div>
<div class="gb"><span class="gbn">3</span><b>{t[gb3t]}</b><span>{t[gb3s]}</span></div>
<div class="gb"><span class="gbn">4</span><b>{t[gb4t]}</b><span>{t[gb4s]}</span></div>
</div>
<p class="fitnote"><span class="fiticon" aria-hidden="true">✓</span> {t[fitnote_v2]}</p>
<p class="limitnote"><span class="limiticon" aria-hidden="true">⚠</span> {t[limitnote]}</p>
<label class="drop" id="drop" for="filein">
<input type="file" name="video" id="filein" class="vh" accept="video/*,.mov,.mkv,.avi,.flv,.wmv,.ts,.mts,.m2ts,.3gp,.ogv,.gif">
<span class="dropicon" aria-hidden="true">⇪</span>
<span class="dropmsg">{t[drop_msg]}</span>
<span class="filebtnlike">{t[choose_file]}</span>
</label>
<p class="filecaprow"><span class="filecap"><span class="filecapicon" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1.5" y="3" width="13" height="10" rx="2"/><path d="M6.5 6.2v3.6L10 8z" fill="currentColor" stroke="none"/></svg></span><span class="filename" id="fname" hidden></span><button type="button" class="ghost small" id="rmfile" hidden>{t[remove]}</button></span></p>
<div class="preview" id="preview" hidden>
<video id="previewVideo" controls muted playsinline preload="metadata"></video>
<p class="previewmeta"><span id="previewDur" hidden>{t[preview_duration]}: <span id="previewDurVal"></span></span> <span id="previewWarn" class="warn" hidden>{t[preview_over3]}</span></p>
<p class="previewnote" id="previewNote" hidden>{t[preview_unavailable]}</p>
</div>
<label class="f" for="hintin">{t[hint_label]}</label>
<div><input type="text" id="hintin" name="task_hint" maxlength="200" autocomplete="off" placeholder="{t[hint_ph]}"></div>
<label class="f"><input type="checkbox" name="vote3" value="yes"> <b>{t[vote_label_t]}</b> <span class="hint">{t[vote_label_d]}</span></label>
{cta}
<div class="keyline">
{key_status}
</div>
</form></div>
<div class="card"><h2>{t[how_h2]}</h2>
<div class="pipe" aria-hidden="true">
<div class="node"><b>{t[pipe1t]}</b><span>{t[pipe1s]}</span></div><div class="arrow">&rarr;</div>
<div class="node"><b>{t[pipe2t]}</b><span>{t[pipe2s]}</span></div><div class="arrow">&rarr;</div>
<div class="node"><b>{t[pipe3t]}</b><span>{t[pipe3s]}</span></div>
</div>
<ul class="chips"><li>{t[chip1]}</li><li>{t[chip2]}</li><li>{t[chip3]}</li></ul>
</div>
<div class="card scroll"><h2>{t[history_h2]}</h2>{history}</div>
<footer>{t[footer]} · {ghlink}</footer>
</main>
<dialog id="settingsDlg" aria-labelledby="dlgTitle">
<form method="dialog" class="dlgclose"><button type="submit" aria-label="Close">×</button></form>
<h2 id="dlgTitle">{t[settings]}</h2>
<p class="mono" id="dlgStatus"></p>
<p class="err" id="dlgErr"></p>
<form id="dlgKeyForm" method="post" action="/settings/save">
<input type="hidden" name="csrf" value="{csrf}">
<label class="f" for="dk">{t[key_label]}</label>
<div><input type="password" id="dk" name="key" autocomplete="off" placeholder="{t[key_ph]}"></div>
<div class="btnrow"><button type="submit">{t[save_key]}</button></div>
</form>
<form id="dlgClearForm" method="post" action="/settings/clear" style="margin-top:8px">
<input type="hidden" name="csrf" value="{csrf}">
<button type="submit" class="ghost">{t[clear_key]}</button>
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
</script>
<script>
(function(){{ // Local video preview: object URL only, never uploaded until Analyze.
var pfi=document.getElementById('filein'),pv=document.getElementById('preview'),
pvvid=document.getElementById('previewVideo'),pdur=document.getElementById('previewDur'),
pdurval=document.getElementById('previewDurVal'),pwarn=document.getElementById('previewWarn'),
pnote=document.getElementById('previewNote'),purl=null;
function phide(){{if(purl){{URL.revokeObjectURL(purl);purl=null;}}
pvvid.removeAttribute('src');pvvid.load();pvvid.hidden=false;pv.hidden=true;pdur.hidden=true;pwarn.hidden=true;pnote.hidden=true;}}
function pshow(f){{phide();purl=URL.createObjectURL(f);pvvid.src=purl;pv.hidden=false;}}
pfi.addEventListener('change',function(){{if(pfi.files.length){{pshow(pfi.files[0]);}}else{{phide();}}}});
document.getElementById('rmfile').addEventListener('click',function(){{phide();}});
document.getElementById('drop').addEventListener('drop',function(){{setTimeout(function(){{
if(pfi.files.length){{pshow(pfi.files[0]);}}}},0);}});
pvvid.addEventListener('loadedmetadata',function(){{var d=pvvid.duration;
if(!isFinite(d)){{return;}}
var m=Math.floor(d/60),s=Math.floor(d%60);
pdurval.textContent=m+':'+(s<10?'0':'')+s;pdur.hidden=false;
if(d>180){{pwarn.hidden=false;}}}});
pvvid.addEventListener('error',function(){{pvvid.hidden=true;pnote.hidden=false;pdur.hidden=true;pwarn.hidden=true;}});
}})();
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
{langjs}
</body></html>"""

JOB_TMPL = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{t[job_title]} {job}</title>{css}</head>
<body><main><p class="topnav"><a href="/{langqs}">&larr; {t[home]}</a><span class="topright">{langsw}</span></p><h1 id="title">{t[job_title]}</h1>
<div class="jobmeta"><span class="jobcap mono">{job}</span>
<span class="jobcap" id="provline">{t[provider]}: &hellip;</span>
<span class="jobcap">{t[elapsed]} <b id="elapsed">0.0s</b></span></div>
<div class="card"><ol class="stages" id="stages">
<li class="wait"><span class="sic">1</span><div class="sbody"><b>{t[checking_video]}</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">2</span><div class="sbody"><b>{t[extracting_frames]}</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">3</span><div class="sbody"><b>{t[sending_to_spark]}</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">4</span><div class="sbody"><b>{t[model_analyzing]}</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">5</span><div class="sbody"><b>{t[validating_steps]}</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">6</span><div class="sbody"><b>{t[writing_manual]}</b><p class="sdetail"></p></div></li>
<li class="wait"><span class="sic">7</span><div class="sbody"><b>{t[done]}</b><p class="sdetail"></p></div></li>
</ol></div>
<div class="card" id="filmcard" hidden><h2>{t[frames]}</h2><div class="film" id="film"></div>
<div class="scanwrap" id="scanwrap" hidden><div class="scanbeam"></div><p>{t[watching]}</p></div></div>
<div class="card" id="donecard" hidden><h2>{t[done]}</h2><div class="donesteps" id="donesteps"></div><p id="donesum"></p><p><a id="openresult" class="btnlink" href="#">{t[open_result]}</a></p></div>
<p id="err" class="err"></p>
<p class="errlinks" id="errlinks" hidden><a href="/{langqs}">&larr; {t[back]}</a> &middot; <a href="/{langqs}#pick">{t[try_again]}</a></p>
<details class="advlog"><summary>{t[activity_log]}</summary><pre id="alog" class="mono"></pre></details>
</main>
<script>
var NAMES=["Checking video","Extracting frames","Sending to Muse Spark","Model analyzing","Validating steps","Writing manual","Done"];
var IDX={{}};NAMES.forEach(function(n,i){{IDX[n]=i;}});
var shown=0,t0=Date.now(),finale=false,filmSeen={{}};
var LONGNOTE="{t[long_video_note]}";
function tick(){{document.getElementById("elapsed").textContent=((Date.now()-t0)/1000).toFixed(1)+"s";}}
setInterval(tick,200);
function detailFor(k,j,prov){{var d=j.detail||{{}};
  if(k===0){{var p=[];if(d.duration_s!=null)p.push(d.duration_s+" s");if(d.resolution)p.push(d.resolution);if(d.sha12)p.push("sha "+d.sha12);
    var pre=d.converted_from?("Converted from "+d.converted_from+" to MP4 (H.264) · "):"";
    var note="";if(d.duration_s>60){{note=" · ⚠ "+LONGNOTE.replace("{{s}}",d.duration_s).replace("{{e}}",(d.duration_s*0.002).toFixed(2));}}
    return pre+(j.video_name||"video")+(p.length?" · "+p.join(" · "):" · reading file…")+note;}}
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
  var j,r;try{{r=await fetch("/api/job/{job}");j=await r.json();}}catch(e){{setTimeout(poll,1500);return;}}
  if(!j||!j.state||(r&&r.status===404)){{fail("Unknown job.");return;}}
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
{langjs}
</body></html>"""

RESULT_TMPL = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{t[result_title]} {run}</title>{css}</head>
<body><main>
<p class="topnav"><a href="/{langqs}">&larr; {t[home]}</a><span class="topright">{langsw} · {ghlink}</span></p>
<h1>{t[result_title]}: {videoname}</h1>
<p class="runid">{run}</p>
<p>{badges}</p>
{hintline}
{flagline}
{metaline}
<div class="cols">
<div class="card manual pane"><h2>{t[human_manual]}</h2>
<p class="sub">{t[model_output_note]}</p>
<ol class="tl">{timeline}</ol>
<details class="provenance"><summary>{t[provenance]}</summary>{provelog}</details>
<details open><summary>{t[full_manual]}</summary>{manual}</details></div>
<div class="card pane"><h2>{t[robot_json]}</h2>{cards}
<details><summary>{t[full_operations]}</summary><pre>{ops}</pre></details></div>
</div>
<div class="card"><h2>{t[export_h2]}</h2>
<p class="sub">{t[export_note]}</p>
<p><a href="/export/{run}/lerobot">LeRobot annotation</a> · <a href="/export/{run}/vla">VLA sequence</a> · <a href="/export/{run}/plan">Plan steps</a></p>
{export_preview}
</div>
<div class="card"><h2>{t[navide_h2]}</h2>
<p>{navide_summary}</p>
<form method="post" action="/api/navide/{run}">
<button type="submit">{t[gen_payload]}</button></form>
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
{langjs}
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
            if job.get("converted_from"):
                detail["converted_from"] = job["converted_from"]
            job["detail"] = detail
        time.sleep(0.5)


def _merge_vote_rounds(job_id, out_dir, round_dirs, started_at):
    """Merge 3 voted runs into out_dir. Returns rc (0 ok)."""
    try:
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        if str(APP_DIR / "scripts") not in sys.path:
            sys.path.insert(0, str(APP_DIR / "scripts"))
        from vote import merge_runs
        from cli import manual
        from navide import plan_payload
        base, stats = merge_runs([str(d) for d in round_dirs])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "operations.json").write_text(json.dumps(base, ensure_ascii=False, indent=1))
        (out_dir / "vote.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
        total_in = total_out = 0
        for d in round_dirs:
            usage = json.loads((d / "provider-result.json").read_text()).get("metadata", {}).get("usage", {})
            total_in += int(usage.get("input_tokens", 0) or 0)
            total_out += int(usage.get("output_tokens", 0) or 0)
        first = json.loads((round_dirs[0] / "provider-result.json").read_text())
        first["metadata"]["usage"] = {"input_tokens": total_in, "output_tokens": total_out}
        first["metadata"]["vote"] = stats
        first["response"] = base
        (out_dir / "provider-result.json").write_text(json.dumps(first, ensure_ascii=False, indent=1))
        manifest = json.loads((round_dirs[0] / "frames.json").read_text())
        (out_dir / "frames.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        import shutil
        src_frames = round_dirs[0] / "frames"
        if src_frames.is_dir():
            shutil.copytree(src_frames, out_dir / "frames", dirs_exist_ok=True)
        (out_dir / "manual.md").write_text(manual(base, manifest), encoding="utf-8")
        payload = plan_payload(base, manifest, str(WORKSPACE))
        (out_dir / "navide-plan.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        sub = {"source": base["source"], "status": "exported_not_sent",
               "reason": "JSON export only; --send-navide was not requested."}
        (out_dir / "submission.json").write_text(json.dumps(sub, ensure_ascii=False, indent=1))
        _push_event(job_id, time.time() - started_at,
                    f"Voted: {stats['n_steps']} steps from {stats['n_runs']} runs, "
                    f"{stats['proposals']} proposals")
        return 0
    except (OSError, ValueError, KeyError, ImportError) as exc:
        _push_event(job_id, time.time() - started_at, f"Vote merge failed: {exc}")
        return 1


def run_cli_job(job_id, video_path, provider, task_hint="", vote3=False):
    out_name = f"web-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = RUNS_DIR / out_name
    started_at = time.time()
    with jobs_lock:
        jobs[job_id].update(state="running", stage=PROGRESS_STAGES[0], run_id=out_name,
                            started_at=started_at)
        video_name = jobs[job_id].get("video_name", "video")
    voted = " · majority vote x3" if vote3 else ""
    _push_event(job_id, 0.0, f"Job started · {provider}{voted} · {video_name}")
    try:
        video_path, provenance = prepare_upload(video_path)
    except UploadError as exc:
        with jobs_lock:
            jobs[job_id].update(state="failed", error=str(exc), stage="Failed")
        _push_event(job_id, time.time() - started_at, f"Failed: {exc}")
        return
    if provenance["note"] != "original mp4, no transcode":
        _push_event(job_id, time.time() - started_at,
                    f"Transcoded {provenance['original_name']} for upload")
        with jobs_lock:
            jobs[job_id]["converted_from"] = provenance.get("source", "?")
    base_cmd = [sys.executable, "-B", "cli.py", str(video_path),
                "--workspace-path", str(WORKSPACE), "--provider", provider]
    if provider == "meta":
        base_cmd.append("--confirm-paid-api")
        base_cmd += ["--model", get_limits()["model"]]
        hint = " ".join(str(task_hint or "").split())[:200]
        if hint:
            base_cmd += ["--task-hint", hint]
    rounds = [f"runs/{out_name}-v{i}" for i in (1, 2, 3)] if vote3 else [f"runs/{out_name}"]
    try:
        env = None
        if provider == "meta" and not os.environ.get("META_API_KEY", "").strip():
            file_key = dotenv_key()
            if file_key:
                env = dict(os.environ, META_API_KEY=file_key)
        log_lines = []
        rc = 0
        for ri, out_sub in enumerate(rounds, 1):
            if vote3:
                _push_event(job_id, time.time() - started_at, f"Vote pass {ri}/3")
            cmd = base_cmd + ["--out", out_sub]
            proc = subprocess.Popen(cmd, cwd=str(APP_DIR), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=env)
            proc_done = threading.Event()
            watcher = threading.Thread(target=_watch_job,
                                       args=(job_id, RUNS_DIR / out_sub, provider, started_at, proc_done),
                                       daemon=True)
            watcher.start()
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
            if rc != 0:
                break
        if vote3 and rc == 0:
            rc = _merge_vote_rounds(job_id, out_dir, [RUNS_DIR / s for s in rounds], started_at)
        try:
            (out_dir / "transcode.json").write_text(
                json.dumps(provenance, ensure_ascii=False, indent=1))
        except OSError:
            pass
        err = "" if rc == 0 else plain_cli_error(clean_path_text("\n".join(log_lines[-6:]) or f"exit {rc}"))
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
                b["paid_calls"] = int(b.get("paid_calls", 0)) + (3 if vote3 else 1)
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


def beforeafter_html(t):
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
            fn = "meta-" + mid_ref(refs) + ".jpg"
            if not (d / "frames" / fn).is_file():
                return ""
            thumbs.append((fn, s.get("id", "?"),
                           f"{s.get('start_ms', 0) / 1000:g}–{s.get('end_ms', 0) / 1000:g}s",
                           s.get("concept", {}).get("action", "?")))
        lis = "".join(
            f'<li><img alt="{esc(sid)}" src="/frames/{SHOWCASE_RUN}/{esc(fn)}" loading="lazy">'
            f'<span><b>{esc(sid)}</b> <span class="batime">{esc(span)}</span><br>{esc(action)}</span></li>'
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
            f"<figcaption>{t['ba_before']}</figcaption></figure>"
            '<div class="arrow">&rarr;</div>'
            f'<div class="baout"><p class="balead">{t["ba_after"]}</p>'
            f'<ul class="basteps">{lis}</ul>'
            f'<p class="basrc">{t["ba_src"].format(model=model_disp)}</p></div></div>')
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
            lang = request_lang(self.path)
            t = load_lang(lang)
            langqs = f"?lang={lang}" if lang != "en" else ""
            body = (f"<!doctype html><html lang=\"{lang}\"><head><meta charset=\"utf-8\">"
                    f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                    f"<title>WatchOnce</title>{CSS}</head><body><main>"
                    f"<p class=\"topnav\"><a href=\"/{langqs}\">&larr; {t['home']}</a>"
                    f"{lang_switcher(lang, self.path)}</p><div class=\"card errpage\">{body}"
                    f"<p class=\"sub\">{t['error_nothing']} "
                    f"{t['error_goback']}</p>"
                    f"<p><a class=\"cta\" href=\"/{langqs}#pick\">{t['back_to_upload']}</a></p></div>"
                    f"{LANG_JS}</main></body></html>")
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
                    t404 = load_lang(request_lang(self.path))
                    return self.send_html(f"<h1>{t404['unknown_job']}</h1>", 404)
            lang = request_lang(self.path)
            langqs = f"?lang={lang}" if lang != "en" else ""
            return self.send_html(JOB_TMPL.format(css=CSS, job=esc(job), t=load_lang(lang),
                                                 lang=lang, langsw=lang_switcher(lang, self.path),
                                                 langqs=langqs, langjs=LANG_JS))
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
        lang = request_lang(self.path)
        t = load_lang(lang)
        runs = list_runs()
        rows = "".join(
            f'<tr><td><a href="/result/{esc(r["id"])}">{esc(r["id"])}</a></td>'
            f'<td>{esc(r["video"])}</td><td>{esc(r["provider"])}</td>'
            f'<td>{r["steps"]}</td><td>{esc(r["mtime"])}</td></tr>'
            for r in runs)
        history = (f"<table><tr><th>{t['th_run']}</th><th>{t['th_video']}</th><th>{t['th_provider']}</th><th>{t['th_steps']}</th><th>{t['th_time']}</th></tr>{rows}</table>"
                   if rows else f"<p>{t['history_empty']}</p>")
        b = load_budget()
        lim = get_limits()
        budget = (f"{t['budget_used']}: {int(b.get('paid_calls', 0))}/{lim['max_calls']} {t['budget_calls']}, "
                  f"${float(b.get('spend_usd', 0.0)):.4f} / ${lim['max_usd']:.2f}")
        herocompare = beforeafter_html(t)
        last4 = masked_last4()
        if last4:
            key_status = ('<div id="keywrap">'
                          f'<p class="keycaps"><span class="keycap">Meta API key: <b class="ok">{t["status_configured"]} · {esc(last4)}</b></span>'
                          f'<span class="keycap">{budget}</span> '
                          f'<button type="button" class="textbtn" data-opendlg>{t["set_up"]}</button></p></div>')
        else:
            setup_html = t["setup_text"].format(
                export="<code>export META_API_KEY=…</code>", env="<code>.env.example</code>",
                envfile="<code>.env</code>",
                dlg=f'<button type="button" class="textbtn" data-opendlg>{t["settings_dialog"]}</button>')
            key_status = ('<div id="keywrap">'
                          f'<p class="keycaps"><span class="keycap">Meta API key: <b class="warn">{t["not_configured"]}</b></span>'
                          f'<span class="keycap">{budget}</span> '
                          f'<button type="button" class="textbtn" data-opendlg>{t["set_up"]}</button></p>'
                          f'<p class="sub">{setup_html}</p></div>')
        if last4:
            cta = (f'<label class="f"><input type="checkbox" name="confirm" value="yes"> <b>{t["confirm_cost_t"]}</b> <span class="hint">{t["confirm_cost_d"]}</span></label>'
                   f'<div class="btnrow"><button type="submit" class="big">{t["analyze"]}</button></div>')
        else:
            cta = (f'<div class="btnrow"><button type="button" class="big" data-opendlg>{t["setup_cta"]}</button></div>')
        langqs = f"?lang={lang}" if lang != "en" else ""
        self.send_html(HOME_TMPL.format(css=CSS, history=history, herocompare=herocompare,
                                        key_status=key_status, cta=cta, showcase=SHOWCASE_RUN,
                                        csrf=_CSRF_TOKEN, t=t, lang=lang,
                                        langsw=lang_switcher(lang, self.path), langqs=langqs,
                                        langjs=LANG_JS, ghlink=github_link(t)))

    def page_log_run(self, d):
        lang = request_lang(self.path)
        t = load_lang(lang)
        langqs = f"?lang={lang}" if lang != "en" else ""
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
        body = f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{t['checklist_title']} {esc(d.name)}</title>{CSS}</head>
<body><main><p class="topnav"><a href="/{langqs}">&larr; {t['home']}</a>{lang_switcher(lang, self.path)}</p><h1>{t['run']}: {esc(d.name)}</h1>
<p class="sub">Loop / log run (no validated operations.json).</p>
<div class="cols"><div class="card manual pane"><h2>Report</h2>{left}</div>
<div class="card pane"><h2>Summary JSON</h2><pre>{right}</pre></div></div>
{LANG_JS}</main></body></html>"""
        self.send_html(body)

    def page_result(self, run_id):
        lang = request_lang(self.path)
        t = load_lang(lang)
        d = safe_run_dir(unquote(run_id))
        if d is None:
            return self.send_html(f"<h1>{t['error_run_not_found']}</h1>", 404)
        try:
            doc = json.loads((d / "operations.json").read_text()) if (d / "operations.json").is_file() else None
        except ValueError:
            doc = None
        if doc is None:
            return self.page_log_run(d)
        src = doc.get("source", {})
        manual_src = (d / "manual.md").read_text() if (d / "manual.md").is_file() else t["no_manual"]
        mlines = manual_src.splitlines()
        cut = next((k for k, ln in enumerate(mlines)
                    if re.match(r"^#{2,3}\s+(step-0*\d|\d+\.\s+step-)", ln)), None)
        if cut is None:
            head_md, body_md = "", manual_src
        else:
            head_md, body_md = "\n".join(mlines[:cut]), "\n".join(mlines[cut:])
        provelog = render_markdown(head_md, d.name) if head_md.strip() else f"<p>{t['no_provenance']}</p>"
        manual = render_markdown(body_md, d.name)
        ops = esc(clean_path_text(json.dumps(doc, indent=1, ensure_ascii=False)[:20000]))
        steps = doc.get("steps", [])
        is_mock = bool(src.get("mock"))
        badges = (f"<span class=\"badge {'sample' if is_mock else 'live'}\">"
                  f"{'SAMPLE' if is_mock else 'META'}</span>"
                  f"<span class=\"badge n\">{len(steps)} steps</span>")
        meta = (" — sample output from the built-in fixture (no model call)"
                if is_mock else "")
        metaline = (f"<p class=\"runid\">{esc(d.name)} · {esc(_display_name(src.get('video_name', d.name)))}{meta}</p>"
                    if is_mock else "")
        try:
            prov = json.loads((d / "provider-result.json").read_text())
            hint = (prov.get("metadata", {}) or {}).get("task_hint") or ""
        except (OSError, ValueError):
            hint = ""
        hintline = (f'<p class="hintline">{t["task_hint_used"]}: {esc(hint[:200])}</p>' if hint else "")
        flagline = "".join(f'<p class="warnline">⚠ {esc(f)}</p>' for f in review_flags(d))
        cards = []
        for i, s in enumerate(steps, 1):
            c = s.get("concept", {})
            refs = s.get("source_frame_refs", [])
            img = ""
            if refs:
                fn = frame_file_for(d, mid_ref(refs))
                if fn:
                    img = f"<img alt=\"step {i} frame\" src=\"/frames/{esc(d.name)}/{esc(fn)}\" loading=\"lazy\">"
            nr = f" <span class=\"nrbadge\">{t['needs_review']}</span>" if str(s.get("evidence") or "").startswith("needs_review") else ""
            cards.append(
                f"<div class=\"step\" data-step=\"{i}\">{img}<div class=\"t\">"
                f"<b>{t['step']} {i} · {esc(c.get('action', '?')[:60])}</b>{nr}<br>"
                f"{esc(c.get('object', '?')[:80])} &rarr; {esc(c.get('target', '?')[:80])}<br>"
                f"<small class=\"ev\">{t['evidence']}: {esc(_ellipsize(str(s.get('evidence') or t['not_recorded']), 140))}</small><br>"
                f"{_tcaps(s.get('start_ms'), s.get('end_ms'))} "
                f"<button type=\"button\" class=\"ghost\" data-copy=\"stepjson-{i}\">{t['copy_json']}</button>"
                f"<details><summary>{t['step_json']}</summary><pre id=\"stepjson-{i}\">"
                f"{esc(clean_path_text(json.dumps(s, ensure_ascii=False)[:3000]))}</pre></details>"
                f"</div></div>")
        cards_html = "\n".join(cards)
        tl = []
        for i, s in enumerate(steps, 1):
            c = s.get("concept", {})
            refs = s.get("source_frame_refs", [])
            img = ""
            if refs:
                fn = frame_file_for(d, mid_ref(refs))
                if fn:
                    img = (f"<img alt=\"\" src=\"/frames/{esc(d.name)}/{esc(fn)}\" loading=\"lazy\">")
            plain = (c.get("done") or c.get("action") or "?").strip()
            if len(plain) > 110:
                plain = plain[:107].rsplit(" ", 1)[0] + "…"
            tl.append(
                f"<li data-step=\"{i}\">{img}<span class=\"tt\">"
                f"<b>Step {i}</b> {_tcaps(s.get('start_ms'), s.get('end_ms'))}<br>"
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
        langqs = f"?lang={lang}" if lang != "en" else ""
        self.send_html(RESULT_TMPL.format(css=CSS, run=esc(d.name), videoname=esc(_display_name(src.get("video_name", d.name))), metaline=metaline, manual=manual,
                                          ops=ops, navide_summary=navide_summary, payload=payload, verdict=verdict,
                                          export_preview=export_preview, badges=badges, cards=cards_html,
                                          timeline=timeline, provelog=provelog, hintline=hintline,
                                          flagline=flagline, t=t, lang=lang,
                                          langsw=lang_switcher(lang, self.path), langqs=langqs,
                                          langjs=LANG_JS, ghlink=github_link(t)))

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
            return self.send_html("<h1>File too large</h1><p>Max 100 MB. Try trimming or compressing it first.</p>", 400)
        body = self.rfile.read(length)
        fields, files = parse_multipart(body, m.group(1).strip().encode())
        provider = fields.get("provider", "meta")
        if provider not in ("mock", "meta"):
            return self.send_html("<h1>Bad provider</h1>", 400)
        video = None
        if "video" in files and files["video"][0]:
            fname, data = files["video"]
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", Path(fname).name)[:60]
            video = UPLOAD_DIR / f"{int(time.time())}_{safe}"
            video.write_bytes(data)
        else:
            return self.send_html("<h1>Upload a file</h1>", 400)
        probed = probe_video(video)
        if probed is None:
            return self.send_html(f"<h1>Not a video file</h1><p>{NOT_VIDEO_MSG}</p>", 400)
        if probed["duration"] > 180:
            return self.send_html(
                "<h1>Video too long</h1><p>This video is about {:.0f} seconds, over the "
                "3 minutes / 100 MB limit. Please trim it shorter and upload again.</p>".format(
                    probed["duration"]), 400)
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
        vote3 = str(fields.get("vote3", "") or "").strip().lower() == "yes"
        th = threading.Thread(target=run_cli_job, args=(job_id, video, provider, hint, vote3), daemon=True)
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

