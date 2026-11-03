"""Prompt v2 + task-hint contract (work order 12, no paid calls)."""
import unittest

from cli import manual, parser
from common import AppError
from providers import MetaProvider, build_prompt, remote_schema_for, sanitize_hint

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
            build_prompt("v10", MANIFEST)
        with self.assertRaises(AppError):
            MetaProvider("muse-spark-1.2", True, "v10")

    def test_v6_extends_v5_with_video_kind_gate(self):
        v5 = build_prompt("v5", MANIFEST)
        v6 = build_prompt("v6", MANIFEST)
        self.assertTrue(v6.startswith(v5.split("Return schema_version")[0]))
        self.assertIn("First decide video_kind", v6)
        self.assertIn("non_procedural", v6)
        self.assertIn("return steps as an empty array", v6)
        self.assertNotIn("First decide video_kind", v5)
        self.assertIn("leave speech_refs empty", v6)
        self.assertNotIn("cite turn indexes in speech_refs", v6)

    def test_v7_adds_verbatim_rule_to_v6(self):
        v6 = build_prompt("v6", MANIFEST)
        v7 = build_prompt("v7", MANIFEST)
        for chunk in ("gripper opens or closes", "never merge two actions",
                      "exact multiples of 1000 ms", "First decide video_kind"):
            self.assertIn(chunk, v7)
        self.assertIn("never guess synonyms or paraphrases for UI labels", v7)
        self.assertIn("write unreadable", v7)
        self.assertNotIn("never guess synonyms", v6)
        hinted = build_prompt("v7", MANIFEST, "washers")
        self.assertIn("never copy hint words into the answer unseen", hinted)
        self.assertNotIn("never copy hint words",
                         build_prompt("v6", MANIFEST, "washers"))

    def test_v9_adds_zoom_window_preamble_to_v6(self):
        v6 = build_prompt("v6", MANIFEST)
        v9 = build_prompt("v9", MANIFEST)
        self.assertIn("magnified time window", v9)
        self.assertIn("relative to THIS clip", v9)
        self.assertIn("Prefer splitting over merging", v9)
        self.assertNotIn("magnified time window", v6)
        for chunk in ("gripper opens or closes", "never merge two actions",
                      "exact multiples of 1000 ms", "First decide video_kind"):
            self.assertIn(chunk, v9)

    def test_v8_appends_desc_table(self):
        table = [{"kf_id": "kf-000001", "t_s": 8.0, "text_seen": ["Buy milk"], "state": "row visible"}]
        with_table = dict(MANIFEST, desc_table=table)
        p = build_prompt("v8", with_table)
        self.assertIn("ONLY from this table", p)
        self.assertIn("Buy milk", p)
        self.assertIn("First decide video_kind", p)
        self.assertNotIn("ONLY from this table", build_prompt("v8", MANIFEST))
        self.assertNotIn("ONLY from this table", build_prompt("v6", with_table))

    def test_keyframes_manifest_adds_image_reading_rule(self):
        with_kf = dict(MANIFEST, keyframes=[{"id": "kf-000001"}])
        p = build_prompt("v7", with_kf)
        self.assertIn("Full-resolution keyframe images", p)
        self.assertIn("cite the kf id", p)
        self.assertNotIn("Full-resolution keyframe", build_prompt("v7", MANIFEST))

    def test_v6_speech_adds_audio_rules(self):
        p = build_prompt("v6-speech", MANIFEST)
        self.assertIn("First decide video_kind", p)
        self.assertIn("return a speech object", p)
        self.assertIn("speech_refs", p)
        self.assertIn("needs_review", p)

    def test_v3_extends_v2_with_verbatim_text_rule(self):
        v2 = build_prompt("v2", MANIFEST)
        v3 = build_prompt("v3", MANIFEST)
        self.assertTrue(v3.startswith(v2.split("Return schema_version")[0]))
        self.assertIn("never guess synonyms or paraphrases for UI labels", v3)
        self.assertNotIn("never guess synonyms", v2)
        low = v3.lower()
        for word in ("washer", "shaft", "0001_task", "aist"):
            self.assertNotIn(word, low)

    def test_v4_has_no_merge_rule_without_d_rule(self):
        v4 = build_prompt("v4", MANIFEST)
        self.assertIn("never merge two actions", v4)
        self.assertIn("check then uncheck", v4)
        self.assertNotIn("never guess synonyms", v4)
        self.assertIn("gripper opens or closes", v4)

    def test_meta_provider_defaults_v6_no_hint(self):
        m = MetaProvider("muse-spark-1.2", True)
        self.assertEqual(m.prompt_version, "v6")
        self.assertEqual(m.task_hint, "")

    def test_cli_prompt_args_default_and_parse(self):
        args = parser().parse_args(["v.mp4", "--workspace-path", "/tmp/ws"])
        self.assertEqual(args.prompt_version, "v6")
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

    def test_remote_schema_v5_keeps_old_shape(self):
        from cli import APP_DIR
        from common import read_json
        schema = read_json(APP_DIR / "operation.schema.json")
        remote = remote_schema_for(schema, "v5")
        self.assertEqual(remote["required"], ["schema_version", "source", "steps"])
        self.assertNotIn("video_kind", remote["properties"])
        step_props = remote["properties"]["steps"]["items"]["properties"]
        self.assertNotIn("speech_refs", step_props)
        # File schema untouched.
        self.assertIn("video_kind", schema["properties"])

    def test_remote_schema_v6_completes_required(self):
        from cli import APP_DIR
        from common import read_json
        schema = read_json(APP_DIR / "operation.schema.json")
        remote = remote_schema_for(schema, "v6")
        self.assertEqual(remote["required"], list(remote["properties"]))
        self.assertIn("video_kind", remote["properties"])
        step = remote["properties"]["steps"]["items"]
        self.assertEqual(step["required"], list(step["properties"]))
        speech = remote["properties"]["speech"]
        self.assertEqual(speech["required"], list(speech["properties"]))

    def test_v6_ignores_audio_v6_speech_listens(self):
        self.assertIn("Ignore the audio track", build_prompt("v6", MANIFEST))
        p = build_prompt("v6-speech", MANIFEST)
        self.assertNotIn("Ignore the audio track", p)
        self.assertIn("Always return a speech object".lower(), p.lower())


if __name__ == "__main__":
    unittest.main()
