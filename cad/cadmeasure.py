#!/usr/bin/env python3
"""Measure AutoCAD excavation drawings for the unit-of-length test, without exporting a single coordinate.

Runs on the excavation team's own machine. It reads DXF (or DWG, when the free ODA File Converter
is installed), turns every straight drawn edge into measurements on the city's grid and writes a
table of lengths tagged by drawing, layer and entity handle, so any number can be found again in
the original plan.

    python cad/cadmeasure.py layers  drawings/*.dxf -o layer_map.csv      # suggest what each layer is
    python cad/cadmeasure.py extract drawings/*.dxf --layers layer_map.csv -o out/
    python cad/cadmeasure.py freeze  out/measurements.csv --layers layer_map.csv
    python cad/cadmeasure.py selftest                                      # prove it on made-up drawings

What it writes (measurements.csv, summary.json, freeze.json) holds lengths, angles, counts and
names only. Positions are used in memory to line edges up and are never written.
"""
import argparse
import csv
import datetime
import hashlib
import json
import math
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

VERSION = '0.1'
ACIS = {'3DSOLID', 'BODY', 'REGION', 'SURFACE', 'EXTRUDEDSURFACE', 'LOFTEDSURFACE', 'REVOLVEDSURFACE',
        'SWEPTSURFACE', 'PLANESURFACE', 'NURBSURFACE'}
CURVES = {'ARC', 'CIRCLE', 'ELLIPSE', 'SPLINE', 'HELIX'}
INSUNITS = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0, 14: 0.1}
UNIT_NAMES = {'m': 1.0, 'cm': 0.01, 'mm': 0.001, 'dm': 0.1, 'in': 0.0254, 'ft': 0.3048}
COLUMNS = ['id', 'drawing', 'structure', 'layer', 'element', 'phase', 'condition', 'confirmed', 'kind', 'axis', 'value_m',
           'copies', 'segments', 'off_grid_deg', 'incl_deg', 'handles']
MAP_COLUMNS = ['layer', 'drawings', 'entities', 'types', 'colour', 'suggested_element', 'suggested_condition',
               'suggested_phase', 'suggested_include', 'element', 'condition', 'phase', 'include', 'notes']

# Layer-name hints, Spanish and English, checked in order. The archaeologist confirms every one.
ELEMENT_WORDS = [
    (('curva', 'contour', 'nivel'), 'contour', 'no'),
    (('cota', 'texto', 'text', 'dim', 'anot', 'rotul', 'titulo', 'marco', 'norte', 'escala', 'viewport', 'defpoints'), 'annotation', 'no'),
    (('reticula', 'cuadricula', 'grid', 'eje', 'retic'), 'survey grid', 'no'),
    (('exc lim', 'lim exc', 'excavation lim', 'excavation edge', 'limite de exc', 'limite exc'), 'excavation limit', 'no'),
    (('looting', 'loot pit', 'saqueo'), 'looting pit', 'no'),
    (('sondeo', 'pozo', 'trinchera', 'cala', 'excavacion', 'unidad', 'test pit', 'test unit', 'excavation unit',
      'trench', 'cuadro'), 'excavation unit', 'no'),
    (('achur', 'trama', 'hatch', 'foto', 'imagen', 'image', 'lidar', 'topo', 'mdt', 'ortho'), 'background', 'no'),
    (('relleno', 'fill'), 'fill', 'no'),
    (('alfarda', 'balustr'), 'balustrade', 'yes'),
    (('escalin', 'escaler', 'escalon', 'peldan', 'huella', 'peralte', 'stair', 'step'), 'stair', 'yes'),
    (('talud',), 'talud', 'yes'),
    (('tablero',), 'tablero', 'yes'),
    (('pilar', 'column', 'pillar'), 'pillar', 'yes'),
    (('muro', 'pared', 'wall'), 'wall', 'yes'),
    (('banqueta', 'bench'), 'bench', 'yes'),
    (('piso', 'floor', 'aplanado'), 'floor', 'yes'),
    (('estuco', 'stucco', 'plaster'), 'stucco', 'yes'),
    (('plataforma', 'platform', 'basamento'), 'platform', 'yes'),
    (('cuarto', 'room', 'portico', 'patio', 'templo', 'temple'), 'room', 'yes'),
    (('drenaje', 'canal', 'drain'), 'drain', 'yes'),
    (('cimiento', 'cimentacion', 'foundation'), 'foundation', 'yes'),
    (('escultura', 'sculpture', 'clavo', 'tenon'), 'sculpture', 'check'),
    (('ofrenda', 'entierro', 'offering', 'burial'), 'feature', 'check'),
    (('tunel', 'tunnel'), 'tunnel', 'check'),
    (('piedra', 'stone', 'roca', 'rock', 'mamposter', 'masonry'), 'stone', 'check'),
]
CONDITION_WORDS = [
    (('hipot', 'hypothet', 'propuest', 'proposed', 'ideal'), 'hypothetical', 'no'),
    (('reconstr', 'restitu'), 'reconstructed', 'no'),             # 'reconstr' also catches 'Reconstr.'
    (('modern', 'contempor'), 'modern', 'no'),
    (('restaur', 'restor', 'consolid', 'cement', 'concret'), 'restored', 'check'),     # cement or concrete: modern repair
    (('erosion', 'erod', 'destru', 'destroy', 'colaps', 'collaps', 'saque', 'looted', 'danad', 'damag'), 'eroded', None),
    (('excavad', 'excavated', 'original', 'expuest', 'exposed'), 'excavated', None),
]
PHASES = ('tzacualli', 'miccaotli', 'tlamimilolpa', 'xolalpan', 'metepec', 'coyotlatelco', 'patlachique')


ACI_NAMES = {1: 'red', 2: 'yellow', 3: 'green', 4: 'cyan', 5: 'blue', 6: 'magenta', 7: 'white', 8: 'dark grey',
             9: 'light grey'}
BASIC_RGB = {'red': (255, 0, 0), 'orange': (255, 128, 0), 'yellow': (255, 255, 0), 'green': (0, 255, 0),
             'cyan': (0, 255, 255), 'blue': (0, 0, 255), 'magenta': (255, 0, 255), 'white': (255, 255, 255),
             'grey': (128, 128, 128), 'black': (0, 0, 0)}


def colour_name(aci=None, rgb=None):
    """A colour a person can read: AutoCAD's first nine index colours by name, others as nearest name + hex."""
    if rgb is None:
        if aci in (None, 256):
            return 'by layer'
        if aci == 0:
            return 'by block'
        if aci in ACI_NAMES:
            return ACI_NAMES[aci]
        from ezdxf.colors import aci2rgb
        rgb = aci2rgb(aci)
    r, g, b = (int(c) for c in rgb)
    near = min(BASIC_RGB, key=lambda k: sum((x - y) ** 2 for x, y in zip(BASIC_RGB[k], (r, g, b))))
    return f'{near} #{r:02X}{g:02X}{b:02X}'


def layer_colour(doc, layer):
    """The layer's own colour in this drawing, flagged when the layer is switched off or frozen."""
    if layer not in doc.layers:
        return ''
    lt = doc.layers.get(layer)
    c = colour_name(rgb=lt.rgb) if lt.rgb else colour_name(aci=lt.color)
    return c + (' (off)' if lt.is_off() else '') + (' (frozen)' if lt.is_frozen() else '')


def plain(name):
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[_\-.]+', ' ', s)


def suggest(layer):
    """Guess element, condition, phase and whether the layer belongs in the test, from its name."""
    s = plain(layer)
    element, include = '', 'check'
    for words, el, inc in ELEMENT_WORDS:
        if any(w in s for w in words):
            element, include = el, inc
            break
    condition = ''
    for words, cond, inc in CONDITION_WORDS:
        if any(w in s for w in words):
            condition = cond
            if inc == 'no' or (inc == 'check' and include == 'yes'):
                include = inc
            break
    m = re.search(r'\b(fase|etapa|phase|periodo|epoca|subestructura|sub)\s*([0-9]+|[ivx]+)\b', s)
    phase = f'{m.group(1)} {m.group(2)}' if m else next((p for p in PHASES if p in s), '')
    if layer == '0':
        element, include = 'unassigned', 'check'     # drafters often leave real geometry on layer 0
    elif layer.lower() == 'defpoints':
        element, include = 'annotation', 'no'
    return {'element': element, 'condition': condition, 'phase': phase, 'include': include}


# Reading -------------------------------------------------------------------------------------

def load(path):
    path = Path(path)
    if path.suffix.lower() == '.dwg':
        from ezdxf.addons import odafc
        if not odafc.is_installed():
            sys.exit(f'{path.name}: reading DWG needs the free ODA File Converter (opendesign.com/guestfiles/oda_file_converter).\n'
                     'Install it, or batch-convert the DWGs to DXF with it and point this tool at the DXFs.')
        return odafc.readfile(str(path))
    from ezdxf import recover
    doc, auditor = recover.readfile(str(path))
    return doc


class Geometry:
    """Straight edges and faces from one drawing, in drawing units, with their layer and handle."""

    def __init__(self):
        self.segs = []                      # (layer, handle, p0, p1)
        self.faces = []                     # (layer, handle, [p, ...])
        self.read, self.skipped, self.failed = Counter(), Counter(), Counter()
        self.unreadable = Counter()         # (layer, type)
        self.xrefs = Counter()              # external references (block name): not in this file, so not read
        self.layer_types = defaultdict(Counter)
        self.layer_colours = defaultdict(Counter)   # entities drawn in their own colour instead of the layer's


def v3(p):
    return np.array([p[0], p[1], p[2] if len(p) > 2 else 0.0], dtype=float)


def chain(geo, layer, handle, pts, bulges, closed, thickness=0.0, extrusion=(0, 0, 1)):
    """Straight segments of a polyline; arc segments (bulge) are left out. Thickness extrudes it."""
    n = len(pts)
    idx = [(i, i + 1) for i in range(n - 1)] + ([(n - 1, 0)] if closed and n > 2 else [])
    straight = [(i, j) for i, j in idx if not (bulges and abs(bulges[i]) > 1e-9)]
    for i, j in straight:
        geo.segs.append((layer, handle, pts[i], pts[j]))
    if thickness:
        off = v3(extrusion) / np.linalg.norm(v3(extrusion)) * thickness
        for i, j in straight:
            geo.segs.append((layer, handle, pts[i] + off, pts[j] + off))
        for p in pts:
            geo.segs.append((layer, handle, p, p + off))


def add_mesh(geo, layer, handle, mb):
    verts = [v3(v) for v in mb.vertices]
    for face in mb.faces:
        geo.faces.append((layer, handle, [verts[i] for i in face]))


def dedupe(pts):
    out = []
    for p in pts:
        if not out or np.linalg.norm(p - out[-1]) > 1e-9:
            out.append(p)
    if len(out) > 1 and np.linalg.norm(out[0] - out[-1]) <= 1e-9:
        out.pop()
    return out


def walk(e, geo, parent_layer=None, handle=None, depth=0):
    from ezdxf.math import Vec3
    from ezdxf.render import MeshBuilder
    t = e.dxftype()
    layer = e.dxf.get('layer', '0')
    if parent_layer is not None and layer == '0':
        layer = parent_layer                # block contents on layer 0 take the insert's layer
    handle = handle or e.dxf.get('handle') or '?'
    geo.layer_types[layer][t] += 1
    if e.dxf.hasattr('true_color'):
        geo.layer_colours[layer][colour_name(rgb=e.rgb)] += 1
    elif e.dxf.get('color', 256) not in (0, 256):
        geo.layer_colours[layer][colour_name(aci=e.dxf.color)] += 1
    try:
        if t == 'INSERT':
            if depth > 16:
                geo.failed[t] += 1
                return
            blk = e.block()
            if blk is not None and getattr(blk.block, 'is_xref', False):
                geo.xrefs[e.dxf.name] += 1
                return
            inserts = list(e.multi_insert()) if e.mcount > 1 else [e]
            for k, ins in enumerate(inserts):
                h = f'{handle}#{k + 1}' if len(inserts) > 1 else handle   # each MINSERT copy is its own element
                for ve in ins.virtual_entities():
                    walk(ve, geo, layer, h, depth + 1)
        elif t == 'LINE':
            chain(geo, layer, handle, [v3(e.dxf.start), v3(e.dxf.end)], None, False,
                  e.dxf.get('thickness', 0.0), e.dxf.extrusion)
        elif t == 'LWPOLYLINE':
            ocs, elev = e.ocs(), e.dxf.get('elevation', 0.0)
            pts = list(e.get_points('xyb'))
            chain(geo, layer, handle, [v3(ocs.to_wcs(Vec3(x, y, elev))) for x, y, _ in pts], [b for *_, b in pts],
                  e.closed, e.dxf.get('thickness', 0.0), e.dxf.extrusion)
        elif t == 'POLYLINE':
            if e.is_poly_face_mesh or e.is_polygon_mesh:
                add_mesh(geo, layer, handle, MeshBuilder.from_polyface(e))
            else:
                vs = [v for v in e.vertices if not v.dxf.get('flags', 0) & 16]   # skip spline frame points
                if e.is_3d_polyline:
                    chain(geo, layer, handle, [v3(v.dxf.location) for v in vs], None, e.is_closed)
                else:
                    ocs, elev = e.ocs(), e.dxf.elevation.z
                    pts = [v3(ocs.to_wcs(Vec3(v.dxf.location.x, v.dxf.location.y, elev))) for v in vs]
                    chain(geo, layer, handle, pts, [v.dxf.get('bulge', 0.0) for v in vs], e.is_closed,
                          e.dxf.get('thickness', 0.0), e.dxf.extrusion)
        elif t in ('3DFACE', 'SOLID', 'TRACE'):
            pts = dedupe([v3(p) for p in e.wcs_vertices()])
            if len(pts) >= 3:
                geo.faces.append((layer, handle, pts))
        elif t == 'MESH':
            add_mesh(geo, layer, handle, MeshBuilder.from_mesh(e))
        elif t in ACIS:
            geo.unreadable[(layer, t)] += 1
            return
        else:
            geo.skipped[t] += 1                 # curves, text, dimensions, hatches, points ...
            return
        geo.read[t] += 1
    except Exception:                           # one broken entity must not stop a drawing
        geo.failed[t] += 1


def read_drawing(path):
    doc = load(path)
    geo = Geometry()
    for e in doc.modelspace():
        walk(e, geo)
    return doc, geo


def drawing_scale(doc, override=None):
    """Metres per drawing unit, and a note on how it was decided."""
    if override:
        return UNIT_NAMES[override], f'set by --units {override}'
    code = doc.header.get('$INSUNITS', 0)
    if code in INSUNITS:
        return INSUNITS[code], f'$INSUNITS={code}'
    return 1.0, f'$INSUNITS={code} (unitless): assumed metres, set --units if wrong'


# Geometry -> measurements --------------------------------------------------------------------

def feature_edges(faces, snap=0.001, coplanar_deg=1.0):
    """Edges of face meshes, minus edges shared by two coplanar faces (triangulation, tiling)."""
    ids, verts, polys, normals, owners = {}, [], [], [], []
    for handle, pts in faces:
        poly = []
        for p in pts:
            k = tuple(np.round(p / snap).astype(np.int64))
            if k not in ids:
                ids[k] = len(verts)
                verts.append(p)
            if not poly or poly[-1] != ids[k]:
                poly.append(ids[k])
        if len(poly) > 1 and poly[0] == poly[-1]:
            poly.pop()
        if len(poly) < 3:
            continue
        P = np.array([verts[i] for i in poly])
        n = np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0)     # Newell normal
        if np.linalg.norm(n) < 1e-12:
            continue
        polys.append(poly)
        normals.append(n / np.linalg.norm(n))
        owners.append(handle)
    edges = defaultdict(list)
    for f, poly in enumerate(polys):
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            edges[(min(a, b), max(a, b))].append(f)
    cos_tol = math.cos(math.radians(coplanar_deg))
    out = []
    for (a, b), fs in edges.items():
        if len(fs) == 2 and abs(float(normals[fs[0]] @ normals[fs[1]])) > cos_tol:
            continue
        out.append((sorted({owners[f] for f in fs}), verts[a], verts[b]))
    return out


def detect_grid(d, weights):
    """Dominant orientation of horizontal edges, folded to 90 degrees (length-weighted, refined once)."""
    theta = np.arctan2(d[:, 1], d[:, 0])
    est = 0.0
    for width in (None, math.radians(10)):
        w = weights.copy()
        if width is not None:
            dev = (theta - est + math.pi / 4) % (math.pi / 2) - math.pi / 4
            w[np.abs(dev) > width] = 0
        if w.sum() == 0:
            break
        est = math.atan2((w * np.sin(4 * theta)).sum(), (w * np.cos(4 * theta)).sum()) / 4
    return est                                   # angle of the u axis from drawing +x, in radians


def cluster(idx, values, tol):
    order = idx[np.argsort(values[idx], kind='stable')]
    cuts = np.where(np.diff(values[order]) > tol)[0] + 1
    return np.split(order, cuts)


def axis_runs(ids, a, perp, P0, P1, tol, join):
    """Merge collinear, touching segments along axis a into runs. Returns (start, end, seg ids)."""
    mid = (P0[ids] + P1[ids]) / 2
    lo = np.minimum(P0[ids, a], P1[ids, a])
    hi = np.maximum(P0[ids, a], P1[ids, a])
    groups = [np.arange(len(ids))]
    for c in perp:
        groups = [g for grp in groups for g in cluster(grp, mid[:, c], tol)]
    runs = []
    for g in groups:
        order = g[np.argsort(lo[g], kind='stable')]
        cur, s, e = [order[0]], lo[order[0]], hi[order[0]]
        for k in order[1:]:
            if lo[k] <= e + join:
                cur.append(k)
                e = max(e, hi[k])
            else:
                runs.append((s, e, ids[cur], mid[cur].mean(axis=0)))
                cur, s, e = [k], lo[k], hi[k]
        runs.append((s, e, ids[cur], mid[cur].mean(axis=0)))
    return runs


def overlap(A, B):
    return sum(max(0.0, min(a1, b1) - max(a0, b0)) for a0, a1 in A for b0, b1 in B)


def union(intervals, join):
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def structures(P0, P1, join):
    """Split one drawing (already turned onto the grid) into structures. Drawn pieces that come within about
    `join` metres of each other in plan belong together (pieces up to about twice that apart may join too),
    and so does anything lying wholly inside another structure's footprint: the tiers of a stepped platform,
    separated in plan by empty terraces, stay one building. Returns a label per segment, numbered by size
    (0 has the most segments). join <= 0 keeps the drawing whole."""
    n = len(P0)
    if not join or join <= 0 or n == 0:
        return np.zeros(n, dtype=np.int64)
    d = (P1 - P0)[:, :2]
    k = np.maximum(1, np.ceil(np.hypot(d[:, 0], d[:, 1]) / (join / 2)).astype(np.int64))   # samples per segment
    seg = np.repeat(np.arange(n), k + 1)
    first = np.cumsum(k + 1) - (k + 1)
    t = (np.arange(len(seg)) - np.repeat(first, k + 1)) / np.repeat(k, k + 1)
    cells = np.floor((P0[seg, :2] + d[seg] * t[:, None]) / join).astype(np.int64)
    cells -= cells.min(axis=0)
    width = int(cells[:, 1].max()) + 3
    code = cells[:, 0] * width + cells[:, 1] + 1          # +1: a neighbour one column left never wraps a row
    uniq, inv = np.unique(code, return_inverse=True)
    inv = inv.ravel()
    parent = list(range(len(uniq)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for off in (1, width - 1, width, width + 1):          # each pair of touching cells, once
        pos = np.minimum(np.searchsorted(uniq, uniq + off), len(uniq) - 1)
        hit = uniq[pos] == uniq + off
        for a, b in zip(np.nonzero(hit)[0].tolist(), pos[hit].tolist()):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    root = np.array([find(x) for x in range(len(uniq))])
    lab = root[inv[first]]
    # Nested footprints (grid-aligned boxes) are one structure.
    ids, lab = np.unique(lab, return_inverse=True)
    lab = lab.ravel()
    box = np.empty((len(ids), 4))
    box[:, :2], box[:, 2:] = np.inf, -np.inf
    for P in (P0, P1):
        np.minimum.at(box[:, :2], lab, P[:, :2])
        np.maximum.at(box[:, 2:], lab, P[:, :2])
    parent = list(range(len(ids)))
    for i in np.argsort((box[:, 2] - box[:, 0]) * (box[:, 3] - box[:, 1]), kind='stable').tolist():
        inside = np.nonzero((box[:, 0] <= box[i, 0]) & (box[:, 1] <= box[i, 1]) &
                            (box[:, 2] >= box[i, 2]) & (box[:, 3] >= box[i, 3]))[0]
        for j in inside.tolist():
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)
    lab = np.array([find(x) for x in range(len(ids))])[lab]
    ids, counts = np.unique(lab, return_counts=True)
    rank = np.empty(len(ids), dtype=np.int64)
    rank[np.argsort(-counts, kind='stable')] = np.arange(len(ids))
    return rank[np.searchsorted(ids, lab)]


def measure(geo, scale, mapping, grid=None, tol_deg=3.0, tol=0.02, min_len=0.05, max_gap=60.0, join=2.0):
    """Turn one drawing's geometry into records (no positions) plus a summary.

    Lines are only paired, merged and grouped within one structure (see `structures`), so in a drawing of
    a whole site the gap between two separate buildings is not mistaken for a design dimension."""
    def rule(layer):
        return mapping.get(layer) or {**suggest(layer), 'confirmed': False}

    included = {l for l in geo.layer_types if rule(l)['include'] in ('yes', 'check')}
    segs = [(l, [h], p0 * scale, p1 * scale) for l, h, p0, p1 in geo.segs if l in included]
    by_layer = defaultdict(list)
    for l, h, pts in geo.faces:
        if l in included:
            by_layer[l].append((h, [p * scale for p in pts]))
    for l, faces in by_layer.items():
        segs += [(l, hs, a, b) for hs, a, b in feature_edges(faces)]

    summary = {'segments': len(segs), 'grid_deg_east_of_north': None, 'classes': {}, 'layers': {},
               'structures': {'join_m': join, 'count': 0, 'largest': []}}
    if not segs:
        return [], summary
    layers = np.array([s[0] for s in segs])
    handles = [s[1] for s in segs]
    P0 = np.array([s[2] for s in segs])
    P1 = np.array([s[3] for s in segs])
    centre = np.vstack([P0, P1]).mean(axis=0)
    P0, P1 = P0 - centre, P1 - centre               # positions stay relative, in memory only
    d = P1 - P0
    L = np.linalg.norm(d, axis=1)
    h = np.hypot(d[:, 0], d[:, 1])
    flat = (L > 1e-9) & (np.degrees(np.arctan2(np.abs(d[:, 2]), h)) < 10)
    phi = detect_grid(d[flat], h[flat]) if grid is None else math.radians(-grid)
    summary['grid_deg_east_of_north'] = round(-math.degrees(phi), 2)
    R = np.array([[math.cos(phi), math.sin(phi), 0], [-math.sin(phi), math.cos(phi), 0], [0, 0, 1]])
    P0, P1 = P0 @ R.T, P1 @ R.T
    d = P1 - P0
    h = np.hypot(d[:, 0], d[:, 1])
    incl = np.degrees(np.arctan2(np.abs(d[:, 2]), h))
    head = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180
    dev_u = (head + 90) % 180 - 90                  # signed degrees off the u axis
    dev_v = head - 90
    cls = np.full(len(segs), 'oblique', dtype=object)
    off = np.zeros(len(segs))
    cls[L < 1e-9] = 'point'
    cls[(L >= 1e-9) & (incl >= 90 - tol_deg)] = 'z'
    for name, dev in (('u', dev_u), ('v', dev_v)):
        m = (L >= 1e-9) & (incl <= tol_deg) & (np.abs(dev) <= tol_deg)
        cls[m], off[m] = name, dev[m]
        m = (L >= 1e-9) & (incl > tol_deg) & (incl < 90 - tol_deg) & (np.abs(dev) <= tol_deg)
        cls[m], off[m] = 's' + name, dev[m]
    summary['classes'] = {k: {'segments': int(n), 'length_m': round(float(L[cls == k].sum()), 2)}
                          for k, n in zip(*np.unique(cls, return_counts=True))}
    struct = structures(P0, P1, join)
    sname = lambda s: f'S{s + 1:03d}'
    nstruct = int(struct.max()) + 1
    largest = []
    for s in range(min(nstruct, 10)):
        m = struct == s
        pts = np.vstack([P0[m], P1[m]])
        largest.append({'structure': sname(s), 'segments': int(m.sum()),
                        'size_m': [round(float(pts[:, a].max() - pts[:, a].min()), 1) for a in (0, 1)]})
    summary['structures'].update(count=nstruct, largest=largest)

    def tag(layer):
        r = rule(layer)
        return {'layer': layer, 'element': r['element'], 'phase': r['phase'], 'condition': r['condition'],
                'confirmed': int(bool(r['confirmed']))}

    def hlist(ids):
        hs = sorted({x for i in ids for x in handles[i]})
        return ';'.join(hs[:8]) + (f';+{len(hs) - 8}' if len(hs) > 8 else '')

    records = []
    AX = {'u': 0, 'v': 1, 'z': 2}
    groups_of = defaultdict(list)
    for i, key in enumerate(zip(layers.tolist(), struct.tolist())):
        groups_of[key].append(i)
    for (layer, st), idx in sorted(groups_of.items()):
        idx = np.array(idx)
        base = tag(layer) | {'structure': sname(st)}
        lay_runs = {}
        for ax, a in AX.items():
            ids = idx[cls[idx] == ax]
            if len(ids) == 0:
                continue
            perp = [c for c in (0, 1, 2) if c != a]
            runs = axis_runs(ids, a, perp, P0, P1, tol, tol)
            lay_runs[ax] = runs
            # Parallel edges spanning the same interval (top/bottom/front/back of one prism) are one dimension.
            groups = []
            for s, e, sid, _ in sorted(runs, key=lambda r: (r[0], r[1])):
                for g in groups:
                    if abs(g[0] - s) <= tol and abs(g[1] - e) <= tol:
                        g[2].append(sid)
                        break
                else:
                    groups.append([s, e, [sid]])
            for s, e, parts in groups:
                if e - s < min_len:
                    continue
                sid = np.concatenate(parts)
                records.append(base | {'kind': 'edge', 'axis': ax, 'value_m': e - s, 'copies': len(parts),
                                             'segments': len(sid), 'off_grid_deg': float(np.average(off[sid], weights=L[sid])),
                                             'incl_deg': '', 'handles': hlist(sid)})
        # Gaps between parallel edges in plan: wall thickness, room width, balustrade width, tread depth.
        for ax, across in (('u', 1), ('v', 0)):
            if ax not in lay_runs:
                continue
            a = AX[ax]
            pos = np.array([(P0[r[2], across] + P1[r[2], across]).mean() / 2 for r in lay_runs[ax]])
            groups = cluster(np.arange(len(pos)), pos, tol)
            lines = [(pos[g].mean(), union([(lay_runs[ax][i][0], lay_runs[ax][i][1]) for i in g], tol),
                      np.concatenate([lay_runs[ax][i][2] for i in g])) for g in groups]
            for i, (p, iv, sid) in enumerate(lines):
                span = sum(e - s for s, e in iv)
                for q, jv, sj in lines[i + 1:]:
                    if q - p > max_gap:
                        break
                    if overlap(iv, jv) >= max(0.2, 0.25 * min(span, sum(e - s for s, e in jv))):
                        if q - p >= min_len:
                            both = np.concatenate([sid, sj])
                            records.append(base | {'kind': 'gap', 'axis': 'uv'[across], 'value_m': q - p,
                                                         'copies': 1, 'segments': len(both),
                                                         'off_grid_deg': float(np.average(off[both], weights=L[both])),
                                                         'incl_deg': '', 'handles': hlist(both)})
                        break
        # Sloping edges in a grid plane (talud faces, balustrade tops): length and inclination, unmerged.
        for i in idx[np.isin(cls[idx], ['su', 'sv'])]:
            if L[i] >= min_len:
                records.append(base | {'kind': 'edge', 'axis': cls[i], 'value_m': L[i], 'copies': 1, 'segments': 1,
                                             'off_grid_deg': off[i], 'incl_deg': round(float(incl[i]), 2),
                                             'handles': hlist([i])})
        # Overall size of each drawn element (entity or block insert), if it is mostly on the grid.
        # Elements spanning the same interval (32 treads of one stair) count once, with copies.
        per = defaultdict(list)
        for i in idx:
            for hd in handles[i]:
                per[hd].append(i)
        spans = defaultdict(list)
        for hd, ids in per.items():
            ids = np.array(ids)
            if len(ids) < 3 or L[ids][np.isin(cls[ids], ['u', 'v', 'z'])].sum() < 0.5 * L[ids].sum():
                continue
            pts = np.vstack([P0[ids], P1[ids]])
            for ax, a in AX.items():
                lo, hi = pts[:, a].min(), pts[:, a].max()
                if hi - lo < min_len:
                    continue
                for g in spans[ax]:
                    if abs(g[0] - lo) <= tol and abs(g[1] - hi) <= tol:
                        g[2].append(hd)
                        g[3] += len(ids)
                        break
                else:
                    spans[ax].append([lo, hi, [hd], len(ids)])
        for ax, groups in spans.items():
            for lo, hi, hds, nseg in groups:
                records.append(base | {'kind': 'extent', 'axis': ax, 'value_m': hi - lo, 'copies': len(hds),
                                             'segments': nseg, 'off_grid_deg': '', 'incl_deg': '',
                                             'handles': ';'.join(sorted(hds)[:8]) + (f';+{len(hds) - 8}' if len(hds) > 8 else '')})
    per_layer = Counter(r['layer'] for r in records)
    for layer in sorted(set(layers.tolist())):
        on = layers == layer
        summary['layers'][layer] = {
            'segments': int(on.sum()),
            'oblique_segments': int((on & (cls == 'oblique')).sum()),
            'records': per_layer[layer],
            'structures': int(len(np.unique(struct[on]))),
        }
    for r in records:
        r['value_m'] = round(float(r['value_m']), 4)
        if r['off_grid_deg'] != '':
            r['off_grid_deg'] = round(float(r['off_grid_deg']), 2)
    return records, summary


# Commands -----------------------------------------------------------------------------------

def read_map(path):
    if not path:
        return {}
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            confirmed = any((row.get(k) or '').strip() for k in ('element', 'include'))
            pick = lambda k: (row.get(k) or '').strip() or (row.get(f'suggested_{k}') or '').strip()
            include = pick('include').lower() or 'check'
            out[row['layer']] = {'element': pick('element'), 'condition': pick('condition'), 'phase': pick('phase'),
                                 'include': include, 'confirmed': confirmed}
    return out


def warn_xrefs(name, geo):
    if geo.xrefs:
        names = ', '.join(sorted(geo.xrefs)[:6]) + (' ...' if len(geo.xrefs) > 6 else '')
        print(f'{name}: {sum(geo.xrefs.values())} external reference(s) NOT read ({names}). '
              'Bind them in AutoCAD (XREF > Bind) and save, or measure those drawings too.')


def cmd_layers(args):
    rows = {}
    for path in args.drawings:
        doc, geo = read_drawing(path)
        warn_xrefs(Path(path).name, geo)
        for layer, types in geo.layer_types.items():
            r = rows.setdefault(layer, {'layer': layer, 'drawings': set(), 'types': Counter(), 'colours': set(),
                                        'own': Counter()})
            r['drawings'].add(Path(path).name)
            r['types'].update(types)
            r['colours'].add(layer_colour(doc, layer))
            r['own'].update(geo.layer_colours.get(layer, {}))
    old = {}
    if Path(args.output).exists():
        with open(args.output, newline='', encoding='utf-8-sig') as f:
            old = {r['layer']: r for r in csv.DictReader(f)}
    with open(args.output, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, MAP_COLUMNS)
        w.writeheader()
        for layer in sorted(rows, key=plain):
            r, s = rows[layer], suggest(layer)
            keep = old.get(layer, {})
            w.writerow({'layer': layer, 'drawings': len(r['drawings']), 'entities': sum(r['types'].values()),
                        'types': ' '.join(f'{t}:{n}' for t, n in r['types'].most_common()),
                        'colour': ' / '.join(sorted(c for c in r['colours'] if c))
                                  + (f"; entities drawn in {', '.join(f'{c} ({n})' for c, n in r['own'].most_common(3))}"
                                     if r['own'] else ''),
                        **{f'suggested_{k}': v for k, v in s.items()},
                        **{k: keep.get(k, '') for k in ('element', 'condition', 'phase', 'include', 'notes')}})
    print(f'{len(rows)} layers -> {args.output}')
    print('Fill in element / condition / phase / include for each layer (blank = take the suggestion, unconfirmed).')


def extract(drawings, mapping, out_dir, units=None, grid=None, tol_deg=3.0, alias_key=None, quiet=False, join=2.0):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records, summary = [], {'tool': f'cadmeasure {VERSION}', 'contains': 'lengths, angles, counts and names only; no coordinates',
                            'drawings': {}}
    aliases = {}
    for k, path in enumerate(drawings):
        name = f'D{k + 1:02d}' if alias_key else Path(path).name
        aliases[name] = Path(path).name
        doc, geo = read_drawing(path)
        scale, how = drawing_scale(doc, units)
        recs, s = measure(geo, scale, mapping, grid=grid, tol_deg=tol_deg, join=join)
        for r in recs:
            r['drawing'] = name
        records += recs
        s.update(units=how, entities_read=dict(geo.read), skipped=dict(geo.skipped), failed=dict(geo.failed),
                 unreadable=[{'layer': l, 'type': t, 'count': n} for (l, t), n in geo.unreadable.items()],
                 unbound_xrefs=sum(geo.xrefs.values()),
                 excluded_layers=sorted(l for l in geo.layer_types
                                        if (mapping.get(l) or suggest(l))['include'] not in ('yes', 'check')))
        summary['drawings'][name] = s
        if not quiet:
            big = s['structures']['largest'][:1]
            print(f"{name}: {len(recs)} records, grid {s['grid_deg_east_of_north']} deg, {how}, "
                  f"{s['structures']['count']} structure(s)"
                  + (f" (largest {big[0]['size_m'][0]:g} x {big[0]['size_m'][1]:g} m)" if big else '')
                  + (f", {sum(u['count'] for u in s['unreadable'])} unreadable solid(s)" if s['unreadable'] else ''))
            warn_xrefs(name, geo)
    for i, r in enumerate(records):
        r['id'] = f'M{i + 1:05d}'
    with open(out_dir / 'measurements.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows({k: r[k] for k in COLUMNS} for r in records)
    unconfirmed = sorted({r['layer'] for r in records if not r['confirmed']})
    summary['records'] = len(records)
    summary['unconfirmed_layers'] = unconfirmed
    (out_dir / 'summary.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding='utf-8')
    if alias_key:
        Path(alias_key).parent.mkdir(parents=True, exist_ok=True)
        with open(alias_key, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerows([('alias', 'file'), *aliases.items()])
    if not quiet:
        print(f'{len(records)} records -> {out_dir / "measurements.csv"}')
        if unconfirmed:
            print(f'{len(unconfirmed)} layer(s) still unconfirmed in the layer map: {", ".join(unconfirmed[:6])}'
                  + (' ...' if len(unconfirmed) > 6 else ''))
    return records, summary


def cmd_extract(args):
    extract(args.drawings, read_map(args.layers), args.output, args.units, args.grid, args.tol_deg, args.alias_key,
            join=args.join)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cmd_freeze(args):
    with open(args.measurements, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    unconfirmed = sorted({r['layer'] for r in rows if r['confirmed'] != '1'})
    if unconfirmed and not args.allow_unconfirmed:
        sys.exit(f'Not frozen: {len(unconfirmed)} layer(s) have not been confirmed in the layer map '
                 f'({", ".join(unconfirmed[:6])}). Confirm them, re-run extract, then freeze.')
    manifest = {
        'frozen_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'tool': f'cadmeasure {VERSION}',
        'sha256': {'measurements.csv': sha256(args.measurements), 'cadmeasure.py': sha256(__file__),
                   **({'layer_map.csv': sha256(args.layers)} if args.layers else {})},
        'records': len(rows),
        'by_element': dict(Counter(r['element'] or '?' for r in rows).most_common()),
        'by_kind': dict(Counter(r['kind'] for r in rows)),
        'unconfirmed_layers': unconfirmed,
    }
    out = Path(args.output or Path(args.measurements).with_name('freeze.json'))
    out.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding='utf-8')
    print(f"Frozen {len(rows)} records. measurements.csv sha256 {manifest['sha256']['measurements.csv']}")
    print(f'Post {out.name} with the pre-registration before any unit statistics are run.')


# What goes into the unit test. Not every record: the same design length is seen by many edges, and a
# whole wall's length is the sum of the rooms and walls along it, so counting everything makes the noise
# bar meaningless (in made-up drawings with no unit at all it found a "unit" every time). Gaps between
# neighbouring parallel lines and heights are close to the builders' primitive choices; each distinct
# value counts once per drawing. That helps but is not enough: `selftest` still measures false alarms
# well above the bar's nominal rate, because a few design choices drive many of these lengths too.
TEST_SET = (('gap', 'u'), ('gap', 'v'), ('edge', 'z'))


def analysis_values(records, pairs=TEST_SET, lo=1.0, hi=40.0, distinct=0.01,
                    drop_conditions=('hypothetical', 'reconstructed', 'modern', 'eroded', 'restored')):
    by = defaultdict(list)
    for r in records:
        x = float(r['value_m'])
        if (r['kind'], r['axis']) in pairs and lo <= x <= hi and r['condition'] not in drop_conditions:
            by[(r['drawing'], r.get('structure', ''), r['kind'], r['axis'])].append(x)
    out = []
    for xs in by.values():
        keep = []
        for x in sorted(xs):
            if not keep or x - keep[-1] > distinct:
                keep.append(x)
        out += keep
    return np.array(out)


def trial(unit, seed, quanto, make_set):
    """One made-up drawing set through the whole chain; returns the quantogram verdict."""
    with tempfile.TemporaryDirectory() as tmp:
        truth = make_set(tmp, unit=unit, seed=seed)
        drawings = sorted(Path(tmp).glob('*.dxf'))
        mapping = {}
        for path in drawings:
            _, geo = read_drawing(path)
            for layer in geo.layer_types:                       # stands in for the archaeologist's confirmation
                mapping[layer] = {**suggest(layer), 'confirmed': True}
        records, summary = extract(drawings, mapping, Path(tmp) / 'out', quiet=True)
    missing = [f'{name}.{key}' for name, t in truth.items() for key, v in t.items()
               if key not in ('units', 'grid_deg')
               and not any(r['drawing'] == f'{name}.dxf' and abs(r['value_m'] - v) <= 0.02 for r in records)]
    grids_ok = all(abs(summary['drawings'][f'{n}.dxf']['grid_deg_east_of_north'] - t['grid_deg']) < 0.1
                   for n, t in truth.items())
    xs = analysis_values(records)
    qs = quanto.grid(0.5, 2.0, xs.max())
    pk = quanto.peak(qs, quanto.scan(xs, qs))
    th = quanto.threshold(xs, qs)
    found = pk['phi'] > th['p99'] and (unit is None or abs(pk['q'] - unit) / unit < 0.01)
    return {'found': found, 'n': len(xs), 'records': len(records), 'missing': missing, 'grids_ok': grids_ok, **pk, **th}


def cmd_selftest(args):
    """Made-up drawings with a known unit (and with none) -> reader -> quantogram. Never touches real data."""
    import quanto
    from synth_cad import make_set
    seeds = range(1, args.seeds + 1)
    runs = {unit: [trial(unit, s, quanto, make_set) for s in seeds] for unit in (0.83, 0.805, None)}
    every = [r for rs in runs.values() for r in rs]
    missing = sorted({m for r in every for m in r['missing']})
    reader_ok = not missing and all(r['grids_ok'] for r in every)
    print(f'Reader: {len(every)} drawing sets, {every[0]["records"]} records each; '
          + ('every designed length found within 2 cm, grids and units undone' if reader_ok
             else f'MISSED {", ".join(missing)}'))
    found = {unit: sum(r['found'] for r in rs) for unit, rs in runs.items()}
    power_ok = all(found[u] >= 0.9 * args.seeds for u in (0.83, 0.805))
    for unit in (0.83, 0.805):
        print(f'Unit {unit} m: recovered in {found[unit]} of {args.seeds} sets '
              f"(peak {runs[unit][0]['q']:.4f} m, phi {runs[unit][0]['phi']:.1f} vs 1% bar {runs[unit][0]['p99']:.1f})")
    rate = found[None] / args.seeds
    print(f'No unit: the 1% noise bar was crossed in {found[None]} of {args.seeds} sets ({rate:.0%}).')
    if rate > 0.03:
        print('  The noise bar is NOT calibrated for lengths read off drawings: they share design choices, and the\n'
              '  bar assumes independent lengths. Do not call a real-data peak significant on this bar.')
    print('Reader and unit recovery: ' + ('PASS' if reader_ok and power_ok else 'FAIL'))
    sys.exit(0 if reader_ok and power_ok else 1)


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('layers', help='list layers with suggested element types into a CSV to confirm')
    a.add_argument('drawings', nargs='+')
    a.add_argument('-o', '--output', default='layer_map.csv')
    a.set_defaults(fn=cmd_layers)
    a = sub.add_parser('extract', help='measure drawings into measurements.csv and summary.json')
    a.add_argument('drawings', nargs='+')
    a.add_argument('--layers', help='confirmed layer map (from the layers command)')
    a.add_argument('-o', '--output', default='out')
    a.add_argument('--units', choices=sorted(UNIT_NAMES), help='drawing units, if $INSUNITS is missing or wrong')
    a.add_argument('--grid', type=float, help='grid orientation in degrees east of north (default: detect per drawing)')
    a.add_argument('--tol-deg', type=float, default=3.0, help='how far off the grid an edge may lie (degrees)')
    a.add_argument('--alias-key', help='name drawings D01, D02 ... and write the key to this private file')
    a.add_argument('--join', type=float, default=2.0,
                   help='split each drawing into structures: drawn pieces within about this many metres of each '
                        'other belong together, and lines are only paired within one structure (0 = keep each '
                        'drawing whole; default 2)')
    a.set_defaults(fn=cmd_extract)
    a = sub.add_parser('freeze', help='hash the measurements for pre-registration')
    a.add_argument('measurements')
    a.add_argument('--layers')
    a.add_argument('-o', '--output')
    a.add_argument('--allow-unconfirmed', action='store_true')
    a.set_defaults(fn=cmd_freeze)
    a = sub.add_parser('selftest', help='prove the reader on made-up drawings with a known unit')
    a.add_argument('--seeds', type=int, default=20, help='how many made-up drawing sets of each kind')
    a.set_defaults(fn=cmd_selftest)
    args = p.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
