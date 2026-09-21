"""images.process_image: what gets accepted, rejected and normalised."""
import io, os
import pytest
from PIL import Image
import images
from images import ImageError, process_image, SERVE_LIMIT

pytestmark = pytest.mark.skipif(os.environ.get('LEDGER_TEST_BACKEND', 'sqlite') == 'sqlite', reason='new-code unit tests')


def enc(img, fmt, **kw):
    b = io.BytesIO(); img.save(b, fmt, **kw); return b.getvalue()

def noise(w, h):
    return Image.frombytes('RGB', (w, h), os.urandom(w * h * 3))


@pytest.mark.parametrize('fmt, ext', [('PNG', 'png'), ('JPEG', 'jpg'), ('GIF', 'png'), ('WEBP', 'webp')])
def test_valid_formats_are_accepted_and_extension_comes_from_content(fmt, ext):
    p = process_image(enc(Image.new('RGB', (40, 30), (9, 9, 9)), fmt), 'avatars')
    assert (p.ext, p.width, p.height) == (ext, 40, 30)
    assert Image.open(io.BytesIO(p.data)).size == (40, 30)


@pytest.mark.parametrize('blob', [b'', b'not an image', b'<html><script>alert(1)</script></html>', b'\x89PNG\r\n\x1a\n' + b'garbage' * 50,
                                  b'GIF89a' + b'\x00' * 20, b'%PDF-1.4 ' + b'x' * 500])
def test_non_images_are_rejected(blob):
    with pytest.raises(ImageError):
        process_image(blob, 'avatars')


def test_unsupported_but_real_image_formats_are_rejected():
    with pytest.raises(ImageError):
        process_image(enc(Image.new('RGB', (10, 10)), 'BMP'), 'avatars')
    with pytest.raises(ImageError):
        process_image(enc(Image.new('RGB', (10, 10)), 'TIFF'), 'avatars')


def test_polyglot_payload_after_the_image_is_stripped():
    png = enc(Image.new('RGB', (8, 8)), 'PNG') + b'<script>alert(1)</script>'
    out = process_image(png, 'avatars').data
    assert b'<script>' not in out and Image.open(io.BytesIO(out)).size == (8, 8)


def test_exif_orientation_is_baked_in_and_metadata_dropped():
    img = Image.new('RGB', (60, 20), (200, 0, 0))
    exif = Image.Exif(); exif[0x0112] = 6; exif[0x010F] = 'SecretCameraMaker'
    p = process_image(enc(img, 'JPEG', exif=exif), 'avatars')
    out = Image.open(io.BytesIO(p.data))
    assert out.size == (20, 60) and 'SecretCameraMaker' not in str(out.getexif().values())      # rotated, EXIF gone


def test_decompression_bomb_is_rejected_cheaply():
    huge = Image.new('1', (20000, 20000))                        # ~400M px, compresses to a tiny file
    blob = enc(huge, 'PNG', optimize=True)
    assert len(blob) < 1_000_000
    with pytest.raises(ImageError):
        process_image(blob, 'maps')


def test_input_size_limits_per_kind():
    big = b'\x89PNG' + b'0' * (images.KIND_MAX_BYTES['avatars'] + 1)
    with pytest.raises(ImageError):
        process_image(big, 'avatars')


def test_large_noisy_photo_is_shrunk_to_a_servable_size():
    blob = enc(noise(3000, 2000), 'PNG')                          # incompressible ~18 MB
    assert len(blob) > 10_000_000
    p = process_image(blob, 'maps', cap_dimension=images.OPTIMIZE_MAX_DIM)
    assert len(p.data) <= SERVE_LIMIT and max(p.width, p.height) <= 2000
    Image.open(io.BytesIO(p.data)).verify()


def test_big_jpeg_gets_recompressed_towards_1mb():
    blob = enc(noise(2400, 1800), 'JPEG', quality=98)
    assert len(blob) > 1_048_576
    p = process_image(blob, 'sheets')
    assert p.ext == 'jpg' and len(p.data) <= 1_048_576 * 1.05 and max(p.width, p.height) <= 2000


def test_animated_gif_is_kept_animated():
    frames = []
    for i in range(4):
        im = Image.new('RGB', (16, 16), (255, 255, 255))
        im.paste((i * 60, 200 - i * 40, 30 * i), (i * 3, i * 3, i * 3 + 6, i * 3 + 6))     # a square that moves
        frames.append(im.convert('P', palette=Image.ADAPTIVE))
    b = io.BytesIO(); frames[0].save(b, 'GIF', save_all=True, append_images=frames[1:], duration=80, loop=0)
    assert Image.open(io.BytesIO(b.getvalue())).n_frames == 4                              # fixture really is animated
    p = process_image(b.getvalue(), 'avatars')
    assert p.ext == 'gif' and Image.open(io.BytesIO(p.data)).n_frames == 4


def test_transparency_survives_png_roundtrip():
    p = process_image(enc(Image.new('RGBA', (10, 10), (1, 2, 3, 0)), 'PNG'), 'avatars')
    assert Image.open(io.BytesIO(p.data)).convert('RGBA').getpixel((0, 0))[3] == 0


def test_blank_canvas():
    p = images.blank_canvas(300, 200)
    assert (p.width, p.height, p.ext) == (300, 200, 'png') and Image.open(io.BytesIO(p.data)).size == (300, 200)
