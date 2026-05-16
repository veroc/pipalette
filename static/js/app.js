// piPalette -- vanilla JS frontend (no build, no deps).

(function () {
  "use strict";

  // -------- helpers ----------------------------------------------------

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function strong(text) {
    var el = document.createElement("strong");
    el.textContent = text;
    return el;
  }

  function buildNodes(parts) {
    return parts.map(function (p) {
      return typeof p === "string" ? document.createTextNode(p) : p;
    });
  }

  function toast(message, kind) {
    var stack = $("#toast-stack");
    if (!stack) return;
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " toast-" + kind : "");
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(function () {
      el.style.transition = "opacity 200ms ease";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 250);
    }, 3200);
  }

  async function jsonFetch(url, options) {
    options = options || {};
    if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
      options.headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
      options.body = JSON.stringify(options.body);
    }
    var res = await fetch(url, options);
    var ct = res.headers.get("content-type") || "";
    var body = ct.indexOf("application/json") >= 0 ? await res.json() : await res.text();
    if (!res.ok) {
      var msg = (body && body.error) || (typeof body === "string" ? body : res.statusText);
      throw new Error(msg);
    }
    return body;
  }

  async function htmlFetch(url) {
    var res = await fetch(url);
    if (!res.ok) throw new Error("Failed to load " + url);
    return res.text();
  }

  // -------- topbar / status -------------------------------------------

  async function refreshTopbar() {
    try {
      var html = await htmlFetch("/partials/topbar");
      var head = $(".topbar");
      if (head) head.innerHTML = html;
    } catch (err) {
      console.warn(err);
    }
  }

  // -------- confirm dialog --------------------------------------------

  function confirmDialog(opts) {
    return new Promise(function (resolve) {
      var settled = false;
      function done(value) {
        if (settled) return;
        settled = true;
        document.removeEventListener("keydown", onKey);
        backdrop.remove();
        resolve(value);
      }

      var backdrop = document.createElement("div");
      backdrop.className = "modal-backdrop";
      var modal = document.createElement("div");
      modal.className = "modal";

      var head = document.createElement("div");
      head.className = "modal-head";
      var h = document.createElement("h3");
      h.className = "modal-title";
      h.textContent = opts.title || "Are you sure?";
      head.appendChild(h);

      var body = document.createElement("div");
      body.className = "modal-body confirm-body";
      if (opts.messageNodes) {
        opts.messageNodes.forEach(function (n) { body.appendChild(n); });
      } else {
        body.textContent = opts.message || "";
      }

      var foot = document.createElement("div");
      foot.className = "modal-foot";

      var cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "btn btn-ghost";
      cancelBtn.textContent = opts.cancelLabel || "Cancel";
      cancelBtn.addEventListener("click", function () { done(false); });

      var confirmBtn = document.createElement("button");
      confirmBtn.type = "button";
      confirmBtn.className = "btn " + (opts.danger ? "btn-confirm-danger" : "btn-primary");
      confirmBtn.textContent = opts.confirmLabel || "Confirm";
      confirmBtn.addEventListener("click", function () { done(true); });

      foot.appendChild(cancelBtn);
      foot.appendChild(confirmBtn);

      modal.appendChild(head);
      modal.appendChild(body);
      modal.appendChild(foot);
      backdrop.appendChild(modal);

      backdrop.addEventListener("click", function (ev) {
        if (ev.target === backdrop) done(false);
      });

      function onKey(ev) {
        if (ev.key === "Escape") {
          ev.preventDefault();
          done(false);
        } else if (ev.key === "Enter" && !opts.danger) {
          // For non-destructive dialogs, Enter confirms.  For destructive
          // dialogs we leave Enter unbound so accidental keypresses can't
          // trigger the action — user has to explicitly click/tab to confirm.
          ev.preventDefault();
          done(true);
        }
      }
      document.addEventListener("keydown", onKey);

      document.body.appendChild(backdrop);
      // Focus the safer choice on destructive dialogs.
      setTimeout(function () {
        (opts.danger ? cancelBtn : confirmBtn).focus();
      }, 30);
    });
  }

  // -------- modal helpers ---------------------------------------------

  function openModal(title, bodyNode, footNode) {
    var backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";

    var modal = document.createElement("div");
    modal.className = "modal";

    var head = document.createElement("div");
    head.className = "modal-head";
    var h = document.createElement("h3");
    h.className = "modal-title";
    h.textContent = title;
    head.appendChild(h);

    var body = document.createElement("div");
    body.className = "modal-body";
    if (bodyNode) body.appendChild(bodyNode);

    var foot = document.createElement("div");
    foot.className = "modal-foot";
    if (footNode) foot.appendChild(footNode);
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "btn btn-ghost";
    closeBtn.textContent = "Cancel";
    closeBtn.addEventListener("click", function () { backdrop.remove(); });
    foot.appendChild(closeBtn);

    modal.appendChild(head);
    modal.appendChild(body);
    modal.appendChild(foot);
    backdrop.appendChild(modal);

    backdrop.addEventListener("click", function (ev) {
      if (ev.target === backdrop) backdrop.remove();
    });
    document.addEventListener("keydown", function escHandler(ev) {
      if (ev.key === "Escape") {
        backdrop.remove();
        document.removeEventListener("keydown", escHandler);
      }
    });

    document.body.appendChild(backdrop);
    return backdrop;
  }

  // -------- film tables actions ---------------------------------------

  async function uploadFiles(files) {
    if (!files || files.length === 0) return;
    var fd = new FormData();
    for (var i = 0; i < files.length; i++) fd.append("file", files[i]);
    try {
      var data = await jsonFetch("/api/film-tables", { method: "POST", body: fd });
      var added = (data.added || []).length;
      var errs = (data.errors || []).length;
      if (added) toast("Added " + added + " profile" + (added === 1 ? "" : "s"), "ok");
      (data.errors || []).forEach(function (e) {
        toast(e.filename + ": " + e.error, "err");
      });
      if (added) location.reload();
    } catch (err) {
      toast("Upload failed: " + err.message, "err");
    }
  }

  async function deleteProfile(profileId) {
    var row = document.querySelector('[data-profile-id="' + profileId + '"]');
    var nameEl = row ? row.querySelector(".film-table-row-name") : null;
    var name = nameEl ? nameEl.textContent.trim() : "this film table";
    var ok = await confirmDialog({
      title: "Delete film table?",
      messageNodes: buildNodes([
        "Delete ", strong(name), " from your film tables? Existing rolls keep their own snapshot.",
      ]),
      confirmLabel: "Delete film table",
      danger: true,
    });
    if (!ok) return;
    try {
      var res = await fetch("/api/film-tables/" + profileId, { method: "DELETE" });
      if (!res.ok) throw new Error(res.statusText);
      toast("Deleted", "ok");
      // On the detail page there's no row to remove — back to the listing.
      if (document.body.dataset.view === "film-tables" && /\/film-tables\//.test(location.pathname)) {
        location.href = "/film-tables";
        return;
      }
      var rowAfter = document.querySelector('[data-profile-id="' + profileId + '"]');
      if (rowAfter) rowAfter.remove();
    } catch (err) {
      toast("Delete failed: " + err.message, "err");
    }
  }

  // -------- device scan -----------------------------------------------

  async function scanForDevices() {
    toast("Scanning for connected devices…");
    try {
      var data = await jsonFetch("/api/discover", { method: "POST" });
      var hits = data.hits || [];
      if (hits.length === 0) {
        toast("No ProPalette devices found", "warn");
        return;
      }
      renderScanResults(hits);
      toast("Found " + hits.length + " device" + (hits.length === 1 ? "" : "s"), "ok");
    } catch (err) {
      toast("Scan failed: " + err.message, "err");
    }
  }

  function renderScanResults(hits) {
    var panel = $("#scan-results-panel");
    var container = $("#scan-results");
    if (!panel || !container) {
      // Not on the Device page; build a modal instead.
      var list = document.createElement("div");
      list.className = "scan-results";
      hits.forEach(function (h) {
        list.appendChild(buildScanResult(h, function () { closeModalAndApply(h); }));
      });
      var backdrop;
      function closeModalAndApply(hit) {
        backdrop.remove();
        applyConnection(hit.target);
      }
      backdrop = openModal("Discovered devices", list);
      return;
    }
    container.innerHTML = "";
    hits.forEach(function (h) {
      container.appendChild(buildScanResult(h, function () {
        applyConnection(h.target);
      }));
    });
    panel.hidden = false;
  }

  function buildScanResult(hit, onClick) {
    var row = document.createElement("div");
    row.className = "scan-result";
    var transportLabel = hit.transport === "sgio" ? "/dev/sg* (SG_IO)" : "PiSCSI (s2pexec)";
    row.innerHTML =
      '<div class="scan-result-main">' +
        '<div class="scan-result-name"></div>' +
        '<div class="scan-result-meta"></div>' +
      "</div>" +
      '<button type="button" class="btn btn-primary btn-sm">Use</button>';
    row.querySelector(".scan-result-name").textContent =
      (hit.info && hit.info.product) ? hit.info.product : "ProPalette";
    row.querySelector(".scan-result-meta").textContent =
      transportLabel + "  ·  target " + hit.target +
      (hit.info ? "  ·  fw " + hit.info.firmware : "");
    row.addEventListener("click", onClick);
    return row;
  }

  async function applyConnection(target) {
    try {
      await jsonFetch("/api/config", {
        method: "POST",
        body: { mock_mode: false, target: target },
      });
      toast("Connected: " + target, "ok");
      await refreshTopbar();
      if (document.body.dataset.view === "device") location.reload();
    } catch (err) {
      toast("Failed to apply: " + err.message, "err");
    }
  }

  // -------- rolls -----------------------------------------------------

  function newRollDialog() {
    var tmpl = $("#tmpl-new-roll-form");
    if (!tmpl) {
      toast("Cannot create roll from this page", "warn");
      return;
    }
    var form = tmpl.content.firstElementChild.cloneNode(true);
    var select = form.querySelector('select[name="profile_id"]');
    var filterRow = form.querySelector('[data-bw-filter-row]');

    function syncFilter() {
      var opt = select && select.options[select.selectedIndex];
      var isBw = opt && opt.dataset.isBw === "true";
      if (!filterRow) return;
      filterRow.hidden = !isBw;
      if (isBw) {
        // Always reset to the FLM's recommended filter when switching tables.
        var recommended = opt.dataset.bwFilter || "1";
        var radio = form.querySelector('input[name="bw_filter"][value="' + recommended + '"]');
        if (radio) radio.checked = true;
      }
    }
    if (select) select.addEventListener("change", syncFilter);
    syncFilter();

    var save = document.createElement("button");
    save.type = "button";
    save.className = "btn btn-primary";
    save.textContent = "Create roll";
    save.addEventListener("click", async function () {
      var opt = select && select.options[select.selectedIndex];
      var isBw = opt && opt.dataset.isBw === "true";
      var body = {
        name: (form.querySelector('[name="name"]').value || "").trim(),
        profile_id: select && select.value,
      };
      if (!body.name || !body.profile_id) {
        toast("Fill in all fields", "warn");
        return;
      }
      if (isBw) {
        var checked = form.querySelector('input[name="bw_filter"]:checked');
        if (!checked) {
          toast("Pick a filter color", "warn");
          return;
        }
        body.bw_filter = parseInt(checked.value, 10);
      }
      try {
        var roll = await jsonFetch("/api/rolls", { method: "POST", body: body });
        backdrop.remove();
        toast("Roll created", "ok");
        location.href = "/rolls/" + roll.id;
      } catch (err) {
        toast("Failed: " + err.message, "err");
      }
    });

    var backdrop = openModal("New roll", form, save);
  }

  async function deleteRoll(rollId) {
    var titleEl = document.querySelector(".roll-title");
    var name = titleEl ? titleEl.textContent.trim() : "this roll";
    var ok = await confirmDialog({
      title: "Delete roll?",
      messageNodes: buildNodes([
        "Delete ", strong(name), "? All images, rendered outputs, and the snapshotted FLM will be removed permanently.",
      ]),
      confirmLabel: "Delete roll",
      danger: true,
    });
    if (!ok) return;
    try {
      var res = await fetch("/api/rolls/" + rollId, { method: "DELETE" });
      if (!res.ok) throw new Error(res.statusText);
      toast("Roll deleted", "ok");
      location.href = "/rolls";
    } catch (err) {
      toast("Delete failed: " + err.message, "err");
    }
  }

  async function renameRoll(rollId, newName) {
    if (!newName || !newName.trim()) return;
    try {
      await jsonFetch("/api/rolls/" + rollId, {
        method: "PATCH",
        body: { name: newName.trim() },
      });
      toast("Renamed", "ok");
    } catch (err) {
      toast("Rename failed: " + err.message, "err");
    }
  }

  async function updateRollOpt(rollId, key, value) {
    var body = {};
    body[key] = value;
    try {
      await jsonFetch("/api/rolls/" + rollId, { method: "PATCH", body: body });
    } catch (err) {
      toast("Save failed: " + err.message, "err");
    }
  }

  async function uploadFramesToRoll(rollId, files) {
    // Snapshot the live FileList — the caller may clear input.value
    // (or drop's DataTransfer may get invalidated) while we await
    // between iterations.
    files = Array.from(files || []);
    if (files.length === 0) return;
    var total = files.length;
    var cancelled = false;
    var added = 0;
    var errors = [];

    // Build the progress modal body
    var wrap = document.createElement("div");
    wrap.className = "progress-modal";
    wrap.innerHTML =
      '<div class="progress-count"><span class="progress-current">0 of ' + total + '</span><span class="progress-pct">0%</span></div>' +
      '<div class="progress-file">Preparing…</div>' +
      '<div class="progress-bar"><div class="progress-bar-fill"></div></div>' +
      '<div class="progress-errors"></div>';

    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn-ghost";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", function () {
      cancelled = true;
      cancelBtn.disabled = true;
      cancelBtn.textContent = "Cancelling…";
    });

    var backdrop = openModalNoClose("Uploading images", wrap, cancelBtn);

    var currentEl = wrap.querySelector(".progress-current");
    var pctEl = wrap.querySelector(".progress-pct");
    var fileEl = wrap.querySelector(".progress-file");
    var fillEl = wrap.querySelector(".progress-bar-fill");
    var errEl = wrap.querySelector(".progress-errors");

    for (var i = 0; i < total; i++) {
      if (cancelled) break;
      var file = files[i];
      currentEl.textContent = (i + 1) + " of " + total;
      fileEl.textContent = file.name;

      var fd = new FormData();
      fd.append("file", file);
      try {
        var data = await jsonFetch("/api/rolls/" + rollId + "/images", { method: "POST", body: fd });
        if (data.added && data.added.length) {
          added += data.added.length;
          for (var j = 0; j < data.added.length; j++) {
            await appendFrameCard(rollId, data.added[j].id);
          }
        }
        if (data.errors && data.errors.length) {
          data.errors.forEach(function (e) {
            errors.push(e);
            var line = document.createElement("div");
            line.textContent = e.filename + ": " + e.error;
            errEl.appendChild(line);
          });
        }
      } catch (err) {
        errors.push({ filename: file.name, error: err.message });
        var line = document.createElement("div");
        line.textContent = file.name + ": " + err.message;
        errEl.appendChild(line);
      }

      var done = i + 1;
      var pct = Math.round((done / total) * 100);
      pctEl.textContent = pct + "%";
      fillEl.style.width = pct + "%";
    }

    backdrop.remove();

    if (cancelled) {
      toast("Cancelled — added " + added + " of " + total, "warn");
    } else if (added && !errors.length) {
      toast("Added " + added + " frame" + (added === 1 ? "" : "s"), "ok");
    } else if (added) {
      toast("Added " + added + ", " + errors.length + " failed", "warn");
    } else if (errors.length) {
      toast("All uploads failed", "err");
    }
  }

  async function appendFrameCard(rollId, frameId) {
    try {
      var html = await htmlFetch("/partials/roll/" + rollId + "/frame/" + frameId);
      var grid = $("#frame-grid");
      if (!grid) return;
      // Drop the empty-state placeholder on first frame
      var empty = grid.querySelector(".empty-state");
      if (empty) empty.remove();
      var holder = document.createElement("div");
      holder.innerHTML = html.trim();
      var card = holder.firstElementChild;
      if (card) {
        grid.appendChild(card);
        // New pending frame — update the Start button's enabled state.
        syncStartButton(rollId);
      }
    } catch (err) {
      console.warn("partial fetch failed", err);
    }
  }

  function openModalNoClose(title, bodyNode, footNode) {
    // Like openModal but without the auto Cancel button or backdrop/Esc close.
    var backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    var modal = document.createElement("div");
    modal.className = "modal";
    var head = document.createElement("div");
    head.className = "modal-head";
    var h = document.createElement("h3");
    h.className = "modal-title";
    h.textContent = title;
    head.appendChild(h);
    var body = document.createElement("div");
    body.className = "modal-body";
    if (bodyNode) body.appendChild(bodyNode);
    var foot = document.createElement("div");
    foot.className = "modal-foot";
    if (footNode) foot.appendChild(footNode);
    modal.appendChild(head);
    modal.appendChild(body);
    modal.appendChild(foot);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    return backdrop;
  }

  var TRANSFORM_LABELS = { fit: "Fit", fill: "Fill", "1to1": "1:1" };

  async function updateFrame(rollId, frameId, changes) {
    var card = document.querySelector('.frame-card[data-frame-id="' + frameId + '"]');
    if (card) card.classList.add("is-busy");
    try {
      var frame = await jsonFetch("/api/rolls/" + rollId + "/frames/" + frameId, {
        method: "PATCH",
        body: changes,
      });
      // Refresh thumb (its cache key uses exposure_count; we bump via cache-bust)
      if (card) {
        card.dataset.rotation = frame.rotation;
        var img = card.querySelector(".frame-thumb img");
        if (img) img.src = "/rolls/" + rollId + "/thumb/" + frameId + "?v=" + Date.now();
        var meta = card.querySelector(".frame-meta");
        if (meta) {
          meta.innerHTML = "";
          var parts = [
            frame.resolution,
            TRANSFORM_LABELS[frame.transform] || frame.transform,
          ];
          if (frame.rotation) parts.push(frame.rotation + "°");
          parts.forEach(function (p, i) {
            if (i > 0) {
              var sep = document.createElement("span");
              sep.className = "dot-sep";
              sep.textContent = "·";
              meta.appendChild(sep);
            }
            var span = document.createElement("span");
            span.textContent = p;
            meta.appendChild(span);
          });
        }
        renderFrameWarning(card, frame.transform_warning);
      }
    } catch (err) {
      toast("Save failed: " + err.message, "err");
    } finally {
      if (card) card.classList.remove("is-busy");
    }
  }

  function renderFrameWarning(card, message) {
    var body = card.querySelector(".frame-card-body");
    if (!body) return;
    var warn = card.querySelector(".frame-warning");
    if (!message) {
      if (warn) warn.remove();
      return;
    }
    if (!warn) {
      warn = document.createElement("div");
      warn.className = "frame-warning";
      warn.innerHTML =
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
          '<path d="M8 2 L14.5 13 L1.5 13 Z"/>' +
          '<path d="M8 6.5v3"/>' +
          '<circle cx="8" cy="11.3" r="0.6" fill="currentColor" stroke="none"/>' +
        '</svg>' +
        '<span></span>';
      // Insert just after .frame-meta (or after .frame-src as a fallback).
      var meta = body.querySelector(".frame-meta");
      var anchor = meta || body.querySelector(".frame-src");
      if (anchor && anchor.nextSibling) body.insertBefore(warn, anchor.nextSibling);
      else body.appendChild(warn);
    }
    warn.title = message;
    warn.querySelector("span").textContent = message;
  }

  async function deleteFrame(rollId, frameId) {
    var card = document.querySelector('.frame-card[data-frame-id="' + frameId + '"]');
    var nameEl = card ? card.querySelector(".frame-name") : null;
    var name = nameEl ? nameEl.textContent.trim() : "this frame";
    var ok = await confirmDialog({
      title: "Remove frame?",
      messageNodes: buildNodes([
        "Remove ", strong(name), " from the roll? Its source image and rendered output will be deleted.",
      ]),
      confirmLabel: "Remove frame",
      danger: true,
    });
    if (!ok) return;
    try {
      var res = await fetch("/api/rolls/" + rollId + "/frames/" + frameId, { method: "DELETE" });
      if (!res.ok) throw new Error(res.statusText);
      var card = document.querySelector('.frame-card[data-frame-id="' + frameId + '"]');
      if (card) card.remove();
      renumberFrames();
      toast("Frame removed", "ok");
    } catch (err) {
      toast("Delete failed: " + err.message, "err");
    }
  }

  function renumberFrames() {
    $$(".frame-card .frame-order").forEach(function (el, i) {
      el.textContent = (i + 1 < 10 ? "0" : "") + (i + 1);
    });
  }

  async function reorderFrames(rollId) {
    var order = $$(".frame-card").map(function (c) { return c.dataset.frameId; });
    try {
      await jsonFetch("/api/rolls/" + rollId + "/reorder", {
        method: "POST",
        body: { frame_ids: order },
      });
      renumberFrames();
    } catch (err) {
      toast("Reorder failed: " + err.message, "err");
      location.reload();
    }
  }

  // -------- drag & drop for frame reorder -----------------------------

  function bindFrameDragReorder() {
    var grid = $("#frame-grid");
    if (!grid) return;
    var dragEl = null;

    grid.addEventListener("dragstart", function (ev) {
      var card = ev.target.closest(".frame-card");
      if (!card) return;
      dragEl = card;
      card.classList.add("is-dragging");
      ev.dataTransfer.effectAllowed = "move";
      // Firefox needs setData to start dragging
      try { ev.dataTransfer.setData("text/plain", card.dataset.frameId); } catch (e) {}
    });

    grid.addEventListener("dragend", function () {
      if (dragEl) dragEl.classList.remove("is-dragging");
      $$(".frame-card.is-drop-target").forEach(function (c) { c.classList.remove("is-drop-target"); });
      dragEl = null;
    });

    grid.addEventListener("dragover", function (ev) {
      if (!dragEl) return;
      var target = ev.target.closest(".frame-card");
      if (!target || target === dragEl) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      $$(".frame-card.is-drop-target").forEach(function (c) { c.classList.remove("is-drop-target"); });
      target.classList.add("is-drop-target");
    });

    grid.addEventListener("drop", function (ev) {
      if (!dragEl) return;
      var target = ev.target.closest(".frame-card");
      if (!target || target === dragEl) return;
      ev.preventDefault();
      var rect = target.getBoundingClientRect();
      var before = ev.clientY < rect.top + rect.height / 2;
      target.parentNode.insertBefore(dragEl, before ? target : target.nextSibling);
      target.classList.remove("is-drop-target");
      var rollId = dragEl.dataset.rollId;
      reorderFrames(rollId);
    });
  }

  // -------- event delegation ------------------------------------------

  document.addEventListener("click", function (ev) {
    var target = ev.target.closest("[data-action]");
    if (!target) return;
    var action = target.dataset.action;

    if (action === "scan") {
      ev.preventDefault();
      scanForDevices();
    } else if (action === "refresh-status") {
      ev.preventDefault();
      refreshTopbar();
    } else if (action === "delete-profile") {
      ev.preventDefault();
      deleteProfile(target.dataset.profileId);
    } else if (action === "new-roll") {
      ev.preventDefault();
      newRollDialog();
    } else if (action === "delete-roll") {
      ev.preventDefault();
      var panel = target.closest("[data-roll-id]");
      if (panel) deleteRoll(panel.dataset.rollId);
    } else if (action === "edit-name") {
      ev.preventDefault();
      var panel2 = target.closest("[data-roll-id]");
      if (!panel2) return;
      var current = target.textContent.trim();
      var next = prompt("Roll name:", current);
      if (next !== null && next.trim() && next.trim() !== current) {
        target.textContent = next.trim();
        renameRoll(panel2.dataset.rollId, next.trim());
      }
    } else if (action === "rotate-frame") {
      ev.preventDefault();
      var card = target.closest(".frame-card");
      if (!card) return;
      var rot = parseInt(card.dataset.rotation || "0", 10);
      var next = (rot + 90) % 360;
      card.dataset.rotation = next;
      updateFrame(card.dataset.rollId, card.dataset.frameId, { rotation: next });
    } else if (action === "delete-frame") {
      ev.preventDefault();
      var card2 = target.closest(".frame-card");
      if (card2) deleteFrame(card2.dataset.rollId, card2.dataset.frameId);
    } else if (action === "expose-frame") {
      ev.preventDefault();
      var card3 = target.closest(".frame-card");
      if (card3) exposeFrame(card3.dataset.rollId, card3.dataset.frameId);
    } else if (action === "toggle-skip") {
      ev.preventDefault();
      var card4 = target.closest(".frame-card");
      if (card4) toggleSkipFrame(card4.dataset.rollId, card4.dataset.frameId);
    } else if (action === "reset-frame") {
      ev.preventDefault();
      var card5 = target.closest(".frame-card");
      if (card5) resetFrame(card5.dataset.rollId, card5.dataset.frameId);
    } else if (action === "start-roll") {
      ev.preventDefault();
      // Recompute from the live DOM so skip toggles are reflected.
      var pending = $$('.frame-card[data-frame-status="pending"]').length;
      startRoll(target.dataset.rollId, pending);
    } else if (action === "stop-roll") {
      ev.preventDefault();
      stopRoll(target.dataset.rollId);
    } else if (action === "reset-done") {
      ev.preventDefault();
      resetDoneFrames(target.dataset.rollId,
                      parseInt(target.dataset.doneCount, 10) || 0);
    } else if (action === "update-check") {
      ev.preventDefault();
      checkForUpdates(target);
    } else if (action === "update-apply") {
      ev.preventDefault();
      applyUpdate(target.dataset.target);
    } else if (action === "cal-create-roll") {
      ev.preventDefault();
      calCreateRoll(target.dataset.profileId);
    } else if (action === "cal-start-exposure") {
      ev.preventDefault();
      var pStart = target.closest("[data-calibration-panel]");
      if (pStart) calStartExposure(pStart);
    } else if (action === "cal-open-progress") {
      ev.preventDefault();
      var pProg = target.closest("[data-calibration-panel]");
      if (pProg) calOpenProgress(pProg);
    } else if (action === "cal-analyze") {
      ev.preventDefault();
      var pAna = target.closest("[data-calibration-panel]");
      if (pAna) calAnalyze(pAna);
    } else if (action === "cal-prefill") {
      ev.preventDefault();
      var pPre = target.closest("[data-calibration-panel]");
      if (pPre) calPrefill(pPre);
    } else if (action === "cal-apply") {
      ev.preventDefault();
      var pApp = target.closest("[data-calibration-panel]");
      if (pApp) calApply(pApp);
    } else if (action === "cal-cancel") {
      ev.preventDefault();
      var pCan = target.closest("[data-calibration-panel]");
      if (pCan) calCancel(pCan);
    }
  });

  // -------- calibration (v2: dual-resolution, FLM-page driven) -------

  async function calCreateRoll(profileId) {
    try {
      await jsonFetch(
        "/api/film-tables/" + encodeURIComponent(profileId) + "/calibrate",
        { method: "POST" },
      );
      toast("Calibration roll prepared", "ok");
      location.reload();
    } catch (err) {
      toast("Couldn't start calibration: " + err.message, "err");
    }
  }

  async function calStartExposure(panel) {
    var rollId = panel.dataset.rollId;
    try {
      await jsonFetch("/api/rolls/" + rollId + "/start", { method: "POST" });
      calOpenProgress(panel);
    } catch (err) {
      toast("Couldn't start exposure: " + err.message, "err");
    }
  }

  function calOpenProgress(panel) {
    // Minimal progress modal that subscribes to the runner SSE stream
    // and updates per-frame status.  Reuses the existing event source.
    var rollId = panel.dataset.rollId;
    var backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML =
      '<div class="modal cal-progress-modal">' +
        '<div class="modal-head">' +
          '<h3 class="modal-title">Exposing calibration roll</h3>' +
          '<p class="modal-sub">Keep the device powered until all 33 frames are exposed.</p>' +
        '</div>' +
        '<div class="modal-body">' +
          '<div class="cal-progress" data-cal-progress>' +
            '<div class="cal-progress-bar"><div class="cal-progress-fill" style="width:0%"></div></div>' +
            '<div class="cal-progress-text" data-cal-progress-text>Waiting…</div>' +
          '</div>' +
        '</div>' +
        '<div class="modal-foot">' +
          '<button type="button" class="btn btn-ghost" data-cal-progress-close>Close (exposure continues)</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(backdrop);

    var fill = backdrop.querySelector(".cal-progress-fill");
    var textEl = backdrop.querySelector("[data-cal-progress-text]");

    function update(done, total, msg) {
      var pct = total > 0 ? Math.round(done / total * 100) : 0;
      fill.style.width = pct + "%";
      textEl.textContent = (msg || ("Frame " + done + " / " + total)) + "  (" + pct + "%)";
    }

    // Pull initial state.
    fetch("/api/rolls/" + rollId).then(function (r) { return r.json(); }).catch(function () { return null; });

    // Subscribe to SSE.  Re-use the global runner stream; filter on roll_id.
    var es = new EventSource("/api/runner/events");
    var doneFrames = 0;
    var totalFrames = 33;
    es.addEventListener("state", function (e) {
      try {
        var s = JSON.parse(e.data);
        if (s.roll_id === rollId) {
          totalFrames = s.total || 33;
          doneFrames = s.completed || 0;
          update(doneFrames, totalFrames);
        }
      } catch (_) {}
    });
    es.addEventListener("frame_status", function (e) {
      try {
        var s = JSON.parse(e.data);
        if (s.roll_id !== rollId) return;
        if (s.status === "done" || s.status === "skipped" || s.status === "failed") {
          doneFrames++;
          update(doneFrames, totalFrames);
          if (doneFrames >= totalFrames) {
            es.close();
            setTimeout(function () { location.reload(); }, 500);
          }
        } else if (s.status === "exposing") {
          update(doneFrames, totalFrames, "Exposing frame " + (doneFrames + 1) + " / " + totalFrames);
        }
      } catch (_) {}
    });

    function close() {
      es.close();
      backdrop.remove();
    }
    backdrop.querySelector("[data-cal-progress-close]").addEventListener("click", close);
    backdrop.addEventListener("click", function (ev) {
      if (ev.target === backdrop) close();
    });
  }

  function calGatherMeasurements(panel) {
    var inputs = panel.querySelectorAll("input[data-cal-resolution]");
    var by_res = { "4k": [], "8k": [] };
    var missing = 0;
    inputs.forEach(function (inp) {
      var v = inp.value.trim();
      if (v === "") { missing++; return; }
      var d = parseFloat(v);
      if (isNaN(d)) { missing++; return; }
      by_res[inp.dataset.calResolution].push({
        pixel: parseInt(inp.dataset.calPixel, 10),
        density: d,
      });
    });
    return { by_res: by_res, missing: missing, total: inputs.length };
  }

  function calPrefill(panel) {
    // b+f = 0.20; total swing = 1.15.  Same target for 4K and 8K --
    // calibration should make them produce the same density at the same
    // input pixel.
    panel.querySelectorAll("input[data-cal-resolution]").forEach(function (inp) {
      var px = parseInt(inp.dataset.calPixel, 10);
      inp.value = (0.20 + (px / 255) * 1.15).toFixed(2);
    });
  }

  async function calAnalyze(panel) {
    var rollId = panel.dataset.rollId;
    var g = calGatherMeasurements(panel);
    if (g.missing > 0) {
      toast("Fill in all " + g.total + " densities", "warn");
      return;
    }
    var resultEl = panel.querySelector("[data-cal-result]");
    resultEl.hidden = false;
    resultEl.innerHTML = "<p>Analysing…</p>";
    try {
      var data = await jsonFetch(
        "/api/calibration/" + rollId + "/measurements",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            measurements_4k: g.by_res["4k"],
            measurements_8k: g.by_res["8k"],
          }),
        },
      );
      renderCalDiagnostic(resultEl, data);
    } catch (err) {
      resultEl.innerHTML = '<p class="cal-err">' + err.message + '</p>';
    }
  }

  function renderCalDiagnostic(el, data) {
    function block(title, diag) {
      if (!diag) return "";
      var rows = [
        ["b+f", diag.b_plus_f.toFixed(3)],
        ["D_max", diag.d_max.toFixed(3)],
        ["Working range", diag.working_range.toFixed(3)],
        ["Target", diag.target_range.toFixed(3)],
        ["Shortfall", diag.shortfall.toFixed(3)],
        ["Max step error", diag.max_step_error.toFixed(3)],
        ["Speed point", diag.speed_point_pixel != null ? "px " + diag.speed_point_pixel.toFixed(1) : "—"],
      ];
      if (diag.time_multiplier != null) rows.push(["Dev time ×", diag.time_multiplier.toFixed(2)]);
      if (diag.ei_multiplier != null)   rows.push(["EI ×",        diag.ei_multiplier.toFixed(2)]);
      var rowsHtml = rows.map(function (r) {
        return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>";
      }).join("");
      return (
        '<div class="cal-block-result">' +
          '<h4 class="cal-block-result-title">' + title +
            ' <span class="cal-verdict cal-verdict-' + diag.verdict + '">' +
              diag.verdict.replace(/_/g, " ") +
            '</span>' +
          '</h4>' +
          '<dl class="cal-stats">' + rowsHtml + '</dl>' +
        '</div>'
      );
    }
    var any = data.diagnostic_4k || data.diagnostic_8k;
    var html =
      '<div class="cal-blocks">' +
        block("4K (Master A)", data.diagnostic_4k) +
        block("8K (Master B)", data.diagnostic_8k) +
      '</div>';
    var canApply = any && (
      [data.diagnostic_4k, data.diagnostic_8k].every(function (d) {
        return !d || d.verdict === "ok" || d.verdict === "lut_fixable" ||
               d.verdict === "global_underexposure";
      })
    );
    html += canApply
      ? '<div class="cal-actions"><button type="button" class="btn btn-primary" data-action="cal-apply">Apply &amp; save new FLM</button></div>'
      : '<p class="cal-hint">Resolve the chemistry issue above (longer dev / EI bump) and re-measure before applying.</p>';
    el.innerHTML = html;
  }

  async function calApply(panel) {
    var rollId = panel.dataset.rollId;
    try {
      var data = await jsonFetch(
        "/api/calibration/" + rollId + "/apply",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        },
      );
      toast("New FLM saved: " + data.id, "ok");
      location.href = "/film-tables/" + encodeURIComponent(data.id);
    } catch (err) {
      toast("Apply failed: " + err.message, "err");
    }
  }

  async function calCancel(panel) {
    var rollId = panel.dataset.rollId;
    var ok = await confirmDialog({
      title: "Cancel calibration?",
      message: "The calibration roll and any measurements you've entered will be discarded.",
      confirmLabel: "Discard calibration",
      danger: true,
    });
    if (!ok) return;
    try {
      var res = await fetch("/api/rolls/" + rollId, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(res.statusText);
      toast("Calibration cancelled", "ok");
      location.reload();
    } catch (err) {
      toast("Couldn't cancel: " + err.message, "err");
    }
  }

  // -------- reset frame ----------------------------------------------

  async function resetFrame(rollId, frameId) {
    try {
      await jsonFetch(
        "/api/rolls/" + rollId + "/frames/" + frameId + "/reset",
        { method: "POST" },
      );
      await refreshFrameCard(rollId, frameId);
    } catch (err) {
      toast("Couldn't reset frame: " + err.message, "err");
    }
  }

  // -------- skip toggle ----------------------------------------------

  async function toggleSkipFrame(rollId, frameId) {
    try {
      await jsonFetch(
        "/api/rolls/" + rollId + "/frames/" + frameId + "/skip-toggle",
        { method: "POST" },
      );
      // SSE will also fire frame_status, but refresh directly so a user
      // without an open EventSource (unlikely) still sees the change.
      await refreshFrameCard(rollId, frameId);
    } catch (err) {
      toast("Couldn't toggle skip: " + err.message, "err");
    }
  }

  // -------- exposure --------------------------------------------------

  async function exposeFrame(rollId, frameId) {
    var card = document.querySelector('.frame-card[data-frame-id="' + frameId + '"]');
    var statusText = card ? (card.dataset.frameStatus || "") : "";
    // The PP8K can't detect whether film is loaded — every Expose click
    // is a confirm step so we don't burn empty cartridges or stack
    // re-exposures by accident.
    var dialog;
    if (statusText === "done") {
      dialog = {
        title: "Re-expose frame?",
        messageNodes: buildNodes([
          "This frame is already marked exposed. Re-exposing burns the next frame of film. ",
          strong("Confirm film is loaded and ready."),
        ]),
        confirmLabel: "Re-expose",
      };
    } else if (statusText === "failed") {
      dialog = {
        title: "Retry exposure?",
        messageNodes: buildNodes([
          "Try this exposure again — the previous error will be cleared. ",
          strong("Confirm film is loaded and ready."),
        ]),
        confirmLabel: "Retry",
      };
    } else {
      dialog = {
        title: "Expose frame?",
        messageNodes: buildNodes([
          "This will print the rendered image onto the next frame of film. ",
          strong("Confirm film is loaded and ready."),
        ]),
        confirmLabel: "Expose",
      };
    }
    var ok = await confirmDialog(dialog);
    if (!ok) return;
    try {
      await jsonFetch("/api/rolls/" + rollId + "/frames/" + frameId + "/expose", {
        method: "POST",
      });
      await refreshFrameCard(rollId, frameId);
      // SSE will drive progress + the final card refresh on settle.
    } catch (err) {
      toast("Expose failed: " + err.message, "err");
    }
  }

  async function refreshFrameCard(rollId, frameId) {
    try {
      var html = await htmlFetch("/partials/roll/" + rollId + "/frame/" + frameId);
      var card = document.querySelector('.frame-card[data-frame-id="' + frameId + '"]');
      if (!card) return null;
      var holder = document.createElement("div");
      holder.innerHTML = html.trim();
      var next = holder.firstElementChild;
      if (next) {
        card.replaceWith(next);
        syncStartButton(rollId);
        return next;
      }
    } catch (err) {
      console.warn("frame partial fetch failed", err);
    }
    return null;
  }

  function syncStartButton(rollId) {
    var btn = document.querySelector('[data-action="start-roll"][data-roll-id="' + rollId + '"]');
    if (btn) {
      var pending = $$('.frame-card[data-frame-status="pending"]').length;
      btn.disabled = pending === 0;
      btn.dataset.pendingCount = String(pending);
    }
    syncResetDoneButton(rollId);
  }

  function syncResetDoneButton(rollId) {
    var btn = document.querySelector('[data-action="reset-done"][data-roll-id="' + rollId + '"]');
    if (!btn) return;
    var done = $$('.frame-card[data-frame-status="done"]').length;
    btn.hidden = done === 0;
    btn.dataset.doneCount = String(done);
  }

  // On load: connect to SSE if there's a roll panel on the page. The
  // initial "state" event on connect tells us about any in-flight run.
  function resumeExposurePolling() {
    var panel = document.querySelector("[data-roll-id]");
    if (!panel) return;
    connectRunnerEvents(panel.dataset.rollId);
  }

  // -------- SSE: runner events ----------------------------------------

  var _eventSource = null;

  function connectRunnerEvents(rollId) {
    if (_eventSource) return;  // already connected
    var es = new EventSource("/api/runner/events");
    _eventSource = es;

    es.addEventListener("state", function (ev) {
      try {
        var st = JSON.parse(ev.data);
        applyRunnerState(rollId, st);
      } catch (e) { console.warn(e); }
    });

    es.addEventListener("progress", function (ev) {
      try {
        var p = JSON.parse(ev.data);
        if (p.roll_id !== rollId) return;
        renderFrameProgress(p);
        updateExposeModalProgress(p);
      } catch (e) { console.warn(e); }
    });

    es.addEventListener("frame_status", function (ev) {
      try {
        var s = JSON.parse(ev.data);
        if (s.roll_id !== rollId) return;
        if (s.status === "exposing") {
          showFrameProgressOverlay(s.frame_id);
          return;
        }
        // Any other transition (done/failed/skipped/pending) — refresh
        // the card so it picks up the new status, exposure_count,
        // last_error, etc. Modal tear-down also lives here in case the
        // state event arrives slightly later than the frame settle.
        refreshFrameCard(rollId, s.frame_id);
        if (s.status === "done" || s.status === "failed") hideExposeModal();
        if (s.status === "done") toast("Frame exposed", "ok");
        else if (s.status === "failed") toast("Exposure failed: " + (s.error || "unknown"), "err");
      } catch (e) { console.warn(e); }
    });

    es.onerror = function () {
      // EventSource retries automatically. Just log so we know.
      console.warn("SSE error — browser will retry");
    };
  }

  function applyRunnerState(pageRollId, st) {
    var isThisRoll = st.busy && st.roll_id === pageRollId;
    if (isThisRoll) {
      // Hide overlays on any card that isn't the active one.
      $$(".frame-card").forEach(function (card) {
        if (card.dataset.frameId !== st.frame_id) {
          hideFrameProgressOverlay(card.dataset.frameId);
        }
      });
      if (st.frame_id) showFrameProgressOverlay(st.frame_id);
      if (st.mode === "single" && st.frame_id) {
        // Single-frame is a deliberate one-off — block navigation with
        // a modal until the exposure settles. Roll runs intentionally
        // leave the UI navigable so the user can multitask.
        showExposeModal(st.frame_id);
      } else {
        hideExposeModal();
      }
      if (st.mode === "roll") {
        setRunButtons(pageRollId, st.stopping ? "stopping" : "running");
        var counts = countFrameStates();
        setRunStatus(pageRollId, {
          level: st.stopping ? "stopping" : "running",
          text: st.stopping ? "Stopping — finishing current frame" : "Exposing roll",
          progress: counts.done + " / " + counts.total + " done",
        });
      }
    } else if (!st.busy) {
      // Run ended for this page's roll (or none was ours).
      hideExposeModal();
      $$(".frame-card").forEach(function (card) {
        hideFrameProgressOverlay(card.dataset.frameId);
      });
      setRunButtons(pageRollId, "idle");
      // Defer counting until after pending refreshFrameCard fetches (kicked
      // off by frame_status events) have had a chance to land. Without this,
      // the last frame's card still reads "pending" and we mislabel a clean
      // run as "halted".
      var banner = document.querySelector("[data-run-status]");
      if (!banner || banner.hidden) return;
      setTimeout(function () {
        var counts2 = countFrameStates();
        if (counts2.remaining === 0 && counts2.total > 0) {
          setRunStatus(pageRollId, {
            level: "done",
            text: "Run complete — all frames exposed",
            progress: counts2.done + " / " + counts2.total + " done",
          });
        } else {
          setRunStatus(pageRollId, {
            level: null,
            text: "Run halted",
            progress: counts2.done + " / " + counts2.total + " done · " +
                      counts2.remaining + " remaining",
          });
        }
      }, 600);
    }
  }

  function countFrameStates() {
    var cards = $$(".frame-card");
    var done = 0, pending = 0;
    cards.forEach(function (c) {
      var s = c.dataset.frameStatus;
      if (s === "done") done++;
      else if (s === "pending" || s === "failed") pending++;
    });
    return { done: done, total: cards.length, remaining: pending };
  }

  function showFrameProgressOverlay(frameId) {
    var card = document.querySelector('.frame-card[data-frame-id="' + frameId + '"]');
    if (!card) return;
    var overlay = card.querySelector("[data-frame-progress]");
    if (!overlay) return;
    overlay.hidden = false;
    // Default to indeterminate until we receive a progress event with
    // lines_total — the calibration phase has no measurable progress.
    var bar = overlay.querySelector(".frame-progress-bar");
    if (bar && !bar.dataset.everDeterminate) bar.classList.add("is-indeterminate");
    if (!overlay.querySelector("[data-progress-phase]").textContent) {
      overlay.querySelector("[data-progress-phase]").textContent = "Starting";
    }
  }

  function hideFrameProgressOverlay(frameId) {
    var card = document.querySelector('.frame-card[data-frame-id="' + frameId + '"]');
    if (!card) return;
    var overlay = card.querySelector("[data-frame-progress]");
    if (overlay) overlay.hidden = true;
  }

  var PHASE_LABELS = {
    setup: "Setup",
    calibrating: "Calibrating",
    sending: "Exposing",
    finishing: "Finishing",
    complete: "Complete",
    aborted: "Aborted",
    error: "Error",
  };

  // -------- single-frame exposure modal ------------------------------

  var _exposeModal = null;

  function showExposeModal(frameId) {
    if (_exposeModal && _exposeModal.dataset.frameId === frameId) return;
    if (_exposeModal) hideExposeModal();

    var card = document.querySelector('.frame-card[data-frame-id="' + frameId + '"]');
    var orderEl = card ? card.querySelector(".frame-order") : null;
    var nameEl = card ? card.querySelector(".frame-name") : null;
    var orderText = orderEl ? orderEl.textContent.trim() : "";
    var nameText = nameEl ? nameEl.textContent.trim() : "";

    var backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.dataset.frameId = frameId;

    var modal = document.createElement("div");
    modal.className = "modal expose-modal";

    var head = document.createElement("div");
    head.className = "modal-head";
    var title = document.createElement("h3");
    title.className = "modal-title";
    title.textContent = "Exposing frame " + (orderText || "—");
    head.appendChild(title);

    var body = document.createElement("div");
    body.className = "modal-body expose-modal-body";
    body.innerHTML =
      '<div class="expose-modal-frame">' +
        (nameText ? '<strong></strong>' : '') +
      '</div>' +
      '<div class="expose-modal-phase">Starting</div>' +
      '<div class="expose-modal-bar is-indeterminate"><div class="expose-modal-fill"></div></div>' +
      '<div class="expose-modal-time"></div>' +
      '<div class="expose-modal-note">Don’t navigate away — the exposure is running on the device.</div>';
    if (nameText) {
      body.querySelector(".expose-modal-frame strong").textContent = nameText;
    } else {
      body.querySelector(".expose-modal-frame").remove();
    }

    modal.appendChild(head);
    modal.appendChild(body);
    backdrop.appendChild(modal);

    // Intentionally NO click-to-dismiss and NO Escape key handler:
    // single-frame exposure is uncancellable mid-burst, so the modal
    // stays put until the frame settles.
    document.body.appendChild(backdrop);
    _exposeModal = backdrop;
  }

  function hideExposeModal() {
    if (!_exposeModal) return;
    _exposeModal.remove();
    _exposeModal = null;
  }

  function updateExposeModalProgress(p) {
    if (!_exposeModal || _exposeModal.dataset.frameId !== p.frame_id) return;
    var phaseEl = _exposeModal.querySelector(".expose-modal-phase");
    var bar = _exposeModal.querySelector(".expose-modal-bar");
    var fill = _exposeModal.querySelector(".expose-modal-fill");
    var timeEl = _exposeModal.querySelector(".expose-modal-time");

    var phaseLabel = PHASE_LABELS[p.phase] || p.phase || "—";
    if (p.channel) phaseLabel += " · " + String(p.channel).toUpperCase();
    phaseEl.textContent = phaseLabel;

    if (p.lines_total > 0 && p.phase === "sending") {
      bar.classList.remove("is-indeterminate");
      var pct = Math.min(100, Math.max(0, (p.lines_sent / p.lines_total) * 100));
      fill.style.width = pct.toFixed(1) + "%";
    } else {
      bar.classList.add("is-indeterminate");
    }

    var bits = [];
    if (p.elapsed_seconds != null) bits.push(p.elapsed_seconds.toFixed(0) + "s elapsed");
    if (p.eta_seconds != null && p.eta_seconds > 0) bits.push("~" + p.eta_seconds.toFixed(0) + "s left");
    timeEl.textContent = bits.join(" · ");
  }

  function renderFrameProgress(p) {
    var card = document.querySelector('.frame-card[data-frame-id="' + p.frame_id + '"]');
    if (!card) return;
    var overlay = card.querySelector("[data-frame-progress]");
    if (!overlay) return;
    overlay.hidden = false;
    var phaseLabel = PHASE_LABELS[p.phase] || p.phase || "—";
    if (p.channel) phaseLabel += " · " + String(p.channel).toUpperCase();
    overlay.querySelector("[data-progress-phase]").textContent = phaseLabel;

    var bar = overlay.querySelector(".frame-progress-bar");
    var fill = overlay.querySelector("[data-progress-fill]");
    if (p.lines_total > 0 && p.phase === "sending") {
      bar.classList.remove("is-indeterminate");
      bar.dataset.everDeterminate = "1";
      var pct = Math.min(100, Math.max(0, (p.lines_sent / p.lines_total) * 100));
      fill.style.width = pct.toFixed(1) + "%";
    } else {
      bar.classList.add("is-indeterminate");
    }

    var t = overlay.querySelector("[data-progress-time]");
    var bits = [];
    if (p.elapsed_seconds != null) bits.push(p.elapsed_seconds.toFixed(0) + "s elapsed");
    if (p.eta_seconds != null && p.eta_seconds > 0) bits.push("~" + p.eta_seconds.toFixed(0) + "s left");
    t.textContent = bits.join(" · ");
  }

  // -------- roll-level Start/Stop ------------------------------------

  async function startRoll(rollId, pendingCount) {
    if (!pendingCount) {
      toast("No pending frames to expose", "warn");
      return;
    }
    var ok = await confirmDialog({
      title: "Start exposing?",
      messageNodes: buildNodes([
        "About to expose ", strong(pendingCount + " frame" + (pendingCount === 1 ? "" : "s")),
        " sequentially. Stop after current frame is always available. ",
        strong("Confirm film is loaded and ready."),
      ]),
      confirmLabel: "Start exposing",
    });
    if (!ok) return;
    try {
      await jsonFetch("/api/rolls/" + rollId + "/start", { method: "POST" });
      setRunButtons(rollId, "running");
      // SSE state event will drive the rest.
    } catch (err) {
      toast("Couldn't start: " + err.message, "err");
    }
  }

  async function resetDoneFrames(rollId, doneCount) {
    if (!doneCount) return;
    var ok = await confirmDialog({
      title: "Reset done frames?",
      messageNodes: buildNodes([
        "Re-queue ", strong(doneCount + " exposed frame" + (doneCount === 1 ? "" : "s")),
        " for the next roll run. The frames stay in the roll; only the ‘done’ flag is cleared so the runner picks them up again. Exposure history is preserved.",
      ]),
      confirmLabel: "Reset",
    });
    if (!ok) return;
    try {
      var res = await jsonFetch("/api/rolls/" + rollId + "/reset-done", {
        method: "POST",
      });
      toast("Reset " + res.reset + " frame" + (res.reset === 1 ? "" : "s") + " to pending", "ok");
      // SSE frame_status events fire for each reset frame and trigger
      // refreshFrameCard, which syncs the buttons. Nothing else to do here.
    } catch (err) {
      toast("Reset failed: " + err.message, "err");
    }
  }

  async function stopRoll(rollId) {
    var ok = await confirmDialog({
      title: "Stop after current frame?",
      messageNodes: buildNodes([
        "The current frame will finish (no mid-frame abort — that would waste film). After it lands, the run halts. ",
        strong("You can resume later from the next pending frame."),
      ]),
      confirmLabel: "Stop",
    });
    if (!ok) return;
    try {
      await jsonFetch("/api/rolls/" + rollId + "/stop", { method: "POST" });
      setRunButtons(rollId, "stopping");
    } catch (err) {
      toast("Couldn't stop: " + err.message, "err");
    }
  }

  function setRunButtons(rollId, phase) {
    // phase: idle | running | stopping
    var startBtn = document.querySelector('[data-action="start-roll"][data-roll-id="' + rollId + '"]');
    var stopBtn = document.querySelector('[data-action="stop-roll"][data-roll-id="' + rollId + '"]');
    if (!startBtn || !stopBtn) return;
    if (phase === "idle") {
      startBtn.hidden = false;
      stopBtn.hidden = true;
    } else if (phase === "running") {
      startBtn.hidden = true;
      stopBtn.hidden = false;
      stopBtn.disabled = false;
      stopBtn.querySelector("span").textContent = "Stop after current";
    } else if (phase === "stopping") {
      startBtn.hidden = true;
      stopBtn.hidden = false;
      stopBtn.disabled = true;
      stopBtn.querySelector("span").textContent = "Stopping…";
    }
  }

  function setRunStatus(rollId, opts) {
    // opts: { text, progress, level: 'running' | 'stopping' | 'done' | 'failed' | null }
    var el = document.querySelector("[data-run-status]");
    if (!el) return;
    if (!opts) { el.hidden = true; el.className = "run-status"; el.innerHTML = ""; return; }
    el.hidden = false;
    el.className = "run-status" + (opts.level ? " run-" + opts.level : "");
    el.innerHTML =
      '<span class="run-status-dot"></span>' +
      '<span class="run-status-text"></span>' +
      (opts.progress ? '<span class="run-status-progress"></span>' : '');
    el.querySelector(".run-status-text").textContent = opts.text || "";
    if (opts.progress) el.querySelector(".run-status-progress").textContent = opts.progress;
  }


  // -------- updates ---------------------------------------------------

  async function checkForUpdates(btn) {
    var panel = document.querySelector("[data-update-panel]");
    var resultEl = panel ? panel.querySelector("[data-update-result]") : null;
    if (!panel || !resultEl) return;
    btn.disabled = true;
    var origLabel = btn.querySelector("span").textContent;
    btn.querySelector("span").textContent = "Checking…";
    resultEl.hidden = false;
    resultEl.innerHTML = '<div class="update-status">Fetching from origin…</div>';
    try {
      var info = await jsonFetch("/api/update/check", { method: "POST" });
      renderUpdateInfo(resultEl, info);
    } catch (err) {
      resultEl.innerHTML = '<div class="update-status update-err">' +
        escapeHtml("Check failed: " + err.message) + '</div>';
    } finally {
      btn.disabled = false;
      btn.querySelector("span").textContent = origLabel;
    }
  }

  function renderUpdateInfo(el, info) {
    if (!info.latest) {
      el.innerHTML = '<div class="update-status">No version tags found on the remote yet.</div>';
      return;
    }
    if (info.on_latest) {
      el.innerHTML = '<div class="update-status update-ok">Up to date — running ' +
        escapeHtml(info.current) + '.</div>';
      return;
    }
    var notes = "";
    if (info.notes && info.notes.length) {
      notes = '<ul class="update-notes">';
      info.notes.forEach(function (n) {
        notes += '<li><code>' + escapeHtml(n.commit) + '</code> ' +
                 escapeHtml(n.subject) + '</li>';
      });
      notes += '</ul>';
    }
    el.innerHTML =
      '<div class="update-status update-warn">Update available: <strong>' +
        escapeHtml(info.latest) + '</strong> (currently ' + escapeHtml(info.current) + ').</div>' +
      notes +
      '<div class="update-actions"><button type="button" class="btn btn-primary" ' +
        'data-action="update-apply" data-target="' + escapeAttr(info.latest) + '">' +
        'Update now</button></div>';
  }

  async function applyUpdate(target) {
    var ok = await confirmDialog({
      title: "Apply update?",
      messageNodes: buildNodes([
        "piPalette will check out ", strong(target),
        " and restart. The page will reconnect automatically — exposures should not be running.",
      ]),
      confirmLabel: "Update now",
    });
    if (!ok) return;

    var panel = document.querySelector("[data-update-panel]");
    var resultEl = panel ? panel.querySelector("[data-update-result]") : null;
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.innerHTML = '<div class="update-status">Starting update…</div>';
    }

    try {
      await jsonFetch("/api/update/apply", {
        method: "POST",
        body: { target: target },
      });
    } catch (err) {
      if (resultEl) {
        resultEl.innerHTML = '<div class="update-status update-err">' +
          escapeHtml("Failed to start: " + err.message) + '</div>';
      }
      return;
    }
    pollForUpdateCompletion(target, resultEl);
  }

  function pollForUpdateCompletion(target, resultEl) {
    // The service restarts during the update — fetches will fail for a few
    // seconds. Keep trying for ~2 min, then give up and surface a message.
    var started = Date.now();
    var timeoutMs = 120000;
    function tick() {
      if (Date.now() - started > timeoutMs) {
        if (resultEl) {
          resultEl.innerHTML = '<div class="update-status update-err">' +
            'Timed out waiting for the service to come back up. ' +
            'Try <code>sudo journalctl -u pipalette-update -e</code> on the host.' +
            '</div>';
        }
        return;
      }
      fetch("/api/version", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.statusText); })
        .then(function (v) {
          var status = v.status || "starting";
          if (resultEl) {
            resultEl.innerHTML = '<div class="update-status">' +
              escapeHtml("Status: " + status) + '</div>';
          }
          if (/^error:/i.test(status)) {
            if (resultEl) {
              resultEl.innerHTML = '<div class="update-status update-err">' +
                escapeHtml(status) + '</div>';
            }
            return;
          }
          // Match against bare version (strip any "-N-gSHA").
          var bare = (v.version || "").split("-")[0];
          if (bare === target) {
            if (resultEl) {
              resultEl.innerHTML = '<div class="update-status update-ok">' +
                'Updated to ' + escapeHtml(v.version) + '. Reloading…</div>';
            }
            setTimeout(function () { location.reload(); }, 1200);
            return;
          }
          setTimeout(tick, 2000);
        })
        .catch(function () { setTimeout(tick, 2000); });
    }
    setTimeout(tick, 2000);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function escapeAttr(s) { return escapeHtml(s); }

  // Frame card segmented controls + roll options
  document.addEventListener("change", function (ev) {
    var rollOpt = ev.target.closest("[data-roll-opt], [data-roll-opt-seg]");
    if (rollOpt) {
      var panel = rollOpt.closest("[data-roll-id]");
      if (!panel) return;
      var key, value;
      if (rollOpt.dataset.rollOptSeg) {
        key = rollOpt.dataset.rollOptSeg;
        value = ev.target.value;
      } else {
        key = rollOpt.dataset.rollOpt;
        value = rollOpt.type === "checkbox" ? rollOpt.checked : rollOpt.value;
      }
      updateRollOpt(panel.dataset.rollId, key, value);
      return;
    }

    var frameSeg = ev.target.closest("[data-frame-opt-seg]");
    if (frameSeg) {
      var card = frameSeg.closest(".frame-card");
      if (!card) return;
      var changes = {};
      changes[frameSeg.dataset.frameOptSeg] = ev.target.value;
      updateFrame(card.dataset.rollId, card.dataset.frameId, changes);
    }
  });

  // -------- config form (auto-apply on change) ------------------------

  function submitConfigForm(form) {
    var data = {};
    new FormData(form).forEach(function (value, key) { data[key] = value; });
    return jsonFetch("/api/config", { method: "POST", body: data })
      .then(function () {
        toast("Settings saved", "ok");
        setTimeout(function () { location.reload(); }, 300);
      })
      .catch(function (err) { toast("Save failed: " + err.message, "err"); });
  }

  function bindConfigForm() {
    var form = $("[data-config-form]");
    if (!form) return;

    // Radios (Mode) save immediately on change.
    form.querySelectorAll('input[type="radio"]').forEach(function (radio) {
      radio.addEventListener("change", function () { submitConfigForm(form); });
    });

    // Text inputs commit on blur (if changed) or on Enter.
    form.querySelectorAll('input[type="text"], input:not([type])').forEach(function (input) {
      var pristine = input.value;
      input.addEventListener("focus", function () { pristine = input.value; });
      input.addEventListener("blur", function () {
        if (input.value !== pristine) submitConfigForm(form);
      });
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          input.blur();
        }
      });
    });
  }

  // -------- drag & drop ----------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    var dz = document.querySelector("[data-dropzone]");
    if (dz) {
      // On a roll detail page, uploads go to the roll; elsewhere they go to the film-table store.
      var rollPanel = document.querySelector("[data-roll-id]");
      var rollId = rollPanel ? rollPanel.dataset.rollId : null;
      var doUpload = rollId
        ? function (files) { uploadFramesToRoll(rollId, files); }
        : uploadFiles;

      var input = dz.querySelector('input[type="file"]');
      if (input) input.addEventListener("change", function () {
        doUpload(input.files);
        input.value = "";
      });

      ["dragenter", "dragover"].forEach(function (ev) {
        dz.addEventListener(ev, function (e) {
          e.preventDefault();
          dz.classList.add("is-dragging");
        });
      });
      ["dragleave", "dragend", "drop"].forEach(function (ev) {
        dz.addEventListener(ev, function (e) {
          e.preventDefault();
          dz.classList.remove("is-dragging");
        });
      });
      dz.addEventListener("drop", function (e) {
        e.preventDefault();
        if (e.dataTransfer && e.dataTransfer.files) doUpload(e.dataTransfer.files);
      });
    }

    bindFrameDragReorder();
    bindConfigForm();
    bindCurvePanels();
    bindWizard();
    resumeExposurePolling();
  });

  // -------- curve renderer (SVG, read-only) ---------------------------

  var CH_INFO = [
    { key: "red",   suffix: "r", label: "R" },
    { key: "green", suffix: "g", label: "G" },
    { key: "blue",  suffix: "b", label: "B" },
  ];
  var CH_STROKE = { red: "#ff5d5d", green: "#5ed46d", blue: "#6ea8e2" };

  function bindCurvePanels() {
    var dataEl = document.getElementById("curve-data");
    if (!dataEl) return;
    var curves;
    try { curves = JSON.parse(dataEl.textContent); }
    catch (e) { console.warn("curve data parse failed", e); return; }

    $$(".curve-panel").forEach(function (panel) {
      var key = panel.dataset.curvePanel;
      var data = curves[key];
      if (!data) return;
      var state = { data: data, channels: { red: true, green: true, blue: true } };
      renderCurvePanel(panel, state);

      var canvas = panel.querySelector("[data-curve-canvas]");
      // Re-render on resize so the SVG viewBox stays in sync with the
      // container aspect — SVG scales, but we re-tick the axis labels.
      var ro = new ResizeObserver(function () { renderCurvePanel(panel, state); });
      ro.observe(canvas);

      panel.querySelectorAll('[data-ch-toggles] input').forEach(function (cb) {
        cb.addEventListener("change", function () {
          state.channels[cb.dataset.channel] = cb.checked;
          renderCurvePanel(panel, state);
        });
      });
    });
  }

  function renderCurvePanel(panel, state) {
    var canvas = panel.querySelector("[data-curve-canvas]");
    var readout = panel.querySelector("[data-curve-readout]");
    if (!canvas) return;

    var rect = canvas.getBoundingClientRect();
    var w = Math.max(rect.width, 200);
    var h = Math.max(rect.height, 140);
    var pad = { l: 44, r: 8, t: 8, b: 22 };
    var plotW = w - pad.l - pad.r;
    var plotH = h - pad.t - pad.b;

    // Y-axis max: max of *enabled* channels, with a sensible floor so an
    // empty selection still gives a usable axis.
    var maxY = 1;
    CH_INFO.forEach(function (ci) {
      if (!state.channels[ci.key]) return;
      var arr = state.data[ci.key];
      for (var i = 0; i < arr.length; i++) if (arr[i] > maxY) maxY = arr[i];
    });
    // Round up to a nice number for the axis.
    maxY = niceCeil(maxY);

    function px(i) { return pad.l + (i / 255) * plotW; }
    function py(v) { return pad.t + plotH - (v / maxY) * plotH; }

    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("preserveAspectRatio", "none");

    // Grid + axes
    var xTicks = [0, 32, 64, 96, 128, 160, 192, 224, 255];
    var yTicks = niceYTicks(maxY, 5);
    yTicks.forEach(function (v) {
      var y = py(v);
      var line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", pad.l); line.setAttribute("x2", pad.l + plotW);
      line.setAttribute("y1", y); line.setAttribute("y2", y);
      line.setAttribute("class", "curve-grid-line");
      svg.appendChild(line);
      var t = document.createElementNS(svgNS, "text");
      t.setAttribute("x", pad.l - 6); t.setAttribute("y", y + 3);
      t.setAttribute("text-anchor", "end");
      t.setAttribute("class", "curve-axis-label");
      t.textContent = formatY(v);
      svg.appendChild(t);
    });
    xTicks.forEach(function (i) {
      var x = px(i);
      var line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", x); line.setAttribute("x2", x);
      line.setAttribute("y1", pad.t); line.setAttribute("y2", pad.t + plotH);
      line.setAttribute("class", "curve-grid-line");
      svg.appendChild(line);
      var t = document.createElementNS(svgNS, "text");
      t.setAttribute("x", x); t.setAttribute("y", pad.t + plotH + 12);
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("class", "curve-axis-label");
      t.textContent = i;
      svg.appendChild(t);
    });

    // Plot area frame
    var frame = document.createElementNS(svgNS, "rect");
    frame.setAttribute("x", pad.l); frame.setAttribute("y", pad.t);
    frame.setAttribute("width", plotW); frame.setAttribute("height", plotH);
    frame.setAttribute("fill", "none");
    frame.setAttribute("class", "curve-axis-line");
    svg.appendChild(frame);

    // Curves
    CH_INFO.forEach(function (ci) {
      if (!state.channels[ci.key]) return;
      var arr = state.data[ci.key];
      var line = "";
      var fill = "M " + px(0) + " " + (pad.t + plotH);
      for (var i = 0; i < arr.length; i++) {
        var x = px(i).toFixed(2);
        var y = py(arr[i]).toFixed(2);
        line += (i === 0 ? "M " : " L ") + x + " " + y;
        fill += " L " + x + " " + y;
      }
      fill += " L " + px(255) + " " + (pad.t + plotH) + " Z";

      var fp = document.createElementNS(svgNS, "path");
      fp.setAttribute("d", fill);
      fp.setAttribute("class", "curve-fill curve-fill-" + ci.suffix);
      svg.appendChild(fp);

      var lp = document.createElementNS(svgNS, "path");
      lp.setAttribute("d", line);
      lp.setAttribute("class", "curve-line curve-line-" + ci.suffix);
      svg.appendChild(lp);
    });

    // Hover crosshair + per-channel dots
    var hoverLine = document.createElementNS(svgNS, "line");
    hoverLine.setAttribute("y1", pad.t); hoverLine.setAttribute("y2", pad.t + plotH);
    hoverLine.setAttribute("class", "curve-hover-line");
    svg.appendChild(hoverLine);

    var hoverDots = {};
    CH_INFO.forEach(function (ci) {
      var c = document.createElementNS(svgNS, "circle");
      c.setAttribute("r", 3);
      c.setAttribute("class", "curve-hover-dot");
      c.setAttribute("stroke", CH_STROKE[ci.key]);
      svg.appendChild(c);
      hoverDots[ci.key] = c;
    });

    // Hit target — full plot area, so movement is smooth.
    var hit = document.createElementNS(svgNS, "rect");
    hit.setAttribute("x", pad.l); hit.setAttribute("y", pad.t);
    hit.setAttribute("width", plotW); hit.setAttribute("height", plotH);
    hit.setAttribute("fill", "transparent");
    svg.appendChild(hit);

    function setHover(idx) {
      if (idx == null) {
        hoverLine.classList.remove("is-active");
        CH_INFO.forEach(function (ci) { hoverDots[ci.key].classList.remove("is-active"); });
        readout.textContent = "Hover the curve for values";
        return;
      }
      var x = px(idx);
      hoverLine.setAttribute("x1", x); hoverLine.setAttribute("x2", x);
      hoverLine.classList.add("is-active");
      var parts = ['<span class="ro-label">in</span> ' + idx];
      CH_INFO.forEach(function (ci) {
        var dot = hoverDots[ci.key];
        if (!state.channels[ci.key]) {
          dot.classList.remove("is-active");
          return;
        }
        var v = state.data[ci.key][idx];
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", py(v));
        dot.classList.add("is-active");
        parts.push('<span class="ro-' + ci.suffix + '">' + ci.label + '</span> ' + formatY(v));
      });
      readout.innerHTML = parts.join('<span class="dot-sep">·</span>');
    }

    hit.addEventListener("mousemove", function (ev) {
      var r = svg.getBoundingClientRect();
      // SVG viewBox is sized to the rendered px, so coords are 1:1.
      var localX = (ev.clientX - r.left) * (w / r.width);
      var frac = (localX - pad.l) / plotW;
      var idx = Math.max(0, Math.min(255, Math.round(frac * 255)));
      setHover(idx);
    });
    hit.addEventListener("mouseleave", function () { setHover(null); });

    canvas.replaceChildren(svg);
  }

  function niceCeil(v) {
    if (v <= 1) return 1;
    var pow = Math.pow(10, Math.floor(Math.log10(v)));
    var n = v / pow;
    var step;
    if (n <= 1) step = 1;
    else if (n <= 2) step = 2;
    else if (n <= 5) step = 5;
    else step = 10;
    return step * pow;
  }

  function niceYTicks(maxY, count) {
    var ticks = [];
    for (var i = 0; i <= count; i++) ticks.push(Math.round(maxY * i / count));
    return ticks;
  }

  function formatY(v) {
    if (v >= 10000) return (v / 1000).toFixed(0) + "k";
    if (v >= 1000) return (v / 1000).toFixed(1) + "k";
    return String(v);
  }

  // -------- wizard: create new film table -----------------------------

  function bindWizard() {
    var openBtn = document.querySelector("[data-wizard-open]");
    var backdrop = document.getElementById("wizard-backdrop");
    if (!openBtn || !backdrop) return;
    var dataEl = document.getElementById("wizard-baselines");
    if (!dataEl) return;
    var baselines;
    try { baselines = JSON.parse(dataEl.textContent); }
    catch (e) { console.warn("wizard baselines parse failed", e); return; }

    var form = backdrop.querySelector("#wizard-form");
    var cancelBtn = backdrop.querySelector("[data-wizard-cancel]");
    var submitBtn = backdrop.querySelector("[data-wizard-submit]");
    var filterSeg = backdrop.querySelector("[data-filter-seg]");

    // Track existing internal_names so we can warn on collision before submit.
    var existingIds = Array.from(document.querySelectorAll("[data-profile-id]"))
      .map(function (el) { return el.dataset.profileId; });

    openBtn.addEventListener("click", function () {
      backdrop.hidden = false;
      // Reset form to defaults.
      form.reset();
      clearFieldError("internal_name");
      updatePreview();
      setTimeout(function () { form.querySelector('input[name="name"]').focus(); }, 30);
    });

    function close() { backdrop.hidden = true; }
    cancelBtn.addEventListener("click", close);
    backdrop.addEventListener("click", function (ev) {
      if (ev.target === backdrop) close();
    });
    document.addEventListener("keydown", function (ev) {
      if (!backdrop.hidden && ev.key === "Escape") close();
    });

    // Live preview: any field change re-renders curves.
    form.addEventListener("change", updatePreview);
    form.addEventListener("input", function (ev) {
      if (ev.target.name === "internal_name") clearFieldError("internal_name");
    });

    submitBtn.addEventListener("click", function (ev) {
      ev.preventDefault();
      submitWizard();
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      submitWizard();
    });

    // First render with default form values (in case the page renders with
    // the wizard hidden -- bind ResizeObserver lazily on open).
    var panels = backdrop.querySelectorAll("[data-wizard-curve]");
    panels.forEach(function (panel) {
      var ro = new ResizeObserver(function () {
        if (!backdrop.hidden) updatePreview();
      });
      ro.observe(panel.querySelector("[data-curve-canvas]"));
    });

    function readForm() {
      var fd = new FormData(form);
      return {
        name: (fd.get("name") || "").trim(),
        internal_name: (fd.get("internal_name") || "").trim(),
        is_color: fd.get("type") === "color",
        bw_filter: parseInt(fd.get("bw_filter") || "3", 10),
        camera_type: parseInt(fd.get("camera_type") || "1", 10),
        iso: parseInt(fd.get("iso") || "100", 10),
      };
    }

    // B&W filter byte (per pp8k's BW_FILTER_TO_CHANNEL, verified against
    // the device on 2026-05-16): 0=Clear (3-pass), 1=Green, 2=Red, 3=Blue.
    var FILTER_CHANNEL = { 1: "green", 2: "red", 3: "blue" };

    function updatePreview() {
      var v = readForm();
      var factor = baselines.ref_iso / v.iso;
      function scale(arr) { return arr.map(function (x) { return Math.round(x * factor); }); }
      var scaledA = scale(baselines.master_a);
      var scaledB = scale(baselines.master_b);

      // For B&W single-channel curves, draw the curve in the colour of the
      // selected filter (the phosphor the firmware drives at exposure time).
      var ch = FILTER_CHANNEL[v.bw_filter] || "blue";
      panels.forEach(function (panel) {
        var key = panel.dataset.wizardCurve;
        var arr = key === "8k" ? scaledB : scaledA;
        var state = {
          data: { red: arr, green: arr, blue: arr },
          channels: { red: ch === "red", green: ch === "green", blue: ch === "blue" },
        };
        renderCurvePanel(panel, state);
      });
    }

    function setFieldError(name, msg) {
      var el = backdrop.querySelector('[data-field-error="' + name + '"]');
      if (!el) return;
      el.textContent = msg;
      el.hidden = false;
      var input = form.querySelector('input[name="' + name + '"]');
      if (input) input.classList.add("is-error");
    }

    function clearFieldError(name) {
      var el = backdrop.querySelector('[data-field-error="' + name + '"]');
      if (el) { el.textContent = ""; el.hidden = true; }
      var input = form.querySelector('input[name="' + name + '"]');
      if (input) input.classList.remove("is-error");
    }

    async function submitWizard() {
      var v = readForm();
      // Client-side checks for fast feedback.
      if (!v.name) { toast("Name is required", "warn"); return; }
      if (!/^[A-Za-z0-9_\-]{1,8}$/.test(v.internal_name)) {
        setFieldError("internal_name", "Must be 1-8 chars: letters, digits, '-', '_'.");
        return;
      }
      if (existingIds.indexOf(v.internal_name) !== -1) {
        setFieldError("internal_name", "Already used by another film table. Pick a different name.");
        return;
      }

      submitBtn.disabled = true;
      try {
        var res = await fetch("/api/film-tables/new", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(v),
        });
        var body = await res.json();
        if (!res.ok) {
          var err = body && body.error ? body.error : res.statusText;
          if (/internal_name/.test(err)) setFieldError("internal_name", err);
          else toast(err, "err");
          submitBtn.disabled = false;
          return;
        }
        toast("Created " + body.name, "ok");
        location.href = "/film-tables/" + encodeURIComponent(body.id);
      } catch (e) {
        toast("Create failed: " + e.message, "err");
        submitBtn.disabled = false;
      }
    }
  }
})();
