import io
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

DEMO = Path(__file__).resolve().parents[1] / 'examples/riverside.inp'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'data')
    with TestClient(main.app) as client:
        yield client


def external_model():
    lines = DEMO.read_text().splitlines()
    rainfall = '\n'.join(line.split(' ', 1)[1] for line in lines if line.startswith('Storm ')).encode()
    model = '\r\n'.join(line for line in lines if not line.startswith('Storm '))
    model = model.replace('[TIMESERIES]', '[TIMESERIES]\r\nStorm FILE "C:\\Projects\\Données\\rain.dat"')
    model = model.replace('Riverside demo', 'Modélisation Québec')
    return model.encode('cp1252'), rainfall


def test_pcswmm_missing_rain_can_open_attach_then_run_and_preserve_originals(client):
    original, rainfall = external_model()
    chi = b'[General]\r\nMap EPSG=4326\r\n'
    ini = b'[General]\r\nMap EPSG=26918\r\n'
    files = [('file', ('sample.inp', original)), ('companions', ('sample.chi', chi)), ('companions', ('sample.ini', ini)), ('companions', ('sample.thm', b'original thumbnail'))]
    response = client.post('/api/models', files=files)
    assert response.status_code == 201, response.text
    model = response.json()
    assert model['source_encoding'] == 'Windows-1252'
    assert model['crs'] == 'EPSG:4326'
    assert model['crs_source'] == 'sample.chi'
    assert 'EPSG:26918' in model['warnings'][0]
    assert model['missing_files'] == ['rain.dat']
    assert model['counts'] == {'nodes': 5, 'links': 4, 'subcatchments': 3}
    assert client.get(f"/api/models/{model['id']}/input").content == original
    assert client.post(f"/api/models/{model['id']}/runs").status_code == 422
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/models/{model['id']}/bundle").content)) as bundle:
        assert bundle.read('sample.inp') == original
        assert bundle.read('sample.chi') == chi
        assert bundle.read('sample.ini') == ini
    added = client.post(f"/api/models/{model['id']}/companions", files=[('companions', ('RAIN.DAT', rainfall))])
    assert added.status_code == 200, added.text
    revised = added.json()
    assert revised['id'] != model['id']
    assert revised['missing_files'] == []
    assert revised['crs'] == model['crs']
    assert client.get(f"/api/models/{model['id']}").json()['missing_files'] == ['rain.dat']
    run_id = client.post(f"/api/models/{revised['id']}/runs").json()['id']
    run = client.get(f'/api/runs/{run_id}').json()
    if run['status'] != 'completed':
        pytest.fail(client.get(f'/api/runs/{run_id}/files/report').text)
    assert run['summary']['engine'] == 52004
    assert run['summary']['periods'] == 72
    assert run['summary']['peak_link_flow'] > 0.1
    run_input = client.get(f'/api/runs/{run_id}/files/input').text
    assert 'C:\\Projects' not in run_input
    assert 'assets/' in run_input
    series = client.get(f'/api/runs/{run_id}/series', params={'kind': 'links', 'element': 'C4', 'metric': 'flow'}).json()
    assert max(series['values']) > 0.1
    baseline = client.post('/api/models/demo').json()
    baseline_run = client.post(f"/api/models/{baseline['id']}/runs").json()['id']
    baseline_series = client.get(f'/api/runs/{baseline_run}/series', params={'kind': 'links', 'element': 'C4', 'metric': 'flow'}).json()
    assert series['values'] == pytest.approx(baseline_series['values'])


def test_database_crs_is_read_without_importing_old_results(client, tmp_path):
    path = tmp_path / 'sample.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE Settings(Name TEXT, Value TEXT)')
        db.execute("INSERT INTO Settings VALUES('Map EPSG','4326')")
        db.execute('CREATE TABLE OldResults(Flow REAL)')
        db.execute('INSERT INTO OldResults VALUES(99999)')
    response = client.post('/api/models', files=[('file', ('sample.inp', DEMO.read_bytes())), ('companions', ('sample.db', path.read_bytes()))])
    model = response.json()
    assert model['crs'] == 'EPSG:4326'
    assert model['crs_source'] == 'sample.db'
    assert client.get(f"/api/models/{model['id']}/runs").json() == []


def test_explicit_crs_overrides_companion_metadata(client):
    response = client.post('/api/models', data={'crs': 'EPSG:4326'}, files=[('file', ('sample.inp', DEMO.read_bytes())), ('companions', ('sample.chi', b'[General]\nMap EPSG=32187'))])
    assert response.json()['crs_source'] == 'Entered on import'
    assert 'EPSG:32187' in response.json()['warnings'][0]


def test_duplicate_companion_names_are_rejected(client):
    response = client.post('/api/models', files=[('file', ('sample.inp', DEMO.read_bytes())), ('companions', ('rain.dat', b'a')), ('companions', ('RAIN.DAT', b'b'))])
    assert response.status_code == 422
    assert 'unique' in response.json()['detail']


def test_ambiguous_external_filenames_are_rejected(client):
    original, _ = external_model()
    original += b'\r\n[TIMESERIES]\r\nOther FILE "C:\\OtherProject\\rain.dat"\r\n'
    response = client.post('/api/models', files={'file': ('sample.inp', original)})
    assert response.status_code == 422
    assert 'distinct filenames' in response.json()['detail']
