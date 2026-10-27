import { $, el, hydrateIcons, siteFooter, SITE_PAGES, svg } from "/ui.js";
import { applyStatic, lang, langSwitch, makeT } from "/i18n.js";

/* Information pages carry one article per language; this shows the reader's one and builds the shared parts. */
const t = makeT({ "Peta": "Map", "Peta insiden": "Incident map" }, "id");
const cur = lang();

function main() {
  const articles = [...document.querySelectorAll("article[lang]")];
  const art = articles.find((a) => a.lang === cur) || articles[0];
  articles.forEach((a) => { a.hidden = a !== art; });
  applyStatic(t);
  document.documentElement.lang = art.lang;
  document.title = `${art.dataset.title}: Fight Karhutla`;
  hydrateIcons();
  $("#lang-slot").replaceWith(langSwitch(el));
  $("#crumbs").replaceChildren(
    el("a", { href: "/" }, t("Peta insiden")), svg("chevron", "icon icon-sm crumb-sep"), el("span", { class: "current" }, art.dataset.title));
  $("#doc-tabs").replaceChildren(...SITE_PAGES.map((p) =>
    el("a", { href: p.path, "aria-current": location.pathname === p.path ? "page" : null }, p[cur] || p.en)));
  siteFooter($("#site-footer"), cur);
}

main();
