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
        self.assertIn("Back to examples", html)
        self.assertIn('href="/#pick"', html)

    def test_csp_allows_same_origin_fetch(self):
        head, _ = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        csp = [ln for ln in head.splitlines() if ln.lower().startswith("content-security-policy")]
        self.assertTrue(csp, "CSP header missing")
        self.assertIn("connect-src 'self'", csp[0])

    def test_browser_shaped_post_returns_303_without_spawn(self):
        bound = "----WebKitFormBoundary7AbC9xYz"
        body = b""
        for k, v in (("provider", "mock"), ("example", "synthetic")):
            body += (f"--{bound}\r\nContent-Disposition: form-data; "
                     f'name="{k}"\r\n\r\n{v}\r\n').encode()
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

        with patch.object(WEB.threading, "Thread", FakeThread):
            head, _ = request(raw)
        self.assertIn("303", head.splitlines()[0])
        loc = [ln for ln in head.splitlines() if ln.lower().startswith("location:")]
        self.assertTrue(loc and "/job/" in loc[0])
        self.assertEqual(len(started), 1)
        with WEB.jobs_lock:
            WEB.jobs.pop(started[0][1], None)

    def test_home_shows_key_status_without_value(self):
        with patch.dict(os.environ, {"META_API_KEY": "sentinel-KEY-999"}):
            head, data = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        html = data.decode()
        self.assertIn("Meta API key:", html)
        self.assertIn(">configured<", html)
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

    def test_home_defaults_to_m1(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(WEB, "RUNS_DIR", Path(td)):
                _, data = request(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        html = data.decode()
        self.assertIn('<option value="m1" selected>', html)
        self.assertIn("Test pattern (synthetic, for pipeline check)", html)
        self.assertLess(html.find('value="m1"'), html.find('value="synthetic"'))
        self.assertIn("es.value='m1'", html)

    def test_synthetic_thumbs_badged(self):
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
        self.assertEqual(syn.decode().count("TEST PATTERN"), 2)  # card + timeline
        self.assertNotIn("TEST PATTERN", m1.decode())

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


if __name__ == "__main__":
    unittest.main()
