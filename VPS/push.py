"""Web Push for the board: a notification on each subscribed phone or computer when the Lelelilo finishes a prompt.

The browser hands the page a subscription (an address at its push service, and two keys). The board keeps it, and
when a prompt is done or failed it sends each subscription a short message, encrypted for that browser alone
(RFC 8291), signed with the board's own key (VAPID, RFC 8292). The push service passes it on even when the page is
closed, and the page's service worker (web/sw.js) shows it.

This needs the `cryptography` package (Ubuntu's python3-cryptography). Without it the board runs as before, with
push switched off.
"""
import base64
import json
import os
import queue
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:  # pragma: no cover - the board runs without push
    ec = None

AVAILABLE = ec is not None

# where browsers' push services live: a subscription anywhere else is refused (the board would post to it)
PUSH_HOSTS = ('fcm.googleapis.com', '.push.services.mozilla.com', 'web.push.apple.com', '.push.apple.com',
              '.notify.windows.com')
TTL = 24 * 3600          # a phone that is off for longer than this doesn't need to hear about it any more
MAX_SUBSCRIPTIONS = 50
MAX_ENDPOINT = 1_000

# what the notification says, in the language and the words the subscription asked for
WORDS = {
    'en': {'done': '#{id} is ready to play', 'failed': "#{id} didn't work out", 'test': 'Notifications are on',
           'test_body': 'This is how you will hear that the Lelelilo has finished.'},
    'es': {'done': '#{id} está listo para jugar', 'failed': '#{id} no salió', 'test': 'Las notificaciones están activadas',
           'test_body': 'Así te vas a enterar cuando el Lelelilo termine.'},
    'en_kids': {'done': 'Your game #{id} is ready!', 'failed': "Oops! #{id} didn't work", 'test': 'Hi! It works',
                'test_body': "Neri will tell you here when your game is ready."},
    'es_kids': {'done': '¡Tu juego #{id} está listo!', 'failed': '¡Uy! #{id} no funcionó', 'test': '¡Hola! Funciona',
                'test_body': 'Neri te va a avisar aquí cuando tu juego esté listo.'},
}


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def unb64url(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def push_host_ok(endpoint, hosts=PUSH_HOSTS):
    parts = urlsplit(endpoint)
    host = (parts.hostname or '').lower()
    return (parts.scheme == 'https' and parts.port in (None, 443) and not parts.username
            and any(host == h.lstrip('.') if not h.startswith('.') else host.endswith(h) for h in hosts))


def words(lang, kids):
    return WORDS[f'{lang}_kids' if kids else lang]


def message(prompt, lang, kids):
    """The notification for a prompt that just ended, as the service worker shows it."""
    w = words(lang, kids)
    pid = prompt['id']
    title = w[prompt['status']].format(id=pid)
    body = prompt.get('title') or ' '.join((prompt.get('text') or '').split()[:12])
    return {'title': title, 'body': body, 'tag': f'p{pid}', 'url': f'/#p{pid}', 'id': pid, 'status': prompt['status']}


class Keys:
    """The board's VAPID key: made once, kept in a file only the board's user can read."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path, 'rb') as f:
                self.private = serialization.load_pem_private_key(f.read(), password=None)
        except FileNotFoundError:
            self.private = ec.generate_private_key(ec.SECP256R1())
            pem = self.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(pem)
        self.public = self.private.public_key().public_bytes(serialization.Encoding.X962,
                                                              serialization.PublicFormat.UncompressedPoint)

    @property
    def public_b64(self):
        return b64url(self.public)

    def vapid(self, endpoint, subject, t=None):
        """The Authorization header for one push service: a JWT it can check against our public key."""
        parts = urlsplit(endpoint)
        claims = {'aud': f'{parts.scheme}://{parts.netloc}', 'exp': int(t or time.time()) + 12 * 3600, 'sub': subject}
        head = b64url(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode())
        body = b64url(json.dumps(claims, separators=(',', ':')).encode())
        der = self.private.sign(f'{head}.{body}'.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f'vapid t={head}.{body}.{b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}, k={self.public_b64}'


def encrypt(payload, p256dh, auth):
    """RFC 8291 (aes128gcm): `payload` for the browser that holds the private half of `p256dh`, and nobody else."""
    ua_public = unb64url(p256dh)
    auth_secret = unb64url(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public = as_private.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint)
    shared = as_private.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b'WebPush: info\x00' + ua_public + as_public).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b'Content-Encoding: aes128gcm\x00').derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b'Content-Encoding: nonce\x00').derive(ikm)
    record = AESGCM(cek).encrypt(nonce, payload + b'\x02', None)  # \x02: the last (and only) record
    return salt + struct.pack('!IB', 4096, len(as_public)) + as_public + record


def check_subscription(body):
    """The subscription as the browser gives it ({endpoint, keys: {p256dh, auth}}), or ValueError saying what's wrong."""
    endpoint = body.get('endpoint')
    keys = body.get('keys') if isinstance(body.get('keys'), dict) else {}
    if not isinstance(endpoint, str) or len(endpoint) > MAX_ENDPOINT or not push_host_ok(endpoint):
        raise ValueError('"endpoint" must be an https address of a browser push service')
    try:
        p256dh, auth = unb64url(keys['p256dh']), unb64url(keys['auth'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('"keys" must have p256dh and auth, in base64url')
    if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) < 16:
        raise ValueError('"keys" are not a browser push key pair')
    return endpoint, b64url(p256dh), b64url(auth)


class Pusher:
    """Sends notifications from a thread of its own, so the agent's call that finished a prompt never waits on it."""

    def __init__(self, store, key_path, subject, hosts=PUSH_HOSTS, opener=None, log=None):
        self.store = store
        self.keys = Keys(key_path)
        self.subject = subject
        self.hosts = hosts
        self.opener = opener or urllib.request.urlopen
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.jobs = queue.Queue()
        threading.Thread(target=self._work, name='push', daemon=True).start()

    def finished(self, prompt):
        """A prompt was just marked done or failed: tell everyone who asked."""
        self.jobs.put(('prompt', dict(prompt)))

    def test(self, endpoint):
        self.jobs.put(('test', endpoint))

    def wait(self):
        """For tests: until everything queued so far is sent."""
        self.jobs.join()

    def _work(self):
        while True:
            kind, what = self.jobs.get()
            try:
                if kind == 'prompt':
                    for sub in self.store.subscriptions():
                        self._send(sub, message(what, sub['lang'], sub['kids']))
                else:
                    sub = self.store.subscription(what)
                    if sub:
                        w = words(sub['lang'], sub['kids'])
                        self._send(sub, {'title': w['test'], 'body': w['test_body'], 'tag': 'test', 'url': '/'})
            except Exception as e:  # pragma: no cover - logged; the next job still goes
                self.log(f'push: {e!r}')
            finally:
                self.jobs.task_done()

    def _send(self, sub, msg):
        endpoint = sub['endpoint']
        if not push_host_ok(endpoint, self.hosts):
            return
        body = encrypt(json.dumps(msg, ensure_ascii=False).encode(), sub['p256dh'], sub['auth'])
        req = urllib.request.Request(endpoint, data=body, method='POST', headers={
            'Authorization': self.keys.vapid(endpoint, self.subject),
            'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream',
            'TTL': str(TTL), 'Urgency': 'normal', 'Topic': msg['tag'],
        })
        host = urlsplit(endpoint).hostname
        try:
            with self.opener(req, timeout=20) as res:
                status = res.status
        except urllib.error.HTTPError as e:
            status = e.code
            e.close()
        except (OSError, ValueError) as e:
            self.log(f'push to {host}: {e}')
            self.store.push_failed(endpoint)
            return
        if status in (404, 410):  # the browser dropped it: so do we
            self.log(f'push to {host}: {status}, subscription removed')
            self.store.unsubscribe(endpoint)
        elif status >= 400:
            self.log(f'push to {host}: {status}')
            self.store.push_failed(endpoint)
        else:
            self.store.push_ok(endpoint)
