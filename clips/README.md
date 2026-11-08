# Bukti rekaman CCTV — malam 8 November 2026

Klip ini bukti progres Tugas 2 (Traffic Violation Mesh), **bukan** data latih dan **bukan**
putusan pelanggaran. Semua keluaran berlabel **indikasi** dan hanya dipakai internal
kelompok untuk laporan/presentasi.

**Sumber:** CCTV ATCS Kota Yogyakarta — <https://cctv.jogjakota.go.id/>
Daftar kamera diambil dari `GET /home/getdata` (header `X-Requested-With: XMLHttpRequest`),
diakses 8 November 2026 pukul 22:22 WIB. Stream: `https://cctvjss.jogjakota.go.id/atcs/<slug>.stream/playlist.m3u8`.

## Isi folder

| Berkas | Isi |
|---|---|
| `JGJ-163-night-raw.mp4` | Simpang Pingit 1, Jl. Tentara Pelajar — video asli, 60 dtk, H.264 stream copy |
| `JGJ-163-night-annotated.mp4` | sama, dengan kotak + `id` + kelas + confidence dari YOLO11n/ByteTrack |
| `JGJ-163-night-preview.gif` | 8 dtk, 640 px, untuk slide |
| `JGJ-157-night-*` | Simpang Jogokariyan V. Selatan — zebra crosswalk + jalur sepeda |
| `JGJ-159-night-*` | Simpang APPI, Jl. Mentri Supeno — jalan lurus, kandidat uji arah arus |

Semua kamera **statis** (bukan PTZ), dipilih setelah membandingkan frame malam dari 10
kamera; kamera yang sepi/tidak menampilkan marka jalan dibuang.

## Angka hasil rekaman (60 detik, 3 kamera paralel)

| Kamera | Aturan yang disiapkan | Frame diproses | fps steady | p50 | p95 | Deteksi/frame | Track unik | Error skema |
|---|---|---|---|---|---|---|---|---|
| JGJ-163 | `NO_STOP_ZONE` | 411 | 6,21 | 17,0 ms | 39,1 ms | 4,55 | 36 | 0 |
| JGJ-157 | `ZEBRA_STOP` | 414 | 7,14 | 26,5 ms | 27,4 ms | 1,46 | 20 | 0 |
| JGJ-159 | `WRONG_WAY` | 411 | 6,91 | 15,3 ms | 27,8 ms | 1,16 | 44 | 0 |

Sumber 1280×720 @ 25 fps, `--stride 3` (target 8,3 fps), GPU RTX 4050 Laptop.
Angka di atas diukur saat **tiga worker jalan bersamaan**, jadi lebih rendah dari uji satu
kamera (8,6 fps). Tidak ada read error di ketiga kamera.

Deteksi per kelas:

- JGJ-163: car 1344, person 515, bus 9, truck 3, motorcycle 1
- JGJ-157: car 468, motorcycle 87, person 48, truck 1
- JGJ-159: car 311, person 164, motorcycle 2

Catatan jujur: pukul 22:20–22:30 arus sudah sepi, jadi `deteksi/frame` JGJ-157 dan JGJ-159
rendah. Untuk tabel siang vs malam, rekam ulang kamera yang sama pada 07:00–09:00 dengan
`--label day`.

## Cara mereproduksi

```bash
cd ~/Projects/kuliah/dabd/tugas-2
scripts/record_cctv.sh --label night --duration 60          # 3 kamera
scripts/record_cctv.sh --label day --duration 90            # besok pagi
scripts/record_cctv.sh --cams JGJ-163 --duration 60         # satu kamera saja
scripts/record_cctv.sh --reuse                              # olah ulang rekaman yang ada
```

Alur skrip: HLS Wowza → MediaMTX (`rtsp://localhost:8654/<id>`) → worker
`--stride 3 --duration 60 --record` (anotasi) + ffmpeg stream copy (raw) → re-encode H.264
CRF 28 + banner atribusi → GIF. Bukti mentah per kamera ada di `runs/evidence/<ID>/`
(`detections.jsonl`, `stats.json`, `metrics.md`).

## Batasan etis (sesuai proposal bagian 13)

- Tidak ada pembacaan plat nomor, tidak ada pengenalan wajah; resolusi 720p tidak dipakai
  untuk memperbesar wajah/plat.
- Klip **tidak untuk dipublikasikan ulang**; hanya untuk laporan dan presentasi kelas.
- Simpan maksimal sampai penilaian selesai (± 30 hari), lalu hapus folder ini.
- Atribusi wajib bila klip ditampilkan: "CCTV ATCS Kota Yogyakarta, 8 Nov 2026 22:22 WIB".
- Tiga kamera ini simulasi aturan zona; tidak ada klaim bahwa kendaraan di dalamnya
  benar-benar melanggar.
