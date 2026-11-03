#!/usr/bin/env python3
"""Score on-screen text reading of a run. Reads operations.json only, no API.

Usage: python3 scripts/score_text.py --run runs/<id>
Baseline (root-cause report sec 4): 11:58 run halluc 3, 20:28 run halluc 1.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

KEYWORDS = ["鳳梨酥 3 盒", "新增", "清除已完成", "已完成", "已清除 1 項已完成事項。"]
REQUIRED = "鳳梨酥 3 盒"
WHITELIST = {
    "待辦清單", "待辦事項", "輸入待辦事項", "新增", "清除已完成", "重置",
    "目前沒有待辦事項，請輸入後按「新增」。", "鳳梨酥 3 盒", "已新增待辦事項。",
    "已完成", "已標記完成。", "已改為待完成。", "已清除 1 項已完成事項。",
}
QUOTED = re.compile(r"「([^」]{1,30})」|'([^'\n]{1,30})'|\"([^\"\n]{1,30})\"")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    ops = json.loads((Path(args.run) / "operations.json").read_text())
    text = unicodedata.normalize("NFC", json.dumps(ops, ensure_ascii=False))
    keywords = {k: (unicodedata.normalize("NFC", k) in text) for k in KEYWORDS}
    quoted = set()

    def walk(value):
        if isinstance(value, str):
            for m in QUOTED.finditer(unicodedata.normalize("NFC", value)):
                quoted.add(next(g for g in m.groups() if g is not None))
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(ops)
    unreadable = text.count("unreadable")
    hallucinations = sorted(q for q in quoted
                            if q not in WHITELIST and q.lower() != "unreadable"
                            and any("\u4e00" <= ch <= "\u9fff" for ch in q))
    out = {
        "run": Path(args.run).name,
        "keywords": keywords,
        "required_hit": keywords[REQUIRED],
        "hallucinations": hallucinations,
        "n_halluc": len(hallucinations),
        "unreadable": unreadable,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"text: required={keywords[REQUIRED]}, "
          f"kw={sum(keywords.values())}/{len(keywords)}, "
          f"halluc={len(hallucinations)}, unreadable={unreadable}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
