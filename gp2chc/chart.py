"""Modèle intermédiaire -> chart de batterie Clone Hero (pro drums, Expert) -> pistes MIDI."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass, field

from . import midi
from .i18n import tr
from .model import ACCENT, GHOST, PPQ, Bar, Score, expand_repeats

# Lanes Clone Hero : 0 = grosse caisse, 1 = rouge, 2 = jaune, 3 = bleu, 4 = vert
# "-tom" / "-cymbal" : en pro drums, les notes jaune/bleu/vert sont des cymbales sauf si un marqueur tom les couvre.
LANES: dict[str, tuple[int, bool] | None] = {
    "kick": (0, False),
    "red": (1, False),
    "yellow-tom": (2, True), "yellow-cymbal": (2, False),
    "blue-tom": (3, True), "blue-cymbal": (3, False),
    "green-tom": (4, True), "green-cymbal": (4, False),
    "none": None,
}  # fmt: skip

# Numéros de notes General MIDI (ceux de Guitar Pro) -> lane
DEFAULT_MAP: dict[int, str] = {
    35: "kick", 36: "kick",
    37: "red", 38: "red", 40: "red",
    42: "yellow-cymbal", 46: "yellow-cymbal", 44: "yellow-cymbal",  # 44 = charley au pied
    50: "yellow-tom", 48: "yellow-tom",
    47: "blue-tom", 45: "blue-tom",
    43: "green-tom", 41: "green-tom",
    51: "blue-cymbal", 59: "blue-cymbal", 53: "blue-cymbal",
    49: "green-cymbal", 57: "green-cymbal", 55: "green-cymbal", 52: "green-cymbal",
}  # fmt: skip

EXPERT_BASE = 96  # kick = 96, rouge = 97 ... vert = 100
TOM_MARKER_BASE = 110  # 110 = tom jaune, 111 = tom bleu, 112 = tom vert
NOTE_LENGTH = PPQ // 8
HAND_PRIORITY = [1, 4, 3, 2]  # lanes gardées en premier quand il y a trop de notes simultanées
PEDAL_HAT = {44}  # charley au pied

VELOCITY = {GHOST: 1, ACCENT: 127}
VELOCITY_DEFAULT = 96


@dataclass
class ChartNote:
    tick: int
    lane: int
    tom: bool
    dynamic: int


@dataclass
class BarTiming:
    start_tick: int
    length_ticks: int
    positions: list[float]  # position de chaque note jouée dans la mesure (0 = début, 1 = fin)


@dataclass
class Chart:
    notes: list[ChartNote] = field(default_factory=list)
    bars: list[BarTiming] = field(default_factory=list)
    tempos: list[tuple[int, float]] = field(default_factory=list)
    signatures: list[tuple[int, int, int]] = field(default_factory=list)
    sections: list[tuple[int, str]] = field(default_factory=list)
    end_tick: int = 0
    unmapped: Counter = field(default_factory=Counter)  # pitch GM ignoré -> occurrences
    muted: Counter = field(default_factory=Counter)  # pitch GM volontairement non chartés (mapping "none")
    dropped_hands: int = 0  # notes supprimées pour respecter max_hands
    pedal_dropped: int = 0  # charleys au pied retirés (joués avec un tom, ou avec la caisse claire dans un fill)

    def ticks_to_ms(self, tick: int) -> float:
        ms, last_tick, bpm = 0.0, 0, self.tempos[0][1]
        for t, new_bpm in self.tempos:
            if t >= tick:
                break
            ms += (t - last_tick) * 60000 / (bpm * PPQ)
            last_tick, bpm = t, new_bpm
        return ms + (tick - last_tick) * 60000 / (bpm * PPQ)


def load_mapping(path: str | None = None, overrides: dict[int, str] | None = None) -> dict[int, str]:
    """Table par défaut, surchargée par un fichier JSON {"56": "yellow-cymbal", "44": "none"} puis par `overrides`."""
    mapping = dict(DEFAULT_MAP)
    for pitch, lane in (overrides or {}).items():
        if lane not in LANES:
            raise ValueError(tr("Lane inconnue '{lane}' (choix : {choices})", lane=lane, choices=", ".join(LANES)))
        mapping[int(pitch)] = lane
    if path:
        with open(path, encoding="utf-8") as f:
            for pitch, lane in json.load(f).items():
                if lane not in LANES:
                    raise ValueError(tr("Lane inconnue '{lane}' (choix : {choices})", lane=lane, choices=", ".join(LANES)))
                mapping[int(pitch)] = lane
    return mapping


def _laid_out(played: list[Bar], layout: list[int | None]) -> tuple[list[Bar], list[str | None]]:
    """Mesures du chart selon le layout, et la section affichée au début de chacune.

    Une mesure insérée (None) est vide, à la signature de la mesure suivante. La section d'une mesure retirée
    passe à la mesure gardée suivante, pour ne pas perdre de repère."""
    kept = [i for i in layout if i is not None]
    bars: list[Bar] = []
    sections: list[str | None] = []
    pending: str | None = None
    previous = -1
    for k, index in enumerate(layout):
        if index is None:
            model = next((played[i] for i in layout[k + 1 :] if i is not None), played[kept[-1]] if kept else Bar())
            bars.append(Bar(numerator=model.numerator, denominator=model.denominator))
            sections.append(None)
            continue
        for skipped in played[previous + 1 : index]:
            pending = skipped.section or pending
        bars.append(played[index])
        sections.append(played[index].section or pending)
        pending = None
        previous = index
    return bars, sections


def build_chart(
    score: Score,
    mapping: dict[int, str],
    max_hands: int = 2,
    offset_ms: float = 0,
    bar_tempos: list[list[float]] | None = None,
    layout: list[int | None] | None = None,
) -> Chart:
    """bar_tempos : pour chaque mesure du chart, le tempo de chacun de ses temps ; remplace ceux de la tablature.
    layout : mesures du chart, en indices de mesures jouées (reprises déroulées) ; None = mesure vide insérée
    (l'enregistrement en a plus que la tablature). Les mesures absentes du layout sont retirées du chart."""
    chart = Chart()
    played = expand_repeats(score.bars)
    if layout is None:
        layout = list(range(len(played)))
    bars, sections = _laid_out(played, layout)
    if bar_tempos is not None and len(bar_tempos) != len(bars):
        raise ValueError("bar_tempos doit contenir une liste de tempos par mesure du chart")
    current = next((bpm for bar in played for _, bpm in bar.tempos), 120.0)
    for bar in played[: next((i for i in layout if i is not None), 0)]:
        for _, bpm in bar.tempos:  # tempo de la tablature en vigueur au début du chart
            current = bpm
    first_bpm = bar_tempos[0][0] if bar_tempos else current

    # Silence avant la première mesure : un nombre entier de temps, à un tempo ajusté pour durer exactement
    # offset_ms. La grille de temps reste ainsi régulière et la première mesure tombe sur une barre de mesure.
    first_signature = (bars[0].numerator, bars[0].denominator)
    chart.signatures.append((0, *first_signature))
    tick = 0
    if offset_ms > 0:
        beats = max(1, round(offset_ms / 60000 * first_bpm))
        chart.tempos.append((0, beats * 60000 / offset_ms))
        tick = beats * PPQ
        chart.signatures.append((tick, *first_signature))  # la mesure 1 recommence ici
    chart.tempos.append((tick, first_bpm))
    fills = fill_beats(bars)

    for number, bar in enumerate(bars):
        signature = (bar.numerator, bar.denominator)
        # une signature de temps au début de chaque mesure : la grille est recalée à chaque mesure
        if chart.signatures[-1][0] != tick:
            chart.signatures.append((tick, *signature))
        if bar_tempos:
            parts = bar_tempos[number]
            changes = [(bar.length * k / len(parts), bpm) for k, bpm in enumerate(parts)]
        else:
            changes = bar.tempos
        for pos, bpm in changes:
            if bpm != chart.tempos[-1][1]:
                chart.tempos.append((tick + round(pos * PPQ), bpm))
        if sections[number]:
            chart.sections.append((tick, sections[number]))

        by_tick: dict[int, list] = {}
        for note in bar.notes:
            by_tick.setdefault(tick + round(note.pos * PPQ), []).append(note)
        length = round(bar.length * PPQ)
        before = len(chart.notes)
        for t, notes in by_tick.items():
            _add_chord(chart, t, notes, mapping, max_hands, (number, int(notes[0].pos)) in fills)
        positions = sorted({(n.tick - tick) / length for n in chart.notes[before:]})
        chart.bars.append(BarTiming(tick, length, positions))

        tick += length

    chart.end_tick = tick
    chart.notes.sort(key=lambda n: (n.tick, n.lane))
    return chart


def fill_beats(bars: list[Bar]) -> set[tuple[int, int]]:
    """Temps (mesure, temps) joués en fill : plus d'attaques que la médiane des temps joués du morceau."""
    density: dict[tuple[int, int], int] = {}
    for number, bar in enumerate(bars):
        onsets: dict[int, set] = {}
        for note in bar.notes:
            if note.pitch not in PEDAL_HAT:
                onsets.setdefault(int(note.pos), set()).add(note.pos)
        for beat, positions in onsets.items():
            density[(number, beat)] = len(positions)
    if not density:
        return set()
    median = statistics.median(density.values())
    return {key for key, count in density.items() if count > median}


def _simplify_pedal_hat(chart: Chart, notes: list, mapping: dict[int, str], in_fill: bool) -> list:
    """Charley au pied avec un tom (ou avec la caisse claire pendant un fill) : on ne garde que le tom / la caisse
    claire, sinon les fills deviennent injouables."""
    pedal = [n for n in notes if n.pitch in PEDAL_HAT]
    if not pedal:
        return notes
    others = [n for n in notes if n.pitch not in PEDAL_HAT]
    names = {mapping.get(n.pitch) for n in others}
    with_tom = any(LANES.get(name) and LANES[name][1] for name in names)
    if with_tom or (in_fill and "red" in names):
        chart.pedal_dropped += len(pedal)
        return others
    return notes


def _add_chord(chart: Chart, tick: int, notes: list, mapping: dict[int, str], max_hands: int, in_fill: bool = False) -> None:
    notes = _simplify_pedal_hat(chart, notes, mapping, in_fill)
    slots: dict[int, ChartNote] = {}
    for note in notes:
        name = mapping.get(note.pitch)
        if name is None:
            chart.unmapped[note.pitch] += 1
            continue
        spec = LANES[name]
        if spec is None:
            chart.muted[note.pitch] += 1
            continue
        lane, tom = spec
        current = slots.get(lane)
        if current is None:
            slots[lane] = ChartNote(tick, lane, tom, note.dynamic)
        elif current.tom and not tom:  # une cymbale prend le pas sur un tom sur la même lane
            slots[lane] = ChartNote(tick, lane, tom, note.dynamic)
        elif current.tom == tom:
            current.dynamic = max(current.dynamic, note.dynamic)

    # Dans le jeu, tous les pads se frappent à la main (charley au pied compris, sur le pad jaune) :
    # au plus max_hands pads à la fois, seule la grosse caisse s'y ajoute.
    hands = [lane for lane in HAND_PRIORITY if lane in slots]
    if max_hands:
        for lane in hands[max_hands:]:
            del slots[lane]
            chart.dropped_hands += 1
    chart.notes.extend(slots.values())


def _gap_limited(ticks: list[int]) -> list[tuple[int, int]]:
    """(tick, durée) : durée standard, raccourcie pour ne pas chevaucher la note suivante de même hauteur."""
    ticks = sorted(set(ticks))
    return [(t, min(NOTE_LENGTH, (ticks[i + 1] - t) if i + 1 < len(ticks) else NOTE_LENGTH)) for i, t in enumerate(ticks)]


def to_midi_tracks(chart: Chart, title: str, dynamics: bool = True) -> list[list[midi.Event]]:
    conductor = [midi.track_name(title)]
    conductor += [midi.tempo(t, bpm) for t, bpm in chart.tempos]
    conductor += [midi.time_signature(t, n, d) for t, n, d in chart.signatures]

    events = [midi.track_name("EVENTS")]
    events += [midi.text_event(t, f"[section {name}]") for t, name in chart.sections]
    events.append(midi.text_event(chart.end_tick, "[end]"))

    use_dynamics = dynamics and any(n.dynamic for n in chart.notes)
    drums = [midi.track_name("PART DRUMS")]
    if use_dynamics:
        drums.append(midi.text_event(0, "[ENABLE_CHART_DYNAMICS]"))

    by_pitch: dict[int, list[tuple[int, int]]] = {}
    markers: dict[int, list[int]] = {}
    for n in chart.notes:
        pitch = EXPERT_BASE + n.lane
        velocity = VELOCITY.get(n.dynamic, VELOCITY_DEFAULT) if use_dynamics else 100
        by_pitch.setdefault(pitch, []).append((n.tick, velocity))
        if n.tom:
            markers.setdefault(TOM_MARKER_BASE + n.lane - 2, []).append(n.tick)

    for pitch, hits in by_pitch.items():
        velocity_at = dict(hits)
        for tick, length in _gap_limited([t for t, _ in hits]):
            drums.append(midi.note_on(tick, pitch, velocity_at[tick]))
            drums.append(midi.note_off(tick + length, pitch))
    for pitch, ticks in markers.items():
        for tick, length in _gap_limited(ticks):
            drums.append(midi.note_on(tick, pitch))
            drums.append(midi.note_off(tick + length, pitch))

    return [conductor, drums, events]
