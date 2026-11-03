"""Small shared helpers; errors never include remote bodies or credentials."""

import hashlib
import json
from pathlib import Path


class AppError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AppError("Cannot read valid JSON input.") from None


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
