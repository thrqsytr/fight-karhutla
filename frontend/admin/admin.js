import { $, el, hydrateIcons, info, modal, siteFooter, svg, toast } from "/ui.js";
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

function render() {
  const d = state.data;
  const b = d.build;
  const group = (g) => d.settings.filter((s) => s.group === g).map(settingControl);
  const tr = d.triage;
  const n = Object.keys(state.pending).length;
  const sched = d.schedule;
  const srcRow = ([key, v]) => el("tr", {}, el("td", {}, { firms: "NASA FIRMS", weather: "Open-Meteo", osm: "OSM (Overpass)", sentinel2: "Sentinel-2" }[key] || key),
    el("td", {}, el("span", { class: "src-item", dataset: { state: v.state } }, el("span", { class: "src-state" }, el("span", { class: "src-dot" }), t(v.state)))),
    el("td", {}, v.checked_at ? fmtTime(v.checked_at) : ""));
  fill($("#admin-body"),
    card("activity", t("System"),
      el("dl", { class: "kv" },
        kvRow(t("Build"), el("span", { class: "mono" }, b.git_sha)),
        kvRow(t("Mode"), t(b.fixture_mode ? "Simulation (fixtures)" : "Live data")),
        kvRow(t("Scheduler"), t(b.scheduler_enabled ? "On" : "Off")),
        kvRow(t("Meta Model API"), t(b.model_configured ? "configured" : "missing")),
        kvRow(t("Baseten"), t(b.baseten_configured ? "configured" : "missing")),
        kvRow(t("Total metered spend"), `$${d.spend_total.cost_usd.toFixed(2)} · ${t("{n} runs", { n: d.spend_total.runs })}`)),
      el("div", { class: "stats-grid admin-stats" },
        [["Incidents", d.counts.incidents], ["Hotspots", d.counts.hotspots], ["Citizen reports", d.counts.citizen_reports], ["Sentinel-2 pairs", d.counts.satellite_pairs], ["Triaged", d.counts.triaged]]
          .map(([l, v]) => el("div", { class: "stat" }, el("div", { class: "stat-v num" }, Number(v).toLocaleString(locale())), el("div", { class: "stat-l" }, t(l))))),
      el("h3", { class: "admin-sub" }, t("Data sources")),
      el("table", { class: "hs-table" }, el("tr", {}, el("th", {}, ""), el("th", {}, t("State")), el("th", {}, t("Last checked"))), Object.entries(d.sources).map(srcRow))),
    card("satellite", t("Data fetching"),
      el("dl", { class: "kv" },
        kvRow(t("Last fetch"), sched.last_fetch ? fmtTime(sched.last_fetch) : t("never")),
        kvRow(t("Next fetch"), sched.next_fetch ? fmtTime(sched.next_fetch) : t("at the next scheduler tick"))),
      group("data")),
    card("shield", t("AI triage"),
      el("div", { class: "admin-setting" },
        el("div", { class: "admin-setting-head" }, el("span", { class: "admin-label" }, t("Scheduled triage"))),
        el("div", { class: "segmented admin-seg", role: "group" }, tr.modes.map((m) => el("button", { type: "button", "aria-pressed": String(m === tr.mode), onclick: () => m !== tr.mode && changeMode(m) }, t(MODE_LABEL[m]))))),
      el("dl", { class: "kv" }, kvRow(t("Today"), t("{runs} of {cap} runs, ${cost}", { runs: tr.today.runs, cap: tr.daily_cap, cost: tr.today.cost_usd.toFixed(2) }))),
      group("triage"),
      el("h3", { class: "admin-sub" }, t("Recent triage runs")),
      d.recent_runs.length ? el("div", { class: "doc-table-wrap" }, el("table", { class: "hs-table" },
        el("tr", {}, ["Incident", "Started", "Outcome", "Seconds", "Cost"].map((h) => el("th", {}, t(h)))),
        d.recent_runs.map((r) => el("tr", {}, el("td", { class: "mono" }, r.incident_id), el("td", {}, fmtTime(r.started_at)),
          el("td", {}, r.outcome), el("td", { class: "num" }, r.seconds == null ? "" : Math.round(r.seconds)), el("td", { class: "num" }, r.cost_usd == null ? "" : `$${r.cost_usd.toFixed(3)}`)))))
        : el("p", { class: "empty" }, t("No triage runs yet."))),
    n ? el("div", { class: "card admin-savebar" }, el("span", {}, t("{n} unsaved change(s)", { n })),
      el("div", { class: "actions" }, el("button", { class: "btn", type: "button", onclick: () => { state.pending = {}; render(); } }, t("Discard")),
        el("button", { class: "btn btn-primary", type: "button", onclick: save }, svg("check", "icon icon-sm"), t("Save")))) : null,
  );
  hydrateIcons($("#admin-body"));
}

async function load() {
  try {
    state.data = await api("/api/admin/overview");
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
