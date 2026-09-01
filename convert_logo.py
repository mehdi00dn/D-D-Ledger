#!/usr/bin/env python3
"""
Convert a source image to favicon and logo files in various formats and sizes.
Usage: python convert_logo.py <source_image_path>
"""

import sys
import os
from PIL import Image

def convert_logo_and_favicon(source_path):
    """Convert source image to favicon and logo files."""
    
    if not os.path.exists(source_path):
        print(f"Error: Source image not found: {source_path}")
        sys.exit(1)
    
    # Open the source image
    img = Image.open(source_path)
    
    # Convert RGBA if needed, ensuring good quality
    if img.mode != 'RGB' and img.mode != 'RGBA':
        img = img.convert('RGBA')
    elif img.mode == 'RGBA':
        # Keep as is for PNG, but create RGB version for JPEG
        pass
    
    static_dir = 'static'
    
    # Create favicon versions
    print("Creating favicon files...")
    favicon_sizes = [16, 32, 64]
    
    for size in favicon_sizes:
        favicon_img = img.copy()
        favicon_img.thumbnail((size, size), Image.Resampling.LANCZOS)
        # Ensure square
        new_img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        offset = ((size - favicon_img.size[0]) // 2, (size - favicon_img.size[1]) // 2)
        new_img.paste(favicon_img, offset, favicon_img if favicon_img.mode == 'RGBA' else None)
        new_img.save(os.path.join(static_dir, f'favicon-{size}x{size}.png'), 'PNG')
        print(f"  ✓ favicon-{size}x{size}.png")
    
    # Create ICO favicon
    print("Creating favicon.ico...")
    ico_sizes = [(16, 16), (32, 32), (64, 64)]
    ico_images = []
    for size in ico_sizes:
        favicon_img = img.copy()
        favicon_img.thumbnail(size, Image.Resampling.LANCZOS)
        new_img = Image.new('RGBA', size, (0, 0, 0, 0))
        offset = ((size[0] - favicon_img.size[0]) // 2, (size[1] - favicon_img.size[1]) // 2)
        new_img.paste(favicon_img, offset, favicon_img if favicon_img.mode == 'RGBA' else None)
        ico_images.append(new_img)
    
    ico_images[0].save(
        os.path.join(static_dir, 'favicon.ico'),
        'ICO',
        sizes=[(16, 16), (32, 32), (64, 64)],
        append_images=ico_images[1:]
    )
    print("  ✓ favicon.ico")
    
    # Create logo versions
    print("Creating logo files...")
    logo_sizes = {'logo-256.png': 256, 'logo-128.png': 128, 'logo.png': 512}
    
    for filename, size in logo_sizes.items():
        logo_img = img.copy()
        logo_img.thumbnail((size, size), Image.Resampling.LANCZOS)
        new_img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        offset = ((size - logo_img.size[0]) // 2, (size - logo_img.size[1]) // 2)
        new_img.paste(logo_img, offset, logo_img if logo_img.mode == 'RGBA' else None)
        new_img.save(os.path.join(static_dir, filename), 'PNG')
        print(f"  ✓ {filename}")
    
    print("\n✅ All files created successfully!")
    print("\nFiles created:")
    print("  - favicon.ico (multi-size)")
    print("  - favicon-16x16.png")
    print("  - favicon-32x32.png")
    print("  - favicon-64x64.png")
    print("  - logo.png (512x512)")
    print("  - logo-128.png")
    print("  - logo-256.png")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python convert_logo.py <source_image_path>")
        sys.exit(1)
    
    convert_logo_and_favicon(sys.argv[1])
