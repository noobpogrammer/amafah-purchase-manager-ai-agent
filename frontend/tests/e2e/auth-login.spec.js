import { test, expect, setupUnauthenticatedSession } from './fixtures/test-base.js';
import { mockAdminProfile } from './fixtures/data.js';

test.describe('Authentication — Login', () => {
  test('should submit login form with valid credentials', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/login');

    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();

    await page.locator('#login-email').fill('admin@amafah.com');
    await page.locator('#login-password').fill('correctpassword123');

    // Intercept successful login
    await page.route('**/auth/v1/token*', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { 'access-control-allow-origin': '*' },
        body: JSON.stringify({
          access_token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTEiLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9.fake_sig',
          token_type: 'bearer',
          expires_in: 3600,
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          refresh_token: 'fake-refresh-token',
          user: {
            id: mockAdminProfile.id,
            email: mockAdminProfile.email,
            user_metadata: { role: 'admin', client_id: 'client-111' },
            app_metadata: { role: 'admin', client_id: 'client-111' },
          },
        }),
      });
    });

    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();
  });

  test('should display error message on invalid credentials', async ({ page }) => {
    page.allowConsoleError(/Failed to load resource|400|Invalid login credentials/);
    await setupUnauthenticatedSession(page);
    await page.goto('/login');

    await page.locator('#login-email').fill('admin@amafah.com');
    await page.locator('#login-password').fill('wrongpassword');

    // Intercept 400 error
    await page.route('**/auth/v1/token*', async (route) => {
      return route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'invalid_grant', error_description: 'Invalid login credentials', message: 'Invalid login credentials' }),
      });
    });

    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.locator('.msg.error')).toBeVisible();
    await expect(page.locator('.msg.error')).toContainText('Invalid login credentials');
  });

  test('should navigate to signup page', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/login');

    await page.getByRole('button', { name: 'Sign up' }).click();
    await expect(page).toHaveURL('/signup');
    await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible();
  });

  test('should navigate to forgot password page', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/login');

    await page.getByRole('button', { name: 'Forgot password?' }).click();
    await expect(page).toHaveURL('/forgot-password');
    await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeVisible();
  });
});
