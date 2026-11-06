import os, subprocess, sys

def test_evals_pass():
    r = subprocess.run([sys.executable, "-m", "evals.run_evals", "--brain", "rule"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout[-2000:]
