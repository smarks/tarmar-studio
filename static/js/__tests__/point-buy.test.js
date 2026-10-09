/**
 * Unit tests for point-buy.js
 */

const { initPointBuy, tallyPointBuy } = require('../point-buy.js');

const TOTAL = 65;
const MIN = 3;
const MAX = 18;

const ATTRIBUTES = ['strength', 'dexterity', 'intelligence', 'wisdom', 'constitution', 'charisma'];

function renderForm(values) {
    const fields = ATTRIBUTES.map((name, index) => `
        <input type="number" class="point-buy-input" name="${name}"
               min="${MIN}" max="${MAX}" value="${values[index]}">
    `).join('');

    document.body.innerHTML = `
        <form id="point-buy-form">
            ${fields}
            <p class="point-buy-tally" id="point-buy-tally">
                <span id="point-buy-spent"></span>
                <span id="point-buy-remaining"></span>
            </p>
            <p class="point-buy-off-budget" id="point-buy-off-budget"></p>
            <button type="submit" id="point-buy-submit">Create</button>
        </form>
    `;
}

function setValue(name, value) {
    const input = document.querySelector(`input[name="${name}"]`);
    input.value = String(value);
    input.dispatchEvent(new Event('input'));
}

const spent = () => document.getElementById('point-buy-spent').textContent;
const remaining = () => document.getElementById('point-buy-remaining').textContent;
const submitDisabled = () => document.getElementById('point-buy-submit').disabled;
const tallyClasses = () => document.getElementById('point-buy-tally').classList;
const offBudgetText = () => document.getElementById('point-buy-off-budget').textContent;

describe('tallyPointBuy', () => {
    test('sums valid values', () => {
        renderForm([11, 11, 11, 11, 11, 10]);
        const inputs = Array.from(document.querySelectorAll('.point-buy-input'));
        expect(tallyPointBuy(inputs, MIN, MAX)).toEqual({ spent: 65, allValid: true });
    });

    test('flags an out-of-range value but still counts it', () => {
        renderForm([19, 11, 11, 11, 11, 2]);
        const inputs = Array.from(document.querySelectorAll('.point-buy-input'));
        expect(tallyPointBuy(inputs, MIN, MAX)).toEqual({ spent: 65, allValid: false });
    });

    test('treats a blank field as unset', () => {
        renderForm(['', 11, 11, 11, 11, 10]);
        const inputs = Array.from(document.querySelectorAll('.point-buy-input'));
        expect(tallyPointBuy(inputs, MIN, MAX)).toEqual({ spent: 54, allValid: false });
    });

    test('treats a field the browser could not parse as unset', () => {
        // A number input sanitizes "eleven" to "", so this holds whether the
        // value survives into .value or not.
        renderForm(['eleven', 11, 11, 11, 11, 10]);
        const inputs = Array.from(document.querySelectorAll('.point-buy-input'));
        expect(tallyPointBuy(inputs, MIN, MAX)).toEqual({ spent: 54, allValid: false });
    });
});

describe('tallyPointBuy value parsing', () => {
    // tallyPointBuy only reads `.value`, so plain objects exercise the parsing
    // rule directly — without a number input's own value sanitization deciding
    // what the string is before the function ever sees it.
    const tally = values => tallyPointBuy(values.map(value => ({ value })), MIN, MAX);

    test('accepts plain whole numbers', () => {
        expect(tally(['14', '12', '12', '11', '10', '6'])).toEqual({
            spent: 65, allValid: true
        });
    });

    test('accepts a signed whole number, as int() does server-side', () => {
        expect(tally(['+14', '12', '12', '11', '10', '6'])).toEqual({
            spent: 65, allValid: true
        });
    });

    test('accepts surrounding whitespace, as .strip() does server-side', () => {
        expect(tally([' 14 ', '12', '12', '11', '10', '6'])).toEqual({
            spent: 65, allValid: true
        });
    });

    test('rejects a fractional value instead of truncating it', () => {
        // parseInt("14.5") would be 14 and would leave the buy looking legal;
        // the server calls int("14.5"), which raises, so this must be invalid.
        expect(tally(['14.5', '12', '12', '11', '10', '6'])).toEqual({
            spent: 51, allValid: false
        });
    });

    test('rejects non-numeric text', () => {
        expect(tally(['eleven', '12', '12', '11', '10', '6'])).toEqual({
            spent: 51, allValid: false
        });
    });
});

describe('initPointBuy', () => {
    test('returns undefined when the form is absent', () => {
        document.body.innerHTML = '<p>no form here</p>';
        expect(initPointBuy({ total: TOTAL, min: MIN, max: MAX })).toBeUndefined();
    });

    test('renders the tally on init', () => {
        renderForm([11, 11, 11, 11, 11, 10]);
        initPointBuy({ total: TOTAL, min: MIN, max: MAX });

        expect(spent()).toBe('65');
        expect(remaining()).toBe('0');
        expect(submitDisabled()).toBe(false);
        expect(tallyClasses().contains('over')).toBe(false);
        expect(tallyClasses().contains('under')).toBe(false);
    });

    test('blocks submit and marks the tally when points are left over', () => {
        renderForm([11, 11, 11, 11, 11, 10]);
        initPointBuy({ total: TOTAL, min: MIN, max: MAX });

        setValue('charisma', 8);

        expect(spent()).toBe('63');
        expect(remaining()).toBe('2');
        expect(submitDisabled()).toBe(true);
        expect(tallyClasses().contains('under')).toBe(true);
    });

    test('blocks submit and marks the tally when overspent', () => {
        renderForm([11, 11, 11, 11, 11, 10]);
        initPointBuy({ total: TOTAL, min: MIN, max: MAX });

        setValue('charisma', 14);

        expect(remaining()).toBe('-4');
        expect(submitDisabled()).toBe(true);
        expect(tallyClasses().contains('over')).toBe(true);
    });

    test('blocks submit when an attribute is out of range even at the right total', () => {
        renderForm([11, 11, 11, 11, 11, 10]);
        initPointBuy({ total: TOTAL, min: MIN, max: MAX });

        // 19 + 8 still totals 65 across the six, but 19 exceeds the max.
        setValue('strength', 19);
        setValue('dexterity', 3);

        expect(remaining()).toBe('0');
        expect(submitDisabled()).toBe(true);
    });

    test('re-enables submit once the buy is valid again', () => {
        renderForm([11, 11, 11, 11, 11, 10]);
        initPointBuy({ total: TOTAL, min: MIN, max: MAX });

        setValue('charisma', 8);
        expect(submitDisabled()).toBe(true);

        setValue('charisma', 10);
        expect(submitDisabled()).toBe(false);
        expect(remaining()).toBe('0');
    });

    test('the returned refresh function recomputes on demand', () => {
        renderForm([11, 11, 11, 11, 11, 10]);
        const refresh = initPointBuy({ total: TOTAL, min: MIN, max: MAX });

        // Set the value without dispatching an event.
        document.querySelector('input[name="wisdom"]').value = '3';

        expect(refresh()).toBe(8);
        expect(remaining()).toBe('8');
    });
});


describe('initPointBuy with allowOffBudget (issue #299)', () => {
    // Admins and DMs may build NPCs the 65-point budget does not describe, so
    // for them an off-budget total warns and leaves the button live. The
    // per-attribute range still gates the button, because the server still
    // refuses a value outside it whatever the role.

    test('an over-budget buy leaves the submit button enabled', () => {
        renderForm([18, 18, 17, 11, 10, 6]);   // 80
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(remaining()).toBe('-15');
        expect(submitDisabled()).toBe(false);
    });

    test('an under-budget buy leaves the submit button enabled', () => {
        renderForm([10, 10, 10, 10, 10, 5]);   // 55
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(submitDisabled()).toBe(false);
    });

    test('the warning names the amount and the direction', () => {
        renderForm([18, 18, 17, 11, 10, 6]);   // 80, 15 over
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(offBudgetText()).toContain('15 points over');
        expect(offBudgetText()).toContain(String(TOTAL));
    });

    test('the warning says under when the buy is short', () => {
        renderForm([10, 10, 10, 10, 10, 5]);   // 55, 10 under
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(offBudgetText()).toContain('10 points under');
    });

    test('a single point reads as "point", not "points"', () => {
        renderForm([11, 11, 11, 11, 11, 11]);  // 66, 1 over
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(offBudgetText()).toContain('1 point over');
        expect(offBudgetText()).not.toContain('1 points');
    });

    test('the warning clears the moment the buy comes back on budget', () => {
        renderForm([18, 18, 17, 11, 10, 6]);   // 80
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(offBudgetText()).not.toBe('');

        setValue('strength', 14);
        setValue('dexterity', 12);
        setValue('intelligence', 12);          // back to 65

        expect(remaining()).toBe('0');
        expect(offBudgetText()).toBe('');
        expect(submitDisabled()).toBe(false);
    });

    test('an on-budget buy never writes a warning', () => {
        renderForm([14, 12, 12, 11, 10, 6]);   // 65
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(offBudgetText()).toBe('');
    });

    test('an out-of-range attribute still disables the button', () => {
        renderForm([19, 18, 16, 11, 10, 6]);   // 80, but STR is above MAX
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(submitDisabled()).toBe(true);
    });

    test('a non-numeric attribute still disables the button', () => {
        renderForm([14, 12, 12, 11, 10, 6]);
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        setValue('charisma', 'eleven');
        expect(submitDisabled()).toBe(true);
    });

    test('an out-of-range attribute writes no warning', () => {
        // The buy is off budget AND illegal; the illegal part is what the
        // player must fix, so the off-budget line would only be noise.
        renderForm([19, 18, 16, 11, 10, 6]);
        initPointBuy({ total: TOTAL, min: MIN, max: MAX, allowOffBudget: true });
        expect(offBudgetText()).toBe('');
    });

    test('without the flag an off-budget buy still disables the button', () => {
        renderForm([18, 18, 17, 11, 10, 6]);   // 80
        initPointBuy({ total: TOTAL, min: MIN, max: MAX });
        expect(submitDisabled()).toBe(true);
    });

    test('without the flag no warning is written even when the element exists', () => {
        renderForm([18, 18, 17, 11, 10, 6]);
        initPointBuy({ total: TOTAL, min: MIN, max: MAX });
        expect(offBudgetText()).toBe('');
    });
});
