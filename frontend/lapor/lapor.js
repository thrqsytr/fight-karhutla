import { $, el, hydrateIcons, siteFooter, svg } from "/ui.js";
import { applyStatic, lang, langSwitch, locale, makeT } from "/i18n.js";

/* The report page is written in Indonesian for residents; English is here for visitors and judges.
   Server messages stay in Indonesian. */
const LAPOR_EN = {
  "Lapor karhutla": "Report a forest or land fire",
  "Peta": "Map", "Peta insiden": "Incident map", "Lapor": "Report",
  "Bukan kanal darurat resmi.": "Not an official emergency channel.",
  "Bila ada bahaya bagi orang, hubungi 112 atau BPBD setempat lebih dulu. Laporan di sini diperiksa verifikator manusia sebelum diteruskan.":
    "If people are in danger, call 112 or your local BPBD first. Reports here are checked by a human verifier before they are passed on.",
  "Lokasi": "Location",
  "Pakai GPS ponsel, lalu geser penanda bila perlu. Lokasi persis hanya terlihat oleh verifikator; peta publik membulatkan ke sekitar 100 m.":
    "Use your phone's GPS, then drag the marker if needed. The exact location is visible to verifiers only; the public map rounds it to about 100 m.",
  "Gunakan lokasi saya": "Use my location", "Mencari lokasi...": "Finding location...", "Perbarui lokasi": "Update location",
  "Foto": "Photos", "(1 sampai 3)": "(1 to 3)",
  "Foto asap atau api dari jarak aman. Data tersembunyi di foto (EXIF, termasuk merek ponsel dan GPS) dihapus sebelum disimpan.":
    "Photograph smoke or fire from a safe distance. Hidden photo data (EXIF, including phone model and GPS) is removed before storage.",
  "Ambil atau pilih foto": "Take or choose photos", "JPG, PNG, atau WEBP": "JPG, PNG, or WEBP",
  "Pesan suara": "Voice note", "(opsional)": "(optional)",
  "Ceritakan apa yang terlihat: asap, api, arah angin, akses jalan. Maksimal 2 menit. Suara diubah menjadi teks untuk verifikator.":
    "Describe what you see: smoke, fire, wind direction, road access. Up to 2 minutes. The audio is transcribed for verifiers.",
  "Keterangan": "Description",
  "Contoh: asap tebal dari arah timur kebun, terlihat sejak pagi.": "Example: thick smoke east of the garden, visible since morning.",
  "Saya paham ini bukan kanal darurat resmi, dan laporan saya akan diperiksa verifikator manusia.":
    "I understand this is not an official emergency channel and that a human verifier will check my report.",
  "Kirim laporan": "Send report",
  "di luar Indonesia": "outside Indonesia", "GPS, akurasi sekitar {m} m": "GPS, accuracy about {m} m", "dipilih di peta": "picked on the map",
  "Perangkat ini tidak mendukung GPS. Pilih lokasi dengan mengetuk peta.": "This device has no GPS. Tap the map to pick the location.",
  "Izin lokasi ditolak. Ketuk peta untuk memilih lokasi secara manual.": "Location permission was denied. Tap the map to pick the location.",
  "Lokasi tidak ditemukan. Coba lagi di tempat terbuka, atau ketuk peta.": "Location not found. Try again in the open, or tap the map.",
  "Maksimal {n} foto. Foto berlebih tidak ditambahkan.": "At most {n} photos. Extra photos were not added.",
  "Foto {n}": "Photo {n}", "Hapus foto {n}": "Remove photo {n}",
  "Perekam suara tidak didukung di browser ini. Tulis keterangan di langkah 4.": "Voice recording is not supported in this browser. Write a description in step 4.",
  "Merekam": "Recording", "maks {t}": "max {t}", "Selesai": "Done", "Putar pesan suara": "Play voice note", "Putar": "Play", "Berhenti": "Stop",
  "Hapus pesan suara": "Remove voice note", "Rekam pesan suara": "Record a voice note",
  "Izin mikrofon ditolak. Anda tetap bisa mengirim foto dan keterangan.": "Microphone permission was denied. You can still send photos and a description.",
  "Tentukan lokasi dulu: ketuk \"Gunakan lokasi saya\" atau ketuk peta.": "Set the location first: tap \"Use my location\" or tap the map.",
  "Lokasi berada di luar wilayah Indonesia.": "The location is outside Indonesia.",
  "Tambahkan minimal satu foto, pesan suara, atau keterangan.": "Add at least one photo, voice note, or description.",
  "Centang pernyataan di atas tombol kirim.": "Tick the statement above the send button.",
  "Laporan belum terkirim (kode {code}). Coba lagi.": "The report was not sent (code {code}). Try again.",
  "Koneksi terputus. Laporan belum terkirim; coba lagi saat sinyal lebih baik.": "The connection dropped. The report was not sent; try again with a better signal.",
  "Laporan diterima": "Report received", "Nomor insiden": "Incident number",
  "Laporan Anda digabung dengan insiden yang sudah terpantau di dekat lokasi itu.": "Your report joined an incident already monitored near that location.",
  "Laporan Anda membuka insiden baru.": "Your report opened a new incident.",
  "Status: menunggu verifikasi manusia. Fight Karhutla tidak mengirim perintah ke siapa pun; verifikator yang memutuskan langkah berikutnya.":
    "Status: waiting for human verification. Fight Karhutla sends no orders to anyone; a verifier decides the next step.",
  "Lihat peta": "View map", "Kirim laporan lain": "Send another report",
};
const t = makeT(LAPOR_EN, "id");

const MAPLIBRE_URL = "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
const MAX_PHOTOS = 3;
const MAX_EDGE = 2048; // matches the server; shrinking here saves the reporter's mobile data
const MAX_RECORD_S = 120;
// Generous Indonesia box, the same sanity check the server applies.
const ID_BBOX = { w: 94.5, s: -11.5, e: 141.5, n: 6.5 };

const state = { loc: null, photos: [], audio: null, consent: false, sending: false };

/** replaceChildren that skips null and false, which the DOM would otherwise print as text. */
const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));

/* Location */
let map, marker, maplibregl;

async function initMiniMap() {
  maplibregl = await import(MAPLIBRE_URL);
  map = new maplibregl.Map({ container: "mini-map", style: BASEMAP_STYLE, center: [117.5, -1.5], zoom: 3.6, attributionControl: { compact: true } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.on("click", (e) => setLocation(e.lngLat.lat, e.lngLat.lng, null, "map_pick"));
}

function inIndonesia(lat, lon) {
  return lon >= ID_BBOX.w && lon <= ID_BBOX.e && lat >= ID_BBOX.s && lat <= ID_BBOX.n;
}

function setLocation(lat, lon, accuracy, source) {
  state.loc = { lat, lon, accuracy, source };
  const r = $("#loc-readout");
  r.hidden = false;
  const outside = !inIndonesia(lat, lon);
  r.className = `loc-readout ${outside ? "warn" : ""}`;
  fill(r,
    svg(outside ? "alert" : "pin", "icon icon-sm"),
    el("span", { class: "num" }, `${lat.toLocaleString(locale(), { maximumFractionDigits: 5 })}, ${lon.toLocaleString(locale(), { maximumFractionDigits: 5 })}`),
    el("span", { class: "muted" }, outside ? t("di luar Indonesia") : source === "gps" ? t("GPS, akurasi sekitar {m} m", { m: Math.round(accuracy) }) : t("dipilih di peta")),
  );
  if (map && maplibregl) {
    if (!marker) {
      marker = new maplibregl.Marker({ color: "#0064e0", draggable: true }).setLngLat([lon, lat]).addTo(map);
      marker.on("dragend", () => { const p = marker.getLngLat(); setLocation(p.lat, p.lng, null, "map_pick"); });
    } else marker.setLngLat([lon, lat]);
    if (source === "gps") map.flyTo({ center: [lon, lat], zoom: 14 });
  }
  validate();
}

function useGps() {
  const btn = $("#btn-gps");
  const label = (text) => fill(btn, svg("crosshair", "icon icon-sm"), t(text));
  if (!("geolocation" in navigator)) return showError(t("Perangkat ini tidak mendukung GPS. Pilih lokasi dengan mengetuk peta."));
  btn.disabled = true;
  label("Mencari lokasi...");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      btn.disabled = false;
      label("Perbarui lokasi");
      setLocation(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy, "gps");
    },
    (err) => {
      btn.disabled = false;
      label("Gunakan lokasi saya");
      showError(t(err.code === 1 ? "Izin lokasi ditolak. Ketuk peta untuk memilih lokasi secara manual." : "Lokasi tidak ditemukan. Coba lagi di tempat terbuka, atau ketuk peta."));
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 },
  );
}

/* Photos */
async function shrink(file) {
  // Re-encoding on a canvas also drops EXIF on the device. The server strips it again regardless.
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, MAX_EDGE / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.85));
    return blob ? new File([blob], (file.name || "foto").replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file;
  }
}

async function addPhotos(files) {
  const room = MAX_PHOTOS - state.photos.length;
  const chosen = [...files].filter((f) => f.type.startsWith("image/")).slice(0, room);
  if ([...files].length > room) showError(t("Maksimal {n} foto. Foto berlebih tidak ditambahkan.", { n: MAX_PHOTOS }));
  for (const f of chosen) {
    const small = await shrink(f);
    state.photos.push({ file: small, url: URL.createObjectURL(small) });
  }
  renderThumbs();
}

function renderThumbs() {
  fill($("#thumbs"),
    state.photos.map((p, i) =>
      el("div", { class: "thumb" }, el("img", { src: p.url, alt: t("Foto {n}", { n: i + 1 }) }),
        el("button", { type: "button", class: "thumb-x", "aria-label": t("Hapus foto {n}", { n: i + 1 }), onclick: () => { URL.revokeObjectURL(p.url); state.photos.splice(i, 1); renderThumbs(); } }, svg("x", "icon icon-sm")))),
  );
  $("#photo-drop").hidden = state.photos.length >= MAX_PHOTOS;
  validate();
}

/* Voice */
let recorder, chunks, timer, startedAt, player;

function pickMime() {
  const options = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  return options.find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";
}

function fmtSecs(s) {
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

function renderRecorder(mode = "idle") {
  const box = $("#recorder");
  if (!window.MediaRecorder || !navigator.mediaDevices?.getUserMedia) {
    fill(box, el("p", { class: "empty" }, t("Perekam suara tidak didukung di browser ini. Tulis keterangan di langkah 4.")));
    return;
  }
  if (mode === "recording") {
    const time = el("span", { class: "rec-time num", id: "rec-time" }, "0:00");
    fill(box, el("div", { class: "rec-live" }, el("span", { class: "pulse rec-dot" }), t("Merekam"), time, el("span", { class: "muted" }, t("maks {t}", { t: fmtSecs(MAX_RECORD_S) }))),
      el("button", { type: "button", class: "btn btn-block", onclick: stopRecording }, svg("stop", "icon icon-sm"), t("Selesai")));
    return;
  }
  if (state.audio) {
    const label = (playing) => [svg(playing ? "stop" : "play", "icon icon-sm"), t(playing ? "Berhenti" : "Putar")];
    const play = el("button", { type: "button", class: "btn", "aria-label": t("Putar pesan suara") }, label(false));
    play.onclick = () => {
      if (!player) player = new Audio(state.audio.url);
      if (player.paused) { player.play(); fill(play, label(true)); player.onended = () => fill(play, label(false)); }
      else { player.pause(); player.currentTime = 0; fill(play, label(false)); }
    };
    fill(box, el("div", { class: "rec-done" }, svg("mic", "icon icon-sm"), el("span", {}, `${t("Pesan suara")} `, el("span", { class: "num" }, fmtSecs(state.audio.secs))), play,
      el("button", { type: "button", class: "btn btn-ghost-danger", "aria-label": t("Hapus pesan suara"), onclick: () => { player?.pause(); player = null; URL.revokeObjectURL(state.audio.url); state.audio = null; renderRecorder(); validate(); } }, svg("trash", "icon icon-sm"))));
    return;
  }
  fill(box, el("button", { type: "button", class: "btn btn-block", onclick: startRecording }, svg("mic", "icon icon-sm"), t("Rekam pesan suara")));
}

async function startRecording() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    return showError(t("Izin mikrofon ditolak. Anda tetap bisa mengirim foto dan keterangan."));
  }
  const mime = pickMime();
  recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = () => {
    stream.getTracks().forEach((track) => track.stop());
    clearInterval(timer);
    const type = (recorder.mimeType || mime || "audio/webm").split(";")[0];
    const blob = new Blob(chunks, { type });
    const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
    state.audio = { blob, type, ext, url: URL.createObjectURL(blob), secs: (Date.now() - startedAt) / 1000 };
    renderRecorder();
    validate();
  };
  startedAt = Date.now();
  recorder.start();
  renderRecorder("recording");
  timer = setInterval(() => {
    const s = (Date.now() - startedAt) / 1000;
    const clock = $("#rec-time");
    if (clock) clock.textContent = fmtSecs(s);
    if (s >= MAX_RECORD_S) stopRecording();
  }, 250);
}

function stopRecording() {
  if (recorder && recorder.state !== "inactive") recorder.stop();
}

/* Validation and submit */
function hasContent() {
  return state.photos.length > 0 || !!state.audio || $("#text").value.trim().length > 0;
}

function validate() {
  const ok = state.loc && inIndonesia(state.loc.lat, state.loc.lon) && hasContent() && state.consent && !state.sending;
  // Looks inactive until ready but stays clickable, so a tap can explain what is missing.
  const b = $("#btn-submit");
  b.classList.toggle("is-inactive", !ok);
  b.setAttribute("aria-disabled", String(!ok));
  b.disabled = state.sending;
  return ok;
}

function missing() {
  if (!state.loc) return t("Tentukan lokasi dulu: ketuk \"Gunakan lokasi saya\" atau ketuk peta.");
  if (!inIndonesia(state.loc.lat, state.loc.lon)) return t("Lokasi berada di luar wilayah Indonesia.");
  if (!hasContent()) return t("Tambahkan minimal satu foto, pesan suara, atau keterangan.");
  if (!state.consent) return t("Centang pernyataan di atas tombol kirim.");
  return null;
}

function showError(msg) {
  const e = $("#form-error");
  e.hidden = !msg;
  fill(e, msg ? [svg("alert", "icon icon-sm"), el("span", {}, msg)] : []);
}

function send() {
  if (state.sending) return;
  const why = missing();
  if (why) return showError(why);
  showError(null);
  state.sending = true;
  validate();
  const fd = new FormData();
  fd.append("lat", String(state.loc.lat));
  fd.append("lon", String(state.loc.lon));
  fd.append("location_source", state.loc.source);
  if (state.loc.accuracy != null) fd.append("accuracy_m", String(Math.round(state.loc.accuracy)));
  const text = $("#text").value.trim();
  if (text) fd.append("text", text);
  state.photos.forEach((p, i) => fd.append("photos", p.file, p.file.name || `foto-${i + 1}.jpg`));
  if (state.audio) fd.append("audio", state.audio.blob, `suara.${state.audio.ext}`);

  // XHR rather than fetch: fetch cannot report upload progress, which matters on slow mobile links.
  const xhr = new XMLHttpRequest();
  const bar = $("#progress-bar");
  $("#progress").hidden = false;
  bar.style.width = "0%";
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.style.width = `${Math.round((e.loaded / e.total) * 100)}%`; };
  xhr.onload = () => {
    state.sending = false;
    $("#progress").hidden = true;
    let body = {};
    try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON error page */ }
    if (xhr.status === 201) return showSuccess(body);
    showError(body.detail && typeof body.detail === "string" ? body.detail : t("Laporan belum terkirim (kode {code}). Coba lagi.", { code: xhr.status }));
    validate();
  };
  xhr.onerror = () => {
    state.sending = false;
    $("#progress").hidden = true;
    showError(t("Koneksi terputus. Laporan belum terkirim; coba lagi saat sinyal lebih baik."));
    validate();
  };
  xhr.open("POST", "/api/reports");
  xhr.send(fd);
}

function showSuccess(body) {
  $("#report-form").hidden = true;
  const s = $("#success");
  s.hidden = false;
  fill(s,
    el("div", { class: "success-icon" }, svg("check")),
    el("h2", {}, t("Laporan diterima")),
    el("p", { class: "muted" }, t("Nomor insiden")),
    el("p", { class: "success-id mono" }, body.incident_id),
    el("p", {}, t(body.joined_existing ? "Laporan Anda digabung dengan insiden yang sudah terpantau di dekat lokasi itu." : "Laporan Anda membuka insiden baru.")),
    el("p", { class: "muted" }, t("Status: menunggu verifikasi manusia. Fight Karhutla tidak mengirim perintah ke siapa pun; verifikator yang memutuskan langkah berikutnya.")),
    el("div", { class: "actions" },
      el("a", { class: "btn btn-primary", href: "/" }, svg("layers", "icon icon-sm"), t("Lihat peta")),
      el("button", { type: "button", class: "btn", onclick: () => location.reload() }, t("Kirim laporan lain"))),
  );
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function main() {
  applyStatic(t);
  hydrateIcons();
  $("#lang-slot").replaceWith(langSwitch(el));
  siteFooter($("#site-footer"), lang());
  $("#btn-gps").addEventListener("click", useGps);
  $("#photo-input").addEventListener("change", (e) => { addPhotos(e.target.files); e.target.value = ""; });
  const drop = $("#photo-drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); addPhotos(e.dataTransfer.files); });
  const text = $("#text");
  text.addEventListener("input", () => { $("#counter").textContent = `${text.value.length} / 1000`; validate(); });
  const consent = $("#consent");
  consent.addEventListener("click", () => { state.consent = !state.consent; consent.setAttribute("aria-checked", String(state.consent)); validate(); });
  $("#report-form").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  renderRecorder();
  validate();
  // A verifier can share a link with the incident location filled in (from the incident view).
  const q = new URLSearchParams(location.search);
  const lat = Number(q.get("lat"));
  const lon = Number(q.get("lon"));
  const preset = q.has("lat") && q.has("lon") && Number.isFinite(lat) && Number.isFinite(lon);
  initMiniMap().then(() => {
    if (!preset) return;
    setLocation(lat, lon, null, "map_pick");
    map.flyTo({ center: [lon, lat], zoom: 13 });
  });
}

main();
