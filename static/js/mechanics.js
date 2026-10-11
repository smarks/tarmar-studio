/**
 * The sheet's rules controls (issues #753, #756, #757, #774): tying a Weapon
 * skill to its weapon, the skill check, a Game Master's coin and mana, and
 * learning a spell.
 *
 * Every control is a `[data-mechanics-form]` block naming the URL key it
 * posts to. The module reads the block's named inputs, posts them, and then
 * either reloads the page (a change to the record, which the server renders)
 * or writes the server's check into the block's result element, which stays
 * until the next roll replaces it. A refusal is written beside the control
 * and stays until the next attempt; nothing here dismisses itself.
 *
 * It computes nothing: every die, modifier and total is the server's.
 * Clicks are delegated from the document, so a skill row the add-years
 * response inserts later works without re-binding.
 *
 * postWithCsrf/csrfToken come from api.js, which base.html loads in <head>.
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
}

(function () {
    const configBlock = document.getElementById('mechanics-config');
    if (!configBlock) return;
    const CONFIG = JSON.parse(configBlock.textContent);

    /** The block's named controls as a request body; blanks are left out. */
    function formBody(block) {
        const body = {};
        block.querySelectorAll('[name]').forEach(function (control) {
            if (control.value !== '') body[control.name] = control.value;
        });
        return body;
    }

    function showError(block, message) {
        const slot = block.querySelector('.mechanics-error');
        if (!slot) return;
        slot.textContent = message;
        slot.hidden = !message;
    }

    /** One line for the check, from the server's numbers only. */
    function checkText(check) {
        const dice = check.stated
            ? '2d6 stated by a Game Master = ' + check.dice_total
            : '2d6 [' + check.faces.join(', ') + '] = ' + check.dice_total;
        let text = check.skill + ': ' + dice + ' ' + formatSigned(check.attribute_modifier) +
            ' ' + check.attribute.slice(0, 3).toUpperCase() + ' (' + check.attribute_score + ') ' +
            formatSigned(check.level) + ' level' +
            // A focused Knowledge field (#808), then the injury table's -1/-2
            // (#840), each named only when it counts, so the line adds up.
            (check.focus_bonus ? ' ' + formatSigned(check.focus_bonus) + ' focus' : '') +
            (check.injury_penalty ? ' ' + formatSigned(check.injury_penalty) + ' injured' : '') +
            ' = ' + check.total;
        // A player's roll carries no difficulty (the Game Master sets it) and
        // so no outcome; a natural is named either way.
        if (check.difficulty !== null) text += ' vs ' + check.difficulty;
        if (check.outcome) text += ': ' + check.outcome;
        if (check.natural) text += ' (' + check.natural + ')';
        // What a Game Master called the check past (#863, #840 review).
        if (check.game_master_call) {
            text += " (a Game Master's call: " + check.game_master_call + ')';
        }
        return text;
    }

    async function submit(block, button) {
        const url = CONFIG[block.dataset.mechanicsForm];
        if (!url) return;
        button.disabled = true;
        showError(block, '');
        try {
            const response = await postWithCsrf(url, csrfToken(), formBody(block));
            const data = await response.json();
            if (!response.ok || !data.success) {
                showError(block, data.error || 'That was refused.');
                return;
            }
            if (block.dataset.reload === 'true') {
                pageNavigation.reload();
                return;
            }
            // The change's history line joins Recent Changes (issue #792):
            // character-edit.js's painter, when the sheet has loaded it.
            if (typeof paintSheet === 'function') paintSheet(data);
            const result = document.getElementById(block.dataset.result);
            if (result && data.check) {
                result.textContent = checkText(data.check);
                result.className = 'skill-check-result ' + (data.check.outcome || 'rolled');
                result.hidden = false;
            }
        } catch (error) {
            showError(block, 'Could not reach the server: ' + error.message);
        } finally {
            button.disabled = false;
        }
    }

    document.addEventListener('click', function (event) {
        const button = event.target.closest('.mechanics-submit');
        if (!button) return;
        const block = button.closest('[data-mechanics-form]');
        if (!block) return;
        event.preventDefault();
        submit(block, button);
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { checkText: checkText, formBody: formBody };
    }
})();
