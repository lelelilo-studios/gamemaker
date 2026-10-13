#!/usr/bin/env python3
"""The prompt board behind board.example.com.

The brother writes prompts on the page. An agent on another machine (agent/watch.py) claims them, runs them with
Claude Code, publishes each result to GitHub Pages and reports back here, and the page shows that result.

Caddy stands in front (deploy/Caddyfile.gamemaker): only the allowed IPs get in, everyone logs in, and Caddy passes
the login name on in X-Gamemaker-User. This server trusts that header, so it listens on 127.0.0.1 only and turns
away any request that comes without it.

  python3 server.py [--port 8090] [--db var/gamemaker.sqlite3] [--agent-user agent] [--game-url https://.../game/]

--game-url is the game's GitHub Pages address: the page shows that game as the starting point, playable before the
first prompt. Once a prompt is done the root holds the newest version, so the starting point is then its frozen copy
in v/0/ (the agent makes it before it changes anything).

Branches. Every prompt builds on a version: the starting point (0) or an earlier prompt, by default the newest. The
first prompt to go on from a version stays on that version's branch; another one from the same version starts a new
branch (B, C, ...). A version that failed or was cancelled is skipped: what builds on it builds on the version before.

Each prompt also says in which language the page was (en or es) and whether Kids mode was on: the agent writes what
the page shows (title, notes, summary) in that language, in simple words for a child.

Notifications (push.py): each browser can subscribe, and hears when a prompt is done or failed, even with the page
closed. The board signs them with its own key, made on first start in var/vapid.pem.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import push

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'

MAX_BODY = 64 * 1024
MAX_TEXT = 20_000        # a prompt
MAX_SUMMARY = 20_000     # what the agent says it did (or why it failed)
MAX_NOTE = 2_000         # one progress note
MAX_URL = 2_000
LIST_LIMIT = 5_000      # the tree needs every prompt
STALE_AFTER = 30 * 60    # a running prompt with no heartbeat for this long may be stuck: it can be sent again

STATUSES = ('queued', 'running', 'done', 'failed', 'cancelled')
LIVE = ('queued', 'running', 'done')  # a version that is, or will be: what can be built on
MAX_TITLE = 120
MAX_BRANCH_SUMMARY = 1_000
CUT = 400                # how much of a prompt and of its summary the list carries (the viewer fetches the rest)
BRANCHES_FROM = 3        # the watcher's version (gamemaker-watch/N) that can build on any version
LANGS = ('en', 'es')     # the page's languages: the agent writes in the one a prompt was sent in

SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
  id          INTEGER PRIMARY KEY,
  text        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
  created_by  TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  claimed_by  TEXT,
  started_at  INTEGER,
  finished_at INTEGER,
  alive_at    INTEGER,
  updated_at  INTEGER NOT NULL,
  summary     TEXT,
  result_url  TEXT,
  base_id     INTEGER,             -- the version it builds on: 0 is the starting point
  branch      INTEGER NOT NULL DEFAULT 0 REFERENCES branches (id),
  title       TEXT,                -- a few words from the agent: what this version changes
  lang        TEXT,                -- the page's language when it was sent: en or es
  kids        INTEGER NOT NULL DEFAULT 0  -- sent in Kids mode: the agent writes for a child
);
CREATE INDEX IF NOT EXISTS prompts_status ON prompts (status, id);
-- 0 is the main line; the others get B, C, ... in the order they start, and keep it
CREATE TABLE IF NOT EXISTS branches (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  summary    TEXT,                  -- from the agent: what this branch changes, since it split off
  summary_at INTEGER,
  created_at INTEGER NOT NULL
);
-- the timeline of each prompt: kind is sent, retried, picked, note, done, failed or cancelled
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY,
  prompt_id INTEGER NOT NULL REFERENCES prompts (id),
  at        INTEGER NOT NULL,
  by        TEXT NOT NULL,
  kind      TEXT NOT NULL,
  message   TEXT
);
CREATE INDEX IF NOT EXISTS events_prompt ON events (prompt_id, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- the browsers that want a notification when a prompt ends, in their language
CREATE TABLE IF NOT EXISTS push_subs (
  endpoint   TEXT PRIMARY KEY,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  user       TEXT NOT NULL,
  lang       TEXT NOT NULL DEFAULT 'en',
  kids       INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  ok_at      INTEGER,
  failures   INTEGER NOT NULL DEFAULT 0
);
"""

SELECT_PROMPT = """
SELECT p.*, (SELECT message FROM events e WHERE e.prompt_id = p.id AND e.kind = 'note' ORDER BY e.id DESC LIMIT 1) AS last_note
FROM prompts p
"""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def now():
    return int(time.time())


def branch_name(n):
    """The name of the n-th branch: 0 is Main, then B, C, ..., Z, AA, AB, ..."""
    if n == 0:
        return 'Main'
    k, name = n + 1, ''
    while k:
        k, r = divmod(k - 1, 26)
        name = chr(65 + r) + name
    return name


class Lineage:
    """Every prompt's place in the tree, read in one go: where each one builds on, and the branches."""

    def __init__(self, db):
        self.rows = {r['id']: dict(r) for r in db.execute(
            'SELECT id, base_id, branch, status, title, text FROM prompts ORDER BY id')}
        self.branches = {r['id']: dict(r) for r in db.execute('SELECT * FROM branches ORDER BY id')}

    def live(self, pid):
        return pid == 0 or (pid in self.rows and self.rows[pid]['status'] in LIVE)

    def parent(self, pid):
        """The version a prompt builds on: its base, or the version before it if that failed or was cancelled."""
        base = self.rows[pid]['base_id'] or 0
        while base and base in self.rows and self.rows[base]['status'] not in LIVE:
            base = self.rows[base]['base_id'] or 0
        return base if base in self.rows else 0

    def done_parent(self, pid):
        """What a run builds on: the nearest version above it that is done (0, the starting point, at the top)."""
        base = self.parent(pid)
        while base and self.rows[base]['status'] != 'done':
            base = self.parent(base)
        return base

    def branch_of(self, version):
        return 0 if version == 0 else self.rows[version]['branch']

    def goes_on(self, version, branch, but=None):
        """Whether `branch` already goes on from `version`: a live prompt on it builds on that version."""
        return any(q['id'] != but and q['status'] in LIVE and q['branch'] == branch and self.parent(q['id']) == version
                   for q in self.rows.values())

    def newest_live(self):
        return next((pid for pid in sorted(self.rows, reverse=True) if self.rows[pid]['status'] in LIVE), 0)

    def fork(self, branch):
        """Where a branch split off: the version its first prompt builds on (None for the main line)."""
        if branch == 0:
            return None
        first = next((pid for pid in sorted(self.rows) if self.rows[pid]['branch'] == branch), None)
        return self.parent(first) if first is not None else None

    def title(self, version):
        if version == 0:
            return 'the starting point'
        row = self.rows[version]
        return row['title'] or ' '.join(row['text'].split()[:8])

    def new_branch(self, db, t):
        n = len(self.branches)
        cur = db.execute('INSERT INTO branches (id, name, created_at) VALUES ((SELECT MAX(id) + 1 FROM branches), ?, ?)',
                         (branch_name(n), t))
        self.branches[cur.lastrowid] = {'id': cur.lastrowid, 'name': branch_name(n), 'summary': None,
                                        'summary_at': None, 'created_at': t}
        return cur.lastrowid


def text_field(body, key, limit, required=False):
    value = body.get(key)
    if value is None:
        if required:
            raise ApiError(400, f'"{key}" is missing')
        return None
    if not isinstance(value, str):
        raise ApiError(400, f'"{key}" must be text')
    value = value.strip()
    if required and not value:
        raise ApiError(400, f'"{key}" is empty')
    if len(value) > limit:
        raise ApiError(413, f'"{key}" is longer than {limit} characters')
    return value or None


class Store:
    def __init__(self, path, result_hosts=('github.io',), game_url=None):
        self.path = str(path)
        self.pusher = None  # set by main() when push is on: told about every prompt that ends
        self.result_hosts = tuple(h.lower().lstrip('.') for h in result_hosts)
        self.game_url = game_url.rstrip('/') + '/' if game_url else None
        db = self._open()
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA foreign_keys=OFF')  # the columns added below point at a table that may be new
            db.executescript(SCHEMA)
            self._migrate(db)
        finally:
            db.close()

    @staticmethod
    def _migrate(db):
        """A board from before branches: its prompts were one line, each on the one before it."""
        cols = {r['name'] for r in db.execute('PRAGMA table_info(prompts)')}
        for col, decl in (('base_id', 'INTEGER'), ('branch', 'INTEGER NOT NULL DEFAULT 0'), ('title', 'TEXT'),
                          ('lang', 'TEXT'), ('kids', 'INTEGER NOT NULL DEFAULT 0')):
            if col not in cols:
                db.execute(f'ALTER TABLE prompts ADD COLUMN {col} {decl}')
        db.execute("INSERT OR IGNORE INTO branches (id, name, created_at) VALUES (0, 'Main', ?)", (now(),))
        before = 0
        for (pid, base) in db.execute('SELECT id, base_id FROM prompts ORDER BY id').fetchall():
            if base is None:
                db.execute('UPDATE prompts SET base_id = ? WHERE id = ?', (before, pid))
            before = pid

    def _open(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def reading(self):
        db = self._open()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def writing(self):
        # BEGIN IMMEDIATE takes the write lock up front, so two claims can't both see the same prompt as queued
        db = self._open()
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
            except BaseException:
                db.execute('ROLLBACK')
                raise
            db.execute('COMMIT')
        finally:
            db.close()

    # -- reading

    def _prompt(self, db, prompt_id):
        row = db.execute(SELECT_PROMPT + ' WHERE p.id = ?', (prompt_id,)).fetchone()
        if row is None:
            raise ApiError(404, f'there is no prompt #{prompt_id}')
        return row

    @staticmethod
    def _json(row, t):
        p = dict(row)
        p['stalled'] = p['status'] == 'running' and t - (p['alive_at'] or p['started_at'] or t) > STALE_AFTER
        return p

    @staticmethod
    def _agent_version(db):
        row = db.execute("SELECT value FROM meta WHERE key = 'agent_version'").fetchone()
        return int(row[0]) if row and str(row[0]).isdigit() else 0

    def state(self, agent_user):
        t = now()
        with self.reading() as db:
            rows = db.execute(SELECT_PROMPT + ' ORDER BY p.id DESC LIMIT ?', (LIST_LIMIT,)).fetchall()
            seen = db.execute("SELECT value FROM meta WHERE key = 'agent_seen'").fetchone()
            version = self._agent_version(db)
            counts = dict(db.execute('SELECT status, COUNT(*) FROM prompts GROUP BY status').fetchall())
            published = db.execute("SELECT 1 FROM prompts WHERE status = 'done' AND result_url IS NOT NULL LIMIT 1").fetchone()
            lin = Lineage(db)
        game = None
        if self.game_url:
            # the root is the newest version: after the first published result, the start is the copy in v/0/
            game = {'url': self.game_url, 'start_url': self.game_url + 'v/0/' if published else self.game_url}
        prompts = []
        for r in rows:
            p = self._json(r, t)
            # the list and the tree need only the start of the texts: the viewer fetches the whole prompt
            p['text'] = p['text'][:CUT]
            p['excerpt'] = (p.pop('summary') or '')[:CUT] or None
            p['parent'] = lin.parent(p['id'])
            prompts.append(p)
        branches = [{'id': b['id'], 'name': b['name'], 'summary': b['summary'], 'from': lin.fork(b['id']),
                     'created_at': b['created_at']} for b in lin.branches.values()]
        return {
            'now': t,
            'agent': {'user': agent_user, 'last_seen': int(seen[0]) if seen else None,
                      'version': version, 'branches': version >= BRANCHES_FROM},
            'game': game,
            'counts': {s: counts.get(s, 0) for s in STATUSES},
            'prompts': prompts,
            'branches': branches,
            'push': {'key': self.pusher.keys.public_b64} if self.pusher else None,
        }

    def get(self, prompt_id):
        t = now()
        with self.reading() as db:
            p = self._json(self._prompt(db, prompt_id), t)
            p['events'] = [dict(r) for r in db.execute(
                'SELECT at, by, kind, message FROM events WHERE prompt_id = ? ORDER BY id', (prompt_id,))]
        return p

    def peek(self):
        t = now()
        with self.reading() as db:
            row = db.execute(SELECT_PROMPT + " WHERE p.status = 'queued' ORDER BY p.id LIMIT 1").fetchone()
        return self._json(row, t) if row else None

    # -- writing

    @staticmethod
    def _event(db, prompt_id, t, by, kind, message=None):
        db.execute('INSERT INTO events (prompt_id, at, by, kind, message) VALUES (?, ?, ?, ?, ?)',
                   (prompt_id, t, by, kind, message))

    def touch_agent(self, version=None):
        with self.writing() as db:
            for key, value in (('agent_seen', now()), ('agent_version', version)):
                if value is not None:
                    db.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                               "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, str(value)))

    def create(self, user, text, base=None, lang='en', kids=False):
        """A new prompt, built on `base` (0: the starting point; None: the newest version, as before branches)."""
        t = now()
        with self.writing() as db:
            lin = Lineage(db)
            newest = lin.newest_live()
            if base is None:
                base = newest
            elif base != 0 and base not in lin.rows:
                raise ApiError(404, f'there is no prompt #{base}')
            elif not lin.live(base):
                raise ApiError(409, f'#{base} is {lin.rows[base]["status"]}: build on a version that is done or still to come')
            elif base != newest and self._agent_version(db) < BRANCHES_FROM:
                raise ApiError(409, "the agent's machine needs the new watcher before it can build on an earlier version")
            branch = lin.branch_of(base)
            if lin.goes_on(base, branch):
                branch = lin.new_branch(db, t)  # that branch already goes on from there: this starts another one
            prompt_id = db.execute(
                'INSERT INTO prompts (text, created_by, created_at, updated_at, base_id, branch, lang, kids) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (text, user, t, t, base, branch, lang, int(bool(kids)))).lastrowid
            self._event(db, prompt_id, t, user, 'sent')
            return self._json(self._prompt(db, prompt_id), t)

    def cancel(self, user, prompt_id):
        t = now()
        with self.writing() as db:
            p = self._prompt(db, prompt_id)
            if p['status'] != 'queued':
                raise ApiError(409, f'#{prompt_id} is {p["status"]}: only a waiting prompt can be cancelled')
            db.execute("UPDATE prompts SET status = 'cancelled', finished_at = ?, updated_at = ? WHERE id = ?",
                       (t, t, prompt_id))
            self._event(db, prompt_id, t, user, 'cancelled')
            return self._json(self._prompt(db, prompt_id), t)

    def retry(self, user, prompt_id):
        t = now()
        with self.writing() as db:
            p = self._json(self._prompt(db, prompt_id), t)
            if not (p['status'] in ('failed', 'cancelled') or p['stalled']):
                raise ApiError(409, f'#{prompt_id} is {p["status"]}: only a failed, cancelled or stuck prompt can be sent again')
            # if its branch went on from the same version meanwhile, it goes on its own branch now
            lin = Lineage(db)
            branch = p['branch']
            if lin.goes_on(lin.parent(prompt_id), branch, but=prompt_id):
                branch = lin.new_branch(db, t)
            db.execute("UPDATE prompts SET status = 'queued', claimed_by = NULL, started_at = NULL, finished_at = NULL, "
                       "alive_at = NULL, summary = NULL, result_url = NULL, title = NULL, branch = ?, updated_at = ? "
                       "WHERE id = ?", (branch, t, prompt_id))
            self._event(db, prompt_id, t, user, 'retried')
            return self._json(self._prompt(db, prompt_id), t)

    def claim(self, user, prompt_id=None):
        """The oldest waiting prompt (or the one asked for, if it is still waiting) is the agent's now: None if there is none."""
        t = now()
        with self.writing() as db:
            if prompt_id is None:
                row = db.execute("SELECT id FROM prompts WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
            else:
                row = db.execute("SELECT id FROM prompts WHERE status = 'queued' AND id = ?", (prompt_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE prompts SET status = 'running', claimed_by = ?, started_at = ?, alive_at = ?, updated_at = ? "
                       "WHERE id = ?", (user, t, t, t, row['id']))
            self._event(db, row['id'], t, user, 'picked')
            p = self._json(self._prompt(db, row['id']), t)
            # where it goes in the tree, for the agent: the version to start from, and its branch
            lin = Lineage(db)
            base, branch = lin.done_parent(p['id']), p['branch']
            fork = lin.fork(branch)
            p.update(base=base, base_title=lin.title(base), branch_name=lin.branches[branch]['name'],
                     branch_from=fork, branch_from_title=None if fork is None else lin.title(fork),
                     branch_summary=lin.branches[branch]['summary'])
            return p

    def heartbeat(self, user, prompt_id=None):
        if prompt_id is None:
            return
        with self.writing() as db:
            db.execute("UPDATE prompts SET alive_at = ? WHERE id = ? AND status = 'running' AND claimed_by = ?",
                       (now(), prompt_id, user))

    def check_url(self, url):
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        if parts.scheme != 'https' or not any(host == h or host.endswith('.' + h) for h in self.result_hosts):
            raise ApiError(400, f'result_url must be an https address on {", ".join(self.result_hosts)}')
        return url

    def update(self, user, prompt_id, status=None, message=None, summary=None, result_url=None, title=None,
               branch_summary=None):
        """A progress note while the agent works, and at the end done or failed with the summary and the result's address."""
        if status not in (None, 'done', 'failed'):
            raise ApiError(400, 'status can only be "done" or "failed"')
        if result_url is not None:
            self.check_url(result_url)
        t = now()
        with self.writing() as db:
            p = self._prompt(db, prompt_id)
            if p['status'] != 'running':
                raise ApiError(409, f'#{prompt_id} is {p["status"]}, not running')
            if p['claimed_by'] != user:
                raise ApiError(409, f'#{prompt_id} was picked up by {p["claimed_by"]}')
            sets, args = ['alive_at = ?', 'updated_at = ?'], [t, t]
            if summary is not None:
                sets.append('summary = ?'); args.append(summary)
            if result_url is not None:
                sets.append('result_url = ?'); args.append(result_url)
            if title is not None:
                sets.append('title = ?'); args.append(title)
            if branch_summary is not None:
                db.execute('UPDATE branches SET summary = ?, summary_at = ? WHERE id = ?', (branch_summary, t, p['branch']))
            if status:
                sets += ['status = ?', 'finished_at = ?']; args += [status, t]
            db.execute(f'UPDATE prompts SET {", ".join(sets)} WHERE id = ?', (*args, prompt_id))
            if message:
                self._event(db, prompt_id, t, user, 'note', message)
            if status:
                self._event(db, prompt_id, t, user, status)
            p = self._json(self._prompt(db, prompt_id), t)
        if status and self.pusher:
            self.pusher.finished(p)
        return p

    # -- who hears about it

    def subscribe(self, user, endpoint, p256dh, auth, lang, kids):
        with self.writing() as db:
            known = db.execute('SELECT 1 FROM push_subs WHERE endpoint = ?', (endpoint,)).fetchone()
            if not known and db.execute('SELECT COUNT(*) FROM push_subs').fetchone()[0] >= push.MAX_SUBSCRIPTIONS:
                raise ApiError(409, 'too many browsers are signed up for notifications already')
            db.execute('INSERT INTO push_subs (endpoint, p256dh, auth, user, lang, kids, created_at) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (endpoint) DO UPDATE SET p256dh = excluded.p256dh, '
                       'auth = excluded.auth, user = excluded.user, lang = excluded.lang, kids = excluded.kids',
                       (endpoint, p256dh, auth, user, lang, int(bool(kids)), now()))

    def unsubscribe(self, endpoint):
        with self.writing() as db:
            db.execute('DELETE FROM push_subs WHERE endpoint = ?', (endpoint,))

    def subscriptions(self):
        with self.reading() as db:
            return [dict(r) for r in db.execute('SELECT * FROM push_subs ORDER BY created_at')]

    def subscription(self, endpoint):
        with self.reading() as db:
            row = db.execute('SELECT * FROM push_subs WHERE endpoint = ?', (endpoint,)).fetchone()
        return dict(row) if row else None

    def push_ok(self, endpoint):
        with self.writing() as db:
            db.execute('UPDATE push_subs SET ok_at = ?, failures = 0 WHERE endpoint = ?', (now(), endpoint))

    def push_failed(self, endpoint):
        with self.writing() as db:
            db.execute('UPDATE push_subs SET failures = failures + 1 WHERE endpoint = ?', (endpoint,))


# -- HTTP

STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/words.js': ('words.js', 'text/javascript; charset=utf-8'),
    '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
    # the service worker shows the notifications: it lives at the root, so it covers the whole board
    '/sw.js': ('sw.js', 'text/javascript; charset=utf-8'),
    '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
    '/fonts/nunito-v1.woff2': ('fonts/nunito-v1.woff2', 'font/woff2'),
    '/fonts/fredoka-v1.woff2': ('fonts/fredoka-v1.woff2', 'font/woff2'),
    '/icons/icon-192-v1.png': ('icons/icon-192-v1.png', 'image/png'),
    '/icons/icon-512-v1.png': ('icons/icon-512-v1.png', 'image/png'),
    '/icons/apple-touch-icon-v1.png': ('icons/apple-touch-icon-v1.png', 'image/png'),
}
# these never change under the same name (a new one gets a new -vN): browsers may keep them
IMMUTABLE = ('/fonts/', '/icons/')
# the watcher is fetched from here by the agent's machine, so a fix made here reaches it with one download
DOWNLOADS = {'/agent/watch.py': (ROOT / 'agent' / 'watch.py', 'text/plain; charset=utf-8')}

# (method, path, who may call it, handler)
ROUTES = [
    ('GET', r'/api/state', 'any', 'api_state'),
    ('GET', r'/api/prompts/(\d+)', 'any', 'api_prompt'),
    ('POST', r'/api/prompts', 'brother', 'api_create'),
    ('POST', r'/api/prompts/(\d+)/cancel', 'brother', 'api_cancel'),
    ('POST', r'/api/prompts/(\d+)/retry', 'brother', 'api_retry'),
    ('GET', r'/api/agent/next', 'agent', 'api_next'),
    ('POST', r'/api/agent/claim', 'agent', 'api_claim'),
    ('POST', r'/api/agent/heartbeat', 'agent', 'api_heartbeat'),
    ('POST', r'/api/prompts/(\d+)/update', 'agent', 'api_update'),
    ('POST', r'/api/push/subscribe', 'any', 'api_subscribe'),
    ('POST', r'/api/push/unsubscribe', 'any', 'api_unsubscribe'),
    ('POST', r'/api/push/test', 'any', 'api_push_test'),
]
ROUTES = [(m, re.compile(p + r'/?'), who, name) for m, p, who, name in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = 'gamemaker'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    # set on the class by make_server
    store: Store = None
    agent_user = 'agent'
    origins = ()
    unread = False  # the request has a body we haven't read

    # -- plumbing

    def log_request(self, code='-', size='-'):
        # the agent polls every 30 s and the page every few seconds: only changes and errors go to the log
        if self.command == 'POST' or (str(code).isdigit() and int(code) >= 400):
            super().log_request(code, size)

    def send_bytes(self, status, data, content_type, extra=()):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        path = urlsplit(self.path).path
        self.send_header('Cache-Control', 'no-store' if path.startswith('/api/')
                         else 'public, max-age=31536000, immutable' if status == 200 and path.startswith(IMMUTABLE)
                         else 'no-cache')
        for k, v in extra:
            self.send_header(k, v)
        if self.close_connection or self.unread:
            # a body we turned away unread would be taken for the next request on this connection: end it here
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def send_json(self, status, obj):
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def send_error_json(self, status, message):
        self.send_json(status, {'error': message})

    def read_json(self):
        if self.headers.get_content_type() != 'application/json':
            raise ApiError(415, 'send JSON (Content-Type: application/json)')
        # a page on another site can't send JSON here without asking first, but it can try: browsers say where from
        origin = self.headers.get('Origin')
        if origin is not None and origin not in self.origins:
            raise ApiError(403, 'requests from other sites are not accepted')
        if 'Transfer-Encoding' in self.headers:
            raise ApiError(411, 'send a Content-Length')
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise ApiError(400, 'bad Content-Length')
        if length > MAX_BODY:
            raise ApiError(413, 'request too large')
        raw = self.rfile.read(length) if length else b''
        self.unread = 0
        if not raw.strip():
            return {}
        try:
            body = json.loads(raw)
        except ValueError:
            raise ApiError(400, 'that is not valid JSON')
        if not isinstance(body, dict):
            raise ApiError(400, 'send a JSON object')
        return body

    def who(self):
        user = (self.headers.get('X-Gamemaker-User') or '').strip()
        if not user:
            raise ApiError(401, 'not logged in')
        return user, 'agent' if user == self.agent_user else 'brother'

    def handle_any(self):
        path = urlsplit(self.path).path
        self.unread = self.headers.get('Content-Length') not in (None, '0') or 'Transfer-Encoding' in self.headers
        try:
            user, role = self.who()
            if role == 'agent':
                m = re.match(r'gamemaker-watch/(\d+)', self.headers.get('User-Agent') or '')
                self.store.touch_agent(int(m.group(1)) if m else None)
            for method, pattern, allowed, name in ROUTES:
                m = pattern.fullmatch(path)
                if m and method == self.command:
                    if allowed != 'any' and allowed != role:
                        raise ApiError(403, f'only the {allowed} can do that')
                    body = self.read_json() if method == 'POST' else None
                    getattr(self, name)(user, body, *(int(g) for g in m.groups()))
                    return
            if path.startswith('/api/'):
                raise ApiError(404 if not any(p.fullmatch(path) for _, p, _, _ in ROUTES) else 405, 'no such call')
            if self.command in ('GET', 'HEAD') and (path in STATIC or path in DOWNLOADS):
                file, content_type = (WEB / STATIC[path][0], STATIC[path][1]) if path in STATIC else DOWNLOADS[path]
                self.send_bytes(200, file.read_bytes(), content_type)
                return
            self.send_bytes(404, b'Not here.\n', 'text/plain; charset=utf-8')
        except ApiError as e:
            if e.status == 401:
                self.close_connection = True
            self.send_error_json(e.status, e.message)
        except Exception as e:  # pragma: no cover - logged, and the client gets a 500
            self.log_error('error on %s %s: %r', self.command, path, e)
            self.close_connection = True
            self.send_error_json(500, 'something went wrong on the server')

    do_GET = do_HEAD = do_POST = handle_any

    # -- calls

    def api_state(self, user, body):
        state = self.store.state(self.agent_user)
        state['me'] = {'user': user, 'role': 'agent' if user == self.agent_user else 'brother'}
        self.send_json(200, state)

    def api_prompt(self, user, body, prompt_id):
        self.send_json(200, self.store.get(prompt_id))

    def api_create(self, user, body):
        base = body.get('base')
        if base is not None and (not isinstance(base, int) or isinstance(base, bool) or base < 0):
            raise ApiError(400, '"base" must be a version number')
        lang, kids = self.lang_kids(body)
        self.send_json(201, self.store.create(user, text_field(body, 'text', MAX_TEXT, required=True), base, lang, kids))

    @staticmethod
    def lang_kids(body):
        lang, kids = body.get('lang', 'en'), body.get('kids', False)
        if lang not in LANGS:
            raise ApiError(400, f'"lang" must be one of {", ".join(LANGS)}')
        if not isinstance(kids, bool):
            raise ApiError(400, '"kids" must be true or false')
        return lang, kids

    def api_cancel(self, user, body, prompt_id):
        self.send_json(200, self.store.cancel(user, prompt_id))

    def api_retry(self, user, body, prompt_id):
        self.send_json(200, self.store.retry(user, prompt_id))

    def api_next(self, user, body):
        p = self.store.peek()
        if p is None:
            self.send_bytes(204, b'', 'application/json')
        else:
            self.send_json(200, p)

    def api_claim(self, user, body):
        prompt_id = body.get('id')
        if prompt_id is not None and (not isinstance(prompt_id, int) or isinstance(prompt_id, bool)):
            raise ApiError(400, '"id" must be a number')
        p = self.store.claim(user, prompt_id)
        if p is None:
            self.send_bytes(204, b'', 'application/json')
        else:
            self.send_json(200, p)

    def api_heartbeat(self, user, body):
        prompt_id = body.get('prompt_id')
        if prompt_id is not None and (not isinstance(prompt_id, int) or isinstance(prompt_id, bool)):
            raise ApiError(400, '"prompt_id" must be a number')
        self.store.heartbeat(user, prompt_id)
        self.send_json(200, {'ok': True})

    def api_update(self, user, body, prompt_id):
        self.send_json(200, self.store.update(
            user, prompt_id,
            status=body.get('status'),
            message=text_field(body, 'message', MAX_NOTE),
            summary=text_field(body, 'summary', MAX_SUMMARY),
            result_url=text_field(body, 'result_url', MAX_URL),
            title=text_field(body, 'title', MAX_TITLE),
            branch_summary=text_field(body, 'branch_summary', MAX_BRANCH_SUMMARY),
        ))

    def pusher(self):
        if not self.store.pusher:
            raise ApiError(503, 'notifications are off on this board')
        return self.store.pusher

    def api_subscribe(self, user, body):
        self.pusher()
        try:
            endpoint, p256dh, auth = push.check_subscription(body.get('subscription') or {})
        except ValueError as e:
            raise ApiError(400, str(e))
        lang, kids = self.lang_kids(body)
        self.store.subscribe(user, endpoint, p256dh, auth, lang, kids)
        self.send_json(200, {'ok': True})

    def api_unsubscribe(self, user, body):
        endpoint = body.get('endpoint')
        if not isinstance(endpoint, str):
            raise ApiError(400, '"endpoint" is missing')
        self.store.unsubscribe(endpoint)
        self.send_json(200, {'ok': True})

    def api_push_test(self, user, body):
        endpoint = body.get('endpoint')
        if not isinstance(endpoint, str) or not self.store.subscription(endpoint):
            raise ApiError(404, 'that browser is not signed up for notifications')
        self.pusher().test(endpoint)
        self.send_json(202, {'ok': True})


def make_server(host, port, store, agent_user='agent', origins=('https://board.example.com',)):
    handler = type('BoundHandler', (Handler,), {'store': store, 'agent_user': agent_user, 'origins': tuple(origins)})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8090)
    ap.add_argument('--db', default=str(ROOT / 'var' / 'gamemaker.sqlite3'))
    ap.add_argument('--agent-user', default='agent', help='the login the agent uses (every other login is the brother)')
    ap.add_argument('--origin', action='append', help='address the page is served from (repeatable)')
    ap.add_argument('--result-host', action='append', help='where results may live (default github.io; repeatable)')
    ap.add_argument('--game-url', help="the game's GitHub Pages address, shown as the starting point")
    ap.add_argument('--vapid-key', default=str(ROOT / 'var' / 'vapid.pem'),
                    help='the key notifications are signed with (made on first start)')
    ap.add_argument('--push-subject', default='https://board.example.com',
                    help='who the push services can contact about this board (https:// or mailto:)')
    args = ap.parse_args()
    if args.game_url and not args.game_url.startswith('https://'):
        ap.error('--game-url must be an https:// address')

    Path(args.db).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Store(args.db, args.result_host or ['github.io'], args.game_url)
    if push.AVAILABLE:
        store.pusher = push.Pusher(store, args.vapid_key, args.push_subject)
    else:
        print('notifications are off: python3-cryptography is not installed', file=sys.stderr, flush=True)
    server = make_server(args.host, args.port, store, args.agent_user, args.origin or ['https://board.example.com'])
    print(f'gamemaker board on http://{args.host}:{args.port} (db {args.db})', file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
