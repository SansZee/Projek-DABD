#!/usr/bin/env python3
"""Generator `detections.frames` sintetis.

Dipakai untuk dua hal:
  1. bukti teknis P1 tanpa GPU (kontrak data + dashboard bisa diuji),
  2. uji pipeline Kafka/Flink tanpa video (lihat semua pembaca harus diberi
     label "SIMULASI/SINTETIS" sesuai proposal bagian 12.4).

Contoh:
    python edge/fake_detections.py --duration 20 --fps 5
    python edge/fake_detections.py --cameras CAM-01,CAM-02 --duration 60 --out runs
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CLASS_MIX = [
    ("motorcycle", 0.45, 0.030, 0.060),
    ("car", 0.35, 0.070, 0.120),
    ("bus", 0.08, 0.120, 0.220),
    ("truck", 0.07, 0.110, 0.200),
    ("person", 0.05, 0.020, 0.050),
]


def pick_class(rng: random.Random):
    x = rng.random()
    acc = 0.0
    for name, weight, w, h in CLASS_MIX:
        acc += weight
        if x <= acc:
            return name, w, h
    return CLASS_MIX[-1][0], CLASS_MIX[-1][1], CLASS_MIX[-1][2]


class Vehicle:
    def __init__(self, rng: random.Random, track_id: int) -> None:
        self.cls, self.w, self.h = pick_class(rng)
        self.track_id = track_id
        self.direction = -1 if rng.random() < 0.15 else 1  # 15% melawan arus
        self.x = rng.uniform(0.0, 0.85)
        self.y = rng.uniform(0.35, 0.90)
        self.speed = rng.uniform(0.004, 0.012) * self.direction
        self.conf = rng.uniform(0.55, 0.95)

    def step(self) -> None:
        self.x += self.speed
        if self.x > 1.0:
            self.x = -self.w
        elif self.x < -self.w:
            self.x = 1.0

    def to_detection(self) -> dict:
        # geser ke dalam frame tanpa mengubah ukuran, supaya x1 < x2 selalu benar
        x1 = min(max(self.x, 0.0), max(0.0, 1.0 - self.w))
        y1 = min(max(self.y, 0.0), max(0.0, 1.0 - self.h))
        x2 = x1 + self.w
        y2 = y1 + self.h
        return {
            "track_id": self.track_id,
            "cls": self.cls,
            "bbox": [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)],
            "conf": round(self.conf, 3),
        }


def stream_camera(
    camera_id: str,
    duration: float,
    fps: float,
    vehicles: int,
    seed: int,
    emit,
) -> int:
    rng = random.Random(seed)
    fleet = [Vehicle(rng, track_id=100 + i) for i in range(vehicles)]
    interval = 1.0 / max(fps, 0.1)
    frame_id = 0
    t_end = time.perf_counter() + duration if duration else None
    next_tick = time.perf_counter()

    while t_end is None or time.perf_counter() < t_end:
        frame_id += 1
        active = [v for v in fleet if rng.random() > 0.03]
        dets = [v.to_detection() for v in active]
        message = {
            "camera_id": camera_id,
            "frame_ts": int(time.time() * 1000),
            "frame_id": frame_id,
            "detections": dets,
            "model_version": "synthetic-v1",
        }
        emit(message)

        for v in fleet:
            v.step()
        # rotasi track_id sesekali, meniru perilaku tracker nyata
        if rng.random() < 0.05 and fleet:
            victim = rng.choice(fleet)
            victim.track_id += 1000
            victim.cls, victim.w, victim.h = pick_class(rng)

        next_tick += interval
        sleep_for = next_tick - time.perf_counter()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_tick = time.perf_counter()
    return frame_id


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generator detections.frames sintetis")
    p.add_argument("--cameras", default="CAM-01", help="ID kamera dipisah koma")
    p.add_argument("--fps", type=float, default=5.0, help="pesan per detik per kamera")
    p.add_argument("--duration", type=float, default=20.0, help="detik (0 = tanpa batas)")
    p.add_argument("--vehicles", type=int, default=12, help="kendaraan per kamera")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="-", help="direktori output atau '-' untuk stdout")
    args = p.parse_args(argv)

    cameras = [c.strip() for c in args.cameras.split(",") if c.strip()]
    if args.out == "-":
        def emit(msg: dict) -> None:
            sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    else:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        handles = {
            cam: (out_dir / f"{cam.lower()}-synthetic.jsonl").open("w", encoding="utf-8")
            for cam in cameras
        }

        def emit_factory(cam):
            def _emit(msg: dict) -> None:
                handles[cam].write(json.dumps(msg, separators=(",", ":")) + "\n")

            return _emit

    started = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    total = 0
    if args.out == "-":
        for cam in cameras:
            total += stream_camera(cam, args.duration, args.fps, args.vehicles, args.seed, emit)
    else:
        import threading

        threads = []
        for idx, cam in enumerate(cameras):
            t = threading.Thread(
                target=stream_camera,
                args=(cam, args.duration, args.fps, args.vehicles, args.seed + idx, emit_factory(cam)),
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        for fh in handles.values():
            fh.close()

    print(
        f"{total or args.duration * args.fps * len(cameras):.0f} pesan sintetis "
        f"({len(cameras)} kamera, mulai {started})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
