# GP2CHC

*[English version](README.md)*

Convertit la **piste de batterie** d'une tablature Guitar Pro en chart **Clone Hero** (`notes.mid` + `song.ini`) : pro drums, difficulté Expert, calé sur l'enregistrement.

**Uniquement la batterie.** La guitare, la basse et le chant ne sont pas convertis.

**Ça ne remplace pas un charter.** Le résultat demande toujours un passage dans Moonscraper (corriger des notes, vérifier la jouabilité), mais le gros du travail est fait en quelques secondes.

## Téléchargement

Windows : téléchargez `GP2CHC-windows.zip` sur la page [Releases](../../releases), décompressez-le n'importe où et lancez `GP2CHC.exe`. Rien à installer et aucune connexion nécessaire : Python, numpy, ffmpeg, Demucs, PyTorch et le modèle de séparation sont inclus. `gp2chc-cli.exe` est la version ligne de commande.

L'exécutable n'est pas signé : Windows SmartScreen peut afficher un avertissement au premier lancement (« Informations complémentaires » → « Exécuter quand même »). Vous pouvez aussi le construire vous-même (voir [plus bas](#construire-lexécutable-windows)) ou lancer le programme depuis les sources.

## Fonctions

- **Chart pro drums en Expert** : toms et cymbales distingués (marqueurs de toms), notes fantômes et accents, sections, reprises et fins alternatives déroulées, changements de tempo et de mesure.
- **Calage automatique sur le morceau**, à partir des pistes de batterie (`drums_*.ogg/opus`) ou d'un mix complet, dont la batterie est isolée avec [Demucs](https://github.com/adefossez/demucs) :
  - trouve où commence la batterie, même tard dans le morceau ;
  - aligne la structure mesure par mesure sur tout le morceau : insère des mesures vides là où l'enregistrement en a plus que la tablature, retire les mesures de la tablature que l'enregistrement ne joue pas ;
  - un tempo et une signature de temps par mesure (ou un tempo par temps en option) : la grille suit le batteur.
- **Piste de batterie à partir d'un mix complet** : quand seul un mix complet est fourni, la batterie isolée est enregistrée dans `drums.opus` et la chanson garde tout le reste (le mix moins la batterie). Clone Hero peut ainsi couper la batterie sur une note ratée, sans la jouer deux fois. Le mix complet d'origine est gardé dans `gp2chc_original_audio/full_mix/`, et les conversions suivantes réutilisent directement `drums.opus` (sans nouvelle séparation).
- **3 s de silence au début** du chart et des fichiers audio du morceau, pour ne pas être surpris par les premières notes. L'audio d'origine est gardé dans `gp2chc_original_audio/`, et une nouvelle conversion n'ajoute jamais le silence deux fois.
- **Règles de jouabilité** : jamais plus de 2 pads en même temps (la grosse caisse s'y ajoute) ; le charley au pied (jaune cymbale par défaut) est retiré quand il tombe avec un tom, ou avec la caisse claire pendant un fill.
- **song.ini** créé ou fusionné avec un existant (`song_length` et `preview_start_time` suivent le silence ajouté).
- Interface en **français et en anglais**, selon la langue du système par défaut.

## Utilisation

1. Lancez `GP2CHC.exe` (ou `python -m gp2chc` depuis les sources, ou double-clic sur `GP2CHC.pyw`).
2. Choisissez la tablature Guitar Pro.
3. Cliquez sur **Dossier du morceau...** et choisissez le dossier Clone Hero du morceau : les pistes de batterie et le `song.ini` sont détectés, et le chart y est écrit. Ou choisissez l'audio vous-même : **Batterie seule** ou **Mix complet**.
4. Cliquez sur **Convertir**. Le journal indique où la première note de batterie a été placée, la plage de tempo, les mesures insérées ou retirées, et la part des notes qui tombent sur une attaque de l'audio.

Si le début trouvé est faux (décompte aux baguettes, bruit avant le morceau...), indiquez l'instant de la première note de batterie dans **1re note de batterie à (s)** et relancez.

Autres onglets : **Avancé** (correspondance instrument Guitar Pro → lane, tempo par temps, lignes libres du `song.ini`), **Modules** (état de numpy / ffmpeg / Demucs depuis les sources), **À propos**.

## Ligne de commande

```
gp2chc-cli.exe tab.gp --audio drums_1.ogg drums_2.ogg drums_3.ogg -o "Mon morceau"
python -m gp2chc tab.gp --mix song.ogg
python -m gp2chc tab.gp --list-tracks
```

| Option | Effet |
|--------|-------|
| `--audio F1 F2...` | audio de batterie seule pour caler le chart |
| `--mix F1 F2...` | mix complet : la batterie est isolée avec Demucs puis sert au calage |
| `--drums-start S` | impose l'instant (s, silence compris) de la première note de batterie |
| `--lead-in S` | silence ajouté au début du chart et de l'audio (3 par défaut, `0` remet l'audio d'origine) |
| `--tempo-par-temps` / `--per-beat` | un tempo par temps au lieu d'un par mesure |
| `--track N` | piste de batterie par numéro ou par nom (défaut : la première) |
| `--max-hands N` | pads simultanés max, grosse caisse non comptée (2 par défaut, 0 = illimité) |
| `--map fichier.json` | change la correspondance instrument → lane, ex. `{"56": "yellow-cymbal", "44": "none"}` |
| `--ini-template FICHIER` / `--set cle=valeur` | reprend un `song.ini` existant / force une valeur |
| `--offset-ms N` | décalage manuel quand aucun audio n'est fourni |
| `--no-dynamics` | sans notes fantômes / accents |
| `--lang fr\|en\|auto` | langue des messages |

## Correspondance par défaut

| Guitar Pro (GM) | Clone Hero |
|-----------------|------------|
| Grosse caisse (35, 36) | kick |
| Caisse claire, side stick (38, 37, 40) | rouge |
| Charley fermé / ouvert / au pied (42, 46, 44) | jaune cymbale |
| Toms aigus (50, 48) | jaune tom |
| Toms medium / grave (47, 45) | bleu tom |
| Floor toms (43, 41) | vert tom |
| Ride, cloche de ride (51, 59, 53) | bleu cymbale |
| Crashes, splash, china (49, 57, 55, 52) | vert cymbale |

Les autres instruments (cowbell, tambourin...) sont ignorés et listés dans le journal.

## Précision

Mesurée sur un multipiste Rock Band utilisé pendant le développement, avec un détecteur d'attaques indépendant de celui du calage : écart médian de 3,7 ms entre une note et l'attaque audio correspondante, 93 % des notes à moins de 20 ms (un tempo par mesure ; 2,6 ms avec un tempo par temps). L'alignement de structure a été vérifié en projetant la ligne de chant de la tablature avec la même carte de tempo sur la piste de chant (0,07 s d'écart au premier couplet), et sur des cas simulés (batterie retardée de 50 s, intro allongée de 12 mesures, 2 mesures supprimées ou doublées) : 98 à 100 % des notes retrouvent leur place. En pratique, le résultat dépend de la fidélité de la tablature à l'enregistrement.

## Limites

- Batterie uniquement.
- Expert uniquement : pas de Easy / Medium / Hard.
- Pas de star power, pas de fills d'activation, pas de roulements, pas de double pédale (Expert+).
- Les notes d'ornement (flams) sont ignorées.
- Guitar Pro 7/8 (`.gp`) est testé ; Guitar Pro 6 (`.gpx`) est écrit d'après la description du format mais n'a pas été testé sur de vrais fichiers. Pour un `.gp5` ou plus ancien, réenregistrez-le en `.gp` dans Guitar Pro.
- Le calage tolère des mesures en plus ou en moins, mais pas des sections jouées dans un autre ordre que dans la tablature.
- Dossiers Rock Band : `song.opus` ne contient pas la batterie, il ne sert pas au calage.

## Comment il a été fait

GP2CHC a été développé avec l'aide de **Claude Code** (l'assistant de programmation d'Anthropic). Dark_Brocoli a défini les fonctions et les règles de jouabilité, testé les charts dans Clone Hero et signalé ce qu'il fallait changer (problèmes de calage, charley au pied dans les fills, accords à trois pads, silence au début...) ; l'essentiel du code a été écrit par Claude sous cette direction. Les chiffres de précision ci-dessus viennent de mesures faites pendant le développement.

## Depuis les sources

Python 3.10+. La conversion n'a besoin que de la bibliothèque standard. Le calage sur l'audio demande `numpy` et `ffmpeg` (dans le PATH) ; isoler la batterie d'un mix complet demande en plus `demucs` (volumineux : il installe PyTorch).

```
pip install -r requirements.txt
python -m gp2chc
```

Tests : `pip install -r requirements-dev.txt`, puis `python -m pytest`. Les tests qui utilisent une vraie tablature sont ignorés quand le fichier est absent (aucune tablature ni aucun audio dans ce dépôt).

## Construire l'exécutable Windows

```
pip install -r requirements.txt -r requirements-dev.txt
python packaging/build.py
```

Il faut un `ffmpeg.exe` statique (dans le PATH, installé par Chocolatey, ou indiqué par la variable d'environnement `FFMPEG`) ; le modèle Demucs est téléchargé si besoin. Résultat : `dist/GP2CHC/` et `dist/GP2CHC-windows.zip`.

## Licence

MIT, voir [LICENSE](LICENSE). L'exécutable Windows inclut aussi FFmpeg (GPL v3, licence fournie à côté), Demucs (MIT), PyTorch (BSD), NumPy (BSD) et Python (PSF).

Créé par Dark_Brocoli · dark.brocoli.ttv@gmail.com
