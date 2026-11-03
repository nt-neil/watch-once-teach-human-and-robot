import math
from pathlib import Path

from jsonschema import Draft202012Validator

from common import AppError, sha256


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
        refs = step["source_frame_refs"]
        if len(set(refs)) != len(refs):
            raise AppError("Frame references must be unique within a step.")
        for ref in refs:
            if ref not in frames:
                raise AppError("Step references a nonexistent source frame.")
            frame = frames[ref]
            if not start <= frame["sample_time_ms"] < end:
                raise AppError("Referenced sample is outside its step time range.")
            path = Path(output_dir) / frame["file"]
            if not path.is_file() or sha256(path) != frame["image_sha256"]:
                raise AppError("Referenced source image is missing or changed.")

