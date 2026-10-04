"""Modèle intermédiaire indépendant du format Guitar Pro."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

PPQ = 480  # ticks par noire dans le MIDI généré (valeur standard de Clone Hero)

GHOST = -1
NORMAL = 0
ACCENT = 1


@dataclass
class DrumNote:
    pos: Fraction  # position dans la mesure, en noires
    pitch: int  # numéro de note MIDI General MIDI (35 = grosse caisse, 38 = caisse claire...)
    dynamic: int = NORMAL


@dataclass
class Bar:
    numerator: int = 4
    denominator: int = 4
    notes: list[DrumNote] = field(default_factory=list)
    tempos: list[tuple[Fraction, float]] = field(default_factory=list)  # (position en noires, BPM noire)
    section: str | None = None
    repeat_start: bool = False
    repeat_end: int = 0  # nombre total de passages si la mesure ferme une reprise, sinon 0
    alternatives: frozenset[int] = frozenset()  # passages durant lesquels la mesure est jouée (1re/2e fin...)

    @property
    def length(self) -> Fraction:
        return Fraction(self.numerator * 4, self.denominator)


@dataclass
class Score:
    title: str = ""
    artist: str = ""
    album: str = ""
    bars: list[Bar] = field(default_factory=list)
    track_name: str = ""
    grace_notes_skipped: int = 0


def expand_repeats(bars: list[Bar]) -> list[Bar]:
    """Déroule les reprises et les fins alternatives : renvoie les mesures dans l'ordre joué."""
    played: list[Bar] = []
    start = 0
    passage = 1
    i = 0
    jumped = False
    limit = len(bars) * 64  # garde-fou contre les reprises mal formées
    while i < len(bars) and len(played) < limit:
        bar = bars[i]
        if bar.repeat_start and not jumped:
            start = i
            passage = 1
        jumped = False
        if bar.alternatives and passage not in bar.alternatives:
            i += 1
            continue
        played.append(bar)
        if bar.repeat_end:
            if passage < bar.repeat_end:
                passage += 1
                i = start
                jumped = True
                continue
            passage = 1
        i += 1
    return played
