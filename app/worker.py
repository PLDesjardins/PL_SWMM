"""Each engine run gets a fresh process: SWMM's solver uses global state."""
import sys
import os
from pathlib import Path

from swmm.toolkit import solver

from app.results import summarize


def main():
    folder = Path(sys.argv[1]).resolve()
    # Uploaded references are rewritten to assets/ under this run directory.
    os.chdir(folder)
    if solver.swmm_get_version() != 52004:
        raise RuntimeError("This application requires EPA SWMM 5.2.4.")
    solver.swmm_run(str(folder / "model.inp"), str(folder / "report.rpt"), str(folder / "results.out"))
    summary = summarize(folder / "results.out")
    import json
    (folder / "summary.json").write_text(json.dumps(summary, allow_nan=False))


if __name__ == "__main__":
    main()
