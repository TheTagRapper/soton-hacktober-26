// Browser verification against the real site (spec section 14, items 5-10).
//
// Loads the page, waits for the map and the colour layer to actually have
// data, captures every console message, and screenshots a set of views:
// the whole world on the solar axis, a playback step, a deep zoom into a
// dense city, a shared-hash reload, and the reduced-motion case.
//
// Usage: node tests/verify_browser.mjs <baseUrl> <outDir>

import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// playwright is a devDependency of site/, and this script lives in tests/, so
// resolve it explicitly rather than relying on where node was started.
const here = path.dirname(fileURLToPath(import.meta.url));
const { firefox } = await import(
  pathToFileURL(path.join(here, '..', 'site', 'node_modules', 'playwright', 'index.mjs')).href
);

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
  // Only errors fail the run. MapLibre and deck.gl emit WebGL deprecation
  // warnings on every texture upload in Firefox; counting those as defects
  // would bury real ones.
  const consoleErrors = [];
  const consoleWarnings = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(`error: ${m.text()}`);
    else if (m.type() === 'warning') consoleWarnings.push(m.text());
  });
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));
  await page.goto(`${base}/${hash}`, { waitUntil: 'load', timeout: 60000 });
  return { context, page, consoleErrors, consoleWarnings };
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

/** How much of the map area differs between two screenshots.
 *
 *  Reading the WebGL buffer back directly returns zeros -- the drawing buffer
 *  is not preserved after composite -- so compare what the viewer actually
 *  sees. PNG bytes would be useless for a magnitude, so both shots are
 *  decoded in the page with a canvas and compared pixel by pixel. */
async function framePixels(page, clip) {
  const buf = await page.screenshot({ clip });
  return buf.toString('base64');
}

async function fractionChanged(page, a, b, clip) {
  return page.evaluate(
    async ([aB64, bB64, w, h]) => {
      const load = (b64) =>
        new Promise((res) => {
          const img = new Image();
          img.onload = () => res(img);
          img.src = `data:image/png;base64,${b64}`;
        });
      const [ia, ib] = await Promise.all([load(aB64), load(bB64)]);
      const draw = (img) => {
        const c = document.createElement('canvas');
        c.width = w;
        c.height = h;
        const ctx = c.getContext('2d');
        ctx.drawImage(img, 0, 0);
        return ctx.getImageData(0, 0, w, h).data;
      };
      const pa = draw(ia);
      const pb = draw(ib);
      let changed = 0;
      let total = 0;
      for (let i = 0; i < pa.length; i += 4) {
        total++;
        const d =
          Math.abs(pa[i] - pb[i]) +
          Math.abs(pa[i + 1] - pb[i + 1]) +
          Math.abs(pa[i + 2] - pb[i + 2]);
        if (d > 12) changed++;
      }
      return changed / total;
    },
    [a, b, clip.width, clip.height],
  );
}

const browser = await firefox.launch();

try {
  // --- 5. whole world, solar axis -----------------------------------------
  {
    const { context, page, consoleErrors, consoleWarnings } = await openPage(
      browser,
      '#axis=solar&frame=21&src=model&geo=point&z=1.4&lat=20&lon=0&playing=0',
    );
    const readout = await waitForData(page);
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(out, '01-world-2100.png') });
    log(`[world] readout: ${readout}`);
    const clip = { x: 120, y: 120, width: 1100, height: 450 };
    const shotA = await framePixels(page, clip);

    // Step the scrubber several frames and confirm the picture changes.
    for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowRight');
    await waitForData(page);
    await page.waitForTimeout(2000);
    await page.screenshot({ path: path.join(out, '02-world-0300.png') });
    const shotB = await framePixels(page, clip);
    log(`[world] readout after 6 frames: ${await page.textContent('#readout')}`);

    const changed = await fractionChanged(page, shotA, shotB, clip);
    log(`[world] ${(changed * 100).toFixed(1)}% of the map area differs between 21:00 and 03:00`);
    if (changed < 0.02) {
      problems.push(
        `solar axis: only ${(changed * 100).toFixed(1)}% of the picture changes between frames`,
      );
    }
    log(`[world] console warnings (not failures): ${consoleWarnings.length}`);
    if (consoleErrors.length) problems.push(`console (world): ${consoleErrors.slice(0, 6).join(' | ')}`);
    await context.close();
  }

  // --- 6. zoom ladder over a dense city ------------------------------------
  {
    const { context, page, consoleErrors, consoleWarnings } = await openPage(
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
    log(`[zoom] console warnings (not failures): ${consoleWarnings.length}`);
    if (consoleErrors.length) problems.push(`console (zoom): ${consoleErrors.slice(0, 6).join(' | ')}`);
    await context.close();
  }

  // --- 7. a shared hash reproduces the view --------------------------------
  {
    const hash = '#axis=chrono&frame=2012-06&src=model&geo=point&z=5.00&lat=40.7128&lon=-74.0060&playing=0';
    const { context, page, consoleErrors, consoleWarnings } = await openPage(browser, hash);
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
    const { context, page, consoleErrors, consoleWarnings } = await openPage(
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
