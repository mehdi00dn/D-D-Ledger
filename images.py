"""Image validation and normalisation for every upload path.

Nothing a user uploads is stored as-is (except animations, see below).  Each file
is decoded with Pillow, checked, and re-encoded, which:
  * proves it really is an image (a renamed .html/.exe is rejected),
  * strips EXIF/metadata and any payload hidden after the image data,
  * bounds memory use (decompression-bomb guard),
  * keeps the result small enough to be served through a serverless function
    (Vercel caps a function's response body at 4.5 MB),
  * derives the stored extension from the *detected format*, never from the
    user's file name (so non-ASCII names like 'نقشه.png' are harmless).
"""
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 50_000_000          # hard decode budget (Pillow errors at 2x this)

MAX_PIXELS = 50_000_000
MAX_SIDE = 16_000
SERVE_LIMIT = int(4_000_000)                  # bytes: stay under Vercel's 4.5 MB response cap
OPTIMIZE_THRESHOLD = 1 * 1024 * 1024          # files above this get recompressed (legacy behaviour)
OPTIMIZE_MAX_DIM = 2000                       # px, longest edge

# Maximum accepted *input* size per kind of upload (bytes).
KIND_MAX_BYTES = {
    'avatars': 8 * 1024 * 1024,
    'sheets': 25 * 1024 * 1024,
    'maps': 30 * 1024 * 1024,
}

FORMATS = {
    'PNG': ('png', 'image/png'),
    'JPEG': ('jpg', 'image/jpeg'),
    'GIF': ('gif', 'image/gif'),
    'WEBP': ('webp', 'image/webp'),
}
ALLOWED_CONTENT_TYPES = {ct for _, ct in FORMATS.values()}
EXT_TO_CONTENT_TYPE = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                       'gif': 'image/gif', 'webp': 'image/webp'}


class ImageError(ValueError):
    """The upload is not an acceptable image (message is safe to show a user)."""


@dataclass
class Processed:
    data: bytes
    ext: str
    content_type: str
    width: int
    height: int


def _encode(img, fmt, quality=None):
    buf = io.BytesIO()
    if fmt == 'JPEG':
        img = img.convert('RGB')
        img.save(buf, 'JPEG', quality=quality or 85, optimize=True)
    elif fmt == 'WEBP':
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGBA' if 'A' in img.getbands() or img.mode == 'P' else 'RGB')
        img.save(buf, 'WEBP', quality=quality or 85)
    else:
        img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def process_image(data, kind, cap_dimension=None):
    """Validate + normalise raw upload bytes.  Raises ImageError if unacceptable.

    kind:          'avatars' | 'sheets' | 'maps' (selects the input size limit)
    cap_dimension: always shrink the longest edge to this (maps pass 2000).  When
                   None, only files above OPTIMIZE_THRESHOLD are shrunk (legacy rule).
    """
    limit = KIND_MAX_BYTES.get(kind, KIND_MAX_BYTES['sheets'])
    if not data:
        raise ImageError('The file is empty.')
    if len(data) > limit:
        raise ImageError('The image is too large (limit %d MB).' % (limit // (1024 * 1024)))

    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        width, height = img.size
    except Exception:
        raise ImageError('This file is not a valid image.')
    if fmt not in FORMATS:
        raise ImageError('Unsupported image type (use PNG, JPEG, GIF or WebP).')
    if width < 1 or height < 1 or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
        raise ImageError('The image dimensions are too large.')

    ext, ctype = FORMATS[fmt]

    # Animations are kept byte-for-byte (re-encoding would flatten them to one frame),
    # after making sure the whole stream decodes.
    if getattr(img, 'is_animated', False) and getattr(img, 'n_frames', 1) > 1:
        try:
            for frame in range(img.n_frames):
                img.seek(frame)
                img.load()
        except Exception:
            raise ImageError('This file is not a valid image.')
        if len(data) > SERVE_LIMIT:
            raise ImageError('The animated image is too large.')
        return Processed(data, ext, ctype, width, height)

    try:
        img.load()                                    # full decode == integrity check
        img = ImageOps.exif_transpose(img)            # bake in phone-camera rotation, drop EXIF
    except Exception:
        raise ImageError('This file is not a valid image.')

    shrink = cap_dimension or (OPTIMIZE_MAX_DIM if len(data) > OPTIMIZE_THRESHOLD else None)
    if shrink and max(img.size) > shrink:
        img = img.copy()
        img.thumbnail((shrink, shrink), Image.LANCZOS)

    # Above the legacy 1 MB threshold we recompress towards 1 MB; below it, just stay servable.
    target = OPTIMIZE_THRESHOLD if len(data) > OPTIMIZE_THRESHOLD else SERVE_LIMIT
    out, out_fmt = None, fmt

    if fmt in ('JPEG', 'WEBP'):
        quality = 85
        while True:
            out = _encode(img, fmt, quality)
            if len(out) <= target or quality <= 40:
                break
            quality -= 10
    else:                                             # PNG (GIF single-frame is stored as PNG too)
        out_fmt = 'PNG'
        out = _encode(img, 'PNG')
        if len(out) > target:                          # legacy fallback: 256-colour palette
            try:
                pal = img.convert('RGBA').convert('P', palette=Image.ADAPTIVE, colors=256)
                cand = _encode(pal, 'PNG')
                if len(cand) < len(out):
                    out = cand
            except Exception:
                pass

    # Guarantee the file can be served: shrink dimensions / switch to lossy WebP if still too big.
    tries = 0
    while len(out) > SERVE_LIMIT and tries < 8:
        tries += 1
        if out_fmt == 'PNG':
            out, out_fmt = _encode(img, 'WEBP', 82), 'WEBP'
        if len(out) > SERVE_LIMIT:
            w, h = img.size
            img = img.resize((max(1, int(w * 0.85)), max(1, int(h * 0.85))), Image.LANCZOS)
            out = _encode(img, out_fmt, 80 if out_fmt != 'PNG' else None)
    if len(out) > SERVE_LIMIT:
        raise ImageError('The image is too large to store.')

    ext, ctype = FORMATS[out_fmt]
    return Processed(out, ext, ctype, img.size[0], img.size[1])


def blank_canvas(width, height):
    """A plain white PNG (used for maps created without a background image)."""
    buf = io.BytesIO()
    Image.new('RGB', (width, height), (255, 255, 255)).save(buf, 'PNG', optimize=True)
    return Processed(buf.getvalue(), 'png', 'image/png', width, height)
