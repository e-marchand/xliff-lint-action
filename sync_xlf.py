#!/usr/bin/env python3
"""Check that every localized XLIFF file is in sync with the English reference.

The English files in ``Resources/en.lproj`` are the source of truth. For every
other ``*.lproj`` language, and for every English ``.xlf`` file, this script
verifies that:

  1. Every English ``<trans-unit>`` (matched by ``resname``) also exists in the
     localized file. Missing units are reported and, with ``--fix``, copied over
     with the target flagged ``state="new"`` (untranslated).
     -> toggle with --check-missing / --no-check-missing

  2. The localized ``<source>`` text matches the English ``<source>`` text.
     When it differs:
       * if the target is already translated, the source is updated to the
         English value and the target is flagged
         ``state="needs-review-translation"`` (the translation must be reviewed
         because the original text changed);
       * if the target is not yet translated, both source and target are synced
         to English and flagged ``state="new"``.
     -> toggle with --check-source-drift / --no-check-source-drift

The ``state`` values used are the standard XLIFF state values
(``new`` and ``needs-review-translation``).

Usage:
    python3 sync_xlf.py                       # report drift (CI mode)
    python3 sync_xlf.py --fix                 # apply all fixes
    python3 sync_xlf.py -i                    # prompt before each fix
    python3 sync_xlf.py --languages fr,de     # only check fr and de
    python3 sync_xlf.py --no-check-source-drift   # only flag missing units

Exit code is non-zero while any (enabled) drift remains (so it can fail CI).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

STATE_NEW = "new"
STATE_NEEDS_REVIEW = "needs-review-translation"

GROUP_OPEN = re.compile(r'<group\b[^>]*\bresname="([^"]*)"')
GROUP_CLOSE = re.compile(r"</group\s*>")
TU_OPEN = re.compile(r"<trans-unit\b([^>]*)>")
TU_CLOSE = re.compile(r"</trans-unit\s*>")
ATTR = re.compile(r'(\w+)="([^"]*)"')
SOURCE_LINE = re.compile(r"^(\s*)<source>(.*)</source>\s*$")
TARGET_LINE = re.compile(r"^(\s*)<target\b([^>]*)>(.*)</target>\s*$")
BODY_CLOSE = re.compile(r"</body\s*>")

# Indentation used by the 4D XLIFF writer.
GROUP_INDENT = "      "
TU_INDENT = "        "
INNER_INDENT = "          "

CHECK_MISSING = "xliff-missing"
CHECK_DRIFT = "xliff-source-drift"


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


def github_annotation(level: str, message: str, file: str, line: int | None) -> None:
    loc = f"file={file}"
    if line is not None:
        loc += f",line={line}"
    print(f"::{level} {loc}::{_gh_escape(message)}")


def make_issue(check: str, severity: str, description: str, path: str, line: int) -> dict:
    fingerprint = hashlib.md5(f"{check}:{path}:{description}".encode("utf-8")).hexdigest()
    return {
        "description": description,
        "check_name": check,
        "fingerprint": fingerprint,
        "severity": severity,
        "location": {"path": path, "lines": {"begin": line}},
    }


def parse_languages(spec: str | None) -> set[str] | None:
    """Return a set of language codes to check, or None to check them all."""
    if not spec or spec.strip().lower() == "all":
        return None
    langs: set[str] = set()
    for part in spec.split(","):
        name = part.strip()
        if not name:
            continue
        if name.endswith(".lproj"):
            name = name[: -len(".lproj")]
        langs.add(name)
    return langs


class Unit:
    __slots__ = (
        "resname", "uid", "group",
        "src_text", "src_idx", "src_indent",
        "tgt_text", "tgt_idx", "tgt_indent",
    )

    def __init__(self, resname: str, uid: str, group: str) -> None:
        self.resname = resname
        self.uid = uid
        self.group = group
        self.src_text: str | None = None
        self.src_idx: int | None = None
        self.src_indent = INNER_INDENT
        self.tgt_text: str | None = None
        self.tgt_idx: int | None = None
        self.tgt_indent = INNER_INDENT


class XlfFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        text = path.read_text(encoding="utf-8")
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines(keepends=True)
        self.units: dict[str, Unit] = {}
        self.order: list[str] = []
        self.group_close: dict[str, int] = {}
        self.group_order: list[str] = []
        self.body_close_idx: int | None = None
        self._parse()

    def _parse(self) -> None:
        current_group = ""
        pending: Unit | None = None
        for idx, raw in enumerate(self.lines):
            line = raw.rstrip("\r\n")
            g = GROUP_OPEN.search(line)
            if g:
                current_group = g.group(1)
                if current_group not in self.group_order:
                    self.group_order.append(current_group)
            if GROUP_CLOSE.search(line):
                self.group_close[current_group] = idx
            tu = TU_OPEN.search(line)
            if tu:
                attrs = dict(ATTR.findall(tu.group(1)))
                resname = attrs.get("resname", "")
                pending = Unit(resname, attrs.get("id", resname), current_group)
            sm = SOURCE_LINE.match(line)
            if sm and pending is not None:
                pending.src_indent = sm.group(1)
                pending.src_text = sm.group(2)
                pending.src_idx = idx
            tm = TARGET_LINE.match(line)
            if tm and pending is not None:
                pending.tgt_indent = tm.group(1)
                pending.tgt_text = tm.group(3)
                pending.tgt_idx = idx
            if TU_CLOSE.search(line) and pending is not None:
                if pending.resname:
                    self.units[pending.resname] = pending
                    self.order.append(pending.resname)
                pending = None
            if BODY_CLOSE.search(line):
                self.body_close_idx = idx


def find_repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parent


def is_translated(unit: Unit) -> bool:
    return unit.tgt_text is not None and unit.tgt_text != unit.src_text


def confirm(interactive: bool, message: str) -> bool:
    if not interactive:
        return True
    return input(f"  {message} [y/N] ").strip().lower() in {"y", "yes"}


def sync_file(
    eng: XlfFile,
    loc: XlfFile,
    root: Path,
    do_fix: bool,
    interactive: bool,
    check_missing: bool,
    check_drift: bool,
    fmt: str,
    issues: list[dict],
) -> tuple[int, int]:
    """Return (issues_found, issues_resolved) for one localized file."""
    replacements: dict[int, str] = {}
    group_inserts: dict[str, list[str]] = {}
    new_group_blocks: list[str] = []
    nl = loc.nl
    rel = str(loc.path.relative_to(root))
    apath = annotation_path(loc.path)

    found = 0
    resolved = 0

    # Track, per missing English group, the units to create.
    missing_by_group: dict[str, list[Unit]] = {}

    for resname in eng.order:
        eng_unit = eng.units[resname]
        loc_unit = loc.units.get(resname)

        if loc_unit is None:
            if not check_missing:
                continue
            found += 1
            print(f"  + missing '{resname}'  (group {eng_unit.group})")
            if fmt == "github":
                github_annotation("error", f"Missing trans-unit '{resname}' (group {eng_unit.group})", apath, None)
            issues.append(make_issue(CHECK_MISSING, "major", f"Missing trans-unit '{resname}'", rel, 1))
            if do_fix and confirm(interactive, f"add '{resname}' as new?"):
                missing_by_group.setdefault(eng_unit.group, []).append(eng_unit)
                resolved += 1
            continue

        if loc_unit.src_text == eng_unit.src_text:
            continue

        if not check_drift:
            continue

        # Source drift.
        found += 1
        translated = is_translated(loc_unit)
        kind = "translated" if translated else "untranslated"
        print(
            f"  ~ source drift '{resname}' [{kind}]\n"
            f"      en : {eng_unit.src_text!r}\n"
            f"      loc: {loc_unit.src_text!r}"
        )
        drift_line = (loc_unit.src_idx + 1) if loc_unit.src_idx is not None else 1
        if fmt == "github":
            github_annotation(
                "warning",
                f"Source drift '{resname}': en={eng_unit.src_text!r} loc={loc_unit.src_text!r}",
                apath, drift_line,
            )
        issues.append(make_issue(CHECK_DRIFT, "minor", f"Source drift '{resname}'", rel, drift_line))
        if not do_fix:
            continue
        action = "update source + flag needs-review" if translated else "sync source/target as new"
        if not confirm(interactive, f"{action} for '{resname}'?"):
            continue
        if loc_unit.src_idx is not None:
            replacements[loc_unit.src_idx] = (
                f"{loc_unit.src_indent}<source>{eng_unit.src_text}</source>{nl}"
            )
        if loc_unit.tgt_idx is not None:
            if translated:
                replacements[loc_unit.tgt_idx] = (
                    f'{loc_unit.tgt_indent}<target state="{STATE_NEEDS_REVIEW}">'
                    f"{loc_unit.tgt_text}</target>{nl}"
                )
            else:
                replacements[loc_unit.tgt_idx] = (
                    f'{loc_unit.tgt_indent}<target state="{STATE_NEW}">'
                    f"{eng_unit.src_text}</target>{nl}"
                )
        resolved += 1

    # Report units that exist only in the localized file (not deleted here).
    if check_missing:
        for resname in loc.order:
            if resname not in eng.units:
                print(f"  ! extra '{resname}' not in English (left untouched)")

    # Build new trans-unit blocks for missing units.
    for group, units in missing_by_group.items():
        blocks: list[str] = []
        for u in units:
            blocks.append(f'{TU_INDENT}<trans-unit id="{u.uid}" resname="{u.resname}">{nl}')
            blocks.append(f"{INNER_INDENT}<source>{u.src_text}</source>{nl}")
            blocks.append(f'{INNER_INDENT}<target state="{STATE_NEW}">{u.src_text}</target>{nl}')
            blocks.append(f"{TU_INDENT}</trans-unit>{nl}")
        if group in loc.group_close:
            group_inserts.setdefault(group, []).extend(blocks)
        else:
            new_group_blocks.append(f'{GROUP_INDENT}<group resname="{group}">{nl}')
            new_group_blocks.extend(blocks)
            new_group_blocks.append(f"{GROUP_INDENT}</group>{nl}")

    if not replacements and not group_inserts and not new_group_blocks:
        return found, resolved

    inserts_before: dict[int, list[str]] = {}
    for group, blocks in group_inserts.items():
        inserts_before.setdefault(loc.group_close[group], []).extend(blocks)
    if new_group_blocks and loc.body_close_idx is not None:
        inserts_before.setdefault(loc.body_close_idx, []).extend(new_group_blocks)

    out: list[str] = []
    for idx, line in enumerate(loc.lines):
        if idx in inserts_before:
            out.extend(inserts_before[idx])
        out.append(replacements.get(idx, line))
    loc.path.write_text("".join(out), encoding="utf-8")

    return found, resolved


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Sync localized XLIFF files with English.")
    parser.add_argument("--fix", action="store_true", help="apply fixes to localized files")
    parser.add_argument("-i", "--interactive", action="store_true", help="prompt before each fix")
    parser.add_argument("--root", default=None, help="project root (default: this script's folder)")
    parser.add_argument("--languages", default="all",
                        help="comma-separated languages to check (e.g. 'fr,de'), or 'all' (default)")
    parser.add_argument("--check-missing", action=argparse.BooleanOptionalAction, default=True,
                        help="report trans-units present in English but missing in a language (default: on)")
    parser.add_argument("--check-source-drift", action=argparse.BooleanOptionalAction, default=True,
                        help="report localized <source> text that differs from English (default: on)")
    parser.add_argument("--format", choices=("auto", "text", "github"), default="auto",
                        help="output format (auto-detects GitHub Actions)")
    parser.add_argument("--codequality", default=None, metavar="FILE",
                        help="write a GitLab Code Quality JSON report to FILE")
    args = parser.parse_args(argv)

    fmt = detect_format(args.format)
    issues: list[dict] = []

    if not args.check_missing and not args.check_source_drift:
        print("Nothing to check: both --no-check-missing and --no-check-source-drift were given.",
              file=sys.stderr)
        return 2

    root = find_repo_root(args.root)
    eng_dir = root / "Resources" / "en.lproj"
    if not eng_dir.is_dir():
        print(f"English reference folder not found: {eng_dir}", file=sys.stderr)
        return 2

    do_fix = args.fix or args.interactive
    eng_files = sorted(eng_dir.glob("*.xlf"))
    if not eng_files:
        print(f"No .xlf files in {eng_dir}", file=sys.stderr)
        return 2

    wanted = parse_languages(args.languages)
    lang_dirs = sorted(
        d for d in (root / "Resources").glob("*.lproj")
        if d.is_dir() and d.name != "en.lproj"
        and (wanted is None or d.name[: -len(".lproj")] in wanted)
    )
    if not lang_dirs:
        scope = "any" if wanted is None else ", ".join(sorted(wanted))
        print(f"No localized .lproj folders to check (languages: {scope}).", file=sys.stderr)
        return 2

    total_issues = 0
    total_resolved = 0

    for eng_path in eng_files:
        eng = XlfFile(eng_path)
        for lang_dir in lang_dirs:
            loc_path = lang_dir / eng_path.name
            rel = loc_path.relative_to(root)
            if not loc_path.exists():
                if not args.check_missing:
                    continue
                total_issues += 1
                print(f"\n{rel}: MISSING file (copy {eng_path.name} and translate it)")
                if fmt == "github":
                    github_annotation("error", f"Missing localized file (copy {eng_path.name} and translate it)",
                                      annotation_path(loc_path), None)
                issues.append(make_issue(CHECK_MISSING, "major", f"Missing localized file {eng_path.name}", str(rel), 1))
                continue
            loc = XlfFile(loc_path)
            print(f"\n{rel}:")
            found, resolved = sync_file(
                eng, loc, root, do_fix, args.interactive,
                args.check_missing, args.check_source_drift, fmt, issues,
            )
            if found == 0:
                print("  in sync")
            total_issues += found
            total_resolved += resolved

    if args.codequality:
        Path(args.codequality).write_text(json.dumps(issues, indent=2), encoding="utf-8")

    print()
    if total_issues == 0:
        print("✓ All checked localized files are in sync with English.")
        return 0

    if do_fix:
        remaining = total_issues - total_resolved
        print(f"Applied {total_resolved} fix(es); {remaining} issue(s) remaining.")
        return 0 if remaining == 0 else 1

    print(f"✗ Found {total_issues} sync issue(s). Run with --fix or -i to resolve them.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
