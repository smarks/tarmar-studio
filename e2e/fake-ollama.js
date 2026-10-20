/**
 * A stand-in for Ollama's HTTP API, for the e2e suite's dev server (issue #515).
 *
 * Playwright starts it beside `./run dev` and points OLLAMA_BASE_URL at it, so
 * a spec can play a real GM turn through the server without a model, and no
 * run of the suite ever reaches a real Ollama. Only `POST /api/chat` answers:
 * everything else is a 404, which the app already treats as a degraded
 * grounding step rather than a failed turn.
 *
 * A turn makes two calls (issue #590). A planning call (its last message asks
 * for "the JSON plan") is answered with a JSON plan: "build twins <name> and
 * <name>" records both as new characters with all 18s, "dice step" waits
 * MARKER_PAUSE_MS and then rolls 3d6 for a test of nerve, "walk to <place>"
 * moves the party there (issue #579), "meet <name>" records that person and
 * their iron box with their optional fields written as null (issue #648),
 * "roll up <name>" rolls a new character's attributes, which starts it with
 * its background rolled, and "reroll <name>'s background" rolls it again by the
 * id the state block lists (issue #754), "award <n> xp to <name>", "give
 * <name> <n> gold", "check <name>'s <skill> against <n>" and "<name> fell from
 * the tower" plan a Game Master's records and a skill check (issues #802,
 * #843), "<name> tests her nerve" and "roll <name>'s survival save" plan a
 * character's roll and survival save (issue #886), "<name> makes a
 * <attribute> check" plans an attribute check (issue #911), "<name> makes a
 * <attribute> check at <total> with <modifier>" plans one a Game Master
 * stated with a modifier (issue #70), "twins in brackets"
 * names a character the name rule refuses (issue #908), "behind another
 * table" holds the planning call QUEUE_PAUSE_MS before answering anything,
 * as Ollama holds a request queued behind another (issue #64), and anything
 * else plans nothing. A structured call asking for a
 * player's foes is answered with set_foes: gray wolves (dire wolves when the
 * request says "dire"), as many as the request counts (issue #841), or
 * bandits by calling when it says "bandit" (issue #894). Every other call is narration: a fixed reply that pauses
 * after its first words for SLOW_PAUSE_MS when the message says "slowly",
 * long enough for a spec to reload or close the page while the turn runs.
 * "what level" is narrated instead with prose whose only number is a weapon
 * modifier, so a spec can see the record line fire beside it (issue #727).
 *
 * Usage: node e2e/fake-ollama.js <port>
 */

const http = require('http');

const port = Number(process.argv[2] || process.env.FAKE_OLLAMA_PORT || 9000);
const SLOW_PAUSE_MS = Number(process.env.FAKE_OLLAMA_SLOW_PAUSE_MS || 6000);
// How long a "dice step" plan takes, which is how long the page has to show
// that the turn is planning (issue #569).
const MARKER_PAUSE_MS = Number(process.env.FAKE_OLLAMA_MARKER_PAUSE_MS || 4000);
// How long a request "behind another table" is held before anything is sent,
// not even headers, as Ollama holds one queued behind another turn (issue
// #64). Past the server's 10 s keep-alive, so the page is told it waits.
const QUEUE_PAUSE_MS = Number(process.env.FAKE_OLLAMA_QUEUE_PAUSE_MS || 12000);

function lastUserMessage(messages) {
    const users = (messages || []).filter((message) => message.role === 'user');
    return users.length ? String(users[users.length - 1].content || '') : '';
}

// What a plan-engine call's last message says the latest message was.
function latestMessage(said) {
    const match = said.match(/Latest message: ([\s\S]*?)(?:\n\nNarrate this turn|$)/);
    return match ? match[1] : said;
}

function isPlanCall(said) {
    return said.includes('Reply with only the JSON plan');
}

// The id the state block lists for the character called ``name``.
function listedId(said, name) {
    const listed = said.match(new RegExp(`- id (\\d+): ${name}\\b`));
    return listed ? Number(listed[1]) : null;
}

// A Game Master's records and a skill check (issues #802, #843), by the id
// the state block lists for the character the message names.
function recordsPlanFor(latest, said) {
    const awarded = latest.match(/award (\d+) xp to (\w+)/i);
    if (awarded) {
        return { actions: [{ action: 'award_xp', character_id: listedId(said, awarded[2]), xp: Number(awarded[1]) }] };
    }
    const coin = latest.match(/give (\w+) (\d+) gold/i);
    if (coin) {
        return {
            actions: [{ action: 'set_coin', character_id: listedId(said, coin[1]), change: Number(coin[2]), reason: 'the bounty' }],
        };
    }
    const checked = latest.match(/check (\w+)'s (\w+) against (\d+)/i);
    if (checked) {
        return {
            actions: [{
                action: 'skill_check',
                character_id: listedId(said, checked[1]),
                skill: checked[2],
                difficulty: Number(checked[3]),
            }],
        };
    }
    // A listed character's free-form roll, planned with a backwards
    // direction the server must not read (issue #911), an attribute check
    // whose injury penalty the rule applies, and a survival save (#886).
    const nerve = latest.match(/(\w+) tests (?:her|his|their) nerve/i);
    if (nerve) {
        return {
            actions: [{
                action: 'roll',
                dice: '3d6',
                purpose: 'a test of nerve',
                character_id: listedId(said, nerve[1]),
                direction: 'over',
            }],
        };
    }
    // A Game Master's attribute check stated at his table, with a modifier
    // (issues #911, #953, #70): "<name> makes a <attribute> check at <total>
    // with <modifier>".
    const statedCheck = latest.match(/(\w+) makes an? (\w+) check at (-?\d+) with ([+-]?\d+)/i);
    if (statedCheck) {
        return {
            actions: [{
                action: 'attribute_check',
                character_id: listedId(said, statedCheck[1]),
                attribute: statedCheck[2].toLowerCase(),
                total: Number(statedCheck[3]),
                modifier: Number(statedCheck[4]),
            }],
        };
    }
    const attributeCheck = latest.match(/(\w+) makes an? (\w+) check/i);
    if (attributeCheck) {
        return {
            actions: [{
                action: 'attribute_check',
                character_id: listedId(said, attributeCheck[1]),
                attribute: attributeCheck[2].toLowerCase(),
            }],
        };
    }
    const save = latest.match(/roll (\w+)'s survival save/i);
    if (save) {
        return { actions: [{ action: 'survival_save', character_id: listedId(said, save[1]) }] };
    }
    // A name the name rule refuses, on a first message (issue #908).
    if (/twins in brackets/i.test(latest)) {
        return {
            actions: [{
                action: 'record_attributes',
                new_character: { name: 'Twin 1 (Male)', label: 'male twin' },
                all: 18,
            }],
        };
    }
    const died = latest.match(/(\w+) fell from the tower/i);
    if (died) {
        return { actions: [{ action: 'mark_dead', character_id: listedId(said, died[1]), cause: 'fell from the tower' }] };
    }
    return null;
}

// A player's /fight is answered by the Game Master's set_foes, held to the
// bestiary catalog (issue #841): wolves, as many as the request counts.
const COUNT_WORDS = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4 };

function setFoesFor(said) {
    const asked = said.match(/The player asked for: "([^"]*)"/);
    const request = asked ? asked[1].toLowerCase() : '';
    const counted = request.match(/^(\d+|a|an|one|two|three|four)\s/);
    const count = counted ? Number(counted[1]) || COUNT_WORDS[counted[1]] : 1;
    if (/bandit/.test(request)) {
        // A person, named by calling (issue #894).
        return { action: 'set_foes', foes: [{ calling: 'bandit', count }] };
    }
    const slug = /dire/.test(request) ? 'dire-wolf' : 'gray-wolf';
    return { action: 'set_foes', foes: [{ beast_slug: slug, count }] };
}

function planFor(latest, said) {
    const records = recordsPlanFor(latest, said);
    if (records) return records;
    if (/dice step/i.test(latest)) {
        return { actions: [{ action: 'roll', dice: '3d6', purpose: 'a test of nerve' }] };
    }
    // Where the party is, which the Map tab follows (issue #579).
    const walked = latest.match(/walk to ([^.!?\n]+)/i);
    if (walked) {
        return { actions: [{ action: 'move_party', place: walked[1].trim() }] };
    }
    // A plan whose optional fields the model had nothing to put in, written
    // as null exactly as a real model writes them (issue #648). One of these
    // used to make the whole reply no plan, so none of its actions ran.
    const met = latest.match(/meet (\w+)/i);
    if (met) {
        return {
            actions: [
                { action: 'record_npc', name: met[1], where: 'the shrine', wants: null, condition: '' },
                { action: 'record_object', name: `${met[1]}'s iron box`, wants: null, notes: null },
            ],
        };
    }
    // A character built in chat has its background rolled when it is made,
    // and a later turn may ask for it again (issue #754).
    const rolledUp = latest.match(/roll up (\w+)/i);
    if (rolledUp) {
        const actions = [{ action: 'roll_attributes', new_character: { name: rolledUp[1], label: '' } }];
        // An Origin the message states is recorded with set_background, as
        // the prompt teaches (#754); the server leaves it unrolled (#970).
        const grewUp = latest.match(/grew up in (?:a |the )?(city|village|rural)/i);
        if (grewUp) {
            actions.push({ action: 'set_background', new_character_ref: rolledUp[1], location: grewUp[1] });
        }
        return { actions };
    }
    const reroll = latest.match(/reroll (\w+)'s background/i);
    if (reroll) {
        return { actions: [{ action: 'roll_background', character_id: listedId(said, reroll[1]) }] };
    }
    const twins = latest.match(/build twins (\w+) and (\w+)/i);
    if (!twins) return { actions: [] };
    return {
        actions: [twins[1], twins[2]].map((name) => ({
            action: 'record_attributes',
            new_character: { name, label: '' },
            all: 18,
        })),
    };
}

function chunk(content, done) {
    return `${JSON.stringify({ message: { role: 'assistant', content }, done })}\n`;
}

function sleep(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function answerChat(request, response, payload) {
    const said = lastUserMessage(payload.messages);
    if (payload.stream === false) {
        // A structured call: a player's /fight foes (issue #841), or nothing
        // to report.
        const content = said.includes('"action": "set_foes"') ? JSON.stringify(setFoesFor(said)) : '{}';
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ message: { role: 'assistant', content }, done: true }));
        return;
    }
    if (isPlanCall(said) && /behind another table/i.test(latestMessage(said))) {
        await sleep(QUEUE_PAUSE_MS);
    }
    response.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    if (isPlanCall(said)) {
        const latest = latestMessage(said);
        if (/dice step/i.test(latest)) {
            // A slow plan, so the page can be looked at while it is planning.
            await sleep(MARKER_PAUSE_MS);
        }
        response.end(chunk(JSON.stringify(planFor(latest, said)), true));
        return;
    }
    if (/what level/i.test(latestMessage(said))) {
        // A reply carrying a number for another reason: the modifier a sword
        // skill grants, not the level that was asked for. Any digit in the
        // reply used to pass for the record's own figure, so the server's
        // record line stayed silent (issue #727).
        response.write(chunk('The record is the record. ', false));
        response.write(chunk('Her sword arm gains **+1** against armored foes.', false));
        response.end(chunk('', true));
        return;
    }
    response.write(chunk('The fake Game Master considers it. ', false));
    if (/slowly/i.test(latestMessage(said))) {
        await sleep(SLOW_PAUSE_MS);
    }
    response.write(chunk('The crypt is quiet, and nothing stirs.', false));
    response.end(chunk('', true));
}

const server = http.createServer((request, response) => {
    if (request.method === 'GET' && request.url === '/api/version') {
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ version: 'fake' }));
        return;
    }
    if (request.method !== 'POST' || request.url !== '/api/chat') {
        response.writeHead(404, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ error: 'not faked' }));
        return;
    }
    let body = '';
    request.on('data', (data) => { body += data; });
    request.on('end', () => {
        let payload;
        try {
            payload = JSON.parse(body);
        } catch (error) {
            response.writeHead(400, { 'Content-Type': 'application/json' });
            response.end(JSON.stringify({ error: `bad JSON: ${error.message}` }));
            return;
        }
        answerChat(request, response, payload).catch((error) => {
            console.error('fake Ollama failed to answer', error);
            response.destroy(error);
        });
    });
});

server.listen(port, '127.0.0.1', () => {
    console.log(`fake Ollama listening on http://127.0.0.1:${port}`);
});
