"""Silence ajouté au début du morceau : les fichiers audio du dossier du morceau sont décalés.

Les originaux sont gardés dans un sous-dossier : une nouvelle conversion repart d'eux (le silence ne s'ajoute
jamais deux fois) et le calage se fait toujours sur l'audio d'origine.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import deps
from .i18n import tr

BACKUP_DIR = "gp2chc_original_audio"
STATE_FILE = "gp2chc_padding.json"
# Noms de pistes audio lus par Clone Hero
STEMS = {
    "song", "guitar", "bass", "rhythm", "keys", "vocals", "vocals_1", "vocals_2", "crowd",
    "drums", "drums_1", "drums_2", "drums_3", "drums_4",
}  # fmt: skip
CODECS = {
    ".opus": ["-c:a", "libopus", "-b:a", "192k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "7"],
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac"],
}


def original(path: str | Path) -> Path:
    """Version non décalée d'un fichier audio (sauvegardée par une conversion précédente, sinon le fichier lui-même)."""
    path = Path(path)
    backup = path.parent / BACKUP_DIR / path.name
    return backup if backup.exists() else path


def _is_song_audio(path: Path) -> bool:
    return path.is_file() and path.stem.lower() in STEMS and path.suffix.lower() in CODECS


def song_files(folder: Path) -> list[Path]:
    """Fichiers audio du morceau présents dans le dossier (y compris ceux dont seul l'original est sauvegardé)."""
    names = {p.name for p in folder.glob("*") if _is_song_audio(p)}
    backup = folder / BACKUP_DIR
    if backup.is_dir():
        names |= {p.name for p in backup.glob("*") if _is_song_audio(p)}
    return [folder / name for name in sorted(names)]


def _target_name(source: Path, role: str, taken: set[str], index: int, count: int) -> str:
    """Nom Clone Hero pour un fichier choisi hors du dossier du morceau (role : "drums" ou "song")."""
    suffix = source.suffix.lower() if source.suffix.lower() in CODECS else ".ogg"
    if source.stem.lower() in STEMS:
        return source.stem.lower() + suffix
    stem = role if count == 1 else f"{role}_{index + 1}"
    name = stem + suffix
    return name if name not in taken else source.stem + suffix


def duration_ms(path: Path) -> float | None:
    """Durée d'un fichier audio, lue dans l'en-tête affiché par ffmpeg (« Duration: 00:04:14.71 »)."""
    executable = deps.ffmpeg()
    if not executable:
        return None
    run = subprocess.run([executable, "-hide_banner", "-i", str(path)], capture_output=True, creationflags=deps.NO_WINDOW)
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", run.stderr.decode(errors="replace"))
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return ((int(hours) * 60 + int(minutes)) * 60 + float(seconds)) * 1000


def _encode(source: Path, target: Path, lead_in_ms: float) -> None:
    tmp = target.with_name(target.stem + ".gp2chc_tmp" + target.suffix)
    if lead_in_ms <= 0 and source.suffix.lower() == target.suffix.lower():
        shutil.copy2(source, tmp)
    else:
        delay = int(round(lead_in_ms))
        cmd = [deps.ffmpeg() or "ffmpeg", "-v", "error", "-y", "-i", str(source), "-af", f"adelay={delay}:all=1", "-map_metadata", "0"]
        cmd += CODECS[target.suffix.lower()] + [str(tmp)]
        run = subprocess.run(cmd, capture_output=True, creationflags=deps.NO_WINDOW)
        if run.returncode != 0:
            tmp.unlink(missing_ok=True)
            error = run.stderr.decode(errors="replace").strip()
            raise ValueError(tr("Impossible d'ajouter le silence à {name} : {error}", name=source.name, error=error))
    tmp.replace(target)


def add_lead_in(folder: Path, drums: list[str], mixes: list[str], lead_in_ms: float, progress=None) -> list[Path]:
    """Écrit dans `folder` chaque piste audio du morceau précédée de `lead_in_ms` de silence.

    Pistes concernées : celles déjà dans le dossier et celles choisies ailleurs (copiées sous un nom Clone Hero).
    Renvoie les fichiers écrits ou déjà à jour.
    """
    if not deps.ffmpeg():
        raise ValueError(tr("ffmpeg est introuvable : nécessaire pour ajouter le silence au début de l'audio"))
    folder = Path(folder)
    sources: dict[str, Path] = {p.name: original(p) for p in song_files(folder)}
    for role, chosen in (("drums", drums), ("song", mixes)):
        outside = [Path(p) for p in chosen if Path(p).resolve().parent != folder.resolve()]
        for index, path in enumerate(outside):
            name = _target_name(path, role, set(sources), index, len(outside))
            sources.setdefault(name, original(path))
    if not sources:
        return []

    backup = folder / BACKUP_DIR
    state_path = backup / STATE_FILE
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}

    jobs = []
    for name, source in sources.items():
        target = folder / name
        if source == target:  # original encore en place : on le met de côté avant d'écrire la version décalée
            backup.mkdir(exist_ok=True)
            shutil.move(str(target), str(backup / name))
            source = backup / name
        stat = source.stat()
        signature = {"source": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "lead_in_ms": lead_in_ms}
        if target.exists() and state.get(name, {}).get("signature") == signature and state[name].get("target_size") == target.stat().st_size:
            continue  # déjà décalé avec ce silence
        jobs.append((name, source, target, signature))

    if jobs and progress:
        progress(tr("Ajout de {seconds:g} s de silence au début de {count} fichier(s) audio...", seconds=lead_in_ms / 1000, count=len(jobs)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda job: _encode(job[1], job[2], lead_in_ms), jobs))
    for name, _, target, signature in jobs:
        state[name] = {"signature": signature, "target_size": target.stat().st_size}
    if jobs:
        backup.mkdir(exist_ok=True)
        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return [folder / name for name in sources]
