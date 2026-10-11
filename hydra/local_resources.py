"""Per-worker CPU admission, without pretending utilization equals power draw."""
from __future__ import annotations

import math
import os


def cpu_budget():
    fraction = float(os.environ.get('HYDRA_LOCAL_CPU_FRACTION', '0.15'))
    if not math.isfinite(fraction) or not 0 < fraction <= 1:
        raise ValueError('HYDRA_LOCAL_CPU_FRACTION must be >0 and <=1')
    import psutil
    process = psutil.Process()
    count = psutil.cpu_count() or 1
    if not hasattr(process, 'cpu_affinity'):
        return {'status': 'unsupported', 'requested_cpu_fraction': fraction,
                'note': 'No affinity backend; no CPU or power limit claimed.'}
    available = process.cpu_affinity()
    cores = available[:max(1, min(len(available), math.floor(count * fraction)))]
    process.cpu_affinity(cores)
    for variable in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[variable] = str(len(cores))
    return {'status': 'affinity_applied', 'logical_cpus': cores, 'machine_logical_cpus': count,
            'requested_cpu_fraction': fraction, 'max_logical_cpu_share': len(cores) / count,
            'note': 'Scheduling affinity, not a wattage guarantee. Minimum allocation is one logical CPU.'}
