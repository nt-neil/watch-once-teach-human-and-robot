import math
from pathlib import Path

from jsonschema import Draft202012Validator

from common import AppError, sha256


def frame_tolerance_ms(manifest):
    """One source frame in ms; falls back to ~25fps when fps is unknown."""
    try:
        num, den = str(manifest.get("video_fps", "")).split("/")
        fps = float(num) / float(den)
        if math.isfinite(fps) and fps > 0:
            return 1000.0 / fps
    except (ValueError, ZeroDivisionError):
        pass
    return 40.0


def clamp_document(document, manifest):
    """Clamp step boundaries into [0, duration]. Returns (doc, clamped_ids).

    Whole-second boundaries can overshoot non-integer durations (L3 R5);
    clamping keeps the run alive. Steps that collapse (start >= end after
    clamping) are left for validate_operations to reject.
    """
    duration = manifest["source"]["duration_ms"]
    clamped = []
    for step in document.get("steps", []):
        start, end = step["start_ms"], step["end_ms"]
        new_start, new_end = max(0, start), min(end, int(duration))
        if (new_start, new_end) != (start, end):
            step["start_ms"], step["end_ms"] = new_start, new_end
            clamped.append(step["id"])
    return document, clamped


def repair_frame_refs(document, manifest):
    """Drop frame refs outside [start-1f, end+1f); flag the step for review.

    One hallucinated ref must not kill a paid run (web-20261103T142454Z).
    Refs pointing at nonexistent frames are dropped the same way. When every
    ref is dropped, the closest one is kept so the step still cites evidence.
    Flagged steps get a "needs_review ..." evidence prefix (same convention
    as scripts/vote.py). Returns (doc, repaired_step_ids).
    """
    frames = {frame["id"]: frame for frame in manifest["frames"]}
    tolerance = frame_tolerance_ms(manifest)
    repaired = []
    for step in document.get("steps", []):
        start, end = step["start_ms"], step["end_ms"]
        kept, dropped = [], []
        for ref in step["source_frame_refs"]:
            frame = frames.get(ref)
            if frame is None or not start - tolerance <= frame["sample_time_ms"] < end + tolerance:
                dropped.append(ref)
            else:
                kept.append(ref)
        if not dropped:
            continue
        existing = [r for r in dropped if r in frames]
        if kept:
            note = "dropped " + ", ".join(dropped)
            step["source_frame_refs"] = kept
        elif existing:
            closest = min(existing, key=lambda r: abs(frames[r]["sample_time_ms"] - start))
            note = "kept closest " + closest + "; dropped " + ", ".join(r for r in dropped if r != closest)
            step["source_frame_refs"] = [closest]
        else:
            continue
        if not step["evidence"].startswith("needs_review"):
            step["evidence"] = f"needs_review (frame ref out of range, {note}): {step['evidence']}"
        repaired.append(step["id"])
    return document, repaired


def validate_operations(document, schema, manifest, output_dir):
    Draft202012Validator.check_schema(schema)
    if next(Draft202012Validator(schema).iter_errors(document), None):
        # Do not echo untrusted model/fixture data in errors.
        raise AppError("Operation JSON failed schema validation; no task exported.")
    if document["source"] != manifest["source"]:
        raise AppError("Operation source does not match the local video manifest.")
    duration = manifest["source"]["duration_ms"]
    if not math.isfinite(duration):
        raise AppError("Source duration is not finite.")
    frames = {frame["id"]: frame for frame in manifest["frames"]}
    tolerance = frame_tolerance_ms(manifest)
    kind = document.get("video_kind", "procedural")
    if kind == "non_procedural" and document["steps"]:
        raise AppError("Non-procedural video must have empty steps.")
    if kind == "procedural" and not document["steps"]:
        raise AppError("Procedural video must have at least one step.")
    seen_ids, previous_end = set(), 0
    for step in document["steps"]:
        if step["id"] in seen_ids:
            raise AppError("Step IDs must be unique.")
        seen_ids.add(step["id"])
        start, end = step["start_ms"], step["end_ms"]
        if not previous_end <= start < end <= duration:
            raise AppError("Step time must be in source range, ordered, and non-overlapping.")
        previous_end = end
        if any(not text.strip() for text in step["concept"].values()):
            raise AppError("All five concept fields must be non-empty strings.")
        if not step["evidence"].strip():
            raise AppError("Step evidence must be a non-empty string.")
        refs = step["source_frame_refs"]
        if len(set(refs)) != len(refs):
            raise AppError("Frame references must be unique within a step.")
        flagged = step["evidence"].startswith("needs_review")
        for ref in refs:
            if ref not in frames:
                raise AppError("Step references a nonexistent source frame.")
            frame = frames[ref]
            if not flagged and not start - tolerance <= frame["sample_time_ms"] < end + tolerance:
                raise AppError("Referenced sample is outside its step time range.")
            path = Path(output_dir) / frame["file"]
            if not path.is_file() or sha256(path) != frame["image_sha256"]:
                raise AppError("Referenced source image is missing or changed.")

