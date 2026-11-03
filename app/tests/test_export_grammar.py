"""Export grammar regression: dual-verb tie-break, onto-split, no target cut."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from cli import APP_DIR

sys.path.insert(0, str(APP_DIR / "export"))
import exporter  # noqa: E402


def concept(action, obj, target):
    return {"action": action, "object": obj, "target": target,
            "pre": "part on surface", "done": "part seated"}


class ExportGrammarTests(unittest.TestCase):
    def test_dual_verb_last_mention_wins(self):
        self.assertEqual(exporter.to_primitive("picks and positions"), "place")
        self.assertEqual(exporter.to_primitive("repositions and picks"), "pick")
        self.assertEqual(exporter.to_primitive("approaches and picks"), "pick")
        self.assertEqual(exporter.to_primitive("transports and positions onto"), "insert")

    def test_place_onto_split_exact(self):
        c = concept("picks and positions", "right robotic gripper",
                    "small round part onto upper stud of metal block")
        self.assertEqual(exporter.to_instruction("place", c),
                         "place the small round part onto the upper stud of metal block")

    def test_target_never_truncated(self):
        c = concept("place", "gripper",
                    "small round part onto upper stud of metal block with extra words here")
        out = exporter.to_instruction("place", c)
        self.assertIn("with extra words here", out)

    def test_export_validates_schema(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "operations.json").write_text(json.dumps({
                "schema_version": "1.0",
                "source": {"video_sha256": "ab" * 32, "video_name": "x.mp4",
                           "duration_ms": 14000.0, "provider": "meta", "mock": False,
                           "frame_manifest": "frames.json"},
                "steps": [{"id": "step-01", "start_ms": 0, "end_ms": 7000,
                           "source_frame_refs": ["frame-1"],
                           "concept": concept("picks and positions",
                                              "right robotic gripper",
                                              "small round part onto upper stud of metal block")}]}))
            for kind in ("lerobot", "vla", "plan"):
                p = exporter.export_run(d, kind, "t")
                self.assertTrue(p)


if __name__ == "__main__":
    unittest.main()
