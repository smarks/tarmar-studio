/**
 * Unit tests for gm-reviews.js (issue #572): the reviews list reloads when a
 * review it rendered as open has moved on, and otherwise leaves the page be.
 */

const {
    POLL_INTERVAL_MS,
    renderedStatuses,
    statusesChanged,
    statusesQuery,
    initReviewsPage,
} = require('../gm-reviews');

function renderPage({ rows, openIds, statusesUrl = '/tarmar/gm/reviews/statuses/' }) {
    document.body.innerHTML = `
        <section id="gm-reviews" data-statuses-url="${statusesUrl}">
            <table><tbody>
                ${rows.map(([reviewId, reviewStatus]) => `<tr data-review-id="${reviewId}" data-review-status="${reviewStatus}"></tr>`).join('')}
            </tbody></table>
        </section>
        <script id="gm-reviews-open" type="application/json">${JSON.stringify(openIds)}</script>
    `;
}

function answer(reviews, ok = true) {
    return jest.fn().mockResolvedValue({ ok, json: async () => ({ reviews }) });
}

describe('renderedStatuses', () => {
    test('reads the status each open row was rendered with', () => {
        renderPage({ rows: [[3, 'pending'], [2, 'done'], [1, 'claimed']], openIds: [3, 1] });
        expect(renderedStatuses(document, [3, 1])).toEqual({ 3: 'pending', 1: 'claimed' });
    });

    test('skips an id with no row', () => {
        renderPage({ rows: [[3, 'pending']], openIds: [3, 9] });
        expect(renderedStatuses(document, [3, 9])).toEqual({ 3: 'pending' });
    });
});

describe('statusesChanged', () => {
    test('is false while every watched review stands where it did', () => {
        expect(statusesChanged({ 3: 'pending' }, { 3: 'pending' })).toBe(false);
    });

    test('is true when one has moved on', () => {
        expect(statusesChanged({ 3: 'pending', 1: 'claimed' }, { 3: 'pending', 1: 'done' })).toBe(true);
    });

    test('is true when one is missing from the answer', () => {
        expect(statusesChanged({ 3: 'pending' }, {})).toBe(true);
        expect(statusesChanged({ 3: 'pending' }, undefined)).toBe(true);
    });
});

test('statusesQuery names the watched reviews', () => {
    expect(statusesQuery('/s/', { 3: 'pending', 1: 'claimed' })).toBe('/s/?ids=1,3');
});

describe('initReviewsPage', () => {
    test('does not poll when no review is open', () => {
        renderPage({ rows: [[2, 'done']], openIds: [] });
        const schedule = jest.fn();
        expect(initReviewsPage({ schedule })).toBeNull();
        expect(schedule).not.toHaveBeenCalled();
    });

    test('does nothing off the reviews page', () => {
        document.body.innerHTML = '<p>elsewhere</p>';
        expect(initReviewsPage({ schedule: jest.fn() })).toBeNull();
    });

    test('polls the watched reviews on the interval and leaves an unchanged page alone', async () => {
        renderPage({ rows: [[3, 'pending'], [2, 'done']], openIds: [3] });
        const schedule = jest.fn();
        const fetchImpl = answer({ 3: 'pending' });

        const poll = initReviewsPage({ schedule, fetchImpl });
        expect(schedule).toHaveBeenCalledWith(poll, POLL_INTERVAL_MS);
        await poll();

        expect(fetchImpl).toHaveBeenCalledWith('/tarmar/gm/reviews/statuses/?ids=3');
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('reloads once when a watched review changes', async () => {
        renderPage({ rows: [[3, 'pending']], openIds: [3] });
        const poll = initReviewsPage({ schedule: jest.fn(), fetchImpl: answer({ 3: 'claimed' }) });

        await poll();
        await poll();

        expect(pageNavigation.reload).toHaveBeenCalledTimes(1);
    });

    test('a failed request or refused answer leaves the page as it is', async () => {
        renderPage({ rows: [[3, 'pending']], openIds: [3] });
        const failing = jest.fn().mockRejectedValue(new Error('offline'));
        const poll = initReviewsPage({ schedule: jest.fn(), fetchImpl: failing });
        await poll();

        const refused = initReviewsPage({ schedule: jest.fn(), fetchImpl: answer({}, false) });
        await refused();

        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });
});
