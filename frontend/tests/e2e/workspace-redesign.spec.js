import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

for (const viewport of [{ width: 1440, height: 1000 }, { width: 768, height: 1024 }, { width: 375, height: 812 }]) {
  test(`workspace navigation and core actions stay available at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Procurement overview' })).toBeVisible();
    await page.screenshot({ path: `/private/tmp/amafah-workspace-${viewport.width}.png`, fullPage: true });
    const nav = page.getByRole('navigation', { name: 'Main navigation' });
    for (const name of ['Overview', 'Requests & tracking', 'Suppliers', 'Quotes & rankings', 'Conversations', 'Agent attention', 'Daily reports']) {
      await expect(nav.getByRole('button', { name, exact: name !== 'Agent attention' })).toBeVisible();
    }
    await nav.getByRole('button', { name: 'Suppliers', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Supplier Directory' })).toBeVisible();
    await expect(nav.getByRole('button', { name: 'Suppliers', exact: true })).toHaveAttribute('aria-current', 'page');
    await page.getByRole('button', { name: 'Add New Supplier' }).click();
    await expect(page.locator('.modal-card')).toBeVisible();
    await page.locator('.modal-card').getByRole('button', { name: 'Cancel' }).click();
    await page.locator('.sidebar-create').click();
    await expect(page.getByRole('heading', { name: 'Launch Request for Quote (RFQ)' })).toBeVisible();
    for (const [name, heading] of [
      ['Requests & tracking', 'RFQs & Live Tracking'],
      ['Quotes & rankings', 'Quotes Comparison & AI Recommendation Report'],
      ['Conversations', 'WhatsApp Audit Logs & Conversation Transcripts'],
      ['Agent attention', 'Agent Attention & Human Review'],
      ['Daily reports', 'Daily Procurement Report'],
    ]) {
      await nav.getByRole('button', { name, exact: name !== 'Agent attention' }).click();
      await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), { message: `Page overflow in ${name}` }).toBeTruthy();
      if (viewport.width === 1440) await page.screenshot({ path: `/private/tmp/amafah-${name.replaceAll(' ', '-')}.png`, fullPage: true });
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  });
}
