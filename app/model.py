"""Read SWMM geometry without rewriting the input model."""
import math
import re
from collections import defaultdict

from pyproj import CRS, Transformer


NODE_SECTIONS = {"JUNCTIONS": "junction", "OUTFALLS": "outfall", "STORAGE": "storage", "DIVIDERS": "divider"}
LINK_SECTIONS = {"CONDUITS": "conduit", "PUMPS": "pump", "ORIFICES": "orifice", "WEIRS": "weir", "OUTLETS": "outlet"}

# Prefixes match the engine's input.c/text.h section matching, in engine order.
SECTION_PREFIXES = [tuple(pair.split(':')) for pair in (
    'TITLE:TITLE OPTION:OPTIONS FILE:FILES RAINGAGE:RAINGAGES TEMPERATURE:TEMPERATURE '
    'EVAP:EVAPORATION SUBCATCHMENT:SUBCATCHMENTS SUBAREA:SUBAREAS INFIL:INFILTRATION '
    'AQUIFER:AQUIFERS GROUNDWATER:GROUNDWATER SNOWPACK:SNOWPACKS JUNC:JUNCTIONS '
    'OUTFALL:OUTFALLS STORAGE:STORAGE DIVIDER:DIVIDERS CONDUIT:CONDUITS PUMP:PUMPS '
    'ORIFICE:ORIFICES WEIR:WEIRS OUTLET:OUTLETS XSECT:XSECTIONS TRANSECT:TRANSECTS '
    'LOSS:LOSSES CONTROL:CONTROLS POLLUT:POLLUTANTS LANDUSE:LANDUSES BUILDUP:BUILDUP '
    'WASHOFF:WASHOFF COVERAGE:COVERAGES INFLOW:INFLOWS DWF:DWF PATTERN:PATTERNS RDII:RDII '
    'HYDROGRAPH:HYDROGRAPHS LOADING:LOADINGS TREATMENT:TREATMENT CURVE:CURVES '
    'TIMESERIES:TIMESERIES REPORT:REPORT COORDINATE:COORDINATES VERTICES:VERTICES '
    'POLYGON:POLYGONS LABEL:LABELS SYMBOL:SYMBOLS BACKDROP:BACKDROP TAG:TAGS PROFILE:PROFILES '
    'MAP:MAP LID_CONTROL:LID_CONTROLS LID_USAGE:LID_USAGE GWF:GWF ADJUSTMENT:ADJUSTMENTS '
    'EVENT:EVENTS STREET:STREETS INLET_USAGE:INLET_USAGE INLET:INLETS'
).split()]


def tokenize(line):
    """Match SWMM's double-quote tokenizer; single quotes are ordinary text."""
    tokens = []
    while line:
        line = line.lstrip(" \t\r\n")
        if not line:
            break
        if line[0] == '"':
            end = line.find('"', 1)
            if end < 0:
                raise ValueError("Unclosed quotation in model input.")
            tokens.append(line[1:end])
            line = line[end + 1:]
        else:
            match = re.match(r"[^ \t\r\n]+", line)
            tokens.append(match[0])
            line = line[len(match[0]):]
    return tokens


def parse_model(text: str, crs: str | None = None, *, allow_external: bool = False) -> dict:
    if any(ord(c) < 32 and c not in '\t\r\n' for c in text) or re.search(r'\r(?!\n)', text):
        raise ValueError("Model input contains unsupported control characters.")
    sections = defaultdict(list)
    section = ""
    for line in text.split('\n'):
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        tokens = tokenize(line)
        if tokens and tokens[0].startswith("["):
            header = tokens[0][1:].upper()
            section = next((canonical for prefix, canonical in SECTION_PREFIXES if header.startswith(prefix)), None)
            if section is None:
                raise ValueError("Unknown SWMM input section: " + line[:100])
            continue
        if section == "TITLE":
            continue
        # Server-side paths are never accepted. Package support can be added later.
        external = (section == "FILES" or
                    section == "RAINGAGES" and len(tokens) > 4 and tokens[4].upper().startswith("FILE") or
                    section == "TIMESERIES" and len(tokens) > 1 and tokens[1].upper() == "FILE" or
                    section in {"TEMPERATURE", "EVAPORATION", "BACKDROP"} and tokens and tokens[0].upper().startswith("FILE"))
        if external and (not allow_external or section == "FILES"):
            raise ValueError("External FILE references are unsupported. Embed rainfall and time series in the INP file.")
        if section == "LID_USAGE" and len(tokens) > 8 and tokens[8] != "*":
            raise ValueError("External LID report paths are unsupported. Omit the report filename or replace it with *.")
        if tokens:
            sections[section].append(tokens)
    if not any(sections[s] for s in NODE_SECTIONS):
        raise ValueError("No SWMM nodes found. Upload a complete .inp model.")

    transform = None
    if crs:
        source = CRS.from_user_input(crs)
        transform = Transformer.from_crs(source, "EPSG:4326", always_xy=True)

    def point(row):
        if len(row) < 3:
            raise ValueError("Geometry rows must contain an ID, X and Y.")
        x, y = float(row[1]), float(row[2])
        if transform:
            x, y = transform.transform(x, y, errcheck=True)
            if not (-180 <= x <= 180 and -90 <= y <= 90):
                raise ValueError("Coordinates are outside WGS84 bounds. Check the source CRS.")
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Coordinates must be finite numbers.")
        return [x, y]

    coordinates = {r[0]: point(r) for r in sections["COORDINATES"]}
    vertices, polygons = defaultdict(list), defaultdict(list)
    for row in sections["VERTICES"]:
        vertices[row[0]].append(point(row))
    for row in sections["POLYGONS"]:
        polygons[row[0]].append(point(row))

    nodes, links, catchments = [], [], []
    for sec, kind in NODE_SECTIONS.items():
        for row in sections[sec]:
            nodes.append({"id": row[0], "kind": kind, "coordinate": coordinates.get(row[0]), "parameters": row[1:]})
    for sec, kind in LINK_SECTIONS.items():
        for row in sections[sec]:
            if len(row) < 3:
                raise ValueError(f"Incomplete link definition: {row[0]}")
            coords = []
            if row[1] in coordinates and row[2] in coordinates:
                coords = [coordinates[row[1]], *vertices[row[0]], coordinates[row[2]]]
            links.append({"id": row[0], "kind": kind, "from": row[1], "to": row[2], "coordinates": coords, "parameters": row[3:]})
    for row in sections["SUBCATCHMENTS"]:
        coords = polygons[row[0]]
        if len(coords) >= 3 and coords[0] != coords[-1]:
            coords = [*coords, coords[0]]
        catchments.append({"id": row[0], "kind": "subcatchment", "coordinates": coords if len(coords) >= 4 else [], "parameters": row[1:]})
    return {"nodes": nodes, "links": links, "subcatchments": catchments,
            "crs": crs, "map_mode": "geographic" if crs else "local",
            "has_geometry": bool(coordinates), "options": {r[0]: " ".join(r[1:]) for r in sections["OPTIONS"]}}


def as_geojson(network: dict) -> dict:
    if network["map_mode"] != "geographic":
        raise ValueError("Set the model's source CRS before exporting WGS84 GeoJSON.")
    features = []
    for category, geometry, key in [("nodes", "Point", "coordinate"), ("links", "LineString", "coordinates"), ("subcatchments", "Polygon", "coordinates")]:
        for item in network[category]:
            coords = item[key]
            if not coords:
                continue
            if geometry == "Polygon":
                coords = [coords]
            features.append({"type": "Feature", "properties": {"id": item["id"], "kind": item["kind"]}, "geometry": {"type": geometry, "coordinates": coords}})
    return {"type": "FeatureCollection", "features": features}
