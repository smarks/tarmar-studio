// @ts-check
/**
 * The background a character holds until it is rolled, and the rows the sheet
 * offers for it, are the loaded canon's (issue #66). Every value is read where
 * the server reads it (``characters.rules.character_rules()``), never typed
 * here, so the spec holds under the default canon and the Tarmar canon alike,
 * and under a canon whose tables use other labels.
 */

const { test, expect, loginAs, TEST_ADMIN } = require('./fixtures');
const { readDjangoJson } = require('./django');
const { loadedCharacterRules } = require('./character-rules');

/**
 * A character nobody rolled a background for, as one started in a Games
 * Master session is: the model's own defaults, with nothing on the record.
 * @returns {number} its id
 */
function unrolledCharacterId() {
    return readDjangoJson(`
import json
from django.contrib.auth import get_user_model
from characters.models import Character
user = get_user_model().objects.get(username='${TEST_ADMIN.username}')
character = Character.objects.create(user=user, name='Default Testwright', provenance_recorded=False, location_recorded=False, wealth_recorded=False)
print("CHARACTER " + json.dumps(character.pk))
`, 'CHARACTER');
}

test.describe('The background fields follow the loaded canon (#66)', () => {
    test('an unrolled character holds the canon\'s default rows, and the selects offer its rows', async ({ page }) => {
        const rules = loadedCharacterRules();
        const id = unrolledCharacterId();

        // A Game Master may set every background field, so each renders as a control.
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${id}/`);
        const socialClass = page.locator('[data-field="provenance_class"] select');
        const origin = page.locator('[data-field="location"] select');
        const wealth = page.locator('[data-field="wealth_level"] select');

        await expect(socialClass).toHaveValue(rules.defaults.social_class);
        await expect(page.locator('[data-field="provenance_detail"] input'))
            .toHaveValue(rules.defaults.provenance_detail);
        await expect(origin).toHaveValue(rules.defaults.location);
        await expect(wealth).toHaveValue(rules.defaults.wealth);
        const coin = rules.defaults.starting_coin;
        await expect(page.locator('[data-field="starting_coin"] input'))
            .toHaveValue(coin === null ? '' : String(coin));

        // The selects offer exactly the canon's rows, in the tables' order.
        const values = (select) => select.locator('option').evaluateAll(
            (options) => options.map((option) => /** @type {HTMLOptionElement} */ (option).value)
        );
        expect(await values(socialClass)).toEqual(rules.social_class_labels);
        expect(await values(origin)).toEqual(rules.origins.map((row) => row.label));
        expect(await values(wealth)).toEqual(rules.wealth_labels);
    });
});
