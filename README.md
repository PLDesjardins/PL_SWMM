# PL SWMM

A browser workspace with a Python backend for **EPA SWMM 5.2.4**. This first version focuses on importing models, running the actual engine, viewing results, and displaying GIS geometry. It is independent of CHI and PCSWMM.

## Use in your browser — no installation or administrator rights

[Deploy PL SWMM on Render](https://render.com/deploy?repo=https://github.com/PLDesjardins/PL_SWMM)

1. Open the link, create/sign in to your Render account, and connect GitHub. If prompted, allow access to `PLDesjardins/PL_SWMM`.
2. Review the `pl-swmm` web service. The template selects the **free** plan. Set `PL_SWMM_PASSWORD` to a strong password in the deployment form, then deploy.
3. Wait for the service to become live, then open the HTTPS address shown in Render. Your browser asks for a username and password: use **swmm** and the password you chose.
4. Select **Explore a demo model**, then **Run simulation**, or import your `.inp` model.

Your computer needs only a browser. Python and the SWMM solver run on the hosting server; you do not download or run an `.exe`. Setting up hosting requires a hosting account, not PC administrator rights. The repository itself and GitHub Pages do not run the Python backend.

**Free hosting is for trying the app:** it can sleep when idle, and model/run files can be lost on restarts or redeploys. Download models and results you want to keep. For persistent storage, choose a disk-capable paid service and mount a disk at `/var/data` (review the provider's price before changing plans). This remains a single-user workspace; anyone with its password can access its models and results. Automatic deployment is disabled in the template.

If your organization requires its own hosting, IT can run the included Docker image on a server behind HTTPS. Nothing needs to be installed on your workstation. The image uses a non-root account (UID 10001), one server process, `PORT` (default 8000), and `PL_SWMM_DATA_DIR` (default `/var/data/pl-swmm`). Any persistent disk must be writable by UID 10001. Set `PL_SWMM_REQUIRE_AUTH=1` and `PL_SWMM_PASSWORD` in the server's secret settings. `/api/health` remains public for platform health checks; the app, API, and downloadable files require the password. The shared password is a first-version access gate, not a multi-user account system.

## Run locally (developer option)

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js/npm for the locally served Leaflet library.

```sh
export UV_CACHE_DIR=/tmp/pl-swmm-uv
uv sync --frozen --group dev
npm ci --ignore-scripts --cache /tmp/pl-swmm-npm
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in a local browser. Select **Explore a demo model**, then **Run simulation**, or import your own `.inp` file. The included six-hour rainfall example is synthetic and uncalibrated. Results are calculated when you run the engine.

`swmm-toolkit==0.17.0` bundles the solver. The server and each simulation worker reject any engine version other than `52004`. Completed runs expose the engine version recorded in binary output. `/api/health` reports the installed version. Dependencies are pinned in `uv.lock` and `package-lock.json`.

## Features

- Import and retain SWMM INP models; browse nodes, links, and subcatchments.
- Run hydrologic and hydraulic simulations in isolated subprocesses, with up to two concurrent workers and four active runs.
- View node depth/head/inflow/flooding, link flow/depth/velocity/capacity, and subcatchment runoff/rainfall/infiltration time series.
- Inspect the original report, including continuity errors and engine diagnostics. Download INP, RPT, OUT, engine logs, and selected time series as CSV.
- Display model geometry, including vertices and catchment polygons; select objects, control layers, and colour links by peak reported flow.
- Transform an explicitly supplied source CRS to WGS84 using PROJ. Export network GeoJSON and add WGS84 GeoJSON overlays.
- Retain models and run history on the server. Reload restores the last selected model and latest run.

SWMM coordinates do **not** identify their CRS. Supply an EPSG code on import for geographical maps and exports. Without a CRS, the app displays local coordinates; without coordinates, it uses a labelled schematic. GeoJSON overlays are visual reference layers for the current view; they are not persisted and do not modify the hydraulic model. The optional OpenStreetMap basemap requests `tile.openstreetmap.org` in your browser; the default map requires no external tiles.

## Current scope

This is a single-user app with local and container-hosted startup options. Multi-user accounts, advanced production operations, model editing, terrain processing, shapefile import, automated network generation, calibration, scenario comparison, and coupled 1D/2D flooding are future milestones.

Models must be self-contained: external `FILE` references, interface/hotstart files, and LID report paths are rejected. Use `*` for the optional LID report filename. Inputs and overlays are limited to 8 MiB. Simulations have a 120-second time limit; previews support up to 100,000 reporting periods. Large networks omit aggregate peaks above 2,000 nodes plus links or two million element-periods; individual series remain available. Summaries use **reported** elements and timestamps and cannot detect events omitted by `REPORT_STEP` or `REPORT` selections. Review the full report for numerical quality.

Data is stored under `.data/`, or `PL_SWMM_DATA_DIR`. Run one Uvicorn process: queue and restart handling assume a single server. Restarting marks interrupted runs as failed. No automatic file retention is implemented; back up or remove data with the server stopped. Keep the default loopback binding for local use. Hosted access needs HTTPS and a workspace password; free hosting does not retain files reliably.

## Validation

```sh
.venv/bin/python -m pytest
npm run test:e2e
PL_SWMM_E2E_HOSTED=1 npm run test:e2e
```

API tests run real simulations and verify hydrologic output, engine version, timestamps, CSV values, artifacts, engine failures, rejected external paths, CRS transformation, and restart recovery. Browser tests exercise upload → simulation → chart/report → GIS export → reload and a failed model. They use `/usr/bin/chromium`; set `CHROMIUM_PATH` for another installed executable. Browser test data is separate under `/tmp/pl-swmm-browser-test`.

## Next milestones

1. Model editing, rainfall-file packages, and persistent GIS layers.
2. Scenario copies, comparable results, and observed-series import.
3. Calibration, sensitivity analysis, and GIS-assisted model creation.
4. Terrain and inundation workflows with separately validated 2D capabilities.

The solver and wrapper come from [EPA SWMM](https://github.com/USEPA/Stormwater-Management-Model) and [swmm-toolkit](https://github.com/pyswmm/swmm-python). Mapping uses [Leaflet](https://leafletjs.com/); CRS transforms use [pyproj/PROJ](https://pyproj4.github.io/pyproj/).
