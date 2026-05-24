import os
def convert(input_path: str, output_path: str) -> str:
    if not os.path.exists(input_path):
        raise ValueError(f"Input file not found: {input_path}")
        
    ext = input_path.lower().split(".")[-1]
    
    if ext in ["docx", "doc"]:
        try:
            from docx2pdf import convert as d2p
            d2p(input_path, output_path)
            
            if not os.path.exists(output_path):
                raise RuntimeError("Conversion produced no output.")
            return output_path
        except Exception as e:
            raise RuntimeError(f"DOCX to PDF conversion failed: {str(e)}")
            
    else:
        raise RuntimeError(f"Server cannot convert .{ext} files to PDF natively without LibreOffice installed on the host.")
