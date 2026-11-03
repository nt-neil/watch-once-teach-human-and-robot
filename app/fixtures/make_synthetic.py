#!/usr/bin/env python3
"""Generate a self-authored 3-second test pattern with a sine tone; no downloaded media."""

from pathlib import Path
import subprocess

app = Path(__file__).resolve().parents[1]
out = app / "runs" / "synthetic-fixture.mp4"
out.parent.mkdir(exist_ok=True)
if out.exists():
    raise SystemExit("Synthetic fixture already exists; reuse it for the CLI, or remove that fixture explicitly.")
result = subprocess.run([
    "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n",
    "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=3",
    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(out),
], capture_output=True, text=True)
if result.returncode:
    raise SystemExit("Synthetic FFmpeg generation failed.")
print(out)
