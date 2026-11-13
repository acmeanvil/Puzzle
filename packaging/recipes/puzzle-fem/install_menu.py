"""Put the menu entry and its icons into the package (menuinst, ADR 0068).

Run by the recipe's build script on every platform: Python rather than mkdir and cp,
which Windows' cmd doesn't have (the package is noarch, but may be built on Windows).
"""

import os
import shutil
from pathlib import Path

root = Path.cwd()
menu = Path(os.environ["PREFIX"]) / "Menu"
menu.mkdir(parents=True, exist_ok=True)
shutil.copy(root / "packaging" / "recipes" / "puzzle-fem" / "menu.json", menu / "puzzle-fem.json")
art = root / "src" / "puzzle" / "gui" / "art"
for name in ("Puzzle.icns", "Puzzle.ico"):
    shutil.copy(art / name, menu / name)
shutil.copy(art / "icon.png", menu / "Puzzle.png")
