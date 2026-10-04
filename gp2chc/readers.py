"""Choix du lecteur selon l'extension du fichier Guitar Pro."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import gpif, gpx
from .i18n import tr
from .model import Score


def _gpif_bytes(path: Path) -> bytes:
    ext = path.suffix.lower()
    if ext == ".gp":  # Guitar Pro 7/8 : archive zip
        with zipfile.ZipFile(path) as z:
            return z.read("Content/score.gpif")
    if ext == ".gpx":  # Guitar Pro 6
        return gpx.extract_gpif(path.read_bytes())
    raise ValueError(tr("Format '{ext}' non supporté (formats gérés : .gp, .gpx)", ext=ext))


def read_score(path: str | Path, track: str | None = None) -> Score:
    return gpif.read_gpif(_gpif_bytes(Path(path)), track)


def list_tracks(path: str | Path) -> list[tuple[int, str, bool]]:
    return gpif.list_tracks(ET.fromstring(_gpif_bytes(Path(path))))
