/**
 * Bestiary E2E: browse the DM-gated catalog, generate a beast variant, and
 * see it rendered on its own (state-driven) detail page.
 *
 * Uses the admin fixture user, who has the DM role in global-setup.
 */

const { test, expect } = require('./fixtures');

test.describe('Bestiary', () => {
    test('DM browses the catalog and filters by category', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/bestiary/');
        await expect(adminPage.locator('h1')).toHaveText('Bestiary');

        // The catalog opens on its first page of cards (#674), not all 239.
        await expect(adminPage.locator('.beast-card').first()).toBeVisible();

        // Filter down to mammals: the wolf lives there, wherever the
        // unfiltered catalog's pages happen to put it.
        await adminPage.locator('form[action*="bestiary"] select[name="category"]').first()
            .selectOption('mammals');
        await adminPage.click('button:has-text("Filter")');
        await expect(adminPage.locator('.beast-card', { hasText: 'Dire Wolf' }).first())
            .toBeVisible();

        // Filter down to dragons only.
        await adminPage.locator('form[action*="bestiary"] select[name="category"]').first()
            .selectOption('dragons');
        await adminPage.click('button:has-text("Filter")');
        // Exact link text: a bare hasText would also match "Young Adult Dragon".
        await expect(adminPage.getByRole('link', { name: 'Adult Dragon', exact: true })).toBeVisible();
        await expect(adminPage.locator('.beast-card', { hasText: 'Dire Wolf' })).toHaveCount(0);
    });

    test('the catalog is paged, and every beast is reachable', async ({ adminPage }) => {
        // The page used to render all 239 catalog beasts at once, each
        // drawing an inline SVG plate, with an unbounded list of every
        // generated variant ever rolled beneath them (#674).
        await adminPage.goto('/tarmar/gm/bestiary/');
        const firstPageNames = await adminPage.locator('.beast-grid .beast-card h3')
            .allTextContents();
        expect(firstPageNames.length).toBeLessThan(239);

        const pager = adminPage.locator('nav.pagination').first();
        await expect(pager).toContainText('Page 1 of');
        await pager.getByRole('link', { name: /Next/ }).click();
        await adminPage.waitForURL(/[?&]page=2/);
        await expect(adminPage.locator('nav.pagination').first())
            .toContainText('Page 2 of');

        // A different page, not the same one re-rendered.
        const secondPageNames = await adminPage.locator('.beast-grid .beast-card h3')
            .allTextContents();
        expect(secondPageNames).not.toEqual(firstPageNames);

        // And the way back is there — neither end is a dead end.
        await adminPage.locator('nav.pagination').first()
            .getByRole('link', { name: /Previous/ }).click();
        await expect(adminPage.locator('nav.pagination').first())
            .toContainText('Page 1 of');

        // A category small enough to fit is offered no pager at all.
        await adminPage.goto('/tarmar/gm/bestiary/?category=constructs');
        await expect(adminPage.locator('nav.pagination')).toHaveCount(0);
    });

    test('turning the catalog keeps the category filter', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/bestiary/?category=insects_vermin');
        const pager = adminPage.locator('nav.pagination').first();
        await expect(pager).toContainText('Page 1 of');
        await pager.getByRole('link', { name: /Next/ }).click();
        expect(adminPage.url()).toContain('category=insects_vermin');
        await expect(
            adminPage.locator('select[name="category"]').first()
        ).toHaveValue('insects_vermin');
    });

    test('DM generates a beast and sees the saved variant rendered', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/bestiary/');
        await adminPage.fill('#generate-form input[name="name"]', 'Dire Wolf');
        await adminPage.click('#generate-form button:has-text("Generate beast")');

        // The POST saves the variant and redirects to its detail page.
        await adminPage.waitForURL(/\/tarmar\/gm\/bestiary\/dire-wolf-variant-\d+\/$/);
        await expect(adminPage.locator('h1')).toContainText('Dire Wolf (variant');
        await expect(adminPage.locator('.beast-provenance')).toContainText('generated variant');
        await expect(adminPage.locator('#beast-statblock')).toBeVisible();
        await expect(adminPage.locator('.beast-attacks')).toContainText('Bite');

        // State-driven: a reload renders the same saved beast from the DB.
        await adminPage.reload();
        await expect(adminPage.locator('h1')).toContainText('Dire Wolf (variant');
    });

    test('players cannot reach the bestiary', async ({ authenticatedPage }) => {
        const response = await authenticatedPage.goto('/tarmar/gm/bestiary/');
        expect(response.status()).toBe(403);
    });

    test('a beast page trails back to its filtered category', async ({ adminPage }) => {
        // Straight to the beast: the catalog can carry a second "Dire Wolf"
        // link in its generated-variants section, left by an earlier test.
        await adminPage.goto('/tarmar/gm/bestiary/dire-wolf/');

        // The trail says where this beast sits, and the last step is not a link.
        const trail = adminPage.locator('.breadcrumb');
        await expect(trail).toBeVisible();
        await expect(trail.locator('[aria-current="page"]')).toHaveText('Dire Wolf');

        // Clicking the category step lands on the catalog already filtered.
        // It is the deepest link: Tarmar > Bestiary > Category > (this page,
        // which is never a link).
        const category = trail.locator('a').last();
        const categoryLabel = await category.textContent();
        await category.click();
        await expect(adminPage.locator('h1')).toHaveText('Bestiary');
        expect(adminPage.url()).toContain('category=');
        await expect(
            adminPage.locator('select[name="category"]').first()
        ).not.toHaveValue('');
        expect(categoryLabel.trim().length).toBeGreaterThan(0);
    });

    test('generate another variant without returning to the catalog', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/bestiary/dire-wolf/');

        // From a catalog beast: generate a variant of it.
        await adminPage.click('#generate-panel button:has-text("variant")');
        const firstVariant = adminPage.url();
        await expect(adminPage.locator('.beast-provenance')).toContainText('generated variant');

        // The variant's own trail runs back through the beast it came from.
        const trail = adminPage.locator('.breadcrumb');
        await expect(trail.getByRole('link', { name: 'Dire Wolf', exact: true })).toBeVisible();

        // And the panel is still here, so another roll needs no back-tracking.
        await expect(adminPage.locator('#generate-panel')).toBeVisible();
        await adminPage.click('#generate-panel button:has-text("variant")');
        expect(adminPage.url()).not.toBe(firstVariant);
        await expect(adminPage.locator('.beast-provenance')).toContainText('generated variant');

        // The tweaked route: a random beast from the same category.
        await adminPage.click('#generate-panel button:has-text("Draw from")');
        await expect(adminPage.locator('.beast-provenance')).toContainText('generated variant');
    });

    test('every beast wears original silhouette art', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/bestiary/');

        // A plate on every card, drawn inline — no image requests at all,
        // because the art is SVG in the page (issue #193).
        const cards = await adminPage.locator('.beast-card').count();
        expect(cards).toBeGreaterThan(0);
        await expect(adminPage.locator('.beast-plate-card')).toHaveCount(cards);
        await expect(adminPage.locator('.beast-card img')).toHaveCount(0);

        // The plate is painted, not an empty frame.
        const painted = await adminPage.locator('.beast-plate-card').first()
            .evaluate((svg) => ({
                ground: svg.querySelector('rect').getAttribute('fill'),
                shapes: svg.querySelectorAll('g path[fill]:not([fill="none"])').length,
            }));
        expect(painted.ground).toMatch(/^hsl\(/);
        expect(painted.shapes).toBeGreaterThan(0);

        // A one-hex beast and a seven-hex beast are not drawn the same size.
        const scaleOf = (path) => adminPage.goto(path).then(() =>
            adminPage.locator('.beast-plate-detail g').getAttribute('transform'));
        const small = await scaleOf('/tarmar/gm/bestiary/lesser-parrot/');
        const large = await scaleOf('/tarmar/gm/bestiary/giant-roc/');
        const scale = (t) => parseFloat(/scale\(([\d.]+)\)/.exec(t)[1]);
        expect(scale(large)).toBeGreaterThan(scale(small));
    });

    test('a beast looks the same every time it is loaded', async ({ adminPage }) => {
        // The tint is identity, so a reload must not repaint the beast.
        const read = async () => {
            await adminPage.goto('/tarmar/gm/bestiary/dire-wolf/');
            return adminPage.locator('.beast-plate-detail rect').getAttribute('fill');
        };
        expect(await read()).toBe(await read());
    });
});

test('a generate error can be dismissed (issue #339, item 33)', async ({ adminPage: page }) => {
    await page.goto('/tarmar/gm/bestiary/');
    await page.fill('#generate-form input[name="name"]', 'no such beast anywhere');
    await page.click('#generate-form button[type="submit"]');

    const notice = page.locator('.notice-error');
    await expect(notice).toContainText('No catalog beast matches');
    await notice.getByRole('link', { name: 'Dismiss' }).click();

    await expect(page.locator('.notice-error')).toHaveCount(0);
    expect(page.url()).not.toContain('generate_error');
});

test('a DM deletes a generated variant after confirming (issue #339, item 21)', async ({ adminPage: page }) => {
    await page.goto('/tarmar/gm/bestiary/');
    await page.fill('#generate-form input[name="name"]', 'Dire Wolf');
    await page.click('#generate-form button:has-text("Generate beast")');
    await expect(page.locator('.beast-provenance')).toContainText('generated variant');
    const variantUrl = new URL(page.url()).pathname;
    const variantName = (await page.locator('h1').textContent()).trim();

    await page.click('button:has-text("Delete this variant")');
    const question = page.locator('.notice-confirm');
    await expect(question).toContainText(`Delete the generated variant ${variantName}?`);
    await question.locator('button:has-text("Delete")').click();

    await page.waitForURL('**/tarmar/gm/bestiary/');
    await expect(page.locator(`.generated-section a[href="${variantUrl}"]`)).toHaveCount(0);
    const response = await page.request.get(variantUrl);
    expect(response.status()).toBe(404);

    // The catalog beast it came from is still there, with no delete of its
    // own. Reached through its category, since the catalog is paged (#674).
    await page.goto('/tarmar/gm/bestiary/?category=mammals');
    await page.getByRole('link', { name: 'Dire Wolf', exact: true }).first().click();
    await expect(page.locator('h1')).toHaveText('Dire Wolf');
    await expect(page.locator('form.beast-delete')).toHaveCount(0);
});
