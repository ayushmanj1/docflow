"""
PDF to Word (DOCX) converter.
Uses pdf2docx which preserves layout, tables, images, and formatting.
"""
import os
from pdf2docx import Converter


def convert(input_path: str, output_path: str) -> str:
    """
    Convert a PDF file to DOCX format.
    
    Args:
        input_path: Absolute path to the source PDF file.
        output_path: Absolute path for the output DOCX file.
    
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
    
    import fitz  # PyMuPDF
    doc = fitz.open(input_path)
    has_text = False
    for page in doc:
        if page.get_text().strip():
            has_text = True
            break
    
    # Close the document immediately so we don't lock the file on Windows!
    doc.close()
    
    if has_text:
        # Standard text-based conversion
        cv = None
        try:
            cv = Converter(input_path)
            cv.convert(output_path)
        except Exception as e:
            raise RuntimeError(f"PDF to DOCX conversion failed: {str(e)}")
        finally:
            if cv:
                cv.close()
    else:
        # Image-based fallback for scanned PDFs
        import docx
        from docx.shared import Inches
        
        word_doc = docx.Document()
        # Reduce margins to fit the images better
        sections = word_doc.sections
        for section in sections:
            section.top_margin = Inches(0.5)
            section.bottom_margin = Inches(0.5)
            section.left_margin = Inches(0.5)
            section.right_margin = Inches(0.5)
            
        # We need to reopen it since we closed it earlier
        doc = fitz.open(input_path)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=200)
            img_path = f"{input_path}_page_{i}.png"
            pix.save(img_path)
            word_doc.add_picture(img_path, width=Inches(7.0))
            os.remove(img_path)
            
        word_doc.save(output_path)
        doc.close()
    
    # Verify output was created and is not empty
    if not os.path.exists(output_path):
        raise RuntimeError("Conversion produced no output file.")
    if os.path.getsize(output_path) < 100:
        raise RuntimeError("Conversion produced an empty or corrupt file.")
    
    return output_path
