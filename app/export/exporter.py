"""Export validated operations.json to robot/VLA-friendly annotation formats.

Read-only: never modifies operations.json, schema, or validation.
Annotation layer only: NO robot states, actions, or sensor data are produced
or fabricated. Outputs are input-ready annotations for downstream pipelines.
"""
import json
import re
from pathlib import Path

EXPORT_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = EXPORT_DIR / "schemas"

PRIMITIVES = ("pick", "place", "insert", "press", "rotate", "push", "pull",
              "open", "close", "click", "type", "other")

# Ordered rules: first keyword hit wins. UI verbs before generic ones.
RULES = (
    (("click", "check", "uncheck", "tick", "untick", "toggle", "select"), "click"),
    (("type", "input", "enter text", "fill", "keystroke"), "type"),
    (("insert", "put on", "put onto", "slide into", "push onto", "onto"), "insert"),
    (("grasp", "pick", "grab", "lift", "take", "seize"), "pick"),
    (("place", "position", "set down", "release", "drop", "put "), "place"),
    (("press",), "press"),
    (("rotate", "turn", "twist", "screw", "fasten"), "rotate"),
    (("push",), "push"),
    (("pull",), "pull"),
    (("open",), "open"),
    (("close",), "close"),
)

VERBS = {"pick": "pick up", "place": "place", "insert": "insert",
         "press": "press", "rotate": "rotate", "push": "push", "pull": "pull",
         "open": "open", "close": "close", "click": "click", "type": "type",
         "other": "perform"}
WITH_TARGET = {"place": "on", "insert": "into", "press": "on"}


PICK_WORDS = ("grasp", "pick", "grab", "lift", "take", "seize")
PLACE_WORDS = ("place", "position", "set down", "release", "drop", "put ")


def to_primitive(action_text):
    t = (action_text or "").lower()
    for keywords, prim in RULES:
        if any(k in t for k in keywords):
            if prim in ("pick", "place"):
                return _pick_or_place(t)
            return prim
    return "other"


def _pick_or_place(t):
    """Dual verbs ("picks and positions") resolve by last mention."""
    picks = [k for k in PICK_WORDS if k in t]
    places = [k for k in PLACE_WORDS if k in t]
    if picks and places:
        last_pick = max(t.rfind(k) for k in picks)
        last_place = max(t.rfind(k) for k in places)
        return "place" if last_place > last_pick else "pick"
    return "place" if places else "pick"


def short_noun(phrase, limit=8):
    p = re.split(r"[,(;]", phrase or "")[0].strip().rstrip(".")
    words = p.split()
    if limit is not None and len(words) > limit:
        p = " ".join(words[:limit])
    if p and not re.match(r"^(the|a|an|this|that)\b", p, re.I):
        p = "the " + p
    return p or "the object"


ACTOR_WORDS = ("gripper", "hand", "robot", "arm", "claw", "finger", "cursor", "operator")


def held_part(object_text):
    m = re.search(r"holding\s+(.+)", object_text or "", re.I)
    return m.group(1).strip() if m else None


def to_instruction(primitive, concept):
    obj_text = concept.get("object", "")
    is_actor = any(w in obj_text.lower() for w in ACTOR_WORDS)
    verb = VERBS[primitive]
    if primitive == "pick":
        patient = held_part(obj_text) or (concept.get("target", "") if is_actor else obj_text)
        return f"{verb} {short_noun(patient)}"
    if primitive in WITH_TARGET:
        if is_actor:
            patient = held_part(obj_text) or "the part"
        else:
            patient = obj_text
        prep = WITH_TARGET[primitive]
        raw_tgt = concept.get("target", "")
        m = re.split(r"\s+onto\s+", raw_tgt, maxsplit=1, flags=re.I)
        if primitive == "place" and len(m) == 2 and m[0].strip() and m[1].strip():
            patient, raw_tgt, prep = m[0].strip(), m[1].strip(), "onto"
        tgt = short_noun(raw_tgt, limit=None)
        return f"{verb} {short_noun(patient)} {prep} {tgt}"
    return f"{verb} {short_noun(obj_text)}"


def load_operations(run_dir):
    doc = json.loads((Path(run_dir) / "operations.json").read_text())
    if doc.get("schema_version") != "1.0":
        raise ValueError("unsupported operations schema_version")
    return doc


def build_subtasks(doc):
    out = []
    for i, step in enumerate(doc["steps"]):
        concept = step["concept"]
        prim = to_primitive(concept.get("action", ""))
        out.append({
            "subtask_id": f"subtask-{i + 1:02d}",
            "step_id": step["id"],
            "instruction": to_instruction(prim, concept),
            "instruction_origin": "rule_based_template_v2",
            "action_primitive": prim,
            "primitive_origin": "keyword_rules_v2",
            "object": concept["object"],
            "action": concept["action"],
            "target": concept["target"],
            "pre": concept["pre"],
            "done": concept["done"],
            "start_s": round(step["start_ms"] / 1000, 3),
            "end_s": round(step["end_ms"] / 1000, 3),
            "source_frames": list(step["source_frame_refs"]),
        })
    return out


def build_lerobot(doc, run_id):
    """LeRobot v3-style episode annotation layer (meta/episodes + tasks only)."""
    src = doc["source"]
    return {
        "format": "lerobot-v3-episode-annotation/0.1",
        "annotation_only": True,
        "robot_state_action_data": None,
        "episode": {
            "episode_id": run_id,
            "task": f"Reproduce the {len(doc['steps'])} demonstrated assembly steps",
            "task_id": 0,
            "source_video_sha256": src["video_sha256"],
            "source_video_name": src["video_name"],
            "duration_s": round(src["duration_ms"] / 1000, 3),
            "subtasks": build_subtasks(doc),
        },
        "not_included": ["data/*.parquet robot states and actions",
                         "videos/*.mp4 shards", "meta/stats.json normalization"],
        "reference": "https://huggingface.co/docs/lerobot/lerobot-dataset-v3",
    }


def build_vla(doc, run_id):
    return {
        "format": "vla-instruction-sequence/0.1",
        "episode_id": run_id,
        "source_video_sha256": doc["source"]["video_sha256"],
        "steps": [{"id": s["subtask_id"], "instruction": s["instruction"],
                   "start_s": s["start_s"], "end_s": s["end_s"]}
                  for s in build_subtasks(doc)],
    }


def build_plan(doc, run_id):
    return {
        "format": "bt-pddl-plan-steps/0.1",
        "episode_id": run_id,
        "source_video_sha256": doc["source"]["video_sha256"],
        "note": "Preconditions/effects are the demonstrated five-field strings; executor must ground them.",
        "steps": [{"id": s["subtask_id"], "action": s["action_primitive"],
                   "instruction": s["instruction"], "target": s["target"],
                   "preconditions": [s["pre"]], "effects": [s["done"]],
                   "start_s": s["start_s"], "end_s": s["end_s"]}
                  for s in build_subtasks(doc)],
    }


BUILDERS = {"lerobot": ("lerobot_episode_annotation.schema.json", build_lerobot),
            "vla": ("vla_sequence.schema.json", build_vla),
            "plan": ("plan_steps.schema.json", build_plan)}


def validate_against(name, payload):
    from jsonschema import Draft202012Validator
    schema = json.loads((SCHEMA_DIR / name).read_text())
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        raise ValueError(f"{name}: {len(errors)} schema errors; first: {errors[0].message[:200]}")


def export_run(run_dir, kind, run_id=None):
    run_dir = Path(run_dir)
    run_id = run_id or run_dir.name
    if kind not in BUILDERS:
        raise ValueError(f"unknown export kind: {kind}")
    schema_name, build = BUILDERS[kind]
    payload = build(load_operations(run_dir), run_id)
    validate_against(schema_name, payload)
    return payload


def export_all(run_dir, out_dir, run_id=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for kind in BUILDERS:
        payload = export_run(run_dir, kind, run_id)
        p = out_dir / f"{(run_id or Path(run_dir).name)}-{kind}.json"
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        paths[kind] = str(p)
    return paths
