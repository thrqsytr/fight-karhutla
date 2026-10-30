# Fight Karhutla: whitepaper untuk umum

Versi 30 Oktober 2026. Situs: https://karhutla.thariq.xyz. Kode sumber terbuka: https://github.com/thrqsytr/fight-karhutla (lisensi MIT).

> Fight Karhutla bukan kanal darurat resmi dan tidak menggantikan BPBD, Manggala Agni, atau pemadam kebakaran. Bila ada bahaya bagi orang, hubungi 112 lebih dulu.

## 1. Ringkasan

Fight Karhutla mengubah sinyal satelit dan laporan warga menjadi insiden kebakaran hutan dan lahan yang disertai bukti dan rekomendasi pemadaman, untuk diputuskan manusia. Aplikasi ini mendeteksi lahan yang sedang terbakar dan mengukur lahan yang baru terbakar.

Setiap insiden melewati lima tahap: deteksi, bukti yang dikumpulkan kode, analisis oleh model AI Meta, pemeriksaan oleh gerbang bukti, dan keputusan verifikator manusia. AI menyusun draf; kode memeriksa setiap angka; manusia yang memutuskan. Tidak ada pesan atau perintah yang dikirim otomatis ke siapa pun.

Aplikasi ini dibuat untuk Meta Global AI Developer Hackathon 2026, dimulai 26 Oktober 2026, dan masih berupa prototipe.

## 2. Masalahnya

Setiap musim kemarau, satelit menangkap ribuan titik panas di Indonesia; dalam pengambilan data kami sendiri, sekitar 3.000 titik dalam dua hari. Titik panas adalah anomali panas, bukan bukti kebakaran: perkebunan, suar gas, tambang, dan tanah yang panas juga memicunya. Laporan warga datang sebagai foto, pesan suara, dan teks yang tidak terstruktur.

Orang yang harus memutuskan ke mana regu dikirim menerima banjir sinyal tanpa jejak bukti dan tanpa ukuran berapa luas yang sudah terbakar. Asap dari kebakaran ini menurunkan kualitas udara di Indonesia dan, pada sebagian musim, di negara tetangga, sehingga keputusan yang cepat dan berdasar bukti penting melampaui lahan yang terbakar itu sendiri.

## 3. Untuk siapa

| Pengguna | Yang dilakukan |
| --- | --- |
| Warga di dekat lokasi | Melapor dengan foto, pesan suara, teks, dan lokasi lewat halaman Lapor atau WhatsApp; menandai titik yang bukan kebakaran lahan |
| Verifikator, misalnya petugas posko | Memeriksa bukti, menyetujui atau menolak rekomendasi AI, menyetujui survei drone, memutuskan pengajuan sumber panas tetap |
| Pengambil keputusan dan regu darat | Memakai insiden yang sudah diverifikasi dan rekomendasi pemadaman sebagai bahan, bukan perintah |
| Publik | Melihat peta insiden, bukti satelit, dan contoh laporan yang dipilih verifikator |

## 4. Cara kerja dalam lima tahap

1. **Deteksi.** Titik panas NASA FIRMS (VIIRS dan MODIS) diambil terjadwal untuk seluruh Indonesia. Titik dalam radius 2 km dan 24 jam digabung menjadi satu insiden. Laporan warga dari Lapor dan WhatsApp masuk ke insiden yang sama.
2. **Bukti, dikumpulkan kode.** Cuaca, tempat di sekitar (jalan, sumber air, permukiman, pos pemadam, kantor), dan citra satelit Sentinel-2 sebelum dan sesudah dikumpulkan oleh kode. Setiap bukti disimpan dengan nomor (EV-001, EV-002, dan seterusnya).
3. **Meta AI menganalisis dan menyusun.** Model Meta membaca bukti, foto, dan suara, lalu menulis status, ringkasan, klaim, risiko, dan tindakan yang disarankan, dalam bahasa Indonesia dan Inggris. Setiap klaim wajib menunjuk nomor bukti.
4. **Gerbang bukti, kode memeriksa AI.** Kode memeriksa setiap klaim: apakah bukti yang dirujuk ada, apakah angkanya sama dengan hasil alat, apakah luas berasal dari masker nyata, apakah lokasinya masuk akal. Klaim tanpa dukungan dibuang atau statusnya diturunkan. Kode juga menghitung rekomendasi pemadaman.
5. **Manusia memutuskan.** Setiap insiden berakhir menunggu verifikator. Setiap keputusan tercatat dan bisa dibatalkan dengan alasan.

## 5. Layanan data dan fungsinya

| Layanan | Fungsi dalam aplikasi | Bukti atau tampilan |
| --- | --- | --- |
| NASA FIRMS (VIIRS dan MODIS NRT) | Titik panas, yaitu anomali panas dari satelit; dasar setiap insiden | Bukti |
| Open-Meteo | Angin, kelembapan, dan hujan di lokasi insiden | Bukti |
| OpenStreetMap | Jalan, sungai, danau, kanal, anak sungai, permukiman, pos pemadam, kantor polisi, tentara, dan pemerintahan terdekat | Bukti |
| Overture Maps Places | Pos pemadam, kantor polisi, situs militer, dan kantor pemerintahan tambahan (sebagian besar datanya bersumber dari Meta) | Bukti |
| Copernicus Sentinel-2 (lewat Earth Search) | Citra sebelum dan sesudah; luas bekas terbakar dihitung kode dari dNBR | Bukti |
| NASA GIBS | Lapisan asap dan aerosol di peta | Tampilan saja |
| OpenFreeMap dan Esri World Imagery | Peta dasar | Tampilan saja |
| WhatsApp Cloud API | Menerima laporan warga | Jalur masuk laporan |

Layanan yang sering mati diberi cadangan. Server OpenStreetMap publik (Overpass) sering tidak menjawab, jadi aplikasi menyimpan salinan offline seluruh Indonesia (ekstrak Geofabrik 28 Oktober 2026 ditambah Overture Maps). Bila sebuah sumber gagal, buktinya dicatat "tidak diketahui", bukan "nol", dan dicoba lagi otomatis.

## 6. Peran Meta AI

| Model Meta | Tugas | Yang dijaga kode |
| --- | --- | --- |
| Muse Spark 1.3 (Meta Model API) | Agen triase: memanggil alat yang terikat pada satu insiden, membaca foto warga, menulis status, ringkasan, klaim, risiko, dan tindakan dalam dua bahasa; meninjau klaim yang bersandar pada bukti warga | Setiap klaim wajib menunjuk nomor bukti; angka dicocokkan dengan hasil alat; kalimat tentang penyebab atau pihak dibuang |
| Muse Voice Transcribe (Meta Model API) | Mengubah pesan suara warga berbahasa Indonesia menjadi teks | Agen membaca transkripnya, bukan audionya |
| SAM 3.1 (Meta Model API) | Menandai area terbakar pada foto dan frame drone, dengan konsep yang dipilih Muse Spark | Hektar dihitung kode dari masker dan ukuran piksel di tanah |
| Muse Spark Open (Baseten) | Model kedua yang menyanggah klaim yang tidak didukung bukti, khusus untuk bukti publik | Klaim yang disanggah ditandai untuk verifikator |
| Muse Glimmer (bobot terbuka, di Modal) | Dalam simulasi drone, memilih frame yang layak dikirim ke stasiun darat | Frame yang tidak dikirim tetap dicatat |

Semua model hanya dipakai untuk inferensi. Tidak ada model yang dilatih dengan data pengguna.

Pembagian kerjanya disengaja. Model bahasa pandai membaca dan menulis, tetapi bisa salah angka. Karena itu angka, jarak, dan luas selalu dihitung kode, dan model hanya boleh mengutip angka yang ada di hasil alat. Model tidak pernah memutuskan; ia menyiapkan bahan untuk manusia.

## 7. Peran manusia

- **Verifikator** menyetujui atau menolak setiap rekomendasi AI, menyetujui rencana survei drone, memilih laporan WhatsApp yang boleh tampil publik, menyamarkan data pribadi di foto, dan memutuskan pengajuan sumber panas tetap. Panel admin menyediakan antrian kerja yang menunjukkan apa saja yang menunggu.
- **Warga** memberi bukti dari lapangan. Foto dan pesan suara adalah bukti independen yang paling kuat, karena satelit sering tertutup awan.
- **Pengambil keputusan dan regu darat** memakai hasilnya sebagai bahan pertimbangan, bukan perintah.

## 8. Fitur

**Untuk semua orang**
- Peta insiden dengan saringan, lapisan asap dan aerosol, peta dasar biasa atau citra satelit, legenda, dan status setiap sumber data.
- Detail insiden yang tersusun dalam lima tahap di atas, dengan penunjuk tahap dan baris ringkas: permukiman terdekat, arah sebaran, air terdekat.
- Kartu aksi di titik yang dipilih di peta: buka detail, kirim bukti bahwa itu kebakaran, tandai bukan kebakaran lahan, bagikan.
- Rekomendasi pemadaman (lihat bagian 9).
- Bekas terbakar Sentinel-2: citra sebelum, sesudah, dan masker di detail dan di peta, dengan luas dalam hektar.
- Halaman Lapor: lokasi, 1 sampai 3 foto, pesan suara, dan teks; metadata foto dihapus.
- WhatsApp: foto, suara, teks, dan pin lokasi dalam urutan apa pun menjadi satu laporan.
- Log laporan WhatsApp publik: isi kiriman, hasil analisis model Meta, langkah pemrosesan, dan balasan bot, untuk laporan yang dipilih verifikator.
- Tautan berbagi dengan pratinjau WhatsApp dan Facebook.
- Dua bahasa, Indonesia dan Inggris, termasuk teks yang ditulis AI. Bahasa awal mengikuti bahasa browser.
- Penjelasan "Cara kerja" saat membuka situs.

**Untuk verifikator**
- Antrian verifikasi: rekomendasi AI yang menunggu keputusan, laporan warga yang belum ditriase, rencana drone yang perlu persetujuan, pengajuan sumber panas tetap, laporan WhatsApp yang belum diputuskan.
- Mencari pasangan citra Sentinel-2, mengumpulkan bukti yang kurang, dan menjalankan triase AI.
- Survei drone (simulasi): agen atau verifikator mengusulkan jalur pemetaan 120 m di atas tanah; hanya verifikator yang menyetujui; pilot simulasi menerbangkannya dengan telemetri dari rencana dan angin; tampilan 3D di atas citra Sentinel-2 dengan konsol data.

**Untuk operator**
- Panel admin: interval pengambilan data, kuota, biaya per hasil run, kesehatan sumber data, riwayat perubahan pengaturan, aksi operator (ambil data sekarang, kumpulkan bukti tertunda, cari citra Sentinel-2), ekspor CSV.
- Replay demo: tiga insiden rekaman musim kemarau 2025 (jelas terbakar, positif palsu, butuh info tambahan), dimuat sebagai SIMULASI tanpa jaringan dan tanpa memanggil model.

## 9. Rekomendasi pemadaman

Bagian ini menerjemahkan bukti menjadi hal yang berguna bagi regu darat. Semua dihitung kode, dengan ambang yang tertulis:

| Hal | Cara menghitung |
| --- | --- |
| Arah sebaran dan sisi pendekatan | Dari arah dan kecepatan angin; regu disarankan mendekat dari arah datangnya angin atau dari samping |
| Akses darat | Jalan terpetakan terdekat; tanpa jalan dalam 10 km berarti perlu jalur lain |
| Sumber air | Sungai, danau, kanal, atau anak sungai terdekat. Dalam 1 km terjangkau pompa portabel; dalam 3 km dengan estafet pompa atau tangki; lebih jauh, tangki atau dukungan udara. Kanal dan anak sungai bisa surut di kemarau |
| Pos pemadam dan kantor untuk koordinasi | Terdekat dalam 30 km, dari OpenStreetMap dan Overture Maps |
| Kekeringan | Kering bila kelembapan di bawah 50% dan hujan 24 jam di bawah 1 mm; lembap bila 80% atau lebih atau hujan 5 mm |
| Ukuran dan intensitas | Jumlah titik panas, daya radiasi api, dan luas bekas terbakar Sentinel-2 |

Tempat sekitar bisa digambar di peta sebagai garis dari insiden. "Tidak terpetakan" bukan berarti tidak ada di lapangan. Bila AI sudah menganalisis, skor risiko, pendorong, dan tindakan yang disarankan AI ditampilkan di sampingnya.

## 10. Sumber panas tetap

Beberapa tempat terus memancarkan panas yang dibaca satelit sebagai titik panas: tambang, smelter, suar gas, kilang, pabrik, pembangkit listrik, gunung api. Siapa pun bisa menandai sebuah insiden sebagai sumber panas tetap. Tanda itu tidak mengubah apa pun sampai verifikator menyetujuinya. Bila disetujui, terbentuk zona (bawaan 1 km): insiden di dalamnya tampil abu-abu, titik panas baru di sana ikut ditandai, dan status "kemungkinan besar api" di zona itu butuh foto atau pesan suara warga. Insiden tidak pernah ditutup karenanya, karena api sungguhan bisa terjadi di dekat sumber panas. Zona menyebut jenis sumber panas saja, tidak menyebut siapa yang mengelolanya.

## 11. Privasi dan keamanan

- Foto, suara, dan teks warga hanya terlihat oleh verifikator. Metadata foto (EXIF, termasuk lokasi GPS kamera) dihapus saat diterima.
- Nomor WhatsApp hanya disimpan sebagai hash bergaram di bukti; nomor asli hanya disimpan sementara untuk membalas.
- Peta publik membulatkan koordinat laporan warga.
- Verifikator bisa menyamarkan bagian foto dan menghapus data warga per insiden.
- Kunci API tidak pernah ada di repositori; repositori publik sejak commit pertama.
- AI tidak memberi saran kesehatan dan tidak menyebut penyebab atau pihak yang bertanggung jawab atas kebakaran; gerbang bukti membuang kalimat seperti itu.

## 12. Hasil evaluasi, apa adanya

Kami menguji 30 klaster titik panas dari musim kemarau 2025 di Kalimantan Barat, Kalimantan Tengah, Riau, Sumatera Selatan, dan Jambi: 15 terbakar dan 15 tidak, dilabeli dari Sentinel-2 lalu diperiksa mata. Agen hanya diberi titik panas, cuaca, dan OpenStreetMap; Sentinel-2 sengaja ditahan karena menjadi sumber label.

| Muse Spark 1.3, 30 klaster | Presisi | Recall | Abstain |
| --- | --- | --- | --- |
| Aturan sederhana: titik panas berkeyakinan nominal atau tinggi | 52% | 100% | 0 |
| Agen setelah gerbang dan kritik | 100% | 13% | 27 |

Artinya: tanpa bukti independen, agen jarang mau memutuskan. Agen menjawab "butuh info tambahan" untuk 27 dari 30 klaster dan menemukan 2 dari 15 lahan terbakar, tanpa satu pun alarm palsu. Dengan titik panas, cuaca, dan peta saja, agen tidak lebih baik dari aturan sederhana dalam mendeteksi. Nilai agen ada pada ringkasan yang berdasar bukti dan permintaan data yang tepat; deteksi yang meyakinkan datang dari bukti independen (citra Sentinel-2, foto warga, drone).

Semua 160 klaim yang sampai ke keluaran menunjuk bukti yang tersimpan (grounding 100%), dan gerbang memperbaiki 3 angka ke nilai alat. Biaya evaluasi $1,73 untuk 30 insiden, atau $0,058 per insiden.

Simulasi drone diukur terhadap kebenaran render, bukan akurasi dunia nyata: SAM 3.1 mengukur 7,69 ha terhadap 8,70 ha sebenarnya pada frame yang bisa ditandai (selisih 11,6%), dan frame yang terbakar dari tepi ke tepi tidak mendapat masker sehingga dilaporkan "tidak terukur".

## 13. Batasan

- Titik panas adalah anomali termal, bukan kebakaran yang pasti.
- Awan dan asap sering menutupi lokasi dari satelit. Sentinel-2 lewat sekitar setiap 2 sampai 5 hari; saat kabut asap tebal, luas tidak diklaim.
- OpenStreetMap dan Overture Maps tidak lengkap.
- Drone hanya simulasi; tidak ada drone yang terhubung.
- WhatsApp saat ini satu arah: laporan masuk, tetapi Meta menolak balasan bot sampai akun lolos verifikasi bisnis, dan hanya nomor penguji yang terdaftar yang bisa mengirim.
- AI bisa keliru. Ringkasan adalah bahan pertimbangan, bukan keputusan.

## 14. Biaya

Triase memakai kredit model dan hanya berjalan bila diminta: verifikator memilih paling banyak 5 insiden sekali pilih dengan kode eksekusi, dalam batas harian, atau lewat jadwal untuk laporan warga. Di produksi, 6 run triase dua bahasa yang selesai rata-rata $0,10 per insiden (rentang $0,08 sampai $0,21). Pengambilan data, bukti, rekomendasi pemadaman, dan pencarian citra Sentinel-2 tidak memanggil model.

## 15. Teknologi

Backend FastAPI dengan SQLite; frontend JavaScript tanpa framework, MapLibre GL untuk peta, dan three.js untuk tampilan 3D. Pekerjaan berat (bangun salinan offline, model Glimmer) berjalan di Modal. Aplikasi berjalan di satu server; tes otomatis berjalan tanpa jaringan.

## 16. Pembuat, lisensi, atribusi

Dibuat oleh Muh. Thariq Rabbany. Kode di bawah lisensi MIT. Pertanyaan dan masukan lewat GitHub Issues; jangan menulis data pribadi di sana.

Atribusi data: NASA FIRMS; NASA GIBS / Worldview; Contains modified Copernicus Sentinel data (Sentinel-2 L2A via Earth Search); Weather data by Open-Meteo.com (CC BY 4.0); Map data (c) OpenStreetMap contributors, ODbL; Overture Maps Foundation dan sumbernya (CDLA Permissive 2.0, Apache 2.0, CC0); OpenFreeMap; Esri World Imagery.
