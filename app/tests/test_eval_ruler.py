"""Ruler tests: symmetric point matching (A1), true-majority vote (A2/A3),
word-boundary keywords (A6), proxy opt-in (E2). No paid calls."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from eval import gt_tolerance_ms, match_events  # noqa: E402
from vote import merge_runs  # noqa: E402


def step(sid, start, end, action="do x"):
    return {"id": sid, "start_ms": start, "end_ms": end,
            "concept": {"object": "o", "action": action, "target": "t",
                        "pre": "p", "done": "d"},
            "source_frame_refs": ["frame-000001"], "evidence": "saw it"}


class RulerTests(unittest.TestCase):
    def test_tolerance_reads_field_then_method_text_then_default(self):
        # Synthetic only; heldout2 GTs are never opened during tuning.
        self.assertEqual(gt_tolerance_ms({"tolerance_ms": 2500}), 2500)
        self.assertEqual(gt_tolerance_ms({"tolerance_s": 2.5}), 2500)
        self.assertEqual(gt_tolerance_ms({"tolerance": 1.5}), 1500)
        self.assertEqual(
            gt_tolerance_ms({"annotation_method": "Eyeballed; tolerance ±2.0s for scoring"}), 2000)
        self.assertEqual(gt_tolerance_ms({}), 1000)

    def test_a2_style_pass_rule_range(self):
        # Synthetic mirror of the A2 contract: 0 steps + uncertain/non_proc,
        # or procedural 1-3 steps PASS; 5+ steps FAIL.
        from eval import apply_pass_rule
        rule = {"type": "kind_or_max_steps",
                "accept_kinds_with_zero_steps": ["uncertain", "non_procedural"],
                "procedural_max_steps": 3, "fail_if_steps_ge": 5}
        self.assertEqual(apply_pass_rule(rule, "uncertain", 0), "PASS")
        self.assertEqual(apply_pass_rule(rule, "non_procedural", 0), "PASS")
        self.assertEqual(apply_pass_rule(rule, "procedural", 1), "PASS")
        self.assertEqual(apply_pass_rule(rule, "procedural", 3), "PASS")
        self.assertEqual(apply_pass_rule(rule, "procedural", 5), "FAIL")
        self.assertEqual(apply_pass_rule(rule, "procedural", 0), "FAIL")

    def test_point_gt_symmetric_start_rule(self):
        gt = [{"id": "GT1", "start_ms": 1366, "end_ms": 1400}]
        # Step starting 634ms late hits (old ruler: miss for lack of overlap).
        hits, _, _ = match_events([step("s1", 2000, 8000)], gt, 1000)
        self.assertIn("GT1", hits)
        self.assertEqual(hits["GT1"]["err_ms"], 634)
        # Symmetric: 634ms early also hits.
        gt2 = [{"id": "GT1", "start_ms": 2634, "end_ms": 2668}]
        hits, _, _ = match_events([step("s1", 2000, 8000)], gt2, 1000)
        self.assertIn("GT1", hits)
        # Beyond tolerance still misses.
        hits, _, _ = match_events([step("s1", 0, 1000)], gt, 1000)
        self.assertNotIn("GT1", hits)

    def test_interval_gt_still_requires_overlap(self):
        gt = [{"id": "GT1", "start_ms": 3000, "end_ms": 9000}]
        hits, _, _ = match_events([step("s1", 9500, 12000)], gt, 1000)
        self.assertNotIn("GT1", hits)
        hits, _, _ = match_events([step("s1", 3500, 9000)], gt, 1000)
        self.assertIn("GT1", hits)

    def _votedoc(self, steps):
        return {"schema_version": "1.0", "video_kind": "procedural",
                "source": {}, "steps": steps}

    def test_vote_keeps_strict_majority_only(self):
        docs = [self._votedoc([step("a", 0, 3000), step("b", 9000, 12000)]),
                self._votedoc([step("a", 0, 3000)]),
                self._votedoc([step("a", 100, 3000)])]
        with tempfile.TemporaryDirectory() as td:
            dirs = []
            for i, d in enumerate(docs):
                p = Path(td) / f"r{i}"
                p.mkdir()
                (p / "operations.json").write_text(json.dumps(d))
                dirs.append(str(p))
            base, stats = merge_runs(dirs)
        self.assertEqual(stats["n_runs"], 3)
        self.assertEqual(len(base["steps"]), 1)  # 3/3 cluster kept
        self.assertEqual(len(base["proposal_steps"]), 1)  # 1/3 cluster proposed
        self.assertIn("1/3", base["proposal_steps"][0]["evidence"])

    def test_vote_five_runs_need_three(self):
        docs = [self._votedoc([step("a", 0, 3000)]) for _ in range(2)]
        docs += [self._votedoc([step("a", 5000, 8000)]) for _ in range(3)]
        with tempfile.TemporaryDirectory() as td:
            dirs = []
            for i, d in enumerate(docs):
                p = Path(td) / f"r{i}"
                p.mkdir()
                (p / "operations.json").write_text(json.dumps(d))
                dirs.append(str(p))
            base, stats = merge_runs(dirs)
        self.assertEqual(stats["n_runs"], 5)
        self.assertEqual(len(base["steps"]), 1)  # only the 3/5 cluster
        self.assertEqual(base["steps"][0]["start_ms"], 5000)
        self.assertEqual(len(base["proposal_steps"]), 1)  # 2/5 is a proposal

    def test_keyword_ignores_filename_and_substrings(self):
        import eval as eval_mod
        ops = {"source": {"video_name": "wok-dev.mp4"},
               "steps": [step("s1", 0, 1000)]}
        ops["steps"][0]["evidence"] = "wait a minute then block the pan"
        text = json.dumps({k: v for k, v in ops.items() if k != "source"},
                          ensure_ascii=False).lower()
        import re
        hit = lambda w: (w in text) if re.search(r"[\u4e00-\u9fff]", w) \
            else re.search(r"\b" + re.escape(w) + r"s?\b", text) is not None
        self.assertFalse(hit("wok"))  # filename excluded
        self.assertFalse(hit("nut"))  # "minute" is not "nut"
        self.assertFalse(hit("locking"))  # "blocking" is not "locking"

    def test_temperature_passed_to_api_only_when_set(self):
        import httpx
        from providers import MetaProvider
        bodies = []

        def handler(request):
            if request.url.path == "/v1/files":
                return httpx.Response(200, json={"id": "file-test"})
            bodies.append(json.loads(request.content))
            doc = {"schema_version": "1.0", "source": {},
                   "steps": [{"id": "s1", "start_ms": 0, "end_ms": 1000,
                              "concept": {"object": "o", "action": "a", "target": "t",
                                          "pre": "p", "done": "d"},
                              "source_frame_refs": ["f1"], "evidence": "e"}]}
            return httpx.Response(200, json={
                "status": "completed",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps(doc)}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1}})
        real_client = httpx.Client
        from cli import APP_DIR as _APP
        from common import read_json as _read_json
        manifest = {"source": {"video_sha256": "x", "duration_ms": 1000},
                    "frames": [{"id": "f1", "sample_time_ms": 0,
                                "source_frame": 0, "pts": 0}],
                    "timeline_origin_ms": 0}
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            video = Path(td) / "v.mp4"
            video.write_bytes(b"fake-video-bytes")
            manifest["source"]["video_sha256"] = \
                __import__("hashlib").sha256(b"fake-video-bytes").hexdigest()
            schema = _read_json(_APP / "operation.schema.json")
            mk = lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
            with patch.dict(os.environ, {"META_API_KEY": "unit-test-placeholder"}), \
                    patch("providers.httpx.Client", side_effect=mk):
                MetaProvider(confirm_paid=True).generate(video, manifest, schema)
                MetaProvider(confirm_paid=True, temperature=0).generate(video, manifest, schema)
        self.assertNotIn("temperature", bodies[0])
        self.assertEqual(bodies[1]["temperature"], 0)

    def test_proxy_trust_defaults_off(self):
        from providers import trust_proxy_env
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_TRUST_PROXY", None)
            self.assertFalse(trust_proxy_env())
        with patch.dict(os.environ, {"META_TRUST_PROXY": "1"}):
            self.assertTrue(trust_proxy_env())
        with patch.dict(os.environ, {"META_TRUST_PROXY": "yes"}):
            self.assertFalse(trust_proxy_env())


if __name__ == "__main__":
    unittest.main()
