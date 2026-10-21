/**
 * Attack Resolution panel controller (issue #101).
 *
 * A separate module from character-edit.js on purpose: the two share a page
 * but nothing else, and issue #63's whole point was that untestable page logic
 * belongs in files Jest can reach. This one is small because it is meant to
 * be: it collects the form's values, posts them, and prints the numbers that
 * come back.
 *
 * It computes nothing. Every Target Number, bonus, die, multiplier and damage
 * figure below is read straight off the response — the same rule the skill
 * rows follow (issues #146, #154). If a number is not in the payload, the fix
 * is to add it server-side, not to derive it here.
 *
 * postWithCsrf/csrfToken/uiNotify come from api.js, which base.html loads in
 * <head>.
 * Node/Jest has no shared script scope, so require it there.
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
}

(function () {
    const configBlock = document.getElementById('attack-panel-config');
    const form = document.getElementById('attack-form');
    // The panel renders its form only when the character owns a catalog
    // weapon, so both of these are legitimately absent on some sheets.
    if (!configBlock || !form) return;

    const CONFIG = JSON.parse(configBlock.textContent);
    const resultBox = document.getElementById('attack-result');
    const outcomeHeading = document.getElementById('attack-outcome');
    const breakdownList = document.getElementById('attack-breakdown');
    const diceLogList = document.getElementById('attack-dice-log');
    const resolveButton = document.getElementById('attack-resolve-btn');

    // Sign a modifier the way the sheet's combat tiles do: +3, +0, -2. The
    // one definition is api.js's formatSigned (#338 item 22).
    const signed = formatSigned;

    /** The request body, read off the form's controls. */
    function attackParams() {
        return {
            // No skill: the server decides which skill applies to the weapon
            // from the character's record (issue #753).
            weapon_index: document.getElementById('attack-weapon').value,
            armour_tier: document.getElementById('attack-armour-tier').value,
            defender_stops: document.getElementById('attack-stops').value,
            shield_item_id: document.getElementById('attack-shield').value,
            defender_dodge: document.getElementById('attack-dodge').value,
            situational: document.getElementById('attack-situational').value
        };
    }

    /**
     * The itemized lines, as [label, value] pairs.
     *
     * Built as data rather than as a template string so the renderer can use
     * textContent for every value — nothing here is ever parsed as markup.
     */
    function breakdownRows(attack) {
        const targetNumber = attack.target_number;
        const toHit = attack.to_hit;
        const damage = attack.damage;
        const skill = attack.skill || {};
        const skillText = skill.name
            ? skill.name + ' level ' + skill.level + ' (' + skill.rule + ')'
            : 'untrained';
        const rows = [
            ['Skill', skillText],
            ['Target Number', targetNumber.base + ' base + ' + targetNumber.shield_bonus +
                ' shield + ' + targetNumber.defender_dodge + ' dodge = ' + targetNumber.total],
            ['To hit', signed(toHit.dex_modifier) + ' DEX ' + signed(toHit.skill_bonus) +
                ' skill ' + signed(toHit.strength_fit_penalty) + ' strength ' +
                signed(toHit.weapon_mod) + ' weapon ' + signed(toHit.situational) +
                ' situational' +
                // The injury table's -1/-2 (issue #840), named only when it bites.
                (toHit.injury_penalty ? ' ' + signed(toHit.injury_penalty) + ' injured' : '') +
                ' = ' + signed(toHit.total)],
            ['Attack roll', 'd20 ' + attack.attack_roll.die + ' ' + signed(toHit.total) +
                ' = ' + attack.attack_roll.total + ' vs ' + targetNumber.total]
        ];
        if (attack.confirm) {
            rows.push(['Confirmation', 'd20 ' + attack.confirm.die + ' = ' + attack.confirm.total +
                (attack.confirm.hit ? ' — confirmed' : ' — not confirmed')]);
        }
        if (attack.fumble) {
            rows.push(['Fumble', 'd6 ' + attack.fumble.die + ' — ' + attack.fumble.label +
                (attack.fumble.effect ? ' (' + attack.fumble.effect + ')' : '')]);
        }
        if (damage.multiplier > 0) {
            rows.push(['Damage dice', '[' + damage.rolls.join(', ') + '] × ' + damage.multiplier +
                ' roll(s) ' + signed(damage.weapon_mod) + ' = ' + damage.raw_total]);
            rows.push(['Armour', damage.applied_stops + ' of ' + damage.stops + ' stops applied' +
                (damage.hybrid_bypass ? ' (hybrid bypass)' : '')]);
            rows.push(['Damage through', String(damage.final)]);
        }
        if (attack.severe) rows.push(['Severe', 'Confirmed severe critical']);
        if (attack.bleeding) rows.push(['Bleeding', 'Reported for the GM to adjudicate']);
        if (damage.multiplier > 0) {
            rows.push(['Reaches Body', attack.reaches_body ? 'yes' : 'no']);
        }
        return rows;
    }

    /** Paint one resolved attack into the result box. */
    function renderAttack(attack) {
        outcomeHeading.textContent = attack.attack_roll.outcome;
        outcomeHeading.className = 'attack-outcome ' + attack.attack_roll.outcome.replace(/\s+/g, '-');

        breakdownList.textContent = '';
        breakdownRows(attack).forEach(function (row) {
            const item = document.createElement('li');
            const label = document.createElement('span');
            label.className = 'attack-label';
            label.textContent = row[0];
            const value = document.createElement('span');
            value.className = 'attack-value';
            value.textContent = row[1];
            item.appendChild(label);
            item.appendChild(value);
            breakdownList.appendChild(item);
        });

        diceLogList.textContent = '';
        attack.dice_log.forEach(function (line) {
            const entry = document.createElement('li');
            entry.textContent = line;
            diceLogList.appendChild(entry);
        });

        resultBox.hidden = false;
    }

    /**
     * Put the attack's line at the top of Recent attacks (issue #792).
     *
     * The log was rendered at load, so it said "No attacks resolved yet."
     * beside the result of the first one. The line is the server's own
     * rendering, the one a reload shows.
     *
     * @param {string|undefined} entryHtml - The rendered log line.
     */
    function appendToCombatLog(entryHtml) {
        const list = document.getElementById('combat-log-list');
        if (!list || !entryHtml) return;
        const holder = document.createElement('template');
        holder.innerHTML = String(entryHtml).trim();
        list.insertBefore(holder.content.firstElementChild, list.firstChild);
        // The record keeps the latest MAX_COMBAT_LOG_ENTRIES, and so does the
        // page, so it never shows a line a reload would not.
        const limit = CONFIG.maxCombatLogEntries;
        while (limit && list.children.length > limit) list.lastElementChild.remove();
        const empty = document.querySelector('.no-combat-log');
        if (empty) empty.hidden = true;
    }

    async function resolveAttack() {
        resolveButton.disabled = true;
        try {
            const response = await postWithCsrf(CONFIG.attackUrl, csrfToken(), attackParams());
            const data = await response.json();
            if (!response.ok || !data.success) {
                uiNotify(data.error || 'Could not resolve the attack.');
                return;
            }
            renderAttack(data.attack);
            appendToCombatLog(data.combat_log_entry_html);
            // The swing's dice joined the character's dice log (issue #147):
            // character-edit.js's painter, when the sheet has loaded it.
            if (typeof paintSheet === 'function') paintSheet(data);
        } catch (error) {
            uiNotify('Could not resolve the attack: ' + error.message);
        } finally {
            resolveButton.disabled = false;
        }
    }

    resolveButton.addEventListener('click', resolveAttack);
})();
