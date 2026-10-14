/* What the reader has typed into a GM side panel tab, carried through the
 * tab's refetch (issue #16).
 *
 * While a turn runs, gm-panel-characters.js replaces the Characters and
 * Campaign tabs with the server's rendering of them on every procedure and
 * done frame. A Game Master halfway through correcting a record had the form
 * closed under him and what he typed thrown away. So every form that holds
 * typing names itself by the record it changes (data-draft-key, set by the
 * template, never by position) and says what it is (data-draft-label). Just
 * before a tab is replaced, read() takes each such form that is open, typed
 * in or focused; just after, restore() puts it back on the form of the same
 * key: open, the typed text, and the focus and caret if it had them. A field
 * counts as typed in when its value is no longer the one the server rendered
 * (its defaultValue), so a form the reader only opened takes the record's
 * new value.
 *
 * A form whose record has gone from the tab (the turn resolved the thread,
 * say) cannot be put back. What was typed in it is not dropped: the tab
 * shows it, record by record, under a notice that stays until the reader
 * dismisses it (no transient UI). The notice is drawn from this module's
 * state after every refetch, since the refetch replaces it with the rest.
 *
 * discard() is for a form whose change the server has just saved: the form
 * on the page under that key is emptied and closed, so the refetch that
 * follows does not put the saved text back as a draft.
 */
(function (root) {
  /** The controls that hold typing. Selects and checkboxes are not carried. */
  const TEXT_FIELDS = 'textarea, input:not([type]), input[type="text"], input[type="search"]';

  /** Drafts that could not be put back, per tab: tab id -> key -> draft. */
  const lostByTab = new Map();

  function keyedForms(scope) {
    return Array.from(scope.querySelectorAll('form[data-draft-key]'));
  }

  function formByKey(scope, key) {
    return keyedForms(scope).find((form) => form.dataset.draftKey === key) || null;
  }

  /** A field's name within its form: its name, or its id for one without. */
  function fieldName(control) {
    return control.name || control.id;
  }

  function fieldByName(form, name) {
    return Array.from(form.querySelectorAll(TEXT_FIELDS))
      .find((control) => fieldName(control) === name) || null;
  }

  /** The words of a field's label, without the text of the field inside it. */
  function fieldLabel(control) {
    const label = control.labels && control.labels[0];
    const words = label
      ? Array.from(label.childNodes)
        .filter((node) => node.nodeType === root.Node.TEXT_NODE)
        .map((node) => node.textContent.trim())
        .filter(Boolean)
        .join(' ')
      : '';
    return words || fieldName(control);
  }

  /** Where the focus is in ``form``, if it is there: the field and its caret. */
  function readFocus(form) {
    const active = root.document.activeElement;
    if (!active || active === root.document.body) return null;
    const details = form.closest('details');
    const summary = details && details.querySelector('summary');
    if (summary && active === summary) return { summary: true };
    if (!form.contains(active) || !active.matches(TEXT_FIELDS) || !fieldName(active)) return null;
    return {
      name: fieldName(active),
      start: active.selectionStart,
      end: active.selectionEnd,
      direction: active.selectionDirection,
    };
  }

  /**
   * Take what the reader has open, typed or focused in ``container``'s forms.
   * Read just before the container is replaced: nothing may happen between.
   * @param {Element} container - The tab whose contents are about to go.
   * @returns {Map<string, {label: string, open: boolean, fields: Array<{name: string, label: string, value: string}>, focus: object|null}>}
   */
  function read(container) {
    const drafts = new Map();
    keyedForms(container).forEach((form) => {
      const details = form.closest('details');
      const open = Boolean(details && details.open);
      const fields = Array.from(form.querySelectorAll(TEXT_FIELDS))
        .filter((control) => fieldName(control) && control.value !== control.defaultValue)
        .map((control) => ({ name: fieldName(control), label: fieldLabel(control), value: control.value }));
      const focus = readFocus(form);
      if (!open && !fields.length && !focus) return;
      const key = form.dataset.draftKey;
      drafts.set(key, { label: form.dataset.draftLabel || key, open, fields, focus });
    });
    return drafts;
  }

  function putFocus(form, focus) {
    if (focus.summary) {
      const details = form.closest('details');
      const summary = details && details.querySelector('summary');
      if (summary) summary.focus({ preventScroll: true });
      return;
    }
    const control = fieldByName(form, focus.name);
    if (!control) return;
    control.focus({ preventScroll: true });
    if (focus.start !== null && focus.start !== undefined) {
      control.setSelectionRange(focus.start, focus.end, focus.direction || 'none');
    }
  }

  /**
   * Put ``drafts`` back on the forms of the same keys in the refetched
   * ``container``. A draft whose form has gone joins the tab's notice.
   * @param {Element} container - The tab, just refilled.
   * @param {Map} drafts - What read() took from it.
   */
  function restore(container, drafts) {
    const lost = lostByTab.get(container.id) || new Map();
    let newlyLost = false;
    drafts.forEach((draft, key) => {
      const form = formByKey(container, key);
      if (!form) {
        if (draft.fields.length) {
          lost.set(key, { label: draft.label, fields: draft.fields });
          newlyLost = true;
        }
        return;
      }
      const details = form.closest('details');
      if (details && draft.open) details.open = true;
      const missing = draft.fields.filter((field) => {
        const control = fieldByName(form, field.name);
        if (control) control.value = field.value;
        return !control;
      });
      if (missing.length) {
        lost.set(key, { label: draft.label, fields: missing });
        newlyLost = true;
      }
      if (draft.focus) putFocus(form, draft.focus);
    });
    if (lost.size) lostByTab.set(container.id, lost);
    showLost(container, newlyLost);
  }

  /**
   * Draw the tab's notice of drafts that could not be put back, or take it
   * away when there are none. It is announced only when a draft is newly
   * lost, not each time a refetch draws it again.
   * @param {Element} container
   * @param {boolean} announce
   */
  function showLost(container, announce) {
    const shown = container.querySelector(':scope > [data-drafts-lost]');
    if (shown) shown.remove();
    const lost = lostByTab.get(container.id);
    if (!lost || !lost.size) return;
    const doc = root.document;
    const notice = doc.createElement('div');
    notice.className = 'gm-panel-error gm-drafts-lost';
    notice.dataset.draftsLost = '';
    if (announce) notice.setAttribute('role', 'alert');
    const intro = doc.createElement('p');
    intro.textContent = 'This could not be put back where you were typing it: the record '
      + 'is no longer in this list. What you typed is kept here until you dismiss it.';
    notice.append(intro);
    const list = doc.createElement('ul');
    lost.forEach((draft, key) => {
      const item = doc.createElement('li');
      item.dataset.draftLost = key;
      const heading = doc.createElement('strong');
      heading.textContent = draft.label;
      const fields = doc.createElement('dl');
      draft.fields.forEach((field) => {
        const term = doc.createElement('dt');
        term.textContent = field.label;
        const value = doc.createElement('dd');
        value.textContent = field.value;
        fields.append(term, value);
      });
      const dismiss = doc.createElement('button');
      dismiss.type = 'button';
      dismiss.className = 'btn btn-sm';
      dismiss.textContent = 'Dismiss';
      dismiss.setAttribute('aria-label', `Dismiss what you typed for ${draft.label}`);
      dismiss.addEventListener('click', () => {
        lost.delete(key);
        showLost(container, false);
      });
      item.append(heading, fields, dismiss);
      list.append(item);
    });
    notice.append(list);
    container.prepend(notice);
  }

  /**
   * The server saved ``form``'s change: empty and close the form of its key
   * on the page now (a refetch may have replaced ``form`` while the request
   * was out), and drop any notice of it, so nothing saved is put back.
   * @param {HTMLFormElement} form
   */
  function discard(form) {
    const key = form.dataset.draftKey;
    if (!key) return;
    const live = form.isConnected ? form : formByKey(root.document, key);
    if (live) {
      live.reset();
      const details = live.closest('details');
      if (details) details.open = false;
    }
    lostByTab.forEach((lost, tabId) => {
      const container = root.document.getElementById(tabId);
      if (lost.delete(key) && container) showLost(container, false);
    });
  }

  const api = { read, restore, discard };
  root.gmPanelDrafts = api;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
