// Makes sure nothing a check started is still running: kills any browser whose profile is one of ours, removes the
// profile folders left behind, and shows what is left of anything that looks like a headless browser or a server.
//
//   node tools/reap.mjs

import { spawnSync } from 'node:child_process';
import { PROFILE_PREFIX, killProfile, leftovers } from './chrome.mjs';

let clean = true;
for (const profile of leftovers()) {
  const gone = killProfile(profile);
  console.log(`${gone ? 'removed' : 'COULD NOT STOP'} ${profile}`);
  clean &&= gone;
}
// (a browser whose folder is already gone)
// (only a browser started with one of our profiles: not whatever else has the name on its command line, such as the
// shell that started this)
const OURS = `user-data-dir=[^ ]*${PROFILE_PREFIX}`;
if (spawnSync('pgrep', ['-f', OURS], { stdio: 'ignore' }).status === 0) {
  spawnSync('pkill', ['-KILL', '-f', OURS], { stdio: 'ignore' });
  spawnSync('sleep', ['0.5']);
  clean &&= spawnSync('pgrep', ['-f', OURS], { stdio: 'ignore' }).status !== 0;
}
const ps = spawnSync('sh', ['-c', `ps -eo pid,etime,args | grep -E 'headless|http.server|xtask serve|${PROFILE_PREFIX}' | grep -v -E 'grep|reap.mjs' || true`], { encoding: 'utf8' });
const left = ps.stdout.trim();
console.log(left ? `still running (not all of it need be ours):\n${left}` : 'nothing of ours is left running');
process.exit(clean ? 0 : 1);
