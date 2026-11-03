#!/usr/bin/env python3
"""Rescore every eval run under the current ruler. Zero API calls.

Usage: python3 scripts/rescore.py [--vote-only | --singles-only]
Vote products are re-merged with strict-majority vote.py into
runs/<id>-maj (originals untouched), then scored. Subclip runs
(zoomin/v9/v9w) have no GT and are skipped.
"""
import json
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
RUNS = APP / "runs"
DESKTOP_GT = "runs/desktop-1103/ground_truth.json"

# run-id prefix/substring -> (gt path, kind or None)
GT_MAP = [
    ("held-aalto-03", ("eval/gt/heldout/aalto-03.json", None)),
    ("held-l4", ("eval/gt/heldout/l4-strex.json", None)),
    ("held-m1-0008", ("eval/gt/heldout/m1-0008.json", "m1")),
    ("held-postfire", ("eval/gt/heldout/postfire-forest.json", None)),
    ("held-stump", ("eval/gt/heldout/stump-grinder.json", None)),
    ("held-ui-a", ("eval/gt/heldout/ui-a.json", None)),
    ("held-wok", ("eval/gt/heldout/wok-cooking.json", None)),
    ("heldvote-aalto-03", ("eval/gt/heldout/aalto-03.json", None)),
    ("heldvote-l4", ("eval/gt/heldout/l4-strex.json", None)),
    ("heldvote-m1-0008", ("eval/gt/heldout/m1-0008.json", "m1")),
    ("heldvote-postfire", ("eval/gt/heldout/postfire-forest.json", None)),
    ("heldvote-stump", ("eval/gt/heldout/stump-grinder.json", None)),
    ("heldvote-ui-a", ("eval/gt/heldout/ui-a.json", None)),
    ("heldvote-wok", ("eval/gt/heldout/wok-cooking.json", None)),
    ("live-m1", ("eval/gt/m1.json", "m1")),
    ("m1b", ("eval/gt/m1-0002.json", "m1")),
    ("m1-0008", ("eval/gt/heldout/m1-0008.json", "m1")),
    ("m1", ("eval/gt/m1.json", "m1")),
    ("l3", ("runs/l3-1030/ground_truth.json", None)),
    ("w1", ("runs/w1-1030/ground_truth.json", None)),
    ("s003", ("eval/gt/s003-window.json", None)),
    ("winter", ("eval/gt/winter-stream.json", None)),
    ("talk", ("eval/gt/talk-slides.json", None)),
    ("uib", ("eval/gt/ui-b.json", None)),
    ("obs", (DESKTOP_GT, "desktop")),
    ("hyb", (DESKTOP_GT, "desktop")),
    ("v5obs", (DESKTOP_GT, "desktop")),
    ("vote5-obs", (DESKTOP_GT, "desktop")),
    ("vote-obs", (DESKTOP_GT, "desktop")),
    ("phab", ("eval/gt/phab-ui.json", None)),
    ("wok", ("eval/gt/wok-dev.json", None)),
    ("chicken", ("eval/gt/chicken-rice.json", None)),
    ("alioli60", ("eval/gt/alioli-0-60.json", None)),
    ("taliya", ("eval/gt/taliya.json", None)),
    ("pot60", ("eval/gt/pottery-0-60.json", None)),
    ("smith", ("eval/gt/blacksmith.json", None)),
]

SKIP_SUBSTR = ("zoomin", "v9w-", "v9-obs", "v9-uib", "r8-alioli")


def gt_for(run_id):
    for key, val in GT_MAP:
        if key in run_id:
            return val
    return None


def vote_sources(run_id):
    """Original single runs behind a vote product, or None."""
    if run_id == "eval-vote-obs":
        return ["eval-e2pp-obs-1", "eval-e2pp-obs-2", "eval-e2pp-obs-3"]
    if run_id == "eval-vote5-obs":
        return [f"eval-v5obs-{i}" for i in (1, 2, 3, 4, 5)]
    if run_id.startswith("eval-evote-"):
        clip = run_id.split("eval-evote-")[1]
        if clip == "obs":
            return ["eval-ed-obs", "eval-eb-obs", "eval-ec-obs"]
        return [f"eval-ea-{clip}", f"eval-eb-{clip}", f"eval-ec-{clip}"]
    if run_id.startswith("eval-heldvote-"):
        clip = run_id.split("eval-heldvote-")[1]
        names = {"aalto-03": "aalto-03", "l4": "l4", "m1-0008": "m1-0008",
                 "postfire": "postfire", "stump": "stump", "ui-a": "ui-a",
                 "wok": "wok"}
        return [f"eval-held-{names[clip]}-{i}" for i in (1, 2, 3)]
    return None


def score(run_dir, gt, kind):
    cmd = [sys.executable, "-B", str(APP / "scripts" / "eval.py"),
           "--run", str(run_dir), "--gt", str(APP / gt)]
    if kind:
        cmd += ["--kind", kind]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout)[-200:]}
    return json.loads(r.stdout)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--all"
    sys.path.insert(0, str(APP / "scripts"))
    from vote import merge_runs
    rows = []
    for rundir in sorted(RUNS.glob("eval-*")):
        rid = rundir.name
        if any(s in rid for s in SKIP_SUBSTR):
            continue
        if not (rundir / "operations.json").is_file():
            rows.append((rid, "NO_OPERATIONS", "", "", "", "", ""))
            continue
        srcs = vote_sources(rid)
        target, tag = rundir, "single"
        if srcs:
            if mode == "--singles-only":
                continue
            if any(not (RUNS / s / "operations.json").is_file() for s in srcs):
                rows.append((rid, "VOTE_SRC_MISSING", "", "", "", "", ""))
                continue
            out = RUNS / (rid + "-maj")
            out.mkdir(exist_ok=True)
            base, stats = merge_runs([str(RUNS / s) for s in srcs])
            (out / "operations.json").write_text(json.dumps(base, ensure_ascii=False))
            target, tag = out, f"maj{stats['n_runs']}p{stats['proposals']}"
        elif mode == "--vote-only":
            continue
        g = gt_for(rid)
        if g is None:
            rows.append((rid, "NO_GT_MAP", "", "", "", "", ""))
            continue
        d = score(target, g[0], g[1])
        if "error" in d:
            rows.append((rid, "SCORE_ERROR", "", "", "", "", ""))
            continue
        kw = f"{sum(d['keywords'].values())}/{len(d['keywords'])}"
        rows.append((rid, tag, f"{d['n_hits']}/{d['n_gt']}",
                     f"m{d['merges']}", kw, f"h{len(d['hallucinations'])}",
                     "PASS" if d["passed"] else "FAIL"))
    print("run\ttype\thits\tmerges\tkw\thalluc\tverdict")
    for r in rows:
        print("\t".join(r))


if __name__ == "__main__":
    main()
