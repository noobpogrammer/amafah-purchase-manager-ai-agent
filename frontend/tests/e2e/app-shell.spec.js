import { test, expect, setupAuthenticatedSession, setupUnauthenticatedSession } from './fixtures/test-base.js';

test.describe('App Shell & Routing', () => {
  test('should redirect unauthenticated users from protected routes to login', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();

    await page.goto('/team');
    await expect(page).toHaveURL(/\/login/);
  });

  test('should redirect authenticated users from login/signup to root dashboard', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/login');
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();

    await page.goto('/signup');
    await expect(page).toHaveURL('/');
  });

  test('should support direct page refresh across public and protected routes', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/forgot-password');
    await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeVisible();

    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/team');
    await expect(page.getByRole('heading', { name: 'Team & invites' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Team & invites' })).toBeVisible();
  });

  test('should handle browser back and forward navigation gracefully', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();

    // Navigate to team
    await page.getByRole('button', { name: 'Team' }).click();
    await expect(page).toHaveURL('/team');
    await expect(page.getByRole('heading', { name: 'Team & invites' })).toBeVisible();

    // Go back
    await page.goBack();
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();

    // Go forward
    await page.goForward();
    await expect(page).toHaveURL('/team');
    await expect(page.getByRole('heading', { name: 'Team & invites' })).toBeVisible();
  });

  test('should render profile load error message without crashing when ensureProfile fails', async ({ page }) => {
    page.allowConsoleError(/Database connection failed|Missing client_id|Failed to load resource|Error loading/);
    await setupAuthenticatedSession(page, 'admin');

    // Override profiles query to fail
    await page.route('**/rest/v1/profiles*', async (route) => {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'Database connection failed' }),
      });
    });

    await page.goto('/');
    await expect(page.locator('.msg.error')).toBeVisible();
  });
});
