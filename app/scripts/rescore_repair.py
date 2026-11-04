#!/usr/bin/env python3
"""Re-run repair_frame_refs (with boundary tolerance) over saved runs.

Reads provider-result.json (raw model output) + frames.json, applies the
current repair, and reports needs_review counts before/after. Read-only:
never overwrites the run. Usage: scripts/rescore_repair.py [run ...]
"""
import copy
import json
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from validation import repair_frame_refs


def rescore(run_dir):
    run_dir = Path(run_dir)
    ops = json.loads((run_dir / "operations.json").read_text())
    old = sum(1 for s in ops.get("steps", []) if s.get("evidence", "").startswith("needs_review"))
    pr = json.loads((run_dir / "provider-result.json").read_text())
    manifest = json.loads((run_dir / "frames.json").read_text())
    doc = copy.deepcopy(pr["response"])
    doc, repaired, forgiven = repair_frame_refs(doc, manifest)
    new = len(repaired)
    return {"run": run_dir.name, "steps": len(doc.get("steps", [])),
            "old_needs_review": old, "new_needs_review": new,
            "forgiven": forgiven, "repaired": repaired}


def main(argv):
    runs = [APP_DIR / "runs" / r for r in argv] if argv else sorted((APP_DIR / "runs").iterdir())
    rows = []
    for rd in runs:
        if not rd.is_dir() or not (rd / "provider-result.json").is_file():
            continue
        if not (rd / "frames.json").is_file() or not (rd / "operations.json").is_file():
            continue
        try:
            rows.append(rescore(rd))
        except Exception as exc:  # noqa: BLE001 - report, don't stop the batch
            rows.append({"run": rd.name, "error": str(exc)[:120]})
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    ok = [r for r in rows if "error" not in r]
    print(f"# rescored={len(ok)} errors={len(rows)-len(ok)} "
          f"old_total={sum(r['old_needs_review'] for r in ok)} "
          f"new_total={sum(r['new_needs_review'] for r in ok)}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
