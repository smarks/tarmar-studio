/**
 * Claude's reviews list (issue #572): reload when an open review moves on.
 *
 * The page is rendered from the database. While any listed review is waiting
 * or being reviewed, this asks the statuses endpoint about those reviews on
 * an interval and reloads the page the moment one of them answers with a
 * status other than the one the page was rendered with -- the battle lobby's
 * pattern (battle.js initLobbyPage). Nothing is drawn in place, and nothing
 * appears or disappears on a timer.
 */
(function (root) {
    'use strict';

    // Reviews take minutes, not seconds; a quarter-minute is prompt enough.
    const POLL_INTERVAL_MS = 15000;

    /**
     * The statuses the page was rendered with, for the reviews still open.
     *
     * @param {Document|Element} scope - where the rows are
     * @param {number[]} openIds - the ids the server listed as open
     * @returns {Object<string, string>} review id to status
     */
    function renderedStatuses(scope, openIds) {
        const baseline = {};
        openIds.forEach((reviewId) => {
            const row = scope.querySelector(`[data-review-id="${reviewId}"]`);
            if (row) { baseline[String(reviewId)] = row.dataset.reviewStatus; }
        });
        return baseline;
    }

    /**
     * True when any watched review's status differs from the rendered one.
     * A review missing from the answer counts as changed: it was deleted or
     * is no longer this reader's, and the page should say so.
     *
     * @param {Object<string, string>} baseline - from renderedStatuses
     * @param {Object<string, string>} current - the endpoint's `reviews`
     * @returns {boolean}
     */
    function statusesChanged(baseline, current) {
        return Object.keys(baseline).some(
            (reviewId) => !current || current[reviewId] !== baseline[reviewId],
        );
    }

    /**
     * The URL that asks about the watched reviews.
     *
     * @param {string} statusesUrl - the endpoint
     * @param {Object<string, string>} baseline - the watched reviews
     * @returns {string}
     */
    function statusesQuery(statusesUrl, baseline) {
        return `${statusesUrl}?ids=${Object.keys(baseline).join(',')}`;
    }

    /**
     * Start watching, if there is anything to watch.
     *
     * @param {Object} [options]
     * @param {Document} [options.doc]
     * @param {Function} [options.fetchImpl]
     * @param {Function} [options.schedule] - setInterval's shape
     * @returns {Function|null} one poll, for tests; null when nothing is open
     */
    function initReviewsPage(options = {}) {
        const doc = options.doc || document;
        const fetchImpl = options.fetchImpl || ((url) => fetch(url));
        const schedule = options.schedule || ((callback, delay) => setInterval(callback, delay));

        const page = doc.getElementById('gm-reviews');
        const openScript = doc.getElementById('gm-reviews-open');
        if (!page || !openScript || !page.dataset.statusesUrl) { return null; }
        const baseline = renderedStatuses(page, JSON.parse(openScript.textContent));
        if (Object.keys(baseline).length === 0) { return null; }
        const url = statusesQuery(page.dataset.statusesUrl, baseline);

        let reloading = false;
        async function poll() {
            if (reloading) { return; }
            let response;
            try {
                response = await fetchImpl(url);
            } catch (error) {
                return; // transient network trouble; the next tick retries
            }
            if (!response.ok) { return; }
            const data = await response.json();
            if (statusesChanged(baseline, data.reviews)) {
                reloading = true;
                pageNavigation.reload();
            }
        }
        schedule(poll, POLL_INTERVAL_MS);
        return poll;
    }

    if (typeof document !== 'undefined' && document.getElementById('gm-reviews')) {
        initReviewsPage();
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            POLL_INTERVAL_MS,
            renderedStatuses,
            statusesChanged,
            statusesQuery,
            initReviewsPage,
        };
    }
    root.gmReviews = { initReviewsPage };
}(typeof window !== 'undefined' ? window : globalThis));
