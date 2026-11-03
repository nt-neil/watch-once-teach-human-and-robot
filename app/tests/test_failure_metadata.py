"""Regression for diagnostics lost when a successful HTTP response is incomplete."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from cli import APP_DIR, parser, run
from common import AppError, read_json, sha256


class FailureMetadataTest(unittest.TestCase):
    def test_incomplete_saves_safe_usage_and_id_without_exporting_task(self):
        with tempfile.TemporaryDirectory(dir=APP_DIR) as temporary:
            base = Path(temporary)
            video = base / "fake-unit.mp4"
            video.write_bytes(b"in-process-test-only")
            out = base / "output"
            source = {"video_sha256": sha256(video), "video_name": video.name, "duration_ms": 1000,
                      "provider": "meta", "mock": False, "frame_manifest": "frames.json"}
            manifest = {"source": source, "frames": []}
            incomplete = {
                "id": "resp_unit_test", "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 123, "output_tokens": 4000,
                          "output_tokens_details": {"reasoning_tokens": 3000}},
                "error": {"message": "sensitive-remote-message"},
                "headers": {"Authorization": "sensitive-remote-header"},
            }
            calls = []
            def handle(request):
                calls.append(request.url.path)
                if request.url.path == "/v1/files":
                    return httpx.Response(200, json={"id": "file-unit"})
                self.assertEqual(json.loads(request.content)["reasoning"], {"effort": "low"})
                return httpx.Response(200, json=incomplete)
            real_client = httpx.Client
            args = parser().parse_args([str(video), "--provider", "meta", "--confirm-paid-api",
                                       "--workspace-path", str(APP_DIR), "--out", str(out)])
            with patch("cli.extract", return_value=manifest), patch.dict(os.environ, {"META_API_KEY": "fake-unit-key"}), patch(
                "providers.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)
            ):
                with self.assertRaises(AppError):
                    run(args)
            self.assertEqual(calls, ["/v1/files", "/v1/responses"])
            failure = read_json(out / "failure.json")
            metadata = failure["provider_metadata"]
            self.assertEqual(metadata["response_id"], "resp_unit_test")
            self.assertEqual(metadata["response_status"], "incomplete")
            self.assertEqual(metadata["incomplete_details"], {"reason": "max_output_tokens"})
            self.assertEqual(metadata["usage"]["output_tokens"], 4000)
            self.assertEqual(metadata["upload_http_status"], 200)
            self.assertEqual(metadata["response_http_status"], 200)
            self.assertNotIn("sensitive-remote", json.dumps(failure))
            self.assertNotIn("fake-unit-key", json.dumps(failure))
            self.assertFalse((out / "navide-plan.json").exists())
            self.assertFalse((out / "manual.md").exists())


if __name__ == "__main__":
    unittest.main()
