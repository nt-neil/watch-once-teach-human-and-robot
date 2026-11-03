"""Prompt v2 + task-hint contract (work order 12, no paid calls)."""
import unittest

from cli import manual, parser
from common import AppError
from providers import MetaProvider, build_prompt, sanitize_hint

MANIFEST = {
    "source": {"video_sha256": "a" * 64, "video_name": "unit.mp4",
               "duration_ms": 3000, "provider": "meta", "mock": False,
               "frame_manifest": "frames.json"},
    "frames": [{"id": "frame-1", "file": "unit-0.jpg", "image_sha256": "b" * 64,
                "source_frame": 0, "pts": 0, "sample_time_ms": 0}],
    "time_base": "1/1000",
}


class PromptV2Tests(unittest.TestCase):
    def test_v1_keeps_original_rules_only(self):
        p = build_prompt("v1", MANIFEST)
        self.assertIn("group continuous motions into meaningful steps", p)
        self.assertNotIn("gripper opens or closes", p)
        self.assertNotIn("in evidence naming what is seen", p)

    def test_v2_has_no_reference_answer_words(self):
        p = build_prompt("v2", MANIFEST)
        low = p.lower()
        for word in ("washer", "shaft", "0001_task", "aist"):
            self.assertNotIn(word, low)
        self.assertNotIn("M1", p)
        # v1 keeps its original wording untouched.
        self.assertIn("do not assume a washer is a nut", build_prompt("v1", MANIFEST))

    def test_v2_adds_cut_frame_evidence_done_rules(self):
        p = build_prompt("v2", MANIFEST)
        self.assertIn("gripper opens or closes", p)
        self.assertIn("one grasp and one place each count as one step", p)
        self.assertIn("cite the frame IDs", p)
        self.assertIn("in evidence naming what is seen and at which cited frames", p)
        self.assertIn("never write tightening, locking, or securing", p)
        self.assertIn("say unverified when completion is not shown", p)
        self.assertIn("Write done as only the end state visible on screen", p)

    def test_hint_appended_naming_help_only_and_capped(self):
        p = build_prompt("v2", MANIFEST, "  washers   on a shaft  ")
        self.assertIn("naming help only, verify against the video: washers on a shaft.", p)
        self.assertNotIn("washers   on", p)
        long_hint = "w " * 200
        self.assertLessEqual(len(sanitize_hint(long_hint)), 200)
        self.assertNotIn("task hint", build_prompt("v2", MANIFEST))

    def test_bad_version_rejected(self):
        with self.assertRaises(AppError):
            build_prompt("v3", MANIFEST)
        with self.assertRaises(AppError):
            MetaProvider("muse-spark-1.2", True, "v3")

    def test_meta_provider_defaults_v2_no_hint(self):
        m = MetaProvider("muse-spark-1.2", True)
        self.assertEqual(m.prompt_version, "v2")
        self.assertEqual(m.task_hint, "")

    def test_cli_prompt_args_default_and_parse(self):
        args = parser().parse_args(["v.mp4", "--workspace-path", "/tmp/ws"])
        self.assertEqual(args.prompt_version, "v2")
        self.assertEqual(args.task_hint, "")
        args = parser().parse_args(["v.mp4", "--workspace-path", "/tmp/ws",
                                    "--prompt-version", "v1", "--task-hint", "washers"])
        self.assertEqual(args.prompt_version, "v1")
        self.assertEqual(args.task_hint, "washers")

    def test_manual_includes_evidence_line(self):
        doc = {"source": dict(MANIFEST["source"]),
               "steps": [{"id": "step-01", "start_ms": 0, "end_ms": 1000,
                          "concept": {"object": "o", "action": "a", "target": "t",
                                      "pre": "p", "done": "d"},
                          "source_frame_refs": ["frame-1"],
                          "evidence": "gripper closed at frame-1"}]}
        out = manual(doc, MANIFEST)
        self.assertIn("- **evidence**：gripper closed at frame-1", out)
        self.assertLess(out.index("- **target**"), out.index("- **evidence**"))


if __name__ == "__main__":
    unittest.main()
