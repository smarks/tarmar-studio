/* GM side panel — the tab strip (issue #341).
 *
 * gm/chat.html renders <aside id="gm-panel"> with a row of tab buttons and
 * one container per tab. This file owns nothing but the switching: which
 * container is shown, which button reads as selected, and the small-screen
 * open/close toggle. What a tab *does* lives in its own file
 * (gm-panel-characters.js for the Characters tab; Map and Battle follow).
 *
 * A tab is a button plus a container. Two ways to register one:
 *
 *   1. Markup only (the usual way). Give the button
 *      data-panel-tab="<name>" and aria-controls="<container id>", give the
 *      container data-panel-container="<name>" (and `hidden`), and this
 *      file picks both up on load.
 *   2. From script, for a tab built after load:
 *      window.gmPanel.registerTab('map', buttonElement, containerElement)
 *
 * The first tab registered is shown unless the viewer opened another one
 * last time (remembered per browser in localStorage). Everything here is
 * plain DOM: no innerHTML, nothing model- or user-derived.
 *
 * Where gm-panel-layout.js is loaded too (issue #351) it takes over what is
 * shown: the tabs become panes the viewer can stack, collapse, resize and
 * pop out. This file still owns the registry and the strip, and hands the
 * display over — activate() reveals a pane rather than hiding its
 * neighbours, and a tab click shows that one alone, as it always did.
 */
(function (root) {
  const STORAGE_KEY = 'gm-panel-tab';
  const OPEN_CLASS = 'gm-panel-open';

  /** @type {Map<string, {button: HTMLElement, container: HTMLElement}>} */
  const tabs = new Map();
  let activeName = null;

  function rememberedTab() {
    try {
      return root.localStorage ? root.localStorage.getItem(STORAGE_KEY) : null;
    } catch {
      return null;
    }
  }

  function rememberTab(name) {
    try {
      if (root.localStorage) root.localStorage.setItem(STORAGE_KEY, name);
    } catch {
      // A browser that refuses storage still gets a working panel; the
      // remembered tab is a convenience, not state.
    }
  }

  /**
   * The pane layout, once it has taken over the panel.
   * @returns {object|null} gm-panel-layout.js's API, or null when the file
   *   is not loaded (or has not built its chrome yet).
   */
  function layout() {
    const candidate = root.gmPanelLayout;
    return candidate && candidate.isActive() ? candidate : null;
  }

  /**
   * Show the named tab and hide every other registered one.
   * @param {string} name - A registered tab name.
   * @param {boolean} [remember=true] - Store the choice for next time. The
   *   automatic picks made while tabs are still being registered pass
   *   false, so they never overwrite what the viewer chose last visit.
   * @returns {boolean} false when no tab of that name is registered.
   */
  function activate(name, remember = true) {
    if (!tabs.has(name)) return false;
    const panes = layout();
    if (panes) {
      // The viewer arranged the other panes; bringing this one up leaves
      // them where they are.
      panes.reveal(name);
    } else {
      for (const [tabName, tab] of tabs) {
        const selected = tabName === name;
        tab.container.hidden = !selected;
        tab.button.setAttribute('aria-pressed', selected ? 'true' : 'false');
        tab.button.tabIndex = selected ? 0 : -1;
      }
    }
    activeName = name;
    if (remember) rememberTab(name);
    return true;
  }

  /**
   * Show one tab and nothing else — what clicking the strip does, with or
   * without the pane layout.
   * @param {string} name - A registered tab name.
   * @returns {boolean} false when no tab of that name is registered.
   */
  function selectTab(name) {
    if (!tabs.has(name)) return false;
    const panes = layout();
    if (!panes) return activate(name);
    panes.showOnly(name);
    activeName = name;
    rememberTab(name);
    return true;
  }

  /**
   * Register a tab. Idempotent per name: registering a name twice replaces
   * the earlier button/container pair.
   * @param {string} name - Tab name (`data-panel-tab` value).
   * @param {HTMLElement} button - The tab strip button.
   * @param {HTMLElement} container - The element shown while the tab is active.
   */
  function registerTab(name, button, container) {
    if (!name || !button || !container) {
      throw new Error('gmPanel.registerTab needs a name, a button and a container');
    }
    tabs.set(name, { button, container });
    // A plain button, not a tab: several panes can be open at once, so the
    // strip was never a tablist and its split buttons had nowhere legal to
    // live inside one (issue #707).
    button.removeAttribute('role');
    // Not a tabpanel: the strip is a group of buttons, not a tablist
    // (issue #707), and a tabpanel with no tab is as wrong as the tablist was.
    container.setAttribute('role', 'region');
    button.addEventListener('click', () => selectTab(name));
    if (activeName === null) {
      activate(name, false);
    } else if (activeName !== name) {
      container.hidden = true;
      button.setAttribute('aria-pressed', 'false');
      button.tabIndex = -1;
    }
  }

  /** @returns {string|null} The active tab's name. */
  function activeTab() {
    return activeName;
  }

  /** @returns {string[]} Registered tab names, in registration order. */
  function tabNames() {
    return Array.from(tabs.keys());
  }

  /**
   * Every registered tab, for the pane layout to build its chrome around.
   * @returns {Array<{name: string, button: HTMLElement, container: HTMLElement}>}
   *   In registration order.
   */
  function tabEntries() {
    return Array.from(tabs, ([name, tab]) => ({
      name,
      button: tab.button,
      container: tab.container,
    }));
  }

  /**
   * Register every tab the markup declares under `scope`.
   * @param {ParentNode} scope - Defaults to the document.
   */
  function discoverTabs(scope) {
    const documentScope = scope || root.document;
    documentScope.querySelectorAll('[data-panel-tab]').forEach((button) => {
      const name = button.getAttribute('data-panel-tab');
      const controlledId = button.getAttribute('aria-controls');
      const container =
        (controlledId && root.document.getElementById(controlledId)) ||
        documentScope.querySelector(`[data-panel-container="${name}"]`);
      if (container) registerTab(name, button, container);
    });
    // With every declared tab known, reopen the one the viewer used last.
    const remembered = rememberedTab();
    if (remembered && tabs.has(remembered)) activate(remembered, false);
  }

  /**
   * Open or close the panel on small screens, where CSS hides it unless the
   * panel carries the open class. Desktop layouts ignore the class.
   * @param {boolean} [open] - Force a state; omitted toggles.
   */
  function setOpen(open) {
    const panel = root.document.getElementById('gm-panel');
    const toggle = root.document.getElementById('gm-panel-toggle');
    if (!panel) return false;
    const next = typeof open === 'boolean' ? open : !panel.classList.contains(OPEN_CLASS);
    panel.classList.toggle(OPEN_CLASS, next);
    if (toggle) toggle.setAttribute('aria-expanded', next ? 'true' : 'false');
    return next;
  }

  /**
   * The session the panel acts on. A new session gains its id when the
   * first message is sent — chat.js writes it into the URL without a reload
   * — so the URL is consulted ahead of the id the page loaded with.
   * @param {number|null} [pageSessionId] - The id the page rendered with.
   * @returns {number|null}
   */
  function currentSessionId(pageSessionId) {
    const fromUrl = new URLSearchParams(root.location.search || '').get('session');
    if (fromUrl && /^\d+$/.test(fromUrl)) return Number(fromUrl);
    return pageSessionId || null;
  }

  function init() {
    discoverTabs();
    // The pane layout, when the page loads it, takes the display over from
    // here (issue #351): the tabs it finds through tabEntries() become the
    // panel's panes.
    if (root.gmPanelLayout) root.gmPanelLayout.init(gmPanel);
    const toggle = root.document.getElementById('gm-panel-toggle');
    if (toggle) toggle.addEventListener('click', () => setOpen());
  }

  const gmPanel = {
    registerTab,
    activate,
    selectTab,
    activeTab,
    tabNames,
    tabEntries,
    discoverTabs,
    setOpen,
    currentSessionId,
    init,
  };
  root.gmPanel = gmPanel;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = gmPanel;
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
