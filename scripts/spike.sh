#!/usr/bin/env bash
# Satu perintah untuk spike: jalankan worker, rekam, lalu hitung metrik.
#
#   scripts/spike.sh clip.mp4                 # CAM-01, 5 fps proses, 60 detik
#   scripts/spike.sh rtsp://localhost:8554/cam2 CAM-02 120
#
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:?pakai: spike.sh <source> [camera_id=CAM-01] [durasi_detik=60]}"
CAM="${2:-CAM-01}"
DUR="${3:-60}"
RUN_DIR="runs/${CAM,,}"
STRIDE="${STRIDE:-3}"

mkdir -p "$RUN_DIR"
echo "== worker: $CAM <- $SRC (${DUR}s, stride=$STRIDE)"

python edge/worker.py \
  --source "$SRC" \
  --camera-id "$CAM" \
  --stride "$STRIDE" \
  --duration "$DUR" \
  --out "$RUN_DIR/detections.jsonl" \
  --stats-out "$RUN_DIR/stats.json" \
  --record "$RUN_DIR/annotated.mp4" \
  ${DEVICE:+--device "$DEVICE"} \
  ${HALF:+--half}

echo "== metrik"
python edge/metrics.py "$RUN_DIR/detections.jsonl" \
  --stats "$RUN_DIR/stats.json" \
  --out-dir "$RUN_DIR" \
  --strict

echo
echo "bukti untuk slide:"
ls -lh "$RUN_DIR"
