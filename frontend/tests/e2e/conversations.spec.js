import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('WhatsApp Logs & Conversation Transcripts', () => {
  test('should filter conversation transcripts by supplier', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'WhatsApp Logs' }).click();
    await expect(page.getByRole('heading', { name: 'WhatsApp Audit Logs & Conversation Transcripts' })).toBeVisible();

    const suppFilter = page.locator('.filter-bar select').first();
    await expect(suppFilter).toBeVisible();

    // Select Gulf Plumbing Solutions by value
    await suppFilter.selectOption('supp-3');

    const messages = page.locator('.message-bubble-wrapper');
    await expect(messages).toHaveCount(2);
    await expect(page.locator('.chat-messages-container').getByText('Gulf Plumbing Solutions').first()).toBeVisible();
  });

  test('should filter conversation transcripts by RFQ', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'WhatsApp Logs' }).click();

    const rfqFilter = page.locator('.filter-bar select').nth(1);
    await expect(rfqFilter).toBeVisible();

    // Select Copper Water Pipe RFQ by value
    await rfqFilter.selectOption('rfq-active-1');
    await expect(page.locator('.message-bubble-wrapper')).toHaveCount(2);
  });

  test('should reload message logs when clicking Refresh Log button', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'WhatsApp Logs' }).click();

    const refreshBtn = page.getByRole('button', { name: 'Refresh Log' });
    await expect(refreshBtn).toBeVisible();
    await refreshBtn.click();

    await expect(page.locator('.chat-messages-container')).toBeVisible();
  });

  test('should render inbound and outbound message bubbles correctly', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'WhatsApp Logs' }).click();

    // Outbound bubble
    const outbound = page.locator('.message-bubble-wrapper.outbound');
    await expect(outbound).toBeVisible();
    await expect(outbound.getByText('Amafha AI Agent')).toBeVisible();
    await expect(outbound.getByText('Hello Gulf Plumbing Solutions')).toBeVisible();

    // Inbound bubble
    const inbound = page.locator('.message-bubble-wrapper.inbound');
    await expect(inbound).toBeVisible();
    await expect(inbound.getByText('Gulf Plumbing Solutions')).toBeVisible();
    await expect(inbound.getByText('we can supply 50 pcs Copper Water Pipe')).toBeVisible();
  });

  test('should render empty state when no messages match', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin', { messages: [] });
    await page.goto('/');

    await page.getByRole('button', { name: 'WhatsApp Logs' }).click();
    await expect(page.getByText('No WhatsApp Messages Logged')).toBeVisible();
  });
});
