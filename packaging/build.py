"""Construit l'exécutable autonome Windows : dist/GP2CHC/ (GP2CHC.exe + gp2chc-cli.exe) et dist/GP2CHC-windows.zip.

    python packaging/build.py

Il faut Python avec les dépendances du projet (numpy, demucs, pyinstaller) et un ffmpeg statique : celui du PATH,
ou celui indiqué par la variable FFMPEG (chemin de ffmpeg.exe). Le modèle Demucs est téléchargé s'il manque.
Aucune tablature ni aucun audio du dépôt n'est inclus : seulement le programme et ses outils.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "exe"
DIST = ROOT / "dist"


def find_ffmpeg() -> Path:
    """Le vrai ffmpeg.exe (le raccourci de Chocolatey dans le PATH ne fonctionne pas seul)."""
    candidates = [os.environ.get("FFMPEG", "")]
    candidates += [str(p) for p in Path("C:/ProgramData/chocolatey/lib").glob("ffmpeg*/tools/ffmpeg*/bin/ffmpeg.exe")]
    candidates.append(shutil.which("ffmpeg") or "")
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and Path(candidate).stat().st_size > 10_000_000:
            return Path(candidate)
    sys.exit("ffmpeg statique introuvable : indiquez son chemin dans la variable FFMPEG")


def prepare_ffmpeg() -> Path:
    folder = BUILD / "ffmpeg"
    folder.mkdir(parents=True, exist_ok=True)
    source = find_ffmpeg()
    shutil.copy2(source, folder / "ffmpeg.exe")
    for license_file in (source.parent.parent / "LICENSE", source.parent / "LICENSE"):
        if license_file.is_file():
            shutil.copy2(license_file, folder / "LICENSE")
            break
    else:
        (folder / "LICENSE").write_text("FFmpeg: GPL v3, https://ffmpeg.org/legal.html\n", encoding="utf-8")
    print(f"ffmpeg : {source}")
    return folder


def prepare_model() -> Path:
    import yaml
    from huggingface_hub import hf_hub_download

    folder = BUILD / "models" / "htdemucs"
    folder.mkdir(parents=True, exist_ok=True)
    config = Path(hf_hub_download("adefossez/HTDemucs", "htdemucs.yaml"))
    shutil.copy2(config, folder / "htdemucs.yaml")
    for signature in yaml.safe_load(config.read_text(encoding="utf-8"))["models"]:
        shutil.copy2(hf_hub_download("adefossez/HTDemucs", f"{signature}.safetensors"), folder / f"{signature}.safetensors")
    print(f"modèle Demucs : {folder}")
    return folder


def main() -> None:
    env = dict(os.environ, GP2CHC_FFMPEG_DIR=str(prepare_ffmpeg()), GP2CHC_MODEL_DIR=str(prepare_model()))
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), str(ROOT / "packaging" / "GP2CHC.spec")],
        check=True, env=env,
    )  # fmt: skip
    app = DIST / "GP2CHC"
    shutil.copy2(ROOT / "packaging" / "LISEZMOI.txt", app / "LISEZMOI.txt")

    archive = DIST / "GP2CHC-windows.zip"
    print(f"Archive : {archive}")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(app.rglob("*")):
            z.write(path, Path("GP2CHC") / path.relative_to(app))
    size = sum(p.stat().st_size for p in app.rglob("*") if p.is_file())
    print(f"Dossier : {app} ({size / 1e6:.0f} Mo) | archive : {archive.stat().st_size / 1e6:.0f} Mo")


if __name__ == "__main__":
    main()
