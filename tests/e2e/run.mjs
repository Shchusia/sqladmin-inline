// End-to-end check of the inline UI in a headless DOM (jsdom).
// Usage: node run.mjs http://127.0.0.1:PORT
// Every request to a host other than the local server is blocked and reported,
// which simulates an admin used inside a VPN without internet access.
import { JSDOM, ResourceLoader, VirtualConsole } from "jsdom";

const BASE = process.argv[2];
const external = [];
const errors = [];
const steps = [];

class LocalOnly extends ResourceLoader {
  fetch(url, options) {
    if (!url.startsWith(BASE)) { external.push(url); return null; }
    return super.fetch(url, options);
  }
}

const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  const msg = String(e && e.message || e) + (e && e.detail && e.detail.stack ? "\n" + e.detail.stack.split("\n").slice(0, 4).join("\n") : "");
  const known = /Could not parse CSS stylesheet|Not implemented: (window\.scrollTo|navigation)/;
  if (!known.test(msg)) errors.push(msg);
});
vc.on("error", (e) => errors.push(String(e)));

function bridgeFetch(win) {
  return (input, init = {}) => {
    const url = new URL(String(input), win.location.href).href;
    if (!url.startsWith(BASE)) { external.push(url); return Promise.reject(new Error("blocked: " + url)); }
    let body = init.body;
    if (body && body instanceof win.FormData) {
      const fd = new FormData();
      for (const [k, v] of body.entries()) fd.append(k, v);
      body = fd;
    }
    return fetch(url, { ...init, body }).catch((err) => { errors.push("fetch " + url + ": " + (err.cause && err.cause.message || err)); throw err; });
  };
}

const pageUrl = BASE + "/admin/post/edit/1";
const html = await (await fetch(pageUrl)).text();
const dom = new JSDOM(html, {
  url: pageUrl,
  runScripts: "dangerously",
  resources: new LocalOnly(),
  virtualConsole: vc,
  pretendToBeVisual: true,
  beforeParse(win) {
    win.fetch = bridgeFetch(win);
    win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {},
                              addEventListener() {}, removeEventListener() {} });
    win.CSS = win.CSS || {};
    win.CSS.escape = win.CSS.escape || ((s) => String(s).replace(/["\\]/g, "\\$&"));
    win.alert = (m) => errors.push("alert: " + m);
  },
});
const win = dom.window, doc = win.document;
await new Promise((r) => win.addEventListener("load", r));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(what, fn, timeout = 10000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try { const v = fn(); if (v) return v; } catch (e) { /* retry */ }
    await sleep(25);
  }
  throw new Error("timeout waiting for: " + what);
}
const $ = (s, root = doc) => root.querySelector(s);
const $$ = (s, root = doc) => Array.from(root.querySelectorAll(s));
const tags = () => $("#inline-tag_inline");
const comments = () => $("#inline-comment_inline");
const rows = (sec) => $$("tbody tr[data-pk]", sec).map((tr) => tr.children[2] ? tr.textContent.replace(/\s+/g, " ").trim() : "");
const count = (sec) => Number($("[data-inline-count]", sec).textContent);
const click = (el) => el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
const modalOpen = () => $("#inline-modal").classList.contains("show");
async function step(name, fn) {
  try { await fn(); steps.push(["ok", name]); }
  catch (e) { steps.push(["FAIL", name + ": " + e.message]); }
}
async function modalClosed() { await waitFor("modal closed", () => !modalOpen() && !$(".modal-backdrop")); }

await step("libraries loaded from the local server", async () => {
  if (!win.Sortable) throw new Error("Sortable missing");
  if (!win.jQuery) throw new Error("sqladmin assets missing");
  if ($$(".inline-section").length !== 2) throw new Error("expected 2 sections");
  if (!$("#inline-tag_inline tbody")._inlineSortable) throw new Error("Sortable not initialised");
});

await step("add: open modal, save, section reloads", async () => {
  click($(".inline-add-btn", tags()));
  await waitFor("add form", () => $("#inline-modal-form") && modalOpen());
  $('#inline-modal-form [name="name"]').value = "e2e-new";
  click($("#inline-modal-save"));
  await waitFor("count 6", () => count(tags()) === 6);
  await modalClosed();
});

await step("add: validation error is shown inside the modal", async () => {
  click($(".inline-add-btn", tags()));
  await waitFor("add form", () => $("#inline-modal-form [name=name]"));
  click($("#inline-modal-save"));
  await waitFor("invalid field", () => $("#inline-modal-form .is-invalid"));
  if (!modalOpen()) throw new Error("modal closed on validation error");
  click($('#inline-modal [data-inline-dismiss]'));
  await modalClosed();
  if (count(tags()) !== 6) throw new Error("count changed");
});

await step("modal closes with Escape and with a backdrop click", async () => {
  click($(".inline-add-btn", tags()));
  await waitFor("open", () => modalOpen() && $(".modal-backdrop"));
  doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await modalClosed();
  click($(".inline-add-btn", tags()));
  await waitFor("open again", () => modalOpen());
  $("#inline-modal").dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true }));
  await modalClosed();
  if (doc.body.classList.contains("modal-open")) throw new Error("body still modal-open");
});

await step("edit: prefilled form, saved value appears", async () => {
  click($(".inline-edit-btn", tags()));
  const input = await waitFor("edit form", () => $('#inline-modal-form [name="name"]'));
  if (input.value !== "tag1") throw new Error("prefill was " + input.value);
  input.value = "renamed";
  click($("#inline-modal-save"));
  await waitFor("renamed row", () => tags().textContent.includes("renamed"));
  await modalClosed();
});

await step("search with Enter, then clear", async () => {
  const input = $(".inline-search-input", tags());
  input.value = "tag3";
  input.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await waitFor("1 result", () => $$("tbody tr[data-pk]", tags()).length === 1 && tags().textContent.includes("tag3"));
  click($(".inline-search-clear", tags()));
  await waitFor("all rows back", () => $$("tbody tr[data-pk]", tags()).length === 3);
});

await step("clicking header controls does not collapse the section", async () => {
  for (const sel of [".inline-search-input", ".inline-search-btn"]) {
    click($(sel, tags()));
    await sleep(150);
    const body = $("#inline-body-tag_inline");
    if (!body.classList.contains("show") || body.classList.contains("collapsing")) throw new Error("collapsed by " + sel);
  }
});

await step("pagination link loads page 2", async () => {
  const link = $$(".inline-page-link", tags()).find((a) => a.textContent.trim() === "2");
  click(link);
  await waitFor("page 2", () => tags().textContent.includes("tag4") && !tags().textContent.includes("renamed"));
});

await step("load more appends rows", async () => {
  click($$(".inline-page-link", tags()).find((a) => a.textContent.trim() === "1"));
  await waitFor("page 1", () => tags().textContent.includes("renamed"));
  click($(".inline-load-more", tags()));
  await waitFor("6 rows", () => $$("tbody tr[data-pk]", tags()).length === 6);
  if ($(".inline-load-more", tags())) throw new Error("load more still visible");
  if (!$(".inline-range", tags()).textContent.includes("1–6 of 6")) throw new Error("range text");
});

await step("drag-and-drop reorder is persisted", async () => {
  const tbody = $("tbody", tags());
  const last = $$("tr[data-pk]", tbody).pop();
  tbody.insertBefore(last, tbody.firstElementChild);
  tbody._inlineSortable.options.onEnd({ oldIndex: 5, newIndex: 0 });
  const listUrl = tags().dataset.baseUrl + "/list";
  await waitFor("server order", async () => true);
  await sleep(300);
  const fresh = await (await fetch(BASE + listUrl)).text();
  const firstPk = /<tr data-pk="([^"]+)"/.exec(fresh)[1];
  if (firstPk !== last.dataset.pk) throw new Error(`first row is ${firstPk}, expected ${last.dataset.pk}`);
});

await step("bulk delete with confirmation", async () => {
  const before = count(tags());
  const box = $(".inline-select-box", tags());
  box.checked = true;
  box.dispatchEvent(new win.Event("change", { bubbles: true }));
  const bulk = $(".inline-bulk-delete", tags());
  if (bulk.classList.contains("d-none")) throw new Error("bulk button hidden");
  click(bulk);
  await waitFor("delete modal", () => $("#inline-delete-modal").classList.contains("show"));
  click($("#inline-delete-confirm"));
  await waitFor("count decreased", () => count(tags()) === before - 1);
  await waitFor("delete modal closed", () => !$("#inline-delete-modal").classList.contains("show") && !$(".modal-backdrop"));
});

await step("comment with FK select (select2) is created", async () => {
  click($(".inline-add-btn", comments()));
  await waitFor("comment form", () => $('#inline-modal-form [name="body"]'));
  $('#inline-modal-form [name="body"]').value = "Hello from e2e";
  const author = $('#inline-modal-form select[name="author"]');
  const alice = Array.from(author.options).find((o) => o.textContent.trim() === "Alice");
  author.value = alice.value;
  const post = $('#inline-modal-form select[name="post"]');
  if (post.value !== "1") throw new Error("parent not preselected: " + post.value);
  click($("#inline-modal-save"));
  await waitFor("comment row", () => comments().textContent.includes("Hello from e2e") && comments().textContent.includes("Alice"));
  await modalClosed();
});

await step("header click collapses and reload keeps it collapsed", async () => {
  click($(".inline-header", comments()));
  await waitFor("collapsed", () => $(".inline-header", comments()).getAttribute("aria-expanded") === "false");
  await waitFor("hidden", () => !$("#inline-body-comment_inline").classList.contains("show") &&
                                !$("#inline-body-comment_inline").classList.contains("collapsing"));
  const input = $(".inline-search-input", comments());
  input.value = "Hello";
  input.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await waitFor("reloaded", () => comments().dataset.currentUrl.includes("search=Hello"));
  if ($("#inline-body-comment_inline").classList.contains("show")) throw new Error("expanded after reload");
  if ($(".inline-header", comments()).getAttribute("aria-expanded") !== "false") throw new Error("aria-expanded");
});

await step("create page loads without JS errors", async () => {
  const before = errors.length;
  const createUrl = BASE + "/admin/post/create";
  const page = new JSDOM(await (await fetch(createUrl)).text(), {
    url: createUrl, runScripts: "dangerously", resources: new LocalOnly(), virtualConsole: vc,
    pretendToBeVisual: true,
    beforeParse(w) { w.fetch = bridgeFetch(w); w.matchMedia = win.matchMedia; },
  });
  await new Promise((r) => page.window.addEventListener("load", r));
  await sleep(200);
  page.window.close();
  if (errors.length !== before) throw new Error(errors.slice(before).join(" | "));
});

const failed = steps.filter((s) => s[0] !== "ok");
console.log(JSON.stringify({ steps, external, errors }, null, 2));
win.close();
process.exit(failed.length || external.length || errors.length ? 1 : 0);
