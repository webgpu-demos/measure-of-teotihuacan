"""Tests on made-up files only. Run: python -m pytest vault/tests"""
import csv
import struct
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import datacheck as dc  # noqa: E402

E0, N0 = 516000.0, 2177000.0          # UTM-like, so a leaked coordinate is easy to spot
WKT_UTM = ('PROJCS["WGS 84 / UTM zone 14N",GEOGCS["WGS 84",AUTHORITY["EPSG","4326"]],'
           'UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","32614"]]')


def vlr(user, rid, data):
    return struct.pack('<H16sHH32s', 0, user.encode(), rid, len(data), b'') + data


def evlr(user, rid, data):
    return struct.pack('<H16sHQ32s', 0, user.encode(), rid, len(data), b'') + data


def geokeys(*pairs):
    """GeoKeyDirectory with values stored inline."""
    vals = [1, 1, 0, len(pairs)]
    for key, value in pairs:
        vals += [key, 0, 1, value]
    return struct.pack(f'<{len(vals)}H', *vals)


def las_bytes(version=(1, 2), fmt=1, n=1000, first=None, bounds=(E0, E0 + 1000, N0, N0 + 1000, 2270, 2300),
              vlrs=(), evlrs=(), compressed=False):
    major, minor = version
    hsize = 375 if version >= (1, 4) else 235 if version == (1, 3) else 227
    vblob = b''.join(vlrs)
    h = bytearray(hsize)
    h[0:4] = b'LASF'
    h[24], h[25] = major, minor
    struct.pack_into('<HLL', h, 94, hsize, hsize + len(vblob), len(vlrs))
    legacy = n if version < (1, 4) else 0
    struct.pack_into('<BHL', h, 104, fmt | (0x80 if compressed else 0), 28, legacy)
    first = n if first is None else first
    struct.pack_into('<5L', h, 111, first if version < (1, 4) else 0, 0, 0, 0, 0)
    struct.pack_into('<3d', h, 131, 0.01, 0.01, 0.01)
    minx, maxx, miny, maxy, minz, maxz = bounds
    struct.pack_into('<6d', h, 179, maxx, minx, maxy, miny, maxz, minz)
    points = b'\0' * 280                                   # a few placeholder point bytes
    eblob = b''.join(evlrs)
    if version >= (1, 4):
        start = hsize + len(vblob) + len(points) if evlrs else 0
        struct.pack_into('<QLQ', h, 235, start, len(evlrs), n)
        struct.pack_into('<15Q', h, 255, first, *([0] * 14))
    return bytes(h) + vblob + points + eblob


@pytest.fixture
def delivery(tmp_path):
    root = tmp_path / 'delivery'
    (root / 'lidar').mkdir(parents=True)
    (root / 'cad').mkdir()
    (root / '.hidden').mkdir()
    # Two 1 x 1 km tiles, 20 million points each, LAS 1.2 with GeoTIFF keys.
    keys = vlr('LASF_Projection', 34735, geokeys((3072, 32614), (4096, 5703)))
    for k in range(2):
        b = (E0 + 1000 * k, E0 + 1000 * (k + 1), N0, N0 + 1000, 2270, 2300)
        (root / 'lidar' / f'tile_{k}.las').write_bytes(las_bytes(n=20_000_000, first=15_000_000, bounds=b, vlrs=[keys]))
    (root / 'cad' / 'plan.dwg').write_bytes(b'AC1032' + b'\0' * 994)
    (root / 'cad' / 'notes.pdf').write_bytes(b'%PDF' + b'\0' * 96)
    (root / 'lidar' / 'broken.las').write_bytes(b'not lidar at all' * 20)
    (root / 'Thumbs.db').write_bytes(b'x' * 50)
    (root / '.hidden' / 'secret.las').write_bytes(las_bytes())
    return root


def test_header_reader_versions_formats_and_crs(tmp_path):
    p = tmp_path / 'a.laz'
    p.write_bytes(las_bytes(version=(1, 4), fmt=6, n=5_000_000_000, first=3_000_000_000, compressed=True,
                            vlrs=[vlr('copc', 1, b'\0' * 160)], evlrs=[evlr('LASF_Projection', 2112, WKT_UTM.encode() + b'\0')]))
    h = dc.read_las_header(p)
    assert h['version'] == '1.4' and h['format'] == 6 and h['compressed'] and h['copc']
    assert h['points'] == 5_000_000_000 and h['first_returns'] == 3_000_000_000      # 64-bit counts
    assert h['crs'] == 'WGS 84 / UTM zone 14N (EPSG:32614)' and h['unit_m'] == 1.0
    q = tmp_path / 'b.las'
    q.write_bytes(las_bytes(vlrs=[vlr('LASF_Projection', 34735, geokeys((3072, 32614), (4096, 5703)))]))
    h = dc.read_las_header(q)
    assert h['version'] == '1.2' and not h['compressed'] and not h['copc'] and h['points'] == 1000
    assert h['crs'] == 'GeoTIFF keys: EPSG:32614, vertical EPSG:5703'
    with pytest.raises(ValueError):
        (tmp_path / 'c.las').write_bytes(b'garbage' * 100)
        dc.read_las_header(tmp_path / 'c.las')


def test_feet_are_converted_for_area(tmp_path):
    ft = 1000 / 0.3048                                       # a 1 x 1 km tile drawn in international feet
    p = tmp_path / 'feet.las'
    p.write_bytes(las_bytes(n=1_000_000, bounds=(0, ft, 0, ft, 0, 10),
                            vlrs=[vlr('LASF_Projection', 34735, geokeys((3072, 2229), (3076, 9002)))]))
    s = dc.summarise_lidar([dict(dc.read_las_header(p), bytes=p.stat().st_size)])
    assert s['area_m2'] == pytest.approx(1e6, rel=1e-9)
    assert s['density'] == pytest.approx(1.0)


def test_degrees_are_not_given_an_area(tmp_path):
    p = tmp_path / 'geo.las'
    p.write_bytes(las_bytes(bounds=(-98.86, -98.84, 19.68, 19.70, 2270, 2300)))
    s = dc.summarise_lidar([dict(dc.read_las_header(p), bytes=1)])
    assert s['in_degrees'] == 1 and s['area_m2'] == 0 and s['density'] is None


def test_specs_counts_density_and_storage(delivery):
    c = dc.collect([delivery])
    assert c['count']['lidar point clouds'] == 3 and c['unreadable'] == 1           # broken.las counted, not read
    assert c['count']['CAD drawings'] == 1 and c['count']['documents'] == 1
    assert sum(c['count'].values()) == 5                                              # Thumbs.db, .hidden skipped
    s = dc.summarise_lidar(c['lidar'])
    assert s['points'] == 40_000_000
    assert s['area_m2'] == pytest.approx(2e6) and s['extent_m2'] == pytest.approx(2e6)
    assert s['density'] == pytest.approx(20.0) and s['first_density'] == pytest.approx(15.0)
    assert s['tile_density'] == pytest.approx((20.0, 20.0, 20.0))
    assert s['dem_cell_m'] == pytest.approx((3 / 20) ** 0.5)
    est = dc.storage_estimate(sum(c['bytes'].values()), s)
    assert est['cell'] == 0.5
    assert est['derived'] == pytest.approx(2e6 / 0.25 * 4 * 6)
    assert est['ssd_gb'] == 512


def test_specs_report_names_no_files_and_no_coordinates(delivery, tmp_path):
    out = tmp_path / 'specs.txt'
    r = subprocess.run([sys.executable, str(HERE / 'datacheck.py'), 'specs', str(delivery), '-o', str(out)],
                       capture_output=True, text=True, check=True)
    text = out.read_text(encoding='ascii')                                             # ASCII only
    assert text in r.stdout
    for name in ('tile_0', 'tile_1', 'broken', 'plan', 'notes', 'delivery', 'lidar/'):
        assert name not in text
    for coord in (E0, N0, E0 + 1000, N0 + 1000):
        assert str(int(coord)) not in text and f'{coord:,.0f}' not in text
    assert '20.0 points per m2' in text and '2.00 km2' in text
    assert 'EPSG:32614' in text and 'Ask the vendor' in text and 'suggested SSD: 512 GB' in text


def run(*args):
    return subprocess.run([sys.executable, str(HERE / 'datacheck.py'), *map(str, args)], capture_output=True, text=True)


def test_manifest_and_verify_round_trip(delivery, tmp_path):
    manifest = tmp_path / 'manifest.csv'
    r = run('manifest', delivery, '-o', manifest)
    assert r.returncode == 0 and 'Fingerprint of the manifest itself' in r.stdout
    rows = list(csv.DictReader(open(manifest, newline='')))
    assert {row['path'] for row in rows} == {'lidar/tile_0.las', 'lidar/tile_1.las', 'lidar/broken.las',
                                             'cad/plan.dwg', 'cad/notes.pdf'}
    assert all(len(row['sha256']) == 64 for row in rows)
    r = run('verify', delivery, manifest)
    assert r.returncode == 0 and 'All good' in r.stdout and '5 of 5' in r.stdout

    # Same size, one byte different: only the fingerprint catches it.
    p = delivery / 'cad' / 'plan.dwg'
    b = bytearray(p.read_bytes())
    b[100] ^= 1
    p.write_bytes(bytes(b))
    (delivery / 'cad' / 'notes.pdf').unlink()
    (delivery / 'lidar' / 'new_tile.las').write_bytes(las_bytes())
    r = run('verify', delivery, manifest)
    assert r.returncode == 1
    assert 'MISSING: 1' in r.stdout and 'cad/notes.pdf' in r.stdout
    assert 'CHANGED: 1' in r.stdout and 'cad/plan.dwg (contents differ)' in r.stdout
    assert 'EXTRA (not in the manifest): 1' in r.stdout and 'lidar/new_tile.las' in r.stdout
    quick = run('verify', delivery, manifest, '--quick')
    assert quick.returncode == 1 and 'CHANGED' not in quick.stdout                   # sizes alone miss it


def test_manifest_inside_the_folder_is_not_listed(delivery):
    manifest = delivery / 'manifest.csv'
    assert run('manifest', delivery, '-o', manifest).returncode == 0
    assert 'manifest.csv' not in manifest.read_text()
    assert run('verify', delivery, manifest).returncode == 0


def test_agrees_with_laspy(tmp_path):
    """An independent writer: laspy's LAS 1.4 file, with a coordinate system, read by our header parser."""
    laspy = pytest.importorskip('laspy')
    np = pytest.importorskip('numpy')
    pyproj = pytest.importorskip('pyproj')
    hdr = laspy.LasHeader(point_format=6, version='1.4')
    hdr.offsets = [E0, N0, 2270.0]
    hdr.scales = [0.01, 0.01, 0.01]
    hdr.add_crs(pyproj.CRS.from_epsg(32614))
    las = laspy.LasData(hdr)
    rng = np.random.default_rng(1)
    n = 12_345
    las.x = E0 + rng.uniform(0, 500, n)
    las.y = N0 + rng.uniform(0, 250, n)
    las.z = 2270 + rng.uniform(0, 30, n)
    las.return_number = np.where(rng.uniform(size=n) < 0.7, 1, 2)
    las.number_of_returns = np.full(n, 2)
    path = tmp_path / 'laspy.las'
    las.write(path)
    ref = laspy.read(path).header
    h = dc.read_las_header(path)
    assert h['version'] == '1.4' and h['format'] == 6 and h['points'] == ref.point_count == n
    assert h['first_returns'] == int(ref.number_of_points_by_return[0])
    minx, maxx, miny, maxy, minz, maxz = h['bounds']
    assert (minx, miny, minz) == pytest.approx(tuple(ref.mins)) and (maxx, maxy, maxz) == pytest.approx(tuple(ref.maxs))
    assert 'EPSG:32614' in h['crs'] and h['unit_m'] == 1.0
