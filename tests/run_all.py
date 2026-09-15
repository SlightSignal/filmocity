"""Run every suite in order; exit non-zero on the first failure. usage: python3 tests/run_all.py"""
import subprocess, sys, os
# The launchers set PYTHONUTF8=1 (see Filmocity.bat); tests run outside them, so
# set it here too or every unmarked open()/print of ✅ dies on cp1252 Windows.
os.environ["PYTHONUTF8"] = "1"
here = os.path.dirname(os.path.abspath(__file__))
for t in ("test_spec.py", "test_routes.py", "test_render.py", "test_api.py", "test_fuzz.py", "test_footage.py", "test_catalog.py", "test_recipes.py", "test_agent.py", "test_ui.py"):
    print(f"== {t}"); r = subprocess.run([sys.executable, os.path.join(here, t)]); 
    if r.returncode: sys.exit(r.returncode)
print("ALL OK")
