"""PCSWMM imports: preserve original files and resolve only uploaded assets."""
import configparser
import hashlib
import re
import sqlite3
import tempfile
from pathlib import Path

from app.model import SECTION_PREFIXES, tokenize


def basename(name):
    name = name.replace('\\', '/').split('/')[-1]
    if not name or name in {'.', '..'} or any(ord(c) < 32 for c in name):
        raise ValueError('Invalid uploaded filename.')
    return name


def decode_text(data):
    try:
        return data.decode('utf-8-sig'), 'UTF-8'
    except UnicodeDecodeError:
        try:
            return data.decode('cp1252'), 'Windows-1252'
        except UnicodeDecodeError as exc:
            raise ValueError('Model text must use UTF-8 or Windows-1252 encoding.') from exc


def references(text):
    """Locate supported read-only FILE references, using engine section rules."""
    section = ''
    refs = []
    for index, raw in enumerate(text.split('\n')):
        line = raw.split(';', 1)[0].strip()
        if not line:
            continue
        tokens = tokenize(line)
        if tokens[0].startswith('['):
            header = tokens[0][1:].upper()
            section = next((v for k, v in SECTION_PREFIXES if header.startswith(k)), '')
            continue
        column = None
        if section == 'TIMESERIES' and len(tokens) >= 3 and tokens[1].upper() == 'FILE':
            column = 2
        elif section == 'RAINGAGES' and len(tokens) >= 6 and tokens[4].upper().startswith('FILE'):
            column = 5
        elif section == 'TEMPERATURE' and len(tokens) >= 2 and tokens[0].upper().startswith('FILE'):
            column = 1
        if column is not None:
            refs.append({'line': index, 'column': column, 'tokens': tokens, 'name': basename(tokens[column]), 'section': section})
    return refs


def ini_epsg(data):
    text, _ = decode_text(data)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(text)
    section = next((s for s in parser.sections() if s.casefold() == 'general'), None)
    value = parser.get(section, 'Map EPSG', fallback='') if section else ''
    return 'EPSG:' + value.strip() if re.fullmatch(r'\d+', value.strip()) else None


def db_epsg(data):
    # No extensions, writes, attachments, or arbitrary queries. Imported result
    # tables are never presented as results of a new engine run.
    with tempfile.TemporaryDirectory(prefix='pl-swmm-metadata-') as tmp:
        path = Path(tmp) / 'metadata.db'
        path.write_bytes(data)
        with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as connection:
            connection.enable_load_extension(False)
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.execute('PRAGMA query_only=ON')
            connection.set_progress_handler(lambda: 1, 100_000)
            rows = connection.execute("SELECT Value FROM Settings WHERE Name='Map EPSG' LIMIT 1").fetchall()
            value = str(rows[0][0]).strip() if rows else ''
            return 'EPSG:' + value if re.fullmatch(r'\d+', value) else None


def detect_crs(model_name, files, override=None):
    stem = Path(model_name).stem.casefold()
    candidates, warnings = [], []
    for extension in ['.chi', '.db', '.ini']:
        for name, data in files.items():
            if Path(name).stem.casefold() != stem or Path(name).suffix.casefold() != extension:
                continue
            try:
                crs = db_epsg(data) if extension == '.db' else ini_epsg(data)
                if crs:
                    candidates.append((crs, name))
            except (ValueError, configparser.Error, sqlite3.Error):
                warnings.append(f'Could not read coordinate metadata from {name}; original file retained.')
    selected, source = (override, 'Entered on import') if override else candidates[0] if candidates else (None, None)
    for crs, name in candidates:
        if crs != selected:
            warnings.append(f'{name} specifies {crs}; using {selected} from {source}.')
    return selected, source, warnings


def prepare_model(text, files):
    """Return a runnable copy with safe relative paths, plus missing filenames.

    Full Windows/Unix paths in the source are used ONLY as matching labels.
    Their targets are never read from or written to the server filesystem.
    """
    supplied = {name.casefold(): (name, data) for name, data in files.items()}
    lines = text.split('\n')
    assets, missing = {}, []
    origins = {}
    for ref in references(text):
        key = ref['name'].casefold()
        origin = ref['tokens'][ref['column']].replace('\\', '/').casefold()
        if key in origins and origins[key] != origin:
            raise ValueError('Different external paths use the same filename: ' + ref['name'] + '. Give these inputs distinct filenames before importing.')
        origins[key] = origin
        match = supplied.get(ref['name'].casefold())
        if match is None:
            if ref['name'] not in missing:
                missing.append(ref['name'])
            # Remove inaccessible paths even in a blocked prepared copy.
            replacement = 'assets/missing-' + hashlib.sha256(ref['name'].encode()).hexdigest()[:20] + '.dat'
        else:
            _, data = match
            replacement = 'assets/' + hashlib.sha256(data).hexdigest() + '.dat'
            assets[replacement] = data
        tokens = ref['tokens'].copy()
        tokens[ref['column']] = replacement
        lines[ref['line']] = ' '.join('"' + t + '"' if re.search(r'\s', t) else t for t in tokens)
    return '\n'.join(lines), assets, missing
