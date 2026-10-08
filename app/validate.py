"""Read and validate inputs without running a simulation, in a fresh process."""
import os
import sys
from swmm.toolkit import solver

if __name__ == '__main__':
    os.chdir(sys.argv[1])
    if solver.swmm_get_version() != 52004:
        raise RuntimeError('Expected EPA SWMM 5.2.4.')
    try:
        solver.swmm_open('model.inp', 'report.rpt', 'results.out')
        solver.swmm_start(0)
        solver.swmm_end()
    finally:
        solver.swmm_close()
