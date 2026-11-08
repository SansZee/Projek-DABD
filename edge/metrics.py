#!/usr/bin/env python3
"""Validasi dan ukur output edge worker (`detections.frames` JSONL).

Menghasilkan:
    - tabel ringkas di stdout (siap tempel ke slide),
    - <out-dir>/metrics.md  (tabel markdown),
    - <out-dir>/metrics.json (angka mentah untuk laporan).

Contoh:
    python edge/metrics.py runs/cam01.jsonl --stats runs/cam01.stats.json
    python edge/metrics.py runs/*.jsonl --out-dir runs/metrics
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ALLOWED_CLASSES = {"person", "bicycle", "car", "motorcycle", "bus", "truck"}
REQUIRED = ("camera_id", "frame_ts", "frame_id", "detections", "model_version")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def validate(msg: dict) -> list[str]:
    errors = []
    for key in REQUIRED:
        if key not in msg:
            errors.append(f"field wajib hilang: {key}")
    if errors:
        return errors
    if not isinstance(msg["frame_ts"], int) or msg["frame_ts"] <= 0:
        errors.append("frame_ts bukan epoch ms yang valid")
    if not isinstance(msg["frame_id"], int) or msg["frame_id"] < 0:
        errors.append("frame_id harus integer >= 0")
    if not isinstance(msg["detections"], list):
        errors.append("detections bukan array")
        return errors
    for i, det in enumerate(msg["detections"]):
        for key in ("track_id", "cls", "bbox", "conf"):
            if key not in det:
                errors.append(f"detections[{i}] tanpa {key}")
                continue
        bbox = det.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            errors.append(f"detections[{i}].bbox bukan 4 angka")
            continue
        if any(not (0.0 <= float(v) <= 1.0) for v in bbox):
            errors.append(f"detections[{i}].bbox di luar 0-1")
        if float(bbox[0]) >= float(bbox[2]) or float(bbox[1]) >= float(bbox[3]):
            errors.append(f"detections[{i}].bbox terbalik")
        if det.get("cls") not in ALLOWED_CLASSES:
            errors.append(f"detections[{i}].cls tidak dikenal: {det.get('cls')!r}")
        if not (0.0 <= float(det.get("conf", -1)) <= 1.0):
            errors.append(f"detections[{i}].conf di luar 0-1")
    return errors


def analyze_file(path: Path) -> dict:
    messages = 0
    bad_lines = 0
    errors: list[str] = []
    cameras: set[str] = set()
    frame_ts: list[int] = []
    dets_per_frame: list[int] = []
    per_class: dict[str, int] = {}
    track_frames: dict[tuple[str, int], list[int]] = {}
    model_versions: set[str] = set()
    non_monotonic = 0

    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                bad_lines += 1
                if len(errors) < 50:
                    errors.append(f"baris {lineno}: JSON invalid ({exc.msg})")
                continue
            problems = validate(msg)
            if problems:
                if len(errors) < 50:
                    errors.extend(f"baris {lineno}: {p}" for p in problems[:3])
                continue

            messages += 1
            cameras.add(msg["camera_id"])
            model_versions.add(msg["model_version"])
            if frame_ts and msg["frame_ts"] < frame_ts[-1]:
                non_monotonic += 1
            frame_ts.append(msg["frame_ts"])
            dets = msg["detections"]
            dets_per_frame.append(len(dets))
            for det in dets:
                per_class[det["cls"]] = per_class.get(det["cls"], 0) + 1
                key = (msg["camera_id"], det["track_id"])
                track_frames.setdefault(key, []).append(msg["frame_id"])

    gaps_ms = [
        (frame_ts[i] - frame_ts[i - 1]) for i in range(1, len(frame_ts))
    ]
    span_s = (frame_ts[-1] - frame_ts[0]) / 1000.0 if len(frame_ts) > 1 else 0.0
    track_lengths = [len(v) for v in track_frames.values()]
    short_tracks = sum(1 for n in track_lengths if n <= 1)

    return {
        "file": str(path),
        "messages": messages,
        "bad_lines": bad_lines,
        "validation_errors": errors[:50],
        "validation_error_count": len(errors),
        "cameras": sorted(cameras),
        "model_versions": sorted(model_versions),
        "span_s": round(span_s, 2),
        "message_fps": round(messages / span_s, 2) if span_s > 0 else 0.0,
        "gap_ms": {
            "p50": round(percentile(gaps_ms, 0.50), 1),
            "p95": round(percentile(gaps_ms, 0.95), 1),
            "max": max(gaps_ms) if gaps_ms else 0,
        },
        "non_monotonic_ts": non_monotonic,
        "detections_total": sum(dets_per_frame),
        "detections_per_frame": {
            "mean": round(statistics.fmean(dets_per_frame), 2) if dets_per_frame else 0,
            "median": statistics.median(dets_per_frame) if dets_per_frame else 0,
            "empty_frames": sum(1 for n in dets_per_frame if n == 0),
        },
        "detections_per_class": dict(sorted(per_class.items())),
        "unique_tracks": len(track_frames),
        "short_tracks": short_tracks,
        "short_track_ratio": round(short_tracks / len(track_frames), 3)
        if track_frames
        else 0.0,
    }


def load_worker_stats(pattern: str | None) -> dict:
    """Gabungkan stats.json dari worker (berisi latensi yang tidak ada di kontrak)."""
    if not pattern:
        return {}
    merged = {"cameras": [], "latency_ms": [], "process_fps": []}
    for path in sorted(glob.glob(pattern)):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        merged["cameras"].append(
            {
                "camera_id": data.get("camera_id"),
                "process_fps": data.get("process_fps"),
                "frames_processed": data.get("frames_processed"),
                "latency_ms": data.get("latency_ms"),
            }
        )
        if data.get("latency_ms"):
            merged["latency_ms"].append(data["latency_ms"])
        if data.get("process_fps"):
            merged["process_fps"].append(data["process_fps"])
    return merged


def fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def render(result: dict, combined: dict) -> str:
    lines = []
    lines.append(f"### `{Path(result['file']).name}`")
    lines.append("")
    lines.append("| Metrik | Nilai |")
    lines.append("|---|---|")
    rows = [
        ("Pesan terbaca", result["messages"]),
        ("Baris rusak / gagal validasi", result["bad_lines"]),
        ("Error validasi skema", result["validation_error_count"]),
        ("Kamera", ", ".join(result["cameras"]) or "-"),
        ("Model", ", ".join(result["model_versions"]) or "-"),
        ("Durasi data (dari frame_ts)", f"{result['span_s']} s"),
        ("Laju pesan", f"{result['message_fps']} msg/s"),
        ("Jarak antar pesan p50 / p95", f"{result['gap_ms']['p50']} / {result['gap_ms']['p95']} ms"),
        ("frame_ts tidak monoton", result["non_monotonic_ts"]),
        ("Deteksi total", result["detections_total"]),
        ("Deteksi per frame (mean / median)", f"{result['detections_per_frame']['mean']} / {result['detections_per_frame']['median']}"),
        ("Frame kosong", result["detections_per_frame"]["empty_frames"]),
        ("Track unik", result["unique_tracks"]),
        ("Track berumur <= 1 frame", f"{result['short_tracks']} ({result['short_track_ratio']*100:.1f}%)"),
    ]
    for label, value in rows:
        lines.append(f"| {label} | {fmt(value)} |")
    if result["detections_per_class"]:
        lines.append(
            "| Deteksi per kelas | "
            + ", ".join(f"{k}: {v}" for k, v in result["detections_per_class"].items())
            + " |"
        )
    if result["validation_errors"]:
        lines.append("")
        lines.append("Error pertama:")
        for err in result["validation_errors"][:5]:
            lines.append(f"- `{err}`")

    if combined:
        lines.append("")
        lines.append("| Kamera | Proses FPS | Frame | Latensi p50 / p95 / max (ms) |")
        lines.append("|---|---|---|---|")
        for cam in combined.get("cameras", []):
            lat = cam.get("latency_ms") or {}
            lines.append(
                f"| {cam.get('camera_id')} | {fmt(cam.get('process_fps'))} | "
                f"{cam.get('frames_processed')} | "
                f"{lat.get('p50', '-')} / {lat.get('p95', '-')} / {lat.get('max', '-')} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validasi + ukur output detections.frames")
    p.add_argument("inputs", nargs="+", help="file .jsonl")
    p.add_argument("--stats", default=None, help="glob file stats.json dari worker")
    p.add_argument("--out-dir", default=None, help="direktori output metrics.md/json")
    p.add_argument("--strict", action="store_true", help="exit 1 kalau ada error validasi")
    args = p.parse_args(argv)

    paths = []
    for pattern in args.inputs:
        matches = sorted(glob.glob(pattern))
        if not matches:
            print(f"tidak ada file: {pattern}", file=sys.stderr)
            return 2
        paths.extend(Path(m) for m in matches)

    combined = load_worker_stats(args.stats)
    results = [analyze_file(path) for path in paths]

    blocks = [render(r, combined if len(results) == 1 else {}) for r in results]
    if len(results) > 1:
        total_messages = sum(r["messages"] for r in results)
        total_tracks = sum(r["unique_tracks"] for r in results)
        total_dets = sum(r["detections_total"] for r in results)
        agg = {
            "files": len(results),
            "messages": total_messages,
            "detections": total_dets,
            "unique_tracks": total_tracks,
            "validation_errors": sum(r["validation_error_count"] for r in results),
        }
        blocks.insert(
            0,
            "| Agregat | Nilai |\n|---|---|\n"
            + "\n".join(f"| {k} | {v} |" for k, v in agg.items()),
        )

    markdown = f"# Metrik edge worker\n\n_Dibuat {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}_\n\n" + "\n\n".join(blocks) + "\n"
    print(markdown)

    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "metrics.md").write_text(markdown, encoding="utf-8")
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "files": results, "worker_stats": combined}
        (out_dir / "metrics.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"tersimpan: {out_dir/'metrics.md'} , {out_dir/'metrics.json'}", file=sys.stderr)

    if args.strict and any(r["validation_error_count"] for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
