// Puts the pictures the browser checks made into `shots/` of a working copy, for the run to look at.
//
// It runs outside the sandbox, writing into a folder a run can write in: so it first stands in the real `shots/`
// (not wherever a link by that name leads), and then makes each picture as a new file there, never through a link.
//
//   node deliver.mjs <working copy> <picture> <name.png> [<picture> <name.png> ...]

import { closeSync, constants, existsSync, lstatSync, mkdirSync, openSync, readFileSync, unlinkSync, writeSync } from 'node:fs';

const [work, ...rest] = process.argv.slice(2);
const stop = why => { console.error(`✗ the pictures were not put in shots/: ${why}`); process.exit(1); };
if (!work || rest.length % 2) stop('usage');
process.chdir(work);
if (process.cwd() !== work) stop(`${work} is not where it says`);
try { const s = lstatSync('shots'); if (!s.isDirectory()) unlinkSync('shots'); } catch { /* not there yet */ }
try { mkdirSync('shots'); } catch (e) { if (e.code !== 'EEXIST') stop(e.message); }
process.chdir('shots');
if (process.cwd() !== `${work}/shots`) stop('shots is not a folder of the working copy');
for (let i = 0; i < rest.length; i += 2) {
  const [from, name] = [rest[i], rest[i + 1]];
  if (!/^[a-z0-9-]{1,40}\.png$/.test(name)) stop(`${name} is no name for a picture`);
  if (!existsSync(from)) continue;  // (the check that makes it failed before it could)
  try { unlinkSync(name); } catch { /* not there yet */ }
  let fd;
  try { fd = openSync(name, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o644); }
  catch (e) { stop(`${name}: ${e.code}`); }
  writeSync(fd, readFileSync(from));
  closeSync(fd);
  console.log(`  picture: shots/${name}`);
}
