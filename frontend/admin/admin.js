import { $, aiBadge, el, hydrateIcons, info, modal, section, siteFooter, svg, tabs, toast } from "/ui.js";
import { applyStatic, lang, langSwitch, locale, makeT } from "/i18n.js";
import { authHeaders, openSignIn, token, verifierControl, VERIFIER_ID } from "/verifier.js";

/* Admin panel: operator settings, schedule, spend, and source health. Written in English; Indonesian below.
   Reading needs the verifier token. Saving needs the execution code too, because several settings change spend. */
const ADMIN_ID = {
  "Admin panel": "Panel admin", "Map": "Peta", "Incident map": "Peta insiden",
  "Sign in to open the admin panel.": "Masuk sebagai verifikator untuk membuka panel admin.",
  "Could not load the admin panel: {e}": "Panel admin gagal dimuat: {e}",
  "System": "Sistem", "Data fetching": "Pengambilan data", "AI triage": "Triase AI", "Recent triage runs": "Riwayat triase terakhir",
  "Build": "Build", "Mode": "Mode", "Live data": "Data langsung", "Simulation (fixtures)": "Simulasi (fixture)",
  "Scheduler": "Penjadwal", "On": "Aktif", "Off": "Mati", "Meta Model API": "Meta Model API", "Baseten": "Baseten", "configured": "terpasang", "missing": "belum ada",
  "Incidents": "Insiden", "Hotspots": "Titik panas", "Citizen reports": "Laporan warga", "Sentinel-2 pairs": "Pasangan Sentinel-2", "Triaged": "Sudah ditriase",
  "Total metered spend": "Total biaya terukur", "{n} runs": "{n} run",
  "Last fetch": "Pengambilan terakhir", "Next fetch": "Pengambilan berikutnya", "never": "belum pernah", "at the next scheduler tick": "pada putaran penjadwal berikutnya",
  "FIRMS fetch interval": "Interval ambil FIRMS", "Weather lookups per fetch": "Cek cuaca per pengambilan", "OSM tiles per fetch": "Tile OSM per pengambilan",
  "Sentinel-2 reuse window": "Masa pakai ulang Sentinel-2", "Triage runs per day": "Run triase per hari", "Incidents per selection": "Insiden per pilihan",
  "Incidents per scheduled pass": "Insiden per putaran terjadwal",
  "How often new satellite hotspots are fetched for Indonesia. Each fetch is one FIRMS call; enrichment of new incidents follows it.":
    "Seberapa sering titik panas satelit baru untuk Indonesia diambil. Satu pengambilan adalah satu panggilan FIRMS, lalu insiden baru dilengkapi buktinya.",
  "Incidents that get Open-Meteo weather after each fetch. Open-Meteo allows under 10,000 calls a day.":
    "Jumlah insiden yang mendapat data cuaca Open-Meteo setiap pengambilan. Open-Meteo membatasi di bawah 10.000 panggilan per hari.",
  "New OpenStreetMap tiles queried after each fetch. The public Overpass servers are shared and often down.":
    "Tile OpenStreetMap baru yang ditanyakan setiap pengambilan. Server Overpass publik dipakai bersama dan sering mati.",
  "A stored Sentinel-2 pair is reused this long before the archive is searched again. A pass comes about every 5 days.":
    "Pasangan Sentinel-2 yang tersimpan dipakai ulang selama ini sebelum arsip dicari lagi. Satelit lewat sekitar setiap 5 hari.",
  "Hard limit on triage runs per WITA day, manual and scheduled together. Each run costs about $0.15 of Meta credit.":
    "Batas keras run triase per hari WITA, manual dan terjadwal digabung. Satu run memakai sekitar $0,15 kredit Meta.",
  "How many incidents one confirmed selection may triage.": "Jumlah insiden yang boleh ditriase dalam satu pilihan yang dikonfirmasi.",
  "How many citizen-reported incidents one scheduled pass may triage.": "Jumlah insiden laporan warga yang boleh ditriase dalam satu putaran terjadwal.",
  "default {v}": "bawaan {v}", "Scheduled triage": "Triase terjadwal", "Daily": "Harian", "Hourly": "Per jam",
  "Today": "Hari ini", "{runs} of {cap} runs, ${cost}": "{runs} dari {cap} run, ${cost}",
  "Incident": "Insiden", "Started": "Mulai", "Outcome": "Hasil", "Seconds": "Detik", "Cost": "Biaya", "No triage runs yet.": "Belum ada run triase.",
  "{n} unsaved change(s)": "{n} perubahan belum disimpan", "Discard": "Batalkan", "Save": "Simpan",
  "Save settings": "Simpan pengaturan", "Execution code": "Kode eksekusi",
  "Settings that change spend or load on shared services need the execution code.": "Pengaturan yang mengubah biaya atau beban layanan bersama memerlukan kode eksekusi.",
  "Saved: {list}.": "Tersimpan: {list}.", "Not saved: {e}": "Tidak tersimpan: {e}",
  "Set schedule": "Ubah jadwal", "Schedule set to {mode}.": "Jadwal diubah ke {mode}.",
  "30 min": "30 menit", "1 h": "1 jam", "3 h": "3 jam", "6 h": "6 jam", "12 h": "12 jam", "1 day": "1 hari", "2 days": "2 hari",
  "Data sources": "Sumber data", "State": "Status", "Last checked": "Terakhir dicek",
  "ok": "aktif", "error": "gangguan", "sim": "simulasi", "unknown": "belum dicek",
  "Demo replay": "Replay demo", "Load replay": "Muat replay", "Remove replay": "Hapus replay", "Remove": "Hapus", "Cancel": "Batal",
  "Three recorded incidents from the 2025 dry season (clearly burned, false positive, needs more info), loaded as SIMULASI. They come from files in the app, so loading needs no network and no model call.":
    "Tiga insiden rekaman dari musim kemarau 2025 (jelas terbakar, positif palsu, butuh info tambahan), dimuat sebagai SIMULASI. Datanya dari berkas di aplikasi, jadi memuatnya tidak perlu jaringan dan tidak memanggil model.",
  "Not loaded": "Belum dimuat", "{n} incidents loaded": "{n} insiden dimuat", "No recorded cases in this build.": "Build ini tidak berisi kasus rekaman.",
  "Replay loaded.": "Replay dimuat.", "Replay was already loaded.": "Replay sudah dimuat.", "Replay removed.": "Replay dihapus.", "Failed: {e}": "Gagal: {e}",
  "Remove the replayed incidents, their evidence, and any verdicts given on them? Real incidents are not touched, and the replay can be loaded again.":
    "Hapus insiden replay, buktinya, dan keputusan yang sudah diberikan padanya? Insiden nyata tidak tersentuh, dan replay bisa dimuat lagi.",
  "Open on map": "Buka di peta", "Recorded with {m}.": "Direkam dengan {m}.", "Indonesian text translated from the recording by {m}; the recording is unchanged.": "Teks Indonesia diterjemahkan dari rekaman oleh {m}; rekamannya tidak berubah.",
  "Overview": "Ringkasan", "WhatsApp": "WhatsApp", "Cloud API": "Cloud API", "Last inbound message": "Pesan masuk terakhir", "Verifier numbers": "Nomor verifikator",
  "Meta refuses outgoing messages from this business account until it passes business verification. Incoming reports work; every reply is still attempted and its refusal recorded.":
    "Meta menolak pesan keluar dari akun bisnis ini sampai lolos verifikasi bisnis. Laporan masuk berjalan; setiap balasan tetap dicoba dan penolakannya dicatat.",
  "Recent outgoing messages": "Pesan keluar terakhir", "Numbers are shown as the first characters of a salted hash.": "Nomor ditampilkan sebagai karakter awal dari hash bergaram.",
  "Sent": "Dikirim", "Purpose": "Tujuan", "To": "Ke", "Result": "Hasil", "reply": "balasan", "verifier": "verifikator", "verdict": "keputusan",
  "failed": "gagal", "sent": "terkirim", "No outgoing messages yet.": "Belum ada pesan keluar.", "Open the WhatsApp report log": "Buka log laporan WhatsApp",
  "Reports received": "Laporan masuk", "Items processed": "Isi diproses", "Photos read by Muse Spark": "Foto dibaca Muse Spark", "Voice notes transcribed": "Suara ditranskripsi",
  "Latest reports": "Laporan terbaru", "Received": "Diterima", "Meta models": "Model Meta", "Status": "Status", "text": "teks", "photo": "foto", "voice": "suara",
  "transcribed": "ditranskripsi", "photo read": "foto dibaca", "Pending triage": "Menunggu triase", "Triage running": "Triase berjalan", "Likely fire": "Kemungkinan besar api", "Possible fire": "Mungkin api", "Unlikely": "Kecil kemungkinan", "Needs more info": "Perlu info tambahan", "No WhatsApp reports yet.": "Belum ada laporan WhatsApp.",
  "Outgoing replies and alerts": "Balasan dan notifikasi keluar", "{n} attempted, {r} refused by Meta (account not verified).": "{n} dicoba, {r} ditolak Meta (akun belum terverifikasi).",
  "Meta refuses outgoing messages from this business account until it passes business verification. Incoming reports work; every reply is still attempted and its refusal recorded. Numbers are shown as the first characters of a salted hash.":
    "Meta menolak pesan keluar dari akun bisnis ini sampai lolos verifikasi bisnis. Laporan masuk berjalan; setiap balasan tetap dicoba dan penolakannya dicatat. Nomor ditampilkan sebagai karakter awal dari hash bergaram.",
  "refused by Meta": "ditolak Meta", "accepted": "diterima",
  "Weather": "Cuaca",
  "Verification": "Verifikasi", "Drone": "Drone", "Refresh": "Muat ulang", "Updated {t}": "Diperbarui {t}",
  "Needs attention": "Perlu perhatian", "All clear": "Semua lancar",
  "{n} AI recommendation(s) wait for a verifier": "{n} rekomendasi AI menunggu verifikator",
  "{n} citizen report(s) not triaged": "{n} laporan warga belum ditriase", "{n} drone plan(s) wait for approval": "{n} rencana drone menunggu persetujuan",
  "{n} eligible incident(s) still without evidence": "{n} insiden layak belum punya bukti", "{src}: {detail}": "{src}: {detail}",
  "Overpass is down; nearby places come from the offline file": "Overpass mati; tempat sekitar diambil dari salinan offline",
  "WhatsApp media download failing (access token?)": "Unduhan media WhatsApp gagal (token akses?)",
  "AI recommendations waiting for a decision": "Rekomendasi AI menunggu keputusan",
  "Approve or reject on the incident page, where the evidence is. A revoked decision returns here.":
    "Setujui atau tolak di halaman insiden, tempat buktinya. Keputusan yang dibatalkan kembali ke sini.",
  "Nothing waits for a decision.": "Tidak ada yang menunggu keputusan.", "AI status": "Status AI", "Confidence": "Keyakinan", "Triaged": "Ditriase",
  "Evidence": "Bukti", "Open": "Buka", "Practice (replay)": "Latihan (replay)",
  "Citizen reports not triaged yet": "Laporan warga belum ditriase", "No citizen report waits.": "Tidak ada laporan warga yang menunggu.",
  "Channel": "Kanal", "Items": "Isi", "web form": "formulir web",
  "Drone plans waiting for approval": "Rencana drone menunggu persetujuan", "No drone plan waits.": "Tidak ada rencana drone yang menunggu.",
  "Plan": "Rencana", "Proposed by": "Diusulkan oleh", "agent": "agen",
  "WhatsApp reports not shown publicly": "Laporan WhatsApp belum tampil publik",
  "A verifier decides on the WhatsApp report log whether a report becomes a public example.": "Verifikator memutuskan di log laporan WhatsApp apakah laporan menjadi contoh publik.",
  "Every WhatsApp report has been decided.": "Semua laporan WhatsApp sudah diputuskan.",
  "Recent decisions": "Keputusan terakhir", "No decisions yet.": "Belum ada keputusan.", "Decision": "Keputusan", "Note": "Catatan", "When": "Waktu", "Verifier": "Verifikator",
  "approve": "setuju", "reject": "tolak", "revoke": "batal",
  "Export": "Ekspor", "Incidents (CSV)": "Insiden (CSV)", "Decisions (CSV)": "Keputusan (CSV)",
  "Exact coordinates and verifier hashes: keep the file inside the team.": "Koordinat persis dan hash verifikator: simpan berkasnya di dalam tim.",
  "Incident status": "Status insiden", "Real incidents by status; replayed ones are counted apart.": "Insiden nyata per status; insiden replay dihitung terpisah.",
  "{n} simulated (replay)": "{n} simulasi (replay)", "{n} in the last 24 h": "{n} dalam 24 jam terakhir", "newest {t}": "terbaru {t}",
  "Detail": "Keterangan", "display only, not checked": "tampilan saja, tidak dicek", "from the offline file": "dari salinan offline",
  "WhatsApp media": "Media WhatsApp", "Overture Maps": "Overture Maps",
  "Offline data file": "Salinan data offline", "Used for nearby places: OSM rows when Overpass is down, Overture places always.":
    "Dipakai untuk tempat sekitar: baris OSM saat Overpass mati, tempat Overture selalu.",
  "Not downloaded yet.": "Belum diunduh.", "OpenStreetMap extract": "Ekstrak OpenStreetMap", "Overture release": "Rilis Overture", "Size": "Ukuran",
  "Overpass": "Overpass", "down, using the file": "mati, memakai salinan", "answering": "menjawab",
  "canal": "kanal", "stream": "anak sungai", "river": "sungai", "lake": "danau", "road": "jalan", "settlement": "permukiman",
  "fire_station": "pos pemadam", "police": "polisi", "military": "tentara", "government": "pemerintahan",
  "System": "Sistem", "Run now": "Jalankan sekarang", "Operator actions": "Aksi operator",
  "None of these calls a model. They load shared services, so they need the execution code.": "Tidak ada yang memanggil model. Aksi ini membebani layanan bersama, jadi butuh kode eksekusi.",
  "Fetch FIRMS now": "Ambil FIRMS sekarang", "One FIRMS call for Indonesia, then evidence for new incidents. The schedule counts from this fetch.":
    "Satu panggilan FIRMS untuk Indonesia, lalu bukti untuk insiden baru. Jadwal dihitung dari pengambilan ini.",
  "Collect pending evidence": "Kumpulkan bukti tertunda", "Weather and nearby places for eligible incidents that have none or had a failed source, within the budgets below.":
    "Cuaca dan tempat sekitar untuk insiden layak yang belum punya atau sumbernya gagal, dalam kuota di bawah.",
  "Look for Sentinel-2 pairs": "Cari pasangan Sentinel-2", "For the largest real incidents (3 or more hotspots) without a pair from the reuse window. Open data, about 10 to 30 s each.":
    "Untuk insiden nyata terbesar (3 titik panas atau lebih) yang belum punya pasangan dalam masa pakai ulang. Data terbuka, sekitar 10 sampai 30 detik per insiden.",
  "Incidents": "Insiden", "running since {t}": "berjalan sejak {t}", "done {t}": "selesai {t}", "failed {t}: {e}": "gagal {t}: {e}",
  "Started: {job}.": "Dimulai: {job}.", "Already running: {job}.": "Sedang berjalan: {job}.", "Run": "Jalankan",
  "Evidence coverage": "Cakupan bukti", "Eligible incidents: 3 or more hotspots, or a fire radiative power of 20 MW or more, or a citizen report.":
    "Insiden layak: 3 titik panas atau lebih, atau daya radiasi api 20 MW atau lebih, atau laporan warga.",
  "{a} of {b} eligible incidents have evidence": "{a} dari {b} insiden layak sudah punya bukti", "Failed sources": "Sumber gagal", "none": "tidak ada",
  "Settings changes": "Perubahan pengaturan", "No changes recorded yet.": "Belum ada perubahan tercatat.", "Setting": "Pengaturan", "From": "Dari",
  "Newest detection": "Deteksi terbaru",
  "Spend": "Biaya", "Meta Model API usage is metered per run; Baseten runs on hackathon credit and is not metered. The credit balance is not available through the API.":
    "Pemakaian Meta Model API diukur per run; Baseten memakai kredit hackathon dan tidak diukur. Saldo kredit tidak tersedia lewat API.",
  "Average per completed run": "Rata-rata per run selesai", "By outcome": "Per hasil", "Runs": "Run",
  "ok": "selesai", "schema_failed": "gagal skema", "error": "galat", "running": "berjalan",
  "A run that fails the schema still costs and replaces the earlier triage.": "Run yang gagal skema tetap berbiaya dan menimpa triase sebelumnya.",
  "Drone surveys (simulation)": "Survei drone (simulasi)", "Plans": "Rencana", "Flights": "Penerbangan",
  "proposed": "diusulkan", "approved": "disetujui", "rejected": "ditolak", "cancelled": "dibatalkan", "flying": "terbang", "completed": "selesai", "aborted": "dihentikan",
  "No drone plans yet.": "Belum ada rencana drone.", "Latest plans": "Rencana terakhir", "Flight": "Penerbangan",
  "Plans are proposed by the agent or a verifier and approved by a verifier only; the pilot and the drone are simulated.":
    "Rencana diusulkan agen atau verifikator dan hanya disetujui verifikator; pilot dan drone disimulasikan.",
};
const t = makeT({ ...ADMIN_ID, ...VERIFIER_ID }, "en");
const INTERVAL_LABEL = { 30: "30 min", 60: "1 h", 180: "3 h", 360: "6 h", 720: "12 h", 1440: "1 day", 2880: "2 days" };
const MODE_LABEL = { off: "Off", daily: "Daily", hourly: "Hourly" };

const state = { data: null, pending: {} };
// Operators work in WITA, so times are shown there whatever the browser's zone.
const fmtTime = (iso) => (iso ? `${new Date(iso).toLocaleString(locale(), { timeZone: "Asia/Makassar", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} WITA` : t("never"));
const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

function card(icon, title, ...content) {
  return el("section", { class: "card admin-card" }, el("h2", { class: "admin-title" }, el("span", { class: "section-icon" }, svg(icon, "icon icon-sm")), title), content);
}

function kvRow(label, value) {
  return [el("dt", {}, label), el("dd", {}, value)];
}

function settingControl(s) {
  const current = s.key in state.pending ? state.pending[s.key] : s.value;
  const set = (v) => {
    if (v === s.value) delete state.pending[s.key];
    else state.pending[s.key] = v;
    render();
  };
  let control;
  if (s.choices) {
    control = el("div", { class: "segmented admin-seg", role: "group", "aria-label": t(s.label) },
      s.choices.map((c) => el("button", { type: "button", "aria-pressed": String(c === current), onclick: () => set(c) }, t(INTERVAL_LABEL[c] || String(c)))));
  } else {
    const input = el("input", { type: "number", class: "input admin-num", min: s.min, max: s.max, step: 1, value: current, "aria-label": t(s.label) });
    input.addEventListener("change", () => {
      const v = Math.round(Number(input.value));
      if (Number.isFinite(v)) set(Math.min(s.max, Math.max(s.min, v)));
    });
    control = el("div", { class: "admin-num-wrap" }, input, s.unit ? el("span", { class: "muted" }, s.unit) : null);
  }
  return el("div", { class: `admin-setting ${s.key in state.pending ? "changed" : ""}` },
    el("div", { class: "admin-setting-head" }, el("span", { class: "admin-label" }, t(s.label), info(t(s.help))),
      el("span", { class: "muted admin-default" }, t("default {v}", { v: s.choices ? t(INTERVAL_LABEL[s.default] || String(s.default)) : `${s.default}${s.unit ? " " + s.unit : ""}` }))),
    control);
}

function changeMode(mode) {
  const code = el("input", { type: "password", class: "input", placeholder: t("Execution code"), "aria-label": t("Execution code") });
  const err = el("p", { class: "form-error", hidden: true });
  const ok = el("button", { class: "btn btn-primary", type: "button" }, t("Set schedule"));
  const dlg = modal({ title: `${t("Scheduled triage")}: ${t(MODE_LABEL[mode])}`, body: [code, err],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Cancel")), ok] });
  const go = async () => {
    ok.disabled = true;
    try {
      await api("/api/triage/control/mode", { method: "PUT", body: JSON.stringify({ mode, code: code.value }) });
      dlg.close();
      toast(t("Schedule set to {mode}.", { mode: t(MODE_LABEL[mode]) }), "check");
      load();
    } catch (e) {
      err.hidden = false;
      fill(err, svg("alert", "icon icon-sm"), el("span", {}, e.message));
      ok.disabled = false;
    }
  };
  ok.onclick = go;
  code.addEventListener("keydown", (e) => e.key === "Enter" && go());
}

function save() {
  const code = el("input", { type: "password", class: "input", placeholder: t("Execution code"), "aria-label": t("Execution code") });
  const err = el("p", { class: "form-error", hidden: true });
  const ok = el("button", { class: "btn btn-primary", type: "button" }, t("Save"));
  const list = Object.entries(state.pending).map(([k, v]) => {
    const s = state.data.settings.find((x) => x.key === k);
    const fmt = (x) => (s.choices ? t(INTERVAL_LABEL[x] || String(x)) : `${x}${s.unit ? " " + s.unit : ""}`);
    return el("li", {}, `${t(s.label)}: ${fmt(s.value)} → ${fmt(v)}`);
  });
  const dlg = modal({ title: t("Save settings"), body: [el("ul", { class: "admin-diff" }, list), el("p", { class: "muted", style: "margin:0" }, t("Settings that change spend or load on shared services need the execution code.")), code, err],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Cancel")), ok] });
  const go = async () => {
    ok.disabled = true;
    try {
      const r = await api("/api/admin/settings", { method: "PUT", body: JSON.stringify({ values: state.pending, code: code.value }) });
      dlg.close();
      state.pending = {};
      state.data = r;
      toast(t("Saved: {list}.", { list: r.changed.map((k) => t(r.settings.find((s) => s.key === k).label)).join(", ") }), "check");
      render();
    } catch (e) {
      err.hidden = false;
      fill(err, svg("alert", "icon icon-sm"), el("span", {}, t("Not saved: {e}", { e: e.message })));
      ok.disabled = false;
    }
  };
  ok.onclick = go;
  code.addEventListener("keydown", (e) => e.key === "Enter" && go());
}

const pill = (cls, text) => el("span", { class: `pill ${cls}` }, text);
const statusPill = (st) => el("span", { class: "status", dataset: { status: st } }, t(STATUS_LABEL[st] || st));
const incLink = (id) => el("a", { class: "mono", href: `/?incident=${encodeURIComponent(id)}` }, id);
const table = (head, rows, cls = "") => el("div", { class: "doc-table-wrap" }, el("table", { class: `hs-table admin-table ${cls}` },
  el("thead", {}, el("tr", {}, head.map((h) => el("th", {}, h)))), el("tbody", {}, rows)));
const td = (label, ...kids) => el("td", { "data-label": label }, kids);
const num = (v) => Number(v ?? 0).toLocaleString(locale());
const SOURCE_NAME = { firms: "NASA FIRMS", weather: "Open-Meteo", osm: "OpenStreetMap", sentinel2: "Sentinel-2", whatsapp_media: "WhatsApp media" };

function stat(value, label, sub, href) {
  const body = [el("div", { class: "stat-v num" }, value), el("div", { class: "stat-l" }, label), sub ? el("div", { class: "stat-sub" }, sub) : null];
  return href ? el("a", { class: "stat stat-link", href }, body) : el("div", { class: "stat" }, body);
}

/* What needs a person or is broken, above every tab. Each line says what and links to where it is handled. */
function attention(d, q) {
  const items = [];
  const real = (q?.awaiting_decision || []).filter((a) => !a.simulation).length;
  if (real) items.push(["alert", t("{n} AI recommendation(s) wait for a verifier", { n: real }), "#tab=queue"]);
  if (q?.citizen_pending?.length) items.push(["chat", t("{n} citizen report(s) not triaged", { n: q.citizen_pending.length }), "#tab=queue"]);
  if (q?.drone_proposed?.length) items.push(["target", t("{n} drone plan(s) wait for approval", { n: q.drone_proposed.length }), "#tab=queue"]);
  Object.entries(d.sources).filter(([, v]) => v.state === "error").forEach(([k, v]) => items.push(["alert", t("{src}: {detail}", { src: t(SOURCE_NAME[k] || k), detail: v.detail || t("error") }), "#tab=system"]));
  if (d.sources.whatsapp_media?.state === "error") items.push(["alert", t("WhatsApp media download failing (access token?)"), "#tab=whatsapp"]);
  if (d.offline?.overpass_down) items.push(["clock", t("Overpass is down; nearby places come from the offline file"), "#tab=system"]);
  if (d.coverage?.pending > 0) items.push(["file", t("{n} eligible incident(s) still without evidence", { n: d.coverage.pending }), "#tab=data"]);
  const nav = (href) => (e) => { e.preventDefault(); state.view?.select(href.split("=")[1]); };
  return el("section", { class: `card admin-attn ${items.length ? "" : "clear"}` },
    el("div", { class: "admin-attn-head" }, svg(items.length ? "alert" : "check", "icon icon-sm"), el("strong", {}, t(items.length ? "Needs attention" : "All clear"))),
    items.length ? el("ul", {}, items.map(([icon, text, href]) => el("li", {}, el("a", { href, onclick: nav(href) }, svg(icon, "icon icon-sm"), text)))) : null);
}

function queueTab(q) {
  if (!q) return card("check", t("Verification"), el("p", { class: "empty" }, t("Could not load the admin panel: {e}", { e: state.queueError || "" })));
  const evChips = (types) => el("div", { class: "chips" }, types.map((x) => pill("", t({ hotspot: "Hotspots", weather: "Weather", osm: "OSM", satellite: "Sentinel-2", report: "text", image: "photo", voice: "voice" }[x] || x))));
  const awaiting = card("gavel", [t("AI recommendations waiting for a decision"), info(t("Approve or reject on the incident page, where the evidence is. A revoked decision returns here."))],
    q.awaiting_decision.length ? table([t("Incident"), t("AI status"), t("Confidence"), t("Triaged"), t("Evidence"), ""],
      q.awaiting_decision.map((a) => el("tr", {}, td(t("Incident"), incLink(a.id), a.simulation ? [" ", pill("pill-sim", t("Practice (replay)"))] : null), td(t("AI status"), statusPill(a.status)),
        td(t("Confidence"), a.confidence == null ? "" : `${Math.round(a.confidence * 100)}%`), td(t("Triaged"), fmtTime(a.triaged_at)), td(t("Evidence"), evChips(a.evidence)),
        td("", el("a", { class: "btn btn-sm", href: `/?incident=${encodeURIComponent(a.id)}` }, t("Open"))))), "admin-stack")
      : el("p", { class: "empty" }, t("Nothing waits for a decision.")));
  const citizen = card("chat", t("Citizen reports not triaged yet"),
    q.citizen_pending.length ? table([t("Incident"), t("Channel"), t("Items"), t("Received")],
      q.citizen_pending.map((c) => el("tr", {}, td(t("Incident"), incLink(c.id)), td(t("Channel"), c.channel === "whatsapp" ? "WhatsApp" : t("web form")), td(t("Items"), String(c.items)), td(t("Received"), fmtTime(c.created_at)))), "admin-stack")
      : el("p", { class: "empty" }, t("No citizen report waits.")));
  const drone = card("target", t("Drone plans waiting for approval"),
    q.drone_proposed.length ? table([t("Plan"), t("Incident"), t("Proposed by"), t("Received")],
      q.drone_proposed.map((p) => el("tr", {}, td(t("Plan"), `#${p.id}`), td(t("Incident"), incLink(p.incident_id)), td(t("Proposed by"), t(p.source === "agent" ? "agent" : "verifier")), td(t("Received"), fmtTime(p.created_at)))), "admin-stack")
      : el("p", { class: "empty" }, t("No drone plan waits.")));
  const wa = card("send", [t("WhatsApp reports not shown publicly"), info(t("A verifier decides on the WhatsApp report log whether a report becomes a public example."))],
    q.wa_unshown.length ? el("ul", { class: "admin-list" }, q.wa_unshown.map((w) => el("li", {}, incLink(w.id), el("span", { class: "muted" }, fmtTime(w.received_at)))))
      : el("p", { class: "empty" }, t("Every WhatsApp report has been decided.")),
    el("div", { class: "actions" }, el("a", { class: "btn", href: "/whatsapp/" }, svg("chat", "icon icon-sm"), t("Open the WhatsApp report log"))));
  const decisions = card("list", t("Recent decisions"),
    q.recent_decisions.length ? table([t("When"), t("Incident"), t("Decision"), t("Note"), t("Verifier")],
      q.recent_decisions.map((v) => el("tr", {}, td(t("When"), fmtTime(v.created_at)), td(t("Incident"), incLink(v.incident_id), v.simulation ? [" ", pill("pill-sim", "SIMULASI")] : null),
        td(t("Decision"), pill(`pill-decision-${v.decision}`, t(v.decision))), td(t("Note"), v.note || ""), td(t("Verifier"), el("span", { class: "mono" }, `#${v.verifier_hash.slice(0, 6)}`)))), "admin-stack")
      : el("p", { class: "empty" }, t("No decisions yet.")),
    el("div", { class: "actions" }, exportBtn("incidents", t("Incidents (CSV)")), exportBtn("verdicts", t("Decisions (CSV)")), info(t("Exact coordinates and verifier hashes: keep the file inside the team."))));
  return [awaiting, el("div", { class: "admin-dash" }, citizen, drone), el("div", { class: "admin-dash" }, wa, decisions)];
}

function exportBtn(name, label) {
  const b = el("button", { class: "btn", type: "button" }, svg("download", "icon icon-sm"), label);
  // The export needs the verifier token, which a plain link can not send: fetch it, then hand the browser a file.
  b.onclick = async () => {
    b.disabled = true;
    try {
      const res = await fetch(`/api/admin/export/${name}.csv`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const a = el("a", { href: url, download: (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `${name}.csv` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) {
      toast(t("Failed: {e}", { e: e.message }), "alert");
    }
    b.disabled = false;
  };
  return b;
}

function systemTab(d) {
  const b = d.build;
  const sim = d.simulated_incidents || 0;
  const tiles = el("div", { class: "stats-grid admin-stats" },
    stat(num(d.counts.incidents - sim), t("Incidents"), sim ? t("{n} simulated (replay)", { n: sim }) : null, "/"),
    stat(num(d.counts.hotspots), t("Hotspots"), t("{n} in the last 24 h", { n: num(d.hotspots_last_24h) })),
    stat(num(d.counts.citizen_reports), t("Citizen reports"), null, "/whatsapp/"),
    stat(num(d.counts.satellite_pairs), t("Sentinel-2 pairs")),
    stat(num(d.counts.triaged), t("Triaged"), d.cost_per_ok_run != null ? `$${d.cost_per_ok_run.toFixed(3)} / run` : null, "#tab=triage"));
  const order = ["pending_triage", "triaging", "needs_more_info", "possible_fire", "likely_fire", "unlikely"];
  const total = Object.values(d.status_counts).reduce((a, x) => a + x, 0) || 1;
  const statusCard = card("flame", [t("Incident status"), info(t("Real incidents by status; replayed ones are counted apart."))],
    el("div", { class: "admin-bars" }, order.filter((k) => d.status_counts[k]).map((k) => el("div", { class: "admin-bar-row" },
      statusPill(k), el("div", { class: "admin-bar" }, el("span", { dataset: { status: k }, style: `width:${Math.max(1, (100 * d.status_counts[k]) / total)}%` })),
      el("strong", { class: "num" }, num(d.status_counts[k]))))));
  const srcRow = (name, st, when, detail) => el("tr", {}, td(t("Data sources"), name),
    td(t("State"), el("span", { class: "src-item", dataset: { state: st } }, el("span", { class: "src-state" }, el("span", { class: "src-dot" }), t(st)))),
    td(t("Last checked"), when ? fmtTime(when) : ""), td(t("Detail"), detail || ""));
  const sources = card("satellite", t("Data sources"), table([t("Data sources"), t("State"), t("Last checked"), t("Detail")], [
    ...Object.entries(d.sources).map(([k, v]) => srcRow(t(SOURCE_NAME[k] || k), v.state, v.checked_at,
      k === "firms" && v.newest_acquisition ? `${t("Newest detection")}: ${fmtTime(v.newest_acquisition)}` : v.detail)),
    srcRow(t("Overture Maps"), d.offline?.overture_release ? "ok" : "unknown", null, d.offline?.overture_release ? `${t("from the offline file")} ${d.offline.overture_release}` : ""),
    srcRow("NASA GIBS", "unknown", null, t("display only, not checked")),
  ], "admin-stack"));
  const off = d.offline;
  const counts = (o) => Object.entries(o || {}).sort((a, x) => x[1] - a[1]).map(([k, v]) => `${t(k)} ${num(v)}`).join(", ");
  const offline = card("layers", [t("Offline data file"), info(t("Used for nearby places: OSM rows when Overpass is down, Overture places always."))],
    off?.present ? el("dl", { class: "kv" },
      kvRow(t("OpenStreetMap extract"), off.source || ""), kvRow(t("Overture release"), off.overture_release ? `${off.overture_release} (≥ ${off.overture_min_confidence})` : t("none")),
      kvRow(t("Size"), `${off.size_mb} MB`), kvRow(t("Overpass"), t(off.overpass_down ? "down, using the file" : "answering")),
      kvRow("OSM", counts(off.osm_counts)), kvRow("Overture", counts(off.overture_counts)))
      : el("p", { class: "empty" }, t("Not downloaded yet.")));
  const system = card("activity", t("System"), el("dl", { class: "kv" },
    kvRow(t("Build"), el("span", { class: "mono" }, b.git_sha)),
    kvRow(t("Mode"), t(b.fixture_mode ? "Simulation (fixtures)" : "Live data")),
    kvRow(t("Scheduler"), t(b.scheduler_enabled ? "On" : "Off")),
    kvRow(t("Meta Model API"), t(b.model_configured ? "configured" : "missing")),
    kvRow(t("Baseten"), t(b.baseten_configured ? "configured" : "missing")),
    kvRow(t("Total metered spend"), `$${d.spend_total.cost_usd.toFixed(2)} · ${t("{n} runs", { n: d.spend_total.runs })}`)));
  return [tiles, el("div", { class: "admin-dash" }, statusCard, system), sources, offline];
}

const JOB_LABEL = { fetch: "Fetch FIRMS now", enrich: "Collect pending evidence", sentinel: "Look for Sentinel-2 pairs" };
const JOB_HELP = {
  fetch: "One FIRMS call for Indonesia, then evidence for new incidents. The schedule counts from this fetch.",
  enrich: "Weather and nearby places for eligible incidents that have none or had a failed source, within the budgets below.",
  sentinel: "For the largest real incidents (3 or more hotspots) without a pair from the reuse window. Open data, about 10 to 30 s each.",
};

function jobRow(d, name) {
  const job = d.jobs?.[name];
  const limit = name === "sentinel" ? el("input", { type: "number", class: "input admin-num", min: 1, max: 10, value: 5, "aria-label": t("Incidents") }) : null;
  const go = el("button", { class: "btn", type: "button", disabled: job?.state === "running" }, svg("play", "icon icon-sm"), t("Run"));
  go.onclick = () => runJob(name, limit ? Math.round(Number(limit.value)) || 5 : 5);
  const status = !job ? null : job.state === "running" ? el("span", { class: "muted" }, el("span", { class: "spinner", "aria-hidden": "true" }), " ", t("running since {t}", { t: fmtTime(job.started_at) }))
    : job.state === "done" ? el("span", { class: "muted" }, t("done {t}", { t: fmtTime(job.finished_at) }), job.result ? ` · ${jobResult(job.result)}` : "")
    : el("span", { class: "form-error" }, t("failed {t}: {e}", { t: fmtTime(job.finished_at), e: job.error || "" }));
  return el("div", { class: "admin-job" },
    el("div", { class: "admin-job-head" }, el("span", { class: "admin-label" }, t(JOB_LABEL[name]), info(t(JOB_HELP[name]))),
      el("div", { class: "admin-job-go" }, limit, go)), status);
}
const jobResult = (r) => Object.entries(r).filter(([, v]) => typeof v === "number").map(([k, v]) => `${k} ${v}`).join(", ");

function runJob(name, limit) {
  const code = el("input", { type: "password", class: "input", placeholder: t("Execution code"), "aria-label": t("Execution code") });
  const err = el("p", { class: "form-error", hidden: true });
  const ok = el("button", { class: "btn btn-primary", type: "button" }, t("Run"));
  const dlg = modal({ title: t(JOB_LABEL[name]), body: [el("p", { class: "muted", style: "margin:0" }, t(JOB_HELP[name])), code, err],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Cancel")), ok] });
  const go = async () => {
    ok.disabled = true;
    try {
      const r = await api(`/api/admin/jobs/${name}`, { method: "POST", body: JSON.stringify({ code: code.value, limit }) });
      dlg.close();
      toast(t(r.started ? "Started: {job}." : "Already running: {job}.", { job: t(JOB_LABEL[name]) }), "check");
      load();
    } catch (e) {
      err.hidden = false;
      fill(err, svg("alert", "icon icon-sm"), el("span", {}, e.message));
      ok.disabled = false;
    }
  };
  ok.onclick = go;
  code.addEventListener("keydown", (e) => e.key === "Enter" && go());
}

function fetchTab(d) {
  const sched = d.schedule;
  const cov = d.coverage;
  const failed = Object.entries(cov.failed_by_source || {});
  return [
    el("div", { class: "admin-dash" },
      card("satellite", t("Data fetching"), el("dl", { class: "kv" },
        kvRow(t("Last fetch"), sched.last_fetch ? fmtTime(sched.last_fetch) : t("never")),
        kvRow(t("Next fetch"), sched.next_fetch ? fmtTime(sched.next_fetch) : t("at the next scheduler tick")),
        kvRow(t("Newest detection"), d.newest_acquisition ? fmtTime(d.newest_acquisition) : t("never")))),
      card("file", [t("Evidence coverage"), info(t("Eligible incidents: 3 or more hotspots, or a fire radiative power of 20 MW or more, or a citizen report."))],
        el("div", { class: "admin-meter" }, el("span", { style: `width:${cov.eligible ? (100 * cov.enriched) / cov.eligible : 0}%` })),
        el("p", { style: "margin:0" }, t("{a} of {b} eligible incidents have evidence", { a: num(cov.enriched), b: num(cov.eligible) })),
        el("dl", { class: "kv" }, kvRow(t("Failed sources"), failed.length ? failed.map(([k, v]) => `${t(k)} ${v}`).join(", ") : t("none"))))),
    card("play", [t("Operator actions"), info(t("None of these calls a model. They load shared services, so they need the execution code."))],
      el("div", { class: "admin-jobs" }, ["fetch", "enrich", "sentinel"].map((n) => jobRow(d, n)))),
    card("gear", t("Data fetching"), el("div", { class: "admin-grid" }, d.settings.filter((s) => s.group === "data").map(settingControl))),
    settingsLog(d),
  ];
}

function settingsLog(d) {
  const label = (k) => t(d.settings.find((s) => s.key === k)?.label || k);
  return card("list", t("Settings changes"), d.settings_log.length
    ? table([t("When"), t("Setting"), t("From"), t("To")], d.settings_log.map((x) => el("tr", {}, td(t("When"), fmtTime(x.at)), td(t("Setting"), label(x.key)),
      td(t("From"), JSON.parse(x.old ?? "null") ?? ""), td(t("To"), JSON.parse(x.new ?? "null") ?? ""))), "admin-stack")
    : el("p", { class: "empty" }, t("No changes recorded yet.")));
}

function spendCard(d) {
  const by = d.runs_by_outcome || {};
  return card("sparkle", [t("Spend"), info(t("Meta Model API usage is metered per run; Baseten runs on hackathon credit and is not metered. The credit balance is not available through the API."))],
    el("dl", { class: "kv" },
      kvRow(t("Total metered spend"), `$${d.spend_total.cost_usd.toFixed(2)} · ${t("{n} runs", { n: d.spend_total.runs })}`),
      kvRow(t("Today"), t("{runs} of {cap} runs, ${cost}", { runs: d.triage.today.runs, cap: d.triage.daily_cap, cost: d.triage.today.cost_usd.toFixed(2) })),
      kvRow(t("Average per completed run"), d.cost_per_ok_run != null ? `$${d.cost_per_ok_run.toFixed(3)}` : t("none"))),
    Object.keys(by).length ? table([t("By outcome"), t("Runs"), t("Cost")], Object.entries(by).map(([k, v]) => el("tr", {},
      td(t("By outcome"), outcomePill(k)), td(t("Runs"), String(v.runs)), td(t("Cost"), `$${v.cost_usd.toFixed(3)}`)))) : null,
    by.schema_failed ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("A run that fails the schema still costs and replaces the earlier triage.")) : null);
}
const outcomePill = (o) => pill(o === "ok" ? "pill-live" : o === "running" ? "" : "pill-bad", t(o));

function triageCard(d) {
  const tr = d.triage;
  return card("shield", t("AI triage"),
    el("div", { class: "admin-cols" },
      el("div", { class: "admin-setting" },
        el("div", { class: "admin-setting-head" }, el("span", { class: "admin-label" }, t("Scheduled triage"))),
        el("div", { class: "segmented admin-seg", role: "group" }, tr.modes.map((m) => el("button", { type: "button", "aria-pressed": String(m === tr.mode), onclick: () => m !== tr.mode && changeMode(m) }, t(MODE_LABEL[m])))))),  // today's runs and spend are in the spend card above
    el("div", { class: "admin-grid" }, d.settings.filter((s) => s.group === "triage").map(settingControl)),
    el("h3", { class: "admin-sub" }, t("Recent triage runs"), " ", aiBadge("Muse Spark 1.3")),
    d.recent_runs.length ? el("div", { class: "doc-table-wrap" }, el("table", { class: "hs-table" },
      el("tr", {}, ["Incident", "Started", "Outcome", "Seconds", "Cost"].map((h) => el("th", {}, t(h)))),
      d.recent_runs.map((r) => el("tr", {}, el("td", { class: "mono" }, el("a", { href: `/?incident=${encodeURIComponent(r.incident_id)}` }, r.incident_id)), el("td", {}, fmtTime(r.started_at)),
        el("td", {}, outcomePill(r.outcome)), el("td", { class: "num" }, r.seconds == null ? "" : Math.round(r.seconds)), el("td", { class: "num" }, r.cost_usd == null ? "" : `$${r.cost_usd.toFixed(3)}`)))))
      : el("p", { class: "empty" }, t("No triage runs yet.")));
}

const STATUS_LABEL = { pending_triage: "Pending triage", triaging: "Triage running", likely_fire: "Likely fire", possible_fire: "Possible fire", unlikely: "Unlikely", needs_more_info: "Needs more info" };

function whatsappCard(w, log) {
  const reports = log?.items || [];
  const items = reports.flatMap((r) => r.items);
  const photos = items.filter((i) => i.type === "image"), voices = items.filter((i) => i.type === "voice");
  const stat = (v, l) => el("div", { class: "stat" }, el("div", { class: "stat-v num" }, String(v)), el("div", { class: "stat-l" }, t(l)));
  const kind = { report: "text", image: "photo", voice: "voice" };
  const ok = (good, label) => el("span", { class: `wa-flag ${good ? "on" : ""}` }, svg(good ? "check" : "clock", "icon icon-sm"), t(label));
  const out = w.recent_outbound;
  const refused = out.filter((x) => x.status !== "sent" || String(x.delivery || "").startsWith("failed")).length;
  return card("chat", t("WhatsApp"),
    el("div", { class: "stats-grid admin-stats" },
      stat(reports.length, "Reports received"), stat(items.length, "Items processed"),
      stat(photos.filter((p) => p.reading_status === "ok").length + "/" + photos.length, "Photos read by Muse Spark"),
      stat(voices.filter((v) => v.asr_status === "ok").length + "/" + voices.length, "Voice notes transcribed"),
      stat(w.last_inbound ? fmtTime(w.last_inbound) : t("never"), "Last inbound message")),
    el("h3", { class: "admin-sub" }, t("Latest reports")),
    reports.length ? el("div", { class: "doc-table-wrap" }, el("table", { class: "hs-table" },
      el("tr", {}, ["Received", "Incident", "Sent", "Meta models", "Status"].map((h) => el("th", {}, t(h), h === "Meta models" ? [" ", aiBadge("Muse Spark, Muse Voice Transcribe")] : null))),
      reports.slice(0, 8).map((r) => el("tr", {},
        el("td", {}, fmtTime(r.items.reduce((a, b) => (a.at < b.at ? a : b)).at)),
        el("td", { class: "mono" }, el("a", { href: `/?incident=${encodeURIComponent(r.incident_id)}` }, r.incident_id)),
        el("td", {}, [...new Set(r.items.map((i) => t(kind[i.type])))].join(", ")),
        el("td", {}, el("div", { class: "wa-flags" },
          r.items.some((i) => i.type === "voice") ? ok(r.items.filter((i) => i.type === "voice").every((i) => i.asr_status === "ok"), "transcribed") : null,
          r.items.some((i) => i.type === "image") ? ok(r.items.filter((i) => i.type === "image").every((i) => i.reading_status === "ok"), "photo read") : null)),
        el("td", {}, t(STATUS_LABEL[r.status] || r.status))))))
      : el("p", { class: "empty" }, t("No WhatsApp reports yet.")),
    el("div", { class: "actions" }, el("a", { class: "btn btn-primary", href: "/whatsapp/" }, svg("chat", "icon icon-sm"), t("Open the WhatsApp report log"))),
    // Replies are secondary: one folded line says what happens to them and why; the rows are inside.
    section({ icon: "send", title: t("Outgoing replies and alerts"), count: out.length, open: false,
      tip: t("Meta refuses outgoing messages from this business account until it passes business verification. Incoming reports work; every reply is still attempted and its refusal recorded. Numbers are shown as the first characters of a salted hash."),
      content: [
        el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("{n} attempted, {r} refused by Meta (account not verified).", { n: out.length, r: refused })),
        out.length ? el("div", { class: "doc-table-wrap" }, el("table", { class: "hs-table" },
          el("tr", {}, ["Sent", "Purpose", "To", "Result", "Incident"].map((h) => el("th", {}, t(h)))),
          out.map((x) => el("tr", {}, el("td", {}, fmtTime(x.created_at)), el("td", {}, t(x.purpose)), el("td", { class: "mono" }, `#${x.to_hash.slice(0, 6)}`),
            el("td", {}, el("span", { class: "pill" }, t(x.status !== "sent" || String(x.delivery || "").startsWith("failed") ? "refused by Meta" : "accepted"))),
            el("td", { class: "mono" }, x.incident_id ? el("a", { href: `/?incident=${encodeURIComponent(x.incident_id)}` }, x.incident_id) : "")))))
          : null] }));
}

function droneTab(d) {
  const dr = d.drone;
  const kv = (o) => Object.entries(o).map(([k, v]) => `${t(k)} ${v}`).join(", ") || t("none");
  return card("target", [t("Drone surveys (simulation)"), info(t("Plans are proposed by the agent or a verifier and approved by a verifier only; the pilot and the drone are simulated."))],
    el("dl", { class: "kv" }, kvRow(t("Plans"), kv(dr.proposals)), kvRow(t("Flights"), kv(dr.flights))),
    dr.latest.length ? [el("h3", { class: "admin-sub" }, t("Latest plans")), table([t("Plan"), t("Incident"), t("Status"), t("Flight"), t("Proposed by"), t("When")],
      dr.latest.map((p) => el("tr", {}, td(t("Plan"), `#${p.id}`), td(t("Incident"), incLink(p.incident_id)), td(t("Status"), t(p.status)),
        td(t("Flight"), p.flight_state ? t(p.flight_state) : ""), td(t("Proposed by"), t(p.source === "agent" ? "agent" : "verifier")), td(t("When"), fmtTime(p.created_at)))), "admin-stack")]
      : el("p", { class: "empty" }, t("No drone plans yet.")));
}

// Which tab a setting belongs to, so the tab can show how many unsaved changes it holds.
const pendingIn = (group) => Object.keys(state.pending).filter((k) => state.data.settings.find((s) => s.key === k)?.group === group).length;

function render() {
  const d = state.data;
  const q = state.queue;
  const n = Object.keys(state.pending).length;
  const realWaiting = q ? q.count : 0;
  const view = tabs([
    { key: "queue", label: t("Verification"), icon: "gavel", count: realWaiting, render: () => queueTab(q) },
    { key: "system", label: t("Overview"), icon: "activity", render: () => systemTab(d) },
    { key: "data", label: t("Data fetching"), icon: "satellite", count: pendingIn("data"), render: () => fetchTab(d) },
    { key: "triage", label: t("AI triage"), icon: "shield", count: pendingIn("triage"), render: () => [spendCard(d), triageCard(d)] },
    d.whatsapp ? { key: "whatsapp", label: t("WhatsApp"), icon: "chat", render: () => whatsappCard(d.whatsapp, state.waLog) } : null,
    { key: "drone", label: t("Drone"), icon: "target", render: () => droneTab(d) },
    { key: "replay", label: t("Demo replay"), icon: "play", render: () => replayCard(d.replay) },
  ].filter(Boolean));
  state.view = view;
  const refresh = el("button", { class: "btn btn-sm", type: "button", onclick: () => load() }, svg("reset", "icon icon-sm"), t("Refresh"));
  fill($("#admin-body"),
    el("div", { class: "admin-head" }, el("h2", {}, t("Admin panel")), el("span", { class: "muted" }, t("Updated {t}", { t: fmtTime(state.loadedAt) })), refresh),
    attention(d, q),
    view.node,
    // Unsaved changes stay visible on every tab, so leaving a tab never hides them.
    n ? el("div", { class: "card admin-savebar" }, el("span", {}, t("{n} unsaved change(s)", { n })),
      el("div", { class: "actions" }, el("button", { class: "btn", type: "button", onclick: () => { state.pending = {}; render(); } }, t("Discard")),
        el("button", { class: "btn btn-primary", type: "button", onclick: save }, svg("check", "icon icon-sm"), t("Save")))) : null,
  );
  hydrateIcons($("#admin-body"));
}

function replayCard(r) {
  const run = async (btn, method, path, done) => {
    btn.disabled = true;
    try {
      const res = await api(path, { method });
      toast(t(done(res)), "check");
      load();
    } catch (e) {
      toast(t("Failed: {e}", { e: e.message }), "alert");
      btn.disabled = false;
    }
  };
  const loadBtn = el("button", { class: "btn btn-primary", type: "button" }, svg("play", "icon icon-sm"), t("Load replay"));
  loadBtn.onclick = () => run(loadBtn, "POST", "/api/replay/start", (res) => (res.started ? "Replay loaded." : "Replay was already loaded."));
  const removeBtn = el("button", { class: "btn", type: "button" }, svg("trash", "icon icon-sm"), t("Remove replay"));
  removeBtn.onclick = () => {
    const ok = el("button", { class: "btn btn-primary", type: "button" }, t("Remove"));
    const dlg = modal({ title: t("Remove replay"), body: [el("p", { style: "margin:0" }, t("Remove the replayed incidents, their evidence, and any verdicts given on them? Real incidents are not touched, and the replay can be loaded again."))],
      actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Cancel")), ok] });
    ok.onclick = () => { dlg.close(); run(removeBtn, "DELETE", "/api/replay", () => "Replay removed."); };
  };
  return card("play", t("Demo replay"),
    el("p", { class: "muted", style: "margin:0" }, t("Three recorded incidents from the 2025 dry season (clearly burned, false positive, needs more info), loaded as SIMULASI. They come from files in the app, so loading needs no network and no model call.")),
    el("dl", { class: "kv" }, kvRow(t("State"), r.loaded.length ? t("{n} incidents loaded", { n: r.loaded.length }) : t("Not loaded"))),
    el("ul", { class: "replay-cases" }, r.cases.map((c, i) => {
      const id = r.loaded[i];
      return el("li", {}, el("div", { class: "replay-case-head" }, el("strong", {}, c.title[lang()] || c.title.en),
        id ? el("a", { href: `/?incident=${encodeURIComponent(id)}`, class: "mono" }, id) : null, id ? el("span", { class: "pill pill-sim" }, "SIMULASI") : null),
        el("p", { class: "muted" }, (c.note && (c.note[lang()] || c.note.en)) || ""));
    })),
    r.cases.length ? el("div", { class: "actions" }, r.loaded.length ? removeBtn : loadBtn) : el("p", { class: "empty" }, t("No recorded cases in this build.")),
    r.cases.length ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("Recorded with {m}.", { m: r.cases[0].recorded_with }), r.cases[0].translated_with ? ` ${t("Indonesian text translated from the recording by {m}; the recording is unchanged.", { m: r.cases[0].translated_with })}` : "") : null);
}

let pollTimer;
async function load() {
  clearTimeout(pollTimer);
  try {
    [state.data, state.waLog, state.queue] = await Promise.all([api("/api/admin/overview"), api("/api/wa/reports").catch(() => null), api("/api/admin/queue").catch((e) => { state.queueError = e.message; return null; })]);
    state.loadedAt = new Date().toISOString();
    // Keep the open tab and scroll; only the content is new.
    const y = window.scrollY;
    render();
    window.scrollTo(0, y);
    // While an operator job runs, look again every few seconds so its result appears by itself.
    if (Object.values(state.data.jobs || {}).some((j) => j.state === "running")) pollTimer = setTimeout(load, 4000);
  } catch (e) {
    fill($("#admin-body"), el("p", { class: "empty" }, t("Could not load the admin panel: {e}", { e: e.message })));
  }
}

function main() {
  applyStatic(t);
  hydrateIcons();
  $("#lang-slot").replaceWith(langSwitch(el));
  $("#verifier-slot").replaceWith(verifierControl(t));
  $("#crumbs").replaceChildren(el("a", { href: "/" }, t("Incident map")), svg("chevron", "icon icon-sm crumb-sep"), el("span", { class: "current" }, t("Admin panel")));
  siteFooter($("#site-footer"), lang());
  if (!token()) {
    fill($("#admin-body"), el("section", { class: "card admin-card" }, el("p", { style: "margin:0" }, t("Sign in to open the admin panel.")),
      el("div", { class: "actions" }, el("button", { class: "btn btn-primary", type: "button", onclick: () => openSignIn(t) }, svg("shield", "icon icon-sm"), t("Verifier sign in")))));
    return;
  }
  fill($("#admin-body"),
    el("div", { class: "admin-head" }, el("span", { class: "sk sk-title" })),
    el("div", { class: "card admin-attn" }, el("span", { class: "sk sk-line" })),
    el("div", { class: "card tabbar" }, [1, 2, 3, 4, 5, 6].map(() => el("span", { class: "sk sk-chip" }))),
    el("div", { class: "stats-grid admin-stats" }, [1, 2, 3, 4, 5].map(() => el("div", { class: "stat" }, el("span", { class: "sk sk-title" }), el("span", { class: "sk sk-line" })))),
    el("div", { class: "admin-dash" }, [1, 2].map(() => el("div", { class: "card admin-card" }, el("span", { class: "sk sk-title" }), el("span", { class: "sk sk-block" })))));
  load();
}

main();
