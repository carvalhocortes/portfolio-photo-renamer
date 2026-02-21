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
    f = _safe_float(value)
    if f is None:
        return None
    return int(round(f))


def _first_tag_value(
    tags: Dict[str, Any], candidates: Sequence[str]
) -> Optional[Any]:
    """Retorna o valor bruto do primeiro tag existente em <candidates>."""
    for name in candidates:
        if name in tags and tags[name] is not None:
            return tags[name]
    return None


# ---------------------------------------------------------------------------
# Dataclasses de domínio
# ---------------------------------------------------------------------------


@dataclass
class ImageInfo:
    """Contém os metadados extraídos de um arquivo de mídia."""

    caminho_origem: Path
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

    src: Path
    dst: Optional[Path] = None
    success: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# Protocols (Dependency Inversion)
# ---------------------------------------------------------------------------


class ExifReaderProtocol(Protocol):
    """Protocolo para leitura de metadados de um arquivo."""

    def read(self, caminho: Path) -> Dict[str, Any]: ...


class FilenameFactoryProtocol(Protocol):
    """Protocolo para construção do nome de arquivo final."""

    def format(self, info: ImageInfo) -> str: ...


class CollisionResolverProtocol(Protocol):
    """Protocolo para resolução de colisão de nomes de arquivo."""

    def resolve(self, destino: Path) -> Path: ...


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

    def read(self, caminho: Path) -> Dict[str, Any]:
        """Extrai todos os metadados de <caminho> via ExifTool."""
        if not self._exiftool:
            raise RuntimeError("exiftool não encontrado no PATH")
        try:
            resultado = subprocess.run(
                [self._exiftool, "-n", "-j", str(caminho)],
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
                f"exiftool excedeu timeout de {exc.timeout}s para {caminho}"
            ) from exc

        entries = json.loads(resultado.stdout)
        if not entries:
            return {}
        return entries[0]


# ---------------------------------------------------------------------------
# Field Extractors — cada um extrai um campo independente (SRP + OCP)
# ---------------------------------------------------------------------------


class DateExtractor:
    """Extrai timestamp dos metadados com múltiplos formatos."""

    def extract(
        self, tags: Dict[str, Any], caminho: Path
    ) -> Optional[datetime]:
        """Retorna o datetime do primeiro tag de data válido encontrado."""
        for tag_name in DATE_TAGS:
            raw = tags.get(tag_name)
            if raw is None:
                continue
            valor = str(raw).strip()
            if valor in ZERO_DATES:
                continue
            parsed = self._parse_datetime(valor)
            if parsed is not None:
                return parsed
        return None

    def _parse_datetime(self, valor: str) -> Optional[datetime]:
        """Tenta parsear <valor> em múltiplos formatos de data."""
        # Remove subsegundos com timezone colado (ex: ".993-03:00")
        limpo = re.sub(r"\.\d+", "", valor)
        for fmt in DATE_FORMATS:
            try:
                dt = datetime.strptime(limpo, fmt)
                # Normaliza para naive (sem timezone) para consistência no nome
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
            except ValueError:
                continue
        return None


class ModelExtractor:
    """Extrai o modelo da câmera dos metadados."""

    def extract(self, tags: Dict[str, Any], caminho: Path) -> Optional[str]:
        """Retorna o modelo da câmera limpo, ou None."""
        raw = _first_tag_value(tags, MODEL_TAGS)
        if raw is None:
            return None
        modelo = str(raw).strip()
        return modelo if modelo else None


class GPSExtractor:
    """Extrai coordenadas GPS em graus decimais."""

    def extract(
        self, tags: Dict[str, Any], caminho: Path
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
        self, tags: Dict[str, Any], caminho: Path
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

    def extract(self, tags: Dict[str, Any], caminho: Path) -> Optional[int]:
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

    def extract(self, tags: Dict[str, Any], caminho: Path) -> int:
        """Retorna o tamanho em bytes do arquivo (sempre disponível)."""
        try:
            return caminho.stat().st_size
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

    def extract_all(self, tags: Dict[str, Any], caminho: Path) -> ImageInfo:
        """Extrai todos os campos independentemente e retorna ImageInfo."""
        timestamp = self._date.extract(tags, caminho)
        model = self._model.extract(tags, caminho)
        gps = self._gps.extract(tags, caminho)
        dims = self._dimension.extract(tags, caminho)
        ppi = self._ppi.extract(tags, caminho)
        size_bytes = self._size.extract(tags, caminho)

        return ImageInfo(
            caminho_origem=caminho,
            timestamp=timestamp,
            model=model,
            gps_lat=gps[0] if gps else None,
            gps_lon=gps[1] if gps else None,
            width=dims[0] if dims else None,
            height=dims[1] if dims else None,
            ppi=ppi,
            size_bytes=size_bytes,
            extension=caminho.suffix.lower(),
        )


# ---------------------------------------------------------------------------
# FilenameFactory — Construção do nome de arquivo (SRP)
# ---------------------------------------------------------------------------


class FilenameFactory:
    """Constrói o nome de arquivo final a partir de um ImageInfo."""

    def format(self, info: ImageInfo) -> str:
        """Gera o nome no padrão YYYYMMDD_HHMMSS[_campos...].ext."""
        partes: List[str] = []

        # Data/hora (obrigatório para nome válido)
        if info.timestamp:
            partes.append(info.timestamp.strftime("%Y%m%d"))
            partes.append(info.timestamp.strftime("%H%M%S"))

        # GPS em decimal
        if info.gps_lat is not None and info.gps_lon is not None:
            partes.append(f"{info.gps_lat:.6f},{info.gps_lon:.6f}")

        # Modelo da câmera
        if info.model:
            partes.append(slugify(info.model))

        # Dimensões em pixels
        if info.width is not None and info.height is not None:
            partes.append(f"{info.width}x{info.height}px")

        # PPI
        if info.ppi is not None:
            partes.append(f"{info.ppi}ppi")

        # Tamanho em KB com 2 casas decimais
        if info.size_bytes > 0:
            tamanho_kb = info.size_bytes / 1024
            partes.append(f"{tamanho_kb:.2f}KB")

        nome = "_".join(partes)
        if nome:
            return f"{nome}{info.extension}"
        return f"sem_nome{info.extension}"


# ---------------------------------------------------------------------------
# CollisionResolver — Resolução de colisão de nomes (SRP)
# ---------------------------------------------------------------------------


class SuffixCollisionResolver:
    """Resolve colisões adicionando sufixo incremental (_01, _02, ...)."""

    def __init__(self, max_tentativas: int = 999) -> None:
        self._max_tentativas = max_tentativas

    def resolve(self, destino: Path) -> Path:
        """Retorna um Path único, adicionando sufixo se necessário."""
        if not destino.exists():
            return destino

        stem = destino.stem
        suffix = destino.suffix
        diretorio = destino.parent

        for i in range(1, self._max_tentativas + 1):
            candidato = diretorio / f"{stem}_{i:02d}{suffix}"
            if not candidato.exists():
                return candidato

        raise RuntimeError(
            f"Não foi possível resolver colisão para {destino} "
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

    def process(self, caminho: Path) -> RenameResult:
        """Tenta renomear <caminho> com base nos metadados extraídos."""
        try:
            tags = self._reader.read(caminho)
        except FileNotFoundError:
            return RenameResult(src=caminho, reason="arquivo não encontrado")
        except RuntimeError as exc:
            return RenameResult(src=caminho, reason=f"erro ao ler: {exc}")

        info = self._extractor.extract_all(tags, caminho)

        # Sem timestamp → prefixar com _ para evitar reprocessamento
        if info.timestamp is None:
            return self._prefixar_sem_data(caminho, info)

        novo_nome = self._factory.format(info)
        destino = caminho.with_name(novo_nome)

        # Mesmo nome → nada a fazer
        if destino == caminho:
            return RenameResult(
                src=caminho,
                dst=caminho,
                success=True,
                reason="nome já correto",
            )

        destino = self._resolver.resolve(destino)

        if self._dry_run:
            return RenameResult(src=caminho, dst=destino, success=True)

        try:
            caminho.rename(destino)
        except OSError as exc:
            return RenameResult(src=caminho, reason=f"erro ao renomear: {exc}")

        return RenameResult(src=caminho, dst=destino, success=True)

    def _prefixar_sem_data(
        self, caminho: Path, info: ImageInfo
    ) -> RenameResult:
        """Prefixa sem data com '_' incluindo dimensões e tamanho."""
        nome_atual = caminho.name

        # Constrói sufixo informativo mesmo sem data
        partes: List[str] = []
        if info.width is not None and info.height is not None:
            partes.append(f"{info.width}x{info.height}px")
        if info.ppi is not None:
            partes.append(f"{info.ppi}ppi")
        if info.size_bytes > 0:
            partes.append(f"{info.size_bytes / 1024:.2f}KB")

        sufixo_info = "_".join(partes)
        if sufixo_info:
            novo_nome = f"_{sufixo_info}{info.extension}"
        else:
            novo_nome = f"_{nome_atual}"

        destino = caminho.with_name(novo_nome)
        if destino.exists():
            destino = self._resolver.resolve(destino)

        if self._dry_run:
            return RenameResult(
                src=caminho, dst=destino, success=True, reason="sem data"
            )

        try:
            caminho.rename(destino)
        except OSError as exc:
            return RenameResult(src=caminho, reason=f"erro ao prefixar: {exc}")

        return RenameResult(
            src=caminho, dst=destino, success=True, reason="sem data"
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

    def process(self, caminho: Path) -> List[RenameResult]:
        """Processa <caminho> (arquivo ou diretório) e retorna resultados."""
        resultados: List[RenameResult] = []

        if caminho.is_file():
            resultado = self._processar_arquivo(caminho)
            if resultado is not None:
                resultados.append(resultado)
        elif caminho.is_dir():
            resultados.extend(self._processar_diretorio(caminho))
        else:
            print(f"Caminho inválido: {caminho}")

        return resultados

    def _processar_arquivo(self, caminho: Path) -> Optional[RenameResult]:
        """Processa um arquivo se sua extensão for suportada."""
        if caminho.suffix.lower() not in self._extensions:
            return None
        return self._renamer.process(caminho)

    def _processar_diretorio(self, caminho: Path) -> List[RenameResult]:
        """Percorre recursivamente <caminho> e processa todos os arquivos."""
        resultados: List[RenameResult] = []
        print(f"\nProcessando diretório: {caminho}")

        entradas = sorted(caminho.iterdir())
        for entrada in entradas:
            if entrada.is_file():
                resultado = self._processar_arquivo(entrada)
                if resultado is not None:
                    resultados.append(resultado)
            elif (
                entrada.is_dir()
                and not entrada.name.startswith(".")
                and entrada.name not in IGNORED_DIRS
            ):
                resultados.extend(self._processar_diretorio(entrada))

        return resultados


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


def _imprimir_relatorio(resultados: List[RenameResult]) -> None:
    """Imprime resumo dos resultados de renomeação."""
    renomeados = 0
    sem_data = 0
    erros = 0

    for r in resultados:
        if r.success and r.dst and r.dst != r.src:
            if r.reason == "sem data":
                sem_data += 1
                print(f"  [SEM DATA] {r.src.name} → {r.dst.name}")
            else:
                renomeados += 1
                print(f"  [OK] {r.src.name} → {r.dst.name}")
        elif r.success and r.reason == "nome já correto":
            pass  # Silencioso para arquivos que já têm o nome correto
        elif not r.success:
            erros += 1
            print(f"  [ERRO] {r.src.name}: {r.reason}")

    print("\n--- Resumo ---")
    print(f"  Total processados: {len(resultados)}")
    print(f"  Renomeados:        {renomeados}")
    print(f"  Sem data (prefixo _): {sem_data}")
    print(f"  Erros:             {erros}")
    print(
        f"  Sem alteração:     "
        f"{len(resultados) - renomeados - sem_data - erros}"
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

    caminho = Path(args[0]) if args else Path(".")
    _, processor = build_components(dry_run=dry_run)

    if dry_run:
        print("*** MODO DRY-RUN: nenhum arquivo será renomeado ***\n")

    try:
        resultados = processor.process(caminho)
    except RuntimeError as exc:
        print(f"Erro crítico: {exc}")
        return 1

    _imprimir_relatorio(resultados)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
