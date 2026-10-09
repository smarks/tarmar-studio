/**
 * Unit tests for local-time.js (issue #225).
 *
 * local-time.js is the client half of the progressive-enhancement local-time
 * feature: the server (common/templatetags/local_time.py) renders every
 * timestamp as a <time class="local-time" datetime="...UTC ISO-8601..."
 * data-local-time-style="datetime|date">UTC fallback text</time> element,
 * and this script rewrites each one's visible text to the viewer's own time
 * zone via Intl.DateTimeFormat. It sets window.applyLocalTimes as a global
 * via an IIFE rather than module.exports, so tests require the file for its
 * side effect (which also runs an initial pass over the current document)
 * and then read window.applyLocalTimes for any later passes.
 *
 * Zone handling in these tests: a `process.env.TZ` assignment inside a test
 * file is NOT reliable (Intl caches the process zone before Jest loads the
 * file — it held on a developer Mac already in that zone and failed on the
 * UTC CI runner). So expectations for the load-time pass are DERIVED with
 * the same Intl options the module uses, in whatever zone the runner has —
 * asserting the rewrite happened, with the right instant and granularity —
 * and the genuine zone-shift semantics are pinned separately through
 * applyLocalTimes' explicit `timeZone` option, which works identically in
 * every runner zone.
 */

const DATETIME_OPTIONS = {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
};
const DATE_OPTIONS = { year: 'numeric', month: 'short', day: '2-digit' };

function expectedText(isoString, options, timeZone) {
    const withZone = timeZone ? { ...options, timeZone } : options;
    return new Intl.DateTimeFormat(undefined, withZone).format(new Date(isoString));
}

beforeEach(() => {
    jest.resetModules();
    document.body.innerHTML = '';
});

describe('the initial pass made when the script loads', () => {
    test('rewrites a datetime-style element into the runner zone', () => {
        document.body.innerHTML =
            '<time class="local-time" datetime="2026-01-15T05:30:00+00:00" ' +
            'data-local-time-style="datetime">Jan 15, 2026 05:30</time>';

        require('../local-time.js');

        expect(document.querySelector('time').textContent).toBe(
            expectedText('2026-01-15T05:30:00+00:00', DATETIME_OPTIONS)
        );
    });

    test('renders a date-style element date-only', () => {
        document.body.innerHTML =
            '<time class="local-time" datetime="2026-01-15T02:30:00+00:00" ' +
            'data-local-time-style="date">Jan 15, 2026</time>';

        require('../local-time.js');

        expect(document.querySelector('time').textContent).toBe(
            expectedText('2026-01-15T02:30:00+00:00', DATE_OPTIONS)
        );
    });

    test('treats a missing data-local-time-style as "datetime"', () => {
        document.body.innerHTML =
            '<time class="local-time" datetime="2026-01-15T05:30:00+00:00">Jan 15, 2026 05:30</time>';

        require('../local-time.js');

        expect(document.querySelector('time').textContent).toBe(
            expectedText('2026-01-15T05:30:00+00:00', DATETIME_OPTIONS)
        );
    });

    test('leaves non-local-time elements alone', () => {
        document.body.innerHTML =
            '<time datetime="2026-01-15T05:30:00+00:00">unrelated</time>';

        require('../local-time.js');

        expect(document.querySelector('time').textContent).toBe('unrelated');
    });

    test('leaves an element with an unparsable datetime untouched', () => {
        document.body.innerHTML =
            '<time class="local-time" datetime="not-a-real-timestamp">fallback text</time>';

        require('../local-time.js');

        expect(document.querySelector('time').textContent).toBe('fallback text');
    });
});

describe('window.applyLocalTimes', () => {
    test('is exposed for re-running over content inserted after load', () => {
        require('../local-time.js');

        const container = document.createElement('div');
        container.innerHTML =
            '<time class="local-time" datetime="2026-01-15T05:30:00+00:00" ' +
            'data-local-time-style="datetime">Jan 15, 2026 05:30</time>';
        document.body.appendChild(container);

        window.applyLocalTimes(container);

        expect(container.querySelector('time').textContent).toBe(
            expectedText('2026-01-15T05:30:00+00:00', DATETIME_OPTIONS)
        );
    });

    test('defaults to scanning the whole document when called with no argument', () => {
        require('../local-time.js');

        document.body.innerHTML =
            '<time class="local-time" datetime="2026-01-15T05:30:00+00:00" ' +
            'data-local-time-style="datetime">Jan 15, 2026 05:30</time>';

        window.applyLocalTimes();

        expect(document.querySelector('time').textContent).toBe(
            expectedText('2026-01-15T05:30:00+00:00', DATETIME_OPTIONS)
        );
    });

    test('an explicit timeZone shifts the clock time across zones', () => {
        require('../local-time.js');

        document.body.innerHTML =
            '<time class="local-time" datetime="2026-01-15T05:30:00+00:00" ' +
            'data-local-time-style="datetime">Jan 15, 2026 05:30</time>';

        window.applyLocalTimes(document, { timeZone: 'America/New_York' });

        // 05:30 UTC on Jan 15 is 00:30 EST (UTC-5) on the same calendar day —
        // derived through the same Intl path, so ICU formatting quirks
        // (comma placement, AM/PM casing) can't make this brittle.
        expect(document.querySelector('time').textContent).toBe(
            expectedText('2026-01-15T05:30:00+00:00', DATETIME_OPTIONS, 'America/New_York')
        );
        expect(document.querySelector('time').textContent).not.toBe(
            expectedText('2026-01-15T05:30:00+00:00', DATETIME_OPTIONS, 'UTC')
        );
    });

    test('an explicit timeZone can move a date-style display onto the previous calendar day', () => {
        require('../local-time.js');

        document.body.innerHTML =
            '<time class="local-time" datetime="2026-01-15T02:30:00+00:00" ' +
            'data-local-time-style="date">Jan 15, 2026</time>';

        window.applyLocalTimes(document, { timeZone: 'America/New_York' });

        // 02:30 UTC on Jan 15 is 21:30 EST on Jan 14 — the calendar date
        // itself moves, even though the enhanced display omits the clock.
        expect(document.querySelector('time').textContent).toBe(
            expectedText('2026-01-15T02:30:00+00:00', DATE_OPTIONS, 'America/New_York')
        );
        expect(document.querySelector('time').textContent).toContain('14');
    });
});
