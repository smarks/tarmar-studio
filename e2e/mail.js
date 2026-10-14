/**
 * The mail the dev server sends during a browser run (issue #929).
 *
 * Under DEBUG the site prints mail to runserver's console, which a spec cannot
 * read. playwright.config.js starts the server with the project's file
 * backend instead, pointed at this directory, so a spec can open the message
 * the site really sent -- the password-reset link included -- rather than
 * building the link itself. One directory per E2E_PORT, so two worktrees' runs
 * never read each other's mail.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

/**
 * The project's file backend (common/mail_backends.py), as settings.py's
 * EMAIL_BACKEND names it. Django's own appends messages sent in the same
 * second to one file when the allocator reuses a connection's address, and
 * takeMessageTo below removes a whole file, so one spec's take could throw
 * away another worker's message (issue #967). The project's names each
 * connection's file uniquely.
 */
const FILE_EMAIL_BACKEND = 'common.mail_backends.OneFilePerConnectionEmailBackend';

/**
 * The port this run's dev server listens on, which names its mail directory.
 * Digits only: the teardown removes the directory it names (issue #17), so a
 * value such as `../x` must stop the run here rather than reach the path.
 * @returns {string}
 */
function e2ePort() {
    const port = process.env.E2E_PORT || '8000';
    if (!/^\d+$/.test(port)) {
        throw new Error(`E2E_PORT must be a port number (digits only), not ${JSON.stringify(port)}`);
    }
    return port;
}

/** Where the dev server writes each message, one file per connection. */
const E2E_MAIL_DIRECTORY = path.join(os.tmpdir(), `tarmar-e2e-mail-${e2ePort()}`);

/**
 * A quoted-printable body as the reader sees it. Django sends text bodies
 * quoted-printable, which wraps long lines with a trailing `=` -- a reset link
 * among them -- and writes some bytes as `=XX`.
 * @param {string} encoded
 * @returns {string}
 */
function decodeQuotedPrintable(encoded) {
    const joined = encoded.replace(/=\r?\n/g, '');
    const bytes = [];
    for (let index = 0; index < joined.length; index += 1) {
        const escaped = joined.slice(index + 1, index + 3);
        if (joined[index] === '=' && /^[0-9A-F]{2}$/.test(escaped)) {
            bytes.push(parseInt(escaped, 16));
            index += 2;
        } else {
            bytes.push(...Buffer.from(joined[index], 'utf8'));
        }
    }
    return Buffer.from(bytes).toString('utf8');
}

/**
 * A message with its body decoded, headers left as written.
 * @param {string} raw
 * @returns {string}
 */
function decodedMessage(raw) {
    const split = raw.indexOf('\n\n');
    const headers = raw.slice(0, split);
    const body = raw.slice(split + 2);
    if (!/^Content-Transfer-Encoding: quoted-printable$/mi.test(headers)) {
        return raw;
    }
    return `${headers}\n\n${decodeQuotedPrintable(body)}`;
}

/**
 * The first message addressed to `address`, removed from the directory once
 * read so a rerun cannot find a stale one. Null while none has arrived.
 * @param {string} address
 * @returns {string | null} The message, headers as written and body decoded.
 */
function takeMessageTo(address) {
    if (!fs.existsSync(E2E_MAIL_DIRECTORY)) {
        return null;
    }
    for (const name of fs.readdirSync(E2E_MAIL_DIRECTORY)) {
        const file = path.join(E2E_MAIL_DIRECTORY, name);
        const message = fs.readFileSync(file, 'utf8');
        if (message.split('\n').includes(`To: ${address}`)) {
            fs.rmSync(file);
            return decodedMessage(message);
        }
    }
    return null;
}

/**
 * Remove a run's mail directory and every message left in it (issue #17).
 *
 * The dev server makes the directory when it sends its first message, and
 * nothing else ever removed it, so each run on a new E2E_PORT -- the gate
 * picks two from a span of 200 -- left a folder in the system temp directory.
 * The global teardown calls this once the specs are done, pass or fail. A
 * directory that was never made is not an error.
 * @param {string} [directory] Defaults to this run's E2E_MAIL_DIRECTORY; a
 *     test passes one of its own so it never empties the directory a running
 *     spec is reading.
 */
function removeMailDirectory(directory = E2E_MAIL_DIRECTORY) {
    fs.rmSync(directory, { recursive: true, force: true });
}

/**
 * What a spec says when the message never arrives, naming the usual cause.
 * @param {string} address
 * @returns {string}
 */
function missingMailMessage(address) {
    return `no message to ${address} in ${E2E_MAIL_DIRECTORY}. The likeliest `
        + 'cause is a dev server Playwright reused (reuseExistingServer) that was '
        + `started without EMAIL_BACKEND=${FILE_EMAIL_BACKEND} and EMAIL_FILE_PATH `
        + 'set to that directory, so its mail went to its console. Stop that '
        + 'server, or set E2E_PORT to a free port, and run again.';
}

module.exports = {
    E2E_MAIL_DIRECTORY,
    FILE_EMAIL_BACKEND,
    missingMailMessage,
    removeMailDirectory,
    takeMessageTo,
};
