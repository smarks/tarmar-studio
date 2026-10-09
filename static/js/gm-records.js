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
 * until the next attempt (no transient UI).
 *
 * The server decides who may (gm/hand_records.py): these controls are only
 * drawn for a Game Master, and a request from anyone else is refused.
 * postWithCsrf/csrfToken/uiConfirm come from api.js.
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

  async function submitForm(form) {
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
    await afterCampaignEdit(data);
    return true;
  }

  async function pressButton(button) {
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

  const api = { submitForm, pressButton, init };
  root.gmRecords = api;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
