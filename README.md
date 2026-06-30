# 4D XLIFF Lint Action

Lint the [XLIFF](http://docs.oasis-open.org/xliff/) translation files of a **4D
project** in CI. Two independent checks:

| Check | Script | What it catches |
| --- | --- | --- |
| **Unused trans-units** | [`lint_xlf.py`](lint_xlf.py) | A `<trans-unit resname="…">` is declared under `Resources/` but its `resname` is **never referenced** anywhere in the project source (`.4dm`, `.4DForm`, `menus.json`, stylesheets, the catalog…). Dead translations to remove. |
| **Language sync** | [`sync_xlf.py`](sync_xlf.py) | A localized language drifts from the English reference (`Resources/en.lproj`): a **missing** trans-unit, or a `<source>` whose text **no longer matches** English. |

Works on **GitHub Actions** (with inline annotations) and **GitLab CI** (with a
Code Quality report). The scripts are pure Python standard library — no
dependencies — so you can also run them locally.

---

## Quick start (GitHub Actions)

```yaml
# .github/workflows/xliff-lint.yml
name: XLIFF lint
on:
  pull_request:
    paths: ["**/*.xlf", "**/*.4dm"]
jobs:
  xliff-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: e-marchand/xliff-lint-action@v1
```

That's it. With no inputs it runs **both** checks against the project root and
fails the job (with file annotations) when something is off. A ready-to-copy
file is in [`examples/github-workflow.yml`](examples/github-workflow.yml).

---

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `project-path` | `.` | Path to the 4D project root (the folder containing `Resources/`). |
| `lint-unused` | `true` | Run the **unused trans-unit** check. Set `false` to skip it (e.g. if you only want the sync check). |
| `lint-unused-4dm-only` | `false` | Only scan `.4dm` files when deciding whether a `resname` is used. |
| `check-sync` | `true` | Run the **language sync** check. |
| `sync-languages` | `all` | Comma-separated languages to check, e.g. `fr,de,ja`. `all` checks every `*.lproj` except `en.lproj`. |
| `sync-check-missing` | `true` | Within the sync check, report trans-units present in English but **missing** in a language. |
| `sync-check-source-drift` | `true` | Within the sync check, report localized `<source>` text that **differs** from English. |
| `fail-on-error` | `true` | Fail the job when issues are found. Set `false` to only annotate (non-blocking). |
| `python-version` | `3.x` | Python version set up before running the checks. |

Each check is an independent boolean, so you can enable/disable any combination.
`lint-unused` is on by default and can be turned off if you only want the sync
check (and vice-versa).

### Examples

Only the sync check, French and German, drift turned off (e.g. while a
translation is in progress and source drift would be too noisy):

```yaml
- uses: e-marchand/xliff-lint-action@v1
  with:
    lint-unused: "false"
    sync-languages: "fr,de"
    sync-check-source-drift: "false"
```

Annotate but never block the PR:

```yaml
- uses: e-marchand/xliff-lint-action@v1
  with:
    fail-on-error: "false"
```

Project lives in a subfolder:

```yaml
- uses: e-marchand/xliff-lint-action@v1
  with:
    project-path: "MyApp"
```

---

## The two checks in detail

### `lint_xlf.py` — unused trans-units

For every `<trans-unit resname="…">` under `Resources/`, the `resname` is
searched (as a whole token) across the project source. Suffixes scanned:
`.4dm`, `.4DForm`, `.json`, `.css`, `.4DCatalog`, `.4DSettings`
(`--4dm-only` narrows this to `.4dm`). Generated/system folders
(`DerivedData`, `Trash`, `CompiledCode`, `.git`, `temporary files`) are skipped.
A `resname` that matches nowhere is reported as unused; the script exits
non-zero so CI fails.

### `sync_xlf.py` — language sync

The English files in `Resources/en.lproj` are the source of truth. For every
other `*.lproj` language and every English `.xlf`, two things are verified — and
each can be toggled because the full check can be **too aggressive** mid-work:

1. **Missing entries** (`--check-missing`, on by default) — an English
   trans-unit (matched by `resname`) that does not exist in the language, or a
   localized `.xlf` file that is missing entirely. With `--fix` the unit is
   copied over with `state="new"`.
2. **Source drift** (`--check-source-drift`, on by default) — the localized
   `<source>` no longer matches the English `<source>`. With `--fix`:
   - if the target was already translated, the source is updated to English and
     the target is flagged `state="needs-review-translation"`;
   - if the target was not translated, source and target are synced to English
     and flagged `state="new"`.

Trans-units that exist only in a language ("extra") are reported for information
and never deleted.

---

## CI output

Both scripts auto-detect the environment via `--format auto` (the default):

- **GitHub Actions** (`GITHUB_ACTIONS=true`) → inline
  [workflow annotations](https://docs.github.com/actions/using-workflows/workflow-commands-for-github-actions)
  (`::warning`/`::error file=…,line=…::…`) that appear on the file/line in the PR
  "Files changed" tab. The action sets this automatically.
- **Otherwise** → plain human-readable text.

Force it with `--format text` or `--format github`.

### GitLab Code Quality

Pass `--codequality <file>` to also emit a
[GitLab Code Quality](https://docs.gitlab.com/ee/ci/testing/code_quality.html)
JSON report so findings render in the merge-request widget. The GitLab template
below does this for you.

---

## GitLab CI

GitLab can't run a GitHub Action, but the scripts are dependency-free, so a
ready-made template downloads and runs them — and produces a Code Quality
report. Add to your `.gitlab-ci.yml`:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/e-marchand/xliff-lint-action/main/gitlab/xliff-lint.gitlab-ci.yml'
```

Override any knob as plain CI/CD variables on the `xliff-lint` job:

```yaml
xliff-lint:
  variables:
    XLIFF_SYNC_LANGUAGES: "fr,de"
    XLIFF_SYNC_CHECK_SOURCE_DRIFT: "false"
    XLIFF_FAIL_ON_ERROR: "true"
```

| Variable | Default | Maps to |
| --- | --- | --- |
| `XLIFF_LINT_REF` | `main` | Git ref of this action to fetch the scripts from. |
| `XLIFF_PROJECT_PATH` | `.` | `--root` |
| `XLIFF_LINT_UNUSED` | `true` | run `lint_xlf.py` |
| `XLIFF_LINT_4DM_ONLY` | `false` | `--4dm-only` |
| `XLIFF_CHECK_SYNC` | `true` | run `sync_xlf.py` |
| `XLIFF_SYNC_LANGUAGES` | `all` | `--languages` |
| `XLIFF_SYNC_CHECK_MISSING` | `true` | `--check-missing` / `--no-check-missing` |
| `XLIFF_SYNC_CHECK_SOURCE_DRIFT` | `true` | `--check-source-drift` / `--no-check-source-drift` |
| `XLIFF_FAIL_ON_ERROR` | `true` | fail the job on findings |

The full template is in
[`gitlab/xliff-lint.gitlab-ci.yml`](gitlab/xliff-lint.gitlab-ci.yml).

---

## Running locally / standalone

By default each script treats its own folder as the project root, so the simplest
setup is to drop them at the root of your 4D project. Copy them in:

```bash
curl -fsSL https://raw.githubusercontent.com/e-marchand/xliff-lint-action/main/lint_xlf.py -o lint_xlf.py
curl -fsSL https://raw.githubusercontent.com/e-marchand/xliff-lint-action/main/sync_xlf.py -o sync_xlf.py
```

Then, from the project root:

```bash
# unused trans-units
python3 lint_xlf.py            # report (exit non-zero if any)
python3 lint_xlf.py --fix      # remove them
python3 lint_xlf.py -v         # verbose: show a referencing file each

# language sync
python3 sync_xlf.py                       # report drift
python3 sync_xlf.py --fix                 # apply fixes
python3 sync_xlf.py -i                    # prompt before each fix
python3 sync_xlf.py --languages fr,de     # only some languages
python3 sync_xlf.py --no-check-source-drift   # only flag missing units
```

Use `--root PATH` to point at a project root other than the script's folder
(handy if you keep the scripts elsewhere).

### `lint_xlf.py` options

| Option | Description |
| --- | --- |
| `--fix` | Remove every unused trans-unit from all `.xlf` files. |
| `-i`, `--interactive` | Prompt before removing each unused `resname`. |
| `--4dm-only` | Only scan `.4dm` files for usage. |
| `--root PATH` | Project root (default: the script's folder). |
| `--format {auto,text,github}` | Output format (auto-detects GitHub Actions). |
| `--codequality FILE` | Also write a GitLab Code Quality JSON report. |
| `-v`, `--verbose` | Show a referencing file for each used `resname`. |

### `sync_xlf.py` options

| Option | Description |
| --- | --- |
| `--fix` | Apply fixes to localized files. |
| `-i`, `--interactive` | Prompt before each fix. |
| `--root PATH` | Project root (default: the script's folder). |
| `--languages LIST` | Comma-separated languages (e.g. `fr,de`) or `all`. |
| `--check-missing` / `--no-check-missing` | Toggle the missing-entry check (default on). |
| `--check-source-drift` / `--no-check-source-drift` | Toggle the source-drift check (default on). |
| `--format {auto,text,github}` | Output format (auto-detects GitHub Actions). |
| `--codequality FILE` | Also write a GitLab Code Quality JSON report. |

Exit codes: `0` clean, `1` issues found, `2` bad invocation (no `.xlf` files,
missing `en.lproj`, both checks disabled, …).

---

## Development

```bash
pip install -r requirements-dev.txt
pytest -v
```

Tests copy the fixtures under [`tests/fixtures/`](tests/fixtures) to a temp dir
and run the scripts as subprocesses, asserting exit codes, reports and `--fix`
behavior. The action itself is smoke-tested in
[`.github/workflows/test.yml`](.github/workflows/test.yml).

### Layout

```
xliff-lint-action/
├── action.yml                       # GitHub composite action
├── lint_xlf.py                      # unused trans-unit check
├── sync_xlf.py                      # language sync check
├── .github/workflows/test.yml       # CI for this repo
├── gitlab/xliff-lint.gitlab-ci.yml  # includable GitLab template
├── examples/github-workflow.yml     # copy-paste workflow for consumers
└── tests/                           # pytest + fixtures
```

## License

[MIT](LICENSE)
