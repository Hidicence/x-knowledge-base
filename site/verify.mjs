import {chromium} from '@playwright/test';
import fs from 'node:fs/promises';
const out='node_modules/.cache/archive-check';await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const errors=[];const results=[];
try{
for(const width of [1440,390,360]){
const page=await browser.newPage({viewport:{width,height:width===1440?900:844},permissions:['clipboard-read','clipboard-write']});
await page.addInitScript(()=>{Element.prototype.requestPointerLock=()=>Promise.reject();Element.prototype.setPointerCapture=()=>{};});
page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:4173',{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready);
await page.waitForTimeout(300);
const opening=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,copyOpacity:getComputedStyle(document.querySelector('.hero-statement')).opacity,images:[...document.images].every(i=>i.complete&&i.naturalWidth>0)}));
if(opening.overflow||opening.copyOpacity!=='1'||!opening.images)errors.push({width,opening});
await page.screenshot({path:`${out}/${width}-hero.png`});
const before=await page.locator('.scene-subject').evaluate(e=>getComputedStyle(e).transform);
await page.evaluate(()=>{document.documentElement.style.scrollBehavior='auto';scrollTo(0,180)});await page.waitForTimeout(300);
const after=await page.locator('.scene-subject').evaluate(e=>getComputedStyle(e).transform);
if(before===after)errors.push(`${width}: parallax unchanged`);
await page.screenshot({path:`${out}/${width}-hero-mid.png`});
const bounds=await page.locator('#thread').evaluate(e=>({top:e.offsetTop,travel:e.offsetHeight-innerHeight}));
for(const p of [.05,.5,.95]){await page.evaluate(y=>scrollTo(0,y),bounds.top+bounds.travel*p);await page.waitForTimeout(300);await page.screenshot({path:`${out}/${width}-thread-${p}.png`});}
const receipt=await page.locator('.recall-receipt').evaluate(e=>getComputedStyle(e).opacity);if(receipt!=='1')errors.push(`${width}: recall never fully visible`);
await page.locator('.evidence-ledger details').nth(2).locator('summary').click();await page.waitForFunction(()=>document.querySelectorAll('.evidence-ledger details[open]').length===1);
await page.locator('#tab-service').click();if(!(await page.locator('#setup-command').textContent()).includes('xkb_knowledge_service.py'))errors.push(`${width}: setup mode not updated`);
await page.locator('#copy-command').click();await page.waitForFunction(()=>document.querySelector('#copy-status').textContent==='指令已複製');
await page.locator('#tab-service').focus();await page.keyboard.press('ArrowLeft');if(await page.locator('#tab-local').getAttribute('aria-selected')!=='true')errors.push(`${width}: keyboard tabs`);
await page.screenshot({path:`${out}/${width}-setup.png`});results.push({width,...opening,parallax:before!==after,receipt});await page.close();
}
const page=await browser.newPage({viewport:{width:390,height:844},reducedMotion:'reduce'});await page.goto('http://127.0.0.1:4173',{waitUntil:'networkidle'});await page.locator('#thread').scrollIntoViewIfNeeded();await page.screenshot({path:`${out}/reduced.png`,fullPage:true});
const reduced=await page.evaluate(()=>({hidden:document.querySelectorAll('.source-fragment[aria-hidden="true"],.assembled-card[aria-hidden="true"],.review-stamp[aria-hidden="true"],.recall-receipt[aria-hidden="true"]').length,overflow:document.documentElement.scrollWidth>innerWidth}));if(reduced.hidden||reduced.overflow)errors.push({reduced});
console.log(JSON.stringify({results,reduced,errors},null,2));if(errors.length)process.exitCode=1;
}finally{await browser.close();}
