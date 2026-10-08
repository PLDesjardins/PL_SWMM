/* INP data editor. Only explicitly changed sections are sent to the server. */
let editorCatalog = null;
let editingRecord = null;
const objectSections = {junction: 'JUNCTIONS', outfall: 'OUTFALLS', storage: 'STORAGE', divider: 'DIVIDERS', conduit: 'CONDUITS', pump: 'PUMPS', orifice: 'ORIFICES', weir: 'WEIRS', outlet: 'OUTLETS', subcatchment: 'SUBCATCHMENTS'};
const sectionTitle = section => ({TIMESERIES: 'Time series', RAINGAGES: 'Rain gages', XSECTIONS: 'Cross sections', SUBAREAS: 'Subcatchment subareas', DWF: 'Dry-weather inflows'}[section] || section.toLowerCase().replaceAll('_', ' '));

async function refreshEditor() {
  if (!state.model) { $('data-list').textContent = 'Import a model to edit its data.'; return; }
  const id = state.model.id;
  const catalog = await api(`/api/models/${id}/editor`);
  if (id !== state.model?.id) return;
  editorCatalog = {...catalog, modelId: id};
  const previous = $('data-section').value || 'TIMESERIES';
  $('data-section').innerHTML = catalog.sections.map(s => `<option value="${s.name}">${escapeHTML(sectionTitle(s.name))} (${s.items.length})</option>`).join('');
  $('data-section').value = previous;
  renderDataList();
}

function renderDataList() {
  const section = editorCatalog?.sections.find(s => s.name === $('data-section').value);
  if (!section) return;
  const term = $('data-search').value.toLowerCase();
  const items = section.items.filter(item => item.id.toLowerCase().includes(term));
  $('data-list').replaceChildren();
  if (!items.length) { $('data-list').textContent = 'No matching items in this section.'; return; }
  for (const item of items) {
    const button = document.createElement('button'); button.className = 'data-item';
    const name = document.createElement('strong'); name.textContent = item.id;
    const count = document.createElement('span'); count.textContent = `${item.rows} ${item.rows === 1 ? 'row' : 'rows'} · Edit →`;
    button.append(name, count); button.addEventListener('click', attempt(() => openEditor(section.name, item.id)));
    $('data-list').append(button);
  }
}

async function editNetworkObject(obj) { await openEditor(objectSections[obj.kind], obj.id); }

function inpToken(value) { return /\s/.test(value) ? '"' + value + '"' : value; }
function rowsText(rows) { return rows.map(row => row.map(inpToken).join('\t')).join('\n'); }
function readRows(text) {
  // Mirror SWMM: only double quotes quote a field; semicolons start comments.
  return text.split(/\r?\n/).map(line => {
    line = line.split(';', 1)[0].trim();
    const tokens = [];
    while (line) {
      if (line.startsWith('"')) {
        const end = line.indexOf('"', 1);
        if (end < 0) throw new Error('Unclosed double quote in data.');
        tokens.push(line.slice(1, end)); line = line.slice(end + 1).trimStart();
      } else {
        const token = line.match(/^\S+/)[0]; tokens.push(token); line = line.slice(token.length).trimStart();
      }
    }
    return tokens;
  }).filter(row => row.length);
}

async function openEditor(section, name = '', create = false) {
  if (!state.model) throw new Error('Import a model first.');
  await refreshEditor();
  const sourceId = state.model.id;
  const record = create ? {section, id: '', rows: [], related: []} : await api(`/api/models/${sourceId}/editor?section=${encodeURIComponent(section)}&item=${encodeURIComponent(name)}`);
  if (sourceId !== state.model?.id) return;
  editingRecord = {...record, sourceId, create, blocks: []};
  $('edit-name').value = name; $('edit-name').readOnly = !create;
  $('edit-title').textContent = create ? 'Add ' + (section === 'CURVES' ? 'curve' : 'time series') : 'Edit ' + name;
  $('edit-section-title').textContent = sectionTitle(section).toUpperCase();
  $('edit-error').hidden = true; $('edit-fields').replaceChildren();
  for (const group of [record, ...record.related]) addEditBlock(group);
  $('edit-dialog').showModal();
}

function fieldLabels(section, row, defaults) {
  const labels = [...defaults];
  if (section === 'OUTFALLS') {
    if (['FREE', 'NORMAL'].includes(row[1]?.toUpperCase())) labels.push('Flap gate', 'Route to subcatchment');
    else labels.push('Stage value / curve / time series', 'Flap gate', 'Route to subcatchment');
  }
  if (section === 'STORAGE') {
    const shape = row[3]?.toUpperCase();
    if (shape === 'TABULAR') labels.push('Storage curve');
    else if (shape === 'FUNCTIONAL') labels.push('Coefficient', 'Exponent', 'Constant');
    else labels.push('Length', 'Width', shape === 'PARABOLOID' ? 'Height' : shape === 'CYLINDRICAL' ? 'Unused shape parameter' : 'Side slope');
    labels.push('Surcharge depth', 'Evaporation factor', 'Suction head', 'Conductivity', 'Initial deficit');
  }
  if (section === 'OUTLETS') {
    if (row[3]?.toUpperCase().startsWith('TABULAR')) labels.push('Rating curve', 'Flap gate');
    else labels.push('Coefficient', 'Exponent', 'Flap gate');
  }
  if (section === 'RAINGAGES') {
    if (row[3]?.toUpperCase() === 'FILE') labels.push('Filename', 'Station ID', 'Rain units');
    else labels.push('Time series');
  }
  if (section === 'DIVIDERS') {
    const type = row[2]?.toUpperCase();
    if (type === 'CUTOFF') labels.push('Cutoff flow');
    if (type === 'TABULAR') labels.push('Diversion curve');
    if (type === 'WEIR') labels.push('Minimum flow', 'Maximum weir depth', 'Coefficient');
    labels.push('Maximum depth', 'Initial depth', 'Surcharge depth', 'Ponded area');
  }
  if (section === 'INFILTRATION') {
    const methods = ['HORTON', 'MODIFIED_HORTON', 'GREEN_AMPT', 'MODIFIED_GREEN_AMPT', 'CURVE_NUMBER'];
    const override = row.at(-1)?.toUpperCase();
    const method = methods.includes(override) ? override : state.model.network.options.INFILTRATION || 'HORTON';
    if (method.includes('HORTON')) labels.push('Maximum rate', 'Minimum rate', 'Decay constant', 'Drying time', 'Maximum volume', 'Method override');
    else if (method.includes('GREEN_AMPT')) labels.push('Suction head', 'Conductivity', 'Initial deficit', 'Method override');
    else labels.push('Curve number', 'Conductivity (unused)', 'Drying time', 'Method override');
  }
  return labels;
}

function addEditBlock(group) {
  const schema = editorCatalog.sections.find(s => s.name === group.section);
  const wrapper = document.createElement('section'); wrapper.className = 'edit-block';
  const title = document.createElement('h3'); title.textContent = sectionTitle(group.section); wrapper.append(title);
  const hint = document.createElement('p'); hint.className = 'muted small';
  hint.textContent = schema.hint || (schema.labels.length ? schema.labels.join(' · ') : 'Use the SWMM 5.2.4 parameters for this section.'); wrapper.append(hint);
  const block = {section: group.section, original: JSON.stringify(group.rows), fields: null, textarea: null};
  // Single-row object definitions receive labelled controls; every layout also
  // has a full row editor for optional fields and switching conditional types.
  if (group.rows.length === 1 && !['CURVES', 'TIMESERIES', 'TREATMENT'].includes(group.section) && schema.labels.length) {
    const row = group.rows[0]; const labels = fieldLabels(group.section, row, schema.labels);
    const grid = document.createElement('div'); grid.className = 'edit-grid';
    block.fields = row.map((value, i) => {
      const label = document.createElement('label'); label.textContent = labels[i] || `Parameter ${i + 1}`;
      const input = document.createElement('input'); input.value = value; input.setAttribute('aria-label', label.textContent);
      input.addEventListener('input', () => {block.textarea.value = rowsText([block.fields.map(f => f.value)]);});
      label.append(input); grid.append(label); return input;
    }); wrapper.append(grid);
  }
  const label = document.createElement('label'); label.className = 'field-label';
  label.textContent = block.fields ? 'Full parameter row (including optional fields)' : 'Data rows (without the object name)';
  const textarea = document.createElement('textarea'); textarea.className = 'edit-rows'; textarea.rows = ['CURVES', 'TIMESERIES'].includes(group.section) ? 12 : 3;
  textarea.spellcheck = false; textarea.value = rowsText(group.rows); textarea.setAttribute('aria-label', group.section + ' data rows');
  if (!group.rows.length) textarea.placeholder = 'Optional: add parameter rows here';
  if (group.section === 'CURVES') textarea.placeholder = 'STORAGE 0 0\n1 100\n2 200';
  if (group.section === 'TIMESERIES') textarea.placeholder = '01/01/2025 00:00 0\n01/01/2025 01:00 10';
  // Direct raw edits switch to a single source of truth; reopen after saving
  // to get labels appropriate to a changed shape/type.
  textarea.addEventListener('input', () => {block.fields?.forEach(input => input.disabled = true);});
  label.append(textarea); wrapper.append(label); block.textarea = textarea;
  if (['CURVES', 'TIMESERIES'].includes(group.section)) addObservationTable(group, block, wrapper);
  editingRecord.blocks.push(block); $('edit-fields').append(wrapper);
}

function addObservationTable(group, block, wrapper) {
  const curve = group.section === 'CURVES';
  if (group.rows.length > 200 || group.rows.some(row => ![2, 3].includes(row.length)) || (!curve && group.rows[0]?.[0]?.toUpperCase() === 'FILE')) return;
  const controls = document.createElement('div'); controls.className = 'observation-editor';
  const heading = document.createElement('h4'); heading.textContent = curve ? 'Curve points' : 'Observations'; controls.append(heading);
  let type = null;
  if (curve) {
    const label = document.createElement('label'); label.textContent = 'Curve type';
    type = document.createElement('select'); type.setAttribute('aria-label', 'Curve type');
    const types = ['STORAGE', 'DIVERSION', 'TIDAL', 'RATING', 'CONTROL', 'SHAPE', 'WEIR', 'PUMP1', 'PUMP2', 'PUMP3', 'PUMP4', 'PUMP5'];
    const existing = group.rows[0]?.length === 3 ? group.rows[0][0].toUpperCase() : 'STORAGE';
    if (!types.includes(existing)) types.push(existing);
    for (const value of types) {const option = document.createElement('option'); option.value = value; option.textContent = value; type.append(option);}
    type.value = existing; label.append(type); controls.append(label);
  }
  const table = document.createElement('table'); table.className = 'observation-table';
  const headers = curve ? ['X', 'Y'] : ['Date (optional)', 'Time / elapsed hours', 'Value'];
  const head = document.createElement('thead'); const hr = document.createElement('tr');
  for (const text of [...headers, '']) {const th = document.createElement('th'); th.textContent = text; hr.append(th);}
  head.append(hr); const body = document.createElement('tbody'); table.append(head, body); controls.append(table);
  const rows = [];
  const sync = () => {
    const values = rows.map((entry, index) => {
      const tokens = entry.inputs.map(input => input.value.trim());
      if (curve) return index === 0 ? [type.value, ...tokens] : tokens;
      return tokens[0] ? tokens : tokens.slice(1);
    });
    block.textarea.value = rowsText(values);
  };
  const add = values => {
    const tr = document.createElement('tr');
    const entry = {tr, inputs: headers.map((header, index) => {
      const td = document.createElement('td'); const input = document.createElement('input'); input.value = values[index] || '';
      input.setAttribute('aria-label', header); input.placeholder = !curve && index === 0 ? 'MM/DD/YYYY' : header;
      input.addEventListener('input', sync); td.append(input); tr.append(td); return input;
    })};
    const td = document.createElement('td'); const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'icon-button'; remove.textContent = '×'; remove.setAttribute('aria-label', 'Remove row');
    remove.addEventListener('click', () => {rows.splice(rows.indexOf(entry), 1); tr.remove(); sync();}); td.append(remove); tr.append(td);
    rows.push(entry); body.append(tr);
  };
  for (const row of group.rows) add(curve ? row.slice(-2) : row.length === 2 ? ['', ...row] : row);
  const addButton = document.createElement('button'); addButton.type = 'button'; addButton.className = 'button subtle'; addButton.textContent = curve ? '+ Add point' : '+ Add observation';
  addButton.addEventListener('click', () => {add(curve ? ['0', '0'] : ['', '00:00', '0']); sync(); rows.at(-1).inputs[0].focus();});
  type?.addEventListener('change', sync); controls.append(addButton);
  block.checkTable = () => {
    if (addButton.disabled) return;
    for (const entry of rows) {
      const required = curve ? entry.inputs : entry.inputs.slice(1);
      if (required.some(input => !input.value.trim())) throw new Error('Fill every X/Y or time/value cell, or remove its row.');
    }
  };
  // Put the table above the bulk editor. Raw edits disable table controls to
  // prevent an older table from replacing pasted data.
  wrapper.insertBefore(controls, block.textarea.parentElement);
  block.textarea.addEventListener('input', () => {
    controls.querySelectorAll('input,select,button').forEach(input => input.disabled = true);
    heading.textContent = 'Bulk data editing active (reopen to use table)';
  });
}

$('data-section').addEventListener('change', renderDataList);
$('data-search').addEventListener('input', renderDataList);
$('add-series').addEventListener('click', attempt(() => openEditor('TIMESERIES', '', true)));
$('add-curve').addEventListener('click', attempt(() => openEditor('CURVES', '', true)));
for (const id of ['close-edit', 'cancel-edit']) $(id).addEventListener('click', () => $('edit-dialog').close());
$('edit-dialog').addEventListener('cancel', event => {if ($('save-edit').disabled) event.preventDefault();});
$('edit-form').addEventListener('submit', async event => {
  event.preventDefault(); $('edit-error').hidden = true;
  const record = editingRecord;
  try {
    for (const block of record.blocks) {
      if (block.fields && !block.fields[0]?.disabled) {
        const values = block.fields.map(input => input.value.trim());
        while (values.length && !values.at(-1)) values.pop();
        if (values.some(value => !value)) throw new Error('Only optional trailing fields can be blank. Use * where the SWMM format requires a placeholder.');
        if (values.some(value => /[;"\[\]\x00-\x1f]/.test(value))) throw new Error('Parameter fields cannot contain quotes, semicolons, brackets, or control characters.');
        block.textarea.value = rowsText([values]);
      }
      block.checkTable?.();
    }
    const changes = record.blocks.map((block, index) => ({section: block.section, id: $('edit-name').value.trim(), rows: readRows(block.textarea.value), create: index === 0 && record.create}))
      .filter((change, index) => (index === 0 && record.create) || JSON.stringify(change.rows) !== record.blocks[index].original);
    if (!changes.length) throw new Error('No parameters have changed.');
    $('save-edit').disabled = true; $('save-edit').textContent = 'Checking with SWMM…';
    ['close-edit', 'cancel-edit'].forEach(id => $(id).disabled = true);
    const revised = await api(`/api/models/${record.sourceId}/edits`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({changes})});
    $('edit-dialog').close(); await refreshModels(); await loadModel(revised.id);
    showTab('data'); $('data-section').value = record.section;
    toast(revised.edit_validation === 'engine_checked' ? 'Revision saved and input checked with SWMM 5.2.4. Run it for new results.' : 'Revision saved. Attach missing files to validate and run it.');
  } catch (error) { $('edit-error').textContent = error.message; $('edit-error').hidden = false; }
  finally { $('save-edit').disabled = false; $('save-edit').textContent = 'Save new revision'; ['close-edit', 'cancel-edit'].forEach(id => $(id).disabled = false); }
});
