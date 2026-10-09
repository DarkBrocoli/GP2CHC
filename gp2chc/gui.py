"""Interface graphique (tkinter) : tout le programme est utilisable depuis cette fenêtre."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, deps, i18n, ini, readers
from .chart import DEFAULT_MAP
from .convert import Options, convert, safe_name
from .i18n import tr

AUTHOR = "Dark_Brocoli"
AUTHOR_EMAIL = "dark.brocoli.ttv@gmail.com"
AUDIO_EXT = {".opus", ".ogg", ".mp3", ".wav", ".flac", ".m4a"}
LANES = ["kick", "red", "yellow-cymbal", "yellow-tom", "blue-cymbal", "blue-tom", "green-cymbal", "green-tom", "none"]
DRUM_PITCHES = [35, 36, 37, 38, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 59]


def _tab_types():
    return [(tr("Tablatures Guitar Pro"), "*.gp *.gpx"), (tr("Tous les fichiers"), "*.*")]


def _audio_types():
    return [(tr("Audio"), " ".join(f"*{e}" for e in sorted(AUDIO_EXT))), (tr("Tous les fichiers"), "*.*")]


def _ini_types():
    return [("song.ini", "*.ini"), (tr("Tous les fichiers"), "*.*")]


def _no_track() -> str:
    return tr("(automatique : première batterie)")


def _keep() -> str:
    return tr("(inchangée)")


def _lane_label(key: str) -> str:
    return {
        "kick": tr("Grosse caisse"),
        "red": tr("Rouge (caisse claire)"),
        "yellow-cymbal": tr("Jaune - cymbale"),
        "yellow-tom": tr("Jaune - tom"),
        "blue-cymbal": tr("Bleu - cymbale"),
        "blue-tom": tr("Bleu - tom"),
        "green-cymbal": tr("Vert - cymbale"),
        "green-tom": tr("Vert - tom"),
        "none": tr("Ignorer"),
    }[key]


def _drum_name(pitch: int) -> str:
    return {
        35: tr("Grosse caisse acoustique"), 36: tr("Grosse caisse"), 37: tr("Side stick"), 38: tr("Caisse claire"),
        40: tr("Caisse claire électrique"), 41: tr("Floor tom très grave"), 42: tr("Charley fermé"),
        43: tr("Floor tom"), 44: tr("Charley au pied"), 45: tr("Tom grave"), 46: tr("Charley ouvert"),
        47: tr("Tom medium"), 48: tr("Tom aigu"), 49: tr("Crash aigu"), 50: tr("Tom très aigu"),
        51: tr("Ride"), 52: tr("China"), 53: tr("Cloche de ride"), 54: tr("Tambourin"), 55: tr("Splash"),
        56: tr("Cowbell"), 57: tr("Crash medium"), 59: tr("Ride 2"),
    }[pitch]  # fmt: skip


def _language_choices() -> list[tuple[str, str]]:
    """(code, libellé) : automatique (langue du système), puis chaque langue."""
    system = i18n.LANGUAGES[i18n.system_language()]
    return [(i18n.AUTO, tr("Automatique ({language})", language=system))] + list(i18n.LANGUAGES.items())


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.minsize(800, 740)
        self.audio: list[str] = []
        self.mix: list[str] = []
        self.tracks: list[tuple[int, str, bool]] = []
        self.last_output: Path | None = None
        self.busy = False
        self.messages: queue.Queue = queue.Queue()

        self.language = tk.StringVar()
        self.tab = tk.StringVar()
        self.track = tk.StringVar(value=_no_track())
        self.audio_text = tk.StringVar()
        self.mix_text = tk.StringVar()
        self.output = tk.StringVar()
        self.template = tk.StringVar()
        self.max_hands = tk.IntVar(value=2)
        self.offset = tk.StringVar(value="0")
        self.drums_start = tk.StringVar()
        self.lead_in = tk.StringVar(value="3")
        self.per_beat = tk.BooleanVar(value=False)
        self.dynamics = tk.BooleanVar(value=True)
        self.meta = {key: tk.StringVar() for key in ("album", "year", "genre", "charter")}
        self.difficulty = tk.StringVar(value=_keep())
        self.mapping_vars = {pitch: tk.StringVar(value=_lane_label(DEFAULT_MAP.get(pitch, "none"))) for pitch in DRUM_PITCHES}
        self.extra_lines = ""

        self._build_ui()
        self._log(tr("Choisissez une tablature (ou un dossier de morceau) puis cliquez sur Convertir."))
        self._poll_id = self.after(100, self._poll)

    def destroy(self) -> None:
        self.after_cancel(self._poll_id)  # sinon Tk signale l'appel prévu après la fermeture
        super().destroy()

    def _build_ui(self) -> None:
        self.title(tr("GP2CHC - Guitar Pro vers Clone Hero (batterie)"))
        self.frame = ttk.Frame(self)
        self.frame.pack(fill="both", expand=True)

        header = ttk.Frame(self.frame)
        header.pack(fill="x", padx=10, pady=(8, 0))
        choices = _language_choices()
        current = i18n.preference()
        self.language.set(next(label for code, label in choices if code == current))
        language_box = ttk.Combobox(
            header, textvariable=self.language, state="readonly", width=22, values=[label for _, label in choices]
        )
        language_box.pack(side="right")
        language_box.bind("<<ComboboxSelected>>", self._change_language)
        ttk.Label(header, text=tr("Langue")).pack(side="right", padx=(0, 6))

        self.notebook = ttk.Notebook(self.frame)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)
        main, advanced, modules, about = (ttk.Frame(self.notebook, padding=12) for _ in range(4))
        self.main_tab = main
        self.notebook.add(main, text="  " + tr("Conversion") + "  ")
        self.notebook.add(advanced, text="  " + tr("Avancé") + "  ")
        self.notebook.add(modules, text="  " + tr("Modules") + "  ")
        self.notebook.add(about, text="  " + tr("À propos") + "  ")
        self._build_main(main)
        self._build_advanced(advanced)
        self._build_modules(modules)
        self._build_about(about)
        self._refresh_state()

    def _change_language(self, _event=None) -> None:
        code = next(code for code, label in _language_choices() if label == self.language.get())
        if self.busy:  # on ne reconstruit pas la fenêtre pendant une conversion
            self.language.set(next(label for c, label in _language_choices() if c == i18n.preference()))
            return
        # valeurs dont le libellé dépend de la langue : on garde leur sens
        lanes = {pitch: self._lane_key(var.get()) for pitch, var in self.mapping_vars.items()}
        keep = self.difficulty.get() == _keep()
        track = self._selected_track()
        self.extra_lines = self.extra_text.get("1.0", "end-1c")
        log = self.log.get("1.0", "end-1c")

        i18n.save_preference(code)
        i18n.set_language(code)
        self.frame.destroy()
        for pitch, key in lanes.items():
            self.mapping_vars[pitch].set(_lane_label(key))
        if keep:
            self.difficulty.set(_keep())
        self._build_ui()
        self._fill_tracks(track)
        self._log(log)

    # ---- onglet Conversion --------------------------------------------------------------------

    def _row(self, parent, row, label, variable, pick=None, clear=None, **entry_options):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        entry = ttk.Entry(parent, textvariable=variable, **entry_options)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        if pick:
            ttk.Button(parent, text=tr("Parcourir..."), command=pick).grid(row=row, column=2, padx=(8, 0), pady=3)
        if clear:
            ttk.Button(parent, text="✕", width=3, command=clear).grid(row=row, column=3, padx=(4, 0), pady=3)
        return entry

    def _build_main(self, root) -> None:
        files = ttk.LabelFrame(root, text=tr("Fichiers"), padding=10)
        files.pack(fill="x")
        files.columnconfigure(1, weight=1)
        self._row(files, 0, tr("Tablature (.gp, .gpx)"), self.tab, self._pick_tab)
        ttk.Label(files, text=tr("Piste de batterie")).grid(row=1, column=0, sticky="w", pady=3)
        self.track_box = ttk.Combobox(files, textvariable=self.track, state="readonly", values=[_no_track()])
        self.track_box.grid(row=1, column=1, sticky="ew", pady=3)
        self._row(files, 2, tr("Batterie seule"), self.audio_text, self._pick_audio, self._clear_audio, state="readonly")
        self._row(files, 3, tr("Mix complet"), self.mix_text, self._pick_mix, self._clear_mix, state="readonly")
        ttk.Label(
            files,
            text=tr(
                "Facultatif : cale le début et le tempo sur l'enregistrement. Le mix complet passe d'abord par Demucs (quelques\n"
                "minutes) : la batterie isolée est enregistrée dans drums.opus et la chanson garde le reste. "
                "Ignoré si la batterie seule est fournie."
            ),
            foreground="gray", justify="left",
        ).grid(row=4, column=1, columnspan=3, sticky="w")  # fmt: skip
        ttk.Button(files, text=tr("Dossier du morceau..."), command=self._pick_song_folder).grid(
            row=5, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Label(files, text=tr("détecte la batterie, le song.ini et le dossier de sortie"), foreground="gray").grid(
            row=5, column=1, sticky="e", pady=(6, 0)
        )
        self._row(files, 6, tr("Dossier de sortie"), self.output, self._pick_output)
        self._row(files, 7, tr("song.ini modèle"), self.template, self._pick_template, lambda: self.template.set(""))

        options = ttk.LabelFrame(root, text=tr("Options"), padding=10)
        options.pack(fill="x", pady=(10, 0))
        ttk.Label(options, text=tr("Pads simultanés max")).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=0, to=4, width=4, textvariable=self.max_hands).grid(row=0, column=1, padx=(6, 4))
        ttk.Label(options, text=tr("(+ grosse caisse ; 0 = illimité)")).grid(row=0, column=2, sticky="w", padx=(0, 20))
        ttk.Label(options, text=tr("Décalage (ms)")).grid(row=0, column=3)
        self.offset_entry = ttk.Entry(options, textvariable=self.offset, width=8)
        self.offset_entry.grid(row=0, column=4, padx=6)
        ttk.Checkbutton(options, text=tr("Notes fantômes / accents"), variable=self.dynamics).grid(
            row=0, column=5, padx=(20, 0)
        )
        ttk.Label(options, text=tr("1re note de batterie à (s)")).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.drums_start_entry = ttk.Entry(options, textvariable=self.drums_start, width=8)
        self.drums_start_entry.grid(row=1, column=1, padx=(6, 4), pady=(8, 0))
        ttk.Label(
            options,
            text=tr("vide = automatique ; seulement si le début trouvé est faux (temps du journal, silence compris)"),
            foreground="gray",
        ).grid(row=1, column=2, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(options, text=tr("Silence au début (s)")).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(options, from_=0, to=10, increment=0.5, width=6, textvariable=self.lead_in).grid(
            row=2, column=1, padx=(6, 4), pady=(8, 0)
        )
        ttk.Label(
            options,
            text=tr("ajouté au chart et aux fichiers audio du dossier (originaux gardés dans gp2chc_original_audio)"),
            foreground="gray",
        ).grid(row=2, column=2, columnspan=4, sticky="w", pady=(8, 0))

        meta = ttk.LabelFrame(root, text=tr("Infos du song.ini (vide = valeur de la tablature ou du modèle)"), padding=10)
        meta.pack(fill="x", pady=(10, 0))
        meta.columnconfigure(1, weight=1)
        meta.columnconfigure(3, weight=1)
        fields = (("album", tr("Album")), ("year", tr("Année")), ("genre", tr("Genre")), ("charter", tr("Charter")))
        for i, (key, label) in enumerate(fields):
            ttk.Label(meta, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 6), pady=3)
            ttk.Entry(meta, textvariable=self.meta[key]).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="ew", padx=(0, 16), pady=3
            )
        ttk.Label(meta, text=tr("Difficulté batterie")).grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Combobox(
            meta, textvariable=self.difficulty, state="readonly", width=12, values=[_keep(), *map(str, range(7))]
        ).grid(row=2, column=1, sticky="w", pady=3)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=10)
        self.button = ttk.Button(actions, text=tr("Convertir"), command=self._convert)
        self.button.pack(side="left")
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=160)
        self.progress.pack(side="left", padx=12)
        self.open_button = ttk.Button(actions, text=tr("Ouvrir le dossier"), command=self._open_output, state="disabled")
        self.open_button.pack(side="right")
        if self.last_output:
            self.open_button.configure(state="normal")

        self.log = tk.Text(root, height=10, wrap="word", state="disabled", font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)

    # ---- onglet Avancé ------------------------------------------------------------------------

    def _build_advanced(self, root) -> None:
        mapping = ttk.LabelFrame(root, text=tr("Correspondance instruments Guitar Pro -> lanes Clone Hero"), padding=10)
        mapping.pack(fill="x")
        half = (len(DRUM_PITCHES) + 1) // 2
        labels = [_lane_label(key) for key in LANES]
        for i, pitch in enumerate(DRUM_PITCHES):
            col = (i // half) * 2
            ttk.Label(mapping, text=f"{pitch}  {_drum_name(pitch)}").grid(
                row=i % half, column=col, sticky="w", padx=(0, 8), pady=2
            )
            ttk.Combobox(mapping, textvariable=self.mapping_vars[pitch], state="readonly", width=20, values=labels).grid(
                row=i % half, column=col + 1, sticky="w", padx=(0, 24), pady=2
            )
        ttk.Button(mapping, text=tr("Rétablir les valeurs par défaut"), command=self._reset_mapping).grid(
            row=half, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            mapping,
            text=tr("Jaune/bleu/vert : « cymbale » ou « tom » (pro drums). Deux notes sur la même lane : la cymbale l'emporte."),
            foreground="gray",
        ).grid(row=half + 1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        tempo = ttk.LabelFrame(root, text=tr("Calage sur l'audio"), padding=10)
        tempo.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(tempo, text=tr("Un tempo par temps (au lieu d'un tempo par mesure)"), variable=self.per_beat).pack(
            anchor="w"
        )
        ttk.Label(
            tempo,
            text=tr(
                "Par mesure : grille régulière dans chaque mesure, une signature de temps à chaque mesure. "
                "Par temps : suit le batteur de plus près (écart médian environ 30 % plus faible)."
            ),
            foreground="gray", wraplength=720, justify="left",
        ).pack(anchor="w", pady=(4, 0))  # fmt: skip

        extra = ttk.LabelFrame(root, text=tr("Autres lignes du song.ini (une par ligne : cle = valeur)"), padding=10)
        extra.pack(fill="both", expand=True, pady=(12, 0))
        self.extra_text = tk.Text(extra, height=8, font=("Consolas", 10))
        self.extra_text.pack(fill="both", expand=True)
        self.extra_text.insert("1.0", self.extra_lines)
        ttk.Label(
            extra, text=tr("Exemples : preview_start_time = 52500, delay = 0, icon = rb2dlc, diff_band = 6"), foreground="gray"
        ).pack(anchor="w", pady=(4, 0))

    @staticmethod
    def _lane_key(label: str) -> str:
        return next(key for key in LANES if _lane_label(key) == label)

    def _reset_mapping(self) -> None:
        for pitch, var in self.mapping_vars.items():
            var.set(_lane_label(DEFAULT_MAP.get(pitch, "none")))

    def _mapping_overrides(self) -> dict[int, str]:
        overrides = {}
        for pitch, var in self.mapping_vars.items():
            lane = self._lane_key(var.get())
            if lane != DEFAULT_MAP.get(pitch, "none"):
                overrides[pitch] = lane
        return overrides

    def _extra_values(self) -> dict[str, str]:
        values = {}
        for line in self.extra_text.get("1.0", "end").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key.strip():
                    values[key.strip()] = value.strip()
        return values

    # ---- onglet Modules -----------------------------------------------------------------------

    def _build_modules(self, root) -> None:
        if deps.frozen():
            self._build_modules_included(root)
            return
        intro = tr(
            "La conversion simple n'a besoin de rien. Ces modules servent à caler le tempo sur l'audio "
            "et à isoler la batterie d'un mix complet."
        )
        ttk.Label(root, text=intro, wraplength=700, justify="left").pack(anchor="w")
        box = ttk.LabelFrame(root, text=tr("État"), padding=10)
        box.pack(fill="x", pady=(10, 0))
        box.columnconfigure(1, weight=1)
        rows = (
            ("numpy", "numpy", tr("calage sur l'audio"), lambda: self._install(["numpy"])),
            ("ffmpeg", "ffmpeg", tr("lecture des fichiers audio"), lambda: webbrowser.open(deps.FFMPEG_URL)),
            ("demucs", "Demucs (+ PyTorch)", tr("isolement de la batterie d'un mix (plusieurs centaines de Mo)"),
             lambda: self._install(["demucs"])),
        )  # fmt: skip
        self.module_labels: dict[str, ttk.Label] = {}
        self.module_buttons: dict[str, ttk.Button] = {}
        for i, (key, name, role, action) in enumerate(rows):
            ttk.Label(box, text=name, width=18).grid(row=i, column=0, sticky="w", pady=4)
            self.module_labels[key] = ttk.Label(box, text="")
            self.module_labels[key].grid(row=i, column=1, sticky="w")
            ttk.Label(box, text=role, foreground="gray").grid(row=i, column=2, sticky="w", padx=12)
            button = ttk.Button(box, text=tr("Télécharger...") if key == "ffmpeg" else tr("Installer"), command=action)
            button.grid(row=i, column=3, padx=(8, 0))
            self.module_buttons[key] = button
        ttk.Button(root, text=tr("Actualiser"), command=self._refresh_modules).pack(anchor="w", pady=(10, 0))
        ttk.Label(
            root,
            text=tr(
                "ffmpeg ne s'installe pas avec pip : téléchargez-le, puis ajoutez son dossier bin au PATH de Windows\n"
                "(ou, dans un terminal : winget install Gyan.FFmpeg), et relancez GP2CHC."
            ),
            foreground="gray", justify="left",
        ).pack(anchor="w", pady=(10, 0))  # fmt: skip
        self._refresh_modules()

    def _build_modules_included(self, root) -> None:
        """Exécutable autonome : numpy, ffmpeg et Demucs (avec son modèle) sont inclus, rien à installer."""
        ttk.Label(root, text=tr("Tout est inclus dans cet exécutable : rien à installer, aucune connexion nécessaire."),
                  wraplength=700, justify="left").pack(anchor="w")  # fmt: skip
        box = ttk.LabelFrame(root, text=tr("État"), padding=10)
        box.pack(fill="x", pady=(10, 0))
        rows = (
            ("numpy", "numpy", tr("calage sur l'audio")),
            ("ffmpeg", "ffmpeg", tr("lecture des fichiers audio")),
            ("demucs", "Demucs (+ PyTorch)", tr("isolement de la batterie d'un mix")),
        )
        self.module_labels: dict[str, ttk.Label] = {}
        self.module_buttons: dict[str, ttk.Button] = {}
        for i, (key, name, role) in enumerate(rows):
            ttk.Label(box, text=name, width=18).grid(row=i, column=0, sticky="w", pady=4)
            self.module_labels[key] = ttk.Label(box, text="")
            self.module_labels[key].grid(row=i, column=1, sticky="w")
            ttk.Label(box, text=role, foreground="gray").grid(row=i, column=2, sticky="w", padx=12)
        self._refresh_modules()

    def _refresh_modules(self) -> None:
        state = deps.status()
        for key, ok in state.items():
            self.module_labels[key].configure(
                text=tr("✔ installé") if ok else tr("✘ absent"), foreground="#2e7d32" if ok else "#c62828"
            )
            if key in self.module_buttons:
                self.module_buttons[key].configure(state="disabled" if ok else "normal")

    def _install(self, packages: list[str]) -> None:
        if self.busy:
            return
        self.notebook.select(self.main_tab)  # l'avancement s'affiche dans le journal de l'onglet Conversion
        self._set_busy(True)
        self._log("", clear=True)
        threading.Thread(target=self._worker, args=(None, None, None, packages), daemon=True).start()

    # ---- onglet À propos ----------------------------------------------------------------------

    def _build_about(self, root) -> None:
        box = ttk.Frame(root, padding=(10, 30))
        box.pack(anchor="n")
        ttk.Label(box, text="GP2CHC", font=("Segoe UI", 22, "bold")).pack()
        ttk.Label(box, text=tr("Version {version}", version=__version__), foreground="gray").pack(pady=(2, 14))
        ttk.Label(box, text=tr("Convertit la batterie des tablatures Guitar Pro en charts Clone Hero.")).pack()
        ttk.Separator(box).pack(fill="x", pady=22)
        ttk.Label(box, text=tr("Créé par {name}", name=AUTHOR), font=("Segoe UI", 13, "bold")).pack()
        email = ttk.Label(box, text=AUTHOR_EMAIL, foreground="#1a5fb4", cursor="hand2", font=("Segoe UI", 11, "underline"))
        email.pack(pady=(8, 6))
        email.bind("<Button-1>", lambda _event: webbrowser.open(f"mailto:{AUTHOR_EMAIL}"))
        self.copy_note = ttk.Label(box, text="", foreground="gray")
        ttk.Button(box, text=tr("Copier l'adresse"), command=self._copy_email).pack()
        self.copy_note.pack(pady=(6, 0))

    def _copy_email(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(AUTHOR_EMAIL)
        self.copy_note.configure(text=tr("Adresse copiée."))

    # ---- sélection de fichiers ----------------------------------------------------------------

    def _pick_tab(self) -> None:
        path = filedialog.askopenfilename(title=tr("Tablature Guitar Pro"), filetypes=_tab_types())
        if path:
            self._set_tab(path)

    def _set_tab(self, path: str) -> None:
        self.tab.set(path)
        try:
            self.tracks = readers.list_tracks(path)
        except Exception as e:  # fichier illisible : l'erreur réelle sera affichée à la conversion
            self.tracks = []
            self._log(tr("Impossible de lire les pistes : {error}", error=e), clear=True)
        drums = [i for i, _, drum in self.tracks if drum]
        self._fill_tracks(str(drums[0]) if len(drums) == 1 else None)
        if not self.output.get():
            self.output.set(str(Path(path).parent / safe_name(Path(path).stem)))

    def _track_label(self, index: int, name: str, drum: bool) -> str:
        return f"{index} - {name}" + ("  " + tr("[batterie]") if drum else "")

    def _fill_tracks(self, selected: str | None) -> None:
        """Remplit la liste des pistes et sélectionne la piste `selected` (indice), sinon le choix automatique."""
        labels = {str(i): self._track_label(i, name, drum) for i, name, drum in self.tracks}
        self.track_box.configure(values=[_no_track(), *labels.values()])
        self.track.set(labels.get(selected, _no_track()) if selected is not None else _no_track())

    def _pick_audio(self) -> None:
        paths = filedialog.askopenfilenames(
            title=tr("Audio de la batterie (plusieurs fichiers possibles)"), filetypes=_audio_types()
        )
        if paths:
            self.audio = list(paths)
            self.audio_text.set("; ".join(Path(p).name for p in paths))
            self.output.set(str(Path(paths[0]).parent))  # le chart va dans le dossier du morceau
            self._refresh_state()

    def _pick_mix(self) -> None:
        paths = filedialog.askopenfilenames(title=tr("Mix complet du morceau (avec la batterie)"), filetypes=_audio_types())
        if paths:
            self.mix = list(paths)
            self.mix_text.set("; ".join(Path(p).name for p in paths))
            if not self.audio:
                self.output.set(str(Path(paths[0]).parent))
            self._refresh_state()

    def _clear_audio(self) -> None:
        self.audio = []
        self.audio_text.set("")
        self._refresh_state()

    def _clear_mix(self) -> None:
        self.mix = []
        self.mix_text.set("")
        self._refresh_state()

    def _pick_song_folder(self) -> None:
        folder = filedialog.askdirectory(title=tr("Dossier du morceau Clone Hero"))
        if not folder:
            return
        files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in AUDIO_EXT)
        stems = [p for p in files if p.stem.lower().startswith("drums")]
        mixes = [p for p in files if p.stem.lower() in ("mix", "full", "fullmix")]
        self.audio = [str(p) for p in stems]
        self.audio_text.set("; ".join(p.name for p in stems))
        self.mix = [] if stems else [str(p) for p in mixes]
        self.mix_text.set("; ".join(Path(p).name for p in self.mix))
        self.output.set(folder)
        found = [tr("{count} piste(s) de batterie", count=len(stems)) if stems else tr("pas de piste de batterie seule")]
        if self.mix:
            found.append(tr("mix complet"))
        if (Path(folder) / "song.ini").exists():
            self.template.set(str(Path(folder) / "song.ini"))
            self._prefill_from_ini(Path(folder) / "song.ini")
            found.append("song.ini")
        self._log(tr("Dossier : {found}.", found=", ".join(found)), clear=True)
        self._refresh_state()

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title=tr("Dossier de sortie (dossier du morceau Clone Hero)"))
        if path:
            self.output.set(path)

    def _pick_template(self) -> None:
        path = filedialog.askopenfilename(title=tr("song.ini à reprendre"), filetypes=_ini_types())
        if path:
            self.template.set(path)
            self._prefill_from_ini(Path(path))

    def _prefill_from_ini(self, path: Path) -> None:
        try:
            values = ini.read_ini(path)
        except OSError:
            return
        for key, var in self.meta.items():
            if not var.get() and values.get(key):
                var.set(values[key])
        if values.get("diff_drums", "-1") != "-1":
            self.difficulty.set(values["diff_drums"])

    def _refresh_state(self) -> None:
        """Le décalage manuel ne sert que sans calage automatique."""
        calibrated = bool(self.audio or self.mix)
        self.offset_entry.configure(state="disabled" if calibrated else "normal")
        self.drums_start_entry.configure(state="normal" if calibrated else "disabled")

    # ---- conversion ---------------------------------------------------------------------------

    def _selected_track(self) -> str | None:
        value = self.track.get()
        return None if value == _no_track() else value.split(" - ", 1)[0]

    def _build_options(self) -> Options:
        ini_values = self._extra_values()
        ini_values.update({k: v.get().strip() for k, v in self.meta.items() if v.get().strip()})
        if self.difficulty.get() != _keep():
            ini_values["diff_drums"] = self.difficulty.get()
        return Options(
            track=self._selected_track(),
            ini_template=self.template.get() or None,
            ini_values=ini_values,
            mapping_overrides=self._mapping_overrides(),
            max_hands=int(self.max_hands.get()),
            offset_ms=float(self.offset.get().replace(",", ".") or 0),
            drums_start=float(self.drums_start.get().replace(",", ".")) if self.drums_start.get().strip() else None,
            lead_in_ms=float(self.lead_in.get().replace(",", ".") or 0) * 1000,
            tempo_per_beat=self.per_beat.get(),
            dynamics=self.dynamics.get(),
            audio=self.audio,
            mix=self.mix,
            progress=lambda message: self.messages.put(("progress", message)),
        )

    def _convert(self) -> None:
        if self.busy:
            return
        if not self.tab.get():
            messagebox.showinfo("GP2CHC", tr("Choisissez d'abord une tablature."))
            return
        try:
            options = self._build_options()
        except (ValueError, tk.TclError):
            messagebox.showerror("GP2CHC", tr("Décalage, début de la batterie, silence ou nombre de pads invalide."))
            return

        install = []
        if options.audio or options.mix:
            if not deps.has_ffmpeg():
                question = tr("ffmpeg est nécessaire pour lire l'audio et n'est pas installé.\n\nOuvrir la page de téléchargement ?")
                if messagebox.askyesno("GP2CHC", question):
                    webbrowser.open(deps.FFMPEG_URL)
                return
            install = [m for m in (("numpy",) + (("demucs",) if not options.audio else ())) if not deps.has_module(m)]
        if install and deps.frozen():  # ne devrait pas arriver : tout est inclus dans l'exécutable
            messagebox.showerror("GP2CHC", tr("Module manquant dans l'exécutable : {names}", names=", ".join(install)))
            return
        if install:
            names = tr(" et ").join(install)
            if not messagebox.askyesno("GP2CHC", tr("Il manque : {names}.\n\nL'installer maintenant puis lancer la conversion ?", names=names)):
                return

        self._set_busy(True)
        self._log("", clear=True)
        output = self.output.get().strip() or None
        threading.Thread(target=self._worker, args=(self.tab.get(), output, options, install), daemon=True).start()

    def _worker(self, tab: str | None, output: str | None, options: Options | None, install: list[str]) -> None:
        def progress(message: str) -> None:
            self.messages.put(("progress", message))

        try:
            for package in install:
                deps.pip_install(package, progress)
            result = convert(tab, output, options) if tab else None
            self.messages.put(("ok", result))
        except Exception as e:  # toute erreur est affichée dans la fenêtre plutôt que de fermer le programme
            self.messages.put(("error", e))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.button.configure(state="disabled" if busy else "normal")
        if busy:
            self.open_button.configure(state="disabled")
            self.progress.start(12)
        else:
            self.progress.stop()

    def _poll(self) -> None:
        while True:
            try:
                status, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if status == "progress":
                self._log(payload)
                continue
            self._set_busy(False)
            self._refresh_modules()
            if status == "error":
                self._log(tr("Erreur : {error}", error=payload))
            elif payload is None:
                self._log(tr("Installation terminée."))
            else:
                self.last_output = payload.out_dir
                self.open_button.configure(state="normal")
                self._log("\n".join(payload.messages) + "\n\n" + tr("Terminé."), clear=True)
                for warning in payload.warnings:
                    messagebox.showwarning(tr("GP2CHC - attention"), warning)
        self._poll_id = self.after(100, self._poll)

    def _open_output(self) -> None:
        if self.last_output:
            os.startfile(self.last_output)  # Windows ; l'application vise Windows / Clone Hero

    def _log(self, text: str, clear: bool = False) -> None:
        self.log.configure(state="normal")
        if clear:
            self.log.delete("1.0", "end")
        if text:
            self.log.insert("end", text + "\n")
            self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    i18n.set_language(i18n.preference())
    App().mainloop()
    return 0
