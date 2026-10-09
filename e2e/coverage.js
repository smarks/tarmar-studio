/**
 * Coverage collection for Playwright E2E tests.
 * Uses Chrome's V8 coverage and converts to Istanbul format.
 */

const fs = require('fs');
const path = require('path');
const v8toIstanbul = require('v8-to-istanbul');

const COVERAGE_DIR = path.join(__dirname, '..', '.nyc_output');
const STATIC_JS_PATTERNS = ['/static/js/', '/accounts/static/'];

/**
 * Initialize coverage collection for a page.
 * @param {import('@playwright/test').Page} page
 */
async function startCoverage(page) {
    await page.coverage.startJSCoverage({ resetOnNavigation: false });
}

/**
 * Stop coverage collection and save results.
 * @param {import('@playwright/test').Page} page
 * @param {string} testName - Name of the test for the coverage file
 */
async function stopCoverage(page, testName) {
    const coverage = await page.coverage.stopJSCoverage();

    // Filter to only include our static JS files
    const filteredCoverage = coverage.filter(entry =>
        STATIC_JS_PATTERNS.some(pattern => entry.url.includes(pattern))
    );

    if (filteredCoverage.length === 0) {
        return;
    }

    // Ensure coverage directory exists
    if (!fs.existsSync(COVERAGE_DIR)) {
        fs.mkdirSync(COVERAGE_DIR, { recursive: true });
    }

    // Convert V8 coverage to Istanbul format
    const istanbulCoverage = {};

    for (const entry of filteredCoverage) {
        try {
            // Extract the file path from the URL
            const url = new URL(entry.url);
            const urlPath = url.pathname;

            // Map URL path to actual file path
            let filePath;
            if (urlPath.includes('/accounts/static/')) {
                filePath = path.join(__dirname, '..', 'accounts', 'static', urlPath.split('/accounts/static/')[1]);
            } else if (urlPath.includes('/static/')) {
                filePath = path.join(__dirname, '..', 'static', urlPath.split('/static/')[1]);
            } else {
                continue;
            }

            // Skip if file doesn't exist (might be external)
            if (!fs.existsSync(filePath)) {
                continue;
            }

            const converter = v8toIstanbul(filePath, 0, { source: entry.source });
            await converter.load();
            converter.applyCoverage(entry.functions);
            const istanbulData = converter.toIstanbul();

            // Merge into coverage object
            Object.assign(istanbulCoverage, istanbulData);
        } catch (err) {
            // Skip files that can't be converted
            console.warn(`Could not convert coverage for ${entry.url}: ${err.message}`);
        }
    }

    // Save coverage data
    if (Object.keys(istanbulCoverage).length > 0) {
        const safeName = testName.replace(/[^a-z0-9]/gi, '_');
        const coverageFile = path.join(COVERAGE_DIR, `coverage-${safeName}-${Date.now()}.json`);
        fs.writeFileSync(coverageFile, JSON.stringify(istanbulCoverage, null, 2));
    }
}

module.exports = {
    startCoverage,
    stopCoverage,
    COVERAGE_DIR
};
