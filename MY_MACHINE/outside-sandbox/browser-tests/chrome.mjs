// Headless Chrome with WebGPU, driven over the DevTools protocol, for the browser checks.
//
// THIS COPY runs outside the sandbox of a Gamemaker run (see ../test-in-browser.sh): the browser is fixed, and the
// page it opens gets no internet (anything but this machine itself goes to a proxy that is not there).
//
// The checks run with no screen, so WebGPU runs on SwiftShader's software Vulkan. It doesn't always come up on the
// first launch (the GPU process can start late), so the adapter is probed patiently and Chrome is relaunched if it
// never appears.
//
// Every browser started here is closed again, whatever happens: a headless browser left running keeps drawing the
// game on the CPU for ever. Each one has a profile folder of its own whose name starts with `gmk-browser-profile-`, and that
// name is how it is found and killed if closing it politely fails (`node tools/reap.mjs` does the same from outside).
//
// CHROME picks the browser (default: `google-chrome` if there is one, else the Chromium snap). A snap can only use a
// profile under its own folder, so there it goes in ~/snap/chromium/common. Needs Node 22+ (global WebSocket, fetch).

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';

export const WEBGPU_FLAGS = ['--enable-unsafe-webgpu', '--enable-features=Vulkan', '--use-vulkan=swiftshader', '--use-webgpu-adapter=swiftshader',
  '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--disable-vulkan-surface', '--ignore-gpu-blocklist',
  '--disable-gpu-sandbox', '--disable-dev-shm-usage', '--proxy-server=http://127.0.0.1:1'];

export const PROFILE_PREFIX = 'gmk-browser-profile-';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const has = bin => spawnSync('which', [bin], { stdio: 'ignore' }).status === 0;

/** The browser to run, and the folder its profiles go in: fixed here, whatever the environment says. */
export function browser() {
  return { cmd: ['/snap/bin/chromium'], profiles: join(homedir(), 'snap/chromium/common') };
}

/** Whether any process still has `profile` on its command line. */
const alive = profile => spawnSync('pgrep', ['-f', profile], { stdio: 'ignore' }).status === 0;

/** Kills everything started with `profile` and removes it. Synchronous, so it can run as the tool exits. */
export function killProfile(profile) {
  for (let i = 0; i < 20 && alive(profile); i++) {
    spawnSync('pkill', [i < 4 ? '-TERM' : '-KILL', '-f', profile], { stdio: 'ignore' });
    spawnSync('sleep', ['0.25']);
  }
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* still held: `reap.mjs` clears it later */ }
  return !alive(profile);
}

// Whatever this tool has open is closed when it ends, however it ends
const open = new Set();
const closeAll = () => { for (const p of [...open]) { killProfile(p); open.delete(p); } };
process.on('exit', closeAll);
for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, () => { closeAll(); process.exit(130); });
process.on('uncaughtException', e => { console.error(e); closeAll(); process.exit(1); });
process.on('unhandledRejection', e => { console.error(e); closeAll(); process.exit(1); });

/** Ends the tool, closing everything, if it has not finished by itself in `seconds`. */
export function deadline(seconds) {
  setTimeout(() => { console.error(`✗ still running after ${seconds} s: giving up`); closeAll(); process.exit(1); }, seconds * 1000).unref();
}

async function launch(url, width, height) {
  const { cmd, profiles } = browser();
  const profile = mkdtempSync(join(profiles, `${PROFILE_PREFIX}${process.pid}-`));
  open.add(profile);
  const chrome = spawn(cmd[0], [...cmd.slice(1), '--headless=new', '--remote-debugging-port=0', ...WEBGPU_FLAGS, '--no-first-run',
    '--mute-audio', `--window-size=${width},${height}`, `--user-data-dir=${profile}`, url], { stdio: 'ignore' });
  chrome.on('error', () => {});
  const close = () => { killProfile(profile); open.delete(profile); };
  try {
    // The browser writes the port it chose into its profile
    let port = 0, page;
    for (let i = 0; i < 80 && !page; i++) {
      await sleep(250);
      try {
        port ||= Number(readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]);
        if (port) page = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(t => t.type === 'page');
      } catch { /* not up yet */ }
    }
    if (!page) throw new Error('the browser did not start');
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((r, e) => { ws.addEventListener('open', r, { once: true }); ws.addEventListener('error', e, { once: true }); });
    let id = 0;
    const send = (method, params = {}) => new Promise(res => {
      const i = ++id, h = e => { const m = JSON.parse(e.data); if (m.id === i) { ws.removeEventListener('message', h); res(m.result ?? { error: m.error }); } };
      ws.addEventListener('message', h); ws.send(JSON.stringify({ id: i, method, params }));
    });
    const evaluate = async expression => {
      const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result?.value;
    };
    return {
      send, evaluate,
      close: async () => {
        await Promise.race([send('Browser.close').catch(() => {}), sleep(1500)]);
        try { ws.close(); } catch { /* already gone */ }
        await sleep(300);
        close();
      },
    };
  } catch (e) {
    close();
    throw e;
  }
}

/** Opens `url` (a secure page: localhost) in a Chrome whose WebGPU works, relaunching it if the adapter never shows. */
export async function webgpuChrome(url, { width = 1280, height = 720, attempts = 3 } = {}) {
  let why = '';
  for (let attempt = 1; attempt <= attempts; attempt++) {
    const session = await launch(url, width, height);
    for (let i = 0; i < 10; i++) {
      if (await session.evaluate(`(async () => !!(await navigator.gpu?.requestAdapter()))()`).catch(() => false)) return session;
      await sleep(1000);
    }
    why = await session.evaluate(`JSON.stringify({ gpu: !!navigator.gpu, secure: isSecureContext, agent: navigator.userAgent })`).catch(e => e.message);
    await session.close();
    if (attempt < attempts) console.warn(`no WebGPU adapter yet (${why}); relaunching the browser (${attempt}/${attempts - 1})`);
  }
  throw new Error('no WebGPU adapter in the browser: ' + why);
}

/** The profile folders checks have left behind, wherever they may be. */
export function leftovers() {
  const places = [tmpdir(), join(homedir(), 'snap/chromium/common'), process.env.CHROME_PROFILE].filter(p => p && existsSync(p));
  return places.flatMap(dir => readdirSync(dir).filter(n => n.startsWith(PROFILE_PREFIX)).map(n => join(dir, n)));
}
