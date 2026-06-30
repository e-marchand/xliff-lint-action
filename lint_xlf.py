#!/usr/bin/env python3
"""Lint XLIFF translation units against their usage in the 4D project source.

For every ``<trans-unit resname="...">`` declared in the ``.xlf`` files under
``Resources/``, this script checks whether the ``resname`` is referenced
anywhere in the project source (``.4dm`` code, ``.4DForm`` forms, ``menus.json``,
style sheets, the catalog, ...). Translation units whose ``resname`` is never
referenced are reported as unused.

Usage:
    python3 lint_xlf.py            # report unused trans-units (CI mode)
    python3 lint_xlf.py --fix      # remove unused trans-units
    python3 lint_xlf.py -i         # interactively remove unused trans-units

Options:
    --fix             Remove every unused trans-unit from all .xlf files.
    -i, --interactive Prompt before removing each unused resname.
    --4dm-only        Only consider .4dm files when deciding if a resname is used.
    --root PATH       Project root (defaults to this script's folder).
    --format FORMAT   Output format: auto (default), text or github.
    --codequality F   Also write a GitLab Code Quality JSON report to file F.
    -v, --verbose     Also list, for each used resname, a file that references it.

Exit code is non-zero when unused trans-units remain (so it can fail CI).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Directories that never contain hand-written source we should scan.
EXCLUDED_DIRS = {"DerivedData", "Trash", "CompiledCode", ".git", "temporary files"}

# Extensions of source files that may reference a resname.
ALL_SOURCE_SUFFIXES = {".4dm", ".4DForm", ".json", ".css", ".4DCatalog", ".4DSettings"}
ONLY_4DM_SUFFIXES = {".4dm"}

# Matches a trans-unit opening tag and captures its resname.
TRANS_UNIT_OPEN = re.compile(r'<trans-unit\b[^>]*\bresname="([^"]*)"')
TRANS_UNIT_CLOSE = re.compile(r"</trans-unit\s*>")

CHECK_NAME = "xliff-unused"


# --------------------------------------------------------------------------- #
# CI / reporting helpers (kept self-contained so the script can run alone).
# --------------------------------------------------------------------------- #
def detect_format(choice: str) -> str:
    """Resolve 'auto' to 'github' on GitHub Actions, else 'text'."""
    if choice != "auto":
        return choice
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "github"
    return "text"


def _gh_escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotation_path(path: Path) -> str:
    """Path relative to GITHUB_WORKSPACE when available (so annotations link)."""
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        try:
            return str(path.resolve().relative_to(Path(workspace).resolve()))
        except ValueError:
            pass
    return str(path)


def github_annotation(level: str, message: str, file: str, line: int) -> None:
    print(f"::{level} file={file},line={line}::{_gh_escape(message)}")


def make_issue(check: str, severity: str, description: str, path: str, line: int) -> dict:
    fingerprint = hashlib.md5(f"{check}:{path}:{description}".encode("utf-8")).hexdigest()
    return {
        "description": description,
        "check_name": check,
        "fingerprint": fingerprint,
        "severity": severity,
        "location": {"path": path, "lines": {"begin": line}},
    }


def find_repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    # Normally this file lives at <root>/lint_xlf.py. When piped to python
    # (curl ... | python3 -), __file__ is '<stdin>' or unset, so fall back to
    # the current directory.
    script = globals().get("__file__")
    if script and script != "<stdin>":
        return Path(script).resolve().parent
    return Path.cwd()


def iter_source_files(root: Path, suffixes: set[str]) -> list[Path]:
    """Return source files that may reference a resname, skipping the .xlf files."""
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.suffix == ".xlf":
            continue
        if path.suffix in suffixes:
            files.append(path)
    return files


def build_corpus(files: list[Path]) -> list[tuple[Path, str]]:
    corpus: list[tuple[Path, str]] = []
    for path in files:
        try:
            corpus.append((path, path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            # Skip binary or unreadable files.
            continue
    return corpus


def usage_finder(corpus: list[tuple[Path, str]]):
    cache: dict[str, Path | None] = {}

    def find(resname: str) -> Path | None:
        if resname in cache:
            return cache[resname]
        # Match the resname as a whole token, so "name" does not match "fileName".
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(resname) + r"(?![A-Za-z0-9_])")
        for path, text in corpus:
            if pattern.search(text):
                cache[resname] = path
                return path
        cache[resname] = None
        return None

    return find


class TransUnit:
    __slots__ = ("resname", "start", "end")

    def __init__(self, resname: str, start: int, end: int) -> None:
        self.resname = resname  # resname attribute value
        self.start = start  # first line index of the block (inclusive)
        self.end = end  # last line index of the block (inclusive)


def parse_trans_units(lines: list[str]) -> list[TransUnit]:
    """Locate every <trans-unit> ... </trans-unit> block and its resname."""
    units: list[TransUnit] = []
    i = 0
    n = len(lines)
    while i < n:
        match = TRANS_UNIT_OPEN.search(lines[i])
        if not match:
            i += 1
            continue
        resname = match.group(1)
        start = i
        # The closing tag may be on the same line or a later line.
        if TRANS_UNIT_CLOSE.search(lines[i]):
            units.append(TransUnit(resname, start, i))
            i += 1
            continue
        j = i + 1
        while j < n and not TRANS_UNIT_CLOSE.search(lines[j]):
            j += 1
        end = j if j < n else n - 1
        units.append(TransUnit(resname, start, end))
        i = end + 1
    return units


def collect_xlf_files(root: Path) -> list[Path]:
    resources = root / "Resources"
    base = resources if resources.is_dir() else root
    return sorted(base.rglob("*.xlf"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Lint unused XLIFF translation units.")
    parser.add_argument("--fix", action="store_true", help="remove unused trans-units from all .xlf files")
    parser.add_argument("-i", "--interactive", action="store_true", help="prompt before removing each resname")
    parser.add_argument("--4dm-only", dest="only_4dm", action="store_true", help="only scan .4dm files for usage")
    parser.add_argument("--root", default=None, help="project root (default: this script's folder)")
    parser.add_argument("--format", choices=("auto", "text", "github"), default="auto",
                        help="output format (auto-detects GitHub Actions)")
    parser.add_argument("--codequality", default=None, metavar="FILE",
                        help="write a GitLab Code Quality JSON report to FILE")
    parser.add_argument("-v", "--verbose", action="store_true", help="show a referencing file for each used resname")
    args = parser.parse_args(argv)

    fmt = detect_format(args.format)
    issues: list[dict] = []

    root = find_repo_root(args.root)
    xlf_files = collect_xlf_files(root)
    if not xlf_files:
        print(f"No .xlf files found under {root}", file=sys.stderr)
        return 2

    suffixes = ONLY_4DM_SUFFIXES if args.only_4dm else ALL_SOURCE_SUFFIXES
    corpus = build_corpus(iter_source_files(root, suffixes))
    find_usage = usage_finder(corpus)

    do_fix = args.fix or args.interactive

    total_unused = 0
    total_removed = 0

    for xlf in xlf_files:
        try:
            text = xlf.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Cannot read {xlf}: {exc}", file=sys.stderr)
            continue
        lines = text.splitlines(keepends=True)
        units = parse_trans_units(lines)

        unused: list[TransUnit] = []
        for unit in units:
            if find_usage(unit.resname) is None:
                unused.append(unit)

        rel = xlf.relative_to(root)
        if not unused:
            if args.verbose:
                print(f"OK  {rel}: {len(units)} trans-units, all referenced")
            continue

        total_unused += len(unused)
        print(f"\n{rel}: {len(unused)} unused trans-unit(s)")
        remove_lines: set[int] = set()
        for unit in unused:
            should_remove = False
            if do_fix:
                if args.interactive:
                    answer = input(f"  remove '{unit.resname}'? [y/N] ").strip().lower()
                    should_remove = answer in {"y", "yes"}
                else:
                    should_remove = True
            tag = "remove" if should_remove else "unused"
            print(f"  - {unit.resname}  (line {unit.start + 1})  [{tag}]")
            if fmt == "github" and not should_remove:
                github_annotation(
                    "warning",
                    f"Unused trans-unit '{unit.resname}' (never referenced in project source)",
                    annotation_path(xlf), unit.start + 1,
                )
            if args.codequality and not should_remove:
                issues.append(make_issue(
                    CHECK_NAME, "minor",
                    f"Unused trans-unit '{unit.resname}'",
                    str(rel), unit.start + 1,
                ))
            if should_remove:
                remove_lines.update(range(unit.start, unit.end + 1))

        if remove_lines:
            new_lines = [line for idx, line in enumerate(lines) if idx not in remove_lines]
            xlf.write_text("".join(new_lines), encoding="utf-8")
            total_removed += sum(1 for u in unused if u.start in remove_lines)

    if args.codequality:
        Path(args.codequality).write_text(json.dumps(issues, indent=2), encoding="utf-8")

    print()
    if total_unused == 0:
        print("✓ No unused translation units found.")
        return 0

    if do_fix:
        print(f"Removed {total_removed} unused trans-unit(s); {total_unused - total_removed} left untouched.")
        return 0 if total_removed == total_unused else 1

    print(f"✗ Found {total_unused} unused translation unit(s). Run with --fix or -i to remove them.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
