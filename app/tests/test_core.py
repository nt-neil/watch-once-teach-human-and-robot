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
from providers import MetaProvider, parse_meta_response, remote_schema_for
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
        for bad in ({**probe, "streams": []}, {**probe, "format": {"duration": "nan"}},
                    {**probe, "format": {}}, {**probe, "streams": [{"codec_type": "audio"}]}):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                check_probe(bad)

    def test_probe_accepts_180s_and_rejects_beyond_in_plain_words(self):
        # 60s was our own cap, not the API's; UI/README promise ~3 minutes.
        for ok in ("61", "120", "180"):
            probe = {"streams": [{"codec_type": "video", "index": 0, "duration": ok}],
                     "format": {"duration": ok, "format_name": "mov,mp4"}}
            self.assertEqual(check_probe(probe)[1], int(float(ok) * 1000))
        probe = {"streams": [{"codec_type": "video", "index": 0, "duration": "181"}],
                 "format": {"duration": "181", "format_name": "mov,mp4"}}
        with self.assertRaises(AppError) as ctx:
            check_probe(probe)
        self.assertIn("3 minutes", str(ctx.exception))
        self.assertIn("trim", str(ctx.exception).lower())

    def test_keyframe_plan_scales_with_duration(self):
        from video import keyframe_plan
        self.assertEqual(keyframe_plan(30000), (3000, 50))
        self.assertEqual(keyframe_plan(60000), (3000, 50))
        iv, mx = keyframe_plan(120000)
        self.assertEqual(mx, 24)
        self.assertGreaterEqual(iv, 5000)
        iv, mx = keyframe_plan(180000)
        self.assertEqual(mx, 24)
        self.assertGreaterEqual(iv, 5000)

    def test_output_cap_grows_with_duration(self):
        from providers import output_cap_for
        self.assertEqual(output_cap_for(30000), 8000)
        self.assertEqual(output_cap_for(60000), 8000)
        self.assertEqual(output_cap_for(120000), 12000)
        self.assertEqual(output_cap_for(180000), 16000)

    def test_synth_120s_180s_mock_pass_and_181s_rejected(self):
        import shutil
        import subprocess
        from cli import parser, run
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg missing")
        for secs, ok in ((120, True), (180, True), (181, False)):
            clip = self.base / f"synth-{secs}.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", f"testsrc=size=96x72:rate=5:duration={secs}",
                            "-c:v", "libx264", "-preset", "ultrafast", str(clip)],
                           check=True, timeout=120)
            args = parser().parse_args([str(clip), "--workspace-path", str(APP_DIR),
                                        "--provider", "mock",
                                        "--out", str(self.base / f"o-{secs}")])
            if ok:
                run(args)
                self.assertTrue((self.base / f"o-{secs}" / "operations.json").is_file())
            else:
                with self.assertRaises(AppError) as ctx:
                    run(args)
                self.assertIn("3 minutes", str(ctx.exception))

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
            mutate(lambda x: x["steps"][0].pop("evidence")),
            mutate(lambda x: x["steps"][0].update(evidence="   ")),
            mutate(lambda x: x["steps"][0].update(evidence=7)),
        ]
        for index, bad in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(AppError):
                validate_operations(bad, self.schema, manifest, self.base)
        (self.base / "unit-0.jpg").unlink()
        with self.assertRaises(AppError):
            validate_operations(doc, self.schema, manifest, self.base)

    def test_frame_ref_tolerates_one_frame_at_step_boundary(self):
        manifest, doc = self.fixture(self.base)
        manifest["video_fps"] = "30/1"
        # R4 case: sample exactly on the exclusive end is accepted.
        doc["steps"][0].update(source_frame_refs=["frame-000002"])
        validate_operations(doc, self.schema, manifest, self.base)
        # R2 case: sample 1 ms before start is accepted.
        doc["steps"][1].update(start_ms=1001, source_frame_refs=["frame-000002"])
        validate_operations(doc, self.schema, manifest, self.base)
        # Far outside the step is still rejected.
        doc["steps"][1].update(source_frame_refs=["frame-000001"])
        with self.assertRaises(AppError):
            validate_operations(doc, self.schema, manifest, self.base)

    def test_frame_ref_tolerance_falls_back_without_fps(self):
        manifest, doc = self.fixture(self.base)
        self.assertNotIn("video_fps", manifest)
        doc["steps"][0].update(source_frame_refs=["frame-000002"])
        validate_operations(doc, self.schema, manifest, self.base)
        doc["steps"][1].update(source_frame_refs=["frame-000001"])
        with self.assertRaises(AppError):
            validate_operations(doc, self.schema, manifest, self.base)

    def test_repair_frame_refs_drops_out_of_range_and_marks_review(self):
        # Real case: runs/web-20261103T142454Z step-001 (3000-8000ms) cited
        # frame@2000ms; the whole paid run failed on one bad ref. Repair must
        # drop the ref, flag the step, and let validation pass.
        from validation import repair_frame_refs
        manifest, doc = self.fixture(self.base)
        manifest["video_fps"] = "30/1"
        doc["steps"][0].update(source_frame_refs=["frame-000001", "frame-000003"])
        with self.assertRaises(AppError):
            validate_operations(doc, self.schema, manifest, self.base)
        doc, repaired, _ = repair_frame_refs(doc, manifest)
        self.assertEqual(repaired, ["observe"])
        self.assertEqual(doc["steps"][0]["source_frame_refs"], ["frame-000001"])
        self.assertTrue(doc["steps"][0]["evidence"].startswith("needs_review"))
        validate_operations(doc, self.schema, manifest, self.base)

    def test_repair_frame_refs_keeps_closest_when_all_out_of_range(self):
        from validation import repair_frame_refs
        manifest, doc = self.fixture(self.base)
        manifest["video_fps"] = "30/1"
        doc["steps"][0].update(start_ms=2000, end_ms=3000,
                               source_frame_refs=["frame-000001", "frame-000002"])
        del doc["steps"][1]
        doc, repaired, _ = repair_frame_refs(doc, manifest)
        self.assertEqual(repaired, ["observe"])
        self.assertEqual(doc["steps"][0]["source_frame_refs"], ["frame-000002"])
        self.assertTrue(doc["steps"][0]["evidence"].startswith("needs_review"))
        validate_operations(doc, self.schema, manifest, self.base)

    def test_clamp_document_pulls_overshoot_into_range(self):
        from validation import clamp_document
        manifest, doc = self.fixture(self.base)
        doc["steps"][1].update(end_ms=5000)
        doc, clamped = clamp_document(doc, manifest)
        self.assertEqual(clamped, ["compare"])
        self.assertEqual(doc["steps"][1]["end_ms"], 3000)
        validate_operations(doc, self.schema, manifest, self.base)
        doc["steps"][0].update(start_ms=-50, end_ms=1000)
        doc, clamped = clamp_document(doc, manifest)
        self.assertEqual(doc["steps"][0]["start_ms"], 0)

    def test_apply_shot_candidates_rewrites_start(self):
        from validation import apply_shot_candidates
        _, doc = self.fixture(self.base)
        cands = [{"id": 0, "t_ms": 1400}, {"id": 1, "t_ms": 6333}]
        doc["steps"][0].update(start_ms=1000,
                               evidence="[trans:1] typing appears")
        doc["steps"][1].update(evidence="no marker here")
        doc, aligned = apply_shot_candidates(doc, cands)
        self.assertEqual(doc["steps"][0]["start_ms"], 6333)
        self.assertEqual(aligned, [doc["steps"][0]["id"]])
        # Unknown id flags the step instead of rewriting.
        doc["steps"][0].update(evidence="already aligned, no marker")
        doc["steps"][1].update(evidence="[trans:9] guess")
        doc, aligned = apply_shot_candidates(doc, cands)
        self.assertTrue(doc["steps"][1]["evidence"].startswith("needs_review"))
        self.assertEqual(aligned, [])

    def test_repair_keeps_frame_within_one_sample_interval(self):
        # Real case: S2 web-20261104T013132Z step 41000-54000 cited
        # frame-000041 @40.1s; the model reads frame-NN as second NN.
        # Within one sample interval of the boundary -> keep, no needs_review.
        from validation import repair_frame_refs
        manifest, doc = self.fixture(self.base)
        manifest["video_fps"] = "30/1"
        manifest["sample_interval_ms"] = 1000
        manifest["frames"] = [
            {"id": "frame-000041", "file": "x.jpg", "image_sha256": "c" * 64,
             "source_frame": 1203, "pts": 40100, "sample_time_ms": 40100}]
        doc["steps"] = [dict(doc["steps"][0], id="s41", start_ms=41000,
                             end_ms=54000, source_frame_refs=["frame-000041"],
                             evidence="saw it")]
        doc, repaired, _ = repair_frame_refs(doc, manifest)
        self.assertEqual(repaired, [])
        self.assertEqual(doc["steps"][0]["source_frame_refs"], ["frame-000041"])
        self.assertNotIn("needs_review", doc["steps"][0]["evidence"])
        # Truly far refs are still dropped and flagged.
        doc["steps"][0].update(start_ms=60000, end_ms=70000)
        doc, repaired, _ = repair_frame_refs(doc, manifest)
        self.assertEqual(repaired, ["s41"])
        self.assertTrue(doc["steps"][0]["evidence"].startswith("needs_review"))

    def test_validator_accepts_boundary_tolerance_and_never_kills_run_on_ref(self):
        # Real case: web-20261104T081028Z step-005 cited frame-000017 within
        # +/-1 sample interval; repair forgave it but strict validation killed
        # the whole run. 0.5s outside -> forgiven and passes; 3s outside ->
        # dropped and flagged; neither kills the run.
        from validation import repair_frame_refs
        manifest, doc = self.fixture(self.base)
        manifest["video_fps"] = "30/1"
        manifest["sample_interval_ms"] = 1000
        doc["steps"][1].update(start_ms=1500, end_ms=3000,
                               source_frame_refs=["frame-000002"])
        doc, repaired, forgiven = repair_frame_refs(doc, manifest)
        self.assertEqual(repaired, [])
        self.assertEqual(forgiven, {"compare": ["frame-000002"]})
        validate_operations(doc, self.schema, manifest, self.base, forgiven=forgiven)
        doc["steps"][1].update(start_ms=2500, end_ms=3000,
                               source_frame_refs=["frame-000003", "frame-000001"],
                               evidence="plain evidence")
        doc, repaired, forgiven = repair_frame_refs(doc, manifest)
        self.assertEqual(doc["steps"][1]["source_frame_refs"], ["frame-000003"])
        self.assertTrue(doc["steps"][1]["evidence"].startswith("needs_review"))
        validate_operations(doc, self.schema, manifest, self.base, forgiven=forgiven)

    def test_video_kind_gate_empty_steps_rules(self):
        manifest, doc = self.fixture(self.base)
        # non_procedural must have empty steps.
        nonp = copy.deepcopy(doc)
        nonp.update(video_kind="non_procedural", steps=[])
        validate_operations(nonp, self.schema, manifest, self.base)
        nonp["steps"] = doc["steps"]
        with self.assertRaises(AppError):
            validate_operations(nonp, self.schema, manifest, self.base)
        # procedural (default) must have at least one step.
        empty = copy.deepcopy(doc)
        empty.update(steps=[])
        with self.assertRaises(AppError):
            validate_operations(empty, self.schema, manifest, self.base)
        # uncertain may have empty steps (wrapper shows a warning).
        unc = copy.deepcopy(doc)
        unc.update(video_kind="uncertain", steps=[])
        validate_operations(unc, self.schema, manifest, self.base)

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
            self.assertEqual(request_body["text"]["format"]["schema"],
                             remote_schema_for(self.schema, "v6"))
            self.assertIs(request_body["text"]["format"]["strict"], True)
            self.assertEqual(request_body["input"][0]["content"][1], {"type": "input_file", "file_id": "file-test"})
            self.assertEqual(request_body["max_output_tokens"], 8000)
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

    def test_keyframes_mixed_send_and_400_fallback(self):
        manifest, doc = self.fixture(self.base)
        video = self.base / "unit.mp4"
        video.write_bytes(b"original-mp4-with-audio-unit-bytes")
        manifest["source"].update(provider="meta", mock=False, video_sha256=sha256(video))
        kf_path = self.base / "kf-000001.jpg"
        kf_path.write_bytes(b"explicit-keyframe-bytes")
        manifest["keyframes"] = [{"id": "kf-000001", "file": "kf-000001.jpg",
                                  "image_sha256": sha256(kf_path), "sample_time_ms": 2000,
                                  "source_frame": 60, "pts": 60}]
        doc["source"] = copy.deepcopy(manifest["source"])
        body = {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(doc)}]}],
            "usage": {"input_tokens": 20, "output_tokens": 50}}
        seen = {}

        def handler(request):
            if request.url.path == "/v1/files":
                return httpx.Response(200, json={"id": "file-%d" % len(seen.setdefault("up", []))})
            rb = json.loads(request.content)
            seen.setdefault("responses", []).append(rb)
            if len(seen["responses"]) == 1:
                types = [c["type"] for c in rb["input"][0]["content"]]
                self.assertIn("input_file", types)
                self.assertIn("input_image", types)
                self.assertTrue(any("kf-000001" in c.get("text", "") for c in rb["input"][0]["content"]
                                    if c["type"] == "input_text"))
                return httpx.Response(400, json={"error": "mixed"})
            types = [c["type"] for c in rb["input"][0]["content"]]
            self.assertNotIn("input_file", types)
            self.assertIn("input_image", types)
            return httpx.Response(200, json=body)

        real_client = httpx.Client
        with patch.dict(os.environ, {"META_API_KEY": "unit-test-placeholder"}), \
                patch("providers.httpx.Client",
                      side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
            prov = MetaProvider(confirm_paid=True, keyframe_dir=self.base, keyframes=True)
            result, meta = prov.generate(video, manifest, self.schema)
        self.assertEqual(result["steps"], doc["steps"])
        self.assertEqual(len(seen["responses"]), 2)

    def test_keyframes_default_on_and_mock_skips(self):
        args = parser().parse_args(["unit.mp4", "--workspace-path", str(self.base),
                                    "--out", str(self.base / "o")])
        self.assertTrue(args.keyframes)
        args = parser().parse_args(["unit.mp4", "--workspace-path", str(self.base),
                                    "--out", str(self.base / "o"), "--no-keyframes"])
        self.assertFalse(args.keyframes)
        manifest, _ = self.fixture(self.base)
        args = parser().parse_args(["unit.mp4", "--workspace-path", str(self.base),
                                    "--out", str(self.base / "o"), "--keyframes"])
        with patch("cli.extract", side_effect=lambda *a: self.fixture(a[1])[0]), \
                patch("cli.extract_keyframes") as mock_kf:
            run(args)
            mock_kf.assert_not_called()

    def test_keyframes_rejected_without_meta_or_files(self):
        manifest, _ = self.fixture(self.base)
        prov = MetaProvider(confirm_paid=True, keyframes=True)
        with self.assertRaises(AppError) as cm:
            prov._upload_keyframes(None, {}, manifest, {})
        self.assertIn("none extracted", str(cm.exception))

    def test_diff_keyframes_catch_scene_cuts_between_uniform_samples(self):
        import shutil
        import subprocess
        from video import extract_keyframes
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not present")
        clip = self.base / "cut.mp4"
        # 6s clip: red 0-2s, blue 2-4s, red 4-6s. Uniform 3s sampling hits
        # ~0/3s only; diff mode must also catch the cuts at ~2s and ~4s.
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               "color=red:size=160x120:rate=10:duration=2", "-f", "lavfi", "-i",
               "color=blue:size=160x120:rate=10:duration=2", "-f", "lavfi", "-i",
               "color=red:size=160x120:rate=10:duration=2", "-filter_complex",
               "[0][1][2]concat=n=3:v=1:a=0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               str(clip)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stderr[:200])
        manifest = {"timeline_origin_ms": 0, "video_stream_index": 0,
                    "source": {"duration_ms": 6000}}
        (self.base / "u").mkdir()
        (self.base / "d").mkdir()
        uni = extract_keyframes(clip, self.base / "u", manifest, interval_ms=3000)
        diff = extract_keyframes(clip, self.base / "d", manifest, interval_ms=3000, mode="diff")
        times = [k["sample_time_ms"] for k in diff]
        self.assertTrue(any(1500 < t < 2500 for t in times), times)
        self.assertTrue(any(3500 < t < 4500 for t in times), times)
        self.assertGreater(len(diff), len(uni))

    def test_describe_sends_images_only(self):
        manifest, _ = self.fixture(self.base)
        kf_path = self.base / "kf-000001.jpg"
        kf_path.write_bytes(b"explicit-keyframe-bytes")
        manifest["keyframes"] = [{"id": "kf-000001", "file": "kf-000001.jpg",
                                  "image_sha256": sha256(kf_path), "sample_time_ms": 2000,
                                  "source_frame": 60, "pts": 60}]
        table = {"schema_version": "1.0", "source": manifest["source"],
                 "frames": [{"kf_id": "kf-000001", "t_s": 2.0, "text_seen": [], "state": "s"}]}
        body = {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(table)}]}],
            "usage": {"input_tokens": 20, "output_tokens": 50}}
        seen = {}

        def handler(request):
            if request.url.path == "/v1/files":
                return httpx.Response(200, json={"id": "file-kf"})
            rb = json.loads(request.content)
            seen["body"] = rb
            return httpx.Response(200, json=body)

        real_client = httpx.Client
        with patch.dict(os.environ, {"META_API_KEY": "unit-test-placeholder"}), \
                patch("providers.httpx.Client",
                      side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
            prov = MetaProvider(confirm_paid=True, keyframe_dir=self.base, keyframes=True)
            result, meta = prov.describe(manifest, read_json(APP_DIR / "desc.schema.json"))
        types = [c["type"] for c in seen["body"]["input"][0]["content"]]
        self.assertNotIn("input_file", types)
        self.assertIn("input_image", types)
        self.assertEqual(seen["body"]["text"]["format"]["name"], "frame_descriptions")
        self.assertEqual(result["frames"][0]["kf_id"], "kf-000001")
        self.assertEqual(meta["pass"], "describe")

    def test_two_stage_requires_v8(self):
        args = parser().parse_args(["unit.mp4", "--workspace-path", str(self.base),
                                    "--out", str(self.base / "o"), "--provider", "meta",
                                    "--confirm-paid-api", "--two-stage"])
        with patch("cli.extract", side_effect=lambda *a: self.fixture(a[1])[0]), \
                patch("cli.extract_keyframes", return_value=[]):
            with self.assertRaises(AppError):
                run(args)

    def test_meta_http_errors_plain_and_no_retry(self):
        manifest, _ = self.fixture(self.base)
        video = self.base / "unit.mp4"
        video.write_bytes(b"original-mp4-with-audio-unit-bytes")
        manifest["source"].update(provider="meta", mock=False, video_sha256=sha256(video))
        real_client = httpx.Client

        def run_with(handler):
            with patch.dict(os.environ, {"META_API_KEY": "unit-test-placeholder"}), \
                    patch("providers.httpx.Client",
                          side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
                return MetaProvider(confirm_paid=True).generate(video, manifest, self.schema)

        def h401(request):
            return httpx.Response(401, json={"error": "bad key"})
        with self.assertRaises(AppError) as cm:
            run_with(h401)
        self.assertIn("401", str(cm.exception))

        def htimeout(request):
            raise httpx.ConnectTimeout("slow")
        with self.assertRaises(AppError) as cm:
            run_with(htimeout)
        self.assertIn("timed out", str(cm.exception))

        def hdown(request):
            raise httpx.ConnectError("down")
        with self.assertRaises(AppError) as cm:
            run_with(hdown)
        self.assertIn("Meta request failed", str(cm.exception))

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

    def test_plan_payload_follows_ui_language(self):
        from navide import plan_payload
        manifest, doc = self.fixture(self.base)
        en = plan_payload(doc, manifest, str(APP_DIR))
        self.assertIn("Video operation concepts", en["name"])
        zh = plan_payload(doc, manifest, str(APP_DIR), "zh-Hant")
        self.assertIn("影片操作概念", zh["name"])
        for lang in ("en", "zh-Hant", "zh-Hans", "ja", "es"):
            p = plan_payload(doc, manifest, str(APP_DIR), lang)
            self.assertTrue(p["name"] and p["overview"])
            self.assertIn("pending", p["todos"][0]["status"])


if __name__ == "__main__":
    unittest.main()
