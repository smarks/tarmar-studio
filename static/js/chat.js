/* Chat JS — handles SSE streaming, message display, markdown rendering. */

// postWithCsrf/csrfToken/uiNotify/uiConfirm come from api.js, which base.html loads in
// <head> — ahead of this file's <script> tag in gm/chat.html. Node/Jest has no
// shared script scope, so require api.js there; either way the names end up on
// the global object.
if (typeof module !== 'undefined' && module.exports) {
  require('./api.js');
  // autosave.js declares initAutosave at script scope — a global when a
  // <script> tag loads it, but not under CommonJS. Publish it so the call
  // below is the same line in both places.
  globalThis.initAutosave = require('./autosave.js').initAutosave;
}

// Endpoints, CSRF token and the active session id, from the #chat-config
// json_script block gm/chat.html renders (issue #63). These five names were
// declared by an inline <script> before that; nothing else about their use
// changed, so sessionId is still the mutable one — /save on an unsaved
// session assigns it the id the server hands back.
const CHAT_CONFIG = JSON.parse(document.getElementById('chat-config').textContent);
const STREAM_URL = CHAT_CONFIG.streamUrl;
const SAVE_URL = CHAT_CONFIG.saveUrl;
const DELETE_URL = CHAT_CONFIG.deleteUrl;
let sessionId = CHAT_CONFIG.sessionId;
// The mode the page is showing (issue #372). It only ever travels on the turn
// that creates a session: once a session exists the server reads the mode off
// its own row and ignores this. Sending it is safe because a mode says how the
// GM behaves, never what the person may do — authority stays with the account.
const SESSION_MODE = CHAT_CONFIG.mode;
// A started turn runs to the end on the server whether or not this page is
// still listening (issue #515). These say where to ask after one, and which
// one was still running when the page loaded.
const TURN_URL_TEMPLATE = CHAT_CONFIG.turnUrlTemplate;
const RUNNING_TURN = CHAT_CONFIG.runningTurn || null;
const TURN_POLL_MS = typeof CHAT_CONFIG.turnPollMs === 'number' ? CHAT_CONFIG.turnPollMs : 2000;

// Assistant message content comes from the Ollama model, which is
// influenceable via prompt injection in user-supplied text (issue #141).
// marked.parse() passes raw HTML straight through, so every marked.parse()
// result must go through DOMPurify before it reaches innerHTML. This
// allowlist covers the tags/attributes markdown (GFM) actually produces —
// nothing script- or event-handler-capable is on it.
const MARKDOWN_SANITIZE_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'hr',
    'strong', 'em', 'b', 'i', 'u', 's', 'del',
    'code', 'pre', 'blockquote',
    'ul', 'ol', 'li',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'a', 'img',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'span',
    // GFM task lists (marked emits <input disabled type="checkbox"> for
    // "- [ ]"/"- [x]" list items) — issue #168. The afterSanitizeAttributes
    // hook below is what actually keeps this safe: it removes any <input>
    // that isn't type=checkbox and forces disabled on every survivor, so
    // this tag can't become a form-injection vector.
    'input',
  ],
  ALLOWED_ATTR: [
    'href', 'src', 'alt', 'title', 'class', 'target', 'rel',
    // GFM table column alignment (issue #168).
    'align',
    // GFM task-list checkboxes — locked down in the hook below.
    'type', 'checked', 'disabled',
  ],
};

if (typeof DOMPurify !== 'undefined') {
  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    // If a link DOMPurify lets through opens in a new tab, don't hand that
    // tab a window.opener reference back into this session.
    if (node.hasAttribute('target')) {
      node.setAttribute('rel', 'noopener noreferrer');
    }

    // 'input' is only on the allowlist for GFM task-list checkboxes. Every
    // other DOMPurify-permitted attribute (onclick etc. are already
    // rejected by ALLOWED_ATTR) still leaves room for a model-authored
    // <input type="text"> or an enabled checkbox to act as a form-injection
    // vector, so enforce the checkbox-and-disabled shape directly: strip
    // any <input> that isn't type=checkbox, and force disabled on every one
    // that survives (issue #168).
    if (node.nodeName === 'INPUT') {
      if (node.getAttribute('type') !== 'checkbox') {
        node.remove();
        return;
      }
      node.setAttribute('disabled', 'disabled');
    }
  });
}

/**
 * Render markdown to sanitized HTML. Every marked.parse() → innerHTML sink
 * in this file must go through here — never assign marked.parse() output to
 * innerHTML directly.
 * @param {string} text - raw markdown (possibly LLM-authored, untrusted)
 * @returns {string} sanitized HTML safe to assign to innerHTML
 */
function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text), MARKDOWN_SANITIZE_CONFIG);
}

const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');

let isStreaming = false;

// The last GM reply's raw markdown, read before the loop below renders it
// away, so a reloaded session can get its choice bar back (issue #342). Only
// a reply that is the last message in the history still holds an open
// question, so nothing is captured when the player spoke last.
const historyMessageDivs = messagesEl.querySelectorAll('.message');
const lastHistoryMessageDiv = historyMessageDivs[historyMessageDivs.length - 1] || null;
const lastHistoryReplyMarkdown =
  lastHistoryMessageDiv && lastHistoryMessageDiv.classList.contains('assistant')
    ? lastHistoryMessageDiv.querySelector('.message-content').textContent
    : '';

// Render markdown in all existing assistant messages on page load
document.querySelectorAll('[data-markdown]').forEach(el => {
  el.innerHTML = renderMarkdown(el.textContent);
});

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setStreaming(active) {
  isStreaming = active;
  sendBtn.disabled = active;
  // The box stays open while the GM works (issue #398): the player can write
  // their next move, and handleSubmit holds it until the turn ends.
  setWorkingStatus(active ? '' : null);
  if (!active) inputEl.focus();
}

/**
 * Say what the GM is doing, for as long as the turn lasts (issue #398).
 *
 * A turn that runs procedures can go a minute or more without new text, and
 * a greyed-out box with nothing else on screen read as frozen. The line is
 * part of the page and is emptied when the turn ends, never on a timer.
 *
 * The phrase may be one the server sent mid-turn (issue #569), so it is
 * always written as text: whatever it says, this line never renders markup.
 *
 * @param {string|null} activity - what the GM is doing now, '' for no
 *   detail, or null when the turn is over.
 */
function setWorkingStatus(activity) {
  const status = document.getElementById('gm-status');
  if (!status) return;
  if (activity === null) {
    status.textContent = '';
    status.hidden = true;
    return;
  }
  status.textContent = activity
    ? `The Game Master is working: ${activity}`
    : 'The Game Master is working';
  status.hidden = false;
}

function addMessage(role, content, opts = {}) {
  // Clear empty state if present
  const empty = messagesEl.querySelector('.empty-state');
  if (empty) empty.remove();

  // A choice bar only makes sense under the last message; whatever is being
  // added now — the player's pick, a typed action, a command's echo, an
  // error — supersedes it (issue #342).
  clearChoiceBar();

  const msgDiv = document.createElement('div');
  msgDiv.className = `message ${role}`;

  const roleDiv = document.createElement('div');
  roleDiv.className = 'message-role';
  roleDiv.textContent = role === 'user' ? 'You' : 'GM';

  const contentDiv = document.createElement('div');
  contentDiv.className = 'message-content';

  if (opts.isMarkdown && content) {
    contentDiv.innerHTML = renderMarkdown(content);
  } else {
    contentDiv.textContent = content;
  }

  msgDiv.appendChild(roleDiv);
  msgDiv.appendChild(contentDiv);
  messagesEl.appendChild(msgDiv);
  scrollToBottom();

  return contentDiv;
}

/**
 * Send one player turn: show it, remember it, stream the reply.
 *
 * Every path that speaks for the player goes through here (issue #339,
 * item 11) so that a failed turn has something to hand back. `restoreText`
 * is what would have to be typed to make this turn again — for a slash
 * command that is the command line itself, which handleSubmit re-parses
 * exactly as it did the first time.
 *
 * @param {string} displayText - the line to show in the transcript
 * @param {object} body - the stream endpoint's request body
 * @param {string} [restoreText] - what to put back in the input on failure;
 *   defaults to the displayed line
 */
function sendUserTurn(displayText, body, restoreText) {
  const contentDiv = addMessage('user', displayText);
  inFlightUserMessage = {
    text: restoreText === undefined ? displayText : restoreText,
    displayText,
    body,
    div: contentDiv,
    restore: true,
  };
  streamMessage(body);
}

function addThinking() {
  const empty = messagesEl.querySelector('.empty-state');
  if (empty) empty.remove();

  const div = document.createElement('div');
  div.className = 'thinking-indicator';
  div.id = 'thinking';
  div.textContent = 'Thinking';
  messagesEl.appendChild(div);
  scrollToBottom();
}

function removeThinking() {
  const el = document.getElementById('thinking');
  if (el) el.remove();
}

// ---------- GM choice prompts (issue #342) ----------
//
// A GM reply that ends in a numbered list is offered back to the player as
// buttons, plus a "Something else…" button that hands focus back to the text
// input. The parser is deliberately conservative: a false negative costs the
// player one typed digit, a false positive dresses a recap or a rules list
// up as a menu.

// `N. text` or `N) text`, any indentation, trailing whitespace ignored.
const CHOICE_LINE = /^\s*(\d+)([.)])\s+(\S.*?)\s*$/;
const CHOICE_LABEL_MAX_LENGTH = 80;
const CHOICE_OTHER_LABEL = 'Something else…';
// A last numbered item that only offers "something else" (issue #384). The
// model writes one about one reply in five instead of saying choices are not
// exhaustive; the bar's own "Something else…" button already is that item,
// so it is not also offered as a numbered option.
const CHOICE_ESCAPE_ITEM = new RegExp(
  '^(?:or\\s+)?(?:do\\s+)?(?:something|anything)\\s+else\\b'
  + '|^other(?:\\s+action)?\\W*$'
  + '|^describe\\s+(?:your\\s+own|another|any\\s+other)\\b',
  'i',
);

/**
 * Extract the numbered choices a GM reply ends with.
 *
 * Accepts only a contiguous run of numbered lines that closes the message:
 * numbered 1..N in order, one marker style (`.` or `)`) throughout, at least
 * two options, nothing but whitespace after the last one. A list followed by
 * more prose, a list starting anywhere but 1, a lone `1.` line, or blank
 * lines between items all return [] and the reply stays plain text.
 * @param {string} markdown - the reply's final markdown (model output)
 * @returns {string[]} option texts in order; [] when this is not a choice prompt
 */
function parseChoicePrompt(markdown) {
  if (typeof markdown !== 'string') return [];
  const lines = markdown.replace(/\s+$/, '').split('\n');
  const options = [];
  let marker = null;
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const match = CHOICE_LINE.exec(lines[index]);
    if (!match) break;
    if (marker === null) marker = match[2];
    if (match[2] !== marker) return [];
    options.unshift({ number: Number(match[1]), text: match[3] });
  }
  if (options.length < 2) return [];
  const numberedInOrder = options.every(
    (option, position) => option.number === position + 1,
  );
  if (!numberedInOrder) return [];
  const texts = options.map((option) => option.text);
  if (CHOICE_ESCAPE_ITEM.test(plainChoiceText(texts[texts.length - 1]))) {
    texts.pop();
  }
  return texts.length >= 2 ? texts : [];
}

/**
 * The option text as plain words: strong/bold and code markers the model
 * likes to wrap options in are dropped so neither the button nor the
 * transcript line reads `**Bribe the guard**`.
 * @param {string} text - one option's markdown
 * @returns {string}
 */
function plainChoiceText(text) {
  return text.replace(/\*\*|__|`/g, '').trim();
}

/**
 * Button label for an option: the plain text, cut to ~80 characters.
 * @param {string} text - one option's markdown
 * @returns {string}
 */
function choiceLabel(text) {
  const plain = plainChoiceText(text);
  if (plain.length <= CHOICE_LABEL_MAX_LENGTH) return plain;
  return `${plain.slice(0, CHOICE_LABEL_MAX_LENGTH - 1).trimEnd()}…`;
}

function clearChoiceBar() {
  messagesEl.querySelectorAll('.choice-bar').forEach((bar) => bar.remove());
}

/**
 * Send one of the offered choices as the player's message. Number and text
 * both go, so the transcript reads "2. Bribe the guard" rather than "2".
 * @param {number} number - 1-based option number
 * @param {string} text - the option's markdown
 */
function sendChoice(number, text) {
  if (isStreaming) return;
  const message = `${number}. ${plainChoiceText(text)}`;
  sendUserTurn(message, { session_id: sessionId, message });
}

/**
 * Render the choice bar under a GM message when its reply ends in a numbered
 * list; otherwise leave the message as it is. Only one bar exists at a time.
 * Every label is set through textContent — option text is model output.
 * @param {Element} messageDiv - the `.message.assistant` wrapper
 * @param {string} markdown - the reply's final markdown
 */
function offerChoices(messageDiv, markdown) {
  clearChoiceBar();
  const options = parseChoicePrompt(markdown);
  if (options.length === 0) return;

  const bar = document.createElement('div');
  bar.className = 'choice-bar';
  bar.setAttribute('role', 'group');
  bar.setAttribute('aria-label', 'Choices');

  options.forEach((text, index) => {
    const number = index + 1;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'choice-btn';
    button.dataset.choice = String(number);

    const keyHint = document.createElement('span');
    keyHint.className = 'choice-key';
    keyHint.textContent = String(number);

    const label = document.createElement('span');
    label.className = 'choice-label';
    label.textContent = choiceLabel(text);

    const plain = plainChoiceText(text);
    if (label.textContent !== plain) button.title = plain;

    button.append(keyHint, label);
    button.addEventListener('click', () => sendChoice(number, text));
    bar.appendChild(button);
  });

  const other = document.createElement('button');
  other.type = 'button';
  other.className = 'choice-btn choice-btn-other';
  other.textContent = CHOICE_OTHER_LABEL;
  other.addEventListener('click', () => {
    clearChoiceBar();
    inputEl.focus();
  });
  bar.appendChild(other);

  messageDiv.appendChild(bar);
  scrollToBottom();
}

// Number keys 1..N pick that option while the input is empty. Typing into
// any other field (the session picker, a side panel) is left alone.
// The message box is a textarea, so a multi-paragraph action can be typed
// (#716, low finding). A textarea does not submit its form on Enter the way
// the single-line input did, so the two keys are bound here: Enter sends,
// Shift+Enter makes a new line. The box grows with what is in it, up to a
// cap, and shrinks back when it is emptied.
const MAX_INPUT_ROWS_IN_PIXELS = 160;

/**
 * Fit the message box to its contents, within a cap.
 * @param {HTMLTextAreaElement} box - The message box.
 */
function fitMessageBox(box) {
  box.style.height = 'auto';
  box.style.height = `${Math.min(box.scrollHeight, MAX_INPUT_ROWS_IN_PIXELS)}px`;
}

if (inputEl && inputEl.tagName === 'TEXTAREA') {
  inputEl.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    const form = inputEl.closest('form');
    if (form) form.requestSubmit();
  });
  inputEl.addEventListener('input', () => fitMessageBox(inputEl));
  fitMessageBox(inputEl);
}

document.addEventListener('keydown', (event) => {
  if (isStreaming || event.altKey || event.ctrlKey || event.metaKey) return;
  if (!/^[1-9]$/.test(event.key)) return;
  const target = event.target;
  const inAnotherField =
    target !== inputEl
    && target instanceof Element
    && target.closest('input, textarea, select, [contenteditable="true"]') !== null;
  if (inAnotherField || inputEl.value !== '') return;
  const button = messagesEl.querySelector(
    `.choice-bar .choice-btn[data-choice="${event.key}"]`,
  );
  if (!button) return;
  event.preventDefault();
  button.click();
});

// ---------- when a turn fails (issue #339, item 11) ----------
//
// Three things used to go wrong together when the model was slow, down, or
// answered with something that was not JSON. The player's message was gone:
// handleSubmit clears the input, the send failed, and nothing was persisted
// server-side, so what they had written existed nowhere. The failure arrived
// dressed as a GM bubble, which put words in the Game Master's mouth and
// left them in the transcript. And a non-JSON body (a proxy's HTML error
// page, an empty 502) made `resp.json()` throw inside the handler, so the
// player read "Unexpected token < in JSON" instead of what happened.
//
// Now: the reason is a persistent notice, not a GM turn; the message goes
// back in the box exactly as typed, ready to send again; and the body is
// only parsed as JSON if it parses.

/** The user message this turn is carrying, so a failure can hand it back. */
let inFlightUserMessage = null;

/**
 * Read a failed response's reason without assuming it is JSON.
 * @param {Response} resp
 * @returns {Promise<string>} the server's message, or a plain description
 */
async function failureReason(resp) {
  try {
    const data = await resp.json();
    if (data && data.error) return data.error;
  } catch {
    // Not JSON: a gateway's HTML error page, or an empty body. The status
    // is the honest thing to say, and it is what the player can act on.
  }
  return resp.statusText
    ? `The Game Master could not answer (${resp.status} ${resp.statusText}).`
    : `The Game Master could not answer (${resp.status}).`;
}

/**
 * Take on the session id the server named, and keep it in the address bar.
 *
 * @param {number} id - the session this page's turns now belong to
 * @param {{name?: string, started?: string}} [details] - what the server
 *   calls it and when it started, so the rail can show it at once (#704)
 */
function adoptSessionId(id, details) {
  if (!id || id === sessionId) return;
  sessionId = id;
  // The turn has a session now, so the server can find it for any page
  // showing that session; this tab no longer has to hold it (issue #650).
  forgetOwnTurn();
  const url = new URL(window.location);
  url.searchParams.set('session', sessionId);
  history.replaceState(null, '', url);
  showNewSessionInRail(id, details);
}

/**
 * Put a session the current turn just created into the rail, and swap the
 * "a new session takes its name from your first message" line for the
 * session-name field (issue #704).
 *
 * Both were left at their pre-send state until the next full page load: the
 * rail showed only "+ New" though a session existed, and the panel went on
 * describing something that had already happened. Nothing here is transient
 * -- the entry is the session, and it stays.
 *
 * @param {number} id - The new session's id.
 * @param {{name?: string, started?: string}} [details] - The server's name
 *   for it and its creation instant, ISO-8601.
 */
function showNewSessionInRail(id, details) {
  const list = document.getElementById('session-list');
  if (!list || list.querySelector(`[data-id="${id}"]`)) return;
  const name = (details && details.name) || 'New Session';
  const started = (details && details.started) || new Date().toISOString();

  const item = document.createElement('li');
  item.className = 'session-item active';
  item.dataset.id = String(id);

  const link = document.createElement('a');
  link.href = `?session=${encodeURIComponent(id)}`;
  const nameSpan = document.createElement('span');
  nameSpan.className = 'session-name';
  nameSpan.textContent = name;
  const meta = document.createElement('span');
  meta.className = 'session-meta';
  const when = document.createElement('time');
  when.className = 'local-time';
  when.setAttribute('datetime', started);
  when.setAttribute('data-local-time-style', 'date');
  when.textContent = started.slice(0, 10);
  const counted = document.createElement('span');
  counted.className = 'session-count';
  counted.textContent = '1 msg';
  meta.append(when, document.createTextNode(' \u00b7 '), counted);
  link.append(nameSpan, meta);

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'session-delete-btn';
  remove.title = 'Delete session';
  remove.setAttribute('aria-label', `Delete session ${name}`);
  remove.addEventListener('click', () => deleteSession(id, name, when.textContent));

  item.append(link, remove);
  list.prepend(item);
  // local-time.js reformats a timestamp into the viewer's zone; this one
  // arrived after load, so it is asked for explicitly.
  if (typeof window.applyLocalTimes === 'function') window.applyLocalTimes(item);

  const placeholder = document.querySelector('.session-name-none');
  if (placeholder) {
    const field = document.createElement('div');
    field.className = 'session-name-field';
    field.dataset.field = 'name';
    const label = document.createElement('label');
    label.className = 'session-name-label';
    label.setAttribute('for', 'session-name');
    label.textContent = 'Session';
    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'session-name';
    input.className = 'autosave session-name-input';
    input.value = name;
    input.autocomplete = 'off';
    input.dataset.sessionId = String(id);
    field.append(input);
    placeholder.replaceWith(label, field);
  }
}

/**
 * Send a failed turn again (issue #410).
 *
 * It goes to the session the page knows now, which a procedure or roll may
 * have created before the connection went, and says it is a retry, so the
 * server carries on from what it had already run instead of running it twice.
 *
 * @param {object} pending - the in-flight turn failTurn was handed
 * @returns {boolean} true when the turn was sent
 */
function retryTurn(pending) {
  if (isStreaming) {
    uiNotify('Wait for the Game Master to finish this turn, then retry.');
    return false;
  }
  if (inputEl.value === pending.text) inputEl.value = '';
  sendUserTurn(pending.displayText,
               { ...pending.body, session_id: sessionId, retry: true },
               pending.text);
  return true;
}

/**
 * Say why a turn failed, with a Retry button when it can be sent again.
 *
 * The notice stays until Retry or its dismiss button is used; nothing takes
 * it away on a timer.
 *
 * @param {string} message - what went wrong, in the player's words
 * @param {Function|null} retry - sends the turn again; returns true if it did
 */
function showFailure(message, retry) {
  if (!retry || typeof window.showNotice !== 'function') {
    uiNotify(message);
    return;
  }
  const notice = window.showNotice(message, 'error');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'notice-retry';
  button.textContent = 'Retry';
  button.addEventListener('click', () => {
    if (retry()) notice.remove();
  });
  notice.insertBefore(button, notice.querySelector('.notice-dismiss'));
}

/**
 * Write the server's notices for a turn into the chat (issues #374, #553).
 *
 * The server is speaking here, not the Game Master, so each notice is its own
 * block rather than a GM bubble (issue #339, item 11), and it stays put:
 * nothing dismisses it on a timer. Text, never markup — a notice can quote
 * what the model or the player wrote.
 *
 * @param {Element|null} where - the element the notices are appended to
 * @param {Array<string>|undefined} notices - what the server has to say
 */
function showNotices(where, notices) {
  if (!where || !Array.isArray(notices)) return;
  for (const notice of notices) {
    if (!notice) continue;
    const warning = document.createElement('div');
    warning.className = 'grounding-notice';
    warning.setAttribute('role', 'status');
    warning.textContent = `⚠️ ${notice}`;
    where.appendChild(warning);
  }
}

/**
 * End a turn that failed: say why, and give the player their message back.
 *
 * @param {string} reason - what went wrong, in the player's words
 * @param {boolean} recoverable - true when nothing of the reply had arrived,
 *   so the typed message can be restored and its bubble withdrawn. A turn
 *   that failed part-way keeps what streamed: the words are on screen and
 *   deleting them would be a second loss.
 */
function failTurn(reason, recoverable) {
  removeThinking();
  const pending = inFlightUserMessage;
  inFlightUserMessage = null;
  const retry = pending && pending.body ? () => retryTurn(pending) : null;
  if (recoverable && pending && pending.restore) {
    if (pending.div && pending.div.parentElement) {
      pending.div.parentElement.remove();
    }
    if (inputEl.value.trim()) {
      // The player already wrote their next move while the GM worked
      // (issue #398); putting the failed one back would erase it.
      showFailure(`${reason} Your message was: ${pending.text}`, retry);
    } else {
      inputEl.value = pending.text;
      showFailure(`${reason} Your message is back in the box — send it again.`, retry);
    }
  } else {
    showFailure(reason, retry);
  }
  setStreaming(false);
  if (recoverable && pending && pending.restore) { inputEl.focus(); }
}

/**
 * Say that a turn is still running on the server, or clear the line (issue #515).
 *
 * Part of the page, like the working line: it stays until the reply lands or
 * the turn fails, never on a timer.
 *
 * @param {string|null} text - what to say, or null to clear it
 */
function setTurnRunning(text) {
  const line = document.getElementById('gm-turn-running');
  if (!line) return;
  line.textContent = text || '';
  line.hidden = !text;
}

/** Show the session a finished turn saved into, from the server's own history. */
function showFinishedTurn(finishedSessionId) {
  const target = finishedSessionId || sessionId;
  window.location.href = target
    ? `${window.location.pathname}?session=${encodeURIComponent(target)}`
    : window.location.pathname;
}

// Where this tab remembers a turn it started before there was a session to
// pin it to (issue #650).
//
// A turn sent from a blank page keeps `session_id = NULL` for the whole of
// its run, so the server cannot tell which page started it. It used to hand
// every blank page whatever session-less turn the player had running: a
// second tab opened to start a different game was read-only until the first
// tab's turn landed, and was then navigated into the first tab's session.
// sessionStorage belongs to one tab, so the page that sent the turn is the
// only one that waits for it, and a blank tab is free to start a new game —
// which is what the server already allowed, since claim_turn never refuses a
// session-less turn. Once the session exists the server owns the resume
// again: a session's turn belongs to the session, whichever page shows it.
const OWN_TURN_KEY = 'gmOwnSessionlessTurn';

/** This tab's session-less turn, or null. Storage may be barred; say so. */
function ownTurn() {
  try {
    const held = window.sessionStorage.getItem(OWN_TURN_KEY);
    return held ? JSON.parse(held) : null;
  } catch (err) {
    console.warn(`Could not read this tab's running GM turn: ${err.message}`);
    return null;
  }
}

/**
 * Remember a turn this tab started before its session existed (issue #650).
 *
 * @param {number|string} turnId - the turn the server named in X-GM-Turn
 * @param {string} message - what was sent, to say what is being waited for
 */
function rememberOwnTurn(turnId, message) {
  try {
    window.sessionStorage.setItem(OWN_TURN_KEY, JSON.stringify({ id: turnId, message }));
  } catch (err) {
    // Only the resume after a reload is lost; the turn itself is the
    // server's and runs to the end regardless.
    console.warn(`Could not remember this tab's running GM turn: ${err.message}`);
  }
}

/** Forget it: it has landed, failed, or has a session of its own now. */
function forgetOwnTurn() {
  try {
    window.sessionStorage.removeItem(OWN_TURN_KEY);
  } catch (err) {
    console.warn(`Could not forget this tab's running GM turn: ${err.message}`);
  }
}

/**
 * Ask the server where a turn stands (issue #515).
 *
 * @param {number|string} turnId - the turn to ask about
 * @returns {Promise<object>} the server's `{state, session_id, error}`
 * @throws {Error} carrying `status` when the server answered with one
 */
async function fetchTurnState(turnId) {
  const url = TURN_URL_TEMPLATE.replace('{turn_id}', String(turnId));
  const resp = await fetch(url, { credentials: 'same-origin' });
  if (!resp.ok) {
    const failure = new Error(`status ${resp.status}`);
    failure.status = resp.status;
    throw failure;
  }
  return resp.json();
}

/**
 * Wait for a turn the server is running without this page (issue #515).
 *
 * The turn was started and runs to the end whether or not anyone listens, so
 * the page asks after it until it lands, then shows the session with the
 * reply in it. A turn that failed on the server takes the usual failed-turn
 * path: the reason, and the player's message handed back.
 *
 * @param {number|string} turnId - the running turn
 * @param {object|null} pending - the turn as sendUserTurn remembered it
 */
async function awaitRunningTurn(turnId, pending) {
  isStreaming = true;
  sendBtn.disabled = true;
  setWorkingStatus(null);
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, TURN_POLL_MS));
    let data;
    try {
      data = await fetchTurnState(turnId);
    } catch (err) {
      if (err.status === 404) {
        // There is no such turn to wait for. Going on asking would leave
        // the page locked for good, with Send disabled and nothing coming
        // (issue #650).
        forgetOwnTurn();
        setTurnRunning(null);
        inFlightUserMessage = pending;
        failTurn('That turn is no longer on the server.', true);
        return;
      }
      // The network the stream was lost on may still be down; the turn is
      // safe on the server either way, so the page keeps asking.
      console.warn(`Could not ask after GM turn ${turnId}: ${err.message}`);
      continue;
    }
    if (data.state === 'running') continue;
    forgetOwnTurn();
    setTurnRunning(null);
    if (data.state === 'finished') {
      showFinishedTurn(data.session_id);
      return;
    }
    inFlightUserMessage = pending;
    failTurn(`That turn did not finish: ${data.error || 'the server gave no reason'}.`, true);
    return;
  }
}

/**
 * Pick up the session-less turn this tab started, if it is still running.
 *
 * Asked before the page locks itself, so a tab that comes back to a turn
 * that finished, failed or was cleaned up while it was away is usable
 * straight away rather than waiting on something that will never arrive
 * (issue #650).
 *
 * @param {object} remembered - `{id, message}` as rememberOwnTurn stored it
 */
async function resumeOwnTurn(remembered) {
  let data;
  try {
    data = await fetchTurnState(remembered.id);
  } catch (err) {
    if (err.status === 404) {
      // The record is gone -- cleaned up, or never this player's. There is
      // nothing to come back for, so the page is simply usable.
      forgetOwnTurn();
      return;
    }
    // Something else is wrong, and the turn may well still be running. The
    // page stays usable and keeps the turn, so a later load can try again.
    console.warn(`Could not ask after GM turn ${remembered.id}: ${err.message}`);
    return;
  }
  if (data.state === 'finished') {
    forgetOwnTurn();
    showFinishedTurn(data.session_id);
    return;
  }
  if (data.state !== 'running') {
    forgetOwnTurn();
    showFailure(`That turn did not finish: ${data.error || 'the server gave no reason'}.`, null);
    return;
  }
  setTurnRunning(`The Game Master is still working on your last message ("${remembered.message}"). ` +
                 'The reply will appear here when it is ready.');
  awaitRunningTurn(remembered.id, {
    text: remembered.message,
    displayText: remembered.message,
    body: { session_id: null, message: remembered.message },
    div: null,
    restore: true,
  });
}

/**
 * The page lost a stream the server had already started (issue #515).
 *
 * Not a failure: the turn carries on without the page. The words so far stay
 * on screen, and the reply appears when the turn is saved.
 *
 * @param {number|string} turnId - the turn the lost stream belonged to
 */
function turnLostFromView(turnId) {
  removeThinking();
  const pending = inFlightUserMessage;
  inFlightUserMessage = null;
  setTurnRunning('The connection to the Game Master dropped, but your turn is still ' +
                 'running. The reply will appear here when it is ready.');
  awaitRunningTurn(turnId, pending);
}

async function streamMessage(body) {
  setTurnRunning(null);
  setStreaming(true);
  addThinking();

  let contentDiv = null;
  let fullText = '';
  // A GM roll (issue #363) closes the paragraph the model was writing and
  // opens a fresh one after the dice, so the transcript reads prose → dice →
  // prose. Once any have landed the DOM already holds the whole reply in
  // pieces, so the done frame must not re-render it as one block.
  let gmRollCount = 0;

  /**
   * Show server-computed chip lines, then continue the reply beneath them.
   *
   * @param {string[]} lines Chip text, already formatted by the server.
   * @returns {Element} The block the GM's narration carries on in.
   */
  function showServerChips(lines) {
    if (!contentDiv) {
      removeThinking();
      contentDiv = addMessage('assistant', '');
    }
    const parent = contentDiv.parentElement;
    for (const line of lines) {
      if (!line) continue;
      gmRollCount += 1;
      const chip = document.createElement('div');
      chip.className = 'dice-roll';
      chip.textContent = line;
      parent.appendChild(chip);
    }
    // The narration that follows belongs after the chips, so the GM carries
    // on in a new block rather than above the outcome it was handed.
    const nextDiv = document.createElement('div');
    nextDiv.className = 'message-content';
    parent.appendChild(nextDiv);
    fullText = '';
    scrollToBottom();
    return nextDiv;
  }

  // The turn the server started for this send, from the response header, so
  // a stream lost after that is known to be still running (issue #515).
  let turnId = null;

  // The largest elapsed count the server has sent this turn (issue #569), so
  // the seconds on the status line only ever climb.
  let elapsedShown = 0;

  try {
    const resp = await postWithCsrf(STREAM_URL, csrfToken(),
                                    { ...body, mode: SESSION_MODE });

    if (!resp.ok) {
      failTurn(await failureReason(resp), true);
      return;
    }
    if (resp.headers && typeof resp.headers.get === 'function') {
      turnId = resp.headers.get('X-GM-Turn');
      // A turn sent from a blank page has no session to be found by, so this
      // tab remembers it and nobody else does (issue #650).
      if (turnId && !sessionId) rememberOwnTurn(turnId, body.message || '');
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE events
      const lines = buffer.split('\n');
      buffer = lines.pop(); // Keep incomplete line in buffer

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const jsonStr = line.slice(6);
        let data;
        try {
          data = JSON.parse(jsonStr);
        } catch {
          continue;
        }

        // A procedure or roll can create the session mid-turn, and its frame
        // names it, so a turn that breaks after that retries into the same
        // session rather than a new one (issue #410).
        if (data.session_id) {
          adoptSessionId(data.session_id, {
            name: data.session_name,
            started: data.session_started,
          });
        }

        // A frame carrying an `error` key is a failure however short the
        // reason is: an exception raised with no arguments used to stream
        // {"error": ""}, which `if (data.error)` read as no failure at all,
        // and the page waited for a done frame that never comes (#663).
        if ('error' in data) {
          // A turn the time budget cut short says what the server had
          // already worked out for it (issue #553): those dice fell and that
          // ledger stands, so the notice goes into the chat beside the chips
          // rather than vanishing with the failure banner. It is written
          // where the reply was being written when there is one, and at the
          // end of the chat when the turn was cut before a word arrived.
          showNotices(contentDiv ? contentDiv.parentElement : messagesEl, data.notices);
          failTurn(data.error || 'The Game Master stopped without saying why.',
                   contentDiv === null);
          return;
        }

        // What the GM is doing when nothing is arriving to show (issue #569):
        // planning, running a step, or writing (issue #590). The words are the
        // server's own and are shown as text, never as markup. A frame
        // carrying `elapsed` says how long the turn has been going, which is
        // how a slow plan shows it is alive.
        if (data.status) {
          let working = data.status;
          const seconds = Math.floor(Number(data.elapsed));
          if (Number.isFinite(seconds) && seconds > 0) {
            if (seconds > elapsedShown) elapsedShown = seconds;
            working = `${working}, ${elapsedShown}s`;
          }
          setWorkingStatus(working);
        }

        if (data.token) {
          if (!contentDiv) {
            removeThinking();
            contentDiv = addMessage('assistant', '');
          }
          fullText += data.token;
          setWorkingStatus('writing');
          contentDiv.innerHTML = renderMarkdown(fullText);
          scrollToBottom();
        }

        // The server rolled dice the GM asked for mid-reply (issue #363).
        // The line is built server-side and shown verbatim — textContent,
        // never innerHTML, and never a number this page worked out.
        if (data.gm_roll) {
          contentDiv = showServerChips([data.gm_roll.line || '']);
          setWorkingStatus('rolling dice');
        }

        // The server ran a rules procedure the GM asked for (issue #370):
        // a year of prior experience and its survival check. Same chips, same
        // rule — every line is built server-side and shown verbatim, so a
        // death is visible as dice, modifier, target and verdict rather than
        // arguable from the prose beside it.
        if (data.procedure) {
          contentDiv = showServerChips(data.procedure.lines || []);
          setWorkingStatus(data.procedure.label || 'running a rules procedure');
          // The step changed a character's record as it ran (issue #571), so
          // the side panel refetches it now rather than after the reply.
          document.dispatchEvent(new CustomEvent('gm-stream-procedure', { detail: data }));
        }

        if (data.done) {
          if (!contentDiv) {
            removeThinking();
            contentDiv = addMessage('assistant', '');
          }
          if (gmRollCount === 0) {
            // Final render with clean response
            contentDiv.innerHTML = renderMarkdown(data.full_response || fullText);
          } else {
            // The reply is already on screen in pieces around its dice; only
            // the block being written when the stream ended needs re-rendering.
            contentDiv.innerHTML = renderMarkdown(fullText);
          }

          // A /roll done frame carries the authoritative dice the server
          // rolled (issue #175) — show the actual faces ahead of the GM's
          // narration, which may mangle or omit them. textContent, never
          // innerHTML: the expression echoes user input.
          if (data.roll) {
            const rollDiv = document.createElement('div');
            rollDiv.className = 'dice-roll';
            // gm.messages.player_roll_line writes the same words for a
            // reload (issue #838).
            rollDiv.textContent =
              `🎲 ${data.roll.expression}: [${data.roll.rolls}] = ${data.roll.total}`;
            contentDiv.parentElement.insertBefore(rollDiv, contentDiv);
          }
          // A hidden grounding block the server could not build this turn
          // (issue #374). The turn survived without it — better than the 500
          // that used to end the session — but the reply was written with
          // less than the usual grounding, and only the player can judge what
          // that costs, so it is said plainly. The server is speaking here,
          // not the Game Master, so it is its own block rather than a GM
          // bubble (issue #339, item 11), and it stays put: nothing dismisses
          // it on a timer.
          showNotices(contentDiv.parentElement, data.notices);
          offerChoices(contentDiv.parentElement, data.full_response || fullText);
          scrollToBottom();

          if (data.session_id) {
            refreshSessionAfterTurn(data.session_id, data.session_name, data.message_count);
          }

          // The side panel's tabs react to the finished reply — the Map tab
          // follows the `map` a done frame may carry (issue #343). One event
          // for all of them, after the URL holds the session id, so a
          // listener keying on the session sees the right one.
          document.dispatchEvent(new CustomEvent('gm-stream-done', { detail: data }));
        }
      }
    }
  } catch (err) {
    if (turnId) {
      // The server had started the turn, and it runs to the end without us
      // (issue #515): the page lost sight of it, nothing failed.
      turnLostFromView(turnId);
      return;
    }
    failTurn(`The connection to the Game Master broke: ${err.message}.`,
             contentDiv === null);
    return;
  }

  inFlightUserMessage = null;
  setStreaming(false);
}

function handleSubmit(e) {
  e.preventDefault();
  // Saving the transcript never touches the GM, so it works mid-turn (issue #397).
  const typed = inputEl.value.trim();
  if (/^\/review(\s|$)/i.test(typed)) {
    inputEl.value = '';
    sendForReview(typed.slice('/review'.length).trim());
    return false;
  }
  if (inputEl.value.trim().toLowerCase() === '/transcript') {
    inputEl.value = '';
    downloadTranscript();
    return false;
  }
  if (isStreaming) return false;

  const message = inputEl.value.trim();
  if (!message) return false;

  inputEl.value = '';

  // Check for slash commands
  if (message.startsWith('/')) {
    const parts = message.split(/\s+/, 2);
    const cmd = parts[0].toLowerCase();
    const arg = message.slice(parts[0].length).trim();

    if (cmd === '/roll') {
      const expr = arg || '3d6';
      sendUserTurn(`/roll ${expr}`,
                   { session_id: sessionId, message: expr, command: 'roll' });
      return false;
    }
    if (cmd === '/state') {
      sendUserTurn('/state', { session_id: sessionId, command: 'state' });
      return false;
    }
    if (cmd === '/rules') {
      if (!arg) {
        // The app telling the player how to use a command is not the Game
        // Master speaking (issue #339, item 11): it is a notice, and it
        // does not go in the transcript.
        uiNotify('Usage: /rules <question>', 'info');
        inputEl.value = message;
        return false;
      }
      sendUserTurn(`/rules ${arg}`,
                   { session_id: sessionId, message: arg, command: 'rules' });
      return false;
    }
    // Rename is what the button is called (issue #339, item 10); /save is
    // kept for the hands that learned it (issue #830).
    if (cmd === '/rename' || cmd === '/save') {
      promptSave(arg);
      return false;
    }
    if (cmd === '/fight') {
      // The Battle tab owns the fight (gm-panel-battle.js, issue #345):
      // it posts, reports in the tab, and reloads on the session.
      window.gmPanelBattle.startFight(arg);
      return false;
    }
    if (cmd === '/help') {
      showHelp();
      return false;
    }
  }

  sendUserTurn(message, { session_id: sessionId, message });

  return false;
}

async function sendCommand(command, defaultArg) {
  // Saving the transcript never touches the GM, so it works mid-turn (issue #397).
  if (command === 'transcript') {
    downloadTranscript();
    return;
  }
  // Sending for review reads what the server stored, so it works mid-turn too.
  if (command === 'review') {
    promptReview();
    return;
  }
  if (isStreaming) return;

  if (command === 'roll') {
    // The dice expression is asked for in the page's own panel rather than
    // the browser's prompt() (issue #339, item 9).
    const expr = await uiPrompt('Dice expression:', {
      defaultValue: defaultArg || '3d6',
      confirmLabel: 'Roll',
      placeholder: '3d6, 2d6+3',
    });
    if (!expr) return;
    sendUserTurn(`/roll ${expr}`,
                 { session_id: sessionId, message: expr, command: 'roll' });
  } else if (command === 'state') {
    sendUserTurn('/state', { session_id: sessionId, command: 'state' });
  }
}

/**
 * Download the whole session as a text file, for debugging (issue #389).
 *
 * Nothing is sent to the GM and nothing enters the chat: the file comes
 * from what the server stored, hidden entries and turn records included,
 * which is the one version of the conversation a model cannot misquote.
 * The page fetches the file and hands it to the browser to save, so it
 * stays where it is and can say whether the download worked.
 */
/**
 * Ask what Claude should look at, then send the session (issue #402).
 */
async function promptReview() {
  const note = await uiPrompt('What should Claude look at?', {
    defaultValue: '',
    confirmLabel: 'Send to Claude',
    placeholder: 'the twins merged, the death went unnoticed…',
  });
  if (note === null || note === undefined) return;
  sendForReview(note);
}

/**
 * Send this session to Claude for review, frozen as it stands (issue #402).
 *
 * The server stores the whole session with the note; a reviewer on the
 * Mac Studio picks it up within a minute or so. The notice says where the
 * findings will appear, and stays until the player closes it.
 *
 * @param {string} note - what Claude should look at; may be empty.
 */
async function sendForReview(note) {
  if (!CHAT_CONFIG.reviewUrl) {
    uiNotify('Only a Game Master can send a session to Claude.', 'info');
    return;
  }
  if (!sessionId) {
    uiNotify('There is nothing to send yet: this session has no messages.', 'info');
    return;
  }
  let response;
  try {
    response = await postWithCsrf(CHAT_CONFIG.reviewUrl, csrfToken(), { session_id: sessionId, note });
  } catch (err) {
    uiNotify(`The session could not be sent to Claude: ${err.message}.`);
    return;
  }
  if (!response.ok) {
    uiNotify(`The session could not be sent to Claude: ${await failureReason(response)}`);
    return;
  }
  const data = await response.json();
  uiNotify(
    `Sent to Claude as review #${data.review_id}. The findings will appear at ${data.url} once Claude has reviewed it.`,
    'info',
  );
}

/**
 * Where this session's transcript downloads from (issue #436).
 *
 * Filled in from the live session id rather than read from a URL fixed when
 * the page loaded, so a session this page started has one without a reload.
 *
 * @returns {string|null} the download URL, or null before any session exists
 */
function transcriptUrl() {
  if (!sessionId) return null;
  return CHAT_CONFIG.transcriptUrlTemplate.replace('{session_id}', String(sessionId));
}

async function downloadTranscript() {
  if (!transcriptUrl()) {
    uiNotify('There is nothing to download yet: this session has no messages.', 'info');
    return;
  }
  // Fetched and saved from the page (issue #397). Pointing the location at
  // the URL downloaded once and then did nothing in Safari, and the page
  // could never say whether it had worked; this way every press is a real
  // request whose outcome the player is told.
  let response;
  let blob;
  try {
    response = await fetch(transcriptUrl(), { credentials: 'same-origin' });
    if (!response.ok) {
      uiNotify(`The transcript could not be downloaded: the server answered ${response.status}.`);
      return;
    }
    blob = await response.blob();
  } catch (err) {
    uiNotify(`The transcript could not be downloaded: ${err.message}.`);
    return;
  }
  const disposition = response.headers.get('Content-Disposition') || '';
  const named = disposition.match(/filename="?([^";]+)"?/i);
  const filename = named ? named[1] : `gm-session-${sessionId}.txt`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Released once the browser has taken the file: revoking in the same tick
  // cancels the save in some browsers. Cleanup only, nothing on screen.
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  uiNotify(`Transcript saved as ${filename}.`, 'info');
}

// ---------- naming a session (issue #339, item 10) ----------
//
// The name lives in a field on the page and saves on blur like every other
// editable field in the studio. Everything below exists so the rest of the
// page agrees with it without a reload: the sidebar entry and the mobile
// picker are rendered from the same name, so they are updated in place when
// it changes. The old path renamed through prompt() and then called
// location.reload(), which threw away the confirmation it had just written
// and anything half-typed in the input.

const sessionNameField = document.getElementById('session-name');

/**
 * Show a session's new name everywhere the page already shows it.
 * textContent throughout: a session name is whatever its owner typed.
 * @param {number|string} id - the session's id
 * @param {string} name - the stored name, as the server confirmed it
 */
function applySessionName(id, name) {
  const key = String(id);
  const entry = document.querySelector(
    `.session-item[data-id="${key}"] .session-name`,
  );
  if (entry) entry.textContent = name;
  const option = document.querySelector(
    `#mobile-session-select option[value="${key}"]`,
  );
  if (option) {
    option.dataset.name = name;
    option.textContent = mobileOptionLabel(option);
  }
  // Looked up now, not at load: a session created by this page's first
  // message has a field the page made after it loaded (issue #704).
  const field = document.getElementById('session-name');
  if (field && field.dataset.sessionId === key) {
    field.value = name;
  }
}

/**
 * The mobile picker's words for a session, as the server renders them:
 * "name — date (count)".
 * @param {HTMLOptionElement} option - the session's option
 * @returns {string}
 */
function mobileOptionLabel(option) {
  const railName = document.querySelector(
    `.session-item[data-id="${option.value}"] .session-name`,
  );
  const name = option.dataset.name
    || (railName && railName.textContent)
    || option.textContent;
  const started = option.dataset.started;
  const count = option.dataset.messageCount;
  const dated = started ? `${name} \u2014 ${started}` : name;
  return count ? `${dated} (${count})` : dated;
}

/**
 * After a turn, show the session as the server now holds it (issue #829):
 * the name its first reply gave it and how many messages it has. A session
 * made before its first message -- by attaching a character -- was left
 * reading "New Session" and "0 msgs" beside a finished reply until the page
 * was reloaded. The name field is left alone while someone is typing in it.
 * @param {number|string} id - the session's id
 * @param {string} [name] - its stored name, from the done frame
 * @param {number} [count] - its visible messages, from the done frame
 */
function refreshSessionAfterTurn(id, name, count) {
  const key = String(id);
  if (name) {
    const field = document.getElementById('session-name');
    const typing = field && field.dataset.sessionId === key
      && document.activeElement === field;
    if (!typing) applySessionName(key, name);
  }
  if (typeof count !== 'number') return;
  const counted = document.querySelector(
    `.session-item[data-id="${key}"] .session-count`,
  );
  if (counted) counted.textContent = `${count} msg${count === 1 ? '' : 's'}`;
  const option = document.querySelector(
    `#mobile-session-select option[value="${key}"]`,
  );
  if (option) {
    option.dataset.messageCount = String(count);
    option.textContent = mobileOptionLabel(option);
  }
}

/**
 * Rename the active session and show the result in place.
 * @param {string} name - the new name
 * @returns {Promise<boolean>} whether the rename stuck
 */
async function renameSession(name) {
  try {
    const resp = await postWithCsrf(SAVE_URL, csrfToken(), {
      session_id: sessionId,
      name,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) {
      uiNotify(`Rename failed: ${data.error || resp.statusText}`);
      return false;
    }
    applySessionName(sessionId, data.name);
    return true;
  } catch (err) {
    uiNotify(`Rename failed: ${err.message}`);
    return false;
  }
}

/**
 * The Rename button, and `/save [name]`.
 *
 * With a name, it renames. Without one it sends the player to the name
 * field, which is the control that does this now — rather than opening a
 * dialog that duplicates it.
 * @param {string} [name] - the new name, when the command carried one
 */
async function promptSave(name) {
  if (!sessionId) {
    uiNotify(
      'There is no session yet — send a message first, and it will be named '
      + 'after what you said.',
      'info',
    );
    return;
  }
  if (name) {
    if (await renameSession(name)) {
      uiNotify(`Session renamed to "${name}".`, 'success');
    }
    return;
  }
  if (sessionNameField) {
    sessionNameField.focus();
    sessionNameField.select();
    return;
  }
  // A session created during this page load has no field yet (it renders on
  // the next load); ask for the name in the page's own panel meanwhile.
  const typed = await uiPrompt('Session name:', { confirmLabel: 'Rename' });
  if (!typed) return;
  if (await renameSession(typed)) {
    uiNotify(`Session renamed to "${typed}".`, 'success');
  }
}

// The name field autosaves through the shared loop (autosave.js), so its
// saving/saved/error states are the studio's, not a second set invented
// here. gm_save is addressed by session id, which is what buildBody says.
if (sessionNameField && typeof initAutosave === 'function') {
  initAutosave({
    updateUrl: SAVE_URL,
    buildBody: (field, value) => ({ session_id: sessionId, [field]: value }),
    onSuccess: (field, value, data) => applySessionName(sessionId, data.name || value),
  });
}

function newSession() {
  window.location.href = window.location.pathname;
}

/**
 * Delete a GM session (issue #115: the gm_delete endpoint had no UI caller).
 *
 * The confirmation names the date as well as the name (issue #705). A session
 * is named after its first message, so two games opened with the same opening
 * line produced two identical questions and which one was about to go was a
 * guess.
 *
 * @param {number} id - Session id (GMSession pk)
 * @param {string} name - Session name, for the confirmation prompt
 * @param {string} [started] - The session's date, as the rail shows it
 */
async function deleteSession(id, name, started) {
  const named = name ? `"${name}"` : 'this session';
  const label = started ? `${named}, started ${started},` : named;
  const confirmed = await uiConfirm(`Delete ${label}? This can't be undone.`, {
    danger: true,
    confirmLabel: 'Delete',
  });
  if (!confirmed) return;

  try {
    const resp = await postWithCsrf(DELETE_URL, csrfToken(), { session_id: id });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      uiNotify(`Delete failed: ${(data && data.error) || resp.statusText}`);
      return;
    }
    if (id === sessionId) {
      // Deleted the active session — drop back to a blank chat.
      window.location.href = window.location.pathname;
    } else {
      location.reload();
    }
  } catch (err) {
    uiNotify(`Delete failed: ${err.message}`);
  }
}

/**
 * The /help table, for the account reading it (issue #830).
 *
 * Built from what this page offers: /review only where the page carries the
 * review endpoint, which the server sets for a Game Master alone, so a
 * player is not offered a command that would refuse them. The rename row
 * names the command after the button, Rename.
 * @returns {string} markdown
 */
function helpTable() {
  const rows = [
    ['`/roll NdN`', 'Roll dice (e.g., /roll 3d6, /roll 2d6+3)'],
    ['`/state`', 'Ask the GM to summarize current game state'],
    ['`/transcript`', 'Download this whole session as a text file, for debugging'],
  ];
  if (CHAT_CONFIG.reviewUrl) {
    rows.push(['`/review <what to look at>`', 'Send this session to Claude for review']);
  }
  rows.push(
    ['`/rules <question>`', 'Precise rules lookup (slower)'],
    ['`/fight [foes]`', 'Take the fight to the hex board (name the foes, or let the GM)'],
    ['`/rename <name>` (or `/save`)', 'Rename this session'],
    ['`/help`', 'Show this help'],
  );
  const lines = rows.map(([command, what]) => `| ${command} | ${what} |`);
  return ['**Commands**', '', '| Command | Description |', '|---------|-------------|', ...lines]
    .join('\n');
}

function showHelp() {
  const help = helpTable();

  addMessage('assistant', help, { isMarkdown: true });
}

// A reloaded session whose last message is a GM choice prompt gets its bar
// back (issue #342). Runs here, after every definition it depends on.
if (lastHistoryReplyMarkdown) {
  offerChoices(lastHistoryMessageDiv, lastHistoryReplyMarkdown);
}

// A turn this session was still running when the page loaded (issue #515):
// the page was reloaded, or came back after losing the stream. Its reply is
// saved on the server whether or not the page watches; the page says so and
// shows it when it lands.
if (RUNNING_TURN) {
  setTurnRunning(`The Game Master is still working on your last message ("${RUNNING_TURN.message}"). ` +
                 'The reply will appear here when it is ready.');
  awaitRunningTurn(RUNNING_TURN.id, {
    text: RUNNING_TURN.message,
    displayText: RUNNING_TURN.message,
    body: { session_id: sessionId, message: RUNNING_TURN.message },
    div: null,
    restore: true,
  });
} else if (!sessionId) {
  // A blank page waits only for a turn it started itself (issue #650). The
  // server offers no running turn here, because a session-less turn names no
  // session and so cannot be told from another tab's.
  const remembered = ownTurn();
  if (remembered) resumeOwnTurn(remembered);
}

// Scroll to bottom on load
scrollToBottom();

// Export for testing (Node.js environment)
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    renderMarkdown,
    MARKDOWN_SANITIZE_CONFIG,
    deleteSession,
    streamMessage,
    parseChoicePrompt,
    CHOICE_LABEL_MAX_LENGTH,
    CHOICE_OTHER_LABEL,
    sendCommand,
    downloadTranscript,
    sendForReview,
    promptReview,
    handleSubmit,
    promptSave,
    renameSession,
    applySessionName,
    refreshSessionAfterTurn,
    failureReason,
    // sessionId is module-scoped now that it comes from #chat-config rather
    // than an inline <script>'s global, so read it through a getter — a
    // snapshot would miss the reassignment a /save or a first message makes.
    get sessionId() { return sessionId; },
  };
}
