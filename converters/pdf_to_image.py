"""
PDF to Images converter.
Uses pdf2image (poppler wrapper) to render each page as a high-quality image.
Returns a ZIP file containing all page images.
"""
import os
import zipfile
from pdf2image import convert_from_path


def convert(input_path: str, output_path: str, fmt: str = 'png', dpi: int = 200) -> str:
    """
    Convert a PDF file to a ZIP of images (one per page).
    
    Args:
        input_path: Absolute path to the source PDF file.
        output_path: Absolute path for the output ZIP file (must end in .zip).
        fmt: Image format — 'png' or 'jpg'.
        dpi: Resolution for rendering. 200 is a good balance of quality/size.
    
    Returns:
        The output_path on success.
    
    Raises:
        ValueError: If input file doesn't exist or is not a PDF.
        RuntimeError: If conversion fails (e.g. poppler not installed).
    """
    if not os.path.exists(input_path):
        raise ValueError(f"Input file not found: {input_path}")
    
    if not input_path.lower().endswith('.pdf'):
        raise ValueError("Input file must be a PDF.")
    
    output_dir = os.path.dirname(output_path)
    
    try:
        # pdf2image renders every page to a PIL Image
        # NOTE: Requires poppler to be installed on the system.
        #   Windows: choco install poppler  OR download from GitHub
        #   Linux:   apt-get install poppler-utils
        #   macOS:   brew install poppler
        images = convert_from_path(input_path, dpi=dpi, fmt=fmt)
        
        if not images:
            raise RuntimeError("PDF contains no renderable pages.")
        
        # Save each page image temporarily, then zip them
        temp_files = []
        for i, img in enumerate(images):
            ext = 'png' if fmt == 'png' else 'jpg'
            img_path = os.path.join(output_dir, f"page_{i + 1}.{ext}")
            img.save(img_path, fmt.upper())
            temp_files.append(img_path)
        
        # Create ZIP
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for img_path in temp_files:
                zf.write(img_path, os.path.basename(img_path))
        
        # Clean up individual images
        for img_path in temp_files:
            try:
                os.remove(img_path)
            except OSError:
                pass
    
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            f"PDF to Images conversion failed: {str(e)}. "
            "Make sure poppler-utils is installed on this system."
        )
    
    # Verify output
    if not os.path.exists(output_path):
        raise RuntimeError("Conversion produced no output file.")
    if os.path.getsize(output_path) < 100:
        raise RuntimeError("Conversion produced an empty ZIP.")
    
    return output_path
