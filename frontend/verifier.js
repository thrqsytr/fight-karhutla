import { el, modal, svg } from "/ui.js";

/* Verifier sign in, shared by the dashboard and the admin panel. The token is checked with the server,
   then kept in this browser's localStorage so it is entered once, not on every action. Triage still asks
   for the execution code each time, because that is what spends credit. */
export const TOKEN_KEY = "karhutla.verifierToken";
export const token = () => localStorage.getItem(TOKEN_KEY) || "";
export const authHeaders = () => (token() ? { Authorization: `Bearer ${token()}` } : {});

async function check(value) {
  const res = await fetch("/api/auth/check", { headers: { Authorization: `Bearer ${value}` } });
  return res.ok;
}

export function openSignIn(t, { onDone } = {}) {
  const input = el("input", { type: "password", class: "input", autocomplete: "current-password", placeholder: t("Verifier token"), "aria-label": t("Verifier token") });
  const err = el("p", { class: "form-error", hidden: true });
  const ok = el("button", { class: "btn btn-primary", type: "button" }, svg("shield", "icon icon-sm"), t("Save and sign in"));
  const dlg = modal({
    title: t("Verifier sign in"),
    body: [
      el("p", { style: "margin:0" }, t("The token unlocks AI triage, verdicts, citizen media, and the admin panel. It is checked with the server and kept in this browser only.")),
      input, err,
    ],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Cancel")), ok],
  });
  const go = async () => {
    if (!input.value.trim()) return;
    ok.disabled = true;
    try {
      if (!(await check(input.value.trim()))) throw new Error(t("Token not accepted by the server."));
      localStorage.setItem(TOKEN_KEY, input.value.trim());
      dlg.close();
      onDone ? onDone() : location.reload();
    } catch (e) {
      err.hidden = false;
      err.replaceChildren(svg("alert", "icon icon-sm"), el("span", {}, e.message));
      ok.disabled = false;
    }
  };
  ok.onclick = go;
  input.addEventListener("keydown", (e) => e.key === "Enter" && go());
}

function openAccount(t) {
  const dlg = modal({
    title: t("Verifier mode is on"),
    body: [
      el("p", { style: "margin:0" }, t("This browser holds a verifier token. AI triage, verdicts, and citizen media are available. Triage still asks for the execution code before it spends credit.")),
    ],
    actions: [
      el("button", { class: "btn btn-ghost-danger", type: "button", onclick: () => { localStorage.removeItem(TOKEN_KEY); location.reload(); } }, svg("x", "icon icon-sm"), t("Sign out")),
      el("a", { class: "btn btn-primary", href: "/admin/" }, svg("gear", "icon icon-sm"), t("Open admin panel")),
    ],
  });
  return dlg;
}

/** Header control: a sign in button, or a "verifier mode" badge with the admin link once a token is saved. */
export function verifierControl(t) {
  if (!token()) {
    return el("button", { class: "btn verifier-btn", type: "button", onclick: () => openSignIn(t), title: t("Verifier sign in") },
      svg("shield", "icon icon-sm"), el("span", { class: "btn-label" }, t("Verifier")));
  }
  return el("div", { class: "verifier-on" },
    el("button", { class: "btn verifier-btn is-on", type: "button", onclick: () => openAccount(t), title: t("Verifier mode is on") },
      el("span", { class: "pulse" }), el("span", { class: "btn-label" }, t("AI triage ready"))),
    el("a", { class: "btn btn-icon", href: "/admin/", title: t("Admin panel"), "aria-label": t("Admin panel") }, svg("gear", "icon icon-sm")));
}

export const VERIFIER_ID = {
  "Verifier token": "Token verifikator", "Save and sign in": "Simpan dan masuk", "Verifier sign in": "Masuk verifikator",
  "The token unlocks AI triage, verdicts, citizen media, and the admin panel. It is checked with the server and kept in this browser only.":
    "Token membuka triase AI, keputusan verifikator, media warga, dan panel admin. Token dicek ke server lalu disimpan di browser ini saja.",
  "Token not accepted by the server.": "Token ditolak server.", "Cancel": "Batal",
  "Verifier mode is on": "Mode verifikator aktif",
  "This browser holds a verifier token. AI triage, verdicts, and citizen media are available. Triage still asks for the execution code before it spends credit.":
    "Browser ini menyimpan token verifikator. Triase AI, keputusan, dan media warga tersedia. Triase tetap meminta kode eksekusi sebelum memakai kredit.",
  "Sign out": "Keluar", "Open admin panel": "Buka panel admin", "Verifier": "Verifikator", "AI triage ready": "Triase AI siap", "Admin panel": "Panel admin",
};
