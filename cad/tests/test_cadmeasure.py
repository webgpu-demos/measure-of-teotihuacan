"""Tests on made-up drawings whose every designed length is known. Run: python -m pytest cad/tests"""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import cadmeasure as cm  # noqa: E402
import quanto  # noqa: E402
from synth_cad import ORIGIN, Drafter, make_set  # noqa: E402


def confirmed_map(drawings):
    mapping = {}
    for path in drawings:
        _, geo = cm.read_drawing(path)
        for layer in geo.layer_types:
            mapping[layer] = {**cm.suggest(layer), 'confirmed': True}
    return mapping


@pytest.fixture(scope='module')
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('drawings')
    truth = make_set(str(tmp), unit=0.83, seed=3)
    drawings = sorted(tmp.glob('*.dxf'))
    records, summary = cm.extract(drawings, confirmed_map(drawings), tmp / 'out', quiet=True)
    return {'tmp': tmp, 'truth': truth, 'drawings': drawings, 'records': records, 'summary': summary}


def near(records, drawing, value, tol=0.02, **match):
    return [r for r in records if r['drawing'] == drawing and abs(r['value_m'] - value) <= tol
            and all(r[k] == v for k, v in match.items())]


def test_grid_and_units_are_undone(run):
    s = run['summary']['drawings']
    assert s['estructura_1.dxf']['grid_deg_east_of_north'] == pytest.approx(15.5, abs=0.1)
    assert s['conjunto_2.dxf']['grid_deg_east_of_north'] == pytest.approx(15.9, abs=0.1)
    assert s['conjunto_2.dxf']['units'] == '$INSUNITS=5'           # centimetres, converted to metres


@pytest.mark.parametrize('drawing,key,match', [
    ('estructura_1.dxf', 'base_u', {'layer': 'PLATAFORMA TALUD', 'kind': 'edge', 'axis': 'u'}),
    ('estructura_1.dxf', 'base_v', {'layer': 'PLATAFORMA TALUD', 'kind': 'edge', 'axis': 'v'}),
    ('estructura_1.dxf', 'talud_h', {'layer': 'PLATAFORMA TALUD', 'kind': 'extent', 'axis': 'z'}),
    ('estructura_1.dxf', 'tablero_h', {'layer': 'PLATAFORMA TABLERO', 'kind': 'edge', 'axis': 'z'}),
    ('estructura_1.dxf', 'stair_w', {'layer': 'ESCALINATA', 'kind': 'edge', 'axis': 'u'}),
    ('estructura_1.dxf', 'alfarda_w', {'layer': 'ALFARDA', 'kind': 'gap', 'axis': 'u'}),
    ('conjunto_2.dxf', 'patio_w', {'layer': 'PISO ESTUCO', 'kind': 'edge'}),
    ('conjunto_2.dxf', 'wall_t', {'layer': 'FASE 2 MUROS', 'kind': 'gap'}),
    ('conjunto_2.dxf', 'room_d', {'layer': 'FASE 2 MUROS', 'kind': 'gap'}),
    ('conjunto_2.dxf', 'wall_h', {'layer': 'FASE 2 MUROS', 'kind': 'edge', 'axis': 'z'}),
    ('conjunto_2.dxf', 'pillar_w', {'layer': 'PILARES', 'kind': 'extent'}),
    ('conjunto_2.dxf', 'pillar_gap', {'layer': 'PILARES', 'kind': 'gap', 'axis': 'u'}),
])
def test_designed_lengths_come_back(run, drawing, key, match):
    hits = near(run['records'], drawing, run['truth'][drawing.removesuffix('.dxf')][key], **match)
    assert hits, f'{key} not found'


def test_parallel_copies_count_once(run):
    stair = near(run['records'], 'estructura_1.dxf', run['truth']['estructura_1']['stair_w'],
                 layer='ESCALINATA', kind='edge', axis='u')
    assert len(stair) == 1 and stair[0]['copies'] > 10           # every step edge, one stair width


def test_triangulation_and_tiling_edges_are_dropped(run):
    layers = run['summary']['drawings']['estructura_1.dxf']['layers']
    assert layers['PLATAFORMA TABLERO']['oblique_segments'] == 0   # every quad was split into two triangles
    floor = run['summary']['drawings']['conjunto_2.dxf']['layers']['PISO ESTUCO']
    assert floor['segments'] == 12                                  # nine tiles: only the outline's 12 pieces remain


def test_entity_types(run):
    s = run['summary']['drawings']
    read = {**s['estructura_1.dxf']['entities_read'], **s['conjunto_2.dxf']['entities_read']}
    for t in ('LINE', 'LWPOLYLINE', 'POLYLINE', '3DFACE', 'MESH', 'INSERT'):
        assert read.get(t), t
    assert s['estructura_1.dxf']['unreadable'] == [{'layer': 'ESCULTURA', 'type': '3DSOLID', 'count': 1}]
    assert {'CIRCLE', 'ARC', 'TEXT', 'DIMENSION'} <= set(s['estructura_1.dxf']['skipped'])
    assert not s['estructura_1.dxf']['failed'] and not s['conjunto_2.dxf']['failed']


def test_minsert_copies_are_separate_elements(run):
    pillars = [r for r in run['records'] if r['layer'] == 'PILARES' and r['kind'] == 'extent' and r['axis'] == 'u']
    handles = ';'.join(r['handles'] for r in pillars)
    assert re.search(r'#3', handles)                                # the three-column MINSERT, one by one


def test_excluded_layers_never_reach_the_table(run):
    layers = {r['layer'] for r in run['records']}
    for bad in ('TEMPLO SUPERIOR - RECONSTRUCCION HIPOTETICA', 'SONDEO 2X2', 'RETICULA', 'CURVAS DE NIVEL', 'COTAS'):
        assert bad not in layers


def test_no_coordinates_leave_the_machine(run):
    out = run['tmp'] / 'out'
    text = (out / 'measurements.csv').read_text() + (out / 'summary.json').read_text()
    for c in ORIGIN[:2]:
        for scale in (1, 100):
            assert str(int(c * scale))[:5] not in text
    with open(out / 'measurements.csv') as f:
        rows = list(csv.DictReader(f))
        assert list(rows[0]) == cm.COLUMNS
    assert max(float(r['value_m']) for r in rows) < 100              # lengths of buildings, not positions


@pytest.mark.parametrize('name,expected', [
    ('ALFARDA', ('balustrade', '', '', 'yes')),
    ('Escalinata_poniente', ('stair', '', '', 'yes')),
    ('FASE 2 MUROS', ('wall', '', 'fase 2', 'yes')),
    ('Muros Tlamimilolpa', ('wall', '', 'tlamimilolpa', 'yes')),
    ('MURO EROSIONADO', ('wall', 'eroded', '', 'yes')),
    ('TEMPLO SUPERIOR - RECONSTRUCCION HIPOTETICA', ('room', 'hypothetical', '', 'no')),
    ('CUARTO RESTAURADO', ('room', 'restored', '', 'check')),
    ('SONDEO 2X2', ('excavation unit', '', '', 'no')),
    ('Curvas de nivel', ('contour', '', '', 'no')),
    ('0', ('unassigned', '', '', 'check')),
    ('XREF-mystery', ('', '', '', 'check')),
    # English and drafting names: modern lines out, reconstruction out, repairs flagged.
    ('EXC_LIMIT 03', ('excavation limit', '', '', 'no')),
    ('Limite de excavacion', ('excavation limit', '', '', 'no')),
    ('Looting pit', ('looting pit', '', '', 'no')),
    ('Muro saqueado', ('wall', 'eroded', '', 'yes')),
    ('Reconstructed facade', ('', 'reconstructed', '', 'no')),
    ('Wall reconstr.', ('wall', 'reconstructed', '', 'no')),          # abbreviated: must still stay out
    ('Modern wall', ('wall', 'modern', '', 'no')),
    ('Rocks', ('stone', '', '', 'check')),
    ('Wall restored', ('wall', 'restored', '', 'check')),
    ('Stone with cement', ('stone', 'restored', '', 'check')),
    ('Stucco', ('stucco', '', '', 'yes')),
    ('Stucco original 2', ('stucco', 'excavated', '', 'yes')),
    ('Stucco floor', ('floor', '', '', 'yes')),
    ('Stair eroded', ('stair', 'eroded', '', 'yes')),
])
def test_layer_suggestions(name, expected):
    s = cm.suggest(name)
    assert (s['element'], s['condition'], s['phase'], s['include']) == expected


def test_layer_map_round_trip_and_freeze(run, tmp_path):
    tool = [sys.executable, str(HERE / 'cadmeasure.py')]
    drawings = [str(p) for p in run['drawings']]
    lm = tmp_path / 'layer_map.csv'
    subprocess.run(tool + ['layers', *drawings, '-o', str(lm)], check=True, capture_output=True)
    rows = list(csv.DictReader(open(lm)))
    assert {r['layer'] for r in rows} >= {'ALFARDA', 'ESCULTURA', 'PILARES'}
    # Unconfirmed: freeze must refuse.
    subprocess.run(tool + ['extract', *drawings, '--layers', str(lm), '-o', str(tmp_path / 'a')], check=True, capture_output=True)
    r = subprocess.run(tool + ['freeze', str(tmp_path / 'a' / 'measurements.csv')], capture_output=True, text=True)
    assert r.returncode != 0 and 'Not frozen' in r.stderr
    # The archaeologist confirms every layer (here: accepts each suggestion); a rerun keeps the answers.
    for row in rows:
        row['element'], row['include'] = row['suggested_element'] or 'other', row['suggested_include']
    with open(lm, 'w', newline='') as f:
        w = csv.DictWriter(f, cm.MAP_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    subprocess.run(tool + ['layers', *drawings, '-o', str(lm)], check=True, capture_output=True)
    assert all(r['element'] for r in csv.DictReader(open(lm)))
    key = tmp_path / 'private' / 'drawing_key.csv'
    subprocess.run(tool + ['extract', *drawings, '--layers', str(lm), '-o', str(tmp_path / 'b'), '--alias-key', str(key)],
                   check=True, capture_output=True)
    table = (tmp_path / 'b' / 'measurements.csv').read_text()
    assert 'estructura_1' not in table and ',D01,' in table and 'estructura_1.dxf' in key.read_text()
    subprocess.run(tool + ['freeze', str(tmp_path / 'b' / 'measurements.csv'), '--layers', str(lm)], check=True, capture_output=True)
    manifest = json.loads((tmp_path / 'b' / 'freeze.json').read_text())
    assert manifest['sha256']['measurements.csv'] == cm.sha256(tmp_path / 'b' / 'measurements.csv')
    assert not manifest['unconfirmed_layers']


def test_counting_every_record_invents_a_unit():
    """Why the test set is restricted: with no unit in the drawings, all records together still 'find' one."""
    hits = 0
    for seed in range(1, 6):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            make_set(tmp, unit=None, seed=seed)
            drawings = sorted(Path(tmp).glob('*.dxf'))
            records, _ = cm.extract(drawings, confirmed_map(drawings), Path(tmp) / 'out', quiet=True)
        everything = np.array([r['value_m'] for r in records if 1 <= r['value_m'] <= 40 and r['axis'] in 'uvz'
                               and r['condition'] not in ('hypothetical', 'eroded', 'restored')])
        qs = quanto.grid(0.5, 2.0, everything.max())
        hits += quanto.peak(qs, quanto.scan(everything, qs))['phi'] > quanto.threshold(everything, qs)['p99']
    assert hits >= 4


@pytest.mark.parametrize('unit', [0.83, 0.805])   # 3% apart, as in the plan
def test_recovers_a_known_unit(unit):
    runs = [cm.trial(unit, s, quanto, make_set) for s in range(1, 6)]
    assert all(r['found'] and not r['missing'] for r in runs)


def test_noise_bar_is_right_for_independent_lengths():
    rng = np.random.default_rng(5)
    hits = 0
    for _ in range(100):
        xs = np.exp(rng.uniform(np.log(1), np.log(40), 20))
        qs = quanto.grid(0.5, 2.0, xs.max())
        hits += quanto.scan(xs, qs).max() > quanto.threshold(xs, qs)['p99']
    assert hits <= 3


@pytest.mark.xfail(strict=True, reason='Known: Kendall\'s noise bar assumes independent lengths; lengths read off '
                                       'drawings share design choices, so unit-free drawings cross the 1% bar in '
                                       'roughly one set in eight. A structure-aware null has to make this pass.')
def test_noise_bar_is_right_for_lengths_from_drawings():
    runs = [cm.trial(None, s, quanto, make_set) for s in range(101, 141)]
    assert sum(r['found'] for r in runs) <= 2


# A whole-site drawing: two separate buildings on the same layer --------------------------------

def site_drawing(path, gap=30.0, xref=False):
    """Two identical rooms 30 m apart on one wall layer, a survey grid across both, optionally an XREF."""
    d = Drafter(unit=0.83, seed=7, sigma=0.0, jitter=0.0)
    W, t, h = d.dim(6), d.dim(1), d.dim(3)
    for u0 in (0.0, gap):
        a, b = u0, u0 + 2 * t + W
        for wall in ((a, b, 0, t), (a, b, t + W, 2 * t + W), (a, a + t, 0, 2 * t + W), (b - t, b, 0, 2 * t + W)):
            d.rect('MUROS', *wall, thickness=h)
    for i in range(-2, 8):
        d.line('RETICULA', (i * 10.0, -20, 0), (i * 10.0, 40, 0))
    if xref:
        d.doc.add_xref_def('another_structure.dwg', 'ANOTHER_STRUCTURE')
        d.msp.add_blockref('ANOTHER_STRUCTURE', d.w(0, 50, 0, jit=False), dxfattribs=d.layer('MUROS'))
    d.doc.saveas(path)
    return {'room': W, 'wall': t, 'outer': W + 2 * t, 'gap': gap}


def test_whole_site_drawing_is_split_into_structures(tmp_path):
    truth = site_drawing(tmp_path / 'site.dxf')
    drawings = [tmp_path / 'site.dxf']
    mapping = confirmed_map(drawings)
    split, s1 = cm.extract(drawings, mapping, tmp_path / 'split', quiet=True)
    whole, s0 = cm.extract(drawings, mapping, tmp_path / 'whole', quiet=True, join=0)
    gaps = lambda rs: [r['value_m'] for r in rs if r['kind'] == 'gap']
    assert s1['drawings']['site.dxf']['structures']['count'] == 2           # the survey grid does not join them
    assert {r['structure'] for r in split} == {'S001', 'S002'}
    assert s0['drawings']['site.dxf']['structures']['count'] == 1
    # Kept whole, the space between the two buildings is measured as if it were a design dimension.
    between = truth['gap'] - truth['outer']
    assert any(abs(g - between) < 0.02 for g in gaps(whole))
    assert not any(abs(g - between) < 0.02 for g in gaps(split))
    assert max(gaps(split)) < truth['outer'] + 0.02
    # Room widths and wall thicknesses are still found, in each building.
    for key in ('room', 'wall'):
        for st in ('S001', 'S002'):
            assert near(split, 'site.dxf', truth[key], kind='gap', structure=st), (key, st)
    # The same width in two buildings is two pieces of evidence, not one (square rooms: once per axis).
    count = lambda rs: sum(abs(x - truth['room']) < 0.01 for x in cm.analysis_values(rs))
    assert count(whole) == 2 and count(split) == 4
    text = (tmp_path / 'split' / 'summary.json').read_text() + (tmp_path / 'split' / 'measurements.csv').read_text()
    for c in ORIGIN[:2]:
        assert str(int(c))[:5] not in text                                  # sizes only, no positions


def test_unbound_external_references_are_reported(tmp_path, capsys):
    site_drawing(tmp_path / 'site.dxf', xref=True)
    drawings = [tmp_path / 'site.dxf']
    _, summary = cm.extract(drawings, confirmed_map(drawings), tmp_path / 'out')
    s = summary['drawings']['site.dxf']
    assert s['unbound_xrefs'] == 1 and not s['failed']
    assert 'external reference(s) NOT read (ANOTHER_STRUCTURE)' in capsys.readouterr().out


@pytest.mark.parametrize('seed', [2, 6, 9])      # seeds whose terraces are wider than the join distance
def test_stepped_platform_stays_one_structure(tmp_path, seed):
    """In plan, each tier of a stepped platform is a ring separated from the next by an empty terrace."""
    make_set(str(tmp_path), unit=None, seed=seed)
    drawings = sorted(tmp_path.glob('*.dxf'))
    _, summary = cm.extract(drawings, confirmed_map(drawings), tmp_path / 'out', quiet=True)
    assert summary['drawings']['estructura_1.dxf']['structures']['count'] == 1


def test_layer_map_shows_each_layers_colour(tmp_path):
    """Teams often code meaning in colour; whoever confirms the layer map should see it."""
    import argparse
    d = Drafter(unit=0.83, seed=1)
    d.doc.layers.add('STUCCO A', color=7)
    d.doc.layers.add('PLATFORM B', color=5)
    d.doc.layers.add('LATER C').rgb = (40, 200, 40)
    d.doc.layers.add('OLD D', color=30).off()
    for name in ('STUCCO A', 'PLATFORM B', 'LATER C', 'OLD D'):
        d.line(name, (0, 0, 0), (5, 0, 0))
    d.msp.add_line(d.w(0, 1, 0), d.w(5, 1, 0), dxfattribs={'layer': 'STUCCO A', 'color': 1})   # overrides its layer
    d.doc.saveas(tmp_path / 'c.dxf')
    out = tmp_path / 'layer_map.csv'
    cm.cmd_layers(argparse.Namespace(drawings=[str(tmp_path / 'c.dxf')], output=str(out)))
    colour = {r['layer']: r['colour'] for r in csv.DictReader(open(out))}
    assert colour['STUCCO A'] == 'white; entities drawn in red (1)'
    assert colour['PLATFORM B'] == 'blue'
    assert colour['LATER C'] == 'green #28C828'
    assert colour['OLD D'] == 'orange #FF7F00 (off)'
