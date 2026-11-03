"""WCAG contrast check for the WatchOnce palette (code-measured, no eyeballing).

Asserts every hex below appears in server.CSS (drift guard), then asserts
text pairs meet >= 4.5 (normal) / >= 3.0 (large/bold UI) contrast.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as S  # noqa: E402

PALETTE = {
    "page": "#f1f3f1", "surface": "#ffffff", "line": "#d8dee6",
    "ink": "#1a2420", "secondary": "#3d4a44", "accent": "#0b5c4c",
    "accent_text": "#ffffff", "accent_tint": "#e3efe9", "ok": "#166534",
    "warn": "#8a4d00", "err": "#b42318", "code_bg": "#edefec",
}
TEXT_PAIRS = [("ink", "surface"), ("secondary", "surface"),
              ("accent_text", "accent"), ("ok", "surface"),
              ("warn", "surface"), ("err", "surface"),
              ("ink", "code_bg"), ("ink", "page"), ("accent", "accent_tint"),
              ("accent", "surface")]


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    x, y = lum(a), lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


fails = []
for name, hx in PALETTE.items():
    if hx.lower() not in S.CSS.lower():
        fails.append(f"palette {name} {hx} missing from CSS (drift)")
for fg, bg in TEXT_PAIRS:
    r = ratio(PALETTE[fg], PALETTE[bg])
    ok = r >= 4.5
    print(f"{r:5.2f} {'PASS' if ok else 'FAIL'} {fg} on {bg}")
    if not ok:
        fails.append(f"contrast {fg}/{bg}={r:.2f}")
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
