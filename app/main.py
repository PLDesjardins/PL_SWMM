import asyncio
import base64
import binascii
import csv
import io
import json
import os
import re
import shutil
import secrets
import sys
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from swmm.toolkit import solver

from app.model import as_geojson, parse_model
from app.results import series
from app.importing import basename, decode_text, detect_crs, prepare_model

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("PL_SWMM_DATA_DIR", ROOT / ".data"))
MAX_UPLOAD = 8 * 1024 * 1024
MAX_BUNDLE = 32 * 1024 * 1024
slots = asyncio.Semaphore(2)


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, allow_nan=False))
    temp.replace(path)


def folder(category, identifier):
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise HTTPException(404, "Unknown identifier.")
    path = DATA / category / identifier
    if not (path / "meta.json").is_file():
        raise HTTPException(404, "Not found.")
    return path


@asynccontextmanager
async def lifespan(app):
    if os.environ.get("PL_SWMM_REQUIRE_AUTH") == "1" and not os.environ.get("PL_SWMM_PASSWORD"):
        raise RuntimeError("Hosted mode requires PL_SWMM_PASSWORD. Set it in your hosting dashboard.")
    if solver.swmm_get_version() != 52004:
        raise RuntimeError("Wrong engine installed: expected EPA SWMM 5.2.4 (52004).")
    for name in ["models", "runs"]:
        (DATA / name).mkdir(parents=True, exist_ok=True)
    for path in (DATA / "runs").glob("*/meta.json"):
        meta = json.loads(path.read_text())
        if meta["status"] in {"queued", "running"}:
            meta.update(status="failed", error="Server restarted before the run completed.", finished_at=now())
            save_json(path, meta)
    yield


app = FastAPI(title="PL SWMM", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def protect_hosted_workspace(request: Request, call_next):
    # Health checks stay public. Everything else, including files and docs,
    # is protected when a password is set. Local use remains unchanged.
    password = os.environ.get("PL_SWMM_PASSWORD", "")
    required = os.environ.get("PL_SWMM_REQUIRE_AUTH") == "1"
    if request.url.path == "/api/health" and request.method in {"GET", "HEAD"}:
        return await call_next(request)
    if password or required:
        authorized = False
        try:
            scheme, encoded = request.headers.get("authorization", "").split(" ", 1)
            if scheme.lower() == "basic" and password:
                credentials = base64.b64decode(encoded, validate=True)
                expected = ("swmm:" + password).encode("utf-8")
                authorized = secrets.compare_digest(credentials, expected)
        except (ValueError, binascii.Error):
            pass
        if not authorized:
            return Response("Sign in with username swmm and your workspace password.", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="PL SWMM workspace", charset="UTF-8"', "Cache-Control": "no-store"})
    response = await call_next(request)
    if password or required:
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
app.mount("/vendor/leaflet", StaticFiles(directory=ROOT / "node_modules/leaflet/dist", check_dir=False), name="leaflet")


@app.get("/")
def index():
    return FileResponse(ROOT / "app/static/index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "engine": "5.2.4", "engine_code": solver.swmm_get_version()}


def add_model(text, name, crs, *, original=None, supplied_files=None):
    supplied_files = supplied_files or {}
    try:
        crs, crs_source, warnings = detect_crs(name, supplied_files, crs)
        network = parse_model(text, crs, allow_external=True)
        prepared, assets, missing = prepare_model(text, supplied_files)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    identifier = uuid.uuid4().hex
    path = DATA / "models" / identifier
    path.mkdir(parents=True)
    (path / "model.inp").write_bytes(original if original is not None else text.encode('utf-8'))
    (path / "prepared.inp").write_text(prepared, encoding='utf-8')
    for relative, data in assets.items():
        target = path / relative
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
    companions = []
    for filename, data in supplied_files.items():
        stored = 'companions/' + uuid.uuid4().hex
        target = path / stored
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        companions.append({'name': filename, 'stored': stored})
    meta = {"id": identifier, "name": name[:150], "created_at": now(), "crs": crs,
            "crs_source": crs_source, "warnings": warnings, "missing_files": missing, "can_run": not missing,
            "companions": companions, "source_encoding": decode_text(original)[1] if original else 'UTF-8',
            "counts": {kind: len(network[kind]) for kind in ["nodes", "links", "subcatchments"]}}
    save_json(path / "network.json", network)
    save_json(path / "meta.json", meta)
    return {**meta, "network": network}


@app.get("/api/models")
def models():
    return sorted([json.loads(p.read_text()) for p in (DATA / "models").glob("*/meta.json")], key=lambda m: m["created_at"], reverse=True)


@app.post("/api/models", status_code=201)
async def upload_model(file: UploadFile = File(...), crs: str = Form(""), companions: list[UploadFile] = File(default=[])):
    if not (file.filename or "").lower().endswith(".inp"):
        raise HTTPException(422, "Choose a SWMM .inp file.")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Model exceeds the 8 MiB upload limit.")
    try:
        name = basename(file.filename)
        text, _ = decode_text(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    supplied = await read_companions(companions, len(data), model_name=name)
    return add_model(text, name, crs.strip() or None, original=data, supplied_files=supplied)


async def read_companions(uploads, total=0, *, model_name=None):
    if len(uploads) > 64:
        raise HTTPException(413, 'Attach up to 64 companion files at once.')
    files = {}
    used = {model_name.casefold()} if model_name else set()
    for upload in uploads:
        try:
            name = basename(upload.filename or '')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if name.casefold() in used:
            raise HTTPException(422, 'Uploaded filenames must be unique, ignoring case: ' + name)
        used.add(name.casefold())
        data = await upload.read(MAX_UPLOAD + 1)
        total += len(data)
        if len(data) > MAX_UPLOAD or total > MAX_BUNDLE:
            raise HTTPException(413, 'Each file must be at most 8 MiB; total upload at most 32 MiB.')
        files[name] = data
    return files


@app.post('/api/models/{identifier}/companions')
async def attach_companions(identifier: str, companions: list[UploadFile] = File(...)):
    source = folder('models', identifier)
    current = json.loads((source / 'meta.json').read_text())
    added = await read_companions(companions, model_name=current['name'])
    retained = {item['name']: (source / item['stored']).read_bytes() for item in current.get('companions', [])}
    # Import a new revision; prior runs and original model remain reproducible.
    added_names = {name.casefold() for name in added}
    retained = {name: data for name, data in retained.items() if name.casefold() not in added_names}
    retained.update(added)
    if len(retained) > 64:
        raise HTTPException(413, 'A project can contain up to 64 companion files.')
    if sum(map(len, retained.values())) + (source / 'model.inp').stat().st_size > MAX_BUNDLE:
        raise HTTPException(413, 'Combined project exceeds 32 MiB.')
    original = (source / 'model.inp').read_bytes()
    text, _ = decode_text(original)
    explicit_crs = current['crs'] if current.get('crs_source') == 'Entered on import' else None
    return add_model(text, current['name'], explicit_crs, original=original, supplied_files=retained)


@app.post("/api/models/demo", status_code=201)
def demo():
    return add_model((ROOT / "examples/riverside.inp").read_text(), "Riverside demo", "EPSG:4326")


@app.get("/api/models/{identifier}")
def model(identifier: str):
    path = folder("models", identifier)
    return {**json.loads((path / "meta.json").read_text()), "network": json.loads((path / "network.json").read_text())}


@app.get("/api/models/{identifier}/input")
def model_input(identifier: str):
    return FileResponse(folder("models", identifier) / "model.inp", filename="model.inp", media_type="text/plain")


@app.get('/api/models/{identifier}/bundle')
def model_bundle(identifier: str):
    source = folder('models', identifier)
    meta = json.loads((source / 'meta.json').read_text())
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w', zipfile.ZIP_DEFLATED) as bundle:
        name = basename(meta['name'])
        bundle.write(source / 'model.inp', name if name.lower().endswith('.inp') else name + '.inp')
        for item in meta.get('companions', []):
            bundle.write(source / item['stored'], item['name'])
    return Response(content.getvalue(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="project.zip"'})


@app.get("/api/models/{identifier}/geojson")
def geojson(identifier: str):
    network = json.loads((folder("models", identifier) / "network.json").read_text())
    try:
        data = as_geojson(network)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(json.dumps(data), media_type="application/geo+json", headers={"Content-Disposition": 'attachment; filename="network.geojson"'})


async def execute_run(path):
    async with slots:
        meta = json.loads((path / "meta.json").read_text())
        meta.update(status="running", started_at=now())
        save_json(path / "meta.json", meta)
        process = None
        try:
            with (path / "engine.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.worker", str(path.resolve()), cwd=ROOT, stdout=log, stderr=log)
                try:
                    code = await asyncio.wait_for(process.wait(), timeout=120)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    raise RuntimeError("Simulation exceeded the 120-second limit. Reduce the duration or network complexity.")
            if code != 0:
                raise RuntimeError("SWMM could not complete the model. Open the report and engine log for diagnostics.")
            meta.update(status="completed", summary=json.loads((path / "summary.json").read_text()))
        except asyncio.CancelledError:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            meta.update(status="failed", error="Run interrupted by server shutdown.")
            raise
        except Exception as exc:
            meta.update(status="failed", error=str(exc))
        finally:
            meta["finished_at"] = now()
            save_json(path / "meta.json", meta)


@app.post("/api/models/{identifier}/runs", status_code=202)
async def start_run(identifier: str, tasks: BackgroundTasks):
    source = folder("models", identifier)
    meta_source = json.loads((source / 'meta.json').read_text())
    if meta_source.get('missing_files'):
        raise HTTPException(422, 'Attach missing input files before simulating: ' + ', '.join(meta_source['missing_files']))
    active = [p for p in (DATA / "runs").glob("*/meta.json") if json.loads(p.read_text())["status"] in {"queued", "running"}]
    if len(active) >= 4:
        raise HTTPException(429, "Four runs are already active. Wait for a run to finish.")
    run_id = uuid.uuid4().hex
    path = DATA / "runs" / run_id
    path.mkdir()
    shutil.copyfile(source / "prepared.inp" if (source / "prepared.inp").exists() else source / "model.inp", path / "model.inp")
    if (source / 'assets').is_dir():
        shutil.copytree(source / 'assets', path / 'assets')
    meta = {"id": run_id, "model_id": identifier, "status": "queued", "created_at": now()}
    save_json(path / "meta.json", meta)
    tasks.add_task(execute_run, path)
    return meta


@app.get("/api/models/{identifier}/runs")
def model_runs(identifier: str):
    folder("models", identifier)
    result = [json.loads(p.read_text()) for p in (DATA / "runs").glob("*/meta.json")]
    return sorted([r for r in result if r["model_id"] == identifier], key=lambda r: r["created_at"], reverse=True)


@app.get("/api/runs/{identifier}")
def run(identifier: str):
    return json.loads((folder("runs", identifier) / "meta.json").read_text())


@app.get("/api/runs/{identifier}/series")
def result_series(identifier: str, kind: str, element: str, metric: str, format: str = "json"):
    path = folder("runs", identifier)
    if json.loads((path / "meta.json").read_text())["status"] != "completed":
        raise HTTPException(409, "Results are only available for completed runs.")
    try:
        data = series(path / "results.out", kind, element, metric)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if format == "csv":
        content = io.StringIO()
        writer = csv.writer(content)
        writer.writerow(["model_time", "element", "metric", "unit", "value"])
        for date, value in zip(data["timestamps"], data["values"], strict=True):
            # Avoid spreadsheet formula execution from user-controlled SWMM IDs.
            safe_element = "'" + element if element.startswith(("=", "+", "-", "@")) else element
            writer.writerow([date, safe_element, metric, data["unit"], value])
        return Response(content.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="series.csv"'})
    return data


@app.get("/api/runs/{identifier}/files/{kind}")
def run_file(identifier: str, kind: str):
    names = {"report": "report.rpt", "output": "results.out", "input": "model.inp", "log": "engine.log"}
    if kind not in names:
        raise HTTPException(404, "Unknown artifact.")
    path = folder("runs", identifier)
    meta = json.loads((path / "meta.json").read_text())
    if meta["status"] in {"queued", "running"}:
        raise HTTPException(409, "Wait until the run finishes before downloading artifacts.")
    target = path / names[kind]
    if not target.is_file():
        raise HTTPException(404, "This run did not produce that artifact.")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream" if kind == "output" else "text/plain")
