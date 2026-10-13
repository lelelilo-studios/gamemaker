// Loads the built web version in headless Chrome and checks that it really draws and really plays: it must start
// without errors, its offscreen capture (the page is opened with `?capture`) must look like pencil on paper, not
// a blank or a black page, and holding the right arrow must move the hero to the right. Saves the capture as a
// PNG for a human to look at.
//
//   node tools/web-smoke.mjs [dist/web | https://…/] [capture.png] [query]
//
// `SMOKE_WAIT=<seconds>` waits longer for the first picture. `SMOKE_AT=v/3/` opens the page in that folder of what
// is served (a frozen iteration of the site) instead of the one at its top.
// CHROME / CHROME_PROFILE pick the browser, as described in chrome.mjs.

import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { deadline, webgpuChrome } from './chrome.mjs';
import { serve } from './serve.mjs';

const dir = process.argv[2] || 'dist/web', out = process.argv[3] || '/data/scratch/gamemaker/shots/web-smoke.png', query = process.argv[4] || 'capture';
const live = /^https?:/.test(dir), at = (process.env.SMOKE_AT || '').replace(/^\/+/, '').replace(/\/?$/, '/').replace(/^\/$/, '');
if (!live && !existsSync(join(dir, at, 'index.html'))) { console.error(`no build in ${join(dir, at)}`); process.exit(1); }
const wait = Number(process.env.SMOKE_WAIT || 60);
deadline(wait + 90);
const server = await serve(live ? null : dir);
const base = (live ? dir.replace(/\/?$/, '/') : `${server.url}/`) + at;

let failed = true, browser = null;
try {
  browser = await webgpuChrome(`${server.url}/__blank`);
  const send = browser.send;
  await send('Page.enable');
  // (the page sees no gamepad: on Linux a keyboard's media keys can show up as one, and what is pressed on this
  // machine while the check runs would then move the hero)
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `Object.defineProperty(Navigator.prototype, 'getGamepads', { value: () => [], configurable: true });` });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `window.__errors=[]; const e0=console.error; console.error=function(...a){ window.__errors.push(a.join(' ')); return e0.apply(console,a); };
    addEventListener('error', e => window.__errors.push('uncaught: ' + e.message)); addEventListener('unhandledrejection', e => window.__errors.push('rejected: ' + (e.reason && e.reason.message || e.reason)));` });
  const started = Date.now();
  await send('Page.navigate', { url: `${base}?${query}` });
  let state = null;
  for (let i = 0; i < wait * 2 && !state; i++) {
    await new Promise(r => setTimeout(r, 500));
    state = (await browser.evaluate(`(() => {
      const shown = id => !document.getElementById(id).classList.contains('hidden');
      if (shown('failed')) return { error: document.getElementById('errtext').textContent };
      if (shown('unsupported')) return { error: 'the page says WebGPU is not supported: ' + document.getElementById('why').textContent };
      const c = window.__lelelilo_capture, ready = document.getElementById('loading').classList.contains('gone');
      if (!c || !ready) return null;
      if (!c.data.length) return { error: 'the capture is empty' };
      // Pencil on paper: most of the page is light and warm, some of it is dark line, and it is not all one colour
      let paper = 0, ink = 0, n = 0, lo = 255, hi = 0;
      for (let y = 0; y < c.height; y += 3) for (let x = 0; x < c.width; x += 3) {
        const i = (y * c.width + x) * 4, r = c.data[i], g = c.data[i + 1], b = c.data[i + 2], l = (r + g + b) / 3;
        n++; lo = Math.min(lo, l); hi = Math.max(hi, l);
        if (l > 170 && r >= b) paper++;
        if (l < 140) ink++;
      }
      const cv = document.createElement('canvas'); cv.width = c.width; cv.height = c.height;
      cv.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(c.data), c.width, c.height), 0, 0);
      return { size: c.width + 'x' + c.height, paper: paper / n, ink: ink / n, range: hi - lo, png: cv.toDataURL('image/png'), errors: window.__errors,
        build: window.__lelelilo_build };
    })()`).catch(() => null));
  }
  if (!state) throw new Error(`the game neither started nor reported an error within ${wait} s`);
  if (state.error) throw new Error(state.error);
  // (the folder for pictures is scratch: it may have been cleared away since the last run)
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, Buffer.from(state.png.split(',')[1], 'base64'));
  console.log(`capture ${state.size} of build ${state.build}: ${(state.paper * 100).toFixed(1)}% paper, ${(state.ink * 100).toFixed(2)}% dark line (saved ${out}; drawn ${((Date.now() - started) / 1000).toFixed(1)} s after opening)`);
  if (state.errors.length) throw new Error('console errors: ' + state.errors.join(' | '));
  if (state.range < 80) throw new Error('the capture looks blank: it is all one colour');
  if (state.paper < 0.3) throw new Error('the capture does not look like paper');
  if (state.ink < 0.001) throw new Error('the capture has no pencil lines in it');
  console.log('✓ the web build starts and draws pencil on paper');
  // The game itself: the right arrow held for a second and a half takes the hero to the right
  const where = async () => JSON.parse(await browser.evaluate('window.__lelelilo_state || "null"'));
  await browser.evaluate(`document.getElementById('lelelilo').focus()`);
  let before = null;
  for (let i = 0; i < 40 && !before; i++) { await new Promise(r => setTimeout(r, 250)); before = await where(); }
  if (!before) throw new Error('the game says nothing of where its hero is');
  const key = { key: 'ArrowRight', code: 'ArrowRight', windowsVirtualKeyCode: 39, nativeVirtualKeyCode: 39 };
  // (a browser with no graphics card draws slowly, and the game then runs behind the clock: the key is held until
  // the hero has gone somewhere, for a quarter of a minute at most, and then the hero is given time to stop)
  const pause = ms => new Promise(r => setTimeout(r, ms));
  await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...key });
  let held = before;
  for (let i = 0; i < 60 && !(held.x > before.x + 1.5); i++) { await pause(250); held = await where(); }
  await send('Input.dispatchKeyEvent', { type: 'keyUp', ...key });
  let after = await where(), rest = null;
  for (let i = 0; i < 40; i++) { await pause(500); rest = await where(); if (Math.abs(rest.x - after.x) < 0.01) break; after = rest; }
  console.log(`the hero: at ${before.x.toFixed(2)}, ${held.x.toFixed(2)} with → held, and at rest at ${rest.x.toFixed(2)} once it was let go`);
  if (!(held.x > before.x + 1.5)) throw new Error('holding → did not move the hero to the right');
  if (Math.abs(rest.x - after.x) >= 0.01) throw new Error('the hero went on moving after → was let go');
  // The sound: its context, and the synthesizer loaded into its worklet and connected (what it plays is checked
  // by `sound-check.mjs`; it cannot be listened to from here)
  let sound = null;
  for (let i = 0; i < 40 && !(sound && sound.synth); i++) {
    await new Promise(r => setTimeout(r, 250));
    sound = await browser.evaluate(`(() => { const a = window.__lelelilo_audio; return { state: a ? a.state : 'none', rate: a ? a.sampleRate : 0, synth: window.__lelelilo_sound || '' }; })()`);
  }
  console.log(`sound: context ${sound.state} at ${sound.rate} Hz, synthesizer ${sound.synth || 'not loaded'}`);
  if (sound.state === 'none' || sound.synth !== 'on') throw new Error('the sound was not set up');
  const rate = await browser.evaluate(`new Promise(r => { let n = 0; const t0 = performance.now(); const f = () => { n++; if (performance.now() - t0 < 2000) requestAnimationFrame(f); else r(n / 2); }; requestAnimationFrame(f); })`);
  console.log(`frames: ${rate.toFixed(0)} a second here (a software GPU: no measure of a real one)`);
  const errors = await browser.evaluate('window.__errors');
  if (errors.length) throw new Error('console errors while playing: ' + errors.join(' | '));
  console.log('✓ the hero runs when → is held');
  failed = false;
} catch (e) {
  console.error(`✗ ${e.message}`);
} finally {
  await browser?.close(); server.close();
}
process.exit(failed ? 1 : 0);
