"""Mode « sans tablature » : construit les notes de batterie par reconnaissance de l'audio (expérimental).

1. la batterie est découpée en 6 instruments par DrumSep (grosse caisse, caisse claire, toms, charley, ride, crash) ;
2. les frappes de chaque instrument sont détectées (flux spectral) ; les toms sont classés aigu / medium / basse ;
3. les temps sont suivis (tempo estimé puis programmation dynamique) et les débuts de mesure choisis (4/4) ;
4. chaque frappe est placée sur la grille la plus probable de son temps (doubles croches, triolets, triples croches).

Le résultat est une « tablature » (model.Score) que la suite du programme traite comme une vraie.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import align, models, separate
from .i18n import tr
from .model import Bar, DrumNote, Score

RATE = models.SAMPLE_RATE
HOP = 256
N_FFT = 2048
FPS = RATE / HOP
STEMS = ["kick", "snare", "toms", "hh", "ride", "crash"]
# Seuil de détection par instrument (fraction du 99e centile du flux au-dessus de la médiane locale)
# (réglés sur un morceau de référence dont on a la tablature calée ; écarts minimaux assez courts pour la
# double pédale et les roulements)
DELTA = {"kick": 0.3, "snare": 0.4, "toms": 0.5, "hh": 0.3, "ride": 0.3, "crash": 0.5}
MIN_GAP = {"kick": 0.05, "snare": 0.05, "toms": 0.04, "hh": 0.05, "ride": 0.06, "crash": 0.12}
LOW_RATE = 2205  # piste des toms ré-échantillonnée pour mesurer la hauteur de chaque coup
KICK_BLEED_HZ = 85  # un « tom » plus grave que ça, en même temps qu'une grosse caisse, est de la grosse caisse
MIN_TOM_STEP = 2.5  # demi-tons minimum entre deux toms distincts
PITCH = {"kick": 36, "snare": 38, "hh": 42, "ride": 51, "crash": 49}
TOM_PITCHES = {3: [48, 45, 43], 2: [48, 43], 1: [45]}  # du plus aigu au plus grave
GRIDS = [(4, 0.0), (3, 0.002), (2, 0.0), (6, 0.006), (8, 0.008)]  # (divisions du temps, coût de complexité)


@dataclass
class Transcription:
    score: Score
    bar_tempos: list[list[float]]  # tempo de chaque temps, par mesure
    offset_ms: float  # début de la première mesure dans l'audio
    counts: dict[str, int]
    tempo: float


# ---- analyse -------------------------------------------------------------------------------------------------


def _flux(x, floor_db: float = 60.0):
    """Flux spectral positif (log, plancher relatif au maximum de la piste) et centroïde grave (pour les toms)."""
    np = align._numpy()
    mono = x.mean(0) if x.ndim == 2 else x
    pad = np.concatenate([np.zeros(N_FFT // 2, np.float32), mono.astype(np.float32), np.zeros(N_FFT // 2, np.float32)])
    frames = np.lib.stride_tricks.sliding_window_view(pad, N_FFT)[::HOP]
    window = np.hanning(N_FFT).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1 / RATE)
    low = (freqs >= 50) & (freqs <= 700)
    flux, centroid = [], []
    previous, top = None, None
    for start in range(0, len(frames), 2048):
        mag = np.abs(np.fft.rfft(frames[start : start + 2048] * window, axis=1))
        if top is None:
            top = float(np.abs(np.fft.rfft(frames[:: max(1, len(frames) // 4000)] * window, axis=1)).max()) + 1e-12
        logmag = 20 * np.log10(np.maximum(mag / top, 10 ** (-floor_db / 20)))
        stacked = logmag if previous is None else np.vstack([previous, logmag])
        diff = np.maximum(0, np.diff(stacked, axis=0)).mean(1)
        flux.append(diff if previous is not None else np.concatenate([[0.0], diff]))
        energy = mag[:, low] ** 2
        centroid.append((energy * freqs[low]).sum(1) / (energy.sum(1) + 1e-12))
        previous = logmag[-1:]
    return np.concatenate(flux), np.concatenate(centroid)


def _peaks(env, delta: float, gap: float):
    """Maxima locaux au-dessus de la médiane locale (0,5 s) + delta * 99e centile ; un seul pic par `gap`."""
    np = align._numpy()
    k = int(0.5 * FPS) | 1
    padded = np.pad(env, (k // 2, k // 2), mode="edge")
    median = np.median(np.lib.stride_tricks.sliding_window_view(padded, k)[:: 4], axis=1)
    median = np.interp(np.arange(len(env)), np.arange(0, len(env), 4)[: len(median)], median)
    threshold = median + delta * (np.percentile(env, 99) + 1e-9)
    inner = env[1:-1]
    candidates = np.nonzero((inner > threshold[1:-1]) & (inner >= env[:-2]) & (inner > env[2:]))[0] + 1
    out: list[int] = []
    for i in candidates:
        if out and (i - out[-1]) / FPS < gap:
            if env[i] > env[out[-1]]:
                out[-1] = i
            continue
        out.append(int(i))
    return np.array(out, dtype=int)


def _cache_file(drums_audio: Path) -> Path:
    stat = drums_audio.stat()
    key = f"{drums_audio.name}|{stat.st_size}|{stat.st_mtime_ns}|drumsep"
    return separate.CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + "_drumsep.npz")


def _low(x):
    """Piste mono ramenée à LOW_RATE (filtre passe-bas par FFT), pour la hauteur des toms."""
    np = align._numpy()
    mono = (x.mean(0) if x.ndim == 2 else x).astype(np.float64)
    spectrum = np.fft.rfft(mono)
    spectrum[np.fft.rfftfreq(len(mono), 1 / RATE) > LOW_RATE * 0.45] = 0
    step = RATE // LOW_RATE
    return np.fft.irfft(spectrum, len(mono))[::step].astype(np.float32)


def features(stems: dict) -> dict:
    """Courbes d'attaque des 6 instruments, piste grave des toms, et attaque globale (pour le tempo)."""
    np = align._numpy()
    data = {}
    for name in STEMS:
        data[f"{name}_flux"] = _flux(stems[name])[0].astype(np.float32)
    data["toms_low"] = _low(stems["toms"])
    data["all_flux"] = sum(data[f"{n}_flux"] / (np.percentile(data[f"{n}_flux"], 99) + 1e-9) for n in STEMS).astype(np.float32)
    return data


def analyse(drums_audio: Path, progress=None) -> dict:
    """Courbes d'attaque des 6 instruments (et centroïde des toms), mises en cache pour ce fichier de batterie."""
    np = align._numpy()
    cache = _cache_file(drums_audio)
    if cache.exists():
        if progress:
            progress(tr("Instruments de la batterie déjà séparés précédemment (cache)."))
        return dict(np.load(cache))
    audio = align.decode_audio(str(drums_audio), RATE, 2).T
    if progress:
        progress(tr("Séparation de la batterie en 6 instruments (DrumSep) : environ 1 min de calcul par minute de musique..."))
    stems = models.run("drumsep", audio, progress)
    if progress:
        progress(tr("Détection des frappes..."))
    data = features(stems)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **data)
    return data


def _near(times, others, tolerance: float):
    np = align._numpy()
    if not len(others) or not len(times):
        return np.zeros(len(times), dtype=bool)
    i = np.clip(np.searchsorted(others, times), 1, len(others) - 1)
    return np.minimum(np.abs(others[i] - times), np.abs(others[i - 1] - times)) < tolerance


def _tom_hz(low, t: float) -> float:
    """Hauteur d'un coup de tom : pic du spectre entre 60 et 400 Hz, juste après l'attaque."""
    np = align._numpy()
    n = 2048
    start = int((t + 0.01) * LOW_RATE)
    segment = low[start : start + n]
    if len(segment) < n:
        segment = np.pad(segment, (0, n - len(segment)))
    freqs = np.fft.rfftfreq(n, 1 / LOW_RATE)
    band = (freqs >= 60) & (freqs <= 400)
    return float(freqs[band][np.argmax(np.abs(np.fft.rfft(segment * np.hanning(n)))[band])])


def onsets(data: dict) -> dict[str, list[tuple[float, int]]]:
    """{instrument: [(instant en s, pitch GM)]} ; les toms sont répartis par hauteur."""
    np = align._numpy()
    frames = {name: _peaks(data[f"{name}_flux"], DELTA[name], MIN_GAP[name]) for name in STEMS}
    times = {name: f / FPS for name, f in frames.items()}

    # charley et ride au même instant : DrumSep dédouble souvent la ride, on garde le plus net des deux
    strength = {n: data[f"{n}_flux"] / (np.percentile(data[f"{n}_flux"], 99) + 1e-9) for n in ("hh", "ride")}
    hh_both = _near(times["hh"], times["ride"], 0.03)
    ride_both = _near(times["ride"], times["hh"], 0.03)
    keep_hh = ~hh_both | np.array([strength["hh"][f] > strength["ride"][min(f, len(strength["ride"]) - 1)] for f in frames["hh"]], dtype=bool)
    keep_ride = ~ride_both | np.array([strength["ride"][f] >= strength["hh"][min(f, len(strength["hh"]) - 1)] for f in frames["ride"]], dtype=bool)
    times["hh"], times["ride"] = times["hh"][keep_hh], times["ride"][keep_ride]

    found = {name: [(float(t), PITCH[name]) for t in times[name]] for name in STEMS if name != "toms"}
    # toms : hauteur de chaque coup ; les coups très graves sur une grosse caisse sont de la grosse caisse
    hz = np.array([_tom_hz(data["toms_low"], t) for t in times["toms"]])
    real = ~((hz < KICK_BLEED_HZ) & _near(times["toms"], times["kick"], 0.02))
    found["toms"] = list(zip(times["toms"][real].tolist(), _tom_pitches(hz[real])))
    return found


def _tom_pitches(hz) -> list[int]:
    """Regroupe les hauteurs de tom en 3, 2 ou 1 toms (k-moyennes sur l'échelle des demi-tons), du plus aigu
    au plus grave ; on garde le plus de toms possible tant qu'ils sont bien distincts."""
    np = align._numpy()
    if not len(hz):
        return []
    semis = 12 * np.log2(np.maximum(hz, 30.0))
    for k in (3, 2):
        if len(semis) < 3 * k:
            continue
        centers = np.quantile(semis, np.linspace(0.15, 0.85, k))
        for _ in range(50):
            label = np.argmin(np.abs(semis[:, None] - centers[None, :]), axis=1)
            centers = np.array([semis[label == j].mean() if (label == j).any() else centers[j] for j in range(k)])
        sizes = np.bincount(label, minlength=k) / len(semis)
        if np.all(np.diff(np.sort(centers)) >= MIN_TOM_STEP) and sizes.min() >= 0.05:
            rank = np.argsort(np.argsort(-centers))  # 0 = plus aigu
            return [TOM_PITCHES[k][rank[j]] for j in label]
    return [TOM_PITCHES[1][0]] * len(semis)


# ---- temps et mesures ----------------------------------------------------------------------------------------


def _tempo(env) -> float:
    """Période (en trames) la plus probable entre 70 et 200 BPM, autocorrélation pondérée autour de 120 BPM."""
    np = align._numpy()
    x = env - env.mean()
    spectrum = np.fft.rfft(x, 2 * len(x))
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[: len(x)]
    lags = np.arange(int(FPS * 60 / 200), int(FPS * 60 / 70) + 1)
    bpm = 60 * FPS / lags
    weight = np.exp(-0.5 * (np.log2(bpm / 120) / 0.9) ** 2)
    return float(lags[np.argmax(ac[lags] * weight)])


def _beats(env, period: float, tightness: float = 100.0):
    """Suivi des temps (Ellis 2007) : maximise l'attaque aux temps en gardant des intervalles proches de la période."""
    np = align._numpy()
    local = env / (env.std() + 1e-9)
    n = len(local)
    score = local.copy()
    back = np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offsets = np.arange(lo, hi + 1)
    penalty = -tightness * np.log(offsets / period) ** 2
    for t in range(lo, n):
        prev = t - offsets
        ok = prev >= 0
        cand = score[prev[ok]] + penalty[ok]
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[t] = local[t] + cand[k]
            back[t] = prev[ok][k]
    end = int(np.argmax(score[max(0, n - int(period * 2)) :])) + max(0, n - int(period * 2))
    beats = [end]
    while back[beats[-1]] >= 0:
        beats.append(int(back[beats[-1]]))
    return np.array(beats[::-1]) / FPS


def _downbeat_phase(beats, hits: dict[str, list[tuple[float, int]]]) -> int:
    """Phase (0..3) des débuts de mesure : caisse claire sur le 2 et le 4, grosse caisse et crash plutôt sur le 1."""
    np = align._numpy()

    def near(kind):
        times = np.array([t for t, _ in hits.get(kind, [])])
        if not len(times):
            return np.zeros(len(beats))
        i = np.clip(np.searchsorted(times, beats), 1, len(times) - 1)
        return (np.minimum(np.abs(times[i] - beats), np.abs(times[i - 1] - beats)) < 0.07).astype(float)

    kick, snare, crash = near("kick"), near("snare"), near("crash")
    position = np.arange(len(beats))
    scores = []
    for phase in range(4):
        beat = (position - phase) % 4
        # caisse claire sur 2 et 4 (la règle la plus sûre), puis grosse caisse et crash sur le 1 (moins sûrs)
        scores.append(
            2 * snare[(beat == 1) | (beat == 3)].sum() - 2 * snare[(beat == 0) | (beat == 2)].sum()
            + 0.5 * kick[beat == 0].sum() + 0.5 * crash[beat == 0].sum()
        )
    return int(np.argmax(scores))


def _snap(fractions) -> list[Fraction]:
    """Positions (0..1) dans un temps -> positions sur la grille la plus probable (16es, triolets, 32es...)."""
    best = None
    for div, cost in GRIDS:
        snapped = [Fraction(round(f * div), div) for f in fractions]
        error = sum((float(s) - f) ** 2 for s, f in zip(snapped, fractions)) / max(len(fractions), 1) + cost
        if best is None or error < best[0] - 1e-12:
            best = (error, snapped)
    return best[1]


def transcribe(drums_audio: Path, per_beat: bool = False, progress=None) -> Transcription:
    """Notes de batterie reconnues dans `drums_audio` (batterie seule, 44,1 kHz ou autre)."""
    np = align._numpy()
    data = analyse(drums_audio, progress)
    hits = onsets(data)
    if progress:
        progress(tr("Recherche du tempo et des mesures..."))
    env = data["all_flux"]
    period = _tempo(env)
    beats = _beats(env, period)
    if len(beats) < 8:
        raise ValueError(tr("Pas assez de batterie dans l'audio pour reconnaître le rythme"))

    phase = _downbeat_phase(beats, hits)
    if phase:
        # premiers temps avant le premier début de mesure : on complète cette mesure vers l'arrière au même
        # tempo si l'audio le permet, sinon on la laisse de côté
        step = float(np.median(np.diff(beats[:8])))
        missing = beats[0] - step * np.arange(4 - phase, 0, -1)
        beats = np.concatenate([missing, beats]) if missing[0] >= 0 else beats[phase:]
    usable = (len(beats) - 1) // 4 * 4
    beats = beats[: usable + 1]

    # notes de chaque temps, placées sur la grille
    events = sorted((t, pitch) for notes in hits.values() for t, pitch in notes)
    bars = [Bar(numerator=4, denominator=4) for _ in range(usable // 4)]
    per_beat_notes: dict[int, list[tuple[float, int]]] = {}
    for t, pitch in events:
        k = int(np.searchsorted(beats, t, side="right")) - 1
        if 0 <= k < usable:
            per_beat_notes.setdefault(k, []).append(((t - beats[k]) / (beats[k + 1] - beats[k]), pitch))
    seen = set()
    for k, notes in per_beat_notes.items():
        for (fraction, pitch), snapped in zip(notes, _snap([f for f, _ in notes])):
            index = k + int(snapped)  # 1.0 -> début du temps suivant
            if index >= usable:
                continue
            pos = Fraction(index % 4) + (snapped - int(snapped))
            key = (index // 4, pos, pitch)
            if key not in seen:
                seen.add(key)
                bars[index // 4].notes.append(DrumNote(pos, pitch))

    durations = np.diff(beats)
    if per_beat:
        tempos = [list(60 / durations[4 * b : 4 * b + 4]) for b in range(len(bars))]
    else:
        tempos = [[240 / float(durations[4 * b : 4 * b + 4].sum())] for b in range(len(bars))]
    counts = {name: len(found) for name, found in hits.items()}
    score = Score(title="", artist="", album="", bars=bars, track_name=tr("Batterie reconnue dans l'audio"))
    return Transcription(score, tempos, float(beats[0] * 1000), counts, 60 * FPS / period)
