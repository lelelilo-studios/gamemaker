// Serves a folder (the built game) on a port of this machine, for the browser checks.
//
// THIS COPY runs outside the sandbox of a Gamemaker run (see ../test-in-browser.sh), on folders a run can write
// in. So it serves only real files that are inside the working copy named in GAMEMAKER_WITHIN: it follows no link
// out of it (what was opened is asked where it really is), and with no GAMEMAKER_WITHIN it serves nothing.

import { closeSync, constants, fstatSync, openSync, readFileSync, readlinkSync, realpathSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, sep } from 'node:path';

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.wasm': 'application/wasm', '.svg': 'image/svg+xml',
  '.webmanifest': 'application/manifest+json', '.jpg': 'image/jpeg', '.png': 'image/png', '.webm': 'video/webm', '.mp4': 'video/mp4',
};
const LARGEST = 64 * 1024 * 1024;

/** Starts serving `dir` (or only a blank page, with no `dir`). `/__blank` is always an empty page. */
export async function serve(dir) {
  let root = null;
  if (dir) {
    const within = realpathSync(process.env.GAMEMAKER_WITHIN || '/nowhere/at/all');
    root = realpathSync(dir);
    if (root !== within && !root.startsWith(within + sep)) throw new Error(`${dir} is not inside ${within}`);
  }
  const server = createServer((req, res) => {
    let fd = null;
    try {
      const path = normalize(decodeURIComponent(req.url.split('?')[0])).replace(/^(\.\.[/\\])+/, '');
      if (!root || path === '/__blank') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end('<!doctype html><title>blank</title>'); return; }
      const file = join(root, path.endsWith('/') ? path + 'index.html' : path);
      fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
      const real = readlinkSync(`/proc/self/fd/${fd}`), stat = fstatSync(fd);
      if (!real.startsWith(root + sep) || !stat.isFile() || stat.size > LARGEST) throw new Error('not a file of the build');
      const body = readFileSync(fd);
      res.writeHead(200, { 'Content-Type': TYPES[extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
      res.end(body);
    } catch { res.writeHead(404); res.end(); } finally { if (fd !== null) closeSync(fd); }
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  return { url: `http://127.0.0.1:${server.address().port}`, close: () => server.close() };
}
