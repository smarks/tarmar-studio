/* GM side panel — its width, its stack of panes, and popping a pane out
 * into its own window (issue #351).
 *
 * gm-panel.js owns which tabs exist (registerTab) and the small-screen
 * overlay. This file owns how much room the panel takes and what is shown
 * inside it, because a GM at the table wants the map or the board to have
 * real estate, and wants two of them at once.
 *
 * The panel is a stack of panes. Every registered tab can be a pane; the
 * open ones are stacked top to bottom, each with a title bar carrying a
 * collapse toggle, a pop-out control and a close control, and a draggable
 * divider between neighbours dividing the panel's height between them. The
 * tab strip still switches — clicking a tab shows that one alone — and the
 * small "+" beside each tab adds it as a second pane (or closes it again,
 * which is what "a pane closed from the strip goes back to being a tab"
 * means). At least one pane is always open.
 *
 * Everything the viewer arranges is state, kept in localStorage and
 * rendered from what is stored: the panel's width under `gm-panel-width`,
 * and which panes are open, in what order, collapsed or not, at what share
 * of the height, and whether each is popped out, under `gm-panel-panes`.
 * A missing or corrupt value falls back to one pane (the tab gm-panel.js
 * remembered) at the default width; a stored width that no longer fits the
 * viewport is clamped to what fits. Nothing here is transient: no toast
 * announces a pop-out, the pane itself says it is open elsewhere until the
 * viewer brings it back.
 *
 * A pane's pop-out target comes from its markup, so this file knows nothing
 * about maps or battles: a container carrying `data-popout-url` opens that
 * page, and a container holding `[data-popout-item]` children (the
 * Characters pane's sheets) opens the one nearest the top of the pane —
 * the sheet the viewer is looking at. `data-popout-empty` is what the
 * control says when there is nothing to open yet.
 *
 * All chrome here is built in script and every string set through
 * textContent or an attribute; the panes' own markup is the templates'.
 */
(function (root) {
  const WIDTH_KEY = 'gm-panel-width';
  const PANES_KEY = 'gm-panel-panes';
  const DEFAULT_WIDTH = 320;
  const MIN_WIDTH = 280;
  // The chat column must stay readable at the panel's widest.
  const MIN_CHAT_WIDTH = 420;
  const MAX_WIDTH_FRACTION = 0.5;
  const WIDTH_STEP = 16;
  const HEIGHT_STEP = 24;
  const MIN_WEIGHT = 0.15;
  const DEFAULT_WEIGHT = 1;
  const MOBILE_QUERY = '(max-width: 768px)';
  const POPOUT_WINDOW_PREFIX = 'gm-panel-';

  /**
   * @typedef {object} PaneEntry
   * @property {HTMLElement} button - The tab strip button.
   * @property {HTMLElement} container - The tab's own section.
   * @property {HTMLElement} wrapper - The pane box built around it.
   * @property {HTMLElement} body - The scrolling part of the pane.
   * @property {HTMLElement} detachedNotice - Shown while popped out.
   * @property {HTMLElement} splitButton - The strip's add/close toggle.
   * @property {HTMLElement} popoutButton - The title bar's pop-out control.
   * @property {HTMLElement} collapseButton - The title bar's collapse toggle.
   * @property {HTMLElement} closeButton - The title bar's close control.
   * @property {string} title - The pane's name, as the tab spells it.
   */

  /** @type {Map<string, PaneEntry>} */
  const panes = new Map();
  /** Open pane names, top to bottom, and the settings of every known pane. */
  const state = { open: [], settings: {} };
  /** Windows this page opened, so a second pop-out focuses rather than duplicates. */
  const popoutWindows = new Map();

  let panelElement = null;
  let stackElement = null;
  let handleElement = null;
  let panelWidth = DEFAULT_WIDTH;
  let built = false;

  /* ===== Storage ===== */

  function readStored(key) {
    try {
      return root.localStorage ? root.localStorage.getItem(key) : null;
    } catch {
      // A browser that refuses storage still gets a working panel; the
      // remembered arrangement is a convenience, not state the panel needs.
      return null;
    }
  }

  function writeStored(key, value) {
    try {
      if (root.localStorage) root.localStorage.setItem(key, value);
    } catch {
      // See readStored: refusing storage costs only the memory of the
      // arrangement, so there is nothing to report and nothing to retry.
    }
  }

  /* ===== Width ===== */

  /**
   * The widest the panel may be beside a readable chat column.
   * @param {number} viewportWidth - The window's inner width.
   * @param {number} [reservedWidth=0] - Room other columns take (the session
   *   sidebar), which the chat does not get either.
   * @returns {number} A width in pixels, never below the panel's minimum:
   *   on a window too narrow for both, the panel keeps its minimum and the
   *   chat gives way (below the mobile breakpoint the panel overlays anyway).
   */
  function maxWidth(viewportWidth, reservedWidth = 0) {
    const half = viewportWidth * MAX_WIDTH_FRACTION;
    const beside = viewportWidth - reservedWidth - MIN_CHAT_WIDTH;
    return Math.max(MIN_WIDTH, Math.min(half, beside));
  }

  /**
   * Clamp a wanted panel width to what this window can give it.
   * @param {number} width - The width asked for, in pixels.
   * @param {number} viewportWidth - The window's inner width.
   * @param {number} [reservedWidth=0] - Room other columns take.
   * @returns {number} The width to use.
   */
  function clampWidth(width, viewportWidth, reservedWidth = 0) {
    const wanted = Number.isFinite(width) ? width : DEFAULT_WIDTH;
    return Math.round(Math.min(Math.max(wanted, MIN_WIDTH), maxWidth(viewportWidth, reservedWidth)));
  }

  function isMobile() {
    return typeof root.matchMedia === 'function' && root.matchMedia(MOBILE_QUERY).matches;
  }

  /** How much width the layout's other columns take (the session sidebar). */
  function reservedWidth() {
    const sidebar = root.document.getElementById('sidebar');
    return sidebar ? sidebar.offsetWidth : 0;
  }

  function viewportWidth() {
    return root.innerWidth || root.document.documentElement.clientWidth || 0;
  }

  /**
   * Set the panel's width, clamped to the window, and show it on the handle.
   * @param {number} width - The width asked for, in pixels.
   * @param {boolean} [remember=true] - Store it for next visit.
   * @returns {number} The width actually used.
   */
  function setWidth(width, remember = true) {
    panelWidth = clampWidth(width, viewportWidth(), reservedWidth());
    if (panelElement) {
      // Below the breakpoint the panel is a full-height overlay whose width
      // is the stylesheet's; an inline width would fight it.
      panelElement.style.width = isMobile() ? '' : `${panelWidth}px`;
    }
    if (handleElement) {
      handleElement.setAttribute('aria-valuenow', String(panelWidth));
      handleElement.setAttribute('aria-valuemin', String(MIN_WIDTH));
      handleElement.setAttribute('aria-valuemax', String(Math.round(maxWidth(viewportWidth(), reservedWidth()))));
      handleElement.setAttribute('aria-valuetext', `${panelWidth} pixels`);
    }
    if (remember) writeStored(WIDTH_KEY, String(panelWidth));
    return panelWidth;
  }

  /** The stored width, or the default when there is none or it is junk. */
  function storedWidth() {
    const raw = readStored(WIDTH_KEY);
    const parsed = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_WIDTH;
  }

  /* ===== Pane state ===== */

  function defaultSettings() {
    return { weight: DEFAULT_WEIGHT, collapsed: false, detached: false };
  }

  function settingsFor(name) {
    if (!state.settings[name]) state.settings[name] = defaultSettings();
    return state.settings[name];
  }

  /**
   * Read the stored arrangement, keeping only panes this page has.
   * @param {string[]} knownNames - Every registered pane name.
   * @returns {{open: string[], settings: object}|null} null when nothing
   *   usable is stored, so the caller falls back to a single pane.
   */
  function parsePanes(knownNames, raw) {
    if (!raw) return null;
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // Junk in storage (a half-written value, an older shape) means the
      // viewer gets the default arrangement rather than a broken panel.
      return null;
    }
    if (!parsed || !Array.isArray(parsed.open)) return null;
    const open = [];
    parsed.open.forEach((name) => {
      if (typeof name === 'string' && knownNames.includes(name) && !open.includes(name)) {
        open.push(name);
      }
    });
    if (open.length === 0) return null;
    const settings = {};
    knownNames.forEach((name) => {
      const stored = parsed.settings && parsed.settings[name];
      const pane = defaultSettings();
      if (stored) {
        if (Number.isFinite(stored.weight) && stored.weight >= MIN_WEIGHT) pane.weight = stored.weight;
        pane.collapsed = stored.collapsed === true;
        pane.detached = stored.detached === true;
      }
      settings[name] = pane;
    });
    return { open, settings };
  }

  function savePanes() {
    writeStored(PANES_KEY, JSON.stringify({ open: state.open, settings: state.settings }));
  }

  /* ===== Pane heights ===== */

  /**
   * Divide two neighbouring panes' share of the height after a drag.
   * @param {number} weightAbove - The upper pane's current weight.
   * @param {number} weightBelow - The lower pane's current weight.
   * @param {number} pixelsAbove - The upper pane's current height.
   * @param {number} pixelsBelow - The lower pane's current height.
   * @param {number} deltaPixels - How far the divider moved; positive is down.
   * @returns {number[]} The two new weights, each at least MIN_WEIGHT and
   *   together the same total as before, so the other panes keep their share.
   */
  function dividedWeights(weightAbove, weightBelow, pixelsAbove, pixelsBelow, deltaPixels) {
    const totalWeight = weightAbove + weightBelow;
    const totalPixels = pixelsAbove + pixelsBelow;
    if (totalPixels <= 0 || totalWeight <= 0) return [weightAbove, weightBelow];
    const wantedAbove = (totalWeight * (pixelsAbove + deltaPixels)) / totalPixels;
    const above = Math.min(Math.max(wantedAbove, MIN_WEIGHT), totalWeight - MIN_WEIGHT);
    return [above, totalWeight - above];
  }

  /**
   * Whether a pane takes a share of the panel's height. A collapsed pane is
   * its title bar, and a popped-out one is a line saying where it went:
   * neither should hold room the panes with something to show could use.
   * @param {string} name
   * @returns {boolean}
   */
  function takesHeight(name) {
    const settings = settingsFor(name);
    return !settings.collapsed && !settings.detached;
  }

  /** Push the current weights onto the open panes' boxes. */
  function applyWeights() {
    state.open.forEach((name) => {
      const pane = panes.get(name);
      if (!pane) return;
      const flexible = takesHeight(name);
      // Longhands, not the `flex` shorthand: the weight is read back in
      // tests and by nothing else, and every engine agrees on these three.
      pane.wrapper.style.flexGrow = flexible ? String(settingsFor(name).weight) : '0';
      pane.wrapper.style.flexShrink = flexible ? '1' : '0';
      pane.wrapper.style.flexBasis = flexible ? '0' : 'auto';
    });
  }

  /* ===== Building the chrome ===== */

  function makeElement(tag, className, text) {
    const element = root.document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function makeButton(className, label, text) {
    const button = makeElement('button', className, text);
    button.type = 'button';
    button.setAttribute('aria-label', label);
    button.title = label;
    return button;
  }

  /**
   * Wrap one registered tab in a pane box: a title bar over its container.
   * @param {string} name - The tab name.
   * @param {{button: HTMLElement, container: HTMLElement}} tab
   * @returns {PaneEntry}
   */
  function buildPane(name, tab) {
    const title = (tab.button.textContent || name).trim();
    const wrapper = makeElement('div', 'gm-pane');
    wrapper.setAttribute('data-pane', name);

    const titleBar = makeElement('div', 'gm-pane-title');
    const collapseButton = makeButton('gm-pane-collapse', `Collapse ${title}`, '▾');
    collapseButton.setAttribute('aria-expanded', 'true');
    const heading = makeElement('h3', 'gm-pane-name', title);
    const popoutButton = makeButton('gm-pane-popout', `Open ${title} in its own window`, '⧉');
    const closeButton = makeButton('gm-pane-close', `Close ${title}`, '×');
    titleBar.append(collapseButton, heading, popoutButton, closeButton);

    const body = makeElement('div', 'gm-pane-body');
    const detachedNotice = makeElement('div', 'gm-pane-detached');
    detachedNotice.append(makeElement('p', 'gm-pane-detached-text', `${title} is open in another window.`));
    const returnButton = makeElement('button', 'btn btn-sm gm-pane-return', 'Bring it back');
    returnButton.type = 'button';
    detachedNotice.append(returnButton);
    detachedNotice.hidden = true;

    body.append(tab.container, detachedNotice);
    wrapper.append(titleBar, body);
    stackElement.append(wrapper);

    const splitButton = makeButton('gm-panel-split', `Show ${title} as its own pane`, '+');
    splitButton.setAttribute('aria-pressed', 'false');
    const group = makeElement('div', 'gm-panel-tab-group');
    tab.button.parentNode.insertBefore(group, tab.button);
    group.append(tab.button, splitButton);

    const entry = {
      button: tab.button,
      container: tab.container,
      wrapper,
      body,
      detachedNotice,
      splitButton,
      popoutButton,
      collapseButton,
      closeButton,
      title,
    };

    collapseButton.addEventListener('click', () => toggleCollapsed(name));
    closeButton.addEventListener('click', () => closePane(name));
    splitButton.addEventListener('click', () => togglePane(name));
    popoutButton.addEventListener('click', () => popOut(name));
    returnButton.addEventListener('click', () => bringBack(name));

    return entry;
  }

  /** The panel's left-edge grab handle: drag, double-click, or arrow keys. */
  function buildHandle() {
    const handle = makeElement('div', 'gm-panel-resize');
    handle.id = 'gm-panel-resize';
    handle.setAttribute('role', 'separator');
    handle.setAttribute('aria-orientation', 'vertical');
    handle.setAttribute('aria-label', 'Resize the session panel');
    handle.tabIndex = 0;
    handle.addEventListener('dblclick', () => setWidth(DEFAULT_WIDTH));
    handle.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowLeft') setWidth(panelWidth + WIDTH_STEP);
      else if (event.key === 'ArrowRight') setWidth(panelWidth - WIDTH_STEP);
      else if (event.key === 'Home') setWidth(DEFAULT_WIDTH);
      else return;
      event.preventDefault();
    });
    handle.addEventListener('pointerdown', (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      const startX = event.clientX;
      const startWidth = panelWidth;
      if (handle.setPointerCapture && event.pointerId !== undefined) {
        handle.setPointerCapture(event.pointerId);
      }
      root.document.body.classList.add('gm-panel-resizing');
      const onMove = (moveEvent) => setWidth(startWidth + (startX - moveEvent.clientX), false);
      const onUp = () => {
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        handle.removeEventListener('pointercancel', onUp);
        root.document.body.classList.remove('gm-panel-resizing');
        setWidth(panelWidth);
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
      handle.addEventListener('pointercancel', onUp);
      event.preventDefault();
    });
    return handle;
  }

  /**
   * A divider between two open panes, dragged or arrowed to divide the
   * height between them.
   * @param {string} aboveName
   * @param {string} belowName
   * @returns {HTMLElement}
   */
  function buildDivider(aboveName, belowName) {
    const above = panes.get(aboveName);
    const below = panes.get(belowName);
    const divider = makeElement('div', 'gm-pane-divider');
    divider.setAttribute('role', 'separator');
    divider.setAttribute('aria-orientation', 'horizontal');
    divider.setAttribute('aria-label', `Resize ${above.title} and ${below.title}`);
    divider.setAttribute('data-above', aboveName);
    divider.setAttribute('data-below', belowName);
    divider.tabIndex = 0;

    const move = (deltaPixels) => {
      const aboveSettings = settingsFor(aboveName);
      const belowSettings = settingsFor(belowName);
      if (!takesHeight(aboveName) || !takesHeight(belowName)) return;
      const [nextAbove, nextBelow] = dividedWeights(
        aboveSettings.weight,
        belowSettings.weight,
        above.wrapper.offsetHeight,
        below.wrapper.offsetHeight,
        deltaPixels,
      );
      aboveSettings.weight = nextAbove;
      belowSettings.weight = nextBelow;
      applyWeights();
    };

    divider.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowUp') move(-HEIGHT_STEP);
      else if (event.key === 'ArrowDown') move(HEIGHT_STEP);
      else return;
      event.preventDefault();
      savePanes();
    });
    divider.addEventListener('pointerdown', (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      let lastY = event.clientY;
      if (divider.setPointerCapture && event.pointerId !== undefined) {
        divider.setPointerCapture(event.pointerId);
      }
      root.document.body.classList.add('gm-panel-resizing-rows');
      const onMove = (moveEvent) => {
        move(moveEvent.clientY - lastY);
        lastY = moveEvent.clientY;
      };
      const onUp = () => {
        divider.removeEventListener('pointermove', onMove);
        divider.removeEventListener('pointerup', onUp);
        divider.removeEventListener('pointercancel', onUp);
        root.document.body.classList.remove('gm-panel-resizing-rows');
        savePanes();
      };
      divider.addEventListener('pointermove', onMove);
      divider.addEventListener('pointerup', onUp);
      divider.addEventListener('pointercancel', onUp);
      event.preventDefault();
    });
    return divider;
  }

  /* ===== Pop-out ===== */

  /**
   * Where a pane's "own window" control goes, read from its markup.
   * @param {string} name
   * @returns {{url: string, label: string}|null} null when the pane has
   *   nothing to open yet (no fight on the board, no character attached).
   */
  function popoutTarget(name) {
    const pane = panes.get(name);
    if (!pane) return null;
    const own = pane.container.getAttribute('data-popout-url');
    if (own) return { url: own, label: pane.title };
    const items = Array.from(pane.container.querySelectorAll('[data-popout-item]'));
    if (items.length === 0) return null;
    // The item the viewer is looking at: the one nearest the top of the
    // scrolled pane, which for one attached character is simply the one.
    const bodyTop = pane.body.getBoundingClientRect().top;
    let best = items[0];
    let bestDistance = Number.POSITIVE_INFINITY;
    items.forEach((item) => {
      const distance = Math.abs(item.getBoundingClientRect().top - bodyTop);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = item;
      }
    });
    const url = best.getAttribute('data-popout-url');
    if (!url) return null;
    return { url, label: best.getAttribute('data-popout-label') || pane.title };
  }

  /**
   * Open a pane's page in its own window and leave the pane saying so.
   * @param {string} name
   * @returns {boolean} Whether a window was asked for.
   */
  function popOut(name) {
    const target = popoutTarget(name);
    if (!target) return false;
    const windowName = `${POPOUT_WINDOW_PREFIX}${name}`;
    // A window name is what makes the second click focus the window this
    // pane already opened instead of piling up a third. `noopener` in the
    // features string would defeat that — the specification turns a named
    // open with noopener into a fresh `_blank` — so the opener is severed
    // on the window itself, which is the same protection.
    const opened = root.open(target.url, windowName);
    if (opened) {
      opened.opener = null;
      popoutWindows.set(name, opened);
      if (opened.focus) opened.focus();
    }
    settingsFor(name).detached = true;
    render();
    savePanes();
    return true;
  }

  /**
   * Bring a popped-out pane back into the panel, closing the window it went
   * to if this page still holds it.
   * @param {string} name
   */
  function bringBack(name) {
    const opened = popoutWindows.get(name);
    if (opened && !opened.closed && opened.close) opened.close();
    popoutWindows.delete(name);
    settingsFor(name).detached = false;
    render();
    savePanes();
  }

  /* ===== Rendering ===== */

  /** Draw the panel from `state`: the strip, the stack, the dividers. */
  function render() {
    if (!stackElement) return;
    Array.from(stackElement.querySelectorAll('.gm-pane-divider')).forEach((divider) => divider.remove());

    state.open.forEach((name, index) => {
      const pane = panes.get(name);
      if (!pane) return;
      // A divider only where there is height to divide: beside a collapsed
      // or popped-out pane its own title bar is the seam, and a handle
      // that could not move would be a lie.
      if (index > 0 && takesHeight(state.open[index - 1]) && takesHeight(name)) {
        stackElement.append(buildDivider(state.open[index - 1], name));
      }
      stackElement.append(pane.wrapper);
    });
    // A closed pane's box stays in the page, hidden, below the open ones:
    // its tab's script found its elements by id at load and keeps them.
    panes.forEach((pane, name) => {
      if (!state.open.includes(name)) stackElement.append(pane.wrapper);
    });

    panes.forEach((pane, name) => {
      const open = state.open.includes(name);
      const settings = settingsFor(name);
      const only = open && state.open.length === 1;
      pane.wrapper.hidden = !open;
      pane.container.hidden = !open || settings.detached;
      pane.detachedNotice.hidden = !open || !settings.detached;
      pane.body.hidden = settings.collapsed;
      pane.wrapper.classList.toggle('gm-pane-collapsed', settings.collapsed);
      pane.collapseButton.setAttribute('aria-expanded', settings.collapsed ? 'false' : 'true');
      pane.collapseButton.textContent = settings.collapsed ? '▸' : '▾';
      const collapseLabel = `${settings.collapsed ? 'Expand' : 'Collapse'} ${pane.title}`;
      pane.collapseButton.setAttribute('aria-label', collapseLabel);
      pane.collapseButton.title = collapseLabel;
      // The last pane open stays open: the panel is never empty, and the
      // tab strip is how a viewer swaps which single pane that is.
      pane.closeButton.disabled = only;
      pane.button.setAttribute('aria-pressed', open ? 'true' : 'false');
      pane.button.tabIndex = open ? 0 : -1;
      pane.splitButton.setAttribute('aria-pressed', open ? 'true' : 'false');
      pane.splitButton.textContent = open ? '−' : '+';
      pane.splitButton.disabled = only;
      const splitLabel = open ? `Close the ${pane.title} pane` : `Show ${pane.title} as its own pane`;
      pane.splitButton.setAttribute('aria-label', splitLabel);
      pane.splitButton.title = splitLabel;
      paintPopout(name, pane);
    });

    applyWeights();
  }

  /**
   * Paint a pane's "own window" control from what its tab holds now.
   * @param {string} name
   * @param {object} pane
   */
  function paintPopout(name, pane) {
    const settings = settingsFor(name);
    const open = state.open.includes(name);
    const target = open ? popoutTarget(name) : null;
    pane.popoutButton.disabled = !target || settings.detached;
    const popoutLabel = target
      ? `Open ${target.label} in its own window`
      : pane.container.getAttribute('data-popout-empty') || `${pane.title} has nothing to open yet`;
    pane.popoutButton.setAttribute('aria-label', popoutLabel);
    pane.popoutButton.title = popoutLabel;
  }

  /**
   * Paint every pane's "own window" control again, and nothing else: for a
   * tab whose contents were replaced in place (issue #89), which changes
   * what there is to open. Unlike render() it moves no pane, so the keyboard
   * focus stays where the reader has it.
   */
  function paintPopouts() {
    panes.forEach((pane, name) => paintPopout(name, pane));
  }

  /* ===== The arrangement ===== */

  /**
   * Show one pane and close the rest — what clicking a tab does.
   * @param {string} name
   * @returns {boolean} Whether the name is a registered pane.
   */
  function showOnly(name) {
    if (!panes.has(name)) return false;
    state.open = [name];
    settingsFor(name).collapsed = false;
    render();
    savePanes();
    return true;
  }

  /**
   * Open a pane below the ones already open.
   * @param {string} name
   * @returns {boolean} Whether the name is a registered pane.
   */
  function openPane(name) {
    if (!panes.has(name)) return false;
    if (!state.open.includes(name)) state.open.push(name);
    settingsFor(name).collapsed = false;
    render();
    savePanes();
    return true;
  }

  /**
   * Close a pane, which makes it a tab again. The last one open stays.
   * @param {string} name
   * @returns {boolean} Whether it closed.
   */
  function closePane(name) {
    if (state.open.length <= 1 || !state.open.includes(name)) return false;
    state.open = state.open.filter((open) => open !== name);
    render();
    savePanes();
    return true;
  }

  /** Open the pane if it is closed, close it if it is open. */
  function togglePane(name) {
    return state.open.includes(name) ? closePane(name) : openPane(name);
  }

  /**
   * Collapse a pane to its title bar, or expand it again.
   * @param {string} name
   */
  function toggleCollapsed(name) {
    if (!panes.has(name)) return false;
    const settings = settingsFor(name);
    settings.collapsed = !settings.collapsed;
    render();
    savePanes();
    return true;
  }

  /**
   * Make sure a pane is on show — what gm-panel.js's activate() asks for
   * when another script (the Fight button, say) brings a tab up. An already
   * open pane keeps its neighbours: the viewer arranged those.
   * @param {string} name
   * @returns {boolean} Whether the name is a registered pane.
   */
  function reveal(name) {
    if (!panes.has(name)) return false;
    if (!state.open.includes(name)) {
      state.open.push(name);
    }
    settingsFor(name).collapsed = false;
    render();
    savePanes();
    const pane = panes.get(name);
    if (pane.wrapper.scrollIntoView) pane.wrapper.scrollIntoView({ block: 'nearest' });
    return true;
  }

  /* ===== Set-up ===== */

  /**
   * Build the panel's chrome around the tabs gm-panel.js registered.
   * @param {{tabEntries: function(): Array}} panel - The gm-panel.js API.
   * @returns {boolean} Whether the layout took over the panel.
   */
  function init(panel) {
    panelElement = root.document.getElementById('gm-panel');
    const tabs = panel && panel.tabEntries ? panel.tabEntries() : [];
    if (!panelElement || tabs.length === 0) return false;

    stackElement = makeElement('div', 'gm-panel-stack');
    stackElement.id = 'gm-panel-stack';
    panelElement.append(stackElement);
    tabs.forEach(({ name, button, container }) => {
      panes.set(name, buildPane(name, { button, container }));
    });
    handleElement = buildHandle();
    panelElement.insertBefore(handleElement, panelElement.firstChild);

    const known = Array.from(panes.keys());
    const stored = parsePanes(known, readStored(PANES_KEY));
    if (stored) {
      state.open = stored.open;
      state.settings = stored.settings;
    } else {
      const remembered = panel.activeTab && panel.activeTab();
      state.open = [remembered && panes.has(remembered) ? remembered : known[0]];
      state.settings = {};
      known.forEach((name) => {
        state.settings[name] = defaultSettings();
      });
    }
    built = true;
    render();
    setWidth(storedWidth(), false);
    root.addEventListener('resize', () => setWidth(panelWidth, false));
    return true;
  }

  /** Whether the panel is under this file's control. */
  function isActive() {
    return built;
  }

  /** The arrangement on show, for tests and for other scripts to read. */
  function openPanes() {
    return state.open.slice();
  }

  /** The panel's width in pixels. */
  function width() {
    return panelWidth;
  }

  const gmPanelLayout = {
    init,
    isActive,
    reveal,
    showOnly,
    openPane,
    closePane,
    togglePane,
    toggleCollapsed,
    popOut,
    bringBack,
    popoutTarget,
    openPanes,
    width,
    setWidth,
    clampWidth,
    maxWidth,
    dividedWeights,
    parsePanes,
    render,
    paintPopouts,
    DEFAULT_WIDTH,
    MIN_WIDTH,
    WIDTH_KEY,
    PANES_KEY,
  };
  root.gmPanelLayout = gmPanelLayout;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = gmPanelLayout;
  }
})(typeof window !== 'undefined' ? window : globalThis);
