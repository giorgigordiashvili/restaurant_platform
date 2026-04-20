"""
BlurHash generation helpers.

BlurHash encodes a ~30-character string that the frontend decodes into a
32x32 RGBA placeholder — a color-accurate blurred preview that ships inline
with the API response (no extra network hop). Used for LQIP on slow
connections so users see shapes/colors immediately instead of a grey box.

Package: https://github.com/woltapp/blurhash  (pure-python reference impl)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 4x3 components balance placeholder fidelity vs. encoded-string length
# (~30 chars). Increase for more detail at the cost of longer strings.
BLURHASH_COMPONENTS_X = 4
BLURHASH_COMPONENTS_Y = 3

# Downscale the source to this max dimension before encoding. BlurHash
# encoding is O(width * height * components²); full-resolution sources make
# this multi-second for little benefit.
ENCODE_MAX_DIMENSION = 128


def generate_blurhash(image_file) -> str:
    """
    Given a Django ImageField file (or any file-like image), return the
    BlurHash string. Returns an empty string on any failure — blurhash is
    a nice-to-have LQIP, never worth blowing up an upload for.
    """
    try:
        import blurhash as blurhash_lib
        import numpy as np
        from PIL import Image

        image_file.seek(0)
        with Image.open(image_file) as img:
            img = img.convert("RGB")
            img.thumbnail((ENCODE_MAX_DIMENSION, ENCODE_MAX_DIMENSION))
            # The blurhash lib expects a 3D array (y, x, rgb) of 0-255 ints.
            # np.asarray on a PIL RGB image gives exactly that shape.
            array = np.asarray(img)
            return blurhash_lib.encode(
                array,
                components_x=BLURHASH_COMPONENTS_X,
                components_y=BLURHASH_COMPONENTS_Y,
            )
    except Exception:
        logger.warning("blurhash encode failed", exc_info=True)
        return ""
