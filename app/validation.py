import math
import re
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


def sample_interval_ms(manifest, fallback):
    """Keyframe sample interval; falls back when missing or invalid."""
    try:
        interval = float(manifest.get("sample_interval_ms", fallback))
        if math.isfinite(interval) and interval > 0:
            return interval
    except (TypeError, ValueError):
        pass
    return fallback


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
    as scripts/vote.py). Refs within one sample interval of the boundary are
    kept as boundary tolerance (the model reads frame-NN as second NN) and
    recorded in the returned forgiven map ({step_id: [refs]}), never flagged.
    Returns (doc, repaired_step_ids, forgiven_map).
    """
    frames = {frame["id"]: frame for frame in manifest["frames"]}
    tolerance = frame_tolerance_ms(manifest)
    interval = sample_interval_ms(manifest, tolerance)
    repaired = []
    forgiven_map = {}
    for step in document.get("steps", []):
        start, end = step["start_ms"], step["end_ms"]
        kept, dropped = [], []
        for ref in step["source_frame_refs"]:
            frame = frames.get(ref)
            if frame is None or not start - tolerance <= frame["sample_time_ms"] < end + tolerance:
                dropped.append(ref)
            else:
                kept.append(ref)
        if dropped:
            forgiven = [r for r in dropped if r in frames
                        and start - interval <= frames[r]["sample_time_ms"] < end + interval]
            if forgiven:
                kept = sorted(set(kept + forgiven),
                              key=lambda r: step["source_frame_refs"].index(r))
                dropped = [r for r in dropped if r not in forgiven]
                step["source_frame_refs"] = kept
                forgiven_map[step["id"]] = forgiven
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
    return document, repaired, forgiven_map


def apply_shot_candidates(document, candidates):
    """Rewrite step starts from [trans:ID] evidence markers (v10/F2).

    The model cites a local pixel-difference candidate per step; the exact
    t_ms replaces the model's guessed start. Unknown IDs flag the step
    instead of rewriting; [trans:new] and unmarked steps pass through.
    Returns (doc, aligned_step_ids).
    """
    by_id = {c["id"]: c["t_ms"] for c in candidates}
    aligned = []
    for step in document.get("steps", []):
        match = re.search(r"\[trans:(\w+)\]", step.get("evidence", ""))
        if not match or match.group(1) == "new":
            continue
        try:
            cid = int(match.group(1))
        except ValueError:
            cid = match.group(1)
        if cid not in by_id:
            if not step["evidence"].startswith("needs_review"):
                step["evidence"] = ("needs_review (unknown trans id "
                                    f"{match.group(1)}): {step['evidence']}")
            continue
        step["start_ms"] = by_id[cid]
        aligned.append(step["id"])
    return document, aligned


def validate_operations(document, schema, manifest, output_dir, forgiven=None):
    """forgiven: {step_id: [refs]} from repair_frame_refs; those refs are
    legal within +/-1 sample interval of the step boundary."""
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
    interval = sample_interval_ms(manifest, tolerance)
    forgiven = forgiven or {}
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
        forgiven_refs = set(forgiven.get(step["id"], []))
        for ref in refs:
            if ref not in frames:
                raise AppError("Step references a nonexistent source frame.")
            frame = frames[ref]
            window = interval if ref in forgiven_refs else tolerance
            if not flagged and not start - window <= frame["sample_time_ms"] < end + window:
                raise AppError("Referenced sample is outside its step time range.")
            path = Path(output_dir) / frame["file"]
            if not path.is_file() or sha256(path) != frame["image_sha256"]:
                raise AppError("Referenced source image is missing or changed.")

