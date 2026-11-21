"""The offline viewer: no outside URLs, vendored files intact, and a server that shows only what it should.

Run: .venv/bin/python -m pytest lidar/tests/test_view.py -q
"""
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import view  # noqa: E402

PAGE = (HERE / 'view.html').read_text(encoding='utf-8')


def test_page_references_nothing_outside_this_computer():
    assert not re.search(r'(https?:)?//[a-z0-9.-]+\.[a-z]{2,}/', PAGE, re.I), 'view.html must not load from the web'
    imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', PAGE, re.S).group(1))['imports']
    for name, target in imports.items():
        assert target.startswith('./vendor/'), name
        assert (HERE / target).is_file(), target
    for spec in set(re.findall(r"from '([^']+)'", PAGE)):
        assert spec in imports, f'{spec} is not in the import map'


def test_vendored_files_match_their_checksums():
    lines = (HERE / 'vendor' / 'SHA256SUMS').read_text().splitlines()
    assert len(lines) == 6
    for line in lines:
        digest, name = line.split(maxsplit=1)
        assert hashlib.sha256((HERE / 'vendor' / name).read_bytes()).hexdigest() == digest, name


def test_vendored_libraries_import_only_each_other():
    """three.module imports three.core; OrbitControls and Spark import 'three', which the import map resolves."""
    vendor = HERE / 'vendor'
    for f in vendor.rglob('*.js'):
        code = re.sub(r'/\*.*?\*/', '', f.read_text(encoding='utf-8'), flags=re.S)     # doc comments show examples
        for spec in re.findall(r'''(?:from|import)\s*["']([^"']+)["']''', code):
            assert spec == 'three' or (f.parent / spec).resolve().is_file(), f'{f.name} imports {spec}'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@pytest.fixture
def server(tmp_path):
    ply = tmp_path / 'my site.ply'
    ply.write_bytes(b'ply\nformat binary_little_endian 1.0\nend_header\n' + bytes(range(256)) * 10)
    ply.with_suffix('.json').write_text('{"splats": 0}')
    (tmp_path / 'secret.laz').write_bytes(b'not to be served')
    s = view.serve(ply)
    yield s, ply
    s.shutdown()
    s.server_close()


def get(url):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b''


def test_server_listens_on_this_computer_only(server):
    s, _ = server
    assert s.server_address[0] == '127.0.0.1'


def test_server_opens_the_viewer_on_the_chosen_file(server):
    s, ply = server
    status, headers, _ = get(s.url)
    assert status == 302 and headers['Location'] == '/view.html?file=data/my%20site.ply'
    status, headers, body = get(s.url + 'view.html')
    assert status == 200 and body.decode() == PAGE and headers['Content-Type'].startswith('text/html')
    status, headers, body = get(s.url + 'vendor/spark/spark.module.min.js')
    assert status == 200 and headers['Content-Type'].startswith('text/javascript')
    assert get(s.url + 'data/my%20site.ply')[2] == ply.read_bytes()
    assert get(s.url + 'data/my%20site.json')[2] == b'{"splats": 0}'


@pytest.mark.parametrize('path', ['view.py', 'splat.py', 'vendor/../view.py', 'vendor/%2e%2e/splat.py',
                                  'data/secret.laz', 'data/../view.py', 'data/%2e%2e%2fsecret.laz', 'vendor/',
                                  'out/splat-demo/synthetic.ply', 'tests/test_view.py'])
def test_server_serves_nothing_else(server, path):
    s, _ = server
    assert get(s.url + path)[0] == 404
