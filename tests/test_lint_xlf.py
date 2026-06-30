"""Tests for lint_xlf.py (unused trans-unit detection)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_script


def test_reports_unused_and_fails(lint_project: Path):
    result = run_script("lint_xlf.py", lint_project)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ORPHAN_KEY" in result.stdout
    # Used keys must not be reported.
    assert "WELCOME_TITLE" not in result.stdout
    assert "OK_BUTTON" not in result.stdout


def test_github_format_emits_annotation(lint_project: Path):
    result = run_script("lint_xlf.py", lint_project, "--format", "github")
    assert "::warning" in result.stdout
    assert "ORPHAN_KEY" in result.stdout


def test_codequality_report(lint_project: Path, tmp_path: Path):
    report = tmp_path / "cq.json"
    run_script("lint_xlf.py", lint_project, "--codequality", str(report))
    data = json.loads(report.read_text())
    assert len(data) == 1
    assert data[0]["check_name"] == "xliff-unused"
    assert "ORPHAN_KEY" in data[0]["description"]


def test_fix_removes_unused(lint_project: Path):
    xlf = lint_project / "Resources" / "en.lproj" / "Strings.xlf"
    assert "ORPHAN_KEY" in xlf.read_text()

    result = run_script("lint_xlf.py", lint_project, "--fix")
    assert result.returncode == 0, result.stdout + result.stderr

    after = xlf.read_text()
    assert "ORPHAN_KEY" not in after
    # Used trans-units are preserved.
    assert "WELCOME_TITLE" in after
    assert "OK_BUTTON" in after

    # A second run is now clean.
    again = run_script("lint_xlf.py", lint_project)
    assert again.returncode == 0


def test_no_xlf_files_returns_2(tmp_path: Path):
    (tmp_path / "Resources").mkdir()
    result = run_script("lint_xlf.py", tmp_path)
    assert result.returncode == 2
