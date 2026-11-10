/**
 * E2E spot check for issue #182: a stranger is refused before the body is read.
 *
 * The unit test (characters/tests/test_edit_right_first.py) holds every
 * character endpoint to the order; this proves it from a real signed-in
 * browser against the running server for one of them, the sheet's field
 * update: a body that is not JSON, posted at another player's character,
 * answers 403 and not 400, while the same body at the player's own character
 * answers 400. The other player's character is made through the Django shell
 * for this worker's watcher account and removed afterwards.
 */

const { test, expect, TEST_WATCHER, createCharacter } = require('./fixtures');
const { readDjangoShell, runDjangoShell } = require('./django');
const { uniqueName } = require('./unique-name');

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

function createCharacterFor(username, name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from common.test_helpers import create_character
character = create_character(get_user_model().objects.get(username=${JSON.stringify(username)}), name=${JSON.stringify(name)})
print('CHARACTER', character.pk)
`);
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no id in: ${output}`).not.toBeNull();
    return Number(match[1]);
}

function deleteCharacter(characterId) {
    runDjangoShell(`
from characters.models import Character
Character.objects.filter(pk=${Number(characterId)}).delete()
`);
}

test('a bad body at another player\'s character is 403, at your own 400', async ({ authenticatedPage: page }, testInfo) => {
    const theirs = createCharacterFor(TEST_WATCHER.username, uniqueName('Theirs'));
    try {
        // The page's own character, made as every spec makes one, so it is
        // the signed-in player's.
        await createCharacter(page);
        const mine = Number(page.url().match(/\/characters\/(\d+)\//)[1]);

        expect(await postRaw(page, `/tarmar/characters/${theirs}/update-field/`, '{not json')).toBe(403);
        expect(await postRaw(page, `/tarmar/characters/${mine}/update-field/`, '{not json')).toBe(400);
    } finally {
        deleteCharacter(theirs);
    }
});
