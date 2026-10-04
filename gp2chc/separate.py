"""Isole la batterie d'un mix complet avec Demucs (facultatif : pip install demucs)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import align, deps
from .i18n import tr

MODEL = "htdemucs"
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


def _cache_file(path: Path) -> Path:
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{MODEL}"
    return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".npy")


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
    drums = sources[model.sources.index("drums")] * (reference.std() + 1e-8)

    mono = drums.mean(0).cpu().numpy().astype(np.float32)
    step = model.samplerate // align.SR
    usable = len(mono) // step * step
    return mono[:usable].reshape(-1, step).mean(axis=1)  # ré-échantillonnage simple vers align.SR


def drums(path: str, progress=None):
    """Batterie isolée du mix `path`, mono à align.SR. Le résultat est mis en cache pour les conversions suivantes."""
    np = align._numpy()
    source = Path(path)
    cache = _cache_file(source)
    if cache.exists():
        if progress:
            progress(tr("Batterie déjà isolée précédemment (cache)."))
        return np.load(cache)
    samples = _separate(source, progress)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, samples)
    return samples
