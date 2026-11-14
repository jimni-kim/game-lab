"""Bounded PNG decoding: a capture is read only when it is a plain, opaque PNG of a size the harness holds it to."""
from __future__ import annotations

from io import BytesIO
import warnings
from typing import Any

# the most pixels a capture may have
MAX_IMAGE_PIXELS = 8_000_000
MAX_IMAGE_BYTES = 4_000_000


def decode(png: bytes, viewport: list[int] | None) -> Any:
    """The PNG as RGB, of exactly `viewport` pixels, or of any bounded size when there is none yet to
    hold it to (a screen whose board is still to be measured)."""
    from PIL import Image, UnidentifiedImageError

    if not isinstance(png, bytes) or not png or len(png) > MAX_IMAGE_BYTES:
        raise ValueError("invalid_image")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(png)) as image:
                if image.format != "PNG" or (viewport is not None and image.size != tuple(viewport)) or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("image_geometry_mismatch")
                if getattr(image, "n_frames", 1) != 1 or image.mode not in ("RGB", "RGBA"):
                    raise ValueError("unsupported_image")
                image.load()
                if image.mode == "RGBA" and image.getchannel("A").getextrema() != (255, 255):
                    raise ValueError("transparent_image")
                return image.convert("RGB")
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("invalid_image") from error
