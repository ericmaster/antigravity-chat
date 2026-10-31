'use strict';
// Spec: docs/specs/agy-integration.md — real-browser assertions, called by authenticated E2E.
const assert = require('node:assert/strict');
const path = require('node:path');

// Spec: docs/specs/agy-integration.md
module.exports = async function geometry(page, evidence, check) {
  const widths = [320, 360, 390, 419, 420, 421, 639, 640, 641, 768, 1280];
  const original = await page.evaluate(() => ({cid: currentConversationId, cwd: currentCwd}));
  for (const width of widths) {
    await page.setViewportSize({width, height: 800});
    for (const fixture of [false, true]) {
      await page.evaluate(({fixture, original}) => {
        currentConversationId = fixture ? 'conversation-'.repeat(40) : original.cid;
        currentCwd = fixture ? '/home/qa/' + 'long-workspace-'.repeat(30) : original.cwd;
        updateConversationPill(); updateWorkspacePill();
      }, {fixture, original});
      await page.locator('#input').fill(fixture ? 'Long composer text '.repeat(400) : '');
      const result = await page.evaluate(() => {
        const controls = [...document.querySelectorAll('header button, header select, .brand, #input, #send')]
          .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
          .map(e => ({id: e.id || e.className, ...Object.fromEntries(['x','y','width','height','right','bottom'].map(k => [k,e.getBoundingClientRect()[k]]))}));
        const outside = controls.filter(r => r.x < 0 || r.right > innerWidth + 1 || r.y < 0 || r.bottom > innerHeight + 1);
        const overlaps = [];
        for (let i=0;i<controls.length;i++) for(let j=i+1;j<controls.length;j++) {
          const a=controls[i], b=controls[j];
          if(Math.min(a.right,b.right)-Math.max(a.x,b.x)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1) overlaps.push([a.id,b.id]);
        }
        const covered = controls.filter(r => {
          const e = document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          const own = r.id === 'brand' ? document.querySelector('.brand') : document.getElementById(r.id);
          return !own || !e || !(own === e || own.contains(e));
        }).map(r => r.id);
        return {width:innerWidth, fixture: null, controls, outside, overlaps, covered,
          overflow:document.documentElement.scrollWidth>innerWidth || document.body.scrollWidth>innerWidth,
          headerText:document.querySelector('header').innerText,
          send:{name:document.querySelector('#send').getAttribute('aria-label'),title:document.querySelector('#send').title,
            text:document.querySelector('#send').textContent.trim(),svg:!!document.querySelector('#send svg')}};
      });
      result.fixture = fixture;
      check(`geometry ${width} ${fixture ? 'long-content fixture' : 'normal'}`,
        !result.overflow && !result.outside.length && !result.overlaps.length && !result.covered.length, result);
      check(`header and send icon ${width} ${fixture}`, !result.headerText.includes('Antigravity Chat') &&
        result.send.name === 'Send' && result.send.title === 'Send' && result.send.text === '' && result.send.svg, result.send);
      await page.locator('#input').blur();
      await page.screenshot({path:path.join(evidence, `${width}-${fixture ? 'long' : 'normal'}.png`)});
    }
  }
  await page.evaluate(original => {
    currentConversationId=original.cid; currentCwd=original.cwd;
    updateConversationPill(); updateWorkspacePill();
  }, original);
  await page.locator('#input').fill('');
  await page.setViewportSize({width:390,height:800});
  assert.equal(await page.title(), 'antigravity chat');
};
