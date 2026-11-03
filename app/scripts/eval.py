#!/usr/bin/env python3
"""Score a run against a frozen GT. GT is for scoring only, never for prompts.

Usage: python3 scripts/eval.py --run runs/<id> --gt eval/gt/m1.json
       python3 scripts/eval.py --run runs/<id> --gt runs/desktop-1103/ground_truth.json
"""
import argparse
import json
import sys
from pathlib import Path

KEYWORDS = {
    "desktop": ["鳳梨酥", "新增", "清除已完成", "已清除"],
    "m1": ["washer"],
}
HALLUCINATION_WORDS = {
    "desktop": ["篩選", "filter"],
    "m1": ["nut", "screw", "bolt", "tighten", "weld", "locking", "securing"],
}
TOL_MS = 1000


def load_run(run_dir):
    ops = json.loads((run_dir / "operations.json").read_text())
    meta = {}
    pr = run_dir / "provider-result.json"
    if pr.is_file():
        meta = json.loads(pr.read_text()).get("metadata", {})
    return ops, meta


def match_events(steps, gt_steps):
    """Greedy in-order matching. A step hits a GT event when intervals overlap
    and step start is within tolerance of GT start."""
    hits, used = {}, set()
    for g in gt_steps:
        gmid = (g["start_ms"] + g["end_ms"]) / 2
        best, best_err = None, None
        for i, s in enumerate(steps):
            if i in used:
                continue
            if not (s["start_ms"] < g["end_ms"] and g["start_ms"] < s["end_ms"]):
                continue
            err = abs(s["start_ms"] - g["start_ms"])
            if err <= TOL_MS and (best_err is None or err < best_err):
                best, best_err = i, err
        if best is not None:
            used.add(best)
            hits[g["id"]] = {"step": steps[best]["id"], "err_ms": best_err}
    # merges: one step covering 2+ GT mids. splits: 2+ steps hitting one GT.
    merges, splits = 0, 0
    for i, s in enumerate(steps):
        covered = [g for g in gt_steps
                   if s["start_ms"] <= (g["start_ms"] + g["end_ms"]) / 2 < s["end_ms"]]
        if len(covered) >= 2:
            merges += 1
    per_gt = {}
    for gid, h in hits.items():
        per_gt.setdefault(gid, []).append(h["step"])
    splits = sum(len(v) - 1 for v in per_gt.values())
    return hits, merges, splits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--kind", choices=("desktop", "m1"), required=True)
    args = ap.parse_args()
    run_dir = Path(args.run)
    ops, meta = load_run(run_dir)
    gt = json.loads(Path(args.gt).read_text())
    steps = ops.get("steps", [])
    gt_steps = gt.get("steps", [])
    hits, merges, splits = match_events(steps, gt_steps)
    text = json.dumps(ops, ensure_ascii=False).lower()
    keywords = {k: (k.lower() in text) for k in KEYWORDS[args.kind]}
    hallucinations = sorted({w for w in HALLUCINATION_WORDS[args.kind] if w in text})
    cost = None
    rr = run_dir / "run-report.json"
    if rr.is_file():
        try:
            cost = json.loads(rr.read_text()).get("cost_usd")
        except ValueError:
            pass
    if cost is None:
        usage = meta.get("usage") or {}
        try:
            cost = round((int(usage.get("input_tokens", 0)) * 1.25
                          + int(usage.get("output_tokens", 0)) * 4.25) / 1e6, 6)
        except (TypeError, ValueError):
            cost = None
    out = {
        "run": run_dir.name,
        "prompt_version": meta.get("prompt_version"),
        "model": meta.get("model"),
        "task_hint": meta.get("task_hint"),
        "n_steps": len(steps),
        "n_gt": len(gt_steps),
        "step_diff": len(steps) - len(gt_steps),
        "hits": hits,
        "n_hits": len(hits),
        "merges": merges,
        "splits": splits,
        "keywords": keywords,
        "hallucinations": hallucinations,
        "cost_usd": cost,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    missed = [g["id"] for g in gt_steps if g["id"] not in hits]
    print(f"score: {len(hits)}/{len(gt_steps)} events, merges={merges}, "
          f"splits={splits}, kw={sum(keywords.values())}/{len(keywords)}, "
          f"halluc={len(hallucinations)}, missed={missed or 'none'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
