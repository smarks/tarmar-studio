/**
 * Unit tests for notify.js (issue #110).
 *
 * notify.js exposes window.showNotice (a persistent, dismissible banner)
 * and window.confirmDialog (a Promise-based, focus-trapped modal) as
 * replacements for the browser's native alert()/confirm(). It sets these
 * as globals via an IIFE rather than module.exports, so tests require the
 * file for its side effect and then read window.showNotice/confirmDialog.
 */

let showNotice;
let confirmDialog;

beforeEach(() => {
    jest.resetModules();
    document.body.innerHTML = '';
    require('../notify.js');
    ({ showNotice, confirmDialog } = window);
});

describe('showNotice', () => {
    test('creates a notice-region under <main> on first use', () => {
        document.body.innerHTML = '<main></main>';
        showNotice('Saved.', 'success');

        const region = document.getElementById('notice-region');
        expect(region).not.toBeNull();
        expect(region.getAttribute('aria-live')).toBe('polite');
        expect(document.querySelector('main').firstElementChild).toBe(region);
    });

    test('falls back to <body> when there is no <main>', () => {
        showNotice('Saved.', 'success');

        const region = document.getElementById('notice-region');
        expect(region).not.toBeNull();
        expect(document.body.firstElementChild).toBe(region);
    });

    test('reuses the same notice-region across multiple calls', () => {
        showNotice('First');
        showNotice('Second');

        const regions = document.querySelectorAll('#notice-region');
        expect(regions.length).toBe(1);
        expect(regions[0].querySelectorAll('.notice').length).toBe(2);
    });

    test('defaults to type "error" with role="alert"', () => {
        const notice = showNotice('Something broke');
        expect(notice.className).toBe('notice notice-error');
        expect(notice.getAttribute('role')).toBe('alert');
    });

    test('a non-error type gets role="status"', () => {
        const notice = showNotice('All set', 'success');
        expect(notice.className).toBe('notice notice-success');
        expect(notice.getAttribute('role')).toBe('status');
    });

    test('renders the message text and a labeled dismiss button', () => {
        const notice = showNotice('Character saved.');
        expect(notice.querySelector('.notice-text').textContent).toBe('Character saved.');

        const dismiss = notice.querySelector('.notice-dismiss');
        expect(dismiss).not.toBeNull();
        expect(dismiss.getAttribute('aria-label')).toBe('Dismiss');
    });

    test('persists in the DOM — no auto-dismiss timer removes it', () => {
        jest.useFakeTimers();
        const notice = showNotice('Stays until dismissed.');

        jest.advanceTimersByTime(60_000);

        expect(notice.isConnected).toBe(true);
        expect(document.getElementById('notice-region').contains(notice)).toBe(true);
        jest.useRealTimers();
    });

    test('clicking the dismiss button removes only that notice', () => {
        const first = showNotice('First');
        const second = showNotice('Second');

        first.querySelector('.notice-dismiss').click();

        expect(first.isConnected).toBe(false);
        expect(second.isConnected).toBe(true);
    });
});

describe('confirmDialog', () => {
    // Issue #190: the question is an inline panel in the notice region, not
    // an overlay. The promise contract is unchanged.
    beforeEach(() => {
        document.body.innerHTML = '<main></main>';
    });

    function panel() {
        return document.querySelector('.notice-confirm');
    }

    function buttons() {
        return panel().querySelectorAll('.confirm-actions button');
    }

    test('renders inside the notice region with default labels, OK focused', () => {
        confirmDialog('Sure?');
        expect(panel()).not.toBeNull();
        expect(panel().parentElement.id).toBe('notice-region');
        expect(panel().querySelector('.notice-text').textContent).toBe('Sure?');
        const [cancel, ok] = buttons();
        expect(cancel.textContent).toBe('Cancel');
        expect(ok.textContent).toBe('OK');
        expect(document.activeElement).toBe(ok);
    });

    test('is not a modal — no overlay, no aria-modal, no focus trap', () => {
        confirmDialog('Sure?');
        expect(document.querySelector('.confirm-overlay')).toBeNull();
        expect(document.querySelector('[aria-modal]')).toBeNull();
        expect(panel().getAttribute('role')).toBe('group');
    });

    test('honors custom labels and the danger style', () => {
        confirmDialog('Delete?', {
            confirmLabel: 'Delete', cancelLabel: 'Keep', danger: true,
        });
        const [cancel, ok] = buttons();
        expect(cancel.textContent).toBe('Keep');
        expect(ok.textContent).toBe('Delete');
        expect(ok.classList.contains('danger')).toBe(true);
    });

    test('resolves true and removes the panel when OK is clicked', async () => {
        const answer = confirmDialog('Sure?');
        buttons()[1].click();
        await expect(answer).resolves.toBe(true);
        expect(panel()).toBeNull();
    });

    test('resolves false when Cancel is clicked', async () => {
        const answer = confirmDialog('Sure?');
        buttons()[0].click();
        await expect(answer).resolves.toBe(false);
        expect(panel()).toBeNull();
    });

    test('Escape inside the panel resolves false', async () => {
        const answer = confirmDialog('Sure?');
        panel().dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })
        );
        await expect(answer).resolves.toBe(false);
        expect(panel()).toBeNull();
    });

    test('Escape elsewhere on the page leaves the question standing', async () => {
        confirmDialog('Sure?');
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        expect(panel()).not.toBeNull();
    });

    test('asking again answers the standing question no and replaces it', async () => {
        const first = confirmDialog('First?');
        const second = confirmDialog('Second?');

        await expect(first).resolves.toBe(false);
        expect(document.querySelectorAll('.notice-confirm')).toHaveLength(1);
        expect(panel().querySelector('.notice-text').textContent).toBe('Second?');

        buttons()[1].click();
        await expect(second).resolves.toBe(true);
    });

    test('a notice shown alongside the question keeps both visible', () => {
        showNotice('Heads up', 'info');
        confirmDialog('Sure?');
        expect(document.querySelectorAll('#notice-region > *')).toHaveLength(2);
    });
});

/**
 * promptDialog and the data-confirm form hook (issue #339, items 5 and 9).
 *
 * promptDialog is the in-page replacement for the browser's prompt(); the
 * form hook is what lets a page with no JavaScript of its own — the battle
 * list — still ask before a destructive post.
 */
describe('promptDialog', () => {
    let promptDialog;

    beforeEach(() => {
        promptDialog = window.promptDialog;
    });

    function panel() {
        return document.querySelector('.notice-prompt');
    }

    function field() {
        return panel().querySelector('.notice-prompt-input');
    }

    function buttons() {
        return panel().querySelectorAll('.confirm-actions button');
    }

    test('asks in the notice region, not through the browser', () => {
        promptDialog('Cause of death:');
        expect(panel().parentElement.id).toBe('notice-region');
        expect(panel().querySelector('.notice-text').textContent)
            .toContain('Cause of death:');
        expect(document.activeElement).toBe(field());
    });

    test('resolves with the trimmed text when confirmed', async () => {
        const answer = promptDialog('Name:');
        field().value = '  Deep Wells  ';
        buttons()[1].click();
        await expect(answer).resolves.toBe('Deep Wells');
    });

    test('resolves null when cancelled, so callers read it like prompt()', async () => {
        const answer = promptDialog('Name:');
        buttons()[0].click();
        await expect(answer).resolves.toBeNull();
    });

    test('Escape inside the panel backs out', async () => {
        const answer = promptDialog('Name:');
        field().dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
        );
        await expect(answer).resolves.toBeNull();
    });

    test('Enter in the field answers with what is in it', async () => {
        const answer = promptDialog('Name:');
        field().value = 'Salt Road';
        field().dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
        );
        await expect(answer).resolves.toBe('Salt Road');
    });

    test('opens on a default value, selected and ready to replace', () => {
        promptDialog('Dice expression:', { defaultValue: '3d6' });
        expect(field().value).toBe('3d6');
    });

    test('labels its buttons and caps the field when asked to', () => {
        promptDialog('Name:', {
            confirmLabel: 'Rename',
            cancelLabel: 'Never mind',
            maxLength: 200,
            placeholder: 'A name',
        });
        expect(buttons()[0].textContent).toBe('Never mind');
        expect(buttons()[1].textContent).toBe('Rename');
        expect(field().maxLength).toBe(200);
        expect(field().placeholder).toBe('A name');
    });

    test('the panel leaves the page behind it usable — nothing is modal', () => {
        promptDialog('Name:');
        expect(panel().getAttribute('role')).toBe('group');
        expect(panel().hasAttribute('aria-modal')).toBe(false);
        expect(document.querySelector('.modal-overlay')).toBeNull();
    });

    test('a standing confirm is answered no when a prompt replaces it', async () => {
        const question = window.confirmDialog('Sure?');
        promptDialog('Name:');
        await expect(question).resolves.toBe(false);
        expect(document.querySelectorAll('.notice-confirm')).toHaveLength(1);
    });

    test('a standing prompt is answered null when a confirm replaces it', async () => {
        const answer = promptDialog('Name:');
        window.confirmDialog('Sure?');
        await expect(answer).resolves.toBeNull();
    });
});

describe('data-confirm forms', () => {
    /** A form the delegated listener should intercept. */
    function deleteForm(extra = '') {
        document.body.innerHTML = `
            <main></main>
            <form id="delete" method="post" action="/delete/"
                  data-confirm="Delete this?" ${extra}>
                <button type="submit">Delete</button>
            </form>
        `;
        const form = document.getElementById('delete');
        form.submit = jest.fn();
        return form;
    }

    /** Let the confirmDialog promise's .then run. */
    const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

    test('asks before posting, and posts once confirmed', async () => {
        const form = deleteForm();
        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        const panel = document.querySelector('.notice-confirm');
        expect(panel.querySelector('.notice-text').textContent).toBe('Delete this?');
        expect(form.submit).not.toHaveBeenCalled();

        panel.querySelectorAll('.confirm-actions button')[1].click();
        await settle();
        expect(form.submit).toHaveBeenCalled();
    });

    test('a declined question leaves the form unposted', async () => {
        const form = deleteForm();
        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        document.querySelectorAll('.notice-confirm .confirm-actions button')[0].click();
        await settle();
        expect(form.submit).not.toHaveBeenCalled();
    });

    test('the confirming button takes its label and danger tint from the form', () => {
        const form = deleteForm('data-confirm-label="Delete" data-confirm-danger="true"');
        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        const ok = document.querySelectorAll('.notice-confirm .confirm-actions button')[1];
        expect(ok.textContent).toBe('Delete');
        expect(ok.classList.contains('danger')).toBe(true);
    });

    test('a form without data-confirm is left alone', () => {
        document.body.innerHTML = `
            <main></main>
            <form id="plain" method="post" action="/go/"></form>
        `;
        const form = document.getElementById('plain');
        const event = new Event('submit', { bubbles: true, cancelable: true });
        form.dispatchEvent(event);
        expect(event.defaultPrevented).toBe(false);
        expect(document.querySelector('.notice-confirm')).toBeNull();
    });
});

describe('a confirmation lands beside the control it is about (#711)', () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <main>
                <table><tbody><tr><td id="cell">
                    <button id="row-delete">Delete</button>
                </td></tr></tbody></table>
            </main>`;
    });

    test('an anchored question is drawn right after the button that raised it', async () => {
        const button = document.getElementById('row-delete');

        const answer = window.confirmDialog('Delete "Mira"?', { anchor: button });

        const panel = document.querySelector('.notice-confirm');
        expect(panel).not.toBeNull();
        expect(button.nextElementSibling).toBe(panel);
        expect(document.getElementById('notice-region')).toBeNull();
        panel.querySelector('.confirm-actions button:last-child').click();
        await expect(answer).resolves.toBe(true);
    });

    test('without an anchor it still goes to the notice region', async () => {
        const answer = window.confirmDialog('Delete everything?');

        const region = document.getElementById('notice-region');
        expect(region).not.toBeNull();
        expect(region.querySelector('.notice-confirm')).not.toBeNull();
        region.querySelector('.confirm-actions button').click();
        await expect(answer).resolves.toBe(false);
    });
});
