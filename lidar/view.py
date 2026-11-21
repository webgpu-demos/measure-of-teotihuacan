#!/usr/bin/env python3
"""Open a splat .ply in the offline viewer: a web server on this computer only, then the browser.

    python lidar/view.py out/site.ply           # on Windows: py lidar\\view.py out\\site.ply
    python lidar/view.py                        # the viewer alone: pick or drop a file in it

Serves three things on 127.0.0.1, reachable from this computer and nowhere else: lidar/view.html, the libraries in
lidar/vendor/, and the one .ply you name (with its .json sidecar from splat.py, if there is one). Nothing else on
the disk is served and nothing goes to the internet. Ctrl+C stops it. Standard library only.
"""
import argparse
import shutil
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

HERE = Path(__file__).resolve().parent
PAGE = HERE / 'view.html'
VENDOR = HERE / 'vendor'
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json',
         '.ply': 'application/octet-stream'}


def make_handler(ply=None):
    """Request handler serving the page, vendor/ and the chosen splat (at /data/<its file name>)."""
    data = {}
    if ply is not None:
        data[ply.name] = ply
        sidecar = ply.with_suffix('.json')
        if sidecar.is_file():
            data[sidecar.name] = sidecar

    class Handler(BaseHTTPRequestHandler):
        def resolve(self):
            path = unquote(urlsplit(self.path).path)
            if path == '/view.html':
                return PAGE
            if path.startswith('/vendor/'):
                f = (VENDOR / path[len('/vendor/'):]).resolve()
                return f if f.is_file() and VENDOR in f.parents else None
            if path.startswith('/data/'):
                return data.get(path[len('/data/'):])
            return None

        def do_GET(self):
            if urlsplit(self.path).path == '/':
                target = '/view.html' + (f'?file=data/{quote(ply.name)}' if ply is not None else '')
                self.send_response(302)
                self.send_header('Location', target)
                self.end_headers()
                return
            f = self.resolve()
            if f is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', TYPES.get(f.suffix.lower(), 'application/octet-stream'))
            self.send_header('Content-Length', str(f.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            with open(f, 'rb') as src:
                try:
                    shutil.copyfileobj(src, self.wfile, 1 << 20)
                except (BrokenPipeError, ConnectionResetError):
                    pass                                    # the page was closed mid-download

        def log_message(self, *args):
            pass

    return Handler


def serve(ply=None, port=0):
    """Start the server in a background thread; returns it (its URL is server.url)."""
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(ply))
    server.url = f'http://127.0.0.1:{server.server_address[1]}/'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split('\n\n', 1)[1])
    ap.add_argument('ply', nargs='?', help='a splat .ply, e.g. from lidar/splat.py')
    ap.add_argument('--port', type=int, default=0, help='default: any free port')
    ap.add_argument('--no-browser', action='store_true', help='print the address instead of opening a browser')
    a = ap.parse_args(argv)
    ply = None
    if a.ply:
        ply = Path(a.ply).resolve()
        if not ply.is_file():
            sys.exit(f'No such file: {a.ply}')
    server = serve(ply, a.port)
    print(f'Viewer at {server.url} (this computer only). Ctrl+C to stop.', flush=True)
    if not a.no_browser:
        webbrowser.open(server.url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == '__main__':
    main()
