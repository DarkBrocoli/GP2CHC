"""Lecture / écriture du fichier song.ini de Clone Hero."""

from __future__ import annotations

from pathlib import Path


def read_ini(path: str | Path) -> dict[str, str]:
    """Clés de la section [Song], dans l'ordre du fichier."""
    values: dict[str, str] = {}
    in_song = False
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("["):
            in_song = line.lower() == "[song]"
        elif in_song and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def write_ini(path: str | Path, values: dict[str, str]) -> None:
    lines = ["[Song]"] + [f"{key} = {value}" for key, value in values.items()]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
