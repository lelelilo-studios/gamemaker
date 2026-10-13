# The prompt board

`board.example.com` stands for the board's own address everywhere in this repository: see
[Make it yours](#make-it-yours) for where to put yours. To see the board without any of the setup below, run
`python3 try-local.py` (see the [top-level README](../README.md#try-the-board-locally)).

My brother writes a prompt on the page. An AI agent on another machine (Claude Code, run by `agent/watch.py`) picks it
up by itself, makes the game, publishes it to GitHub Pages and reports back. The result shows on the same page,
playable in a frame next to the prompt. Each prompt is **Waiting**, **Working**, **Done**, **Failed** or **Cancelled**
(and **Stuck?** when the agent has gone quiet for 30 min while working). The page has the night look of
[lelelilo.com](https://lelelilo.com): midnight blue, brass rims, a golden glow and stitched seams, with the sewing-needle
pointer. The agent is shown as **Neri**, the games Lelelilo of lelelilo.com's studio (courier cap, red scarf). While a
prompt is on its way, Neri works at a workbench: sewing, hopping over to playtest on an arcade, wiping its brow. It
naps when the watcher is away, and holds the game up when it is ready. The drawing is an SVG `<template>` in
index.html, posed by `data-mood` and, while working, by `data-beat` from `direct()` in app.js.

- **English or Español**, and a **Kids mode** (simpler words, bigger buttons, fewer details, idea starters), are chosen
  in Settings and kept per browser. All the words are in `web/words.js`. A prompt carries its language and Kids mode,
  and the agent writes its title, notes and summary to match.
- **Notifications** (`push.py`, `web/sw.js`): each browser can switch on a notice for when a prompt is done or failed,
  even with the page closed. It uses Web Push with the board's own key (`var/vapid.pem`, made on first start) and
  needs Ubuntu's `python3-cryptography`; without it, the board runs with notifications off. On an iPhone it works
  from the Home Screen only.

The game it starts from, at my place, is [lelelilo-world-web](https://lelelilo-studios.github.io/lelelilo-world-web/)
(Lelelilo 2D's web build; its source is not published). Under the prompts, the page shows it as the **starting point**,
so there is something to play before the first prompt, and while a prompt is on its way ("Play the latest version
meanwhile"). The site's root always holds the newest version, every result is frozen in `v/<id>/`, and the starting
point in `v/0/`. Until a first result is published, the starting point is the root. The address is `--game-url` in
`deploy/gamemaker.service`.

```
browser / watcher ─HTTPS─▶ Caddy (board.example.com)
                            1. only the allowed IPs, others get a 403 that shows their IP
                            2. login (basic auth): brother, guest, agent
                            3. → 127.0.0.1:8090, with the login name in X-Gamemaker-User
                           server.py (Python, no dependencies) + SQLite in var/
```

- `server.py`: the board and its API. Without the `X-Gamemaker-User` header it answers 401. It listens on 127.0.0.1 only.
- `web/`: the page (no build step).
- `agent/watch.py`: the watcher on the agent's machine. The board serves the newest copy at `/agent/watch.py`.
  Claude works in the source (`WORK_DIR`) and publishes into a clone of the site (`SITE_DIR`), following the
  `GAMEMAKER.md` that the setup writes into the source. Its docstring explains the rest:
  - **Branches.** A prompt builds on the newest version of the branch in view, or on any version picked with "Build
    on this version" (or the starting point). Going on from a version that already goes on starts a new branch (B, C,
    ...). The server decides this when the prompt is sent (`Lineage` in server.py). On the agent's machine each branch
    is a git branch, and each version's source is `refs/gamemaker/v/<id>`. Branching stays hidden until the watcher
    reports `gamemaker-watch/3`. The page shows the tree two ways:
    - **the map**: one row per branch, every version a button sewn on its thread, in the column of its generation;
    - **the line**: the branch in view, from the starting point down, with summaries, and a row wherever another
      branch goes its own way.
  - **Each prompt is a new `claude -p` session.** It finds its way through GAMEMAKER.md (which includes a map of the
    code), NOTES.md and git. A prompt sent within 30 min of a finished one continues that session instead.
  - **Every run is fenced in, whatever the prompt says.** Claude runs in restricted mode with six tools and no
    MCP servers or hooks. Its commands run in Claude Code's sandbox, which can write only in the two working copies,
    the run's temporary folders and what `SANDBOX_WRITE` names (cargo's package caches by default), can't see the
    rest of home, and can reach only the package hosts. Claude Code is told to fail when the sandbox can't
    start. Two things are outside the sandbox, when the watcher's settings say so: a script in `OUTSIDE_SANDBOX`,
    and everything with `SANDBOX=off`. The [top-level README](../README.md#the-fence) lists every part of the
    fence, with its flag, and what the fence does not protect against.
  - **The system prompt keeps it to this game.** Its rules refuse any other request.
  - **Claude can't push.** The watcher pushes only the site (the source stays on the agent's machine) after a run
    that worked. It first checks that no source files went into the site. After a run that failed or was refused,
    it resets both working copies. A run starts only when they have no uncommitted changes.
  - **Git can't be turned against the watcher.** Its git runs outside the sandbox, and git runs programs a repository
    names. So runs can't write either working copy's `.git/config` or hooks, and what a run leaves running is stopped.
    Before the watcher's git touches a working copy again, its git setup is put back byte for byte and any nested
    repository is taken out. A run that changed any of it fails. See `put_back_git_setup` in watch.py.
  - **Runs work in the game's own project, as the agent's user.** They have the same tools, so they build and test
    like an interactive session. A test that can't run in the sandbox (GPU, screen) is a script outside the
    working copies, listed in `OUTSIDE_SANDBOX`.
  - **Limits:** a time limit (`RUN_TIMEOUT`, 2 hours by default), which the watcher enforces itself, and a cost
    cap (`MAX_COST`, in dollars at API prices: 10 by default, `0` for no cap; my own machine runs with `0`). The
    cap is passed to Claude Code as `--max-budget-usd`. Claude Code enforces it, and a run can end somewhat
    past it.
  - `watch.py check` tries the fence for real.
- `agent/SETUP.md`: the watcher's part of the setup, as a text for Claude Code on the agent's machine. It can be
  pasted there once its placeholders are filled in. I keep a copy with the password filled in as
  `var/agent-setup.txt`, which is not in git. The password is then in that file too, and in the transcript of
  the session it is pasted into: the first lines of `agent/SETUP.md` say more.
- `try-local.py`: the board on this machine alone, for trying it. No login: never the real thing.
- `deploy/`: logins, the Caddy block, the user service.
- `var/`: the database and the logins. It is not in git, and only you can read it.

## Logins
`./deploy/make-credentials.sh` always makes two: `brother` uses the page (send, cancel, send again), and `agent`
uses the API (claim, notes, done or failed). My board has a third, `guest`, for the page: such a login is added
with `--add NAME`.
Every login other than `agent` uses the page the same way, and the board shows who sent each prompt. The passwords are
in `var/credentials.txt`. After any of these, run `sudo bash deploy/setup-caddy.sh`:
- one more login for the page: `./deploy/make-credentials.sh --add NAME` (and `--remove NAME` to take it away);
- new passwords: `./deploy/make-credentials.sh --new` (or `--new brother`, `--new agent`). After `--new agent`,
  put the new password in the watcher's settings on the agent's machine (`~/.config/gamemaker/env`), or the
  watcher can no longer log in.

## Allowed IPs
203.0.113.10, 203.0.113.11 and 198.51.100.7. These three are documentation addresses, standing in for the real
ones: put your own. They are in `deploy/Caddyfile.gamemaker` (`@outside not remote_ip …`).
Edit the list and run `sudo bash deploy/setup-caddy.sh`.
When an IP changes, opening the site from the new one shows that IP on the 403 page.

## Make it yours
What is in this folder is my install with its names replaced. Do this first, before the commands of
[Setup](#setup-done-once) below: as they are here, the service file points at my board's folder
(`~/Projects/gamemaker`) and the Caddy block lets in three addresses that are nobody's. Set:

| Where | What |
|---|---|
| `deploy/Caddyfile.gamemaker` | The first line of the block, `board.example.com {`: your board's name. The `remote_ip` list: the addresses that may open it. |
| `deploy/gamemaker.service` | The two paths (the folder `server.py` is in), `--origin` and `--push-subject` (the address people open the board at, with `https://`), and `--game-url` (your game's GitHub Pages address). |
| `deploy/setup-caddy.sh` | Nothing has to change. The name there only labels the markers and the last line it prints. If you change it, change all of them together. |
| `agent/SETUP.md` | Nothing has to change in the file. Its placeholders, listed at its top, are filled in only in a copy that is pasted by itself, and such a copy is never put in git. |
| `agent/watch.py` | Nothing has to change: the board's address comes from `GAMEMAKER_URL` in the watcher's settings. The rules and the brief it gives Claude name the board and the Lelelilo studio in their wording; change that text to yours. |
| `web/` | The page carries the Lelelilo name, the Neri character and my drawings and icons. They are not under the MIT licence: see [`web/ARTWORK.md`](web/ARTWORK.md). Put your own there. |

`server.py` refuses a POST from a browser whose `Origin` is not one of its `--origin` addresses, so a board
opened at an address it was not told about can be read but not written to.

## Setup (done once)
After [Make it yours](#make-it-yours):
```
./deploy/make-credentials.sh          # the logins → var/
./deploy/install-service.sh           # the board as a user service on 127.0.0.1:8090
sudo bash deploy/setup-caddy.sh       # the board.example.com block in /etc/caddy/Caddyfile (other sites untouched)
```
`setup-caddy.sh` keeps its block between `# >>> board.example.com` / `# <<<` markers. It checks the new config
before using it, backs up the old file and reloads Caddy. It is safe to run again.

It needs Caddy on the server, with its config in `/etc/caddy/Caddyfile`, a DNS name that points at it, and
lingering on for the user (`sudo loginctl enable-linger $USER`), so that the board starts on boot.
Notifications also need Ubuntu's `python3-cryptography`.

## Running it
- `systemctl --user status gamemaker` and `journalctl --user -u gamemaker -f`. The log shows only changes and errors,
  not the polling.
- Back up `var/gamemaker.sqlite3`.
- My board runs from a folder of its own (`~/Projects/gamemaker`, with `var/` beside the code), not from a clone
  of this repository. That is the path `deploy/gamemaker.service` has until you put yours in.
- Tests: `python3 -m unittest discover -s tests`. They cover the API, push (a real encrypt and decrypt round trip, and
  the VAPID signature), and the watcher end to end with a stand-in for claude. There are 71. Without the
  `cryptography` package the 7 push tests are skipped, and 64 run.

## API (JSON; POST needs `Content-Type: application/json`, and a browser's Origin must be this site)
| Call | Who | |
|---|---|---|
| `GET /api/state` | both | everything for the page: prompts (the latest 5,000, with the start of their texts), branches, counts, when the agent was last seen, the game's starting point |
| `GET /api/prompts/{id}` | both | one prompt with its notes |
| `POST /api/prompts {text, base?, lang?, kids?}` | brother | send a prompt (`lang` en or es; `kids` true or false) |
| `POST /api/prompts/{id}/cancel`, `/retry` | brother | cancel a waiting one; send a failed, cancelled or stuck one again |
| `GET /api/agent/next` | agent | the oldest waiting prompt, or 204 (also counts as "the agent checked in") |
| `POST /api/agent/claim {id?}` | agent | take it: waiting → working (never the same prompt twice) |
| `POST /api/agent/heartbeat {prompt_id?}` | agent | still alive, still working |
| `POST /api/prompts/{id}/update {message?, status?, summary?, result_url?, title?, branch_summary?}` | agent | a note, or the end: `done`/`failed`. `result_url` must be https on github.io |
| `POST /api/push/subscribe {subscription, lang, kids}`, `/unsubscribe {endpoint}`, `/test {endpoint}` | both | notifications for this browser (`state.push.key` is the board's public key) |
