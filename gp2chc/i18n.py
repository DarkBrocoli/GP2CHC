"""Langue de l'interface et des messages : français ou anglais, celle du système par défaut.

Les textes sont écrits en français dans le code et passés à tr() ; la traduction anglaise est dans EN.
"""

from __future__ import annotations

import json
import locale
import os
from pathlib import Path

LANGUAGES = {"fr": "Français", "en": "English"}
AUTO = "auto"


def _settings_path() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if not base:
        try:
            base = str(Path.home() / ".config")
        except RuntimeError:  # pas de dossier utilisateur : la préférence ne sera pas gardée
            import tempfile

            base = tempfile.gettempdir()
    return Path(base) / "gp2chc" / "settings.json"


SETTINGS = _settings_path()

_current: str | None = None


def system_language() -> str:
    """'fr' si le système est en français, sinon 'en'."""
    names = []
    try:  # Windows : langue de l'interface du système
        import ctypes

        names.append(locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), ""))
    except (AttributeError, OSError):
        pass
    names += [os.environ.get(var, "") for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        names.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    for name in names:
        if name and name not in ("C", "POSIX"):
            return "fr" if name.lower().startswith(("fr", "french")) else "en"
    return "en"


def set_language(code: str | None) -> str:
    """Choisit la langue ('fr', 'en', ou 'auto' / None = celle du système) ; renvoie la langue effective."""
    global _current
    _current = code if code in LANGUAGES else system_language()
    return _current


def language() -> str:
    return _current or set_language(AUTO)


def preference() -> str:
    """Langue choisie dans l'interface ('auto' tant que rien n'a été choisi)."""
    try:
        code = json.loads(SETTINGS.read_text(encoding="utf-8")).get("language", AUTO)
    except (OSError, ValueError, AttributeError):
        return AUTO
    return code if code in LANGUAGES else AUTO


def save_preference(code: str) -> None:
    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    settings["language"] = code
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(settings, indent=1), encoding="utf-8")
    except OSError:
        pass  # préférence non enregistrée : la langue choisie vaut pour cette session


def tr(text: str, **values) -> str:
    """Texte (écrit en français) dans la langue courante, avec ses valeurs {nom} remplacées."""
    if language() == "en":
        text = EN.get(text, text)
    return text.format(**values) if values else text


EN: dict[str, str] = {
    # calage sur l'audio
    "ffmpeg est introuvable (nécessaire pour lire l'audio) : https://ffmpeg.org/download.html":
        "ffmpeg not found (needed to read audio): https://ffmpeg.org/download.html",
    "Impossible de lire l'audio '{path}' : {error}": "Cannot read audio '{path}': {error}",
    "Aucune note à caler": "No notes to align",
    "Aucun audio fourni": "No audio given",
    "Analyse des attaques de l'audio...": "Analysing audio onsets...",
    "Alignement de la structure (mesures ajoutées ou absentes)...": "Aligning the structure (added or missing bars)...",
    "Le calage sur l'audio demande numpy : pip install numpy": "Audio alignment needs numpy: pip install numpy",
    "Affinage du tempo temps par temps...": "Refining the tempo beat by beat...",
    "Affinage du tempo mesure par mesure...": "Refining the tempo bar by bar...",
    # silence au début
    "ffmpeg est introuvable : nécessaire pour ajouter le silence au début de l'audio":
        "ffmpeg not found: needed to add silence at the start of the audio",
    "Ajout de {seconds:g} s de silence au début de {count} fichier(s) audio...":
        "Adding {seconds:g} s of silence at the start of {count} audio file(s)...",
    "Impossible d'ajouter le silence à {name} : {error}": "Cannot add silence to {name}: {error}",
    # correspondance
    "Lane inconnue '{lane}' (choix : {choices})": "Unknown lane '{lane}' (choices: {choices})",
    # ligne de commande
    "Convertit la batterie d'un fichier Guitar Pro (.gp, .gpx) en notes.mid + song.ini pour Clone Hero.":
        "Converts the drums of a Guitar Pro file (.gp, .gpx) to notes.mid + song.ini for Clone Hero.",
    "fichier Guitar Pro (.gp = GP7/8, .gpx = GP6)": "Guitar Pro file (.gp = GP7/8, .gpx = GP6)",
    "dossier de sortie (défaut : output/<Artiste - Titre>)": "output folder (default: output/<Artist - Title>)",
    "affiche les pistes du fichier et quitte": "list the tracks of the file and exit",
    "numéro ou nom de la piste de batterie (défaut : la première)": "number or name of the drum track (default: the first one)",
    "song.ini existant dont on reprend les valeurs (difficultés, song_length...)":
        "existing song.ini whose values are reused (difficulties, song_length...)",
    "CLE=VALEUR": "KEY=VALUE",
    "force une valeur du song.ini (répétable)": "force a song.ini value (repeatable)",
    'JSON qui surcharge la table pitch GM -> lane, ex. {"56": "yellow-cymbal"}':
        'JSON overriding the GM pitch -> lane table, e.g. {"56": "yellow-cymbal"}',
    "pads simultanés max, grosse caisse non comptée, 0 = illimité (défaut : 2)":
        "max simultaneous pads, kick not counted, 0 = unlimited (default: 2)",
    "décale le chart de N ms vers l'avant (sans calage sur l'audio)": "delay the chart by N ms (without audio alignment)",
    "FICHIER": "FILE",
    "audio de la batterie (drums_*.ogg/opus, idéalement) : cale début et tempo sur l'enregistrement":
        "drum audio (ideally drums_*.ogg/opus): aligns start and tempo to the recording",
    "mix complet (song.ogg...) : la batterie est isolée avec Demucs (pip install demucs), puis sert au calage":
        "full mix (song.ogg...): drums are isolated with Demucs (pip install demucs), then used for alignment",
    "SECONDES": "SECONDS",
    "impose l'instant de la première note de batterie, silence ajouté compris (si le calage automatique se trompe de début)":
        "force the time of the first drum note, added silence included (if automatic alignment gets the start wrong)",
    "silence ajouté au début du chart et des fichiers audio du dossier (défaut : 3)":
        "silence added at the start of the chart and of the folder's audio files (default: 3)",
    "calage : un tempo par temps au lieu d'un tempo par mesure": "alignment: one tempo per beat instead of one per bar",
    "n'écrit pas les notes fantômes / accentuées": "do not write ghost / accented notes",
    "langue des messages (défaut : celle choisie dans l'interface, sinon celle du système)":
        "message language (default: the one chosen in the window, otherwise the system's)",
    "[batterie]": "[drums]",
    "ATTENTION : {warning}": "WARNING: {warning}",
    "Erreur : {error}": "Error: {error}",
    # compte rendu de conversion
    "Piste : {name}": "Track: {name}",
    "{count} notes | durée du chart : {length}": "{count} notes | chart length: {length}",
    "Fichiers écrits dans {folder}": "Files written to {folder}",
    "charley au pied": "pedal hi-hat",
    "pitch {pitch}": "pitch {pitch}",
    "Première note de batterie calée à {time}": "First drum note aligned at {time}",
    " (première attaque détectée dans l'audio : {time})": " (first onset detected in the audio: {time})",
    "Calage impossible : seulement {rate:.0%} des notes tombent sur une attaque de l'audio. "
    "Le chart garde le tempo de la tablature : il ne sera PAS synchronisé avec la musique. "
    "Vérifiez que l'audio contient bien la batterie (song.opus des dossiers Rock Band n'en contient pas : "
    "prenez drums_*.opus ou un mix complet) et que la tablature correspond au morceau.":
        "Alignment failed: only {rate:.0%} of the notes land on an audio onset. The chart keeps the tab's tempo: "
        "it will NOT be in sync with the music. Check that the audio really contains the drums (song.opus in "
        "Rock Band folders does not: use drums_*.opus or a full mix) and that the tab matches the song.",
    "{count} x notes d'ornement (flams)": "{count} x grace notes (flams)",
    "Volontairement non charté : {notes}": "Deliberately not charted: {notes}",
    "{count} charleys au pied retirés (joués avec un tom, ou avec la caisse claire dans un fill)":
        "{count} pedal hi-hats removed (played with a tom, or with the snare during a fill)",
    "{count} notes retirées (plus de {pads} pads en même temps, grosse caisse non comptée)":
        "{count} notes removed (more than {pads} pads at once, kick not counted)",
    "Notes ignorées (pitch GM sans lane) : {pitches}": "Ignored notes (GM pitch without a lane): {pitches}",
    "La 1re note de batterie ne peut pas être avant la fin du silence ajouté ({seconds:g} s)":
        "The first drum note cannot be before the end of the added silence ({seconds:g} s)",
    "{close:.0%} des notes à moins de 15 ms d'une attaque, {hit:.0%} à moins de 30 ms (tempo constant : {before:.0%})":
        "{close:.0%} of the notes within 15 ms of an onset, {hit:.0%} within 30 ms (constant tempo: {before:.0%})",
    "{seconds:g} s de silence ajoutées au début du chart et de {count} fichier(s) audio (originaux conservés dans {folder})":
        "{seconds:g} s of silence added at the start of the chart and of {count} audio file(s) (originals kept in {folder})",
    "Le chart commence après {seconds:g} s de silence, mais aucun fichier audio n'est dans le dossier : "
    "choisissez l'audio dans la conversion (il sera décalé automatiquement), ou ajoutez vous-même ce silence.":
        "The chart starts after {seconds:g} s of silence, but there is no audio file in the folder: choose the audio "
        "in the conversion (it will be shifted automatically), or add this silence yourself.",
    "kick {0}, rouge {1}, jaune {2}, bleu {3}, vert {4}": "kick {0}, red {1}, yellow {2}, blue {3}, green {4}",
    "Tempo suivi temps par temps : {low:.1f} à {high:.1f} BPM": "Tempo followed beat by beat: {low:.1f} to {high:.1f} BPM",
    "Tempo suivi mesure par mesure : {low:.1f} à {high:.1f} BPM": "Tempo followed bar by bar: {low:.1f} to {high:.1f} BPM",
    "{count} mesure(s) d'intro sans batterie retirée(s) : l'intro de la tablature est plus longue que le début de l'enregistrement":
        "{count} intro bar(s) without drums removed: the tab's intro is longer than the start of the recording",
    "{count} mesure(s) vide(s) insérée(s) avant la mesure {bar} de la tablature : l'enregistrement en contient plus à cet endroit":
        "{count} empty bar(s) inserted before bar {bar} of the tab: the recording has more bars there",
    "Mesures de la tablature absentes de l'enregistrement, retirées : {bars}":
        "Tab bars missing from the recording, removed: {bars}",
    "La première note de la tablature a été calée à {note}, mais l'audio contient des attaques de batterie dès {attack}. "
    "Si le début est faux, indiquez l'instant de la première note de batterie (champ « 1re note de batterie » ou "
    "--drums-start) et relancez.":
        "The tab's first note was aligned at {note}, but the audio has drum onsets from {attack}. If the start is "
        "wrong, enter the time of the first drum note (\"First drum note at\" field or --drums-start) and run again.",
    "Mesures où les notes ne collent pas à l'audio (tablature différente de l'enregistrement ?) : {bars}":
        "Bars where the notes do not match the audio (tab different from the recording?): {bars}",
    # modules
    "Installation de {package} (peut durer plusieurs minutes)...": "Installing {package} (may take several minutes)...",
    "L'installation de {package} a échoué (voir les messages ci-dessus)": "Installing {package} failed (see the messages above)",
    # lecture des tablatures
    "Piste '{name}' introuvable ou ambiguë": "Track '{name}' not found or ambiguous",
    "Aucune piste de batterie trouvée (utilisez --track pour en désigner une)": "No drum track found (use --track to pick one)",
    "score.gpif introuvable dans le fichier .gpx": "score.gpif not found in the .gpx file",
    "Fichier .gpx invalide (en-tête BCFZ/BCFS introuvable)": "Invalid .gpx file (BCFZ/BCFS header not found)",
    "Format '{ext}' non supporté (formats gérés : .gp, .gpx)": "Unsupported format '{ext}' (supported: .gp, .gpx)",
    # Demucs
    "Chargement du modèle Demucs (premier lancement : téléchargement d'environ 80 Mo)...":
        "Loading the Demucs model (first run: downloads about 80 MB)...",
    "La séparation de la batterie demande Demucs : pip install demucs (ou fournissez directement les pistes de batterie seule)":
        "Drum separation needs Demucs: pip install demucs (or give the drum-only tracks directly)",
    "Séparation de la batterie (carte graphique), cela peut prendre quelques minutes...":
        "Separating the drums (graphics card), this may take a few minutes...",
    "Séparation de la batterie (processeur), cela peut prendre quelques minutes...":
        "Separating the drums (processor), this may take a few minutes...",
    "Batterie déjà isolée précédemment (cache).": "Drums already isolated previously (cache).",
    # interface
    "GP2CHC - Guitar Pro vers Clone Hero (batterie)": "GP2CHC - Guitar Pro to Clone Hero (drums)",
    "Langue": "Language",
    "Automatique ({language})": "Automatic ({language})",
    "Conversion": "Conversion",
    "Avancé": "Advanced",
    "Modules": "Modules",
    "Fichiers": "Files",
    "Tablature (.gp, .gpx)": "Tab (.gp, .gpx)",
    "Piste de batterie": "Drum track",
    "(automatique : première batterie)": "(automatic: first drum track)",
    "Batterie seule": "Drums only",
    "Mix complet": "Full mix",
    "Facultatif : cale le début et le tempo sur l'enregistrement. Le mix complet passe d'abord par Demucs (quelques\n"
    "minutes) : la batterie isolée est enregistrée dans drums.opus et la chanson garde le reste. "
    "Ignoré si la batterie seule est fournie.":
        "Optional: aligns the start and tempo to the recording. The full mix first goes through Demucs (a few\n"
        "minutes): the isolated drums are saved to drums.opus and the song keeps the rest. Ignored if drums-only audio is given.",
    "Dossier du morceau...": "Song folder...",
    "détecte la batterie, le song.ini et le dossier de sortie": "detects the drums, the song.ini and the output folder",
    "Dossier de sortie": "Output folder",
    "song.ini modèle": "Template song.ini",
    "Parcourir...": "Browse...",
    "Options": "Options",
    "Pads simultanés max": "Max simultaneous pads",
    "(+ grosse caisse ; 0 = illimité)": "(+ kick; 0 = unlimited)",
    "Décalage (ms)": "Offset (ms)",
    "Notes fantômes / accents": "Ghost notes / accents",
    "1re note de batterie à (s)": "First drum note at (s)",
    "vide = automatique ; seulement si le début trouvé est faux (temps du journal, silence compris)":
        "empty = automatic; only if the start found is wrong (time from the log, silence included)",
    "Silence au début (s)": "Silence at start (s)",
    "ajouté au chart et aux fichiers audio du dossier (originaux gardés dans gp2chc_original_audio)":
        "added to the chart and the folder's audio files (originals kept in gp2chc_original_audio)",
    "Infos du song.ini (vide = valeur de la tablature ou du modèle)": "song.ini info (empty = value from the tab or the template)",
    "Album": "Album",
    "Année": "Year",
    "Genre": "Genre",
    "Charter": "Charter",
    "Difficulté batterie": "Drums difficulty",
    "(inchangée)": "(unchanged)",
    "Convertir": "Convert",
    "Ouvrir le dossier": "Open folder",
    "Choisissez une tablature (ou un dossier de morceau) puis cliquez sur Convertir.":
        "Choose a tab (or a song folder), then click Convert.",
    "Correspondance instruments Guitar Pro -> lanes Clone Hero": "Guitar Pro instruments -> Clone Hero lanes",
    "Grosse caisse": "Kick",
    "Rouge (caisse claire)": "Red (snare)",
    "Jaune - cymbale": "Yellow - cymbal",
    "Jaune - tom": "Yellow - tom",
    "Bleu - cymbale": "Blue - cymbal",
    "Bleu - tom": "Blue - tom",
    "Vert - cymbale": "Green - cymbal",
    "Vert - tom": "Green - tom",
    "Ignorer": "Ignore",
    "Grosse caisse acoustique": "Acoustic kick",
    "Side stick": "Side stick",
    "Caisse claire": "Snare",
    "Caisse claire électrique": "Electric snare",
    "Floor tom très grave": "Very low floor tom",
    "Charley fermé": "Closed hi-hat",
    "Floor tom": "Floor tom",
    "Charley au pied": "Pedal hi-hat",
    "Tom grave": "Low tom",
    "Charley ouvert": "Open hi-hat",
    "Tom medium": "Mid tom",
    "Tom aigu": "High tom",
    "Crash aigu": "High crash",
    "Tom très aigu": "Very high tom",
    "Ride": "Ride",
    "China": "China",
    "Cloche de ride": "Ride bell",
    "Tambourin": "Tambourine",
    "Splash": "Splash",
    "Cowbell": "Cowbell",
    "Crash medium": "Medium crash",
    "Ride 2": "Ride 2",
    "Rétablir les valeurs par défaut": "Restore defaults",
    "Jaune/bleu/vert : « cymbale » ou « tom » (pro drums). Deux notes sur la même lane : la cymbale l'emporte.":
        "Yellow/blue/green: \"cymbal\" or \"tom\" (pro drums). Two notes on the same lane: the cymbal wins.",
    "Calage sur l'audio": "Audio alignment",
    "Un tempo par temps (au lieu d'un tempo par mesure)": "One tempo per beat (instead of one tempo per bar)",
    "Par mesure : grille régulière dans chaque mesure, une signature de temps à chaque mesure. "
    "Par temps : suit le batteur de plus près (écart médian environ 30 % plus faible).":
        "Per bar: regular grid within each bar, a time signature on every bar. Per beat: follows the drummer more "
        "closely (median gap about 30% smaller).",
    "Autres lignes du song.ini (une par ligne : cle = valeur)": "Other song.ini lines (one per line: key = value)",
    "Exemples : preview_start_time = 52500, delay = 0, icon = rb2dlc, diff_band = 6":
        "Examples: preview_start_time = 52500, delay = 0, icon = rb2dlc, diff_band = 6",
    "La conversion simple n'a besoin de rien. Ces modules servent à caler le tempo sur l'audio et à isoler la "
    "batterie d'un mix complet.":
        "A plain conversion needs nothing. These modules are used to align the tempo to the audio and to isolate the "
        "drums from a full mix.",
    "État": "Status",
    "calage sur l'audio": "audio alignment",
    "lecture des fichiers audio": "reading audio files",
    "isolement de la batterie d'un mix (plusieurs centaines de Mo)": "isolating drums from a mix (several hundred MB)",
    "Télécharger...": "Download...",
    "Installer": "Install",
    "Actualiser": "Refresh",
    "ffmpeg ne s'installe pas avec pip : téléchargez-le, puis ajoutez son dossier bin au PATH de Windows\n"
    "(ou, dans un terminal : winget install Gyan.FFmpeg), et relancez GP2CHC.":
        "ffmpeg cannot be installed with pip: download it, then add its bin folder to the Windows PATH\n"
        "(or, in a terminal: winget install Gyan.FFmpeg), and restart GP2CHC.",
    "✔ installé": "✔ installed",
    "✘ absent": "✘ missing",
    "Tablatures Guitar Pro": "Guitar Pro tabs",
    "Tous les fichiers": "All files",
    "Audio": "Audio",
    "Tablature Guitar Pro": "Guitar Pro tab",
    "Impossible de lire les pistes : {error}": "Cannot read the tracks: {error}",
    "Audio de la batterie (plusieurs fichiers possibles)": "Drum audio (several files allowed)",
    "Mix complet du morceau (avec la batterie)": "Full mix of the song (with drums)",
    "Dossier du morceau Clone Hero": "Clone Hero song folder",
    "{count} piste(s) de batterie": "{count} drum track(s)",
    "pas de piste de batterie seule": "no drums-only track",
    "mix complet": "full mix",
    "Dossier : {found}.": "Folder: {found}.",
    "Dossier de sortie (dossier du morceau Clone Hero)": "Output folder (Clone Hero song folder)",
    "song.ini à reprendre": "song.ini to reuse",
    "Choisissez d'abord une tablature.": "Choose a tab first.",
    "Décalage, début de la batterie, silence ou nombre de pads invalide.": "Invalid offset, drum start, silence or number of pads.",
    "ffmpeg est nécessaire pour lire l'audio et n'est pas installé.\n\nOuvrir la page de téléchargement ?":
        "ffmpeg is needed to read audio and is not installed.\n\nOpen the download page?",
    " et ": " and ",
    "Il manque : {names}.\n\nL'installer maintenant puis lancer la conversion ?":
        "Missing: {names}.\n\nInstall it now and then run the conversion?",
    "Installation terminée.": "Installation finished.",
    "Batterie isolée enregistrée dans {drums} ; {song} contient maintenant le morceau sans la batterie "
    "(mix complet d'origine gardé dans {folder})":
        "Isolated drums saved to {drums}; {song} now holds the song without the drums "
        "(original full mix kept in {folder})",
    "Batterie isolée enregistrée dans {drums} ; {song} contient le morceau sans la batterie "
    "(votre mix complet n'est pas modifié)":
        "Isolated drums saved to {drums}; {song} holds the song without the drums (your full mix is unchanged)",
    "À propos": "About",
    "Version {version}": "Version {version}",
    "Convertit la batterie des tablatures Guitar Pro en charts Clone Hero.": "Converts the drums of Guitar Pro tabs into Clone Hero charts.",
    "Créé par {name}": "Created by {name}",
    "Copier l'adresse": "Copy address",
    "Adresse copiée.": "Address copied.",
    "Tout est inclus dans cet exécutable : rien à installer, aucune connexion nécessaire.":
        "Everything is included in this executable: nothing to install, no internet connection needed.",
    "isolement de la batterie d'un mix": "isolating drums from a mix",
    "Module manquant dans l'exécutable : {names}": "Module missing from the executable: {names}",
    "Terminé.": "Done.",
    "GP2CHC - attention": "GP2CHC - warning",
}  # fmt: skip
