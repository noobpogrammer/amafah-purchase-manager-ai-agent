import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

for (const width of [1440, 768, 375]) {
  test(`request detail prioritizes quotes and preserves expandable records at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('navigation').getByRole('button', { name: 'Requests & tracking' }).click();
    const quotes = page.getByRole('region', { name: 'Recorded quotes', exact: true });
    await expect(quotes.locator('.quote-summary-card')).toHaveCount(2);
    const authority = page.locator('.rfq-disclosure').filter({ hasText: 'Negotiation authority' });
    const progress = page.locator('.rfq-disclosure').filter({ hasText: 'Supplier progress' });
    await expect(authority).not.toHaveAttribute('open');
    await expect(progress).not.toHaveAttribute('open');
    const boxes = await page.locator('.rfq-header-actions > .btn').evaluateAll(els => els.map(el => ({ width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height })));
    expect(boxes).toHaveLength(3);
    expect(Math.max(...boxes.map(b => b.width)) - Math.min(...boxes.map(b => b.width))).toBeLessThan(1);
    expect(new Set(boxes.map(b => b.height)).size).toBe(1);
    expect((await quotes.boundingBox()).y).toBeLessThan((await authority.boundingBox()).y);
    await authority.locator('summary').click();
    await expect(authority.getByText('Trade-offs require approval')).toBeVisible();
    await authority.locator('summary').click();
    await progress.locator('summary').click();
    await expect(progress.getByRole('cell', { name: 'Gulf Plumbing Solutions' })).toBeVisible();
    await quotes.locator('summary').first().click();
    await expect(quotes.locator('.raw-msg').first()).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: `/private/tmp/amafah-rfq-reviewed-${width}.png`, fullPage: true });
  });
}
