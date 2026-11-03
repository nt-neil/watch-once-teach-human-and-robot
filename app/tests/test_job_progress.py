"""Job progress backend: stage derivation, detail, lite validation, events."""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from cli import APP_DIR

sys.path.insert(0, str(APP_DIR / "web"))
import server as WEB  # noqa: E402

SHOWCASE = APP_DIR / "runs" / "web-20261103T083626Z"


def _manifest(duration_ms=14000.0, n=4):
    frames = [{"id": f"frame-{i + 1:06d}", "sample_time_ms": float(i * 1000),
               "file": f"frames/f-{i + 1:06d}.jpg"} for i in range(n)]
    return {"source": {"video_sha256": "ab" * 32, "video_name": "x.mp4",
                       "duration_ms": duration_ms, "provider": "mock", "mock": True},
            "source_duration_ms": duration_ms, "sample_interval_ms": 1000,
            "frames": frames}


def _concept():
    return {"object": "o", "action": "a", "target": "t", "pre": "p", "done": "d"}


def _ops(source, spans):
    return {"source": source,
            "steps": [{"id": f"step-{i + 1:03d}", "start_ms": a, "end_ms": b,
                       "concept": _concept(), "source_frame_refs": ["frame-000001"]}
                      for i, (a, b) in enumerate(spans)]}


class DeriveStageTests(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="stage-"))
        self.addCleanup(shutil.rmtree, self.td, True)

    def test_empty_is_checking(self):
        self.assertEqual(WEB.derive_stage_index(self.td, "mock", 0), 0)

    def test_jpg_is_extracting(self):
        (self.td / "frames").mkdir()
        (self.td / "frames" / "a.jpg").write_bytes(b"x")
        self.assertEqual(WEB.derive_stage_index(self.td, "mock", 0), 1)

    def test_manifest_is_sending(self):
        (self.td / "frames.json").write_text("{}")
        self.assertEqual(WEB.derive_stage_index(self.td, "mock", 0), 2)
        self.assertEqual(WEB.derive_stage_index(self.td, "meta", 0.5), 2)

    def test_meta_late_is_analyzing(self):
        (self.td / "frames.json").write_text("{}")
        self.assertEqual(WEB.derive_stage_index(self.td, "meta", 5.0), 3)

    def test_provider_result_is_validating(self):
        (self.td / "frames.json").write_text("{}")
        (self.td / "provider-result.json").write_text("{}")
        self.assertEqual(WEB.derive_stage_index(self.td, "meta", 30), 4)

    def test_operations_is_writing_manual(self):
        (self.td / "frames.json").write_text("{}")
        (self.td / "provider-result.json").write_text("{}")
        (self.td / "operations.json").write_text("{}")
        self.assertEqual(WEB.derive_stage_index(self.td, "meta", 30), 5)


class JpegSizeTests(unittest.TestCase):
    def test_real_frame(self):
        p = SHOWCASE / "frames" / "meta-frame-000001.jpg"
        if not p.is_file():
            self.skipTest("showcase run not present")
        self.assertRegex(WEB.jpeg_size(p), r"^\d+×\d+$")

    def test_garbage_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.jpg"
            p.write_bytes(b"not a jpeg at all" * 10)
            self.assertEqual(WEB.jpeg_size(p), "")
            self.assertEqual(WEB.jpeg_size(Path(td) / "nope.jpg"), "")


class ScrubNameTests(unittest.TestCase):
    def test_basename_only(self):
        self.assertEqual(WEB.scrub_video_name("/tmp/a/b.mp4"), "b.mp4")
        self.assertEqual(WEB.scrub_video_name("..\\..\\x.mp4"), ".._.._x.mp4")
        self.assertLessEqual(len(WEB.scrub_video_name("y" * 200)), 80)


class LiteValidationTests(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="liteval-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        (self.td / "frames").mkdir()
        (self.td / "frames" / "f-000001.jpg").write_bytes(b"x")

    def _write(self, manifest, ops):
        (self.td / "frames.json").write_text(json.dumps(manifest))
        (self.td / "operations.json").write_text(json.dumps(ops))

    def test_showcase_all_pass(self):
        if not (SHOWCASE / "operations.json").is_file():
            self.skipTest("showcase run not present")
        checks, n = WEB.lite_validation_checks(SHOWCASE)
        self.assertTrue(checks)
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(n, 2)

    def test_overlap_fails_time_order_only(self):
        m = _manifest()
        ops = _ops(m["source"], [(0, 3000), (2000, 14000)])
        ops["steps"][1]["source_frame_refs"] = ["frame-000003"]
        (self.td / "frames" / "f-000003.jpg").write_bytes(b"x")
        self._write(m, ops)
        checks, n = WEB.lite_validation_checks(self.td)
        self.assertEqual(n, 2)
        self.assertFalse(checks["time order"])
        self.assertTrue(checks["schema"])
        self.assertTrue(checks["frame refs"])

    def test_malformed_concept_all_fail(self):
        m = _manifest()
        ops = _ops(m["source"], [(0, 7000)])
        ops["steps"][0]["concept"] = "not-a-dict"
        self._write(m, ops)
        checks, _ = WEB.lite_validation_checks(self.td)
        self.assertTrue(checks)
        self.assertFalse(any(checks.values()))

    def test_missing_files_empty(self):
        checks, n = WEB.lite_validation_checks(self.td)
        self.assertEqual((checks, n), ({}, 0))


class JobDetailTests(unittest.TestCase):
    def test_showcase_detail(self):
        if not (SHOWCASE / "operations.json").is_file():
            self.skipTest("showcase run not present")
        d = WEB.job_detail(SHOWCASE)
        self.assertEqual(d["duration_s"], 14.0)
        self.assertEqual(len(d["sha12"]), 12)
        self.assertEqual(d["sample_total"], 14)
        self.assertEqual(d["sample_times_s"][:3], [0.0, 1.0, 2.0])
        self.assertRegex(d.get("resolution", ""), r"^\d+×\d+$")
        self.assertEqual(d["steps"], 2)
        self.assertTrue(all(d["validation"].values()))

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            d = WEB.job_detail(Path(td))
        self.assertEqual(d["frame_count"], 0)
        self.assertNotIn("steps", d)


class JobPageTests(unittest.TestCase):
    def test_job_page_renders_7_stages(self):
        import socket
        import threading
        import types
        DUMMY = types.SimpleNamespace(server_version="T", sys_version="")

        def request(raw):
            a, b = socket.socketpair()
            t = threading.Thread(target=lambda: WEB.Handler(b, ("127.0.0.1", 0), DUMMY),
                                 daemon=True)
            t.start()
            try:
                a.sendall(raw)
                a.settimeout(15)
                out = b""
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
                return head.decode("latin1"), rest
            finally:
                try:
                    b.close()
                except OSError:
                    pass
                a.close()

        with WEB.jobs_lock:
            WEB.jobs["t-page"] = {"state": "running"}
        try:
            head, data = request(b"GET /job/t-page?slow=1 HTTP/1.1\r\nHost: x\r\n"
                                 b"Connection: close\r\n\r\n")
        finally:
            with WEB.jobs_lock:
                WEB.jobs.pop("t-page", None)
        self.assertIn("200", head.splitlines()[0])
        html = data.decode()
        for name in ("Checking video", "Extracting frames", "Sending to Muse Spark",
                     "Model analyzing", "Validating steps", "Writing manual"):
            self.assertIn(name, html)
        self.assertIn("Activity log", html)
        self.assertIn("Muse Spark is watching the video", html)
        self.assertIn("Try again", html)
        self.assertNotIn("dots", html)
        self.assertNotIn("slow", html)
        self.assertNotIn("mock", html.lower())


class EventTests(unittest.TestCase):
    def test_push_and_cap(self):
        with WEB.jobs_lock:
            WEB.jobs["t-ev"] = {"events": []}
        try:
            WEB._push_event("t-ev", 1.25, "Stage: X")
            with WEB.jobs_lock:
                evs = WEB.jobs["t-ev"]["events"]
            self.assertEqual(evs, [{"t": 1.2, "msg": "Stage: X"}])
            for i in range(150):
                WEB._push_event("t-ev", float(i), "spam")
            with WEB.jobs_lock:
                self.assertLessEqual(len(WEB.jobs["t-ev"]["events"]), 100)
        finally:
            with WEB.jobs_lock:
                WEB.jobs.pop("t-ev", None)


if __name__ == "__main__":
    unittest.main()
