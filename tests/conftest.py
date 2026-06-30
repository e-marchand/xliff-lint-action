"""Shared test helpers: run the scripts as subprocesses against copied fixtures."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_script(script: str, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run <script> with --root <root> and return the completed process."""
    cmd = [sys.executable, str(SCRIPTS / script), "--root", str(root), *args]
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture
def lint_project(tmp_path: Path) -> Path:
    dst = tmp_path / "lint_project"
    shutil.copytree(FIXTURES / "lint_project", dst)
    return dst


@pytest.fixture
def sync_project(tmp_path: Path) -> Path:
    dst = tmp_path / "sync_project"
    shutil.copytree(FIXTURES / "sync_project", dst)
    return dst
