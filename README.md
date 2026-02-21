# portfolio-photo-renamer

CLI em Python para renomear fotos e videos usando metadados EXIF via exiftool.

## Uso rapido

```bash
python rename.py                          # processa a pasta atual (recursivo)
python rename.py path/to/folder           # processa uma pasta especifica
python rename.py path/to/image.jpg        # processa um arquivo
python rename.py --dry-run                # simula sem renomear
```

## Dependencias

- Python 3.10+
- exiftool no PATH (macOS: brew install exiftool)

## Lint e typecheck

```bash
black rename.py
isort rename.py
flake8 rename.py
mypy rename.py
```
