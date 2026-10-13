"""The board's API, against a real server on a free port with a throwaway database.

  python3 -m unittest discover -s tests
"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

ORIGIN = 'https://board.example.com'


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store = server.Store(Path(self.tmp.name) / 'board.sqlite3')
        self.httpd = server.make_server('127.0.0.1', 0, store, origins=[ORIGIN])
        self.httpd.RequestHandlerClass.log_message = lambda *args: None
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, method, path, user='brother', body=None, headers=None, raw=None):
        h = {}
        if user:
            h['X-Gamemaker-User'] = user
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as res:
                text = res.read()
                return res.status, (json.loads(text) if text and 'json' in res.headers.get('Content-Type', '') else text)
        except urllib.error.HTTPError as e:
            with e:
                text = e.read()
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, text

    def send(self, text='make the ball bounce'):
        status, p = self.call('POST', '/api/prompts', body={'text': text})
        self.assertEqual(status, 201, p)
        return p

    # -- who may do what

    def test_no_login_header_is_turned_away(self):
        self.assertEqual(self.call('GET', '/api/state', user=None)[0], 401)
        self.assertEqual(self.call('GET', '/', user=None)[0], 401)

    def test_page_and_watcher_are_served(self):
        status, body = self.call('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'Lelelilo Gamemaker', body)
        status, body = self.call('GET', '/agent/watch.py', user='agent')
        self.assertEqual(status, 200)
        self.assertIn(b'def main', body)
        self.assertEqual(self.call('GET', '/../server.py')[0], 404)

    def test_roles(self):
        self.assertEqual(self.call('POST', '/api/prompts', user='agent', body={'text': 'x'})[0], 403)
        self.assertEqual(self.call('POST', '/api/agent/claim', user='brother', body={})[0], 403)
        self.assertEqual(self.call('GET', '/api/agent/next', user='brother')[0], 403)
        p = self.send()
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/cancel', user='agent', body={})[0], 403)

    def test_any_other_login_uses_the_page_like_the_brother(self):
        status, s = self.call('GET', '/api/state', user='guest')
        self.assertEqual(s['me'], {'user': 'guest', 'role': 'brother'})
        status, p = self.call('POST', '/api/prompts', user='guest', body={'text': 'a snowy level'})
        self.assertEqual((status, p['created_by']), (201, 'guest'))
        self.assertEqual(self.call('POST', '/api/agent/claim', user='guest', body={})[0], 403)

    def test_state_says_who_is_asking_and_when_the_agent_was_seen(self):
        status, s = self.call('GET', '/api/state')
        self.assertEqual(s['me'], {'user': 'brother', 'role': 'brother'})
        self.assertIsNone(s['agent']['last_seen'])
        self.call('GET', '/api/agent/next', user='agent')
        status, s = self.call('GET', '/api/state')
        self.assertIsNotNone(s['agent']['last_seen'])

    # -- requests from browsers

    def test_post_needs_json(self):
        status, _ = self.call('POST', '/api/prompts', raw=b'text=hi', headers={'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(status, 415)

    def test_post_from_another_site_is_refused(self):
        status, _ = self.call('POST', '/api/prompts', body={'text': 'hi'}, headers={'Origin': 'https://evil.example'})
        self.assertEqual(status, 403)
        status, _ = self.call('POST', '/api/prompts', body={'text': 'hi'}, headers={'Origin': ORIGIN})
        self.assertEqual(status, 201)

    def test_a_refused_body_does_not_spill_into_the_next_request(self):
        # Caddy keeps connections open: a body turned away unread must not be read as the next request
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1])
        try:
            for headers in ({'Origin': 'https://evil.example'}, {}):
                body = json.dumps({'text': 'hi'})
                h = {'X-Gamemaker-User': 'agent' if not headers else 'brother', 'Content-Type': 'application/json', **headers}
                conn.request('POST', '/api/prompts', body=body, headers=h)
                res = conn.getresponse()
                res.read()
                self.assertEqual(res.status, 403)
                self.assertEqual(res.getheader('Connection'), 'close')
                conn.request('GET', '/api/state', headers={'X-Gamemaker-User': 'brother'})
                res = conn.getresponse()
                self.assertEqual(res.status, 200, res.read())
                res.read()
            # a body that was read leaves the connection open
            conn.request('POST', '/api/prompts', body=json.dumps({'text': 'hi'}),
                         headers={'X-Gamemaker-User': 'brother', 'Content-Type': 'application/json'})
            res = conn.getresponse()
            res.read()
            self.assertEqual((res.status, res.getheader('Connection')), (201, None))
        finally:
            conn.close()

    def test_prompt_text_is_checked(self):
        self.assertEqual(self.call('POST', '/api/prompts', body={'text': '   '})[0], 400)
        self.assertEqual(self.call('POST', '/api/prompts', body={'text': 5})[0], 400)
        self.assertEqual(self.call('POST', '/api/prompts', body={})[0], 400)
        self.assertEqual(self.call('POST', '/api/prompts', body={'text': 'x' * (server.MAX_TEXT + 1)})[0], 413)

    # -- the life of a prompt

    def test_a_prompt_from_sent_to_done(self):
        p = self.send()
        self.assertEqual(p['status'], 'queued')

        status, peek = self.call('GET', '/api/agent/next', user='agent')
        self.assertEqual((status, peek['id']), (200, p['id']))

        status, claimed = self.call('POST', '/api/agent/claim', user='agent', body={'id': p['id']})
        self.assertEqual((status, claimed['status'], claimed['claimed_by']), (200, 'running', 'agent'))
        self.assertEqual(self.call('GET', '/api/agent/next', user='agent')[0], 204)

        status, upd = self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'message': 'Drawing the ball'})
        self.assertEqual((status, upd['status'], upd['last_note']), (200, 'running', 'Drawing the ball'))
        self.assertEqual(self.call('POST', '/api/agent/heartbeat', user='agent', body={'prompt_id': p['id']})[0], 200)

        url = 'https://lelelilo-studios.github.io/brother-game/v/1/'
        status, done = self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent',
                                 body={'status': 'done', 'summary': 'The ball bounces.', 'result_url': url})
        self.assertEqual((status, done['status'], done['result_url'], done['summary']), (200, 'done', url, 'The ball bounces.'))
        self.assertIsNotNone(done['finished_at'])

        status, full = self.call('GET', f'/api/prompts/{p["id"]}')
        self.assertEqual([e['kind'] for e in full['events']], ['sent', 'picked', 'note', 'done'])

        # done is done: no more updates, no cancelling, no sending again
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'message': 'more'})[0], 409)
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/cancel', body={})[0], 409)
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/retry', body={})[0], 409)

    def test_claim_takes_the_oldest_and_nothing_twice(self):
        a, b = self.send('first'), self.send('second')
        self.assertEqual(self.call('POST', '/api/agent/claim', user='agent', body={})[1]['id'], a['id'])
        self.assertEqual(self.call('POST', '/api/agent/claim', user='agent', body={})[1]['id'], b['id'])
        self.assertEqual(self.call('POST', '/api/agent/claim', user='agent', body={})[0], 204)

    def test_claims_at_the_same_time_never_share_a_prompt(self):
        ids = [self.send(f'prompt {i}')['id'] for i in range(5)]
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.call('POST', '/api/agent/claim', user='agent', body={}), range(12)))
        claimed = [body['id'] for status, body in results if status == 200]
        self.assertEqual(sorted(claimed), ids)
        self.assertEqual(sum(1 for status, _ in results if status == 204), 7)

    def test_cancel_then_send_again(self):
        p = self.send()
        status, c = self.call('POST', f'/api/prompts/{p["id"]}/cancel', body={})
        self.assertEqual((status, c['status']), (200, 'cancelled'))
        self.assertEqual(self.call('POST', '/api/agent/claim', user='agent', body={})[0], 204)
        status, r = self.call('POST', f'/api/prompts/{p["id"]}/retry', body={})
        self.assertEqual((status, r['status']), (200, 'queued'))
        self.assertEqual(self.call('POST', '/api/agent/claim', user='agent', body={})[1]['id'], p['id'])

    def test_a_running_prompt_cannot_be_cancelled_or_sent_again_unless_stuck(self):
        p = self.send()
        self.call('POST', '/api/agent/claim', user='agent', body={})
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/cancel', body={})[0], 409)
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/retry', body={})[0], 409)
        # no heartbeat for longer than STALE_AFTER: it shows as stuck and can be sent again
        db = self.httpd.RequestHandlerClass.store._open()
        db.execute('UPDATE prompts SET alive_at = alive_at - ? WHERE id = ?', (server.STALE_AFTER + 5, p['id']))
        db.close()
        status, s = self.call('GET', '/api/state')
        self.assertTrue(s['prompts'][0]['stalled'])
        status, r = self.call('POST', f'/api/prompts/{p["id"]}/retry', body={})
        self.assertEqual((status, r['status'], r['claimed_by']), (200, 'queued', None))

    def test_failed_then_sent_again_clears_the_old_result(self):
        p = self.send()
        self.call('POST', '/api/agent/claim', user='agent', body={})
        status, f = self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'status': 'failed', 'summary': 'boom'})
        self.assertEqual((status, f['status']), (200, 'failed'))
        status, r = self.call('POST', f'/api/prompts/{p["id"]}/retry', body={})
        self.assertEqual((r['status'], r['summary'], r['finished_at']), ('queued', None, None))

    def test_only_done_or_failed_and_only_while_running(self):
        p = self.send()
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'message': 'hi'})[0], 409)
        self.call('POST', '/api/agent/claim', user='agent', body={})
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'status': 'queued'})[0], 400)
        self.assertEqual(self.call('POST', f'/api/prompts/{p["id"]}/update', user='brother', body={'message': 'x'})[0], 403)
        self.assertEqual(self.call('POST', '/api/prompts/999/update', user='agent', body={'message': 'x'})[0], 404)

    def test_result_url_must_be_https_on_github_io(self):
        p = self.send()
        self.call('POST', '/api/agent/claim', user='agent', body={})
        for bad in ('http://x.github.io/', 'javascript:alert(1)', 'https://github.io.evil.com/', 'https://example.com/'):
            status, _ = self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'result_url': bad})
            self.assertEqual(status, 400, bad)
        status, _ = self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'result_url': 'https://someone.github.io/game/'})
        self.assertEqual(status, 200)

    def test_the_starting_point_moves_to_v0_once_a_result_is_published(self):
        self.assertIsNone(self.call('GET', '/api/state')[1]['game'])
        self.httpd.RequestHandlerClass.store.game_url = 'https://someone.github.io/game/'
        root = {'url': 'https://someone.github.io/game/', 'start_url': 'https://someone.github.io/game/'}
        self.assertEqual(self.call('GET', '/api/state')[1]['game'], root)
        # working on it, or done with nothing published: the root is still the starting point
        p = self.send()
        self.call('POST', '/api/agent/claim', user='agent', body={})
        self.assertEqual(self.call('GET', '/api/state')[1]['game'], root)
        self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent',
                  body={'status': 'done', 'result_url': f'https://someone.github.io/game/v/{p["id"]}/'})
        self.assertEqual(self.call('GET', '/api/state')[1]['game']['start_url'], 'https://someone.github.io/game/v/0/')

    # -- branches

    NEW_WATCHER = {'User-Agent': 'gamemaker-watch/3'}

    def build(self, base, text='make it so'):
        status, p = self.call('POST', '/api/prompts', body={'text': text, 'base': base})
        self.assertEqual(status, 201, p)
        return p

    def finish(self, pid, status='done', **more):
        status_, claimed = self.call('POST', '/api/agent/claim', user='agent', body={'id': pid}, headers=self.NEW_WATCHER)
        self.assertEqual(status_, 200, claimed)
        body = {'status': status, **more}
        if status == 'done':
            body.setdefault('result_url', f'https://someone.github.io/game/v/{pid}/')
        self.assertEqual(self.call('POST', f'/api/prompts/{pid}/update', user='agent', body=body)[0], 200)
        return claimed

    def tree(self):
        state = self.call('GET', '/api/state')[1]
        return ({p['id']: (p['parent'], p['branch']) for p in state['prompts']},
                {b['id']: (b['name'], b['from']) for b in state['branches']})

    def test_a_version_that_already_goes_on_starts_a_branch(self):
        self.call('GET', '/api/agent/next', user='agent', headers=self.NEW_WATCHER)
        one = self.send('a parry')
        self.finish(one['id'])
        two = self.build(one['id'], 'a floaty jump')
        self.finish(two['id'])
        three = self.build(one['id'], 'a pillow instead of the parry')  # #1 already goes on (#2): a new branch
        four = self.build(three['id'], 'bouncy enemies')               # the end of that branch: it goes on
        five = self.build(0, 'start over: a cat')                       # the starting point already goes on too
        six = self.build(two['id'], 'a bouncy scenery')                 # the end of the main line
        prompts, branches = self.tree()
        b, c = three['branch'], five['branch']
        self.assertEqual(prompts, {one['id']: (0, 0), two['id']: (1, 0), three['id']: (1, b), four['id']: (3, b),
                                   five['id']: (0, c), six['id']: (2, 0)})
        self.assertEqual(branches, {0: ('Main', None), b: ('B', 1), c: ('C', 0)})

    def test_a_failed_version_is_skipped(self):
        self.call('GET', '/api/agent/next', user='agent', headers=self.NEW_WATCHER)
        one = self.send()
        self.finish(one['id'])
        two = self.build(one['id'])
        three = self.build(two['id'])  # queued behind #2
        self.finish(two['id'], 'failed', summary='boom')
        prompts, _ = self.tree()
        self.assertEqual(prompts[three['id']], (one['id'], 0))  # it builds on #1 now
        claimed = self.call('POST', '/api/agent/claim', user='agent', body={'id': three['id']})[1]
        self.assertEqual((claimed['base'], claimed['branch_name'], claimed['branch_from']), (one['id'], 'Main', None))
        # #3 goes on from #1 on the main line, so another prompt from #1 starts a branch
        self.assertNotEqual(self.build(one['id'])['branch'], 0)
        status, err = self.call('POST', '/api/prompts', body={'text': 'x', 'base': two['id']})
        self.assertEqual(status, 409, err)
        self.assertEqual(self.call('POST', '/api/prompts', body={'text': 'x', 'base': 99})[0], 404)
        self.assertEqual(self.call('POST', '/api/prompts', body={'text': 'x', 'base': 'one'})[0], 400)

    def test_sent_again_it_takes_a_new_branch_only_if_its_place_was_taken(self):
        self.call('GET', '/api/agent/next', user='agent', headers=self.NEW_WATCHER)
        one = self.send()
        self.finish(one['id'])
        two = self.build(one['id'])
        self.finish(two['id'], 'failed')
        three = self.build(one['id'])  # #2 failed: this one goes on from #1 on the main line
        self.assertEqual(three['branch'], 0)
        again = self.call('POST', f'/api/prompts/{two["id"]}/retry', body={})[1]
        self.assertNotEqual(again['branch'], 0)
        self.finish(three['id'])
        four = self.build(three['id'])
        self.finish(four['id'], 'failed')
        again = self.call('POST', f'/api/prompts/{four["id"]}/retry', body={})[1]
        self.assertEqual(again['branch'], 0)  # nothing took its place

    def test_the_agent_learns_where_to_start_and_says_what_it_made(self):
        self.call('GET', '/api/agent/next', user='agent', headers=self.NEW_WATCHER)
        one = self.send('a parry')
        self.finish(one['id'], title='A parry in slow motion')
        two = self.build(one['id'])
        self.finish(two['id'], title='A floaty jump')
        three = self.build(one['id'], 'a pillow')
        claimed = self.call('POST', '/api/agent/claim', user='agent', body={}, headers=self.NEW_WATCHER)[1]
        self.assertEqual((claimed['id'], claimed['base'], claimed['base_title'], claimed['branch_name'],
                          claimed['branch_from'], claimed['branch_from_title']),
                         (three['id'], one['id'], 'A parry in slow motion', 'B', one['id'], 'A parry in slow motion'))
        self.call('POST', f'/api/prompts/{three["id"]}/update', user='agent',
                  body={'status': 'done', 'title': 'A soft pillow', 'branch_summary': 'The parry gives way to a pillow.',
                        'result_url': 'https://someone.github.io/game/v/3/'})
        state = self.call('GET', '/api/state')[1]
        self.assertEqual([p['title'] for p in state['prompts']], ['A soft pillow', 'A floaty jump', 'A parry in slow motion'])
        self.assertEqual([b['summary'] for b in state['branches']], [None, 'The parry gives way to a pillow.'])

    def test_until_the_new_watcher_is_there_only_the_newest_can_be_built_on(self):
        one = self.send()
        self.call('POST', '/api/agent/claim', user='agent', body={})
        self.call('POST', f'/api/prompts/{one["id"]}/update', user='agent', body={'status': 'done'})
        self.send()
        self.assertFalse(self.call('GET', '/api/state')[1]['agent']['branches'])
        status, err = self.call('POST', '/api/prompts', body={'text': 'x', 'base': one['id']})
        self.assertEqual(status, 409, err)
        self.call('GET', '/api/agent/next', user='agent', headers=self.NEW_WATCHER)
        self.assertTrue(self.call('GET', '/api/state')[1]['agent']['branches'])
        self.assertEqual(self.call('POST', '/api/prompts', body={'text': 'x', 'base': one['id']})[0], 201)

    def test_the_list_carries_the_start_of_long_texts(self):
        p = self.send('x' * 5000)
        self.call('POST', '/api/agent/claim', user='agent', body={})
        self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'status': 'done', 'summary': 'y' * 5000})
        listed = self.call('GET', '/api/state')[1]['prompts'][0]
        self.assertEqual((len(listed['text']), len(listed['excerpt'])), (server.CUT, server.CUT))
        self.assertNotIn('summary', listed)
        self.assertEqual(len(self.call('GET', f'/api/prompts/{p["id"]}')[1]['summary']), 5000)

    def test_a_board_from_before_branches_becomes_one_line(self):
        path = Path(self.tmp.name) / 'old.sqlite3'
        import sqlite3
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE prompts (id INTEGER PRIMARY KEY, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
              created_by TEXT NOT NULL, created_at INTEGER NOT NULL, claimed_by TEXT, started_at INTEGER,
              finished_at INTEGER, alive_at INTEGER, updated_at INTEGER NOT NULL, summary TEXT, result_url TEXT);
            CREATE TABLE events (id INTEGER PRIMARY KEY, prompt_id INTEGER NOT NULL, at INTEGER NOT NULL, by TEXT NOT NULL,
              kind TEXT NOT NULL, message TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO prompts (text, status, created_by, created_at, updated_at) VALUES
              ('a', 'done', 'b', 1, 1), ('b', 'done', 'b', 2, 2), ('c', 'failed', 'b', 3, 3);
        """)
        db.close()
        store = server.Store(path)
        server.Store(path)  # twice: nothing changes the second time
        state = store.state('agent')
        self.assertEqual({p['id']: (p['base_id'], p['parent'], p['branch']) for p in state['prompts']},
                         {1: (0, 0, 0), 2: (1, 1, 0), 3: (2, 2, 0)})
        self.assertEqual([(b['id'], b['name']) for b in state['branches']], [(0, 'Main')])
        self.assertEqual(store.create('brother', 'd')['base_id'], 2)

    def test_branch_names(self):
        self.assertEqual([server.branch_name(n) for n in (0, 1, 2, 25, 26, 27)], ['Main', 'B', 'C', 'Z', 'AA', 'AB'])

    # -- language and Kids mode

    def test_a_prompt_says_its_language_and_whether_it_was_sent_in_kids_mode(self):
        plain = self.send('make it snow')
        self.assertEqual((plain['lang'], plain['kids']), ('en', 0))
        status, p = self.call('POST', '/api/prompts', body={'text': 'que nieve', 'lang': 'es', 'kids': True})
        self.assertEqual(status, 201, p)
        self.assertEqual((p['lang'], p['kids']), ('es', 1))
        for bad in ({'lang': 'fr'}, {'lang': None}, {'kids': 'yes'}, {'kids': 1}):
            self.assertEqual(self.call('POST', '/api/prompts', body={'text': 'x', **bad})[0], 400, bad)
        self.call('POST', '/api/agent/claim', user='agent', body={'id': plain['id']})
        status, claimed = self.call('POST', '/api/agent/claim', user='agent', body={})
        self.assertEqual((claimed['id'], claimed['lang'], claimed['kids']), (p['id'], 'es', 1))
        # sent again, it keeps them
        self.call('POST', f'/api/prompts/{p["id"]}/update', user='agent', body={'status': 'failed'})
        status, again = self.call('POST', f'/api/prompts/{p["id"]}/retry', body={})
        self.assertEqual((again['lang'], again['kids']), ('es', 1))

    def test_every_page_file_is_there(self):
        for path, (name, kind) in server.STATIC.items():
            status, body = self.call('GET', path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body, path)
        req = urllib.request.Request(self.base + '/fonts/nunito-v1.woff2', headers={'X-Gamemaker-User': 'brother'})
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.headers['Content-Type'], 'font/woff2')
            self.assertIn('immutable', res.headers['Cache-Control'])
        req = urllib.request.Request(self.base + '/sw.js', headers={'X-Gamemaker-User': 'brother'})
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.headers['Cache-Control'], 'no-cache')

    def test_without_push_the_board_says_so(self):
        self.assertIsNone(self.call('GET', '/api/state')[1]['push'])
        status, body = self.call('POST', '/api/push/subscribe', body={'subscription': {}})
        self.assertEqual(status, 503, body)

    def test_unknown_calls(self):
        self.assertEqual(self.call('GET', '/api/nope')[0], 404)
        self.assertEqual(self.call('GET', '/api/prompts/1')[0], 404)
        self.assertEqual(self.call('GET', '/api/agent/claim', user='agent')[0], 405)


if __name__ == '__main__':
    unittest.main()
