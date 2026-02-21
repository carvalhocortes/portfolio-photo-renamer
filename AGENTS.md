# AGENTS.md — portfolio-photo-renamer

Reference file for agentic coding agents. Read before making changes.

## 1 — Project overview

Single-file Python CLI (`rename.py`) that batch-renames photos and videos using
EXIF metadata extracted via `exiftool`. SOLID architecture with Protocols,
dependency injection, independent field extractors, and a collision resolver.

Filename pattern (with date):
`YYYYMMDD_HHMMSS[_lat,lon][_model][_WxHpx][_PPIppi][_sizeKB].ext`

For files without EXIF date:
`_ORIGINALNAME_...` (original stem kept, prefixed with `_` and extra fields).

## 2 — Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install exifread pillow-heif Pillow
pip install pytest pytest-cov black isort flake8 mypy
```

External dependency: **exiftool** on PATH
```bash
brew install exiftool
sudo apt install libimage-exiftool-perl
```

## 3 — Run

```bash
python rename.py                          # process current directory (recursive)
python rename.py path/to/folder           # process specific directory
python rename.py path/to/image.jpg        # process single file
python rename.py --dry-run                # preview without renaming
```

## 4 — Lint / Format / Typecheck

```bash
black rename.py
isort rename.py
flake8 rename.py
mypy rename.py
```

All checks:
```bash
black --check rename.py && isort --check-only rename.py && flake8 rename.py && mypy rename.py
```

## 5 — Tests

```bash
pytest                                   # all tests
pytest tests/test_rename.py -q           # single file
pytest tests/test_rename.py::test_name -q # single test
pytest -k "slugify" -q                   # keyword match
pytest --cov=rename --cov-report=term-missing
```

Goal: 100% coverage for `rename.py`.

## 6 — Architecture summary

- `ExifReader` shells out to `exiftool -n -j` (numeric JSON), 10s timeout.
- Independent extractors: `DateExtractor`, `ModelExtractor`, `GPSExtractor`,
  `DimensionExtractor`, `PPIExtractor`, `SizeExtractor`.
- `MetadataExtractor` composes extractors and builds `ImageInfo`.
- `FilenameFactory` builds the final filename string.
- `SuffixCollisionResolver` adds `_01`, `_02` when target exists.
- `ImageRenamer` orchestrates read -> extract -> rename; uses `_rename()` helper.
- `DirectoryProcessor` recursively traverses; skips dot dirs + `IGNORED_DIRS`.

## 7 — Key data structures

- `ImageInfo`: `source_path`, `timestamp`, `model`, `gps_lat`, `gps_lon`,
  `width`, `height`, `ppi`, `size_bytes`, `extension`.
- `RenameResult`: `source_path`, `target_path`, `success`, `reason`.

## 8 — Conventions

- Identifiers (variables, functions, classes, attributes) in **English**.
- Docstrings and user-facing messages in **Portuguese**.
- Public APIs require type annotations; prefer `Optional[X]`.
- Use `from typing import X` imports; keep stdlib imports grouped.
- Keep changes in `rename.py` unless a clear need to split arises.

## 9 — Error handling

- `ExifReader` raises `RuntimeError` for missing exiftool, failures, timeouts.
- `ImageRenamer.process()` catches per-file errors and returns `RenameResult`.
- `SuffixCollisionResolver` raises after `max_tentativas`.

## 10 — Directory rules

- Skip dot-prefixed directories.
- Skip names in `IGNORED_DIRS` (`venv`, `__pycache__`, `node_modules`, `.git`).

## 11 — Design decisions

- File size in KB with 2 decimals (`1234.56KB`).
- PPI only when `ResolutionUnit == 2` (inches).
- GPS decimal is already normalized with `exiftool -n`.
- FileModifyDate/FileCreateDate intentionally excluded from `DATE_TAGS`.
- `_name_already_correct()` prevents `_01` toggling across runs.

## 12 — Cursor / Copilot rules

No `.cursor/`, `.cursorrules`, or `.github/copilot-instructions.md` present.
If added later, follow them.
