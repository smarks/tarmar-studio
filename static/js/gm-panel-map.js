/* GM side panel — the Map tab (issue #343).
 *
 * The tab's markup is gm/templates/gm/_panel_map.html: a picker of every map
 * in gm.maps.MAP_AREAS (each <option> carries the map's title and image URL),
 * a "follow the story" checkbox, zoom buttons, a viewport holding the image,
 * and a caption. This file owns which map is shown and where it is panned
 * and zoomed to.
 *
 * Two things change the map. The picker, by hand. And where the party is:
 * chat.js dispatches a `gm-stream-done` event on the document with the done
 * frame, and its `map` switches the map while "follow the story" is on. That
 * payload is the party's own record since issue #579 -- so the tab follows
 * the party rather than whichever place a reply happened to mention -- and
 * a place no chart shows arrives with `mapped: false`, which is said under
 * the caption instead of quietly leaving the last chart up. A hand-picked
 * map stays until the party moves. The choice, the place and the follow
 * setting are remembered per session in localStorage.
 *
 * Pan and zoom are one CSS transform on the image — no library. Wheel zooms
 * around the pointer, dragging pans, and the buttons zoom around the centre.
 *
 * The caption's place name comes from the model's reply by way of the
 * server's detector, so it is only ever set through textContent.
 */
(function (root) {
  const STORAGE_PREFIX = 'gm-map:';
  const MIN_SCALE = 1;
  const MAX_SCALE = 6;
  const BUTTON_ZOOM_FACTOR = 1.5;
  const WHEEL_ZOOM_FACTOR = 1.1;
  const DONE_EVENT = 'gm-stream-done';

  const configElement = root.document.getElementById('gm-map-config');
  const config = configElement ? JSON.parse(configElement.textContent) : {};

  /** @type {{slug: string|null, place: string|null, party: boolean, follow: boolean}} */
  const state = { slug: null, place: null, party: false, follow: true };
  const view = { scale: 1, translateX: 0, translateY: 0 };
  let drag = null;

  function element(id) {
    return root.document.getElementById(id);
  }

  /**
   * The session whose map this is. A new session gains its id when the
   * first message is sent — chat.js writes it into the URL without a reload
   * — so the URL is consulted ahead of the id the page loaded with.
   * @returns {string|null}
   */
  function sessionId() {
    const fromUrl = new URLSearchParams(root.location.search || '').get('session');
    if (fromUrl && /^\d+$/.test(fromUrl)) return fromUrl;
    return config.sessionId || null;
  }

  /** Where this session's map choice is remembered. */
  function storageKey() {
    return `${STORAGE_PREFIX}${sessionId() || 'new'}`;
  }

  /**
   * Point the pane's pop-out control at the chart on show, so the
   * standalone map page opens on the map the pane was showing, for the same
   * session (issue #351). The panel's own page carries the attribute; the
   * standalone page does not, and there is then nothing to update.
   */
  function updatePopoutUrl() {
    const section = element('gm-panel-map');
    const base = section ? section.getAttribute('data-popout-url') : null;
    if (!base) return;
    const params = new URLSearchParams();
    const session = sessionId();
    if (session) params.set('session', String(session));
    if (state.slug) params.set('map', state.slug);
    const query = params.toString();
    section.setAttribute('data-popout-url', query ? `${base.split('?')[0]}?${query}` : base.split('?')[0]);
  }

  function loadState() {
    try {
      const stored = root.localStorage ? root.localStorage.getItem(storageKey()) : null;
      if (!stored) return;
      const parsed = JSON.parse(stored);
      if (typeof parsed.slug === 'string') state.slug = parsed.slug;
      if (typeof parsed.place === 'string') state.place = parsed.place;
      if (typeof parsed.party === 'boolean') state.party = parsed.party;
      if (typeof parsed.follow === 'boolean') state.follow = parsed.follow;
    } catch {
      // Storage that refuses or holds garbage costs only the memory of the
      // last choice; the tab still works from the page's own config.
    }
  }

  function saveState() {
    try {
      if (root.localStorage) root.localStorage.setItem(storageKey(), JSON.stringify(state));
    } catch {
      // See loadState: remembering is a convenience, not state the tab needs.
    }
  }

  /**
   * The map the tab opens on when nothing else chooses one: the regional
   * chart, which the picker names in data-default-slug because which maps
   * exist is the loaded canon's to say (issue #918). A picker that names
   * none opens on its first map.
   * @returns {string|null} The slug, or null when there are no maps.
   */
  function defaultSlug() {
    const select = element('gm-map-select');
    if (!select) return null;
    const named = select.getAttribute('data-default-slug');
    if (named) return named;
    return select.options.length ? select.options[0].value : null;
  }

  /**
   * The picker option for a map slug.
   * @param {string} slug
   * @returns {HTMLOptionElement|null}
   */
  function optionFor(slug) {
    const select = element('gm-map-select');
    if (!select) return null;
    return Array.from(select.options).find((option) => option.value === slug) || null;
  }

  function applyTransform() {
    const image = element('gm-map-image');
    if (!image) return;
    image.style.transform = `translate(${view.translateX}px, ${view.translateY}px) scale(${view.scale})`;
    const level = element('gm-map-zoom-level');
    if (level) level.textContent = `${Math.round(view.scale * 100)}%`;
  }

  /**
   * Keep the image covering the viewport: at scale 1 it sits at the origin,
   * and zoomed in it may not be dragged past its own edge.
   */
  function clampTranslation() {
    const viewport = element('gm-map-viewport');
    const image = element('gm-map-image');
    if (!viewport || !image) return;
    const width = image.clientWidth * view.scale;
    const height = image.clientHeight * view.scale;
    const minX = Math.min(0, viewport.clientWidth - width);
    const minY = Math.min(0, viewport.clientHeight - height);
    view.translateX = Math.min(0, Math.max(minX, view.translateX));
    view.translateY = Math.min(0, Math.max(minY, view.translateY));
  }

  /**
   * Zoom by a factor, keeping the viewport point (originX, originY) over the
   * same spot of the map.
   * @param {number} factor
   * @param {number} originX - Pointer x within the viewport.
   * @param {number} originY - Pointer y within the viewport.
   */
  function zoomBy(factor, originX, originY) {
    const nextScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
    const ratio = nextScale / view.scale;
    view.translateX = originX - (originX - view.translateX) * ratio;
    view.translateY = originY - (originY - view.translateY) * ratio;
    view.scale = nextScale;
    clampTranslation();
    applyTransform();
  }

  function zoomAroundCentre(factor) {
    const viewport = element('gm-map-viewport');
    const centreX = viewport ? viewport.clientWidth / 2 : 0;
    const centreY = viewport ? viewport.clientHeight / 2 : 0;
    zoomBy(factor, centreX, centreY);
  }

  function resetView() {
    view.scale = 1;
    view.translateX = 0;
    view.translateY = 0;
    applyTransform();
  }

  /**
   * Say the party is somewhere no chart shows (issue #579). The chart on
   * screen is left alone: it is still the ground around them, and blanking
   * it would say less, not more.
   * @param {string} place - Where the party is, in the table's own words.
   */
  function showUnmapped(place) {
    const note = element('gm-map-unmapped');
    if (!note) return;
    note.textContent = place
      ? `The party is at ${place}, which is not on any map.`
      : 'The party is somewhere that is not on any map.';
    note.hidden = false;
  }

  function clearUnmapped() {
    const note = element('gm-map-unmapped');
    if (!note) return;
    note.textContent = '';
    note.hidden = true;
  }

  /**
   * Show a map. Unknown slugs are ignored so a stale stored choice or an
   * unexpected frame cannot blank the tab.
   * @param {string} slug
   * @param {string|null} place - The place named, for the caption.
   * @param {{party?: boolean}} [options] - `party` when `place` is where the
   *   party's own record puts them, which the caption says in as many words.
   * @returns {boolean} Whether the slug named a map.
   */
  function showMap(slug, place, options) {
    const party = Boolean(options && options.party);
    const option = optionFor(slug);
    if (!option) return false;
    const image = element('gm-map-image');
    const select = element('gm-map-select');
    const title = option.getAttribute('data-title') || option.textContent;
    const imageUrl = option.getAttribute('data-image-url');
    if (select.value !== slug) select.value = slug;
    if (image) {
      if (image.getAttribute('src') !== imageUrl) {
        image.setAttribute('src', imageUrl);
        resetView();
      }
      image.alt = `Map of ${title}`;
    }
    const captionTitle = element('gm-map-caption-title');
    if (captionTitle) captionTitle.textContent = title;
    const captionPlace = element('gm-map-caption-place');
    if (captionPlace) {
      const marked = party ? `— the party is at ${place}` : `— ${place}`;
      captionPlace.textContent = place ? marked : '';
      captionPlace.hidden = !place;
    }
    clearUnmapped();
    state.slug = slug;
    state.place = place || null;
    state.party = Boolean(place) && party;
    saveState();
    updatePopoutUrl();
    return true;
  }

  /** The current map slug, or null before one is shown. */
  function currentSlug() {
    return state.slug;
  }

  /** Whether the tab follows the narration. */
  function isFollowing() {
    return state.follow;
  }

  function setFollowing(follow) {
    state.follow = Boolean(follow);
    const checkbox = element('gm-map-follow');
    if (checkbox) checkbox.checked = state.follow;
    saveState();
  }

  /**
   * Handle a stream's done frame: go to where the party now is, if the tab
   * is following. A place no chart shows is said rather than shown.
   * @param {object} frame - The parsed done frame.
   * @returns {boolean} Whether the map changed because of the frame.
   */
  function handleDoneFrame(frame) {
    if (!state.follow || !frame || !frame.map) return false;
    const where = frame.map;
    if (where.mapped === false) {
      showUnmapped(where.place || '');
      return false;
    }
    if (typeof where.slug !== 'string') return false;
    return showMap(where.slug, where.place || null, { party: where.party === true });
  }

  function onWheel(event) {
    const viewport = element('gm-map-viewport');
    if (!viewport) return;
    event.preventDefault();
    const bounds = viewport.getBoundingClientRect();
    const factor = event.deltaY < 0 ? WHEEL_ZOOM_FACTOR : 1 / WHEEL_ZOOM_FACTOR;
    zoomBy(factor, event.clientX - bounds.left, event.clientY - bounds.top);
  }

  function onPointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;
    drag = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      translateX: view.translateX,
      translateY: view.translateY,
    };
    const viewport = element('gm-map-viewport');
    if (viewport) {
      viewport.classList.add('gm-map-dragging');
      if (viewport.setPointerCapture && event.pointerId !== undefined) {
        viewport.setPointerCapture(event.pointerId);
      }
    }
    event.preventDefault();
  }

  function onPointerMove(event) {
    if (!drag) return;
    view.translateX = drag.translateX + (event.clientX - drag.pointerX);
    view.translateY = drag.translateY + (event.clientY - drag.pointerY);
    clampTranslation();
    applyTransform();
  }

  function onPointerUp() {
    if (!drag) return;
    drag = null;
    const viewport = element('gm-map-viewport');
    if (viewport) viewport.classList.remove('gm-map-dragging');
  }

  function bindControls() {
    const select = element('gm-map-select');
    if (select) select.addEventListener('change', () => showMap(select.value, null));

    const follow = element('gm-map-follow');
    if (follow) follow.addEventListener('change', () => setFollowing(follow.checked));

    const zoomIn = element('gm-map-zoom-in');
    if (zoomIn) zoomIn.addEventListener('click', () => zoomAroundCentre(BUTTON_ZOOM_FACTOR));
    const zoomOut = element('gm-map-zoom-out');
    if (zoomOut) zoomOut.addEventListener('click', () => zoomAroundCentre(1 / BUTTON_ZOOM_FACTOR));
    const reset = element('gm-map-reset');
    if (reset) reset.addEventListener('click', resetView);

    const viewport = element('gm-map-viewport');
    if (viewport) {
      viewport.addEventListener('wheel', onWheel, { passive: false });
      viewport.addEventListener('pointerdown', onPointerDown);
      viewport.addEventListener('pointermove', onPointerMove);
      viewport.addEventListener('pointerup', onPointerUp);
      viewport.addEventListener('pointercancel', onPointerUp);
      viewport.addEventListener('pointerleave', onPointerUp);
    }

    root.document.addEventListener(DONE_EVENT, (event) => handleDoneFrame(event.detail));
  }

  /**
   * The first map the tab shows: the one the URL asks for (how a popped-out
   * map page opens where the pane was), else the chart the party's record
   * stands on, else the one remembered for this session, else the one the
   * story last named, else the regional chart.
   * @param {object|null} partyMap - The party's recorded place, or null.
   * @param {object|null} storyMap - The place the last reply named, or null.
   */
  function openFirstMap(partyMap, storyMap) {
    // The standalone page is opened on a named map by the pane that popped
    // it out; that beats the remembered choice, which is the same key.
    const requested = new URLSearchParams(root.location.search || '').get('map');
    if (requested && showMap(requested, null)) return;
    if (
      state.follow
      && partyMap
      && partyMap.mapped !== false
      && showMap(partyMap.slug, partyMap.place || null, { party: true })
    ) {
      return;
    }
    if (state.slug && showMap(state.slug, state.place, { party: state.party })) return;
    if (storyMap && state.follow && showMap(storyMap.slug, storyMap.place || null)) return;
    const fallback = defaultSlug();
    if (fallback) showMap(fallback, null);
  }

  /** Wire the tab and open it where the party is. */
  function init() {
    if (!element('gm-panel-map')) return;
    loadState();
    bindControls();
    setFollowing(state.follow);
    const partyMap = config.partyMap || null;
    openFirstMap(partyMap, config.storyMap || null);
    // Said after the chart is up, because showing one clears the note.
    if (state.follow && partyMap && partyMap.mapped === false) {
      showUnmapped(partyMap.place || '');
    }
  }

  const gmPanelMap = {
    init,
    showMap,
    showUnmapped,
    currentSlug,
    isFollowing,
    setFollowing,
    handleDoneFrame,
    zoomBy,
    resetView,
    storageKey,
    sessionId,
    updatePopoutUrl,
    view,
    DONE_EVENT,
  };
  root.gmPanelMap = gmPanelMap;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = gmPanelMap;
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
