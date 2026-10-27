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
  "Human decides": "Manusia memutuskan",
  "Every incident ends as needs_human. Nothing is sent to anyone automatically. A verifier approves or rejects.":
    "Setiap insiden berakhir dengan status perlu manusia. Tidak ada yang dikirim otomatis ke siapa pun. Verifikator yang menyetujui atau menolak.",
  "Report": "Lapor",
  "Incidents": "Insiden",
  "Hotspots within 2 km and 24 hours are grouped into one incident. A hotspot is a thermal anomaly, not a confirmed fire.":
    "Titik panas dalam radius 2 km dan 24 jam digabung menjadi satu insiden. Titik panas adalah anomali termal, bukan kebakaran yang terkonfirmasi.",
  "Heat": "Panas", "Size": "Jumlah", "Recent": "Terbaru",
  "Search incident ID": "Cari ID insiden",
  "All": "Semua", "3+ hotspots": "3+ titik panas", "High confidence": "Keyakinan tinggi", "Last 6 h": "6 jam terakhir",
  "Fight Karhutla is not an official emergency channel. In an emergency, call 112 or your local BPBD or fire service.":
    "Fight Karhutla bukan kanal darurat resmi. Dalam keadaan darurat, hubungi 112, BPBD, atau pemadam kebakaran setempat.",
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
