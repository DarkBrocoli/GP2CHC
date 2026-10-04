"""Interface en ligne de commande : python -m gp2chc fichier.gp  (sans argument : ouvre l'interface graphique)"""

from __future__ import annotations

import argparse
import sys

from . import i18n, readers
from .convert import Options, convert
from .i18n import tr


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gp2chc",
        description=tr("Convertit la batterie d'un fichier Guitar Pro (.gp, .gpx) en notes.mid + song.ini pour Clone Hero."),
    )
    p.add_argument("input", help=tr("fichier Guitar Pro (.gp = GP7/8, .gpx = GP6)"))
    p.add_argument("-o", "--output", help=tr("dossier de sortie (défaut : output/<Artiste - Titre>)"))
    p.add_argument("--list-tracks", action="store_true", help=tr("affiche les pistes du fichier et quitte"))
    p.add_argument("--track", help=tr("numéro ou nom de la piste de batterie (défaut : la première)"))
    p.add_argument("--ini-template", help=tr("song.ini existant dont on reprend les valeurs (difficultés, song_length...)"))
    p.add_argument("--set", action="append", default=[], metavar=tr("CLE=VALEUR"), help=tr("force une valeur du song.ini (répétable)"))
    p.add_argument("--map", help=tr('JSON qui surcharge la table pitch GM -> lane, ex. {"56": "yellow-cymbal"}'))
    p.add_argument("--max-hands", type=int, default=2, help=tr("pads simultanés max, grosse caisse non comptée, 0 = illimité (défaut : 2)"))
    p.add_argument("--offset-ms", type=float, default=0, help=tr("décale le chart de N ms vers l'avant (sans calage sur l'audio)"))
    p.add_argument("--audio", nargs="+", metavar=tr("FICHIER"), help=tr("audio de la batterie (drums_*.ogg/opus, idéalement) : cale début et tempo sur l'enregistrement"))
    p.add_argument("--mix", nargs="+", metavar=tr("FICHIER"), help=tr("mix complet (song.ogg...) : la batterie est isolée avec Demucs (pip install demucs), puis sert au calage"))
    p.add_argument("--drums-start", type=float, metavar=tr("SECONDES"), help=tr("impose l'instant de la première note de batterie, silence ajouté compris (si le calage automatique se trompe de début)"))
    p.add_argument("--lead-in", type=float, default=3.0, metavar=tr("SECONDES"), help=tr("silence ajouté au début du chart et des fichiers audio du dossier (défaut : 3)"))
    names = ("--per-beat", "--tempo-par-temps") if i18n.language() == "en" else ("--tempo-par-temps", "--per-beat")
    p.add_argument(*names, dest="per_beat", action="store_true", help=tr("calage : un tempo par temps au lieu d'un tempo par mesure"))
    p.add_argument("--no-dynamics", action="store_true", help=tr("n'écrit pas les notes fantômes / accentuées"))
    p.add_argument("--lang", choices=["auto", *i18n.LANGUAGES], help=tr("langue des messages (défaut : celle choisie dans l'interface, sinon celle du système)"))  # fmt: skip
    return p.parse_args(argv)


def _language_from(argv: list[str]) -> str:
    """--lang est lu avant tout le reste : l'aide elle-même doit être dans la bonne langue."""
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return i18n.preference()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from .gui import main as gui_main

        return gui_main()

    i18n.set_language(_language_from(argv))
    args = _parse_args(argv)
    try:
        if args.list_tracks:
            tag = tr("[batterie]")
            for index, name, is_drum in readers.list_tracks(args.input):
                print(f"{index:2d}  {(tag + ' ') if is_drum else ' ' * (len(tag) + 1)}{name}")
            return 0
        options = Options(
            track=args.track,
            ini_template=args.ini_template,
            ini_values=dict(item.split("=", 1) for item in args.set),
            mapping=args.map,
            max_hands=args.max_hands,
            offset_ms=args.offset_ms,
            dynamics=not args.no_dynamics,
            audio=args.audio or [],
            mix=args.mix or [],
            drums_start=args.drums_start,
            lead_in_ms=args.lead_in * 1000,
            tempo_per_beat=args.per_beat,
            progress=lambda message: print(message, flush=True),
        )
        result = convert(args.input, args.output, options)
    except (ValueError, OSError, KeyError) as e:
        print(tr("Erreur : {error}", error=e), file=sys.stderr)
        return 1
    print("\n".join(result.messages))
    for warning in result.warnings:
        print(tr("ATTENTION : {warning}", warning=warning), file=sys.stderr)
    return 0
