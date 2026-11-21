#!/usr/bin/env python3
"""Check a data delivery before it goes on the lab laptop: what it holds, and a fingerprint of every file.

Runs on the team's own machine with plain Python 3.9 or newer. Nothing to install.

    python datacheck.py specs    D:/teo-data -o specs.txt        # what is there: sizes, formats, lidar points, area
    python datacheck.py manifest D:/teo-data -o manifest.csv     # SHA-256 fingerprint of every file (reads every byte)
    python datacheck.py verify   E:/teo-data manifest.csv        # on arrival: every file there and unchanged?

specs.txt holds totals only: no file names and no coordinates. It is for choosing the laptop and sizing its disk.
manifest.csv names every file and stays with the data.

Lidar facts come from the public header of each LAS/LAZ file (the part before the points), so `specs` is fast
even on hundreds of gigabytes. Headers do not say whether ground points are classified: ask the vendor.
"""
import argparse
import csv
import hashlib
import math
import os
import re
import struct
import sys
import time
from collections import Counter
from pathlib import Path

SKIP_NAMES = {'.ds_store', 'thumbs.db', 'desktop.ini'}
KINDS = [
    ('lidar point clouds', ('.las', '.laz')),
    ('rasters (DEMs, images)', ('.tif', '.tiff', '.img', '.asc', '.vrt', '.jp2', '.ecw', '.sid', '.bil', '.dem')),
    ('CAD drawings', ('.dwg', '.dxf', '.dgn')),
    ('photos', ('.jpg', '.jpeg', '.png', '.heic', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.raw', '.orf')),
    ('3D scans and meshes', ('.e57', '.ply', '.obj', '.fbx', '.gltf', '.glb', '.stl', '.rcp', '.rcs', '.3ds', '.dae',
                             '.pts', '.ptx', '.xyz')),
    ('GIS vectors', ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.gpkg', '.geojson', '.kml', '.kmz')),
    ('archives', ('.zip', '.7z', '.rar', '.tar', '.gz', '.tgz')),
    ('documents', ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt', '.ppt', '.pptx', '.md')),
]
GEOKEY_FEET = {9002: 0.3048, 9003: 1200 / 3937}       # international foot, US survey foot
SSD_SIZES_GB = (512, 1000, 2000, 4000, 8000)
DERIVED_RASTERS = ('DTM', 'DSM', 'local relief', 'sky-view', 'hillshade', 'slope')
GROUND_SHARE = 3                                       # rule of thumb: about 1 point in 3 is ground


def kind_of(path):
    ext = path.suffix.lower()
    for kind, exts in KINDS:
        if ext in exts:
            return kind
    return 'other'


def files_under(root):
    """Every file under root, in a stable order, skipping dot-folders and operating-system clutter."""
    root = Path(root)
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for name in sorted(filenames):
            if name.lower() not in SKIP_NAMES and not name.startswith('.'):
                yield Path(dirpath) / name


# LAS / LAZ public header ---------------------------------------------------------------------------------

def read_las_header(path):
    """Version, point format, point counts, bounds and coordinate system from a LAS or LAZ file's header.
    Raises ValueError if the file is not LAS/LAZ."""
    with open(path, 'rb') as f:
        h = f.read(375)
        if len(h) < 227 or h[:4] != b'LASF':
            raise ValueError('not a LAS/LAZ file')
        major, minor = h[24], h[25]
        header_size, _, n_vlr = struct.unpack_from('<HLL', h, 94)
        fmt_raw, _, legacy_n = struct.unpack_from('<BHL', h, 104)
        by_return = list(struct.unpack_from('<5L', h, 111))
        maxx, minx, maxy, miny, maxz, minz = struct.unpack_from('<6d', h, 179)
        n, evlr_start, n_evlr = legacy_n, 0, 0
        if (major, minor) >= (1, 4) and len(h) >= 375:
            evlr_start, n_evlr, n = struct.unpack_from('<QLQ', h, 235)
            by_return = list(struct.unpack_from('<15Q', h, 255))
        vlrs = []
        f.seek(header_size)
        for _ in range(n_vlr):
            vh = f.read(54)
            if len(vh) < 54:
                break
            user = vh[2:18].split(b'\0')[0].decode('ascii', 'replace')
            rid, length = struct.unpack_from('<HH', vh, 18)
            vlrs.append((user, rid, f.read(length)))
        if evlr_start and n_evlr:
            f.seek(evlr_start)
            for _ in range(n_evlr):
                vh = f.read(60)
                if len(vh) < 60:
                    break
                user = vh[2:18].split(b'\0')[0].decode('ascii', 'replace')
                rid, length = struct.unpack_from('<HQ', vh, 18)
                if user == 'LASF_Projection' and length < 10_000_000:
                    vlrs.append((user, rid, f.read(length)))
                else:
                    f.seek(length, 1)
    crs, unit = coordinate_system(vlrs)
    return {
        'version': f'{major}.{minor}',
        'format': fmt_raw & 0x3F,
        'compressed': bool(fmt_raw & 0x80) or Path(path).suffix.lower() == '.laz',
        'copc': any(user == 'copc' for user, _, _ in vlrs),
        'points': int(n),
        'first_returns': int(by_return[0]),
        'bounds': (minx, maxx, miny, maxy, minz, maxz),
        'crs': crs,
        'unit_m': unit,             # metres per horizontal coordinate unit; None = degrees; 1.0 also when unknown
    }


def coordinate_system(vlrs):
    """(readable name, metres per horizontal unit or None for degrees) from the projection records."""
    for user, rid, data in vlrs:
        if user == 'LASF_Projection' and rid == 2112:
            wkt = data.split(b'\0')[0].decode('utf-8', 'replace')
            m = re.match(r'\s*(\w+)\["([^"]+)"', wkt)
            codes = re.findall(r'(?:AUTHORITY|ID)\["EPSG",\s*"?(\d+)"?\]', wkt)
            name = (m.group(2) if m else 'WKT') + (f' (EPSG:{codes[-1]})' if codes else '')
            kind = m.group(1).upper() if m else ''
            if kind in ('GEOGCS', 'GEOGCRS', 'GEODCRS'):
                return name, None
            if re.search(r'LENGTHUNIT\["US survey foot"|UNIT\["US survey foot"', wkt, re.I):
                return name, GEOKEY_FEET[9003]
            if re.search(r'(LENGTH)?UNIT\["(foot|feet|international foot)', wkt, re.I):
                return name, GEOKEY_FEET[9002]
            return name, 1.0
    for user, rid, data in vlrs:
        if user == 'LASF_Projection' and rid == 34735 and len(data) >= 8:
            vals = struct.unpack_from(f'<{len(data) // 2}H', data)
            keys = {}
            for i in range(vals[3]):
                k = 4 + 4 * i
                if k + 3 < len(vals) and vals[k + 1] == 0:          # value stored in the key itself
                    keys[vals[k]] = vals[k + 3]
            parts = []
            if keys.get(3072):
                parts.append(f'EPSG:{keys[3072]}' if keys[3072] != 32767 else 'projected, user-defined')
            elif keys.get(2048):
                parts.append(f'geographic EPSG:{keys[2048]}')
            if keys.get(4096):
                parts.append(f'vertical EPSG:{keys[4096]}')
            if keys.get(3076) in GEOKEY_FEET:
                parts.append('feet' if keys[3076] == 9002 else 'US survey feet')
            name = 'GeoTIFF keys: ' + (', '.join(parts) if parts else 'no EPSG code')
            if not keys.get(3072) and keys.get(2048):
                return name, None
            return name, GEOKEY_FEET.get(keys.get(3076), 1.0)
    return 'none recorded', 1.0


def looks_like_degrees(bounds):
    minx, maxx, miny, maxy = bounds[:4]
    return all(abs(v) <= 180 for v in (minx, maxx)) and all(abs(v) <= 90 for v in (miny, maxy)) \
        and (maxx - minx) < 5 and (maxy - miny) < 5


# specs ----------------------------------------------------------------------------------------------------

def gb(n):
    return n / 1e9


def collect(roots):
    """Totals by kind plus every lidar header, read once. Keeps no file names."""
    by_kind = Counter()
    count = Counter()
    lidar, unreadable = [], 0
    for root in roots:
        for path in files_under(root):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            kind = kind_of(path)
            by_kind[kind] += size
            count[kind] += 1
            if kind == 'lidar point clouds':
                try:
                    lidar.append(dict(read_las_header(path), bytes=size))
                except (ValueError, OSError, struct.error):
                    unreadable += 1
    return {'bytes': by_kind, 'count': count, 'lidar': lidar, 'unreadable': unreadable}


def summarise_lidar(tiles):
    s = {'files': len(tiles), 'points': sum(t['points'] for t in tiles),
         'first_returns': sum(t['first_returns'] for t in tiles),
         'versions': Counter(t['version'] for t in tiles), 'formats': Counter(t['format'] for t in tiles),
         'compressed': sum(t['compressed'] for t in tiles), 'copc': sum(t['copc'] for t in tiles),
         'crs': Counter(t['crs'] for t in tiles),
         'las_bytes': sum(t['bytes'] for t in tiles if not t['compressed']),
         'laz_bytes': sum(t['bytes'] for t in tiles if t['compressed']),
         'copc_bytes': sum(t['bytes'] for t in tiles if t['copc'])}
    measured = [t for t in tiles if t['unit_m'] is not None and not looks_like_degrees(t['bounds'])]
    areas, densities = [], []
    for t in measured:
        minx, maxx, miny, maxy = t['bounds'][:4]
        a = max(0.0, maxx - minx) * max(0.0, maxy - miny) * t['unit_m'] ** 2
        areas.append(a)
        if a > 0:
            densities.append(t['points'] / a)
    s['in_degrees'] = len(tiles) - len(measured)
    s['area_m2'] = sum(areas)
    s['density'] = sum(t['points'] for t in measured) / s['area_m2'] if s['area_m2'] else None
    s['first_density'] = sum(t['first_returns'] for t in measured) / s['area_m2'] if s['area_m2'] else None
    densities.sort()
    s['tile_density'] = (densities[0], densities[len(densities) // 2], densities[-1]) if densities else None
    s['extent_m2'] = None
    if measured and len(s['crs']) == 1 and len({t['unit_m'] for t in measured}) == 1:
        u = measured[0]['unit_m']
        s['extent_m2'] = (max(t['bounds'][1] for t in measured) - min(t['bounds'][0] for t in measured)) \
            * (max(t['bounds'][3] for t in measured) - min(t['bounds'][2] for t in measured)) * u * u
    s['dem_cell_m'] = math.sqrt(GROUND_SHARE / s['density']) if s['density'] else None
    return s


def storage_estimate(total_bytes, lid):
    """Data + a compressed (COPC) working copy of the lidar + derived rasters, plus half again for headroom."""
    copc_copy = lid['las_bytes'] / 7 + (lid['laz_bytes'] - lid['copc_bytes'])
    derived = 0.0
    cell = None
    if lid['area_m2'] and lid['dem_cell_m']:
        cell = next((c for c in (0.25, 0.5, 1.0, 2.0) if c >= lid['dem_cell_m']), 2.0)
        derived = lid['area_m2'] / cell ** 2 * 4 * len(DERIVED_RASTERS)
    need = (total_bytes + copc_copy + derived) * 1.5
    ssd = next((s for s in SSD_SIZES_GB if s * 1e9 >= need), None)
    return {'copc_copy': copc_copy, 'derived': derived, 'cell': cell, 'need': need, 'ssd_gb': ssd}


def render(c):
    total = sum(c['bytes'].values())
    lines = ['DATA SPECS (totals only: no file names, no coordinates)', '',
             'What is there', f"  {'kind':<28}{'files':>8}{'GB':>12}"]
    for kind in [k for k, _ in KINDS] + ['other']:
        if c['count'][kind]:
            lines.append(f"  {kind:<28}{c['count'][kind]:>8,}{gb(c['bytes'][kind]):>12,.2f}")
    lines.append(f"  {'total':<28}{sum(c['count'].values()):>8,}{gb(total):>12,.2f}")
    lines.append('')
    lid = summarise_lidar(c['lidar'])
    lines.append('Lidar (from file headers)')
    if not c['lidar']:
        lines.append('  No readable LAS/LAZ files.')
    else:
        lines.append(f"  files read: {lid['files']:,} ({lid['compressed']:,} compressed LAZ, {lid['copc']:,} already COPC)")
        lines.append('  LAS versions: ' + ', '.join(f'{v} ({n:,})' for v, n in sorted(lid['versions'].items())))
        lines.append('  point formats: ' + ', '.join(f'{v} ({n:,})' for v, n in sorted(lid['formats'].items())))
        lines.append('  coordinate system: ' + '; '.join(f'{v} ({n:,} files)' for v, n in lid['crs'].most_common()))
        lines.append(f"  points: {lid['points']:,} ({lid['points'] / 1e9:.2f} billion)")
        if lid['area_m2']:
            lines.append(f"  area: {lid['area_m2'] / 1e6:,.2f} km2, summing tile footprints (overlapping tiles count twice)")
            if lid['extent_m2']:
                lines.append(f"  overall extent: {lid['extent_m2'] / 1e6:,.2f} km2 (the rectangle around everything)")
            lines.append(f"  density: {lid['density']:,.1f} points per m2 overall")
            if lid['tile_density']:
                lo, mid, hi = lid['tile_density']
                lines.append(f'  density per tile: lowest {lo:,.1f}, median {mid:,.1f}, highest {hi:,.1f} points per m2')
            if lid['first_density']:
                lines.append(f"  first returns (about one per laser pulse): {lid['first_density']:,.1f} per m2")
            lines.append(f"  finest bare-earth DEM this supports: about {lid['dem_cell_m']:.2f} m cells "
                         f'(rule of thumb, assuming about 1 point in {GROUND_SHARE} is ground; fewer under trees '
                         'and buildings)')
        if lid['in_degrees']:
            lines.append(f"  {lid['in_degrees']:,} file(s) use degrees, not metres: area and density not computed for them")
        if lid['crs'].get('none recorded'):
            lines.append('  Files with no coordinate system recorded were assumed to be in metres.')
        lines.append('  Ground classification: file headers cannot say. Ask the vendor whether ground points are')
        lines.append('  classified, and with which method.')
    if c['unreadable']:
        lines.append(f"  {c['unreadable']:,} .las/.laz file(s) could not be read as lidar (damaged, or not LAS).")
    lines.append('')
    est = storage_estimate(total, lid)
    lines.append('Disk space for the lab laptop (estimate)')
    lines.append(f'  data as delivered:            {gb(total):>10,.1f} GB')
    lines.append(f"  compressed working copy:      {gb(est['copc_copy']):>10,.1f} GB  (lidar as COPC, if not already)")
    if est['cell']:
        lines.append(f"  derived rasters:              {gb(est['derived']):>10,.1f} GB  ({len(DERIVED_RASTERS)} rasters at "
                     f"{est['cell']:g} m: {', '.join(DERIVED_RASTERS)})")
    lines.append(f"  with half again for headroom: {gb(est['need']):>10,.1f} GB")
    lines.append('  suggested SSD: ' + (f"{est['ssd_gb'] // 1000} TB" if est['ssd_gb'] and est['ssd_gb'] >= 1000
                                         else f"{est['ssd_gb']} GB" if est['ssd_gb'] else 'more than 8 TB: plan external storage'))
    lines.append('  Plus a separate encrypted backup drive at least as large as the data.')
    return '\n'.join(lines) + '\n'


def cmd_specs(args):
    text = render(collect(args.roots))
    print(text, end='')
    if args.output:
        Path(args.output).write_text(text, encoding='ascii', errors='replace')
        print(f'Written to {args.output}')


# manifest / verify -----------------------------------------------------------------------------------------

def sha256_file(path, progress=None):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            if progress:
                progress(len(chunk))
    return h.hexdigest()


class Progress:
    """A line every ~10 s: GB done, speed and time left."""

    def __init__(self, total, every=10.0):
        self.total, self.done, self.every = total, 0, every
        self.start = self.last = time.monotonic()

    def __call__(self, n):
        self.done += n
        now = time.monotonic()
        if now - self.last >= self.every:
            self.last = now
            rate = self.done / max(now - self.start, 1e-9)
            left = (self.total - self.done) / rate if rate else 0
            print(f'  {gb(self.done):,.1f} of {gb(self.total):,.1f} GB, {rate / 1e6:,.0f} MB/s, '
                  f'about {left / 60:,.0f} min left', flush=True)


def relative(path, root):
    return path.relative_to(root).as_posix()


def cmd_manifest(args):
    root = Path(args.root)
    out = Path(args.output).resolve()
    paths = [p for p in files_under(root) if p.resolve() != out]
    sizes = {p: p.stat().st_size for p in paths}
    total = sum(sizes.values())
    print(f'Fingerprinting {len(paths):,} files, {gb(total):,.1f} GB ...', flush=True)
    progress = Progress(total)
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['path', 'bytes', 'sha256'])
        for p in paths:
            w.writerow([relative(p, root), sizes[p], sha256_file(p, progress)])
    print(f'Manifest: {out} ({len(paths):,} files)')
    print(f'Fingerprint of the manifest itself: {sha256_file(out)}')
    print('Write that fingerprint down somewhere separate from the laptop: it shows the manifest was not changed.')


def cmd_verify(args):
    root = Path(args.root)
    manifest = Path(args.manifest).resolve()
    with open(manifest, newline='', encoding='utf-8') as f:
        expected = {r['path']: (int(r['bytes']), r['sha256']) for r in csv.DictReader(f)}
    present = {relative(p, root): p for p in files_under(root) if p.resolve() != manifest}
    missing = sorted(set(expected) - set(present))
    extra = sorted(set(present) - set(expected))
    both = sorted(set(expected) & set(present))
    total = 0 if args.quick else sum(expected[k][0] for k in both)
    progress = Progress(total)
    changed = []
    if not args.quick and both:
        print(f'Checking fingerprints of {len(both):,} files, {gb(total):,.1f} GB ...', flush=True)
    for k in both:
        size, digest = expected[k]
        p = present[k]
        if p.stat().st_size != size:
            changed.append((k, 'size differs'))
        elif not args.quick and sha256_file(p, progress) != digest:
            changed.append((k, 'contents differ'))
    ok = len(both) - len(changed)
    print(f'{ok:,} of {len(expected):,} files present and unchanged'
          + (' (sizes only: --quick skips fingerprints)' if args.quick else '') + '.')
    for title, items in (('MISSING', missing), ('CHANGED', [f'{k} ({why})' for k, why in changed]),
                         ('EXTRA (not in the manifest)', extra)):
        if items:
            print(f'{title}: {len(items):,}')
            for item in items[:20]:
                print(f'  {item}')
            if len(items) > 20:
                print(f'  ... and {len(items) - 20:,} more')
    if missing or changed or extra:
        sys.exit(1)
    print('All good.')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('specs', help='totals by kind and lidar facts from headers (no names, no coordinates)')
    a.add_argument('roots', nargs='+', help='folders (or files) to look at')
    a.add_argument('-o', '--output', help='also write the report to this file')
    a.set_defaults(fn=cmd_specs)
    a = sub.add_parser('manifest', help='SHA-256 fingerprint of every file under a folder')
    a.add_argument('root')
    a.add_argument('-o', '--output', default='manifest.csv')
    a.set_defaults(fn=cmd_manifest)
    a = sub.add_parser('verify', help='check a folder against a manifest')
    a.add_argument('root')
    a.add_argument('manifest')
    a.add_argument('--quick', action='store_true', help='compare sizes only (fast, but misses changed contents)')
    a.set_defaults(fn=cmd_verify)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == '__main__':
    main()
