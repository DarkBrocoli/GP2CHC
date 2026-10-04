# Recette PyInstaller de l'exécutable autonome. À lancer via packaging/build.py, qui prépare
# ffmpeg et le modèle Demucs et fournit leurs chemins par variables d'environnement.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
FFMPEG_DIR = Path(os.environ["GP2CHC_FFMPEG_DIR"])  # ffmpeg.exe + LICENSE
MODEL_DIR = Path(os.environ["GP2CHC_MODEL_DIR"])  # htdemucs.yaml + *.safetensors

datas = [(str(path), "ffmpeg") for path in FFMPEG_DIR.iterdir()]
datas += [(str(path), "models/htdemucs") for path in MODEL_DIR.iterdir()]

a = Analysis(
    [str(ROOT / "packaging" / "gp2chc_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    # Demucs choisit ses classes de modèle d'après le fichier du modèle : on inclut tous ses modules
    hiddenimports=collect_submodules("demucs") + collect_submodules("gp2chc") + ["yaml", "safetensors", "julius", "einops"],
    excludes=["scipy", "matplotlib", "PIL", "pytest", "IPython", "pandas", "mido", "PyInstaller", "setuptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GP2CHC", console=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="gp2chc-cli", console=True, upx=False)

coll = COLLECT(gui, cli, a.binaries, a.datas, name="GP2CHC", upx=False)
