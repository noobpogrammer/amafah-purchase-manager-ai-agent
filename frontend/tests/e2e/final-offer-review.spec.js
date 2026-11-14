import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';
import { mockQuotes } from './fixtures/data.js';

for (const action of ['accept', 'negotiate']) {
  test(`final offer ${action} decision stays in recorded quotes`, async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    let decision;
    await page.route('**/rfq/*/current-offers', route => route.fulfill({json:{offers:mockQuotes,price_policy:{preferred_target:40,acceptable_max:50},buyer_reviews:[{id:'review-1',quote_id:'quote-1',status:'pending',offer_snapshot:{price:44.5,quantity:50,resolved_delivery_days:2}}]}}));
    await page.route('**/rfq/*/buyer-reviews/*/decision', route => {
      decision=route.request().postDataJSON();return route.fulfill({json:{queued:true}});
    });
    await page.goto('/');
    await page.getByRole('button',{name:'Requests & tracking'}).click();
    const card=page.getByRole('region',{name:'Buyer final offer review'});
    await expect(card).toBeVisible();
    if(action==='accept') await card.getByRole('button',{name:'Accept AED 44.5'}).click();
    else {
      await card.getByRole('button',{name:'Negotiate again'}).click();
      await card.getByLabel('Ask for a unit price (AED)').fill('42');
      await page.screenshot({path:'/private/tmp/buyer-final-offer-review.png',fullPage:true});
      await card.getByRole('button',{name:'Send negotiation instruction'}).click();
    }
    await expect(card.getByRole('status')).toContainText('queued');
    expect(decision).toEqual({decision:action,counter_price:action==='accept'?null:42});
  });
}
