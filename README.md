# PL SWMM

A browser workspace with a Python backend for **EPA SWMM 5.2.4**. This first version focuses on importing models, running the actual engine, editing parameters and data, viewing results, and displaying GIS geometry. It is independent of CHI and PCSWMM.

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
- Import PCSWMM companion files (`.chi`, `.ini`, `.db`, `.thm`) together with a model, preserving the originals. Detect source CRS from matching project metadata and support Windows-1252 French text.
- Open models with external rainfall/time-series references even when their data is missing. Attach referenced files by filename before simulation; original Windows paths are never accessed on the server. Later attachments create a new model revision and preserve previous runs.
- Edit every SWMM network type: junctions, outfalls, storage, dividers, conduits, pumps, orifices, weirs, outlets, subcatchments, and rain gages. Edit related cross sections, losses, inflows, infiltration, and geometry.
- Create and edit curves and time series with observation/point tables and a bulk data editor. Saves create model revisions and validate input with EPA SWMM 5.2.4 when required files are available.
- Run hydrologic and hydraulic simulations in isolated subprocesses, with up to two concurrent workers and four active runs.
- View node depth/head/inflow/flooding, link flow/depth/velocity/capacity, and subcatchment runoff/rainfall/infiltration time series.
- Inspect the original report, including continuity errors and engine diagnostics. Download INP, RPT, OUT, engine logs, and selected time series as CSV.
- Display model geometry, including vertices and catchment polygons; select objects, control layers, and colour links by peak reported flow.
- Transform an explicitly supplied source CRS to WGS84 using PROJ. Export network GeoJSON and add WGS84 GeoJSON overlays.
- Retain models and run history on the server. Reload restores the last selected model and latest run.

SWMM coordinates do **not** identify their CRS. Attach matching PCSWMM project files to detect it: explicit user entry overrides `.chi`, then `.db` Settings, then `.ini`. Conflicting metadata is reported instead of silently merged. Without a CRS, the app displays local coordinates; without coordinates, it uses a labelled schematic. GeoJSON overlays are visual references for the current view; they do not modify the hydraulic model.

Geographical maps default to **Esri satellite imagery with road and place labels**, using public ArcGIS `World_Imagery`, `World_Transportation`, and `World_Boundaries_and_Places` services. No Google key is needed. The background selector also offers OpenStreetMap and **No background**. Imagery and labels load in your browser from `server.arcgisonline.com` (or `tile.openstreetmap.org` for OSM); your connection must allow those hosts. Provider attribution remains visible. Google hybrid is not included because it requires a Google Maps API key and billing-enabled project. Imagery is for geographical context, not hydraulic or elevation inputs.

### Import a PCSWMM project

Choose the `.inp` file and select its companion project files in the import dialog. Referenced rainfall/time-series files (`.dat`, `.txt`, etc.) can be selected at the same time. Leave the CRS field blank for detection, or enter an EPSG code to override it. Project metadata files should have the same base name as the `.inp` file.

If the model references files that were not uploaded, the network still opens and the app lists the missing filenames. Use **+ Project files** to attach them; this creates a new revision rather than altering previous runs. Matching is by case-insensitive filename, so an old Windows directory does not matter. Duplicate filenames from different directories are ambiguous and require distinct names. **Project ZIP** downloads the original model and companion files for backup. This is an export; ZIP/PCZ import is not yet supported.

PCSWMM `.db` files supply coordinate metadata only; historical database results are not treated as newly calculated results. Thumbnail/theme/defaults files are retained without applying proprietary PCSWMM rendering or changing model parameters. The INP download preserves original bytes; each engine run uses a UTF-8 working copy with uploaded input paths rewritten into its private assets directory.

### Edit parameters, time series, and curves

Select an object on the map or in the explorer, then click **Edit parameters**. Its main parameters appear as labelled fields, with related sections below (for example, cross sections and losses for conduits). **Model data** lists every editable section, including rain gages and items without map geometry. Use the section selector and search to locate an item.

Under **Model data**, choose **+ Add time series** or **+ Add curve**, enter a unique name, and add observations or points. You can also paste rows into the bulk editor; omit the item name from each row. Dates use `MM/DD/YYYY`, times use `HH:MM` or decimal elapsed hours, and values use the model's original units. Curves start with a type and X/Y pair; subsequent rows are X/Y pairs. Tables are available for ordinary two/three-column datasets up to 200 rows; longer or FILE-based series use the bulk editor. Raw edits disable the table until you reopen the editor. To replace a missing FILE series with embedded data, replace its FILE row with observations.

**Save new revision** keeps earlier models and runs available in the saved-model selector. The new revision starts with no simulation results; run it to calculate updated results. Object IDs stay fixed to preserve references. Curve and time-series creation does not automatically assign them to an object: edit the referring rain gage, storage, outlet, outfall, pump, or inflow as appropriate. Switching a conditional type or adding optional parameters can use the full parameter row, with format guidance shown beside it. Blank related sections may be filled in; clearing an optional related section removes its rows.

Saving checks the input in an isolated EPA SWMM 5.2.4 process, including routing initialization, without simulating the event. Invalid input displays an error and leaves the current revision intact. If external inputs are missing, edits can be saved as a draft and the app explicitly marks engine validation as pending. Attach the missing files and run the model to validate it. An input check does not establish engineering validity or numerical quality. Download **Model INP** or **Project ZIP** to keep edits before restarting or redeploying free hosting. Imported originals preserve their bytes; edited INP revisions are UTF-8.

## Current scope

This is a single-user app with local and container-hosted startup options. Multi-user accounts, advanced production operations, terrain processing, shapefile import, automated network generation, calibration, scenario comparison, and coupled 1D/2D flooding are future milestones.

External time-series, rain-gage, and temperature/climate inputs must be uploaded before simulation. Interface/hotstart directives and external LID report paths remain unsupported; use `*` for the optional LID report filename. Each uploaded file and GIS overlay is limited to 8 MiB; project uploads allow 64 companion files and 32 MiB total. Simulations have a 120-second time limit; previews support up to 100,000 reporting periods. Large networks omit aggregate peaks above 2,000 nodes plus links or two million element-periods; individual series remain available. Summaries use **reported** elements and timestamps and cannot detect events omitted by `REPORT_STEP` or `REPORT` selections. Review the full report for numerical quality.

Data is stored under `.data/`, or `PL_SWMM_DATA_DIR`. Run one Uvicorn process: queue and restart handling assume a single server. Restarting marks interrupted runs as failed. No automatic file retention is implemented; back up or remove data with the server stopped. Keep the default loopback binding for local use. Hosted access needs HTTPS and a workspace password; free hosting does not retain files reliably.

## Validation

```sh
.venv/bin/python -m pytest
npm run test:e2e
PL_SWMM_E2E_HOSTED=1 npm run test:e2e
```

API tests run real simulations and verify hydrologic output, engine version, timestamps, CSV values, artifacts, engine failures, safe uploaded-file resolution, CRS transformation, and restart recovery. PCSWMM tests cover companion metadata, encoding, missing files, revision creation, and equivalence of externally supplied rainfall to embedded rainfall. Editing tests exercise every network type, revision preservation, curve/series creation, real edited-model simulation, and atomic rejection of invalid inputs. Browser tests exercise object editing and curve/time-series tables, plus import → simulation → chart/report → GIS export → reload, companion attachment, and failures. Basemap tests mock tile responses while checking layer selection and attribution; they do not validate live provider imagery. They use `/usr/bin/chromium`; set `CHROMIUM_PATH` for another installed executable. Browser test data is separate under `/tmp/pl-swmm-browser-test`.

## Next milestones

1. Network creation/renaming, ZIP/PCZ package import, and persistent GIS layers.
2. Scenario copies, comparable results, and observed-series import.
3. Calibration, sensitivity analysis, and GIS-assisted model creation.
4. Terrain and inundation workflows with separately validated 2D capabilities.

The solver and wrapper come from [EPA SWMM](https://github.com/USEPA/Stormwater-Management-Model) and [swmm-toolkit](https://github.com/pyswmm/swmm-python). Mapping uses [Leaflet](https://leafletjs.com/); CRS transforms use [pyproj/PROJ](https://pyproj4.github.io/pyproj/).
