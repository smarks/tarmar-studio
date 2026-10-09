/**
 * Battle setup page (issues #270/#271).
 *
 * Thin on purpose: the setup's state lives on the server-side draft, so the
 * page's JavaScript only wires the shared autosave onto the terms fields —
 * the draft saves on blur, and the round trip through the character editor
 * loses nothing. The saved-character shelf filters as you type through
 * roster-filter.js, which wires its input from data-filter-list exactly as it
 * does the parties page's (issue #338 item 16). Roster changes are plain form
 * posts the server re-renders; nothing here holds state a reload would lose.
 *
 * initAutosave comes from autosave.js, which the template loads ahead of
 * this file (base.html already loads api.js in <head>). Node/Jest has no
 * shared script scope, so require both there; either way the names end up
 * on the global object.
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
    Object.assign(globalThis, require('./autosave.js'));
    globalThis.rosterFilter = require('./roster-filter.js');
}

(function (root) {
    // The shelf filter moved to roster-filter.js when the parties page
    // (#274) grew the same picker; the template loads that file first.
    // Re-exported below so this module's callers (and tests) keep their
    // one import.
    const filterCharacters = root.rosterFilter.filterCharacters;

    /**
     * "Begin the battle" posts once (issue #832).
     *
     * A double press, or a second press while a slow first one is still in
     * flight, used to post twice; the server now refuses the second, and
     * this keeps the page from sending it. The button is disabled from the
     * first submit until the page is left. Two ways back put the button as
     * the server rendered it, so the reader can press again: a page restored
     * from the back-forward cache, and a submission that never left the page
     * because it was stopped (Esc or the Stop button). The second is heard
     * through the Navigation API's navigateerror, which a stopped navigation
     * fires. A browser without that API cannot say a navigation stopped, so
     * there the button comes back on Esc, and on its own after
     * START_REARM_MS: long enough to swallow a double press, and a later
     * press is safe because the server refuses a second start of one draft.
     */
    const START_REARM_MS = 8000;

    function guardSingleStart(form) {
        if (!form) { return; }
        const button = form.querySelector('button[type="submit"]');
        const renderedDisabled = button ? button.disabled : false;
        const hearsStops = Boolean(
            window.navigation && window.navigation.addEventListener
        );
        let rearmTimer = null;
        function reset() {
            if (rearmTimer !== null) {
                clearTimeout(rearmTimer);
                rearmTimer = null;
            }
            delete form.dataset.starting;
            if (button) { button.disabled = renderedDisabled; }
        }
        form.addEventListener('submit', (event) => {
            if (form.dataset.starting === 'yes') {
                event.preventDefault();
                return;
            }
            form.dataset.starting = 'yes';
            if (button) { button.disabled = true; }
            if (!hearsStops) { rearmTimer = setTimeout(reset, START_REARM_MS); }
        });
        window.addEventListener('pageshow', (event) => {
            if (event.persisted) { reset(); }
        });
        if (hearsStops) {
            window.navigation.addEventListener('navigateerror', reset);
        } else {
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && form.dataset.starting === 'yes') {
                    reset();
                }
            });
        }
    }

    function initBattleSetup() {
        guardSingleStart(document.getElementById('setup-start-form'));
        const saveNote = document.getElementById('setup-save-note');
        if (!saveNote || !saveNote.dataset.updateUrl) { return; }
        initAutosave({
            updateUrl: saveNote.dataset.updateUrl,
        });
    }

    if (typeof document !== 'undefined') {
        initBattleSetup();
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            filterCharacters, guardSingleStart, initBattleSetup, START_REARM_MS,
        };
    }
    root.battleSetupPage = { initBattleSetup, filterCharacters, guardSingleStart };
}(typeof window !== 'undefined' ? window : globalThis));
