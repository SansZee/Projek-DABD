# Metrik edge worker

_Dibuat 2026-11-08T22:25:23+07:00_

### `detections.jsonl`

| Metrik | Nilai |
|---|---|
| Pesan terbaca | 411 |
| Baris rusak / gagal validasi | 0 |
| Error validasi skema | 0 |
| Kamera | JGJ-163 |
| Model | yolo11n |
| Durasi data (dari frame_ts) | 55.77 s |
| Laju pesan | 7.37 msg/s |
| Jarak antar pesan p50 / p95 | 114.0 / 125.0 ms |
| frame_ts tidak monoton | 0 |
| Deteksi total | 1872 |
| Deteksi per frame (mean / median) | 4.55 / 5 |
| Frame kosong | 11 |
| Track unik | 36 |
| Track berumur <= 1 frame | 11 (30.6%) |
| Deteksi per kelas | bus: 9, car: 1344, motorcycle: 1, person: 515, truck: 3 |

| Kamera | Proses FPS | Frame | Latensi p50 / p95 / max (ms) |
|---|---|---|---|
| JGJ-163 | 5.97 | 411 | 17.01 / 39.1 / 125.18 |
