#!/usr/bin/env python3
"""Build the Oaken texture and background assets in static/assets/ from the source files the owner supplied (DL-180).

Usage:  python scripts/build_oaken_assets.py <folder with the unzipped sources>
The folder must hold the files from Assets.zip and Background_Images.zip (names as supplied).
Needs Pillow and numpy (not part of the app's runtime requirements).  Outputs are committed; the sources are not.

What it makes
  textures/parchment/parchment-light.webp   seamless 1024 px tile (edges cross-faded)
  textures/parchment/parchment-dark.webp    seamless 1024 px tile, grey source warmed to charcoal-umber
  textures/wood/oak-dark.webp               1024 px tile (source already tiles)
  textures/wood/oak-light.webp              PLACEHOLDER: the dark oak re-graded lighter and warmer (no light oak was supplied)
  backgrounds/*.webp                        the five paintings, 1920 px wide
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

OUT = Path(__file__).resolve().parent.parent / 'static' / 'assets'


def save(img, rel, **kw):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p, **kw)
    print(f'{rel}: {img.size}, {p.stat().st_size // 1024} KB')


def seamless_crossfade(img, band=0.18):
    """Make a tile repeat without a visible seam: blend each edge band with the opposite side."""
    a = np.asarray(img.convert('RGB')).astype(np.float32)
    h, w, _ = a.shape
    # the rolled copy is continuous across the original's edges; fade to it near the edges, keep the original inside
    r = np.roll(a, (h // 2, w // 2), axis=(0, 1))
    yy, xx = np.mgrid[0:h, 0:w]
    ex = np.minimum(xx, w - 1 - xx) / (w * band)
    ey = np.minimum(yy, h - 1 - yy) / (h * band)
    w_orig = np.clip(np.minimum(ex, ey), 0, 1)[..., None]
    out = a * w_orig + r * (1 - w_orig)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def mirror_tile(img):
    """2x2 mirrored tile: always seamless, fine for featureless noise."""
    w, h = img.size
    t = Image.new(img.mode, (w * 2, h * 2))
    t.paste(img, (0, 0))
    t.paste(img.transpose(Image.FLIP_LEFT_RIGHT), (w, 0))
    t.paste(img.transpose(Image.FLIP_TOP_BOTTOM), (0, h))
    t.paste(img.transpose(Image.ROTATE_180), (w, h))
    return t


def lum(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def build(src):
    src = Path(src)

    # --- parchment ---
    lp = Image.open(src / 'Light parchment.jpg').convert('RGB').resize((1024, 1024), Image.LANCZOS)
    save(seamless_crossfade(lp), 'textures/parchment/parchment-light.webp', quality=82, method=6)

    dp = Image.open(src / 'Dark parchment.jpg').convert('L')
    dp = mirror_tile(dp)
    g = np.asarray(dp).astype(np.float32) / 255
    g = (g - g.min()) / (g.max() - g.min())
    dark, mid = np.array([22, 25, 21], np.float32), np.array([52, 50, 41], np.float32)      # forest-charcoal to warm umber-grey
    warm = dark[None, None, :] * (1 - g[..., None]) + mid[None, None, :] * g[..., None]
    save(Image.fromarray(warm.astype(np.uint8)).resize((1024, 1024), Image.LANCZOS), 'textures/parchment/parchment-dark.webp', quality=82, method=6)

    # --- wood ---
    ow = Image.open(src / 'Dark wood.jpg').convert('RGB')
    save(ow, 'textures/wood/oak-dark.webp', quality=82, method=6)
    a = np.asarray(ow).astype(np.float32)
    lift = np.clip(a * 1.0 + (np.array([120, 84, 46], np.float32) - a.mean((0, 1))) * 1.0, 0, 255)       # shift mean to warm oak
    contrast = (lift - lift.mean((0, 1))) * 1.35 + lift.mean((0, 1))
    save(Image.fromarray(np.clip(contrast, 0, 255).astype(np.uint8)), 'textures/wood/oak-light.webp', quality=82, method=6)

    # --- backgrounds ---
    names = {
        'birmingham-museums-trust-VTRWL-_uvlk-unsplash.jpg': 'river-gold',
        'birmingham-museums-trust-rmyNjnJY_7g-unsplash.jpg': 'river-pale',
        'boston-public-library-s9LUPztrlig-unsplash.jpg': 'valley-light',
        'europeana-o676w2FZPT8-unsplash.jpg': 'torrent-storm',
        'europeana-yS_ef_wmzAM-unsplash.jpg': 'cliff-valley',
    }
    bgs = src.parent / 'bgs' if (src.parent / 'bgs').exists() else src
    for f, n in names.items():
        p = next(iter(list(src.rglob(f))), None)
        if not p:
            print('missing background', f)
            continue
        im = Image.open(p).convert('RGB')
        im = im.resize((1920, round(im.height * 1920 / im.width)), Image.LANCZOS)
        save(im, f'backgrounds/{n}.webp', quality=74, method=6)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(sys.argv[1])
