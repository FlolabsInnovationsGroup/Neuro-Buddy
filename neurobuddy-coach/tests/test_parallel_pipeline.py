"""Recordings analyzed side by side give exactly the same results as one at a time."""
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

import mne
import numpy as np

from neurobuddy_v2.comparative import run_athlete_workflow


def fif(folder, index):
    t = np.arange(1500) / 100
    rng = np.random.default_rng(index)
    signal = np.vstack([(6 + index + k) * 1e-6 * np.sin(2 * np.pi * (8 + 2 * k) * t) + 1e-6 * rng.standard_normal(t.size)
                        for k in range(3)])
    raw = mne.io.RawArray(signal, mne.create_info(['C3', 'C4', 'Pz'], 100, 'eeg'), verbose='ERROR')
    path = folder / f'sub-01_task-rest_run-{index + 1}_eeg.fif'
    raw.save(path, overwrite=True, verbose='ERROR')
    return path


def test_parallel_recordings_match_serial(tmp_path):
    files = [fif(tmp_path, i) for i in range(4)]
    serial = run_athlete_workflow(files[:3], files[3], tmp_path / 'serial', team_id='t', athlete_id='a')
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as pool:
        parallel = run_athlete_workflow(files[:3], files[3], tmp_path / 'parallel', team_id='t', athlete_id='a',
                                        executor=pool)
    assert (tmp_path / 'serial' / 'comparative_analysis.csv').read_text() == \
           (tmp_path / 'parallel' / 'comparative_analysis.csv').read_text()
    assert (tmp_path / 'serial' / 'baseline' / 'longitudinal.csv').read_text() == \
           (tmp_path / 'parallel' / 'baseline' / 'longitudinal.csv').read_text()
    assert serial['comparison_summary'] == parallel['comparison_summary']
    # Baselines keep their order (and folder names) whichever finishes first.
    assert [b['report_html'] for b in serial['baseline_reports']] == [b['report_html'] for b in parallel['baseline_reports']]


def test_pipeline_workers_setting(tmp_path, monkeypatch):
    import importlib, sys
    monkeypatch.setenv('NEUROBUDDY_DATA_DIR', str(tmp_path / 'data'))
    sys.modules.pop('backend.app', None)
    app = importlib.import_module('backend.app')
    monkeypatch.setenv('NEUROBUDDY_PIPELINE_WORKERS', '1')
    assert app.pipeline_workers() == 1 and app.pipeline_pool() is None
    monkeypatch.setenv('NEUROBUDDY_PIPELINE_WORKERS', '3')
    assert app.pipeline_workers() == 3
    monkeypatch.setenv('NEUROBUDDY_PIPELINE_WORKERS', 'lots')
    assert 1 <= app.pipeline_workers() <= 4
