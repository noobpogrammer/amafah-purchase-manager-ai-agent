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

for (const action of ['partial', 'decline']) {
  test(`partial supplier quantity ${action} is a buyer decision, not purchase acceptance`, async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    let decision;
    await page.route('**/rfq/*/current-offers', route => route.fulfill({json:{offers:[{...mockQuotes[0],price:105,quantity:2,delivery_time:null}],price_policy:{preferred_target:75,acceptable_max:90},buyer_reviews:[{id:'partial-review',quote_id:mockQuotes[0].id,status:'pending',review_kind:'quantity',offer_snapshot:{price:105,quantity:2,resolved_delivery_days:null}}]}}));
    await page.route('**/rfq/*/buyer-reviews/*/decision', route => {
      decision=route.request().postDataJSON();return route.fulfill({json:{queued:true}});
    });
    await page.goto('/');
    await page.getByRole('button',{name:'Requests & tracking'}).click();
    const card=page.getByRole('region',{name:'Buyer final offer review'});
    await expect(card).toContainText('Delivery unconfirmed');
    await expect(card.getByRole('button',{name:'Accept AED 105'})).toHaveCount(0);
    await card.getByRole('button',{name:action==='partial'?'Consider 2 pieces':'Decline offer'}).click();
    expect(decision).toEqual({decision:action,counter_price:null});
    await expect(card.getByRole('status')).toContainText(action==='partial'?'Price and delivery':'queued');
  });
}

for (const blocked of [false,true]) {
  test(`stated after delivery remains visible and acceptance ${blocked?'blocked by deadline':'available for buyer approval'}`, async ({page}) => {
    await setupAuthenticatedSession(page,'admin');
    await page.route('**/rfq/*/current-offers',route=>route.fulfill({json:{offers:mockQuotes,price_policy:{preferred_target:40,acceptable_max:50},buyer_reviews:[{id:'after-review',quote_id:'quote-1',status:'pending',acceptance_blocker:blocked?'The stated delivery term does not guarantee arrival by your delivery deadline.':null,offer_snapshot:{price:44.5,quantity:50,resolved_delivery_days:4,delivery_time:'Delivery after 4 days',delivery_term:{relation:'after',days:4,unit:'calendar_days',text:'Delivery after 4 days'}}}]}}));
    await page.goto('/');await page.getByRole('button',{name:'Requests & tracking'}).click();
    const card=page.getByRole('region',{name:'Buyer final offer review'});
    await expect(card).toContainText('Delivery after 4 days');
    await expect(card).toContainText('no guaranteed latest arrival date');
    if(blocked) await expect(card.getByRole('button',{name:'Accept AED 44.5'})).toBeDisabled();
    else await expect(card.getByRole('button',{name:'Accept AED 44.5'})).toBeEnabled();
  });
}
