"""Renomeador de fotos e vídeos baseado em metadados EXIF.

Renomeia arquivos usando timestamp, GPS, modelo da câmera, dimensões,
PPI e tamanho em KB extraídos via exiftool.
Padrão: YYYYMMDD_HHMMSS[_lat,lon][_modelo][_WxHpx][_PPIppi][_tamanhoKB].ext
"""

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


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

PHOTO_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".heic", ".tiff", ".webp",
    ".raw", ".arw", ".cr2", ".nef", ".orf", ".rw2",
}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi", ".mkv"}
SUPPORTED_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

DATE_TAGS: Tuple[str, ...] = (
    "DateTimeOriginal",
    "EXIF DateTimeOriginal",
    "EXIF DateTimeDigitized",
    "CreateDate",
    "EXIF CreateDate",
    "ModifyDate",
    "EXIF ModifyDate",
    "Image DateTime",
    "TrackCreateDate",
    "TrackModifyDate",
    "MediaCreateDate",
    "MediaModifyDate",
    "XMP:CreateDate",
    "XMP:DateTimeOriginal",
    "XMP:MetadataDate",
    "IPTC:DateCreated",
    "IPTC:DigitalCreationDate",
    "IPTC:DateTimeCreated",
    "IPTC2:DateCreated",
    "IPTC2:DateTimeCreated",
    "QuickTime:CreateDate",
    "QuickTime:ModifyDate",
    "QuickTime:TrackCreateDate",
    "QuickTime:TrackModifyDate",
    "Composite:DateTimeCreated",
    "Composite:DateCreated",
    "Composite:DateTime",
    # FileModifyDate/FileCreateDate omitidos intencionalmente:
    # refletem operações de filesystem (cópia, sync), não a data real da foto.
)

MODEL_TAGS: Tuple[str, ...] = (
    "Model",
    "EXIF Model",
    "MakerNotes:Model",
    "QuickTime:Model",
    "QuickTime:TrackModel",
    "Composite:Model",
    "Author",
    "SamsungModel",
)

GPS_LAT_TAGS: Tuple[str, ...] = (
    "GPSLatitude",
    "GPS GPSLatitude",
    "XMP:GPSLatitude",
)

GPS_LON_TAGS: Tuple[str, ...] = (
    "GPSLongitude",
    "GPS GPSLongitude",
    "XMP:GPSLongitude",
)

WIDTH_TAGS: Tuple[str, ...] = (
    "ExifImageWidth",
    "EXIF ExifImageWidth",
    "ImageWidth",
    "Image Width",
    "QuickTime:ImageWidth",
    "Composite:ImageWidth",
    "XMP:ImageWidth",
)

HEIGHT_TAGS: Tuple[str, ...] = (
    "ExifImageHeight",
    "EXIF ExifImageHeight",
    "ImageHeight",
    "Image Height",
    "QuickTime:ImageHeight",
    "Composite:ImageHeight",
    "XMP:ImageHeight",
)

RESOLUTION_TAGS: Tuple[str, ...] = (
    "XResolution",
    "EXIF XResolution",
)

RESOLUTION_UNIT_TAGS: Tuple[str, ...] = (
    "ResolutionUnit",
    "EXIF ResolutionUnit",
)

DATE_FORMATS: Tuple[str, ...] = (
    "%Y:%m:%d %H:%M:%S",
    "%Y:%m:%d %H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y:%m:%d %H:%M:%S.%f",
    "%Y:%m:%d %H:%M:%S.%f%z",
)

ZERO_DATES = {"0000:00:00 00:00:00", "0000:00:00", ""}

IGNORED_DIRS = {"venv", "__pycache__", "node_modules", ".git"}


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------


def slugify(value: str) -> str:
    """Retorna uma versão segura para nomes de arquivo de <value>."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    return cleaned.strip("-") or "unknown"


def _safe_float(value: Any) -> Optional[float]:
    """Converte <value> para float com segurança; retorna None em falha."""
    if value is None:
        return None
    if hasattr(value, "num") and hasattr(value, "den"):
        try:
            return float(value.num) / float(value.den)
        except (ZeroDivisionError, TypeError, ValueError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    """Converte <value> para int de forma segura, retornando None em falha."""
    number = _safe_float(value)
    if number is None:
        return None
    return int(round(number))


def _first_tag_value(
    tags: Dict[str, Any], candidates: Sequence[str]
) -> Optional[Any]:
    """Retorna o valor bruto do primeiro tag existente em <candidates>."""
    for name in candidates:
        if name in tags and tags[name] is not None:
            return tags[name]
    return None


def _same_file(path_a: Path, path_b: Path) -> bool:
    """Retorna True se <path_a> e <path_b> apontam para o mesmo arquivo."""
    try:
        return path_a.samefile(path_b)
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Dataclasses de domínio
# ---------------------------------------------------------------------------


@dataclass
class ImageInfo:
    """Contém os metadados extraídos de um arquivo de mídia."""

    source_path: Path
    timestamp: Optional[datetime] = None
    model: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    ppi: Optional[int] = None
    size_bytes: int = 0
    extension: str = ""


@dataclass
class RenameResult:
    """Resultado da tentativa de renomear um arquivo."""

    source_path: Path
    target_path: Optional[Path] = None
    success: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# Protocols (Dependency Inversion)
# ---------------------------------------------------------------------------


class ExifReaderProtocol(Protocol):
    """Protocolo para leitura de metadados de um arquivo."""

    def read(self, path: Path) -> Dict[str, Any]: ...


class FilenameFactoryProtocol(Protocol):
    """Protocolo para construção do nome de arquivo final."""

    def format(self, info: ImageInfo) -> str: ...


class CollisionResolverProtocol(Protocol):
    """Protocolo para resolução de colisão de nomes de arquivo."""

    def resolve(self, target: Path) -> Path: ...


# ---------------------------------------------------------------------------
# ExifReader — Infraestrutura (I/O)
# ---------------------------------------------------------------------------


class ExifReader:
    """Lê metadados via exiftool em modo numérico JSON."""

    def __init__(
        self, exiftool_cmd: str = "exiftool", timeout: int = 10
    ) -> None:
        self._exiftool = shutil.which(exiftool_cmd)
        self._timeout = timeout

    def read(self, path: Path) -> Dict[str, Any]:
        """Extrai todos os metadados de <path> via ExifTool."""
        if not self._exiftool:
            raise RuntimeError("exiftool não encontrado no PATH")
        try:
            result = subprocess.run(
                [self._exiftool, "-n", "-j", str(path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"exiftool falhou ({exc.returncode}): {exc.stderr.strip()}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"exiftool excedeu timeout de {exc.timeout}s para {path}"
            ) from exc

        entries = json.loads(result.stdout)
        if not entries:
            return {}
        return entries[0]


# ---------------------------------------------------------------------------
# Field Extractors — cada um extrai um campo independente (SRP + OCP)
# ---------------------------------------------------------------------------


class DateExtractor:
    """Extrai timestamp dos metadados com múltiplos formatos."""

    def extract(
        self, tags: Dict[str, Any], path: Path
    ) -> Optional[datetime]:
        """Retorna o datetime do primeiro tag de data válido encontrado."""
        for tag_name in DATE_TAGS:
            raw = tags.get(tag_name)
            if raw is None:
                continue
            value = str(raw).strip()
            if value in ZERO_DATES:
                continue
            parsed = self._parse_datetime(value)
            if parsed is not None:
                return parsed
        return None

    def _parse_datetime(self, value: str) -> Optional[datetime]:
        """Tenta parsear <value> em múltiplos formatos de data."""
        # Remove subsegundos com timezone colado (ex: ".993-03:00")
        cleaned = re.sub(r"\.\d+", "", value)
        for fmt in DATE_FORMATS:
            try:
                dt = datetime.strptime(cleaned, fmt)
                # Normaliza para naive (sem timezone) para consistência no nome
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
            except ValueError:
                continue
        return None


class ModelExtractor:
    """Extrai o modelo da câmera dos metadados."""

    def extract(self, tags: Dict[str, Any], path: Path) -> Optional[str]:
        """Retorna o modelo da câmera limpo, ou None."""
        raw = _first_tag_value(tags, MODEL_TAGS)
        if raw is None:
            return None
        model = str(raw).strip()
        return model if model else None


class GPSExtractor:
    """Extrai coordenadas GPS em graus decimais."""

    def extract(
        self, tags: Dict[str, Any], path: Path
    ) -> Optional[Tuple[float, float]]:
        """Retorna (latitude, longitude) em decimal, ou None."""
        lat_raw = _first_tag_value(tags, GPS_LAT_TAGS)
        lon_raw = _first_tag_value(tags, GPS_LON_TAGS)
        if lat_raw is None or lon_raw is None:
            return None

        lat = _safe_float(lat_raw)
        lon = _safe_float(lon_raw)
        if lat is None or lon is None:
            return None

        return (lat, lon)


class DimensionExtractor:
    """Extrai dimensões (largura x altura) em pixels."""

    def extract(
        self, tags: Dict[str, Any], path: Path
    ) -> Optional[Tuple[int, int]]:
        """Retorna (width, height) ou None se indisponível."""
        width = self._first_int(tags, WIDTH_TAGS)
        height = self._first_int(tags, HEIGHT_TAGS)
        if width is None or height is None or width <= 0 or height <= 0:
            return None
        return (width, height)

    def _first_int(
        self, tags: Dict[str, Any], candidates: Sequence[str]
    ) -> Optional[int]:
        """Retorna o primeiro valor inteiro válido entre os candidatos."""
        raw = _first_tag_value(tags, candidates)
        return _safe_int(raw)


class PPIExtractor:
    """Extrai PPI (Pixels Per Inch) quando ResolutionUnit indica polegadas."""

    INCHES_UNIT = 2

    def extract(self, tags: Dict[str, Any], path: Path) -> Optional[int]:
        """Retorna PPI inteiro ou None se não disponível/confiável."""
        unit_raw = _first_tag_value(tags, RESOLUTION_UNIT_TAGS)
        unit = _safe_int(unit_raw)
        if unit != self.INCHES_UNIT:
            return None

        res_raw = _first_tag_value(tags, RESOLUTION_TAGS)
        ppi = _safe_int(res_raw)
        if ppi is None or ppi <= 0:
            return None
        return ppi


class SizeExtractor:
    """Extrai o tamanho do arquivo em bytes via sistema de arquivos."""

    def extract(self, tags: Dict[str, Any], path: Path) -> int:
        """Retorna o tamanho em bytes do arquivo (sempre disponível)."""
        try:
            return path.stat().st_size
        except OSError:
            return 0


# ---------------------------------------------------------------------------
# MetadataExtractor — Composição de Extractors (OCP)
# ---------------------------------------------------------------------------


class MetadataExtractor:
    """Compõe múltiplos extractors para construir um ImageInfo completo."""

    def __init__(
        self,
        date_extractor: DateExtractor,
        model_extractor: ModelExtractor,
        gps_extractor: GPSExtractor,
        dimension_extractor: DimensionExtractor,
        ppi_extractor: PPIExtractor,
        size_extractor: SizeExtractor,
    ) -> None:
        self._date = date_extractor
        self._model = model_extractor
        self._gps = gps_extractor
        self._dimension = dimension_extractor
        self._ppi = ppi_extractor
        self._size = size_extractor

    def extract_all(self, tags: Dict[str, Any], path: Path) -> ImageInfo:
        """Extrai todos os campos independentemente e retorna ImageInfo."""
        timestamp = self._date.extract(tags, path)
        model = self._model.extract(tags, path)
        gps = self._gps.extract(tags, path)
        dims = self._dimension.extract(tags, path)
        ppi = self._ppi.extract(tags, path)
        size_bytes = self._size.extract(tags, path)

        return ImageInfo(
            source_path=path,
            timestamp=timestamp,
            model=model,
            gps_lat=gps[0] if gps else None,
            gps_lon=gps[1] if gps else None,
            width=dims[0] if dims else None,
            height=dims[1] if dims else None,
            ppi=ppi,
            size_bytes=size_bytes,
            extension=path.suffix.lower(),
        )


# ---------------------------------------------------------------------------
# FilenameFactory — Construção do nome de arquivo (SRP)
# ---------------------------------------------------------------------------


class FilenameFactory:
    """Constrói o nome de arquivo final a partir de um ImageInfo."""

    def format(self, info: ImageInfo) -> str:
        """Gera o nome no padrão YYYYMMDD_HHMMSS[_campos...].ext."""
        parts: List[str] = []

        # Data/hora (obrigatório para nome válido)
        if info.timestamp:
            parts.append(info.timestamp.strftime("%Y%m%d"))
            parts.append(info.timestamp.strftime("%H%M%S"))

        # GPS em decimal
        if info.gps_lat is not None and info.gps_lon is not None:
            parts.append(f"{info.gps_lat:.6f},{info.gps_lon:.6f}")

        # Modelo da câmera
        if info.model:
            parts.append(slugify(info.model))

        # Dimensões em pixels
        if info.width is not None and info.height is not None:
            parts.append(f"{info.width}x{info.height}px")

        # PPI
        if info.ppi is not None:
            parts.append(f"{info.ppi}ppi")

        # Tamanho em KB com 2 casas decimais
        if info.size_bytes > 0:
            size_kb = info.size_bytes / 1024
            parts.append(f"{size_kb:.2f}KB")

        name = "_".join(parts)
        if name:
            return f"{name}{info.extension}"
        return f"sem_nome{info.extension}"


# ---------------------------------------------------------------------------
# CollisionResolver — Resolução de colisão de nomes (SRP)
# ---------------------------------------------------------------------------


class SuffixCollisionResolver:
    """Resolve colisões adicionando sufixo incremental (_01, _02, ...)."""

    def __init__(self, max_tentativas: int = 999) -> None:
        self._max_tentativas = max_tentativas

    def resolve(self, target: Path) -> Path:
        """Retorna um Path único, adicionando sufixo se necessário."""
        if not target.exists():
            return target

        stem = target.stem
        suffix = target.suffix
        directory = target.parent

        for i in range(1, self._max_tentativas + 1):
            candidate = directory / f"{stem}_{i:02d}{suffix}"
            if not candidate.exists():
                return candidate

        raise RuntimeError(
            f"Não foi possível resolver colisão para {target} "
            f"após {self._max_tentativas} tentativas"
        )


# ---------------------------------------------------------------------------
# ImageRenamer — Orquestração de renomeação (SRP)
# ---------------------------------------------------------------------------


class ImageRenamer:
    """Orquestra leitura de metadados, extração e renomeação de um arquivo."""

    def __init__(
        self,
        reader: ExifReaderProtocol,
        extractor: MetadataExtractor,
        factory: FilenameFactoryProtocol,
        resolver: CollisionResolverProtocol,
        dry_run: bool = False,
    ) -> None:
        self._reader = reader
        self._extractor = extractor
        self._factory = factory
        self._resolver = resolver
        self._dry_run = dry_run

    def process(self, path: Path) -> RenameResult:
        """Tenta renomear <path> com base nos metadados extraídos."""
        try:
            tags = self._reader.read(path)
        except FileNotFoundError:
            return RenameResult(
                source_path=path, reason="arquivo não encontrado"
            )
        except RuntimeError as exc:
            return RenameResult(source_path=path, reason=f"erro ao ler: {exc}")

        info = self._extractor.extract_all(tags, path)

        # Sem timestamp → prefixar com _ para evitar reprocessamento
        if info.timestamp is None:
            return self._prefix_without_date(path, info)

        new_name = self._factory.format(info)
        target = path.with_name(new_name)

        if target.exists() and _same_file(target, path):
            return RenameResult(
                source_path=path,
                target_path=path,
                success=True,
                reason="nome já correto",
            )

        # Mesmo nome → nada a fazer
        if target == path:
            return RenameResult(
                source_path=path,
                target_path=path,
                success=True,
                reason="nome já correto",
            )

        target = self._resolver.resolve(target)

        if self._dry_run:
            return RenameResult(
                source_path=path, target_path=target, success=True
            )

        try:
            path.rename(target)
        except OSError as exc:
            return RenameResult(
                source_path=path, reason=f"erro ao renomear: {exc}"
            )

        return RenameResult(source_path=path, target_path=target, success=True)

    def _prefix_without_date(
        self, path: Path, info: ImageInfo
    ) -> RenameResult:
        """Prefixa sem data com '_' incluindo dimensões e tamanho."""
        current_name = path.name

        # Constrói sufixo informativo mesmo sem data
        parts: List[str] = []
        if info.width is not None and info.height is not None:
            parts.append(f"{info.width}x{info.height}px")
        if info.ppi is not None:
            parts.append(f"{info.ppi}ppi")
        if info.size_bytes > 0:
            parts.append(f"{info.size_bytes / 1024:.2f}KB")

        info_suffix = "_".join(parts)
        if info_suffix:
            new_name = f"_{info_suffix}{info.extension}"
        else:
            new_name = f"_{current_name}"

        target = path.with_name(new_name)
        if target.exists():
            target = self._resolver.resolve(target)

        if self._dry_run:
            return RenameResult(
                source_path=path,
                target_path=target,
                success=True,
                reason="sem data",
            )

        try:
            path.rename(target)
        except OSError as exc:
            return RenameResult(
                source_path=path, reason=f"erro ao prefixar: {exc}"
            )

        return RenameResult(
            source_path=path,
            target_path=target,
            success=True,
            reason="sem data",
        )


# ---------------------------------------------------------------------------
# DirectoryProcessor — Processamento recursivo (SRP)
# ---------------------------------------------------------------------------


class DirectoryProcessor:
    """Percorre diretórios recursivamente e renomeia arquivos suportados."""

    def __init__(
        self,
        renamer: ImageRenamer,
        extensions: frozenset[str] = frozenset(SUPPORTED_EXTENSIONS),
    ) -> None:
        self._renamer = renamer
        self._extensions = extensions

    def process(self, path: Path) -> List[RenameResult]:
        """Processa <path> (arquivo ou diretório) e retorna resultados."""
        results: List[RenameResult] = []

        if path.is_file():
            result = self._process_file(path)
            if result is not None:
                results.append(result)
        elif path.is_dir():
            results.extend(self._process_directory(path))
        else:
            print(f"Caminho inválido: {path}")

        return results

    def _process_file(self, path: Path) -> Optional[RenameResult]:
        """Processa um arquivo se sua extensão for suportada."""
        if path.suffix.lower() not in self._extensions:
            return None
        return self._renamer.process(path)

    def _process_directory(self, path: Path) -> List[RenameResult]:
        """Percorre recursivamente <path> e processa todos os arquivos."""
        results: List[RenameResult] = []
        print(f"\nProcessando diretório: {path}")

        entries = sorted(path.iterdir())
        for entry in entries:
            if entry.is_file():
                result = self._process_file(entry)
                if result is not None:
                    results.append(result)
            elif (
                entry.is_dir()
                and not entry.name.startswith(".")
                and entry.name not in IGNORED_DIRS
            ):
                results.extend(self._process_directory(entry))

        return results


# ---------------------------------------------------------------------------
# Fábrica de componentes (Dependency Injection)
# ---------------------------------------------------------------------------


def build_components(
    dry_run: bool = False,
) -> Tuple[ImageRenamer, DirectoryProcessor]:
    """Constrói e conecta todos os componentes com injeção de dependência."""
    reader = ExifReader()
    extractor = MetadataExtractor(
        date_extractor=DateExtractor(),
        model_extractor=ModelExtractor(),
        gps_extractor=GPSExtractor(),
        dimension_extractor=DimensionExtractor(),
        ppi_extractor=PPIExtractor(),
        size_extractor=SizeExtractor(),
    )
    factory = FilenameFactory()
    resolver = SuffixCollisionResolver()
    renamer = ImageRenamer(
        reader, extractor, factory, resolver, dry_run=dry_run
    )
    processor = DirectoryProcessor(renamer)
    return renamer, processor


# ---------------------------------------------------------------------------
# Relatório de resultados
# ---------------------------------------------------------------------------


def _print_report(results: List[RenameResult]) -> None:
    """Imprime resumo dos resultados de renomeação."""
    renamed_count = 0
    no_date_count = 0
    error_count = 0

    for r in results:
        if r.success and r.target_path and r.target_path != r.source_path:
            if r.reason == "sem data":
                no_date_count += 1
                print(
                    f"  [SEM DATA] {r.source_path.name} "
                    f"→ {r.target_path.name}"
                )
            else:
                renamed_count += 1
                print(
                    f"  [OK] {r.source_path.name} "
                    f"→ {r.target_path.name}"
                )
        elif r.success and r.reason == "nome já correto":
            pass  # Silencioso para arquivos que já têm o nome correto
        elif not r.success:
            error_count += 1
            print(f"  [ERRO] {r.source_path.name}: {r.reason}")

    print("\n--- Resumo ---")
    print(f"  Total processados: {len(results)}")
    print(f"  Renomeados:        {renamed_count}")
    print(f"  Sem data (prefixo _): {no_date_count}")
    print(f"  Erros:             {error_count}")
    print(
        f"  Sem alteração:     "
        f"{len(results) - renamed_count - no_date_count - error_count}"
    )


# ---------------------------------------------------------------------------
# CLI — Ponto de entrada
# ---------------------------------------------------------------------------


def main(argv: Sequence[str]) -> int:
    """Ponto de entrada da CLI."""
    args = list(argv[1:])
    dry_run = "--dry-run" in args
    if dry_run:
        args.remove("--dry-run")

    if len(args) > 1:
        print("Uso: python rename.py [--dry-run] [caminho_da_imagem_ou_pasta]")
        return 1

    path = Path(args[0]) if args else Path(".")
    _, processor = build_components(dry_run=dry_run)

    if dry_run:
        print("*** MODO DRY-RUN: nenhum arquivo será renomeado ***\n")

    try:
        results = processor.process(path)
    except RuntimeError as exc:
        print(f"Erro crítico: {exc}")
        return 1

    _print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
