"""
Runs every offline test suite of the bot.

    python tests/run_all.py

The suites only need the standard library (plus the project packages), so
they can be executed on a server or in CI without any extra setup.
"""
import os
import subprocess
import sys

# The console must be able to print the emoji of the test output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SUITES = [
    "test_ui.py",
    "test_pricing.py",
    "test_branding.py",
    "test_error_reporter.py",
    "test_joiner.py",
    "test_translations.py",
    "test_features.py",
    "test_audit_fixes.py",
    "test_agreement.py",
    "test_proxy.py",
]


def main():
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    failed = []
    for suite in SUITES:
        print("=" * 70)
        print(f"RUNNING {suite}")
        print("=" * 70)
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, suite)],
            cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = (proc.stdout or "").strip().splitlines()
        for line in out[-6:]:
            print(f"   {line}")
        if proc.returncode != 0:
            failed.append(suite)
            err = (proc.stderr or "").strip().splitlines()
            for line in err[-6:]:
                print(f"   ! {line}")
        print()

    print("=" * 70)
    if failed:
        print(f"FAILED SUITES: {', '.join(failed)}")
        return 1
    print(f"ALL {len(SUITES)} TEST SUITES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
