import struct
from fractions import Fraction
from pathlib import Path

import pytest

from gp2chc import chart, gpx, readers
from gp2chc.model import Bar, DrumNote, Score, expand_repeats

SAMPLE = Path(__file__).parent.parent / "Tabs" / "Iron Maiden-The Trooper-09-08-2026.gp"


def bars(*specs):
    """specs : (nom, options) ; le nom sert de repère dans les assertions."""
    out = []
    for name, opts in specs:
        bar = Bar(section=name, **opts)
        out.append(bar)
    return out


def order(played):
    return [b.section for b in played]


def test_repeat_simple():
    b = bars(("A", {"repeat_start": True}), ("B", {"repeat_end": 2}), ("C", {}))
    assert order(expand_repeats(b)) == ["A", "B", "A", "B", "C"]


def test_repeat_three_times():
    b = bars(("A", {"repeat_start": True, "repeat_end": 3}), ("B", {}))
    assert order(expand_repeats(b)) == ["A", "A", "A", "B"]


def test_alternate_endings():
    b = bars(
        ("A", {"repeat_start": True}),
        ("B1", {"alternatives": frozenset({1}), "repeat_end": 2}),
        ("B2", {"alternatives": frozenset({2})}),
        ("C", {}),
    )
    assert order(expand_repeats(b)) == ["A", "B1", "A", "B2", "C"]


def test_two_separate_repeats():
    b = bars(
        ("A", {"repeat_start": True, "repeat_end": 2}),
        ("B", {"repeat_start": True, "repeat_end": 2}),
    )
    assert order(expand_repeats(b)) == ["A", "A", "B", "B"]


def make_score(*bar_notes):
    return Score(bars=[Bar(notes=[DrumNote(Fraction(p), pitch) for p, pitch in notes]) for notes in bar_notes])


def test_chart_ticks_and_lanes():
    score = make_score([(0, 36), (0, 51), (1, 38), (Fraction(3, 2), 45)])
    c = chart.build_chart(score, chart.load_mapping())
    assert [(n.tick, n.lane, n.tom) for n in c.notes] == [(0, 0, False), (0, 3, False), (480, 1, False), (720, 3, True)]
    assert c.end_tick == 1920


def test_pedal_hat_is_yellow_cymbal_and_unknown_reported():
    c = chart.build_chart(make_score([(0, 44), (0, 56)]), chart.load_mapping())
    assert [(n.lane, n.tom) for n in c.notes] == [(2, False)]  # charley au pied -> jaune cymbale
    assert c.unmapped[56] == 1


def test_max_hands_keeps_snare_and_crash():
    c = chart.build_chart(make_score([(0, 38), (0, 49), (0, 42)]), chart.load_mapping(), max_hands=2)
    assert sorted(n.lane for n in c.notes) == [1, 4]
    assert c.dropped_hands == 1


def test_cymbal_wins_over_tom_on_same_lane():
    c = chart.build_chart(make_score([(0, 49), (0, 43)]), chart.load_mapping())
    assert [(n.lane, n.tom) for n in c.notes] == [(4, False)]


def test_offset_shifts_everything():
    c = chart.build_chart(make_score([(0, 36)]), chart.load_mapping(), offset_ms=500)
    assert c.notes[0].tick == 480  # 120 BPM par défaut : 500 ms = 1 noire


def test_lead_in_is_whole_beats_with_signature_on_first_bar():
    # 3,1 s de silence à 120 BPM = 6,2 temps : on fait 6 temps à un tempo ajusté, la mesure 1 tombe pile
    c = chart.build_chart(make_score([], [(0, 36), (1, 38)]), chart.load_mapping(), offset_ms=3100)
    assert c.tempos[0] == (0, pytest.approx(6 * 60000 / 3100))
    assert c.tempos[1] == (6 * 480, 120.0)
    assert c.ticks_to_ms(6 * 480) == pytest.approx(3100)
    # signatures : début, mesure 1 (après le silence), mesure de la première note de batterie
    assert [t for t, _, _ in c.signatures] == [0, 6 * 480, 6 * 480 + 1920]
    assert c.ticks_to_ms(c.notes[0].tick) == pytest.approx(3100 + 2000)


def test_tempo_to_ms():
    c = chart.Chart(tempos=[(0, 120.0), (960, 60.0)])
    assert c.ticks_to_ms(960) == pytest.approx(1000)
    assert c.ticks_to_ms(1440) == pytest.approx(2000)


def _bcfz_literals(payload: bytes) -> bytes:
    """Encode en BCFZ avec uniquement des littéraux (blocs de 3 octets max)."""
    bits = ""
    for i in range(0, len(payload), 3):
        chunk = payload[i : i + 3]
        bits += "0" + format(len(chunk), "02b")[::-1] + "".join(format(b, "08b") for b in chunk)
    bits += "0" * (-len(bits) % 8)
    return b"BCFZ" + struct.pack("<I", len(payload)) + int(bits, 2).to_bytes(len(bits) // 8, "big")


def _bcfs(name: str, content: bytes) -> bytes:
    entry = bytearray(0x1000)
    entry[0:4] = struct.pack("<i", 2)
    entry[4 : 4 + len(name)] = name.encode()
    entry[0x8C:0x90] = struct.pack("<i", len(content))
    blocks = -(-len(content) // 0x1000)
    for i in range(blocks):  # le contenu occupe les secteurs 2, 3...
        entry[0x94 + 4 * i : 0x98 + 4 * i] = struct.pack("<i", 2 + i)
    return b"BCFS" + bytes(0x1000 - 4) + bytes(entry) + content.ljust(blocks * 0x1000, b"\0")


def test_gpx_container_roundtrip():
    gpif = b"<GPIF>" + b"x" * 5000 + b"</GPIF>"
    raw = _bcfs("score.gpif", gpif)
    assert gpx.extract_gpif(raw) == gpif
    assert gpx.extract_gpif(_bcfz_literals(raw)) == gpif


def test_gpx_back_reference():
    # littéral "abc" (3 octets) puis copie de 3 octets à distance 3 (largeur 2 bits)
    bits = "0" + "11" + "".join(format(b, "08b") for b in b"abc") + "1" + "0010" + "11" + "11"
    bits += "0" * (-len(bits) % 8)
    data = b"BCFZ" + struct.pack("<I", 6) + int(bits, 2).to_bytes(len(bits) // 8, "big")
    assert gpx._decompress(data) == b"abcabc"


@pytest.mark.skipif(not SAMPLE.exists(), reason="exemple absent")
def test_sample_file():
    score = readers.read_score(SAMPLE)
    assert len(score.bars) == 169
    c = chart.build_chart(score, chart.load_mapping())
    assert c.tempos == [(0, 162.0)]
    assert 2100 < len(c.notes) < 2200  # dont les charleys au pied, en jaune
    assert 150 < c.dropped_hands < 260  # charleys au pied tombant avec la caisse claire et la ride
    assert 250_000 < c.ticks_to_ms(c.end_tick) < 260_000
    assert not c.unmapped


def test_calibrate_recovers_offset_and_tempo(monkeypatch):
    np = pytest.importorskip("numpy")
    from gp2chc import align

    # 16 mesures à 120 BPM dans la tablature, jouées dans l'audio à 126 BPM à partir de 3 s
    positions = [(0, 36), (1, 38), (2, 36), (Fraction(5, 2), 36), (3, 38)]
    score = make_score(*[positions] * 16)
    c = chart.build_chart(score, chart.load_mapping())

    real_bar = 4 * 60 / 126
    env = np.zeros(int(60 * align.FPS))
    for bar in range(16):
        for pos, _ in positions:
            env[int(round((3 + bar * real_bar + float(pos) / 4 * real_bar) * align.FPS))] = 5.0

    monkeypatch.setattr(align, "decode_audio", lambda path: env)
    monkeypatch.setattr(align, "onset_envelope", lambda samples: samples)
    cal = align.calibrate(c, ["fake.wav"])

    assert cal.offset_ms == pytest.approx(3000, abs=30)
    assert np.median([b for bar in cal.tempos for b in bar]) == pytest.approx(126, abs=1.5)
    assert cal.hit_rate_after > 0.95

    calibrated = chart.build_chart(score, chart.load_mapping(), offset_ms=cal.offset_ms, bar_tempos=cal.tempos, layout=cal.layout)
    assert calibrated.ticks_to_ms(calibrated.notes[0].tick) == pytest.approx(3000, abs=30)
    assert calibrated.ticks_to_ms(calibrated.end_tick) == pytest.approx(3000 + 16 * real_bar * 1000, abs=100)


def test_convert_writes_files(tmp_path):
    from gp2chc.convert import Options, convert

    if not SAMPLE.exists():
        pytest.skip("exemple absent")
    result = convert(SAMPLE, tmp_path, Options(ini_values={"charter": "moi"}))
    assert (tmp_path / "notes.mid").stat().st_size > 1000
    ini_text = (tmp_path / "song.ini").read_text(encoding="utf-8")
    assert "name = The Trooper" in ini_text and "charter = moi" in ini_text
    assert any("notes d'ornement" in m for m in result.messages)


def test_separate_uses_cache(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    from gp2chc import separate

    audio = tmp_path / "mix.wav"
    audio.write_bytes(b"fake")
    monkeypatch.setattr(separate, "CACHE_DIR", tmp_path / "cache")
    calls = []

    def fake_separate(path, progress):
        calls.append(path)
        return np.arange(5, dtype=np.float32)

    monkeypatch.setattr(separate, "_separate", fake_separate)
    first = separate.drums(str(audio))
    second = separate.drums(str(audio))
    assert len(calls) == 1
    assert (first == second).all()

    audio.write_bytes(b"fake but different")  # fichier modifié : le cache ne doit plus servir
    separate.drums(str(audio))
    assert len(calls) == 2


def test_calibrate_with_mix_goes_through_separation(monkeypatch):
    np = pytest.importorskip("numpy")
    from gp2chc import align, separate

    score = make_score(*[[(0, 36), (1, 38), (2, 36), (3, 38)]] * 8)
    c = chart.build_chart(score, chart.load_mapping())
    env = np.zeros(int(30 * align.FPS))
    for bar in range(8):
        for beat in range(4):
            env[int((2 + bar * 2 + beat * 0.5) * align.FPS)] = 5.0  # 120 BPM, début à 2 s

    messages = []
    monkeypatch.setattr(separate, "drums", lambda path, progress=None: env)
    monkeypatch.setattr(align, "onset_envelope", lambda samples: samples)
    cal = align.calibrate(c, [], ["mix.wav"], progress=messages.append)
    assert cal.offset_ms == pytest.approx(2000, abs=30)
    assert messages  # l'avancement est signalé


def test_mapping_overrides():
    mapping = chart.load_mapping(overrides={44: "yellow-cymbal", 56: "blue-tom"})
    assert mapping[44] == "yellow-cymbal" and mapping[56] == "blue-tom"
    assert mapping[38] == "red"  # le reste ne change pas
    with pytest.raises(ValueError):
        chart.load_mapping(overrides={44: "violet"})


def test_convert_applies_mapping_overrides(tmp_path):
    from gp2chc.convert import Options, convert

    if not SAMPLE.exists():
        pytest.skip("exemple absent")
    default = convert(SAMPLE, tmp_path / "a", Options(max_hands=0))
    no_pedal = convert(SAMPLE, tmp_path / "b", Options(max_hands=0, mapping_overrides={44: "none"}))
    assert len(no_pedal.chart.notes) < len(default.chart.notes) - 400  # les charleys au pied disparaissent


def test_deps_status_keys():
    from gp2chc import deps

    assert set(deps.status()) == {"numpy", "ffmpeg", "demucs"}


def test_failed_calibration_is_reported_as_warning(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    from gp2chc import align
    from gp2chc.convert import Options, convert

    if not SAMPLE.exists():
        pytest.skip("exemple absent")
    fake = align.Calibration(
        offset_ms=0, layout=[], tempos=[], first_note_ms=0, first_attack_ms=None,
        hit_rate_before=0.2, hit_rate_after=0.3, close_rate_after=0.1, audio_ms=1000,
    )  # fmt: skip
    monkeypatch.setattr(align, "calibrate", lambda *a, **k: fake)
    result = convert(SAMPLE, tmp_path, Options(audio=["x.opus"], lead_in_ms=0))
    assert result.warnings and "PAS synchronisé" in result.warnings[0]
    assert result.chart.tempos == [(0, 162.0)]  # tempo de la tablature conservé


def _pattern(k):
    """Motif propre à la mesure k (pour que l'alignement ne puisse pas confondre deux mesures)."""
    notes = [(Fraction(0), 36), (Fraction(2 + k % 3, 2), 38), (Fraction(4 + k % 5, 4), 42)]
    if k % 2:
        notes.append((Fraction(7, 2), 45))
    return notes


def _fake_audio(monkeypatch, bar_patterns, start, bpm, seconds=60):
    """Remplace le décodage audio par des impulsions aux instants joués (bar_patterns : motifs dans l'ordre joué)."""
    np = pytest.importorskip("numpy")
    from gp2chc import align

    bar = 4 * 60 / bpm
    env = np.zeros(int(seconds * align.FPS))
    for k, notes in enumerate(bar_patterns):
        for pos, _ in notes:
            env[int(round((start + k * bar + float(pos) / 4 * bar) * align.FPS))] = 5.0
    monkeypatch.setattr(align, "decode_audio", lambda path: env)
    monkeypatch.setattr(align, "onset_envelope", lambda samples: samples)
    return align


def test_calibrate_late_drums_and_long_intro(monkeypatch):
    # tablature : 6 mesures sans batterie puis 10 mesures ; enregistrement : batterie dès 3 s, à 126 BPM
    patterns = [_pattern(k) for k in range(10)]
    score = make_score(*([[]] * 6 + patterns))
    align = _fake_audio(monkeypatch, patterns, start=3.0, bpm=126)
    cal = align.calibrate(chart.build_chart(score, chart.load_mapping()), ["fake.wav"])

    assert cal.first_note_ms == pytest.approx(3000, abs=30)
    assert cal.intro_removed >= 4  # 6 mesures d'intro ne tiennent pas en 3 s
    calibrated = chart.build_chart(score, chart.load_mapping(), offset_ms=cal.offset_ms, bar_tempos=cal.tempos, layout=cal.layout)
    assert calibrated.ticks_to_ms(calibrated.notes[0].tick) == pytest.approx(3000, abs=30)


def test_calibrate_inserts_bar_missing_from_tab(monkeypatch):
    # l'enregistrement joue une mesure de plus (après la 7e) que la tablature
    tab = [_pattern(k) for k in range(12)]
    played = tab[:7] + [[(Fraction(0), 36), (Fraction(1), 36), (Fraction(2), 36), (Fraction(3), 36)]] + tab[7:]
    score = make_score(*tab)
    align = _fake_audio(monkeypatch, played, start=2.0, bpm=120)
    cal = align.calibrate(chart.build_chart(score, chart.load_mapping()), ["fake.wav"])

    assert sum(count for count, _ in cal.inserted) == 1
    assert cal.layout.count(None) == 1 and cal.layout.index(None) in (6, 7, 8)
    assert not cal.removed
    calibrated = chart.build_chart(score, chart.load_mapping(), offset_ms=cal.offset_ms, bar_tempos=cal.tempos, layout=cal.layout)
    last = max(n.tick for n in calibrated.notes)
    assert calibrated.ticks_to_ms(last) == pytest.approx((2 + 12 * 2 + 3.5 * 0.5) * 1000, abs=60)  # 13 mesures jouées de 2 s, dernière note au temps 3,5


def test_calibrate_removes_bar_absent_from_recording(monkeypatch):
    # la tablature a une mesure (la 6e) que l'enregistrement ne joue pas
    tab = [_pattern(k) for k in range(12)]
    played = tab[:5] + tab[6:]
    score = make_score(*tab)
    align = _fake_audio(monkeypatch, played, start=2.0, bpm=120)
    cal = align.calibrate(chart.build_chart(score, chart.load_mapping()), ["fake.wav"])

    assert len(cal.removed) == 1 and cal.removed[0] in (5, 6, 7)
    assert None not in cal.layout
    assert cal.hit_rate_after > 0.95


def test_layout_carries_section_of_removed_bar():
    score = make_score([(0, 36)], [(0, 38)], [(0, 36)])
    score.bars[1].section = "Refrain"
    c = chart.build_chart(score, chart.load_mapping(), layout=[0, None, 2])
    assert [name for _, name in c.sections] == ["Refrain"]  # la mesure 2 est retirée, sa section passe à la suivante
    assert len(c.bars) == 3 and c.bars[1].positions == []


def _wav(path, seconds=1.0, rate=22050):
    import math
    import struct
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(int(seconds * rate))))


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg absent")
def test_lead_in_pads_audio_once_and_keeps_originals(tmp_path):
    from gp2chc import audio

    _wav(tmp_path / "song.wav")
    _wav(tmp_path / "drums.wav")
    (tmp_path / "notes.txt").write_text("pas un audio")

    files = audio.add_lead_in(tmp_path, [], [], 500)
    assert sorted(f.name for f in files) == ["drums.wav", "song.wav"]
    assert audio.duration_ms(tmp_path / "song.wav") == pytest.approx(1500, abs=5)
    assert audio.duration_ms(tmp_path / audio.BACKUP_DIR / "song.wav") == pytest.approx(1000, abs=5)
    assert audio.original(tmp_path / "song.wav") == tmp_path / audio.BACKUP_DIR / "song.wav"

    # deuxième passage : rien n'est réencodé, le silence ne s'ajoute pas deux fois
    stamp = (tmp_path / "song.wav").stat().st_mtime_ns
    audio.add_lead_in(tmp_path, [], [], 500)
    assert (tmp_path / "song.wav").stat().st_mtime_ns == stamp
    assert audio.duration_ms(tmp_path / "song.wav") == pytest.approx(1500, abs=5)

    # autre durée de silence, puis aucun silence : toujours recalculé depuis l'original
    audio.add_lead_in(tmp_path, [], [], 2000)
    assert audio.duration_ms(tmp_path / "drums.wav") == pytest.approx(3000, abs=5)
    audio.add_lead_in(tmp_path, [], [], 0)
    assert audio.duration_ms(tmp_path / "drums.wav") == pytest.approx(1000, abs=5)


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg absent")
def test_lead_in_copies_audio_chosen_elsewhere_under_clone_hero_names(tmp_path):
    from gp2chc import audio

    elsewhere = tmp_path / "ailleurs"
    elsewhere.mkdir()
    _wav(elsewhere / "Ma batterie.wav")
    _wav(elsewhere / "Mon mix.wav")
    song = tmp_path / "morceau"
    song.mkdir()
    audio.add_lead_in(song, [str(elsewhere / "Ma batterie.wav")], [str(elsewhere / "Mon mix.wav")], 1000)
    assert sorted(p.name for p in song.glob("*.wav")) == ["drums.wav", "song.wav"]
    assert audio.duration_ms(song / "drums.wav") == pytest.approx(2000, abs=5)
    assert (elsewhere / "Ma batterie.wav").exists()  # l'original choisi ailleurs n'est pas déplacé


def test_song_ini_times_follow_lead_in():
    from gp2chc.convert import LEAD_IN_KEY, _shift_times

    rock_band = {"preview_start_time": "52500", "song_length": "254709"}
    _shift_times(rock_band, 3000)
    assert rock_band == {"preview_start_time": "55500", "song_length": "257709"}
    ours = {"preview_start_time": "55500", "song_length": "257709", LEAD_IN_KEY: "3000"}
    _shift_times(ours, 3000)  # déjà décalé : pas de double décalage
    assert ours["preview_start_time"] == "55500"
    _shift_times(ours, 1000)
    assert ours["preview_start_time"] == "53500"


def test_time_signature_on_every_bar():
    c = chart.build_chart(make_score([(0, 36)], [(0, 38)], [(0, 36)]), chart.load_mapping())
    assert [t for t, _, _ in c.signatures] == [0, 1920, 3840]


def _lanes(*pitches):
    c = chart.build_chart(make_score([(0, p) for p in pitches]), chart.load_mapping(), max_hands=2)
    return sorted((n.lane, n.tom) for n in c.notes), c.dropped_hands


def test_kick_comes_on_top_of_two_pads():
    # grosse caisse + 2 cymbales, ou grosse caisse + tom + cymbale : tout est gardé
    assert _lanes(36, 51, 49) == ([(0, False), (3, False), (4, False)], 0)
    assert _lanes(36, 45, 49) == ([(0, False), (3, True), (4, False)], 0)


def test_pedal_hat_is_a_hand_in_game():
    # caisse claire + ride + charley au pied : 3 pads, le charley (jaune) est retiré
    assert _lanes(38, 51, 44) == ([(1, False), (3, False)], 1)
    # caisse claire + 2 cymbales jamais, même avec la grosse caisse
    assert _lanes(36, 38, 51, 49) == ([(0, False), (1, False), (4, False)], 1)


def test_three_hand_pads_are_never_kept():
    # 3 toms / cymbales sans pédale : une note retirée (priorité rouge, vert, bleu, jaune)
    assert _lanes(51, 49, 48) == ([(3, False), (4, False)], 1)
    # charley joué à la main en même temps que la pédale (même lane jaune) : c'est une main
    assert _lanes(44, 42, 51, 38) == ([(1, False), (3, False)], 1)


def test_pedal_hat_with_tom_keeps_only_the_tom():
    assert _lanes(44, 45) == ([(3, True)], 0)  # tom grave seul
    assert _lanes(44, 48) == ([(2, True)], 0)  # le charley au pied n'écrase pas le tom jaune


def test_pedal_hat_with_snare_dropped_only_in_fills():
    groove = [(q, 36) for q in range(4)] + [(Fraction(2 * q + 1, 2), 42) for q in range(4)]  # 2 attaques par temps
    with_pedal = groove + [(1, 38), (1, 44), (3, 38), (3, 44)]
    fill = [(0, 36), (Fraction(1, 2), 42), (1, 38), (1, 44), (Fraction(5, 4), 38), (Fraction(3, 2), 38), (Fraction(7, 4), 38)]
    c = chart.build_chart(make_score(groove, with_pedal, fill), chart.load_mapping())
    yellow_cymbals = {n.tick for n in c.notes if n.lane == 2 and not n.tom}
    assert 1920 + 480 in yellow_cymbals and 1920 + 1440 in yellow_cymbals  # groove : caisse claire + pied gardés
    assert 3840 + 480 not in yellow_cymbals  # fill (4 attaques dans le temps) : seule la caisse claire reste
    assert c.pedal_dropped == 1


def _tr_literals():
    import ast

    package = Path(__file__).parent.parent / "gp2chc"
    found = {}
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                arg = node.args[0]
                assert isinstance(arg, ast.Constant), f"tr() sans texte littéral dans {path.name}:{node.lineno}"
                found[arg.value] = path.name
    return found


def test_every_text_has_an_english_translation():
    import re

    from gp2chc.i18n import EN

    used = _tr_literals()
    assert not [text for text in used if text not in EN], "textes sans traduction anglaise"
    assert not [text for text in EN if text not in used], "traductions inutilisées"
    for french, english in EN.items():  # mêmes valeurs à remplacer dans les deux langues
        assert set(re.findall(r"\{(\w*)", french)) == set(re.findall(r"\{(\w*)", english)), french


def test_english_messages(tmp_path):
    from gp2chc import i18n
    from gp2chc.convert import Options, convert

    if not SAMPLE.exists():
        pytest.skip("exemple absent")
    i18n.set_language("en")
    result = convert(SAMPLE, tmp_path, Options(lead_in_ms=0))
    assert result.messages[0].startswith("Track: ")
    assert any("grace notes (flams)" in m for m in result.messages)
    assert i18n.tr("{count} piste(s) de batterie", count=3) == "3 drum track(s)"


def test_system_language_detection(monkeypatch):
    from gp2chc import i18n

    monkeypatch.setattr(i18n.locale, "windows_locale", {}, raising=False)
    for name, expected in (("fr_FR.UTF-8", "fr"), ("fr_CA", "fr"), ("French_France", "fr"), ("en_US.UTF-8", "en"), ("de_DE", "en")):
        monkeypatch.setenv("LANGUAGE", name)
        assert i18n.system_language() == expected, name
    assert i18n.set_language("auto") in ("fr", "en")


def test_language_preference_is_saved(tmp_path, monkeypatch):
    from gp2chc import i18n

    monkeypatch.setattr(i18n, "SETTINGS", tmp_path / "gp2chc" / "settings.json")
    assert i18n.preference() == "auto"
    i18n.save_preference("en")
    assert i18n.preference() == "en"
