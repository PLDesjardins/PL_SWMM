"""Read actual SWMM binary results, including the file's engine and units."""
import math
from contextlib import contextmanager
from datetime import datetime, timedelta

from swmm.toolkit import output
from swmm.toolkit.shared_enum import ElementType, LinkAttribute, NodeAttribute, SubcatchAttribute, Time

FLOW_UNITS = ["CFS", "GPM", "MGD", "CMS", "LPS", "MLD"]
KINDS = {
    "nodes": (ElementType.NODE, 1, output.get_node_series, {"depth": NodeAttribute.INVERT_DEPTH, "head": NodeAttribute.HYDRAULIC_HEAD, "inflow": NodeAttribute.TOTAL_INFLOW, "flooding": NodeAttribute.FLOODING_LOSSES}),
    "links": (ElementType.LINK, 2, output.get_link_series, {"flow": LinkAttribute.FLOW_RATE, "depth": LinkAttribute.FLOW_DEPTH, "velocity": LinkAttribute.FLOW_VELOCITY, "capacity": LinkAttribute.CAPACITY}),
    "subcatchments": (ElementType.SUBCATCH, 0, output.get_subcatch_series, {"runoff": SubcatchAttribute.RUNOFF_RATE, "rainfall": SubcatchAttribute.RAINFALL, "infiltration": SubcatchAttribute.INFIL_LOSS}),
}


@contextmanager
def opened(path):
    handle = output.init()
    try:
        output.open(handle, str(path))
        yield handle
    finally:
        output.close(handle)


def names(handle, kind):
    etype, size_index, _, _ = KINDS[kind]
    return [output.get_elem_name(handle, etype, i) for i in range(output.get_proj_size(handle)[size_index])]


def units(handle):
    flags = output.get_units(handle)
    flow = FLOW_UNITS[flags[1]]
    return {"flow": flow, "length": "ft" if flags[1] < 3 else "m", "rainfall": "in/hr" if flags[1] < 3 else "mm/hr"}


def series(path, kind, element, metric):
    if kind not in KINDS or metric not in KINDS[kind][3]:
        raise ValueError("Unsupported result category or metric.")
    with opened(path) as handle:
        ids = names(handle, kind)
        if element not in ids:
            raise ValueError("Element is absent from the binary output. Check the model's REPORT selections.")
        periods = output.get_times(handle, Time.NUM_PERIODS)
        if periods < 1:
            raise ValueError("The simulation produced no reporting periods.")
        if periods > 100_000:
            raise ValueError("Result exceeds 100,000 reporting periods. Increase REPORT_STEP for this preview.")
        vals = KINDS[kind][2](handle, ids.index(element), KINDS[kind][3][metric], 0, periods - 1)
        dates = output.get_date_series(handle, 0, periods - 1)
        u = units(handle)
        unit = u["flow"] if metric in {"flow", "inflow", "runoff", "flooding"} else u["rainfall"] if metric in {"rainfall", "infiltration"} else "fraction" if metric == "capacity" else u["length"] + "/s" if metric == "velocity" else u["length"]
        return {"element": element, "kind": kind, "metric": metric, "unit": unit,
                "timestamps": [(datetime(1899, 12, 30) + timedelta(days=d)).isoformat(timespec="seconds") for d in dates],
                "values": [v if math.isfinite(v) else None for v in vals]}


def summarize(path):
    with opened(path) as handle:
        periods = output.get_times(handle, Time.NUM_PERIODS)
        if periods < 1 or periods > 100_000:
            raise ValueError("Simulation must produce between 1 and 100,000 reporting periods.")
        elements = {kind: names(handle, kind) for kind in KINDS}
        peak_flow, peak_depth, flooded = 0.0, 0.0, 0
        link_peaks, node_peaks = {}, {}
        # Bounded summary; large networks still expose all individual series.
        summarize_peaks = len(elements["nodes"]) + len(elements["links"]) <= 2000 and periods * (len(elements["nodes"]) + len(elements["links"])) <= 2_000_000
        if summarize_peaks:
            for i in range(len(elements["links"])):
                peak = max(abs(v) for v in output.get_link_series(handle, i, LinkAttribute.FLOW_RATE, 0, periods - 1))
                link_peaks[elements["links"][i]] = peak
                peak_flow = max(peak_flow, peak)
            for i in range(len(elements["nodes"])):
                peak = max(output.get_node_series(handle, i, NodeAttribute.INVERT_DEPTH, 0, periods - 1))
                node_peaks[elements["nodes"][i]] = peak
                peak_depth = max(peak_depth, peak)
                flooded += int(max(output.get_node_series(handle, i, NodeAttribute.FLOODING_LOSSES, 0, periods - 1)) > 1e-6)
        return {"engine": output.get_version(handle), "periods": periods, "report_step_seconds": output.get_times(handle, Time.REPORT_STEP),
                "units": units(handle), "elements": elements, "peak_link_flow": peak_flow if summarize_peaks else None,
                "peak_node_depth": peak_depth if summarize_peaks else None, "flooded_nodes": flooded if summarize_peaks else None,
                "link_peaks": link_peaks, "node_peaks": node_peaks}
