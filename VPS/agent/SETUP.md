This is the watcher's part of the setup, written as a text for Claude Code. The way in is the prompt in the
[top-level README](../../README.md#set-it-up-for-real-with-your-coding-agent): the agent it starts reads this
file from its clone, does what it says for your game's project, and asks you for the values. You fill in
nothing here. [`docs/AGENT_SETUP.md`](../../docs/AGENT_SETUP.md) has the whole setup, the board's side included.

The text can also be used by itself, as it was first written: pasted into Claude Code on the agent's machine,
started in the project of your game. Then put your own values in before you paste everything below the line:

| In the text | Put |
|---|---|
| `board.example.com` | the board's address |
| `<AGENT_PASSWORD>` | the agent's password, from `var/credentials.txt` on the board's machine, where `deploy/make-credentials.sh` wrote it |
| `<SITE_REPO>` | the GitHub repository that GitHub Pages publishes, as `owner/name` |
| `<PAGES_URL>` | the address GitHub Pages serves it at, like `https://your-name.github.io/your-game-web/` |

**Where the password ends up.** A copy of this text with the password in it must never go into git. Whichever
way you use, the coding agent is given the password: you paste it with this text, or the agent asks you for it.
So it is in the transcript of that session on the agent's machine, as well as in the watcher's settings file
there. Do this only on a machine whose transcripts you trust. Or replace the password when the setup is done:
on the board's machine `./deploy/make-credentials.sh --new agent` and `sudo bash deploy/setup-caddy.sh`, then
the new one into `~/.config/gamemaker/env` with an editor.

The text was written for a game that already publishes a web build to GitHub Pages from a repository of its
own, and for Claude Code: the runs themselves are `claude -p`.

---

Set up the Gamemaker watcher on this machine.

You are Claude Code in the project of the game whose web build you publish to <SITE_REPO>. My brother will write
prompts for this game on board.example.com. From now on this machine picks each one up by itself, carries it out
with `claude -p` in this project, tests it here the way you test my prompts, publishes the build and reports back,
so that he sees the result on the site, playable, right under his prompt. Set that up as follows. Nothing changes
in how the game is published: the source stays on this machine, and only the build goes to <SITE_REPO>, as now.

The starting point is the game as it is published now at <PAGES_URL>. GitHub Pages serves that repository's main
branch, root folder, and a push is online a few minutes later. The site already shows this game as the starting
point. Every prompt changes a version: by default the newest, one after the other, but my brother can also build
on an earlier version or on the starting point, which starts a new branch (Main, B, C, ...). The runs build on the
version they're given, never start over.

How the runs are kept to this game (the watcher does this; don't loosen it). The prompts come from a web page, so
every run is fenced in, whatever the prompt says:
- Claude starts in restricted mode with only Bash, Read, Edit, Write, Glob and Grep: no web tools, no MCP servers, no
  hooks or settings from this machine or the project, and the file tools only see the project and the site's working
  copy.
- Every shell command runs in Claude Code's sandbox (bubblewrap): it can write only in those two folders, in its
  temporary folders and in cargo's package caches (SANDBOX_WRITE), can't read the rest of the home folder (keys,
  tokens, other projects), and can reach only the hosts in SANDBOX_DOMAINS: by default the Rust package hosts
  (crates.io, index.crates.io, static.crates.io, static.rust-lang.org). Claude Code is told to fail when the
  sandbox can't start, not to go on without it. Two things are outside the sandbox, and only if the settings say
  so: a script listed in OUTSIDE_SANDBOX (step 2 has its rules), and everything with SANDBOX=off. Never set
  SANDBOX=off.
- Rules in the system prompt keep the work on this game, keep the source out of the site, and keep the published page
  clean. A prompt that asks for anything else is refused, and shows on the board as not done, with the reason.
- Claude can't push, and never sees the board's password. The watcher commits what is left, checks that no source
  files went into the site, and pushes the site. After a run that failed or was refused it puts both folders back as
  they were. So it never touches anyone else's work, a run only starts when neither folder has uncommitted changes.
- Git itself. The watcher runs git in both folders outside the sandbox, and git runs programs a repository names
  (hooks, settings such as core.fsmonitor, and those of a repository nested inside). So a run can't write either
  folder's .git/config or .git/hooks; when Claude exits, whatever it left running is stopped; and before the watcher's
  git touches a folder again, its git setup is put back byte for byte as it was before the run, without running git,
  and any repository nested in it is taken out. A run that changed any of it fails, and nothing is published. The
  watcher's own git runs no hooks. This also keeps both folders safe for your own git afterwards.
- A time limit (RUN_TIMEOUT, 2 hours by default) stops a runaway run: the watcher enforces it itself. There is
  also a cost cap, unless it is switched off (MAX_COST, in dollars at API prices: 10 by default, 0 for no cap).
  That one is Claude Code's (--max-budget-usd), and a run can end somewhat past it.

Connection (the password must not go into git, onto a command line, or into any file other users can read):
- Board: https://board.example.com (it only answers the allowed IPs: this machine's must be one of them)
- User: agent
- Password: <AGENT_PASSWORD>
  (If this still reads <AGENT_PASSWORD>, you were not given it. Ask me for it when you write the env file in
  step 2, and tell me first that it will then be in this session's transcript.)

1. ASK ME FIRST, and wait for the answers:
   a. Where the runs work. Either this very folder, or a second clone of this project next to it, on its own branch
      (say `world`): `git clone --no-hardlinks <this folder> <second folder>`, so its origin is this folder. Suggest
      the clone if I'll keep working in this folder myself, so my work and my brother's never meet. Not a git
      worktree: a worktree's commits go into this folder's .git, which a run can't write. If it's this folder: a
      prompt waits while the folder has uncommitted changes, and his runs' sessions show up in this folder's session
      list (`claude --continue` here could open one of his).
   b. The cost cap per prompt: suggest MAX_COST=10, the default (dollars at API prices; with a Claude subscription
      it's a share of my usage limits instead of a bill). Claude Code enforces the cap, and a run can end
      somewhat past it. 0 means no cap: then only the time limit stops a run.

2. INSTALL, as my user:
   - Check: Linux with systemd user services, python3 3.8 or newer, git, Claude Code 2.1.259 or later, and bubblewrap
     and socat (`sudo apt-get install bubblewrap socat`, which I run). On Ubuntu 24.04 or later, if
     `sysctl kernel.apparmor_restrict_unprivileged_userns` says 1, bubblewrap also needs the AppArmor profile from
     https://code.claude.com/docs/en/sandboxing ("Set up Linux and WSL2"), which I install with sudo. If
     /etc/apparmor.d/bwrap-userns-restrict exists (Ubuntu 26.04 ships it, and its profile is also named `bwrap`), the
     docs' profile has to replace it, not sit beside it. First:
        sudo ln -s /etc/apparmor.d/bwrap-userns-restrict /etc/apparmor.d/disable/
        sudo apparmor_parser -R /etc/apparmor.d/bwrap-userns-restrict
     then the docs' profile. Until then every command of a run fails with
     `apply-seccomp: write /proc/self/setgroups … Permission denied`. Tell me what that profile is before I
     install it: it is `flags=(unconfined)` with one rule, `userns`. It lets bubblewrap create user namespaces, so
     that the sandbox can start. It confines nothing, and it applies to everything on this machine that runs bwrap.
   - The working copies: the project (1a; for a second clone, also whatever untracked files the build and the tests
     need), and the working copy of <SITE_REPO> you publish from (clone it if you don't keep one). Each needs
     a .git folder of its own, and they sit side by side, not one inside the other, with no repository inside either
     (no submodules). Both must be clean and up to date. The watcher pushes the site with this user's git login, as
     you do now. It adds to each one's .git/info/exclude the names the sandbox puts at the top of a working copy
     during a run (.claude/, .bashrc, .mcp.json and so on; for the site, every name starting with a dot but
     .nojekyll), so that a run's `git status` and `git add -A` work and the folder is clean again after it.
   - Write GAMEMAKER.md at the top of the project and commit it. Every run reads it first, so make it exact, and short:
     - how to build the web version: the commands, and what each needs;
     - how to test it here before publishing, the same way you test the game with my prompts: the commands, the
       browser checks and the pictures, and what to look at;
     - how to publish iteration <id> into the site's working copy:
       - the new build goes at the root, as now: only the build, never source;
       - a frozen copy goes in v/<id>/, with relative links, so that it works from there too. Big files that never
         change (videos, a studio intro) stay only at the root: the copy leaves them out, and its index.html points
         to them there (../../);
       - if the page names its build (a stamp in index.html that the browser loads files by), it is the source
         commit, so every build loads its own files;
       - then commit (the watcher pushes);
     - a map of the code: one line per crate or important file, saying what it does. Runs use it to go straight to the
       right files instead of reading the whole source, which keeps each prompt cheap.
   - Freeze the starting point: copy the site's root as it is now into v/0/, the same way as v/<id>/ (without the
     big files that stay at the root, and pointing to them at ../../). Commit "Starting point (v/0)". Show me
     what the commit holds, and push it when I say yes: it goes to the public site. A few minutes after the
     push, open <PAGES_URL>v/0/ and check that the game loads and plays. The board shows v/0/ as the starting
     point once the first result is out.
   - Record where the starting point's source is, for branches that start over from it: in the project's working copy,
     `git update-ref refs/gamemaker/v/0 HEAD` (HEAD being the commit the runs start from, with GAMEMAKER.md). The
     watcher records each later version the same way, refs/gamemaker/v/<id>, when it publishes it.
   - Write ~/.config/gamemaker/env before you download the watcher: the download reads the password from this
     file. Make the file while it is still empty, for this user only:
        mkdir -p ~/.config/gamemaker && touch ~/.config/gamemaker/env && chmod 600 ~/.config/gamemaker/env
     Then put the lines in with your file tool, not with a shell command: a command line shows in `ps`. Run the
     chmod once more afterwards. One KEY=value per line, with nothing around the `=` and no quotes:
        GAMEMAKER_URL=https://board.example.com
        GAMEMAKER_USER=agent
        GAMEMAKER_PASS=<AGENT_PASSWORD>
        WORK_DIR=<the project's working copy from 1a, full path>
        WORK_BRANCH=<the git branch the runs work on there, the main line, e.g. world>
        SITE_DIR=<the site's working copy, full path>
        PAGES_URL=<PAGES_URL>
        CLAUDE_BIN=<the output of `command -v claude`>
        PATH=<the output of `echo $PATH` in a login shell, so git, cargo, rustup and the test tools are found>
        MAX_COST=<from 1b>
        GIT_NAME=<who the runs commit as: ask me>
        GIT_EMAIL=<that author's email address: it shows in the site's public history>
   - Download the watcher, and read it before you run it. The password is read from the env file and handed to
     curl on its standard input (`-K -`), so it is never on a command line:
        mkdir -p ~/.local/share/gamemaker
        while IFS= read -r line; do
          case $line in GAMEMAKER_PASS=*) printf 'user = "agent:%s"\n' "${line#GAMEMAKER_PASS=}";; esac
        done < ~/.config/gamemaker/env |
          curl -fsS -K - https://board.example.com/agent/watch.py -o ~/.local/share/gamemaker/watch.py
     It expects the line as it is written above: GAMEMAKER_PASS= at the start of the line, and no quotes. (The
     passwords deploy/make-credentials.sh makes have only letters, digits, - and _. A password with " or \ in it
     would need those escaped for curl.)
   - Run `python3 ~/.local/share/gamemaker/watch.py check` and fix every line marked FIX: in the env file, in the
     working copies or on the machine, never by changing watch.py or by taking a piece of the fence out. If a line
     can't be fixed that way, stop and tell me. The check's last part starts Claude the way a run does and has it
     try the fence: a write in the project must work; a write in the home folder, a read of the password, and a
     change to either working copy's git settings or hooks must not; and a git commit must work. Anything it did
     change in the git setup is put back, and said.
   - Then make sure a run can build and test like you do. For the build, and for each test in GAMEMAKER.md, run:
        python3 ~/.local/share/gamemaker/watch.py check --try "<the command>"
     It has Claude run the command as a run would, exactly as given, and gives it a line of its own: ok or FIX, from
     the command's own exit code, with its last 20 lines of output. (FIX too if Claude ran it some other way, say
     with `cd` or a pipe: a script in OUTSIDE_SANDBOX then runs inside the sandbox.)
     When the sandbox blocks a path or a host that a command really needs, add that one thing to SANDBOX_READ,
     SANDBOX_WRITE or SANDBOX_DOMAINS in the env file. They're space-separated; the defaults are at the top of
     watch.py, so repeat them. Never add the whole home folder, ~/.ssh, ~/.config, ~/.cargo/bin, a folder in PATH,
     or a general host like github.com.
     A test that can't work inside the sandbox at all, say a browser that needs the GPU or a screen, goes in a script
     outside both working copies, e.g. ~/.local/share/gamemaker/test-in-browser.sh. It runs outside the sandbox, with
     my rights, so:
     - Claude can run it but not change it: it is outside both working copies and SANDBOX_WRITE;
     - it must not run anything from the working copies (say `node tools/check.mjs`, a build script, a package's
       scripts): a run can change those files, and they would then run outside the sandbox. Give it its own copies of
       the checks, outside the working copies;
     - it only reads files from the working copies: it serves only real files inside them to the browser (no links
       out), gives the page no internet, and writes what it makes (pictures) as new files;
     - it takes no arguments, or only the iteration's number, and prints what Claude needs to see;
     - it runs outside only when it is the whole command: with a pipe, a redirection or `cd … &&` it runs inside,
       where it isn't found. The watcher tells each run to start it bare; say so in GAMEMAKER.md too.
     Put its full path in OUTSIDE_SANDBOX, and name it in GAMEMAKER.md. Try everything again until the build and the
     tests work the way they do for you.

3. RUN IT EVERY 30 SECONDS with a systemd user timer.
   ~/.config/systemd/user/gamemaker-watch.service:
        [Unit]
        Description=Gamemaker watcher: run the prompts waiting on board.example.com

        [Service]
        Type=oneshot
        ExecStart=/usr/bin/env python3 %h/.local/share/gamemaker/watch.py run
        TimeoutStartSec=infinity

   ~/.config/systemd/user/gamemaker-watch.timer:
        [Unit]
        Description=Check board.example.com for prompts every 30 seconds

        [Timer]
        OnActiveSec=10s
        OnUnitInactiveSec=30s
        AccuracySec=5s

        [Install]
        WantedBy=timers.target

   Then `systemctl --user daemon-reload && systemctl --user enable --now gamemaker-watch.timer`.
   It must keep running when nobody is logged in: if `loginctl show-user $USER -p Linger` says no, ask me to run
   `sudo loginctl enable-linger $USER`.

4. CHECK, AND TELL ME.
   - `systemctl --user list-timers gamemaker-watch.timer` shows it scheduled, and
     `journalctl --user -u gamemaker-watch -n 20` shows runs without errors.
   - The board's badge now says "Agent checked … ago".
   - Tell me what you set up:
     - where the runs work, and the branch;
     - how a build and the tests are run, and how long they take;
     - what you added to the sandbox's lists, and why;
     - any script outside the sandbox, and what it does;
     - whether v/0/ is online.
     Also tell me how to follow the log (`journalctl --user -u gamemaker-watch -f`), pause it
     (`systemctl --user stop gamemaker-watch.timer`) and update the watcher: stop the timer and wait for a run
     in progress to end; download the new file beside the installed one (the download above, with
     `-o ~/.local/share/gamemaker/watch.py.new`); read what changed (`diff`) before it runs; put it in place,
     keeping the old one; run `watch.py check` again; start the timer.

How it works, for reference: each run asks the board whether a prompt is waiting. That costs nothing, and Claude isn't
started when nothing is waiting. If one is, the watcher claims it and starts `claude -p` in the project, fenced in as
above, with a brief built around the prompt.

The runs' commits are by the watcher's author alone: Claude Code is told to add no Co-Authored-By trailer (no hook is
needed for that).

Each prompt is a new Claude session that finds its way through the project: GAMEMAKER.md and its map, CLAUDE.md,
NOTES.md (one line per earlier prompt) and git. A prompt sent within 30 minutes of a finished one continues that
session instead, while what it read is still fresh (up to 5 in a row), if it goes on from the version that session
made.

Branches: each branch of the board is a git branch in the project: WORK_BRANCH for the main line, WORK_BRANCH-b for
branch B, and so on. A new branch starts at the source of the version it builds on (refs/gamemaker/v/<id>). The site
stays one line: every version in v/<id>/, the newest build at the root. Claude:
- reads GAMEMAKER.md, CLAUDE.md and NOTES.md, and only the code the prompt needs,
- changes the game,
- builds and tests it here,
- adds progress notes to a file that the watcher posts on the site,
- puts the build at the site's root and a frozen copy in v/<prompt id>/ (so every iteration stays playable),
- commits, and writes a title, a summary and a sentence on what its branch changes, for the site.

The site is in English or Spanish and has a Kids mode. Each prompt says which, and Claude writes the title, its notes,
the summary and the branch sentence in that language, in simple words for a child when it came in Kids mode. Commits,
code and comments stay in English.

The watcher then pushes the site (only the site), waits until GitHub Pages serves the page, and marks the prompt done,
with the time it took and roughly what it cost. Otherwise it marks it failed, with the reason. It runs one prompt at a
time. A lock keeps runs from overlapping, and a run that dies is noticed and marked failed the next time.

Working in the project by hand, for example to merge a fix into the runs' line: pause the watcher first (stop the
timer, and wait until `systemctl --user is-active gamemaker-watch.service` no longer says active), then check out
WORK_BRANCH, because after a run on another branch the checkout stays there (world-b, ...). Start the timer again when
you're done. Each branch uses its own GAMEMAKER.md, build tools and publish step, so a fix merged into the main line
doesn't reach the other branches: merge it into each of them too. A new branch from an older version uses that
version's. So if a fix leaves the old ones unusable, point the refs of the versions concerned at a commit with the
same game and the fixed tools, such as the commit the next run started from (`git update-ref refs/gamemaker/v/<id>
<commit>`), and say which ones you moved.

The refs in refs/gamemaker/v/ are this machine's alone: a clone doesn't bring them. To move the runs to another
machine, fetch them along (`git fetch <the old copy> 'refs/gamemaker/*:refs/gamemaker/*'`). If they ever have to be
recorded again: each version's source is the last commit of its run or, where the tools were fixed after it, the
commit the next run started from, and `watch.py check` names any version without one.
