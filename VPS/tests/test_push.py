"""Notifications: a browser signs up, a prompt ends, and the browser gets a message only it can read.

The push service is a stand-in that keeps what it's sent; the browser's side (its keys, and reading the message) is
done here with the same cryptography, as RFC 8291 describes it, so a mistake in either direction shows.
"""
import base64
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import push  # noqa: E402
import server  # noqa: E402

if push.AVAILABLE:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ORIGIN = 'https://board.example.com'
CHROME = 'https://fcm.googleapis.com/fcm/send/abc:123'
FIREFOX = 'https://updates.push.services.mozilla.com/wpush/v2/xyz'


def unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


class Browser:
    """What a browser keeps for its subscription, and how it reads a message."""

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.public = self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                         serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)

    def subscription(self):
        return {'endpoint': self.endpoint, 'keys': {'p256dh': push.b64url(self.public), 'auth': push.b64url(self.auth)}}

    def read(self, body):
        salt, rs, idlen = body[:16], int.from_bytes(body[16:20], 'big'), body[20]
        sender = body[21:21 + idlen]
        assert rs == 4096 and idlen == 65
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender))
        ikm = HKDF(hashes.SHA256(), 32, self.auth, b'WebPush: info\x00' + self.public + sender).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt, b'Content-Encoding: aes128gcm\x00').derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt, b'Content-Encoding: nonce\x00').derive(ikm)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None).rstrip(b'\x00')
        assert plain.endswith(b'\x02')
        return json.loads(plain[:-1])


class PushService:
    """Stands in for the browsers' push services: keeps each request, and answers with the status it's told to."""

    def __init__(self):
        self.sent = []
        self.answer = {}

    def __call__(self, req, timeout=None):
        self.sent.append(req)
        status = self.answer.get(req.full_url, 201)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, 'no', {}, io.BytesIO(b''))
        res = io.BytesIO(b'')
        res.status = status
        return res


@unittest.skipUnless(push.AVAILABLE, 'python3-cryptography is not installed')
class PushTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = server.Store(Path(self.tmp.name) / 'board.sqlite3')
        self.service = PushService()
        self.key_path = Path(self.tmp.name) / 'vapid.pem'
        self.store.pusher = push.Pusher(self.store, self.key_path, 'https://board.example.com',
                                        opener=self.service, log=lambda msg: None)
        self.httpd = server.make_server('127.0.0.1', 0, self.store, origins=[ORIGIN])
        self.httpd.RequestHandlerClass.log_message = lambda *args: None
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, method, path, user='brother', body=None):
        req = urllib.request.Request(self.base + path, method=method, headers={
            'X-Gamemaker-User': user, **({'Content-Type': 'application/json'} if body is not None else {})},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read() or b'null')
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b'null')

    def sign_up(self, browser, lang='en', kids=False, user='brother'):
        status, body = self.call('POST', '/api/push/subscribe', user=user,
                                 body={'subscription': browser.subscription(), 'lang': lang, 'kids': kids})
        self.assertEqual(status, 200, body)

    def end(self, status='done', title='Snow falls on the meadow', text='make it snow please'):
        p = self.store.create('brother', text)
        self.store.claim('agent')
        self.store.update('agent', p['id'], status=status, title=title,
                          result_url=f'https://x.github.io/g/v/{p["id"]}/' if status == 'done' else None)
        self.store.pusher.wait()
        return p['id']

    def check_vapid(self, req):
        auth = req.get_header('Authorization')
        self.assertTrue(auth.startswith('vapid t='), auth)
        token, k = auth[len('vapid t='):].split(', k=')
        self.assertEqual(k, self.call('GET', '/api/state')[1]['push']['key'])
        head, claims, sig = token.split('.')
        self.assertEqual(json.loads(unb64(head)), {'typ': 'JWT', 'alg': 'ES256'})
        claims = json.loads(unb64(claims))
        self.assertEqual(claims['aud'], 'https://' + req.host)
        self.assertEqual(claims['sub'], 'https://board.example.com')
        raw = unb64(sig)
        public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(k))
        der = encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
        try:
            public.verify(der, f'{head}.{token.split(".")[1]}'.encode(), ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            self.fail('the VAPID signature does not check out')

    def test_a_prompt_that_ends_reaches_every_browser_signed_up_in_its_words(self):
        chrome, firefox = Browser(CHROME), Browser(FIREFOX)
        self.sign_up(chrome)
        self.sign_up(firefox, lang='es', kids=True, user='agent')
        pid = self.end()
        self.assertEqual(len(self.service.sent), 2)
        got = {}
        for req in self.service.sent:
            self.check_vapid(req)
            self.assertEqual(req.get_header('Content-encoding'), 'aes128gcm')
            self.assertEqual(req.get_header('Ttl'), str(push.TTL))
            who = chrome if req.full_url == CHROME else firefox
            got[req.full_url] = who.read(req.data)
        self.assertEqual(got[CHROME], {'title': f'#{pid} is ready to play', 'body': 'Snow falls on the meadow',
                                       'tag': f'p{pid}', 'url': f'/#p{pid}', 'id': pid, 'status': 'done'})
        self.assertEqual(got[FIREFOX]['title'], f'¡Tu juego #{pid} está listo!')
        # one browser can't read the other's
        with self.assertRaises(Exception):
            firefox.read(self.service.sent[0].data if self.service.sent[0].full_url == CHROME else self.service.sent[1].data)

    def test_a_failed_one_is_told_too_and_a_note_is_not(self):
        chrome = Browser(CHROME)
        self.sign_up(chrome, lang='es')
        p = self.store.create('brother', 'make the scribble explode into confetti')
        self.store.claim('agent')
        self.store.update('agent', p['id'], message='reading the code')
        self.store.pusher.wait()
        self.assertEqual(self.service.sent, [])
        self.store.update('agent', p['id'], status='failed')
        self.store.pusher.wait()
        msg = chrome.read(self.service.sent[0].data)
        self.assertEqual((msg['title'], msg['body']), (f'#{p["id"]} no salió', 'make the scribble explode into confetti'))

    def test_a_browser_that_dropped_its_subscription_is_forgotten(self):
        chrome, firefox = Browser(CHROME), Browser(FIREFOX)
        self.sign_up(chrome)
        self.sign_up(firefox)
        self.service.answer[FIREFOX] = 410
        self.end()
        self.assertEqual([s['endpoint'] for s in self.store.subscriptions()], [CHROME])
        self.service.answer[CHROME] = 500
        self.end()
        self.assertEqual(self.store.subscription(CHROME)['failures'], 1)

    def test_signing_up_again_updates_the_words_and_signing_off_stops_it(self):
        chrome = Browser(CHROME)
        self.sign_up(chrome)
        self.sign_up(chrome, lang='es', kids=True)
        self.assertEqual(len(self.store.subscriptions()), 1)
        self.assertEqual((self.store.subscription(CHROME)['lang'], self.store.subscription(CHROME)['kids']), ('es', 1))
        self.assertEqual(self.call('POST', '/api/push/unsubscribe', body={'endpoint': CHROME})[0], 200)
        self.end()
        self.assertEqual(self.service.sent, [])

    def test_a_test_message_goes_to_that_browser_only(self):
        chrome, firefox = Browser(CHROME), Browser(FIREFOX)
        self.sign_up(chrome, kids=True)
        self.sign_up(firefox)
        self.assertEqual(self.call('POST', '/api/push/test', body={'endpoint': CHROME})[0], 202)
        self.store.pusher.wait()
        self.assertEqual([r.full_url for r in self.service.sent], [CHROME])
        self.assertEqual(chrome.read(self.service.sent[0].data)['title'], 'Hi! It works')
        self.assertEqual(self.call('POST', '/api/push/test', body={'endpoint': 'https://fcm.googleapis.com/nope'})[0], 404)

    def test_only_real_push_services_and_real_keys(self):
        good = Browser(CHROME).subscription()
        for endpoint in ('http://fcm.googleapis.com/x', 'https://127.0.0.1/x', 'https://fcm.googleapis.com.evil.example/x',
                         'https://evil.example/fcm.googleapis.com', 'https://fcm.googleapis.com:8443/x',
                         'https://user@fcm.googleapis.com/x', 'https://x' * 600):
            status, _ = self.call('POST', '/api/push/subscribe', body={'subscription': {**good, 'endpoint': endpoint}})
            self.assertEqual(status, 400, endpoint)
        for keys in ({}, {'p256dh': 'abc', 'auth': 'def'}, {'p256dh': good['keys']['p256dh']}, 'nope'):
            status, _ = self.call('POST', '/api/push/subscribe', body={'subscription': {**good, 'keys': keys}})
            self.assertEqual(status, 400, keys)
        status, _ = self.call('POST', '/api/push/subscribe', body={'subscription': good, 'lang': 'fr'})
        self.assertEqual(status, 400)
        for endpoint in (CHROME, FIREFOX, 'https://web.push.apple.com/QW', 'https://wns2-par02p.notify.windows.com/w/?t'):
            self.assertTrue(push.push_host_ok(endpoint), endpoint)

    def test_the_key_is_made_once_and_kept_private(self):
        self.assertEqual(stat.S_IMODE(self.key_path.stat().st_mode), 0o600)
        again = push.Keys(self.key_path)
        self.assertEqual(again.public_b64, self.store.pusher.keys.public_b64)


if __name__ == '__main__':
    unittest.main()
