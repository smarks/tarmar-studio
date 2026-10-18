/* The GM page's "Show earlier" controls (issue #972).
 *
 * The chat renders the latest page of a session's turns, and the Campaign tab
 * the latest page of its resolved threads (gm/_history_entries.html,
 * gm/_resolved_threads.html). While anything is left above a page the server
 * renders a control for it: a [data-earlier] block holding a button whose
 * data-earlier-url names the page above, and a [data-earlier-error] line.
 * Pressing the button fetches that page, rendered by the same partial, and
 * puts it in place of the control: the earlier entries, and the next control
 * when there are more. What the reader was looking at stays where it was on
 * screen, and keyboard focus moves to the next control, or to the first entry
 * loaded when nothing is left.
 *
 * Nothing here is transient: the control stays until it is used, and a page
 * that fails to load says so beside it until the next attempt.
 *
 * The Campaign tab is fetched again while a turn runs, carrying the earliest
 * page the reader has loaded (issue #982), so the control pressed can be
 * replaced by the server's copy of it while its page is on the way. The page
 * goes in place of the control on the page when it lands, found by the page
 * it asks for, never into the one pressed if that has gone (issue #14). When
 * the refetched list carries a control for another page instead (a thread
 * was reopened, so the list starts elsewhere), the page that came is not
 * put in, and the reader's press is honoured by loading the page that
 * control asks for. A page asked for twice is fetched once.
 */

(function (root) {
  /** A "Show earlier" control, its button, and its error line. */
  const CONTROL = '[data-earlier]';
  const CONTROL_BUTTON = '[data-earlier-url]';
  const CONTROL_ERROR = '[data-earlier-error]';
  /** The buttons of every control on the page. */
  const CONTROL_BUTTONS = `${CONTROL} ${CONTROL_BUTTON}`;

  /** The loads on their way, by the page each asks for. */
  const loading = new Map();

  /**
   * The element that scrolls the content around ``element``: the chat's
   * message list, the panel, or the page.
   * @param {Element} element
   * @returns {Element}
   */
  function scroller(element) {
    for (let node = element.parentElement; node; node = node.parentElement) {
      const overflow = root.getComputedStyle(node).overflowY;
      if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight) {
        return node;
      }
    }
    return root.document.scrollingElement || root.document.documentElement;
  }

  /**
   * The control on the page now that asks for ``url``: ``control`` while it
   * is there, or the one that replaced it when what holds it was fetched
   * again (issue #14). None when no control asks for that page any more.
   * @param {Element} control - The control that was pressed.
   * @param {string} url - The page it asks for.
   * @returns {Element|null}
   */
  function liveControl(control, url) {
    if (control.isConnected) return control;
    const asking = Array.from(root.document.querySelectorAll(CONTROL_BUTTONS))
      .find((button) => button.dataset.earlierUrl === url);
    return asking ? asking.closest(CONTROL) : null;
  }

  /**
   * The button that took ``button``'s place when its list was fetched again
   * from another thread, so asks for another page: the control of the same
   * focus key (gm/_resolved_threads.html names its control), or none.
   * @param {HTMLButtonElement} button - The button that was pressed.
   * @returns {HTMLButtonElement|null}
   */
  function replacingButton(button) {
    const key = button.dataset.focusKey;
    if (!key) return null;
    return Array.from(root.document.querySelectorAll(CONTROL_BUTTONS))
      .find((candidate) => candidate.dataset.focusKey === key) || null;
  }

  /** Write a control's error line; an empty message hides it. */
  function setError(control, message) {
    const line = control.querySelector(CONTROL_ERROR);
    if (!line) return;
    line.textContent = message || '';
    line.hidden = !message;
  }

  /**
   * Render the GM replies in a loaded page as the page renders its own:
   * through chat.js's renderMarkdown, which sanitizes.
   * @param {DocumentFragment} fragment
   */
  function renderReplies(fragment) {
    if (typeof root.renderMarkdown !== 'function') return;
    fragment.querySelectorAll('[data-markdown]').forEach((element) => {
      element.innerHTML = root.renderMarkdown(element.textContent);
    });
  }

  /**
   * Load the page above a control and put it in the control's place. A page
   * already on its way is not asked for again: the press waits for it.
   * @param {HTMLButtonElement} button - The control's button.
   * @returns {Promise<boolean>} Whether the page was put in place.
   */
  function loadEarlier(button) {
    const pressed = button.closest(CONTROL);
    if (!pressed || button.disabled) return Promise.resolve(false);
    const url = button.dataset.earlierUrl;
    if (!loading.has(url)) {
      const pending = fetchAndPlace(button, pressed, url).finally(() => {
        // Only its own entry: a load handed on has already left the map,
        // and a later ask for this page may have taken the place.
        if (loading.get(url) === pending) loading.delete(url);
      });
      loading.set(url, pending);
    }
    return loading.get(url);
  }

  /**
   * Fetch the page ``url`` names and put it where the control asking for it
   * is when it lands.
   * @param {HTMLButtonElement} button - The button pressed.
   * @param {Element} pressed - Its control.
   * @param {string} url - The page it asks for.
   * @returns {Promise<boolean>} Whether a page was put in place.
   */
  async function fetchAndPlace(button, pressed, url) {
    button.disabled = true;
    setError(pressed, '');
    let html;
    let failure = '';
    try {
      const response = await fetch(url, { headers: { Accept: 'text/html' } });
      if (response.ok) {
        html = await response.text();
      } else {
        failure = response.statusText || String(response.status);
      }
    } catch (error) {
      failure = error.message;
    } finally {
      button.disabled = false;
    }
    // Looked up after the wait: the pressed control may have been replaced.
    const control = liveControl(pressed, url);
    if (!control) {
      // The list was fetched again from another thread: this page may no
      // longer sit above it, so it is not put in, but the press stands.
      // This page is no longer on its way: a control that asks for it from
      // now on starts its own load rather than waiting on another page's.
      loading.delete(url);
      const replacing = replacingButton(button);
      if (!replacing) return false;
      return loading.get(replacing.dataset.earlierUrl) || loadEarlier(replacing);
    }
    if (html === undefined) {
      setError(control, `The earlier page could not be loaded: ${failure}`);
      return false;
    }
    const template = root.document.createElement('template');
    template.innerHTML = html;
    const fragment = template.content;
    renderReplies(fragment);
    const loaded = Array.from(fragment.children);
    const nextButton = fragment.querySelector(CONTROL_BUTTONS);
    const scrolling = scroller(control);
    const heightBefore = scrolling.scrollHeight;
    const topBefore = scrolling.scrollTop;
    // The focus follows the page only from where the press left it: a page
    // that landed after a whole turn must not take it from the message box.
    const focused = root.document.activeElement;
    const focusFollows = !focused || focused === root.document.body
      || focused === button || control.contains(focused);
    control.replaceWith(fragment);
    // What the reader was looking at stays put: everything added is above it.
    scrolling.scrollTop = topBefore + (scrolling.scrollHeight - heightBefore);
    if (!focusFollows) return true;
    if (nextButton) {
      nextButton.focus();
    } else if (loaded.length) {
      loaded[0].setAttribute('tabindex', '-1');
      loaded[0].focus({ preventScroll: true });
    }
    return true;
  }

  function init() {
    root.document.addEventListener('click', (event) => {
      const button = event.target.closest && event.target.closest(CONTROL_BUTTON);
      if (!button) return;
      event.preventDefault();
      loadEarlier(button);
    });
  }

  const api = { loadEarlier, init };
  root.gmHistory = api;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
