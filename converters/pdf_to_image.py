"""
PDF to Images converter.
Uses PyMuPDF (fitz) to render each page as a high-quality image.
Returns a ZIP file containing all page images.
"""
import os
import zipfile
import fitz  # PyMuPDF

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
        RuntimeError: If conversion fails.
    """
    if not os.path.exists(input_path):
        raise ValueError(f"Input file not found: {input_path}")
    
    if not input_path.lower().endswith('.pdf'):
        raise ValueError("Input file must be a PDF.")
    
    output_dir = os.path.dirname(output_path)
    temp_files = []
    
    try:
        # Open PDF with PyMuPDF
        doc = fitz.open(input_path)
        if len(doc) == 0:
            raise RuntimeError("PDF contains no renderable pages.")
            
        # 200 DPI is ~2.77 zoom factor since default is 72 DPI
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        
        for i in range(len(doc)):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat)
            
            ext = 'png' if fmt == 'png' else 'jpg'
            img_path = os.path.join(output_dir, f"page_{i + 1}.{ext}")
            
            # Save the pixmap directly
            pix.save(img_path)
            temp_files.append(img_path)
            
        doc.close()
        
        # Create ZIP
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for img_path in temp_files:
                zf.write(img_path, os.path.basename(img_path))
        
    except Exception as e:
        raise RuntimeError(f"PDF to Images conversion failed: {str(e)}")
        
    finally:
        # Clean up individual images
        for img_path in temp_files:
            try:
                os.remove(img_path)
            except OSError:
                pass
    
    # Verify output
    if not os.path.exists(output_path):
        raise RuntimeError("Conversion produced no output file.")
    if os.path.getsize(output_path) < 100:
        raise RuntimeError("Conversion produced an empty ZIP.")
    
    return output_path
