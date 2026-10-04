"""Lecture du format GPIF (Guitar Pro 6, 7 et 8) -> modèle intermédiaire."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction

from .i18n import tr
from .model import ACCENT, GHOST, NORMAL, Bar, DrumNote, Score

NOTE_VALUES = {
    "Long": Fraction(16), "DoubleWhole": Fraction(8), "Whole": Fraction(4), "Half": Fraction(2),
    "Quarter": Fraction(1), "Eighth": Fraction(1, 2), "16th": Fraction(1, 4), "32nd": Fraction(1, 8),
    "64th": Fraction(1, 16), "128th": Fraction(1, 32), "256th": Fraction(1, 64),
}  # fmt: skip

# Second nombre de l'automation de tempo : unité de pulsation, exprimée en noires
TEMPO_UNITS = {1: Fraction(1, 2), 2: Fraction(1), 3: Fraction(3, 2), 4: Fraction(2), 5: Fraction(3)}


def _text(element: ET.Element | None, path: str, default: str = "") -> str:
    found = element.find(path) if element is not None else None
    return (found.text or "").strip() if found is not None else default


def _indexed(parent: ET.Element) -> dict[str, ET.Element]:
    return {e.get("id"): e for e in parent}


def _duration(rhythm: ET.Element) -> Fraction:
    value = NOTE_VALUES[_text(rhythm, "NoteValue")]
    dots = rhythm.find("AugmentationDot")
    if dots is not None:
        value *= 2 - Fraction(1, 2 ** int(dots.get("count", "1")))
    tuplet = rhythm.find("PrimaryTuplet")
    if tuplet is not None:
        value *= Fraction(int(tuplet.get("den")), int(tuplet.get("num")))
    return value


def _is_drum_track(track: ET.Element) -> bool:
    if _text(track, "InstrumentSet/Type") == "drumKit":
        return True
    instrument = track.find("Instrument")
    return instrument is not None and instrument.get("ref", "").startswith("drm")


def list_tracks(root: ET.Element) -> list[tuple[int, str, bool]]:
    """(index, nom, est une batterie) pour chaque piste."""
    return [(i, _text(t, "Name"), _is_drum_track(t)) for i, t in enumerate(root.find("Tracks"))]


def _pick_track(root: ET.Element, wanted: str | None) -> int:
    tracks = list_tracks(root)
    if wanted is not None:
        if wanted.isdigit() and int(wanted) < len(tracks):
            return int(wanted)
        matches = [i for i, name, _ in tracks if wanted.lower() in name.lower()]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(tr("Piste '{name}' introuvable ou ambiguë", name=wanted))
    drums = [i for i, _, is_drum in tracks if is_drum]
    if not drums:
        raise ValueError(tr("Aucune piste de batterie trouvée (utilisez --track pour en désigner une)"))
    return drums[0]


def _articulation_table(track: ET.Element) -> list[int]:
    """Numéro MIDI de sortie de chaque articulation, dans l'ordre d'indexation de GP."""
    table = []
    for element in track.findall("InstrumentSet/Elements/Element"):
        for art in element.findall("Articulations/Articulation"):
            table.append(int(_text(art, "OutputMidiNumber", "-1")))
    return table


def _tempos(root: ET.Element) -> dict[int, list[tuple[float, float]]]:
    """Changements de tempo par numéro de mesure : (position en fraction de mesure, BPM noire)."""
    result: dict[int, list[tuple[float, float]]] = {}
    for auto in root.findall("MasterTrack/Automations/Automation"):
        if _text(auto, "Type") != "Tempo":
            continue
        parts = _text(auto, "Value").split()
        unit = TEMPO_UNITS.get(int(parts[1]), Fraction(1)) if len(parts) > 1 else Fraction(1)
        bpm = float(parts[0]) * float(unit)
        result.setdefault(int(_text(auto, "Bar", "0")), []).append((float(_text(auto, "Position", "0")), bpm))
    return result


def _note_pitch(note: ET.Element, articulations: list[int]) -> int | None:
    art = note.find("InstrumentArticulation")
    if art is not None and art.text and 0 <= int(art.text) < len(articulations):
        if articulations[int(art.text)] >= 0:
            return articulations[int(art.text)]
    for prop in note.findall("Properties/Property"):
        if prop.get("name") == "Midi":
            return int(_text(prop, "Number"))
    return None


def _dynamic(note: ET.Element) -> int:
    if note.find("AntiAccent") is not None:
        return GHOST
    if int(_text(note, "Accent", "0")) >= 4:  # 1 = staccato, 4 = accent, 8 = accent fort
        return ACCENT
    return NORMAL


def read_gpif(xml: bytes, track: str | None = None) -> Score:
    root = ET.fromstring(xml)
    track_index = _pick_track(root, track)
    all_tracks = list(root.find("Tracks"))
    track_el = all_tracks[track_index]
    articulations = _articulation_table(track_el)

    # Rang de la portée de la piste dans la liste <Bars> de chaque mesure
    staff_index = sum(len(t.findall("Staves/Staff")) or 1 for t in all_tracks[:track_index])

    bars = _indexed(root.find("Bars"))
    voices = _indexed(root.find("Voices"))
    beats = _indexed(root.find("Beats"))
    notes = _indexed(root.find("Notes"))
    rhythms = _indexed(root.find("Rhythms"))
    tempos = _tempos(root)

    score = Score(
        title=_text(root, "Score/Title"),
        artist=_text(root, "Score/Artist"),
        album=_text(root, "Score/Album"),
        track_name=_text(track_el, "Name"),
    )

    for number, master in enumerate(root.find("MasterBars")):
        num, den = (int(x) for x in _text(master, "Time", "4/4").split("/"))
        bar = Bar(numerator=num, denominator=den)

        section = master.find("Section")
        if section is not None:
            bar.section = _text(section, "Text") or _text(section, "Letter") or None

        repeat = master.find("Repeat")
        if repeat is not None:
            bar.repeat_start = repeat.get("start") == "true"
            if repeat.get("end") == "true":
                bar.repeat_end = int(repeat.get("count", "0")) or 2
        alt = _text(master, "AlternateEndings")
        if alt:
            bar.alternatives = frozenset(int(x) for x in alt.split())

        for position, bpm in tempos.get(number, []):
            bar.tempos.append((Fraction(position).limit_denominator(1000) * bar.length, bpm))

        bar_id = _text(master, "Bars").split()[staff_index]
        for voice_id in _text(bars[bar_id], "Voices").split():
            if voice_id == "-1":
                continue
            pos = Fraction(0)
            for beat_id in _text(voices[voice_id], "Beats").split():
                beat = beats[beat_id]
                if beat.find("GraceNotes") is not None:
                    score.grace_notes_skipped += len(_text(beat, "Notes").split())
                    continue  # ornement : n'occupe pas de temps dans la mesure
                for note_id in _text(beat, "Notes").split():
                    note = notes[note_id]
                    tie = note.find("Tie")
                    if tie is not None and tie.get("destination") == "true":
                        continue
                    pitch = _note_pitch(note, articulations)
                    if pitch is not None:
                        bar.notes.append(DrumNote(pos, pitch, _dynamic(note)))
                pos += _duration(rhythms[beat.find("Rhythm").get("ref")])
        score.bars.append(bar)
    return score
