"""Lecture du conteneur .gpx (Guitar Pro 6) : BCFZ (compressé) / BCFS (système de fichiers)."""

from __future__ import annotations

from .i18n import tr

SECTOR = 0x1000


class _Bits:
    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos * 8

    @property
    def eof(self) -> bool:
        return self.pos >= len(self.data) * 8

    def bit(self) -> int:
        byte = self.data[self.pos >> 3]
        value = (byte >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return value

    def bits(self, n: int) -> int:  # poids fort d'abord
        value = 0
        for _ in range(n):
            value = (value << 1) | self.bit()
        return value

    def bits_reversed(self, n: int) -> int:  # poids faible d'abord
        value = 0
        for i in range(n):
            value |= self.bit() << i
        return value


def _decompress(data: bytes) -> bytes:
    expected = int.from_bytes(data[4:8], "little")
    reader = _Bits(data, 8)
    out = bytearray()
    while len(out) < expected and not reader.eof:
        if reader.bit() == 0:
            for _ in range(reader.bits_reversed(2)):
                out.append(reader.bits(8))
        else:
            width = reader.bits(4)
            offset = reader.bits_reversed(width)
            size = reader.bits_reversed(width)
            source = len(out) - offset
            out += out[source : source + min(offset, size)]
    return bytes(out)


def _int(data: bytes, pos: int) -> int:
    return int.from_bytes(data[pos : pos + 4], "little", signed=True)


def _files(fs: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    offset = SECTOR
    while offset + SECTOR <= len(fs):
        if _int(fs, offset) == 2:
            name = fs[offset + 4 : offset + 4 + 127].split(b"\0", 1)[0].decode("latin-1")
            size = _int(fs, offset + 0x8C)
            blocks = bytearray()
            pos = offset + 0x94
            while pos + 4 <= offset + SECTOR:
                index = _int(fs, pos)
                if index == 0:
                    break
                blocks += fs[index * SECTOR : (index + 1) * SECTOR]
                pos += 4
            files[name] = bytes(blocks[:size])
        offset += SECTOR
    return files


def extract_gpif(data: bytes) -> bytes:
    magic = data[:4]
    if magic == b"BCFZ":
        data = _decompress(data)
    elif magic != b"BCFS":
        raise ValueError(tr("Fichier .gpx invalide (en-tête BCFZ/BCFS introuvable)"))
    files = _files(data)
    if "score.gpif" not in files:
        raise ValueError(tr("score.gpif introuvable dans le fichier .gpx"))
    return files["score.gpif"]
