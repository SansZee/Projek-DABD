# Tugas 2 — Traffic Violation Mesh (edge spike)

Bagian edge dari proposal *Traffic Violation Mesh*: video → YOLO + ByteTrack → **satu pesan
`detections.frames` per frame**, persis kontrak di proposal bagian 7.1. Belum ada Kafka,
Flink, atau ClickHouse di fase ini.

Tujuan spike: mendapatkan angka nyata (fps, latensi, stabilitas `track_id`, jumlah deteksi)
sebelum menyentuh Kafka, sekaligus memvalidasi bahwa kontrak data bisa dihasilkan dari
video sendiri maupun RTSP.

Semua data sintetis wajib diberi label **SIMULASI/SINTETIS** (proposal bagian 12.4).

## Struktur

```
tugas-2/
├── edge/
│   ├── worker.py                     # video/RTSP -> YOLO+ByteTrack -> JSONL
│   ├── metrics.py                    # validasi kontrak + hitung metrik
│   ├── fake_detections.py            # generator sintetis (tanpa GPU)
│   └── schema/detections.frames.json # JSON Schema kontrak
├── scripts/
│   ├── make_clip.sh                  # potong klip uji 720p
│   ├── rtsp_cameras.sh               # MediaMTX + N kamera virtual
│   ├── record_cctv.sh                # rekam CCTV Jogja -> klip + anotasi + metrik
│   ├── cctv_cameras.tsv              # daftar kamera CCTV yang dipakai
│   └── spike.sh                      # worker + metrik dalam satu perintah
├── clips/                            # klip bukti (video gitignored, README ikut)
├── rules.example.json                # zona + vektor arah (bahan rules.config Flink)
└── runs/                             # output (gitignored)
```

## Instalasi

Butuh Python 3.11–3.12 (torch belum mendukung 3.14). `uv` akan mengunduh interpreter-nya.

```bash
cd tugas-2
uv sync                      # ~2 GB pertama kali (torch + opencv + ultralytics)
```

Tanpa `uv`:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install "ultralytics>=8.3" "opencv-python>=4.10"
```

Cek GPU:

```bash
uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

## 1. Bukti kontrak data tanpa GPU (2 menit)

Cocok untuk P1 besok kalau inferensi belum siap.

```bash
uv run python edge/fake_detections.py --cameras CAM-01,CAM-02 --duration 20 --out runs
uv run python edge/metrics.py "runs/*-synthetic.jsonl" --out-dir runs/metrics --strict
```

Output: `runs/metrics/metrics.md` berisi tabel siap tempel ke slide, dan `metrics.json`
untuk lampiran laporan.

## 2. Video file (spike inti)

```bash
scripts/make_clip.sh ~/Videos/rekaman.mp4 00:00:00 60 clip.mp4
uv run python edge/worker.py --source clip.mp4 --stride 3 \
  --out runs/cam01.jsonl --stats-out runs/cam01.stats.json --record runs/cam01.mp4
uv run python edge/metrics.py runs/cam01.jsonl --stats runs/cam01.stats.json --out-dir runs/metrics --strict
```

Atau satu perintah:

```bash
scripts/spike.sh clip.mp4 CAM-01 60
```

`--stride 3` berarti 1 dari 3 frame diproses (≈5–8 fps dari sumber 15 fps).

## 3. Kamera virtual via RTSP

```bash
scripts/rtsp_cameras.sh clip.mp4 3          # rtsp://localhost:8554/cam1..cam3
uv run python edge/worker.py --source rtsp://localhost:8554/cam1 --camera-id CAM-01 \
  --out runs/cam01.jsonl --stats-out runs/cam01.stats.json
```

Uji 3 kamera paralel (beban CPU/GPU per kamera):

```bash
for i in 1 2 3; do
  uv run python edge/worker.py --source "rtsp://localhost:8554/cam$i" \
    --camera-id "CAM-0$i" --out "runs/cam0$i.jsonl" \
    --stats-out "runs/cam0$i.stats.json" --quiet &
done; wait
uv run python edge/metrics.py "runs/cam0?.jsonl" --stats "runs/cam0?.stats.json" --out-dir runs/metrics
```

## 4. Kamera CCTV publik (Kota Yogyakarta)

Sumber nyata: CCTV ATCS Kota Yogyakarta. Daftar kamera diambil dari API
`GET https://cctv.jogjakota.go.id/home/getdata` — **wajib** header
`X-Requested-With: XMLHttpRequest`, kalau tidak dibalas "No direct script access allowed".
Hasilnya 153 kamera; 118 statis, 51 di antaranya simpang lampu merah. Kamera PTZ (35 buah)
tidak dipakai karena berputar dan membuat poligon zona tidak valid.

```bash
scripts/record_cctv.sh --label night --duration 60    # 3 kamera sekaligus
scripts/record_cctv.sh --cams JGJ-163 --duration 60   # satu kamera
scripts/record_cctv.sh --reuse                        # olah ulang rekaman yang sudah ada
```

Skrip menarik HLS Wowza → MediaMTX (`rtsp://localhost:8654/<id>`) → worker `--record`
(anotasi) + ffmpeg stream copy (video asli), lalu re-encode H.264 CRF 28 dengan banner
atribusi dan membuat GIF pratinjau. Detail klip dan catatan etis: `clips/README.md`.

**Jangan tarik HLS langsung di worker.** Terukur: `VideoCapture` butuh 4,2 detik untuk
membuka HLS dan sesudah itu mengeluarkan burst dari buffer (128 fps), bukan real-time.
Lewat MediaMTX hasilnya stabil.

## Metrik yang harus dicatat (P2)

| Metrik | Sumber | Target awal |
|---|---|---|
| FPS proses | `process_fps` di `stats.json` | ≥ 15 (3 kamera × 5 fps) |
| Latensi per frame p50/p95 | `latency_ms` di `stats.json` | p95 < 200 ms (CPU), < 50 ms (GPU) |
| Deteksi per frame | `metrics.md` | 8–20 |
| Track unik & track ≤ 1 frame | `metrics.md` | churn rendah (< 20%) |
| Frame kosong | `metrics.md` | < 10% |
| Error validasi skema | `metrics.md` | 0 |
| FPS 3 kamera paralel | `metrics.md` (agregat) | ≥ 5 fps per kamera |

## Hasil pengukuran awal (8 Nov 2026)

Klip uji: 1280×720, 15 fps, 6 detik di-loop. Model `yolo11n.pt`, `imgsz=640`, `stride=3`,
GPU RTX 4050 Laptop 6 GB, torch 2.15 + CUDA 13.2.

| Skenario | FPS proses | Latensi p50 | Latensi p95 | Deteksi/frame | Error skema |
|---|---|---|---|---|---|
| 1 kamera, file (30 detik) | 4,91 (target 5) | 27,3 ms | 28,4 ms | 4,6 | 0 |
| 3 kamera, RTSP paralel (file) | 4,9 / 5,2 / 5,2 | 14–17 ms | 68–82 ms | 4,5 | 0 |
| 3 kamera CCTV Jogja, malam | 6,2 / 7,1 / 6,9 | 15–27 ms | 27–39 ms | 1,2–4,6 | 0 |

Baris terakhir: 60 detik, 3 kamera nyata (JGJ-163, JGJ-157, JGJ-159), 1280×720 @ 25 fps,
stride 3. Rekaman + angka lengkap di `clips/README.md` dan `runs/evidence/`.

Catatan jujur untuk laporan:

- `realtime_factor` 0,98 pada file: pacing 5 fps tercapai; sisa 2% adalah overhead tidur
  scheduler.
- p95 RTSP lebih tinggi dari file (68–82 ms) karena jitter jaringan, bukan inferensi.
- Warmup CUDA ±2,4 detik per proses; karena itu uji < 10 detik tidak mewakili throughput.
- Klip uji masih foto statis yang di-pan, jadi angka deteksi/frame belum mewakili lalu lintas
  nyata; uji dengan rekaman jalan menyusul di Fase 0.
- Dua proses uji awal sempat menulis video anotasi 973 MB karena lupa `--duration`
  (loop tanpa henti). Selalu set durasi saat merekam.

Angka mentah: `runs/metrics/metrics.md` dan `runs/metrics/metrics.json`.

## Pemetaan ke proposal

- **7.1** → `edge/schema/detections.frames.json` + output `worker.py`.
- **12.4** → `scripts/rtsp_cameras.sh` (kamera virtual dari satu file, offset waktu).
- **5** → `rules.example.json` (polygon zona, `allowed_vector`, ambang `max_cos`,
  `stop_after_s`, `parked_after_s`) yang nanti jadi isi topic `rules.config`.
- **11** → `metrics.md` jadi bahan panel kesehatan kamera (fps, deteksi/frame, frame kosong).

## Catatan

- `frame_ts` memakai jam dinding (epoch ms), bukan timestamp file, supaya event-time Flink
  nanti benar saat video di-loop.
- `track_id` sementara per sesi kamera; restart worker mengulang ID. Ini sudah dinyatakan
  di proposal bagian 7.1.
- Ultralytics berlisensi AGPL-3.0 — aman untuk tugas, dicatat jika jadi produk.
- Jangan pakai Python 3.14: belum ada wheel torch. `uv` memilih 3.12 otomatis.

## Berikutnya (P2)

Output `runs/*.jsonl` tinggal diganti sink Kafka: satu baris JSON = satu value Avro/JSON,
key = `camera_id`. Skema Kafka memakai ulang `schema/detections.frames.json`.
