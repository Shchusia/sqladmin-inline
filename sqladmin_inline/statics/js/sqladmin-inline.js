/*!
 * sqladmin-inline — client side for inline sections.
 * Works fully offline: depends only on assets bundled with sqladmin
 * (Bootstrap 5 via Tabler, optional jQuery/select2/flatpickr) and on
 * SortableJS shipped with this package.
 */
(function () {
  "use strict";

  // sqladmin 0.21-0.27: their edit/create templates wrap the <input type="submit">
  // buttons in `data-toggle="buttons"`. That attribute is meant for checkbox/radio
  // groups; the Bootstrap 4.6 they also load reads `.checked` of a non-existent
  // child <input> on window "load" and throws "Cannot read properties of null
  // (reading 'checked')". This script runs before "load", so drop the attribute
  // wherever a button has no input inside (i.e. where it can only break).
  Array.prototype.forEach.call(document.querySelectorAll('[data-toggle="buttons"]'), function (group) {
    var buttons = group.querySelectorAll(".btn");
    var broken = Array.prototype.some.call(buttons, function (b) { return !b.querySelector("input"); });
    if (broken) group.removeAttribute("data-toggle");
  });

  // ---------------------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------------------

  // Modals are driven by this file, not by Bootstrap's JS: sqladmin 0.21-0.27
  // load Bootstrap 4.6 *and* Bootstrap 5 (inside Tabler) on the same page, so
  // neither `window.bootstrap` nor `$.fn.modal` can be trusted. Only the CSS
  // classes (.modal/.show/.modal-backdrop), identical in both, are used.
  function showModal(el) {
    if (el.classList.contains("show")) return;
    var backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop fade show";
    document.body.appendChild(backdrop);
    el._inlineBackdrop = backdrop;
    el.style.display = "block";
    el.removeAttribute("aria-hidden");
    el.setAttribute("aria-modal", "true");
    document.body.classList.add("modal-open");
    void el.offsetWidth; // reflow so the fade-in transition runs
    el.classList.add("show");
  }

  function hideModal(el) {
    if (!el.classList.contains("show")) return;
    if (el.id === "inline-modal") formToken++;
    el.classList.remove("show");
    el.style.display = "none";
    el.setAttribute("aria-hidden", "true");
    el.removeAttribute("aria-modal");
    if (el._inlineBackdrop) { el._inlineBackdrop.remove(); el._inlineBackdrop = null; }
    if (!document.querySelector(".modal.show")) document.body.classList.remove("modal-open");
  }

  function toggleSection(header) {
    var body = document.getElementById(header.getAttribute("aria-controls"));
    if (!body) return;
    var open = !body.classList.contains("show");
    body.classList.toggle("show", open);
    header.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function parseHTML(html) {
    var tpl = document.createElement("template");
    tpl.innerHTML = html.trim();
    return tpl.content;
  }

  /** fetch() wrapper: follows sqladmin's login redirect by reloading the page. */
  function request(url, options) {
    options = options || {};
    options.credentials = "same-origin";
    options.headers = Object.assign({ "X-Requested-With": "XMLHttpRequest" }, options.headers || {});
    return fetch(url, options).then(function (resp) {
      if (resp.redirected && /\/login(\?|$)/.test(new URL(resp.url).pathname + "?")) {
        window.location.href = resp.url;
        return new Promise(function () {});
      }
      return resp;
    });
  }

  function errorText(resp) {
    var type = resp.headers.get("content-type") || "";
    if (type.indexOf("application/json") !== -1) {
      return resp.json().then(function (data) {
        return (data && data.error) || resp.status + " " + resp.statusText;
      });
    }
    return Promise.resolve(resp.status + " " + resp.statusText);
  }

  function showAlert(container, message) {
    if (!container) { window.alert(message); return; }
    var div = document.createElement("div");
    div.className = "alert alert-danger alert-dismissible m-2";
    div.setAttribute("role", "alert");
    div.textContent = message;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close";
    close.addEventListener("click", function () { div.remove(); });
    close.setAttribute("aria-label", "Close");
    div.appendChild(close);
    container.prepend(div);
  }

  function section(el) { return el.closest(".inline-section"); }
  function sectionAlerts(sec) { return sec ? sec.querySelector(".inline-alerts") : null; }

  // ---------------------------------------------------------------------------
  // Section reload / load more
  // ---------------------------------------------------------------------------

  function reloadSection(sec, url) {
    url = url || sec.dataset.currentUrl;
    var wasCollapsed = !sec.querySelector(".collapse").classList.contains("show");
    return request(url).then(function (resp) {
      if (!resp.ok) return errorText(resp).then(function (msg) { showAlert(sectionAlerts(sec), msg); });
      return resp.text().then(function (html) {
        var fresh = parseHTML(html).querySelector(".inline-section");
        if (!fresh) { window.location.reload(); return; }
        if (wasCollapsed) {
          fresh.querySelector(".collapse").classList.remove("show");
          fresh.querySelector(".inline-header").setAttribute("aria-expanded", "false");
        }
        destroySortable(sec);
        sec.replaceWith(fresh);
        initSortable(fresh);
      });
    }).catch(function (err) { showAlert(sectionAlerts(sec), "Network error: " + err); });
  }

  function loadMore(btn) {
    var sec = section(btn);
    btn.disabled = true;
    request(btn.dataset.url).then(function (resp) {
      if (!resp.ok) {
        btn.disabled = false;
        return errorText(resp).then(function (msg) { showAlert(sectionAlerts(sec), msg); });
      }
      return resp.text().then(function (html) {
        var fresh = parseHTML(html).querySelector(".inline-section");
        if (!fresh) { window.location.reload(); return; }
        var tbody = sec.querySelector("tbody");
        fresh.querySelectorAll("tbody tr[data-pk]").forEach(function (tr) {
          if (!tbody.querySelector('tr[data-pk="' + CSS.escape(tr.dataset.pk) + '"]')) tbody.appendChild(tr);
        });
        var shown = tbody.querySelectorAll("tr[data-pk]").length;
        var total = fresh.querySelector("[data-inline-count]").textContent.trim();
        sec.querySelector(".inline-range").textContent = "Showing 1–" + shown + " of " + total;
        var pagination = sec.querySelector(".inline-pagination");
        if (pagination) pagination.remove();
        var nextBtn = fresh.querySelector(".inline-load-more");
        if (nextBtn) btn.replaceWith(nextBtn); else btn.remove();
        initSortable(sec);
      });
    }).catch(function (err) {
      btn.disabled = false;
      showAlert(sectionAlerts(sec), "Network error: " + err);
    });
  }

  // ---------------------------------------------------------------------------
  // Add / edit modal
  // ---------------------------------------------------------------------------

  var modalEl, modalBody, modalTitle, saveBtn, saveSpinner, activeSection = null;

  function initFormWidgets(root) {
    var $ = window.jQuery;
    if ($ && $.fn.select2) {
      $(root).find('select[data-role="select2"]').each(function () {
        $(this).select2({ width: "100%", dropdownParent: $(modalEl), allowClear: !this.required, placeholder: "" });
      });
      $(root).find('select[data-role="select2-tags"]').each(function () {
        $(this).select2({ width: "100%", dropdownParent: $(modalEl), tags: true, multiple: true });
      });
    }
    if (window.flatpickr) {
      root.querySelectorAll('[data-role="datepicker"]:not([readonly])').forEach(function (el) {
        window.flatpickr(el, { allowInput: true, dateFormat: "Y-m-d" });
      });
      root.querySelectorAll('[data-role="datetimepicker"]:not([readonly])').forEach(function (el) {
        window.flatpickr(el, { enableTime: true, enableSeconds: true, time_24hr: true,
                               allowInput: true, dateFormat: "Y-m-d H:i:s" });
      });
    }
    var first = root.querySelector("input:not([type=hidden]), textarea, select");
    if (first) setTimeout(function () { first.focus(); }, 200);
  }

  function setBody(html) {
    modalBody.innerHTML = html;
    initFormWidgets(modalBody);
  }

  // Each opening gets a token: a slow response for a modal that was closed or
  // re-opened in the meantime must not overwrite the current form.
  var formToken = 0;

  function openForm(trigger, title) {
    var token = ++formToken;
    var current = function () { return token === formToken && modalEl.classList.contains("show"); };
    activeSection = section(trigger);
    modalTitle.textContent = title + " " + (activeSection ? activeSection.dataset.label : "");
    modalBody.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</div>';
    saveBtn.disabled = true;
    showModal(modalEl);
    request(trigger.dataset.formUrl).then(function (resp) {
      if (!current()) return;
      if (!resp.ok) {
        return errorText(resp).then(function (msg) {
          if (current()) { modalBody.innerHTML = ""; showAlert(modalBody, msg); }
        });
      }
      return resp.text().then(function (html) {
        if (current()) { setBody(html); saveBtn.disabled = false; }
      });
    }).catch(function (err) {
      if (current()) { modalBody.innerHTML = ""; showAlert(modalBody, "Network error: " + err); }
    });
  }


  function save() {
    var form = document.getElementById("inline-modal-form");
    if (!form || !activeSection) return;
    saveBtn.disabled = true;
    saveSpinner.classList.remove("d-none");
    var sec = activeSection;
    request(form.dataset.saveUrl, { method: "POST", body: new FormData(form) }).then(function (resp) {
      if (resp.status === 422) return resp.text().then(setBody);
      if (!resp.ok) return errorText(resp).then(function (msg) { showAlert(modalBody, msg); });
      hideModal(modalEl);
      reloadSection(sec);
    }).catch(function (err) {
      showAlert(modalBody, "Network error: " + err);
    }).finally(function () {
      saveBtn.disabled = false;
      saveSpinner.classList.add("d-none");
    });
  }

  // ---------------------------------------------------------------------------
  // Bulk delete
  // ---------------------------------------------------------------------------

  var deleteModalEl, pendingDelete = null;

  function selectedPks(sec) {
    return Array.prototype.map.call(sec.querySelectorAll(".inline-select-box:checked"), function (cb) {
      return cb.value;
    });
  }

  function updateBulkButton(sec) {
    var btn = sec.querySelector(".inline-bulk-delete");
    if (btn) btn.classList.toggle("d-none", selectedPks(sec).length === 0);
  }

  function askDelete(btn) {
    var sec = section(btn), pks = selectedPks(sec);
    if (!pks.length) return;
    pendingDelete = { section: sec, url: btn.dataset.deleteUrl, pks: pks };
    document.getElementById("inline-delete-text").textContent =
      "This will permanently delete " + pks.length + " item(s).";
    showModal(deleteModalEl);
  }

  function confirmDelete(btn) {
    if (!pendingDelete) return;
    var job = pendingDelete, spinner = document.getElementById("inline-delete-spinner");
    btn.disabled = true;
    spinner.classList.remove("d-none");
    request(job.url, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pks: job.pks }),
    }).then(function (resp) {
      if (!resp.ok) return errorText(resp).then(function (msg) { showAlert(sectionAlerts(job.section), msg); });
      pendingDelete = null;
      reloadSection(job.section);
    }).catch(function (err) {
      showAlert(sectionAlerts(job.section), "Network error: " + err);
    }).finally(function () {
      btn.disabled = false;
      spinner.classList.add("d-none");
      hideModal(deleteModalEl);
    });
  }

  // ---------------------------------------------------------------------------
  // Drag-and-drop ordering (SortableJS, bundled)
  // ---------------------------------------------------------------------------

  function destroySortable(sec) {
    var tbody = sec && sec.querySelector("tbody");
    if (tbody && tbody._inlineSortable) {
      tbody._inlineSortable.destroy();
      tbody._inlineSortable = null;
    }
  }

  function initSortable(sec) {
    var table = sec.querySelector("table[data-reorder-url]");
    if (!table || !window.Sortable) return;
    var tbody = table.querySelector("tbody");
    destroySortable(sec);
    tbody._inlineSortable = window.Sortable.create(tbody, {
      handle: ".inline-drag-handle",
      draggable: "tr[data-pk]",
      animation: 150,
      ghostClass: "table-active",
      onEnd: function (evt) {
        if (evt.oldIndex === evt.newIndex) return;
        var pks = Array.prototype.map.call(tbody.querySelectorAll("tr[data-pk]"), function (tr) {
          return tr.dataset.pk;
        });
        request(table.dataset.reorderUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pks: pks }),
        }).then(function (resp) {
          if (!resp.ok) {
            return errorText(resp).then(function (msg) {
              showAlert(sectionAlerts(sec), msg);
              reloadSection(sec);
            });
          }
        }).catch(function (err) { showAlert(sectionAlerts(sec), "Network error: " + err); });
      },
    });
  }

  // ---------------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------------

  function search(sec, term) {
    var url = sec.dataset.baseUrl + "/list?page=1&search=" + encodeURIComponent(term || "");
    reloadSection(sec, url);
  }

  // ---------------------------------------------------------------------------
  // Event wiring (delegated: survives section reloads)
  // ---------------------------------------------------------------------------

  function on(type, selector, handler) {
    document.addEventListener(type, function (e) {
      if (!e.target || typeof e.target.closest !== "function") return; // document, window, text nodes
      var target = e.target.closest(selector);
      if (target) handler(e, target);
    });
  }

  function wire() {
    modalEl = document.getElementById("inline-modal");
    deleteModalEl = document.getElementById("inline-delete-modal");
    if (!modalEl) return;
    modalBody = document.getElementById("inline-modal-body");
    modalTitle = document.getElementById("inline-modal-title");
    saveBtn = document.getElementById("inline-modal-save");
    saveSpinner = document.getElementById("inline-modal-spinner");

    // Header controls sit inside the clickable header; they must not toggle it.
    on("click", ".inline-header", function (e, header) {
      if (!e.target.closest(".inline-no-toggle")) toggleSection(header);
    });
    on("keydown", ".inline-header", function (e, header) {
      if ((e.key === "Enter" || e.key === " ") && e.target === header) { e.preventDefault(); toggleSection(header); }
    });
    on("click", ".inline-add-btn", function (e, t) { e.preventDefault(); openForm(t, "Add"); });
    on("click", ".inline-search-btn", function (e, t) {
      var sec = section(t); search(sec, sec.querySelector(".inline-search-input").value.trim());
    });
    on("click", ".inline-search-clear", function (e, t) { search(section(t), ""); });
    on("click", "[data-inline-dismiss]", function (e, t) { hideModal(t.closest(".modal")); });
    [modalEl, deleteModalEl].forEach(function (m) {
      if (!m) return;
      m.addEventListener("mousedown", function (e) { if (e.target === m) hideModal(m); });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") document.querySelectorAll(".modal.show").forEach(hideModal);
    });

    document.addEventListener("keydown", function (e) {
      var input = e.target.closest && e.target.closest(".inline-search-input");
      if (input && e.key === "Enter") { e.preventDefault(); search(section(input), input.value.trim()); }
    });

    on("click", ".inline-edit-btn", function (e, t) { e.preventDefault(); openForm(t, "Edit"); });
    on("click", ".inline-page-link", function (e, t) {
      e.preventDefault();
      if (!t.closest(".page-item.disabled")) reloadSection(section(t), t.dataset.url);
    });
    on("click", ".inline-load-more", function (e, t) { e.preventDefault(); loadMore(t); });
    on("click", ".inline-bulk-delete", function (e, t) { e.preventDefault(); askDelete(t); });
    on("click", "#inline-delete-confirm", function (e, t) { confirmDelete(t); });
    on("click", "#inline-modal-save", function () { save(); });
    on("change", ".inline-select-all", function (e, t) {
      var sec = section(t);
      sec.querySelectorAll(".inline-select-box").forEach(function (cb) { cb.checked = t.checked; });
      updateBulkButton(sec);
    });
    on("change", ".inline-select-box", function (e, t) { updateBulkButton(section(t)); });

    // Enter inside the modal form saves instead of submitting the page.
    modalEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); save(); }
    });

    document.querySelectorAll(".inline-section").forEach(initSortable);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
