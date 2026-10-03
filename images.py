"""Image validation and normalisation for every upload path.

Nothing a user uploads is stored as-is (except animations, see below).  Each file
is decoded with Pillow, checked, shrunk to a sensible size for what it is, and
re-encoded as WebP (typically a third to a half the size of the PNG/JPEG), which:
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
OPTIMIZE_MAX_DIM = 2000                       # px, longest edge of a map (kept for callers that name it)
MAX_DIM = {'avatars': 512, 'sheets': 2400, 'maps': OPTIMIZE_MAX_DIM}   # px, longest edge, per kind
QUALITY = {'avatars': 85, 'sheets': 85, 'maps': 90}                    # lossy WebP quality, per kind

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


def _webp_ready(img):
    """WebP stores RGB or RGBA; everything else (palette, grey, CMYK, 16-bit...) is converted first."""
    if img.mode in ('RGB', 'RGBA'):
        return img
    has_alpha = 'A' in img.getbands() or (img.mode == 'P' and 'transparency' in img.info)
    return img.convert('RGBA' if has_alpha else 'RGB')


def _few_colours(img):
    """True for flat artwork (grids, line art, pixel art) -- lossless is smaller and sharper there."""
    try:
        small = img.convert('RGBA')
        small.thumbnail((512, 512), Image.NEAREST)          # a cheap sample is enough to tell
        return small.getcolors(256) is not None
    except Exception:
        return False


def _encode(img, quality):
    """Smallest of: lossy WebP at `quality`, and (for flat artwork only) lossless WebP."""
    img = _webp_ready(img)
    buf = io.BytesIO()
    img.save(buf, 'WEBP', quality=quality, method=4)
    best = buf.getvalue()
    if _few_colours(img):
        buf = io.BytesIO()
        img.save(buf, 'WEBP', lossless=True, quality=75, method=4)
        if len(buf.getvalue()) < len(best):
            best = buf.getvalue()
    return best


def process_image(data, kind, cap_dimension=None):
    """Validate + normalise raw upload bytes.  Raises ImageError if unacceptable.

    kind:          'avatars' | 'sheets' | 'maps' -- selects the input size limit, the longest-edge
                   cap (MAX_DIM) and the WebP quality (QUALITY).
    cap_dimension: overrides the kind's longest-edge cap.

    Every still image is stored as WebP, whatever it was uploaded as.  Animated images
    are kept byte-for-byte (re-encoding would flatten them).
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

    # Animations are kept byte-for-byte, after making sure the whole stream decodes.
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

    cap = cap_dimension or MAX_DIM.get(kind, MAX_DIM['sheets'])
    if max(img.size) > cap:
        img = img.copy()
        img.thumbnail((cap, cap), Image.LANCZOS)

    quality = QUALITY.get(kind, 85)
    out = _encode(img, quality)

    # Guarantee the file can be served: lower the quality, then the dimensions, until it fits.
    tries = 0
    while len(out) > SERVE_LIMIT and tries < 10:
        tries += 1
        if quality > 55:
            quality -= 10
        else:
            w, h = img.size
            img = img.resize((max(1, int(w * 0.85)), max(1, int(h * 0.85))), Image.LANCZOS)
        out = _encode(img, quality)
    if len(out) > SERVE_LIMIT:
        raise ImageError('The image is too large to store.')

    return Processed(out, 'webp', 'image/webp', img.size[0], img.size[1])


def blank_canvas(width, height):
    """A plain white PNG (used for maps created without a background image)."""
    buf = io.BytesIO()
    Image.new('RGB', (width, height), (255, 255, 255)).save(buf, 'PNG', optimize=True)
    return Processed(buf.getvalue(), 'png', 'image/png', width, height)
