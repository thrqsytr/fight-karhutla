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
  "Hard limit on triage runs per WITA day, manual and scheduled together. Each run costs about $0.05 of Meta credit.":
    "Batas keras run triase per hari WITA, manual dan terjadwal digabung. Satu run memakai sekitar $0,05 kredit Meta.",
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
  "transcribed": "ditranskripsi", "photo read": "foto dibaca", "Pending triage": "Menunggu triase", "Triage running": "Triase berjalan", "Likely fire": "Kemungkinan besar kebakaran", "Possible fire": "Mungkin kebakaran", "Unlikely": "Kecil kemungkinan", "Needs more info": "Butuh info tambahan", "No WhatsApp reports yet.": "Belum ada laporan WhatsApp.",
  "Outgoing replies and alerts": "Balasan dan notifikasi keluar", "{n} attempted, {r} refused by Meta (account not verified).": "{n} dicoba, {r} ditolak Meta (akun belum terverifikasi).",
  "Meta refuses outgoing messages from this business account until it passes business verification. Incoming reports work; every reply is still attempted and its refusal recorded. Numbers are shown as the first characters of a salted hash.":
    "Meta menolak pesan keluar dari akun bisnis ini sampai lolos verifikasi bisnis. Laporan masuk berjalan; setiap balasan tetap dicoba dan penolakannya dicatat. Nomor ditampilkan sebagai karakter awal dari hash bergaram.",
  "refused by Meta": "ditolak Meta", "accepted": "diterima",
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

function systemCard(d) {
  const b = d.build;
  const srcRow = ([key, v]) => el("tr", {}, el("td", {}, { firms: "NASA FIRMS", weather: "Open-Meteo", osm: "OSM (Overpass)", sentinel2: "Sentinel-2" }[key] || key),
    el("td", {}, el("span", { class: "src-item", dataset: { state: v.state } }, el("span", { class: "src-state" }, el("span", { class: "src-dot" }), t(v.state)))),
    el("td", {}, v.checked_at ? fmtTime(v.checked_at) : ""));
  return card("activity", t("System"),
    el("div", { class: "stats-grid admin-stats" },
      [["Incidents", d.counts.incidents], ["Hotspots", d.counts.hotspots], ["Citizen reports", d.counts.citizen_reports], ["Sentinel-2 pairs", d.counts.satellite_pairs], ["Triaged", d.counts.triaged]]
        .map(([l, v]) => el("div", { class: "stat" }, el("div", { class: "stat-v num" }, Number(v).toLocaleString(locale())), el("div", { class: "stat-l" }, t(l))))),
    el("div", { class: "admin-cols" },
      el("dl", { class: "kv" },
        kvRow(t("Build"), el("span", { class: "mono" }, b.git_sha)),
        kvRow(t("Mode"), t(b.fixture_mode ? "Simulation (fixtures)" : "Live data")),
        kvRow(t("Scheduler"), t(b.scheduler_enabled ? "On" : "Off")),
        kvRow(t("Meta Model API"), t(b.model_configured ? "configured" : "missing")),
        kvRow(t("Baseten"), t(b.baseten_configured ? "configured" : "missing")),
        kvRow(t("Total metered spend"), `$${d.spend_total.cost_usd.toFixed(2)} · ${t("{n} runs", { n: d.spend_total.runs })}`)),
      el("div", {}, el("h3", { class: "admin-sub" }, t("Data sources")),
        el("table", { class: "hs-table" }, el("tr", {}, el("th", {}, ""), el("th", {}, t("State")), el("th", {}, t("Last checked"))), Object.entries(d.sources).map(srcRow)))));
}

function fetchCard(d) {
  const sched = d.schedule;
  return card("satellite", t("Data fetching"),
    el("dl", { class: "kv" },
      kvRow(t("Last fetch"), sched.last_fetch ? fmtTime(sched.last_fetch) : t("never")),
      kvRow(t("Next fetch"), sched.next_fetch ? fmtTime(sched.next_fetch) : t("at the next scheduler tick"))),
    el("div", { class: "admin-grid" }, d.settings.filter((s) => s.group === "data").map(settingControl)));
}

function triageCard(d) {
  const tr = d.triage;
  return card("shield", t("AI triage"),
    el("div", { class: "admin-cols" },
      el("div", { class: "admin-setting" },
        el("div", { class: "admin-setting-head" }, el("span", { class: "admin-label" }, t("Scheduled triage"))),
        el("div", { class: "segmented admin-seg", role: "group" }, tr.modes.map((m) => el("button", { type: "button", "aria-pressed": String(m === tr.mode), onclick: () => m !== tr.mode && changeMode(m) }, t(MODE_LABEL[m]))))),
      el("dl", { class: "kv" }, kvRow(t("Today"), t("{runs} of {cap} runs, ${cost}", { runs: tr.today.runs, cap: tr.daily_cap, cost: tr.today.cost_usd.toFixed(2) })))),
    el("div", { class: "admin-grid" }, d.settings.filter((s) => s.group === "triage").map(settingControl)),
    el("h3", { class: "admin-sub" }, t("Recent triage runs"), " ", aiBadge("Muse Spark 1.3")),
    d.recent_runs.length ? el("div", { class: "doc-table-wrap" }, el("table", { class: "hs-table" },
      el("tr", {}, ["Incident", "Started", "Outcome", "Seconds", "Cost"].map((h) => el("th", {}, t(h)))),
      d.recent_runs.map((r) => el("tr", {}, el("td", { class: "mono" }, el("a", { href: `/?incident=${encodeURIComponent(r.incident_id)}` }, r.incident_id)), el("td", {}, fmtTime(r.started_at)),
        el("td", {}, r.outcome), el("td", { class: "num" }, r.seconds == null ? "" : Math.round(r.seconds)), el("td", { class: "num" }, r.cost_usd == null ? "" : `$${r.cost_usd.toFixed(3)}`)))))
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

// Which tab a setting belongs to, so the tab can show how many unsaved changes it holds.
const pendingIn = (group) => Object.keys(state.pending).filter((k) => state.data.settings.find((s) => s.key === k)?.group === group).length;

function render() {
  const d = state.data;
  const n = Object.keys(state.pending).length;
  const view = tabs([
    { key: "system", label: t("Overview"), icon: "activity", render: () => systemCard(d) },
    { key: "data", label: t("Data fetching"), icon: "satellite", count: pendingIn("data"), render: () => fetchCard(d) },
    { key: "triage", label: t("AI triage"), icon: "shield", count: pendingIn("triage"), render: () => triageCard(d) },
    d.whatsapp ? { key: "whatsapp", label: t("WhatsApp"), icon: "chat", render: () => whatsappCard(d.whatsapp, state.waLog) } : null,
    { key: "replay", label: t("Demo replay"), icon: "play", render: () => replayCard(d.replay) },
  ].filter(Boolean));
  fill($("#admin-body"),
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

async function load() {
  try {
    [state.data, state.waLog] = await Promise.all([api("/api/admin/overview"), api("/api/wa/reports").catch(() => null)]);
    render();
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
  fill($("#admin-body"), [1, 2, 3].map(() => el("div", { class: "card admin-card" }, el("span", { class: "sk sk-block" }))));
  load();
}

main();
