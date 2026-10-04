"""Écriture minimale de fichiers MIDI (format 1), sans dépendance externe."""

from __future__ import annotations

import struct

# Un évènement = (tick, priorité, octets). À tick égal : méta (0) < note off (1) < note on (2).
Event = tuple[int, int, bytes]

META, NOTE_OFF, NOTE_ON = 0, 1, 2


def _varlen(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def meta(tick: int, kind: int, data: bytes) -> Event:
    return tick, META, bytes([0xFF, kind]) + _varlen(len(data)) + data


def track_name(name: str) -> Event:
    return meta(0, 0x03, name.encode("utf-8"))


def text_event(tick: int, text: str) -> Event:
    return meta(tick, 0x01, text.encode("utf-8"))


def tempo(tick: int, bpm: float) -> Event:
    return meta(tick, 0x51, int(round(60_000_000 / bpm)).to_bytes(3, "big"))


def time_signature(tick: int, numerator: int, denominator: int) -> Event:
    return meta(tick, 0x58, bytes([numerator, denominator.bit_length() - 1, 24, 8]))


def note_on(tick: int, pitch: int, velocity: int = 100, channel: int = 0) -> Event:
    return tick, NOTE_ON, bytes([0x90 | channel, pitch, velocity])


def note_off(tick: int, pitch: int, channel: int = 0) -> Event:
    return tick, NOTE_OFF, bytes([0x80 | channel, pitch, 0])


def _track_chunk(events: list[Event]) -> bytes:
    body = bytearray()
    last = 0
    for tick, _, data in sorted(events, key=lambda e: (e[0], e[1])):
        body += _varlen(tick - last) + data
        last = tick
    body += _varlen(0) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def write_midi(path, tracks: list[list[Event]], ppq: int) -> None:
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), ppq)
    with open(path, "wb") as f:
        f.write(header)
        for events in tracks:
            f.write(_track_chunk(events))
