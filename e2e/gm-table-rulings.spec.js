/**
 * E2E for the table rulings a Game Master states (issue #600).
 *
 * No Ollama: the rulings are recorded through gm.testing.run_actions -- the
 * same executor a played turn's plan runs -- on a seeded session. What is
 * checked is the "Table rulings" section, which sits in the Campaign tab with
 * the table's other records since issue #789 (it was in the Characters tab).
 */

const { execSync } = require('child_process');
const { test, expect, loginAs, TEST_ADMIN } = require('./fixtures');

function djangoShell(script) {
    return execSync(`uv run python manage.py shell -c "${script.replace(/"/g, '\\"')}"`, {
        cwd: process.cwd(),
        encoding: 'utf8',
        env: { ...process.env, DEBUG: 'true' },
    });
}

const seedRulings = `
from django.contrib.auth import get_user_model
from gm.models import GMSession
from gm.testing import run_actions
user = get_user_model().objects.get(username='${TEST_ADMIN.username}')
session = GMSession.objects.create(user=user, name='Rulings E2E', mode='aide')
run_actions(session, {'action': 'record_house_rule', 'topic': 'critical hits', 'rule': 'a natural 20 crits after a confirmation roll'})
run_actions(session, {'action': 'record_house_rule', 'topic': 'fumbles', 'rule': 'a roll of 3 drops the weapon'})
run_actions(session, {'action': 'record_house_rule', 'topic': 'Critical hits', 'rule': 'a natural 20 just crits, with no confirmation roll'})
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

test.describe('GM panel — table rulings', () => {
    test('the pane lists the rulings in force, a replaced one gone', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        const match = djangoShell(seedRulings).match(/SESSION (\d+)/);
        expect(match).not.toBeNull();

        await page.goto(`/tarmar/gm/?session=${match[1]}`);
        await page.click('#gm-panel-tab-campaign');

        const rulings = page.locator('#gm-table-rulings li');
        await expect(page.locator('#gm-table-rulings-heading')).toHaveText('Table rulings');
        await expect(rulings).toHaveCount(2);
        await expect(rulings.nth(0)).toContainText('fumbles: a roll of 3 drops the weapon');
        await expect(rulings.nth(1)).toContainText('Critical hits: a natural 20 just crits, with no confirmation roll');
        await expect(page.locator('#gm-table-rulings')).not.toContainText('after a confirmation roll');
    });
});
