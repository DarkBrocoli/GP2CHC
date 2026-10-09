"""Modules facultatifs (numpy, ffmpeg, Demucs) : détection, installation depuis l'interface, et version
incluse dans l'exécutable autonome (PyInstaller)."""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .i18n import tr

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/"

# nom affiché -> (module Python testé, paquet pip)
PIP_MODULES = {
    "numpy": ("numpy", "numpy"),
    "demucs": ("demucs", "demucs"),
    "roformer": ("rotary_embedding_torch", "beartype rotary-embedding-torch"),  # séparation haute qualité
}


# Pas de fenêtre de console pour ffmpeg & co. quand le programme tourne sans console (exécutable, .pyw)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def frozen() -> bool:
    """Vrai dans l'exécutable autonome : tout y est inclus, rien ne s'installe avec pip."""
    return bool(getattr(sys, "frozen", False))


def bundled(relative: str) -> Path | None:
    """Fichier ou dossier inclus dans l'exécutable autonome (None hors exécutable ou s'il manque)."""
    if not frozen():
        return None
    path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / relative
    return path if path.exists() else None


def ffmpeg() -> str | None:
    """Chemin de ffmpeg : celui de l'exécutable autonome, sinon celui du PATH."""
    included = bundled("ffmpeg/ffmpeg.exe")
    return str(included) if included else shutil.which("ffmpeg")


def has_module(name: str) -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec(name) is not None


def has_ffmpeg() -> bool:
    return ffmpeg() is not None


def status() -> dict[str, bool]:
    return {"numpy": has_module("numpy"), "ffmpeg": has_ffmpeg(), "demucs": has_module("demucs")}


def _python() -> str:
    # pythonw.exe (double-clic sur le .pyw) n'a pas de console : pip se lance avec le python.exe voisin
    exe = sys.executable
    return exe[:-len("pythonw.exe")] + "python.exe" if exe.lower().endswith("pythonw.exe") else exe


def pip_install(package: str, log: Callable[[str], None]) -> None:
    """Installe un paquet avec pip en renvoyant les lignes utiles à `log`. Lève ValueError en cas d'échec."""
    log(tr("Installation de {package} (peut durer plusieurs minutes)...", package=package))
    proc = subprocess.Popen(
        [_python(), "-m", "pip", "install", package],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=NO_WINDOW,
    )  # fmt: skip
    last = ""
    for line in proc.stdout:
        line = line.strip()
        if line.startswith(("Downloading", "Installing collected", "Successfully", "ERROR")) and line != last:
            log(line)
            last = line
    if proc.wait() != 0:
        raise ValueError(tr("L'installation de {package} a échoué (voir les messages ci-dessus)", package=package))
    importlib.invalidate_caches()
