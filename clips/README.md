# Bukti rekaman CCTV — malam 8 Nov + siang 9 Nov 2026

Klip ini bukti progres Tugas 2 (Traffic Violation Mesh), **bukan** data latih dan **bukan**
putusan pelanggaran. Semua keluaran berlabel **indikasi** dan hanya dipakai internal
kelompok untuk laporan/presentasi.

**Sumber:** CCTV ATCS Kota Yogyakarta — <https://cctv.jogjakota.go.id/>
Daftar kamera diambil dari `GET /home/getdata` (header `X-Requested-With: XMLHttpRequest`).
Rekaman malam: 8 November 2026 pukul 22:22 WIB. Rekaman siang: 9 November 2026 pukul
08:21 WIB. Stream: `https://cctvjss.jogjakota.go.id/atcs/<slug>.stream/playlist.m3u8`.

## Isi folder

| Berkas | Isi |
|---|---|
| `JGJ-163-night-raw.mp4` | Simpang Pingit 1 — video asli, 60 dtk, H.264 stream copy |
| `JGJ-163-night-annotated.mp4` | sama, dengan kotak + `id` + kelas + confidence dari YOLO11n/ByteTrack |
| `JGJ-163-night-preview.gif` | 8 dtk, 640 px, untuk slide |
| `JGJ-163-day-*` | versi siang, 90 dtk (raw, annotated, preview) |
| `JGJ-157-night-*` | Simpang Jogokariyan V. Selatan — zebra crosswalk + jalur sepeda |
| `JGJ-157-day-*` | versi siang, 90 dtk |
| `JGJ-159-night-*` | Simpang APPI, Jl. Mentri Supeno — kandidat uji arah arus |
| `JGJ-159-day-*` | versi siang, 90 dtk |
| `*-frame.jpg` | frame terpilih per kamera/label (malam: tersibuk; siang: momen yang cocok dengan raw) |

Semua kamera **statis** (bukan PTZ), dipilih setelah membandingkan frame dari 10 kamera;
kamera yang sepi/tidak menampilkan marka jalan dibuang.

## Angka hasil rekaman

| Kamera | Label | Aturan | Frame | fps steady | p50 | p95 | Deteksi/frame | Track | Error |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| JGJ-163 | malam | `NO_STOP_ZONE` | 411 | 6,21 | 17,0 ms | 39,1 ms | 4,55 | 36 | 0 |
| JGJ-157 | malam | `ZEBRA_STOP` | 414 | 7,14 | 26,5 ms | 27,4 ms | 1,46 | 20 | 0 |
| JGJ-159 | malam | `WRONG_WAY` | 411 | 6,91 | 15,3 ms | 27,8 ms | 1,16 | 44 | 0 |
| JGJ-163 | siang | `NO_STOP_ZONE` | 590 | 7,53 | 20,2 ms | 50,8 ms | 6,54 | 107 | 0 |
| JGJ-157 | siang | `ZEBRA_STOP` | 561 | 6,79 | 21,8 ms | 45,1 ms | 7,63 | 132 | 0 |
| JGJ-159 | siang | `WRONG_WAY` | 497 | 6,32 | 20,3 ms | 46,6 ms | 2,75 | 81 | 0 |

Sumber 1280×720 @ 25 fps, `--stride 3` (target 8,3 fps), GPU RTX 4050 Laptop.
Malam: 60 dtk; siang: 90 dtk. Semua 0 read error dan 0 error skema.

Deteksi per kelas:

- JGJ-163 malam: car 1.344, person 515, bus 9, truck 3, motorcycle 1
- JGJ-163 siang: car 1.996, motorcycle 712, person 1.093, truck 56, bus 4
- JGJ-157 malam: car 468, motorcycle 87, person 48, truck 1
- JGJ-157 siang: car 1.681, motorcycle 1.033, person 1.520, truck 42, bus 4
- JGJ-159 malam: car 311, person 164, motorcycle 2
- JGJ-159 siang: car 1.118, motorcycle 99, person 105, truck 40, bus 6

## Siang vs malam (kamera yang sama)

| Metrik | JGJ-163 | JGJ-157 | JGJ-159 |
|---|---|---|---|
| Deteksi/frame malam → siang | 4,55 → **6,54** (+44%) | 1,46 → **7,63** (5,2×) | 1,16 → **2,75** (2,4×) |
| Frame kosong malam → siang | 2,7% → **0%** | 7,7% → **0%** | 36,7% → **8,7%** |
| Track ≤ 1 frame malam → siang | 30,6% → **6,5%** | 20,0% → **17,4%** | 29,5% → **16,0%** |

Kesimpulan singkat: cahaya siang menaikkan jumlah deteksi 1,4–5,2× dan menurunkan track
yang cuma muncul satu frame. fps tidak berubah banyak (6,3–7,5) karena GPU sudah jadi
batas pada stride 3. JGJ-159 tetap paling rendah karena objeknya kecil/jauh dari kamera.

Catatan jujur:

- Rekaman malam JGJ-163 dan JGJ-159 berjalan bersamaan (22:22:51–22:24:00); JGJ-157
  terekam pada 22:27:14–22:28:15, jadi fps malam JGJ-157 tidak sepenuhnya sebanding.
- Rekaman siang ketiganya mulai serentak 08:21:55 dan selesai ±08:23:29.
- Deteksi/frame dan persentase tetap bisa dibandingkan meski durasi 60 vs 90 detik.

## Cara mereproduksi

```bash
cd ~/Projects/kuliah/dabd/Projek-DABD
scripts/record_cctv.sh --label night --duration 60          # 3 kamera
scripts/record_cctv.sh --label day --duration 90             # pagi
scripts/record_cctv.sh --cams JGJ-163 --duration 60          # satu kamera saja
scripts/record_cctv.sh --reuse                               # olah ulang rekaman yang ada
```

Alur skrip: HLS Wowza → MediaMTX (`rtsp://localhost:8654/<id>`) → worker
`--stride 3 --duration N --record` (anotasi) + ffmpeg stream copy (raw) → re-encode H.264
CRF 28 + banner atribusi → GIF. Bukti mentah per label/kamera ada di
`runs/evidence/<label>/<ID>/` (`detections.jsonl`, `stats.json`, `metrics.md`).

## Batasan etis (sesuai proposal bagian 13)

- Tidak ada pembacaan plat nomor, tidak ada pengenalan wajah; resolusi 720p tidak dipakai
  untuk memperbesar wajah/plat.
- Klip **tidak untuk dipublikasikan ulang**; hanya untuk laporan dan presentasi kelas.
- Simpan maksimal sampai penilaian selesai (± 30 hari), lalu hapus folder ini.
- Atribusi wajib bila klip ditampilkan: "CCTV ATCS Kota Yogyakarta, 8–9 Nov 2026".
- Tiga kamera ini simulasi aturan zona; tidak ada klaim bahwa kendaraan di dalamnya
  benar-benar melanggar.
