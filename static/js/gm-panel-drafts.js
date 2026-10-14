/* What the reader has typed into a GM side panel tab, carried through the
 * tab's refetch (issue #16).
 *
 * While a turn runs, gm-panel-characters.js replaces the Characters and
 * Campaign tabs with the server's rendering of them on every procedure and
 * done frame. A Game Master halfway through correcting a record had the form
 * closed under him and what he typed thrown away. So every form that holds
 * typing names itself by the record it changes (data-draft-key, set by the
 * template, never by position) and says what it is (data-draft-label). Just
 * before a tab is replaced, read() takes each such form that is open or
 * typed in, and where the keyboard focus is; just after, restore() puts them
 * back on the form of the same key: open, the typed text, and the focus and
 * caret. A field counts as typed in when its value is no longer the one the
 * server rendered (its defaultValue), so a form the reader only opened takes
 * the record's new value.
 *
 * A typed field whose record the turn changed keeps what was typed, and a
 * note by it says what the turn changed it to: saving would otherwise
 * replace the turn's change without the Game Master knowing there was one.
 * The note stays, through every refetch, until the form is saved, the field
 * no longer differs from the record, or he dismisses it.
 *
 * The focus is carried for the tab's other controls as well (Retire,
 * Detach, the resolved list's summary...): each names itself by
 * data-focus-key, the convention battle.js's turn panel keeps (#646), or by
 * its id. A control whose record has gone hands the focus
 * to the tab itself, never to the page's body.
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
 *
 * readCaret/putCaret/elementByFocusKey come from api.js.
 */
if (typeof module !== 'undefined' && module.exports) {
  require('./api.js');
}

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

  /**
   * The <details> a form opens and closes in, if it has one inside ``scope``
   * (the tab). One outside the tab -- a pane the tab sits in -- is not the
   * form's to open or close.
   */
  function ownDetails(form, scope) {
    const details = form.closest('details');
    return details && scope && scope !== details && scope.contains(details) ? details : null;
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

  /**
   * The record's value a typed field was started from: the one the server
   * rendered, or, once the turn has changed it under the draft, the one the
   * draft was started from, until the Game Master dismisses the note.
   */
  function baseOf(control) {
    return 'draftBase' in control.dataset ? control.dataset.draftBase : control.defaultValue;
  }

  /**
   * Where the keyboard focus is in ``container``, as something the refetched
   * tab can be asked for: a field of a keyed form (with its caret), a button
   * of one, the summary that opens one, or a control by its focus key or id.
   * @returns {object|null}
   */
  function readFocus(container) {
    const active = root.document.activeElement;
    if (!active || active === container || !container.contains(active)) return null;
    const form = active.closest('form[data-draft-key]');
    if (form && active.matches(TEXT_FIELDS) && fieldName(active)) {
      return { form: form.dataset.draftKey, field: fieldName(active), caret: root.readCaret(active) };
    }
    if (active.dataset.focusKey) return { focusKey: active.dataset.focusKey };
    if (form) {
      const buttons = Array.from(form.querySelectorAll('button'));
      return { form: form.dataset.draftKey, button: buttons.indexOf(active) };
    }
    if (active.matches('summary')) {
      const opened = active.parentElement && active.parentElement.querySelector(':scope > form[data-draft-key]');
      if (opened) return { summaryOf: opened.dataset.draftKey };
    }
    if (active.id) return { id: active.id };
    return { lost: true };
  }

  /** The element in the refetched ``container`` that ``focus`` names. */
  function focusTarget(container, focus) {
    if (focus.focusKey) return root.elementByFocusKey(container, focus.focusKey);
    if (focus.id) {
      return Array.from(container.querySelectorAll('[id]'))
        .find((element) => element.id === focus.id) || null;
    }
    const key = focus.form || focus.summaryOf;
    const form = key && formByKey(container, key);
    if (!form) return null;
    if (focus.summaryOf) {
      const details = ownDetails(form, container);
      return details && details.querySelector(':scope > summary');
    }
    if (focus.field) return fieldByName(form, focus.field);
    return form.querySelectorAll('button')[focus.button] || null;
  }

  /** Make the tab itself take the focus, when what had it is gone. */
  function focusTab(container) {
    if (!container.hasAttribute('tabindex')) container.setAttribute('tabindex', '-1');
    container.focus({ preventScroll: true });
  }

  /**
   * Give the focus to ``target``; one the browser will not focus (a button
   * in a form that closed when it saved) hands it to the summary that opens
   * the form, and failing that to the tab.
   */
  function focusWithin(container, target) {
    if (target) {
      target.focus({ preventScroll: true });
      if (root.document.activeElement === target) return;
      const details = target.closest('details');
      const summary = details && container.contains(details) && details.querySelector(':scope > summary');
      if (summary && summary !== target) {
        summary.focus({ preventScroll: true });
        if (root.document.activeElement === summary) return;
      }
    }
    focusTab(container);
  }

  function putFocus(container, focus) {
    const target = focus.lost ? null : focusTarget(container, focus);
    focusWithin(container, target);
    if (target && focus.caret && root.document.activeElement === target) root.putCaret(target, focus.caret);
  }

  /**
   * Take what the reader has open or typed in ``container``'s forms, and
   * where the focus is. Read just before the container is replaced: nothing
   * may happen between.
   * @param {Element} container - The tab whose contents are about to go.
   * @returns {{forms: Map<string, {label: string, open: boolean, fields: Array<{name: string, label: string, value: string, base: string, noted: boolean}>}>, focus: object|null}}
   */
  function read(container) {
    const forms = new Map();
    keyedForms(container).forEach((form) => {
      const details = ownDetails(form, container);
      const open = Boolean(details && details.open);
      const fields = Array.from(form.querySelectorAll(TEXT_FIELDS))
        .filter((control) => fieldName(control) && control.value !== control.defaultValue)
        .map((control) => ({
          name: fieldName(control),
          label: fieldLabel(control),
          value: control.value,
          base: baseOf(control),
          noted: 'draftBase' in control.dataset,
        }));
      if (!open && !fields.length) return;
      const key = form.dataset.draftKey;
      forms.set(key, { label: form.dataset.draftLabel || key, open, fields });
    });
    return { forms, focus: readFocus(container) };
  }

  /**
   * Note by ``control`` that the turn changed its record to what the server
   * now renders. ``announce`` reads it out; a note drawn again is not.
   */
  function noteChanged(control, announce) {
    const doc = root.document;
    const note = doc.createElement('p');
    note.className = 'gm-draft-changed';
    note.dataset.draftChanged = fieldName(control);
    if (announce) note.setAttribute('role', 'status');
    const words = doc.createElement('span');
    words.textContent = control.defaultValue
      ? `The turn changed this to: ${control.defaultValue}`
      : 'The turn cleared this.';
    const dismiss = doc.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'btn btn-sm';
    dismiss.textContent = 'Dismiss';
    dismiss.setAttribute('aria-label', `Dismiss the note that the turn changed ${fieldLabel(control)}`);
    dismiss.addEventListener('click', () => {
      // What he typed now stands against the record as it is.
      delete control.dataset.draftBase;
      note.remove();
      control.focus({ preventScroll: true });
    });
    note.append(words, dismiss);
    (control.closest('label') || control).after(note);
  }

  /**
   * Put ``drafts`` back on the forms of the same keys in the refetched
   * ``container``, and the focus where it was. A draft whose form has gone
   * joins the tab's notice.
   * @param {Element} container - The tab, just refilled.
   * @param {{forms: Map, focus: object|null}} drafts - What read() took from it.
   */
  function restore(container, drafts) {
    const lost = lostByTab.get(container.id) || new Map();
    let newlyLost = false;
    drafts.forms.forEach((draft, key) => {
      const form = formByKey(container, key);
      if (!form) {
        if (draft.fields.length) {
          lost.set(key, { label: draft.label, fields: draft.fields });
          newlyLost = true;
        }
        return;
      }
      const details = ownDetails(form, container);
      if (details && draft.open) details.open = true;
      const missing = draft.fields.filter((field) => {
        const control = fieldByName(form, field.name);
        if (!control) return true;
        control.value = field.value;
        if (control.defaultValue !== field.base) {
          control.dataset.draftBase = field.base;
          noteChanged(control, !field.noted);
        }
        return false;
      });
      if (missing.length) {
        lost.set(key, { label: draft.label, fields: missing });
        newlyLost = true;
      }
    });
    if (lost.size) lostByTab.set(container.id, lost);
    showLost(container, newlyLost);
    if (drafts.focus) putFocus(container, drafts.focus);
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
    Array.from(lost.entries()).forEach(([key, draft], index) => {
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
      dismiss.dataset.focusKey = `dismiss-lost-${key}`;
      dismiss.textContent = 'Dismiss';
      dismiss.setAttribute('aria-label', `Dismiss what you typed for ${draft.label}`);
      dismiss.addEventListener('click', () => {
        lost.delete(key);
        showLost(container, false);
        // The focus goes to the Dismiss that took this one's place, or the
        // last one left, or the tab once none are.
        const left = container.querySelectorAll(':scope > [data-drafts-lost] button');
        focusWithin(container, left[Math.min(index, left.length - 1)] || null);
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
      live.querySelectorAll('[data-draft-changed]').forEach((note) => note.remove());
      live.querySelectorAll(TEXT_FIELDS).forEach((control) => { delete control.dataset.draftBase; });
      const details = ownDetails(live, live.closest('[data-panel-container]'));
      if (details) {
        // The browser drops the focus of a control it hides; a keyboard
        // user who saved stays on the summary that opens the form again.
        const hadFocus = live.contains(root.document.activeElement);
        details.open = false;
        const summary = details.querySelector(':scope > summary');
        if (hadFocus && summary) summary.focus({ preventScroll: true });
      }
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
