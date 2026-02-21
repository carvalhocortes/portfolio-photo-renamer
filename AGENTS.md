# AGENTS.md — renomeador-de-fotos

Reference file for agentic coding agents. Read **before** making any changes.

## 1 — Project overview

Single-file Python CLI (`rename.py`, ~770 lines) that batch-renames photos and videos
using EXIF metadata extracted via `exiftool`. SOLID architecture with Protocols,
dependency injection, and independent field extractors.

Filename pattern: `YYYYMMDD_HHMMSS[_lat,lon][_model][_WxHpx][_PPIppi][_sizeKB].ext`
Files without EXIF date are prefixed with `_` (dimensions/size still included).
Arquivos com `_` no inicio nao sao mais ignorados automaticamente.

### Key classes

| Class | Responsibility |
|---|---|
| `ExifReader` | Shells out to `exiftool -n -j` (numeric JSON mode) |
| `DateExtractor` | Extracts timestamp from 27 tag candidates; filters zero dates |
| `ModelExtractor` | Extracts camera model string |
| `GPSExtractor` | Extracts lat/lon as decimal floats |
| `DimensionExtractor` | Extracts width/height in pixels |
| `PPIExtractor` | Extracts PPI when `ResolutionUnit == 2` (inches) |
| `SizeExtractor` | Gets file size in bytes via `Path.stat()` |
| `MetadataExtractor` | Composes all 6 extractors; each field fails independently |
| `FilenameFactory` | Builds filename string from `ImageInfo` |
| `SuffixCollisionResolver` | Adds `_01`, `_02` suffix when names collide |
| `ImageRenamer` | Orchestrates read -> extract -> rename for one file |
| `DirectoryProcessor` | Recursive directory traversal; skips `IGNORED_DIRS` |

### Protocols (for DI/testing)

`ExifReaderProtocol`, `FilenameFactoryProtocol`,
`CollisionResolverProtocol` — all in `rename.py`.

### Dataclasses

`ImageInfo` (extracted metadata fields),
`RenameResult` (`source_path`, `target_path`, `success`, `reason`).

## 2 — Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install exifread pillow-heif Pillow          # runtime deps
pip install pytest black isort flake8 mypy       # dev deps
```

External: **`exiftool`** must be on `$PATH`.
- macOS: `brew install exiftool`
- Debian/Ubuntu: `sudo apt install libimage-exiftool-perl`

Python: **3.14** in venv, compatible with 3.10+.

## 3 — Run

```bash
python rename.py                          # process current directory (recursive)
python rename.py path/to/folder           # process specific directory
python rename.py path/to/image.jpg        # process single file
python rename.py --dry-run                # preview without renaming
python rename.py --dry-run path/to/folder # preview specific directory
```

## 4 — Lint / Format / Typecheck

No config files (no pyproject.toml, .flake8, etc.). Use tool defaults.

```bash
black rename.py                           # format
isort rename.py                           # sort imports
flake8 rename.py                          # lint
mypy rename.py                            # typecheck
```

All checks together:
```bash
black --check rename.py && isort --check-only rename.py && flake8 rename.py && mypy rename.py
```

## 5 — Tests

No `tests/` directory exists yet. When creating tests:

```bash
pytest                                                          # all tests
pytest tests/test_rename.py -q                                  # single file
pytest tests/test_rename.py::test_slugify -q                    # single function
pytest tests/test_rename.py::TestMetadataExtractor::test_gps -q # class::method
pytest -k "slugify" -q                                          # keyword match
pytest --cov=. tests/                                           # with coverage
```

Guidelines:
- Place in `tests/test_rename.py`.
- Use `tmp_path` for filesystem operations.
- Mock `subprocess.run` via `monkeypatch` or `pytest-mock` (no exiftool dependency).
- One assertion per test where feasible.
- Leverage Protocols: inject fake `ExifReader`/`FilenameFactory` for isolated tests.

## 6 — Code style

### Imports

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

import json
import re
import shutil
import subprocess
import sys
```

Order: `__future__` > `from` stdlib > plain stdlib > `from typing` > third-party > local.
Use isort defaults. Prefer `from typing import X` over `typing.X`.

### Formatting

Black defaults: 88-char lines, double quotes, trailing commas in multi-line structures.

### Naming

| Element | Convention | Examples |
|---|---|---|
| Classes | PascalCase | `ExifReader`, `ImageRenamer`, `PPIExtractor` |
| Functions/methods | snake_case | `slugify()`, `extract_all()` |
| Private methods | `_leading_underscore` | `_parse_datetime()`, `_process_directory()` |
| Constants | UPPER_SNAKE_CASE | `DATE_TAGS`, `IGNORED_DIRS`, `SUPPORTED_EXTENSIONS` |
| Variables | snake_case | `new_name`, `results`, `error_count` |
| Dataclass fields | snake_case | `gps_lat`, `size_bytes`, `source_path` |

**Convention**: identifiers (variables, functions, classes, attributes) in **English**.
Docstrings and user-facing messages in **Portuguese**.

### Type annotations

- Annotate all function signatures (public and private): parameters + return type.
- Use `Optional[X]` not `X | None` for 3.10+ compatibility.
- Use `typing` generics: `Dict`, `List`, `Tuple`, `Sequence`, `Optional`.

### Docstrings

- Triple-quoted, single-line when short. Written in **Portuguese**.
- Every public method has one; private methods also have short docstrings.

```python
def slugify(value: str) -> str:
    """Retorna uma versao segura para nomes de arquivo de <value>."""
```

## 7 — Error handling

- `ExifReader` raises `RuntimeError` wrapping `CalledProcessError`/`TimeoutExpired`.
- `ImageRenamer.process()` catches `FileNotFoundError` and `RuntimeError` per file,
  returns `RenameResult` with `reason` instead of crashing the batch.
- Files without EXIF date are prefixed with `_`, not treated as errors.
- Never swallow exceptions silently. Catch only what you handle, re-raise with context.
- `SuffixCollisionResolver` raises `RuntimeError` after 999 failed attempts.

## 8 — Architecture

- **Single file**: everything in `rename.py`. If it grows, extract into `renomeador/`.
- **Dependency injection**: `ImageRenamer` takes reader, extractor, factory, resolver
  via constructor. `build_components(dry_run)` is the composition root.
- **Independent extractors**: each field extractor (Date, Model, GPS, Dimension, PPI,
  Size) can fail without affecting others. Add new extractors by implementing
  `FieldExtractorProtocol` and wiring into `MetadataExtractor`.
- **External tool**: `exiftool` invoked via `subprocess.run` with `-n` (numeric mode),
  `-j` (JSON output), 10s timeout, `capture_output=True`.
- **Directory skipping**: `IGNORED_DIRS` set (`venv`, `__pycache__`, `node_modules`,
  `.git`) plus dot-prefixed directories are excluded from recursive traversal.
- **FileModifyDate excluded**: `DATE_TAGS` intentionally omits `FileModifyDate` /
  `FileCreateDate` because they reflect filesystem/sync operations, not photo dates.

## 9 — Design decisions

- **File size in KB**: 2 decimal places (`1234.56KB`), not bytes.
- **PPI**: only when `ResolutionUnit == 2` (inches); values are int.
- **GPS**: already decimal from `exiftool -n`; 6 decimal places in filename.
- **Zero dates**: `0000:00:00 00:00:00` filtered (common in MP4 without real date).
- **Collision**: `_01`, `_02` suffix instead of skipping duplicate names.
- **`_` prefix**: arquivos com `_` no inicio sao processados normalmente.

## 10 — Git / commit

- Not yet a git repo. `.gitignore` should include: `venv/`, `__pycache__/`, `*.pyc`.
- Keep commits small, imperative: `add GPS extraction`, `fix timeout handling`.
- Do not commit unless explicitly asked. Never force-push.
- Never commit secrets or credential files.

## 11 — Cursor / Copilot rules

No `.cursor/`, `.cursorrules`, or `.github/copilot-instructions.md` exist.
If added later, agents must read and follow them.

## 12 — Agent safety

- Read this file and `rename.py` before making edits.
- Prefer small, tested changes.
- NEVER run destructive git commands without explicit user approval.
- Maintain the constructor-injection pattern when adding features.
- When renaming or moving files, update references in this AGENTS.md.
- The image/video files in the project root and subdirectories are real user data.
