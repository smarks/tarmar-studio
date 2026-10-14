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
 * Whatever the reader has typed or opened in a tab is carried through its
 * refetch by gm-panel-drafts.js (issue #16), loaded before this file.
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
  require('./gm-panel-drafts.js');
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
    root.pageNavigation.go(`${root.location.pathname}?session=${data.session_id}`);
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
    root.pageNavigation.reload();
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
      // The spend is recorded: its note is not a draft to carry (issue #16).
      root.gmPanelDrafts.discard(form);
      await refreshPanel();
      return true;
    } finally {
      delete form.dataset.spending;
      if (button) button.disabled = false;
    }
  }

  /**
   * Which page of its picker a tab shows, and which pager button has the
   * focus, so the refetched tab shows the same page (issue #16): the server
   * renders the first, and a Game Master three pages into his players'
   * characters was sent back to it on every procedure frame.
   * @param {Element} container
   * @returns {{page: string, direction: (string|undefined)}}
   */
  function readPickerView(container) {
    const picker = container.querySelector('[data-picker]');
    if (!picker) return { page: '1', direction: undefined };
    const active = root.document.activeElement;
    let direction;
    if (active && picker.contains(active) && active.matches('[data-picker-page]')) {
      const buttons = Array.from(picker.querySelectorAll('[data-picker-page]'));
      direction = buttons.indexOf(active) === 0 ? 'previous' : 'next';
    }
    // A page of an earlier search is no page of the one typed since.
    const search = picker.querySelector('[data-picker-search]');
    const typed = search ? search.value.trim() : '';
    const sameSearch = (picker.dataset.pickerShownQuery || '') === typed;
    return { page: sameSearch ? picker.dataset.pickerShownPage || '1' : '1', direction };
  }

  /** A tab with nothing the refetch needs to carry over. */
  const NO_VIEW = { query: '', open: false };

  /**
   * What the Campaign tab's resolved threads show now, so the refetched tab
   * shows the same (issue #982): the first thread listed, which is the
   * earliest page the reader has loaded, as the query that asks the server
   * for every thread from it on, and whether the list is open. Read when the
   * request is sent and again just before the answer replaces the tab, since
   * the reader can load a page or fold the list while it is on its way.
   * @param {Element} container - The Campaign tab's section.
   * @returns {{query: string, open: boolean}}
   */
  function readResolvedView(container) {
    const resolved = container.querySelector('.gm-campaign-resolved');
    const first = resolved && resolved.querySelector('li[data-thread-id]');
    if (!first) return NO_VIEW;
    return {
      query: `?resolved_from=${encodeURIComponent(first.dataset.threadId)}`,
      open: resolved.open,
    };
  }

  /** Put the list back as the reader had it, open or folded. */
  function restoreResolvedView(container, view) {
    const reloaded = container.querySelector('.gm-campaign-resolved');
    if (reloaded) reloaded.open = view.open;
  }

  /**
   * The tabs that refetch themselves while a turn runs: the element whose
   * contents are replaced, the config key holding its URL template, and what
   * to call it when a refresh fails. The Campaign tab joined the Characters
   * tab here (issue #579) -- where the party is and what state a thing is in
   * change mid-turn exactly as a character does. ``keepView`` is what a tab
   * carries through its own refetch (issue #982).
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
      keepView: { read: readResolvedView, restore: restoreResolvedView },
    },
  ];

  const refreshing = new Map();
  const refreshAgain = new Set();

  /**
   * Replace one tab's contents with the server's current rendering of them.
   * Refreshes asked for while one is in flight collapse into one more, so the
   * last frame of a turn is always reflected. A failure leaves the tab as it
   * was and says so in the tab (no transient UI).
   * @param {{containerId: string, urlKey: string, called: string, errorId: string, keepView: ({read: function(Element): {query: string, open: boolean}, restore: function(Element, {query: string, open: boolean}): void})|undefined}} tab
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
    // What the reader has opened in the tab is asked for again and put back.
    const readView = tab.keepView ? tab.keepView.read : () => NO_VIEW;
    const sentView = readView(container);
    const url = config[tab.urlKey].replace('{session_id}', String(sessionId)) + sentView.query;
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
          const body = await response.text();
          // The reader may have loaded a page or folded the list while the
          // answer was on its way: read the tab as it is now. An answer to a
          // cursor that has since moved is not applied, it would take the
          // newly loaded page away; the tab is asked again instead.
          const liveView = readView(container);
          if (liveView.query !== sentView.query) {
            refreshAgain.add(tab.containerId);
          } else {
            // What the reader typed, opened or was typing in goes back on
            // the forms of the same records (issue #16): read with nothing
            // in between, so not a keystroke is missed.
            const drafts = root.gmPanelDrafts.read(container);
            const pickerView = readPickerView(container);
            container.innerHTML = body;
            if (tab.keepView) tab.keepView.restore(container, liveView);
            root.gmPanelDrafts.restore(container, drafts);
            if (root.picker) {
              root.picker.initPickers(container);
              // A search typed into the attach picker is one of those drafts
              // (issue #864), and the page it was on is asked for again with
              // it (issue #16).
              const fresh = container.querySelector('[data-picker]');
              const search = fresh && fresh.querySelector('[data-picker-search]');
              const searched = search && search.value !== search.defaultValue;
              if (fresh && (searched || pickerView.page !== '1')) {
                root.picker.loadPage(fresh, pickerView.page, pickerView.direction);
              }
            }
            replaced = true;
          }
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
