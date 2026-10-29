import { $, aiBadge, el, hydrateIcons, info, siteFooter, svg, toast } from "/ui.js";
import { applyStatic, lang, langSwitch, locale, makeT } from "/i18n.js";
import { authHeaders, verifierControl, VERIFIER_ID } from "/verifier.js";

/* WhatsApp report log: what each resident sent, what the Meta models made of it, how the system processed it,
   and what the bot answered. Real reports from the production system, not a simulation or a replay.
   Verifiers see every report and choose which ones the public may see. Written in English; Indonesian below. */
const WA_ID = {
  "WhatsApp reports": "Laporan WhatsApp", "Map": "Peta", "Incident map": "Peta insiden",
  "Citizen reports via WhatsApp": "Laporan warga lewat WhatsApp",
  "Real data from the production system": "Data nyata dari sistem produksi",
  "Every report on this page was sent by WhatsApp to the Meta test number and processed by the system that runs karhutla.thariq.xyz. It is not a simulation and not a replay. Only reports a verifier marked as public examples are shown to everyone.":
    "Setiap laporan di halaman ini dikirim lewat WhatsApp ke nomor uji Meta dan diproses oleh sistem yang berjalan di karhutla.thariq.xyz. Ini bukan simulasi dan bukan replay. Hanya laporan yang ditandai verifikator sebagai contoh publik yang tampil untuk semua orang.",
  "Replies do not reach WhatsApp yet: Meta refuses messages from this business account until it passes business verification. Each reply is still sent and its refusal recorded below.":
    "Balasan belum sampai ke WhatsApp: Meta menolak pesan dari akun bisnis ini sampai lolos verifikasi bisnis. Setiap balasan tetap dikirim dan penolakannya dicatat di bawah.",
  "{n} report(s)": "{n} laporan", "No public WhatsApp reports yet.": "Belum ada laporan WhatsApp yang ditampilkan untuk publik.",
  "No WhatsApp reports yet.": "Belum ada laporan WhatsApp.",
  "Could not load the reports: {e}": "Laporan gagal dimuat: {e}",
  "Reporter": "Pelapor", "Location": "Lokasi", "Received": "Diterima", "Status": "Status", "Public example": "Contoh publik", "Private": "Privat",
  "Show as public example": "Tampilkan sebagai contoh publik", "Make private": "Jadikan privat",
  "Shown to everyone on this page.": "Ditampilkan untuk semua orang di halaman ini.", "Visible to verifiers only.": "Hanya terlihat oleh verifikator.",
  "Only the first characters of a salted hash of the number. The number itself is never stored with the report.":
    "Hanya karakter awal dari hash bergaram nomor itu. Nomornya sendiri tidak pernah disimpan bersama laporan.",
  "Rounded to about 100 m for the public.": "Dibulatkan sekitar 100 m untuk publik.",
  "What the resident sent": "Yang dikirim warga", "What the Meta models found": "Hasil analisis model Meta",
  "How the system processed it": "Bagaimana sistem memprosesnya", "What the bot answered": "Balasan bot",
  "Text": "Teks", "Photo": "Foto", "Voice note": "Pesan suara",
  "Transcript": "Transkrip", "Photo reading": "Pembacaan foto", "Smoke": "Asap", "Flames": "Api", "Burned ground": "Tanah terbakar",
  "visible": "terlihat", "not visible": "tidak terlihat", "pending": "menunggu", "failed": "gagal",
  "Voice notes are transcribed and photos are read on Meta Model API in Standard mode, so citizen content is not used for training. The agent works from the transcript and the reading, never from raw audio.":
    "Pesan suara ditranskripsi dan foto dibaca di Meta Model API mode Standard, sehingga konten warga tidak dipakai untuk melatih model. Agen bekerja dari transkrip dan hasil bacaan, tidak dari audio mentah.",
  "No photo or voice note in this report.": "Laporan ini tidak berisi foto atau pesan suara.",
  "Webhook received, Meta signature checked": "Webhook diterima, tanda tangan Meta dicek",
  "Media downloaded from WhatsApp; photo re-encoded without metadata (EXIF)": "Media diunduh dari WhatsApp; foto disimpan ulang tanpa metadata (EXIF)",
  "New incident opened from the report": "Insiden baru dibuat dari laporan", "Joined an existing incident within 2 km and 24 h": "Digabung ke insiden yang sudah ada dalam 2 km dan 24 jam",
  "Voice note transcribed": "Pesan suara ditranskripsi", "Photo read": "Foto dibaca",
  "Evidence collected": "Bukti dikumpulkan", "Verifiers notified on WhatsApp": "Verifikator diberi tahu lewat WhatsApp",
  "AI triage": "Triase AI", "Verifier decision": "Keputusan verifikator",
  "Not run yet. Triage spends Meta credit, so it runs only when a verifier starts it.": "Belum dijalankan. Triase memakai kredit Meta, jadi hanya berjalan bila verifikator memulainya.",
  "Waiting for a verifier.": "Menunggu verifikator.", "Approved": "Disetujui", "Rejected": "Ditolak",
  "{ok} of {n} accepted": "{ok} dari {n} diterima", "none sent": "tidak ada yang dikirim",
  "Text not recorded (sent before reply texts were stored).": "Isi tidak tercatat (dikirim sebelum isi balasan mulai disimpan).",
  "Accepted by WhatsApp": "Diterima WhatsApp", "Refused by WhatsApp": "Ditolak WhatsApp", "Delivery": "Pengiriman",
  "delivered": "terkirim", "read": "dibaca", "no delivery result yet": "belum ada hasil pengiriman",
  "Accepted means WhatsApp took the message; the delivery result arrives later from Meta and is shown next to it.":
    "Diterima berarti WhatsApp menerima pesannya; hasil pengiriman datang kemudian dari Meta dan ditampilkan di sebelahnya.",
  "No reply sent.": "Tidak ada balasan yang dikirim.", "Open on map": "Buka di peta",
  "Pending triage": "Menunggu triase", "Triage running": "Triase berjalan", "Likely fire": "Kemungkinan besar kebakaran",
  "Possible fire": "Mungkin kebakaran", "Unlikely": "Kecil kemungkinan", "Needs more info": "Butuh info tambahan",
  "Saved.": "Tersimpan.", "Failed: {e}": "Gagal: {e}",
};
const t = makeT({ ...WA_ID, ...VERIFIER_ID }, "en");
const STATUS = { pending_triage: "Pending triage", triaging: "Triage running", likely_fire: "Likely fire", possible_fire: "Possible fire", unlikely: "Unlikely", needs_more_info: "Needs more info" };
const fmtTime = (iso) => (iso ? `${new Date(iso).toLocaleString(locale(), { timeZone: "Asia/Makassar", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })} WITA` : "");
const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

/* Media needs the verifier header for private reports, which an <img> can not send: fetch it as a blob. */
function mediaInto(node, url, kind) {
  fetch(url, { headers: authHeaders() }).then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`HTTP ${r.status}`)))).then((b) => {
    const src = URL.createObjectURL(b);
    fill(node, kind === "image" ? el("img", { src, alt: t("Photo"), class: "wa-photo" }) : el("audio", { src, controls: true, preload: "metadata", class: "wa-audio" }));
  }).catch((e) => fill(node, el("p", { class: "empty" }, e.message)));
  return node;
}

function block(icon, title, tip, ...content) {
  return el("div", { class: "wa-block" }, el("h3", { class: "wa-block-title" }, svg(icon, "icon icon-sm"), title, tip ? info(tip) : null), content);
}

const ICON = { report: "file", image: "camera", voice: "mic" };
const KIND = { report: "Text", image: "Photo", voice: "Voice note" };

function sentItems(r) {
  return r.items.map((it) => el("div", { class: "wa-msg" },
    el("div", { class: "wa-msg-head" }, svg(ICON[it.type], "icon icon-sm"), el("strong", {}, t(KIND[it.type])), el("span", { class: "ev-chip" }, it.id), el("span", { class: "muted" }, fmtTime(it.at))),
    it.type === "report" ? el("p", { class: "wa-bubble" }, it.text) : mediaInto(el("div", { class: "wa-media" }, el("span", { class: "sk sk-block" })), it.media_url, it.type)));
}

function flag(on, label) {
  return el("span", { class: `wa-flag ${on ? "on" : ""}` }, svg(on ? "check" : "x", "icon icon-sm"), `${t(label)}: ${t(on ? "visible" : "not visible")}`);
}

function modelResults(r) {
  const rows = [];
  for (const it of r.items) {
    if (it.type === "voice") {
      const ok = it.asr_status === "ok";
      rows.push(el("div", { class: "wa-result" },
        el("div", { class: "wa-msg-head" }, svg("mic", "icon icon-sm"), el("strong", {}, t("Transcript")), el("span", { class: "ev-chip" }, it.id), aiBadge(it.asr_provider || "Muse Voice Transcribe")),
        ok ? el("p", { class: "wa-bubble" }, it.transcript) : el("p", { class: "empty" }, it.asr_status === "pending" ? t("pending") : `${t("failed")}: ${it.asr_status}`)));
    } else if (it.type === "image") {
      const rd = it.reading;
      rows.push(el("div", { class: "wa-result" },
        el("div", { class: "wa-msg-head" }, svg("image", "icon icon-sm"), el("strong", {}, t("Photo reading")), el("span", { class: "ev-chip" }, it.id), aiBadge(it.reading_model || "Muse Spark")),
        rd ? [el("div", { class: "wa-flags" }, flag(rd.smoke_visible, "Smoke"), flag(rd.flames_visible, "Flames"), flag(rd.burned_ground_visible, "Burned ground")),
          el("p", { class: "wa-bubble", lang: "en" }, rd.description)]
          : el("p", { class: "empty" }, it.reading_status ? `${t("failed")}: ${it.reading_status}` : t("pending"))));
    }
  }
  return rows.length ? rows : el("p", { class: "empty" }, t("No photo or voice note in this report."));
}

function step(done, label, detail) {
  return el("li", { class: `wa-step ${done === true ? "done" : done === false ? "fail" : "wait"}` },
    el("span", { class: "wa-step-dot" }, svg(done === true ? "check" : done === false ? "x" : "clock", "icon icon-sm")),
    el("div", {}, el("strong", {}, label), detail ? el("p", { class: "muted" }, detail) : null));
}

function processing(r) {
  const first = r.items.reduce((a, b) => (a.at < b.at ? a : b));
  const media = r.items.filter((i) => i.type !== "report");
  const voices = r.items.filter((i) => i.type === "voice");
  const photos = r.items.filter((i) => i.type === "image");
  const notes = r.notifications.filter((n) => n.purpose === "verifier");
  const okNotes = notes.filter((n) => n.status === "sent" && !String(n.delivery || "").startsWith("failed")).length;
  const enr = r.enrichment || {};
  const verdict = r.verdict && r.verdict.decision !== "revoke" ? r.verdict : null;
  return el("ol", { class: "wa-steps" },
    step(true, t("Webhook received, Meta signature checked"), fmtTime(first.at)),
    media.length ? step(true, t("Media downloaded from WhatsApp; photo re-encoded without metadata (EXIF)"), media.map((m) => m.id).join(", ")) : null,
    step(true, t(r.trigger === "citizen_report" ? "New incident opened from the report" : "Joined an existing incident within 2 km and 24 h"), `${r.incident_id}, ${fmtTime(r.created_at)}`),
    voices.map((v) => step(v.asr_status === "ok" ? true : v.asr_status === "pending" ? null : false, t("Voice note transcribed"), [v.id, v.asr_provider, v.audio_ms ? `${(v.audio_ms / 1000).toFixed(1)} s` : null].filter(Boolean).join(", "))),
    photos.map((p) => step(p.reading_status === "ok" ? true : p.reading_status ? false : null, t("Photo read"), [p.id, p.reading_model].filter(Boolean).join(", "))),
    step(r.other_evidence.length ? true : null, t("Evidence collected"), [...r.other_evidence.map((e) => `${e.id} ${e.summary}`), ...Object.entries(enr).map(([k, v]) => `${k}: ${v}`)].join(" · ")),
    step(notes.length ? okNotes > 0 : null, t("Verifiers notified on WhatsApp"), notes.length ? [t("{ok} of {n} accepted", { ok: okNotes, n: notes.length }), ...notes.map((n) => n.delivery || n.status)].join(" · ") : t("none sent")),
    step(r.status === "pending_triage" ? null : true, t("AI triage"), r.status === "pending_triage" ? t("Not run yet. Triage spends Meta credit, so it runs only when a verifier starts it.") : t(STATUS[r.status] || r.status)),
    step(verdict ? true : null, t("Verifier decision"), verdict ? `${t(verdict.decision === "approve" ? "Approved" : "Rejected")}, ${fmtTime(verdict.created_at)}` : t("Waiting for a verifier.")));
}

function delivery(d) {
  if (!d) return el("span", { class: "muted" }, t("no delivery result yet"));
  const failed = d.startsWith("failed");
  return el("span", { class: `wa-flag ${failed ? "bad" : "on"}`, lang: "en" }, svg(failed ? "alert" : "check", "icon icon-sm"), failed ? d : t(d));
}

function replies(r) {
  if (!r.replies.length) return el("p", { class: "empty" }, t("No reply sent."));
  return r.replies.map((x) => el("div", { class: "wa-msg wa-msg-out" },
    el("div", { class: "wa-msg-head" }, svg("send", "icon icon-sm"), el("span", { class: "muted" }, fmtTime(x.created_at)),
      el("span", { class: `pill ${x.status === "sent" ? "" : "pill-sim"}` }, t(x.status === "sent" ? "Accepted by WhatsApp" : "Refused by WhatsApp"))),
    x.body ? el("p", { class: "wa-bubble wa-bubble-out" }, x.body) : el("p", { class: "empty" }, t("Text not recorded (sent before reply texts were stored).")),
    el("div", { class: "wa-delivery" }, el("span", { class: "muted" }, `${t("Delivery")}:`), delivery(x.delivery))));
}

function showcaseButton(r) {
  const b = el("button", { class: `btn ${r.public ? "" : "btn-primary"}`, type: "button" }, svg(r.public ? "x" : "share", "icon icon-sm"), t(r.public ? "Make private" : "Show as public example"));
  b.onclick = async () => {
    b.setAttribute("aria-disabled", "true");
    try {
      await api(`/api/wa/reports/${encodeURIComponent(r.incident_id)}/showcase`, { method: "PUT", body: JSON.stringify({ public: !r.public }) });
      toast(t("Saved."), "check");
      load();
    } catch (e) {
      toast(t("Failed: {e}", { e: e.message }), "alert");
      b.removeAttribute("aria-disabled");
    }
  };
  return b;
}

function reportCard(r, verifier) {
  return el("section", { class: "card admin-card wa-report" },
    el("div", { class: "wa-report-head" },
      el("h2", { class: "admin-title" }, el("span", { class: "section-icon" }, svg("send", "icon icon-sm")),
        el("a", { href: `/?incident=${encodeURIComponent(r.incident_id)}`, class: "mono" }, r.incident_id)),
      el("span", { class: "pill" }, t(STATUS[r.status] || r.status)),
      el("span", { class: `pill ${r.public ? "pill-live" : ""}` }, t(r.public ? "Public example" : "Private"), info(t(r.public ? "Shown to everyone on this page." : "Visible to verifiers only.")))),
    el("dl", { class: "kv" },
      el("dt", {}, t("Received")), el("dd", {}, fmtTime(r.items.reduce((a, b) => (a.at < b.at ? a : b)).at)),
      el("dt", {}, el("span", { class: "wa-dt" }, t("Reporter"), info(t("Only the first characters of a salted hash of the number. The number itself is never stored with the report.")))), el("dd", { class: "mono" }, `#${r.reporter || "?"}`),
      el("dt", {}, el("span", { class: "wa-dt" }, t("Location"), verifier ? null : info(t("Rounded to about 100 m for the public.")))), el("dd", { class: "mono" }, `${r.lat}, ${r.lon}`)),
    block("file", t("What the resident sent"), null, sentItems(r)),
    block("sparkle", t("What the Meta models found"), t("Voice notes are transcribed and photos are read on Meta Model API in Standard mode, so citizen content is not used for training. The agent works from the transcript and the reading, never from raw audio."), modelResults(r)),
    block("list", t("How the system processed it"), null, processing(r)),
    block("send", t("What the bot answered"), t("Accepted means WhatsApp took the message; the delivery result arrives later from Meta and is shown next to it."), replies(r)),
    verifier ? el("div", { class: "actions" }, showcaseButton(r), el("a", { class: "btn", href: `/?incident=${encodeURIComponent(r.incident_id)}` }, svg("layers", "icon icon-sm"), t("Open on map"))) : null);
}

function intro(d) {
  return el("section", { class: "card admin-card" },
    el("div", { class: "wa-report-head" },
      el("h2", { class: "admin-title" }, el("span", { class: "section-icon" }, svg("send", "icon icon-sm")), t("Citizen reports via WhatsApp")),
      el("span", { class: "pill pill-live" }, el("span", { class: "pulse" }), t("Real data from the production system"),
        info(t("Every report on this page was sent by WhatsApp to the Meta test number and processed by the system that runs karhutla.thariq.xyz. It is not a simulation and not a replay. Only reports a verifier marked as public examples are shown to everyone."))),
      el("span", { class: "badge-count num" }, t("{n} report(s)", { n: d.count }))),
    el("p", { class: "wa-limit" }, svg("alert", "icon icon-sm"), t("Replies do not reach WhatsApp yet: Meta refuses messages from this business account until it passes business verification. Each reply is still sent and its refusal recorded below.")));
}

async function load() {
  try {
    const d = await api("/api/wa/reports");
    fill($("#wa-body"), intro(d), d.items.length ? d.items.map((r) => reportCard(r, d.verifier)) : el("p", { class: "empty" }, t(d.verifier ? "No WhatsApp reports yet." : "No public WhatsApp reports yet.")));
  } catch (e) {
    fill($("#wa-body"), el("p", { class: "empty" }, t("Could not load the reports: {e}", { e: e.message })));
  }
}

function skeleton() {
  return el("div", { class: "card admin-card" }, el("span", { class: "sk sk-title" }), el("span", { class: "sk sk-line" }), el("span", { class: "sk sk-block" }), el("span", { class: "sk sk-block" }));
}

function main() {
  applyStatic(t);
  hydrateIcons();
  $("#lang-slot").replaceWith(langSwitch(el));
  $("#verifier-slot").replaceWith(verifierControl(t));
  $("#crumbs").replaceChildren(el("a", { href: "/" }, t("Incident map")), svg("chevron", "icon icon-sm crumb-sep"), el("span", { class: "current" }, t("WhatsApp reports")));
  siteFooter($("#site-footer"), lang());
  fill($("#wa-body"), skeleton(), skeleton());
  load();
}

main();
