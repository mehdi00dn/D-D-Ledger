"""Map fog of war: mask encoding and the Player-safe image composite.

The mask has one bit per CELL x CELL block of the map image (1 = fogged), packed
row by row with every row padded to a whole byte -- the layout Pillow's mode '1'
uses, so it converts to an image with no reshuffling.
"""
import base64
import io
import zlib

CELL = 8                      # map pixels per mask cell
MAX_CELLS = 1_000_000         # sanity cap (an 8000 x 8000 px map)
FOG_RGB = (205, 209, 215)  # what a Player sees under fog -- the real map is never sent


def grid_dims(width, height):
    """(cols, rows) of the mask for an image of this size, or None if the size is unusable."""
    if not width or not height or width < 1 or height < 1:
        return None
    cols, rows = -(-int(width) // CELL), -(-int(height) // CELL)
    return (cols, rows) if cols * rows <= MAX_CELLS else None


def packed_len(cols, rows):
    return ((cols + 7) // 8) * rows


def decode_client(b64, cols, rows):
    """Raw packed bytes from a client-sent base64 string, or ValueError if malformed."""
    try:
        raw = base64.b64decode(b64 or '', validate=True)
    except (ValueError, TypeError):
        raise ValueError('bad mask encoding')
    if len(raw) != packed_len(cols, rows):
        raise ValueError('mask does not match the map size')
    return raw


def all_fogged(cols, rows):
    row = bytearray(b'\xff' * ((cols + 7) // 8))
    if cols % 8:                                  # keep the padding bits of each row at 0
        row[-1] = (0xff << (8 - cols % 8)) & 0xff
    return bytes(row) * rows


def any_fogged(raw):
    return any(raw)


def to_storage(raw):
    return base64.b64encode(zlib.compress(raw, 6)).decode('ascii')


def from_storage(stored, cols, rows):
    """Raw packed bytes from the database column, or None if absent/corrupt."""
    if not stored or not cols or not rows:
        return None
    try:
        raw = zlib.decompress(base64.b64decode(stored))
    except (ValueError, zlib.error):
        return None
    return raw if len(raw) == packed_len(cols, rows) else None


def to_client(raw):
    return base64.b64encode(raw).decode('ascii')


def point_fogged(raw, cols, rows, x, y):
    """True if map-pixel (x, y) lies in a fogged cell. Points off the map are never fogged."""
    try:
        cx, cy = int(x // CELL), int(y // CELL)
    except (TypeError, ValueError, OverflowError):
        return False
    if cx < 0 or cy < 0 or cx >= cols or cy >= rows:
        return False
    return bool(raw[cy * ((cols + 7) // 8) + (cx >> 3)] & (0x80 >> (cx & 7)))


def composite(image_bytes, raw, cols, rows):
    """The map with every fogged area replaced by flat fog colour -> (bytes, content_type).
    The fog edge is grown and softened, never shrunk, so no fogged pixel survives the smoothing."""
    from PIL import Image, ImageFilter
    im = Image.open(io.BytesIO(image_bytes))
    fmt = im.format
    im.load()
    has_alpha = im.mode in ('RGBA', 'LA', 'P') and 'transparency' in im.info or im.mode in ('RGBA', 'LA')
    base = im.convert('RGBA')
    mask = Image.frombytes('1', (cols, rows), raw).convert('L')
    # Grow the fog by one cell (so the soft edge starts outside the hidden area), upscale smoothly and
    # blur lightly: a rounded edge instead of square cells, with every fogged pixel still fully opaque.
    mask = mask.filter(ImageFilter.MaxFilter(3))
    mask = mask.resize(base.size, Image.BILINEAR).filter(ImageFilter.GaussianBlur(CELL * 0.3))
    mask = mask.point(lambda v: min(255, int(v * 1.5)))
    fog = Image.new('RGBA', base.size, FOG_RGB + (255,))
    out = Image.composite(fog, base, mask)
    buf = io.BytesIO()
    if fmt == 'JPEG' and not has_alpha:
        out.convert('RGB').save(buf, 'JPEG', quality=88)
        return buf.getvalue(), 'image/jpeg'
    out.save(buf, 'PNG')
    return buf.getvalue(), 'image/png'
