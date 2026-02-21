# portfolio-photo-renamer

CLI em Python para renomear fotos e videos usando metadados EXIF via exiftool.

## Visao geral

O nome final segue o padrao:

`YYYYMMDD_HHMMSS[_lat,lon][_modelo][_WxHpx][_PPIppi][_tamanhoKB].ext`

Campos sao adicionados quando disponiveis. Arquivos sem data EXIF recebem prefixo
`_` e ainda incluem dimensoes e tamanho. O script processa pastas recursivamente
e resolve colisoes com sufixos `_01`, `_02`, etc.

Os identificadores no codigo estao em ingles, enquanto docstrings e mensagens
continuam em portugues.

## Requisitos

- Python 3.10+
- exiftool no PATH

Instalacao do exiftool:

```bash
brew install exiftool           # macOS
sudo apt install libimage-exiftool-perl  # Debian/Ubuntu
```

## Uso rapido

```bash
python rename.py                          # processa a pasta atual (recursivo)
python rename.py path/to/folder           # processa uma pasta especifica
python rename.py path/to/image.jpg        # processa um arquivo
python rename.py --dry-run                # simula sem renomear
python rename.py --dry-run path/to/folder # simula pasta especifica
```

## Exemplos de saida

```text
20250706_114227_-23.536767,-46.669361_iphone-13_4032x3024px_72ppi_1854.96KB.heic
_1080x1920px_7784.07KB.mp4
```

## Dependencias Python

O script usa apenas a biblioteca padrao, mas o ambiente de trabalho costuma ter:

```bash
pip install exifread pillow-heif Pillow
```

## Lint e typecheck

```bash
black rename.py
isort rename.py
flake8 rename.py
mypy rename.py
```

## Notas importantes

- `FileModifyDate` e `FileCreateDate` sao ignorados de proposito.
- `--dry-run` mostra o que seria renomeado sem alterar arquivos.
- Para rodar com seguranca em acervos grandes, comece com `--dry-run`.
