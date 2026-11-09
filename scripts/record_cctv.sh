#!/usr/bin/env bash
# Rekam klip CCTV ATCS Kota Yogyakarta + anotasi YOLO/ByteTrack.
#
#   scripts/record_cctv.sh                        # 3 kamera, 60 detik, label "night"
#   scripts/record_cctv.sh --label day --duration 90
#   scripts/record_cctv.sh --cams JGJ-163          # satu kamera saja
#
# Alur: HLS Wowza -> MediaMTX (RTSP) -> worker (--record) + perekam raw
# Hasil:
#   clips/<ID>-<label>-annotated.mp4   kotak + track id, banner atribusi
#   clips/<ID>-<label>-raw.mp4         kondisi asli tanpa anotasi (stream copy)
#   clips/<ID>-<label>-preview.gif     8 detik untuk slide
#   runs/evidence/<label>/<ID>/        detections.jsonl, stats.json, metrics.md
set -euo pipefail
cd "$(dirname "$0")/.."

LABEL="night"
DUR=60
PORT=8654
GIF_SEC=8
CAMS=""
REUSE=0
FONT="/usr/share/fonts/noto/NotoSans-Bold.ttf"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --label) LABEL="$2"; shift 2 ;;
    --duration) DUR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --gif-sec) GIF_SEC="$2"; shift 2 ;;
    --cams) CAMS="$2"; shift 2 ;;
    --reuse) REUSE=1; shift ;;   # pakai rekaman yang sudah ada di runs/evidence
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "argumen tidak dikenal: $1" >&2; exit 2 ;;
  esac
done

BASE="rtsp://localhost:${PORT}"
CLIPS="clips"
EVID="runs/evidence/${LABEL}"
START_STAMP="$(date '+%d %b %Y %H:%M WIB')"
mkdir -p "$CLIPS" "$EVID" runs/logs

# --- pilih kamera -----------------------------------------------------------
declare -a IDS NAMES SLUGS RULES
while IFS=$'\t' read -r id name slug rule; do
  [[ -z "${id:-}" || "$id" == \#* ]] && continue
  if [[ -n "$CAMS" && ",$CAMS," != *",$id,"* ]]; then continue; fi
  IDS+=("$id"); NAMES+=("$name"); SLUGS+=("$slug"); RULES+=("$rule")
done < scripts/cctv_cameras.tsv
[[ ${#IDS[@]} -gt 0 ]] || { echo "tidak ada kamera dipilih" >&2; exit 1; }
echo "== ${#IDS[@]} kamera, ${DUR}s, label '${LABEL}', mulai ${START_STAMP}"

# --- MediaMTX ---------------------------------------------------------------
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; }
if [[ $REUSE -eq 0 ]] && ! port_open; then
  docker rm -f mtx-cctv >/dev/null 2>&1 || true
  docker run -d --name mtx-cctv --rm -p "${PORT}:8554" \
    bluenviron/mediamtx:latest >/dev/null
  for _ in $(seq 1 40); do port_open && break; sleep 0.5; done
  port_open || { echo "MediaMTX gagal jalan di ${PORT}" >&2; exit 1; }
  echo "   MediaMTX siap di ${PORT}"
fi

declare -a LOOP_PIDS WORKER_PIDS RAW_PIDS
cleanup() {
  for p in "${LOOP_PIDS[@]:-}" "${WORKER_PIDS[@]:-}" "${RAW_PIDS[@]:-}"; do
    [[ -n "$p" ]] && kill "$p" 2>/dev/null || true
  done
  for f in runs/logs/ffpid.*; do
    [[ -f "$f" ]] && kill "$(cat "$f")" 2>/dev/null || true
  done
  docker rm -f mtx-cctv >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# HLS -> RTSP, diulang kalau playlist mati
run_publisher() {
  local path="$1" url="$2"
  while :; do
    ffmpeg -hide_banner -loglevel error -re -i "$url" -c copy \
      -f rtsp -rtsp_transport tcp "${BASE}/${path}" \
      >>"runs/logs/publish-${path}.log" 2>&1 &
    echo $! > "runs/logs/ffpid.${path}"
    wait $! || true
    echo "   [${path}] publisher berhenti, ulang 3 dtk" >&2
    sleep 3
  done
}

# --- publish + tunggu stream siap ------------------------------------------
if [[ $REUSE -eq 0 ]]; then
for i in "${!IDS[@]}"; do
  path="${IDS[$i],,}"                       # jgj-163
  url="https://cctvjss.jogjakota.go.id/atcs/${SLUGS[$i]}.stream/playlist.m3u8"
  run_publisher "$path" "$url" &
  LOOP_PIDS+=($!)
  echo "   ${IDS[$i]} <- ${url}"
done

for i in "${!IDS[@]}"; do
  path="${IDS[$i],,}"
  ok=0
  for _ in $(seq 1 40); do
    if ffprobe -v error -timeout 3000000 -select_streams v:0 \
        -show_entries stream=width -of csv=p=0 "${BASE}/${path}" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
  done
  [[ $ok -eq 1 ]] || { echo "stream ${path} tidak siap" >&2; exit 1; }
  echo "   ${IDS[$i]} siap di ${BASE}/${path}"
done

# --- rekam serentak: raw (stream copy) + worker (anotasi) -------------------
for i in "${!IDS[@]}"; do
  id="${IDS[$i]}"; path="${id,,}"
  ev="$EVID/$id"; mkdir -p "$ev"
  ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i "${BASE}/${path}" \
    -t "$((DUR + 3))" -c copy -movflags +faststart "$ev/raw-transfer.mp4" \
    >>"runs/logs/raw-${path}.log" 2>&1 &
  RAW_PIDS+=($!)
done

for i in "${!IDS[@]}"; do
  id="${IDS[$i]}"; path="${id,,}"
  ev="$EVID/$id"; mkdir -p "$ev"
  date '+%d %b %Y %H:%M WIB' > "$ev/recorded_at.txt"
  ( uv run python edge/worker.py --source "${BASE}/${path}" --camera-id "$id" \
      --stride 3 --duration "$DUR" --out "$ev/detections.jsonl" \
      --stats-out "$ev/stats.json" --record "$ev/annotated-raw.mp4" --quiet \
      >"runs/logs/worker-${path}.log" 2>&1 ) &
  WORKER_PIDS+=($!)
done

echo "== merekam ${DUR}s ..."
for p in "${RAW_PIDS[@]}"; do wait "$p" || true; done
for p in "${WORKER_PIDS[@]}"; do wait "$p" || true; done
echo "== rekaman selesai, menyusun klip"
fi

# --- olah hasil -------------------------------------------------------------
for i in "${!IDS[@]}"; do
  id="${IDS[$i]}"; name="${NAMES[$i]}"; ev="$EVID/$id"
  [[ -f "$ev/annotated-raw.mp4" ]] || { echo "!! ${id}: tidak ada video anotasi" >&2; continue; }

  # banner atribusi + label "indikasi" dibakar ke video (proposal 12.4 & 13)
  # pakai textfile= supaya koma/titik dua tidak bentrok dengan parser filter ffmpeg
  banner="CCTV ATCS KOTA YOGYAKARTA - ${id} ${name} - INDIKASI"
  stamp="$(cat "$ev/recorded_at.txt" 2>/dev/null || echo "$START_STAMP")"
  sub="Rekaman ${stamp} - ${DUR} dtk - simulasi aturan zona, bukan putusan"
  printf '%s' "$banner" > "runs/logs/banner.txt"
  printf '%s' "$sub" > "runs/logs/sub.txt"

  ffmpeg -hide_banner -loglevel error -y -i "$ev/annotated-raw.mp4" \
    -vf "drawtext=fontfile=${FONT}:textfile=runs/logs/banner.txt:x=16:y=14:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8,drawtext=fontfile=${FONT}:textfile=runs/logs/sub.txt:x=16:y=h-40:fontsize=17:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=7" \
    -c:v libx264 -preset veryfast -crf 28 -pix_fmt yuv420p -an -movflags +faststart \
    "$CLIPS/${id}-${LABEL}-annotated.mp4"

  if [[ -f "$ev/raw-transfer.mp4" ]]; then
    mv "$ev/raw-transfer.mp4" "$CLIPS/${id}-${LABEL}-raw.mp4"
  fi

  ffmpeg -hide_banner -loglevel error -y -ss 2 -t "$GIF_SEC" \
    -i "$CLIPS/${id}-${LABEL}-annotated.mp4" \
    -vf "fps=6,scale=640:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer" \
    "$CLIPS/${id}-${LABEL}-preview.gif"

  rm -f "$ev/annotated-raw.mp4"

  uv run python edge/metrics.py "$ev/detections.jsonl" --stats "$ev/stats.json" \
    --out-dir "$ev" --strict >/dev/null 2>&1 || echo "!! ${id}: metrik gagal"

  python3 - "$ev" "$id" <<'PY' || true
import json, sys, os
ev, cam = sys.argv[1], sys.argv[2]
s = json.load(open(os.path.join(ev, 'stats.json')))
a = json.load(open(os.path.join(ev, 'metrics.json')))['files'][0]
print(f"{cam:8} {s['steady_process_fps']:5.2f} fps  p50 {s['latency_ms']['p50']:5.1f} ms  "
      f"p95 {s['latency_ms']['p95']:5.1f} ms  det/f {s['detections_per_frame']:4.2f}  "
      f"track {s['unique_tracks']:3}  err {a['validation_error_count']}  "
      f"frames {s['frames_processed']}")
PY
done

echo
echo "== klip di ${CLIPS}/"
ls -lh "$CLIPS"/*-"${LABEL}"-* 2>/dev/null | awk '{print "   ", $5, $9}'
echo "== bukti di ${EVID}/<ID>/"
