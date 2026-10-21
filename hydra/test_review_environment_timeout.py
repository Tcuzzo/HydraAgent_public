"""A timed-out environment command cannot keep mutating its workspace."""
import json
import subprocess
import sys
import time
from pathlib import Path

from hydra.environment import create_session, run_session_command, session_status


def test_environment_timeout_stops_descendant_and_persists_failure(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    env_root = tmp_path/'environments'
    session = create_session(source_repo=source, env_root=env_root, title='Timeout evidence')
    workspace = Path(session['workspace'])
    child = ("from pathlib import Path; import time; "
             "Path('child-started').write_text('started'); time.sleep(1.5); "
             "Path('late-effect.txt').write_text('unowned late write')")
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{child!r}], "
              "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
              "print('parent-started',flush=True); time.sleep(15)")
    packet = None
    try:
        packet = run_session_command(env_root, session['session_id'],
                                     [sys.executable, '-c', parent], timeout_seconds=1)
    except subprocess.TimeoutExpired:
        pass
    assert (workspace/'child-started').exists(), 'probe child never started'
    time.sleep(1.5)
    assert not (workspace/'late-effect.txt').exists(), 'timed-out descendant kept writing'
    assert packet is not None and packet['status'] == 'timed_out', packet
    assert 'parent-started' in packet['stdout']
    assert session_status(env_root, session['session_id'])['command_count'] == 1
    saved = list((Path(session['session_path'])/'commands').glob('*.json'))
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == packet
