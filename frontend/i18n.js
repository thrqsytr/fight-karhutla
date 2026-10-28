/* Two languages, Indonesian by default. Each page writes its text in one source language and keeps a
   dictionary for the other, so a missing translation shows the source text instead of a blank. */

const KEY = "karhutla.lang";
export const LANGS = ["id", "en"];

export function lang() {
  const v = localStorage.getItem(KEY);
  return LANGS.includes(v) ? v : "id";
}

export function setLang(l) {
  if (!LANGS.includes(l) || l === lang()) return;
  localStorage.setItem(KEY, l);
  // A reload re-renders every component in the new language; map position survives in the URL hash.
  location.reload();
}

export const locale = () => (lang() === "id" ? "id-ID" : "en-GB");

/** Translator for text written in `source`; `dict` maps source text to the other language. */
export function makeT(dict, source) {
  return (text, vars = {}) => {
    const out = lang() !== source && dict[text] ? dict[text] : text;
    return out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  };
}

/** Static markup: data-i18n (text), data-i18n-placeholder, data-i18n-aria, data-i18n-title. */
export function applyStatic(t, root = document) {
  document.documentElement.lang = lang();
  root.querySelectorAll("[data-i18n]").forEach((n) => (n.textContent = t(n.dataset.i18n)));
  root.querySelectorAll("[data-i18n-placeholder]").forEach((n) => n.setAttribute("placeholder", t(n.dataset.i18nPlaceholder)));
  root.querySelectorAll("[data-i18n-aria]").forEach((n) => n.setAttribute("aria-label", t(n.dataset.i18nAria)));
  root.querySelectorAll("[data-i18n-title]").forEach((n) => n.setAttribute("title", t(n.dataset.i18nTitle)));
}

/** ID | EN switch, the same component on every page. */
export function langSwitch(el) {
  return el("div", { class: "segmented lang-switch", role: "group", "aria-label": "Language / Bahasa" },
    ...LANGS.map((l) => el("button", { type: "button", "aria-pressed": String(l === lang()), onclick: () => setLang(l) }, l.toUpperCase())));
}

/* Dashboard: written in English, Indonesian here. Server-generated text (evidence summaries, gate
   reasons, error details) stays as the server wrote it. */
export const DASHBOARD_ID = {
  "Verified wildfire triage for Indonesia": "Triase karhutla terverifikasi untuk Indonesia",
  "The AI only gathers evidence and drafts a recommendation. A human verifier approves or rejects every incident; nothing is sent to anyone automatically.":
    "AI hanya mengumpulkan bukti dan menyusun rekomendasi. Verifikator manusia yang menyetujui atau menolak setiap insiden; tidak ada yang dikirim otomatis ke siapa pun.",
  "Cancel this decision": "Batalkan keputusan", "Approved": "Disetujui", "Decided on {when}.": "Diputuskan pada {when}.",
  "Reason (optional)": "Alasan (opsional)", "Cancel the approval?": "Batalkan persetujuan?", "Cancel the rejection?": "Batalkan penolakan?",
  "The incident goes back to waiting for a verifier. If it rests on citizen reports only, it leaves the public map until it is approved again. The original approval stays in the history.":
    "Insiden kembali menunggu verifikator. Bila hanya berdasarkan laporan warga, insiden hilang dari peta publik sampai disetujui lagi. Persetujuan awal tetap tercatat di riwayat.",
  "The incident goes back to waiting for a verifier. The original rejection stays in the history.": "Insiden kembali menunggu verifikator. Penolakan awal tetap tercatat di riwayat.",
  "Keep it": "Biarkan", "Decision cancelled. The incident waits for a verifier again.": "Keputusan dibatalkan. Insiden kembali menunggu verifikator.",
  "cancelled": "dibatalkan",
  "Verified": "Terverifikasi", "Rejected": "Ditolak", "Not triaged yet": "Belum ditriase",
  "A human verifier approved this incident on {when}.": "Verifikator manusia menyetujui insiden ini pada {when}.",
  "A human verifier rejected this incident on {when}.": "Verifikator manusia menolak insiden ini pada {when}.",
  "What you see is raw detection: hotspots and the evidence collected so far. No AI or human has judged it. The status will change after AI triage, and only a verifier can mark it verified.":
    "Yang tampil masih deteksi mentah: titik panas dan bukti yang sudah terkumpul. Belum ada AI maupun manusia yang menilainya. Status berubah setelah triase AI, dan hanya verifikator yang bisa menandainya terverifikasi.",
  "AI recommendation: {s}": "Rekomendasi AI: {s}",
  "Built on citizen evidence; details are shown after a verifier approves.": "Berdasarkan bukti warga; rinciannya tampil setelah verifikator menyetujui.",
  "Confidence {c}%, judged on {when}. Waiting for a human verifier to approve or reject.": "Keyakinan {c}%, dinilai pada {when}. Menunggu verifikator manusia menyetujui atau menolak.",
  "Independent evidence": "Bukti independen",
  "A satellite hotspot is a heat signal, not a confirmed fire. To reach \"likely fire\" the AI needs a second, independent source: a photo, voice note, or report from the ground, or a burn scar in Sentinel-2 images.":
    "Titik panas satelit adalah sinyal panas, bukan kebakaran yang pasti. Untuk status \"kemungkinan besar api\", AI butuh sumber kedua yang independen: foto, pesan suara, atau laporan dari lapangan, atau bekas terbakar di citra Sentinel-2.",
  "Copy report link for the field": "Salin tautan lapor untuk lapangan", "Link copied. Anyone near the site can send photos with it.": "Tautan disalin. Siapa pun di dekat lokasi bisa mengirim foto lewat tautan itu.",
  "Weather": "Cuaca", "Wind and rain decide how fast a fire spreads and which way smoke goes.": "Angin dan hujan menentukan seberapa cepat api menyebar dan ke mana asap bergerak.",
  "Nearby settlements and water": "Permukiman dan sumber air terdekat",
  "Distance to villages, roads, and water points from OpenStreetMap. The public OSM servers are often down; collection retries automatically.":
    "Jarak ke desa, jalan, dan sumber air dari OpenStreetMap. Server OSM publik sering mati; pengambilan diulang otomatis.",
  "What the AI asked for": "Yang diminta AI",
  "Next: run AI triage on the evidence above.": "Berikutnya: jalankan triase AI atas bukti di atas.", "Next: a verifier runs AI triage.": "Berikutnya: verifikator menjalankan triase AI.",
  "New evidence arrived after the last triage. Run it again to use it.": "Ada bukti baru sejak triase terakhir. Jalankan lagi agar bukti itu dipakai.",
  "Next: review the brief and claims, then approve or reject below.": "Berikutnya: periksa ringkasan dan klaim, lalu setujui atau tolak di bawah.",
  "Next: a verifier reviews the AI brief and approves or rejects it.": "Berikutnya: verifikator memeriksa ringkasan AI lalu menyetujui atau menolaknya.",
  "Detected": "Terdeteksi", "AI triage": "Triase AI", "Human verifier": "Verifikator manusia",
  "Citizen": "Warga", "not yet": "belum", "approved": "disetujui", "rejected": "ditolak", "waiting": "menunggu",
  "Still missing": "Yang masih kurang",
  "Approving or rejecting needs verifier mode.": "Menyetujui atau menolak memerlukan mode verifikator.",
  "Zoom in": "Perbesar", "Zoom out": "Perkecil", "Data sources": "Sumber data",
  "Sentinel-2 burn scar": "Bekas terbakar Sentinel-2",
  "Share": "Bagikan", "Not shareable yet: {e}": "Belum bisa dibagikan: {e}", "Share...": "Bagikan...", "WhatsApp": "WhatsApp", "Facebook": "Facebook",
  "Copy link": "Salin tautan", "Copy text": "Salin teks", "Download image": "Unduh gambar", "Link copied.": "Tautan disalin.",
  "Summary and link copied.": "Ringkasan dan tautan disalin.", "Share {id}": "Bagikan {id}",
  "Only public information is shared: rounded location, satellite hotspots, status, and the nearest settlement. Citizen photos and text are never included.":
    "Hanya informasi publik yang dibagikan: lokasi yang dibulatkan, titik panas satelit, status, dan permukiman terdekat. Foto dan teks warga tidak pernah disertakan.",
  "Cancelled": "Dibatalkan", "Play simulated flight": "Putar simulasi terbang", "Cancel plan": "Batalkan rencana",
  "Keep plan": "Pertahankan rencana", "See an example survey result": "Lihat contoh hasil survei",
  "Simulated flight: frame {k} of {n}, {done} of {total} flight time": "Simulasi terbang: frame {k} dari {n}, {done} dari {total} waktu terbang",
  "Flight replayed about {x} times faster. Only the route is simulated: this plan has no imagery until a pilot flies it.":
    "Diputar sekitar {x} kali lebih cepat. Hanya rutenya yang disimulasikan: rencana ini belum punya citra sampai pilot menerbangkannya.",
  "One active plan per incident. Cancel the approved plan to make a new one.": "Satu rencana aktif per insiden. Batalkan rencana yang sudah disetujui untuk membuat yang baru.",
  "Cancel this drone survey?": "Batalkan survei drone ini?",
  "The approved plan is withdrawn and kept on record. A new plan can then be proposed.": "Rencana yang disetujui ditarik dan tetap tercatat. Setelah itu rencana baru bisa diusulkan.",
  "Mission cancelled and recorded.": "Misi dibatalkan dan tercatat.", "An active plan already exists; showing it.": "Sudah ada rencana aktif; ditampilkan.",
  "Flown": "Sudah diterbangkan", "In flight": "Sedang terbang", "Battery": "Baterai", "Ground speed": "Kecepatan darat", "Altitude": "Ketinggian", "Wind": "Angin",
  "pack {n} of {m}": "paket {n} dari {m}", "calm (no weather evidence)": "tenang (tanpa bukti cuaca)", "{v} m/s from {d} deg": "{v} m/s dari {d} derajat",
  "Playback speed": "Kecepatan putar", "Take off": "Lepas landas", "Pre-flight check (simulated pilot)": "Cek pra-terbang (pilot simulasi)",
  "Plan approved by a verifier": "Rencana disetujui verifikator", "Wind from the incident's weather evidence: {w}": "Angin dari bukti cuaca insiden: {w}",
  "No real drone is connected. The flight is simulated: telemetry is computed from the plan and the wind, and everyone who opens this incident sees the same flight.":
    "Tidak ada drone sungguhan yang terhubung. Penerbangan ini simulasi: telemetri dihitung dari rencana dan angin, dan semua yang membuka insiden ini melihat penerbangan yang sama.",
  "Take-off. The simulated flight is on the map.": "Lepas landas. Simulasi penerbangan tampil di peta.",
  "Land and finish": "Mendarat dan selesai", "Abort flight": "Batalkan penerbangan", "Land and finish the flight?": "Mendaratkan dan menyelesaikan penerbangan?",
  "Abort this flight?": "Batalkan penerbangan ini?", "The drone lands and the flight is recorded as completed.": "Drone mendarat dan penerbangan tercatat selesai.",
  "The drone returns and the flight is recorded as aborted. The plan can be flown again.": "Drone kembali dan penerbangan tercatat dibatalkan. Rencana bisa diterbangkan lagi.",
  "Keep flying": "Lanjutkan terbang", "Flight completed and recorded.": "Penerbangan selesai dan tercatat.", "Flight aborted and recorded.": "Penerbangan dibatalkan dan tercatat.",
  "See the survey result below": "Lihat hasil survei di bawah",
  "The route is not finished yet. Abort instead, or wait for the last frame.": "Rute belum selesai. Batalkan penerbangan, atau tunggu frame terakhir.",
  "Route done: ready to land": "Rute selesai: siap mendarat", "Battery swap on the ground": "Ganti baterai di darat", "Flying (simulated pilot)": "Terbang (pilot simulasi)",
  "Played x{x} faster than real time.": "Diputar x{x} lebih cepat dari waktu nyata.", "Replay flight": "Putar ulang penerbangan", "Replay": "Putar ulang",
  "Landed": "Mendarat", "Frames": "Frame", "Flight time": "Waktu terbang", "{n} battery pack(s)": "{n} paket baterai", "Imagery": "Citra",
  "Simulated survey of this incident: frames, Glimmer filter, SAM area below.": "Survei simulasi insiden ini: frame, filter Glimmer, dan luas SAM ada di bawah.",
  "None: only the route was simulated. A real flight would upload its frames here.": "Tidak ada: hanya rutenya yang disimulasikan. Penerbangan sungguhan akan mengunggah framenya di sini.",
  "Preview route": "Pratinjau rute", "Fly again": "Terbangkan lagi", "Start flight (simulated pilot)": "Mulai terbang (pilot simulasi)",
  "Last flight aborted at frame {k} of {n}, {when}.": "Penerbangan terakhir dibatalkan di frame {k} dari {n}, {when}.",
  "Loading 3D view...": "Memuat tampilan 3D...", "3D flight view": "Tampilan terbang 3D", "Follow the drone": "Ikuti drone", "Close": "Tutup",
  "View in 3D": "Lihat 3D", "Replay in 3D": "Putar ulang 3D", "Preview in 3D": "Pratinjau 3D", "Route preview": "Pratinjau rute",
  "Ground: Sentinel-2 after the fire ({d}), burn scar in red, laid flat (no elevation model). Hotspots in orange. The drone is drawn {x} times larger than life. Inset: what its camera sees.":
    "Medan: Sentinel-2 setelah kebakaran ({d}), bekas terbakar merah, dibuat datar (tanpa model elevasi). Titik panas oranye. Drone digambar {x} kali lebih besar dari aslinya. Inset: yang dilihat kameranya.",
  "Ground: OpenStreetMap, laid flat (no elevation model). Hotspots in orange. The drone is drawn {x} times larger than life. Inset: what its camera sees. OpenStreetMap shows little in forest: a Sentinel-2 pair (Find Sentinel-2 images in the incident view) gives this view real imagery.":
    "Medan: OpenStreetMap, dibuat datar (tanpa model elevasi). Titik panas oranye. Drone digambar {x} kali lebih besar dari aslinya. Inset: yang dilihat kameranya. OpenStreetMap nyaris kosong di hutan: pasangan Sentinel-2 (Cari citra Sentinel-2 di jendela insiden) memberi tampilan ini citra nyata.",
  "Drone camera": "Kamera drone",
  "3D view unavailable: {e}": "Tampilan 3D tidak tersedia: {e}",
  "Already running: {what}": "Sudah berjalan: {what}", "In progress": "Sedang diproses", "Working in the background: ": "Proses latar belakang: ",
  "Searching Sentinel-2": "Mencari Sentinel-2", "Collecting evidence": "Mengumpulkan bukti", "Planning a drone survey": "Merencanakan survei drone",
  "Hide from map": "Sembunyikan dari peta", "Preparing the share": "Menyiapkan tautan", "See the drone survey plan": "Lihat rencana drone",
  "Wait for the triage to finish, then decide.": "Tunggu triase selesai, lalu putuskan.",
  "AI triage is running. The decision opens when it finishes.": "Triase AI sedang berjalan. Keputusan bisa diambil setelah selesai.",
  "Another plan of this incident is in the air. One drone flies at a time.": "Rencana lain di insiden ini sedang terbang. Hanya satu drone terbang dalam satu waktu.",
  "Drone survey plan": "Rencana survei drone", "Proposed": "Diusulkan",
  "Planned by a verifier from the incident view.": "Direncanakan verifikator dari tampilan insiden.",
  "Drone survey proposed. Approve it to send it to a pilot.": "Survei drone diusulkan. Setujui agar diteruskan ke pilot.",
  "An open proposal already exists; showing it.": "Sudah ada usulan yang terbuka; ditampilkan.", "Not proposed: {e}": "Tidak diusulkan: {e}",
  "Note (optional)": "Catatan (opsional)", "Approve mission": "Setujui misi", "Reject mission": "Tolak misi",
  "Approve this drone survey?": "Setujui survei drone ini?", "Reject this drone survey?": "Tolak survei drone ini?",
  "Approval clears the plan for a pilot. This prototype has no drone connected, so nothing flies by itself; the approval is recorded with the plan.":
    "Persetujuan meneruskan rencana ke pilot. Prototipe ini belum terhubung ke drone, jadi tidak ada yang terbang sendiri; persetujuan dicatat bersama rencananya.",
  "The plan is closed. A new one can be proposed later.": "Rencana ditutup. Usulan baru bisa dibuat nanti.",
  "Mission approved and recorded.": "Misi disetujui dan dicatat.", "Mission rejected and recorded.": "Misi ditolak dan dicatat.",
  "proposed by the AI agent": "diusulkan agen AI", "proposed by a verifier": "diusulkan verifikator",
  "Flight": "Penerbangan", "about {min} min, {km} km path, {b} battery(ies)": "sekitar {min} menit, lintasan {km} km, {b} baterai",
  "Approved {when}. Waiting for a pilot: no drone is connected to this prototype. The simulated survey of INC-20261025-0407 shows what a flight returns.":
    "Disetujui {when}. Menunggu pilot: prototipe ini belum terhubung ke drone. Survei simulasi INC-20261025-0407 menunjukkan hasil sebuah penerbangan.",
  "Note": "Catatan", "Show plan on map": "Tampilkan rencana di peta", "Propose a drone survey": "Usulkan survei drone",
  "A mapping flight planned over the hotspots: lawnmower lines at 120 m above ground. The AI agent may propose one when independent evidence is missing; only a verifier can approve it. Planning is free.":
    "Penerbangan pemetaan yang direncanakan di atas titik panas: jalur bolak-balik 120 m di atas tanah. Agen AI boleh mengusulkannya bila bukti independen belum ada; hanya verifikator yang bisa menyetujui. Merencanakan tidak berbiaya.",
  "No drone survey planned.": "Belum ada rencana survei drone.",
  "Drone survey (simulation)": "Survei drone (simulasi)", "Drone frame": "Frame drone", "Drone view": "Tampilan drone",
  "A simulated mapping flight: Muse Glimmer at the ground station picks which frames to send, SAM 3.1 segments them, and code turns masks into hectares from the telemetry.":
    "Penerbangan pemetaan simulasi: Muse Glimmer di stasiun darat memilih frame yang dikirim, SAM 3.1 menyegmentasinya, dan kode mengubah masker menjadi hektar dari telemetri.",
  "Frame": "Frame", "SAM mask": "Masker SAM", "Truth": "Kebenaran",
  "Glimmer: uplinked": "Glimmer: dikirim", "Glimmer: dropped": "Glimmer: tidak dikirim", "Glimmer: no decision": "Glimmer: tanpa keputusan",
  "Mission": "Misi", "{lines} lines x {n} frames, {alt} m above ground, GSD {gsd} m": "{lines} jalur x {n} frame, {alt} m di atas tanah, GSD {gsd} m",
  "Glimmer filter": "Filter Glimmer", "{up} of {all} frames uplinked, {drop} dropped": "{up} dari {all} frame dikirim, {drop} tidak dikirim",
  "Measured frames": "Frame terukur", "{m} of {up} uplinked frames; SAM found no mask on frames burned edge to edge": "{m} dari {up} frame terkirim; SAM tidak menemukan masker pada frame yang terbakar penuh",
  "Missed by filter": "Terlewat filter", "{ha} ha of burned ground in dropped frames": "{ha} ha lahan terbakar di frame yang tidak dikirim",
  "SAM area": "Luas SAM", "not measured": "belum diukur", "Truth (render)": "Kebenaran (render)", "error {e}%": "galat {e}%", "Cost": "Biaya",
  "Frames are rendered in Blender over the real Sentinel-2 burn mask of this incident. The error is against the render, not real-world accuracy. These numbers never enter the triage.":
    "Frame dirender di Blender di atas masker bekas terbakar Sentinel-2 yang nyata dari insiden ini. Galat dihitung terhadap render, bukan akurasi dunia nyata. Angka ini tidak pernah masuk ke triase.",
  "Show footprints on map": "Tampilkan jejak frame di peta",
  "Sentinel-2 before and after images for burn scars, fetched on request.": "Citra Sentinel-2 sebelum dan sesudah untuk bekas terbakar, diambil bila diminta.",
  "Before and after images from Sentinel-2. dNBR is the drop in the normalized burn ratio; burned hectares count 20 m pixels at dNBR 0.27 or more, computed by code. Under heavy haze no hectares are claimed.":
    "Citra sebelum dan sesudah dari Sentinel-2. dNBR adalah penurunan rasio bakar ternormalisasi; luas terbakar menghitung piksel 20 m dengan dNBR 0,27 atau lebih, dihitung oleh kode. Saat kabut asap tebal, luas tidak diklaim.",
  "Check for a newer pair": "Cek pasangan citra terbaru", "Find Sentinel-2 images": "Cari citra Sentinel-2",
  "No Sentinel-2 pair yet. Clouds often cover the area; a pass comes about every 5 days.": "Belum ada pasangan citra Sentinel-2. Awan sering menutup area; satelit lewat sekitar setiap 5 hari.",
  "Burn scar": "Bekas terbakar", "After": "Sesudah", "Before": "Sebelum", "Sentinel-2 image": "Citra Sentinel-2", "Sentinel-2 view": "Tampilan Sentinel-2",
  "{pct}% clear": "{pct}% cerah", "not claimed": "tidak diklaim", "Show scar on map": "Tampilkan di peta",
  "Heavy haze: no hectares are claimed from this pair.": "Kabut asap tebal: luas tidak diklaim dari pasangan ini.",
  "Burned area": "Luas terbakar", "Low severity": "Keparahan rendah", "Clear ground": "Area tanpa awan",
  "Searching Sentinel-2...": "Mencari Sentinel-2...", "Pair checked in the last 24 hours; showing it.": "Pasangan sudah dicek dalam 24 jam terakhir; ditampilkan.",
  "Sentinel-2 pair stored as {id}.": "Pasangan Sentinel-2 disimpan sebagai {id}.", "No pair: {why}": "Tidak ada pasangan: {why}", "Sentinel-2 failed: {e}": "Sentinel-2 gagal: {e}",
  "NASA FIRMS satellite hotspots (VIIRS and MODIS), fetched every 30 minutes.": "Titik panas satelit NASA FIRMS (VIIRS dan MODIS), diambil setiap 30 menit.",
  "NASA GIBS satellite layers for the map, checked from this browser.": "Lapisan citra satelit NASA GIBS untuk peta, dicek dari browser ini.",
  "Wind, humidity, and rain evidence for each incident.": "Bukti angin, kelembapan, dan hujan untuk setiap insiden.",
  "Nearby settlements, roads, and water from OpenStreetMap (Overpass).": "Permukiman, jalan, dan perairan terdekat dari OpenStreetMap (Overpass).",
  "Offline copy": "Salinan offline",
  "On": "Aktif", "Down": "Gangguan", "Simulation": "Simulasi", "Not checked": "Belum dicek",
  "Newest pass: {when}.": "Lintasan terbaru: {when}.", "Last checked: {when}.": "Terakhir dicek: {when}.",
  "Report": "Lapor",
  "Incidents": "Insiden",
  "Hotspots within 2 km and 24 hours are grouped into one incident. A hotspot is a thermal anomaly, not a confirmed fire.":
    "Titik panas dalam radius 2 km dan 24 jam digabung menjadi satu insiden. Titik panas adalah anomali termal, bukan kebakaran yang terkonfirmasi.",
  "Heat": "Panas", "Size": "Jumlah", "Recent": "Terbaru",
  "Search incident ID": "Cari ID insiden",
  "All": "Semua", "3+ hotspots": "3+ titik panas", "High confidence": "Keyakinan tinggi", "Last 6 h": "6 jam terakhir",
  "Pending triage": "Menunggu triase", "Likely fire": "Kemungkinan besar api", "Possible fire": "Mungkin api",
  "Unlikely": "Kecil kemungkinan", "Needs more info": "Perlu info tambahan", "Triage running": "Triase berjalan",
  "With 3+ hotspots": "Dengan 3+ titik panas", "Hotspots, 24 h": "Titik panas, 24 jam", "Newest pass": "Lintasan terbaru",
  "Hotspot clusters currently stored. Most are a single hotspot; filter for 3+ to focus.":
    "Klaster titik panas yang tersimpan. Sebagian besar hanya satu titik; pakai filter 3+ untuk fokus.",
  "Clusters with at least three detections, a stronger thermal signal.": "Klaster dengan minimal tiga deteksi, sinyal termal yang lebih kuat.",
  "FIRMS detections in the 24 hours before the newest acquisition.": "Deteksi FIRMS dalam 24 jam sebelum akuisisi terbaru.",
  "VIIRS high confidence (h) or MODIS 80 and above in the same window.": "Keyakinan tinggi VIIRS (h) atau MODIS 80 ke atas pada jendela yang sama.",
  "Acquisition time of the newest satellite detection.": "Waktu akuisisi deteksi satelit terbaru.",
  "Google Maps adapter is not built; showing MapLibre.": "Adaptor Google Maps belum dibuat; menampilkan MapLibre.",
  "Reset map view": "Kembalikan posisi peta",
  "Hide panel": "Sembunyikan panel", "Show panel": "Tampilkan panel",
  "{n} hotspots · max FRP {frp}": "{n} titik panas · FRP maks {frp}",
  "FRP {frp} · confidence {c}": "FRP {frp} · keyakinan {c}",
  "Map legend": "Legenda peta",
  "Fire radiative power": "Daya radiasi api (FRP)",
  "Measured by VIIRS or MODIS in megawatts. Higher FRP means a hotter, more intense fire signal. Opacity follows detection confidence.":
    "Diukur VIIRS atau MODIS dalam megawatt. FRP lebih tinggi berarti sinyal api lebih panas dan kuat. Transparansi mengikuti tingkat keyakinan deteksi.",
  "Incident status": "Status insiden", "Layers": "Lapisan",
  "Heat density": "Kepadatan panas", "Hotspots": "Titik panas",
  "NASA satellite layers": "Lapisan satelit NASA",
  "Latest available day from NASA GIBS. Display only: these images are not used as evidence and clouds can hide smoke.":
    "Hari terbaru yang tersedia dari NASA GIBS. Hanya tampilan: citra ini tidak dipakai sebagai bukti, dan awan bisa menutupi asap.",
  "True color (VIIRS)": "Warna asli (VIIRS)", "Aerosol optical depth (MODIS)": "Kedalaman optik aerosol (MODIS)",
  "Smoke and dust index (OMPS)": "Indeks asap dan debu (OMPS)",
  "Daily true-color image from VIIRS. Smoke plumes are visible as grey-brown haze.": "Citra warna asli harian VIIRS. Gumpalan asap terlihat sebagai kabut abu kecokelatan.",
  "How much haze, smoke, or dust blocks sunlight. Higher is thicker. Gaps are clouds or no data.": "Seberapa banyak kabut, asap, atau debu menghalangi cahaya. Makin tinggi makin tebal. Celah berarti awan atau tidak ada data.",
  "UV aerosol index: positive values flag light-absorbing aerosol such as smoke and dust, even above some clouds.": "Indeks aerosol UV: nilai positif menandai aerosol penyerap cahaya seperti asap dan debu, bahkan di atas sebagian awan.",
  "No incidents match this filter.": "Tidak ada insiden yang cocok dengan filter ini.",
  "({n})": "({n})", "({n} of {total})": "({n} dari {total})",
  "Show {n} more of {rest}": "Tampilkan {n} lagi dari {rest}",
  "Max fire radiative power": "FRP maksimum", "Hotspots in cluster": "Titik panas dalam klaster", "Last detection": "Deteksi terakhir",
  "Verifier token": "Token verifikator", "Sign in": "Masuk", "Sign out": "Keluar", "Approve": "Setujui", "Reject": "Tolak",
  "Approved and recorded.": "Disetujui dan tercatat.", "Rejected and recorded.": "Ditolak dan tercatat.", "Not recorded: {e}": "Tidak tercatat: {e}",
  "hotspot cluster": "klaster titik panas", "citizen report": "laporan warga", "hotspots + citizen report": "titik panas + laporan warga",
  "Photo unavailable": "Foto tidak tersedia", "Play voice note": "Putar pesan suara", "Stop": "Berhenti", "Voice note unavailable: {e}": "Pesan suara tidak tersedia: {e}",
  "Transcript: {t}": "Transkrip: {t}", "Transcript pending": "Transkrip belum tersedia", "citizen photo": "foto warga",
  "UNKNOWN": "TIDAK DIKETAHUI", "{src}: not collected yet ({why}). Retried automatically.": "{src}: belum terkumpul ({why}). Dicoba ulang otomatis.",
  "No evidence collected.": "Belum ada bukti terkumpul.", "Not collected yet. Small clusters are collected on request.": "Belum dikumpulkan. Klaster kecil dikumpulkan atas permintaan.",
  "Collected, but {list} failed and will be retried.": "Terkumpul, tetapi {list} gagal dan akan dicoba ulang.", "Evidence collected.": "Bukti terkumpul.", "Not collected: {e}": "Tidak terkumpul: {e}",
  "Show {id}": "Tampilkan {id}", "{id} is not visible in this view": "{id} tidak terlihat di tampilan ini",
  "No claims passed the evidence gate.": "Belum ada klaim yang lolos gerbang bukti.", "Disputed: ": "Disanggah: ",
  "No gate result yet.": "Belum ada hasil gerbang.", "Grounding rate": "Rasio terverifikasi", "Claims passed": "Klaim lolos", "Disputed": "Disanggah",
  "Removed claims": "Klaim dibuang", "Corrections": "Koreksi", "Content rule removals": "Dibuang karena aturan isi", "Status changes": "Perubahan status", "Not reviewed": "Tidak ditinjau",
  "{where}: {claimed} replaced with {corrected}": "{where}: {claimed} diganti dengan {corrected}", "nothing": "kosong",
  "Triaged {when} in {s} s, metered cost ${cost}. Critique: {who}.": "Ditriase {when} dalam {s} detik, biaya terukur ${cost}. Penyanggah: {who}.", "none": "tidak ada",
  "At most {n} incidents per execution.": "Maksimal {n} insiden per eksekusi.",
  "{n} of {max} selected": "{n} dari {max} dipilih", "Estimated ${cost}, metered after the run": "Perkiraan ${cost}, diukur setelah dijalankan",
  "Tick incidents, or draw an area on the map": "Centang insiden, atau tarik area di peta", "Area": "Area", "Cancel": "Batal", "Triage": "Triase",
  "Drag a box over the area to triage. Esc cancels.": "Tarik kotak di atas area yang akan ditriase. Esc untuk batal.",
  "No incidents in that area.": "Tidak ada insiden di area itu.",
  "{n} incidents in the area; picked the {max} with the highest FRP.": "{n} insiden di area itu; dipilih {max} dengan FRP tertinggi.",
  "{n} incident(s) picked.": "{n} insiden dipilih.", "Execution code": "Kode eksekusi",
  "Run {n} triage(s)": "Jalankan {n} triase", "Triage {n} incident(s)": "Triase {n} insiden",
  "{n} hotspots, max FRP {frp}": "{n} titik panas, FRP maks {frp}",
  "Estimated cost ${cost} (about ${each} each, metered after the run).": "Perkiraan biaya ${cost} (sekitar ${each} per insiden, diukur setelah dijalankan).",
  "{left} of {cap} runs left today.": "Sisa {left} dari {cap} run hari ini.",
  "Triage started for {n} incident(s). About a minute each.": "Triase dimulai untuk {n} insiden. Sekitar satu menit per insiden.",
  "Off": "Mati", "Daily": "Harian", "Hourly": "Per jam", "Scheduled triage": "Triase terjadwal",
  "Scheduled triage is off.": "Triase terjadwal mati.", "Next scheduled pass: {when}.": "Jadwal berikutnya: {when}.", "soon": "segera",
  "Schedule": "Jadwal",
  "Scheduled triage covers citizen-reported incidents only, at most {n} per pass. Daily is the default; hourly is the high-intensity mode. Changing it needs the execution code.":
    "Triase terjadwal hanya untuk insiden dengan laporan warga, maksimal {n} per putaran. Harian adalah bawaan; per jam adalah mode intensitas tinggi. Mengubahnya butuh kode eksekusi.",
  "Runs today": "Run hari ini", "Metered today": "Biaya hari ini", "Per selection": "Per seleksi",
  "Stop selecting": "Berhenti memilih", "Select incidents": "Pilih insiden", "Select area on map": "Pilih area di peta",
  "No execution code is configured on the server, so triage cannot run.": "Kode eksekusi belum diatur di server, jadi triase tidak bisa dijalankan.",
  "Triage control": "Kontrol triase", "Set {mode}": "Atur {mode}", "Scheduled triage: {mode}": "Triase terjadwal: {mode}",
  "No scheduled triage. Verifiers still run triage on selected incidents.": "Tidak ada triase terjadwal. Verifikator tetap bisa menjalankan triase untuk insiden pilihan.",
  "One scheduled pass per day over citizen-reported incidents.": "Satu putaran terjadwal per hari untuk insiden dengan laporan warga.",
  "High intensity: one scheduled pass per hour over citizen-reported incidents. The daily cap still applies.": "Intensitas tinggi: satu putaran terjadwal per jam untuk insiden dengan laporan warga. Batas harian tetap berlaku.",
  "Scheduled triage set to {mode}.": "Triase terjadwal diatur ke {mode}.",
  "Triage running...": "Triase berjalan...", "Run triage again": "Jalankan triase lagi", "Run triage": "Jalankan triase",
  "Could not load {id}: {e}": "Gagal memuat {id}: {e}", "SIMULASI": "SIMULASI", "Active in last 6 h": "Aktif 6 jam terakhir",
  "Highest FRP among the cluster's hotspots, as measured by the satellite.": "FRP tertinggi di antara titik panas klaster, sesuai ukuran satelit.",
  "Total FRP, MW": "Total FRP, MW", "Overview": "Ringkasan", "Location": "Lokasi", "First seen": "Pertama terlihat", "Last seen": "Terakhir terlihat",
  "Dispatch brief": "Ringkasan tindakan",
  "Written by Muse Spark 1.3 from the claims below, then checked by the evidence gate and a second model. Nothing is sent to anyone automatically.":
    "Ditulis Muse Spark 1.3 dari klaim di bawah, lalu diperiksa gerbang bukti dan model kedua. Tidak ada yang dikirim otomatis ke siapa pun.",
  "Built on citizen evidence: shown after a verifier approves.": "Disusun dari bukti warga: ditampilkan setelah verifikator menyetujui.",
  "Brief disputed: ": "Ringkasan disanggah: ", "Uncertainties": "Ketidakpastian", "Triage is running...": "Triase sedang berjalan...", "Not triaged yet.": "Belum ditriase.",
  "Claims": "Klaim",
  "Each claim cites stored evidence. Click an ID to see it. A disputed claim was flagged by the critique model as not supported by its evidence. Claims are written in English by the model.":
    "Setiap klaim merujuk bukti tersimpan. Klik ID untuk melihatnya. Klaim yang disanggah ditandai model penyanggah karena tidak didukung buktinya. Klaim ditulis model dalam bahasa Inggris.",
  "Evidence gate": "Gerbang bukti",
  "Deterministic checks between the model and the verifier: schema, evidence existence, numbers against tool results, area from real masks, location, and content rules. Every change is listed.":
    "Pemeriksaan deterministik antara model dan verifikator: skema, keberadaan bukti, angka terhadap hasil alat, luas dari masker nyata, lokasi, dan aturan isi. Setiap perubahan dicantumkan.",
  "Evidence": "Bukti",
  "Every tool result is stored with a numbered ID. Claims link to these IDs. Citizen photos, voice, and text are shown to verifiers only.":
    "Setiap hasil alat disimpan dengan ID bernomor. Klaim merujuk ke ID ini. Foto, suara, dan teks warga hanya terlihat oleh verifikator.",
  "Collect missing evidence": "Kumpulkan bukti yang kurang",
  "NASA FIRMS detections. Confidence l, n, h means low, nominal, high.": "Deteksi NASA FIRMS. Keyakinan l, n, h berarti rendah, nominal, tinggi.",
  "Time": "Waktu", "Sat": "Sat", "Conf": "Keyakinan", "Showing 25 of {n}.": "Menampilkan 25 dari {n}.",
  "Verdicts": "Keputusan", "No verdict yet.": "Belum ada keputusan", "Verifier decision": "Keputusan verifikator",
  "Approve or reject is recorded as a label. Labels calibrate the evidence gate thresholds.": "Setujui atau tolak dicatat sebagai label. Label dipakai untuk mengkalibrasi ambang gerbang bukti.",
  "Live FIRMS": "FIRMS langsung", "Could not load incidents: {e}": "Gagal memuat insiden: {e}", "n/a": "t/a",
  "approve": "setuju", "reject": "tolak",
};
