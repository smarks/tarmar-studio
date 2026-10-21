/* GM side panel — the Battle tab (issue #345).
 *
 * The tab's markup is gm/templates/gm/_panel_battle.html: a fight form and,
 * once a fight is on, the battle app's own arena (battle/_arena.html), which
 * battle.js drives exactly as it drives the battle page — this file never
 * touches the board. It owns starting a fight, and drawing the tab again.
 * The tab's form, the command bar's Fight button and chat.js's /fight
 * command all come through startFight(foesText), which posts to the fight
 * endpoint. The fight's board is then drawn in place (issue #89): the tab
 * as the server renders it (gm:battle_panel, the partial the page renders),
 * wired by battle.js as the page's own arena is, and the lines the fight
 * wrote into the chat, through chat.js. A reload did this before, and threw
 * away every earlier turn the reader had loaded. While the request runs
 * (asking the model who the party faces can take a while) the tab says so;
 * a failure stays in the tab's error line (no toasts). Advance, the
 * player's turn menu, restart and the poll are battle.js's, found by id
 * within the tab.
 *
 * The tab is drawn again the same way when battle.js's poll finds the board
 * restarted elsewhere (its REWOUND_EVENT), and when a Game Master's ruling
 * form in the board (HTH by agreement, magic bounds, the stale pools'
 * resnapshot) is posted: each posts as a script caller, which the battle
 * endpoints answer in JSON, rather than as a form the server answers with a
 * reload of the page.
 *
 * The panel's width is the viewer's now, not this tab's: it used to widen
 * the panel for the hex board, and issue #351 replaced that with a panel
 * the viewer drags to whatever width the map deserves.
 *
 * Reads #gm-panel-config (sessionId and battlePanelUrlTemplate — the block
 * the Characters tab reads — and the two error-page messages,
 * fightTimeoutMessage and fightErrorPageMessage) and the fight endpoint from
 * the form's data-fight-url; the CSRF token comes from the page, through
 * api.js's csrfToken().
 * postWithCsrf/csrfToken come from api.js; gmPanel from gm-panel.js.
 */
if (typeof module !== 'undefined' && module.exports) {
  require('./api.js');
  require('./gm-panel-drafts.js');
}

(function (root) {
  const configElement = root.document.getElementById('gm-panel-config');
  const config = configElement ? JSON.parse(configElement.textContent) : {};
  const TAB_NAME = 'battle';
  /** The tab's section, which stays in place while its contents are drawn again. */
  const SECTION_ID = 'gm-panel-battle';
  /** The section's attributes a fresh rendering carries over (gm-panel-layout.js reads them). */
  const POPOUT_ATTRIBUTES = ['data-popout-url', 'data-popout-empty'];
  /** The board's ruling forms, which post as a script caller (issue #89). */
  const RULING_FORMS = '#gm-battle form[method="post"]';

  function byId(id) {
    return root.document.getElementById(id);
  }

  /** The session the fight belongs to: the URL first, then the page's. */
  function currentSessionId() {
    if (root.gmPanel && root.gmPanel.currentSessionId) {
      return root.gmPanel.currentSessionId(config.sessionId || null);
    }
    return config.sessionId || null;
  }

  /**
   * Show a line in the tab. Both lines stay until the next attempt clears
   * them — no auto-dismiss (house rule: no transient UI).
   * @param {'status'|'error'} kind
   * @param {string} message
   */
  function show(kind, message) {
    const element = byId(`gm-panel-battle-${kind}`);
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
  }

  function clear(kind) {
    const element = byId(`gm-panel-battle-${kind}`);
    if (!element) return;
    element.textContent = '';
    element.hidden = true;
  }

  function setBusy(busy) {
    const button = byId('gm-fight-start');
    const input = byId('gm-fight-foes');
    const commandButton = byId('gm-fight-button');
    [button, input, commandButton].forEach((element) => {
      if (element) element.disabled = busy;
    });
  }

  /** What the Fight button says while a fight is on the board. */
  const FIGHT_UNDER_WAY = 'A fight is already under way';
  /** What the Battle tab's button says while a fight is on the board. */
  const TAB_LIVE = 'A fight is under way';

  /**
   * Paint the tab's Fight form, and the tab's own live mark, from whether
   * the board's fight is still on (issue #868). The page renders it disabled while a fight is live; the
   * board's responses carry `live`, so when the fight ends the form opens
   * without a reload. A response with no `live` (an older payload) leaves
   * the form as it is.
   * @param {boolean|undefined} live
   */
  function paintFightForm(live) {
    if (typeof live !== 'boolean') return;
    const button = byId('gm-fight-start');
    const input = byId('gm-fight-foes');
    if (input) input.disabled = live;
    if (button) {
      button.disabled = live;
      if (live) {
        button.title = FIGHT_UNDER_WAY;
      } else {
        button.removeAttribute('title');
      }
    }
    // The tab's own mark, rendered from the same state (the sweep of #868's
    // class): lit while a fight is on, quiet once it ends.
    const tab = byId('gm-panel-tab-battle');
    if (tab) {
      tab.classList.toggle('is-live', live);
      if (live) {
        tab.title = TAB_LIVE;
      } else {
        tab.removeAttribute('title');
      }
    }
  }

  /**
   * Paint the line naming who fights bare-handed (issue #844) from the
   * board's response: the server's sentence, or nothing once no figure is
   * bare-handed or the fight is over. A response without it (an older
   * payload) leaves the line as it is.
   * @param {string|undefined} sentence
   */
  function paintBareHanded(sentence) {
    if (typeof sentence !== 'string') return;
    const line = byId('gm-panel-unarmed');
    if (!line) return;
    line.textContent = sentence;
    line.hidden = !sentence;
  }

  /**
   * What to say about a fight request answered with something other than
   * the app's JSON (issue #936), with the HTTP status in it.
   * @param {Response} response - The answer that would not parse.
   * @returns {string} the line for the tab's error.
   */
  function withoutJson(response) {
    // The server's words (settings GM_FIGHT_GATEWAY_TIMEOUT_MESSAGE and
    // GM_FIGHT_ERROR_PAGE_MESSAGE, #94), from the panel's config block.
    if (response.status === 504) {
      return root.filledWords(config.fightTimeoutMessage);
    }
    return root.filledWords(config.fightErrorPageMessage, { status: response.status });
  }

  /** Bring the tab into view: select it, and open the panel on a phone. */
  function showTab() {
    if (!root.gmPanel) return;
    root.gmPanel.activate(TAB_NAME);
    root.gmPanel.setOpen(true);
  }

  /** The draw of the tab on its way, and whether another was asked for meanwhile. */
  let drawing = null;
  let drawAgain = false;

  /**
   * Draw the tab again as the server renders it now (issue #89): the
   * section's contents and its pop-out attributes from gm:battle_panel, the
   * forms pointed back at this page, the new arena wired by battle.js, and
   * the tab's live mark and the pane's pop-out control painted from it. The
   * arena it replaces stops polling once it is off the page (battle.js).
   * Draws asked for while one is on its way collapse into one more.
   * @returns {Promise<boolean>} false when there is no session or no tab.
   * @throws {Error} when the tab could not be fetched; the tab is left as it was.
   */
  async function drawTab() {
    if (drawing) {
      drawAgain = true;
      return drawing;
    }
    drawing = fetchAndDrawTab();
    let drawn;
    try {
      drawn = await drawing;
    } catch (error) {
      // Every caller waiting on this draw hears why it failed.
      drawAgain = false;
      throw error;
    } finally {
      drawing = null;
    }
    if (!drawAgain) return drawn;
    drawAgain = false;
    return drawTab();
  }

  async function fetchAndDrawTab() {
    const sessionId = currentSessionId();
    const section = byId(SECTION_ID);
    if (!sessionId || !section || !config.battlePanelUrlTemplate) return false;
    const url = config.battlePanelUrlTemplate.replace('{session_id}', String(sessionId));
    const response = await fetch(url, { headers: { Accept: 'text/html' } });
    if (!response.ok) throw new Error(response.statusText || `HTTP ${response.status}`);
    const holder = root.document.createElement('template');
    holder.innerHTML = (await response.text()).trim();
    const fresh = holder.content.querySelector(`#${SECTION_ID}`);
    if (!fresh) throw new Error('the answer held no Battle tab');
    POPOUT_ATTRIBUTES.forEach((name) => {
      if (fresh.hasAttribute(name)) {
        section.setAttribute(name, fresh.getAttribute(name));
      } else {
        section.removeAttribute(name);
      }
    });
    // Where the keyboard focus is in the tab (a ruling's button just
    // pressed) goes back on the same control, by its focus key or id, the
    // way the refetched tabs carry it (gm-panel-drafts.js).
    const carried = root.gmPanelDrafts.read(section);
    section.replaceChildren(...fresh.childNodes);
    // Rendered under the fragment's address, the forms would go back there.
    const here = `${root.location.pathname}${root.location.search}`;
    section.querySelectorAll('input[name="next"]').forEach((input) => {
      input.value = here;
    });
    if (typeof root.applyLocalTimes === 'function') root.applyLocalTimes(section);
    if (root.battlePage) root.battlePage.initBattlePage(section);
    root.gmPanelDrafts.restore(section, carried);
    const form = byId('gm-fight-form');
    if (form) paintFightForm(form.dataset.live === 'true');
    const panes = root.gmPanelLayout;
    if (panes && panes.isActive()) panes.paintPopouts();
    return true;
  }

  /**
   * Show what a change on the board left behind, in place: the tab drawn
   * again and, when the change wrote into the chat, the conversation as
   * stored (chat.js's showConversationAsStored).
   * @param {boolean} wroteToChat
   * @returns {Promise<boolean>} Whether everything was shown.
   */
  async function showBoardChange(wroteToChat) {
    const asked = [drawTab()];
    if (wroteToChat && typeof root.showConversationAsStored === 'function') {
      asked.push(root.showConversationAsStored());
    }
    try {
      await Promise.all(asked);
      return true;
    } catch (error) {
      show('error', `The board changed, but the page could not show it: ${error.message}`);
      return false;
    }
  }

  /**
   * Post one of the board's ruling forms as a script caller (issue #89),
   * then draw the tab again. A refusal stays in the tab's error line.
   * @param {HTMLFormElement} form
   * @param {HTMLElement|null} submitter - The button pressed, whose name
   *   and value are part of what the form says.
   * @returns {Promise<boolean>} Whether the ruling was recorded.
   */
  async function postRuling(form, submitter) {
    clear('error');
    const body = new FormData(form);
    if (submitter && submitter.name) body.append(submitter.name, submitter.value);
    // One post per press: the button waits until the tab is drawn from the
    // answer, which brings a fresh one.
    if (submitter) submitter.disabled = true;
    try {
      return await postAndDraw(form, body);
    } finally {
      if (submitter) submitter.disabled = false;
    }
  }

  /** postRuling's request, and the tab drawn from it once it is recorded. */
  async function postAndDraw(form, body) {
    let data;
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body,
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken(), 'X-Requested-With': 'XMLHttpRequest' },
      });
      data = await response.json().catch(() => ({}));
      if (!response.ok) {
        show('error', data.error || response.statusText || `HTTP ${response.status}`);
        return false;
      }
    } catch (error) {
      show('error', `Request failed: ${error.message}`);
      return false;
    }
    return showBoardChange(false);
  }

  /**
   * Start a fight from the session's attached characters.
   * @param {string} [foesText] - Who the party faces, in the player's words
   *   ("two gray wolves, a dire wolf"); empty asks the GM.
   * @returns {Promise<boolean>} true when the fight started.
   */
  async function startFight(foesText) {
    const foes = (foesText || '').trim();
    showTab();
    clear('error');
    const sessionId = currentSessionId();
    if (!sessionId) {
      show('error', 'Attach a character or send a message first.');
      return false;
    }
    const form = byId('gm-fight-form');
    const fightUrl = form ? form.getAttribute('data-fight-url') : '';
    if (!fightUrl) {
      show('error', 'This page has no fight endpoint.');
      return false;
    }
    setBusy(true);
    show('status', foes ? 'Setting the board…' : 'Asking the GM who you face…');
    let response;
    try {
      response = await postWithCsrf(fightUrl, csrfToken(), {
        session_id: sessionId,
        foes,
      });
    } catch (error) {
      clear('status');
      show('error', `Request failed: ${error.message}`);
      setBusy(false);
      return false;
    }
    let data;
    try {
      data = await response.json();
    } catch (notJson) {
      // A page the proxy wrote, not the app: nginx answers a request that
      // outran its proxy_read_timeout with an HTML 504 (issue #936).
      clear('status');
      show('error', withoutJson(response));
      setBusy(false);
      return false;
    }
    if (!response.ok || !data.ok) {
      clear('status');
      show('error', data.error || response.statusText);
      setBusy(false);
      return false;
    }
    show('status', `The board is set: ${data.battle_name}. Loading…`);
    // The fight writes its opening lines into the chat, and the board is
    // the session's now: both are shown in place (issue #89). The tab's own
    // form comes back as the server renders it, closed while the fight is on.
    const shown = await showBoardChange(true);
    if (!shown) {
      // The fight is on whether or not its board could be shown.
      clear('status');
      paintFightForm(true);
    }
    const commandButton = byId('gm-fight-button');
    if (commandButton) commandButton.disabled = false;
    return true;
  }

  /** Statuses in which the board can go no further (battle.js keeps the same). */
  const TERMINAL_STATUSES = ['finished', 'stalemate'];
  let verdictAsked = null;

  /** What the chat calls a notice the app writes, as gm/chat.html does. */
  const NOTICE_LABEL = 'Notice';

  /**
   * Show how the fight ended, in the tab and in the chat (issue #785).
   *
   * The server files the notice into the session when the turn that ends
   * the fight commits; this only reads it back, so the page shows it
   * without a reload. A reload renders the same notice from the session,
   * so nothing here is transient.
   * @returns {Promise<boolean>} whether a verdict was shown
   */
  async function showVerdict() {
    if (verdictAsked) return verdictAsked;
    const form = byId('gm-fight-form');
    const url = form ? form.getAttribute('data-verdict-url') : '';
    const sessionId = currentSessionId();
    if (!url || !sessionId) return false;
    verdictAsked = (async () => {
      let data;
      try {
        const query = new URLSearchParams({ session_id: String(sessionId) });
        const response = await fetch(`${url}?${query}`, { headers: { Accept: 'application/json' } });
        data = await response.json();
        if (!response.ok || !data.ok) {
          show('error', `How the fight ended could not be shown: ${data.error || response.statusText}`);
          return false;
        }
      } catch (error) {
        show('error', `How the fight ended could not be shown: ${error.message}`);
        return false;
      }
      if (!data.verdict) return false;
      const verdict = byId('gm-battle-verdict');
      if (verdict) {
        verdict.textContent = data.verdict;
        verdict.hidden = false;
      }
      const messages = byId('messages');
      const shown = root.document.querySelector(
        `[data-battle-verdict="${String(data.notice)}"]`,
      );
      if (messages && !shown) {
        // The app speaking, not the Game Master (issue #339, item 11).
        const notice = root.document.createElement('div');
        notice.className = 'message assistant notice';
        // One notice per ending: battle and run (review of #785).
        notice.dataset.battleVerdict = String(data.notice);
        const role = root.document.createElement('div');
        role.className = 'message-role';
        role.textContent = NOTICE_LABEL;
        const content = root.document.createElement('div');
        content.className = 'message-content';
        content.textContent = data.verdict;
        notice.append(role, content);
        messages.append(notice);
        messages.scrollTop = messages.scrollHeight;
      }
      return true;
    })();
    // Only a request in flight is shared: a restart played to a new end asks
    // again, and the chat holds each notice once by its key.
    const shownNow = await verdictAsked;
    verdictAsked = null;
    return shownNow;
  }

  function init() {
    // battle.js announces every change to the board; the one that ends the
    // fight is the one the chat must report (issue #785).
    root.document.addEventListener('battle-applied', (event) => {
      const status = event.detail && event.detail.status;
      if (TERMINAL_STATUSES.includes(status)) showVerdict();
    });
    // Delegated from the section, which stays in place when the tab is
    // drawn again around it (issue #89).
    const section = byId(SECTION_ID);
    if (section) {
      section.addEventListener('submit', (event) => {
        const form = event.target;
        if (form.id === 'gm-fight-form') {
          event.preventDefault();
          const input = byId('gm-fight-foes');
          startFight(input ? input.value : '');
          return;
        }
        if (!form.matches(RULING_FORMS)) return;
        event.preventDefault();
        postRuling(form, event.submitter || null);
      });
      // A restart made elsewhere: the board is drawn again here, not the page.
      if (root.battlePage) {
        section.addEventListener(root.battlePage.REWOUND_EVENT, (event) => {
          event.preventDefault();
          showBoardChange(false);
        });
      }
    }
    const commandButton = byId('gm-fight-button');
    if (commandButton) {
      commandButton.addEventListener('click', () => startFight(''));
    }
    // battle.js announces every response it applies to the board.
    root.document.addEventListener('battle-applied', (event) => {
      const detail = event.detail || {};
      paintFightForm(detail.live);
      paintBareHanded(detail.bareHanded);
    });
  }

  const api = {
    startFight, showVerdict, currentSessionId, init, paintFightForm, paintBareHanded, drawTab,
  };
  root.gmPanelBattle = api;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
