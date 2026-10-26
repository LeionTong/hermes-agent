"""REVIEW PROBE (never merge): windows.ps1 Restore-UserProxyEnv on real Windows PowerShell 5.1.

The function text is lifted from the real script and run in a powershell.exe whose env carries what
the Desktop's systemProxyEnv hands an update child; a Start-Process child (the tethered relaunch rung)
reports the env it inherited.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = ROOT / 'scripts/desktop-update/windows.ps1'
POWERSHELL = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
KEYS = ['HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'HERMES_UPDATE_SYSTEM_PROXY', 'HERMES_UPDATE_USER_NO_PROXY']
BYPASS = 'localhost,127.0.0.1,::1,127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.local'


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('user_no_proxy', [None, 'corp.internal'])
def test_review_i_restore_user_proxy_env(tmp_path: Path, user_no_proxy: str | None) -> None:
    text = SCRIPT.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    fn = re.search(r'^function Restore-UserProxyEnv \{.*?^\}\n', text, re.S | re.M).group(0)
    child = tmp_path / 'child.py'
    out = tmp_path / 'env.json'
    child.write_text(f"import json, os; json.dump({{k: os.environ.get(k) for k in {KEYS!r}}}, open(r'{out}', 'w'))", encoding='utf-8')
    (tmp_path / 'probe.ps1').write_text(
        fn + f"\nRestore-UserProxyEnv\n$p = Start-Process -FilePath '{sys.executable}' -ArgumentList '\"{child}\"' -PassThru -Wait\n",
        encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if 'PROXY' not in k.upper()}
    np = f'{user_no_proxy},{BYPASS}' if user_no_proxy else BYPASS
    env.update({'HTTPS_PROXY': 'http://10.0.0.7:7890', 'HTTP_PROXY': 'http://10.0.0.7:7890', 'NO_PROXY': np,
                'HERMES_UPDATE_SYSTEM_PROXY': 'HTTPS_PROXY,https_proxy,HTTP_PROXY,http_proxy,' + ('no_proxy' if user_no_proxy else 'NO_PROXY,no_proxy')})
    if user_no_proxy:
        env['HERMES_UPDATE_USER_NO_PROXY'] = user_no_proxy
    subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(tmp_path / 'probe.ps1')],
                   check=True, timeout=60, env=env)
    got = json.loads(out.read_text())
    print('RESULT', user_no_proxy, json.dumps(got))
    want = {k: None for k in KEYS}
    if user_no_proxy:
        want['NO_PROXY'] = user_no_proxy
    assert got == want, got
