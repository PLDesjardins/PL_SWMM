"""Targeted INP edits: retain unrelated lines, validate in an isolated solver."""
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from app.model import SECTION_PREFIXES, tokenize, parse_model
from app.importing import prepare_model


# Labels exclude the immutable object ID. Variable layouts are described below.
FIELDS = {
    'JUNCTIONS': ['Invert elevation', 'Maximum depth', 'Initial depth', 'Surcharge depth', 'Ponded area'],
    'OUTFALLS': ['Invert elevation', 'Boundary type'],
    'STORAGE': ['Invert elevation', 'Maximum depth', 'Initial depth', 'Shape'],
    'DIVIDERS': ['Invert elevation', 'Diverted link', 'Divider type'],
    'CONDUITS': ['From node', 'To node', 'Length', 'Manning roughness', 'Inlet offset', 'Outlet offset', 'Initial flow', 'Maximum flow'],
    'PUMPS': ['From node', 'To node', 'Pump curve', 'Initial status', 'Startup depth', 'Shutoff depth'],
    'ORIFICES': ['From node', 'To node', 'Type', 'Offset', 'Discharge coefficient', 'Flap gate', 'Opening time'],
    'WEIRS': ['From node', 'To node', 'Type', 'Crest height', 'Discharge coefficient', 'Flap gate', 'End contractions', 'End coefficient', 'Surcharge', 'Road width', 'Road surface', 'Coefficient curve'],
    'OUTLETS': ['From node', 'To node', 'Offset', 'Rating type'],
    'SUBCATCHMENTS': ['Rain gage', 'Outlet', 'Area', 'Impervious (%)', 'Width', 'Slope (%)', 'Curb length', 'Snow pack'],
    'RAINGAGES': ['Rain format', 'Recording interval', 'Snow catch factor', 'Source type'],
    'XSECTIONS': ['Shape', 'Geometry 1 / curve / transect', 'Geometry 2', 'Geometry 3', 'Geometry 4', 'Barrels', 'Culvert code'],
    'LOSSES': ['Entry loss', 'Exit loss', 'Average loss', 'Flap gate', 'Seepage rate'],
    'SUBAREAS': ['Impervious roughness', 'Pervious roughness', 'Impervious depression storage', 'Pervious depression storage', 'Zero storage (%)', 'Route to', 'Percent routed'],
    'INFILTRATION': [], 'INFLOWS': ['Constituent', 'Time series', 'Type', 'Mass conversion factor', 'Scale factor', 'Baseline', 'Pattern'],
    'DWF': ['Constituent', 'Baseline', 'Pattern 1', 'Pattern 2', 'Pattern 3', 'Pattern 4'],
    'TREATMENT': ['Pollutant', 'Treatment expression'],
    'COORDINATES': ['X', 'Y'], 'VERTICES': ['X', 'Y'], 'POLYGONS': ['X', 'Y'],
    'CURVES': [], 'TIMESERIES': [],
    'GROUNDWATER': [], 'GWF': [], 'RDII': [], 'LID_USAGE': [], 'COVERAGES': [], 'LOADINGS': [],
}
HINTS = {
    'OUTFALLS': 'FREE/NORMAL: flap gate, optional route-to. FIXED/TIDAL/TIMESERIES: stage value / curve / series, flap gate, optional route-to.',
    'STORAGE': 'After shape: TABULAR uses a curve name; FUNCTIONAL uses coefficient, exponent, constant; CYLINDRICAL/CONICAL/PARABOLOID/PYRAMIDAL use length, width and a shape parameter (side slope or paraboloid height). Then surcharge depth, evaporation factor and optional seepage parameters.',
    'DIVIDERS': 'After type: CUTOFF uses cutoff flow; TABULAR uses a curve; WEIR uses minimum flow, maximum depth, coefficient; OVERFLOW has no type parameters. Then maximum depth, initial depth, surcharge depth, ponded area.',
    'OUTLETS': 'TABULAR/DEPTH or TABULAR/HEAD: curve name, flap gate. FUNCTIONAL/DEPTH or FUNCTIONAL/HEAD: coefficient, exponent, flap gate.',
    'RAINGAGES': 'TIMESERIES: series name. FILE: filename, station ID, rain units. Attach referenced files using + Project files.',
    'INFILTRATION': 'Parameters follow the model infiltration method: Horton, Modified Horton, Green-Ampt, Modified Green-Ampt, or Curve Number. An optional method can override the model setting.',
    'CURVES': 'First row: TYPE X Y. Following rows: X Y. Types: STORAGE, DIVERSION, TIDAL, RATING, CONTROL, SHAPE, WEIR, PUMP1–PUMP5. X must increase. Do not include the curve name.',
    'TIMESERIES': 'One observation per row: MM/DD/YYYY HH:MM VALUE, or HH:MM VALUE (date carried forward). Decimal elapsed hours are also supported. For external data: FILE "filename.dat". Do not include the series name.',
}
PRIMARY = set(list(FIELDS)[:11])
RELATED = {
    'JUNCTIONS': ['INFLOWS', 'DWF', 'TREATMENT', 'RDII', 'COORDINATES'],
    'OUTFALLS': ['INFLOWS', 'DWF', 'TREATMENT', 'COORDINATES'],
    'STORAGE': ['INFLOWS', 'DWF', 'TREATMENT', 'RDII', 'COORDINATES'],
    'DIVIDERS': ['INFLOWS', 'DWF', 'TREATMENT', 'RDII', 'COORDINATES'],
    'CONDUITS': ['XSECTIONS', 'LOSSES', 'VERTICES'],
    'ORIFICES': ['XSECTIONS', 'VERTICES'], 'WEIRS': ['XSECTIONS', 'VERTICES'],
    'PUMPS': ['VERTICES'], 'OUTLETS': ['VERTICES'],
    'SUBCATCHMENTS': ['SUBAREAS', 'INFILTRATION', 'GROUNDWATER', 'GWF', 'LID_USAGE', 'COVERAGES', 'LOADINGS', 'POLYGONS'],
}


def scan(text):
    section = ''
    for index, line in enumerate(text.splitlines(keepends=True)):
        tokens = tokenize(line.split(';', 1)[0].strip())
        if tokens and tokens[0].startswith('['):
            header = tokens[0][1:].upper()
            section = next((s for prefix, s in SECTION_PREFIXES if header.startswith(prefix)), '')
            yield index, section, None
        elif tokens:
            yield index, section, tokens


def groups(text):
    result = defaultdict(lambda: defaultdict(list))
    for _, section, row in scan(text):
        if row and section in FIELDS:
            result[section][row[0]].append(row[1:])
    return result


def catalog(text):
    data = groups(text)
    return {'sections': [{'name': section, 'labels': labels, 'hint': HINTS.get(section, ''),
                           'items': [{'id': name, 'rows': len(rows)} for name, rows in data[section].items()]}
                          for section, labels in FIELDS.items()]}


def detail(text, section, identifier):
    if section not in FIELDS:
        raise ValueError('Unsupported editable section.')
    data = groups(text)
    if identifier not in data[section]:
        raise ValueError('Object not found in this section.')
    return {'section': section, 'id': identifier, 'rows': data[section][identifier],
            'related': [{'section': other, 'id': identifier, 'rows': data[other].get(identifier, [])}
                        for other in RELATED.get(section, [])]}


def quote(token):
    if not token or any(ord(c) < 32 or c in ';"[]' for c in token):
        raise ValueError('Fields cannot be empty or contain control characters, semicolons, brackets, or double quotes. Omit unused trailing fields.')
    return '"' + token + '"' if any(c.isspace() for c in token) else token


def edit(text, changes):
    data = groups(text)
    replacements = {}
    for change in changes:
        section, identifier, rows = change.section, change.id, change.rows
        if section not in FIELDS:
            raise ValueError('Unsupported editable section.')
        quote(identifier)
        if len(identifier.encode('utf-8')) > 31 or any(c.isspace() for c in identifier):
            raise ValueError('IDs must be 1–31 bytes with no spaces.')
        key = (section, identifier)
        if key in replacements:
            raise ValueError('Each object can be edited only once per save.')
        exists = identifier in data[section]
        if change.create:
            if section not in {'CURVES', 'TIMESERIES'}:
                raise ValueError('Only curves and time series can be created here.')
            if any(name.casefold() == identifier.casefold() for name in data[section]):
                raise ValueError('This name already exists. Choose it from the list to edit it.')
        elif not exists and not any(identifier in data[parent] and section in related for parent, related in RELATED.items()):
            raise ValueError('Object not found. Existing object names cannot be changed.')
        if section in PRIMARY | {'CURVES', 'TIMESERIES'} and not rows:
            raise ValueError('An object, curve, or time series must contain data.')
        if section in PRIMARY and len(rows) != 1:
            raise ValueError('Network objects must have exactly one parameter row.')
        if any(not row for row in rows):
            raise ValueError('Remove empty rows before saving.')
        replacements[key] = [' '.join([quote(identifier), *(quote(value) for value in row)]) for row in rows]
    # Place replacements at the first matching row; keep inline comments and
    # all other lines intact. Repeated section headings are supported.
    lines = text.splitlines(keepends=True)
    output = []
    emitted = set()
    for index, section, row in scan(text):
        if not row or (section, row[0]) not in replacements:
            continue
        key = (section, row[0])
        old = lines[index]
        newline = '\r\n' if old.endswith('\r\n') else '\n'
        comment = ';' + old.split(';', 1)[1].rstrip('\r\n') if ';' in old else ''
        if key not in emitted:
            lines[index] = ''.join(value + ((' ' + comment) if i == 0 and comment else '') + newline
                                   for i, value in enumerate(replacements[key]))
            if not replacements[key] and comment:
                lines[index] = comment + newline
            emitted.add(key)
        else:
            lines[index] = comment + newline if comment else ''
    output.extend(lines)
    for (section, identifier), rows in replacements.items():
        if (section, identifier) not in emitted:
            output.append('\n[' + section + ']\n' + '\n'.join(rows) + '\n')
    return ''.join(output)


def validate(text, crs, companions, root):
    parse_model(text, crs, allow_external=True)
    prepared, assets, missing = prepare_model(text, companions)
    if missing:
        return 'pending_missing_files'
    with tempfile.TemporaryDirectory(prefix='pl-swmm-validate-') as tmp:
        path = Path(tmp)
        (path / 'model.inp').write_text(prepared, encoding='utf-8')
        for relative, value in assets.items():
            target = path / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(value)
        try:
            result = subprocess.run([sys.executable, '-m', 'app.validate', str(path)], cwd=root,
                                    capture_output=True, timeout=20)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('SWMM validation exceeded 20 seconds. The edit was not saved.') from exc
        if result.returncode:
            report = (path / 'report.rpt').read_text(errors='replace') if (path / 'report.rpt').exists() else ''
            errors = re.findall(r'ERROR[^\n]*(?:\n[^\n]*)?', report)
            message = '\n'.join(errors) or 'SWMM could not validate the model.'
            raise ValueError('Edit not saved. SWMM 5.2.4 input validation:\n' + message[:5000])
    return 'engine_checked'
