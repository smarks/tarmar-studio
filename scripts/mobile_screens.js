// Dev utility: screenshot key pages at a mobile viewport for responsive work.
// Usage: node scripts/mobile_screens.js <outdir> [width]
const { chromium } = require("@playwright/test");
const { GATE_PASSPHRASE } = require("../e2e/gate");

const PAGES = {
  "reference-index": "/tarmar/reference/",
  "reference-world": "/tarmar/reference/the-world-of-tarmar/",
  "reference-table":
    "/tarmar/reference/public-rules/characters/prior-experience/skill-tracks/",
  "about": "/tarmar/about/",
  "home": "/tarmar/",
};

(async () => {
  const fs = require("fs");
  const outdir = process.argv[2] || "/tmp/tarmar_shots/before";
  const width = parseInt(process.argv[3] || "390", 10);
  fs.mkdirSync(outdir, { recursive: true });
  const base = "http://127.0.0.1:8000";

  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width, height: 844 },
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();

  await page.goto(base + "/", { waitUntil: "domcontentloaded" });
  const gate = page.locator('form input[name="passphrase"]');
  if ((await gate.count()) > 0) {
    await gate.fill(GATE_PASSPHRASE);
    await page.click('button[type="submit"]');
    await page.waitForLoadState("networkidle");
  }

  for (const [name, path] of Object.entries(PAGES)) {
    try {
      await page.goto(base + path, { waitUntil: "networkidle" });
      await page.screenshot({ path: `${outdir}/${name}.png`, fullPage: true });
      console.log("shot", name, "->", path);
    } catch (err) {
      console.log("FAILED", name, path, err.message);
    }
  }
  await browser.close();
})();
