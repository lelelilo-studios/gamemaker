// Checks the studio intro the page plays first, as the studio's kit asks of every game: it is on screen at once, as
// a centred square on its own dark fill; a press about 1 s in changes nothing; a press about 3 s in skips it; nothing
// pressed during it reaches the game; left alone it ends by itself; it is silent when the game is started silent; and
// the page names the studio and links to it. Saves a picture at about 3 s for a human to look at.
//
//   node tools/intro-check.mjs [dist/web | https://…/] [folder for the pictures]
//
// `SMOKE_AT=v/3/` checks the page in that folder of what is served (a frozen iteration of the site, which plays the
// intro kept at the site's top) instead of the one at its top.
//
// A headless browser's software WebGPU can take longer than the intro to start the game, so where a skip is to be
// seen the check tells the page the game is ready (`__lelelilo_game_ready`), as the game itself does when it is.
// Starting the game there also keeps the page too busy to press at a given moment, so for the presses the intro's
// picture is played at a third of its speed (its own clock is the picture's): the moments are the same, with
// three times as long to hit them. Left alone, further down, it plays at its own speed.
// CHROME / CHROME_PROFILE pick the browser, as described in chrome.mjs.

import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { deadline, webgpuChrome } from './chrome.mjs';
import { serve } from './serve.mjs';

const dir = process.argv[2] || 'dist/web', out = process.argv[3] || '/data/scratch/gamemaker/shots/intro';
const live = /^https?:/.test(dir), at = (process.env.SMOKE_AT || '').replace(/^\/+/, '').replace(/\/?$/, '/').replace(/^\/$/, '');
if (!live && !existsSync(join(dir, at, 'index.html'))) { console.error(`no build in ${join(dir, at)}`); process.exit(1); }
mkdirSync(out, { recursive: true });
deadline(300);
const server = await serve(live ? null : dir);
const base = (live ? dir.replace(/\/?$/, '/') : `${server.url}/`) + at;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (ok, what) => { results.push(ok); console.log(`${ok ? '✓' : '✗'} ${what}`); };

let browser;
try {
  browser = await webgpuChrome(`${server.url}/__blank`);
  const { send, evaluate } = browser;
  await send('Page.enable');
  // (the page sees no gamepad: on Linux a keyboard's media keys can show up as one, and what is pressed on this
  // machine while the check runs would then reach the game)
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `Object.defineProperty(Navigator.prototype, 'getGamepads', { value: () => [], configurable: true });` });
  const until = async (cond, ms) => { const end = Date.now() + ms; while (Date.now() < end) { if (await cond()) return true; await sleep(30); } return false; };
  const t = () => evaluate(`(() => { const v = document.querySelector('#lelelilo-intro video'); return v ? v.currentTime : -1; })()`).catch(() => -1);
  const overlay = () => evaluate(`!!document.getElementById('lelelilo-intro')`).catch(() => false);
  const state = () => evaluate(`(() => { try { return JSON.parse(window.__lelelilo_state || 'null'); } catch (e) { return null; } })()`).catch(() => null);
  const shot = async name => { const r = await send('Page.captureScreenshot', { format: 'png' }); if (r.data) writeFileSync(join(out, name), Buffer.from(r.data, 'base64')); };
  const open = async (width, height, query = 'state') => {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: `${server.url}/__blank` });
    await sleep(100);
    await send('Page.navigate', { url: `${base}?${query}` });
  };
  // (the jump key: had it reached the game, the hero would be in the air)
  const press = async () => {
    await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: ' ', code: 'Space', windowsVirtualKeyCode: 32 });
    await sleep(80);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ' ', code: 'Space', windowsVirtualKeyCode: 32 });
  };
  const square = async (w, h, name) => {
    const b = JSON.parse(await evaluate(`JSON.stringify((() => { const v = document.querySelector('#lelelilo-intro video').getBoundingClientRect(), o = document.getElementById('lelelilo-intro');
      return { x: v.x, y: v.y, w: v.width, h: v.height, bg: getComputedStyle(o).backgroundColor, ow: o.clientWidth, oh: o.clientHeight }; })())`));
    const side = Math.min(w, h);
    const ok = Math.abs(b.w - side) <= 1 && Math.abs(b.h - side) <= 1 && Math.abs(b.x + b.w / 2 - w / 2) <= 1 && Math.abs(b.y + b.h / 2 - h / 2) <= 1
      && b.bg === 'rgb(7, 13, 24)' && b.ow === w && b.oh === h;
    check(ok, `${name}: a centred square as large as fits (${Math.round(b.w)}×${Math.round(b.h)} in ${w}×${h}) on ${b.bg}`);
  };

  // 1. With presses
  await open(1280, 720);
  check(await until(overlay, 4000), 'the intro is the first thing on the page');
  check(await until(async () => (await t()) > 0.05, 8000), 'its video plays');
  await evaluate(`document.querySelector('#lelelilo-intro video').playbackRate = 1 / 3`);
  check(await evaluate('window.__lelelilo_held === true'), 'the game is held back while it plays');
  await square(1280, 720, 'an ordinary window');
  await until(async () => (await t()) >= 1.0, 12000);
  const before = await t();
  await press();
  await sleep(400);
  const after = await t();
  check(before < 2.6 && await overlay() && after > before + 0.05, `a press at ${before.toFixed(2)} s changes nothing (still playing at ${after.toFixed(2)} s)`);
  await until(async () => (await t()) >= 2.0, 12000);
  await evaluate(`window.__lelelilo_game_ready()`);
  await until(async () => (await t()) >= 2.9, 12000);
  const at = await t();
  await press();
  const gone = await until(async () => !(await overlay()), 1500);
  // (on a busy machine the intro can be over before the press is made: then nothing was learnt, and it is said)
  if (at < 2.8 || at > 4.3) check(false, `the press meant for about 3 s came at ${at.toFixed(2)} s: the machine is too busy for this check, run it again`);
  else check(gone, `a press at ${at.toFixed(2)} s skips it (gone within 1.5 s)`);
  check(await evaluate('window.__lelelilo_held === false'), 'and the game is let go');
  // The game, once it is up, has its hero standing where it began: neither press made it jump
  await until(async () => (await state()) !== null, 90000);
  let highest = -Infinity, first = null;
  for (let i = 0; i < 12; i++) { const s = await state(); if (s) { first ??= s; highest = Math.max(highest, s.y); } await sleep(100); }
  check(first !== null && Math.abs(highest - first.y) < 0.05 && !first.paused, `nothing pressed during the intro reached the game (the hero stands at ${first?.x.toFixed(2)}, ${first?.y.toFixed(2)})`);
  const link = JSON.parse(await evaluate(`JSON.stringify([...document.querySelectorAll('a.studio')].map(a => ({ href: a.href, text: a.textContent })))`));
  check(link.length > 0 && link.every(a => a.href === 'https://lelelilo.com/?ref=lelelilo-2d' && a.text === 'lelelilo.com'), 'the page links to lelelilo.com with this game\'s name');
  check(await evaluate(`document.querySelector('meta[name=author]')?.content`) === 'lelelilo.com', 'and names it as its author');

  // 2. A very wide window, left alone: it ends by itself and makes way once the game has drawn
  await open(1600, 480);
  await until(overlay, 4000);
  await until(async () => (await t()) > 0.05, 8000);
  await evaluate(`(() => { const v = document.querySelector('#lelelilo-intro video'); window.__introEnd = null; v.addEventListener('ended', () => { window.__introEnd = v.currentTime; }, { once: true }); })()`);
  await square(1600, 480, 'a very wide window');
  await until(async () => (await t()) >= 2.95, 6000);
  await shot('at-3s.png');
  await until(async () => (await evaluate('window.__introEnd')) !== null || !(await overlay()), 15000);
  const ended = await evaluate('window.__introEnd');
  check(ended !== null && ended > 4.6, `left alone, its picture ends by itself at ${ended?.toFixed(2)} s`);
  check(await until(async () => !(await overlay()), 120000), 'and it makes way once the game has drawn its first frame');
  check(await until(async () => (await state()) !== null, 10000), 'to the game');

  // 3. Started silent, it stays silent
  await open(1280, 720, 'state&mute');
  await until(async () => (await t()) > 0.05, 8000);
  const [muted, volume] = await evaluate(`(() => { const v = document.querySelector('#lelelilo-intro video'); return [v.muted, v.volume]; })()`);
  check(muted || volume === 0, `started silent (?mute) it is silent (muted ${muted}, volume ${volume})`);
  console.log(`  the picture at about 3 s is in ${out}`);
} catch (e) {
  check(false, e.message);
} finally {
  await browser?.close(); server.close();
}
const ok = results.every(Boolean);
console.log(ok ? '✓ the studio intro plays first, holds its skip, ends by itself and keeps presses from the game' : '✗ the studio intro is not right');
process.exit(ok ? 0 : 1);
