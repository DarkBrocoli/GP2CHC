"""Équivalents minimaux des aides matérielles d'audio-separator utilisées par les modèles copiés ici."""

from contextlib import nullcontext

import torch


def should_fallback_to_cpu_for_complex_ops(device: torch.device) -> bool:
    """Opérations complexes (STFT) : le processeur et CUDA les gèrent, MPS / DirectML passent par le processeur."""
    return device.type not in ("cpu", "cuda")


def autocast_disabled(device: torch.device):
    """Désactive la précision mixte là où PyTorch la propose (processeur, CUDA)."""
    if device.type not in ("cpu", "cuda"):
        return nullcontext()
    return torch.autocast(device_type=device.type, enabled=False)
