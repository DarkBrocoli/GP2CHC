"""Modèles de séparation téléchargés à la première utilisation, et leur inférence.

- « bs_roformer_sw » : BS-RoFormer SW (jarredou), 6 pistes dont la batterie. Meilleure séparation de la batterie
  que Demucs, mais plus lent ; sert à la séparation « haute qualité ».
- « drumsep » : MDX23C DrumSep (aufr33 & jarredou), découpe une piste de batterie en grosse caisse, caisse claire,
  toms, charley, ride et crash ; sert au mode « sans tablature ».

Les poids viennent de la page de publication de python-audio-separator ; ils ne sont pas inclus dans GP2CHC.
L'inférence reproduit celle d'audio-separator (découpage en fenêtres qui se chevauchent).
"""

from __future__ import annotations

import urllib.request
import warnings
from pathlib import Path
from types import SimpleNamespace

from . import align, separate
from .i18n import tr

SOURCE = "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/"
SAMPLE_RATE = 44100
MODELS = {
    "bs_roformer_sw": {
        "kind": "roformer",
        "config": ("BS-Roformer-SW.yaml", 4653),
        "weights": ("BS-Roformer-SW.ckpt", 699412152),
    },
    "drumsep": {
        "kind": "mdx23c",
        "config": ("config_drumsep_mdx23c.yaml", 2417),
        "weights": ("MDX23C-DrumSep-aufr33-jarredou.ckpt", 437652699),
    },
}
_LOADED: dict[str, tuple] = {}


def folder() -> Path:
    return separate.CACHE_DIR.parent / "models"


def is_downloaded(name: str) -> bool:
    spec = MODELS[name]
    return all((folder() / f).is_file() and (folder() / f).stat().st_size == size for f, size in (spec["config"], spec["weights"]))


def download(name: str, progress=None) -> None:
    """Télécharge les fichiers du modèle s'ils manquent. Un téléchargement interrompu reprend là où il s'était
    arrêté (plusieurs essais) ; la taille est vérifiée avant de garder le fichier."""
    folder().mkdir(parents=True, exist_ok=True)
    for filename, size in (MODELS[name]["config"], MODELS[name]["weights"]):
        target = folder() / filename
        if target.is_file() and target.stat().st_size == size:
            continue
        if progress and size > 1_000_000:
            progress(tr("Téléchargement du modèle {name} ({size} Mo, une seule fois)...", name=filename, size=round(size / 1e6)))
        tmp = target.with_suffix(target.suffix + ".part")
        _fetch(SOURCE + filename, tmp, size, progress if size > 1_000_000 else None)
        if tmp.stat().st_size != size:
            tmp.unlink(missing_ok=True)
            raise ValueError(tr("Téléchargement incomplet ({name}), réessayez", name=filename))
        tmp.replace(target)


def _fetch(url: str, tmp, size: int, progress, attempts: int = 8) -> None:
    error = None
    reported = -1
    for _ in range(attempts):
        done = tmp.stat().st_size if tmp.exists() else 0
        if done >= size:
            return
        request = urllib.request.Request(url, headers={"Range": f"bytes={done}-"} if done else {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if done and response.status != 206:  # le serveur ne reprend pas : on recommence
                    done = 0
                with open(tmp, "ab" if done else "wb") as out:
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        if progress and done * 10 // size > reported:
                            reported = done * 10 // size
                            progress(tr("  {percent} %", percent=min(100, reported * 10)))
        except OSError as e:  # coupure, délai dépassé... : on reprend à la position atteinte
            error = e
    if not tmp.exists() or tmp.stat().st_size < size:
        raise ValueError(tr("Téléchargement impossible ({name}) : {error}", name=tmp.name.removesuffix(".part"), error=error))


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _namespace(v) for k, v in value.items()})
    return value


def _read_config(path: Path) -> dict:
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("tag:yaml.org,2002:python/tuple", lambda loader, node: tuple(loader.construct_sequence(node)))
    return yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)


def load(name: str, progress=None):
    """(modèle PyTorch, configuration) ; téléchargé puis gardé en mémoire."""
    if name in _LOADED:
        return _LOADED[name]
    import torch

    download(name, progress)
    spec = MODELS[name]
    config = _read_config(folder() / spec["config"][0])
    weights = folder() / spec["weights"][0]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if spec["kind"] == "roformer":
            from .nets.bs_roformer import BSRoformer

            model = BSRoformer(**config["model"])
        else:
            from .nets.tfc_tdf_v3 import TFC_TDF_net

            model = TFC_TDF_net(_namespace(config), device=torch.device("cpu"))
        model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
    model.eval()
    _LOADED[name] = (model, config)
    return _LOADED[name]


def instruments(name: str) -> list[str]:
    return list(_read_config(folder() / MODELS[name]["config"][0])["training"]["instruments"])


def run(name: str, mix, progress=None) -> dict:
    """Sépare `mix` (canaux, échantillons) à 44,1 kHz ; renvoie {instrument: (canaux, échantillons)}."""
    np = align._numpy()
    import torch

    model, config = load(name, progress)
    peak = float(np.abs(mix).max()) if mix.size else 0.0
    scale = max(peak / 0.9, 1.0)  # comme audio-separator : le mix est ramené sous 0,9 avant l'inférence
    audio = torch.tensor(mix / scale, dtype=torch.float32)
    names = list(config["training"]["instruments"])
    overlap = int(config.get("inference", {}).get("num_overlap", 2))

    with torch.no_grad(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if MODELS[name]["kind"] == "roformer":
            out = _run_roformer(model, config, audio, overlap, progress)
        else:
            out = _run_mdx23c(model, config, audio, overlap, progress)
    return {key: value.numpy() * scale for key, value in zip(names, out)}


def _report(progress, done: int, total: int, last: list) -> None:
    percent = done * 100 // max(total, 1)
    if progress and percent // 10 > last[0]:
        last[0] = percent // 10
        progress(tr("  {percent} %", percent=percent))


def _run_roformer(model, config, audio, overlap, progress):
    import torch

    chunk = int(config["model"]["stft_hop_length"]) * (int(config["inference"]["dim_t"]) - 1)
    step = chunk // overlap
    length = audio.shape[1]
    starts = []
    for offset in range(0, length, step):
        if offset + chunk >= length:
            tail = max(length - chunk, 0)
            if not starts or starts[-1] != tail:
                starts.append(tail)
            break
        starts.append(offset)
    window = torch.hamming_window(chunk, periodic=False)
    result = torch.zeros((len(config["training"]["instruments"]),) + tuple(audio.shape))
    counter = torch.zeros_like(result)
    last = [0]
    for i, start in enumerate(starts):
        part = audio[:, start : start + chunk]
        x = model(part.unsqueeze(0))[0]
        n = min(part.shape[-1], x.shape[-1], chunk)
        result[..., start : start + n] += x[..., :n] * window[:n]
        counter[..., start : start + n] += window[:n]
        _report(progress, i + 1, len(starts), last)
    return result / counter.clamp(min=1e-10)


def _run_mdx23c(model, config, audio, overlap, progress):
    import torch

    chunk = int(config["audio"]["hop_length"]) * (int(config["inference"]["dim_t"]) - 1)
    hop = chunk // overlap
    length = audio.shape[1]
    pad = hop - (length - chunk) % hop
    padded = torch.cat([torch.zeros(2, chunk - hop), audio, torch.zeros(2, pad + chunk - hop)], 1)
    chunks = padded.unfold(1, chunk, hop).transpose(0, 1)
    stems = len(config["training"]["instruments"])
    acc = torch.zeros(stems, *padded.shape)
    last = [0]
    for i, part in enumerate(chunks):
        acc[..., i * hop : i * hop + chunk] += model(part.unsqueeze(0))[0]
        _report(progress, i + 1, len(chunks), last)
    acc /= overlap
    return acc[..., chunk - hop : -(pad + chunk - hop)]


def remove(name: str) -> None:
    """Supprime les fichiers téléchargés d'un modèle (pour libérer la place)."""
    for filename, _ in (MODELS[name]["config"], MODELS[name]["weights"]):
        (folder() / filename).unlink(missing_ok=True)
    _LOADED.pop(name, None)

