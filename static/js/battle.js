/**
 * Battle detail page: SVG hex arena + per-turn event log + Advance turn.
 *
 * State-driven per house rules: the arena and roster render entirely from the
 * server's view model (embedded via json_script on load, returned by the
 * advance endpoint thereafter), so a full reload reproduces exactly what
 * incremental updates showed. The view model is profile-neutral (#251) — the
 * server converts a classic Melee battle's rectangular offset field and its
 * ST pool into the same shape a Tarmar battle answers in, so this file draws
 * both without knowing which rules produced them. The event log is
 * server-rendered for existing turns, and new turns arrive from the endpoints
 * already rendered by the same battle/_turn_group.html partial (#504), which
 * appendTurns() inserts. Nothing is transient: every roll and decision stays
 * on the page until the user navigates away.
 *
 * postJson/postForSuccess/bindCommitKeys/uiNotify and the inline field-error
 * pair come from api.js, which base.html loads in <head>. Node/Jest has no shared script
 * scope, so require it there; either way the names end up on the global
 * object.
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
}

(function (root) {
    const HEX_SIZE = 10; // SVG units per hex circumradius
    const SQRT3 = Math.sqrt(3);

    // Which way up the hexes sit. The server names it per battle: the Tarmar
    // arena is pointy-top, the classic Melee map flat-top (the orientation the
    // printed map uses). It is a layout parameter, not a second renderer.
    const FLAT = 'flat';

    /**
     * A candidate's score at the width its rationale was written to.
     *
     * The engine sends score_text so the number beside an option matches the
     * "... = 0.500" its rationale ends in (#301); a bare float renders at
     * whatever width repr chose. Battles logged before that field existed
     * fall back to the raw score.
     */
    function scoreText(candidate) {
        return candidate.score_text === undefined
            ? candidate.score
            : candidate.score_text;
    }

    /**
     * Axial (q, r) to pixel centre, in the given orientation.
     *
     * Both profiles' coordinates reach the page as axial (q, r) — the server
     * converts classic's 1-based offset col/row through the arena's own
     * layout — so this is the one place hexes become pixels.
     */
    function axialToPixel(q, r, orientation) {
        if (orientation === FLAT) {
            return {
                x: HEX_SIZE * 1.5 * q,
                y: HEX_SIZE * SQRT3 * (r + q / 2),
            };
        }
        return {
            x: HEX_SIZE * (SQRT3 * q + (SQRT3 / 2) * r),
            y: HEX_SIZE * 1.5 * r,
        };
    }

    /** The six corner points of a hex centred on (cx, cy), in orientation. */
    function hexCorners(cx, cy, size, orientation) {
        const skew = orientation === FLAT ? 0 : -30;
        const points = [];
        for (let corner = 0; corner < 6; corner += 1) {
            const angle = (Math.PI / 180) * (60 * corner + skew);
            points.push(
                `${(cx + size * Math.cos(angle)).toFixed(2)},${(cy + size * Math.sin(angle)).toFixed(2)}`
            );
        }
        return points.join(' ');
    }

    /** Half the width and height of one hex, so a viewBox can clear its edges. */
    function hexHalfSize(orientation) {
        return orientation === FLAT
            ? { x: HEX_SIZE, y: (HEX_SIZE * SQRT3) / 2 }
            : { x: (HEX_SIZE * SQRT3) / 2, y: HEX_SIZE };
    }

    /**
     * The viewBox that frames these cells with a hex of margin.
     *
     * Derived from the cells the server sent rather than from an arena
     * radius, because the two profiles' fields are different shapes: a disc
     * centred on the origin, and a rectangle running away from it.
     */
    function arenaViewBox(cells, orientation) {
        if (!cells.length) { return '0 0 100 100'; }
        const half = hexHalfSize(orientation);
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        cells.forEach(([q, r]) => {
            const { x, y } = axialToPixel(q, r, orientation);
            minX = Math.min(minX, x - half.x);
            maxX = Math.max(maxX, x + half.x);
            minY = Math.min(minY, y - half.y);
            maxY = Math.max(maxY, y + half.y);
        });
        const margin = HEX_SIZE;
        return [
            (minX - margin).toFixed(2),
            (minY - margin).toFixed(2),
            (maxX - minX + 2 * margin).toFixed(2),
            (maxY - minY + 2 * margin).toFixed(2),
        ].join(' ');
    }

    function svgElement(tag, attributes) {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
        Object.entries(attributes).forEach(([name, value]) => {
            node.setAttribute(name, value);
        });
        return node;
    }

    /** Fraction of a pool remaining, clamped to [0, 1] for the bar width. */
    function poolFraction(current, maximum) {
        if (!maximum || maximum <= 0) { return 0; }
        return Math.max(0, Math.min(1, current / maximum));
    }

    // Pool bar colours by the server's pool key. Unknown pools still draw,
    // in the neutral tone, rather than vanishing.
    // The death, fatigue and gold tones are base.css tokens (#338 item 12),
    // read from the page so the stylesheet stays their one definition.
    function cssToken(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }
    const POOL_COLOURS = {
        fatigue: cssToken('--colour-pool-fatigue'),
        body: cssToken('--colour-pool-death'),
        st: cssToken('--colour-pool-death'),
    };
    const DEFAULT_POOL_COLOUR = '#5a6270';

    // Team tints (#275): mid-lightness, well-separated hues that read on
    // both the light and dark themes, assigned by order of first appearance
    // in the view's roster and repeating past six teams. Deliberately away
    // from the character blue (#4a6da7) and beast brown (#8a5a2b) so a
    // free-for-all marker never masquerades as a team.
    const TEAM_TINTS = [
        '#c96f2a', // orange
        '#7d5bbe', // violet
        '#1f8a70', // teal
        '#c25588', // magenta
        '#6b8e23', // olive
        '#3f7cac', // steel blue
    ];

    /** Teams in this view, in order of first appearance in the roster. */
    function teamOrderOf(view) {
        const order = [];
        (view.combatants || []).forEach((combatant) => {
            if (combatant.team && !order.includes(combatant.team)) {
                order.push(combatant.team);
            }
        });
        return order;
    }

    /** The tint for one team, or null for the free-for-all (no team). */
    function teamTint(teamOrder, team) {
        const index = teamOrder.indexOf(team);
        if (index === -1) { return null; }
        return TEAM_TINTS[index % TEAM_TINTS.length];
    }

    /**
     * Render the hex arena from the server's view model: the cells it sent,
     * then one marker per living combatant — a facing wedge, a body circle,
     * the name, and a bar per pool.
     *
     * Every piece of geometry here arrives computed: which hexes make the
     * field, which hexes a figure covers, and which way it faces. The page
     * turns those into pixels and paints; it works out nothing about the
     * rules, which is what lets one renderer draw both profiles.
     *
     * ``picker`` (optional) is the hex picker for a selected option that
     * places the figure (#819, #866): ``{hexes, chosen, onPick}``, every
     * hex the server listed for the option, drawn on top so each is a
     * button, the chosen one marked.
     */
    function renderArena(svg, view, picker) {
        svg.textContent = '';
        const arena = view.arena || {};
        const orientation = arena.orientation;
        const cells = arena.cells || [];
        svg.setAttribute('viewBox', arenaViewBox(cells, orientation));

        cells.forEach(([q, r]) => {
            const { x, y } = axialToPixel(q, r, orientation);
            svg.appendChild(svgElement('polygon', {
                points: hexCorners(x, y, HEX_SIZE - 0.4, orientation),
                fill: 'none',
                stroke: 'currentColor',
                'stroke-opacity': '0.15',
                'stroke-width': '0.5',
            }));
        });

        const teamOrder = teamOrderOf(view);
        (view.combatants || []).forEach((combatant) => {
            if (!combatant.alive) { return; }
            const { x, y } = axialToPixel(combatant.q, combatant.r, orientation);
            // A teamed combatant wears its team's tint (#275); the free
            // pool keeps the historic character blue and beast brown.
            const bodyColour = teamTint(teamOrder, combatant.team)
                || (combatant.is_beast ? '#8a5a2b' : '#4a6da7');
            const group = svgElement('g', {
                class: `arena-combatant${combatant.conscious ? '' : ' is-down'}`
                    + (combatant.is_beast ? ' is-beast' : '')
                    // Only a figure this viewer may read is offered as a
                    // target for the click that opens its panel (#302).
                    + (combatant.detail ? ' is-selectable' : ''),
                'data-combatant': combatant.combatant_id,
                'data-team': combatant.team || '',
            });
            // Multi-hex figures paint their whole footprint under the marker.
            const footprint = combatant.footprint || [];
            if (footprint.length > 1) {
                footprint.forEach(([hq, hr]) => {
                    const centre = axialToPixel(hq, hr, orientation);
                    group.appendChild(svgElement('polygon', {
                        class: 'footprint-hex',
                        points: hexCorners(
                            centre.x, centre.y, HEX_SIZE - 0.6, orientation
                        ),
                        fill: bodyColour,
                        'fill-opacity': '0.3',
                        stroke: bodyColour,
                        'stroke-width': '0.5',
                    }));
                });
            }
            // Facing wedge: a small triangle pointing at the faced hex. The
            // server sends the step to that hex, so the angle is the pixel
            // direction of the step — no direction table on this side.
            const [stepQ, stepR] = combatant.facing_vector || [1, 0];
            const faced = axialToPixel(
                combatant.q + stepQ, combatant.r + stepR, orientation
            );
            const angle = Math.atan2(faced.y - y, faced.x - x);
            const tipX = x + Math.cos(angle) * HEX_SIZE * 0.95;
            const tipY = y + Math.sin(angle) * HEX_SIZE * 0.95;
            const leftX = x + Math.cos(angle + 0.5) * HEX_SIZE * 0.55;
            const leftY = y + Math.sin(angle + 0.5) * HEX_SIZE * 0.55;
            const rightX = x + Math.cos(angle - 0.5) * HEX_SIZE * 0.55;
            const rightY = y + Math.sin(angle - 0.5) * HEX_SIZE * 0.55;
            group.appendChild(svgElement('polygon', {
                class: 'facing-wedge',
                points: `${tipX.toFixed(2)},${tipY.toFixed(2)} ${leftX.toFixed(2)},${leftY.toFixed(2)} ${rightX.toFixed(2)},${rightY.toFixed(2)}`,
                fill: cssToken('--colour-accent-gold'),
            }));
            group.appendChild(svgElement('circle', {
                cx: x, cy: y, r: HEX_SIZE * 0.5,
                fill: combatant.conscious ? bodyColour : '#777',
                stroke: '#222', 'stroke-width': '0.5',
            }));
            group.appendChild(Object.assign(svgElement('text', {
                x, y: y - HEX_SIZE * 0.85,
                'text-anchor': 'middle',
                'font-size': '3.4',
                fill: 'currentColor',
            }), { textContent: shortName(combatant.name) }));
            // One bar per pool, stacked under the name — two for a Tarmar
            // combatant (fatigue, body), one for a classic figure (ST).
            (combatant.pools || []).forEach((pool, index) => {
                const width = HEX_SIZE * 1.4;
                group.appendChild(svgElement('rect', {
                    class: `pool-${pool.key}`,
                    x: x - width / 2,
                    y: y + HEX_SIZE * (0.5 + index * 0.12),
                    width: (
                        width * poolFraction(pool.current, pool.maximum)
                    ).toFixed(2),
                    height: 1.1,
                    fill: POOL_COLOURS[pool.key] || DEFAULT_POOL_COLOUR,
                }));
            });
            svg.appendChild(group);
        });
        renderGroundWeapons(svg, view, orientation);
        if (picker) { renderDestinationPicker(svg, picker, orientation); }
    }

    /** Do two hexes, as ``[q, r]`` pairs, name the same cell? */
    function sameHex(one, other) {
        return Boolean(one && other) && one[0] === other[0] && one[1] === other[1];
    }

    /** A hex as the page writes it: "(q, r)", the engine's own spelling. */
    function hexLabel(hex) {
        return `(${hex[0]}, ${hex[1]})`;
    }

    /**
     * The hex picker (#819, #866): one button per hex the server listed for
     * the selected option, from the engine's own rule, so nothing it offers
     * is a hex the engine would refuse on this board. The chosen hex is
     * marked; the picker holds no state of its own, so a repaint draws
     * exactly the selection. (The AI names no hex on tarmar-engine 0.9.10,
     * house ruling ``ai_destinations``, so there is no AI hex to mark; a
     * hand-off would still carry one the AI named.)
     */
    function renderDestinationPicker(svg, picker, orientation) {
        const layer = svgElement('g', {
            class: 'arena-destinations',
            role: 'group',
            'aria-label': 'Hexes this action can move to',
        });
        (picker.hexes || []).forEach((hex) => {
            const { x, y } = axialToPixel(hex[0], hex[1], orientation);
            const chosen = sameHex(hex, picker.chosen);
            const cell = svgElement('polygon', {
                class: 'arena-destination' + (chosen ? ' is-chosen' : ''),
                points: hexCorners(x, y, HEX_SIZE - 1.2, orientation),
                role: 'button',
                tabindex: '0',
                'aria-pressed': chosen ? 'true' : 'false',
                'aria-label': `Move to hex ${hexLabel(hex)}`,
                'data-hex': `${hex[0]},${hex[1]}`,
            });
            const pick = () => { if (picker.onPick) { picker.onPick(hex); } };
            cell.addEventListener('click', pick);
            cell.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    pick();
                }
            });
            layer.appendChild(cell);
            if (chosen) {
                layer.appendChild(svgElement('circle', {
                    class: 'arena-destination-mark',
                    cx: x, cy: y, r: HEX_SIZE * 0.25,
                    'pointer-events': 'none',
                }));
            }
        });
        svg.appendChild(layer);
    }

    /**
     * What a hex's lying weapons are called, for its tooltip and its label.
     *
     * @param {{weapons: string[]}} entry - one of the view's ground_weapons
     * @returns {string} e.g. "Lying here: Broadsword, Dagger"
     */
    function groundWeaponLabel(entry) {
        return `Lying here: ${(entry.weapons || []).join(', ')}`;
    }

    /**
     * Draw the weapons lying on the field (issue #878): a small blade in the
     * hex's left-hand corner, clear of a figure standing on it, named in the
     * hex's tooltip. PICK UP WEAPON (q) takes one from the figure's own hex
     * or the next, so where each lies is what a player needs to see. Drawn
     * over the figures, so a figure's marker never hides the tooltip.
     */
    function renderGroundWeapons(svg, view, orientation) {
        (view.ground_weapons || []).forEach((entry) => {
            const { x, y } = axialToPixel(entry.q, entry.r, orientation);
            const label = groundWeaponLabel(entry);
            const group = svgElement('g', {
                class: 'arena-ground-weapon',
                'data-q': entry.q,
                'data-r': entry.r,
                role: 'img',
                'aria-label': label,
            });
            group.appendChild(Object.assign(
                svgElement('title', {}), { textContent: label }
            ));
            const centreX = x - HEX_SIZE * 0.62;
            const centreY = y + HEX_SIZE * 0.1;
            const reach = HEX_SIZE * 0.2;
            // The blade, hilt low-left and point up-right, and its crossguard.
            group.appendChild(svgElement('line', {
                class: 'ground-weapon-blade',
                x1: (centreX - reach).toFixed(2), y1: (centreY + reach).toFixed(2),
                x2: (centreX + reach).toFixed(2), y2: (centreY - reach).toFixed(2),
                stroke: 'currentColor', 'stroke-width': '0.9',
                'stroke-linecap': 'round',
            }));
            group.appendChild(svgElement('line', {
                class: 'ground-weapon-guard',
                x1: (centreX - reach * 0.9).toFixed(2), y1: (centreY - reach * 0.1).toFixed(2),
                x2: (centreX - reach * 0.1).toFixed(2), y2: (centreY + reach * 0.9).toFixed(2),
                stroke: 'currentColor', 'stroke-width': '0.7',
                'stroke-linecap': 'round',
            }));
            if ((entry.weapons || []).length > 1) {
                group.appendChild(Object.assign(svgElement('text', {
                    class: 'ground-weapon-count',
                    x: (centreX + reach).toFixed(2),
                    y: (centreY + reach * 1.6).toFixed(2),
                    'font-size': '2.4',
                    fill: 'currentColor',
                }), { textContent: String(entry.weapons.length) }));
            }
            svg.appendChild(group);
        });
    }

    /** "Arena Duel 3 Archer" -> "3 Archer" (drop the battle-name prefix). */
    function shortName(name) {
        const match = /(\d+ .*)$/.exec(name);
        return match ? match[1] : name;
    }

    /**
     * The rules' letter for a menu key, for the badge a player reads.
     *
     * The engine keys a variant of a lettered option by a token of its own:
     * "sprint" is MOVE (a) at the Sprint gait, and "<key>_yield" is that
     * option with its movement taken in Final Movement (tarmar-engine 0.9.5).
     * The badge shows the option's letter from action-options.md; the name
     * beside it already says "(SPRINT)" or "(YIELD: ...)". The key itself is
     * still what the choice submits. Grapple tokens ("hold_still") print as
     * they are.
     */
    function optionLetter(key) {
        const base = String(key).replace(/_yield$/, '');
        return base === 'sprint' ? 'a' : base;
    }

    /** Terminal statuses: the battle can advance no further (#289). */
    const TERMINAL_STATUSES = ['finished', 'stalemate'];

    /**
     * Status chip text for a server status code. ``labels`` is the model's
     * STATUS_CHOICES, which the page embeds as #battle-status-labels
     * (#338 item 20) rather than this file restating them; a code missing
     * from it still reads, capitalised.
     */
    function statusLabel(status, labels) {
        return (labels && labels[status])
            || status.charAt(0).toUpperCase() + status.slice(1);
    }

    /** Map a battle_state/advance response's seats list by combatant id. */
    function seatsById(seats) {
        const map = {};
        (seats || []).forEach((seat) => { map[seat.combatant_id] = seat; });
        return map;
    }

    // What a rename could not do -- the server refused the name, the
    // network failed, or the figure could no longer be renamed when the
    // roster was redrawn under the edit -- is roster state, not a span hung
    // on a card (issue #16). The roster repaints every couple of seconds, and
    // a note written into a card went with the card; a refusal that arrived
    // after a repaint was written into a card no longer on the page. Each
    // roster keeps its notes by figure, with what was typed, and paints them
    // after every draw: in the figure's card, or above the roster for a
    // figure no longer in it. A note stays until it is dismissed or the
    // figure is renamed again. The span wears autosave.js's class
    // (showFieldError), so the two refusals still look alike.
    //
    // What is NOT shared with autosave.js is the rest of the rename:
    // initAutosave binds fields that are already in the page, keyed by
    // data-field, posting {field, value} to one URL. A rename creates its
    // input by swapping out a heading on a roster that repaints, and posts
    // {combatant_id, name}. Running one through the other would couple two
    // different lifecycles for the sake of a six-line keyboard contract.
    const rosterNotes = new WeakMap();

    /** A roster's notes, by combatant id (registering the roster). */
    function notesOf(roster) {
        let notes = rosterNotes.get(roster);
        if (!notes) {
            notes = new Map();
            rosterNotes.set(roster, notes);
        }
        return notes;
    }

    /** The roster ``element`` is drawn in, or null once it has left it. */
    function rosterOf(element) {
        for (let node = element; node; node = node.parentElement) {
            if (rosterNotes.has(node)) { return node; }
        }
        return null;
    }

    /**
     * Keep a note for one figure and paint the roster's notes again.
     * @param {Element|null} roster
     * @param {number|string} combatantId
     * @param {string} message
     * @param {string} typed - What the person had typed, kept with the note.
     */
    function noteRename(roster, combatantId, message, typed) {
        if (!roster) { return; }
        notesOf(roster).set(String(combatantId), { message, typed, announce: true });
        keepRosterFocus(roster, () => { paintNotes(roster); });
    }

    /** Drop a figure's note: it is being renamed again. */
    function forgetRenameNote(roster, combatantId) {
        if (roster && notesOf(roster).delete(String(combatantId))) {
            keepRosterFocus(roster, () => { paintNotes(roster); });
        }
    }

    /** Give the roster itself the focus, when what had it is gone. */
    function focusRoster(roster) {
        if (!roster.hasAttribute('tabindex')) { roster.setAttribute('tabindex', '-1'); }
        roster.focus({ preventScroll: true });
    }

    /**
     * Run ``redraw`` and put the keyboard focus back where it was in the
     * roster (issue #16): a redraw empties it, and the focus fell to the
     * page on every applied poll. Every control a card draws carries a
     * data-focus-key (its figure's name, a seat hand-off, the attributes
     * summary, a fatal-roll link, a note's Dismiss), the convention of the
     * turn panel (#646) and the GM side panel's tabs, and the focus goes to
     * the control of the same key, or to the roster when it has gone. A
     * redraw that left the focus in the roster (a rename carried through
     * it) is left alone, and focus elsewhere on the page is never taken.
     * @param {Element} roster
     * @param {function(): void} redraw
     */
    function keepRosterFocus(roster, redraw) {
        const active = document.activeElement;
        const held = Boolean(active) && active !== roster && roster.contains(active);
        const key = held ? active.dataset.focusKey || '' : '';
        redraw();
        if (!held) { return; }
        const now = document.activeElement;
        if (now && now !== roster && roster.contains(now)) { return; }
        const target = key ? elementByFocusKey(roster, key) : null;
        if (target) { target.focus({ preventScroll: true }); }
        if (document.activeElement !== target) { focusRoster(roster); }
    }

    /**
     * Draw the roster's notes: each in its figure's card, or in a block
     * above the roster for a figure no longer in it. Read out once, when
     * the note is new, not on every repaint.
     */
    function paintNotes(roster) {
        roster.querySelectorAll('.roster-rename-note, .roster-notes')
            .forEach((node) => { node.remove(); });
        const notes = notesOf(roster);
        let index = 0;
        notes.forEach((note, combatantId) => {
            const place = index;
            index += 1;
            const line = document.createElement('span');
            line.className = 'autosave-error roster-rename-note';
            line.dataset.renameNote = combatantId;
            if (note.announce) { line.setAttribute('role', 'alert'); }
            note.announce = false;
            const words = document.createElement('span');
            words.textContent = note.typed
                ? `${note.message} What you typed: "${note.typed}".`
                : note.message;
            const dismiss = document.createElement('button');
            dismiss.type = 'button';
            dismiss.className = 'battle-button battle-button-outline roster-note-dismiss';
            dismiss.dataset.focusKey = `note-dismiss-${combatantId}`;
            dismiss.textContent = 'Dismiss';
            dismiss.addEventListener('click', () => {
                notes.delete(combatantId);
                paintNotes(roster);
                // Onto the Dismiss that took this one's place, the last one
                // left, or the roster once none are.
                const left = roster.querySelectorAll('.roster-note-dismiss');
                const next = left[Math.min(place, left.length - 1)];
                if (next) {
                    next.focus();
                    return;
                }
                focusRoster(roster);
            });
            line.append(words, ' ', dismiss);
            const card = roster.querySelector(`[data-combatant="${combatantId}"]`);
            if (card) {
                card.appendChild(line);
                return;
            }
            let block = roster.querySelector(':scope > .roster-notes');
            if (!block) {
                block = document.createElement('div');
                block.className = 'roster-notes';
                roster.prepend(block);
            }
            block.appendChild(line);
        });
    }

    /**
     * Swap a roster name for an editable field. Blur/Enter saves through the
     * rename endpoint, Escape cancels; a refused save reverts the name and
     * leaves a note on the roster that stays until dismissed (no modals, no
     * transient UI).
     *
     * ``draft`` is a rename the roster was redrawn under (takeRename): the
     * field opens holding what was typed, the caret where it was. If the
     * figure's name changed meanwhile -- another seat renamed it -- a note
     * under the field says what to, so saving does not replace that name
     * unawares. It goes when the edit ends.
     */
    function startRename(card, title, combatant, options, draft) {
        const roster = rosterOf(card);
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'combatant-rename-input';
        input.value = draft ? draft.value : combatant.name;
        // The name the edit started from, carried through redraws.
        input.dataset.base = draft ? draft.base : combatant.name;
        let cancelled = false;
        title.replaceWith(input);
        let changed = null;
        if (draft && combatant.name !== draft.base) {
            changed = document.createElement('span');
            changed.className = 'rename-changed-note';
            if (!draft.noted) { changed.setAttribute('role', 'status'); }
            changed.textContent = `Renamed meanwhile to "${combatant.name}".`;
            input.after(changed);
        }
        input.focus();
        if (draft) {
            putCaret(input, draft.caret);
        } else {
            input.select();
        }
        const revert = () => {
            if (changed) { changed.remove(); }
            if (input.isConnected) { input.replaceWith(title); }
        };
        bindCommitKeys(input, { onCancel: () => { cancelled = true; } });
        input.addEventListener('blur', () => {
            // Removed by a redraw mid-edit: the browser blurs a focused field
            // it removes, and this used to save the half-typed name. The edit
            // goes on in the redrawn card instead (issue #16).
            if (input.dataset.carried) { return; }
            input.dataset.finished = 'true';
            const value = input.value.trim();
            if (cancelled || !value || value === combatant.name) {
                revert();
                return;
            }
            postForSuccess(options.url, {
                combatant_id: combatant.combatant_id,
                name: value,
            })
            .then(({ succeeded, data }) => {
                if (succeeded) {
                    // The server's state is the single source of truth; the
                    // caller re-renders roster and arena from it.
                    options.onRenamed(data);
                } else {
                    revert();
                    // The roster the card was drawn in, whether or not a
                    // repaint has replaced the card since the save went out.
                    noteRename(
                        roster, combatant.combatant_id,
                        data && data.error ? data.error : 'Rename failed', value
                    );
                }
            })
            .catch(() => {
                revert();
                noteRename(roster, combatant.combatant_id, FIELD_NETWORK_ERROR, value);
            });
        });
    }

    /**
     * One roster card per combatant, dead ones linking their fatal rolls.
     * Reads the server's view model, so a classic figure's single ST bar and
     * a Tarmar combatant's fatigue/body pair render through the same loop.
     * With ``renameOptions`` ({url, csrf, onRenamed}) the names become
     * click-to-edit fields saved through the rename endpoint. ``seatMap``
     * (combatant id -> seat summary) tags player-controlled combatants
     * with who plays them. ``openDetail`` is the Set of combatant ids whose
     * attribute panel the reader has opened (#302) — carried across
     * re-renders, because the poll repaints this list every couple of
     * seconds and a panel that closed itself would be a transient popup by
     * another name. A rename being typed is carried the same way, and the
     * roster's rename notes are painted again (issue #16).
     *
     * The lobby page takes the other way with its poll (lobbyEditing): it
     * holds the poll off while the viewer is mid-edit, because its answer is
     * a reload of the whole document. The roster redraws in place, so the
     * edit is carried through the redraw instead of the redraw waiting.
     */
    function renderRoster(container, view, renameOptions, seatMap, openDetail) {
        notesOf(container);
        keepRosterFocus(container, () => {
            const renaming = takeRename(container);
            drawRoster(container, view, renameOptions, seatMap, openDetail);
            if (renaming) { resumeRename(container, view, renameOptions, renaming); }
            paintNotes(container);
        });
    }

    /**
     * The rename being typed in ``container``, if one is, taken before a
     * redraw (issue #16): which figure, what was typed, the name it started
     * from, and where the caret was. A field whose edit has finished (it
     * blurred, and is saving or reverting) is not taken. The field is marked
     * so its own blur, which the redraw's removal fires, does not save it.
     * @param {Element} container - The roster.
     * @returns {{combatantId: string, value: string, base: string, noted: boolean, caret: object|null}|null}
     */
    function takeRename(container) {
        const input = container.querySelector('.combatant-rename-input:not([data-finished])');
        const card = input && input.closest('[data-combatant]');
        if (!card) { return null; }
        input.dataset.carried = 'true';
        return {
            combatantId: card.dataset.combatant,
            value: input.value,
            base: input.dataset.base,
            noted: Boolean(card.querySelector('.rename-changed-note')),
            caret: readCaret(input),
        };
    }

    /**
     * Open the taken rename again on the redrawn card of the same figure. A
     * figure this viewer may no longer rename, or one gone from the fight,
     * gets a note with what was typed rather than having it dropped.
     */
    function resumeRename(container, view, renameOptions, renaming) {
        const card = container.querySelector(`[data-combatant="${renaming.combatantId}"]`);
        const title = card && card.querySelector('.combatant-name');
        const combatant = (view.combatants || []).find(
            (each) => String(each.combatant_id) === renaming.combatantId
        );
        if (title && combatant && renameOptions) {
            startRename(card, title, combatant, renameOptions, renaming);
            return;
        }
        const message = card
            ? 'Not renamed: this figure can no longer be renamed here.'
            : `Not renamed: ${renaming.base} is no longer in this fight.`;
        notesOf(container).set(
            renaming.combatantId, { message, typed: renaming.value, announce: true }
        );
    }

    /** Draw the roster's cards into ``container`` (see renderRoster). */
    function drawRoster(container, view, renameOptions, seatMap, openDetail) {
        container.textContent = '';
        const teamOrder = teamOrderOf(view);
        if (!teamOrder.length) {
            // Free-for-all: the flat roster, exactly as before teams.
            (view.combatants || []).forEach((combatant) => {
                container.appendChild(
                    combatantCard(combatant, renameOptions, seatMap, openDetail)
                );
            });
            return;
        }
        // Teamed battle (#275): one group per team in appearance order,
        // headed and tinted, then whoever fights alone under their own head.
        const grouped = teamOrder.map((team) => ({
            team,
            tint: teamTint(teamOrder, team),
            combatants: (view.combatants || []).filter(
                (combatant) => combatant.team === team
            ),
        }));
        const free = (view.combatants || []).filter(
            (combatant) => !combatant.team
        );
        if (free.length) {
            grouped.push({ team: 'Free-for-all', tint: null, combatants: free });
        }
        grouped.forEach((group) => {
            const head = document.createElement('div');
            head.className = 'roster-team-head';
            if (group.tint) {
                const swatch = document.createElement('span');
                swatch.className = 'roster-team-swatch';
                swatch.style.backgroundColor = group.tint;
                head.appendChild(swatch);
            }
            head.appendChild(document.createTextNode(group.team));
            container.appendChild(head);
            group.combatants.forEach((combatant) => {
                container.appendChild(
                    combatantCard(combatant, renameOptions, seatMap, openDetail)
                );
            });
        });
    }

    /**
     * The attribute panel inside a roster card (#302).
     *
     * Everything in it was computed and formatted server-side (see
     * battle/viewmodel.py) — this walks groups and rows and knows nothing
     * about either rules profile's vocabulary. A <details>, not a popup:
     * it opens on the reader's click and stays open until they close it,
     * which is why the open ids live in ``openDetail`` rather than in the
     * element the next poll throws away.
     */
    function detailPanel(combatant, openDetail) {
        const panel = document.createElement('details');
        panel.className = 'combatant-detail';
        panel.open = Boolean(openDetail && openDetail.has(combatant.combatant_id));
        const summary = document.createElement('summary');
        summary.textContent = 'Full attributes';
        summary.dataset.focusKey = `detail-${combatant.combatant_id}`;
        panel.appendChild(summary);
        (combatant.detail.groups || []).forEach((group) => {
            const section = document.createElement('section');
            section.className = 'detail-group';
            const heading = document.createElement('h4');
            heading.textContent = group.label;
            section.appendChild(heading);
            const rows = document.createElement('dl');
            rows.className = 'detail-rows';
            (group.rows || []).forEach((row) => {
                const term = document.createElement('dt');
                term.textContent = row.label;
                const value = document.createElement('dd');
                const figure = document.createElement('span');
                figure.className = 'detail-value';
                figure.textContent = row.value;
                value.appendChild(figure);
                if (row.note) {
                    const note = document.createElement('small');
                    note.className = 'detail-note';
                    note.textContent = row.note;
                    value.appendChild(note);
                }
                rows.appendChild(term);
                rows.appendChild(value);
            });
            section.appendChild(rows);
            panel.appendChild(section);
        });
        if (openDetail) {
            panel.addEventListener('toggle', () => {
                if (panel.open) {
                    openDetail.add(combatant.combatant_id);
                } else {
                    openDetail.delete(combatant.combatant_id);
                }
            });
        }
        return panel;
    }

    /** One roster card (see renderRoster, which owns the grouping). */
    function combatantCard(combatant, renameOptions, seatMap, openDetail) {
        const card = document.createElement('div');
        card.className = 'combatant-card'
            + (combatant.alive && combatant.conscious ? '' : ' is-down');
        card.dataset.combatant = combatant.combatant_id;

        const title = document.createElement('strong');
        title.textContent = shortName(combatant.name);
        title.dataset.focusKey = `name-${combatant.combatant_id}`;
        // Only the figures the server says this viewer may rename (#762): in
        // a Games Master session's fight the foes are not the player's.
        const seatFacts = (seatMap || {})[combatant.combatant_id];
        if (renameOptions && !(seatFacts && seatFacts.can_rename === false)) {
            title.classList.add('combatant-name');
            title.title = 'Click to rename';
            title.setAttribute('role', 'button');
            title.tabIndex = 0;
            const beginEdit = () => {
                forgetRenameNote(rosterOf(card), combatant.combatant_id);
                startRename(card, title, combatant, renameOptions);
            };
            title.addEventListener('click', beginEdit);
            title.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    beginEdit();
                }
            });
        }
        card.appendChild(title);

        // The line the server wrote: each profile says what a standing
        // combatant *is* in its own vocabulary, and both say Dead and
        // Unconscious the same way.
        const status = document.createElement('div');
        status.className = 'combatant-status';
        status.textContent = combatant.status;
        card.appendChild(status);

        // Who decides for this figure (#308). The sentence is the
        // server's (battle.control.mode_line) — the browser does not
        // re-derive the battle-default-versus-override precedence, it
        // shows the answer. "Played by you" survives as the friendlier
        // wording for your own seat.
        const seat = (seatMap || {})[combatant.combatant_id];
        if (seat && (seat.control_line || seat.player)) {
            const controller = document.createElement('div');
            controller.className = 'combatant-controller';
            controller.dataset.mode = seat.effective_control_mode || '';
            if (!seat.control_line) {
                // A payload from before control modes: say what it knows.
                controller.textContent = seat.is_you
                    ? 'Played by you'
                    : `Played by ${seat.player}`;
            } else if (seat.you_decide
                    && seat.effective_control_mode === 'manual') {
                // "You" only where being asked is actually true: whoever
                // runs the battle *would* decide for an automatic figure
                // if it went manual, which is not the same as playing it.
                // The server's words for it (issue #136); a payload from
                // before them shows the line it carries.
                controller.textContent = seat.own_control_line || seat.control_line;
            } else {
                controller.textContent = seat.control_line;
            }
            card.appendChild(controller);
        }
        // Who plays a saved character (#853): its owner, unless a Game
        // Master takes an absent owner's seat. The server says which of the
        // two hand-offs this viewer may make; initBattlePage posts them.
        if (seat) {
            [
                ['take_seat_url', 'Take this seat (owner absent)', 'take'],
                ['return_seat_url', 'Give the seat back to its owner', 'return'],
            ].forEach(([key, label, kind]) => {
                if (!seat[key]) { return; }
                const handOff = document.createElement('button');
                handOff.type = 'button';
                handOff.className = 'battle-button battle-button-outline seat-hand-off';
                handOff.dataset.seatUrl = seat[key];
                handOff.dataset.seatHandOff = kind;
                handOff.dataset.focusKey = `hand-off-${kind}-${combatant.combatant_id}`;
                handOff.textContent = label;
                card.appendChild(handOff);
            });
        }

        (combatant.pools || []).forEach((pool) => {
            const label = document.createElement('div');
            label.className = 'pool-label';
            label.textContent = `${pool.label} ${pool.current}/${pool.maximum}`;
            card.appendChild(label);
            const bar = document.createElement('div');
            bar.className = `pool-bar ${pool.key}`;
            const fill = document.createElement('span');
            fill.style.width = `${poolFraction(pool.current, pool.maximum) * 100}%`;
            bar.appendChild(fill);
            card.appendChild(bar);
        });

        // A spell running away from this figure (tarmar-engine #30): the
        // server's sentence, from the engine's state and the canon's rules
        // (battle.viewmodel._tarmar_runaway). It stays until the state says
        // the Runaway is over.
        if (combatant.runaway) {
            const runaway = document.createElement('div');
            runaway.className = 'combatant-runaway';
            runaway.dataset.spell = combatant.runaway.spell_key;
            runaway.textContent = combatant.runaway.line;
            card.appendChild(runaway);
        }

        // A spell of this figure's that stands countered (tarmar-engine
        // #31): the server's sentence, from the engine's state
        // (battle.viewmodel._tarmar_contest). It stays until the state says
        // the contest is over.
        if (combatant.contest) {
            const contest = document.createElement('div');
            contest.className = 'combatant-contest';
            contest.dataset.spell = combatant.contest.spell_key;
            contest.textContent = combatant.contest.line;
            card.appendChild(contest);
        }

        if (!combatant.alive && combatant.fatal_chain
                && combatant.fatal_chain.length) {
            const chain = document.createElement('div');
            chain.className = 'fatal-chain';
            chain.appendChild(document.createTextNode('Fatal rolls: '));
            combatant.fatal_chain.forEach((sequence, index) => {
                if (index) { chain.appendChild(document.createTextNode(', ')); }
                const link = document.createElement('a');
                link.href = `#event-${sequence}`;
                link.dataset.focusKey = `fatal-${combatant.combatant_id}-${sequence}`;
                link.textContent = `#${sequence}`;
                chain.appendChild(link);
            });
            card.appendChild(chain);
        }
        // Last in the card: the condition gauges stay the thing you read at
        // a glance, and the sheet is what you open when you need it.
        if (combatant.detail) {
            card.appendChild(detailPanel(combatant, openDetail));
        }
        return card;
    }

    /**
     * The AI's favorite among a pending menu.
     *
     * The server marks it (``ai_choice``, #775): a player's menu also holds
     * each attack aimed at every other target, and one of those may score
     * above the AI's own pick. A menu stored before the mark falls back to
     * the highest score, first wins ties — the pick max() made server-side.
     */
    function topCandidateIndex(candidates) {
        const marked = (candidates || []).findIndex((candidate) => candidate.ai_choice);
        if (marked !== -1) { return marked; }
        let best = 0;
        (candidates || []).forEach((candidate, index) => {
            if (candidate.score > candidates[best].score) { best = index; }
        });
        return best;
    }

    /**
     * The cast candidates on a menu (one per castable spell), their pushed
     * copies, and the rest. Casting collapses into a single CAST SPELL
     * option so a wizard's spells do not flood the action list — the spells
     * live in the cast stage the option opens.
     *
     * A pushed copy (#960) is the same cast with ``push_mana`` more mana in
     * it: the engine offers one per amount it allows. It is not a spell of
     * its own, so it does not count as a cast; ``pushes`` holds the copies
     * under their cast's key (:func:`castKey`), smallest push first, for the
     * stage's push control. The amounts are the menu's, never the page's.
     *
     * A borrowing copy (tarmar-engine #28) carries ``borrow``: the same cast,
     * plain or pushed, paid partly with mana the caster lacks. ``borrows``
     * holds those under their cast's key the same way, for the borrowing
     * control; a spell the caster cannot afford has borrowing copies and no
     * plain cast at all.
     *
     * A counter (tarmar-engine #31) carries ``counter_mana``: one per
     * commitment the engine allows against each enemy caster. ``counters``
     * holds them under the caster's id (``target_id``), least first, for
     * that caster's counter control; the amounts are the menu's.
     */
    function splitCastCandidates(candidates) {
        const plain = [];
        const casts = [];
        const pushes = new Map();
        const borrows = new Map();
        const counters = new Map();
        (candidates || []).forEach((candidate, index) => {
            if (candidate.counter_mana != null) {
                const caster = String(candidate.target_id);
                if (!counters.has(caster)) { counters.set(caster, []); }
                counters.get(caster).push({ candidate, index });
            } else if (!candidate.spell_key) {
                plain.push({ candidate, index });
            } else if (candidate.borrow || candidate.push_mana) {
                const copies = candidate.borrow ? borrows : pushes;
                const key = castKey(candidate.spell_key, candidate.target_id);
                if (!copies.has(key)) { copies.set(key, []); }
                copies.get(key).push({ candidate, index });
            } else {
                casts.push({ candidate, index });
            }
        });
        [pushes, borrows].forEach((byCast) => byCast.forEach((copies) => {
            copies.sort((first, second) => (first.candidate.push_mana || 0)
                - (second.candidate.push_mana || 0));
        }));
        counters.forEach((copies) => {
            copies.sort((first, second) => first.candidate.counter_mana
                - second.candidate.counter_mana);
        });
        return { plain, casts, pushes, borrows, counters };
    }

    /**
     * Render the turn panel from the pending decision (state-driven: the
     * whole panel re-renders from data on every change, and a reload shows
     * exactly the same thing from the server-embedded copy).
     *
     * Three shapes: hidden (nothing pending), the awaited player's action
     * menu (candidates with the policy's scores as advice; click to select,
     * confirm to send), or a waiting notice naming who the battle waits on
     * (with the DM's let-the-AI-choose escape hatch).
     *
     * The cast flow (#250, melee's gate ported to this command bar): every
     * cast candidate collapses into one CAST SPELL option, and declaring it
     * opens a spell stage in place — never a modal, nothing transient. The
     * stage lists the caster's whole hand from ``pending.magic``, the
     * unavailable ones disabled with the server's reason, and NOTHING is
     * pre-selected: a declared cast owes an explicit spell pick before the
     * confirm ungates, or an explicit stand-down back to the plain menu.
     * The AI's favourite is tagged as advice, never as a selection.
     *
     * ``options``: { isDm, selectedIndex, castOpen, showScoring, busy,
     * onSelect(index), onCastOpen(), onCastClose(), onToggleScoring(),
     * onConfirm(candidate), onAiChoose() }.
     */
    /**
     * The empty pool a stage falls back to when the server published no
     * magic for the awaited caster — a wizard's hand is never guessed at.
     */
    function emptyMagic() {
        return { pool_label: 'Mana', pool: 0, max_pool: 0, spells: [] };
    }

    /**
     * The key that pairs a published spell row with its menu candidate:
     * the spell AND the figure it is aimed at (#263). The spell alone is
     * not enough — the classic profile offers one cast per (spell, target)
     * pair, so keying by spell would collapse a wizard's targets to
     * whichever came last and cast at the wrong figure.
     */
    function castKey(spellKey, targetId) {
        const target = (targetId === null || targetId === undefined)
            ? '' : String(targetId);
        return `${spellKey}\u0000${target}`;
    }

    /**
     * Which control inside the turn panel keyboard focus should land on
     * after the panel is rebuilt (#646).
     *
     * Every selection empties the panel and builds every button again, so
     * the button the person just activated leaves the document and focus
     * falls back to <body> — putting a keyboard user back at the top of a
     * regenerated menu on every keystroke that changes anything. Each
     * control carries a ``data-focus-key`` that survives the rebuild, and
     * the panel remembers the last one, so an action in flight (which
     * disables the confirm button and therefore drops focus) still lands
     * the person back where they were once it answers.
     *
     * Focus elsewhere on the page is left alone: this only claims it back
     * when it was in the panel, or when nothing holds it at all.
     *
     * @param {HTMLElement} panel
     * @returns {string} The key to restore, or '' to leave focus alone.
     */
    function focusKeyToRestore(panel) {
        const active = document.activeElement;
        if (active && panel.contains(active) && active.dataset.focusKey) {
            return active.dataset.focusKey;
        }
        if (!active || active === document.body) {
            return panel.dataset.lastFocusKey || '';
        }
        return '';
    }

    /**
     * Focus the rebuilt control that ``key`` names, or the nearest thing
     * that still makes sense. A spell row and the stand-down button both
     * disappear when the cast stage closes; their reader belongs on the
     * CAST SPELL option that opened it, not at the top of the menu.
     *
     * @param {HTMLElement} panel
     * @param {string} key
     */
    function restoreTurnPanelFocus(panel, key) {
        if (!key) { return; }
        panel.dataset.lastFocusKey = key;
        const byKey = (wanted) => elementByFocusKey(panel, wanted);
        let target = byKey(key);
        if ((!target || target.disabled)
                && (key.startsWith('spell-') || key.startsWith('push-')
                    || key.startsWith('borrow-') || key === 'cast-standdown')) {
            target = byKey('cast');
        }
        if (target && !target.disabled) { target.focus(); }
    }

    /**
     * One row of pressable choices, each selecting one menu candidate: the
     * shape the push control (#960), the borrowing control (tarmar-engine
     * #28) and the counter control (tarmar-engine #31) share. Every word is the caller's from the server's
     * data; the selected copy's cost is the server's own line.
     *
     * @param {Object<string, string>} words - the panel's words, the page's
     *     #battle-turn-panel-words (#94)
     * @param {string} kind - ``push``, ``borrow`` or ``counter``: the class
     *     and focus-key
     *     prefix (``turn-push``, ``turn-push-amount``, ``turn-push-cost``)
     * @param {string} labelText - the words before the choices
     * @param {string} ariaLabel - the group's accessible name
     * @param {Array<{entry: {candidate: object, index: number}, text: string}>}
     *     choices - each button's candidate and words, in order
     * @param {Array<{candidate: object, index: number}>} copies - the copies
     *     whose cost the control prints when one is selected
     * @param {number} aiPick - the AI's pick on the menu
     * @param {object} opts - the panel's options (``selectedIndex``,
     *     ``onSelect``)
     */
    function buildCopyControl(words, kind, labelText, ariaLabel, choices, copies, aiPick, opts) {
        const control = document.createElement('div');
        control.className = `turn-${kind}`;
        control.setAttribute('role', 'group');
        control.setAttribute('aria-label', ariaLabel);

        const label = document.createElement('span');
        label.className = `turn-${kind}-label`;
        label.textContent = labelText;
        control.appendChild(label);

        choices.forEach((choice) => {
            const candidate = choice.entry.candidate;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `turn-${kind}-amount`;
            button.dataset.focusKey = `${kind}-${choice.entry.index}`;
            if (candidate.counter_mana != null) {
                button.dataset.counterMana = String(candidate.counter_mana);
            } else {
                button.dataset.pushMana = String(candidate.push_mana || 0);
            }
            if (candidate.borrow) {
                button.dataset.borrowed = String(candidate.borrowed || 0);
            }
            button.setAttribute(
                'aria-pressed',
                choice.entry.index === opts.selectedIndex ? 'true' : 'false'
            );
            button.textContent = choice.text;
            if (choice.entry.index === aiPick) {
                const tag = document.createElement('span');
                tag.className = 'turn-option-ai-pick';
                tag.textContent = filledWords(words.ai_pick);
                button.appendChild(tag);
            }
            button.addEventListener('click', () => {
                if (opts.onSelect && choice.entry.index !== opts.selectedIndex) {
                    opts.onSelect(choice.entry.index);
                }
            });
            control.appendChild(button);
        });

        const selected = copies.find((copy) => copy.index === opts.selectedIndex);
        if (selected && selected.candidate.cost) {
            const cost = document.createElement('span');
            cost.className = `turn-${kind}-cost`;
            cost.textContent = filledWords(words.costs, { cost: selected.candidate.cost });
            control.appendChild(cost);
        }
        return control;
    }

    /**
     * The push control beside a cast the engine offers pushed copies of
     * (#960): no push, or one of the amounts the menu holds, each a
     * pressable button that selects that copy. Nothing is pushed unless the
     * player picks an amount. The words under it are the server's: the
     * selected copy's cost, and the spell's Channel when the rules give one.
     *
     * @param {Object<string, string>} words - the panel's words (#94)
     * @param {object} spell - the stage row (``name``, ``channel``)
     * @param {{candidate: object, index: number}} entry - the plain cast
     * @param {Array<{candidate: object, index: number}>} copies - its pushed
     *     copies, smallest first
     * @param {number} aiPick - the AI's pick on the menu
     * @param {object} opts - the panel's options (``selectedIndex``,
     *     ``onSelect``)
     */
    function buildPushControl(words, spell, entry, copies, aiPick, opts) {
        const label = (spell.channel === null || spell.channel === undefined)
            ? filledWords(words.push_label)
            : filledWords(words.push_label_channel, { channel: spell.channel });
        const choices = [{ entry, text: filledWords(words.push_none) }].concat(copies.map((copy) => ({
            entry: copy, text: filledWords(words.push_amount, { mana: copy.candidate.push_mana }),
        })));
        return buildCopyControl(
            words, 'push', label, filledWords(words.push_group, { spell: spell.name }),
            choices, copies, aiPick, opts
        );
    }

    /**
     * The borrowing control beside a cast the engine offers borrowing copies
     * of (tarmar-engine #28): one button per copy the menu holds, naming the
     * mana it borrows (the server's ``borrowed``) and, for a pushed copy, the
     * mana it pushes. Nothing is borrowed unless the player picks one; the
     * selected copy's cost, debt roll and all, is the server's line.
     *
     * @param {Object<string, string>} words - the panel's words (#94)
     * @param {object} spell - the stage row (``name``)
     * @param {Array<{candidate: object, index: number}>} copies - its
     *     borrowing copies, smallest push first
     * @param {number} aiPick - the AI's pick on the menu
     * @param {object} opts - the panel's options (``selectedIndex``,
     *     ``onSelect``)
     */
    function buildBorrowControl(words, spell, copies, aiPick, opts) {
        const choices = copies.map((copy) => ({
            entry: copy,
            text: copy.candidate.push_mana
                ? filledWords(words.borrow_pushed, {
                    pushed: copy.candidate.push_mana, borrowed: copy.candidate.borrowed,
                })
                : filledWords(words.borrow_amount, { borrowed: copy.candidate.borrowed }),
        }));
        return buildCopyControl(
            words, 'borrow', filledWords(words.borrow_label),
            filledWords(words.borrow_group, { spell: spell.name }),
            choices, copies, aiPick, opts
        );
    }

    /**
     * The counter control for one enemy caster (tarmar-engine #31): one
     * button per commitment the menu holds against that caster, least
     * first. Nothing is countered unless the player picks an amount; the
     * selected commitment's cost is the server's line, and the bonus it buys
     * is in the engine's rationale behind the scoring switch.
     *
     * @param {Object<string, string>} words - the panel's words (#94)
     * @param {Array<{candidate: object, index: number}>} copies - the
     *     counters against one caster, least commitment first
     * @param {number} aiPick - the AI's pick on the menu
     * @param {object} opts - the panel's options (``selectedIndex``,
     *     ``onSelect``)
     */
    function buildCounterControl(words, copies, aiPick, opts) {
        const caster = copies[0].candidate.target_name || filledWords(words.the_caster);
        const choices = copies.map((copy) => ({
            entry: copy,
            text: filledWords(words.counter_amount, { mana: copy.candidate.counter_mana }),
        }));
        const control = buildCopyControl(
            words, 'counter', filledWords(words.counter_label, { caster }),
            filledWords(words.counter_group, { caster }),
            choices, copies, aiPick, opts
        );
        control.dataset.caster = String(copies[0].candidate.target_id);
        return control;
    }

    /**
     * The caster's pool in the panel's words: "Mana 3 of 10", and what is
     * owed when ``owing`` and the caster owes any (tarmar-engine #28).
     * @param {Object<string, string>} words - the panel's words (#94)
     * @param {object} magic - the pending payload's ``magic``
     * @param {{owing: boolean}} shown - whether to name a debt
     * @returns {string}
     */
    function poolWords(words, magic, shown) {
        const figures = {
            pool_label: magic.pool_label, pool: magic.pool, max_pool: magic.max_pool,
        };
        if (shown.owing && magic.owed) {
            return filledWords(words.pool_owing, { ...figures, owed: magic.owed });
        }
        return filledWords(words.pool, figures);
    }

    /**
     * The spell stage a declared cast opens: the caster's pool, then their
     * whole known hand. A spell the server marked uncastable renders
     * disabled with its reason, so a wizard sees why Lightning Bolt is out
     * of reach instead of wondering where it went. The stand-down button
     * ends the declaration without spending the turn.
     *
     * Every word of a row comes from the server (#263): its name, what the
     * cast costs, and what it needs to land. The page knows only that a
     * caster has a named pool and some rows — which is what lets one stage
     * serve Tarmar's mana and classic's ST without printing either
     * profile's numbers under the other's label.
     */
    function buildCastStage(pending, casts, pushes, borrows, aiPick, opts) {
        const words = opts.words;
        const stage = document.createElement('div');
        stage.className = 'turn-cast-stage';
        stage.setAttribute('role', 'group');
        stage.setAttribute('aria-label', filledWords(words.spells_group));

        const magic = pending.magic || emptyMagic();
        const pool = document.createElement('p');
        pool.className = 'turn-cast-pool';
        // Mana borrowed and not paid back (tarmar-engine #28), as published.
        pool.textContent = filledWords(words.stage_pool, {
            pool: poolWords(words, magic, { owing: true }),
        });
        stage.appendChild(pool);

        const castByKey = new Map(casts.map((entry) => [
            castKey(entry.candidate.spell_key, entry.candidate.target_id),
            entry,
        ]));
        (magic.spells || []).forEach((spell) => {
            const key = castKey(spell.key, spell.target_id);
            const entry = castByKey.get(key);
            const copies = (entry && pushes.get(key)) || [];
            // A spell the caster cannot afford has no plain cast, only
            // borrowing copies (tarmar-engine #28).
            const borrowing = borrows.get(key) || [];
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'turn-spell';
            // Keyed by the candidate it stands for, so the key survives a
            // rebuild. A row with no candidate is disabled and can never
            // hold focus, so it needs none.
            if (entry) { button.dataset.focusKey = `spell-${entry.index}`; }
            button.disabled = !spell.castable || !entry;
            // A pushed or borrowing copy selected is this spell selected too.
            const selected = (Boolean(entry) && entry.index === opts.selectedIndex)
                || copies.concat(borrowing)
                    .some((copy) => copy.index === opts.selectedIndex);
            button.setAttribute('aria-pressed', selected ? 'true' : 'false');

            const name = document.createElement('span');
            name.className = 'turn-spell-name';
            name.textContent = spell.name;
            button.appendChild(name);

            const cost = document.createElement('span');
            cost.className = 'turn-spell-cost';
            cost.textContent = spell.cost;
            button.appendChild(cost);

            if (entry && entry.index === aiPick) {
                const tag = document.createElement('span');
                tag.className = 'turn-option-ai-pick';
                tag.textContent = filledWords(words.ai_pick);
                button.appendChild(tag);
            }

            // A cast pressing a countered spell again (tarmar-engine #31):
            // the engine marks every copy of it, plain, pushed or borrowing,
            // and the server's line says so. Read from whichever the menu
            // holds, the plain cast first, so a spell only borrowing pays
            // for is marked too.
            const pressing = [entry].concat(copies, borrowing)
                .find((copy) => copy && copy.candidate.escalate);
            if (pressing) {
                button.classList.add('is-escalation');
                const escalation = document.createElement('span');
                escalation.className = 'turn-spell-escalate';
                escalation.textContent = pressing.candidate.plain
                    || filledWords(words.presses_again);
                button.appendChild(escalation);
            }

            const note = document.createElement('span');
            note.className = 'turn-spell-note';
            if (button.disabled) {
                note.textContent = spell.reason || filledWords(words.not_castable);
                note.classList.add('is-blocked');
            } else {
                note.textContent = spell.note;
            }
            button.appendChild(note);

            if (entry) {
                button.addEventListener('click', () => {
                    if (opts.onSelect) { opts.onSelect(entry.index); }
                });
            }
            const pushable = copies.length && !button.disabled;
            if (!pushable && !borrowing.length) {
                stage.appendChild(button);
                return;
            }
            // The cast and its controls travel together in the grid.
            const group = document.createElement('div');
            group.className = 'turn-spell-group';
            group.appendChild(button);
            if (pushable) {
                group.appendChild(buildPushControl(
                    words, spell, entry, copies, aiPick, opts
                ));
            }
            if (borrowing.length) {
                group.appendChild(buildBorrowControl(
                    words, spell, borrowing, aiPick, opts
                ));
            }
            stage.appendChild(group);
        });

        const standDown = document.createElement('button');
        standDown.type = 'button';
        standDown.className = 'battle-button battle-button-quiet turn-cast-standdown';
        standDown.dataset.focusKey = 'cast-standdown';
        standDown.textContent = filledWords(words.stand_down);
        standDown.addEventListener('click', () => {
            if (opts.onCastClose) { opts.onCastClose(); }
        });
        stage.appendChild(standDown);
        return stage;
    }

    /**
     * What the selected option does with a hex (#819, #866): pick one on
     * the board, or leave it and the option makes its own move. The words
     * and which hexes are legal are both the server's (#94).
     */
    function buildDestinationNote(selected, opts) {
        const words = opts.words;
        const note = document.createElement('div');
        note.className = 'turn-destination';
        const line = document.createElement('p');
        line.className = 'turn-destination-line';
        if (opts.destination) {
            line.textContent = filledWords(words.moving_to, { hex: hexLabel(opts.destination) });
        } else {
            line.textContent = filledWords(words.pick_hex, {
                count: selected.destinations.length,
            });
        }
        note.appendChild(line);
        if (opts.destination) {
            const clear = document.createElement('button');
            clear.type = 'button';
            clear.className = 'battle-button battle-button-quiet turn-destination-clear';
            clear.dataset.focusKey = 'destination-clear';
            clear.textContent = filledWords(words.leave_hex);
            clear.addEventListener('click', () => {
                if (opts.onClearDestination) { opts.onClearDestination(); }
            });
            note.appendChild(clear);
        }
        return note;
    }

    /**
     * The engine's refusals of a hex this viewer picked (#819, #866), as
     * notices: the log keeps every refusal on the page for good, and the
     * notice stays until dismissed, so the person who picked the hex sees
     * why the figure did not go there without reading the whole turn.
     *
     * @param {Array} events - the events a response carried
     * @param {Array} seats - the seat summaries, ``you_decide`` marking the
     *     figures this viewer chooses for
     * @returns {string[]} one message per refused hex of this viewer's figures
     */
    function refusedMoveNotices(events, seats) {
        const mine = new Set(
            (seats || []).filter((seat) => seat.you_decide)
                .map((seat) => seat.display_name)
        );
        return (events || [])
            .filter((event) => event.payload && event.payload.destination_refused
                && mine.has(event.actor))
            .map((event) => event.message);
    }

    function renderTurnPanel(panel, pending, options) {
        const focusKey = focusKeyToRestore(panel);
        panel.textContent = '';
        if (!pending) {
            panel.hidden = true;
            delete panel.dataset.lastFocusKey;
            return;
        }
        panel.hidden = false;
        const opts = options || {};
        // The panel's own words (#94): #battle-turn-panel-words, the server's.
        const words = opts.words;

        if (!pending.is_you) {
            const notice = document.createElement('p');
            notice.className = 'turn-waiting';
            const who = document.createElement('strong');
            who.textContent = pending.display_name;
            if (pending.player) {
                appendFilledWords(notice, words.waiting_played, {
                    name: who, player: pending.player,
                });
            } else {
                appendFilledWords(notice, words.waiting, { name: who });
            }
            panel.appendChild(notice);
            if (opts.isDm) {
                const aiButton = document.createElement('button');
                aiButton.type = 'button';
                aiButton.className = 'battle-button battle-button-outline turn-ai-choose';
                aiButton.dataset.focusKey = 'ai-choose';
                aiButton.textContent = filledWords(words.ai_choose);
                aiButton.disabled = Boolean(opts.busy);
                aiButton.addEventListener('click', () => {
                    if (opts.onAiChoose) { opts.onAiChoose(); }
                });
                panel.appendChild(aiButton);
            }
            restoreTurnPanelFocus(panel, focusKey);
            return;
        }

        const heading = document.createElement('p');
        heading.className = 'turn-prompt';
        const name = document.createElement('strong');
        name.textContent = pending.display_name;
        appendFilledWords(heading, words.your_move, { name, turn: pending.turn });
        panel.appendChild(heading);

        const menu = document.createElement('div');
        menu.className = 'turn-options';
        menu.setAttribute('role', 'group');
        menu.setAttribute('aria-label', filledWords(words.options_group));
        const aiPick = topCandidateIndex(pending.candidates);
        const {
            plain, casts, pushes, borrows, counters,
        } = splitCastCandidates(pending.candidates);
        // Every cast on the menu that only borrowing pays for.
        const borrowingCopies = Array.from(borrows.values()).flat();
        const castable = casts.length || borrowingCopies.length;

        /**
         * One menu row: letter, rules name, an optional AI-pick tag, then
         * what a player weighs (#788) — the plain line and what it costs,
         * both the server's words — and the AI's scoring when the reader
         * has asked to see it. A row the server wrote no plain line for (a
         * classic figure's) keeps the scoring as its line, as it always had.
         */
        function buildOption(letter, name, advice, pressed, focusKeyOfOption, described) {
            const option = document.createElement('button');
            option.type = 'button';
            option.className = 'turn-option';
            option.dataset.focusKey = focusKeyOfOption;
            option.setAttribute('aria-pressed', pressed ? 'true' : 'false');
            const letterNode = document.createElement('span');
            letterNode.className = 'turn-option-letter';
            letterNode.textContent = optionLetter(letter);
            option.appendChild(letterNode);
            const label = document.createElement('span');
            label.className = 'turn-option-name';
            label.textContent = name;
            option.appendChild(label);
            const plainLine = described && described.plain;
            if (plainLine) {
                const plainNode = document.createElement('span');
                plainNode.className = 'turn-option-plain';
                plainNode.textContent = plainLine;
                option.appendChild(plainNode);
                const costNode = document.createElement('span');
                costNode.className = 'turn-option-cost';
                costNode.textContent = described.cost
                    ? filledWords(words.costs, { cost: described.cost })
                    : filledWords(words.costs_nothing);
                option.appendChild(costNode);
            }
            if (!plainLine || opts.showScoring) {
                const adviceNode = document.createElement('span');
                adviceNode.className = 'turn-option-advice';
                adviceNode.textContent = advice;
                option.appendChild(adviceNode);
            }
            return option;
        }

        /** The 'AI pick' tag, placed right after the option's name. */
        function tagAiPick(option) {
            const tag = document.createElement('span');
            tag.className = 'turn-option-ai-pick';
            tag.textContent = filledWords(words.ai_pick);
            option.insertBefore(tag, option.querySelector('.turn-option-name').nextSibling);
        }

        plain.forEach(({ candidate, index }) => {
            const option = buildOption(
                candidate.letter,
                candidate.name,
                filledWords(words.advice, {
                    score: scoreText(candidate), rationale: candidate.rationale,
                }),
                index === opts.selectedIndex,
                `option-${index}`,
                candidate
            );
            if (index === aiPick) { tagAiPick(option); }
            // An anchor (tarmar-engine #30) is a plain row like any other:
            // the server wrote its line and cost from the Runaway it names.
            if (candidate.anchor_target != null) {
                option.classList.add('turn-option-anchor');
            }
            option.addEventListener('click', () => {
                if (opts.onSelect) { opts.onSelect(index); }
            });
            menu.appendChild(option);
        });

        // A counter control per enemy caster (tarmar-engine #31), with the
        // commitments the menu offers against it.
        counters.forEach((copies) => {
            menu.appendChild(buildCounterControl(words, copies, aiPick, opts));
        });

        if (castable) {
            const magic = pending.magic || emptyMagic();
            // Casts, not spells: the classic profile offers the same spell
            // at each figure it could be aimed at, and the stage lists one
            // row per offer, so counting spells would undercount the stage.
            // A cast only borrowing pays for, one with no plain cast on the
            // menu, is counted apart (#28); an affordable cast whose pushed
            // copies borrow is already one of the casts ready.
            const castKeys = new Set(casts.map(({ candidate }) => castKey(
                candidate.spell_key, candidate.target_id
            )));
            const borrowingOnly = Array.from(borrows.keys())
                .filter((key) => !castKeys.has(key)).length;
            const casting = casts.length === 1
                ? filledWords(words.casts_ready_one)
                : filledWords(words.casts_ready, { count: casts.length });
            const ready = borrowingOnly
                ? filledWords(words.ready_borrowing, { ready: casting, count: borrowingOnly })
                : casting;
            const castOption = buildOption(
                (casts[0] || borrowingCopies[0]).candidate.letter,
                filledWords(words.cast_option),
                filledWords(words.cast_advice, {
                    pool: poolWords(words, magic, { owing: false }), ready,
                }),
                Boolean(opts.castOpen),
                'cast'
            );
            castOption.classList.add('turn-option-cast');
            const pushedCopies = Array.from(pushes.values()).flat();
            if (casts.concat(pushedCopies, borrowingCopies)
                .some(({ index }) => index === aiPick)) {
                tagAiPick(castOption);
            }
            castOption.addEventListener('click', () => {
                if (opts.onCastOpen) { opts.onCastOpen(); }
            });
            menu.appendChild(castOption);
        }
        panel.appendChild(menu);

        // The AI's scoring behind a switch the reader holds (#788): every
        // number is still on the page for anyone who wants the working, but
        // no longer the first thing a player reads. It stays as set, across
        // repaints and reloads, until the reader changes it.
        if (plain.some(({ candidate }) => candidate.plain)) {
            const scoring = document.createElement('button');
            scoring.type = 'button';
            scoring.className = 'battle-button battle-button-quiet turn-scoring-toggle';
            scoring.dataset.focusKey = 'scoring';
            scoring.setAttribute('aria-pressed', opts.showScoring ? 'true' : 'false');
            scoring.textContent = filledWords(
                opts.showScoring ? words.hide_scoring : words.show_scoring
            );
            scoring.addEventListener('click', () => {
                if (opts.onToggleScoring) { opts.onToggleScoring(); }
            });
            panel.appendChild(scoring);
        }

        if (castable && opts.castOpen) {
            panel.appendChild(
                buildCastStage(pending, casts, pushes, borrows, aiPick, opts)
            );
        }

        const selected = opts.selectedIndex == null
            ? null : pending.candidates[opts.selectedIndex];
        if (selected && (selected.destinations || []).length) {
            panel.appendChild(buildDestinationNote(selected, opts));
        }

        const confirm = document.createElement('button');
        confirm.type = 'button';
        confirm.className = 'battle-button battle-button-primary turn-confirm';
        confirm.dataset.focusKey = 'confirm';
        confirm.textContent = filledWords(words.confirm);
        confirm.disabled = opts.selectedIndex == null || Boolean(opts.busy);
        confirm.addEventListener('click', () => {
            if (opts.onConfirm && opts.selectedIndex != null) {
                opts.onConfirm(pending.candidates[opts.selectedIndex]);
            }
        });
        panel.appendChild(confirm);
        restoreTurnPanelFocus(panel, focusKey);
    }

    /**
     * Append the turn sections the server rendered (#504).
     *
     * Each entry is ``{turn, html}``: one battle/_turn_group.html section
     * holding that turn's new events, the same partial the page renders, so
     * this file writes no event markup of its own. A turn not yet on the page
     * is appended whole; a turn already there (a paused turn resumed by an
     * act or a poll) takes the new events into its existing list, so each
     * turn keeps one section.
     */
    function appendTurns(log, turns) {
        const emptyNotice = log.querySelector('#battle-log-empty');
        if (emptyNotice && turns.length) { emptyNotice.remove(); }
        turns.forEach(({ turn, html }) => {
            const fragment = document.createElement('template');
            fragment.innerHTML = html.trim();
            const incoming = fragment.content.querySelector('.turn-group');
            if (!incoming) { return; }
            const existing = log.querySelector(`.turn-group[data-turn="${turn}"]`);
            if (!existing) {
                log.appendChild(incoming);
                return;
            }
            const list = existing.querySelector('.turn-events');
            Array.from(incoming.querySelectorAll('.turn-events > .battle-event'))
                .forEach((item) => { list.appendChild(item); });
        });
    }

    /**
     * Empty the log back to its no-turns-yet state — what a restart leaves
     * behind. ``emptyHtml`` is the restart response's ``empty_log_html``,
     * rendered from battle/_log_empty.html, the partial the page itself
     * renders for a battle with no turns, so this file writes no notice of
     * its own (#508).
     */
    function resetLog(log, emptyHtml) {
        log.textContent = '';
        const fragment = document.createElement('template');
        fragment.innerHTML = (emptyHtml || '').trim();
        log.appendChild(fragment.content);
    }

    /** Poll cadence for lobby seats and awaited player turns. */
    const POLL_INTERVAL_MS = 2000;

    // The polls this script has started. Each runs until the page goes, and
    // nothing on the page stops one. The Jest suites outlive the page: they
    // wire page after page into one jsdom, and a poll left running there
    // fetches with a later test's mock (#25). stopPagePolls is for them.
    const pagePolls = new Set();

    /**
     * Run `poll` every POLL_INTERVAL_MS until stopPagePolls or stopPagePoll.
     * @returns {number} the poll's id, for stopPagePoll
     */
    function startPagePoll(poll) {
        const id = setInterval(poll, POLL_INTERVAL_MS);
        pagePolls.add(id);
        return id;
    }

    /** Stop the one poll `id` names. */
    function stopPagePoll(id) {
        clearInterval(id);
        pagePolls.delete(id);
    }

    /**
     * What an arena's poll announces, on the arena, when the battle has gone
     * back under it: a restart made elsewhere (#9). Cancellable: a page that
     * can draw the arena again in place (the GM chat's Battle tab, #89)
     * cancels it and does, so the rest of that page stays as it is; a page
     * where nothing cancels it reloads for the server's clean slate.
     */
    const REWOUND_EVENT = 'battle-rewound';

    /** Stop every poll this script has started. */
    function stopPagePolls() {
        for (const id of pagePolls) { clearInterval(id); }
        pagePolls.clear();
    }

    /** Wire the page up. No-op on pages without the battle detail markup. */
    /**
     * Wire the "Show every die roll" checkbox to the log's own class.
     *
     * The roll events are always written by the engine and always rendered
     * into the DOM; this only decides whether they are on screen (#301). The
     * choice is remembered per reader in localStorage, so it survives a
     * reload the way any other setting would, and the control itself is
     * always present -- nothing appears or vanishes on its own.
     */
    const DICE_PREFERENCE_KEY = 'battle.showDiceRolls';

    /**
     * Whether this reader has asked to see the AI's scoring on the turn
     * menu (#788), remembered per reader like the dice switch above. Off
     * until asked for: the plain lines lead.
     */
    const SCORING_PREFERENCE_KEY = 'battle.showScoring';

    function scoringPreference() {
        try {
            return window.localStorage.getItem(SCORING_PREFERENCE_KEY) === 'shown';
        } catch (error) {
            return false; // private browsing, blocked storage: the default
        }
    }

    function rememberScoringPreference(shown) {
        try {
            window.localStorage.setItem(SCORING_PREFERENCE_KEY, shown ? 'shown' : 'hidden');
        } catch (error) {
            // Nothing to remember it with; the switch still holds for this page.
        }
    }

    function initDiceToggle(log, scope) {
        const toggle = (scope || document).querySelector('#battle-log-dice');
        if (!toggle || !log) { return; }
        let stored = null;
        try {
            stored = window.localStorage.getItem(DICE_PREFERENCE_KEY);
        } catch (error) {
            stored = null; // private browsing, blocked storage: use the default
        }
        const apply = (shown) => {
            toggle.checked = shown;
            log.classList.toggle('dice-hidden', !shown);
        };
        apply(stored !== 'hidden');
        toggle.addEventListener('change', () => {
            apply(toggle.checked);
            try {
                window.localStorage.setItem(
                    DICE_PREFERENCE_KEY, toggle.checked ? 'shown' : 'hidden'
                );
            } catch (error) {
                // Nothing to remember it with; the class still applies now.
            }
        });
    }

    /**
     * The ruling panels a payload's ``rulings_html`` changes (#867, #37).
     *
     * The server renders each Game Master's ruling panel with its own
     * partial and keys it by the panel's element id (views.ruling_panels_html);
     * a panel counts as changed when its ``data-record`` (the stored ruling)
     * differs from the one on the page, so the CSRF token a fresh render
     * carries never counts. An empty string means the battle shows no such
     * panel. Panels are looked up by id within ``scope``, as everything
     * ``initBattlePage`` wires is; panels it does not carry are left alone.
     *
     * @param {Object<string, string>} htmlById
     * @param {ParentNode} [scope] - defaults to the document
     * @returns {{current: Element, fresh: Element|null}[]}
     */
    function rulingPanelChanges(htmlById, scope) {
        const container = scope || document;
        const changes = [];
        Object.entries(htmlById || {}).forEach(([panelId, html]) => {
            const current = container.querySelector(`#${panelId}`);
            if (!current) { return; }
            const holder = document.createElement('template');
            holder.innerHTML = (html || '').trim();
            const fresh = holder.content.firstElementChild;
            if (fresh && fresh.dataset.record === current.dataset.record) {
                return;
            }
            changes.push({ current, fresh });
        });
        return changes;
    }

    /**
     * Put the server's ruling panels in place of the changed ones (#867, #37).
     * Each form's ``next`` is pointed at ``backTo`` (this page), since the
     * server rendered it under the endpoint that answered.
     *
     * @param {Object<string, string>} htmlById
     * @param {string} backTo
     * @param {ParentNode} [scope] - defaults to the document
     * @returns {number} How many panels were repainted.
     */
    function repaintRulings(htmlById, backTo, scope) {
        const changes = rulingPanelChanges(htmlById, scope);
        changes.forEach(({ current, fresh }) => {
            if (!fresh) {
                current.remove();
                return;
            }
            fresh.querySelectorAll('input[name="next"]').forEach((input) => {
                input.value = backTo;
            });
            current.replaceWith(fresh);
            // local-time.js writes a timestamp in the viewer's zone; a panel
            // painted after load is asked for explicitly (#62).
            if (typeof window.applyLocalTimes === 'function') window.applyLocalTimes(fresh);
        });
        return changes.length;
    }

    /**
     * Wire a started battle's arena: the map, roster, log, turn panel, the
     * Advance and Restart buttons, and the poll.
     *
     * ``scope`` is the element holding the arena markup
     * (battle/_arena.html plus its controls); it defaults to the document,
     * which is what the battle page needs. The GM chat embeds the same
     * partial in its side panel (#345): the load-time call below finds it
     * there just the same, and a container built after load is wired by
     * calling this with that container. Every lookup is by id within the
     * scope, so a page carries one arena. Wiring twice is a no-op.
     * @param {ParentNode} [scope]
     */
    function initBattlePage(scope) {
        const root = scope || document;
        const byId = (id) => root.querySelector(`#${id}`);
        const viewScript = byId('battle-view');
        const arena = byId('battle-arena');
        if (!viewScript || !arena) { return; }
        if (arena.dataset.wired) { return; }
        arena.dataset.wired = 'true';
        const roster = byId('battle-roster');
        const log = byId('battle-log');
        const button = byId('advance-turn');
        const statusChip = byId('battle-status');
        const turnLabel = byId('battle-turn');
        const panel = byId('turn-panel');
        const seatsScript = byId('battle-seats');
        const labelsScript = byId('battle-status-labels');
        const statusLabels = labelsScript ? JSON.parse(labelsScript.textContent) : {};
        const pendingScript = byId('battle-pending');
        // The turn panel's own words (#94), the server's, once per page.
        const panelWordsScript = byId('battle-turn-panel-words');
        if (panel && !panelWordsScript) {
            // battle/_arena.html renders it beside the panel; a page that
            // lost it would fail at the first word, with no name for why.
            throw new Error('the turn panel has no #battle-turn-panel-words block (#94)');
        }
        const panelWords = panelWordsScript ? JSON.parse(panelWordsScript.textContent) : null;
        const isDm = Boolean(panel && panel.dataset.isDm);
        const staleSlot = byId('battle-stale-slot');

        /**
         * Show the stale-pools notice the server rendered, or clear it (#842).
         * The server renders it from the advance endpoint or a poll, whose
         * own path the Game Master's form would otherwise return to, so the
         * form is pointed back at this page (the battle's or the GM chat's).
         */
        // The notice last rendered, so a poll that brings a new one -- the
        // records moved while a player's move is awaited -- counts as a
        // change (#892).
        let lastStaleHtml = staleSlot ? staleSlot.innerHTML.trim() : '';
        function renderStale(html) {
            if (!staleSlot) { return; }
            staleSlot.innerHTML = html || '';
            lastStaleHtml = (html || '').trim();
            const back = staleSlot.querySelector('input[name="next"]');
            if (back) {
                back.value = `${window.location.pathname}${window.location.search}`;
            }
        }

        /**
         * Render a refusal of stale pools (#842) from the 409 that carries
         * the notice and the Game Master's control; they stay in the slot
         * until a reload or an advance clears them. The Advance button and a
         * player's choice reach the same refusal and are answered alike
         * (#112). True when the refusal was rendered here; anything else is
         * left to the caller's notice.
         */
        function showStale(data, status) {
            if (status !== 409 || !staleSlot || !data || !data.stale_html) {
                return false;
            }
            renderStale(data.stale_html);
            if (button && data.advance) {
                button.textContent = data.advance.label;
                button.disabled = !data.advance.enabled;
            }
            return true;
        }

        initDiceToggle(log, root);

        // What to draw, not the rules state: the server computes this per
        // profile (see battle/viewmodel.py).
        let view = JSON.parse(viewScript.textContent);
        let seats = seatsScript ? JSON.parse(seatsScript.textContent) : [];
        let pending = pendingScript ? JSON.parse(pendingScript.textContent) : null;
        let currentStatus = null;
        let currentTurn = null;
        let selectedIndex = null;
        // The cast stage is open when the player has declared a cast and
        // not yet picked a spell or stood down (#250's cast gate).
        let castOpen = false;
        // The hex picked for the selected option (#819, #866), or null.
        let destination = null;
        let showScoring = scoringPreference();
        let actionBusy = false;
        // Battle-global sequence high-water mark, for incremental polling.
        let lastSequence = (view.next_sequence || 1) - 1;
        // Who is awaited and who sits where, as last drawn. A pause that
        // moves from one seated figure to the next writes no event and
        // keeps the turn and the status (the partial turn rolls back), so
        // this is the only thing a poll can see change (#9).
        const seatsAndPause = (seatList, pause) => JSON.stringify(
            { seats: seatList, pending: pause }
        );
        let lastSeatsAndPause = seatsAndPause(seats, pending);
        // Counts every response the page applies. A poll asked before an
        // act or an advance was answered may describe the battle before
        // it: applied after it, that would redraw the earlier pause, or
        // read a turn that went backwards as a restart and reload (#9).
        let appliedCount = 0;
        // Which combatants' attribute panels the reader has open (#302).
        // The poll repaints the roster every couple of seconds; this is
        // what keeps an opened panel open across those repaints.
        const openDetail = new Set();

        // Click-to-rename options for roster cards, when the template
        // supplies the endpoint. A saved rename re-renders roster and arena
        // from the server-returned state (state-driven, house rules).
        const renameOptions = roster.dataset.renameUrl ? {
            url: roster.dataset.renameUrl,
            onRenamed: (data) => {
                if (data && data.view) { view = data.view; }
                drawArena();
                renderRoster(
                    roster, view, renameOptions, seatsById(seats), openDetail
                );
            },
        } : undefined;

        /** The selected option, while the awaited figure is this viewer's. */
        function selectedCandidate() {
            if (!pending || !pending.is_you || selectedIndex == null) { return null; }
            return (pending.candidates || [])[selectedIndex] || null;
        }

        /**
         * The arena, with the hex picker while a placing option is selected.
         *
         * A repaint replaces every hex, so the one keyboard focus was on
         * (or ``focusOn``, the hex just picked) takes focus back afterwards,
         * as the turn panel's controls do (#646): a person picking hexes by
         * keyboard stays on the board rather than landing on the option.
         */
        function drawArena(focusOn) {
            const active = document.activeElement;
            const held = focusOn ? `${focusOn[0]},${focusOn[1]}`
                : (active && arena.contains(active) && active.getAttribute('data-hex'));
            const selected = selectedCandidate();
            const hexes = (selected && selected.destinations) || [];
            renderArena(arena, view, hexes.length ? {
                hexes,
                chosen: destination,
                onPick: (hex) => {
                    destination = sameHex(hex, destination) ? null : hex;
                    renderPanel();
                    drawArena(hex);
                },
            } : null);
            const again = held && arena.querySelector(`[data-hex="${held}"]`);
            if (again) { again.focus(); }
        }

        /** A cast selection is dropped whenever the stage closes. */
        function closeCastStage() {
            castOpen = false;
            const candidates = (pending && pending.candidates) || [];
            const selected = selectedIndex == null ? null : candidates[selectedIndex];
            if (selected && selected.spell_key) { selectedIndex = null; }
        }

        function renderPanel() {
            if (!panel) { return; }
            // The pause these buttons are being built for. Held here rather
            // than read from `pending` when a button is pressed, so the body
            // names the menu the person actually looked at even if the poll
            // has moved the battle on since (#644). The server refuses the
            // mismatch and the arena shows why.
            const answering = pending;
            renderTurnPanel(panel, answering, {
                words: panelWords,
                isDm,
                selectedIndex,
                castOpen,
                showScoring,
                busy: actionBusy,
                destination,
                onClearDestination: () => {
                    destination = null;
                    drawArena();
                    renderPanel();
                },
                onToggleScoring: () => {
                    showScoring = !showScoring;
                    rememberScoringPreference(showScoring);
                    renderPanel();
                },
                onSelect: (index) => {
                    selectedIndex = index === selectedIndex ? null : index;
                    destination = null;
                    drawArena();
                    renderPanel();
                },
                onCastOpen: () => {
                    if (castOpen) { closeCastStage(); } else { castOpen = true; }
                    renderPanel();
                },
                onCastClose: () => {
                    closeCastStage();
                    renderPanel();
                },
                onConfirm: (candidate) => submitAction(Object.assign({
                    combatant_id: answering.combatant_id,
                    turn: answering.turn,
                    letter: candidate.letter,
                    target_id: candidate.target_id,
                    spell_key: candidate.spell_key || '',
                // The mana a pushed copy pushes (#960), and that a borrowing
                // copy borrows (tarmar-engine #28), only for such a copy: the
                // server takes neither as the plain cast. Whose Runaway an
                // anchor takes back (tarmar-engine #30), only for an anchor.
                // The mana a counter commits against the caster its target
                // names (tarmar-engine #31), only for a counter.
                // The hex only when one was picked (#819, #866): with none
                // the option makes its own move, as it always has.
                }, candidate.push_mana ? { push_mana: candidate.push_mana } : {},
                candidate.borrow ? { borrow: true } : {},
                candidate.anchor_target != null
                    ? { anchor_target: candidate.anchor_target } : {},
                candidate.counter_mana != null
                    ? { counter_mana: candidate.counter_mana } : {},
                destination ? { destination } : {})),
                onAiChoose: () => submitAction({
                    combatant_id: answering.combatant_id,
                    turn: answering.turn,
                    ai: true,
                }),
            });
        }

        // Advance, restart, act, and the state poll all answer in the same
        // shape — status, turn, view, events, seats, pending — so one
        // function applies any of them to the page.
        function applyBattleResponse(data) {
            view = data.view;
            seats = data.seats || seats;
            appendTurns(log, data.turns_html || []);
            renderRoster(
                roster, view, renameOptions, seatsById(seats), openDetail
            );
            statusChip.textContent = statusLabel(data.status, statusLabels);
            turnLabel.textContent = data.turn;
            currentStatus = data.status;
            currentTurn = data.turn;
            lastSequence = (view.next_sequence || 1) - 1;
            appliedCount += 1;
            const wasPendingFor = pending && pending.combatant_id;
            pending = data.pending || null;
            lastSeatsAndPause = seatsAndPause(seats, pending);
            if (!pending || pending.combatant_id !== wasPendingFor) {
                selectedIndex = null;
                castOpen = false;
                destination = null;
            }
            drawArena();
            renderPanel();
            refusedMoveNotices(data.events || [], seats).forEach(
                (message) => uiNotify(message)
            );
            // The board has changed, and in a fight that writes to records
            // so has each saved character's record (#750, #751): anything on
            // the page drawn from those records -- the GM chat's Characters
            // tab -- refetches on this rather than waiting for a chat frame.
            document.dispatchEvent(
                new CustomEvent('battle-applied', {
                    detail: {
                        status: data.status,
                        live: data.live,
                        bareHanded: data.bare_handed,
                    },
                })
            );
            // Every response says whether the records moved under the fight,
            // a poll while a player's move is awaited included (#842).
            if (Object.prototype.hasOwnProperty.call(data, 'stale_html')) {
                renderStale(data.stale_html);
            }
            // The Game Master's rulings, repainted from the records (#867, #37).
            repaintRulings(
                data.rulings_html,
                `${window.location.pathname}${window.location.search}`,
                root
            );
            if (button) {
                // The label and the disabled state are the server's
                // (views.advance_control): in a mixed battle the button
                // has to name whose move it stopped for, and that answer
                // belongs beside the rule that produces it. Older payloads
                // carry no readout, so the status rule stays as the
                // fallback rather than blanking the button.
                if (data.advance) {
                    button.textContent = data.advance.label;
                    button.disabled = !data.advance.enabled;
                } else {
                    button.disabled = TERMINAL_STATUSES.includes(data.status)
                        || data.status === 'awaiting';
                }
            }
        }

        /**
         * POST an action endpoint and hand the parsed body back, or null
         * after leaving a persistent notice describing the failure.
         * ``action`` names the attempt in that notice ("Advancing the turn").
         */
        async function postAction(url, action, body, onRefused) {
            try {
                const { ok, status, data, parseError } = await postJson(url, body);
                if (!ok && onRefused && onRefused(data, status)) {
                    // The caller rendered the refusal from state it was sent.
                    return null;
                }
                if (!ok) {
                    // A non-JSON error page leaves data empty: the status line
                    // is all there is to say.
                    const detail = data.error
                        ? `HTTP ${status}: ${data.error}`
                        : `HTTP ${status}`;
                    uiNotify(`${action} failed (${detail}).`);
                    return null;
                }
                if (parseError) {
                    // Accepted, but with nothing readable to render from.
                    throw parseError;
                }
                return data;
            } catch (error) {
                uiNotify(`${action} failed (${error.message}).`);
                return null;
            }
        }

        async function submitAction(body) {
            if (!panel || actionBusy) { return; }
            actionBusy = true;
            renderPanel();
            const data = await postAction(
                panel.dataset.actUrl, 'Sending the action', body, showStale
            );
            actionBusy = false;
            if (!data) {
                renderPanel();
                return;
            }
            applyBattleResponse(data);
        }

        /**
         * Follow the battle from the poll endpoint while someone else can
         * move it: players follow the whole battle; the DM only needs the
         * poll while a player's choice is being awaited. A turn counter or
         * sequence going backwards means a restart happened elsewhere: the
         * page shows the clean server-rendered slate, in place where it can
         * (REWOUND_EVENT). An arena the page has since drawn again in its
         * place stops polling: the arena that replaced it polls for itself.
         */
        async function pollOnce() {
            if (!arena.isConnected) {
                stopPagePoll(pollId);
                return;
            }
            const shouldPoll = !TERMINAL_STATUSES.includes(currentStatus)
                && (!isDm || currentStatus === 'awaiting');
            if (!panel || !shouldPoll) { return; }
            const appliedWhenAsked = appliedCount;
            let response;
            try {
                response = await fetch(
                    `${panel.dataset.stateUrl}?after=${lastSequence}`
                );
            } catch (error) {
                return; // transient network trouble; the next tick retries
            }
            if (!response.ok) { return; }
            const data = await response.json();
            // Drawn again in its place while this was on its way: the answer
            // is the new arena's to read, and a rewind seen here would reach
            // no page that draws in place, and reload it (#89).
            if (!arena.isConnected) {
                stopPagePoll(pollId);
                return;
            }
            // Something newer was applied while this was in flight: drop
            // it, and let the next tick ask again.
            if (appliedCount !== appliedWhenAsked) { return; }
            const nextSequence = (data.view.next_sequence || 1) - 1;
            if (data.turn < currentTurn || nextSequence < lastSequence) {
                const rewound = new CustomEvent(
                    REWOUND_EVENT, { bubbles: true, cancelable: true }
                );
                if (arena.dispatchEvent(rewound)) { pageNavigation.reload(); }
                return;
            }
            const changed = data.status !== currentStatus
                || data.turn !== currentTurn
                || nextSequence !== lastSequence
                || (data.events || []).length > 0
                || seatsAndPause(data.seats || seats, data.pending || null)
                    !== lastSeatsAndPause
                || (typeof data.stale_html === 'string'
                    && data.stale_html.trim() !== lastStaleHtml)
                || rulingPanelChanges(data.rulings_html, root).length > 0;
            if (changed) { applyBattleResponse(data); }
        }

        drawArena();
        renderRoster(roster, view, renameOptions, seatsById(seats), openDetail);

        // Selecting a figure on the map opens that combatant's panel in the
        // roster and brings the card into view (#302). Delegated, so it
        // survives the re-render the poll does; and it opens the one panel
        // the card already owns rather than floating a second copy over the
        // map, which would be the transient popup the house rule forbids.
        arena.addEventListener('click', (event) => {
            const marker = event.target.closest
                && event.target.closest('[data-combatant]');
            if (!marker) { return; }
            const combatantId = Number(marker.dataset.combatant);
            const card = roster.querySelector(
                `[data-combatant="${combatantId}"]`
            );
            const detail = card && card.querySelector('.combatant-detail');
            if (!detail) { return; }
            openDetail.add(combatantId);
            detail.open = true;
            card.scrollIntoView({ block: 'nearest' });
        });

        currentStatus = statusChip.textContent.trim().toLowerCase();
        currentStatus = Object.keys(statusLabels).find(
            (key) => statusLabels[key].toLowerCase() === currentStatus
        ) || currentStatus;
        currentTurn = Number(turnLabel.textContent) || 0;
        renderPanel();
        // The poll's id, so an arena drawn again in this one's place stops
        // this one's poll (#89).
        let pollId = null;
        if (panel && panel.dataset.stateUrl) {
            pollId = startPagePoll(pollOnce);
        }

        if (button) {
            let refusedAsStale = false;
            button.addEventListener('click', async () => {
                button.disabled = true;
                refusedAsStale = false;
                const data = await postAction(
                    button.dataset.url, 'Advancing the turn', undefined,
                    (body, status) => {
                        refusedAsStale = showStale(body, status);
                        return refusedAsStale;
                    }
                );
                if (!data) {
                    button.disabled = refusedAsStale;
                    return;
                }
                applyBattleResponse(data);
            });
        }

        // A seat hand-off (#853): delegated, because the roster repaints on
        // every poll. The answer is the battle payload, so the roster that
        // comes back says who plays the figure now.
        roster.addEventListener('click', async (event) => {
            const handOff = event.target.closest
                && event.target.closest('[data-seat-url]');
            if (!handOff) { return; }
            handOff.disabled = true;
            const data = await postAction(
                handOff.dataset.seatUrl,
                handOff.dataset.seatHandOff === 'take'
                    ? 'Taking the seat' : 'Giving the seat back'
            );
            if (!data) {
                handOff.disabled = false;
                return;
            }
            applyBattleResponse(data);
        });

        // Restart confirms in place: the button swaps for a confirm/cancel
        // pair (no modal — state-driven UI, house rule), and confirming
        // clears the log the server has already emptied.
        const restartGroup = byId('restart-group');
        const restartButton = byId('restart-battle');
        const restartConfirm = byId('restart-confirm');
        const restartCancel = byId('restart-cancel');
        if (restartGroup && restartButton && restartConfirm && restartCancel) {
            const setConfirming = (confirming) => {
                restartGroup.dataset.confirming = confirming ? 'true' : 'false';
            };
            restartButton.addEventListener('click', () => {
                setConfirming(true);
                restartConfirm.focus();
            });
            restartCancel.addEventListener('click', () => {
                setConfirming(false);
                restartButton.focus();
            });
            restartConfirm.addEventListener('click', async () => {
                restartConfirm.disabled = true;
                const data = await postAction(
                    restartButton.dataset.url,
                    'Restarting the battle'
                );
                restartConfirm.disabled = false;
                setConfirming(false);
                if (!data) { return; }
                resetLog(log, data.empty_log_html);
                applyBattleResponse(data);
            });
        }
    }

    /**
     * True while the viewer is mid-interaction in the lobby (a claim form
     * open, a control focused): reloading then would throw away their
     * keystrokes, so the poll holds off until they settle.
     */
    function lobbyEditing(lobby, activeElement) {
        if (lobby.querySelector('details[open]')) { return true; }
        return Boolean(activeElement) && lobby.contains(activeElement)
            && ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(
                activeElement.tagName
            );
    }

    /**
     * Wire the lobby page: poll the state endpoint and reload whenever the
     * seats change under the viewer or the battle starts. The reload lands
     * on the server-rendered truth — same state-driven idiom as the battle
     * page, with the whole document as the render target.
     */
    function initLobbyPage() {
        const lobby = document.getElementById('battle-lobby');
        const seatsScript = document.getElementById('battle-seats');
        if (!lobby || !seatsScript || !lobby.dataset.stateUrl) { return; }
        let baseline = JSON.stringify(JSON.parse(seatsScript.textContent));
        startPagePoll(async () => {
            if (lobbyEditing(lobby, document.activeElement)) { return; }
            let response;
            try {
                response = await fetch(lobby.dataset.stateUrl);
            } catch (error) {
                return; // transient network trouble; the next tick retries
            }
            if (!response.ok) { return; }
            const data = await response.json();
            if (data.status !== 'lobby'
                    || JSON.stringify(data.seats) !== baseline) {
                pageNavigation.reload();
            }
        });
    }

    if (typeof document !== 'undefined') {
        initBattlePage();
        initLobbyPage();
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            axialToPixel,
            hexCorners,
            hexHalfSize,
            arenaViewBox,
            poolFraction,
            shortName,
            groundWeaponLabel,
            optionLetter,
            statusLabel,
            seatsById,
            topCandidateIndex,
            refusedMoveNotices,
            sameHex,
            hexLabel,
            teamOrderOf,
            teamTint,
            renderArena,
            renderRoster,
            detailPanel,
            renderTurnPanel,
            appendTurns,
            resetLog,
            rulingPanelChanges,
            repaintRulings,
            lobbyEditing,
            initDiceToggle,
            initBattlePage,
            initLobbyPage,
            stopPagePolls,
            REWOUND_EVENT,
        };
    }
    root.battlePage = { initBattlePage, initLobbyPage, REWOUND_EVENT };
}(typeof window !== 'undefined' ? window : globalThis));
