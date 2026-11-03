"""Web UI regression: error page + browser fetch (CSP connect-src) + key status.

Covers round-6/7/8 fixes without binding a port or spawning CLI jobs:
socketpair drives web.server.Handler directly; the /analyze worker thread
is stubbed so no runs/ directory is created.
"""
import os
import socket
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import APP_DIR

sys.path.insert(0, str(APP_DIR / "web"))
import server as WEB  # noqa: E402

DUMMY = types.SimpleNamespace(server_version="T", sys_version="")
REAL_THREAD = threading.Thread


def request(raw):
    a, b = socket.socketpair()
    errs = []

    def serve():
        try:
            WEB.Handler(b, ("127.0.0.1", 0), DUMMY)
        except Exception as e:  # pragma: no cover
            errs.append(repr(e))

    t = REAL_THREAD(target=serve, daemon=True)
    t.start()
    a.sendall(raw)
    a.settimeout(15)
    out = b""
    try:
        while b"\r\n\r\n" not in out:
            ch = a.recv(65536)
            if not ch:
                break
            out += ch
        head, _, rest = out.partition(b"\r\n\r\n")
        want = 0
        for ln in head.decode("latin1").splitlines():
            if ln.lower().startswith("content-length:"):
                want = int(ln.split(":", 1)[1].strip())
        while len(rest) < want:
            ch = a.recv(65536)
            if not ch:
                break
            rest += ch
        out = head + b"\r\n\r\n" + rest
    finally:
        try:
            b.close()
        except OSError:
            pass
        a.close()
    t.join(timeout=10)
    assert not errs, errs
    head, _, data = out.partition(b"\r\n\r\n")
    return head.decode("latin1"), data


class WebRegressionTests(unittest.TestCase):
    def test_error_page_has_viewport_and_cta(self):
        head, data = request(b"GET /result/does-not-exist HTTP/1.1\r\n"
                             b"Host: x\r\nConnection: close\r\n\r\n")
        self.assertIn("404", head.splitlines()[0])
        html = data.decode()
        self.assertIn('name="viewport"', html)
        self.assertIn("Back to upload", html)
        self.assertIn('href="/#pick"', html)
        self.assertNotIn("example", html.lower())

    def test_csp_allows_same_origin_fetch(self):
        head, _ = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        csp = [ln for ln in head.splitlines() if ln.lower().startswith("content-security-policy")]
        self.assertTrue(csp, "CSP header missing")
        self.assertIn("connect-src 'self'", csp[0])

    def test_browser_shaped_post_returns_303_without_spawn(self):
        syn = WEB.RUNS_DIR / "synthetic-fixture.mp4"
        if not syn.is_file():
            self.skipTest("synthetic fixture not present")
        bound = "----WebKitFormBoundary7AbC9xYz"
        body = (f"--{bound}\r\nContent-Disposition: form-data; "
                f'name="provider"\r\n\r\nmock\r\n').encode()
        body += (f"--{bound}\r\nContent-Disposition: form-data; "
                 f'name="video"; filename="syn.mp4"\r\n'
                 f"Content-Type: video/mp4\r\n\r\n").encode() + syn.read_bytes() + b"\r\n"
        body += f"--{bound}--\r\n".encode()
        raw = (f"POST /analyze HTTP/1.1\r\nHost: 127.0.0.1\r\n"
               f"Origin: http://127.0.0.1\r\nReferer: http://127.0.0.1/\r\n"
               f"User-Agent: Mozilla/5.0 (Macintosh) TestBrowser/1.0\r\n"
               f"Accept: */*\r\n"
               f"Content-Type: multipart/form-data; boundary={bound}\r\n"
               f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
        started = []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                started.append((target.__name__, args[0]))

            def start(self):
                pass

        with tempfile.TemporaryDirectory() as td, \
                patch.object(WEB, "UPLOAD_DIR", Path(td) / "up"), \
                patch.object(WEB.threading, "Thread", FakeThread):
            head, _ = request(raw)
        self.assertIn("303", head.splitlines()[0])
        loc = [ln for ln in head.splitlines() if ln.lower().startswith("location:")]
        self.assertTrue(loc and "/job/" in loc[0])
        self.assertEqual(len(started), 1)
        with WEB.jobs_lock:
            WEB.jobs.pop(started[0][1], None)

    def _post_analyze(self, fields, file_content=None, filename="clip.mp4"):
        bound = "----TestBoundary9xYz"
        body = b""
        for k, v in fields:
            body += (f"--{bound}\r\nContent-Disposition: form-data; "
                     f'name="{k}"\r\n\r\n{v}\r\n').encode()
        if file_content is not None:
            body += (f"--{bound}\r\nContent-Disposition: form-data; "
                     f'name="video"; filename="{filename}"\r\n'
                     f"Content-Type: video/mp4\r\n\r\n").encode() + file_content + b"\r\n"
        body += f"--{bound}--\r\n".encode()
        raw = (f"POST /analyze HTTP/1.1\r\nHost: 127.0.0.1\r\n"
               f"Content-Type: multipart/form-data; boundary={bound}\r\n"
               f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
        started = []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                started.append(args)

            def start(self):
                pass

        with tempfile.TemporaryDirectory() as td, \
                patch.object(WEB, "UPLOAD_DIR", Path(td) / "up"), \
                patch.object(WEB.threading, "Thread", FakeThread):
            head, data = request(raw)
        for args in started:
            with WEB.jobs_lock:
                WEB.jobs.pop(args[0], None)
        return head, data, started

    def test_post_without_file_rejected(self):
        head, data, started = self._post_analyze(
            [("quick", "m1"), ("provider", "meta"), ("example", "m1")])
        self.assertIn("400", head.splitlines()[0])
        self.assertIn("Upload a file", data.decode())
        self.assertEqual(started, [])

    def test_hint_passed_to_worker(self):
        syn = WEB.RUNS_DIR / "synthetic-fixture.mp4"
        if not syn.is_file():
            self.skipTest("synthetic fixture not present")
        head, _, started = self._post_analyze(
            [("provider", "mock"), ("task_hint", "  washers   on a shaft  ")],
            file_content=syn.read_bytes(), filename="syn.mp4")
        self.assertIn("303", head.splitlines()[0])
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][3], "washers on a shaft")

    def test_vote3_checkbox_passed_to_worker(self):
        syn = WEB.RUNS_DIR / "synthetic-fixture.mp4"
        if not syn.is_file():
            self.skipTest("synthetic fixture not present")
        head, _, started = self._post_analyze(
            [("provider", "mock"), ("vote3", "yes")],
            file_content=syn.read_bytes(), filename="syn.mp4")
        self.assertIn("303", head.splitlines()[0])
        self.assertEqual(len(started), 1)
        self.assertTrue(started[0][4])
        head, _, started = self._post_analyze(
            [("provider", "mock")],
            file_content=syn.read_bytes(), filename="syn.mp4")
        self.assertIn("303", head.splitlines()[0])
        self.assertFalse(started[0][4])

    def test_vote3_form_label_shows_cost(self):
        head, data = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertIn("Majority vote", data.decode())
        self.assertIn("3&times; cost", data.decode())

    def test_mock_fixture_matches(self):
        syn = WEB.RUNS_DIR / "synthetic-fixture.mp4"
        if not syn.is_file():
            self.skipTest("synthetic fixture not present")
        self.assertTrue(WEB.mock_fixture_matches(syn))
        with tempfile.TemporaryDirectory() as td:
            other = Path(td) / "other.mp4"
            other.write_bytes(b"definitely not the fixture")
            self.assertFalse(WEB.mock_fixture_matches(other))

    def test_mock_wrong_video_rejected_no_job(self):
        with tempfile.TemporaryDirectory() as td:
            real = self._make_clip(td, "other.mp4")
            with WEB.jobs_lock:
                before = set(WEB.jobs)
            head, data, started = self._post_analyze(
                [("provider", "mock")],
                file_content=real.read_bytes(), filename="other.mp4")
            with WEB.jobs_lock:
                after = set(WEB.jobs)
        self.assertIn("400", head.splitlines()[0])
        self.assertIn("Fixture does not match", data.decode())
        self.assertEqual(started, [])
        self.assertEqual(before, after)

    def test_garbage_upload_rejected_plainly_before_guard(self):
        # A 7-byte garbage .mov must say "cannot be read", not fixture mismatch.
        with WEB.jobs_lock:
            before = set(WEB.jobs)
        head, data, started = self._post_analyze(
            [("provider", "mock")],
            file_content=b"garbage", filename="bad.mov")
        with WEB.jobs_lock:
            after = set(WEB.jobs)
        self.assertIn("400", head.splitlines()[0])
        self.assertIn("Not a video file", data.decode())
        self.assertNotIn("Fixture does not match", data.decode())
        self.assertEqual(started, [])
        self.assertEqual(before, after)

    def test_home_shows_key_status_without_value(self):
        with patch.dict(os.environ, {"META_API_KEY": "sentinel-KEY-999"}):
            head, data = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        html = data.decode()
        self.assertIn("Meta API key:", html)
        self.assertIn(">configured · ****-999<", html)
        self.assertNotIn("sentinel-KEY-999", html)

    def test_home_shows_setup_hint_when_no_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_API_KEY", None)
            if WEB.DOTENV.is_file():
                self.skipTest("real app/.env present; hint covered by dotenv test")
            head, data = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        html = data.decode()
        self.assertIn("not configured", html)
        self.assertIn(".env.example", html)

    def test_dotenv_parsing(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".env"
            p.write_text("# comment\nEMPTY=\nMETA_API_KEY_FOO=nope\n"
                         "export META_API_KEY='file-key-abc'\n")
            self.assertEqual(WEB.dotenv_key(p), "file-key-abc")
            self.assertEqual(WEB.dotenv_key(Path(td) / "missing"), "")
            p.write_text("META_API_KEY=\n")
            self.assertEqual(WEB.dotenv_key(p), "")

    def test_dotenv_example_ships_empty(self):
        ex = Path(WEB.APP_DIR) / ".env.example"
        self.assertTrue(ex.is_file())
        self.assertEqual(WEB.dotenv_key(ex), "")

    def _get_home(self, env_key=None):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(WEB, "RUNS_DIR", Path(td)), \
                    patch.object(WEB, "EVIDENCE_DIR", Path(td) / "no-evidence"), \
                    patch.object(WEB, "DOTENV", Path(td) / ".env"), \
                    patch.dict(os.environ, {}, clear=False):
                os.environ.pop("META_API_KEY", None)
                if env_key:
                    os.environ["META_API_KEY"] = env_key
                _, data = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        return data.decode()

    def test_home_no_key_cta(self):
        html = self._get_home()
        self.assertIn("Upload a how-to video. Get back:", html)
        self.assertIn('class="gbgrid"', html)
        self.assertEqual(html.count('class="gb"'), 4)
        self.assertIn("Human manual", html)
        self.assertIn("Robot-ready step JSON", html)
        self.assertIn("object / action / target / pre / done + timestamps", html)
        self.assertIn("LeRobot annotations, VLA instructions, planner steps", html)
        self.assertIn("generated for review only, not auto-submitted", html)
        self.assertIn("Best for: one person or machine doing one thing", html)
        self.assertIn("may split into 2 or 4 steps", html)
        self.assertNotIn("any video", html.lower())
        self.assertIn('id="drop"', html)
        self.assertIn("Drop a video here or click to choose", html)
        self.assertIn("any format ffmpeg can read", html)
        self.assertIn('<label class="drop"', html)
        self.assertIn("Choose file", html)
        self.assertIn('class="vh"', html)
        self.assertIn('id="rmfile"', html)
        self.assertLess(html.find("</label>"), html.find('id="rmfile"'))
        self.assertLess(html.find('id="pick"'), html.find("How it works"))
        self.assertIn('href="#pick">Upload a video', html)
        self.assertNotIn("demo", html.lower())
        cleaned = html.lower().replace(".env.example", "")
        self.assertNotIn("example", cleaned)
        self.assertNotIn('name="example"', html)
        self.assertIn("Turn a how-to video into a manual for people and step data for robots.", html)
        self.assertIn("Upload a short video &rarr; we split it into timed steps", html)
        self.assertIn("How it works", html)
        self.assertNotIn("Advanced:", html)
        self.assertIn('class="keyline"', html)
        self.assertIn('name="task_hint"', html)
        self.assertIn("naming help only", html)
        self.assertIn("No runs yet", html)
        self.assertNotIn("Before: one frame in", html)
        self.assertIn("Set up your Meta API key to analyze", html)
        self.assertNotIn("about US$0.02-0.03", html)
        self.assertNotIn('name="provider"', html)
        self.assertNotIn('name="example"', html)
        self.assertIn("Budget used today:", html)
        self.assertNotIn("mock", html.lower())
        self.assertNotIn("TEST PATTERN", html)
        self.assertIn('id="settingsDlg"', html)
        self.assertIn('id="dlgKeyForm"', html)
        self.assertNotIn('dlgLimForm', html)
        self.assertIn('id="keywrap"', html)
        self.assertIn('data-opendlg', html)
        self.assertIn('name="csrf"', html)
        self.assertIn('method="dialog"', html)

    def test_settings_redirects_home_anchor(self):
        head, _ = request(b"GET /settings HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertIn("303", head.splitlines()[0])
        loc = [ln for ln in head.splitlines() if ln.lower().startswith("location:")]
        self.assertTrue(loc and loc[0].strip().endswith("/#settings"))

    def test_home_with_key_shows_analyze(self):
        html = self._get_home("sentinel-KEY-999")
        self.assertIn(">Analyze</button>", html)
        self.assertIn("This analysis uses your Meta API credit (about US$0.02-0.03 for a 14s clip", html)
        self.assertIn("includes full-resolution keyframes", html)
        self.assertNotIn("Set up your Meta API key to analyze", html)
        self.assertNotIn("sentinel-KEY-999", html)

    def test_history_hides_mock_runs(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            for rid, mock in (("r-meta", False), ("r-mock", True)):
                d = rd / rid
                d.mkdir()
                (d / "operations.json").write_text(json.dumps(
                    {"source": {"provider": "meta", "mock": mock, "video_name": "v.mp4"},
                     "steps": []}))
            with patch.object(WEB, "RUNS_DIR", rd):
                ids = [r["id"] for r in WEB.list_runs()]
        self.assertEqual(ids, ["r-meta"])

    def test_home_showcase_beforeafter(self):
        if WEB.safe_run_dir(WEB.SHOWCASE_RUN) is None:
            self.skipTest("showcase run not present (e.g. fresh clone)")
        _, data = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        html = data.decode()
        hero, how = html.split("How it works")
        self.assertIn("Before: one frame in", hero)
        self.assertIn("After: timed steps + manual", hero)
        self.assertIn("step-001", hero)
        self.assertIn("Example output from a real Muse Spark 1.2 run (prompt v2)", hero)
        self.assertNotIn("open full result", hero)
        self.assertNotIn(f"/result/{WEB.SHOWCASE_RUN}", hero)
        self.assertNotIn("Before: one frame in", how)
        self.assertNotIn("synthetic", hero)

    def test_no_test_pattern_badge_anywhere(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            for rid, video in (("t-syn", "synthetic-fixture.mp4"), ("t-m1", "x.mp4")):
                d = rd / rid
                (d / "frames").mkdir(parents=True)
                (d / "frames" / "f-frame-1.jpg").write_bytes(b"fakejpg")
                (d / "operations.json").write_text(json.dumps({
                    "source": {"video_name": video, "provider": "mock", "mock": True},
                    "steps": [{"concept": {"action": "a", "object": "o", "target": "t"},
                               "source_frame_refs": ["frame-1"]}]}))
                (d / "manual.md").write_text("# Operation manual\n")
            with patch.object(WEB, "RUNS_DIR", rd):
                _, syn = request(b"GET /result/t-syn HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                _, m1 = request(b"GET /result/t-m1 HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                _, home = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertNotIn("TEST PATTERN", syn.decode())
        self.assertNotIn("TEST PATTERN", m1.decode())
        self.assertNotIn("TEST PATTERN", home.decode())

    def test_review_flags_even_split(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "r"
            d.mkdir()
            steps = [{"id": f"s{i}", "start_ms": i * 2400, "end_ms": (i + 1) * 2400}
                     for i in range(4)]
            (d / "operations.json").write_text(json.dumps({"steps": steps}))
            flags = WEB.review_flags(d)
            self.assertEqual(len(flags), 1)
            self.assertIn("Even split", flags[0])
            self.assertIn("4 steps", flags[0])
            steps[3]["end_ms"] = 13000
            (d / "operations.json").write_text(json.dumps({"steps": steps}))
            self.assertEqual(WEB.review_flags(d), [])
            (d / "operations.json").write_text(json.dumps({"steps": steps[:2]}))
            self.assertEqual(WEB.review_flags(d), [])

    def test_result_shows_hint_and_flags(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            d = rd / "t-hint"
            (d / "frames").mkdir(parents=True)
            (d / "frames" / "f-frame-1.jpg").write_bytes(b"fakejpg")
            steps = [{"id": f"s{i}", "start_ms": i * 2400, "end_ms": (i + 1) * 2400,
                      "concept": {"action": "a", "object": "o", "target": "t"},
                      "source_frame_refs": ["frame-1"],
                      "evidence": "seen at frame-1"} for i in range(4)]
            (d / "operations.json").write_text(json.dumps({
                "source": {"video_name": "x.mp4", "provider": "meta", "mock": False},
                "steps": steps}))
            (d / "manual.md").write_text("# Operation manual\n")
            (d / "provider-result.json").write_text(json.dumps(
                {"metadata": {"task_hint": "washers on a shaft"}}))
            with patch.object(WEB, "RUNS_DIR", rd):
                _, data = request(b"GET /result/t-hint HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        html = data.decode()
        self.assertIn("Task hint used: washers on a shaft", html)
        self.assertIn("Even split", html)
        self.assertIn("Evidence: seen at frame-1", html)

    def test_rendered_scripts_pass_node_check(self):
        import json
        import re
        import shutil
        import subprocess
        if shutil.which("node") is None:
            self.skipTest("node not present")
        pages = []
        _, home = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        pages.append(("home", home.decode()))
        with WEB.jobs_lock:
            WEB.jobs["job-jstest"] = {"state": "running", "stage": "Queued",
                                      "run_id": "", "log_tail": "", "error": "",
                                      "provider": "mock", "video_name": "v.mp4",
                                      "started_at": 0, "events": [], "detail": {}}
        try:
            _, job = request(b"GET /job/job-jstest HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            pages.append(("job", job.decode()))
        finally:
            with WEB.jobs_lock:
                WEB.jobs.pop("job-jstest", None)
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            d = rd / "t-js"
            (d / "frames").mkdir(parents=True)
            (d / "operations.json").write_text(json.dumps({
                "source": {"video_name": "x.mp4", "provider": "meta", "mock": False},
                "steps": [{"id": "s1", "start_ms": 0, "end_ms": 1000,
                           "concept": {"action": "a", "object": "o", "target": "t"},
                           "source_frame_refs": []}]}))
            (d / "manual.md").write_text("# Operation manual\n")
            with patch.object(WEB, "RUNS_DIR", rd):
                _, res = request(b"GET /result/t-js HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                pages.append(("result", res.decode()))
        self.assertTrue(any("<script>" in p for _, p in pages))
        with tempfile.TemporaryDirectory() as td:
            for name, html in pages:
                for i, m in enumerate(re.finditer(r"<script>(.*?)</script>", html, re.S)):
                    f = Path(td) / f"{name}-{i}.js"
                    f.write_text(m.group(1))
                    r = subprocess.run(["node", "--check", str(f)],
                                       capture_output=True, text=True, timeout=30)
                    self.assertEqual(r.returncode, 0,
                                     f"{name} script {i}: {r.stderr[:300]}")

    def _make_clip(self, td, name, vcodec=None, acodec=None, noaudio=False, size="160x120",
                   rate=10):
        import shutil
        import subprocess
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not present")
        p = Path(td) / name
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
               "-i", f"testsrc=duration=1:size={size}:rate={rate}"]
        if not noaudio:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        if vcodec:
            cmd += ["-c:v", vcodec]
        if acodec and not noaudio:
            cmd += ["-c:a", acodec]
        cmd += ["-shortest", str(p)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stderr[:200])
        return p

    def test_prepare_upload_converts_mov_and_webm(self):
        import shutil
        import subprocess
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not present")

        def probe(path):
            r = subprocess.run(["ffprobe", "-v", "error",
                                "-show_entries", "stream=codec_name",
                                "-show_entries", "format=duration",
                                "-of", "default=noprint_wrappers=1", str(path)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0)
            codecs, dur = [], 0.0
            for line in r.stdout.splitlines():
                if line.startswith("codec_name="):
                    codecs.append(line.split("=", 1)[1])
                elif line.startswith("duration="):
                    dur = float(line.split("=", 1)[1])
            return codecs, dur

        with tempfile.TemporaryDirectory() as td:
            cases = [("hevc.mov", "libx265", None, False),
                     ("vp9.webm", "libvpx-vp9", None, False),
                     ("c.3gp", "h263", "aac", False, "176x144"),
                     ("c.ts", "mpeg2video", "mp2", False, "160x120", 25),
                     ("c.mpg", "mpeg1video", "mp2", False, "160x120", 25),
                     ("c.wmv", "wmv2", "wmav2", False),
                     ("c.ogv", "libvpx", None, False),
                     ("c.gif", "gif", None, True),
                     ("noaudio.mp4", "libx264", None, True)]
            for case in cases:
                name, vc, ac, na = case[:4]
                size = case[4] if len(case) > 4 else "160x120"
                rate = case[5] if len(case) > 5 else 10
                src = self._make_clip(td, name, vc, ac, na, size, rate)
                _, dur_before = probe(src)
                out, prov = WEB.prepare_upload(src)
                self.assertTrue(out.is_file(), name)
                self.assertEqual(out.suffix, ".mp4", name)
                self.assertEqual(set(prov), {"original_name", "original_sha256",
                                             "transcoded_sha256", "note", "source"}, name)
                self.assertEqual(prov["original_name"], name)
                self.assertEqual(len(prov["original_sha256"]), 64)
                self.assertEqual(len(prov["transcoded_sha256"]), 64)
                if name == "noaudio.mp4":
                    self.assertIn("no transcode", prov["note"])
                    self.assertEqual(out, src)
                    continue
                self.assertIn("transcoded for upload", prov["note"])
                codecs, dur_after = probe(out)
                self.assertIn("h264", codecs, name)
                if not na:
                    self.assertIn("aac", codecs, name)
                self.assertLess(abs(dur_after - dur_before), 0.5, name)

    def test_transcoded_clip_runs_mock_pipeline(self):
        import json
        import shutil
        import subprocess
        import sys
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not present")
        with tempfile.TemporaryDirectory(dir=str(WEB.APP_DIR)) as td:
            src = self._make_clip(td, "clip.mov")
            mp4, _ = WEB.prepare_upload(src)
            fixture = {"steps": [{"id": "t1", "start_ms": 0, "end_ms": 500,
                                   "concept": {"object": "o", "action": "a",
                                             "target": "t", "pre": "p", "done": "d"},
                                   "source_frame_refs": ["frame-000001"],
                                   "evidence": "test clip frame"}]}
            fp = Path(td) / "resp.json"
            fp.write_text(json.dumps(fixture))
            out = Path(td) / "w13run"
            r = subprocess.run(
                [sys.executable, "-B", "cli.py", str(mp4),
                 "--workspace-path", str(WEB.APP_DIR),
                 "--provider", "mock", "--mock-response", str(fp),
                 "--out", str(out.relative_to(WEB.APP_DIR))],
                cwd=str(WEB.APP_DIR), capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            self.assertTrue((out / "operations.json").is_file())
            self.assertTrue((out / "manual.md").is_file())

    def test_prepare_upload_rejects_bad_file_plainly(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.mp4"
            bad.write_bytes(b"not a video at all")
            with self.assertRaises(Exception) as cm:
                WEB.prepare_upload(bad)
            self.assertIn("not a video file", str(cm.exception))
            txt = Path(td) / "note.txt"
            txt.write_text("hello")
            with self.assertRaises(Exception) as cm:
                WEB.prepare_upload(txt)
            self.assertIn("not a video file", str(cm.exception))
            png = Path(td) / "img.png"
            png.write_bytes(bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"))
            with self.assertRaises(Exception) as cm:
                WEB.prepare_upload(png)
            self.assertIn("not a video file", str(cm.exception))

    def test_post_mov_passes_extension_check(self):
        # A non-fixture .mov reaches the mock guard (not the extension gate),
        # proving .mov uploads are accepted at the door.
        with tempfile.TemporaryDirectory() as td:
            src = self._make_clip(td, "clip.mov")
            head, data, started = self._post_analyze(
                [("provider", "mock")],
                file_content=src.read_bytes(), filename="clip.mov")
        self.assertIn("400", head.splitlines()[0])
        self.assertIn("Fixture does not match", data.decode())
        self.assertEqual(started, [])

    def test_post_exe_rejected(self):
        head, data, started = self._post_analyze(
            [("provider", "mock")],
            file_content=b"MZ fake", filename="run.exe")
        self.assertIn("400", head.splitlines()[0])
        self.assertIn("not a video file", data.decode())
        self.assertEqual(started, [])

    def test_plain_cli_error_mapping(self):
        self.assertIn("check the key", WEB.plain_cli_error("ERROR: Meta upload HTTP 401; no response request made."))
        self.assertIn("429", WEB.plain_cli_error("Meta responses HTTP 429; no task exported."))
        self.assertIn("timeout", WEB.plain_cli_error("Meta request timed out; billing unknown.").lower())
        self.assertIn("network", WEB.plain_cli_error("Meta request failed; remote withheld.").lower())
        self.assertEqual(WEB.plain_cli_error("Source exceeds 60 seconds; input rejected."),
                         "Source exceeds 60 seconds; input rejected.")

    def test_failed_job_renders_error_state(self):
        with WEB.jobs_lock:
            WEB.jobs["job-regtest"] = {"state": "failed", "stage": "Failed",
                                       "run_id": "", "log_tail": "", "error": "boom-regtest"}
        try:
            head, data = request(b"GET /job/job-regtest HTTP/1.1\r\n"
                                 b"Host: x\r\nConnection: close\r\n\r\n")
            self.assertIn("200", head.splitlines()[0])
            self.assertIn('id="err"', data.decode())
            import json
            head, data = request(b"GET /api/job/job-regtest HTTP/1.1\r\n"
                                 b"Host: x\r\nConnection: close\r\n\r\n")
            self.assertEqual(json.loads(data.decode())["state"], "failed")
        finally:
            with WEB.jobs_lock:
                WEB.jobs.pop("job-regtest", None)


class I18nTests(unittest.TestCase):
    def test_default_is_english_unchanged(self):
        head, data = request(b"GET / HTTP/1.1\r\n"
                             b"Host: x\r\nConnection: close\r\n\r\n")
        html = data.decode()
        self.assertIn('<html lang="en">', html)
        self.assertIn("Turn a how-to video into a manual for people", html)
        self.assertIn("1. Pick a video", html)
        self.assertIn("How it works", html)

    def test_lang_packs_have_same_keys_and_render(self):
        import json
        base = set(json.loads((APP_DIR / "web" / "i18n" / "en.json").read_text()))
        self.assertGreater(len(base), 90)
        for code in ("zh-Hant", "zh-Hans", "ja", "es"):
            pack = set(json.loads((APP_DIR / "web" / "i18n" / f"{code}.json").read_text()))
            self.assertEqual(pack, base, code)
            raw = (f"GET /?lang={code} HTTP/1.1\r\n"
                   "Host: x\r\nConnection: close\r\n\r\n").encode()
            head, data = request(raw)
            self.assertIn("200", head.splitlines()[0])
            self.assertIn(f'<html lang="{code}">', data.decode())

    def test_lang_falls_back_to_english(self):
        self.assertEqual(WEB.load_lang("xx")["hero_cta"], "Upload a video")
        head, data = request(b"GET /?lang=xx HTTP/1.1\r\n"
                             b"Host: x\r\nConnection: close\r\n\r\n")
        self.assertIn('<html lang="en">', data.decode())

    def test_switcher_and_persistence_hook_present(self):
        head, data = request(b"GET / HTTP/1.1\r\n"
                             b"Host: x\r\nConnection: close\r\n\r\n")
        html = data.decode()
        self.assertIn('class="langsw"', html)
        self.assertIn('?lang=ja', html)
        self.assertIn("localStorage.setItem('wolang'", html)
        self.assertIn('class="langsel"', html)
        self.assertIn("🌐", html)
        self.assertIn("After: timed steps + manual", html)

    def test_model_output_note_shown(self):
        import re
        run = None
        for d in sorted((APP_DIR / "runs").glob("web-*")):
            if (d / "operations.json").is_file():
                run = d.name
                break
        if run is None:
            self.skipTest("no web run present")
        raw = (f"GET /result/{run} HTTP/1.1\r\n"
               "Host: x\r\nConnection: close\r\n\r\n").encode()
        head, data = request(raw)
        self.assertIn("model output, shown in the original language", data.decode())


if __name__ == "__main__":
    unittest.main()
