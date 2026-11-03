#!/usr/bin/env python3
"""Minimal local web UI for the Meta #6 video CLI (stdlib only).

Wraps app/cli.py without touching core logic. Binds 127.0.0.1 only.
Usage: python3 server.py [--port 18766]
"""
import argparse
import html
import json
import os
import re
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


def dotenv_key(path=None):
    """Read META_API_KEY from app/.env. The value is never logged or displayed."""
    try:
        text = (path or DOTENV).read_text()
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        m = re.match(r"^META_API_KEY\s*=\s*(.*)$", line)
        if m:
            val = m.group(1).strip().strip("'\"")
            if val:
                return val
    return ""


def effective_meta_key():
    return os.environ.get("META_API_KEY", "").strip() or dotenv_key()
RUNS_DIR = APP_DIR / "runs"
UPLOAD_DIR = RUNS_DIR / "web-uploads"
BUDGET_FILE = RUNS_DIR / "web-ui-budget.json"
MAX_UPLOAD = 100 * 1024 * 1024
MAX_PAID_CALLS = 3
MAX_PAID_USD = 0.10
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
EXAMPLES = {
    "m1": ("M1 AIST washers (14s, CC BY 4.0)", "../materials-survey/research/samples/0001_task_video.mp4"),
    "synthetic": ("Test pattern (synthetic, for pipeline check)", "runs/synthetic-fixture.mp4"),
}


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
    d = (RUNS_DIR / run_id).resolve()
    if RUNS_DIR.resolve() not in d.parents and d != RUNS_DIR.resolve():
        return None
    return d if d.is_dir() else None


def list_runs():
    runs = []
    if not RUNS_DIR.is_dir():
        return runs
    for child in sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not child.is_dir():
            continue
        ops = child / "operations.json"
        if ops.is_file():
            try:
                doc = json.loads(ops.read_text())
                src = doc.get("source", {})
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


def load_budget():
    try:
        return json.loads(BUDGET_FILE.read_text())
    except (ValueError, OSError):
        return {"paid_calls": 0, "spend_usd": 0.0}


def save_budget(b):
    RUNS_DIR.mkdir(exist_ok=True)
    BUDGET_FILE.write_text(json.dumps(b, indent=2))


STAGES = [
    ("frames.json", "Checking video / extracting frames"),
    ("provider-result.json", "Running model"),
    ("operations.json", "Validating operations"),
    ("manual.md", "Writing manual"),
    ("navide-plan.json", "Packaging Navide payload"),
]

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
h1{font-size:32px;line-height:1.2;margin:0 0 6px;letter-spacing:-0.01em}
h2{font-size:19px;margin:22px 0 8px}h3{font-size:16px;margin:16px 0 6px}
.sub{color:#3d4a44;margin:0 0 16px;font-size:16px}
.hero{display:grid;grid-template-columns:1.15fr .85fr;gap:20px;align-items:center;margin:2px 0 18px}
@media(max-width:900px){.hero{grid-template-columns:1fr}}
.hero h1{font-size:38px}
.hero .lede{margin:0}
.hero .shot{margin:0}
.hero .shot img{width:100%;max-height:340px;object-fit:cover;border-radius:10px;border:1px solid #d8dee6;display:block}
.hero .shot figcaption{font-size:12.5px;color:#3d4a44;margin-top:6px}
a.cta{display:inline-block;font-size:16px;font-weight:700;padding:11px 26px;background:#0b5c4c;color:#ffffff!important;border-radius:6px;text-decoration:none;margin-top:12px}
a.cta:hover{background:#08463b}
.pipe{display:flex;align-items:stretch;gap:0;margin:10px 0 4px;flex-wrap:wrap}
.node{flex:1 1 0;min-width:150px;background:#ffffff;border:1px solid #d8dee6;border-radius:8px;padding:10px 14px}
.node b{display:block;font-size:14px}.node span{font-size:12.5px;color:#3d4a44}
.arrow{align-self:center;padding:0 10px;font-size:20px;color:#0b5c4c;font-weight:700}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 0;padding:0;list-style:none}
.chips li{font-size:12.5px;font-weight:600;background:#ffffff;border:1px solid #d8dee6;border-radius:99px;padding:3px 12px;color:#3d4a44}
.card{background:#ffffff;border:1px solid #d8dee6;border-radius:8px;padding:16px 18px;margin:14px 0;min-width:0}
.card.scroll{overflow-x:auto}
.errpage{max-width:660px}
.optgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:4px}
@media(max-width:700px){.optgrid{grid-template-columns:1fr}}
.opt{border:2px solid #d8dee6;border-radius:10px;padding:14px 16px;min-width:0}
.opt h3{margin:0 0 4px;font-size:16px}
.opt p{font-size:13.5px;color:#3d4a44;margin:0 0 8px}
.opt.pick{border-color:#0b5c4c;background:#f4faf7}
label.f{display:block;margin:12px 0 4px;font-weight:600}
.filebtn{display:inline-block;font-size:15px;font-weight:600;padding:8px 16px;background:#ffffff;color:#0b5c4c;border:2px solid #0b5c4c;border-radius:6px;cursor:pointer}
.filebtn:hover{background:#e3efe9}
.filebtn input{display:none}
.filename{margin-left:10px;font-size:14px;color:#3d4a44}
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
.badge.mock{background:#fbdfb9;color:#8a4d00}.badge.live{background:#d3e7d8;color:#166534}
.badge.n{background:#e3efe9;color:#0b5c4c}
.dots{display:flex;gap:6px;margin:10px 0}
.dots i{width:10px;height:10px;border-radius:99px;background:#d8dee6}
.dots i.on{background:#0b5c4c}
.tl{list-style:none;margin:6px 0 0;padding:0 0 0 4px}
.tl li{position:relative;padding:0 0 14px 26px;cursor:pointer;border-radius:6px}
.tl li::before{content:"";position:absolute;left:7px;top:22px;bottom:-2px;width:2px;background:#d8dee6}
.tl li:last-child::before{display:none}
.tl li::after{content:"";position:absolute;left:2px;top:6px;width:10px;height:10px;border-radius:99px;background:#fff;border:3px solid #0b5c4c}
.tl li:hover::after{background:#0b5c4c}
.tl li.sel{background:#e3efe9}
.tl img{width:64px;height:48px;object-fit:cover;border-radius:4px;border:1px solid #d8dee6;vertical-align:middle;margin-right:8px}
.thumb{position:relative;display:inline-block;line-height:0}
.step .thumb{flex:none}
.tpat{position:absolute;left:4px;bottom:4px;font-size:10px;font-weight:700;line-height:1.4;background:#1a2420;color:#ffffff;padding:1px 6px;border-radius:4px;opacity:.88;white-space:nowrap}
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
<div class="hero">
<div class="lede">
<h1>Watch Once</h1>
<p class="sub">Turn any demo video into a human manual and robot-ready steps — in one click.</p>
<p><a class="cta" id="hero-cta" href="#pick">Try an example</a></p>
</div>
{hero_shot}
</div>
<div class="pipe" aria-hidden="true">
<div class="node"><b>1 · Video</b><span>Upload MP4 or pick an example</span></div><div class="arrow">&rarr;</div>
<div class="node"><b>2 · Steps</b><span>AI splits motion into timed steps</span></div><div class="arrow">&rarr;</div>
<div class="node"><b>3 · Manual + JSON</b><span>Read it, or feed it to a robot</span></div>
</div>
<ul class="chips"><li>Runs 100% on this machine</li><li>Mock-first, paid by approval</li><li>Every claim traceable to frames</li></ul>
</div>
<div class="card" id="pick"><h2>1. Pick a video</h2>
<form method="post" action="/analyze" enctype="multipart/form-data">
<div class="optgrid">
<div class="opt" id="opt-upload"><h3>Upload</h3><p>Your own MP4, max 100 MB. Stays on this machine.</p>
<div><label class="filebtn">Choose file<input type="file" name="video" accept=".mp4,video/mp4"></label></div>
<p class="filename" id="fname">No file chosen</p></div>
<div class="opt" id="opt-example"><h3>Try an example</h3><p>Curated clips, ready to analyze.</p>
<div class="selwrap"><select name="example" id="exsel">{examples}</select></div></div>
</div>
<label class="f">Provider</label>
<div class="selwrap"><select name="provider"><option value="mock">mock (free, labeled MOCK)</option>
<option value="meta">Meta Muse Spark 1.2 (paid, max 3 calls / $0.10 total)</option></select></div>
{key_status}
<label class="f"><input type="checkbox" name="confirm" value="yes"> I approve a paid Meta API call</label>
<p class="mono">Budget used: {calls}/3 calls, ${spend} / $0.10</p>
<div class="btnrow"><button type="submit" class="big">Analyze</button>
<button type="submit" class="ghost" name="quick" value="m1">Try M1 example</button></div>
</form></div>
<div class="card scroll"><h2>2. History</h2>{history}</div>
<footer>Local demo · CLI untouched · mock results are labeled MOCK</footer>
</main>
<script>
var fi=document.querySelector('input[type=file]'),es=document.getElementById('exsel');
function mark(){{document.getElementById('opt-upload').classList.toggle('pick',fi.files.length>0);
document.getElementById('opt-example').classList.toggle('pick',!fi.files.length&&es.value!=="");}}
fi.addEventListener('change',function(){{document.getElementById('fname').textContent=fi.files.length?fi.files[0].name:'No file chosen';mark();}});
es.addEventListener('change',mark);
document.getElementById('hero-cta').addEventListener('click',function(){{if(!fi.files.length){{es.value='m1';mark();}};}});
</script>
</body></html>"""

JOB_TMPL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Analyzing {job}</title>{css}
<script>var STAGES=["Checking video","Running model","Validating","Writing manual","Packaging"];
async function poll(){{const r=await fetch("/api/job/{job}");const j=await r.json();
document.getElementById("stage").textContent=j.stage;
document.getElementById("log").textContent=j.log_tail||"";
var dots=document.querySelectorAll(".dots i"),idx=0;
for(var k=0;k<STAGES.length;k++){{if((j.stage||"").indexOf(STAGES[k].split(" ")[0])>=0)idx=k;}}
if(j.state==="done")idx=STAGES.length;
dots.forEach(function(d,k){{d.className=k<idx?"on":"";}});
if(j.state==="done"){{location.href="/result/"+encodeURIComponent(j.run_id);}}
else if(j.state==="failed"){{document.getElementById("err").textContent="FAILED: "+(j.error||"");}}
else setTimeout(poll,1500);}}window.onload=poll;</script></head>
<body><main><h1>Analyzing&hellip;</h1><p class="sub mono">{job}</p>
<div class="card"><div class="dots"><i></i><i></i><i></i><i></i><i></i></div><h2 id="stage">Starting</h2><p id="err" class="err"></p>
<pre id="log" class="mono"></pre></div></main></body></html>"""

RESULT_TMPL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Result {run}</title>{css}</head>
<body><main>
<p><a href="/">&larr; Home</a></p>
<h1>Result: {run}</h1>
<p>{badges}</p>
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


def run_cli_job(job_id, video_path, provider):
    out_name = f"web-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = RUNS_DIR / out_name
    with jobs_lock:
        jobs[job_id].update(state="running", stage=STAGES[0][1], run_id=out_name)
    cmd = [sys.executable, "-B", "cli.py", str(video_path),
           "--workspace-path", str(WORKSPACE), "--provider", provider,
           "--out", f"runs/{out_name}"]
    if provider == "meta":
        cmd.append("--confirm-paid-api")
    try:
        env = None
        if provider == "meta" and not os.environ.get("META_API_KEY", "").strip():
            file_key = dotenv_key()
            if file_key:
                env = dict(os.environ, META_API_KEY=file_key)
        proc = subprocess.Popen(cmd, cwd=str(APP_DIR), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env)
        log_lines = []
        while True:
            line = proc.stdout.readline()
            if line:
                log_lines.append(clean_path_text(line.rstrip()))
            stage = STAGES[0][1]
            for fname, sname in STAGES:
                if (out_dir / fname).is_file():
                    stage = sname
            with jobs_lock:
                jobs[job_id].update(stage=stage, log_tail="\n".join(log_lines[-12:]))
            if line == "" and proc.poll() is not None:
                break
        rc = proc.returncode
        err = "" if rc == 0 else clean_path_text("\n".join(log_lines[-6:]) or f"exit {rc}")
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
        with jobs_lock:
            jobs[job_id].update(state="done" if rc == 0 else "failed", error=err,
                                stage="Done" if rc == 0 else "Failed")
    except (OSError, ValueError) as exc:
        with jobs_lock:
            jobs[job_id].update(state="failed", error=clean_path_text(exc), stage="Failed")


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
                    f"Go back and try again with a built-in example.</p>"
                    f"<p><a class=\"cta\" href=\"/#pick\">Back to examples</a></p></div>"
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
            f'<td>{esc(r["video"])}</td><td>{esc(r["provider"])}'
            f'{" (MOCK)" if r["mock"] else ""}</td><td>{r["steps"]}</td><td>{esc(r["mtime"])}</td></tr>'
            for r in runs)
        history = (f"<table><tr><th>Run</th><th>Video</th><th>Provider</th><th>Steps</th><th>Time</th></tr>{rows}</table>"
                   if rows else "<p>No runs with operations.json yet.</p>")
        examples = "".join(f'<option value="{k}"{" selected" if k == "m1" else ""}>{esc(v[0])}</option>'
                                 for k, v in EXAMPLES.items())
        b = load_budget()
        hero_shot = ""
        try:
            run_dirs = sorted(d for d in RUNS_DIR.iterdir() if d.is_dir())
        except OSError:
            run_dirs = []
        for rd in run_dirs:
            cands = sorted((rd / "frames").glob("*.jpg")) if (rd / "frames").is_dir() else []
            if not cands:
                cands = sorted(rd.glob("*.jpg"))
            if cands:
                hero_shot = (f"<figure class=\"shot\"><img alt=\"Sample frame\" "
                             f"src=\"/frames/{esc(rd.name)}/{esc(cands[0].name)}\" loading=\"lazy\">"
                             f"<figcaption>One frame &rarr; timed steps &rarr; manual + robot JSON.</figcaption></figure>")
                break
        if effective_meta_key():
            key_status = '<p class="mono">Meta API key: <b class="ok">configured</b></p>'
        else:
            key_status = ('<p class="mono">Meta API key: <b class="warn">not configured</b></p>'
                          '<p class="sub">To enable live Meta inference, run '
                          '<code>export META_API_KEY=…</code> before starting the server, or copy '
                          '<code>.env.example</code> to <code>.env</code> in app/ and fill it in. '
                          'The key never leaves this machine and is never shown here.</p>')
        self.send_html(HOME_TMPL.format(css=CSS, examples=examples, history=history, hero_shot=hero_shot,
                                        key_status=key_status,
                                        calls=int(b.get("paid_calls", 0)),
                                        spend=f"{float(b.get('spend_usd', 0.0)):.4f}"))

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
        is_synth = "synthetic" in str(src.get("video_name", ""))

        def badge(img):
            if not img or not is_synth:
                return img
            return (f'<span class="thumb">{img}'
                    '<span class="tpat">TEST PATTERN</span></span>')

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
        badges = (f"<span class=\"badge {'mock' if is_mock else 'live'}\">"
                  f"{'MOCK' if is_mock else 'META'}</span>"
                  f"<span class=\"badge n\">{len(steps)} steps</span>")
        meta = esc(src.get('video_name', '?'))
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
                f"<div class=\"step\" data-step=\"{i}\">{badge(img)}<div class=\"t\">"
                f"<b>Step {i} · {esc(c.get('action', '?')[:60])}</b><br>"
                f"{esc(c.get('object', '?')[:80])} &rarr; {esc(c.get('target', '?')[:80])}<br>"
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
                f"<li data-step=\"{i}\">{badge(img)}<span class=\"tt\">"
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
                                          timeline=timeline, provelog=provelog))

    def do_POST(self):
        parts = urlsplit(self.path)
        if parts.path == "/analyze":
            return self.post_analyze()
        if parts.path.startswith("/api/navide/"):
            return self.post_navide(parts.path[len("/api/navide/"):])
        return self.send_html("<h1>Not found</h1>", 404)

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
        if fields.get("quick") == "m1" and not (files.get("video") or [b""])[0]:
            fields["example"] = "m1"
        provider = fields.get("provider", "mock")
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
        elif fields.get("example") in EXAMPLES:
            video = (APP_DIR / EXAMPLES[fields["example"]][1]).resolve()
            if not video.is_file():
                return self.send_html("<h1>Example missing</h1>", 404)
        else:
            return self.send_html("<h1>Upload a file or pick an example</h1>", 400)
        if provider == "meta":
            if fields.get("confirm") != "yes":
                return self.send_html("<h1>Paid API needs explicit confirmation</h1>", 400)
            b = load_budget()
            if int(b.get("paid_calls", 0)) >= MAX_PAID_CALLS or float(b.get("spend_usd", 0.0)) >= MAX_PAID_USD:
                return self.send_html("<h1>Paid budget exhausted (3 calls / $0.10)</h1>", 400)
            if not effective_meta_key():
                return self.send_html("<h1>Server has no META_API_KEY: export it before starting, "
                                      "or put it in app/.env (see .env.example)</h1>", 400)
        job_id = f"job-{int(time.time() * 1000)}"
        with jobs_lock:
            jobs[job_id] = {"state": "queued", "stage": "Queued", "run_id": "", "log_tail": "", "error": ""}
        th = threading.Thread(target=run_cli_job, args=(job_id, video, provider), daemon=True)
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

