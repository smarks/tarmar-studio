/**
 * Point-buy tally for character creation.
 *
 * Keeps a running "points remaining" readout beside the six core attribute
 * inputs and blocks the submit button while the buy is invalid. The readout is
 * state-driven and permanent — it renders the current totals on every input and
 * never appears or disappears on a timer.
 *
 * The server re-validates every submission (characters/views.py
 * _parse_point_buy_attributes); this is convenience only, not enforcement.
 */

// What the server accepts as a whole number (characters/views.py calls int() on
// the stripped value). Anything else — "", "14.5", "eleven" — is a 400 there, so
// it must not look submittable here either. parseInt is deliberately not used:
// it would read "14.5" as 14 and leave the button enabled on a buy the server
// then rejects.
const WHOLE_NUMBER = /^[+-]?\d+$/;

/**
 * Sum the attribute inputs, flagging anything the server would reject.
 * A blank, fractional or non-numeric field counts as unset: it makes the buy
 * invalid and contributes nothing to the total.
 * @param {HTMLInputElement[]} inputs
 * @param {number} min - Lowest allowed value per attribute.
 * @param {number} max - Highest allowed value per attribute.
 * @returns {{spent: number, allValid: boolean}}
 */
function tallyPointBuy(inputs, min, max) {
    let spent = 0;
    let allValid = true;
    inputs.forEach(input => {
        const raw = String(input.value).trim();
        if (!WHOLE_NUMBER.test(raw)) {
            allValid = false;
            return;
        }
        const value = Number(raw);
        if (value < min || value > max) {
            allValid = false;
        }
        spent += value;
    });
    return { spent, allValid };
}

/**
 * Wire up the point-buy form's tally and submit gating.
 * @param {object} options
 * @param {number} options.total - Points the six attributes must total.
 * @param {number} options.min - Lowest allowed value per attribute.
 * @param {number} options.max - Highest allowed value per attribute.
 * @param {boolean} [options.allowOffBudget] - True for an admin or DM, who may
 *   submit a total other than `total` (issue #299). They get a warning naming
 *   the shortfall or excess and a live submit button; everyone else keeps the
 *   button disabled until the buy is exact. The per-attribute range still
 *   gates the button for everyone.
 * @returns {(function(): number)|undefined} A refresh function that recomputes
 *   the readouts and returns the points still unspent (negative if overspent),
 *   or undefined when the form isn't on this page.
 */
function initPointBuy({ total, min, max, allowOffBudget = false }) {
    const form = document.getElementById('point-buy-form');
    if (!form) return undefined;

    const inputs = Array.from(form.querySelectorAll('.point-buy-input'));
    const tally = document.getElementById('point-buy-tally');
    const spentReadout = document.getElementById('point-buy-spent');
    const remainingReadout = document.getElementById('point-buy-remaining');
    const submitButton = document.getElementById('point-buy-submit');
    const offBudgetWarning = document.getElementById('point-buy-off-budget');

    function refresh() {
        const { spent, allValid } = tallyPointBuy(inputs, min, max);
        const remaining = total - spent;

        if (spentReadout) spentReadout.textContent = String(spent);
        if (remainingReadout) remainingReadout.textContent = String(remaining);
        if (tally) {
            tally.classList.toggle('over', remaining < 0);
            tally.classList.toggle('under', remaining > 0);
        }
        if (offBudgetWarning) {
            // Rewritten from the current inputs every time, and emptied the
            // moment the buy comes back on budget: state, not an event. Gated
            // on the flag as well as the element, so the copy ("you may create
            // it anyway") can never reach someone the budget actually binds.
            if (allowOffBudget && allValid && remaining !== 0) {
                const amount = Math.abs(remaining);
                const direction = remaining < 0 ? 'over' : 'under';
                const points = amount === 1 ? 'point' : 'points';
                offBudgetWarning.textContent =
                    `This buy is ${amount} ${points} ${direction} the ` +
                    `${total}-point budget. You may create it anyway; the ` +
                    `character's dice log will record that it is off budget.`;
            } else {
                offBudgetWarning.textContent = '';
            }
        }
        if (submitButton) {
            // An off-budget total blocks the button only for users the budget
            // binds. A value outside the per-attribute range blocks everyone —
            // the server refuses that from an admin and a DM too.
            submitButton.disabled =
                !allValid || (remaining !== 0 && !allowOffBudget);
        }
        return remaining;
    }

    inputs.forEach(input => {
        input.addEventListener('input', refresh);
        input.addEventListener('change', refresh);
    });

    refresh();
    return refresh;
}

// Export for testing (Node.js environment)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { initPointBuy, tallyPointBuy };
}
