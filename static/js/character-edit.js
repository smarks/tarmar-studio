/**
 * Character edit page controller.
 *
 * This is the ~1,100 lines that used to sit in a <script> block inside
 * characters/templates/characters/character_edit.html (issue #63). Living in
 * the template put it out of Jest's reach, so the page's most intricate client
 * logic -- the skill allocation flow, the experience-term panel and its live
 * log append, and the whole equipment editor -- was covered by nothing but a
 * handful of e2e specs.
 *
 * The move was deliberately mechanical: the code below is the template's code,
 * comments included. Only four things changed, and none of them is a rewrite:
 *
 *   1. Values that used to be rendered into the JavaScript by Django ({% url %}
 *      calls, {{ csrf_token }}, {{ character.age }}, the survival/aging tables,
 *      the initial equipment list) now arrive in one json_script block, read
 *      once below as CHARACTER_EDIT_CONFIG.
 *   2. The eleven CSRF-bearing fetch() calls go through postWithCsrf from
 *      api.js, which builds the identical request (issue #111). The token
 *      itself comes from api.js's csrfToken() (issue #338) -- this file used
 *      to reach for it three different ways: a config key copied into two
 *      sub-configs, a hidden form input read straight out of the DOM, and a
 *      module constant aliasing the config key.
 *   3. showNotice/confirmDialog became uiNotify/uiConfirm -- the same api.js
 *      wrappers, which pass straight through to notify.js in the browser and
 *      fall back to alert/confirm under Node.
 *   4. The DM-only block was wrapped in {% if request.user.can_manage_users %}.
 *      It is unconditional here: both handlers already bound with `?.`, and the
 *      buttons they look for are the ones that template condition renders, so a
 *      non-DM page binds nothing either way.
 *
 * csrfToken/postWithCsrf/postJson/uiNotify/uiConfirm come from api.js, which
 * base.html loads in
 * <head> -- ahead of this file's <script> tag. Node/Jest has no shared script
 * scope, so require api.js there; either way the names end up on the global
 * object.
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
}

// Everything Django hands this page. CharacterEditView builds the dict; the
// template emits it with |json_script, which is inert to markup injection in
// a way that interpolating into a JavaScript string literal never was.
const CHARACTER_EDIT_CONFIG = JSON.parse(
    document.getElementById('character-edit-config').textContent
);

// Each free-text field's limit, as the server holds it (issue #928):
// characters.constants.CHARACTER_TEXT_LIMITS, never a number typed here.
const TEXT_LIMITS = CHARACTER_EDIT_CONFIG.textLimits;

// Each number input's bound, as the server holds it (issue #938):
// characters.views.characters.character_number_limits(). A range symmetric
// about zero is given by its magnitude.
const NUMBER_LIMITS = CHARACTER_EDIT_CONFIG.numberLimits;

// ---- Delete character confirmation ----
// Was its own inline <script> at the foot of the content block.
(function () {
    const form = document.getElementById('delete-character-form');
    if (!form) return;
    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        if (await uiConfirm('Delete this character? You can restore it later from the character list (Show deleted).', { danger: true, confirmLabel: 'Delete' })) {
            form.submit();  // bypasses this listener, so no loop
        }
    });
})();

// Refresh fatigue/body and the injury-thresholds table from a response that
// carries them (fatigue/body/injury_thresholds keys, same shape everywhere
// they appear: attribute-field autosave and the add-term response both use
// this). Pulled out so both call sites stay in sync instead of duplicating
// the DOM-writing logic (issue #163).
/**
 * Write a server value into an element's text, the way the template does.
 *
 * A null value is a number the server would not state -- a pool of an
 * incomplete character, a weight with no STR -- and reads "—" as the page
 * renders it; writing null as text used to leave the cell blank (issue #766).
 * An absent key leaves the element alone.
 *
 * @param {string} elementId - The element's id.
 * @param {*} value - The server's value.
 * @param {string} [empty] - What null reads as.
 */
function paintText(elementId, value, empty = '—') {
    if (value === undefined) return;
    const element = document.getElementById(elementId);
    if (element) element.textContent = value === null ? empty : String(value);
}

function refreshFatigueBodyInjury(data) {
    paintText('fatigue', data.fatigue);
    paintText('body', data.body);
    if (data.injury_thresholds_html !== undefined) {
        // The server's own rendering of the readout, swapped in whole. It
        // used to be written cell by cell, which could only ever update a
        // table that was already there: an incomplete character has no
        // thresholds and renders a line saying so instead (issue #639), and
        // recording the last attribute or rolling Fatigue crosses that line
        // without a reload.
        const section = document.getElementById('injury-thresholds');
        if (section) {
            section.innerHTML = data.injury_thresholds_html;
            // The survival save's control (issue #839) comes and goes with
            // this block; a swapped-in one is wired like those at load.
            section.querySelectorAll('.sheet-roll-btn').forEach(bindSheetRoll);
        }
    }
}

/**
 * Repaint the sheet's roll controls from the server's offers (issue #748).
 *
 * A control rendered at load can stop being offered (a player who buys the
 * last score, or one who has bought any, cannot roll), and one that rolled
 * missing scores becomes a reroll of recorded ones that must be confirmed
 * (a Game Master who types the last score). A response without offers
 * leaves the controls alone.
 *
 * @param {object|undefined} offers - {kind: {available, confirm}}.
 */
function paintSheetRolls(offers) {
    if (!offers) return;
    for (const [kind, offer] of Object.entries(offers)) {
        const button = document.querySelector(`.sheet-roll-btn[data-roll-kind="${kind}"]`);
        if (!button) continue;
        button.hidden = !offer.available;
        if (offer.confirm) {
            button.dataset.confirm = offer.confirm;
        } else {
            delete button.dataset.confirm;
        }
    }
}

/**
 * Turn an input cell into the read-only value and its reason, the way the
 * server renders a value this viewer may no longer set (issue #748).
 *
 * @param {string} field - The model field.
 * @param {string} value - The value just saved.
 * @param {string} reason - Why it is locked, from the server.
 */
function lockFieldCell(field, value, reason) {
    const cell = document.querySelector(`.editable-field[data-field="${field}"]`);
    if (!cell) return;
    const text = document.createElement('span');
    text.dataset.readonlyField = field;
    text.textContent = value;
    const note = document.createElement('span');
    note.className = 'formula field-locked-reason';
    note.dataset.lockedField = field;
    note.textContent = reason;
    cell.replaceWith(text, ' ', note);
}

// Kept so a live repaint can tell the saver what the field now holds:
// otherwise the next blur sees a value that differs from the one the
// page loaded with and re-posts it (issue #638).
const autosave = initAutosave({
    updateUrl: CHARACTER_EDIT_CONFIG.updateFieldUrl,
    validate: (field, value) => {
        // A character needs a name; say so rather than quietly restoring the
        // old one (issue #339, item 23).
        if (field === 'name' && !value) return 'A character needs a name.';
        return true;
    },
    onSuccess: (field, value, data) => {
        // Update derived values; the injury readout and the pools are
        // paintSheet's, below.
        paintText('physical', data.physical);
        paintText('mental', data.mental);
        // Editing DEX moves aim and dodge (issue #634); STR, DEX and CON
        // move the encumbrance bar and the Movement row (issue #763).
        paintCombatDexTiles(data);
        paintMovement(data);
        // Changing the Origin or INT moves the literacy row (issue #740).
        paintLiteracyRow(data);
        // Changing the Origin moves the skill it grants (issue #754).
        if (data.skills_html !== undefined) {
            document.getElementById('skills-list').innerHTML = data.skills_html;
            document.getElementById('allocated-points').textContent = data.total_allocated_points;
        }
        // A Game Master's ruling shows at "Consult GM" only (#801), and moving
        // the Origin off it withdraws the ruling.
        if (field === 'location') {
            const rulingRow = document.getElementById('origin-ruling-row');
            if (rulingRow) rulingRow.hidden = value !== 'Consult GM';
        }
        paintFieldInput('origin_ruling', data.origin_ruling);
        if (data.location_recorded !== undefined) {
            const unrolled = document.getElementById('origin-not-rolled');
            if (unrolled) unrolled.hidden = data.location_recorded;
        }
        if (data.height_display !== undefined) {
            // A height that does not parse, or none, reads "—" (issue #805).
            document.getElementById('height_display').textContent = '(' + (data.height_display || '—') + ')';
        }
        paintText('weight_lbs', data.weight_lbs);
        if (data.starting_coin !== undefined) {
            // A Rich character's coin is "Consult GM" until set (issue #749).
            paintFieldInput('starting_coin', data.starting_coin, 'Consult GM');
        }
        // The coin moves the purse and the Wealth table (review of #757).
        refreshPurse(data);
        if (data.level !== undefined) {
            document.getElementById('character-level').textContent = data.level;
        }
        // The roll controls follow the record, not the page load (#748).
        paintSheetRolls(data.sheet_rolls);
        // A player's bought score locks once recorded (issue #748): the cell
        // becomes the text and reason a reload would render.
        if (data.locked_reason) lockFieldCell(field, value, data.locked_reason);
        // Completion, points, the history line (issues #764, #792), and the
        // name everywhere the sheet prints it, from the saved record (#903).
        paintSheet(data);
    }
});

// Skills system - all constants from Python (single source of truth)
const SKILLS_CONFIG = {
    addYearsUrl: CHARACTER_EDIT_CONFIG.addYearsUrl,
    allocateUrl: CHARACTER_EDIT_CONFIG.allocateUrl,
    renameSkillUrl: CHARACTER_EDIT_CONFIG.renameSkillUrl,
    currentAge: CHARACTER_EDIT_CONFIG.currentAge,
    currentYears: CHARACTER_EDIT_CONFIG.currentYears,
    survivalTargets: CHARACTER_EDIT_CONFIG.survivalTargets,
    agingByAge: CHARACTER_EDIT_CONFIG.agingByAge,
};

const SURVIVAL_TARGETS = SKILLS_CONFIG.survivalTargets;
const SURVIVAL_DICE = CHARACTER_EDIT_CONFIG.survivalDice;
// Aging change points keyed by the age they land at: {36: {STR: -1, DEX: -1}}.
// Every age at which the cumulative totals move, up to the horizon.
const AGING_BY_AGE = SKILLS_CONFIG.agingByAge;
const AGING_HORIZON_AGE = CHARACTER_EDIT_CONFIG.agingHorizonAge;
// What the card says past the horizon, the server's words from the loaded
// canon's aging (issue #68).
const AGING_ESCALATION_NOTE = CHARACTER_EDIT_CONFIG.agingEscalationNote;
const DEFAULT_RUN_YEARS = CHARACTER_EDIT_CONFIG.defaultRunYears;
const MAX_RUN_YEARS = CHARACTER_EDIT_CONFIG.maxRunYears;


// The equipment list's empty state, held to character_edit.html's
// .no-equipment paragraph the same way (issue #338 item 20).
const NO_EQUIPMENT_TEXT = 'No equipment. Add items from the catalog below.';

/** Title Case, the way the template's |title filter renders a skill name. */
function titleCaseSkill(name) {
    return String(name)
        .split(' ')
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}


// Escape HTML to prevent XSS.
//
// Every caller interpolates the result into an HTML string that later goes
// through innerHTML, and several of those spots are inside double-quoted
// attributes (data-skill, data-item-name, data-original, aria-label, the
// notes input's value). So this must be safe in BOTH text and quoted
// attribute contexts: quotes are escaped as well as angle brackets.
//
// The old implementation set textContent on a detached div and read back
// innerHTML, which neutralizes < > & but leaves " and ' untouched — a
// stored equipment note or skill name containing a double quote closed the
// attribute early and could inject an event handler that ran in the session
// of anyone (notably a DM) who opened the character. See issue #120.
function escapeHtml(text) {
    return String(text ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Panel factory — appends persistent content to the page's notice region.
 *
 * This was a modal factory (overlay, aria-modal, focus trap, Escape closes).
 * Issue #190 retired that shape across the app: a panel sits in the page,
 * above the thing it is about, and the rest of the editor stays usable
 * behind it. Issue #217 retired this page's own three callers (the aging
 * confirm, term result, and death panels became the workbench card and its
 * server-mirrored death state) without retiring the factory itself, so a
 * future panel on this page has it ready; its return contract (callers
 * querySelector into it and remove() it) is unchanged.
 */
function createPanel(contentHtml, options) {
    const settings = options || {};
    const region = typeof noticeRegion === 'function'
        ? noticeRegion()
        : document.querySelector('main') || document.body;
    const panel = document.createElement('div');
    panel.className = 'notice notice-panel'
        + (settings.tone ? ` notice-${settings.tone}` : '');
    panel.setAttribute('role', 'group');
    if (settings.label) { panel.setAttribute('aria-label', settings.label); }
    panel.innerHTML = `<div class="panel-content">${contentHtml}</div>`;
    region.appendChild(panel);

    if (panel.scrollIntoView) { panel.scrollIntoView({ block: 'nearest' }); }
    const focusable = panel.querySelector(
        'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable) { focusable.focus(); }
    return panel;
}

/**
 * Write one field's cell from a response that carries it.
 *
 * The inputs are addressed the way the page builds them -- a [data-field]
 * container around the control -- so a repaint and an edit read the same
 * element. A field only a Game Master may set renders for a player as text
 * marked data-readonly-field, with the reason beside it (issue #748), and
 * repaints from the same response. An absent key leaves the cell alone: the
 * allocate-a-point response goes through updateSkillsUI too and carries none
 * of this.
 *
 * @param {string} field - The field name, e.g. "strength".
 * @param {number|string|null|undefined} value - The server's value.
 * @param {string} [empty] - What an unset value reads as in the text form.
 */
function paintFieldInput(field, value, empty = '—') {
    if (value === undefined) return;
    const input = document.querySelector(`[data-field="${field}"] input`);
    if (input) {
        input.value = value === null ? '' : value;
        autosave.updateOriginalValue(field, input.value);
        return;
    }
    const text = document.querySelector(`[data-readonly-field="${field}"]`);
    if (text) text.textContent = value === null ? empty : String(value);
}

// Update UI after an experience run or an allocation
function updateSkillsUI(data) {
    document.getElementById('character-age').textContent = data.age;
    // A run of years can grant +1 STR/DEX/CON and can cross an aging stage,
    // which moves the effective scores Physical and Mental are built from.
    // The response has carried the attributes since #163 and nothing read
    // them, so the Core Attributes inputs and the two derived readouts kept
    // their pre-run values on the page the player was looking at (#638).
    paintFieldInput('strength', data.strength);
    paintFieldInput('dexterity', data.dexterity);
    paintFieldInput('constitution', data.constitution);
    paintText('physical', data.physical);
    paintText('mental', data.mental);
    // Weight is plain STR, which a "+1 STR" year raises (issue #763).
    paintText('weight_lbs', data.weight_lbs);
    const yearsEl = document.getElementById('experience-years');
    if (yearsEl && data.experience_years !== undefined) {
        yearsEl.textContent = data.experience_years;
    }
    // An input for a Game Master, text for a player (issue #748).
    paintFieldInput('total_xp', data.total_xp);
    // Update level and money earned
    if (data.level !== undefined) {
        document.getElementById('character-level').textContent = data.level;
    }
    if (data.money_earned !== undefined) {
        document.getElementById('money-earned').textContent = data.money_earned + ' gp';
        // The Wealth table says it too, and kept the old figure (#792 sweep).
        paintText('wealth-earned', data.money_earned + ' gp');
    }
    document.getElementById('allocated-points').textContent = data.total_allocated_points;

    // The skills list, as the server renders it for the page: the same rows
    // in the same order a reload shows (issue #338 item 3).
    if (data.skills_html !== undefined) {
        document.getElementById('skills-list').innerHTML = data.skills_html;
    }

    paintPointControls(data);
}

/**
 * Repaint the free points and every control that spends them (issue #792).
 *
 * A sheet loaded with no free points used to leave the new-skill input, the
 * spell-point figure and a player's learn-spell control out of the page, so
 * a run that earned the first point gave the player nothing to spend it
 * with. They are rendered hidden and shown from here.
 *
 * @param {object} data - A response carrying free_skill_points and, when it
 *     knows them, free_spell_points and is_alive.
 */
function paintPointControls(data) {
    // The Mana in hand rides on the spell points (#792 sweep).
    paintCurrentMana(data);
    if (data.free_skill_points !== undefined) {
        paintText('free-points', data.free_skill_points);
        const hasPoints = data.free_skill_points > 0;
        // A Game Master may grant a point past the free ones once the
        // endpoint does (#770); the server says so.
        const offered = hasPoints || Boolean(CHARACTER_EDIT_CONFIG.canGrantSkillPoints);
        const addControl = document.querySelector('.add-skill-control');
        if (addControl) {
            addControl.style.display = offered && data.is_alive !== false ? 'flex' : 'none';
        }
        // A player's learn-spell control spends a free point; a Game
        // Master's teaches for nothing and is never hidden.
        document.querySelectorAll('.needs-free-points').forEach((control) => {
            control.hidden = !hasPoints;
        });
    }
    if (data.free_spell_points !== undefined) {
        paintText('spell-points', data.free_spell_points);
        paintText('mana-pool', data.free_spell_points);
        const spellItem = document.getElementById('spell-points-item');
        if (spellItem) spellItem.hidden = !(data.free_spell_points > 0);
    }
}

/**
 * Repaint what any change to the sheet can move (issues #764, #765, #792).
 *
 * Every response to a change made without a reload carries these keys
 * (sheet_repaint in characters/views/characters.py): whether the character
 * is complete and what it still needs, the free points, the skill check's
 * options, the Recent Changes rows and the dice log. Each was rendered once
 * at load and never written again. An absent key leaves its part alone.
 *
 * @param {object} data - The response.
 */
function paintSheet(data) {
    if (!data) return;
    paintLifeControls(data.is_alive);
    if (data.name !== undefined) {
        // Every place the sheet prints the name (issues #792, #903): the
        // title, the breadcrumb and each element marked data-name-chrome --
        // the death card's heading, the Game Masters' sessions holding it.
        paintNameChrome(data.name, {
            title: data.name,
            breadcrumb: '.breadcrumb [aria-current="page"]',
        });
    }
    paintPlayWithGameMaster(data.play_with_gm_html);
    if (data.skill_name_suggestions_html !== undefined) {
        // A skill allocated in place joins the type-ahead (issue #903).
        const suggestions = document.getElementById('skill-name-suggestions');
        if (suggestions) suggestions.innerHTML = data.skill_name_suggestions_html;
    }
    if (data.completion_html !== undefined) {
        const completion = document.getElementById('sheet-completion');
        if (completion) completion.innerHTML = data.completion_html;
    }
    if (data.incomplete_marks_html !== undefined) {
        document.querySelectorAll('.incomplete-marks-list').forEach((list) => {
            list.innerHTML = data.incomplete_marks_html;
        });
    }
    if (data.is_complete !== undefined) {
        // The Attack panel and the skill check wait for a complete
        // character; the response that completes it shows them.
        const waiting = document.getElementById('attack-incomplete');
        if (waiting) waiting.hidden = data.is_complete;
        const ready = document.getElementById('attack-ready');
        if (ready) ready.hidden = !data.is_complete;
        const skillCheck = document.getElementById('skill-check');
        if (skillCheck) skillCheck.hidden = !data.is_complete;
    }
    if (data.point_buy_open === false) {
        const note = document.getElementById('point-buy-note');
        if (note) note.hidden = true;
    }
    paintPointControls(data);
    // A change that moves a pool -- a cast in the story spends Mana (#18) --
    // answers with the injury readout re-rendered, its Mana included.
    refreshFatigueBodyInjury(data);
    if (data.skill_check_options_html !== undefined) {
        const select = document.getElementById('skill-check-skill');
        if (select) {
            const previous = select.value;
            select.innerHTML = data.skill_check_options_html;
            if ([...select.options].some((option) => option.value === previous)) {
                select.value = previous;
            }
        }
    }
    if (data.recent_changes_html !== undefined) {
        const rows = document.getElementById('recent-changes-rows');
        if (rows) {
            rows.innerHTML = data.recent_changes_html;
            // The rows arrive as <time> in UTC; local-time.js writes them in
            // the viewer's own zone, as it does at load (issue #700).
            if (typeof window.applyLocalTimes === 'function') window.applyLocalTimes(rows);
        }
        const section = document.getElementById('recent-changes');
        if (section) section.hidden = !data.recent_changes_html.trim();
    }
    if (data.dice_log !== undefined) {
        paintText('dice-log-text', data.dice_log);
        const section = document.getElementById('dice-log');
        if (section) section.hidden = !data.dice_log;
    }
}

/**
 * Show the controls for a living character or for a dead one (issue #903).
 *
 * Mark Dead and the Magic section's controls are a living character's;
 * Revive is a dead one's. Each renders on the page hidden or shown by the
 * record, marked data-alive-only or data-dead-only, so a run of years that
 * kills the character in place leaves what a reload would.
 *
 * @param {boolean|undefined} isAlive - The record's is_alive; absent leaves
 *     the controls alone.
 */
function paintLifeControls(isAlive) {
    if (isAlive === undefined) return;
    document.querySelectorAll('[data-alive-only]').forEach((control) => {
        control.hidden = !isAlive;
    });
    document.querySelectorAll('[data-dead-only]').forEach((control) => {
        control.hidden = isAlive;
    });
}

/**
 * The slot as the server rendered it, without what the viewer has done to it.
 *
 * play-with-gm.js reveals each form, disables its button while a request is
 * out, and writes a refusal into the error line; those are the page's, not
 * the record's. A chosen session lives in the select's value, which markup
 * does not carry. Undoing the three on a copy leaves markup that compares
 * equal to a fresh rendering of the same record.
 *
 * The session choice is a searchable picker (issue #973). picker.js marks
 * the picker wired, and a search or a page turn replaces its body with
 * another page of sessions and stamps a ticket on it; the marks are dropped
 * on the copy, and once the viewer has searched (a ticket) the body is left
 * out of both sides, since the search is the viewer's and no rendering of
 * the record can match it.
 *
 * @param {Element} slot - A .play-with-gm-slot.
 * @param {boolean} [withoutPickerBody] - Leave the picker's body out.
 * @returns {string} The copy's markup.
 */
function playWithGameMasterMarkup(slot, withoutPickerBody = false) {
    const copy = slot.cloneNode(true);
    copy.querySelectorAll('form.play-with-gm').forEach((form) => { form.hidden = true; });
    copy.querySelectorAll('.play-with-gm-error').forEach((line) => {
        line.textContent = '';
        line.hidden = true;
    });
    copy.querySelectorAll('button').forEach((button) => { button.disabled = false; });
    copy.querySelectorAll('[data-picker]').forEach((picker) => {
        delete picker.dataset.pickerReady;
        delete picker.dataset.pickerTicket;
    });
    if (withoutPickerBody) {
        copy.querySelectorAll('[data-picker-body]').forEach((body) => { body.innerHTML = ''; });
    }
    return copy.outerHTML;
}

/** Whether the viewer has searched or turned a page in the slot's picker. */
function pickerWasSearched(slot) {
    return Array.from(slot.querySelectorAll('[data-picker]'))
        .some((picker) => picker.dataset.pickerTicket !== undefined);
}

/**
 * Swap in the "Play with the Game Master" slot the server rendered (#903).
 *
 * The slot (gm/_play_with_gm.html) holds the form only where the attach
 * would be taken -- a living, complete character -- and names the
 * character in it. Completing, killing or renaming the character in place
 * changes it; the response carries the slot re-rendered from the record,
 * and play-with-gm.js reveals its form as it does at load.
 *
 * Every response carries the slot, and most leave it as it was. Those are
 * not swapped: a swap would drop the session the viewer picked, a refusal
 * the error line keeps until the next attempt, keyboard focus (#903
 * review), and what the viewer typed into the session search (#973).
 *
 * @param {string|undefined} slotHtml - The rendered slot; absent leaves it.
 */
function paintPlayWithGameMaster(slotHtml) {
    if (slotHtml === undefined) return;
    const fresh = elementFromHtml(slotHtml);
    const current = fresh && fresh.id ? document.getElementById(fresh.id) : null;
    if (!current) return;
    const searched = pickerWasSearched(current);
    if (playWithGameMasterMarkup(current, searched) === playWithGameMasterMarkup(fresh, searched)) return;
    current.replaceWith(fresh);
    if (typeof window.revealPlayWithGameMaster === 'function') {
        window.revealPlayWithGameMaster(fresh);
    }
}

/** An element from one fragment of server-rendered markup. */
function elementFromHtml(html) {
    const holder = document.createElement('template');
    holder.innerHTML = String(html).trim();
    return holder.content.firstElementChild;
}

// Prepend a run of years to the Experience History log live, so it shows
// without a page reload -- newest first, expanded, with whichever entry was
// previously open now closed (issues #217, #221). The entry is the server's
// rendering of the stored run, the same partial the page's log uses, so the
// live entry and a reload say the same thing (issue #338 item 4).
function prependRunToLog(runHtml) {
    const log = document.getElementById('experience-log');
    if (!log || !runHtml) return;
    const entry = elementFromHtml(runHtml);
    entry.open = true;
    const previouslyOpen = log.querySelector('.term-entry[open]');
    if (previouslyOpen) previouslyOpen.removeAttribute('open');
    const noHistory = log.querySelector('.no-history');
    if (noHistory) noHistory.remove();
    log.insertBefore(entry, log.firstChild);
    // The record keeps the latest runs only (MAX_EXPERIENCE_LOG_ENTRIES), and
    // so does the page, so it never shows a run a reload would not (#903).
    const limit = CHARACTER_EDIT_CONFIG.maxExperienceLogEntries;
    if (limit) {
        [...log.querySelectorAll('.term-entry')].slice(limit).forEach((old) => old.remove());
    }
}

/**
 * How many years the workbench would roll right now: whatever is in the Years
 * input, clamped to the bounds the server enforces. A blank or nonsense entry
 * falls back to the default rather than disabling anything, so the preview and
 * the button always say something true.
 * @returns {number} The year count to preview and to roll.
 */
function requestedRunYears() {
    const input = document.getElementById('years-input');
    const parsed = input ? parseInt(input.value, 10) : NaN;
    if (!Number.isFinite(parsed)) return DEFAULT_RUN_YEARS;
    return Math.min(MAX_RUN_YEARS, Math.max(1, parsed));
}

/**
 * Render the workbench card's consequence preview for the run `track` would
 * roll next -- the age change, the survival target, and when the next aging
 * hit lands -- from the same rules tables (SURVIVAL_TARGETS, AGING_BY_AGE) the
 * server used for the initial render. Called on track change, on year-count
 * change, and after a successful roll, once SKILLS_CONFIG.currentAge /
 * currentYears have moved on (issues #217, #221). This preview is the
 * workbench's entire "confirmation" -- it is what the deleted aging-confirm
 * panel used to say, just visible before the click instead of gating it.
 * @param {string} track - The selected track key, or 'random'.
 */
function renderWorkbenchPreview(track) {
    const years = requestedRunYears();
    const newAge = SKILLS_CONFIG.currentAge + years;

    const eyebrow = document.getElementById('workbench-eyebrow');
    if (eyebrow) {
        const firstYear = SKILLS_CONFIG.currentYears + 1;
        eyebrow.textContent = years === 1
            ? `NEXT — YEAR ${firstYear}`
            : `NEXT — YEARS ${firstYear}–${firstYear + years - 1}`;
    }

    const agePreview = document.getElementById('workbench-age-preview');
    if (agePreview) agePreview.textContent = `${SKILLS_CONFIG.currentAge} → ${newAge}`;

    const survivalPreview = document.getElementById('workbench-survival-preview');
    if (survivalPreview) {
        const survivalTarget = SURVIVAL_TARGETS[track];
        survivalPreview.textContent = survivalTarget
            ? `${SURVIVAL_DICE} + best attribute modifier ≥ ${survivalTarget}`
            : `${SURVIVAL_DICE} + best attribute modifier (target varies by track)`;
    }

    const agingPreview = document.getElementById('workbench-aging-preview');
    if (agingPreview) {
        // The next age at which the cumulative totals move -- said in as many
        // words, so the player can see a hit coming before they roll into it.
        const nextChangeAge = Object.keys(AGING_BY_AGE)
            .map(Number)
            .filter(changeAge => changeAge > SKILLS_CONFIG.currentAge)
            .sort((a, b) => a - b)[0];
        if (nextChangeAge !== undefined) {
            const penalties = Object.entries(AGING_BY_AGE[nextChangeAge])
                .map(([attr, val]) => `${attr.toUpperCase()} ${val}`)
                .join(', ');
            agingPreview.textContent = `${penalties} at age ${nextChangeAge}`;
        } else if (SKILLS_CONFIG.currentAge >= AGING_HORIZON_AGE) {
            // Aging keeps escalating past the precomputed horizon (server-side,
            // uncapped -- see AgingRules.penalties_for in characters/skill_rules.py), but
            // listing every further stage isn't worth the payload. Say the rate
            // instead of a stale/blank preview (issue #163).
            agingPreview.textContent = AGING_ESCALATION_NOTE;
        } else {
            agingPreview.textContent = `No further aging before age ${AGING_HORIZON_AGE}`;
        }
    }

    const btn = document.getElementById('roll-years-btn');
    if (btn) btn.textContent = `Roll ${years} ${years === 1 ? 'year' : 'years'}`;
}

/**
 * Swap the workbench card's content for the death card when a rolled year
 * kills the character over AJAX (issue #217). Death is a state, not an
 * event: this clones the same `#death-card-template` markup the server
 * renders for an already-dead character (both come from
 * characters/_death_card.html), so an in-place death and a reload show the
 * identical card. No Close button, no reload.
 * @param {string} name - The character's saved name from the server, not
 *     the name input's text (issue #903).
 * @param {string} cause - The character's death_cause from the server.
 * @param {object} [fatalCheck] - The fatal year's survival_check, when the
 *     death came from a failed survival roll — shown so the player sees the
 *     dice that did it (issue #175).
 */
function showDeathCard(name, cause, fatalCheck) {
    const workbenchCard = document.getElementById('workbench-card');
    const template = document.getElementById('death-card-template');
    if (!workbenchCard || !template) return;

    const clone = template.content.cloneNode(true);
    // Marked data-name-chrome, so a later rename repaints it too.
    clone.querySelector('h3 [data-name-chrome]').textContent = name;
    clone.querySelector('.death-cause').textContent = cause;
    const fatalRollEl = clone.querySelector('.fatal-roll');
    if (fatalRollEl) {
        if (fatalCheck) {
            fatalRollEl.textContent = `Fatal roll: ${fatalCheck.text}`;
            fatalRollEl.hidden = false;
        } else {
            fatalRollEl.remove();
        }
    }
    workbenchCard.innerHTML = '';
    workbenchCard.appendChild(clone);
}

// Roll a run of experience years. The workbench card's preview above this
// button is the consequence disclosure the old aging-confirm panel used to
// gate the click with -- one click rolls the run, no confirmation step
// (issues #217, #221).
document.getElementById('roll-years-btn')?.addEventListener('click', async function () {
    const track = document.getElementById('track-select').value;
    const years = requestedRunYears();
    const btn = this;
    let diedThisRoll = false;

    btn.disabled = true;
    btn.textContent = 'Rolling...';

    try {
        const response = await postWithCsrf(
            SKILLS_CONFIG.addYearsUrl,
            csrfToken(),
            { track, years }
        );

        const data = await response.json();

        if (data.success) {
            updateSkillsUI(data.character);
            paintSheet(data);
            prependRunToLog(data.run_html);
            // Aging moves STR/DEX/CON, so the pools, injury thresholds, and
            // encumbrance/movement readout can all go stale the same way an
            // attribute edit does -- refresh them in place (issue #163).
            refreshFatigueBodyInjury(data.character);
            if (data.movement) {
                updateEquipmentDisplay(data.movement);
            }

            // Update config for the next workbench preview
            SKILLS_CONFIG.currentAge = data.character.age;
            SKILLS_CONFIG.currentYears = data.character.experience_years;

            if (!data.character.is_alive) {
                // A fatal run stops at the year that killed the character,
                // so its last year's survival_check is the fatal roll.
                diedThisRoll = true;
                const finalYear = data.run_result.years[data.run_result.years.length - 1];
                showDeathCard(data.name, data.character.death_cause, finalYear && finalYear.survival_check);
            }
        } else {
            uiNotify(data.error || 'Failed to add experience years');
        }
    } catch (error) {
        uiNotify('Error: ' + error.message);
    } finally {
        // A dead character has no workbench card left to update -- the death
        // card replaced it, with no button in it.
        if (!diedThisRoll) {
            btn.disabled = false;
            renderWorkbenchPreview(document.getElementById('track-select').value);
        }
    }
});

// The workbench preview updates live as the track choice or the year count
// changes -- pure DOM, from the rules tables already on the page (issues
// #217, #221).
document.getElementById('track-select')?.addEventListener('change', function () {
    renderWorkbenchPreview(this.value);
});

document.getElementById('years-input')?.addEventListener('input', function () {
    renderWorkbenchPreview(document.getElementById('track-select').value);
});

// Allocate skill point (delegated)
document.getElementById('skills-list')?.addEventListener('click', async function (e) {
    if (!e.target.classList.contains('allocate-btn')) return;
    const skill = e.target.dataset.skill;
    await allocateSkillPoint(skill);
});

// Submit new skill from input
async function submitNewSkill() {
    const input = document.getElementById('new-skill-name');
    const skill = input.value.trim();
    if (!skill) return;
    // A refused allocation keeps what the player typed (issue #798).
    if (await allocateSkillPoint(skill)) input.value = '';
}

// Add new skill - button click or Enter key
document.getElementById('add-skill-btn')?.addEventListener('click', submitNewSkill);
document.getElementById('new-skill-name')?.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') submitNewSkill();
});

// Rename skill - handle blur and Enter key
async function renameSkill(element) {
    const oldName = element.dataset.original;
    const newName = element.textContent.trim();

    if (!newName || newName.toLowerCase() === oldName.toLowerCase()) {
        // Reset to original display name
        element.textContent = titleCaseSkill(oldName);
        return;
    }

    // Saving / saved / error on the name itself, with the reason beside it
    // in the row -- the states autosave.js uses (issue #339, item 22), not a
    // page notice.
    const skillRow = element.closest('.skill-row');
    clearFieldError(skillRow);
    element.classList.remove('saved', 'error');
    element.classList.add('saving');
    function refuseRename(message) {
        element.classList.remove('saving');
        element.classList.add('error');
        showFieldError(skillRow, message);
        element.textContent = titleCaseSkill(oldName);
    }

    try {
        const response = await postWithCsrf(
            SKILLS_CONFIG.renameSkillUrl,
            csrfToken(),
            { old_name: oldName, new_name: newName }
        );
        const data = await response.json();
        if (data.success) {
            element.classList.remove('saving');
            element.classList.add('saved');
            // Update the row's data attributes
            const row = skillRow;
            row.dataset.skill = data.skill;
            element.dataset.original = data.skill;
            // Update allocate button if present
            const allocateBtn = row.querySelector('.allocate-btn');
            if (allocateBtn) allocateBtn.dataset.skill = data.skill;
            // The points don't change, but the level can: renaming to or
            // from Arcane re-prices the skill (issue #154), so take the
            // server's freshly rendered level.
            if (data.display !== undefined) {
                row.querySelector('.skill-level').textContent = `Level ${data.display}`;
            }
            // Update display name
            element.textContent = titleCaseSkill(data.skill);
            // The skill check offered the old name until a reload (#765).
            paintSheet(data);
            // A Village choice joins a skill already held, leaving one row
            // where there were two, and a Weapon skill gains or loses its tie
            // control: the list as the server renders it (#792 sweep).
            if (data.skills_html !== undefined) {
                document.getElementById('skills-list').innerHTML = data.skills_html;
                // The renamed row's name keeps the saved mark it just earned.
                const renamed = document.querySelector(
                    `.skill-row[data-skill="${CSS.escape(data.skill)}"] .skill-name`
                );
                if (renamed) renamed.classList.add('saved');
            }
        } else {
            refuseRename(data.error || 'Failed to rename skill');
        }
    } catch (error) {
        refuseRename(FIELD_NETWORK_ERROR);
    }
}

// Editing a skill name again clears its last saved / error state.
document.getElementById('skills-list')?.addEventListener('focusin', function (e) {
    if (!e.target.classList.contains('skill-name')) return;
    e.target.classList.remove('saved', 'error');
    clearFieldError(e.target.closest('.skill-row'));
});

// Delegated blur handler for skill names
document.getElementById('skills-list')?.addEventListener('focusout', function (e) {
    if (e.target.classList.contains('skill-name') && e.target.contentEditable === 'true') {
        renameSkill(e.target);
    }
});

// Enter key commits rename, Escape cancels
document.getElementById('skills-list')?.addEventListener('keydown', function (e) {
    if (!e.target.classList.contains('skill-name')) return;
    if (e.key === 'Enter') {
        e.preventDefault();
        e.target.blur();
    } else if (e.key === 'Escape') {
        e.preventDefault();
        const oldName = e.target.dataset.original;
        e.target.textContent = titleCaseSkill(oldName);
        e.target.blur();
    }
});

/**
 * Spend one free point on a skill.
 * @param {string} skill - The skill's name.
 * @returns {Promise<boolean>} Whether the server took the point.
 */
async function allocateSkillPoint(skill) {
    try {
        const response = await postWithCsrf(
            SKILLS_CONFIG.allocateUrl,
            csrfToken(),
            { skill }
        );

        const data = await response.json();

        if (data.success) {
            // Update free points display
            document.getElementById('free-points').textContent = data.free_skill_points;
            document.getElementById('allocated-points').textContent = data.total_allocated_points;

            // The row as the page renders it (issue #338 item 3). CSS.escape,
            // not escapeHtml: this is a selector, not markup. A quote or
            // backslash in a skill name would otherwise make the selector
            // invalid and the row lookup silently miss (issue #120).
            const row = elementFromHtml(data.row_html);
            const existingRow = document.querySelector(
                `.skill-row[data-skill="${CSS.escape(data.skill)}"]`
            );
            if (existingRow) {
                existingRow.replaceWith(row);
            } else {
                const noSkills = document.querySelector('.no-skills');
                if (noSkills) noSkills.remove();
                document.getElementById('skills-list').appendChild(row);
            }

            // Hide allocation buttons if no more free points; a Game Master
            // keeps his, since he may grant past them (#770).
            if (data.free_skill_points === 0 && !CHARACTER_EDIT_CONFIG.canGrantSkillPoints) {
                document.querySelectorAll('.allocate-btn').forEach(btn => btn.remove());
            }
            paintSheet(data);
            return true;
        }
        uiNotify(data.error || 'Failed to allocate point');
    } catch (error) {
        uiNotify('Error: ' + error.message);
    }
    return false;
}

// Equipment system
const EQUIPMENT_CONFIG = {
    catalogUrl: CHARACTER_EDIT_CONFIG.catalogUrl,
    addUrl: CHARACTER_EDIT_CONFIG.equipmentAddUrl,
    updateUrl: CHARACTER_EDIT_CONFIG.equipmentUpdateUrl,
    removeUrl: CHARACTER_EDIT_CONFIG.equipmentRemoveUrl,
    bulkRemoveUrl: CHARACTER_EDIT_CONFIG.equipmentBulkRemoveUrl,
    togglePackUrl: CHARACTER_EDIT_CONFIG.equipmentTogglePackUrl,
    initialEquipment: CHARACTER_EDIT_CONFIG.initialEquipment,
    packDropped: CHARACTER_EDIT_CONFIG.packDropped,
    // The rows holding the weapon in hand and the shield in use (issues
    // #804, #846); every equipment response restates them.
    weaponInHandIndex: CHARACTER_EDIT_CONFIG.weaponInHandIndex,
    shieldInUseIndex: CHARACTER_EDIT_CONFIG.shieldInUseIndex,
    shieldIdleReason: CHARACTER_EDIT_CONFIG.shieldIdleReason,
    // Read-only viewers of a public character (issue #273) get static rows:
    // no per-item inputs, checkboxes, or Remove buttons.
    canEdit: CHARACTER_EDIT_CONFIG.canEdit,
    // Item modifiers are a Game Master's grant (issue #752).
    canSetItemModifiers: CHARACTER_EDIT_CONFIG.canSetItemModifiers,
};

let equipmentCatalog = null;

// Load catalog on demand. A refused or unreadable answer -- an expired
// session answers with the login page's HTML -- rejects, and the callers say
// so (issue #797); nothing is cached from a failure, so a retry asks again.
async function loadCatalog() {
    if (equipmentCatalog) return equipmentCatalog;
    const response = await fetch(EQUIPMENT_CONFIG.catalogUrl);
    if (!response.ok) {
        throw new Error(`the catalog answered ${response.status}`);
    }
    equipmentCatalog = await response.json();
    return equipmentCatalog;
}

const CATALOG_ERROR_TEXT = 'The equipment catalog could not be loaded, so items show by their '
    + 'catalog ids and nothing can be picked from it.';

/**
 * Say, persistently, that the catalog did not load (issue #797).
 *
 * The list stayed blank and the picker dead with nothing said. The notice
 * sits above the list until a retry loads the catalog; it never dismisses
 * itself.
 *
 * @param {Error} error - Why the load failed.
 */
function showCatalogError(error) {
    const list = document.getElementById('equipment-list');
    if (!list) return;
    let notice = document.getElementById('equipment-catalog-error');
    if (!notice) {
        notice = document.createElement('p');
        notice.id = 'equipment-catalog-error';
        notice.className = 'notice notice-error';
        notice.setAttribute('role', 'alert');
        const text = document.createElement('span');
        text.id = 'equipment-catalog-error-text';
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.id = 'equipment-catalog-retry';
        retry.className = 'button secondary';
        retry.textContent = 'Try again';
        retry.addEventListener('click', retryCatalog);
        notice.append(text, ' ', retry);
        list.parentNode.insertBefore(notice, list);
    }
    notice.querySelector('#equipment-catalog-error-text').textContent =
        `${CATALOG_ERROR_TEXT} (${error.message})`;
}

/** Ask for the catalog again; on success the notice goes and the list redraws. */
async function retryCatalog() {
    equipmentCatalog = null;
    await renderEquipmentList(equipmentLatest);
}

/**
 * The catalog, or an empty one after saying it failed (issue #797).
 * @returns {Promise<{catalog: object, loaded: boolean}>}
 */
async function catalogOrNotice() {
    try {
        const catalog = await loadCatalog();
        // Gone once the catalog loads: .notice styles its own display, so a
        // hidden attribute alone would leave it on the page.
        const notice = document.getElementById('equipment-catalog-error');
        if (notice) notice.remove();
        return { catalog, loaded: true };
    } catch (error) {
        showCatalogError(error);
        return { catalog: {}, loaded: false };
    }
}

/**
 * Write one combat-stat tile, matching what the template renders for it.
 *
 * A null value is a number the server would not state — an attribute nobody
 * has recorded has no rules value (issue #630) — and shows as an em dash with
 * no positive/negative colouring. An absent key leaves the tile alone: not
 * every response carries every tile.
 *
 * @param {string} tileId - The tile element's id.
 * @param {number|null|undefined} value - The server's number.
 * @param {(value: number) => string} format - How the tile prints a number.
 */
function paintModifierTile(tileId, value, format) {
    const tile = document.getElementById(tileId);
    if (!tile || value === undefined) return;
    tile.textContent = value === null ? '—' : format(value);
    const sign = value === null || value === 0 ? '' : (value > 0 ? ' positive' : ' negative');
    tile.className = 'value' + sign;
}

/**
 * Repaint "DEX Combat Mod" and "Dodge" from any response that carries them.
 *
 * Both derive from combat_dexterity, which moves when armour or a shield is
 * equipped, when DEX is edited, and when a run of experience years applies an
 * aging DEX penalty. The template rendered them once and nothing wrote them
 * again, so they contradicted the Armor DEX Penalty tile beside them and the
 * Attack Resolution panel below them (issue #634). Dodge never goes below 0,
 * so it prints with an explicit "+" the way the template does.
 *
 * @param {object} data - A response carrying dex_modifier / dodge_modifier.
 */
function paintCombatDexTiles(data) {
    paintModifierTile('dex-combat-mod', data.dex_modifier, formatSigned);
    paintModifierTile('dodge-modifier', data.dodge_modifier, (value) => '+' + value);
}

/**
 * The literacy row's keys (issues #740, #741, #743). The field-update response
 * for `location` and `intelligence`, the roll-literacy and the rule-literacy
 * responses carry exactly these; LITERACY_RESPONSE_KEYS in
 * characters/views/characters.py is the same list, and a Python test holds
 * the two equal.
 */
const LITERACY_RESPONSE_KEYS = [
    'location_literacy_check',
    'location_literacy_modifier',
    'literacy_roll',
    'is_literate',
    'literacy_result',
    'can_roll_literacy',
    'can_change_origin',
    'literacy_ruling',
    'can_rule_literacy',
];

/**
 * The ruling select's option for a stored ruling, and back (issue #743): the
 * server stores true, false or null, the select holds a string.
 */
const RULING_OPTIONS = [[true, 'literate'], [false, 'illiterate'], [null, '']];

function rulingOption(ruling) {
    return RULING_OPTIONS.find(([stored]) => stored === ruling)[1];
}

function rulingFromOption(option) {
    return RULING_OPTIONS.find(([, value]) => value === option)[0];
}

/**
 * Repaint the Location section's literacy row from a response carrying it.
 *
 * The row was rendered once from the origin the page loaded with, so picking
 * a new Origin saved it and left the old origin's check on show until a
 * reload (issue #740). A response that does not carry every key is about
 * some other field and leaves the row alone. The roll control hides rather
 * than goes: a "Consult GM" origin has no check to roll, and changing the
 * Origin again can make it usable (issue #741). The same response locks a
 * player's Origin select once the roll is made.
 *
 * @param {object} data - A response keyed by LITERACY_RESPONSE_KEYS.
 */
function paintLiteracyRow(data) {
    if (!LITERACY_RESPONSE_KEYS.every((key) => key in data)) return;
    const check = document.getElementById('literacy-check');
    if (!check) return;
    check.textContent = data.location_literacy_check;
    // The server's own wording (Character.literacy_result), set as sent.
    document.getElementById('literacy-result').textContent = data.literacy_result;
    const button = document.getElementById('roll-literacy-btn');
    if (button) button.hidden = !data.can_roll_literacy;
    // A player's Origin is fixed once literacy is rolled; a Game Master's is
    // not (#741). The select locks in place with the reason beside it, the
    // state the server renders on a reload.
    const origin = document.querySelector('[data-field="location"] select');
    if (origin) origin.disabled = !data.can_change_origin;
    const note = document.getElementById('origin-fixed-note');
    if (note) note.hidden = data.can_change_origin;
    // A Game Master's ruling at "Consult GM" (#743): the control shows there
    // and hides elsewhere, holding whatever ruling the server has.
    const rulingControl = document.getElementById('literacy-ruling-control');
    if (rulingControl) rulingControl.hidden = !data.can_rule_literacy;
    const rulingSelect = document.getElementById('literacy-ruling-select');
    if (rulingSelect) {
        rulingSelect.value = rulingOption(data.literacy_ruling);
        rulingSelect.dataset.confirmed = rulingSelect.value;
    }
}

/**
 * Repaint the encumbrance bar, the Movement row and the combat tiles.
 *
 * The one painter for movement_response's keys (characters/views/
 * characters.py), which an equipment change, an attribute edit and a run of
 * years all send (issues #163, #634, #763). A key the response does not
 * carry leaves its part alone. A null is a number the server will not
 * state for an unrecorded attribute, and paints the way the template
 * renders it: "—" and an "unknown" badge, never "+0" or a thrown error
 * (issues #630, #766).
 *
 * @param {object} data - A response carrying some of those keys.
 */
function paintMovement(data) {
    paintText('carried-weight', data.carried_weight);
    paintText('pack-weight', data.pack_weight);
    // The carrying capacity beside the load is the lightest level's limit,
    // a multiple of effective STR (issues #763 and #75).
    paintText('max-unencumbered', data.lightest_load_limit);

    const levelBadge = document.getElementById('encumbrance-level');
    if (levelBadge && data.encumbrance_level !== undefined) {
        // The server's wording of the level (Character.encumbrance_label).
        levelBadge.textContent = data.encumbrance_level === null ? '—' : data.encumbrance_label;
        // By the level's place, not its name: the classes are the server's
        // (Character.encumbrance_badge_classes, issue #75).
        levelBadge.className = 'level-badge ' + data.encumbrance_badge_classes;
    }

    const penaltyEl = document.getElementById('movement-penalty');
    if (penaltyEl && data.movement_penalty !== undefined) {
        if (data.movement_penalty !== 0) {
            penaltyEl.textContent = `${data.movement_penalty} movement`;
            penaltyEl.style.display = '';
        } else {
            penaltyEl.style.display = 'none';
        }
    }

    const restrictionEl = document.getElementById('movement-restriction');
    if (restrictionEl && data.movement_restriction_label !== undefined) {
        if (data.movement_restriction_label) {
            restrictionEl.textContent = data.movement_restriction_label;
            restrictionEl.style.display = '';
        } else {
            restrictionEl.style.display = 'none';
        }
    }

    // Update movement gaits (server-computed labels, issue #143 --
    // same convention as movement_restriction_label above). An unknown
    // modifier leaves every gait unknown: "—", not "n/a" (issue #630).
    if (data.movement_modifier !== undefined) {
        const unknown = data.movement_modifier === null;
        paintText('movement-modifier', unknown ? null : formatSigned(data.movement_modifier));
        (data.gait_readout || []).forEach((gait) => {
            const gaitEl = document.getElementById(`gait-${gait.key}`);
            if (!gaitEl) return;
            gaitEl.classList.toggle('gait-unknown', unknown);
            gaitEl.classList.toggle('gait-na', !unknown && gait.na);
            let text = gait.distance;
            if (unknown) text = '—';
            else if (gait.na) text = 'n/a';
            gaitEl.querySelector('.gait-value').textContent = text;
        });
    }

    // Update combat stats
    paintText('armor-stops', data.armor_stops);
    const dexPenalty = document.getElementById('armor-dex-penalty');
    if (dexPenalty && data.armor_dex_penalty !== undefined) {
        dexPenalty.textContent = data.armor_dex_penalty;
        dexPenalty.className = 'value' + (data.armor_dex_penalty < 0 ? ' negative' : '');
    }

    paintModifierTile('weapon-to-hit', data.weapon_to_hit_mod, formatSigned);
    paintModifierTile('weapon-damage', data.weapon_damage_mod, formatSigned);
    paintModifierTile('weapon-critical', data.weapon_critical_mod, formatSigned);
    paintCombatDexTiles(data);
}

// Update encumbrance and combat stats display
function updateEquipmentDisplay(data) {
    paintMovement(data);
    paintSheet(data);
    // The Attack panel follows the hand only when the hand changed: after
    // any other edit it keeps the weapon the player picked there.
    const handChanged = data.weapon_in_hand_index !== undefined
        && data.weapon_in_hand_index !== EQUIPMENT_CONFIG.weaponInHandIndex;
    if (data.weapon_in_hand_index !== undefined) {
        EQUIPMENT_CONFIG.weaponInHandIndex = data.weapon_in_hand_index;
    }
    if (data.shield_in_use_index !== undefined) {
        EQUIPMENT_CONFIG.shieldInUseIndex = data.shield_in_use_index;
    }
    if (data.shield_idle_reason !== undefined) {
        EQUIPMENT_CONFIG.shieldIdleReason = data.shield_idle_reason;
    }
    // A Game Master's purchase before the wealth roll is told what it spent
    // (review of #851); the notice stays until dismissed.
    if (data.notice) uiNotify(data.notice, 'info');

    // Update pack dropped state
    EQUIPMENT_CONFIG.packDropped = data.pack_dropped;
    document.getElementById('pack-dropped').checked = data.pack_dropped;

    refreshAttackWeapons(data, { followHand: handChanged });
    refreshPurse(data);

    // Rebuild equipment list
    return renderEquipmentList(data.equipment);
}

// The purse after a purchase or a refund (issue #757): the server's figure,
// written into both readouts, never computed here.
function refreshPurse(data) {
    if (data.purse === undefined) return;
    ['purse', 'equipment-purse'].forEach(function (id) {
        const element = document.getElementById(id);
        if (element) element.textContent = data.purse;
    });
    // The Wealth table's Spent / granted line moves with the purse; it kept
    // its load-time figure after a purchase.
    if (data.coin_adjustment !== undefined) {
        paintText('coin-adjustment', data.coin_adjustment + ' gp');
    }
}

// Refresh the Attack Resolution panel's weapon select from an equipment
// response (issue #339, item 24). The options are the server's
// attack_weapons -- index, name, class and damage, decided in Python -- so
// this only writes them out; it decides nothing about what can be swung.
/**
 * @param {object} data - An equipment response.
 * @param {{followHand?: boolean}} [options] - followHand selects the weapon
 *     in hand, for a response that changed which one it is (issue #846).
 */
function refreshAttackWeapons(data, options = {}) {
    if (!Array.isArray(data.attack_weapons)) return;
    const weapons = data.attack_weapons;
    const select = document.getElementById('attack-weapon');
    if (select) {
        const previous = select.selectedOptions[0];
        const previousKey = previous ? `${previous.value}:${previous.dataset.itemId}` : '';
        // The select keeps the player's last choice; it follows the weapon
        // in hand when the hand changed, or when the choice is gone (#846).
        const kept = weapons.some((weapon) => `${weapon.index}:${weapon.item_id}` === previousKey);
        const followHand = Boolean(options.followHand) || !kept;
        select.replaceChildren(...weapons.map((weapon) => {
            const option = document.createElement('option');
            option.value = String(weapon.index);
            option.dataset.itemId = weapon.item_id;
            option.textContent = `${weapon.name} (${weapon.weapon_class}, ${weapon.damage})`
                + (weapon.in_hand ? ' — in hand' : '');
            option.selected = followHand
                ? Boolean(weapon.in_hand)
                : `${option.value}:${weapon.item_id}` === previousKey;
            return option;
        }));
    }
    const form = document.getElementById('attack-form');
    if (form) form.hidden = weapons.length === 0;
    const noWeapons = document.getElementById('attack-no-weapons');
    if (noWeapons) noWeapons.hidden = weapons.length > 0;
    const footnote = document.getElementById('attack-gm-adjudicated');
    const footnoteNames = document.getElementById('attack-gm-adjudicated-names');
    if (footnote && footnoteNames && Array.isArray(data.attack_gm_adjudicated_weapons)) {
        footnoteNames.textContent = data.attack_gm_adjudicated_weapons.join(', ');
        footnote.hidden = data.attack_gm_adjudicated_weapons.length === 0;
    }
}

// ---- Filter / sort state, persisted in localStorage ----
const EQUIPMENT_SORT_KEY = 'tarmar.equipment.sort';
let equipmentLatest = [];
let equipmentSort = localStorage.getItem(EQUIPMENT_SORT_KEY) || '';
let equipmentFilter = '';

function annotateForSort(equipment, catalog) {
    return equipment.map((item, originalIndex) => {
        const itemData = findItemInCatalog(catalog, item.item_id);
        const isCustom = item.item_id === 'custom';
        const name = (isCustom ? item.name : (itemData?.name || item.item_id)) || '';
        const weight = isCustom ? (item.weight || 0) : (itemData?.weight || 0);
        const category = isCustom ? (item.category || '') : (itemData?.category || '');
        return { item, originalIndex, name, weight, category };
    });
}

function applyEquipmentSort(annotated) {
    const sortKey = equipmentSort;
    if (!sortKey) return annotated;
    const copy = annotated.slice();
    copy.sort((a, b) => {
        if (sortKey === 'name') return a.name.localeCompare(b.name);
        if (sortKey === 'weight_asc') return a.weight - b.weight;
        if (sortKey === 'weight_desc') return b.weight - a.weight;
        if (sortKey === 'category') {
            return a.category.localeCompare(b.category) || a.name.localeCompare(b.name);
        }
        return 0;
    });
    return copy;
}

function applyEquipmentFilter() {
    const needle = equipmentFilter.trim().toLowerCase();
    let visible = 0;
    document.querySelectorAll('#equipment-list .equipment-row').forEach(row => {
        const name = row.dataset.itemName || '';
        const matches = !needle || name.includes(needle);
        row.classList.toggle('is-hidden', !matches);
        if (matches) visible += 1;
    });
    return visible;
}

function updateBulkDeleteButton() {
    const selected = document.querySelectorAll(
        '#equipment-list .equipment-row:not(.is-hidden) .row-select:checked'
    );
    const button = document.getElementById('equipment-bulk-delete');
    const counter = document.getElementById('equipment-bulk-count');
    if (counter) counter.textContent = String(selected.length);
    if (button) button.disabled = selected.length === 0;
}

// Render equipment list
async function renderEquipmentList(equipment) {
    // Without the catalog the stored items still show (issue #797).
    const { catalog } = await catalogOrNotice();
    const list = document.getElementById('equipment-list');
    equipmentLatest = equipment || [];

    if (!equipment || equipment.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'no-equipment';
        empty.textContent = NO_EQUIPMENT_TEXT;
        list.replaceChildren(empty);
        updateBulkDeleteButton();
        return;
    }

    const annotated = applyEquipmentSort(annotateForSort(equipment, catalog));
    // Every value written into this markup is escaped, the limits too: they
    // come from the page's JSON config and the items from the stored sheet,
    // and neither is markup this page may trust (CodeQL js/xss-through-dom).
    const modifierBound = escapeHtml(NUMBER_LIMITS.equipment_modifier);
    const quantityBound = escapeHtml(NUMBER_LIMITS.equipment_quantity);
    const notesMaxLength = escapeHtml(TEXT_LIMITS.item_notes);

    list.innerHTML = annotated.map(({ item, originalIndex, name, weight: itemWeight, category }) => {
        const index = originalIndex;
        const isCustom = item.item_id === 'custom';
        const weight = itemWeight * (item.quantity || 1);
        const packClass = item.in_pack ? 'in-pack' : '';
        const droppedClass = item.in_pack && EQUIPMENT_CONFIG.packDropped ? 'pack-dropped' : '';
        const filterName = name.toLowerCase();

        if (!EQUIPMENT_CONFIG.canEdit) {
            // The read-only row (issue #273): the same facts as the editable
            // one -- name, weight, quantity, pack membership, notes -- as
            // plain text, with no controls to reach for.
            const readOnlyBits = [
                `<span class="item-name">${escapeHtml(name)}${isCustom ? ' <em>(custom)</em>' : ''}</span>`,
                `<span class="item-weight">${weight.toFixed(1)} lbs</span>`,
                `<span class="item-quantity">Qty: ${escapeHtml(item.quantity || 1)}</span>`,
                item.in_pack ? '<span class="item-pack">In pack</span>' : '',
                item.notes ? `<span class="item-notes">${escapeHtml(item.notes)}</span>` : '',
            ].filter(Boolean).join('\n                    ');
            return `
                <div class="equipment-row ${packClass} ${droppedClass}" data-index="${index}" data-item-name="${escapeHtml(filterName)}">
                    ${readOnlyBits}
                </div>
            `;
        }

        let modsHtml = heldControl(category, originalIndex);
        if (!EQUIPMENT_CONFIG.canSetItemModifiers) {
            // A player sees an item's modifiers, never edits them: the rules
            // give equipment none, so they are a Game Master's grant (#752).
            const equipped = category === 'armor' ? `
                    <div class="item-equipped">
                        <input type="checkbox" ${item.equipped ? 'checked' : ''} data-field="equipped">
                        <label>Equipped</label>
                    </div>` : '';
            modsHtml += equipped + itemModifierSummary(item);
        } else if (category === 'weapons') {
            modsHtml += `
                    <div class="item-mods">
                        <div class="mod-field">
                            <label>Hit:</label>
                            <input type="number" value="${escapeHtml(item.to_hit_mod || 0)}" data-field="to_hit_mod" min="-${modifierBound}" max="${modifierBound}">
                        </div>
                        <div class="mod-field">
                            <label>Dmg:</label>
                            <input type="number" value="${escapeHtml(item.damage_mod || 0)}" data-field="damage_mod" min="-${modifierBound}" max="${modifierBound}">
                        </div>
                        <div class="mod-field">
                            <label>Crit:</label>
                            <input type="number" value="${escapeHtml(item.critical_mod || 0)}" data-field="critical_mod" min="-${modifierBound}" max="${modifierBound}">
                        </div>
                    </div>
                `;
        } else if (category === 'armor') {
            // Armor needs equipped checkbox - only one can be equipped
            modsHtml += `
                    <div class="item-equipped">
                        <input type="checkbox" ${item.equipped ? 'checked' : ''} data-field="equipped">
                        <label>Equipped</label>
                    </div>
                    <div class="item-mods">
                        <div class="mod-field">
                            <label>Stops:</label>
                            <input type="number" value="${escapeHtml(item.stops_mod || 0)}" data-field="stops_mod" min="-${modifierBound}" max="${modifierBound}">
                        </div>
                        <div class="mod-field">
                            <label>DEX:</label>
                            <input type="number" value="${escapeHtml(item.dex_mod || 0)}" data-field="dex_mod" min="-${modifierBound}" max="${modifierBound}">
                        </div>
                    </div>
                `;
        } else if (category === 'shield') {
            modsHtml += `
                    <div class="item-mods">
                        <div class="mod-field">
                            <label>Stops:</label>
                            <input type="number" value="${escapeHtml(item.stops_mod || 0)}" data-field="stops_mod" min="-${modifierBound}" max="${modifierBound}">
                        </div>
                        <div class="mod-field">
                            <label>DEX:</label>
                            <input type="number" value="${escapeHtml(item.dex_mod || 0)}" data-field="dex_mod" min="-${modifierBound}" max="${modifierBound}">
                        </div>
                    </div>
                `;
        }

        return `
                <div class="equipment-row ${packClass} ${droppedClass}" data-index="${index}" data-item-name="${escapeHtml(filterName)}">
                    <input type="checkbox" class="row-select" aria-label="Select ${escapeHtml(name)} for bulk delete">
                    <span class="item-name">${escapeHtml(name)}${isCustom ? ' <em>(custom)</em>' : ''}</span>
                    <span class="item-weight">${weight.toFixed(1)} lbs</span>
                    <div class="item-quantity">
                        <label>Qty:</label>
                        <input type="number" value="${escapeHtml(item.quantity || 1)}" min="1" max="${quantityBound}" data-field="quantity">
                    </div>
                    <div class="item-pack">
                        <input type="checkbox" ${item.in_pack ? 'checked' : ''} data-field="in_pack">
                        <label>Pack</label>
                    </div>
                    <div class="item-notes">
                        <input type="text" value="${escapeHtml(item.notes || '')}" placeholder="Notes..." maxlength="${notesMaxLength}" data-field="notes">
                    </div>
                    ${modsHtml}
                    <button class="remove-btn" data-index="${index}">Remove</button>
                </div>
            `;
    }).join('');

    applyEquipmentFilter();
    updateBulkDeleteButton();
}

/**
 * The row's "In hand" / "In use" choice for a weapon or a shield (issues
 * #804, #846).
 *
 * One weapon is in hand and one shield in use at a time: the one the player
 * readied, else the first carried outside the pack. The server says which
 * (weapon_in_hand_index, shield_in_use_index); this only marks the row.
 * Readying a packed item draws it from the pack.
 *
 * @param {string} category - The item's category.
 * @param {number} index - Its equipment index.
 * @returns {string} HTML for the row, or '' for any other category.
 */
function heldControl(category, index) {
    let held;
    let label;
    if (category === 'weapons') {
        held = EQUIPMENT_CONFIG.weaponInHandIndex === index;
        label = 'In hand';
    } else if (category === 'shield') {
        held = EQUIPMENT_CONFIG.shieldInUseIndex === index;
        label = 'In use';
    } else {
        return '';
    }
    // A shield that is not in use because the weapon in hand takes both
    // hands says so in its row (review of #804).
    const idle = category === 'shield' && !held && EQUIPMENT_CONFIG.shieldIdleReason
        ? `<span class="item-held-note">${escapeHtml(EQUIPMENT_CONFIG.shieldIdleReason)}</span>`
        : '';
    return `
                    <div class="item-held">
                        <input type="radio" name="held-${category}" ${held ? 'checked' : ''} data-field="equipped" aria-label="${label}">
                        <label>${label}</label>
                        ${idle}
                    </div>`;
}

/**
 * An item's modifiers as text, with who set them (issue #752).
 *
 * Only a Game Master may set a modifier, and the server stamps the item with
 * their name when they do; an item carrying modifiers from before that rule
 * says it does not know who set them. Nothing is shown for an item with none.
 *
 * @param {object} item - The stored equipment entry.
 * @returns {string} HTML for the row.
 */
function itemModifierSummary(item) {
    const labels = {
        to_hit_mod: 'Hit', damage_mod: 'Dmg', critical_mod: 'Crit',
        stops_mod: 'Stops', dex_mod: 'DEX',
    };
    const parts = Object.entries(labels)
        .filter(([field]) => Number(item[field] || 0) !== 0)
        .map(([field, label]) => `${label} ${formatSigned(Number(item[field]))}`);
    if (parts.length === 0) return '';
    const who = item.modifiers_set_by
        ? `set by ${escapeHtml(item.modifiers_set_by)}, a Game Master`
        : 'not recorded who set them';
    return `
                    <span class="item-mods item-mods-granted">${parts.join(', ')} (${who})</span>`;
}

// Find item in catalog by ID. The catalog response is keyed by group, in the
// server's CATALOG_GROUPS order, so its own keys are the groups to search
// (issue #338 item 21).
function findItemInCatalog(catalog, itemId) {
    for (const group of Object.keys(catalog)) {
        if (catalog[group] && catalog[group][itemId]) {
            return catalog[group][itemId];
        }
    }
    return null;
}

// Category dropdown change
document.getElementById('equipment-category')?.addEventListener('change', async function () {
    const category = this.value;
    const itemSelect = document.getElementById('equipment-item');
    const addBtn = document.getElementById('add-equipment-btn');

    if (!category) {
        itemSelect.innerHTML = '<option value="">Select item...</option>';
        itemSelect.disabled = true;
        addBtn.disabled = true;
        return;
    }

    // A failed load says so and leaves the picker empty (issue #797).
    const { catalog, loaded } = await catalogOrNotice();
    if (!loaded) {
        itemSelect.innerHTML = '<option value="">Select item...</option>';
        itemSelect.disabled = true;
        addBtn.disabled = true;
        return;
    }
    const items = catalog[category] || {};

    itemSelect.innerHTML = '<option value="">Select item...</option>' +
        Object.entries(items)
            .sort((a, b) => a[1].name.localeCompare(b[1].name))
            .map(([id, item]) => `<option value="${id}">${escapeHtml(item.name)} (${item.weight} lbs)</option>`)
            .join('');
    itemSelect.disabled = false;
});

// Item dropdown change
document.getElementById('equipment-item')?.addEventListener('change', function () {
    document.getElementById('add-equipment-btn').disabled = !this.value;
});

// Add equipment
document.getElementById('add-equipment-btn')?.addEventListener('click', async function () {
    const itemId = document.getElementById('equipment-item').value;
    const inPack = document.getElementById('add-in-pack').checked;
    // Only rendered for a Game Master; the server checks who asked anyway.
    const freeBox = document.getElementById('add-free');
    const free = Boolean(freeBox && freeBox.checked);

    if (!itemId) return;

    this.disabled = true;
    try {
        const response = await postWithCsrf(
            EQUIPMENT_CONFIG.addUrl,
            csrfToken(),
            { item_id: itemId, in_pack: inPack, free: free }
        );

        const data = await response.json();
        if (data.success) {
            updateEquipmentDisplay(data);
            // Reset form
            document.getElementById('equipment-category').value = '';
            document.getElementById('equipment-item').innerHTML = '<option value="">Select item...</option>';
            document.getElementById('equipment-item').disabled = true;
            document.getElementById('add-in-pack').checked = false;
        } else {
            uiNotify(data.error || 'Failed to add item');
        }
    } catch (error) {
        uiNotify('Error: ' + error.message);
    }
    // The button offers what the selects hold: nothing after a success, which
    // resets them, and the player's choice after a failure. It used to be
    // disabled unconditionally, and since the item select's change listener is
    // the only thing that re-enables it and the choice had not changed, a retry
    // after a transient refusal needed the whole picker re-driven (issue #659).
    this.disabled = !document.getElementById('equipment-item').value;
});

// Equipment list delegated events
document.getElementById('equipment-list')?.addEventListener('change', async function (e) {
    const row = e.target.closest('.equipment-row');
    if (!row) return;

    const index = parseInt(row.dataset.index);
    const field = e.target.dataset.field;
    if (!field) return;

    // A radio is the weapon-in-hand / shield-in-use choice (#804, #846).
    const isToggle = e.target.type === 'checkbox' || e.target.type === 'radio';
    let value = isToggle ? e.target.checked : e.target.value;
    if (e.target.type === 'number') {
        value = parseInt(value) || 0;
    }

    // The same saving / saved / error states autosave.js gives a field, on
    // the field's own wrapper (issue #339, item 22). It used to say nothing
    // on success and only raise a page notice on failure.
    const input = e.target;
    const container = input.parentElement;
    clearFieldError(container);
    container.classList.remove('saved', 'error');
    container.classList.add('saving');

    function refuse(message) {
        container.classList.remove('saving');
        container.classList.add('error');
        showFieldError(container, message);
        // Put back the value the row was last rendered with: the server
        // refused the new one, so the sheet still holds the old.
        if (input.type === 'checkbox' || input.type === 'radio') {
            input.checked = input.defaultChecked;
            // The radio that was in hand before is the one to put back.
            if (input.type === 'radio') {
                document.querySelectorAll(`#equipment-list input[name="${input.name}"]`)
                    .forEach((radio) => { radio.checked = radio.defaultChecked; });
            }
        } else {
            input.value = input.defaultValue;
        }
    }

    try {
        const response = await postWithCsrf(
            EQUIPMENT_CONFIG.updateUrl,
            csrfToken(),
            { index, [field]: value }
        );

        const data = await response.json();
        if (data.success) {
            // The list is re-rendered from the response, so the saved mark
            // goes on the fresh row's field.
            await updateEquipmentDisplay(data);
            const saved = document.querySelector(
                `#equipment-list .equipment-row[data-index="${index}"] [data-field="${field}"]`
            );
            if (saved) saved.parentElement.classList.add('saved');
        } else {
            refuse(data.error || 'Failed to update item');
        }
    } catch (error) {
        refuse(FIELD_NETWORK_ERROR);
    }
});

// Remove equipment
document.getElementById('equipment-list')?.addEventListener('click', async function (e) {
    if (!e.target.classList.contains('remove-btn')) return;

    const index = parseInt(e.target.dataset.index);
    // Ask first, like bulk delete does: the row goes at once and there is no
    // undo on the sheet (issue #339, item 19).
    const itemName = e.target.closest('.equipment-row')?.querySelector('.item-name')?.textContent.trim() || 'this item';
    if (!await uiConfirm(`Remove ${itemName}?`, { danger: true, confirmLabel: 'Remove' })) return;

    try {
        const response = await postWithCsrf(
            EQUIPMENT_CONFIG.removeUrl,
            csrfToken(),
            { index }
        );

        const data = await response.json();
        if (data.success) {
            updateEquipmentDisplay(data);
        } else {
            uiNotify(data.error || 'Failed to remove item');
        }
    } catch (error) {
        uiNotify('Error: ' + error.message);
    }
});

// Pack dropped toggle
document.getElementById('pack-dropped')?.addEventListener('change', async function () {
    try {
        const response = await postWithCsrf(
            EQUIPMENT_CONFIG.togglePackUrl,
            csrfToken()
        );

        const data = await response.json();
        if (data.success) {
            updateEquipmentDisplay(data);
        } else {
            uiNotify(data.error || 'Failed to toggle pack');
            this.checked = !this.checked;
        }
    } catch (error) {
        uiNotify('Error: ' + error.message);
        this.checked = !this.checked;
    }
});

// Custom item panel
document.getElementById('add-custom-btn')?.addEventListener('click', function () {
    document.getElementById('custom-item-panel').style.display = 'block';
    document.getElementById('custom-name').focus();
});

document.getElementById('custom-cancel-btn')?.addEventListener('click', function () {
    document.getElementById('custom-item-panel').style.display = 'none';
    resetCustomForm();
});

// Show/hide category-specific fields
document.getElementById('custom-category')?.addEventListener('change', function () {
    const category = this.value;
    document.getElementById('custom-weapon-fields').style.display = category === 'weapons' ? 'block' : 'none';
    document.getElementById('custom-armor-fields').style.display = (category === 'armor' || category === 'shield') ? 'flex' : 'none';
});

function resetCustomForm() {
    document.getElementById('custom-name').value = '';
    document.getElementById('custom-category').value = 'gear';
    document.getElementById('custom-weight').value = '0';
    document.getElementById('custom-cost').value = '0';
    document.getElementById('custom-damage').value = '1d6';
    document.getElementById('custom-stops').value = '0';
    document.getElementById('custom-dex-penalty').value = '0';
    document.getElementById('custom-in-pack').checked = false;
    document.getElementById('custom-notes').value = '';
    document.getElementById('custom-weapon-fields').style.display = 'none';
    document.getElementById('custom-armor-fields').style.display = 'none';
}

document.getElementById('custom-add-btn')?.addEventListener('click', async function () {
    const name = document.getElementById('custom-name').value.trim();
    if (!name) {
        uiNotify('Please enter an item name');
        return;
    }

    const category = document.getElementById('custom-category').value;
    const data = {
        is_custom: true,
        name: name,
        category: category,
        weight: parseFloat(document.getElementById('custom-weight').value) || 0,
        cost: parseFloat(document.getElementById('custom-cost').value) || 0,
        in_pack: document.getElementById('custom-in-pack').checked,
        notes: document.getElementById('custom-notes').value.trim(),
    };

    if (category === 'weapons') {
        data.damage = document.getElementById('custom-damage').value.trim() || '1d6';
    } else if (category === 'armor' || category === 'shield') {
        data.stops = parseInt(document.getElementById('custom-stops').value) || 0;
        data.dex_penalty = parseInt(document.getElementById('custom-dex-penalty').value) || 0;
    }

    this.disabled = true;
    try {
        const response = await postWithCsrf(
            EQUIPMENT_CONFIG.addUrl,
            csrfToken(),
            data
        );

        const result = await response.json();
        if (result.success) {
            updateEquipmentDisplay(result);
            document.getElementById('custom-item-panel').style.display = 'none';
            resetCustomForm();
        } else {
            uiNotify(result.error || 'Failed to add custom item');
        }
    } catch (error) {
        uiNotify('Error: ' + error.message);
    } finally {
        this.disabled = false;
    }
});

// Initialize equipment display
(async function () {
    await renderEquipmentList(EQUIPMENT_CONFIG.initialEquipment);

    // Apply persisted sort to the freshly rendered list.
    const sortSelect = document.getElementById('equipment-sort');
    if (sortSelect && equipmentSort) sortSelect.value = equipmentSort;
})();

// ---- Equipment filter / sort / bulk-delete controls ----
document.getElementById('equipment-filter')?.addEventListener('input', function () {
    equipmentFilter = this.value;
    applyEquipmentFilter();
    updateBulkDeleteButton();
});

document.getElementById('equipment-sort')?.addEventListener('change', function () {
    equipmentSort = this.value;
    localStorage.setItem(EQUIPMENT_SORT_KEY, equipmentSort);
    renderEquipmentList(equipmentLatest);
});

document.getElementById('equipment-list')?.addEventListener('change', function (event) {
    if (event.target.classList.contains('row-select')) {
        updateBulkDeleteButton();
    }
});

document.getElementById('equipment-bulk-delete')?.addEventListener('click', async function () {
    const selected = Array.from(
        document.querySelectorAll('#equipment-list .equipment-row:not(.is-hidden) .row-select:checked')
    );
    if (selected.length === 0) return;
    if (!await uiConfirm(`Delete ${selected.length} selected item(s)?`, { danger: true, confirmLabel: "Delete" })) return;

    const indices = selected.map(checkbox => Number(checkbox.closest('.equipment-row').dataset.index));
    this.disabled = true;
    try {
        const response = await postWithCsrf(
            EQUIPMENT_CONFIG.bulkRemoveUrl,
            csrfToken(),
            { indices }
        );
        const data = await response.json();
        if (response.ok && data.success !== false) {
            updateEquipmentDisplay(data);
        } else {
            uiNotify(data.error || 'Failed to delete selected items');
        }
    } catch (error) {
        uiNotify('Error: ' + error.message);
    } finally {
        this.disabled = false;
    }
});

// ---- DM-only mark dead / revive ----
// postJson is api.js's: this file's own copy did exactly what that one does
// -- POST the body with the page's token, parse the answer if there is one,
// and hand back whether the server accepted it (issue #338).

// The cause is asked for in the page's own panel, not the browser's prompt()
// (issue #339, item 9). uiPrompt resolves with the trimmed text or null, so
// the shape of what follows is unchanged; what changed is that the question
// is styled, keyboard-reachable, screen-readable and testable, and that the
// character sheet stays visible behind it while it is answered.
document.getElementById('mark-dead-btn')?.addEventListener('click', async function () {
    const cause = await uiPrompt('Cause of death:', {
        confirmLabel: 'Mark dead',
        maxLength: TEXT_LIMITS.death_cause,
        placeholder: 'How they died',
    });
    if (cause === null) return;
    const trimmed = cause.trim();
    if (!trimmed) {
        uiNotify('Cause of death is required');
        return;
    }
    this.disabled = true;
    try {
        const { ok, data } = await postJson(
            CHARACTER_EDIT_CONFIG.markDeadUrl,
            { cause: trimmed }
        );
        if (ok && data.success) {
            pageNavigation.reload();
        } else {
            uiNotify(data.error || 'Failed to mark character dead');
            this.disabled = false;
        }
    } catch (error) {
        // postJson only guards the body parse; the fetch itself rejects on a
        // network failure, which used to kill the handler at that line and
        // leave this button disabled for the rest of the page's life, having
        // said nothing (issue #643).
        uiNotify(FIELD_NETWORK_ERROR);
        this.disabled = false;
    }
});

// ---- The Fatigue roll (issue #636) ----
// The one field on this sheet with no widget until now. The server rolls the
// dice (code owns the dice) and answers with the roll, the pools it moves and
// the re-rendered injury readout; this only writes them out. A player has one
// roll, so the button goes away once the server says there is no second one.
document.getElementById('roll-fatigue-btn')?.addEventListener('click', async function () {
    this.disabled = true;
    try {
        const { ok, data } = await postJson(CHARACTER_EDIT_CONFIG.rollFatigueUrl, {});
        if (ok && data.success) {
            document.getElementById('fatigue-roll').textContent = data.fatigue_roll;
            // The roll can complete the character (issue #764); paintSheet
            // swaps in the re-rendered injury readout as well.
            paintSheet(data);
            if (data.can_roll_fatigue) {
                this.disabled = false;
            } else {
                this.remove();
            }
            uiNotify(data.note, 'success');
        } else {
            uiNotify(data.error || 'Failed to roll Fatigue');
            this.disabled = false;
        }
    } catch (error) {
        uiNotify(FIELD_NETWORK_ERROR);
        this.disabled = false;
    }
});

// ---- Current Fatigue and Body (issue #751) ----
// A Game Master's inputs, inside the injury readout the server re-renders, so
// the listener sits on the readout's container and survives each swap. A
// value is saved when its input loses focus, as every field on this sheet is;
// the answer carries the readout re-rendered from the record, which puts the
// saved value and the injury band back on the page.
document.getElementById('injury-thresholds')?.addEventListener('change', async function (event) {
    const input = event.target.closest('.current-pool-input');
    if (!input) return;
    input.disabled = true;
    try {
        const { ok, data } = await postJson(
            CHARACTER_EDIT_CONFIG.setPoolsUrl,
            { [input.dataset.pool]: input.value }
        );
        if (ok && data.success) {
            // The readout and the Magic section's Mana are paintSheet's.
            paintSheet(data);
            uiNotify(data.note, 'success');
        } else {
            uiNotify(data.error || 'Failed to set the pools');
            refusePool(input);
        }
    } catch (error) {
        uiNotify(FIELD_NETWORK_ERROR);
        refusePool(input);
    }
});

// The Magic section's "Mana: n of a Pool of m" reads the same record the
// injury readout does; a set Mana repaints it from the server's answer.
function paintCurrentMana(data) {
    const current = document.getElementById('mana-current');
    if (current && data.current_mana !== undefined) {
        current.textContent = data.current_mana;
    }
}

// A refused or unsent value goes back to what the record holds -- the value
// the server rendered the input with -- so the sheet never shows a number
// the record does not.
function refusePool(input) {
    input.value = input.defaultValue;
    input.disabled = false;
}

// The literacy check's control (issue #741). The server rolls the 3d6 and
// answers with the row's values; this repaints the row from them, which also
// hides the control once a player has had their one roll.
document.getElementById('roll-literacy-btn')?.addEventListener('click', async function () {
    this.disabled = true;
    try {
        const { ok, data } = await postJson(CHARACTER_EDIT_CONFIG.rollLiteracyUrl, {});
        if (ok && data.success) {
            paintLiteracyRow(data);
            paintSheet(data);
            uiNotify(data.note, 'success');
        } else {
            uiNotify(data.error || 'Failed to roll literacy');
        }
    } catch (error) {
        uiNotify(FIELD_NETWORK_ERROR);
    } finally {
        this.disabled = false;
    }
});

// ---- The sheet's other rolls (issues #748, #749) ----
// Attributes, appearance, provenance, wealth and a Game Master's coin reroll.
// The server rolls, holds a player to one roll of each, and writes the dice
// into the dice log; a roll can move many cells at once, so the page reloads
// from the saved character, where the result stands in the cells, the dice
// log and the recent changes.
/**
 * Wire one of the sheet's roll controls: ask first where it replaces a
 * recorded value, post, and reload on the saved character. The controls at
 * load are wired below; one that arrives in a re-rendered block (the
 * survival save's, issue #839) is wired by the code that swaps it in.
 *
 * @param {HTMLButtonElement} button - A `.sheet-roll-btn`.
 */
function bindSheetRoll(button) {
    button.addEventListener('click', async function () {
        // A Game Master's roll over a recorded value replaces it: ask first,
        // in the page's own persistent confirm (issue #748 review). A control
        // may name its own confirm button (the survival save's, #839).
        if (this.dataset.confirm
            && !await uiConfirm(this.dataset.confirm, {
                confirmLabel: this.dataset.confirmLabel || 'Roll again',
            })) {
            return;
        }
        this.disabled = true;
        try {
            const { ok, data } = await postJson(this.dataset.rollUrl, {});
            if (ok && data.success) {
                pageNavigation.reload();
                return;
            }
            uiNotify(data.error || 'The roll failed');
        } catch (error) {
            uiNotify(FIELD_NETWORK_ERROR);
        }
        this.disabled = false;
    });
}

document.querySelectorAll('.sheet-roll-btn').forEach(bindSheetRoll);

// The Game Master's literacy ruling (issue #743). It autosaves on change like
// every field on this sheet; a refusal puts the select back to the ruling the
// server last confirmed, which is what the row still shows. The page renders
// the stored ruling selected, so that is the first confirmed value.
const literacyRulingSelect = document.getElementById('literacy-ruling-select');
if (literacyRulingSelect) literacyRulingSelect.dataset.confirmed = literacyRulingSelect.value;
literacyRulingSelect?.addEventListener('change', async function () {
    const confirmed = this.dataset.confirmed;
    this.disabled = true;
    try {
        const { ok, data } = await postJson(
            CHARACTER_EDIT_CONFIG.ruleLiteracyUrl,
            { literate: rulingFromOption(this.value) }
        );
        if (ok && data.success) {
            paintLiteracyRow(data);
            paintSheet(data);
            uiNotify(data.note, 'success');
        } else {
            this.value = confirmed;
            uiNotify(data.error || 'Failed to record the ruling');
        }
    } catch (error) {
        this.value = confirmed;
        uiNotify(FIELD_NETWORK_ERROR);
    } finally {
        this.dataset.confirmed = this.value;
        this.disabled = false;
    }
});

// What a player's import dropped (issue #744). The notice stays until it is
// dismissed here; the server clears it, so it does not come back on a reload.
document.getElementById('dismiss-import-notice-btn')?.addEventListener('click', async function () {
    this.disabled = true;
    try {
        const { ok, data } = await postJson(CHARACTER_EDIT_CONFIG.dismissImportNoticeUrl, {});
        if (ok && data.success) {
            document.getElementById('import-notice').remove();
            paintSheet(data);
        } else {
            uiNotify(data.error || 'Failed to dismiss the notice');
            this.disabled = false;
        }
    } catch (error) {
        uiNotify(FIELD_NETWORK_ERROR);
        this.disabled = false;
    }
});

// ---- Visibility toggle (issue #273) ----
// Owner/DM only -- the control renders for no one else. Autosaves on change
// like every field on this sheet, then updates the badge in place
// (state-driven: the badge always shows what the server last confirmed).
document.getElementById('visibility-toggle')?.addEventListener('change', async function () {
    const makePublic = this.checked;
    this.disabled = true;
    try {
        const { ok, data } = await postJson(
            CHARACTER_EDIT_CONFIG.setVisibilityUrl,
            { is_public: makePublic }
        );
        if (ok && data.success) {
            const badge = document.getElementById('visibility-badge');
            badge.textContent = data.is_public ? 'Public' : 'Private';
            badge.classList.toggle('visibility-public', data.is_public);
            badge.classList.toggle('visibility-private', !data.is_public);
        } else {
            this.checked = !makePublic;  // the server said no; show what stands
            uiNotify(data.error || 'Failed to change visibility');
        }
    } catch (error) {
        // The browser has already flipped the box (this is a change
        // handler), so a network failure used to leave a checkbox reading
        // "Public" beside a badge reading "Private", permanently disabled,
        // with no error anywhere (issue #643). The badge is what the server
        // last confirmed, so the box goes back to it.
        this.checked = !makePublic;
        uiNotify(FIELD_NETWORK_ERROR);
    } finally {
        this.disabled = false;
    }
});

document.getElementById('revive-btn')?.addEventListener('click', async function () {
    if (!await uiConfirm('Revive this character?', { confirmLabel: 'Revive' })) return;
    this.disabled = true;
    try {
        const { ok, data } = await postJson(
            CHARACTER_EDIT_CONFIG.reviveUrl,
            {}
        );
        if (ok && data.success) {
            pageNavigation.reload();
        } else {
            uiNotify(data.error || 'Failed to revive character');
            this.disabled = false;
        }
    } catch (error) {
        // As with Mark Dead above: a network failure used to end the handler
        // here and leave the button dead for good (issue #643).
        uiNotify(FIELD_NETWORK_ERROR);
        this.disabled = false;
    }
});

// Export for testing (Node.js environment)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        refreshAttackWeapons,
        CHARACTER_EDIT_CONFIG,
        SKILLS_CONFIG,
        EQUIPMENT_CONFIG,
        escapeHtml,
        titleCaseSkill,
        createPanel,
        updateSkillsUI,
        refreshFatigueBodyInjury,
        paintText,
        paintMovement,
        paintSheet,
        paintPointControls,
        heldControl,
        showCatalogError,
        retryCatalog,
        paintFieldInput,
        lockFieldCell,
        paintSheetRolls,
        paintLifeControls,
        paintPlayWithGameMaster,
        itemModifierSummary,
        LITERACY_RESPONSE_KEYS,
        paintLiteracyRow,
        prependRunToLog,
        requestedRunYears,
        renderWorkbenchPreview,
        showDeathCard,
        submitNewSkill,
        renameSkill,
        allocateSkillPoint,
        loadCatalog,
        updateEquipmentDisplay,
        annotateForSort,
        applyEquipmentSort,
        applyEquipmentFilter,
        updateBulkDeleteButton,
        renderEquipmentList,
        findItemInCatalog,
        resetCustomForm,
        postJson,
    };
}
