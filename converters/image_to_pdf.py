"""
Image(s) to PDF converter.
Uses Pillow to read images and embed them into a properly-sized PDF.
Supports JPG, PNG, WebP, BMP, TIFF.
"""
import os
from PIL import Image


def convert(input_path: str, output_path: str) -> str:
    """
    Convert one or more image files to a single PDF.
    
    Args:
        input_path: Absolute path to an image file, OR a directory containing images.
        output_path: Absolute path for the output PDF file.
    
    Returns:
        The output_path on success.
    
    Raises:
        ValueError: If input doesn't exist or is not a supported image.
        RuntimeError: If conversion fails.
    """
    SUPPORTED = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif')
    
    # Collect image paths
    if os.path.isdir(input_path):
        image_paths = sorted([
            os.path.join(input_path, f) 
            for f in os.listdir(input_path) 
            if f.lower().endswith(SUPPORTED)
        ])
        if not image_paths:
            raise ValueError("No supported images found in directory.")
    elif os.path.isfile(input_path):
        if not input_path.lower().endswith(SUPPORTED):
            raise ValueError(f"Unsupported image format. Supported: {', '.join(SUPPORTED)}")
        image_paths = [input_path]
    else:
        raise ValueError(f"Input not found: {input_path}")
    
    try:
        images = []
        for path in image_paths:
            img = Image.open(path)
            # Convert to RGB if necessary (RGBA/P modes can't save to PDF directly)
            if img.mode in ('RGBA', 'P', 'LA'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if 'A' in img.mode else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            images.append(img)
        
        if not images:
            raise RuntimeError("No images could be loaded.")
        
        # Save as PDF — first image is the base, rest are appended
        if len(images) == 1:
            images[0].save(output_path, 'PDF', resolution=150)
        else:
            images[0].save(
                output_path, 'PDF', resolution=150,
                save_all=True, append_images=images[1:]
            )
    
    except (ValueError, RuntimeError):
        raise
    except Exception as e:
        raise RuntimeError(f"Image to PDF conversion failed: {str(e)}")
    
    # Verify output
    if not os.path.exists(output_path):
        raise RuntimeError("Conversion produced no output file.")
    if os.path.getsize(output_path) < 100:
        raise RuntimeError("Conversion produced an empty PDF.")
    
    return output_path
