/* GM side panel — the Battle tab (issue #345).
 *
 * The tab's markup is gm/templates/gm/_panel_battle.html: a fight form and,
 * once a fight is on, the battle app's own arena (battle/_arena.html), which
 * battle.js drives exactly as it drives the battle page — this file never
 * touches the board. It owns one thing: starting a fight. The tab's form,
 * the command bar's Fight button and chat.js's /fight command all come
 * through startFight(foesText), which posts to the fight endpoint and
 * reloads the page on the session, so the board on screen is what the
 * server rendered. While the request runs (asking the model who the party
 * faces can take a while) the tab says so; a failure stays in the tab's
 * error line (no toasts). Advance, the player's turn menu, restart and the
 * poll are battle.js's, found by id within the tab at load.
 *
 * The panel's width is the viewer's now, not this tab's: it used to widen
 * the panel for the hex board, and issue #351 replaced that with a panel
 * the viewer drags to whatever width the map deserves.
 *
 * Reads #gm-panel-config (sessionId — the block the Characters tab reads —
 * and the two error-page messages, fightTimeoutMessage and
 * fightErrorPageMessage)
 * and the fight endpoint from the form's data-fight-url; the CSRF token comes
 * from the page, through api.js's csrfToken().
 * postWithCsrf/csrfToken come from api.js; gmPanel from gm-panel.js.
 */
if (typeof module !== 'undefined' && module.exports) {
  require('./api.js');
}

(function (root) {
  const configElement = root.document.getElementById('gm-panel-config');
  const config = configElement ? JSON.parse(configElement.textContent) : {};
  const TAB_NAME = 'battle';

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

  /**
   * Start a fight from the session's attached characters.
   * @param {string} [foesText] - Who the party faces, in the player's words
   *   ("two gray wolves, a dire wolf"); empty asks the GM.
   * @returns {Promise<boolean>} true when the page is reloading on the fight.
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
    root.pageNavigation.go(`${root.location.pathname}?session=${data.session_id}`);
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
    const form = byId('gm-fight-form');
    const input = byId('gm-fight-foes');
    if (form) {
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        startFight(input ? input.value : '');
      });
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
    startFight, showVerdict, currentSessionId, init, paintFightForm, paintBareHanded,
  };
  root.gmPanelBattle = api;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
