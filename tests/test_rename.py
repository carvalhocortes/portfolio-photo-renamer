from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional, cast

import pytest

import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import rename


def test_slugify_basic_and_empty() -> None:
    assert rename.slugify("Canon EOS") == "canon-eos"
    assert rename.slugify("***") == "unknown"


def test_safe_float_and_int() -> None:
    class Ratio:
        def __init__(self, num: int, den: int) -> None:
            self.num = num
            self.den = den

    assert rename._safe_float(None) is None
    assert rename._safe_float("3.5") == 3.5
    assert rename._safe_float(Ratio(1, 2)) == 0.5
    assert rename._safe_float(Ratio(1, 0)) is None
    assert rename._safe_int("3.4") == 3
    assert rename._safe_int(None) is None


def test_first_tag_value() -> None:
    tags = {"A": 1, "B": None, "C": 3}
    assert rename._first_tag_value(tags, ["B", "C"]) == 3
    assert rename._first_tag_value(tags, ["X", "Y"]) is None


def test_same_file_and_name_already_correct(tmp_path: Path) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("x")
    assert rename._same_file(file_path, file_path) is True
    assert rename._name_already_correct(file_path, file_path) is True

    other_path = tmp_path / "b.txt"
    other_path.write_text("y")
    assert rename._name_already_correct(file_path, other_path) is False

    link_path = tmp_path / "a-link.txt"
    try:
        os.link(file_path, link_path)
    except OSError:
        pytest.skip("link not supported on this filesystem")
    assert rename._same_file(file_path, link_path) is True
    assert rename._name_already_correct(file_path, link_path) is True

    missing_path = tmp_path / "missing.txt"
    assert rename._same_file(missing_path, file_path) is False


def test_date_extractor_parses_formats() -> None:
    extractor = rename.DateExtractor()
    tags = {"DateTimeOriginal": "2020:01:02 03:04:05"}
    dt = extractor.extract(tags, Path("x"))
    assert dt == datetime(2020, 1, 2, 3, 4, 5)

    tags = {"CreateDate": "2020:01:02 03:04:05-03:00"}
    dt = extractor.extract(tags, Path("x"))
    assert dt == datetime(2020, 1, 2, 3, 4, 5)

    tags = {"CreateDate": "0000:00:00 00:00:00"}
    assert extractor.extract(tags, Path("x")) is None

    tags = {"CreateDate": "not-a-date"}
    assert extractor.extract(tags, Path("x")) is None


def test_model_extractor() -> None:
    extractor = rename.ModelExtractor()
    tags = {"Model": "  Canon EOS  "}
    assert extractor.extract(tags, Path("x")) == "Canon EOS"
    tags = {"Model": "  "}
    assert extractor.extract(tags, Path("x")) is None
    tags = {}
    assert extractor.extract(tags, Path("x")) is None


def test_gps_extractor() -> None:
    extractor = rename.GPSExtractor()
    tags = {"GPSLatitude": 1.2345, "GPSLongitude": -2.5}
    assert extractor.extract(tags, Path("x")) == (1.2345, -2.5)
    tags = {"GPSLatitude": None, "GPSLongitude": 1}
    assert extractor.extract(tags, Path("x")) is None
    tags = {"GPSLatitude": "x", "GPSLongitude": "y"}
    assert extractor.extract(tags, Path("x")) is None


def test_dimension_extractor() -> None:
    extractor = rename.DimensionExtractor()
    tags = {"ImageWidth": 100, "ImageHeight": 200}
    assert extractor.extract(tags, Path("x")) == (100, 200)
    tags = {"ImageWidth": 0, "ImageHeight": 200}
    assert extractor.extract(tags, Path("x")) is None


def test_ppi_extractor() -> None:
    extractor = rename.PPIExtractor()
    tags = {"ResolutionUnit": 2, "XResolution": 72}
    assert extractor.extract(tags, Path("x")) == 72
    tags = {"ResolutionUnit": 3, "XResolution": 72}
    assert extractor.extract(tags, Path("x")) is None
    tags = {"ResolutionUnit": 2, "XResolution": 0}
    assert extractor.extract(tags, Path("x")) is None


def test_size_extractor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    extractor = rename.SizeExtractor()
    file_path = tmp_path / "a.bin"
    file_path.write_bytes(b"1234")
    assert extractor.extract({}, file_path) == 4

    def raise_oserror(self: Path) -> int:
        raise OSError("fail")

    monkeypatch.setattr(Path, "stat", raise_oserror)
    assert extractor.extract({}, file_path) == 0


def test_metadata_extractor(tmp_path: Path) -> None:
    file_path = tmp_path / "a.jpg"
    file_path.write_text("x")
    tags = {
        "DateTimeOriginal": "2020:01:02 03:04:05",
        "Model": "Canon",
        "GPSLatitude": 1.0,
        "GPSLongitude": 2.0,
        "ImageWidth": 10,
        "ImageHeight": 20,
        "ResolutionUnit": 2,
        "XResolution": 72,
    }
    extractor = rename.MetadataExtractor(
        date_extractor=rename.DateExtractor(),
        model_extractor=rename.ModelExtractor(),
        gps_extractor=rename.GPSExtractor(),
        dimension_extractor=rename.DimensionExtractor(),
        ppi_extractor=rename.PPIExtractor(),
        size_extractor=rename.SizeExtractor(),
    )
    info = extractor.extract_all(tags, file_path)
    assert info.source_path == file_path
    assert info.timestamp == datetime(2020, 1, 2, 3, 4, 5)
    assert info.model == "Canon"
    assert info.gps_lat == 1.0
    assert info.gps_lon == 2.0
    assert info.width == 10
    assert info.height == 20
    assert info.ppi == 72
    assert info.extension == ".jpg"


def test_filename_factory_all_fields() -> None:
    info = rename.ImageInfo(
        source_path=Path("x.jpg"),
        timestamp=datetime(2020, 1, 2, 3, 4, 5),
        model="Canon EOS",
        gps_lat=1.2345678,
        gps_lon=2.0,
        width=400,
        height=300,
        ppi=72,
        size_bytes=1024,
        extension=".jpg",
    )
    factory = rename.FilenameFactory()
    name = factory.format(info)
    assert name == (
        "20200102_030405_1.234568,2.000000_"
        "canon-eos_400x300px_72ppi_1.00KB.jpg"
    )


def test_filename_factory_empty() -> None:
    info = rename.ImageInfo(source_path=Path("x"), extension=".jpg")
    factory = rename.FilenameFactory()
    assert factory.format(info) == "sem_nome.jpg"


def test_suffix_collision_resolver(tmp_path: Path) -> None:
    resolver = rename.SuffixCollisionResolver()
    target = tmp_path / "a.jpg"
    assert resolver.resolve(target) == target

    target.write_text("x")
    assert resolver.resolve(target).name == "a_01.jpg"

    resolver = rename.SuffixCollisionResolver(max_tentativas=1)
    (tmp_path / "a_01.jpg").write_text("y")
    with pytest.raises(RuntimeError):
        resolver.resolve(target)


def test_exif_reader_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rename.shutil, "which", lambda _: "/usr/bin/exiftool")

    result = SimpleNamespace(stdout=json.dumps([{"X": 1}]))
    monkeypatch.setattr(rename.subprocess, "run", lambda *a, **k: result)

    reader = rename.ExifReader()
    assert reader.read(Path("x")) == {"X": 1}


def test_exif_reader_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rename.shutil, "which", lambda _: "/usr/bin/exiftool")
    result = SimpleNamespace(stdout=json.dumps([]))
    monkeypatch.setattr(rename.subprocess, "run", lambda *a, **k: result)
    reader = rename.ExifReader()
    assert reader.read(Path("x")) == {}


def test_exif_reader_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rename.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError):
        rename.ExifReader().read(Path("x"))

    monkeypatch.setattr(rename.shutil, "which", lambda _: "/usr/bin/exiftool")
    error = subprocess.CalledProcessError(1, ["exiftool"], stderr="bad")
    monkeypatch.setattr(rename.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(error))
    with pytest.raises(RuntimeError):
        rename.ExifReader().read(Path("x"))

    timeout = subprocess.TimeoutExpired(cmd=["exiftool"], timeout=1)
    monkeypatch.setattr(rename.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(timeout))
    with pytest.raises(RuntimeError):
        rename.ExifReader().read(Path("x"))


@dataclass
class DummyExtractor:
    info: rename.ImageInfo

    def extract_all(self, tags: Dict[str, object], path: Path) -> rename.ImageInfo:
        return self.info


@dataclass
class DummyReader:
    tags: Dict[str, object]
    error: Optional[Exception] = None

    def read(self, path: Path) -> Dict[str, object]:
        if self.error:
            raise self.error
        return self.tags


class DummyFactory:
    def __init__(self, name: str) -> None:
        self._name = name

    def format(self, info: rename.ImageInfo) -> str:
        return self._name


class DummyResolver:
    def resolve(self, target: Path) -> Path:
        return target


def test_image_renamer_reader_errors(tmp_path: Path) -> None:
    file_path = tmp_path / "a.jpg"
    file_path.write_text("x")
    info = rename.ImageInfo(source_path=file_path)
    extractor = cast(rename.MetadataExtractor, DummyExtractor(info))

    reader = DummyReader({}, error=FileNotFoundError())
    renamer = rename.ImageRenamer(  # type: ignore[arg-type]
        reader, extractor, DummyFactory("a.jpg"), DummyResolver()
    )
    result = renamer.process(file_path)
    assert result.success is False

    reader = DummyReader({}, error=RuntimeError("boom"))
    renamer = rename.ImageRenamer(  # type: ignore[arg-type]
        reader, extractor, DummyFactory("a.jpg"), DummyResolver()
    )
    result = renamer.process(file_path)
    assert result.success is False


def test_image_renamer_name_ok(tmp_path: Path) -> None:
    file_path = tmp_path / "20200102_030405.jpg"
    file_path.write_text("x")
    info = rename.ImageInfo(
        source_path=file_path,
        timestamp=datetime(2020, 1, 2, 3, 4, 5),
        extension=".jpg",
    )
    extractor = cast(rename.MetadataExtractor, DummyExtractor(info))
    reader = DummyReader({})
    renamer = rename.ImageRenamer(  # type: ignore[arg-type]
        reader, extractor, DummyFactory(file_path.name), DummyResolver()
    )
    result = renamer.process(file_path)
    assert result.success is True
    assert result.reason == rename.REASON_NAME_OK


def test_image_renamer_collision_and_rename(tmp_path: Path) -> None:
    source_path = tmp_path / "a.jpg"
    source_path.write_text("x")
    target_path = tmp_path / "20200102_030405.jpg"
    target_path.write_text("y")

    info = rename.ImageInfo(
        source_path=source_path,
        timestamp=datetime(2020, 1, 2, 3, 4, 5),
        extension=".jpg",
    )
    extractor = cast(rename.MetadataExtractor, DummyExtractor(info))
    reader = DummyReader({})
    resolver = rename.SuffixCollisionResolver()
    renamer = rename.ImageRenamer(  # type: ignore[arg-type]
        reader, extractor, DummyFactory(target_path.name), resolver
    )

    result = renamer.process(source_path)
    assert result.success is True
    assert result.target_path is not None
    assert result.target_path.name == "20200102_030405_01.jpg"
    assert result.target_path.exists()


def test_image_renamer_no_date_prefix(tmp_path: Path) -> None:
    source_path = tmp_path / "IMG_1.jpg"
    source_path.write_text("x")

    info = rename.ImageInfo(
        source_path=source_path,
        timestamp=None,
        width=10,
        height=20,
        ppi=72,
        size_bytes=1024,
        extension=".jpg",
    )
    extractor = cast(rename.MetadataExtractor, DummyExtractor(info))
    reader = DummyReader({})
    renamer = rename.ImageRenamer(  # type: ignore[arg-type]
        reader, extractor, DummyFactory("ignored"), DummyResolver()
    )

    result = renamer.process(source_path)
    assert result.success is True
    assert result.target_path is not None
    assert result.target_path.name == "_IMG_1_10x20px_72ppi_1.00KB.jpg"
    assert result.target_path.exists()


def test_image_renamer_no_date_no_info(tmp_path: Path) -> None:
    source_path = tmp_path / "IMG_2.jpg"
    source_path.write_text("x")
    info = rename.ImageInfo(source_path=source_path, timestamp=None, extension=".jpg")
    extractor = cast(rename.MetadataExtractor, DummyExtractor(info))
    reader = DummyReader({})
    renamer = rename.ImageRenamer(  # type: ignore[arg-type]
        reader, extractor, DummyFactory("ignored"), DummyResolver()
    )
    result = renamer.process(source_path)
    assert result.success is True
    assert result.reason == rename.REASON_NAME_OK


def test_image_renamer_rename_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_path = tmp_path / "a.jpg"
    source_path.write_text("x")
    info = rename.ImageInfo(
        source_path=source_path,
        timestamp=datetime(2020, 1, 2, 3, 4, 5),
        extension=".jpg",
    )
    extractor = cast(rename.MetadataExtractor, DummyExtractor(info))
    reader = DummyReader({})

    def raise_oserror(self: Path, target: Path) -> None:
        raise OSError("fail")

    monkeypatch.setattr(Path, "rename", raise_oserror)
    renamer = rename.ImageRenamer(  # type: ignore[arg-type]
        reader, extractor, DummyFactory("b.jpg"), DummyResolver()
    )
    result = renamer.process(source_path)
    assert result.success is False


def test_image_renamer_dry_run(tmp_path: Path) -> None:
    source_path = tmp_path / "a.jpg"
    source_path.write_text("x")
    info = rename.ImageInfo(
        source_path=source_path,
        timestamp=datetime(2020, 1, 2, 3, 4, 5),
        extension=".jpg",
    )
    extractor = cast(rename.MetadataExtractor, DummyExtractor(info))
    reader = DummyReader({})
    renamer = rename.ImageRenamer(  # type: ignore[arg-type]
        reader,
        extractor,
        DummyFactory("b.jpg"),
        DummyResolver(),
        dry_run=True,
    )
    result = renamer.process(source_path)
    assert result.success is True
    assert result.target_path is not None
    assert result.target_path.name == "b.jpg"


def test_directory_processor(tmp_path: Path) -> None:
    processed: list[Path] = []

    class RecorderRenamer:
        def process(self, path: Path) -> rename.RenameResult:
            processed.append(path)
            return rename.RenameResult(source_path=path, success=True)

    (tmp_path / "a.jpg").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.jpg").write_text("x")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "d.jpg").write_text("x")
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "e.jpg").write_text("x")

    processor = rename.DirectoryProcessor(
        cast(rename.ImageRenamer, RecorderRenamer())
    )
    results = processor.process(tmp_path)
    assert len(results) == 2
    assert (tmp_path / "a.jpg") in processed
    assert (sub / "c.jpg") in processed

    missing = tmp_path / "missing"
    results = processor.process(missing)
    assert results == []


def test_directory_processor_single_file(tmp_path: Path) -> None:
    processed: list[Path] = []

    class RecorderRenamer:
        def process(self, path: Path) -> rename.RenameResult:
            processed.append(path)
            return rename.RenameResult(source_path=path, success=True)

    file_path = tmp_path / "single.jpg"
    file_path.write_text("x")

    processor = rename.DirectoryProcessor(
        cast(rename.ImageRenamer, RecorderRenamer())
    )
    results = processor.process(file_path)
    assert len(results) == 1
    assert file_path in processed


def test_build_components_smoke() -> None:
    renamer, processor = rename.build_components(dry_run=True)
    assert isinstance(renamer, rename.ImageRenamer)
    assert isinstance(processor, rename.DirectoryProcessor)


def test_print_report(capsys: pytest.CaptureFixture[str]) -> None:
    results = [
        rename.RenameResult(
            source_path=Path("a"),
            target_path=Path("b"),
            success=True,
            reason=rename.REASON_NO_DATE,
        ),
        rename.RenameResult(
            source_path=Path("c"),
            target_path=Path("d"),
            success=True,
            reason="",
        ),
        rename.RenameResult(source_path=Path("e"), success=False, reason="err"),
        rename.RenameResult(
            source_path=Path("f"),
            success=True,
            reason=rename.REASON_NAME_OK,
        ),
    ]
    rename._print_report(results)
    output = capsys.readouterr().out
    assert "Renomeados:" in output
    assert "Sem data" in output
    assert "Erros" in output


def test_main_usage_and_dry_run(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert rename.main(["rename.py", "a", "b"]) == 1
    output = capsys.readouterr().out
    assert "Uso:" in output

    class DummyProcessor:
        def process(self, path: Path) -> list[rename.RenameResult]:
            return []

    monkeypatch.setattr(rename, "build_components", lambda dry_run=False: (None, DummyProcessor()))
    assert rename.main(["rename.py", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "MODO DRY-RUN" in output


def test_main_processor_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyProcessor:
        def process(self, path: Path) -> list[rename.RenameResult]:
            raise RuntimeError("boom")

    monkeypatch.setattr(rename, "build_components", lambda dry_run=False: (None, DummyProcessor()))
    assert rename.main(["rename.py"]) == 1


def test_cli_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rename, "main", lambda argv: 0)
    assert rename._cli_entrypoint() == 0
