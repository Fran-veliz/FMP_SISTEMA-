import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("configured_url", [None, "   "])
def test_no_se_usan_credenciales_implicitas_si_falta_la_url(configured_url):
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    if configured_url is not None:
        environment["DATABASE_URL"] = configured_url
    process = subprocess.run(
        [sys.executable, "-c", "import app.database"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert process.returncode != 0
    assert "Falta DATABASE_URL" in process.stderr
