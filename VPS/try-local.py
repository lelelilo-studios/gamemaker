#!/usr/bin/env python3
"""Try the board on this machine: no server, no Caddy, no passwords. FOR TRYING ONLY: there is no login.

  python3 try-local.py [--port 8091] [--var DIR] [--game-url https://name.github.io/game/]

It starts server.py as it is, on a throwaway data folder, and stands in front of it the way Caddy does on a real
install: it passes each request on with the name in X-Gamemaker-User. Here there is no IP list and no login. The
port says who you are:

  http://127.0.0.1:8091   the page, as `brother`
  http://127.0.0.1:8092   the API, as `agent` (the calls the watcher makes)

Everything listens on 127.0.0.1 only. Ctrl-C stops the board too. The data folder (the SQLite file, the push key)
is deleted on exit, unless --var named one: then it is kept, and used again the next time.

To play the agent by hand, call the second port with curl (see the README). The board offers "Build on this
version" once a watcher of version 3 or later has called, which it reads from the User-Agent: add
`-A gamemaker-watch/4`. The real watcher works against that port too (GAMEMAKER_URL=http://127.0.0.1:8092).
"""
import argparse
import ctypes
import http.client
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = '127.0.0.1'  # never anything else: this front lets anyone who reaches it be anyone
HOP = {'connection', 'keep-alive', 'proxy-connection', 'transfer-encoding', 'upgrade', 'te', 'trailer'}


def front(port, user, board_port):
    """A listener that passes every request to the board as `user`."""
    names = {f'{LOCAL}:{port}', f'localhost:{port}'}

    class Front(BaseHTTPRequestHandler):
        def log_message(self, *args):  # the board keeps the log
            pass

        def pass_on(self):
            if self.headers.get('Host') not in names:  # a page elsewhere that points its own name at this machine
                self.send_error(403, 'open it as http://%s:%d' % (LOCAL, port))
                return
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length else None
            # the name is always set here, never taken from the client (as in deploy/Caddyfile.gamemaker)
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in HOP and k.lower() != 'x-gamemaker-user'}
            headers['X-Gamemaker-User'] = user
            board = http.client.HTTPConnection(LOCAL, board_port, timeout=30)
            try:
                board.request(self.command, self.path, body=body, headers=headers)
                res = board.getresponse()
                data = res.read()
                self.send_response(res.status)
                for k, v in res.getheaders():
                    if k.lower() not in HOP and k.lower() not in ('server', 'date'):
                        self.send_header(k, v)
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)
            except OSError as e:
                self.send_error(502, f'the board did not answer: {e}')
            finally:
                board.close()

        do_GET = do_HEAD = do_POST = pass_on

    httpd = ThreadingHTTPServer((LOCAL, port), Front)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def stop_with_me():
    """Linux: the board is told to stop if this script dies without cleaning up (a `kill -9`)."""
    try:
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=8091, help='the page, as brother (the agent gets the next port)')
    ap.add_argument('--var', help='keep the data in this folder (default: a temporary one, deleted on exit)')
    ap.add_argument('--game-url', help="a game's GitHub Pages address, shown as the starting point")
    args = ap.parse_args()

    var = Path(args.var).resolve() if args.var else Path(tempfile.mkdtemp(prefix='gamemaker-try-'))
    var.mkdir(parents=True, exist_ok=True, mode=0o700)
    with socket.socket() as s:  # a free port for the board itself
        s.bind((LOCAL, 0))
        board_port = s.getsockname()[1]
    here = f'http://{LOCAL}:{args.port}'
    cmd = [sys.executable, str(HERE / 'server.py'), '--host', LOCAL, '--port', str(board_port),
           '--db', str(var / 'gamemaker.sqlite3'), '--vapid-key', str(var / 'vapid.pem'),
           '--origin', here, '--origin', f'http://localhost:{args.port}', '--push-subject', here]
    if args.game_url:
        cmd += ['--game-url', args.game_url]

    board, fronts = None, []
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # a plain `kill` cleans up like Ctrl-C
    try:
        board = subprocess.Popen(cmd, preexec_fn=stop_with_me)
        for _ in range(100):  # wait until the board answers
            if board.poll() is not None:
                sys.exit('the board did not start')
            try:
                socket.create_connection((LOCAL, board_port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        for port, user in ((args.port, 'brother'), (args.port + 1, 'agent')):
            try:
                fronts.append(front(port, user, board_port))
            except OSError as e:
                sys.exit(f'port {port} is not free ({e}): pick another with --port')
        print(f'\nFOR TRYING ONLY: no login and no IP list. It listens on {LOCAL} and nowhere else.\n'
              f'  the page, as brother:  {here}\n'
              f'  the API, as agent:     http://{LOCAL}:{args.port + 1}\n'
              f'  the data:              {var / "gamemaker.sqlite3"}' + ('' if args.var else '  (deleted on exit)') +
              '\nCtrl-C stops it.', flush=True)
        board.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for httpd in fronts:
            httpd.shutdown()
            httpd.server_close()
        if board and board.poll() is None:
            board.terminate()
            try:
                board.wait(timeout=5)
            except subprocess.TimeoutExpired:
                board.kill()
                board.wait()
        if not args.var:
            shutil.rmtree(var, ignore_errors=True)
        print('stopped.', flush=True)


if __name__ == '__main__':
    main()
