"""Calage de la tablature sur l'audio : structure, début et carte de tempo suivant l'enregistrement.

Demande numpy et ffmpeg (pour décoder mp3/ogg/opus/wav...). Le principe :
  1. enveloppe des attaques de l'audio (flux spectral à fine résolution temporelle) ;
  2. rapport de tempo global entre la tablature et l'enregistrement (corrélation) ;
  3. alignement de structure sur tout le morceau (programmation dynamique, mesure par mesure) : la batterie
     peut commencer n'importe où, l'enregistrement peut contenir des mesures absentes de la tablature (on
     insère des mesures vides) et la tablature des mesures absentes de l'enregistrement (on les retire).
     Indispensable sur un morceau répétitif : un décalage d'une ou deux mesures « colle » presque aussi bien ;
  4. affinage en trois étages (mesures, temps, millisecondes) : les notes tombent sur les attaques.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from . import deps
from .chart import BarTiming, Chart
from .i18n import tr
from .model import PPQ

SR = 22050
N_FFT = 512  # fenêtre de 23 ms : localise mieux les attaques qu'une fenêtre longue
HOP = 64  # pas de 2,9 ms
FPS = SR / HOP
HIT_TOLERANCE = 0.03  # une note "colle" à l'audio si une attaque est à moins de 30 ms
CLOSE_TOLERANCE = 0.015

# Alignement de structure
STRUCT_STEP = 0.02  # résolution (s)
STRUCT_JUMP = 0.12  # écart de durée max d'une mesure par rapport au tempo global
STRUCT_STIFFNESS = 2000.0
EDIT_PENALTY = 8.0  # coût d'une mesure insérée ou retirée, en « attaques bien placées »
MAX_INSERT = 8  # mesures consécutives insérables d'un coup
NO_INSERT_BARS = 4  # pas d'insertion dans les premières mesures : le début libre suffit, et un décompte
# ou un bruit avant le morceau attirerait sinon les premières notes
UNEXPLAINED_COST = 0.5  # coût d'une attaque audio avant la première note ou après la dernière


@dataclass
class Stage:
    per_bar: int  # nombre de segments par mesure (0 = un par temps)
    step: float  # résolution de la recherche (s)
    window: int  # la recherche explore +-window*step autour de la position précédente
    sigma: float  # lissage de l'enveloppe (s) : large au début, étroit pour l'affinage
    jump: float  # écart relatif max de durée d'un segment par rapport à la position précédente
    stiffness: float  # pénalité de variation de tempo
    passes: int = 1


_BAR_COARSE = Stage(per_bar=1, step=0.010, window=20, sigma=0.020, jump=0.06, stiffness=20000.0, passes=2)
# Par défaut : un tempo par mesure (grille régulière dans chaque mesure, plus facile à retoucher dans Moonscraper)
STAGES = [
    _BAR_COARSE,
    Stage(per_bar=1, step=0.002, window=15, sigma=0.004, jump=0.03, stiffness=20000.0),
]
# En option : un tempo par temps (suit plus finement le batteur)
BEAT_STAGES = [
    _BAR_COARSE,
    Stage(per_bar=0, step=0.004, window=25, sigma=0.008, jump=0.10, stiffness=5000.0),
    Stage(per_bar=0, step=0.002, window=10, sigma=0.004, jump=0.05, stiffness=5000.0),
]


@dataclass
class Calibration:
    offset_ms: float  # instant (dans l'audio) où commence la première mesure du chart
    layout: list[int | None]  # mesures du chart : indice de mesure jouée de la tablature, ou None = mesure insérée
    tempos: list[list[float]]  # pour chaque mesure du chart, le tempo (BPM) de chacun de ses temps
    first_note_ms: float  # instant de la première note de batterie dans l'audio
    first_attack_ms: float | None  # première attaque de batterie détectée dans l'audio (pour information)
    hit_rate_before: float  # part des notes tombant sur une attaque avec un tempo constant
    hit_rate_after: float  # idem après le calage (à moins de 30 ms)
    close_rate_after: float  # part des notes à moins de 15 ms d'une attaque
    audio_ms: float
    weak_bars: list[tuple[int, int]] = field(default_factory=list)  # mesures de la tablature (depuis 1) qui ne collent pas
    inserted: list[tuple[int, int]] = field(default_factory=list)  # (nb de mesures insérées, avant la mesure n° de la tablature)
    removed: list[int] = field(default_factory=list)  # mesures de la tablature (depuis 1) absentes de l'enregistrement
    intro_removed: int = 0  # mesures d'intro sans batterie retirées (intro de la tablature trop longue)

    @property
    def bpm_range(self) -> tuple[float, float]:
        flat = [bpm for bar in self.tempos for bpm in bar]
        return min(flat), max(flat)


def _numpy():
    try:
        import numpy as np
    except ImportError as e:
        raise ValueError(tr("Le calage sur l'audio demande numpy : pip install numpy")) from e
    return np


def decode_audio(path: str, sample_rate: int = SR, channels: int = 1):
    """Décode n'importe quel audio avec ffmpeg -> tableau float32 (mono) ou (échantillons, canaux)."""
    np = _numpy()
    executable = deps.ffmpeg()
    if not executable:
        raise ValueError(tr("ffmpeg est introuvable (nécessaire pour lire l'audio) : https://ffmpeg.org/download.html"))
    cmd = [executable, "-v", "error", "-i", str(path), "-ac", str(channels), "-ar", str(sample_rate), "-f", "f32le", "-"]
    run = subprocess.run(cmd, capture_output=True, creationflags=deps.NO_WINDOW)
    if run.returncode != 0 or not run.stdout:
        error = run.stderr.decode(errors="replace").strip()
        raise ValueError(tr("Impossible de lire l'audio '{path}' : {error}", path=path, error=error))
    samples = np.frombuffer(run.stdout, dtype=np.float32)
    return samples if channels == 1 else samples.reshape(-1, channels)


def onset_envelope(samples):
    """Force des attaques à chaque trame (flux spectral positif), normalisée ; la trame k est centrée à k/FPS."""
    np = _numpy()
    pad = np.concatenate([np.zeros(N_FFT // 2, np.float32), samples, np.zeros(N_FFT // 2, np.float32)])
    windows = np.lib.stride_tricks.sliding_window_view(pad, N_FFT)[::HOP]
    hann = np.hanning(N_FFT).astype(np.float32)
    flux = []
    loudness = []
    previous = None
    for start in range(0, len(windows), 4096):
        chunk = windows[start : start + 4096]
        spectrum = np.log1p(100 * np.abs(np.fft.rfft(chunk * hann, axis=1)))
        stacked = spectrum if previous is None else np.vstack([previous, spectrum])
        diff = np.maximum(0, np.diff(stacked, axis=0)).sum(axis=1)
        flux.append(diff if previous is not None else np.concatenate([[0.0], diff]))
        loudness.append(np.sqrt((chunk**2).mean(axis=1)))
        previous = spectrum[-1:]
    env = np.concatenate(flux)
    # L'échelle logarithmique fait ressortir un son faible qui sort du silence autant qu'un vrai coup :
    # on pondère par le volume (plein poids à partir de -20 dB sous le niveau des passages forts).
    rms = np.concatenate(loudness)
    loud = np.percentile(rms, 95) + 1e-9
    env *= np.clip(rms / (0.1 * loud), 0, 1)
    window = int(FPS)
    env = np.maximum(0, env - np.convolve(env, np.ones(window) / window, mode="same"))
    return env / (env.mean() + 1e-9)


def _smooth(env, sigma: float):
    np = _numpy()
    half = int(4 * sigma * FPS)
    kernel = np.exp(-0.5 * (np.arange(-half, half + 1) / (sigma * FPS)) ** 2)
    return np.convolve(env, kernel / kernel.sum() * 2.5, mode="same")


def _capped(env, sigma: float, peaks):
    """Enveloppe lissée dont les valeurs sont plafonnées à celle d'un coup franc normal (90e centile des attaques) :
    une note ne peut pas rapporter plus qu'une bonne attaque, un pic isolé énorme ne fait pas basculer le calage."""
    np = _numpy()
    smooth = _smooth(env, sigma)
    if not len(peaks):
        return smooth
    return np.minimum(smooth, np.percentile(_at(smooth, peaks), 90))


def _strong_attacks(env, peaks):
    """Attaques franches (au moins la moitié d'un coup normal), un seul instant par salve de 60 ms."""
    np = _numpy()
    if not len(peaks):
        return peaks
    heights = _at(env, peaks)
    strong = peaks[heights >= 0.5 * np.percentile(heights, 90)]
    keep = np.concatenate([[True], np.diff(strong) > 0.06]) if len(strong) else strong.astype(bool)
    return strong[keep]


def _peaks(env):
    np = _numpy()
    inner = env[1:-1]
    mask = (inner > env[:-2]) & (inner >= env[2:]) & (inner > 1.0)
    return (np.nonzero(mask)[0] + 1) / FPS


def _distances(times, peaks):
    np = _numpy()
    i = np.clip(np.searchsorted(peaks, times), 1, len(peaks) - 1)
    return np.minimum(np.abs(peaks[i] - times), np.abs(peaks[i - 1] - times))


def _hit_rate(times, peaks, tolerance: float = HIT_TOLERANCE) -> float:
    if not len(times) or not len(peaks):
        return 0.0
    return float((_distances(times, peaks) < tolerance).mean())


def _at(env, seconds):
    np = _numpy()
    return env[np.clip(np.rint(seconds * FPS).astype(int), 0, len(env) - 1)]


def first_attack(env, peaks) -> float | None:
    """Instant (s) de la première vraie attaque de batterie : un pic franc suivi d'autres attaques (pas un bruit isolé)."""
    np = _numpy()
    if not len(peaks):
        return None
    heights = _at(env, peaks)
    strong = peaks[heights >= 0.25 * np.percentile(heights, 90)]
    for t in strong:
        if np.count_nonzero((strong > t) & (strong < t + 3.0)) >= 4:
            return float(t)
    return float(strong[0]) if len(strong) else None


def _tempo_ratio(times, env):
    """(a, b) : meilleur calage à tempo constant, temps_audio = a * temps_tablature + b (corrélation par FFT).

    Seul a (rapport de tempo) sert ensuite : sur un morceau répétitif, b peut tomber à une ou deux mesures près.
    """
    np = _numpy()
    rate = 100.0
    coarse = _at(env, np.arange(0, len(env) / FPS, 1 / rate))
    n = len(coarse)
    span = int(np.ceil(times.max() * 1.06 * rate)) + 2
    size = 1 << int(np.ceil(np.log2(n + span)))
    spectrum = np.fft.rfft(coarse, size)
    best = (-np.inf, 1.0, 0.0)
    for a in np.arange(0.95, 1.05, 0.001):
        train = np.bincount(np.rint(times * a * rate).astype(int), minlength=span)
        corr = np.fft.irfft(spectrum * np.conj(np.fft.rfft(train, size)), size)[:n]  # corr[k] : score si b = k / rate
        k = int(corr.argmax())
        if corr[k] > best[0]:
            best = (float(corr[k]), float(a), k / rate)
    _, a0, b0 = best
    offsets = np.arange(max(0.0, b0 - 0.05), b0 + 0.05, 0.002)
    for a in np.arange(a0 - 0.001, a0 + 0.001, 0.0001):
        s = _at(env, times[None, :] * a + offsets[:, None]).sum(axis=1)
        k = int(s.argmax())
        if s[k] > best[0]:
            best = (float(s[k]), float(a), float(offsets[k]))
    return best[1], best[2]


def _structure(bars: list[BarTiming], durations, env, peaks, strong, anchor: float | None):
    """Alignement mesure par mesure sur tout l'audio (programmation dynamique).

    bars : mesures de la tablature à partir de la première jouée ; durations : leur durée attendue (s).
    env : enveloppe lissée et plafonnée ; peaks : attaques ; strong : attaques franches (pour le début et la fin).
    Renvoie une liste ordonnée d'éléments ("bar", i, début, fin), ("gap", nb, début, fin) ou ("removed", i).
    """
    np = _numpy()
    step = STRUCT_STEP
    # la dernière mesure peut dépasser la fin de l'audio (son du crash final coupé, fondu...)
    n = int((len(env) / FPS + max(durations) * (1 + STRUCT_JUMP)) / step) + 2
    grid = np.arange(n) * step
    hit = float(np.median(_at(env, peaks))) if len(peaks) else 1.0  # valeur typique d'une note bien placée
    penalty = EDIT_PENALTY * hit
    unexplained = UNEXPLAINED_COST * hit

    best = -unexplained * np.searchsorted(strong, grid - 0.15)  # attaques franches laissées avant le début
    if anchor is not None and bars[0].positions:
        first_note = grid + bars[0].positions[0] * durations[0]
        best = np.where(np.abs(first_note - anchor) <= 0.3, best, -np.inf)

    count = len(bars)
    kind = np.zeros((count, n), dtype=np.int8)  # 1 = mesure jouée, 0 = mesure retirée
    origin = np.zeros((count, n), dtype=np.int32)  # début de la mesure (indice de grille)
    gap_size = np.zeros((count, n), dtype=np.int8)  # mesures insérées juste avant (0 = aucune)
    gap_origin = np.zeros((count, n), dtype=np.int32)
    index = np.arange(n)

    for i, (bar, expected) in enumerate(zip(bars, durations)):
        start = best.copy()
        gaps = np.zeros(n, dtype=np.int8)
        sources = index.copy()
        if i >= NO_INSERT_BARS:
            for g in range(1, MAX_INSERT + 1):
                shift = int(round(g * expected / step))
                if shift >= n:
                    break
                cand = np.full(n, -np.inf)
                cand[shift:] = best[:-shift] - g * penalty
                better = cand > start
                start = np.where(better, cand, start)
                gaps[better] = g
                sources[better] = index[better] - shift
        gap_size[i], gap_origin[i] = gaps, sources

        new = start - penalty  # mesure retirée : elle ne prend pas de temps
        bar_kind = np.zeros(n, dtype=np.int8)
        bar_origin = index.copy()
        lo = max(1, int(expected * (1 - STRUCT_JUMP) / step))
        hi = int(expected * (1 + STRUCT_JUMP) / step) + 1
        for d in range(lo, hi + 1):
            if d >= n:
                break
            seconds = d * step
            score = start - STRUCT_STIFFNESS * (seconds / expected - 1) ** 2
            for f in bar.positions:
                score = score + _at(env, grid + f * seconds)
            cand = np.full(n, -np.inf)
            cand[d:] = score[:-d]
            better = cand > new
            new = np.where(better, cand, new)
            bar_kind[better] = 1
            bar_origin[better] = index[better] - d
        kind[i], origin[i] = bar_kind, bar_origin
        best = new

    after = len(strong) - np.searchsorted(strong, grid + 1.0)  # attaques franches laissées après la fin
    pos = int(np.argmax(best - unexplained * after))
    plan = []
    for i in range(count - 1, -1, -1):
        begin = int(origin[i][pos])
        if kind[i][pos] == 1:
            plan.append(("bar", i, begin * step, pos * step))
        else:
            plan.append(("removed", i))
        pos = begin
        g = int(gap_size[i][pos])
        if g:
            source = int(gap_origin[i][pos])
            plan.append(("gap", g, source * step, pos * step))
            pos = source
    plan.reverse()
    return plan


class _Segments:
    """Découpage d'une suite de mesures en segments (mesures entières ou temps) sur lesquels on suit le tempo."""

    def __init__(self, bars: list[BarTiming], per_bar: int):
        np = _numpy()
        self.per_bar = per_bar
        self.positions: list = []  # par segment : position des notes (0..1 dans le segment)
        self.quarters: list[float] = []
        self.parts: list[int] = []  # nombre de segments de chaque mesure
        coords = [0.0]  # abscisse de chaque frontière : indice de mesure + fraction
        for i, bar in enumerate(bars):
            length_quarters = bar.length_ticks / PPQ
            n = per_bar or max(1, round(length_quarters))
            self.parts.append(n)
            fractions = np.array(bar.positions)
            for k in range(n):
                lo, hi = k / n, (k + 1) / n
                inside = fractions[(fractions >= lo) & (fractions < hi)] if k < n - 1 else fractions[fractions >= lo]
                self.positions.append((inside - lo) * n)
                self.quarters.append(length_quarters / n)
                coords.append(i + (k + 1) / n)
        self.coords = np.array(coords)

    def resample(self, bounds, previous: _Segments):
        """Frontières de ce découpage déduites de celles d'un autre découpage (interpolation)."""
        return _numpy().interp(self.coords, previous.coords, bounds)

    def bar_slices(self):
        """(indice du premier segment, nombre de segments) pour chaque mesure."""
        index = 0
        for n in self.parts:
            yield index, n
            index += n


def _track(seg: _Segments, env, prior, stage: Stage):
    """Viterbi sur les frontières des segments ; renvoie les instants (s) de chaque frontière."""
    np = _numpy()
    n = len(seg.positions)
    ks = np.arange(-stage.window, stage.window + 1)
    cands = prior[:, None] + ks[None, :] * stage.step  # (n + 1, m)
    m = len(ks)

    best = np.zeros(m)
    back = np.zeros((n, m), dtype=int)
    for i in range(n):
        start = cands[i][:, None]
        dur = cands[i + 1][None, :] - start
        prior_dur = prior[i + 1] - prior[i]
        rel = dur / prior_dur - 1
        score = -stage.stiffness * rel**2
        for f in seg.positions[i]:
            score = score + _at(env, start + f * dur)
        valid = np.abs(rel) <= stage.jump
        if i == 0:
            valid = valid & (start >= 0)  # la batterie ne peut pas commencer avant l'audio
        score = np.where(valid, score, -np.inf)
        total = best[:, None] + score
        back[i] = total.argmax(axis=0)
        best = total.max(axis=0)

    path = np.zeros(n + 1, dtype=int)
    path[n] = int(best.argmax())
    for i in range(n - 1, -1, -1):
        path[i] = back[i][path[i + 1]]
    return cands[np.arange(n + 1), path]


def _note_times(seg: _Segments, bounds, first: int = 0, count: int | None = None):
    """Instants des notes des segments [first, first + count)."""
    np = _numpy()
    last = len(seg.positions) if count is None else first + count
    return np.array([bounds[i] + f * (bounds[i + 1] - bounds[i]) for i in range(first, last) for f in seg.positions[i]])


def _ranges(numbers: list[int]) -> list[tuple[int, int]]:
    """[3, 4, 5, 9] -> [(3, 5), (9, 9)]"""
    out: list[tuple[int, int]] = []
    for k in numbers:
        if out and k == out[-1][1] + 1:
            out[-1] = (out[-1][0], k)
        else:
            out.append((k, k))
    return out


def calibrate(
    chart: Chart,
    audio_paths: list[str],
    mix_paths: list[str] | None = None,
    progress=None,
    drums_start: float | None = None,
    per_beat: bool = False,
    separation: str = "standard",
) -> Calibration:
    """chart : chart construit avec le tempo de la tablature (offset 0). Renvoie structure, début et tempos.

    audio_paths : pistes de batterie seule. mix_paths : mix complets dont la batterie est d'abord isolée
    (Demucs) ; ignorés si des pistes de batterie sont fournies. progress(message) : suivi pour l'interface.
    drums_start : instant (s) de la première note de batterie dans l'audio, si on veut l'imposer.
    per_beat : un tempo par temps au lieu d'un tempo par mesure.
    """
    np = _numpy()
    stages = BEAT_STAGES if per_beat else STAGES
    if not chart.bars or not any(b.positions for b in chart.bars):
        raise ValueError(tr("Aucune note à caler"))

    if audio_paths:
        sources = [decode_audio(path) for path in audio_paths]
    else:
        from . import separate

        sources = [separate.drums(path, progress, separation) for path in mix_paths or []]
    if not sources:
        raise ValueError(tr("Aucun audio fourni"))
    if progress:
        progress(tr("Analyse des attaques de l'audio..."))
    env = None
    for samples in sources:
        e = onset_envelope(samples)
        env = e if env is None else _pad_add(env, e)
    peaks = _peaks(env)
    attack = first_attack(env, peaks)
    coarse = _capped(env, stages[0].sigma, peaks)

    # Les mesures d'intro sans batterie n'apportent rien au calage : on part de la première mesure jouée.
    first = next(i for i, bar in enumerate(chart.bars) if bar.positions)
    bounds_ticks = [bar.start_tick for bar in chart.bars] + [chart.bars[-1].start_tick + chart.bars[-1].length_ticks]
    nominal_all = np.array([chart.ticks_to_ms(t) / 1000 for t in bounds_ticks])
    nominal = nominal_all[first:] - nominal_all[first]
    tab_bars = chart.bars[first:]
    tab_seg = _Segments(tab_bars, 1)
    a, b = _tempo_ratio(_note_times(tab_seg, nominal), coarse)
    hit_before = _hit_rate(_note_times(tab_seg, a * nominal + b), peaks)

    if progress:
        progress(tr("Alignement de la structure (mesures ajoutées ou absentes)..."))
    plan = _structure(tab_bars, np.diff(nominal) * a, coarse, peaks, _strong_attacks(env, peaks), drums_start)

    # Mesures du chart après la première mesure jouée : mesures de la tablature et mesures insérées (vides)
    layout: list[int | None] = []
    virtual: list[BarTiming] = []
    starts: list[float] = []
    inserted, removed = [], []
    end = 0.0
    for k, item in enumerate(plan):
        if item[0] == "bar":
            _, i, begin, end = item
            layout.append(first + i)
            virtual.append(tab_bars[i])
            starts.append(begin)
        elif item[0] == "gap":
            _, g, begin, end = item
            following = next((it[1] for it in plan[k + 1 :] if it[0] == "bar"), None)
            model = tab_bars[following] if following is not None else virtual[-1]
            inserted.append((g, first + (following if following is not None else len(tab_bars)) + 1))
            for j in range(g):
                layout.append(None)
                virtual.append(BarTiming(0, model.length_ticks, []))
                starts.append(begin + j * (end - begin) / g)
        else:
            removed.append(first + item[1] + 1)
    bounds = np.array(starts + [end])

    seg = _Segments(virtual, 1)
    for stage in stages:
        if progress and stage is stages[1]:
            progress(tr("Affinage du tempo temps par temps...") if per_beat else tr("Affinage du tempo mesure par mesure..."))
        new_seg = _Segments(virtual, stage.per_bar) if stage.per_bar != seg.per_bar else seg
        bounds = new_seg.resample(bounds, seg)
        seg = new_seg
        smooth = _smooth(env, stage.sigma)  # sans plafond : le sommet des pics sert à localiser l'attaque
        for _ in range(stage.passes):
            bounds = _track(seg, smooth, bounds, stage)

    tempos = []
    weak = []
    for k, (index, n) in enumerate(seg.bar_slices()):
        durations = np.diff(bounds[index : index + n + 1])
        tempos.append((np.array(seg.quarters[index : index + n]) * 60 / durations).tolist())
        times = _note_times(seg, bounds, index, n)
        if layout[k] is not None and len(times) >= 3 and _hit_rate(times, peaks) < 0.5:
            weak.append(layout[k] + 1)

    # Intro sans batterie : replacée devant, au tempo de la tablature (mis à l'échelle) ;
    # si elle est plus longue que le début de l'audio, on retire ses premières mesures.
    intro = np.diff(nominal_all[: first + 1]) * a
    skip = 0
    while skip < first and intro[skip:].sum() > bounds[0] + 1e-6:
        skip += 1
    intro_tempos = [[bar.length_ticks / PPQ * 60 / d] for bar, d in zip(chart.bars[skip:first], intro[skip:])]

    times = _note_times(seg, bounds)
    return Calibration(
        offset_ms=float((bounds[0] - intro[skip:].sum()) * 1000),
        layout=list(range(skip, first)) + layout,
        tempos=intro_tempos + tempos,
        first_note_ms=float(times.min() * 1000),
        first_attack_ms=None if attack is None else attack * 1000,
        hit_rate_before=hit_before,
        hit_rate_after=_hit_rate(times, peaks),
        close_rate_after=_hit_rate(times, peaks, CLOSE_TOLERANCE),
        audio_ms=len(env) / FPS * 1000,
        weak_bars=_ranges(weak),
        inserted=inserted,
        removed=removed,
        intro_removed=skip,
    )


def _pad_add(a, b):
    np = _numpy()
    n = max(len(a), len(b))
    out = np.zeros(n, dtype=np.float32)
    out[: len(a)] += a
    out[: len(b)] += b
    return out
