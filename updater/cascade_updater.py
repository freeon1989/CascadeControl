"""Точка входа CascadeUpdater.exe: замена файлов панели после её выхода."""

import sys

from cascade_panel.swap import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
