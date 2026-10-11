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
 */

(function (root) {
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

  /** Write a control's error line; an empty message hides it. */
  function setError(control, message) {
    const line = control.querySelector('[data-earlier-error]');
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
   * Load the page above a control and put it in the control's place.
   * @param {HTMLButtonElement} button - The control's button.
   * @returns {Promise<boolean>} Whether the page was put in place.
   */
  async function loadEarlier(button) {
    const control = button.closest('[data-earlier]');
    if (!control || button.disabled) return false;
    button.disabled = true;
    setError(control, '');
    let html;
    try {
      const response = await fetch(button.dataset.earlierUrl, { headers: { Accept: 'text/html' } });
      if (!response.ok) {
        setError(control, `The earlier page could not be loaded: ${response.statusText || response.status}`);
        return false;
      }
      html = await response.text();
    } catch (error) {
      setError(control, `The earlier page could not be loaded: ${error.message}`);
      return false;
    } finally {
      button.disabled = false;
    }
    const template = root.document.createElement('template');
    template.innerHTML = html;
    const fragment = template.content;
    renderReplies(fragment);
    const loaded = Array.from(fragment.children);
    const nextButton = fragment.querySelector('[data-earlier] [data-earlier-url]');
    const scrolling = scroller(control);
    const heightBefore = scrolling.scrollHeight;
    const topBefore = scrolling.scrollTop;
    control.replaceWith(fragment);
    // What the reader was looking at stays put: everything added is above it.
    scrolling.scrollTop = topBefore + (scrolling.scrollHeight - heightBefore);
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
      const button = event.target.closest && event.target.closest('[data-earlier-url]');
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
