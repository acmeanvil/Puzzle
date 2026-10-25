"""The result legend (colour bar): how results are coloured and where the bar sits.

`Legend` holds the look, kept as preferences (Settings > Results) so it lasts between
sessions: the colour map and whether it runs the other way, smooth colours or a few
bands, one colour for values outside a custom range (as Ansys draws them: grey above
the top, below the bottom), and the bar's place and size. A custom range belongs to a
result, not the look: the viewport keeps one per result (`Viewport.ranges`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

COLORMAPS = [
    ("Rainbow, blue to red (turbo)", "turbo"),
    ("Classic rainbow (jet)", "jet"),
    ("Viridis (colour-blind safe)", "viridis"),
    ("Plasma", "plasma"),
    ("Cool to warm", "coolwarm"),
    ("Greys", "Greys"),
    ("Blues", "Blues"),
    ("Reds", "Reds"),
]
POSITIONS = [
    ("Left, vertical", "left"),
    ("Right, vertical", "right"),
    ("Top, horizontal", "top"),
    ("Bottom, horizontal", "bottom"),
]
OUTSIDE = [
    ("The end colours", "none"),
    ("Grey", "grey"),
    ("Black", "black"),
    ("White", "white"),
    ("Magenta", "magenta"),
]
BANDS = [
    ("Smooth", 0),
    ("6 bands", 6),
    ("9 bands (as Ansys)", 9),
    ("12 bands", 12),
    ("16 bands", 16),
]
SIZES = [("Small", 0.75), ("Normal", 1.0), ("Large", 1.25), ("Larger", 1.5), ("Largest", 2.0)]
COLOURS = {"grey": "#a6a6a6", "black": "#000000", "white": "#ffffff", "magenta": "#d62ad1"}


@dataclass
class Legend:
    colormap: str = "turbo"
    invert: bool = False
    bands: int = 0  # 0: smooth
    outside: str = "none"  # one colour above and below a custom range, or "none"
    position: str = "left"
    size: float = 1.0

    @classmethod
    def from_settings(cls) -> Legend:
        from puzzle.settings import current

        s = current()
        return cls(
            colormap=s.get("results.colormap"),
            invert=bool(s.get("results.invert_colors")),
            bands=int(s.get("results.bands")),
            outside=s.get("results.outside_color"),
            position=s.get("results.legend_position"),
            size=float(s.get("results.legend_size")),
        )

    def save(self) -> None:
        from puzzle.settings import current

        s = current()
        for key, value in (
            ("results.colormap", self.colormap),
            ("results.invert_colors", self.invert),
            ("results.bands", self.bands),
            ("results.outside_color", self.outside),
            ("results.legend_position", self.position),
            ("results.legend_size", self.size),
        ):
            s.set(key, value)

    def cmap(self) -> str:
        return f"{self.colormap}_r" if self.invert else self.colormap

    def mesh_args(self, custom_range: bool) -> dict:
        """add_mesh keywords for the colours (the bar's own are `bar_args`)."""
        out: dict = {"cmap": self.cmap()}
        if self.bands:
            out["n_colors"] = self.bands
        if custom_range and self.outside != "none":
            out["above_color"] = out["below_color"] = COLOURS[self.outside]
        return out

    def finish(self, plotter) -> None:
        """After the mesh is added: a horizontal bar's numbers go under it, clear of
        its title (VTK puts both above it otherwise)."""
        if self.position in ("left", "right"):
            return
        for bar in getattr(plotter, "scalar_bars", {}).values():
            bar.SetTextPositionToPrecedeScalarBar()

    def bar_args(self, title: str, fmt: str) -> dict:
        """scalar_bar_args: placed and sized; fractions of the view."""
        s = self.size
        vertical = self.position in ("left", "right")
        if vertical:
            width, height = 0.035 * s, min(0.55 * s, 0.85)
            x = 0.03 if self.position == "left" else 0.97 - width - 0.07 * s
            y = 0.9 - height
        else:
            width, height = min(0.45 * s, 0.9), 0.06 * s  # the bar, its numbers, its title
            x = 0.5 - width / 2
            y = 0.06 if self.position == "bottom" else 0.93 - height - 0.04 * s
        return {
            "title": title,
            "vertical": vertical,
            "position_x": x,
            "position_y": y,
            "width": width,
            "height": height,
            "fmt": fmt,
            "color": "black",
            "title_font_size": round(14 * s),
            "label_font_size": round(12 * s),
            # A label per band edge on a vertical bar; five across, where they'd crowd.
            "n_labels": (self.bands + 1) if self.bands and vertical else 5,
        }


def bar_format(lo: float, hi: float) -> str:
    """Colour-bar labels as people write numbers (ADR 0030): plain, to three significant
    figures of the largest, between 1e-3 and 1e5; else with an exponent."""
    top = max(abs(lo), abs(hi))
    if not np.isfinite(top) or top == 0:
        return "%.3g"
    if 1e-3 <= top < 1e5:
        decimals = max(0, 2 - int(np.floor(np.log10(top))))
        return f"%.{decimals}f"
    return "%.3e"
