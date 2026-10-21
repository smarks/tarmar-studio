/* The Game Master's hand edits of a session's records (issues #789-#791).
 *
 * The Campaign tab renders, for a Game Master's account only, a form or a
 * button for each change a record allows: add, correct, retire, resolve,
 * reopen, remove (gm/_panel_campaign_body.html, gm/_hand_entity_form.html);
 * the chat renders a form under the fold's recap (gm/chat.html). Each form
 * carries the endpoint (data-url) and the change it makes (data-change); a
 * button carries the same and the record's id. This posts them as JSON --
 * every named field of the form, plus the change -- and then shows the
 * record as the server now holds it: the Campaign tab is fetched again
 * through gm-panel-characters.js, and the recap is replaced by the text
 * saved. A refusal is written into the tab's error line and stays there
 * until the next attempt (no transient UI). A form the reader has open or
 * has typed in while the tab is fetched again keeps both
 * (gm-panel-drafts.js, issue #16); one that saved is emptied first.
 *
 * The server decides who may (gm/hand_records.py): these controls are only
 * drawn for a Game Master, and a request from anyone else is refused.
 * One press sends one request (issue #142): each control's request is held
 * under a key naming its endpoint, its change and its record
 * (data-draft-key on a form, data-focus-key on a button), which the
 * Campaign tab's refetch does not replace, and its button stays held until
 * the answer lands, on the fresh control too.
 * postWithCsrf/csrfToken/uiConfirm/holdRequest come from api.js.
 */
if (typeof module !== 'undefined' && module.exports) {
  require('./api.js');
}

(function (root) {
  function byId(id) {
    return root.document.getElementById(id);
  }

  function setLine(id, message) {
    const line = byId(id);
    if (!line) return;
    line.textContent = message || '';
    line.hidden = !message;
  }

  /** The fields a form sends: each named control's value, as typed. */
  function formFields(form) {
    const body = {};
    form.querySelectorAll('[name]').forEach((control) => {
      body[control.name] = control.value;
    });
    return body;
  }

  /** The id a button names its record by, under the key the endpoint reads. */
  function buttonFields(button) {
    const body = {};
    [['rulingId', 'ruling_id'], ['entityId', 'entity_id'], ['threadId', 'thread_id']]
      .forEach(([dataKey, field]) => {
        if (button.dataset[dataKey]) body[field] = button.dataset[dataKey];
      });
    return body;
  }

  /**
   * Send one hand edit and show what the server holds afterwards.
   * @param {string} url - the endpoint
   * @param {object} body - the change and its fields
   * @param {{errorId: string, statusId: string}} lines - where to report
   * @returns {Promise<object|null>} the server's answer, or null on a refusal
   */
  async function send(url, body, lines) {
    setLine(lines.errorId, '');
    let response;
    let data;
    try {
      response = await postWithCsrf(url, csrfToken(), body);
      data = await response.json();
    } catch (error) {
      setLine(lines.errorId, `The change was not saved: ${error.message}`);
      return null;
    }
    if (!response.ok || !data.ok) {
      setLine(lines.errorId, `The change was not saved: ${data.error || response.statusText}`);
      return null;
    }
    return data;
  }

  const CAMPAIGN_LINES = {
    errorId: 'gm-panel-campaign-error',
    statusId: 'gm-panel-campaign-status',
  };

  /** A campaign edit went through: fetch the tab again, then say what changed. */
  async function afterCampaignEdit(data) {
    if (root.gmPanelCharacters && root.gmPanelCharacters.refreshPanel) {
      await root.gmPanelCharacters.refreshPanel();
    }
    setLine(CAMPAIGN_LINES.statusId, `${data.message} ${data.set_by ? `(${data.set_by})` : ''}`.trim());
  }

  /** Every hand control: the forms and the record buttons. */
  const HAND_CONTROLS = '.gm-hand-form, .gm-hand-action';

  /**
   * The key a control's request is held under (issue #142): its endpoint
   * (which names the session), its change and the record it names -- the
   * same on the control a refetch puts in its place. The recap's form,
   * which no refetch replaces, has its id.
   * @param {HTMLElement} control - A `.gm-hand-form` or `.gm-hand-action`.
   * @returns {string}
   */
  function heldKey(control) {
    const { url, change, draftKey, focusKey } = control.dataset;
    const record = draftKey || focusKey || control.id;
    if (!record) {
      // Every such control would share one key and hold the others.
      throw new Error('a hand control names no record (data-draft-key, data-focus-key or id)');
    }
    return `${url} ${change} ${record}`;
  }

  /** The buttons a control is pressed with: a form's submit, or the button. */
  function pressedBy(control) {
    return control.matches('form') ? root.submitButtonsOf(control) : [control];
  }

  /**
   * Hold every hand control in ``scope`` whose request is on its way, and
   * free the rest to the state they were rendered in. gm-panel-characters.js
   * calls this on each tab it fetches again.
   * @param {ParentNode} scope
   */
  function paintHeld(scope) {
    scope.querySelectorAll(HAND_CONTROLS).forEach((control) => {
      const held = root.requestHeld(heldKey(control));
      pressedBy(control).forEach((button) => root.paintHeldControl(button, held));
    });
  }

  function paintPageHeld() {
    paintHeld(root.document);
  }

  async function submitForm(form) {
    return root.holdRequest(heldKey(form), () => sendForm(form), paintPageHeld);
  }

  async function pressButton(button) {
    return root.holdRequest(heldKey(button), () => sendButton(button), paintPageHeld);
  }

  async function sendForm(form) {
    const body = { ...formFields(form), change: form.dataset.change };
    if (form.id === 'gm-fold-recap-form') {
      const data = await send(form.dataset.url, body, {
        errorId: 'gm-fold-recap-error',
        statusId: 'gm-fold-recap-status',
      });
      if (!data) return false;
      // The recap as the server saved it, lines and paragraphs kept.
      const shown = byId('gm-fold-recap-text');
      if (shown && data.after) shown.textContent = data.after.summary;
      setLine('gm-fold-recap-status', data.set_by || data.message);
      return true;
    }
    const data = await send(form.dataset.url, body, CAMPAIGN_LINES);
    if (!data) return false;
    // Saved: the form is emptied and closed, so the refetch does not carry
    // what was sent back into it as a draft (issue #16).
    if (root.gmPanelDrafts) root.gmPanelDrafts.discard(form);
    await afterCampaignEdit(data);
    return true;
  }

  async function sendButton(button) {
    if (button.dataset.confirm && root.uiConfirm) {
      const sure = await root.uiConfirm(button.dataset.confirm);
      if (!sure) return false;
    }
    const body = { ...buttonFields(button), change: button.dataset.change };
    const data = await send(button.dataset.url, body, CAMPAIGN_LINES);
    if (!data) return false;
    await afterCampaignEdit(data);
    return true;
  }

  function init() {
    // Delegated from the document: the Campaign tab's contents are replaced
    // whenever it is fetched again, and these must keep working after.
    root.document.addEventListener('submit', (event) => {
      const form = event.target.closest && event.target.closest('.gm-hand-form');
      if (!form) return;
      event.preventDefault();
      submitForm(form);
    });
    root.document.addEventListener('click', (event) => {
      const button = event.target.closest && event.target.closest('.gm-hand-action');
      if (!button) return;
      event.preventDefault();
      pressButton(button);
    });
  }

  const api = { submitForm, pressButton, paintHeld, init };
  root.gmRecords = api;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
