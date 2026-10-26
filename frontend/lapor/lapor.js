import { $, el, hydrateIcons, svg } from "/ui.js";

const MAPLIBRE_URL = "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
const MAX_PHOTOS = 3;
const MAX_EDGE = 2048; // matches the server; shrinking here saves the reporter's mobile data
const MAX_RECORD_S = 120;
// Generous Indonesia box, the same sanity check the server applies.
const ID_BBOX = { w: 94.5, s: -11.5, e: 141.5, n: 6.5 };

const state = { loc: null, photos: [], audio: null, consent: false, sending: false };

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
  r.replaceChildren(
    svg(outside ? "alert" : "pin", "icon icon-sm"),
    el("span", { class: "num" }, `${lat.toFixed(5)}, ${lon.toFixed(5)}`),
    el("span", { class: "muted" }, outside ? "di luar Indonesia" : source === "gps" ? `GPS, akurasi sekitar ${Math.round(accuracy)} m` : "dipilih di peta"),
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
  if (!("geolocation" in navigator)) return showError("Perangkat ini tidak mendukung GPS. Pilih lokasi dengan mengetuk peta.");
  btn.disabled = true;
  btn.replaceChildren(svg("crosshair", "icon icon-sm"), "Mencari lokasi...");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      btn.disabled = false;
      btn.replaceChildren(svg("crosshair", "icon icon-sm"), "Perbarui lokasi");
      setLocation(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy, "gps");
    },
    (err) => {
      btn.disabled = false;
      btn.replaceChildren(svg("crosshair", "icon icon-sm"), "Gunakan lokasi saya");
      showError(err.code === 1 ? "Izin lokasi ditolak. Ketuk peta untuk memilih lokasi secara manual." : "Lokasi tidak ditemukan. Coba lagi di tempat terbuka, atau ketuk peta.");
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
  const picked = [...files].filter((f) => f.type.startsWith("image/")).slice(0, room);
  if ([...files].length > room) showError(`Maksimal ${MAX_PHOTOS} foto. Foto berlebih tidak ditambahkan.`);
  for (const f of picked) {
    const small = await shrink(f);
    state.photos.push({ file: small, url: URL.createObjectURL(small) });
  }
  renderThumbs();
}

function renderThumbs() {
  $("#thumbs").replaceChildren(
    ...state.photos.map((p, i) =>
      el("div", { class: "thumb" }, el("img", { src: p.url, alt: `Foto ${i + 1}` }),
        el("button", { type: "button", class: "thumb-x", "aria-label": `Hapus foto ${i + 1}`, onclick: () => { URL.revokeObjectURL(p.url); state.photos.splice(i, 1); renderThumbs(); } }, svg("x", "icon icon-sm")))),
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
    box.replaceChildren(el("p", { class: "empty" }, "Perekam suara tidak didukung di browser ini. Tulis keterangan di langkah 4."));
    return;
  }
  if (mode === "recording") {
    const time = el("span", { class: "rec-time num", id: "rec-time" }, "0:00");
    box.replaceChildren(el("div", { class: "rec-live" }, el("span", { class: "pulse rec-dot" }), "Merekam", time, el("span", { class: "muted" }, `maks ${fmtSecs(MAX_RECORD_S)}`)),
      el("button", { type: "button", class: "btn btn-block", onclick: stopRecording }, svg("stop", "icon icon-sm"), "Selesai"));
    return;
  }
  if (state.audio) {
    const play = el("button", { type: "button", class: "btn", "aria-label": "Putar pesan suara" }, svg("play", "icon icon-sm"), "Putar");
    play.onclick = () => {
      if (!player) player = new Audio(state.audio.url);
      if (player.paused) { player.play(); play.replaceChildren(svg("stop", "icon icon-sm"), "Berhenti"); player.onended = () => play.replaceChildren(svg("play", "icon icon-sm"), "Putar"); }
      else { player.pause(); player.currentTime = 0; play.replaceChildren(svg("play", "icon icon-sm"), "Putar"); }
    };
    box.replaceChildren(el("div", { class: "rec-done" }, svg("mic", "icon icon-sm"), el("span", {}, "Pesan suara ", el("span", { class: "num" }, fmtSecs(state.audio.secs))), play,
      el("button", { type: "button", class: "btn btn-ghost-danger", "aria-label": "Hapus pesan suara", onclick: () => { player?.pause(); player = null; URL.revokeObjectURL(state.audio.url); state.audio = null; renderRecorder(); validate(); } }, svg("trash", "icon icon-sm"))));
    return;
  }
  box.replaceChildren(el("button", { type: "button", class: "btn btn-block", onclick: startRecording }, svg("mic", "icon icon-sm"), "Rekam pesan suara"));
}

async function startRecording() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    return showError("Izin mikrofon ditolak. Anda tetap bisa mengirim foto dan keterangan.");
  }
  const mime = pickMime();
  recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
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
    const t = $("#rec-time");
    if (t) t.textContent = fmtSecs(s);
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
  if (!state.loc) return "Tentukan lokasi dulu: ketuk \"Gunakan lokasi saya\" atau ketuk peta.";
  if (!inIndonesia(state.loc.lat, state.loc.lon)) return "Lokasi berada di luar wilayah Indonesia.";
  if (!hasContent()) return "Tambahkan minimal satu foto, pesan suara, atau keterangan.";
  if (!state.consent) return "Centang pernyataan di atas tombol kirim.";
  return null;
}

function showError(msg) {
  const e = $("#form-error");
  e.hidden = !msg;
  e.replaceChildren(...(msg ? [svg("alert", "icon icon-sm"), el("span", {}, msg)] : []));
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
    showError(body.detail && typeof body.detail === "string" ? body.detail : `Laporan belum terkirim (kode ${xhr.status}). Coba lagi.`);
    validate();
  };
  xhr.onerror = () => {
    state.sending = false;
    $("#progress").hidden = true;
    showError("Koneksi terputus. Laporan belum terkirim; coba lagi saat sinyal lebih baik.");
    validate();
  };
  xhr.open("POST", "/api/reports");
  xhr.send(fd);
}

function showSuccess(body) {
  $("#report-form").hidden = true;
  const s = $("#success");
  s.hidden = false;
  s.replaceChildren(
    el("div", { class: "success-icon" }, svg("check")),
    el("h2", {}, "Laporan diterima"),
    el("p", { class: "muted" }, "Nomor insiden"),
    el("p", { class: "success-id mono" }, body.incident_id),
    el("p", {}, body.joined_existing
      ? "Laporan Anda digabung dengan insiden yang sudah terpantau di dekat lokasi itu."
      : "Laporan Anda membuka insiden baru."),
    el("p", { class: "muted" }, "Status: menunggu verifikasi manusia. Fight Karhutla tidak mengirim perintah ke siapa pun; verifikator yang memutuskan langkah berikutnya."),
    el("div", { class: "actions" },
      el("a", { class: "btn btn-primary", href: "/" }, svg("layers", "icon icon-sm"), "Lihat peta"),
      el("button", { type: "button", class: "btn", onclick: () => location.reload() }, "Kirim laporan lain")),
  );
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function main() {
  hydrateIcons();
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
  initMiniMap();
}

main();
