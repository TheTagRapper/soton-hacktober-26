// Browser verification against the real site (spec section 14, items 5-10).
//
// Loads the page, waits for the map and the colour layer to actually have
// data, captures every console message, and screenshots a set of views:
// the whole world on the solar axis, a playback step, a deep zoom into a
// dense city, a shared-hash reload, and the reduced-motion case.
//
// Usage: node tests/verify_browser.mjs <baseUrl> <outDir>

import { firefox } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const base = process.argv[2] ?? 'http://localhost:5173';
const out = process.argv[3] ?? '/tmp/verify';
mkdirSync(out, { recursive: true });

const problems = [];
const report = [];

function log(line) {
  report.push(line);
  console.log(line);
}

async function openPage(browser, hash, { reducedMotion = 'no-preference' } = {}) {
  const context = await browser.newContext({
    viewport: { width: 1400, height: 900 },
    reducedMotion,
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') {
      consoleErrors.push(`${m.type()}: ${m.text()}`);
    }
  });
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));
  await page.goto(`${base}/${hash}`, { waitUntil: 'load', timeout: 60000 });
  return { context, page, consoleErrors };
}

/** Wait until the readout stops saying "loading" and reports a real cell count. */
async function waitForData(page, timeout = 45000) {
  await page.waitForFunction(
    () => {
      const el = document.querySelector('#readout');
      if (!el) return false;
      const t = el.textContent ?? '';
      const m = t.match(/([\d,]+) cells/);
      return !t.includes('loading') && m && Number(m[1].replace(/,/g, '')) > 0;
    },
    undefined,
    { timeout },
  );
  return (await page.textContent('#readout')) ?? '';
}

/** Average colour of the map canvas, to prove the frame actually changed. */
async function canvasSignature(page) {
  return page.evaluate(() => {
    const c = document.querySelector('canvas');
    if (!c) return null;
    const gl = c.getContext('webgl2') ?? c.getContext('webgl');
    if (!gl) return null;
    const w = 200;
    const h = 120;
    const px = new Uint8Array(w * h * 4);
    gl.readPixels(
      Math.floor(gl.drawingBufferWidth / 2 - w / 2),
      Math.floor(gl.drawingBufferHeight / 2 - h / 2),
      w, h, gl.RGBA, gl.UNSIGNED_BYTE, px,
    );
    let r = 0, g = 0, b = 0;
    for (let i = 0; i < px.length; i += 4) {
      r += px[i];
      g += px[i + 1];
      b += px[i + 2];
    }
    const n = px.length / 4;
    return [r / n, g / n, b / n];
  });
}

const browser = await firefox.launch();

try {
  // --- 5. whole world, solar axis -----------------------------------------
  {
    const { context, page, consoleErrors } = await openPage(
      browser,
      '#axis=solar&frame=21&src=model&geo=point&z=1.4&lat=20&lon=0&playing=0',
    );
    const readout = await waitForData(page);
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(out, '01-world-2100.png') });
    log(`[world] readout: ${readout}`);
    const sigA = await canvasSignature(page);

    // Step the scrubber several frames and confirm the picture changes.
    for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowRight');
    await waitForData(page);
    await page.waitForTimeout(2000);
    await page.screenshot({ path: path.join(out, '02-world-0300.png') });
    const sigB = await canvasSignature(page);
    log(`[world] readout after 6 frames: ${await page.textContent('#readout')}`);

    if (sigA && sigB) {
      const delta = Math.max(...sigA.map((v, i) => Math.abs(v - sigB[i])));
      log(`[world] mean canvas colour 21:00 ${sigA.map((v) => v.toFixed(1))} -> 03:00 ${sigB.map((v) => v.toFixed(1))}, max channel delta ${delta.toFixed(2)}`);
      if (delta < 0.5) problems.push('solar axis: the picture barely changes between frames');
    } else {
      problems.push('could not read the WebGL canvas back');
    }
    if (consoleErrors.length) problems.push(`console (world): ${consoleErrors.slice(0, 6).join(' | ')}`);
    await context.close();
  }

  // --- 6. zoom ladder over a dense city ------------------------------------
  {
    const { context, page, consoleErrors } = await openPage(
      browser,
      '#axis=solar&frame=21&src=model&geo=point&z=2&lat=51.5&lon=-0.12&playing=0',
    );
    await waitForData(page);
    const seen = [];
    for (const z of [2, 4, 6, 8, 10, 12]) {
      await page.evaluate((zz) => {
        // eslint-disable-next-line no-undef
        window.__map?.jumpTo({ zoom: zz });
      }, z);
      await page.mouse.move(700, 450);
      await page.waitForTimeout(1600);
      const readout = await page.textContent('#readout');
      seen.push(`z${z} -> ${readout}`);
      if ((readout ?? '').includes('0 cells')) {
        problems.push(`blank frame while zooming: z${z} reported ${readout}`);
      }
      await page.screenshot({ path: path.join(out, `03-zoom-z${z}.png`) });
    }
    log('[zoom] ' + seen.join(' ; '));
    if (consoleErrors.length) problems.push(`console (zoom): ${consoleErrors.slice(0, 6).join(' | ')}`);
    await context.close();
  }

  // --- 7. a shared hash reproduces the view --------------------------------
  {
    const hash = '#axis=chrono&frame=2012-06&src=model&geo=point&z=5.00&lat=40.7128&lon=-74.0060&playing=0';
    const { context, page, consoleErrors } = await openPage(browser, hash);
    await waitForData(page);
    await page.waitForTimeout(1500);
    const state = await page.evaluate(() => location.hash);
    const label = await page.textContent('#frame-label');
    log(`[hash] reloaded to ${state} showing ${label}`);
    if (!state.includes('axis=chrono') || !state.includes('frame=2012-06')) {
      problems.push(`shared hash not reproduced: ${state}`);
    }
    await page.screenshot({ path: path.join(out, '04-shared-hash.png') });
    if (consoleErrors.length) problems.push(`console (hash): ${consoleErrors.slice(0, 6).join(' | ')}`);
    await context.close();
  }

  // --- 8. no JavaScript ----------------------------------------------------
  {
    const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1000, height: 900 } });
    const page = await context.newPage();
    await page.goto(base, { waitUntil: 'load' });
    const text = (await page.textContent('body')) ?? '';
    await page.screenshot({ path: path.join(out, '05-nojs.png'), fullPage: true });
    const hasExplanation = text.includes('Chromatic Listening') && text.includes('MMTD');
    log(`[nojs] ${text.trim().length} characters of explanation and credits rendered`);
    if (!hasExplanation) problems.push('no-JS fallback did not render the explanation and credits');
    await context.close();
  }

  // --- 9. prefers-reduced-motion -------------------------------------------
  {
    const { context, page, consoleErrors } = await openPage(
      browser,
      '#axis=solar&frame=21&src=model&geo=point&z=1.4&lat=20&lon=0&playing=1',
      { reducedMotion: 'reduce' },
    );
    await waitForData(page);
    const before = await page.textContent('#frame-label');
    await page.waitForTimeout(4000);
    const after = await page.textContent('#frame-label');
    log(`[reduced-motion] autoplay requested in the hash; frame ${before} -> ${after}`);
    if (before !== after) problems.push('autoplay ran under prefers-reduced-motion');
    // The scrubber must still work.
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(1200);
    const stepped = await page.textContent('#frame-label');
    if (stepped === after) problems.push('scrubber did not work under prefers-reduced-motion');
    log(`[reduced-motion] manual step still works: ${after} -> ${stepped}`);
    if (consoleErrors.length) problems.push(`console (reduced-motion): ${consoleErrors.slice(0, 6).join(' | ')}`);
    await context.close();
  }

  // --- phone viewport ------------------------------------------------------
  {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
    const page = await context.newPage();
    const errs = [];
    page.on('pageerror', (e) => errs.push(e.message));
    await page.goto(`${base}/#axis=solar&frame=21&src=model&geo=point&z=1.1&lat=20&lon=0&playing=0`, { waitUntil: 'load' });
    await waitForData(page);
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(out, '06-phone.png') });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    log(`[phone] 390x844 rendered; horizontal overflow: ${overflow}`);
    if (overflow) problems.push('page scrolls horizontally at 390px');
    if (errs.length) problems.push(`console (phone): ${errs.slice(0, 4).join(' | ')}`);
    await context.close();
  }

  // --- 10. first paint on a throttled connection ---------------------------
  {
    const context = await browser.newContext({ viewport: { width: 1200, height: 800 } });
    const page = await context.newPage();
    const t0 = Date.now();
    await page.goto(`${base}/#axis=solar&frame=21&src=model&geo=point&z=1.4&lat=20&lon=0&playing=0`, { waitUntil: 'load' });
    await waitForData(page);
    const ms = Date.now() - t0;
    log(`[first-paint] low-resolution solar frame drawn in ${ms} ms (local server, unthrottled)`);
    await context.close();
  }
} finally {
  await browser.close();
}

log('');
if (problems.length) {
  log(`PROBLEMS (${problems.length}):`);
  for (const p of problems) log(`  - ${p}`);
} else {
  log('No problems found.');
}
writeFileSync(path.join(out, 'report.txt'), report.join('\n'));
process.exit(problems.length ? 1 : 0);
