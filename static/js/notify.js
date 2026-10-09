/**
 * In-page notifications and confirmations — replacements for the browser's
 * jarring, unstyled, untestable alert()/confirm().
 *
 *   showNotice(message, type)        -> a persistent, dismissible banner
 *                                       (state-driven: stays until dismissed)
 *   confirmDialog(message, options)  -> Promise<boolean> from an inline panel
 *
 * No auto-dismiss (matches the project's no-transient-UI rule).
 *
 * confirmDialog used to open a centred overlay with aria-modal and a focus
 * trap. Issue #190 retired that: the question appears as a panel and the page
 * stays usable behind it. The promise API is unchanged, so every uiConfirm
 * call site keeps working — only the rendering moved.
 *
 * Where the panel lands moved once more, for issue #711. The notice region
 * sits at the top of <main>, which is right for a message about the page and
 * wrong for a question about one control: the character list's Delete
 * confirmation asked "Delete X? This can't be undone." at the top of the
 * document while the button that raised it was in a table row further down,
 * and on a list of twenty characters the question appeared off-screen. Pass
 * `anchor` — the element the question is about — and the panel is drawn
 * immediately after it. Without one it still goes to the notice region.
 */
(function () {
  function noticeRegion() {
    let region = document.getElementById("notice-region");
    if (!region) {
      region = document.createElement("div");
      region.id = "notice-region";
      region.setAttribute("aria-live", "polite");
      const main = document.querySelector("main") || document.body;
      main.insertBefore(region, main.firstChild);
    }
    return region;
  }

  function showNotice(message, type) {
    const notice = document.createElement("div");
    const resolvedType = type || "error";
    notice.className = "notice notice-" + resolvedType;
    notice.setAttribute("role", resolvedType === "error" ? "alert" : "status");

    const text = document.createElement("span");
    text.className = "notice-text";
    text.textContent = message;

    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "notice-dismiss";
    dismiss.setAttribute("aria-label", "Dismiss");
    dismiss.textContent = "×";
    dismiss.addEventListener("click", () => notice.remove());

    notice.appendChild(text);
    notice.appendChild(dismiss);
    noticeRegion().appendChild(notice);
    return notice;
  }

  // At most one question stands at a time: asking again answers the
  // previous one "no" and replaces it, so two panels never stack up.
  let openConfirm = null;

  /**
   * Where a confirmation panel belongs: beside the control it is about.
   * @param {Element} [anchor] - The element that raised the question.
   * @returns {{parent: Element, before: Node|null}} Insertion point.
   */
  function confirmPlacement(anchor) {
    if (anchor && anchor.parentNode) {
      return { parent: anchor.parentNode, before: anchor.nextSibling };
    }
    return { parent: noticeRegion(), before: null };
  }

  function confirmDialog(message, options) {
    options = options || {};
    const confirmLabel = options.confirmLabel || "OK";
    const cancelLabel = options.cancelLabel || "Cancel";
    const danger = !!options.danger;
    const placement = confirmPlacement(options.anchor);

    if (openConfirm) { openConfirm(false); }

    return new Promise((resolve) => {
      const panel = document.createElement("div");
      panel.className = "notice notice-confirm";
      // A group, not a dialog: nothing here is modal, and the page behind
      // stays reachable by keyboard and pointer alike.
      panel.setAttribute("role", "group");
      panel.setAttribute("aria-label", "Confirm");

      const text = document.createElement("span");
      text.className = "notice-text";
      text.textContent = message;

      const actions = document.createElement("div");
      actions.className = "confirm-actions";

      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "button secondary";
      cancelBtn.textContent = cancelLabel;

      const okBtn = document.createElement("button");
      okBtn.type = "button";
      okBtn.className = "button" + (danger ? " danger" : "");
      okBtn.textContent = confirmLabel;

      actions.appendChild(cancelBtn);
      actions.appendChild(okBtn);
      panel.appendChild(text);
      panel.appendChild(actions);
      placement.parent.insertBefore(panel, placement.before);

      function close(result) {
        if (openConfirm !== close) { return; }
        openConfirm = null;
        panel.remove();
        resolve(result);
      }
      openConfirm = close;

      cancelBtn.addEventListener("click", () => close(false));
      okBtn.addEventListener("click", () => close(true));
      // Escape backs out, but only while focus is inside the panel — a
      // page-wide listener would be modal behaviour by another name.
      panel.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          close(false);
        }
      });

      if (panel.scrollIntoView) {
        panel.scrollIntoView({ block: "nearest" });
      }
      okBtn.focus();
    });
  }

  /**
   * Ask for one line of text — the in-page replacement for prompt().
   *
   * Same panel and same promise shape as confirmDialog (issue #339, item 9),
   * because the app already had two dialog patterns and did not need a
   * third: it renders in the notice region, nothing is modal, and the page
   * behind stays usable. Resolves with the trimmed text, or null when the
   * asker backs out — so `if (!answer) return;` reads exactly as it did
   * around the prompt() this replaces.
   *
   * @param {string} message - the question
   * @param {object} [options] - defaultValue, confirmLabel, cancelLabel,
   *   placeholder, maxLength
   * @returns {Promise<string|null>}
   */
  function promptDialog(message, options) {
    options = options || {};
    const confirmLabel = options.confirmLabel || "OK";
    const cancelLabel = options.cancelLabel || "Cancel";

    if (openConfirm) { openConfirm(false); }

    return new Promise((resolve) => {
      const panel = document.createElement("div");
      panel.className = "notice notice-confirm notice-prompt";
      panel.setAttribute("role", "group");
      panel.setAttribute("aria-label", "Enter a value");

      const label = document.createElement("label");
      label.className = "notice-text";
      label.textContent = message;

      const field = document.createElement("input");
      field.type = "text";
      field.className = "notice-prompt-input";
      field.value = options.defaultValue || "";
      if (options.placeholder) { field.placeholder = options.placeholder; }
      if (options.maxLength) { field.maxLength = options.maxLength; }
      label.appendChild(field);

      const actions = document.createElement("div");
      actions.className = "confirm-actions";

      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "button secondary";
      cancelBtn.textContent = cancelLabel;

      const okBtn = document.createElement("button");
      okBtn.type = "button";
      okBtn.className = "button";
      okBtn.textContent = confirmLabel;

      actions.appendChild(cancelBtn);
      actions.appendChild(okBtn);
      panel.appendChild(label);
      panel.appendChild(actions);
      noticeRegion().appendChild(panel);

      function close(result) {
        if (openConfirm !== close) { return; }
        openConfirm = null;
        panel.remove();
        // Shares confirmDialog's "at most one question stands at a time"
        // slot, so a prompt and a confirm can never stack up on each other.
        // That path closes the standing question with `false`; a boolean is
        // not an answer to a prompt, so it lands as null — the same "backed
        // out" answer prompt() gives.
        resolve(result === false ? null : result);
      }
      openConfirm = close;

      cancelBtn.addEventListener("click", () => close(null));
      okBtn.addEventListener("click", () => close(field.value.trim()));
      panel.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          close(null);
        }
        if (e.key === "Enter" && e.target === field) {
          e.preventDefault();
          close(field.value.trim());
        }
      });

      if (panel.scrollIntoView) { panel.scrollIntoView({ block: "nearest" }); }
      field.focus();
      field.select();
    });
  }

  /**
   * Forms that ask before they post: `data-confirm="…"` on the <form>.
   *
   * One delegated listener for the whole site (issue #339, item 5) rather
   * than a script per page — battle_list.html loads no JavaScript of its
   * own, and a destructive button there still has to ask. `data-confirm-label`
   * names the confirming button and `data-confirm-danger` tints it.
   *
   * The form is submitted through HTMLFormElement.prototype.submit, which
   * does not re-fire this event; the guard flag is belt and braces for a
   * browser that does.
   */
  function initConfirmForms(root) {
    (root || document).addEventListener("submit", (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) { return; }
      const question = form.dataset.confirm;
      if (!question || form.dataset.confirmed === "yes") { return; }
      event.preventDefault();
      confirmDialog(question, {
        confirmLabel: form.dataset.confirmLabel || "OK",
        danger: form.dataset.confirmDanger === "true",
      }).then((confirmed) => {
        if (!confirmed) { return; }
        form.dataset.confirmed = "yes";
        form.submit();
      });
    });
  }

  // Shared so other scripts put their persistent panels in the same
  // place rather than reimplementing the region lookup (issue #190).
  window.noticeRegion = noticeRegion;
  window.showNotice = showNotice;
  window.confirmDialog = confirmDialog;
  window.promptDialog = promptDialog;
  window.initConfirmForms = initConfirmForms;

  if (typeof document !== "undefined") { initConfirmForms(document); }
})();
