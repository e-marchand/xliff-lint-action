"""Tests for sync_xlf.py (language sync against English)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_script


def test_reports_missing_and_drift(sync_project: Path):
    # fr has a drift (WELCOME_TITLE) and a missing unit (GOODBYE); de is in sync.
    result = run_script("sync_xlf.py", sync_project)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "missing 'GOODBYE'" in result.stdout
    assert "source drift 'WELCOME_TITLE'" in result.stdout
    assert "fr.lproj" in result.stdout


def test_languages_filter_only_fr(sync_project: Path):
    result = run_script("sync_xlf.py", sync_project, "--languages", "fr")
    assert result.returncode == 1
    assert "fr.lproj" in result.stdout
    assert "de.lproj" not in result.stdout


def test_languages_filter_only_de_is_clean(sync_project: Path):
    result = run_script("sync_xlf.py", sync_project, "--languages", "de")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "in sync" in result.stdout


def test_no_check_drift_keeps_only_missing(sync_project: Path):
    result = run_script("sync_xlf.py", sync_project, "--no-check-source-drift")
    assert result.returncode == 1
    assert "missing 'GOODBYE'" in result.stdout
    assert "source drift" not in result.stdout


def test_no_check_missing_keeps_only_drift(sync_project: Path):
    result = run_script("sync_xlf.py", sync_project, "--no-check-missing")
    assert result.returncode == 1
    assert "source drift 'WELCOME_TITLE'" in result.stdout
    assert "missing 'GOODBYE'" not in result.stdout


def test_disabling_both_checks_errors(sync_project: Path):
    result = run_script("sync_xlf.py", sync_project, "--no-check-missing", "--no-check-source-drift")
    assert result.returncode == 2


def test_github_annotations(sync_project: Path):
    result = run_script("sync_xlf.py", sync_project, "--format", "github")
    assert "::error" in result.stdout  # missing
    assert "::warning" in result.stdout  # drift


def test_codequality_report(sync_project: Path, tmp_path: Path):
    report = tmp_path / "cq.json"
    run_script("sync_xlf.py", sync_project, "--codequality", str(report))
    data = json.loads(report.read_text())
    checks = sorted(i["check_name"] for i in data)
    assert "xliff-missing" in checks
    assert "xliff-source-drift" in checks


def test_fix_resolves_everything(sync_project: Path):
    fr = sync_project / "Resources" / "fr.lproj" / "Strings.xlf"
    result = run_script("sync_xlf.py", sync_project, "--fix")
    assert result.returncode == 0, result.stdout + result.stderr

    text = fr.read_text()
    # GOODBYE was added as new.
    assert "GOODBYE" in text
    assert 'state="new"' in text
    # Drifted source synced to English and target flagged for review.
    assert "<source>Welcome</source>" in text
    assert "needs-review-translation" in text

    again = run_script("sync_xlf.py", sync_project)
    assert again.returncode == 0


def test_missing_english_folder_returns_2(tmp_path: Path):
    (tmp_path / "Resources").mkdir()
    result = run_script("sync_xlf.py", tmp_path)
    assert result.returncode == 2
