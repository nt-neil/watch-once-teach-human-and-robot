import asyncio
from contextlib import asynccontextmanager, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from cli import APP_DIR, parser, run
from common import AppError, read_json, sha256, write_json
from navide import plan_payload, submit_plan
from providers import MetaProvider, parse_meta_response
from validation import validate_operations
from video import check_probe, parse_samples


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP_DIR)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.schema = read_json(APP_DIR / "operation.schema.json")

    def fixture(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        source = {"video_sha256": "a" * 64, "video_name": "unit.mp4", "duration_ms": 3000,
                  "provider": "mock", "mock": True, "frame_manifest": "frames.json"}
        frames = []
        for i in range(3):
            path = directory / f"unit-{i}.jpg"
            path.write_bytes(b"explicit-unit-test-image-bytes")
            frames.append({"id": f"frame-{i + 1:06d}", "file": path.name, "image_sha256": sha256(path),
                           "source_frame": i * 10, "pts": i * 1000, "sample_time_ms": i * 1000})
        manifest = {"source": source, "frames": frames, "time_base": "1/1000", "timeline_origin_ms": 0}
        doc = {"schema_version": "1.0", "source": copy.deepcopy(source),
               "steps": read_json(APP_DIR / "fixtures/synthetic-response.json")["steps"]}
        return manifest, doc

    def test_probe_rejects_missing_video_invalid_and_over_limit(self):
        probe = {"streams": [{"codec_type": "video", "index": 0, "duration": "3", "time_base": "1/1000"}],
                 "format": {"duration": "3", "format_name": "mov,mp4"}}
        self.assertEqual(check_probe(probe)[1], 3000)
        for bad in ({**probe, "streams": []}, {**probe, "format": {"duration": "61"}},
                    {**probe, "format": {"duration": "nan"}}, {**probe, "format": {}},
                    {**probe, "streams": [{"codec_type": "audio"}]}):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                check_probe(bad)

    def test_source_frame_comes_from_decoder_n_and_pts(self):
        log = """[showinfo@source @ 0x1] n: 0 pts: 50 pts_time:0.05 other: 1
[showinfo@sample @ 0x2] n: 0 pts: 50 pts_time:0.05
irrelevant log line
[showinfo@source @ 0x1] n: 30 pts: 1050 pts_time:1.05
[showinfo@sample @ 0x2] n: 1 pts: 1050 pts_time:1.05
"""
        origin, frames = parse_samples(log)
        self.assertEqual(origin, 50)
        self.assertEqual([f["source_frame"] for f in frames], [0, 30])
        self.assertEqual(frames[1]["sample_time_ms"], 1000)
        with self.assertRaises(AppError):
            parse_samples(log.replace("n: 1 pts: 1050", "n: 1 pts: 1051"))
        with self.assertRaises(AppError):
            parse_samples("no decoded frames")

    def test_schema_and_semantics_reject_invalid_steps_and_refs(self):
        manifest, doc = self.fixture(self.base)
        validate_operations(doc, self.schema, manifest, self.base)
        def mutate(fn):
            value = copy.deepcopy(doc)
            fn(value)
            return value
        invalid = [
            mutate(lambda x: x.update(steps=[])),
            mutate(lambda x: x["steps"][0]["concept"].update(action=7)),
            mutate(lambda x: x["steps"][0]["concept"].update(action="   ")),
            mutate(lambda x: x["steps"][0]["concept"].update(role="old-appendix")),
            mutate(lambda x: x["steps"][0].update(end_ms=3100)),
            mutate(lambda x: x["steps"][0].update(start_ms=True)),
            mutate(lambda x: x["steps"][0].update(start_ms=1000)),
            mutate(lambda x: x["steps"].reverse()),
            mutate(lambda x: x["steps"][1].update(id="observe")),
            mutate(lambda x: x["steps"][0].update(source_frame_refs=["frame-000099"])),
            mutate(lambda x: x["steps"][0].update(source_frame_refs=["frame-000003"])),
            mutate(lambda x: x["source"].update(mock=False)),
        ]
        for index, bad in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(AppError):
                validate_operations(bad, self.schema, manifest, self.base)
        (self.base / "unit-0.jpg").unlink()
        with self.assertRaises(AppError):
            validate_operations(doc, self.schema, manifest, self.base)

    def test_mock_pipeline_marks_outputs_and_invalid_output_creates_no_task(self):
        def fake_extract(video, out, provider, interval):
            return self.fixture(out)[0]
        for valid in (True, False):
            out = self.base / ("valid" if valid else "invalid")
            fixture = self.base / f"response-{valid}.json"
            write_json(fixture, read_json(APP_DIR / "fixtures/synthetic-response.json") if valid else {"steps": []})
            args = parser().parse_args(["unit.mp4", "--workspace-path", str(APP_DIR), "--out", str(out), "--mock-response", str(fixture)])
            with patch("cli.extract", side_effect=fake_extract), redirect_stdout(io.StringIO()):
                if valid:
                    run(args)
                else:
                    with self.assertRaises(AppError):
                        run(args)
            self.assertEqual((out / "navide-plan.json").exists(), valid)
            if valid:
                payload = read_json(out / "navide-plan.json")
                self.assertIn("MOCK", payload["name"])
                self.assertTrue(all(t["status"] == "pending" and "MOCK" in t["content"] for t in payload["todos"]))
                for name in ("operations.json", "frames.json", "provider-result.json", "submission.json"):
                    self.assertIs(read_json(out / name)["source"]["mock"], True)
                self.assertEqual(read_json(out / "submission.json")["status"], "exported_not_sent")

    def test_meta_original_upload_strict_contract_and_failure_states(self):
        manifest, doc = self.fixture(self.base)
        video = self.base / "unit.mp4"
        video.write_bytes(b"original-mp4-with-audio-unit-bytes")
        manifest["source"].update(provider="meta", mock=False, video_sha256=sha256(video))
        doc["source"] = copy.deepcopy(manifest["source"])
        body = {"status": "completed", "output": [{"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(doc)}]}],
                "usage": {"input_tokens": 20, "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 30}}}
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == "/v1/files":
                self.assertIn(video.read_bytes(), request.content)
                self.assertIn(b'user_data', request.content)
                return httpx.Response(200, json={"id": "file-test"})
            request_body = json.loads(request.content)
            self.assertEqual(request_body["text"]["format"]["schema"], self.schema)
            self.assertIs(request_body["text"]["format"]["strict"], True)
            self.assertEqual(request_body["input"][0]["content"][1], {"type": "input_file", "file_id": "file-test"})
            self.assertEqual(request_body["max_output_tokens"], 4000)
            self.assertIs(request_body["stream"], False)
            return httpx.Response(200, json=body)
        real_client = httpx.Client
        with patch.dict(os.environ, {"META_API_KEY": "unit-test-placeholder"}), patch("providers.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
            result, meta = MetaProvider(confirm_paid=True).generate(video, manifest, self.schema)
        self.assertEqual(len(requests), 2)
        self.assertEqual(result, doc)
        self.assertEqual(meta["usage"]["output_tokens"], 50)  # Not 80: reasoning is included.
        validate_operations(result, self.schema, manifest, self.base)
        for bad in ({**body, "status": "incomplete"}, {**body, "status": "failed"},
                    {**body, "output": []}, {**body, "refusal": "no"},
                    {**body, "output": [{"content": [{"type": "refusal", "refusal": "no"}]}]}):
            with self.subTest(status=bad.get("status")), self.assertRaises(AppError):
                parse_meta_response(bad)
        with self.assertRaises(AppError):
            MetaProvider()

    def test_mcp_optional_explicit_url_initialize_and_single_call(self):
        manifest, doc = self.fixture(self.base)
        payload = plan_payload(doc, manifest, str(APP_DIR))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(asyncio.run(submit_plan(payload))["status"], "exported_not_sent")
        calls = []
        class Session:
            def __init__(self, *args): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def initialize(self): calls.append("initialize")
            async def call_tool(self, name, args):
                calls.append(name)
                self_test.assertEqual(args, payload)
                if fail[0]:
                    raise TimeoutError("hidden transport details")
                return SimpleNamespace(isError=False, structuredContent={"rel_path": ".agent-team/plans/mock.html", "name": payload["name"], "stage": "draft"})
        @asynccontextmanager
        async def transport(url, **kwargs):
            self.assertEqual(url, "http://unit.invalid/mcp?secret=not-real")
            yield (object(), object(), lambda: None)
        self_test, fail = self, [False]
        mcp, client, streamable = ModuleType("mcp"), ModuleType("mcp.client"), ModuleType("mcp.client.streamable_http")
        mcp.ClientSession = Session
        streamable.streamablehttp_client = transport
        with patch.dict("sys.modules", {"mcp": mcp, "mcp.client": client, "mcp.client.streamable_http": streamable}), patch.dict(os.environ, {"NAVIDE_MCP_URL": "http://unit.invalid/mcp?secret=not-real"}):
            self.assertEqual(asyncio.run(submit_plan(payload))["status"], "submitted")
            self.assertEqual(calls, ["initialize", "plan_create"])
            fail[0] = True
            calls.clear()
            with self.assertRaises(AppError) as error:
                asyncio.run(submit_plan(payload))
            self.assertEqual(calls, ["initialize", "plan_create"])
            self.assertNotIn("hidden transport", str(error.exception))
            self.assertNotIn("secret=", str(error.exception))
        with self.assertRaises(AppError):
            plan_payload(doc, manifest, "relative/workspace")


if __name__ == "__main__":
    unittest.main()
