"""Decode actual source frames; never infer frame numbers from log line numbers."""

import json
import math
from pathlib import Path
import re
import subprocess

from common import AppError, sha256


def run_media(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise AppError("ffmpeg/ffprobe is missing; install FFmpeg first.") from None
    except subprocess.TimeoutExpired:
        raise AppError("Local media decode timed out; no task exported.") from None
    if result.returncode:
        raise AppError("Local media probe/decode failed; input rejected.")
    return result


def check_probe(probe):
    streams = probe.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise AppError("Input has no video stream.")
    video = videos[0]
    try:
        duration = float(probe["format"]["duration"])
        video_duration = float(video.get("duration", duration))
        durations = [duration, video_duration] + [float(s["duration"]) for s in streams if "duration" in s]
    except (KeyError, TypeError, ValueError):
        raise AppError("Source duration is missing or invalid.") from None
    if not all(math.isfinite(d) and d > 0 for d in durations):
        raise AppError("Source duration is missing or invalid.")
    if max(durations) > 60:
        raise AppError("Source exceeds 60 seconds; input rejected (no automatic trimming).")
    if "mp4" not in probe.get("format", {}).get("format_name", "").split(","):
        raise AppError("Only a local MP4 is accepted; original MP4 is required for audio upload.")
    return video, max(durations) * 1000, video_duration * 1000


FRAME_LOG = re.compile(r"showinfo@(source|sample)\s+@.*?\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*([-+\d.eE]+)")


def parse_samples(log):
    source = None
    origin = None
    samples = []
    for line in log.splitlines():
        match = FRAME_LOG.search(line)
        if not match:
            continue
        kind, n, pts, pts_time = match.groups()
        frame = {"source_frame": int(n), "pts": int(pts), "pts_time_ms": float(pts_time) * 1000}
        if not math.isfinite(frame["pts_time_ms"]):
            raise AppError("Invalid source PTS.")
        if kind == "source":
            source = frame
            if origin is None:
                origin = frame["pts_time_ms"]
        else:
            if source is None or source["pts"] != frame["pts"] or source["pts_time_ms"] != frame["pts_time_ms"]:
                raise AppError("Cannot match sampled frame to decoder source PTS.")
            samples.append({**source, "sample_time_ms": round(source["pts_time_ms"] - origin, 6)})
    if not samples:
        raise AppError("No decoded sample frames; input rejected.")
    return origin, samples


def extract(video_path, output_dir, provider, interval_ms=1000):
    video_path, output_dir = Path(video_path), Path(output_dir)
    if not video_path.is_file() or video_path.suffix.lower() != ".mp4":
        raise AppError("Provide an existing local .mp4 file.")
    if not 100 <= interval_ms <= 60000:
        raise AppError("Sample interval must be 100..60000 milliseconds.")
    probe_result = run_media(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video_path)])
    try:
        probe = json.loads(probe_result.stdout)
    except ValueError:
        raise AppError("ffprobe returned invalid metadata.") from None
    stream, duration_ms, video_duration_ms = check_probe(probe)
    source = {
        "video_sha256": sha256(video_path), "video_name": video_path.name,
        "duration_ms": duration_ms, "provider": provider, "mock": provider == "mock",
        "frame_manifest": "frames.json",
    }
    frame_dir = output_dir / "frames"
    frame_dir.mkdir()
    prefix = "mock" if source["mock"] else "meta"
    pattern = frame_dir / f"{prefix}-frame-%06d.jpg"
    # showinfo@source emits decoder n before select; @sample emits the same PTS after select.
    filters = f"showinfo@source,select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval_ms / 1000}),showinfo@sample,scale='min(640,iw)':-2"
    result = run_media([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-xerror", "-err_detect", "explode",
        "-copyts", "-i", str(video_path), "-map", f"0:{stream['index']}", "-an", "-sn", "-dn",
        "-vf", filters, "-fps_mode", "passthrough", "-q:v", "3", str(pattern),
    ])
    origin, samples = parse_samples(result.stderr)
    files = sorted(frame_dir.glob("*.jpg"))
    if len(files) != len(samples):
        raise AppError("Decoded sample/image counts differ; refusing fabricated frame references.")
    for i, (frame, path) in enumerate(zip(samples, files), 1):
        if path.name != f"{prefix}-frame-{i:06d}.jpg" or path.stat().st_size == 0:
            raise AppError("A sampled image is missing or empty.")
        if not 0 <= frame["sample_time_ms"] < duration_ms:
            raise AppError("Sample PTS is outside source duration.")
        if i > 1 and frame["sample_time_ms"] <= samples[i - 2]["sample_time_ms"]:
            raise AppError("Source sample timestamps are not increasing.")
        frame.update(id=f"frame-{i:06d}", file=f"frames/{path.name}", image_sha256=sha256(path))
    if sha256(video_path) != source["video_sha256"]:
        raise AppError("Source changed while decoding; input rejected.")
    return {
        "schema_version": "1.0", "source": source, "video_stream_index": stream["index"],
        "source_duration_ms": duration_ms, "video_duration_ms": video_duration_ms,
        "time_base": stream["time_base"], "timeline_origin_ms": origin,
        "sample_interval_ms": interval_ms,
        "has_audio": any(s.get("codec_type") == "audio" for s in probe["streams"]),
        "frames": samples,
    }
