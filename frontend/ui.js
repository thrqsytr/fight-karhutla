/* Shared UI building blocks for every page. One component per job, reused everywhere. */

/* Icons: simple stroke glyphs drawn for this project. */
export const ICONS = {
  flame: '<path d="M12 2.5c.6 3.2 2.6 4.8 4.2 6.6A7 7 0 0 1 12 21.5a7 7 0 0 1-4.6-12.3c.3 1.8 1.2 3 2.4 3.4C9.4 9 10.4 5.4 12 2.5z"/><path d="M12 21.5a3 3 0 0 1-3-3c0-1.8 1.4-2.8 3-4.6 1.6 1.8 3 2.8 3 4.6a3 3 0 0 1-3 3z"/>',
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  dots: '<circle cx="7" cy="12" r="2"/><circle cx="12" cy="7" r="2"/><circle cx="17" cy="12" r="2"/><circle cx="12" cy="17" r="2"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>',
  alert: '<path d="M12 4l9 16H3l9-16z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  satellite: '<rect x="9" y="9" width="6" height="6" rx="1" transform="rotate(45 12 12)"/><path d="M3.5 8.5l3-3 3 3-3 3z"/><path d="M14.5 17.5l3-3 3 3-3 3z"/><path d="M3 21c0-2.5 1.5-4 4-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  pin: '<path d="M12 21s-6-5.3-6-11a6 6 0 0 1 12 0c0 5.7-6 11-6 11z"/><circle cx="12" cy="10" r="2.2"/>',
  file: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/><path d="M10 12h5"/><path d="M10 16h5"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
  layers: '<path d="M12 4l9 5-9 5-9-5 9-5z"/><path d="M3 14l9 5 9-5"/>',
  gavel: '<path d="M14 5l5 5"/><path d="M11 8l5 5"/><path d="M12.5 6.5l-6 6"/><path d="M4 20h9"/><path d="M8.5 15.5L5 19"/>',
  activity: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  list: '<path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/><path d="M4 6h.01"/><path d="M4 12h.01"/><path d="M4 18h.01"/>',
  x: '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
  reset: '<path d="M4 12a8 8 0 1 0 2.5-5.8"/><path d="M4 4v4h4"/><circle cx="12" cy="12" r="1.6"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  crosshair: '<circle cx="12" cy="12" r="7"/><path d="M12 2v4"/><path d="M12 18v4"/><path d="M2 12h4"/><path d="M18 12h4"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
  send: '<path d="M4 12l16-8-6 16-3-6-7-2z"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  arrow: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/><circle cx="12" cy="12" r="6.5"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  image: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="M21 16l-5-5-8 8"/>',
};
export const svg = (name, cls = "icon") => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", cls);
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
};

export const $ = (sel) => document.querySelector(sel);
export const el = (tag, attrs = {}, ...children) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k === "style") n.style.cssText = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  // Flatten fully: sections pass nested arrays of optional parts.
  for (const c of children.flat(Infinity)) if (c != null && c !== false) n.append(c);
  return n;
};
/* (i) tooltips are drawn in one fixed layer above everything, so a scrolling panel or a map card
   can never clip them. The text inside each .info stays in place for screen readers. */
let tipLayer;
function showTip(anchor) {
  const src = anchor.querySelector(".tip");
  if (!src) return;
  if (!tipLayer) { tipLayer = el("div", { class: "tip-layer", "aria-hidden": "true" }); document.body.append(tipLayer); }
  tipLayer.textContent = src.textContent;
  tipLayer.style.left = "0px";
  tipLayer.style.top = "0px";
  const a = anchor.getBoundingClientRect();
  const b = tipLayer.getBoundingClientRect();
  const m = 8;
  const left = Math.max(m, Math.min(a.left + a.width / 2 - b.width / 2, innerWidth - b.width - m));
  let top = a.bottom + 6;
  if (top + b.height > innerHeight - m) top = a.top - b.height - 6;
  tipLayer.style.left = `${left}px`;
  tipLayer.style.top = `${Math.max(m, top)}px`;
  tipLayer.classList.add("show");
}
const hideTip = () => tipLayer?.classList.remove("show");
document.addEventListener("pointerover", (e) => { const a = e.target.closest?.(".info"); if (a) showTip(a); });
document.addEventListener("pointerout", (e) => { const a = e.target.closest?.(".info"); if (a && !a.contains(e.relatedTarget)) hideTip(); });
document.addEventListener("focusin", (e) => { const a = e.target.closest?.(".info"); if (a) showTip(a); else hideTip(); });
document.addEventListener("focusout", hideTip);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
addEventListener("scroll", hideTip, true);
addEventListener("resize", hideTip);

export const info = (text, cls = "") => el("span", { class: `info ${cls}`, tabindex: "0", "aria-label": "More information" }, svg("info", "icon icon-sm"), el("span", { class: "tip", role: "tooltip" }, text));

export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((n) => {
    const cls = n.classList.contains("icon-sm") ? "icon icon-sm" : "icon";
    n.replaceWith(svg(n.dataset.icon, cls));
  });
}

export function toast(msg, icon = "check") {
  const t = el("div", { class: "toast", role: "status" }, svg(icon, "icon icon-sm"), msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 3200);
}

/* Sections: one collapsible component used everywhere */
export function section({ icon, title, count, tip, open = false, live = false, content, extraClass = "" }) {
  const body = el("div", { class: "section-body" }, el("div", { class: "section-inner" }, el("div", { class: "section-content" }, content)));
  const head = el("button", { class: "section-head", "aria-expanded": String(open) },
    el("span", { class: "section-icon" }, svg(icon, "icon icon-sm")),
    el("span", { class: "section-title" }, title, count != null ? el("span", { class: "badge-count num" }, String(count)) : null, live ? el("span", { class: "pulse", title: "Recent data" }) : null),
    tip ? info(tip, "tip-right") : null,
    svg("chevron", "icon chev"),
  );
  const s = el("section", { class: `card section ${extraClass}`, dataset: { open: String(open) } }, head, body);
  head.addEventListener("click", (e) => {
    if (e.target.closest(".info")) return;
    const next = s.dataset.open !== "true";
    s.dataset.open = String(next);
    head.setAttribute("aria-expanded", String(next));
  });
  return s;
}

/* Modal: the one dialog component (native alert, confirm, and prompt are not used). Esc or the backdrop closes it. */
export function modal({ title, body, actions = [], onClose }) {
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); onClose?.(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const card = el("div", { class: "modal-card card", role: "dialog", "aria-modal": "true", "aria-label": title },
    el("div", { class: "modal-head" }, el("h3", {}, title), el("button", { class: "btn btn-icon", type: "button", "aria-label": "Close", onclick: close }, svg("x", "icon icon-sm"))),
    el("div", { class: "modal-body" }, body),
    el("div", { class: "modal-actions" }, actions));
  const back = el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === back) close(); } }, card);
  document.body.append(back);
  document.addEventListener("keydown", onKey);
  setTimeout(() => card.querySelector("input, button.btn-primary")?.focus(), 30);
  return { close, card };
}

/* Site pages: one list feeds the footer on every page and the tabs on the information pages. */
export const SITE_PAGES = [
  { path: "/about/", id: "Tentang", en: "About" },
  { path: "/sources/", id: "Sumber data", en: "Data sources" },
  { path: "/privacy/", id: "Privasi", en: "Privacy" },
  { path: "/terms/", id: "Ketentuan", en: "Terms" },
  { path: "/license/", id: "Lisensi", en: "License" },
];
export const REPO_URL = "https://github.com/thrqsytr/fight-karhutla";

/** Footer: links only. The emergency notice and data attributions live on their own pages. */
export function siteFooter(node, lang) {
  const here = location.pathname;
  const link = (p) => el("a", { href: p.path, "aria-current": here === p.path ? "page" : null }, p[lang] || p.en);
  node.replaceChildren(
    el("a", { class: "footer-emergency", href: "tel:112" }, svg("alert", "icon icon-sm"), lang === "id" ? "Darurat: hubungi 112" : "Emergency: call 112"),
    el("nav", { class: "footer-links", "aria-label": lang === "id" ? "Halaman informasi" : "Information pages" },
      SITE_PAGES.map(link), el("a", { href: REPO_URL, rel: "noopener" }, "GitHub")),
  );
}
