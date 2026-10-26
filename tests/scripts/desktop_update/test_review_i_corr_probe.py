"""REVIEW PROBE (never merge): does the close-holders consent reach the run it was bound to?

The Desktop binds the consent to plan.runId and passes it as -HandoffRun; windows.ps1:1616 prefers
an inherited HERMES_UPDATE_CORRELATION_ID over it. A Desktop relaunched by the Start-Process rung
inherits the previous run's id (ps1:1617 sets it process-wide).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.installation_launcher_fixture import publish_fixture_launcher
from tests.scripts.desktop_update.windows_handoff_support import MARKER, POWERSHELL, SCRIPT, _creation_time

RUN = 'desk-1-ab-12cd'
CLI = """
import os, sys
from pathlib import Path
def main():
    if '--version' in sys.argv:
        print('Install directory: ' + str(Path(__file__).resolve().parents[1])); return 0
    if '--help' in sys.argv:
        print('--keep-stash'); return 0
    if sys.argv[1:2] != ['update']:
        return 0
    Path(os.environ['CORR_OUT']).write_text(os.environ.get('HERMES_UPDATE_CORRELATION_ID', '<unset>'), encoding='utf-8')
    print('Update complete!', flush=True)
    return 0
if __name__ == '__main__':
    sys.exit(main())
"""


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('stale', ['', 'prev.run-x'])
def test_review_i_update_sees_the_handoff_run_id(tmp_path: Path, stale: str) -> None:
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, CLI)
    (install / 'hermes_cli/desktop_update_verify.py').write_text('pass\n', encoding='utf-8')
    home = tmp_path / 'home'
    home.mkdir()
    desktop = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])
    started = int(time.time()) - 5
    ct = _creation_time(desktop.pid)
    (home / MARKER).write_bytes(f'{desktop.pid}\n{started}\nct:{ct}\nrun:{RUN}\n'.encode())
    corr_out = tmp_path / 'corr.txt'
    env = {**os.environ, 'HERMES_HOME': str(home), 'HERMES_RUNTIME_DIR': str(tmp_path / 'empty-store'),
           'PYTHONIOENCODING': 'utf-8', 'CORR_OUT': str(corr_out), 'HERMES_UPDATE_DESKTOP_EXIT_SECONDS': '40'}
    env.pop('HERMES_UPDATE_CORRELATION_ID', None)
    if stale:
        env['HERMES_UPDATE_CORRELATION_ID'] = stale
    proc = subprocess.run(
        [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT), '-InstallRoot', str(install),
         '-NoUi', '-NoGateway', '-DesktopPid', str(desktop.pid), '-HandoffRun', RUN],
        check=False, cwd=tmp_path, capture_output=True, text=True, timeout=170, env=env)
    desktop.wait(timeout=30)
    log = home / 'logs' / 'desktop-update-handoff.log'
    log_text = log.read_text(encoding='utf-8', errors='replace') if log.is_file() else ''
    assert corr_out.is_file(), (proc.returncode, proc.stdout[-2000:], log_text[-3000:])
    corr = corr_out.read_text(encoding='utf-8')

    # The real consumer: a consent the Desktop bound to RUN for a live holder.
    from hermes_cli.desktop_release_holders import HolderConsent, ReleaseHolder, take_consent
    import psutil
    holder_proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        p = psutil.Process(holder_proc.pid)
        holder = ReleaseHolder(pid=p.pid, name=p.name(), exe=p.exe(), create_time=p.create_time())
        release = tmp_path / 'release'
        release.mkdir()
        (release / '.close-holders-consent.json').write_text(json.dumps(
            {'written_at': time.time(), 'run_id': RUN,
             'holders': [{'pid': holder.pid, 'start_time': holder.create_time, 'exe': holder.exe}]}), encoding='utf-8')
        covered = take_consent(release, corr).covers(holder)
    finally:
        holder_proc.kill()
    print(f'RESULT stale={stale!r} update_corr={corr!r} consent_covers={covered}')
    assert corr == RUN and covered, f'stale={stale!r}: update saw {corr!r}, consent covers={covered}'


@pytest.mark.platforms('windows')
def test_review_i_start_process_relaunch_inherits_corr(tmp_path: Path) -> None:
    child = tmp_path / 'child.py'
    out = tmp_path / 'env.txt'
    child.write_text(f"import os; open(r'{out}', 'w').write(os.environ.get('HERMES_UPDATE_CORRELATION_ID', '<unset>'))",
                     encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k != 'HERMES_UPDATE_CORRELATION_ID'}
    subprocess.run([POWERSHELL, '-NoProfile', '-Command',
                    f"$env:HERMES_UPDATE_CORRELATION_ID = 'run-a'; "
                    f"$p = Start-Process -FilePath '{sys.executable}' -ArgumentList '\"{child}\"' -PassThru -Wait"],
                   check=True, timeout=60, env=env)
    got = out.read_text()
    print(f'RESULT start_process_child_corr={got!r}')
    assert got == '<unset>', got
