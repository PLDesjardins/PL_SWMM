import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.model import parse_model

DEMO = Path(__file__).resolve().parents[1] / "examples/riverside.inp"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    with TestClient(main.app) as client:
        yield client


def test_real_swmm_simulation_and_exports(client):
    assert client.get("/api/health").json()["engine_code"] == 52004
    model = client.post("/api/models/demo").json()
    assert model["counts"] == {"nodes": 5, "links": 4, "subcatchments": 3}
    response = client.post(f"/api/models/{model['id']}/runs")
    assert response.status_code == 202
    run_id = response.json()["id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed", run
    assert run["summary"]["engine"] == 52004
    assert run["summary"]["periods"] == 72
    assert run["summary"]["peak_link_flow"] > 0.1
    assert run["summary"]["units"]["flow"] == "CMS"
    for kind, element, metric in [("links", "C4", "flow"), ("nodes", "J2", "depth"), ("subcatchments", "S1", "runoff")]:
        query = {"kind": kind, "element": element, "metric": metric}
        data = client.get(f"/api/runs/{run_id}/series", params=query).json()
        assert len(data["values"]) == len(data["timestamps"]) == 72
        assert max(data["values"]) > 0
        assert data["timestamps"][0] == "2025-01-01T00:05:00"
        assert data["timestamps"][-1] == "2025-01-01T06:00:00"
        rows = list(csv.DictReader(io.StringIO(client.get(f"/api/runs/{run_id}/series", params={**query, "format": "csv"}).text)))
        assert len(rows) == 72
        assert float(rows[20]["value"]) == data["values"][20]
    report = client.get(f"/api/runs/{run_id}/files/report")
    assert "Build 5.2.4" in report.text
    assert "Flow Routing Continuity" in report.text
    assert "ERROR" not in report.text
    assert len(client.get(f"/api/runs/{run_id}/files/output").content) > 1000
    assert client.get(f"/api/runs/{run_id}/files/input").text == DEMO.read_text()
    geojson = client.get(f"/api/models/{model['id']}/geojson").json()
    assert len(geojson["features"]) == 12
    assert geojson["features"][0]["geometry"]["coordinates"] == [-73.573, 45.509]
    assert client.get(f"/api/models/{model['id']}/runs").json()[0]["id"] == run_id
    assert client.get(f"/api/runs/{run_id}/series", params={"kind": "links", "element": "missing", "metric": "flow"}).status_code == 422


def test_import_preserves_input_and_requires_crs_for_export(client):
    data = DEMO.read_bytes()
    response = client.post("/api/models", files={"file": ("sample.inp", data)})
    assert response.status_code == 201
    model = response.json()
    assert model["network"]["map_mode"] == "local"
    assert client.get(f"/api/models/{model['id']}/input").content == data
    assert client.get(f"/api/models/{model['id']}/geojson").status_code == 422
    assert client.post("/api/models", files={"file": ("sample.inp", data)}, data={"crs": "EPSG:bogus"}).status_code == 422


def test_invalid_model_reports_engine_error_without_crashing_server(client):
    text = DEMO.read_text().replace("CIRCULAR 0.6", "CIRCULAR -2")
    model = client.post("/api/models", files={"file": ("invalid.inp", text)}).json()
    run_id = client.post(f"/api/models/{model['id']}/runs").json()["id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert "ERROR" in client.get(f"/api/runs/{run_id}/files/report").text
    assert "ERROR" in client.get(f"/api/runs/{run_id}/files/log").text
    assert client.get(f"/api/runs/{run_id}/series", params={"kind": "links", "element": "C1", "metric": "flow"}).status_code == 409
    assert client.get("/api/health").status_code == 200


def test_external_paths_and_oversized_inputs_rejected(client):
    text = DEMO.read_text() + '\n[FILES]\nUSE HOTSTART "/etc/passwd"\n'
    response = client.post("/api/models", files={"file": ("unsafe.inp", text)})
    assert response.status_code == 422
    assert "External FILE" in response.json()["detail"]
    assert client.post("/api/models", files={"file": ("big.inp", b"x" * (main.MAX_UPLOAD + 1))}).status_code == 413
    assert client.get("/api/models/not-a-valid-id").status_code == 404
    assert client.get("/api/models").json() == []


@pytest.mark.parametrize("addition", [
    '[FILE]\nSAVE OUTFLOWS "/tmp/unsafe.txt"',
    '[FILES] trailing text\nUSE HOTSTART "/etc/passwd"',
    '[TITLE]\n"[FILES]"\nSAVE OUTFLOWS "/tmp/unsafe.txt"',
    '[LID_USAGE]\nS1 L1 1 100 10 0 0 0 "/tmp/unsafe.txt"',
    '[TEMPERATURE]\nFILE "/etc/passwd"',
    '[RAINGAGES]\nRG2 INTENSITY 0:05 1.0 FILE "/etc/passwd" STA MM',
    '[TIMESERIES]\nRain FILE "/etc/passwd"',
])
def test_all_engine_external_file_routes_are_rejected(client, addition):
    response = client.post("/api/models", files={"file": ("unsafe.inp", DEMO.read_text() + '\n' + addition)})
    assert response.status_code == 422
    assert "unsupported" in response.json()["detail"]


def test_projected_gis_coordinates_are_transformed():
    network = parse_model('[JUNCTIONS]\nJ1 0 2\n[COORDINATES]\nJ1 500000 0\n', 'EPSG:32618')
    lon, lat = network["nodes"][0]["coordinate"]
    assert lon == pytest.approx(-75)
    assert lat == pytest.approx(0)


def test_interrupted_run_is_marked_failed_on_startup(tmp_path, monkeypatch):
    path = tmp_path / "runs" / ("a" * 32)
    path.mkdir(parents=True)
    (path / "meta.json").write_text(json.dumps({"id": "a" * 32, "status": "running"}))
    monkeypatch.setattr(main, "DATA", tmp_path)
    with TestClient(main.app) as client:
        run = client.get('/api/runs/' + 'a' * 32).json()
        assert run["status"] == "failed"
        assert "restarted" in run["error"]
