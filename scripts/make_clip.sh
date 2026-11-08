#!/usr/bin/env bash
# Potong klip uji dari video sendiri, maksimal 720p, H.264 agar aman untuk RTSP.
#
#   scripts/make_clip.sh video.mp4                 # 60 detik dari awal -> clip.mp4
#   scripts/make_clip.sh video.mp4 00:03:10 90     # mulai 3:10 selama 90 detik
#
set -euo pipefail

SRC="${1:?pakai: make_clip.sh <video> [start=00:00:00] [durasi_detik=60] [output=clip.mp4]}"
START="${2:-00:00:00}"
DUR="${3:-60}"
OUT="${4:-clip.mp4}"

ffmpeg -hide_banner -loglevel warning -y \
  -ss "$START" -i "$SRC" -t "$DUR" \
  -vf "scale='min(1280,iw)':-2,fps=15" \
  -c:v libx264 -preset veryfast -crf 23 -an \
  -movflags +faststart "$OUT"

echo "siap: $OUT"
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,r_frame_rate,nb_frames,duration \
  -of default=noprint_wrappers=1 "$OUT"
