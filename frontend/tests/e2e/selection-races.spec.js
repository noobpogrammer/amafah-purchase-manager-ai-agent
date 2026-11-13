import {test,expect,setupAuthenticatedSession} from './fixtures/test-base.js';

for (const screen of ['Requests & tracking','Quotes & rankings']) {
 test(`late RFQ response cannot overwrite the current ${screen} selection`,async({page})=>{
  await setupAuthenticatedSession(page,'admin');
  await page.goto('/');
  let release, started;
  const waiting=new Promise(resolve=>started=resolve);
  const gate=new Promise(resolve=>release=resolve);
  await page.route('**/rfq/rfq-active-1/current-offers',async route=>{
   started(); await gate; await route.fulfill({status:200,contentType:'application/json',body:'{"offers":[]}'});
  });
  await page.getByRole('navigation').getByRole('button',{name:screen,exact:true}).click();
  await waiting;
  if(screen==='Requests & tracking') await page.locator('.rfq-item-card').filter({hasText:'Heavy Duty Power Drill'}).click();
  else await page.locator('.rfq-select-dropdown select').selectOption('rfq-closed-1');
  const title=page.locator(screen==='Requests & tracking'?'.rfq-product-title':'.report-title');
  await expect(title).toHaveText('Heavy Duty Power Drill 800W');
  const response=page.waitForResponse(r=>r.url().includes('/rfq/rfq-active-1/current-offers'));
  release(); await (await response).finished();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await expect(title).toHaveText('Heavy Duty Power Drill 800W');
 });
}
