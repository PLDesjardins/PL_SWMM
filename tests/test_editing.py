"""Edits must preserve source revisions and be runnable by the actual solver."""
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, editing

DEMO = Path('examples/riverside.inp').read_text()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    with TestClient(main.app) as client:
        yield client


def get_rows(client, model, section, item):
    return client.get(f"/api/models/{model['id']}/editor", params={'section': section, 'item': item}).json()['rows']


def save(client, model, changes):
    return client.post(f"/api/models/{model['id']}/edits", json={'changes': changes})


def change(section, item, rows, create=False):
    return {'section': section, 'id': item, 'rows': rows, 'create': create}


def run(client, model):
    response = client.post(f"/api/models/{model['id']}/runs")
    assert response.status_code == 202, response.text
    result = client.get('/api/runs/' + response.json()['id']).json()
    assert result['status'] == 'completed', result
    return result


def test_edit_parameters_curves_and_series_then_real_simulation(client):
    original = client.post('/api/models/demo').json()
    first_run = run(client, original)
    junction = get_rows(client, original, 'JUNCTIONS', 'J1')
    junction[0][1] = '4'
    conduit = get_rows(client, original, 'CONDUITS', 'C1')
    conduit[0][3] = '0.018'
    series = get_rows(client, original, 'TIMESERIES', 'Storm')
    for row in series:
        row[-1] = str(float(row[-1]) * 2)
    response = save(client, original, [change('JUNCTIONS', 'J1', junction), change('CONDUITS', 'C1', conduit),
        change('TIMESERIES', 'Storm', series), change('TIMESERIES', 'NewRain', [['01/01/2025', '00:00', '0'], ['01/01/2025', '01:00', '20']], True),
        change('CURVES', 'Stage', [['TIDAL', '0', '0'], ['6', '1']], True),
        change('OUTFALLS', 'O1', [['4', 'TIDAL', 'Stage', 'NO']]),
        change('LOSSES', 'C1', [['0.1', '0.2', '0', 'NO', '0']])])
    assert response.status_code == 201, response.text
    revision = response.json()
    assert revision['id'] != original['id']
    assert revision['parent_id'] == original['id']
    assert revision['revision'] == 1
    assert revision['edit_validation'] == 'engine_checked'
    assert get_rows(client, revision, 'CONDUITS', 'C1') == conduit
    assert client.get(f"/api/models/{original['id']}/input").text == DEMO
    assert client.get(f"/api/models/{revision['id']}/runs").json() == []
    assert client.get(f"/api/runs/{first_run['id']}").json() == first_run
    result = run(client, revision)
    assert result['summary']['peak_link_flow'] > first_run['summary']['peak_link_flow'] * 1.2
    series[0][-1] = '1'
    second = save(client, revision, [change('TIMESERIES', 'Storm', series)]).json()
    assert second['revision'] == 2
    assert second['parent_id'] == revision['id']


@pytest.mark.parametrize('edit', [
    change('XSECTIONS', 'C1', [['CIRCULAR', '-2', '0', '0', '0', '1']]),
    change('CONDUITS', 'C1', [['Missing', 'J2', '210', '0.013', '0', '0']]),
    change('CURVES', 'BadCurve', [['STORAGE', '2', '10'], ['1', '20']], True),
    change('TIMESERIES', 'BadSeries', [['01/01/2025', '01:00', '2'], ['01/01/2025', '00:00', '3']], True),
    change('JUNCTIONS', 'Renamed', [['12', '3']]),
    change('CURVES', 'Storm;bad', [['STORAGE', '0', '0']], True),
    change('JUNCTIONS', 'J1', [['12\n[FILES]', '3']]),
    change('JUNCTIONS', 'J1', []),
])
def test_invalid_edits_are_atomic(client, edit):
    original = client.post('/api/models/demo').json()
    response = save(client, original, [edit])
    assert response.status_code == 422, response.text
    assert len(client.get('/api/models').json()) == 1
    assert client.get(f"/api/models/{original['id']}/input").text == DEMO
    assert client.get('/api/health').json()['engine_code'] == 52004


def test_all_primary_types_and_related_sections_are_editable(client):
    # All SWMM node/link types connected to the demo network; the revised
    # model must be input-checked and complete a real hydraulic simulation.
    text = DEMO + '''
[STORAGE]
ST 6 4 0 FUNCTIONAL 100 0 0 0 0
[DIVIDERS]
DV 6 DC OVERFLOW 4 0 0 0
[CONDUITS]
DC DV J4 100 0.013 0 0 0 0
[XSECTIONS]
DC CIRCULAR 0.5 0 0 0 1
[PUMPS]
P ST J4 PC OFF 0 0
[CURVES]
PC PUMP3 0 0.01
PC 10 0
[ORIFICES]
OR J4 ST SIDE 0 0.6 NO 0
[WEIRS]
W ST J4 TRANSVERSE 1 1.8 NO 0 0 NO
[OUTLETS]
OT J3 ST 0 FUNCTIONAL/DEPTH 0.1 1.5 NO
[XSECTIONS]
OR RECT_CLOSED 0.5 0.5 0 0
W RECT_OPEN 1 1 0 0
'''
    original = client.post('/api/models', files={'file': ('types.inp', text)}).json()
    sections = ['JUNCTIONS', 'OUTFALLS', 'STORAGE', 'DIVIDERS', 'CONDUITS', 'PUMPS', 'ORIFICES', 'WEIRS', 'OUTLETS', 'SUBCATCHMENTS', 'RAINGAGES']
    names = ['J1', 'O1', 'ST', 'DV', 'C1', 'P', 'OR', 'W', 'OT', 'S1', 'RG1']
    changes = []
    for section, name in zip(sections, names):
        rows = get_rows(client, original, section, name)
        # Change a physical parameter on each type, then run the combined model.
        numeric = next(i for i, token in enumerate(rows[0]) if token.replace('.', '', 1).isdigit())
        rows[0][numeric] = str(float(rows[0][numeric]) + 0.1)
        changes.append(change(section, name, rows))
    response = save(client, original, changes)
    assert response.status_code == 201, response.text
    revised = response.json()
    for section, name in zip(sections, names):
        assert get_rows(client, revised, section, name) == next(c['rows'] for c in changes if c['section'] == section)
    run(client, revised)


def test_preserves_comments_and_other_sections():
    text = DEMO.replace('J1 12 3 0 0 0', 'J1 12 3 0 0 0 ; original note')
    changed = editing.edit(text, [main.ModelChange(**change('JUNCTIONS', 'J1', [['12', '4', '0', '0', '0']]))])
    assert changed == text.replace('J1 12 3 0 0 0', 'J1 12 4 0 0 0')
    assert editing.edit(text, [main.ModelChange(**change('CURVES', 'New', [['STORAGE', '0', '0'], ['1', '2']], True))]).startswith(text)


def test_missing_assets_allow_drafts_and_replacing_file_series(client):
    text = '\n'.join(line for line in DEMO.split('\n') if not line.startswith('Storm '))
    text = text.replace('[TIMESERIES]', '[TIMESERIES]\nStorm FILE "C:\\models\\rain.dat"')
    original = client.post('/api/models', files=[('file', ('draft.inp', text)), ('companions', ('draft.chi', '[General]\nMap EPSG=4326'))]).json()
    draft = save(client, original, [change('JUNCTIONS', 'J1', [['12', '4', '0', '0', '0']])]).json()
    assert draft['edit_validation'] == 'pending_missing_files'
    assert draft['missing_files'] == ['rain.dat']
    assert draft['crs'] == 'EPSG:4326'
    assert draft['crs_source'] == original['crs_source']
    rain_rows = editing.groups(DEMO)['TIMESERIES']['Storm']
    fixed = save(client, draft, [change('TIMESERIES', 'Storm', rain_rows)]).json()
    assert fixed['can_run']
    assert fixed['edit_validation'] == 'engine_checked'
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/models/{fixed['id']}/bundle").content)) as bundle:
        assert bundle.read('draft.chi') == b'[General]\nMap EPSG=4326'
        assert 'Storm FILE' not in bundle.read('draft.inp').decode()
    run(client, fixed)


def test_duplicate_creation_and_unknown_section_rejected(client):
    model = client.post('/api/models/demo').json()
    assert save(client, model, [change('TIMESERIES', 'storm', [['00:00', '1']], True)]).status_code == 422
    assert save(client, model, [change('FILES', 'SAVE', [['HOTSTART', 'x']])]).status_code == 422
    assert client.get(f"/api/models/{model['id']}/editor", params={'section': 'FILES', 'item': 'x'}).status_code == 422


def test_edited_external_series_retains_uploaded_assets(client):
    rain = '\n'.join(line[6:] for line in DEMO.splitlines() if line.startswith('Storm '))
    text = '\n'.join(line for line in DEMO.split('\n') if not line.startswith('Storm '))
    text = text.replace('[TIMESERIES]', '[TIMESERIES]\nStorm FILE "C:\\project\\rain.dat"')
    model = client.post('/api/models', files=[('file', ('external.inp', text)), ('companions', ('rain.dat', rain))]).json()
    revised = save(client, model, [change('JUNCTIONS', 'J1', [['12', '4', '0', '0', '0']])])
    assert revised.status_code == 201, revised.text
    revised = revised.json()
    assert revised['edit_validation'] == 'engine_checked'
    assert revised['can_run']
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/models/{revised['id']}/bundle").content)) as bundle:
        assert bundle.read('rain.dat').decode() == rain
        assert 'C:\\project\\rain.dat' in bundle.read('external.inp').decode()
    assert run(client, revised)['summary']['peak_link_flow'] > 0.1


def test_routing_initialization_errors_prevent_saving(client):
    model = client.post('/api/models/demo').json()
    # Input syntax is valid, but two pipes into a terminal outfall are not.
    # swmm_start must be checked as well as swmm_open.
    response = save(client, model, [change('CONDUITS', 'C3', [['J3', 'O1', '220', '0.013', '0', '0', '0', '0']])])
    assert response.status_code == 422
    assert 'ERROR 141' in response.json()['detail']
    assert len(client.get('/api/models').json()) == 1
