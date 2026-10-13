# MY_MACHINE

The agent's machine: where the watcher (`VPS/agent/watch.py`, downloaded from the board) runs each prompt with
Claude Code and publishes the build to lelelilo-world-web.

This folder says how that side is set up and runs at my place, with everything needed to set it up again except
the secrets and the game's source, which stay on the machine. It is a worked example, for one game (Rust,
WebGPU, built for the web) on one machine (Ubuntu). To set up your own, start from
[`docs/AGENT_SETUP.md`](../docs/AGENT_SETUP.md) and take from here what fits. In the files, `/home/you` stands
for the home folder and `board.example.com` for the board's address.

## What runs here

Every 30 seconds a systemd user timer starts `watch.py run`. It asks the board whether a prompt is waiting; if one
is, it claims it, starts Claude Code fenced in, in the game's source, and when the run is done pushes the site.
Nothing else is running between prompts.

| | Where | |
|---|---|---|
| The watcher | `~/.local/share/gamemaker/watch.py` | The file the board serves, unchanged. `VPS/agent/watch.py` in this repository is that file with three things changed for publishing: the board's name is `board.example.com`; the default commit address is `gamemaker@example.com`; and the comment at the top was corrected (what it says about the sandbox, `CLAUDE_ARGS`, the cost cap and the time limit, and a line for `SANDBOX`, which it did not list). It is not copied into this folder. Installed: `gamemaker-watch/4`. The version before the last update is kept beside it as `watch.py.before-update`. |
| Its settings | `~/.config/gamemaker/env`, mode 600 | The agent's password for the board is in this file. It is not the only place that holds it: see [What this does not protect against](../README.md#what-this-does-not-protect-against). [`env.example`](env.example) is a template of the file: the password and this machine's names taken out, a comment on each part, and `MAX_COST` at the watcher's default. |
| The timer | `~/.config/systemd/user/gamemaker-watch.{service,timer}` | [`systemd/`](systemd/). Lingering is on for the user (`sudo loginctl enable-linger <user>`), so it runs with nobody logged in. |
| Its state | `~/.cache/gamemaker/` | The lock, the last session, and during a run the note of each working copy's git setup. Never in git. |
| WORK_DIR | `~/Projects/gamemaker-world` | The game's source, where the runs work. A clone of its own of the owner's folder (`~/Projects/gamemaker`, which is its `origin`), not a git worktree. Never pushed (`PUSH_SOURCE` is not set). |
| SITE_DIR | `~/Projects/lelelilo-world-web` | A clone of the public site repository, pushed by the watcher with the user's own GitHub access (SSH). |
| The browser checks | `~/.local/share/gamemaker/test-in-browser.sh` and `browser-tests/` beside it | The one thing a run starts outside the sandbox: [`outside-sandbox/`](outside-sandbox/). |

The owner's own work on the game is in `~/Projects/gamemaker`, on `main`. The runs never touch it.

## Branches and where each version's source is

`WORK_BRANCH=world`: the board's main line is the git branch `world` in WORK_DIR, its branch B is `world-b`, C is
`world-c`, and so on, each made by the watcher. The source of version `<n>` is the ref `refs/gamemaker/v/<n>` in
WORK_DIR, recorded by the watcher after each published run. The refs exist only there: a clone does not copy them
(`git fetch <WORK_DIR> 'refs/gamemaker/*:refs/gamemaker/*'` does).

- `v/0` and `v/1` were recorded by hand (2026-10-09), and are not the commits one would expect: `v/0` is the
  owner's `main` as it was then, and `v/1` is the commit run 2 started from. Each has the same game as its page,
  with the build tools as they were fixed after run 1, so that a branch from either can publish.
- From `v/2` on, the ref is the run's own commit, the one its page names as its build.
- The build ids on the pages of `v/0` and `v/1` are not those commits: the first was made before the source was
  a git repository, the second names a commit whose message was amended since.

The site has one line: every version in `v/<n>/`, the newest build at its root, whichever branch it came from.

After a run on a branch, WORK_DIR stays on that branch. To work in it by hand: stop the timer, make sure no run is
in progress, look which branch is checked out, and start the timer again after. A fix merged into `world` does not
reach the other branches.

## The fence, as it is here

The fence is the watcher's (see [the top-level README](../README.md#the-fence) and the header of `watch.py`).
What this machine adds to its settings, and why:

- `SANDBOX_READ` also has `~/.local/share/binaryen`: the web build runs Binaryen's `wasm-opt` from there.
- `SANDBOX_WRITE` also has `/data/scratch/gamemaker-world`: the build folder. `target` in WORK_DIR is a link to
  `/data/scratch/gamemaker-world/target`, to keep builds off the main disk.
- `OUTSIDE_SANDBOX` is the browser checks' script. A browser (the Chromium snap) cannot start inside the sandbox.
- `MAX_COST=0`: no cap on what a prompt may spend, the owner's choice. The watcher's default is 10 (dollars at
  API prices). The two-hour limit (`RUN_TIMEOUT`, 7200 seconds by default) still holds.

### The script outside the sandbox

[`outside-sandbox/test-in-browser.sh`](outside-sandbox/test-in-browser.sh) opens the built game in a headless
Chromium and checks that it starts, draws, plays and sounds, and that the studio intro keeps its rules. With no
argument it checks the build in WORK_DIR; with an iteration's number, the site's working copy (its root and
`v/<n>/`). It leaves pictures in WORK_DIR's `shots/` for the run to look at.

A run can start it and cannot change it. It is written so that starting it gives a run nothing but what it prints
and those pictures:

- it takes one number or nothing, and throws the caller's environment away;
- it runs its own copies of the checks ([`browser-tests/`](outside-sandbox/browser-tests/)), never the working
  copy's, and nothing of the working copies is run as a program: their built files are only served to the browser;
- `serve.mjs` serves only real files inside the working copy (it asks where the file it opened really is);
  `chrome.mjs` gives the page no internet and leaves Chromium's own sandbox on for the page (its GPU process
  runs without it, `--disable-gpu-sandbox`: WebGPU runs in software there); `deliver.mjs` writes the pictures as
  new files in the real `shots/`, never through a link; the page is shown no gamepad.

`web-smoke.mjs`, `intro-check.mjs` and `reap.mjs` are the same files as in the game's `tools/`; when they change
there, they are copied here by hand. The other three differ on purpose.

### What was set up by hand for the runs

- **AppArmor** ([`system/install-bwrap-profile.sh`](system/install-bwrap-profile.sh), run by the owner with sudo):
  Ubuntu 26.04 strips every capability from what runs through `bwrap`, and Claude Code's sandbox then cannot start
  (`apply-seccomp: … Permission denied`). Ubuntu's `bwrap-userns-restrict` profile was switched off and the profile
  from Claude Code's documentation installed in its place. That profile is `flags=(unconfined)` with one rule,
  `userns`: it only lets bubblewrap create user namespaces, so that the sandbox can start. It is not a confinement
  layer, and nothing here is fenced in by AppArmor. It is also machine-wide: everything that runs `/usr/bin/bwrap`
  now gets it in place of Ubuntu's stricter one. The undo is at the top of the script.
- **A commit hook** ([`git/commit-msg`](git/commit-msg)) in `.git/hooks/` of both working copies: it takes a
  co-author line out of a commit's message. The watcher already switches that line off; this is the backstop.
- **What git leaves out** in each working copy's `.git/info/exclude`
  ([`git/exclude.work-dir`](git/exclude.work-dir), [`git/exclude.site-dir`](git/exclude.site-dir)): the names
  Claude Code and its sandbox keep at the top of a folder they work in. The watcher adds the same lines by itself.
- **Packages and tools**: `bubblewrap` and `socat` for the sandbox; the Rust toolchain with the
  `wasm32-unknown-unknown` target; `wasm-bindgen` at the version in the game's `Cargo.lock`
  (`cargo install wasm-bindgen-cli --version 0.2.129`); Binaryen unpacked under `~/.local/share/binaryen`; Node;
  the Chromium snap. In WORK_DIR, once: `cargo xtask setup`.

As of 2026-10-09: Ubuntu 26.04.1, Claude Code 2.1.296, Python 3.14, rustc 1.98.1, wasm-bindgen 0.2.129, Binaryen
133, Node 24.21, Chromium 155 (snap), bubblewrap 0.11.1, socat 1.8.1.

One thing about this machine: its keyboard shows up as a joystick (`/dev/input/js0`, its media keys), which
Chromium offers to pages as a gamepad once one of them is pressed. That is why the checks show the page no gamepad.

## Check, pause, update

```bash
python3 ~/.local/share/gamemaker/watch.py check          # is all of it in order? ends in "all set"
python3 ~/.local/share/gamemaker/watch.py check --try "<command>"   # a command, run as a run would run it
journalctl --user -u gamemaker-watch -f                  # follow it
systemctl --user stop gamemaker-watch.timer              # pause (a run in progress goes on to its end)
systemctl --user start gamemaker-watch.timer             # and start again
```

[`watch-check.txt`](watch-check.txt) is a sample of the check's output: a real one from this machine
(2026-10-09), with the home folder, the machine's name and the board's address replaced.

To update the watcher: stop the timer and wait for a run in progress; download the new file beside the installed
one with the board's login (the download in [`VPS/agent/SETUP.md`](../VPS/agent/SETUP.md) reads the password
from the settings file, so it is not on a command line); read all of what changed; install it, keeping the old
one; run the check; on throwaway repositories, with a stand-in for Claude and for the board, try a clean run and
a run that tampers with git (its settings, its hooks, a repository inside a working copy): the second must fail,
be put back, push nothing and run nothing it planted; then start the timer.

## To set this side up again

([`docs/AGENT_SETUP.md`](../docs/AGENT_SETUP.md) is the general version of this, for any machine and any game.)

1. The packages and tools above, the AppArmor profile, lingering.
2. The two working copies, side by side: WORK_DIR as a clone of the game's source on branch `world`, with its
   `target` link and `cargo xtask setup`; SITE_DIR as a clone of lelelilo-world-web. In each, the hook and the
   exclude lines from [`git/`](git/). The version refs, fetched from the old WORK_DIR or recorded again.
3. `watch.py` from the board into `~/.local/share/gamemaker/`, and beside it `test-in-browser.sh` (mode 700) and
   `browser-tests/` from [`outside-sandbox/`](outside-sandbox/). The script has the machine's paths written in
   (`/home/you` here): put the real ones.
4. `~/.config/gamemaker/env` (mode 600) from [`env.example`](env.example), with the board's password, and the paths
   of that machine.
5. The units from [`systemd/`](systemd/); `watch.py check`, and each command of the game's build and tests with
   `check --try`; then `systemctl --user enable --now gamemaker-watch.timer`.

## What is not here, on purpose

- `~/.config/gamemaker/env` and the board's password; any token or key; Claude Code's own login.
- The game's source and its `GAMEMAKER.md` (what a run reads first: how to build, test and publish the game): they
  stay in WORK_DIR and in the owner's folder.
- `watch.py` itself (it is in `VPS/agent/`), and the copy from before the last update.
- The journal, and the watcher's state in `~/.cache/gamemaker/`.
