"""Focused checks followed by legacy integration suites; exits at the first failure.

Legacy suites require a running-capable host, full dependencies, codecs and a
browser. Inspect their historical fixed ports/data paths before running them.
"""
from pathlib import Path
import os
import subprocess
import sys
from run_checks import run_checks

ROOT = Path(__file__).resolve().parents[1]
if __name__ == '__main__':
    result = run_checks()
    if result: raise SystemExit(result)
    env = dict(os.environ, PYTHONUTF8='1')
    if (ROOT / 'bin').is_dir(): env['PATH'] = str(ROOT / 'bin') + os.pathsep + env.get('PATH', '')
    for name in ('test_spec.py', 'test_routes.py', 'test_render.py', 'test_api.py', 'test_fuzz.py',
                 'test_footage.py', 'test_catalog.py', 'test_recipes.py', 'test_agent.py', 'test_ui.py'):
        print(f'Running {name}', flush=True)
        result = subprocess.run([sys.executable, str(ROOT / 'tests' / name)], cwd=ROOT, env=env)
        if result.returncode: raise SystemExit(result.returncode)
    print('ALL OK')
