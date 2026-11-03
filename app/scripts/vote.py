#!/usr/bin/env python3
"""Majority-vote merge of N runs of the same clip. No API calls.

Usage: python3 scripts/vote.py --runs runs/a runs/b runs/c --out runs/voted
Steps cluster by time overlap; only clusters with a strict majority
(> N/2 distinct runs) are kept, with median boundaries and per-field
concept vote. Minority clusters are listed under proposal_steps and do
NOT enter the scored steps (Fable A2/A3).
"""
import argparse
import copy
import json
import statistics
import sys
from pathlib import Path


def overlap(a, b):
    return a["start_ms"] < b["end_ms"] and b["start_ms"] < a["end_ms"]


def iou(a, b):
    inter = max(0, min(a["end_ms"], b["end_ms"]) - max(a["start_ms"], b["start_ms"]))
    union = max(a["end_ms"], b["end_ms"]) - min(a["start_ms"], b["start_ms"])
    return inter / union if union else 0.0


def cluster(all_steps):
    clusters = []
    for cand in sorted(all_steps, key=lambda s: s["start_ms"]):
        placed = False
        for cl in clusters:
            if any(iou(cand, member) > 0.5 for member in cl):
                cl.append(cand)
                placed = True
                break
        if not placed:
            clusters.append([cand])
    return clusters


def vote_field(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values())
    winners = sorted(v for v, c in counts.items() if c == best)
    return winners[0]


def merge_runs(run_dirs):
    """Merge operations.json of each run dir. Returns (doc, stats)."""
    docs = []
    for r in run_dirs:
        docs.append(json.loads((Path(r) / "operations.json").read_text()))
    kinds = [d.get("video_kind", "procedural") for d in docs]
    kind = vote_field(kinds)
    tagged = [(s, i) for i, d in enumerate(docs) for s in d.get("steps", [])]
    steps = sorted(
        [{"start_ms": s["start_ms"], "end_ms": s["end_ms"], "run": i, "step": s}
         for s, i in tagged],
        key=lambda x: x["start_ms"])
    clusters = cluster([{**s["step"], "start_ms": s["start_ms"],
                         "end_ms": s["end_ms"]} for s in steps])
    n = len(docs)
    bar = n / 2
    merged, proposals = [], []
    for cl in clusters:
        voters = set()
        for s in steps:
            if any(iou({"start_ms": s["start_ms"], "end_ms": s["end_ms"]},
                       {"start_ms": m["start_ms"], "end_ms": m["end_ms"]}) > 0.5 for m in cl):
                voters.add(s["run"])
        starts = sorted(m["start_ms"] for m in cl)
        ends = sorted(m["end_ms"] for m in cl)
        concepts = {}
        for field in ("object", "action", "target", "pre", "done"):
            concepts[field] = vote_field([m["concept"][field] for m in cl])
        refs = sorted({r for m in cl for r in m["source_frame_refs"]})
        evidences = sorted({m["evidence"] for m in cl})
        evidence = evidences[0] if len(evidences) == 1 else "VOTE: " + " | ".join(evidences)
        entry = {"start_ms": int(statistics.median(starts)),
                 "end_ms": int(statistics.median(ends)), "concept": concepts,
                 "source_frame_refs": refs[:4] or cl[0]["source_frame_refs"][:1],
                 "evidence": evidence, "votes": f"{len(voters)}/{n}"}
        if len(voters) > bar:
            merged.append(entry)
        else:
            entry["evidence"] = f"proposal ({len(voters)}/{n} votes): " + evidence
            proposals.append(entry)
    merged.sort(key=lambda s: (s["start_ms"], s["end_ms"]))
    for ci, s in enumerate(merged, 1):
        s["id"] = f"vote-{ci:02d}"
        del s["votes"]
    for ci, s in enumerate(proposals, 1):
        s["id"] = f"prop-{ci:02d}"
    base = copy.deepcopy(docs[0])
    base["video_kind"] = kind
    base["steps"] = merged
    base["proposal_steps"] = proposals
    stats = {"n_runs": n, "n_steps": len(merged), "proposals": len(proposals),
             "video_kind": kind}
    return base, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    base, stats = merge_runs(args.runs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "operations.json").write_text(json.dumps(base, ensure_ascii=False, indent=1))
    print(json.dumps(stats))
    print(f"vote: {stats['n_steps']} steps from {stats['n_runs']} runs, "
          f"{stats['proposals']} proposals, kind={stats['video_kind']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
