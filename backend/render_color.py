"""Explicit working formats; project JSON is independent of export processing."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ColorPipeline:
    mode: str = "rgb"

    def __post_init__(self):
        if self.mode not in ("rgb", "legacy"):
            raise ValueError("Unknown color processing. Choose 'rgb' or 'legacy'.")

    @classmethod
    def from_preset(cls, preset):
        return cls((preset or {}).get("color_processing", "rgb"))

    @property
    def rgb(self): return self.mode == "rgb"

    @property
    def alpha_format(self): return "rgba" if self.rgb else "yuva420p"

    @property
    def blend_format(self): return "gbrap" if self.rgb else "yuva420p"

    @property
    def output_format(self): return "rgb24" if self.rgb else "yuv420p"

    @property
    def overlay_option(self): return ":format=rgb" if self.rgb else ""
