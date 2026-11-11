/**
 * E2E spot check for issue #182: a stranger is refused before the body is read.
 *
 * The unit test (characters/tests/test_edit_right_first.py) holds every
 * character endpoint to the order; this proves it from a real signed-in
 * browser against the running server for one of them, the sheet's field
 * update: a body that is not JSON, posted at another player's character,
 * answers 403 and not 400, while the same body at the player's own character
 * answers 400. The other player is an account of this spec's own (issue
 * #889), made with its character through the Django shell and deleted
 * afterwards, so the suite's shared accounts are left as other specs expect.
 */

const { test, expect, createCharacter } = require('./fixtures');
const { readDjangoShell, runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');
const { uniqueName } = require('./unique-name');

const strangerFor = (testInfo) => specAccount('bodystranger', 'bodystranger-pass-182', testInfo);

/** POST a raw body from the page with its CSRF token; the status alone matters. */
async function postRaw(page, path, body) {
    return page.evaluate(async ({ path, body }) => {
        const token = document.querySelector('meta[name="csrf-token"]').content;
        const response = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
            credentials: 'same-origin',
            body,
        });
        return response.status;
    }, { path, body });
}

/** The stranger's account and one character of theirs, made in one shell run. */
function createStrangerWithCharacter(account, name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from common.test_helpers import create_character
user, _created = get_user_model().objects.get_or_create(username=${JSON.stringify(account.username)})
user.set_password(${JSON.stringify(account.password)})
user.save()
character = create_character(user, name=${JSON.stringify(name)})
print('CHARACTER', character.pk)
`);
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no id in: ${output}`).not.toBeNull();
    return Number(match[1]);
}


test('a bad body at another player\'s character is 403, at your own 400', async ({ authenticatedPage: page }, testInfo) => {
    const stranger = strangerFor(testInfo);
    const theirs = createStrangerWithCharacter(stranger, uniqueName('Theirs'));
    try {
        // The page's own character, made as every spec makes one, so it is
        // the signed-in player's.
        await createCharacter(page);
        const mine = Number(page.url().match(/\/characters\/(\d+)\//)[1]);

        expect(await postRaw(page, `/tarmar/characters/${theirs}/update-field/`, '{not json')).toBe(403);
        expect(await postRaw(page, `/tarmar/characters/${mine}/update-field/`, '{not json')).toBe(400);
    } finally {
        // The stranger goes with their character (the FK cascades).
        runDjangoShell(deleteSpecAccountsScript([stranger]));
    }
});
