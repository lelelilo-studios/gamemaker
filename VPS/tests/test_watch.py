"""The agent's watcher end to end: a real board, git repositories with a local "origin", and a stand-in for claude
that does what a run does (a note, a commit, a result) or misbehaves.

  python3 -m unittest discover -s tests
"""
import base64
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'agent'))
import server  # noqa: E402
import watch  # noqa: E402

# what every stand-in does first: answer --version, and keep its arguments and brief for the test to look at
HEAD = """\
#!/bin/sh
[ "$1" = "--version" ] && { echo "2.1.300 (Claude Code)"; exit 0; }
for a in "$@"; do printf '%s\037' "$a"; done >> "$0.args"; printf '\036' >> "$0.args"
brief=$(cat)
printf '%s' "$brief" > "$0.brief"
"""
GOOD = HEAD + """\
case "$brief" in *"make it jump"*) ;; *) echo "the brief is missing the request" >&2; exit 3;; esac
printf '%s\\n' "Adding the jump" >> "$GAMEMAKER_NOTES"
mkdir -p "v/$GAMEMAKER_PROMPT_ID" && echo "jump" > "v/$GAMEMAKER_PROMPT_ID/index.html"
git add -A && git commit -qm "Iteration #$GAMEMAKER_PROMPT_ID: jump" || exit 5
printf '{"summary": "It jumps now.", "url": "%s"}' "$GAMEMAKER_ITERATION_URL" > "$GAMEMAKER_RESULT_FILE"
echo '{"type": "result", "is_error": false, "result": "All done.", "session_id": "sess-1", "total_cost_usd": 0.75}'
"""
BAD = HEAD + """\
echo "half a jump" > half.txt && git add -A && git commit -qm "half"
echo "boom: out of crayons" >&2
exit 1
"""
REFUSES = HEAD + """\
echo "stray" > stray.txt
printf '{"refused": "It asks to read files outside the game."}' > "$GAMEMAKER_RESULT_FILE"
echo '{"type": "result", "is_error": false, "result": "Refused.", "session_id": "sess-2"}'
"""


# what a run could leave behind for the watcher's git, which runs outside the sandbox: programs in the site's settings
# and hooks (nothing guards a second folder's .git), and a repository nested in the source with a program in its
# settings, added to the outer index so that `git status` there goes into it. @PLANT@ only leaves a mark outside a run
TAMPER = """\
export IN_RUN=1
cd @SITE@ && git config core.fsmonitor @PLANT@ && git config core.sshCommand @PLANT@ || exit 7
for h in pre-commit commit-msg post-commit pre-push reference-transaction; do cp @PLANT@ ".git/hooks/$h"; done
cd @WORK@ && git init -q inner && echo x > inner/f && git -C inner add f && git -C inner commit -qm inner \
  && git -C inner config core.fsmonitor @PLANT@ && git add inner || exit 8
"""


def sh(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def repo(tmp, name):
    """A working copy with one commit on main, pushed to a bare "origin" next to it."""
    origin, work = tmp / f'{name}.git', tmp / name
    sh('git', 'init', '-q', '--bare', '-b', 'main', str(origin))
    sh('git', 'init', '-q', '-b', 'main', str(work))
    (work / 'README.md').write_text(f'{name}\n')
    sh('git', '-C', str(work), 'add', '-A')
    sh('git', '-C', str(work), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'start')
    sh('git', '-C', str(work), 'remote', 'add', 'origin', str(origin))
    sh('git', '-C', str(work), 'push', '-q', 'origin', 'main')
    return work, origin


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = self.root = Path(self.tmp.name)
        self.store = server.Store(tmp / 'board.sqlite3')
        self.httpd = server.make_server('127.0.0.1', 0, self.store)
        handler = self.httpd.RequestHandlerClass
        handler.log_message = lambda *args: None

        # in front of the real board Caddy checks the password and passes the name on: here the board reads it itself
        def who(h):
            auth = h.headers.get('Authorization', '')
            user = base64.b64decode(auth[6:]).decode().split(':')[0] if auth.startswith('Basic ') else ''
            if not user:
                raise server.ApiError(401, 'not logged in')
            return user, 'agent' if user == h.agent_user else 'brother'
        handler.who = who
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

        self.work, self.origin = repo(tmp, 'game')
        self.start = sh('git', '-C', str(self.work), 'rev-parse', 'HEAD')
        self.claude = tmp / 'claude'
        self.config = tmp / 'env'
        self.config.write_text(textwrap.dedent(f"""\
            GAMEMAKER_URL=http://127.0.0.1:{self.httpd.server_address[1]}
            GAMEMAKER_USER=agent
            GAMEMAKER_PASS=secret
            WORK_DIR={self.work}
            PAGES_URL=https://lelelilo-studios.github.io/game/
            CLAUDE_BIN={self.claude}
            PATH={os.environ['PATH']}
            """))
        self.saved = (watch.CONFIG, watch.STATE, watch.LOCK, watch.SESSION, watch.GIT_SETUP_FILE, watch.page_is_up,
                      watch.probe, watch.sandbox_missing, dict(os.environ))
        watch.CONFIG, watch.STATE = self.config, tmp / 'state'
        watch.LOCK, watch.SESSION = watch.STATE / 'watch.lock', watch.STATE / 'session.json'
        watch.GIT_SETUP_FILE = watch.STATE / 'git-setup.json'
        watch.page_is_up = lambda url: True  # no real GitHub Pages here
        os.environ['GAMEMAKER_CONFIG'] = str(self.config)

    def tearDown(self):
        for here, dirs, _ in os.walk(self.root):
            for d in dirs:
                p = os.path.join(here, d)
                if not os.path.islink(p):
                    os.chmod(p, 0o755)
        (watch.CONFIG, watch.STATE, watch.LOCK, watch.SESSION, watch.GIT_SETUP_FILE, watch.page_is_up, watch.probe,
         watch.sandbox_missing, env) = self.saved
        os.environ.clear()
        os.environ.update(env)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def stand_in(self, script):
        self.claude.write_text(script)
        self.claude.chmod(self.claude.stat().st_mode | stat.S_IXUSR)

    def setting(self, line):
        with self.config.open('a') as f:
            f.write(line + '\n')

    def runs(self):
        """The arguments of each time claude was started."""
        return [' '.join(run.split('\x1f')) for run in Path(f'{self.claude}.args').read_text().split('\x1e')[:-1]]

    def pushed(self, origin=None):
        return sh('git', '--git-dir', str(origin or self.origin), 'log', '--format=%s', 'main').splitlines()

    def test_a_prompt_runs_and_its_result_reaches_the_board(self):
        self.stand_in(GOOD)
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        done = self.store.get(p['id'])
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['summary'], 'It jumps now.')
        self.assertEqual(done['result_url'], f'https://lelelilo-studios.github.io/game/v/{p["id"]}/')
        self.assertEqual([e['kind'] for e in done['events']], ['sent', 'picked', 'note', 'note', 'done'])
        self.assertEqual(done['events'][2]['message'], 'Adding the jump')
        self.assertEqual(done['events'][3]['message'], 'Finished in 1 min, about $0.75 at API prices')
        # the watcher pushed what Claude committed
        self.assertEqual(self.pushed()[0], f'Iteration #{p["id"]}: jump')

    def test_claude_is_fenced_in(self):
        self.stand_in(GOOD)
        self.store.create('brother', 'make it jump')
        watch.main(['watch.py', 'run'])
        args = self.runs()[0]
        for part in ('--restricted', '--tools Bash,Read,Edit,Write,Glob,Grep', '--disallowedTools mcp__*',
                     '--strict-mcp-config', '--permission-prompts none', '"failIfUnavailable": true',
                     '"allowUnsandboxedCommands": false', '"strictAllowlist": true', '"disableAllHooks": true',
                     '"blockReadsOutsideWorkingDirectories": true', '--max-budget-usd 10', 'GAMEMAKER RULES'):
            self.assertIn(part, args)
        self.assertNotIn('--resume', args)
        self.assertNotIn('secret', args + Path(f'{self.claude}.brief').read_text())

    def test_a_quick_follow_up_continues_the_session_up_to_a_limit(self):
        self.stand_in(GOOD)
        self.setting('RESUME_MAX=2')
        ids = [self.store.create('brother', f'make it jump {i}')['id'] for i in range(3)]
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual([self.store.get(i)['status'] for i in ids], ['done'] * 3)
        self.assertEqual(['--resume sess-1' in a for a in self.runs()], [False, True, False])
        self.assertIn('continuing the last session', self.store.get(ids[1])['events'][-2]['message'])

    def test_a_session_that_cannot_be_continued_is_started_again(self):
        self.stand_in(HEAD + 'case "$*" in *--resume*) echo "No conversation found" >&2; exit 1;; esac\n'
                      + GOOD[len(HEAD):])
        watch.STATE.mkdir()
        # the session that made the version this prompt builds on (the starting point, here)
        watch.SESSION.write_text(json.dumps({'session_id': 'old', 'finished_at': time.time(), 'chain': 1, 'prompt_id': 0}))
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual(self.store.get(p['id'])['status'], 'done')
        self.assertEqual(['--resume old' in a for a in self.runs()], [True, False])

    def test_a_failing_run_is_marked_failed_and_leaves_nothing_behind(self):
        self.stand_in(BAD)
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        failed = self.store.get(p['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('out of crayons', failed['summary'])
        self.assertEqual(sh('git', '-C', str(self.work), 'rev-parse', 'HEAD'), self.start)
        self.assertFalse((self.work / 'half.txt').exists())
        self.assertEqual(self.pushed(), ['start'])
        self.assertFalse(watch.SESSION.exists())

    def test_a_request_outside_the_game_is_refused_and_undone(self):
        self.stand_in(REFUSES)
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        refused = self.store.get(p['id'])
        self.assertEqual(refused['status'], 'failed')
        self.assertTrue(refused['summary'].startswith('Not done: It asks to read files outside the game.'))
        self.assertFalse((self.work / 'stray.txt').exists())
        self.assertEqual(self.pushed(), ['start'])
        self.assertFalse(watch.SESSION.exists())  # the next prompt doesn't continue this conversation

    def test_a_prompt_left_running_by_a_dead_run_is_failed(self):
        self.stand_in(GOOD)
        p = self.store.create('brother', 'make it jump')
        self.store.claim('agent')
        db = self.store._open()
        db.execute('UPDATE prompts SET alive_at = alive_at - 600 WHERE id = ?', (p['id'],))
        db.close()
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual(self.store.get(p['id'])['status'], 'failed')

    def test_nothing_waiting_means_claude_is_not_started(self):
        self.stand_in('#!/bin/sh\ntouch "$0.ran"\n')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertFalse(Path(str(self.claude) + '.ran').exists())

    def test_the_brief_says_who_reads_what_the_site_shows(self):
        self.stand_in(GOOD)
        briefs = []
        for lang, kids in (('en', False), ('es', False), ('es', True), ('en', True)):
            p = self.store.create('brother', 'make it jump', lang=lang, kids=kids)
            self.assertEqual(watch.main(['watch.py', 'run']), 0)
            self.assertEqual(self.store.get(p['id'])['status'], 'done')
            briefs.append(Path(f'{self.claude}.brief').read_text())
        spanish, child = 'write the title, your progress notes, the summary and', 'A child reads what you write'
        self.assertEqual([(spanish in b, child in b) for b in briefs],
                         [(False, False), (True, False), (True, True), (False, True)])
        self.assertIn('Commit messages, code and comments stay in English.', briefs[1])

    def test_a_separate_site_repository(self):
        site, site_origin = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')
        self.stand_in(GOOD.replace('mkdir -p "v/', f'echo src > src.txt && git add -A && git commit -qm source && cd {site} && mkdir -p "v/'))
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual(self.store.get(p['id'])['status'], 'done')
        self.assertIn(f'--add-dir {site} ', self.runs()[0])
        brief = Path(f'{self.claude}.brief').read_text()
        self.assertIn(f"the current directory ({self.work}) is the game's source", brief)
        self.assertIn(f'The site is built into\n{site}', brief)
        self.assertIn('If\n     v/0/ is missing', brief)
        self.assertEqual(self.pushed(site_origin)[0], f'Iteration #{p["id"]}: jump')
        # the source was committed here, and stays here
        self.assertEqual(sh('git', '-C', str(self.work), 'log', '-1', '--format=%s'), 'source')
        self.assertEqual(self.pushed(), ['start'])

    def test_the_source_is_pushed_only_when_asked(self):
        site, site_origin = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')
        self.setting('PUSH_SOURCE=yes')
        self.stand_in(GOOD.replace('mkdir -p "v/', f'echo src > src.txt && git add -A && git commit -qm source && cd {site} && mkdir -p "v/'))
        self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual(self.pushed()[0], 'source')

    def test_source_files_never_reach_the_site(self):
        site, site_origin = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')
        self.stand_in(GOOD.replace('mkdir -p "v/', f'cd {site} && echo "fn main() {{}}" > main.rs && mkdir -p "v/'))
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        failed = self.store.get(p['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('files that belong to the source: main.rs', failed['summary'])
        self.assertEqual(self.pushed(site_origin), ['start'])
        self.assertFalse((site / 'main.rs').exists())

    def test_a_prompt_waits_while_someone_has_uncommitted_work_in_the_folder(self):
        self.stand_in(GOOD)
        (self.work / 'mine.txt').write_text('work in progress\n')
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual(self.store.get(p['id'])['status'], 'queued')
        self.assertFalse(Path(f'{self.claude}.args').exists())
        (self.work / 'mine.txt').unlink()
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual(self.store.get(p['id'])['status'], 'done')

    def test_a_script_can_be_let_out_of_the_sandbox_only_from_outside_the_working_copies(self):
        self.stand_in(GOOD)
        script = self.root / 'browser-test.sh'
        script.write_text('#!/bin/sh\n')
        script.chmod(0o755)
        self.setting(f'OUTSIDE_SANDBOX={script}')
        cfg = watch.Config()
        self.assertEqual(cfg.problems(), [])
        sandbox = watch.settings(cfg)
        self.assertEqual(sandbox['sandbox']['excludedCommands'], [str(script), f'{script} *'])
        self.assertEqual(sandbox['permissions']['allow'], [f'Bash({script})', f'Bash({script} *)'])
        inside = self.work / 'test.sh'
        inside.write_text('#!/bin/sh\n')
        inside.chmod(0o755)
        self.setting(f'OUTSIDE_SANDBOX={inside}')
        self.assertIn('inside a working copy', ' '.join(watch.Config().problems()))

    def probe_with(self, leaky):
        """The check's fence test, with a stand-in for Claude that either stays fenced in or doesn't."""
        home = self.root / 'home'
        home.mkdir()
        os.environ['HOME'] = str(home)
        outside = home / '.gamemaker-probe'
        tool = lambda cmd: json.dumps({'type': 'tool_use', 'name': 'Bash', 'input': {'command': cmd}})
        hook = self.work / '.git' / 'hooks' / 'gamemaker-probe'
        events = [
            {'type': 'system', 'subtype': 'init', 'tools': ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep'], 'mcp_servers': []},
            {'type': 'assistant', 'message': {'content': [json.loads(tool(f'touch {outside}')),
                                                          json.loads(tool(f'head -c 600 {watch.CONFIG}')),
                                                          json.loads(tool(f'git -C {self.work} config --local gamemaker.probe 1')),
                                                          json.loads(tool(f'touch {hook}'))]}},
        ]
        if leaky:
            events.append({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'GAMEMAKER_PASS=secret'}]}})
        events.append({'type': 'result', 'result': 'GAMEMAKER_PASS=secret' if leaky else 'blocked'})
        self.stand_in(HEAD + 'touch .gamemaker-probe\n'
                      + (f'touch {outside}\ngit config --local gamemaker.probe 1\ntouch {hook}\n' if leaky else '')
                      + ''.join(f"echo '{json.dumps(e)}'\n" for e in events))
        return {line: good for good, line in watch.probe(watch.Config())}

    def test_the_check_tries_the_fence_on_the_real_disk(self):
        lines = self.probe_with(leaky=False)
        self.assertTrue(lines['a command can write in WORK_DIR'])
        self.assertTrue(lines['nothing a command wrote to the home folder reached it'])
        self.assertTrue(lines[f'a command could not read the board password in {watch.CONFIG}'])
        self.assertTrue(lines['a command could not change the git settings or hooks of the working copies'])
        self.assertFalse((self.work / '.gamemaker-probe').exists())

    def test_the_check_catches_a_fence_that_leaks(self):
        lines = self.probe_with(leaky=True)
        self.assertFalse(lines['a command wrote to the home folder: the sandbox is not holding'])
        self.assertFalse(lines[f'a command could read the board password in {watch.CONFIG}: the sandbox is not hiding the home folder'])
        changed = [line for line in lines if line.startswith('a command changed the git setup')][0]
        self.assertFalse(lines[changed])
        self.assertIn('game/.git/config was changed', changed)
        self.assertIn('game/.git/hooks/gamemaker-probe appeared', changed)
        self.assertNotIn('gamemaker', sh('git', '-C', str(self.work), 'config', '--local', '--list'))  # put back
        self.assertFalse((self.work / '.git' / 'hooks' / 'gamemaker-probe').exists())
        said = [line for line in lines if line.startswith('Claude said')][0]
        self.assertNotIn('secret', said)
        self.assertFalse((Path(os.environ['HOME']) / '.gamemaker-probe').exists())  # cleaned up

    # -- git, outside the sandbox

    def plant(self):
        """A program a run could name in git's settings or hooks: it leaves a mark when it runs outside a run."""
        plant, mark = self.root / 'plant.sh', self.root / 'planted-ran'
        plant.write_text(f'#!/bin/sh\n[ -n "$IN_RUN" ] || echo "$0 $*" >> {mark}\n')
        plant.chmod(0o755)
        return plant, mark

    def tamper(self, site):
        plant, mark = self.plant()
        return TAMPER.replace('@SITE@', str(site)).replace('@WORK@', str(self.work)).replace('@PLANT@', str(plant)), mark

    def test_the_planted_programs_run_when_nothing_guards_git(self):
        # what the tests below guard against is real: plain git, outside a run, runs what a run planted
        site, _ = repo(self.root, 'game-web')
        script, mark = self.tamper(site)
        subprocess.run(['sh', '-c', script], check=True, capture_output=True)
        sh('git', '-C', str(self.work), 'status', '--porcelain')
        self.assertTrue(mark.exists())  # the nested repository's fsmonitor, from the outer `git status`
        mark.unlink()
        sh('git', '-C', str(site), 'commit', '-q', '--allow-empty', '-m', 'x')
        self.assertTrue(mark.exists())  # the site's settings and hooks

    def test_a_run_that_changes_the_git_setup_fails_and_nothing_it_planted_runs(self):
        site, site_origin = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')
        hooks_before = sorted(p.name for p in (site / '.git' / 'hooks').iterdir())
        config_before = {d: (d / '.git' / 'config').read_bytes() for d in (self.work, site)}
        site_start = sh('git', '-C', str(site), 'rev-parse', 'HEAD')
        script, mark = self.tamper(site)
        self.stand_in(HEAD + f"""\
export IN_RUN=1
echo src > src.txt && git add -A && git commit -qm source
cd {site} && mkdir -p "v/$GAMEMAKER_PROMPT_ID" && echo jump > "v/$GAMEMAKER_PROMPT_ID/index.html" \\
  && git add -A && git commit -qm "Iteration #$GAMEMAKER_PROMPT_ID: jump"
""" + script + """\
printf '{"summary": "It jumps now.", "url": "%s"}' "$GAMEMAKER_ITERATION_URL" > "$GAMEMAKER_RESULT_FILE"
echo '{"type": "result", "is_error": false, "result": "All done.", "session_id": "sess-1"}'
""")
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        failed = self.store.get(p['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('the run changed the git setup of its folders', failed['summary'])
        for what in ('game-web/.git/config was changed', 'game-web/.git/hooks/pre-push appeared', 'game/inner/.git'):
            self.assertIn(what, failed['summary'])
        # nothing was published, both folders are back where they were, and the planted programs never ran
        self.assertEqual(self.pushed(site_origin), ['start'])
        self.assertEqual(self.pushed(), ['start'])
        self.assertEqual(sh('git', '-C', str(site), 'rev-parse', 'HEAD'), site_start)
        self.assertEqual(sh('git', '-C', str(self.work), 'rev-parse', 'HEAD'), self.start)
        self.assertFalse((self.work / 'inner').exists())
        for d in (self.work, site):
            self.assertEqual((d / '.git' / 'config').read_bytes(), config_before[d])
        self.assertEqual(sorted(p.name for p in (site / '.git' / 'hooks').iterdir()), hooks_before)
        self.assertFalse(watch.GIT_SETUP_FILE.exists())
        # and they are clean for whoever uses git there next
        for d in (self.work, site):
            sh('git', '-C', str(d), 'status', '--porcelain')
            sh('git', '-C', str(d), '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'mine')
        self.assertFalse(mark.exists())

    def test_the_guard_puts_back_exactly_what_was_there(self):
        hooks = self.work / '.git' / 'hooks'
        (hooks / 'commit-msg').write_text('#!/bin/sh\nsed -i /^Co-Authored-By/d "$1"\n')
        (hooks / 'commit-msg').chmod(0o750)
        (hooks / 'post-merge').symlink_to('/usr/bin/true')
        config = (self.work / '.git' / 'config').read_bytes()
        before = watch.git_setup(self.work)
        other, _ = repo(self.root, 'other')
        outside = self.root / 'outside.txt'
        outside.write_text('keep me\n')
        # what a run could do: a link where the settings were, so that putting them back would write through it
        (self.work / '.git' / 'config').unlink()
        (self.work / '.git' / 'config').symlink_to(outside)
        (hooks / 'commit-msg').write_text('#!/bin/sh\nrm -rf ~\n')
        (hooks / 'pre-push').write_text('#!/bin/sh\n')
        (hooks / 'post-merge').unlink()
        (hooks / 'post-merge').symlink_to(str(self.root / 'plant.sh'))
        (self.work / '.git' / 'commondir').write_text(str(other / '.git') + '\n')
        (self.work / '.git' / 'config.worktree').touch()  # an empty stand-in the sandbox left behind
        (self.work / '.git' / 'modules' / 'x').mkdir(parents=True)
        (self.work / 'a' / 'b').mkdir(parents=True)
        sh('git', 'init', '-q', str(self.work / 'a' / 'b'))
        (self.work / 'a' / 'b' / 'locked').mkdir()
        (self.work / 'a').chmod(0o100)  # git can go through it, a plain walk can't
        (self.work / 'c').symlink_to(other)
        changed = watch.put_back_git_setup(self.work, before)
        self.assertEqual((self.work / '.git' / 'config').read_bytes(), config)
        self.assertFalse((self.work / '.git' / 'config').is_symlink())
        self.assertEqual(outside.read_text(), 'keep me\n')
        self.assertEqual((hooks / 'commit-msg').read_text(), '#!/bin/sh\nsed -i /^Co-Authored-By/d "$1"\n')
        self.assertEqual(stat.S_IMODE((hooks / 'commit-msg').stat().st_mode), 0o750)
        self.assertEqual(os.readlink(hooks / 'post-merge'), '/usr/bin/true')
        self.assertFalse((hooks / 'pre-push').exists())
        for gone in ('commondir', 'config.worktree', 'modules'):
            self.assertFalse(os.path.lexists(self.work / '.git' / gone))
        self.assertFalse((self.work / 'a' / 'b' / '.git').exists())
        self.assertFalse(os.path.lexists(self.work / 'c'))
        self.assertTrue(other.exists())  # a link is taken out, not what it points to
        self.assertEqual(changed, ['game/.git/config was changed', 'game/.git/hooks/commit-msg was changed',
                                   'game/.git/hooks/post-merge was changed', 'game/.git/hooks/pre-push appeared',
                                   'game/.git/commondir appeared', 'game/.git/modules appeared',
                                   'game/a/b/.git: a repository inside the working copy',
                                   'game/c: a repository inside the working copy'])
        self.assertEqual(watch.put_back_git_setup(self.work, before), [])  # nothing left to put back

    def test_a_git_folder_that_is_no_longer_its_own_stops_all_git(self):
        plant, mark = self.plant()
        other, _ = repo(self.root, 'other')
        sh('git', '-C', str(other), 'config', 'core.fsmonitor', str(plant))
        self.stand_in(GOOD.replace('printf \'{"summary"', f'rm -rf .git && ln -s {other}/.git .git\nprintf \'{{"summary"'))
        p = self.store.create('brother', 'make it jump')
        then = self.store.create('brother', 'make it jump again')
        self.assertEqual(watch.main(['watch.py', 'run']), 1)
        failed = self.store.get(p['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('could not be made safe for git', failed['summary'])
        self.assertEqual(self.pushed(), ['start'])
        self.assertFalse(mark.exists())
        # it stays that way, in this pass and the next ones: no git runs there until someone puts it right
        self.assertEqual(self.store.get(then['id'])['status'], 'queued')
        self.assertEqual(watch.main(['watch.py', 'run']), 1)
        self.assertFalse(mark.exists())
        self.assertEqual(len(self.runs()), 1)

    def test_what_a_run_leaves_running_is_stopped_before_git(self):
        pidfile = self.root / 'left.pid'
        late = self.work / '.git' / 'hooks' / 'pre-push'
        self.stand_in(GOOD.replace('printf \'{"summary"', f"setsid sh -c 'echo $$ > {pidfile}; sleep 1; touch {late}; "
                                   f"chmod +x {late}; exec sleep 60' </dev/null >/dev/null 2>&1 &\n"
                                   f"while [ ! -s {pidfile} ]; do sleep 0.05; done\nprintf '{{\"summary\""))
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual(self.store.get(p['id'])['status'], 'done')
        pid = int(pidfile.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        time.sleep(1.5)
        self.assertFalse(late.exists())

    def test_a_run_that_never_came_back_is_put_right_before_any_git(self):
        plant, mark = self.plant()
        cfg = watch.Config()
        watch.note_before_a_run(cfg)  # the run starts, and the watcher dies with it
        config = (self.work / '.git' / 'config').read_bytes()
        sh('git', '-C', str(self.work), 'config', 'core.fsmonitor', str(plant))
        p = self.store.create('brother', 'make it jump')
        self.stand_in(GOOD)
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual((self.work / '.git' / 'config').read_bytes(), config)
        self.assertFalse(watch.GIT_SETUP_FILE.exists())
        self.assertFalse(mark.exists())
        self.assertEqual(self.store.get(p['id'])['status'], 'done')

    def test_a_prompt_waits_while_a_working_copy_holds_another_repository(self):
        self.stand_in(GOOD)
        sh('git', 'init', '-q', str(self.work / 'vendor' / 'lib'))
        p = self.store.create('brother', 'make it jump')
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual(self.store.get(p['id'])['status'], 'queued')
        self.assertFalse(Path(f'{self.claude}.args').exists())
        self.assertTrue((self.work / 'vendor' / 'lib' / '.git').exists())  # it's someone's: it's not taken out

    def test_what_the_sandbox_leaves_at_the_top_holds_nothing_up(self):
        site, _ = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')
        self.stand_in(GOOD.replace('mkdir -p "v/', f'mkdir -p .claude && touch .claude/x .bashrc .mcp.json && cd {site} '
                                   '&& mkdir -p .claude && touch .bashrc .claude/x && touch .nojekyll && mkdir -p "v/'))
        ids = [self.store.create('brother', f'make it jump {i}')['id'] for i in range(2)]
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual([self.store.get(i)['status'] for i in ids], ['done', 'done'])
        self.assertIn('.nojekyll', sh('git', '-C', str(site), 'ls-files'))  # the site keeps that one

    def test_the_sandbox_and_the_file_tools_guard_both_git_setups(self):
        site, _ = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')
        out = watch.settings(watch.Config())
        for d in (self.work, site):
            self.assertIn(str(d / '.git' / 'config'), out['sandbox']['filesystem']['denyWrite'])
            self.assertIn(str(d / '.git' / 'hooks'), out['sandbox']['filesystem']['denyWrite'])
            self.assertIn(f'Edit(/{d}/.git/config)', out['permissions']['deny'])
            self.assertIn(f'Edit(/{d}/.git/hooks/**)', out['permissions']['deny'])
        self.assertEqual(out['attribution'], {'commit': '', 'pr': '', 'sessionUrl': False})

    def test_programs_the_watcher_starts_cant_come_from_where_a_run_writes(self):
        self.setting(f'PATH={self.work}/bin:{os.environ["PATH"]}')
        self.assertIn(f'PATH has {self.work}/bin, where a run can write', ' '.join(watch.Config().problems()))
        self.setting(f'PATH=.:{os.environ["PATH"]}')
        self.assertIn('PATH has a relative folder (.)', ' '.join(watch.Config().problems()))

    def test_a_script_outside_the_sandbox_is_run_bare(self):
        script = self.root / 'browser-test.sh'
        script.write_text('#!/bin/sh\n')
        script.chmod(0o755)
        self.setting(f'OUTSIDE_SANDBOX={script}')
        self.stand_in(GOOD)
        self.store.create('brother', 'make it jump')
        watch.main(['watch.py', 'run'])
        self.assertIn(f'{script} runs outside the sandbox, but only as the whole command: run it bare',
                      Path(f'{self.claude}.brief').read_text())

    def test_check_with_a_source_that_stays_here(self):
        self.stand_in(GOOD)
        self.config.chmod(0o600)
        site, _ = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')  # only the site is pushed: the source needs no origin
        self.setting('WORK_BRANCH=main')
        sh('git', '-C', str(self.work), 'update-ref', 'refs/gamemaker/v/0', 'HEAD')
        sh('git', '-C', str(self.work), 'remote', 'remove', 'origin')
        watch.probe = lambda cfg, build=None: [(True, 'the fence held (stand-in)')]
        watch.sandbox_missing = lambda cfg: []
        self.assertEqual(watch.main(['watch.py', 'check']), 0)

    # -- check --try: what the tried command itself returned

    def try_with(self, build, ran, content, is_error=False):
        """`check --try build`, with a stand-in for Claude that ran `ran` with Bash and got `content` back."""
        events = [
            {'type': 'system', 'subtype': 'init', 'tools': ['Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep'], 'mcp_servers': []},
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'toolu_1', 'name': 'Bash',
                                                           'input': {'command': ran}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_1', 'content': content,
                                                      'is_error': is_error}]}},
            {'type': 'result', 'result': 'It worked.'},  # what Claude says doesn't count
        ]
        self.stand_in(HEAD + ''.join(f"printf '%s\\n' {shlex.quote(json.dumps(e))}\n" for e in events))
        lines = [(good, line) for good, line in watch.probe(watch.Config(), build) if line.startswith(f'`{build}`')]
        self.assertEqual(len(lines), 1)
        return lines[0]

    def outside_script(self):
        script = self.root / 'test-in-browser.sh'
        script.write_text('#!/bin/sh\n')
        script.chmod(0o755)
        self.setting(f'OUTSIDE_SANDBOX={script}')
        return str(script)

    def test_try_shows_what_an_outside_script_itself_returned(self):
        script = self.outside_script()
        good, line = self.try_with(script, script, '\n'.join(f'line {i}' for i in range(1, 26)))
        self.assertTrue(good)
        self.assertIn('ran bare, as a script in OUTSIDE_SANDBOX needs, and passed (exit code 0)', line)
        self.assertIn('line 6\n', line)
        self.assertIn('line 25', line)
        self.assertNotIn('line 5\n', line)  # the last 20 lines
        task = Path(f'{self.claude}.brief').read_text()
        self.assertIn('exactly as it is written there, as the whole of one Bash call: no `cd`', task)
        self.assertTrue(task.rstrip().endswith(script))
        self.assertNotIn('last 20 lines', task)

    def test_try_catches_an_outside_script_that_was_not_run_bare(self):
        script = self.outside_script()
        good, line = self.try_with(script, f'{script} 2>&1 | tail -n 20',
                                   f'Exit code 127\n/bin/bash: line 1: {script}: No such file or directory', is_error=True)
        self.assertFalse(good)
        self.assertIn(f'was not run as given: Claude ran `{script} 2>&1 | tail -n 20` instead', line)
        self.assertIn('so this ran inside, where the script is hidden', line)

    def test_try_judges_a_build_by_its_own_exit_code(self):
        build = 'cargo xtask verify --no-browser'
        good, line = self.try_with(build, build, 'Finished `release` profile\nall checks passed')
        self.assertTrue(good)
        self.assertIn('ran and passed (exit code 0)', line)
        good, line = self.try_with(build, build, 'Exit code 101\nerror[E0425]: cannot find value `jump`', is_error=True)
        self.assertFalse(good)  # though Claude said it worked
        self.assertIn('ran and failed (exit code 101):\n        error[E0425]', line)
        good, line = self.try_with(build, f'cd {self.work} && {build}', 'all checks passed')
        self.assertFalse(good)
        self.assertIn(f'was not run as given: Claude ran `cd {self.work} && {build}`', line)
        self.assertNotIn('OUTSIDE_SANDBOX', line)

    # -- branches

    def branchy(self, site):
        """A stand-in that notes the git branch and the version it found, and makes the next one in both folders."""
        self.stand_in(HEAD + f"""\
n=$GAMEMAKER_PROMPT_ID
{{ git branch --show-current; cat ver.txt 2>/dev/null || echo none; }} > "$0.found.$n"
echo "v$n" > ver.txt && git add -A && git commit -qm "Iteration #$n: v$n" || exit 5
cd {site} && mkdir -p "v/$n" && echo "v$n" > "v/$n/index.html" && git add -A && git commit -qm "Iteration #$n" || exit 6
printf '{{"title": "Version %s", "summary": "It is v%s.", "branch": "Up to v%s.", "url": "%s"}}' "$n" "$n" "$n" \\
  "$GAMEMAKER_ITERATION_URL" > "$GAMEMAKER_RESULT_FILE"
echo '{{"type": "result", "is_error": false, "result": "All done.", "session_id": "sess-'$n'"}}'
""")

    def found(self, pid):
        return Path(f'{self.claude}.found.{pid}').read_text().split()

    def test_each_branch_starts_from_the_source_of_the_version_it_builds_on(self):
        site, site_origin = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')
        self.setting('WORK_BRANCH=main')
        sh('git', '-C', str(self.work), 'update-ref', 'refs/gamemaker/v/0', 'HEAD')
        self.store.touch_agent(3)
        self.branchy(site)
        one = self.store.create('brother', 'one')
        two = self.store.create('brother', 'two', one['id'])
        three = self.store.create('brother', 'three', one['id'])   # #1 goes on already (#2): branch B
        four = self.store.create('brother', 'four', 0)             # from the starting point: branch C
        five = self.store.create('brother', 'five', three['id'])   # B goes on
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertEqual([self.store.get(p['id'])['status'] for p in (one, two, three, four, five)], ['done'] * 5)
        # each run found the git branch of its branch, holding the version it builds on
        self.assertEqual([self.found(p['id']) for p in (one, two, three, four, five)],
                         [['main', 'none'], ['main', 'v1'], ['main-b', 'v1'], ['main-c', 'none'], ['main-b', 'v3']])
        for p in (one, two, three, four, five):  # and each version's source is recorded
            self.assertEqual(sh('git', '-C', str(self.work), 'show', f'refs/gamemaker/v/{p["id"]}:ver.txt'), f'v{p["id"]}')
        # the site stays one line, with every version
        self.assertEqual(self.pushed(site_origin)[0], f'Iteration #{five["id"]}')
        self.assertEqual(sorted(p.name for p in (site / 'v').iterdir()), ['1', '2', '3', '4', '5'])
        # what the brief said, and what reached the board
        runs = self.runs()
        self.assertEqual(['--resume' in a for a in runs], [False, True, False, False, False])  # only #2 goes on from the last
        brief = Path(f'{self.claude}.brief').read_text()  # #5's
        self.assertIn('This request builds on version #3 ("Version 3", in v/3/), on branch B, which split off from #1.', brief)
        self.assertIn('on git branch main-b', brief)
        self.assertIn('everything branch B has changed so far, compared to #1', brief)
        state = self.store.state('agent')
        self.assertEqual([p['title'] for p in state['prompts']][::-1], [f'Version {i}' for i in range(1, 6)])
        self.assertEqual({b['name']: b['summary'] for b in state['branches']},
                         {'Main': 'Up to v2.', 'B': 'Up to v5.', 'C': 'Up to v4.'})

    def test_a_branch_needs_the_source_of_where_it_starts(self):
        site, site_origin = repo(self.root, 'game-web')
        self.setting(f'SITE_DIR={site}')
        self.store.touch_agent(3)
        self.branchy(site)
        one = self.store.create('brother', 'one')
        two = self.store.create('brother', 'two', 0)
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        # no WORK_BRANCH: the main line goes on as before, a branch can't be made
        self.assertEqual(self.store.get(one['id'])['status'], 'done')
        failed = self.store.get(two['id'])
        self.assertEqual((failed['status'], failed['summary']), ('failed', "Not started: a branch needs WORK_BRANCH set on the agent's machine."))
        self.setting('WORK_BRANCH=main')
        three = self.store.create('brother', 'three', 0)
        self.assertEqual(watch.main(['watch.py', 'run']), 0)
        self.assertIn('the source of the starting point is not known', self.store.get(three['id'])['summary'])
        self.assertEqual(sh('git', '-C', str(self.work), 'branch', '--show-current'), 'main')
        self.assertEqual(len(self.runs()), 1)

    def test_check(self):
        self.stand_in(GOOD)
        self.config.chmod(0o600)
        watch.probe = lambda cfg, build=None: [(True, 'the fence held (stand-in)')]
        watch.sandbox_missing = lambda cfg: []
        self.assertEqual(watch.main(['watch.py', 'check']), 0)


if __name__ == '__main__':
    unittest.main()
