#!/usr/bin/env bash
# MediaMTX + N "kamera virtual" dari satu file video (proposal bagian 12.4).
# Setiap kamera memutar klip yang sama dengan offset waktu berbeda.
#
#   scripts/rtsp_cameras.sh clip.mp4 3
#   -> rtsp://localhost:8554/cam1 .. cam3
#
# Hentikan dengan: pkill -f "ffmpeg.*rtsp" ; docker rm -f mediamtx
set -euo pipefail

SRC="${1:?pakai: rtsp_cameras.sh <video> [jumlah_kamera=3]}"
N="${2:-3}"
PORT="${RTSP_PORT:-8554}"
URL_BASE="rtsp://localhost:${PORT}"

port_open() {
  (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null
}

if ! port_open; then
  docker rm -f mediamtx >/dev/null 2>&1 || true
  docker run -d --name mediamtx --rm -p "${PORT}:8554" -p 9997:9997 \
    bluenviron/mediamtx:latest >/dev/null
  echo "mediamtx mulai di port ${PORT}"
  for _ in $(seq 1 30); do
    port_open && break
    sleep 0.5
  done
else
  echo "server RTSP sudah jalan di port ${PORT}, pakai yang ada"
fi

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup INT TERM

# tunggu dulu stream siap sebelum publish berikutnya, supaya tidak saling menimpa
for i in $(seq 1 "$N"); do
  offset=$(( (i - 1) * 3 ))
  ffmpeg -hide_banner -loglevel error -re -stream_loop -1 \
    -ss "$offset" -i "$SRC" \
    -c:v libx264 -preset ultrafast -tune zerolatency -g 30 -an \
    -f rtsp -rtsp_transport tcp "${URL_BASE}/cam${i}" &
  pids+=($!)
  echo "cam${i} <- offset ${offset}s (pid ${pids[-1]})"
  # beri waktu publisher mendaftar ke path sebelum lanjut
  for _ in $(seq 1 20); do
    ffprobe -v error -timeout 1000000 -select_streams v:0 \
      -show_entries stream=codec_name -of csv=p=0 \
      "${URL_BASE}/cam${i}" >/dev/null 2>&1 && break
    sleep 0.5
  done
done

echo "siap: ${URL_BASE}/cam1 .. cam${N}  (Ctrl+C untuk berhenti)"
wait
