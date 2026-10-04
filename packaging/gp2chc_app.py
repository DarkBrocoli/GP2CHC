"""Point d'entrée de l'exécutable autonome.

GP2CHC.exe (sans console) ouvre l'interface ; gp2chc-cli.exe (console) s'utilise comme `python -m gp2chc`.
"""

import multiprocessing
import sys

from gp2chc.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # PyTorch peut lancer des sous-processus
    sys.exit(main())
