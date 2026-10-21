/**
 * E2E tests for authentication flows.
 */

const { test, expect, TEST_USER, loginAs, logout } = require('./fixtures');
const { uniqueStamp } = require('./unique-name');

test.describe('Authentication', () => {
    test('shows login page', async ({ page }) => {
        await page.goto('/tarmar/accounts/login/');

        await expect(page.locator('h1')).toContainText('Login');
        await expect(page.locator('input[name="username"]')).toBeVisible();
        await expect(page.locator('input[name="password"]')).toBeVisible();
        await expect(page.locator('button[type="submit"]')).toBeVisible();
    });

    test('shows registration page', async ({ page }) => {
        await page.goto('/tarmar/accounts/register/');

        await expect(page.locator('h1')).toContainText('Register');
        await expect(page.locator('input[name="username"]')).toBeVisible();
        await expect(page.locator('input[name="password1"]')).toBeVisible();
        await expect(page.locator('input[name="password2"]')).toBeVisible();
    });

    test('shows error for invalid login', async ({ page }) => {
        await page.goto('/tarmar/accounts/login/');
        await page.fill('input[name="username"]', 'wronguser');
        await page.fill('input[name="password"]', 'wrongpass');
        await page.click('button[type="submit"]');

        // Should still be on login page with error
        await expect(page).toHaveURL(/\/login/);
        await expect(page.locator('.errors, .error')).toBeVisible();
    });

    test('successful login redirects to home', async ({ page }) => {
        await loginAs(page, TEST_USER);

        // Should be redirected away from login page
        await expect(page).not.toHaveURL(/\/login/);
    });

    test('logout redirects to home', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await logout(page);

        // Open menu to check for Login link
        await page.click('#menuToggle');
        await expect(page.locator('.menu a:has-text("Login")')).toBeVisible();
    });

    test('protected pages redirect to login when not authenticated', async ({ page }) => {
        await page.goto('/tarmar/accounts/profile/');

        // Should redirect to login
        await expect(page).toHaveURL(/\/login/);
    });

    test('characters page requires authentication', async ({ page }) => {
        await page.goto('/tarmar/characters/');

        // Should redirect to login
        await expect(page).toHaveURL(/\/login/);
    });
});

test.describe('Registration', () => {
    test('rejects mismatched passwords', async ({ page }) => {
        await page.goto('/tarmar/accounts/register/');
        // Use unique username to avoid "username exists" error
        const uniqueUsername = `testuser_${uniqueStamp()}`;
        await page.fill('input[name="username"]', uniqueUsername);
        await page.fill('input[name="password1"]', 'testpass123');
        await page.fill('input[name="password2"]', 'different123');
        await page.click('button[type="submit"]');

        // Should stay on register page (not redirected to home)
        await expect(page).toHaveURL(/\/register/);
    });

    test('rejects duplicate username', async ({ page }) => {
        await page.goto('/tarmar/accounts/register/');
        // Use existing test user username
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password1"]', 'newpassword123');
        await page.fill('input[name="password2"]', 'newpassword123');
        await page.click('button[type="submit"]');

        // Should stay on register page with error
        await expect(page).toHaveURL(/\/register/);
        await expect(page.getByText('username already exists')).toBeVisible();
    });
});

test.describe('Header identity block', () => {
    // Issue #206: on phones the identity block sat on top of the logo,
    // whose text had wrapped to two lines underneath it. Only a browser
    // proves layout geometry, so the guard lives here.

    /** True when two bounding boxes share any area. */
    function boxesIntersect(a, b) {
        return a.x < b.x + b.width && b.x < a.x + a.width
            && a.y < b.y + b.height && b.y < a.y + a.height;
    }

    test('stays clear of the logo at phone width', async ({ adminPage }) => {
        await adminPage.setViewportSize({ width: 390, height: 844 });
        await adminPage.goto('/tarmar/');

        // Measure the username-and-badges row, not just its container: the
        // bug was that row overflowing its shrunken container leftward over
        // the logo, while the container's own box still looked innocent.
        const logo = await adminPage.locator('header .logo').boundingBox();
        const identityUser = await adminPage.locator('.identity-user').boundingBox();
        expect(logo).not.toBeNull();
        expect(identityUser).not.toBeNull();
        expect(boxesIntersect(logo, identityUser)).toBe(false);

        // The logo itself holds one line — it no longer wraps under the
        // identity block. Wrapped, its box grows well past the 32px icon.
        expect(logo.height).toBeLessThan(40);

        // Nothing pushes the header wider than the phone.
        const scrollWidth = await adminPage.evaluate(
            () => document.documentElement.scrollWidth
        );
        expect(scrollWidth).toBeLessThanOrEqual(390);
    });

    test('shares the top row with the logo at desktop width', async ({ adminPage }) => {
        await adminPage.setViewportSize({ width: 1440, height: 900 });
        await adminPage.goto('/tarmar/');

        const logo = await adminPage.locator('header .logo').boundingBox();
        const identityUser = await adminPage.locator('.identity-user').boundingBox();
        expect(boxesIntersect(logo, identityUser)).toBe(false);
        // Same row: the identity block starts right of the logo's end.
        expect(identityUser.x).toBeGreaterThan(logo.x + logo.width);
        await expect(adminPage.locator('.identity-since')).toBeVisible();
    });
});
