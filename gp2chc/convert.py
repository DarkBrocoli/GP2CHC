"""Conversion complète tablature -> dossier Clone Hero ; partagée par la ligne de commande et l'interface."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import align, audio, separate
from . import chart as chart_mod
from . import ini, midi, readers
from .i18n import tr
from .model import PPQ

MIN_HIT_RATE = 0.5  # en dessous, le calage est considéré comme raté
DEFAULT_LEAD_IN_MS = 3000.0  # silence ajouté au début du morceau, pour ne pas être surpris par la première note
LEAD_IN_KEY = "gp2chc_lead_in_ms"  # noté dans le song.ini pour ne pas décaler deux fois preview_start_time


@dataclass
class Options:
    track: str | None = None
    ini_template: str | None = None
    ini_values: dict[str, str] = field(default_factory=dict)  # --set
    mapping: str | None = None  # fichier JSON de correspondance
    mapping_overrides: dict[int, str] = field(default_factory=dict)  # pitch GM -> lane, prioritaire sur le défaut
    max_hands: int = 2
    offset_ms: float = 0
    dynamics: bool = True
    audio: list[str] = field(default_factory=list)  # pistes de batterie seule, pour caler le tempo
    mix: list[str] = field(default_factory=list)  # mix complet : la batterie est isolée (Demucs) puis sert au calage
    drums_start: float | None = None  # instant (s, silence ajouté compris) imposé pour la 1re note de batterie ; None = auto
    lead_in_ms: float = DEFAULT_LEAD_IN_MS  # silence ajouté au début du chart et des fichiers audio du morceau
    tempo_per_beat: bool = False  # calage : un tempo par temps au lieu d'un tempo par mesure
    separation: str = "standard"  # isolement de la batterie d'un mix : "standard" (Demucs) ou "hq" (BS-RoFormer)
    from_audio: bool = False  # mode sans tablature : les notes sont reconnues dans l'audio (expérimental)
    progress: Callable[[str], None] | None = None  # reçoit les messages d'avancement (interface graphique)


@dataclass
class Result:
    out_dir: Path
    track_name: str
    chart: chart_mod.Chart
    messages: list[str]
    warnings: list[str] = field(default_factory=list)  # problèmes à montrer à l'utilisateur (calage raté...)


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", text).strip() or "song"


def format_ms(ms: float) -> str:
    return f"{int(ms // 60000)}:{ms % 60000 / 1000:06.3f}"


def _shift_times(values: dict[str, str], lead: float) -> None:
    """Remet preview_start_time et song_length d'un song.ini existant dans le temps du morceau avec `lead` ms de
    silence au début (le song.ini a pu être écrit avec un autre silence, ou sans : modèle Rock Band...)."""
    try:
        old = float(values.get(LEAD_IN_KEY, 0) or 0)
    except ValueError:
        old = 0.0
    for key in ("preview_start_time", "song_length"):
        try:
            values[key] = str(round(float(values[key]) - old + lead))
        except (KeyError, ValueError):
            pass


def _from_tab(score, mapping, options: Options, lead: float, messages: list[str], warnings: list[str]):
    """Chart d'une tablature, calé sur l'audio s'il y en a. Renvoie (chart, durée du morceau en ms ou None)."""
    chart = chart_mod.build_chart(score, mapping, options.max_hands, options.offset_ms + lead)
    song_length = None
    if options.audio or options.mix:
        # calage sur l'audio d'origine (sans le silence ajouté par une conversion précédente)
        drums_start = None if options.drums_start is None else options.drums_start - lead / 1000
        if drums_start is not None and drums_start < 0:
            raise ValueError(
                tr("La 1re note de batterie ne peut pas être avant la fin du silence ajouté ({seconds:g} s)", seconds=lead / 1000)
            )
        cal = align.calibrate(
            chart_mod.build_chart(score, mapping, options.max_hands),
            [str(audio.original(p)) for p in options.audio],
            [str(audio.original_mix(p)) for p in options.mix],
            options.progress,
            drums_start,
            options.tempo_per_beat,
            options.separation,
        )
        song_length = round(cal.audio_ms + lead)
        if cal.hit_rate_after >= MIN_HIT_RATE:
            chart = chart_mod.build_chart(score, mapping, options.max_hands, cal.offset_ms + lead, cal.tempos, cal.layout)
            low, high = cal.bpm_range
            start = tr("Première note de batterie calée à {time}", time=format_ms(cal.first_note_ms + lead))
            if cal.first_attack_ms is not None:
                start += tr(" (première attaque détectée dans l'audio : {time})", time=format_ms(cal.first_attack_ms + lead))
            messages.append(start)
            if options.tempo_per_beat:
                messages.append(tr("Tempo suivi temps par temps : {low:.1f} à {high:.1f} BPM", low=low, high=high))
            else:
                messages.append(tr("Tempo suivi mesure par mesure : {low:.1f} à {high:.1f} BPM", low=low, high=high))
            if cal.intro_removed:
                messages.append(
                    tr(
                        "{count} mesure(s) d'intro sans batterie retirée(s) : l'intro de la tablature est plus longue "
                        "que le début de l'enregistrement",
                        count=cal.intro_removed,
                    )
                )
            for count, before in cal.inserted:
                messages.append(
                    tr(
                        "{count} mesure(s) vide(s) insérée(s) avant la mesure {bar} de la tablature : "
                        "l'enregistrement en contient plus à cet endroit",
                        count=count,
                        bar=before,
                    )
                )
            if cal.removed:
                listed = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in align._ranges(cal.removed)[:8])
                messages.append(tr("Mesures de la tablature absentes de l'enregistrement, retirées : {bars}", bars=listed))
            if (
                options.drums_start is None
                and cal.first_attack_ms is not None
                and abs(cal.first_note_ms - cal.first_attack_ms) > 500
            ):
                warnings.append(
                    tr(
                        "La première note de la tablature a été calée à {note}, mais l'audio contient des attaques de "
                        "batterie dès {attack}. Si le début est faux, indiquez l'instant de la première note de batterie "
                        "(champ « 1re note de batterie » ou --drums-start) et relancez.",
                        note=format_ms(cal.first_note_ms + lead),
                        attack=format_ms(cal.first_attack_ms + lead),
                    )
                )
            messages.append(
                tr(
                    "{close:.0%} des notes à moins de 15 ms d'une attaque, {hit:.0%} à moins de 30 ms (tempo constant : {before:.0%})",
                    close=cal.close_rate_after,
                    hit=cal.hit_rate_after,
                    before=cal.hit_rate_before,
                )
            )
            if cal.weak_bars:
                ranges = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in cal.weak_bars[:8])
                messages.append(
                    tr("Mesures où les notes ne collent pas à l'audio (tablature différente de l'enregistrement ?) : {bars}", bars=ranges)
                )
        else:
            warning = tr(
                "Calage impossible : seulement {rate:.0%} des notes tombent sur une attaque de l'audio. "
                "Le chart garde le tempo de la tablature : il ne sera PAS synchronisé avec la musique. "
                "Vérifiez que l'audio contient bien la batterie (song.opus des dossiers Rock Band n'en contient pas : "
                "prenez drums_*.opus ou un mix complet) et que la tablature correspond au morceau.",
                rate=cal.hit_rate_after,
            )
            warnings.append(warning)
            messages.append(warning)
    return chart, song_length


def _output_folder(output, options: Options) -> Path:
    """Mode sans tablature : le dossier choisi, sinon celui de l'audio."""
    if output:
        out = Path(output)
    else:
        source = (options.audio or options.mix or [None])[0]
        if source is None:
            raise ValueError(tr("Le mode sans tablature a besoin de l'audio : pistes de batterie ou mix complet"))
        out = Path(source).parent
    out.mkdir(parents=True, exist_ok=True)
    return out


def _song_names(out: Path, options: Options) -> tuple[str, str]:
    """(titre, artiste) : song.ini du dossier ou modèle, sinon nom du dossier « Artiste - Titre »."""
    template = options.ini_template or (out / "song.ini" if (out / "song.ini").exists() else None)
    values = ini.read_ini(template) if template else {}
    title, artist = values.get("name", ""), values.get("artist", "")
    if not title:
        name = out.name
        artist, _, title = name.partition(" - ") if " - " in name else ("", "", name)
    return title.strip(), artist.strip()


def _drums_for_transcription(out: Path, options: Options) -> Path:
    """Fichier de batterie seule à reconnaître : la piste de batterie (ou leur somme), ou la batterie isolée du mix."""
    if options.audio:
        files = [audio.original(p) for p in options.audio]
        if len(files) == 1:
            return files[0]
        return audio.sum_tracks(files)
    if options.mix:
        drums_file, _ = separate.stems(str(audio.original_mix(options.mix[0])), options.progress, options.separation)
        return drums_file
    raise ValueError(tr("Le mode sans tablature a besoin de l'audio : pistes de batterie ou mix complet"))


def _from_audio(out: Path, options: Options, mapping, lead: float, messages: list[str], warnings: list[str]):
    """Mode sans tablature : notes reconnues dans l'audio. Renvoie (score, chart)."""
    from . import transcribe

    drums_file = _drums_for_transcription(out, options)
    result = transcribe.transcribe(drums_file, options.tempo_per_beat, options.progress)
    score = result.score
    score.title, score.artist = _song_names(out, options)
    chart = chart_mod.build_chart(score, mapping, options.max_hands, result.offset_ms + lead, result.bar_tempos)
    found = result.counts
    messages.append(
        tr(
            "Mode sans tablature (expérimental) : tempo d'environ {tempo:.0f} BPM, {bars} mesures ; frappes reconnues : "
            "grosse caisse {kick}, caisse claire {snare}, toms {toms}, charley {hh}, ride {ride}, crash {crash}",
            tempo=result.tempo, bars=len(score.bars), **{k: found.get(k, 0) for k in transcribe.STEMS},
        )
    )
    warnings.append(
        tr(
            "Chart reconnu automatiquement dans l'audio : il y aura des notes en trop, manquantes ou de la mauvaise "
            "couleur (surtout entre charley et ride, et entre les toms). Repassez-le dans Moonscraper avant de jouer."
        )
    )
    return score, chart


def convert(input_path: str | Path, output: str | Path | None = None, options: Options | None = None) -> Result:
    """Lit la tablature et écrit notes.mid + song.ini. Lève ValueError / OSError en cas de problème."""
    options = options or Options()
    mapping = chart_mod.load_mapping(options.mapping, options.mapping_overrides)
    lead = max(0.0, options.lead_in_ms)
    messages: list[str] = []
    warnings: list[str] = []
    song_length = None

    if options.from_audio:
        out = _output_folder(output, options)
        score, chart = _from_audio(out, options, mapping, lead, messages, warnings)
        title = score.title
    else:
        score = readers.read_score(input_path, options.track)
        title = score.title or Path(input_path).stem
        out = Path(output) if output else Path("output") / safe_name(f"{score.artist} - {title}")
        out.mkdir(parents=True, exist_ok=True)
        chart, song_length = _from_tab(score, mapping, options, lead, messages, warnings)

    midi.write_midi(out / "notes.mid", chart_mod.to_midi_tracks(chart, title, options.dynamics), PPQ)

    # Mix complet sans pistes de batterie : la batterie isolée devient drums.opus et la chanson perd sa batterie
    # (sinon Clone Hero jouerait la batterie deux fois, et une note ratée ne la couperait pas)
    separated_song = None
    if options.mix and not options.audio:
        from . import separate

        mix = options.mix[0]
        drums_audio, rest_audio = separate.stems(str(audio.original_mix(mix)), options.progress, options.separation)
        separated_song = audio.install_separated(out, mix, drums_audio, rest_audio)
        inside = Path(mix).resolve().parent in {out.resolve(), (out / audio.BACKUP_DIR).resolve(),
                                                (out / audio.BACKUP_DIR / audio.FULL_MIX_DIR).resolve()}  # fmt: skip
        if inside:
            messages.append(
                tr(
                    "Batterie isolée enregistrée dans {drums} ; {song} contient maintenant le morceau sans la batterie "
                    "(mix complet d'origine gardé dans {folder})",
                    drums=audio.DRUMS_NAME,
                    song=separated_song,
                    folder=f"{audio.BACKUP_DIR}/{audio.FULL_MIX_DIR}",
                )
            )
        else:
            messages.append(
                tr(
                    "Batterie isolée enregistrée dans {drums} ; {song} contient le morceau sans la batterie "
                    "(votre mix complet n'est pas modifié)",
                    drums=audio.DRUMS_NAME,
                    song=separated_song,
                )
            )

    # Silence au début : les fichiers audio du morceau sont décalés d'autant que le chart
    files = []
    if lead > 0 or (out / audio.BACKUP_DIR).is_dir():
        mixes = [] if separated_song else options.mix
        files = audio.add_lead_in(out, options.audio, mixes, lead, options.progress)
    if files:
        if song_length is None:
            lengths = [audio.duration_ms(audio.original(f)) for f in files]
            song_length = round(max((x for x in lengths if x), default=0) + lead) or None
        if lead > 0:
            messages.append(
                tr(
                    "{seconds:g} s de silence ajoutées au début du chart et de {count} fichier(s) audio "
                    "(originaux conservés dans {folder})",
                    seconds=lead / 1000,
                    count=len(files),
                    folder=audio.BACKUP_DIR,
                )
            )
    elif lead > 0:
        messages.append(
            tr(
                "Le chart commence après {seconds:g} s de silence, mais aucun fichier audio n'est dans le dossier : "
                "choisissez l'audio dans la conversion (il sera décalé automatiquement), ou ajoutez vous-même ce silence.",
                seconds=lead / 1000,
            )
        )

    # song.ini : valeurs déduites de la tablature < song.ini déjà présent / modèle < options explicites
    values = {"name": title, "artist": score.artist, "album": score.album, "genre": "", "year": "",
              "charter": "", "diff_drums": "-1", "pro_drums": "True"}  # fmt: skip
    if song_length:
        values["song_length"] = str(song_length)
    template = options.ini_template or (out / "song.ini" if (out / "song.ini").exists() else None)
    if template:
        previous = ini.read_ini(template)
        _shift_times(previous, lead)
        values.update({k: v for k, v in previous.items() if v != "" or k not in values})
    values[LEAD_IN_KEY] = str(round(lead))
    values.update(options.ini_values)
    ini.write_ini(out / "song.ini", values)

    counts = [sum(1 for n in chart.notes if n.lane == lane) for lane in range(5)]
    messages = [
        tr("Piste : {name}", name=score.track_name),
        tr("{count} notes | durée du chart : {length}", count=len(chart.notes), length=format_ms(chart.ticks_to_ms(chart.end_tick))),
        tr("kick {0}, rouge {1}, jaune {2}, bleu {3}, vert {4}").format(*counts),
        *messages,
    ]
    skipped = [f"{c} x {_pitch_name(p)}" for p, c in sorted(chart.muted.items())]
    if score.grace_notes_skipped:
        skipped.append(tr("{count} x notes d'ornement (flams)", count=score.grace_notes_skipped))
    if skipped:
        messages.append(tr("Volontairement non charté : {notes}", notes=", ".join(skipped)))
    if chart.pedal_dropped:
        messages.append(
            tr("{count} charleys au pied retirés (joués avec un tom, ou avec la caisse claire dans un fill)", count=chart.pedal_dropped)
        )
    if chart.dropped_hands:
        messages.append(
            tr(
                "{count} notes retirées (plus de {pads} pads en même temps, grosse caisse non comptée)",
                count=chart.dropped_hands,
                pads=options.max_hands,
            )
        )
    if chart.unmapped:
        pitches = ", ".join(f"{p} (x{c})" for p, c in sorted(chart.unmapped.items()))
        messages.append(tr("Notes ignorées (pitch GM sans lane) : {pitches}", pitches=pitches))
    messages.append(tr("Fichiers écrits dans {folder}", folder=out))
    return Result(out, score.track_name, chart, messages, warnings)


def _pitch_name(pitch: int) -> str:
    return tr("charley au pied") if pitch == 44 else tr("pitch {pitch}", pitch=pitch)
