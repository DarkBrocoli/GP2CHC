"""Isole la batterie d'un mix complet avec Demucs (facultatif : pip install demucs).

Produit, en cache : la batterie en mono pour le calage, et en pleine qualité (FLAC stéréo) la batterie seule
et le reste du morceau (mix moins la batterie), pour les pistes audio du dossier Clone Hero."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from . import align, deps
from .i18n import tr

MODEL = "htdemucs"
# Qualité de séparation -> modèle : Demucs (inclus, ~1 min par morceau) ou BS-RoFormer (téléchargé, plus lent)
QUALITIES = {"standard": MODEL, "hq": "bs_roformer_sw"}


def _cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if not base:
        try:
            base = str(Path.home() / ".cache")
        except RuntimeError:  # pas de dossier utilisateur
            import tempfile

            base = tempfile.gettempdir()
    return Path(base) / "gp2chc" / "cache"


CACHE_DIR = _cache_dir()


def _cache_base(path: Path, quality: str = "standard") -> Path:
    """Préfixe des fichiers en cache d'un mix : nom, taille et date du fichier (pas son dossier, pour qu'un mix
    rangé dans gp2chc_original_audio garde son cache), et modèle utilisé."""
    stat = path.stat()
    key = f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}|{QUALITIES[quality]}"
    return CACHE_DIR / hashlib.sha1(key.encode("utf-8")).hexdigest()


def _cache_files(path: Path, quality: str = "standard") -> tuple[Path, Path, Path]:
    """(batterie mono pour le calage, batterie stéréo FLAC, reste du morceau FLAC)."""
    base = _cache_base(path, quality)
    return base.with_suffix(".npy"), base.with_name(base.name + "_drums.flac"), base.with_name(base.name + "_rest.flac")


def _write_flac(path: Path, channels_first, rate: int) -> None:
    """Écrit un tableau (canaux, échantillons) en FLAC 24 bits avec ffmpeg."""
    np = align._numpy()
    data = np.ascontiguousarray(np.clip(channels_first, -1.0, 1.0).T, dtype=np.float32)
    cmd = [deps.ffmpeg() or "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", str(data.shape[1]),
           "-i", "-", "-c:a", "flac", "-sample_fmt", "s32", str(path)]  # fmt: skip
    run = subprocess.run(cmd, input=data.tobytes(), capture_output=True, creationflags=deps.NO_WINDOW)
    if run.returncode != 0:
        raise ValueError(tr("Impossible de lire l'audio '{path}' : {error}", path=path, error=run.stderr.decode(errors="replace").strip()))


def _load_model(progress):
    """Modèle htdemucs : celui inclus dans l'exécutable autonome (sans réseau), sinon téléchargé une fois."""
    included = deps.bundled(f"models/{MODEL}")
    if included:
        import yaml
        from demucs.apply import BagOfModels
        from demucs.hf import load_safetensors_model

        bag = yaml.safe_load((included / f"{MODEL}.yaml").read_text(encoding="utf-8"))
        models = [load_safetensors_model(included / f"{sig}.safetensors") for sig in bag["models"]]
        return BagOfModels(models, bag.get("weights"), bag.get("segment"))

    from demucs.pretrained import get_model

    if progress:
        progress(tr("Chargement du modèle Demucs (premier lancement : téléchargement d'environ 80 Mo)..."))
    return get_model(MODEL)


def _separate_hq(path: Path, progress):
    """BS-RoFormer SW : meilleure séparation de la batterie (cymbales comprises) que Demucs, mais plus lente."""
    from . import models

    np = align._numpy()
    try:
        import torch  # noqa: F401
    except ImportError as e:
        raise ValueError(
            tr(
                "La séparation de la batterie demande Demucs : pip install demucs "
                "(ou fournissez directement les pistes de batterie seule)"
            )
        ) from e
    audio = align.decode_audio(str(path), models.SAMPLE_RATE, 2).T  # (canaux, échantillons)
    if progress:
        progress(tr("Séparation haute qualité de la batterie (BS-RoFormer) : environ 2 min de calcul par minute de musique..."))
    drums = models.run("bs_roformer_sw", audio, progress)["drums"]
    return _analysis(drums, models.SAMPLE_RATE), drums, audio - drums, models.SAMPLE_RATE


def _analysis(drums, rate: int):
    """Batterie stéréo -> mono à align.SR, pour le calage."""
    np = align._numpy()
    mono = drums.mean(0).astype(np.float32)
    step = rate // align.SR
    usable = len(mono) // step * step
    return mono[:usable].reshape(-1, step).mean(axis=1)  # ré-échantillonnage simple vers align.SR


def _separate(path: Path, progress):
    np = align._numpy()
    # Avertissements sans conséquence du téléchargement du modèle (jeton absent, liens symboliques sous Windows)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    try:
        import torch
        from demucs.apply import apply_model
    except ImportError as e:
        raise ValueError(
            tr(
                "La séparation de la batterie demande Demucs : pip install demucs "
                "(ou fournissez directement les pistes de batterie seule)"
            )
        ) from e

    try:
        from huggingface_hub.utils import logging as hf_logging

        hf_logging.set_verbosity_error()  # déjà importé avant nos variables d'environnement : on force le niveau
    except ImportError:
        pass
    model = _load_model(progress)
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"

    audio = align.decode_audio(str(path), model.samplerate, model.audio_channels)  # (échantillons, canaux)
    wav = torch.from_numpy(np.ascontiguousarray(audio.T))
    reference = wav.mean(0)
    wav = (wav - reference.mean()) / (reference.std() + 1e-8)

    if progress:
        if device == "cuda":
            progress(tr("Séparation de la batterie (carte graphique), cela peut prendre quelques minutes..."))
        else:
            progress(tr("Séparation de la batterie (processeur), cela peut prendre quelques minutes..."))
    with torch.no_grad():
        sources = apply_model(model, wav[None], device=device, split=True, overlap=0.25, progress=False)[0]
    drums = (sources[model.sources.index("drums")] * (reference.std() + 1e-8)).cpu().numpy()
    rest = audio.T - drums  # le morceau sans la batterie : mix d'origine moins la batterie isolée
    return _analysis(drums, model.samplerate), drums, rest, model.samplerate


def _run(source: Path, progress, quality: str = "standard"):
    """Sépare le mix et range les trois résultats en cache ; renvoie la batterie mono pour le calage."""
    np = align._numpy()
    analysis_file, drums_file, rest_file = _cache_files(source, quality)
    analysis, drums_audio, rest, rate = (_separate_hq if quality == "hq" else _separate)(source, progress)
    analysis_file.parent.mkdir(parents=True, exist_ok=True)
    _write_flac(drums_file, drums_audio, rate)
    _write_flac(rest_file, rest, rate)
    np.save(analysis_file, analysis)
    return analysis


def drums(path: str, progress=None, quality: str = "standard"):
    """Batterie isolée du mix `path`, mono à align.SR. Le résultat est mis en cache pour les conversions suivantes."""
    np = align._numpy()
    source = Path(path)
    analysis_file, drums_file, rest_file = _cache_files(source, quality)
    if analysis_file.exists() and drums_file.exists() and rest_file.exists():
        if progress:
            progress(tr("Batterie déjà isolée précédemment (cache)."))
        return np.load(analysis_file)
    return _run(source, progress, quality)


def stems(path: str, progress=None, quality: str = "standard") -> tuple[Path, Path]:
    """(batterie seule, morceau sans la batterie) du mix `path`, en FLAC stéréo (séparation faite si besoin)."""
    source = Path(path)
    _, drums_file, rest_file = _cache_files(source, quality)
    if not (drums_file.exists() and rest_file.exists()):
        _run(source, progress, quality)
    return drums_file, rest_file
