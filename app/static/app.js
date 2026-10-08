/* Local browser workspace. All simulation values come from SWMM output. */
const $ = id => document.getElementById(id);
const state = {model: null, run: null, selected: null, map: null, layers: {}, overlays: [], basemap: null, series: null, request: 0, poll: null, runGeneration: 0};
const metricLabels = {flow: 'Flow rate', depth: 'Water depth', head: 'Hydraulic head', inflow: 'Total inflow', flooding: 'Flooding losses', velocity: 'Flow velocity', capacity: 'Capacity fraction', runoff: 'Runoff rate', rainfall: 'Rainfall intensity', infiltration: 'Infiltration rate'};
const metrics = {links: ['flow', 'depth', 'velocity', 'capacity'], nodes: ['depth', 'head', 'inflow', 'flooding'], subcatchments: ['runoff', 'rainfall', 'infiltration']};
const categoryNames = {nodes: 'Nodes', links: 'Links', subcatchments: 'Subcatchments'};
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const number = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 3});

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = response.statusText;
    try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : 'The request could not be processed.'; } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}
function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => {$('toast').hidden = true;}, 7000);
}
function attempt(fn) { return async (...args) => {try {await fn(...args);} catch (error) {toast(error.message);}}; }
function download(url) { const a = document.createElement('a'); a.href = url; a.download = ''; a.click(); }
function showTab(name) {
  document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === name));
  ['network', 'results', 'report'].forEach(n => $(n + '-panel').hidden = n !== name);
  if (name === 'network') setTimeout(() => state.map?.invalidateSize(), 0);
}

function status(run) {
  const missing = state.model?.missing_files?.length;
  const label = run?.status || (state.model ? missing ? 'Missing input files' : 'Ready to simulate' : 'No model loaded');
  $('run-status').textContent = label.charAt(0).toUpperCase() + label.slice(1);
  $('run-status').className = 'status-badge ' + (run?.status || '');
  const busy = run && ['queued', 'running'].includes(run.status);
  $('run-button').disabled = !state.model || busy || missing;
  $('run-button').textContent = busy ? '◌ Simulating…' : '▶ Run simulation';
}

async function refreshModels() {
  const models = await api('/api/models');
  $('model-select').innerHTML = '<option value="">Select a model</option>' + models.map(m => `<option value="${m.id}">${escapeHTML(m.name)}</option>`).join('');
  $('model-select').value = state.model?.id || '';
  return models;
}

async function loadModel(id) {
  const request = ++state.request;
  const model = await api('/api/models/' + id);
  if (request !== state.request) return;
  clearTimeout(state.poll); state.runGeneration++;
  state.model = model; state.run = null; state.selected = null; state.series = null;
  localStorage.setItem('pl-swmm-model', id);
  $('model-select').value = id;
  $('project-title').textContent = model.name;
  $('model-heading').textContent = model.name.replace(/\.inp$/i, '');
  $('model-subtitle').textContent = model.name === 'Riverside demo' ? 'Synthetic rainfall event · 6-hour simulation · Metric units' : 'Imported SWMM model · Original input preserved';
  $('empty-state').hidden = true;
  $('download-input').disabled = false;
  $('download-bundle').disabled = false; $('attach-companions').disabled = false;
  renderModelNotices();
  $('object-count').textContent = Object.values(model.counts).reduce((a, b) => a + b, 0) + ' objects';
  $('stat-nodes').innerHTML = `${model.counts.nodes} <small>nodes</small>`;
  $('stat-links').textContent = `${model.counts.links} links · ${model.counts.subcatchments} subcatchments`;
  resetResults(); renderTree(); renderMap(); renderInspector(); status(null); showTab('network');
  const runs = await refreshRuns();
  if (state.model?.id !== id) return;
  const latest = runs[0];
  if (latest) await selectRun(latest.id);
}

function renderModelNotices() {
  const model = state.model;
  const notices = [];
  if (model.missing_files?.length) notices.push(`<strong>Model opened. Attach these rainfall/input files before simulation:</strong><ul>${model.missing_files.map(name => `<li>${escapeHTML(name)}</li>`).join('')}</ul><span>The original computer’s paths cannot be accessed by the server. Use “+ Project files” to attach the files.</span>`);
  for (const warning of model.warnings || []) notices.push(escapeHTML(warning));
  if (model.crs_source && model.crs_source !== 'Entered on import') notices.push(`Coordinate system detected from ${escapeHTML(model.crs_source)}: ${escapeHTML(model.crs)}.`);
  $('model-notices').hidden = !notices.length;
  $('model-notices').innerHTML = notices.map(n => `<div>${n}</div>`).join('');
}

function resetResults() {
  ['stat-flow', 'stat-depth', 'stat-flood'].forEach(id => $(id).textContent = '—');
  $('stat-run').textContent = 'No completed simulation';
  $('results-empty').hidden = false; $('results-content').hidden = true;
  $('results-empty').textContent = 'Run a simulation to explore results.';
  $('report-text').textContent = 'Run a simulation to generate the engine report.';
  $('download-log').disabled = true;
}

function renderTree() {
  if (!state.model) return;
  const query = $('object-search').value.trim().toLowerCase();
  $('model-tree').innerHTML = Object.entries(categoryNames).map(([kind, label]) => {
    const objects = state.model.network[kind].filter(o => o.id.toLowerCase().includes(query));
    return `<details class="tree-group" ${kind === 'nodes' || query ? 'open' : ''}><summary>${label}<span>${objects.length}</span></summary>${objects.map(o => `<button class="tree-object ${state.selected?.kind === kind && state.selected?.id === o.id ? 'active' : ''}" data-kind="${kind}" data-id="${escapeHTML(o.id)}"><span>${kind === 'nodes' ? '●' : kind === 'links' ? '↗' : '◇'}</span>${escapeHTML(o.id)}<small>${escapeHTML(o.kind)}</small></button>`).join('')}</details>`;
  }).join('');
  document.querySelectorAll('.tree-object').forEach(button => button.addEventListener('click', () => selectObject(button.dataset.kind, button.dataset.id)));
}

function renderMap() {
  if (state.map) state.map.remove();
  state.layers = {}; state.basemap = null;
  // GIS overlays belong to the active model view; do not misplace them in a different CRS.
  state.overlays = []; $('overlay-list').replaceChildren();
  const network = state.model?.network;
  const geographic = network?.map_mode === 'geographic' && network.has_geometry;
  state.map = L.map('map', {crs: geographic ? L.CRS.EPSG3857 : L.CRS.Simple, zoomControl: true, minZoom: geographic ? 2 : -12, maxZoom: geographic ? 20 : 12, attributionControl: true});
  const fallback = new Map((network?.nodes || []).map((n, i) => [n.id, [100 + (i % 4) * 100, 100 + Math.floor(i / 4) * 100]]));
  const coordinate = n => n.coordinate || fallback.get(n.id);
  for (const kind of ['subcatchments', 'links', 'nodes']) state.layers[kind] = L.featureGroup();
  const nodesById = new Map((network?.nodes || []).map(n => [n.id, n]));
  const add = (kind, obj, layer) => {
    layer.options.objectId = obj.id;
    layer.on('click', () => selectObject(kind, obj.id));
    if ($('layer-labels').checked) layer.bindTooltip(escapeHTML(obj.id), {permanent: true, direction: 'top', className: 'object-label'});
    state.layers[kind].addLayer(layer);
  };
  for (const obj of network?.subcatchments || []) {
    if (obj.coordinates.length) add('subcatchments', obj, L.polygon(obj.coordinates.map(p => [p[1], p[0]]), {color: '#98b78b', fillColor: '#b6d19c', fillOpacity: .24, weight: 1, dashArray: '4 4'}));
  }
  for (const obj of network?.links || []) {
    let coords = obj.coordinates;
    if (!coords.length && !geographic && nodesById.has(obj.from) && nodesById.has(obj.to)) coords = [coordinate(nodesById.get(obj.from)), coordinate(nodesById.get(obj.to))];
    if (coords.length) add('links', obj, L.polyline(coords.map(p => [p[1], p[0]]), {color: '#4d91ac', weight: 3, opacity: .9}));
  }
  for (const obj of network?.nodes || []) {
    if (geographic && !obj.coordinate) continue;
    const p = coordinate(obj);
    add('nodes', obj, L.circleMarker([p[1], p[0]], {radius: obj.kind === 'outfall' ? 7 : 5, color: 'white', fillColor: obj.kind === 'outfall' ? '#2d666e' : '#25846b', fillOpacity: 1, weight: 2}));
  }
  for (const kind of Object.keys(state.layers)) if ($('layer-' + kind).checked) state.layers[kind].addTo(state.map);
  $('layer-basemap').disabled = !geographic;
  $('layer-basemap').value = geographic ? localStorage.getItem('pl-swmm-basemap') ?? 'satellite' : '';
  $('export-gis').disabled = !geographic;
  $('crs-label').textContent = geographic ? `${network.crs} → WGS84` : network?.has_geometry ? 'Local coordinates · CRS unspecified' : network ? 'Schematic · No model coordinates' : 'No coordinate system';
  $('map-description').textContent = network?.has_geometry ? 'MODEL GEOMETRY' : network ? 'SCHEMATIC NETWORK' : 'MODEL GEOMETRY';
  syncBasemap(); fitMap(); colourMap();
}

function fitMap() {
  const all = L.featureGroup(Object.values(state.layers));
  const bounds = all.getBounds();
  if (bounds.isValid()) state.map.fitBounds(bounds.pad(.18), {maxZoom: state.model?.network.map_mode === 'geographic' ? 16 : 1});
  else state.map.setView([0, 0], 0);
}

function syncBasemap() {
  if (!state.map) return;
  if (state.basemap) state.map.removeLayer(state.basemap);
  state.basemap = null;
  const choice = $('layer-basemap').value;
  if (!choice || state.model?.network.map_mode !== 'geographic') return;
  const tile = (url, options) => {
    const layer = L.tileLayer(url, options);
    layer.on('tileerror', () => {if (!syncBasemap.warned) {toast('Background imagery is unavailable on this connection. Model geometry still works; select No background to hide tiles.'); syncBasemap.warned = true;}});
    return layer;
  };
  if (choice === 'satellite') {
    const service = 'https://server.arcgisonline.com/ArcGIS/rest/services/';
    const image = tile(service + 'World_Imagery/MapServer/tile/{z}/{y}/{x}', {maxZoom: 20, maxNativeZoom: 19, attribution: 'Imagery © <a href="https://www.esri.com/">Esri</a>, Maxar, Earthstar Geographics, GIS User Community'});
    const labels = tile(service + 'Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {maxZoom: 20, maxNativeZoom: 19, attribution: 'Labels © Esri, HERE, Garmin, FAO, NOAA, USGS'});
    const roads = tile(service + 'Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}', {maxZoom: 20, maxNativeZoom: 19, attribution: 'Roads © Esri, HERE, Garmin'});
    state.basemap = L.layerGroup([image, roads, labels]);
  } else {
    state.basemap = tile('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 20, maxNativeZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'});
  }
  state.basemap.addTo(state.map);
}

function colourMap() {
  const summary = state.run?.status === 'completed' ? state.run.summary : null;
  const peaks = summary?.link_peaks || {};
  const hasPeaks = Object.keys(peaks).length > 0;
  const byFlow = $('map-theme').value === 'flow' && hasPeaks;
  state.layers.links?.eachLayer(layer => {
    const id = layer.options.objectId;
    const ratio = summary?.peak_link_flow > 0 ? (peaks[id] || 0) / summary.peak_link_flow : 0;
    const colour = byFlow ? `hsl(${180 - ratio * 155}, 55%, 48%)` : '#4d91ac';
    layer.setStyle({color: state.selected?.kind === 'links' && state.selected.id === id ? '#173d33' : colour, weight: state.selected?.kind === 'links' && state.selected.id === id ? 5 : 3});
  });
}

function selectObject(kind, id) {
  state.selected = {kind, id}; renderTree(); renderInspector(); colourMap();
  const layer = state.layers[kind]?.getLayers().find(l => l.options.objectId === id);
  if (layer?.getLatLng) state.map.panTo(layer.getLatLng());
  else if (layer?.getBounds) state.map.panTo(layer.getBounds().getCenter());
}

function renderInspector() {
  const selected = state.selected;
  if (!selected || !state.model) {
    $('inspector-content').innerHTML = '<div class="inspector-icon">◇</div><h3>Explore the network</h3><p class="muted small">Select a node, link, or subcatchment to inspect its properties and results.</p>';
    return;
  }
  const obj = state.model.network[selected.kind].find(o => o.id === selected.id);
  const labels = {junction: ['Invert elevation', 'Max depth', 'Initial depth', 'Surcharge depth', 'Ponded area'], outfall: ['Invert elevation', 'Boundary type', 'Flap gate'], storage: ['Invert elevation', 'Max depth', 'Initial depth', 'Shape'], conduit: ['Length', 'Roughness', 'Inlet offset', 'Outlet offset', 'Initial flow', 'Max flow'], subcatchment: ['Rain gage', 'Outlet', 'Area', 'Impervious (%)', 'Width', 'Slope (%)', 'Curb length']};
  const properties = [];
  if (obj.from) properties.push(['From node', obj.from], ['To node', obj.to]);
  obj.parameters.forEach((v, i) => properties.push([(labels[obj.kind] || [])[i] || `Parameter ${i + 1}`, v]));
  $('inspector-content').innerHTML = `<div class="inspector-icon">${selected.kind === 'nodes' ? '●' : selected.kind === 'links' ? '↗' : '◇'}</div><h3>${escapeHTML(obj.id)}</h3><span class="object-type">${escapeHTML(obj.kind)}</span><div class="property-list">${properties.map(([k, v]) => `<div class="property-row"><span>${escapeHTML(k)}</span><strong>${escapeHTML(v)}</strong></div>`).join('')}</div><p class="muted small" style="margin-top:12px">Raw INP parameters · Units follow FLOW_UNITS and model options.</p><button class="button subtle" id="inspect-results" ${state.run?.status === 'completed' ? '' : 'disabled'}>⌁ View time series</button>`;
  $('inspect-results').addEventListener('click', attempt(async () => {
    if (!state.run.summary.elements[selected.kind].includes(selected.id)) {toast('This object is excluded from the model’s REPORT selections.'); return;}
    $('result-kind').value = selected.kind; updateResultOptions(selected.id); showTab('results'); await loadSeries();
  }));
}

async function refreshRuns() {
  const id = state.model?.id;
  if (!id) return [];
  const runs = await api(`/api/models/${id}/runs`);
  if (state.model?.id !== id) return [];
  $('run-count').textContent = runs.length;
  $('run-list').innerHTML = runs.length ? runs.map(r => `<button class="run-item ${r.status} ${r.id === state.run?.id ? 'active' : ''}" data-run="${r.id}"><span class="run-icon">${r.status === 'completed' ? '✓' : r.status === 'failed' ? '!' : '◌'}</span><span>${r.status.charAt(0).toUpperCase() + r.status.slice(1)}<small>${new Date(r.created_at).toLocaleString()}</small></span></button>`).join('') : '<p class="muted small">Your runs will appear here.</p>';
  document.querySelectorAll('[data-run]').forEach(button => button.addEventListener('click', attempt(() => selectRun(button.dataset.run))));
  return runs;
}

async function selectRun(id) {
  clearTimeout(state.poll);
  const generation = ++state.runGeneration;
  const run = await api('/api/runs/' + id);
  if (generation !== state.runGeneration || run.model_id !== state.model?.id) return;
  state.run = run; state.series = null;
  resetResults(); status(run); renderInspector(); colourMap(); await refreshRuns();
  if (generation !== state.runGeneration) return;
  if (run.status === 'completed') {
    const s = run.summary;
    $('stat-flow').innerHTML = `${number(s.peak_link_flow)} <small>${escapeHTML(s.units.flow)}</small>`;
    $('stat-depth').innerHTML = `${number(s.peak_node_depth)} <small>${escapeHTML(s.units.length)}</small>`;
    $('stat-flood').textContent = number(s.flooded_nodes);
    $('stat-run').textContent = s.flooded_nodes == null ? 'Summary omitted for large network' : 'Within reported nodes and timesteps';
    $('results-empty').hidden = true; $('results-content').hidden = false;
    $('period-count').textContent = `${s.periods} reporting periods · ${s.report_step_seconds}s interval · Engine 5.2.4`;
    const firstKind = ['links', 'nodes', 'subcatchments'].find(k => s.elements[k].length);
    if (firstKind) { $('result-kind').value = firstKind; updateResultOptions(); await loadSeries(); }
    else { $('results-empty').hidden = false; $('results-empty').textContent = 'No objects were selected for reporting in this model.'; $('results-content').hidden = true; }
  } else if (run.status === 'failed') {
    $('results-empty').textContent = run.error;
    toast(run.error);
  }
  if (['completed', 'failed'].includes(run.status)) {
    $('download-log').disabled = false;
    const response = await fetch(`/api/runs/${id}/files/report`);
    const report = response.ok ? await response.text() : 'The engine did not produce a report. Download the engine log for diagnostics.';
    if (generation === state.runGeneration) $('report-text').textContent = report;
  } else if (generation === state.runGeneration) {
    state.poll = setTimeout(attempt(() => selectRun(id)), 700);
  }
}

function updateResultOptions(preferred) {
  const kind = $('result-kind').value;
  const ids = state.run?.summary?.elements[kind] || [];
  $('result-element').innerHTML = ids.map(id => `<option value="${escapeHTML(id)}">${escapeHTML(id)}</option>`).join('');
  if (preferred && ids.includes(preferred)) $('result-element').value = preferred;
  $('result-metric').innerHTML = metrics[kind].map(metric => `<option value="${metric}">${metricLabels[metric]}</option>`).join('');
}

function seriesURL(format = 'json') {
  const params = new URLSearchParams({kind: $('result-kind').value, element: $('result-element').value, metric: $('result-metric').value, format});
  return `/api/runs/${state.run.id}/series?${params}`;
}

async function loadSeries() {
  if (state.run?.status !== 'completed') return;
  if (!$('result-element').value) {
    state.series = null; $('chart').textContent = 'No objects in this category were selected for reporting.'; return;
  }
  const url = seriesURL();
  $('chart').textContent = 'Loading time series…';
  const data = await api(url);
  if (state.run?.status !== 'completed' || url !== seriesURL()) return;
  state.series = data;
  $('chart-title').textContent = `${data.element} · ${metricLabels[data.metric]}`;
  $('chart-subtitle').textContent = `Model time · ${data.unit} · ${data.timestamps[0].slice(0, 10)}`;
  const finite = data.values.filter(v => v !== null);
  const peak = finite.length ? finite.reduce((a, b) => Math.max(a, b), -Infinity) : null;
  $('chart-peak').textContent = `Peak ${number(peak)} ${data.unit}`;
  drawChart(data);
}

function drawChart(data) {
  const width = 850, height = 255, left = 60, right = 20, top = 20, bottom = 35;
  const values = data.values.filter(v => v !== null);
  if (!values.length) { $('chart').textContent = 'No finite values were reported.'; return; }
  let min = Math.min(0, values.reduce((a, b) => Math.min(a, b), Infinity));
  let max = Math.max(0, values.reduce((a, b) => Math.max(a, b), -Infinity));
  if (max === min) max = min + 1;
  const x = i => left + i / Math.max(data.values.length - 1, 1) * (width - left - right);
  const y = v => top + (max - v) / (max - min) * (height - top - bottom);
  const grid = Array.from({length: 5}, (_, i) => {const v = min + (max - min) * i / 4; return `<line x1="${left}" y1="${y(v)}" x2="${width - right}" y2="${y(v)}" stroke="#e5ece5" stroke-dasharray="3 4"/><text x="${left - 12}" y="${y(v) + 3}" text-anchor="end" fill="#8b9b91" font-size="10">${number(v)}</text>`;}).join('');
  let path = '', segment = false;
  data.values.forEach((v, i) => {if (v === null) {segment = false;} else {path += `${segment ? 'L' : 'M'}${x(i).toFixed(2)},${y(v).toFixed(2)} `; segment = true;}});
  const tickCount = Math.min(data.values.length, 6);
  const ticks = Array.from({length: tickCount}, (_, i) => {const idx = Math.round(i / Math.max(tickCount - 1, 1) * (data.values.length - 1)); const label = data.timestamps[0].slice(0, 10) === data.timestamps.at(-1).slice(0, 10) ? data.timestamps[idx].slice(11, 16) : data.timestamps[idx].slice(5, 16).replace('T', ' '); return `<text x="${x(idx)}" y="${height - 10}" text-anchor="middle" fill="#8b9b91" font-size="10">${escapeHTML(label)}</text>`;}).join('');
  const area = data.values.every(v => v !== null) ? `<path d="${path}L${x(data.values.length - 1)},${y(0)} L${x(0)},${y(0)} Z" fill="url(#chart-fill)"/>` : '';
  $('chart').innerHTML = `<svg role="img" aria-label="${escapeHTML(data.element + ' ' + metricLabels[data.metric] + ', peak ' + number(max) + ' ' + data.unit)}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><defs><linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#419f80" stop-opacity=".2"/><stop offset="100%" stop-color="#419f80" stop-opacity=".02"/></linearGradient></defs>${grid}${area}<path d="${path}" fill="none" stroke="#26816c" stroke-width="2.4" vector-effect="non-scaling-stroke"/>${ticks}</svg>`;
}

function validateGeoJSON(data) {
  if (data.type !== 'FeatureCollection' || !Array.isArray(data.features)) throw new Error('Choose a GeoJSON FeatureCollection in WGS84 longitude/latitude.');
  if (data.features.length > 20000) throw new Error('Overlay exceeds 20,000 features.');
  const validateCoords = coords => {
    if (!Array.isArray(coords) || !coords.length) throw new Error('Invalid GeoJSON coordinates.');
    if (typeof coords[0] === 'number') {
      if (coords.length < 2 || !coords.every(Number.isFinite) || Math.abs(coords[0]) > 180 || Math.abs(coords[1]) > 90) throw new Error('Overlay coordinates must use WGS84 longitude/latitude.');
    } else coords.forEach(validateCoords);
  };
  for (const feature of data.features) {
    if (feature.type !== 'Feature' || !feature.geometry || !['Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'].includes(feature.geometry.type)) throw new Error('Unsupported GeoJSON feature geometry.');
    validateCoords(feature.geometry.coordinates);
  }
}

function bindEvents() {
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => showTab(button.dataset.tab)));
  for (const id of ['open-import', 'empty-import']) $(id).addEventListener('click', () => {$('import-error').hidden = true; $('import-dialog').showModal();});
  $('close-import').addEventListener('click', () => $('import-dialog').close());
  $('import-form').addEventListener('submit', async event => {
    event.preventDefault(); $('submit-import').disabled = true; $('import-error').hidden = true;
    try {
      const body = new FormData(); body.append('file', $('model-file').files[0]); body.append('crs', $('model-crs').value);
      for (const file of $('companion-files').files) body.append('companions', file);
      const model = await api('/api/models', {method: 'POST', body});
      await refreshModels(); await loadModel(model.id); $('import-dialog').close(); toast(model.missing_files?.length ? 'Model opened. Attach the listed input files before running.' : 'Model imported. Ready to run with EPA SWMM 5.2.4.');
    } catch (error) {$('import-error').textContent = error.message; $('import-error').hidden = false;}
    finally {$('submit-import').disabled = false;}
  });
  $('load-demo').addEventListener('click', attempt(async () => {
    $('load-demo').disabled = true;
    try {const model = await api('/api/models/demo', {method: 'POST'}); await refreshModels(); await loadModel(model.id); toast('Demo loaded. Run the simulation to calculate results.');}
    finally {$('load-demo').disabled = false;}
  }));
  $('model-select').addEventListener('change', attempt(event => {if (event.target.value) return loadModel(event.target.value);}));
  $('object-search').addEventListener('input', renderTree);
  $('run-button').addEventListener('click', attempt(async () => {
    if (!state.model) return;
    const modelId = state.model.id;
    $('run-button').disabled = true;
    try {const run = await api(`/api/models/${modelId}/runs`, {method: 'POST'}); if (state.model?.id === modelId) await selectRun(run.id);}
    catch (error) {status(state.run); throw error;}
  }));
  for (const kind of ['nodes', 'links', 'subcatchments']) $('layer-' + kind).addEventListener('change', event => {
    const layer = state.layers[kind]; if (!layer) return;
    if (event.target.checked) layer.addTo(state.map); else state.map.removeLayer(layer);
  });
  $('layer-labels').addEventListener('change', event => {
    Object.values(state.layers).forEach(group => group.eachLayer(layer => {
      if (event.target.checked) layer.bindTooltip(escapeHTML(layer.options.objectId), {permanent: true, direction: 'top', className: 'object-label'});
      else layer.unbindTooltip();
    }));
  });
  $('layer-basemap').addEventListener('change', () => {localStorage.setItem('pl-swmm-basemap', $('layer-basemap').value); syncBasemap();});
  $('fit-map').addEventListener('click', fitMap);
  $('map-theme').addEventListener('change', () => {
    if ($('map-theme').value === 'flow' && !Object.keys(state.run?.summary?.link_peaks || {}).length) {toast('Peak-flow colouring requires a completed run with a network summary.'); $('map-theme').value = 'network';}
    colourMap();
  });
  $('download-input').addEventListener('click', () => download(`/api/models/${state.model.id}/input`));
  $('download-bundle').addEventListener('click', () => download(`/api/models/${state.model.id}/bundle`));
  $('attach-companions').addEventListener('click', () => $('attach-files').click());
  $('attach-files').addEventListener('change', attempt(async event => {
    const files = event.target.files;
    if (!files.length || !state.model) return;
    const id = state.model.id;
    const body = new FormData(); for (const file of files) body.append('companions', file);
    $('attach-companions').disabled = true;
    try {
      const model = await api(`/api/models/${id}/companions`, {method: 'POST', body});
      if (state.model?.id === id) {await refreshModels(); await loadModel(model.id);}
      toast('Project files added as a new model revision. Previous models and runs are retained.');
    } finally {event.target.value = ''; $('attach-companions').disabled = !state.model;}
  }));
  $('export-gis').addEventListener('click', () => {if (state.model) download(`/api/models/${state.model.id}/geojson`);});
  $('result-kind').addEventListener('change', attempt(async () => {updateResultOptions(); await loadSeries();}));
  for (const id of ['result-element', 'result-metric']) $(id).addEventListener('change', attempt(loadSeries));
  $('export-csv').addEventListener('click', () => {if (state.series) download(seriesURL('csv'));});
  for (const [id, kind] of [['download-report', 'report'], ['download-output', 'output'], ['download-log', 'log']]) $(id).addEventListener('click', () => {if (state.run) download(`/api/runs/${state.run.id}/files/${kind}`);});
  $('gis-import').addEventListener('click', () => {
    if (state.model?.network.map_mode !== 'geographic') {toast('Import a model with its source CRS before adding WGS84 overlays.'); return;}
    $('gis-file').click();
  });
  $('gis-file').addEventListener('change', attempt(async event => {
    const file = event.target.files[0]; if (!file) return;
    try {
      if (file.size > 8 * 1024 * 1024) throw new Error('Overlay exceeds the 8 MiB limit.');
      const modelId = state.model?.id;
      const data = JSON.parse(await file.text()); validateGeoJSON(data);
      if (state.model?.id !== modelId) return;
      const layer = L.geoJSON(data, {style: {color: '#ac8857', weight: 2, fillOpacity: .08}, pointToLayer: (_, latlng) => L.circleMarker(latlng, {radius: 4, color: '#ac8857'})}).addTo(state.map);
      const row = document.createElement('div'); row.className = 'overlay-row';
      const label = document.createElement('span'); label.textContent = file.name;
      const remove = document.createElement('button'); remove.textContent = '×'; remove.setAttribute('aria-label', 'Remove ' + file.name);
      remove.addEventListener('click', () => {state.map.removeLayer(layer); row.remove();});
      row.append(label, remove); $('overlay-list').append(row);
      toast('GIS overlay added to this view. It does not change the hydraulic model.');
    } finally {event.target.value = '';}
  }));
}

async function start() {
  if (!window.L) {toast('Map library missing. Run npm ci in the repository and refresh.'); return;}
  bindEvents(); renderMap();
  try {
    const health = await api('/api/health');
    $('engine-version').textContent = health.engine; $('connection-status').textContent = 'Engine connected';
    const models = await refreshModels();
    const previous = localStorage.getItem('pl-swmm-model');
    const model = models.find(m => m.id === previous) || models[0];
    if (model) await loadModel(model.id);
  } catch (error) {$('connection-status').textContent = 'Connection unavailable'; toast(error.message);}
}
start();
