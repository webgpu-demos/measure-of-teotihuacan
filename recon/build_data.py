"""Build the public reconstruction's data from open sources.

Inputs (downloaded into recon-data/ on first run; not committed):
  * INEGI 1.5 m LiDAR terrain (MDT) and surface (MDS) models, sheets E14B21B1-B4
  * OpenStreetMap features from the Overpass query in recon-data/osm/query.overpassql
  * AWS open terrain tiles (terrarium) for the distant horizon

Outputs (docs/reconstruction/data/):
  core_dtm.png / core_dsm.png       4.2 x 4.5 km at 3 m
  context_dtm.png / context_dsm.png the four LiDAR sheets at 12 m
  horizon.png                       ~32 km at 40 m
  sites.json                        OSM monuments, the archaeological zone, the Street of the Dead
  mounds.json                       compact, compound-sized mounds detected in the zone (unverified)
  meta.json                         extents, encoding and attribution

Heights are stored as 16-bit values in the red (high byte) and green (low byte)
channels of an RGB PNG: h = BASE + value / SCALE.

Usage: python3 -I recon/build_data.py   (Python with numpy, scipy, Pillow; GDAL command-line tools)
"""
import json
import math
import os
import shutil
import subprocess
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, 'recon-data')
OUT = os.path.join(ROOT, 'docs', 'reconstruction', 'data')
GDAL = os.path.dirname(shutil.which('gdalwarp') or '/opt/anaconda3/bin/gdalwarp')

# Local frame: origin at the core centre, x east, y north (metres, UTM 14N).
ORIGIN = (516200.0, 2177150.0)
CORE = dict(xmin=514100, xmax=518300, ymin=2174900, ymax=2179400, res=3)
CONTEXT = dict(xmin=511611, xmax=523323, ymin=2169960, ymax=2183856, res=12)
HORIZON = dict(xmin=500200, xmax=532200, ymin=2161150, ymax=2193150, res=40)
BASE, SCALE = 2200.0, 50.0          # 2 cm steps, 2200-3510 m
GRID_DEG = 15.5                     # city orientation, east of north


def run(*args):
    subprocess.run([os.path.join(GDAL, args[0]), *args[1:]], check=True)


def warp(src, box, name):
    tif = os.path.join(RAW, 'work', f'{name}.tif')
    run('gdalwarp', '-q', '-overwrite', '-te', str(box['xmin']), str(box['ymin']), str(box['xmax']), str(box['ymax']),
        '-tr', str(box['res']), str(box['res']), '-r', 'average', src, tif)
    bil = tif[:-4] + '.bil'
    run('gdal_translate', '-q', '-of', 'ENVI', '-ot', 'Float32', tif, bil)
    w = round((box['xmax'] - box['xmin']) / box['res'])
    h = round((box['ymax'] - box['ymin']) / box['res'])
    a = np.fromfile(bil, dtype=np.float32).reshape(h, w)
    a[~np.isfinite(a) | (a < 1000)] = np.nan
    return a


def fill_nan(a):
    if not np.isnan(a).any():
        return a
    idx = ndimage.distance_transform_edt(np.isnan(a), return_distances=False, return_indices=True)
    return a[tuple(idx)]


def encode(a, path):
    v = np.clip(np.round((fill_nan(a) - BASE) * SCALE), 0, 65535).astype(np.uint16)
    rgb = np.zeros(v.shape + (3,), np.uint8)
    rgb[..., 0] = v >> 8
    rgb[..., 1] = v & 255
    Image.fromarray(rgb, 'RGB').save(path, optimize=True)
    return os.path.getsize(path)


def extent(box):
    return {'x0': box['xmin'] - ORIGIN[0], 'x1': box['xmax'] - ORIGIN[0], 'y0': box['ymin'] - ORIGIN[1], 'y1': box['ymax'] - ORIGIN[1],
            'res': box['res'], 'w': round((box['xmax'] - box['xmin']) / box['res']), 'h': round((box['ymax'] - box['ymin']) / box['res'])}


# ------------------------------------------------------------------ horizon tiles
def horizon():
    z = 12
    def tile(lon, lat):
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        return x, y
    x0, y0 = tile(-99.13, 19.92)
    x1, y1 = tile(-98.55, 19.42)
    tdir = os.path.join(RAW, 'tiles')
    os.makedirs(tdir, exist_ok=True)
    W, H = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
    mosaic = np.zeros((H, W), np.float32)
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            p = os.path.join(tdir, f'{z}_{tx}_{ty}.png')
            if not os.path.exists(p):
                req = urllib.request.Request(f'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{tx}/{ty}.png',
                                             headers={'User-Agent': 'measure-of-teotihuacan/0.1'})
                with urllib.request.urlopen(req, timeout=60) as r, open(p, 'wb') as f:
                    f.write(r.read())
            c = np.asarray(Image.open(p).convert('RGB'), dtype=np.float32)
            mosaic[(ty - y0) * 256:(ty - y0 + 1) * 256, (tx - x0) * 256:(tx - x0 + 1) * 256] = c[..., 0] * 256 + c[..., 1] + c[..., 2] / 256 - 32768
    # Georeference in Web Mercator through a raw file and a VRT, then warp to UTM.
    R = 6378137.0
    world = 2 * math.pi * R
    px = world / (256 * 2 ** z)
    gx0 = x0 * 256 * px - world / 2
    gy0 = world / 2 - y0 * 256 * px
    raw = os.path.join(RAW, 'work', 'horizon_3857.raw')
    mosaic.tofile(raw)
    vrt = raw[:-4] + '.vrt'
    with open(vrt, 'w') as f:
        f.write(f'''<VRTDataset rasterXSize="{W}" rasterYSize="{H}">
  <SRS>EPSG:3857</SRS>
  <GeoTransform>{gx0}, {px}, 0, {gy0}, 0, {-px}</GeoTransform>
  <VRTRasterBand dataType="Float32" band="1" subClass="VRTRawRasterBand">
    <SourceFilename relativeToVRT="1">{os.path.basename(raw)}</SourceFilename>
    <ImageOffset>0</ImageOffset><PixelOffset>4</PixelOffset><LineOffset>{W * 4}</LineOffset><ByteOrder>LSB</ByteOrder>
  </VRTRasterBand>
</VRTDataset>''')
    out = os.path.join(RAW, 'work', 'horizon_utm.tif')
    run('gdalwarp', '-q', '-overwrite', '-t_srs', os.path.join(RAW, 'utm.wkt'), '-te', str(HORIZON['xmin']), str(HORIZON['ymin']),
        str(HORIZON['xmax']), str(HORIZON['ymax']), '-tr', str(HORIZON['res']), str(HORIZON['res']), '-r', 'bilinear', vrt, out)
    bil = out[:-4] + '.bil'
    run('gdal_translate', '-q', '-of', 'ENVI', '-ot', 'Float32', out, bil)
    e = extent(HORIZON)
    return np.fromfile(bil, dtype=np.float32).reshape(e['h'], e['w'])


# ------------------------------------------------------------------ OSM
def to_local(points):
    """lon/lat pairs -> local metres, via gdaltransform in one batch."""
    if not points:
        return []
    txt = '\n'.join(f'{lon} {lat}' for lon, lat in points)
    out = subprocess.run([os.path.join(GDAL, 'gdaltransform'), '-s_srs', 'EPSG:4326', '-t_srs', os.path.join(RAW, 'utm.wkt')],
                         input=txt, capture_output=True, text=True, check=True).stdout.split('\n')
    res = []
    for line in out:
        if line.strip():
            x, y = map(float, line.split()[:2])
            res.append((round(x - ORIGIN[0], 2), round(y - ORIGIN[1], 2)))
    return res


SITE_NAMES = {
    'Pirámide de la Luna': 'moon', 'Pirámide del Sol': 'sun', 'Templo del Quetzalcoatl': 'fsp', 'Adosada Platform': 'fsp-adosada',
    'Murallas de la Ciudadela': 'ciudadela-wall', 'Plataforma': 'ciudadela-platform', 'Palacio de Quetzalpapálotl': 'quetzalpapalotl',
    'El Palacio de Xalla': 'xalla', 'Tepantitla': 'tepantitla', 'Tetitla': 'tetitla', 'Atetelco': 'atetelco',
    'Palacio de Zacuala': 'zacuala', 'Palacio de Yayahuala': 'yayahuala', 'Grupo Viking': 'viking', 'Edificios Superpuestos': 'superpuestos',
    'Conjunto Plaza Oeste': 'plaza-oeste', 'Las Viviendas de los Sacerdotes': 'sacerdotes', 'Palacio del Sol': 'palacio-sol',
    'Zona de Monumentos Arqueologicos de Teotihuacan': 'zone',
}


def osm_sites():
    d = json.load(open(os.path.join(RAW, 'osm', 'site.json')))
    sites, street = {}, []
    for e in d['elements']:
        t = e.get('tags', {})
        name = t.get('name', '')
        geom = e.get('geometry')
        if e['type'] == 'relation' and name == 'Pirámide del Sol':
            ways = [m['geometry'] for m in e.get('members', []) if m.get('role') == 'outer' and m.get('geometry')]
            geom = [p for w in ways for p in w]
        if not geom:
            continue
        pts = [(p['lon'], p['lat']) for p in geom]
        if name in SITE_NAMES:
            key = SITE_NAMES[name]
            poly = to_local(pts)
            if key not in sites or len(poly) > len(sites[key]['poly']):
                sites[key] = {'name': name, 'poly': poly, 'osm': f"{e['type']}/{e['id']}"}
        elif name == 'Camino de los Muertos':
            street.append(to_local(pts))
    return sites, street


# ------------------------------------------------------------------ mounds
def detect_mounds(dtm, zone_poly):
    e = extent(CORE)
    res = CORE['res']
    lrm = dtm - ndimage.gaussian_filter(dtm, sigma=15)
    mask_img = Image.new('L', (e['w'], e['h']), 0)
    to_px = lambda x, y: ((x - e['x0']) / res, (e['y1'] - y) / res)
    ImageDraw.Draw(mask_img).polygon([to_px(x, y) for x, y in zone_poly], fill=1)
    zone = np.asarray(mask_img, bool)
    cand = (lrm > 0.5) & zone
    cand = ndimage.binary_opening(cand, iterations=2)
    lab, n = ndimage.label(cand)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        m = lab[sl] == i
        area = m.sum() * res * res
        if not (500 <= area <= 6000):
            continue
        hgt = float(lrm[sl][m].max())
        if hgt > 12:                     # the pyramids are modelled from OSM, not here
            continue
        bh, bw = m.shape
        fill = m.sum() / (bh * bw)
        aspect = max(bh, bw) / max(1, min(bh, bw))
        if fill < 0.45 or aspect > 2.6:
            continue
        cy, cx = ndimage.center_of_mass(m)
        x = e['x0'] + (sl[1].start + cx + 0.5) * res
        y = e['y1'] - (sl[0].start + cy + 0.5) * res
        out.append({'x': round(x, 1), 'y': round(y, 1), 'size': round(math.sqrt(area), 1), 'height': round(hgt, 2), 'fill': round(float(fill), 2)})
    return out, lrm


def grid_frame(x, y):
    """Local metres -> city-grid axes (u along the 15.5 degree east axis, v along its north axis)."""
    a = math.radians(GRID_DEG)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def from_grid(u, v):
    a = math.radians(GRID_DEG)
    return u * math.cos(a) + v * math.sin(a), -u * math.sin(a) + v * math.cos(a)


def measure(poly, dtm):
    """Grid-aligned footprint, base and top elevation of a site, measured on the 3 m DTM."""
    e = extent(CORE)
    res = CORE['res']
    to_px = lambda x, y: ((x - e['x0']) / res, (e['y1'] - y) / res)
    img = Image.new('L', (e['w'], e['h']), 0)
    ImageDraw.Draw(img).polygon([to_px(x, y) for x, y in poly], fill=1)
    m = np.asarray(img, bool)
    if m.sum() < 4:
        return None
    ring = ndimage.binary_dilation(m, iterations=4) & ~ndimage.binary_dilation(m, iterations=1)
    base = float(np.median(dtm[ring]))
    top = float(dtm[m].max())
    uv = [grid_frame(x, y) for x, y in poly]
    us, vs = [p[0] for p in uv], [p[1] for p in uv]
    cu, cv = (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2
    cx, cy = from_grid(cu, cv)
    return {'cx': round(cx, 1), 'cy': round(cy, 1), 'w': round(max(us) - min(us), 1), 'd': round(max(vs) - min(vs), 1),
            'base': round(base, 2), 'top': round(top, 2), 'height': round(top - base, 2)}


def ciudadela(dtm, lrm, fsp):
    """The Ciudadela's raised banks: the large high-relief ring around the Feathered Serpent Pyramid."""
    e = extent(CORE)
    res = CORE['res']
    hi = lrm > 1.6
    lab, _ = ndimage.label(hi)
    px = int((fsp['cx'] - e['x0']) / res), int((e['y1'] - fsp['cy']) / res)
    best, best_area = None, 0
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        area = (lab[sl] == i).sum() * res * res
        cy0, cx0 = (sl[0].start + sl[0].stop) / 2, (sl[1].start + sl[1].stop) / 2
        if area > best_area and abs(cx0 - px[0]) < 200 and abs(cy0 - px[1]) < 200 and area > 20000:
            best, best_area = i, area
    if best is None:
        return None
    ys, xs = np.nonzero(lab == best)
    X = e['x0'] + (xs + 0.5) * res
    Y = e['y1'] - (ys + 0.5) * res
    uv = np.array([grid_frame(x, y) for x, y in zip(X[::7], Y[::7])])
    u0, u1 = np.percentile(uv[:, 0], [0.5, 99.5])
    v0, v1 = np.percentile(uv[:, 1], [0.5, 99.5])
    cx, cy = from_grid((u0 + u1) / 2, (v0 + v1) / 2)
    bank_top = float(np.percentile(dtm[lab == best], 75))
    return {'cx': round(cx, 1), 'cy': round(cy, 1), 'w': round(u1 - u0, 1), 'd': round(v1 - v0, 1), 'bank_top': round(bank_top, 2), 'area_m2': round(best_area)}


def street_axis(street):
    """Fit the Street of the Dead's centre line in grid axes: its u offset and v extent."""
    pts = [grid_frame(x, y) for seg in street for x, y in seg]
    us = np.array([p[0] for p in pts])
    vs = np.array([p[1] for p in pts])
    slope = np.polyfit(vs, us, 1)[0]
    return {'u': round(float(np.median(us)), 1), 'v0': round(float(vs.min()), 1), 'v1': round(float(vs.max()), 1),
            'residual_deg': round(math.degrees(math.atan(slope)), 2)}


INEGI = 'https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/geografia/imagen_cartografica/1_10_000/lidar/1_5m'
SHEETS = {  # 1:10,000 sheet -> INEGI product file (TIFF), 2025 edition
    'B1_dtm': 'Terreno/794551141939', 'B2_dtm': 'Terreno/794551141946', 'B3_dtm': 'Terreno/794551141953', 'B4_dtm': 'Terreno/794551141960',
    'B1_dsm': 'Superficie/794551134504', 'B2_dsm': 'Superficie/794551134511', 'B3_dsm': 'Superficie/794551134528', 'B4_dsm': 'Superficie/794551134535',
}


def download():
    """Fetch the INEGI sheets and the OSM extract if they are not already in recon-data/."""
    import zipfile
    raw = os.path.join(RAW, 'raw')
    os.makedirs(raw, exist_ok=True)
    for name, key in SHEETS.items():
        if os.path.isdir(os.path.join(raw, name)):
            continue
        z = os.path.join(raw, f'{name}.zip')
        req = urllib.request.Request(f'{INEGI}/{key}_t.zip', headers={'User-Agent': 'measure-of-teotihuacan/0.1'})
        with urllib.request.urlopen(req, timeout=300) as r, open(z, 'wb') as f:
            shutil.copyfileobj(r, f)
        zipfile.ZipFile(z).extractall(os.path.join(raw, name))
        print('downloaded', name)
    for kind in ('dtm', 'dsm'):
        vrt = os.path.join(RAW, f'{kind}.vrt')
        if not os.path.exists(vrt):
            tifs = [os.path.join(dp, f) for dp, _, fs in os.walk(raw) for f in fs if f.endswith('.tif') and f'_{kind}' in dp]
            run('gdalbuildvrt', '-q', vrt, *sorted(tifs))
    utm = os.path.join(RAW, 'utm.wkt')
    if not os.path.exists(utm):
        first = next(os.path.join(dp, f) for dp, _, fs in os.walk(raw) for f in fs if f.endswith('_mt.tif'))
        with open(utm, 'w') as f:
            f.write(subprocess.run([os.path.join(GDAL, 'gdalsrsinfo'), '-o', 'wkt1', first], capture_output=True, text=True, check=True).stdout)
    osm = os.path.join(RAW, 'osm', 'site.json')
    if not os.path.exists(osm):
        os.makedirs(os.path.dirname(osm), exist_ok=True)
        q = open(os.path.join(ROOT, 'recon', 'osm_query.overpassql')).read()
        data = urllib.parse.urlencode({'data': q}).encode()
        req = urllib.request.Request('https://overpass-api.de/api/interpreter', data=data,
                                     headers={'User-Agent': 'measure-of-teotihuacan/0.1', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=300) as r, open(osm, 'wb') as f:
            f.write(r.read())
        print('downloaded OSM extract')


def main():
    download()
    os.makedirs(os.path.join(RAW, 'work'), exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    sizes = {}
    core_dtm = warp(os.path.join(RAW, 'dtm.vrt'), CORE, 'core_dtm_3')
    core_dsm = warp(os.path.join(RAW, 'dsm.vrt'), CORE, 'core_dsm_3')
    core_dsm = np.fmax(core_dsm, core_dtm)
    ctx_dtm = warp(os.path.join(RAW, 'dtm.vrt'), CONTEXT, 'context_dtm_12')
    ctx_dsm = np.fmax(warp(os.path.join(RAW, 'dsm.vrt'), CONTEXT, 'context_dsm_12'), ctx_dtm)
    hor = horizon()
    for name, a in [('core_dtm', core_dtm), ('core_dsm', core_dsm), ('context_dtm', ctx_dtm), ('context_dsm', ctx_dsm), ('horizon', hor)]:
        sizes[name] = encode(a, os.path.join(OUT, f'{name}.png'))

    sites, street = osm_sites()
    zone = sites.get('zone', {}).get('poly', [])
    dtm_f = fill_nan(core_dtm)
    mounds, lrm = detect_mounds(dtm_f, zone)
    for k, s in sites.items():
        if k != 'zone':
            s['measured'] = measure(s['poly'], dtm_f)
    extra = {'ciudadela': ciudadela(dtm_f, lrm, sites['fsp']['measured']), 'street': street_axis(street)}
    json.dump({'sites': sites, 'street': street, 'derived': extra}, open(os.path.join(OUT, 'sites.json'), 'w'), separators=(',', ':'))
    json.dump(mounds, open(os.path.join(OUT, 'mounds.json'), 'w'), separators=(',', ':'))

    meta = {
        'origin_utm14n': ORIGIN, 'base': BASE, 'scale': SCALE, 'grid_deg': GRID_DEG,
        'layers': {'core': extent(CORE), 'context': extent(CONTEXT), 'horizon': extent(HORIZON)},
        'attribution': [
            'Terrain and surface: INEGI, Modelos digitales de elevación de alta resolución LiDAR 1.5 m, sheets E14B21B1–B4 (2025 edition). INEGI terms of free use.',
            'Horizon: Terrain Tiles on AWS (Tilezen/Mapzen; SRTM, GMTED2010 and other open sources).',
            'Site outlines and the Street of the Dead: © OpenStreetMap contributors, ODbL.',
        ],
    }
    json.dump(meta, open(os.path.join(OUT, 'meta.json'), 'w'), indent=1)
    zone_area = 0.0
    if zone:
        xs, ys = np.array([p[0] for p in zone]), np.array([p[1] for p in zone])
        zone_area = 0.5 * abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1)))
    print('sizes KB', {k: v // 1024 for k, v in sizes.items()})
    print('derived', extra)
    for k in ('sun', 'moon', 'fsp', 'fsp-adosada', 'quetzalpapalotl', 'xalla', 'tetitla'):
        print(k, sites[k].get('measured'))
    print(f'zone area {zone_area / 1e6:.2f} km2, mounds {len(mounds)}')
    print('core range', float(np.nanmin(core_dtm)), float(np.nanmax(core_dsm)), 'horizon range', float(hor.min()), float(hor.max()))


if __name__ == '__main__':
    main()
