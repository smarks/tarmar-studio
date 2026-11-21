/**
 * Axe, as the accessibility review measured the app (issues #696, #699, #701,
 * #707, #709, #715): every WCAG 2.1 AA violation, with a failure message that
 * names what broke. e2e/accessibility.spec.js holds whole pages to zero; a
 * spec that builds a control the pages do not show until play reaches it
 * (the True Magic controls, tarmar-studio #208) holds that control to zero
 * where it builds it.
 */

const AxeBuilder = require('@axe-core/playwright').default;

//: The rule sets the review used, and the ones WCAG 2.1 AA is made of.
const WCAG_AA = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

/**
 * Every wcag2a/aa + wcag21a/aa violation on the page as it stands, or within
 * the part of it ``include`` selects.
 * @param {import('@playwright/test').Page} page
 * @param {{include?: string}} [scope]
 * @returns {Promise<Array>} axe's violations, worst first as axe orders them.
 */
async function violations(page, { include } = {}) {
    let builder = new AxeBuilder({ page }).withTags(WCAG_AA);
    if (include) { builder = builder.include(include); }
    const results = await builder.analyze();
    return results.violations;
}

/**
 * A failure message that names what broke rather than only a count.
 * @param {Array} found - axe violations.
 * @returns {string}
 */
function describe(found) {
    return found
        .map((violation) => {
            const where = violation.nodes
                .map((node) => node.target.join(' '))
                .slice(0, 4)
                .join(', ');
            return `${violation.id} [${violation.impact}] x${violation.nodes.length}: ${where}`;
        })
        .join('\n');
}

module.exports = { WCAG_AA, violations, describe };
