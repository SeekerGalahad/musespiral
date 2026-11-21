"""infra/visual — L1 光栅化件（浏览器 SVG→PNG；不上探 core/services/studio）。"""
from __future__ import annotations

from .raster import raster_svg, rasterize_svg

__all__ = ["raster_svg", "rasterize_svg"]
