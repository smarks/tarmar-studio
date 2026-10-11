/* GM side panel — the Characters tab (issues #341, #571).
 *
 * The tab's markup is gm/templates/gm/_panel_characters.html: a picker of the
 * player's living characters and the attached characters' sheets. This file
 * wires the two controls to the attach/detach endpoints and then reloads the
 * page, so the sheets on screen are always what the server rendered from the
 * model — never a client-side reconstruction that could drift from what the
 * GM was told.
 *
 * While a turn runs, the tab refetches its contents from the server
 * (charactersPanelUrlTemplate, campaignPanelUrlTemplate) whenever chat.js
 * reports a procedure frame
 * (`gm-stream-procedure`) or the done frame (`gm-stream-done`): a character
 * the GM just started or changed is a saved record, and the pane shows it as
 * the server renders it, without a reload (issue #571). It refetches as well
 * whenever the Battle tab's board applies an advance, a move or a restart
 * (battle.js's `battle-applied`): a session fight writes each wound and death
 * to the character's record as the turn commits (issues #750, #751), and the
 * sheets would otherwise show the old pools until the next chat message.
 *
 * Reads #gm-panel-config (attachUrl, detachUrl, charactersPanelUrlTemplate,
 * campaignPanelUrlTemplate,
 * sessionId); the CSRF token
 * comes from the page, through api.js's csrfToken().
 * postWithCsrf comes from api.js, loaded in <head> by base.html; under Jest
 * it is required here so the global exists.
 */
if (typeof module !== 'undefined' && module.exports) {
  require('./api.js');
}

(function (root) {
  const configElement = root.document.getElementById('gm-panel-config');
  const config = configElement ? JSON.parse(configElement.textContent) : {};

  /**
   * The session the panel acts on. A new session gains its id when the
   * first message is sent — chat.js writes it into the URL without a reload
   * — so the URL is consulted ahead of the id the page loaded with.
   * @returns {number|null}
   */
  function currentSessionId() {
    const fromUrl = new URLSearchParams(root.location.search || '').get('session');
    if (fromUrl && /^\d+$/.test(fromUrl)) return Number(fromUrl);
    return config.sessionId || null;
  }

  function errorElement() {
    return root.document.getElementById('gm-panel-characters-error');
  }

  /**
   * Show an error in the tab. It stays until the next attach or detach
   * clears it — no auto-dismiss (house rule: no transient UI).
   * @param {string} message
   */
  /**
   * Put a message in a tab's own error element, where it stays until the
   * next action clears it (no toasts).
   *
   * Each refreshable tab owns its element: refreshing one replaces
   * everything inside it, so a message written into the Characters tab
   * would be wiped the moment the Characters tab finished refreshing --
   * which is exactly when a campaign failure is reported (issue #579).
   * @param {string} message
   * @param {string} [elementId] The tab's error element; the Characters
   *   tab's by default.
   */
  function showError(message, elementId) {
    const element = elementId
      ? root.document.getElementById(elementId)
      : errorElement();
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
  }

  function clearError() {
    const element = errorElement();
    if (!element) return;
    element.textContent = '';
    element.hidden = true;
  }

  /**
   * POST to one of the endpoints and report failure in the tab.
   * @param {string} url
   * @param {object} body
   * @returns {Promise<object|null>} The JSON answer, or null on failure.
   */
  async function post(url, body) {
    clearError();
    let response;
    let data;
    try {
      response = await postWithCsrf(url, csrfToken(), body);
      data = await response.json();
    } catch (error) {
      showError(`Request failed: ${error.message}`);
      return null;
    }
    if (!response.ok || !data.ok) {
      showError(data.error || response.statusText);
      return null;
    }
    return data;
  }

  /**
   * Attach a character to the session (creating the session if the chat has
   * none yet) and reload on that session.
   * @param {number|string} characterId
   */
  async function attachCharacter(characterId) {
    const data = await post(config.attachUrl, {
      session_id: currentSessionId(),
      character_id: Number(characterId),
    });
    if (!data) return false;
    root.location.href = `${root.location.pathname}?session=${data.session_id}`;
    return true;
  }

  /**
   * Detach a character from the session and reload.
   * @param {number|string} characterId
   */
  async function detachCharacter(characterId) {
    const sessionId = currentSessionId();
    if (!sessionId) {
      showError('No active session.');
      return false;
    }
    const data = await post(config.detachUrl, {
      session_id: sessionId,
      character_id: Number(characterId),
    });
    if (!data) return false;
    root.location.reload();
    return true;
  }

  /**
   * Spend one of a character's Luck uses in this session (issue #861), then
   * refetch the tab so the count is the recorded one. A refusal (a player
   * past their level) stays in the tab's error line.
   * @param {HTMLFormElement} form - A `.gm-luck-form` from gm/_luck_control.html.
   * @returns {Promise<boolean>} Whether the spend was recorded.
   */
  async function spendLuck(form) {
    // One spend at a time per control: a double-click must not spend twice
    // (the #861 review). The button stays disabled until the answer lands.
    if (form.dataset.spending === 'true') return false;
    form.dataset.spending = 'true';
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    const roll = form.querySelector('.gm-luck-roll');
    try {
      const data = await post(form.getAttribute('data-luck-url'), {
        character_id: Number(form.getAttribute('data-character-id')),
        roll: roll ? roll.value : '',
      });
      if (!data) return false;
      await refreshPanel();
      return true;
    } finally {
      delete form.dataset.spending;
      if (button) button.disabled = false;
    }
  }

  /**
   * The tabs that refetch themselves while a turn runs: the element whose
   * contents are replaced, the config key holding its URL template, and what
   * to call it when a refresh fails. The Campaign tab joined the Characters
   * tab here (issue #579) -- where the party is and what state a thing is in
   * change mid-turn exactly as a character does.
   */
  const REFRESHABLE = [
    {
      containerId: 'gm-panel-characters',
      urlKey: 'charactersPanelUrlTemplate',
      called: 'characters',
      errorId: 'gm-panel-characters-error',
    },
    {
      containerId: 'gm-panel-campaign',
      urlKey: 'campaignPanelUrlTemplate',
      called: 'campaign',
      errorId: 'gm-panel-campaign-error',
    },
  ];

  const refreshing = new Map();
  const refreshAgain = new Set();

  /**
   * Replace one tab's contents with the server's current rendering of them.
   * Refreshes asked for while one is in flight collapse into one more, so the
   * last frame of a turn is always reflected. A failure leaves the tab as it
   * was and says so in the tab (no transient UI).
   * @param {{containerId: string, urlKey: string, called: string, errorId: string}} tab
   * @returns {Promise<boolean>} Whether the contents were replaced.
   */
  async function refreshOne(tab) {
    const sessionId = currentSessionId();
    const container = root.document.getElementById(tab.containerId);
    if (!sessionId || !container || !config[tab.urlKey]) return false;
    if (refreshing.has(tab.containerId)) {
      refreshAgain.add(tab.containerId);
      return refreshing.get(tab.containerId);
    }
    const url = config[tab.urlKey].replace('{session_id}', String(sessionId));
    const inFlight = (async () => {
      let replaced = false;
      try {
        const response = await fetch(url, { headers: { Accept: 'text/html' } });
        if (!response.ok) {
          showError(
            `The ${tab.called} could not be refreshed: ${response.statusText}`,
            tab.errorId,
          );
        } else {
          // A search typed into the attach picker survives the refresh
          // (issue #864): the fresh picker is wired and asked again.
          const typed = container.querySelector('[data-picker-search]');
          const query = typed ? typed.value : '';
          container.innerHTML = await response.text();
          if (root.picker) {
            root.picker.initPickers(container);
            const fresh = container.querySelector('[data-picker]');
            const search = fresh && fresh.querySelector('[data-picker-search]');
            if (search && query) {
              search.value = query;
              root.picker.loadPage(fresh, 1);
            }
          }
          replaced = true;
        }
      } catch (error) {
        showError(
          `The ${tab.called} could not be refreshed: ${error.message}`,
          tab.errorId,
        );
      }
      return replaced;
    })();
    refreshing.set(tab.containerId, inFlight);
    const result = await inFlight;
    refreshing.delete(tab.containerId);
    if (refreshAgain.has(tab.containerId)) {
      refreshAgain.delete(tab.containerId);
      return refreshOne(tab);
    }
    return result;
  }

  /**
   * Refresh every tab that refetches itself.
   * @returns {Promise<boolean>} Whether any tab's contents were replaced.
   */
  async function refreshPanel() {
    const replaced = await Promise.all(REFRESHABLE.map(refreshOne));
    return replaced.some(Boolean);
  }

  function init() {
    // Delegated from the tab's section, which stays in place when a refresh
    // replaces everything inside it.
    const container = root.document.getElementById('gm-panel-characters');
    if (container) {
      container.addEventListener('submit', (event) => {
        const luckForm = event.target.closest('.gm-luck-form');
        if (luckForm) {
          event.preventDefault();
          spendLuck(luckForm);
          return;
        }
        if (!event.target.closest('#gm-character-picker')) return;
        event.preventDefault();
        const select = root.document.getElementById('gm-character-select');
        if (!select || !select.value) {
          showError('Pick a character first.');
          return;
        }
        attachCharacter(select.value);
      });
      container.addEventListener('click', (event) => {
        const button = event.target.closest('.gm-sheet-detach');
        if (!button) return;
        detachCharacter(button.getAttribute('data-character-id'));
      });
    }
    root.document.addEventListener('gm-stream-procedure', () => { refreshPanel(); });
    root.document.addEventListener('gm-stream-done', () => { refreshPanel(); });
    root.document.addEventListener('battle-applied', () => { refreshPanel(); });
  }

  const api = {
    attachCharacter, detachCharacter, currentSessionId, refreshPanel, spendLuck, init,
  };
  root.gmPanelCharacters = api;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
