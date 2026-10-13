# Setting Gamemaker up: instructions for a coding agent

You are a coding agent (Claude Code or Codex), and a person has asked you to set Lelelilo Gamemaker up for them.
Read this file and the [top-level README](../README.md) in full first. The person can read it too: it says what
you will do, and what you must not.

What is in the repository is one working install, the author's, with its names replaced by placeholders. Your
job is to fit it to this person's machines and project without loosening the fence.

## Before you start: where you are, which side is yours, when to ask

- **You are on the agent's machine**, the one that will run the prompts. You were started in a new folder of
  your own and cloned this repository into it. That folder is outside the project the runs will work on, and it
  stays outside: while a working copy has another repository inside it, the prompts wait and `watch.py check`
  says FIX.
- **The agent's side is yours:** sections 3a, 3b, 3d and 3e.
- **The board's side (3c) is yours only when the board goes on this machine too** (section 4). When it is on
  another machine, you do not log in there, and you do not ask for a way in. Give the person the commands of
  3c to run on the server, in order, with their values in them, and wait until they say the board is up.
- **The agent's password is made on the board's machine.** `./deploy/make-credentials.sh` writes it to
  `var/credentials.txt` there, on the line that starts with `agent`. Ask the person for it once, when you write
  the watcher's settings file (3d), and tell them first that it will then be in this session's transcript.
  Section 6 says where the password may be.
- **Ask first, every time,** before a command with sudo, before a `git push`, and before you write anything
  outside the places where the table below says "yes".

What the setup writes, and where:

| Machine | Where | What | Write without asking? |
|---|---|---|---|
| agent's | your clone | what you adapt in it (section 5) | yes |
| agent's | `~/.config/gamemaker/env` | the watcher's settings, with the password, mode 600 | yes |
| agent's | `~/.local/share/gamemaker/` | `watch.py`, and a test script for `OUTSIDE_SANDBOX` if the project needs one | yes |
| agent's | `~/.cache/gamemaker/` | written by `watch.py` itself: its lock, the last session, and during a run the note of each working copy's git setup | yes |
| agent's | `~/.config/systemd/user/` | `gamemaker-watch.service` and `gamemaker-watch.timer`, and the link `systemctl --user enable` makes | yes |
| agent's | the project the runs work on | `GAMEMAKER.md` and its commit; the ref `refs/gamemaker/v/0`; the lines `watch.py` adds to `.git/info/exclude`; `.gamemaker-probe`, which `check` makes and removes. If the person chooses it, a second clone of the project beside it | no: ask |
| agent's | the site's working copy | `v/0/` and its commit, which is pushed to the public repository; the lines `watch.py` adds to `.git/info/exclude`. A clone of the site's repository, if there is none yet | no: ask, and ask again before the push |
| agent's | the system | `bubblewrap` and `socat`, lingering, the AppArmor profile | no: sudo, ask |
| board's | the clone there | the person's values in `VPS/deploy/Caddyfile.gamemaker` and `VPS/deploy/gamemaker.service` | not yours. On one machine: ask |
| board's | `var/` beside `server.py` | `credentials.txt` and `caddy-users` from `make-credentials.sh`; the database and, with notifications, the push key, which `server.py` makes when it starts | not yours. On one machine: ask |
| board's | `~/.config/systemd/user/` | `gamemaker.service`, copied by `install-service.sh`, and the link `systemctl --user enable` makes | not yours. On one machine: ask |
| board's | `/etc/caddy/Caddyfile` | one block, put in by `setup-caddy.sh`, which keeps a copy of the old file beside it and reloads Caddy | not yours. On one machine: sudo, ask |

The tests and `watch.py check` also use the system's temporary folder, and remove what they make there. `check`
starts Claude Code, which keeps its own files under `~/.claude/`.

## 1. What you are setting up

Two parts, on one or two machines:

| Part | Where | What |
|---|---|---|
| The board | a server | `VPS/server.py` behind Caddy. The page where prompts are written, and the API the watcher calls. |
| The watcher | the agent's machine | `VPS/agent/watch.py` on a systemd timer. It runs each prompt with `claude -p`, fenced in, in the project's source, and pushes the built site. |

Be straight with the person about what fits before you start:

- **The runs are Claude Code.** The watcher starts `claude -p` with flags that need Claude Code 2.1.259 or later.
  Codex can do this setup. It cannot be the one that runs the prompts: Claude Code has to be installed and logged
  in on the agent's machine.
- **Linux with systemd**, on both sides. The sandbox is bubblewrap, and the services are systemd user units.
- **A static web build on GitHub Pages.** The board accepts a result only at an https address on github.io. The
  project must build to something GitHub Pages can serve.
- **The project is a git repository**, and the site is best in a repository of its own, beside it. Branches need
  that.
- **It was made for one game**, a Rust and WebGPU one. Section 5 lists what is specific to it.

## 2. Ask first, and wait for the answers

1. **Is the board on another machine, a server with a domain, or on this one?** See section 4 for the second.
2. **Which project do the runs work on, and where is it on this machine?** How is it built, how is it tested,
   and what does a good result look like? You will write that down for the runs as `GAMEMAKER.md`.
3. **Where is the build published?** Which GitHub repository, which GitHub Pages address, and does this user
   already push there from this machine?
4. **Where do the runs work:** in the project's own folder, or in a second clone of it on its own branch?
   Suggest the clone if the person keeps working in the folder themselves. Never a git worktree.
5. **The limits.** A cost cap per prompt: suggest `MAX_COST=10`, the default, in dollars at API prices. `0`
   means no cap. The cap is Claude Code's (`--max-budget-usd`), and a run can end somewhat past it. And the time
   limit, `RUN_TIMEOUT`: 7200 seconds, 2 hours, by default. The watcher enforces that one itself.
6. **Who may open the board:** which IP addresses, and which logins for the page beside `brother`.
   `make-credentials.sh` always makes two logins: `brother`, for the page, and `agent`, for the watcher. Any
   other is added with `--add NAME` and uses the page like `brother`. The author's board has one, `guest`.
7. **Who the runs commit as** (`GIT_NAME`, `GIT_EMAIL`). The address shows in the site's public history.

## 3. The steps, in order

### a. See that the code works here

```bash
cd VPS
python3 -m unittest discover -s tests     # 71 tests: none may fail before you go on
python3 try-local.py                      # optional: the board on 127.0.0.1:8091, to show the person
```

The tests need Python 3 and git. With the `cryptography` package all 71 run, and the last line is `OK`.
Without it 64 run and 7 are skipped, and the last line is `OK (skipped=7)`. The 7 are the notification tests in
`tests/test_push.py`: the message a browser gets when a prompt is done or failed (encrypted for that browser,
signed with the board's key, in its language), signing up and signing off, a subscription that is gone, the
test message, which push services are accepted, and the board's key (made once, kept private). A skip is not a
failure. It means that nothing has tested notifications on that machine, and that the board would run there
with notifications off. If a test fails, stop and say exactly what failed.

`try-local.py` has no login. It is for looking at the board, and for a dry run of the watcher against its second
port (`GAMEMAKER_URL=http://127.0.0.1:8092`). It is never the real front. Stop it when you are done.

### b. Check what the machines have

Do not install anything without asking. Say what is missing and what you would run. You can look at this
machine only: for a board on another machine, tell the person what its row says.

| Machine | Needs |
|---|---|
| The board's | Python 3. Caddy 2, with its config in `/etc/caddy/Caddyfile`, and a DNS name that points at the server. systemd user services, with lingering on for the user. For notifications, `python3-cryptography`. |
| The agent's | Python 3.8 or newer. git. Claude Code 2.1.259 or later, logged in. `bubblewrap` and `socat`. systemd user services, with lingering on. This user's own push access to the site's repository. The project's own build and test tools. |

These need sudo. Show the person each command, say why, and let them decide:

- `sudo apt-get install bubblewrap socat`
- `sudo loginctl enable-linger $USER`, so the timer runs with nobody logged in
- `sudo bash deploy/setup-caddy.sh`, on the board's machine, which rewrites one block of `/etc/caddy/Caddyfile`
  and reloads Caddy
- on Ubuntu 24.04 and later, the AppArmor profile for bubblewrap (below)

**AppArmor.** If `sysctl kernel.apparmor_restrict_unprivileged_userns` says 1, bubblewrap cannot make user
namespaces and every command of a run fails. The fix is the profile from
[Claude Code's sandboxing docs](https://code.claude.com/docs/en/sandboxing);
[`MY_MACHINE/system/install-bwrap-profile.sh`](../MY_MACHINE/system/install-bwrap-profile.sh) is the author's
copy of it, for Ubuntu 26.04, where Ubuntu's own profile of the same name has to be switched off first. Tell the
person what it is before they run it: the profile is `flags=(unconfined)` with one rule, `userns`. It lets the
sandbox start. It is not a confinement layer, and it applies to everything on the machine that runs
`/usr/bin/bwrap`. Do not switch the restriction off for the whole system instead.

### c. The board

On the board's machine, in [`VPS/`](../VPS/). [`VPS/README.md`](../VPS/README.md) has the detail, and its "Make
it yours" table says where each name goes.

When the board is on another machine, this part is the person's. Give them this list with their values written
in, and do none of it yourself:

```bash
# on the server, as the user the board will run as
git clone https://github.com/lelelilo-studios/gamemaker && cd gamemaker/VPS
# edit deploy/Caddyfile.gamemaker: the board's name on the first line of the block, and the allowed IPs
#   (the agent's machine must be one of them: the watcher calls the board from there)
# edit deploy/gamemaker.service: the two paths (this folder), --origin, --push-subject, --game-url
./deploy/make-credentials.sh              # the logins, into var/ (it needs caddy, for the hashes)
./deploy/install-service.sh               # the board as a user service on 127.0.0.1:8090
sudo loginctl enable-linger $USER         # so that it starts on boot
sudo bash deploy/setup-caddy.sh           # the block in /etc/caddy/Caddyfile, and a reload of Caddy
curl -s http://127.0.0.1:8090/api/state   # answers {"error": "not logged in"}
```

The same steps, with what each one is:

1. The person's values go in first: the board's name and the allowed IPs in `deploy/Caddyfile.gamemaker`; the
   paths, `--origin`, `--push-subject` and `--game-url` in `deploy/gamemaker.service`.
2. `./deploy/make-credentials.sh`: the logins, into `var/`. It prints no password; they are in
   `var/credentials.txt`, which only this user can read. The line that starts with `agent` has the password
   the watcher needs on the other machine.
3. `./deploy/install-service.sh`: the board as a user service on 127.0.0.1:8090.
4. `sudo bash deploy/setup-caddy.sh` (ask first).
5. Check: from an allowed address the page asks for the login and then opens. From any other address it answers
   403. On the server, `curl -s http://127.0.0.1:8090/api/state` answers `{"error": "not logged in"}`: the
   board itself takes nobody without the header.

### d. The watcher

[`VPS/agent/SETUP.md`](../VPS/agent/SETUP.md) is the text for this part. It was written to be pasted into Claude
Code started in the project's folder, so it says "this project" and "this folder". You are in your clone, not
there: read those words as the project the person named, and work in it by its path. Do what the text says
from "Set up the Gamemaker watcher" down, whether you are Claude Code or Codex. Its four placeholders are the
person's answers: the board's address, the site's repository, the GitHub Pages address, and the agent's
password, which you ask for when you write the settings file and not before. It covers, in this order:

1. the two questions it asks (where the runs work, the cost cap);
2. the checks, and the AppArmor step;
3. the two working copies: the source and the site, side by side, each with a `.git` folder of its own, clean,
   no submodules;
4. `GAMEMAKER.md` at the top of the project: how to build, how to test, how to publish version `<id>`, and a map
   of the code. Every run reads it first;
5. the starting point, frozen in `v/0/` on the site, and its source recorded as `refs/gamemaker/v/0`. That is
   a commit pushed to the site's public repository: show the person what it holds, and ask before you push;
6. the settings in `~/.config/gamemaker/env`, mode 600, before anything needs the password.
   [`MY_MACHINE/env.example`](../MY_MACHINE/env.example) is a template;
7. the watcher itself into `~/.local/share/gamemaker/watch.py`, downloaded from the board. The command in
   `SETUP.md` reads the password from the settings file and gives it to curl on its standard input, so it is
   never on a command line. Read the file before you run it. The board serves its own `agent/watch.py` at
   `/agent/watch.py`: on a board set up from this repository, that is `VPS/agent/watch.py` here;
8. `watch.py check`, until no line says FIX, and `watch.py check --try "<command>"` for the build and for each
   test, until they work as they do for the person;
9. the timer, from [`MY_MACHINE/systemd/`](../MY_MACHINE/systemd/).

A test that cannot run in the sandbox at all (a browser that needs a screen or the GPU) goes in a script outside
both working copies, listed in `OUTSIDE_SANDBOX`. `SETUP.md` has the rules for such a script, and
[`MY_MACHINE/outside-sandbox/`](../MY_MACHINE/outside-sandbox/) is the author's, for his game. Write the
person's own. Do not copy that one as it is.

### e. Verify, then report

Section 7.

## 4. Everything on one machine

The author runs two machines. Nothing in the code needs two, but one machine is not a setup that has been run:
treat it as new work, test it, and say so in your report.

- The board still needs a front that asks for a login and sets `X-Gamemaker-User`. `server.py` has no login of
  its own and believes that header. Caddy on the same machine does it, with the same block, a local address in
  place of the name, and that address given to `server.py` as `--origin`.
- The watcher only needs an address to call: `GAMEMAKER_URL` can be an `http://127.0.0.1:...` one.
- Keep the board's folder, with its `var/`, out of both working copies and out of `SANDBOX_READ` and
  `SANDBOX_WRITE`. Keep `127.0.0.1`, `localhost` and the board's own address out of `SANDBOX_DOMAINS`.
- Check that a run cannot reach the board's own port. Do it with the board running, in two steps:
  1. In your own shell, outside the sandbox: `curl -s -m 5 http://127.0.0.1:8090/api/state`. It must print
     `{"error": "not logged in"}`. That shows the port is open, and what an answer from the board looks like.
     If it prints something else, the board is not up: put that right first, because the next step proves
     nothing against a closed port.
  2. The same command as a run would run it:
     `watch.py check --try "curl -s -m 5 http://127.0.0.1:8090/api/state"`. Look at the output under the
     line for that command. What decides is whether the board's answer is in it. If it has `not logged in`,
     a run can reach the board and could put any name in the header itself: stop, and tell the person that
     this setup is not safe as it is. Expect instead that curl fails, with an error of its own and no answer
     from the board.

  A failure in step 2 counts only because step 1 worked: the same command, the same port, and the sandbox is
  the one thing that differs. `check --try` judges a command by its exit code, so a curl that failed is
  marked FIX, and the check ends with "fix the lines marked FIX". For this one command that is the result to
  expect. Run the plain `watch.py check` afterwards: that one must end in "all set".
- Whoever writes the prompts must be able to open the page. On one machine with no domain, that is this machine
  and its own network.

## 5. What to adapt

| What | Where | Note |
|---|---|---|
| The board's name | `VPS/deploy/Caddyfile.gamemaker`, `VPS/deploy/gamemaker.service`, `GAMEMAKER_URL` | `board.example.com` is a placeholder. In `setup-caddy.sh` it only labels the markers: change all of them together, or none. |
| The allowed IPs | `VPS/deploy/Caddyfile.gamemaker` | The three there are documentation addresses. |
| Paths and the user | `VPS/deploy/gamemaker.service`, the watcher's settings, any script in `OUTSIDE_SANDBOX` | `/home/you` is a placeholder. `PATH`, `CLAUDE_BIN` and `OUTSIDE_SANDBOX` take full paths, with no `~`. |
| The site | `--game-url`, `PAGES_URL`, `SITE_DIR` | The person's own repository and GitHub Pages address. |
| The sandbox's lists | `SANDBOX_READ`, `SANDBOX_WRITE`, `SANDBOX_DOMAINS` | The defaults are for Rust: `~/.cargo`, `~/.rustup`, and the hosts crates.io, index.crates.io, static.crates.io, static.rust-lang.org. Setting a list replaces its default. Add one path or one package host at a time, when `check --try` shows that a command needs it. |
| Files that must not reach the site | `SITE_FORBIDDEN` | The default names Rust and shader sources, scripts, maps and notes. Set it to what the project's sources are called. It goes by file name. |
| Who the runs commit as | `GIT_NAME`, `GIT_EMAIL` | The default name is `Lelelilo Gamemaker`, the author's. The default address, `gamemaker@example.com`, is a placeholder. Set both to the person's. |
| The words | `RULES` and `BRIEF` in `watch.py`, the page in `VPS/web/` | They name Lelelilo and the author's studio site. Change the words to the person's. Leave what the rules forbid as it is. The Lelelilo name and the drawings are the author's and not under the MIT licence: see [`VPS/web/ARTWORK.md`](../VPS/web/ARTWORK.md). |
| The tests of the build | `GAMEMAKER.md`, a script in `OUTSIDE_SANDBOX` | The author's browser checks look for his game's page. |

## 6. What must never be changed

The prompts come from a web page, so the fence has to hold whatever a prompt says. If something does not work
inside it, stop and tell the person. Do not make it work by taking a piece out.

- **The command line and the settings in `watch.py`** (`claude_cmd()` and `settings()`): `--restricted`, the six
  tools, `--disallowedTools mcp__*`, `--strict-mcp-config`, `--permission-prompts none`, `disableAllHooks`,
  `disableBypassPermissionsMode`, `blockReadsOutsideWorkingDirectories`, the sandbox with `failIfUnavailable`
  and `allowUnsandboxedCommands: false`, the deny-read list, the deny-write of `.git/config` and `.git/hooks`,
  `strictAllowlist`. Take none of them out, and soften none.
- **No `SANDBOX=off`.**
- **`CLAUDE_ARGS` is for things like `--model`.** Nothing there that touches tools, permissions, settings, MCP
  or folders.
- **Never in `SANDBOX_READ` or `SANDBOX_WRITE`:** the home folder, `~/.ssh`, `~/.config`, `~/.cargo/bin`, or any
  folder that is in `PATH`. **Never in `SANDBOX_DOMAINS`:** a general host such as github.com, or the board's
  own address.
- **The git guard:** `put_back_git_setup` and what calls it, `core.hooksPath=/dev/null` in the watcher's git,
  the stopping of what a run left running. And no git worktree as a working copy, no submodules, no repository
  inside another.
- **A script in `OUTSIDE_SANDBOX`** stays outside both working copies and outside `SANDBOX_WRITE`. It runs
  nothing from the working copies, takes a number or nothing, and trusts nothing in its environment.
- **The board listens on 127.0.0.1 only**, behind a front that sets `X-Gamemaker-User` itself and never takes it
  from the client. Port 8090 is never opened to the outside. `try-local.py` is never the front.
- **A test or a check that fails is not made to pass.** Show the person the output and the cause. To put it
  right, change only the watcher's settings file, the service files and the `GAMEMAKER.md` you wrote. Never
  change a test, `watch.py` or any part of the fence to make a failure go away.
- **The agent's password** may be in these places and no others: `var/credentials.txt` on the board's machine;
  `~/.config/gamemaker/env` on the agent's machine, mode 600; and, because the person gave it to you, this
  session's transcript. Never on a command line, where `ps` and the shell's history show it. Never in git, and
  never in a file other users can read. Tell the person in your report that the transcript holds it, and how
  to replace it: `./deploy/make-credentials.sh --new agent` and `sudo bash deploy/setup-caddy.sh` on the
  board's machine, then the new password into the settings file with an editor.
- **No sudo without asking**, each time.
- **No `git push` without asking**, each time. Show what would go out, and where to. The setup has one: the
  starting point, `v/0/`, to the site's public repository.
- **Nothing written without asking** outside the places where the table at the top says "yes".

## 7. How to verify

```bash
cd VPS && python3 -m unittest discover -s tests                 # 71 tests: OK, or OK (skipped=7) without cryptography
cd VPS && python3 -m unittest discover -s tests -k planted      # the two about git tampering, by themselves
python3 ~/.local/share/gamemaker/watch.py check                 # ends in "all set"
python3 ~/.local/share/gamemaker/watch.py check --try "<the build command>"
systemctl --user list-timers gamemaker-watch.timer              # scheduled
journalctl --user -u gamemaker-watch -n 20                      # runs, without errors
```

`watch.py check` starts Claude for real, as a run would, and costs a few cents. It must show `ok` on each of these:
only the six tools, no MCP servers, a write in the project works, nothing written to the home folder reached it,
the board's password could not be read, the git settings and hooks of the working copies could not be changed,
and a commit works. [`MY_MACHINE/watch-check.txt`](../MY_MACHINE/watch-check.txt) is a sample of a good one,
with a note at its top on how to read it: the check goes by what is on the disk, not by what Claude says.

Then the real thing: have the person send one small prompt from the page, and follow it with
`journalctl --user -u gamemaker-watch -f` until the board shows the result, playable.

Before you finish, stop everything you started for trying (`try-local.py`, any test server), and check that
nothing of it is still listening.

## 8. What to report

- What runs where: the board's address, the folders, the service and timer names.
- Where the runs work, on which branch, and where the site is published.
- The limits that are set: the cost cap, or that there is none, and the time limit.
- What you added to the sandbox's lists, and why each one was needed.
- Any script outside the sandbox, and what it does.
- Every command that was run with sudo, and every push.
- What you wrote or committed in the project and in the site's working copy.
- Where the agent's password is, this session's transcript included, and how to replace it.
- How many tests ran and how many were skipped.
- What you could not verify.
- How to follow the log, how to pause (`systemctl --user stop gamemaker-watch.timer`), and how to update the
  watcher (read what changed, install it, run the check again).
