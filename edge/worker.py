#!/usr/bin/env python3
"""Traffic Violation Mesh - edge worker (spike Fase 1).

Membaca video file / RTSP / webcam, menjalankan YOLO + ByteTrack, lalu
mengeluarkan satu pesan per frame sesuai kontrak `detections.frames`
(lihat edge/schema/detections.frames.json).

Contoh:
    # file, loop, JSON ke stdout
    python edge/worker.py --source clip.mp4 --stride 3

    # RTSP + rekam video anotasi + simpan statistik
    python edge/worker.py --source rtsp://localhost:8554/cam1 \
        --camera-id CAM-01 --record runs/cam01.mp4 --stats-out runs/cam01.stats.json

    # 3 kamera paralel, JSON ke file masing-masing
    for c in 1 2 3; do
      python edge/worker.py --source rtsp://localhost:8554/cam$c \
        --camera-id CAM-0$c --out runs/cam0$c.jsonl --quiet &
    done
"""

from __future__ import annotations

import argparse
import json
import signal
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

COCO_CLASSES = {
    "person": 0,
    "bicycle": 1,
    "car": 2,
    "motorcycle": 3,
    "bus": 5,
    "truck": 7,
}
DEFAULT_CLASSES = ["person", "car", "motorcycle", "bus", "truck"]
TRACKER = "bytetrack.yaml"


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def percentile(values: list[float], q: float) -> float:
    """Persentil tanpa dependency numpy (interpolasi linear)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def parse_source(raw: str):
    """Mengembalikan (source, is_live)."""
    if raw.isdigit():
        return int(raw), True
    if raw.startswith(("rtsp://", "http://", "https://")):
        return raw, True
    return raw, False


class Stats:
    def __init__(
        self,
        camera_id: str,
        source: str,
        args,
        src_fps: float = 0.0,
        is_live: bool = False,
    ) -> None:
        self.camera_id = camera_id
        self.source = source
        self.args = args
        self.src_fps = src_fps
        self.is_live = is_live
        self.started_at = iso_now()
        self.t_start = time.perf_counter()
        self.frames_read = 0
        self.frames_processed = 0
        self.empty_frames = 0
        self.read_errors = 0
        self.detections_total = 0
        self.latencies: list[float] = []
        self.warmup_latencies: list[float] = []
        self.dets_per_class: dict[str, int] = {}
        self.tracks_per_class: dict[str, int] = {}
        self.seen_tracks: set[int] = set()
        self.per_class_tracks: dict[str, set[int]] = {}
        self.stages = {
            "grab": 0.0,
            "retrieve": 0.0,
            "infer": 0.0,
            "write": 0.0,
            "pace": 0.0,
        }

    def observe(self, latency_ms: float, detections: list[dict]) -> None:
        self.frames_processed += 1
        # 2 frame pertama menanggung inisialisasi CUDA/tracker, jangan campur ke p50/p95
        if len(self.latencies) + len(self.warmup_latencies) < 2:
            self.warmup_latencies.append(latency_ms)
        else:
            self.latencies.append(latency_ms)
            self.stages["infer"] += latency_ms / 1000.0
        self.detections_total += len(detections)
        if not detections:
            self.empty_frames += 1
        for det in detections:
            cls = det["cls"]
            self.dets_per_class[cls] = self.dets_per_class.get(cls, 0) + 1
            tid = det["track_id"]
            if tid >= 0:
                self.seen_tracks.add(tid)
                self.per_class_tracks.setdefault(cls, set()).add(tid)

    def summary(self) -> dict:
        elapsed = max(time.perf_counter() - self.t_start, 1e-9)
        latency = self.latencies
        warmup_s = sum(self.warmup_latencies) / 1000.0
        active_s = max(elapsed - warmup_s, 1e-9)
        steady_frames = max(self.frames_processed - len(self.warmup_latencies), 0)
        steady_fps = steady_frames / active_s
        paced_fps = self.src_fps / max(self.args.stride, 1) if self.src_fps else 0.0
        return {
            "camera_id": self.camera_id,
            "source": str(self.source),
            "model": self.args.model,
            "stride": self.args.stride,
            "imgsz": self.args.imgsz,
            "conf": self.args.conf,
            "src_fps": round(self.src_fps, 2),
            "source_kind": "live" if self.is_live else "file",
            "realtime": self.args.realtime,
            "started_at": self.started_at,
            "ended_at": iso_now(),
            "elapsed_s": round(elapsed, 3),
            "warmup_s": round(warmup_s, 3),
            "frames_read": self.frames_read,
            "frames_processed": self.frames_processed,
            "frames_skipped": self.frames_read - self.frames_processed,
            "process_fps": round(self.frames_processed / elapsed, 2),
            "steady_process_fps": round(steady_fps, 2),
            "read_fps": round(self.frames_read / elapsed, 2),
            "realtime_factor": round(steady_fps / paced_fps, 2)
            if paced_fps and not self.is_live
            else None,
            "latency_ms": {
                "mean": round(statistics.fmean(latency), 2) if latency else 0.0,
                "p50": round(percentile(latency, 0.50), 2),
                "p95": round(percentile(latency, 0.95), 2),
                "p99": round(percentile(latency, 0.99), 2),
                "max": round(max(latency), 2) if latency else 0.0,
                "warmup": [round(v, 2) for v in self.warmup_latencies],
            },
            "detections_total": self.detections_total,
            "detections_per_frame": round(
                self.detections_total / self.frames_processed, 2
            )
            if self.frames_processed
            else 0.0,
            "empty_frames": self.empty_frames,
            "detections_per_class": dict(sorted(self.dets_per_class.items())),
            "unique_tracks": len(self.seen_tracks),
            "tracks_per_class": {
                cls: len(ids) for cls, ids in sorted(self.per_class_tracks.items())
            },
            "read_errors": self.read_errors,
            "stage_ms_per_frame": {
                key: round(value * 1000.0 / steady_frames, 2)
                for key, value in self.stages.items()
            }
            if steady_frames
            else {},
        }


def build_detections(result, width: int, height: int) -> list[dict]:
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []
    ids = boxes.id
    out = []
    for i, (xyxy, conf, cls) in enumerate(zip(boxes.xyxy, boxes.conf, boxes.cls)):
        x1, y1, x2, y2 = (float(v) for v in xyxy)
        track_id = int(ids[i]) if ids is not None else -1
        out.append(
            {
                "track_id": track_id,
                "cls": result.names[int(cls)],
                "bbox": [
                    round(max(0.0, min(1.0, x1 / width)), 4),
                    round(max(0.0, min(1.0, y1 / height)), 4),
                    round(max(0.0, min(1.0, x2 / width)), 4),
                    round(max(0.0, min(1.0, y2 / height)), 4),
                ],
                "conf": round(float(conf), 3),
            }
        )
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Edge worker: video/RTSP -> YOLO+ByteTrack -> detections.frames",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--source", required=True, help="file, URL rtsp/http, atau index webcam")
    p.add_argument("--camera-id", default="CAM-01", help="ID kamera (key partisi Kafka)")
    p.add_argument("--model", default="yolo11n.pt", help="bobot YOLO")
    p.add_argument("--stride", type=int, default=3, help="proses tiap frame ke-N")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument(
        "--classes",
        default=",".join(DEFAULT_CLASSES),
        help="kelas COCO dipisah koma",
    )
    p.add_argument("--device", default=None, help="'0' untuk GPU, 'cpu' untuk CPU")
    p.add_argument("--half", action="store_true", help="FP16 (GPU saja)")
    p.add_argument("--out", default="-", help="file JSONL output, '-' untuk stdout")
    p.add_argument("--stats-out", default=None, help="path file statistik JSON")
    p.add_argument("--record", default=None, help="path video anotasi (.mp4)")
    p.add_argument(
        "--duration",
        type=float,
        default=0,
        help="berhenti setelah N detik (0 = terus sampai Ctrl+C)",
    )
    p.add_argument("--no-loop", action="store_true", help="jangan ulang file saat habis")
    p.add_argument(
        "--no-realtime",
        dest="realtime",
        action="store_false",
        help="baca file secepat mungkin (untuk uji throughput, bukan simulasi kamera)",
    )
    p.set_defaults(realtime=True)
    p.add_argument(
        "--reconnect",
        type=int,
        default=30,
        help="percobaan sambung ulang untuk source live",
    )
    p.add_argument("--quiet", action="store_true", help="tanpa progress di stderr")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    class_ids = []
    for name in args.classes.split(","):
        name = name.strip()
        if name not in COCO_CLASSES:
            raise SystemExit(
                f"kelas tidak dikenal: {name!r}. Pilihan: {', '.join(COCO_CLASSES)}"
            )
        class_ids.append(COCO_CLASSES[name])

    # import lambat: mode --help / validasi argumen tetap cepat tanpa torch
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            f"dependency belum terpasang ({exc}). Jalankan: uv sync (lihat README)."
        ) from exc

    source, is_live = parse_source(args.source)
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"gagal membuka source: {args.source}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if width <= 0 or height <= 0:
        raise SystemExit("resolusi frame tidak terbaca")

    model = YOLO(args.model)
    writer = None
    if args.record:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        out_fps = max(1.0, src_fps / max(args.stride, 1))
        # avc1 jauh lebih kecil dari mp4v; fallback kalau codec tidak tersedia
        for codec in ("avc1", "mp4v"):
            candidate = cv2.VideoWriter(
                args.record,
                cv2.VideoWriter_fourcc(*codec),
                out_fps,
                (width, height),
            )
            if candidate.isOpened():
                writer = candidate
                break
            candidate.release()
        if writer is None:
            raise SystemExit(f"gagal membuat video writer: {args.record}")

    out_path = None
    out_stream = sys.stdout
    if args.out != "-":
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_stream = out_path.open("w", encoding="utf-8")

    stats = Stats(args.camera_id, args.source, args, src_fps, is_live)
    stopping = {"flag": False}

    def handle_signal(signum, _frame):
        stopping["flag"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    track_kwargs = dict(
        persist=True,
        tracker=TRACKER,
        classes=class_ids,
        conf=args.conf,
        imgsz=args.imgsz,
        verbose=False,
    )
    if args.device:
        track_kwargs["device"] = args.device
    if args.half:
        track_kwargs["half"] = True

    frame_id = 0
    source_frame = 0
    reconnect_attempts = 0
    t_next = time.perf_counter()
    last_progress = 0.0
    t_start = time.perf_counter()

    try:
        while not stopping["flag"]:
            # grab() hanya memajukan demuxer; decode penuh dilakukan retrieve()
            # pada frame yang memang diproses, jadi frame terbuang tidak di-decode.
            t_grab = time.perf_counter()
            grabbed = cap.grab()
            stats.stages["grab"] += time.perf_counter() - t_grab
            if not grabbed:
                if is_live:
                    stats.read_errors += 1
                    reconnect_attempts += 1
                    if reconnect_attempts > args.reconnect:
                        print("source live mati, menyerah", file=sys.stderr)
                        break
                    if not args.quiet:
                        print(
                            f"[{args.camera_id}] read gagal, reconnect "
                            f"{reconnect_attempts}/{args.reconnect}",
                            file=sys.stderr,
                        )
                    cap.release()
                    time.sleep(min(2.0 * reconnect_attempts, 10.0))
                    cap = cv2.VideoCapture(source)
                    continue
                if args.no_loop:
                    break
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                source_frame = 0
                t_next = time.perf_counter()
                continue

            reconnect_attempts = 0
            stats.frames_read += 1
            source_frame += 1
            stride = max(args.stride, 1)
            if source_frame % stride != 0:
                continue
            t_ret = time.perf_counter()
            ok, frame = cap.retrieve()
            stats.stages["retrieve"] += time.perf_counter() - t_ret
            if not ok:
                stats.read_errors += 1
                continue
            if args.realtime and not is_live:
                t_next += stride / src_fps
                delay = t_next - time.perf_counter()
                t_pace = time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    t_next = time.perf_counter()
                stats.stages["pace"] += time.perf_counter() - t_pace
            if args.duration and (time.perf_counter() - t_start) >= args.duration:
                break

            t0 = time.perf_counter()
            result = model.track(frame, **track_kwargs)[0]
            latency_ms = (time.perf_counter() - t0) * 1000.0

            t_write = time.perf_counter()
            frame_id += 1
            detections = build_detections(result, width, height)
            message = {
                "camera_id": args.camera_id,
                "frame_ts": now_ms(),
                "frame_id": frame_id,
                "detections": detections,
                "model_version": args.model.replace(".pt", ""),
            }
            out_stream.write(json.dumps(message, separators=(",", ":")) + "\n")
            if out_path is not None:
                out_stream.flush()

            stats.observe(latency_ms, detections)

            if writer is not None:
                writer.write(result.plot())

            stats.stages["write"] += time.perf_counter() - t_write

            if not args.quiet:
                elapsed = time.perf_counter() - t_start
                if elapsed - last_progress >= 2.0:
                    last_progress = elapsed
                    print(
                        f"[{args.camera_id}] frame={frame_id} "
                        f"fps={stats.frames_processed / elapsed:.1f} "
                        f"lat={latency_ms:.0f}ms dets={len(detections)}",
                        file=sys.stderr,
                    )
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if out_path is not None:
            out_stream.close()

        summary = stats.summary()
        if args.stats_out:
            path = Path(args.stats_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        if not args.quiet:
            lat = summary["latency_ms"]
            print(
                f"[{args.camera_id}] selesai: {summary['frames_processed']} frame "
                f"dalam {summary['elapsed_s']}s ({summary['process_fps']} fps), "
                f"p50={lat['p50']}ms p95={lat['p95']}ms, "
                f"dets/frame={summary['detections_per_frame']}, "
                f"track={summary['unique_tracks']}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
