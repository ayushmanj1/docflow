"""
DocFlow — Flask Backend for File Conversion API.

Endpoints:
  POST /api/convert/pdf-to-docx     PDF → Word
  POST /api/convert/docx-to-pdf     Word → PDF
  POST /api/convert/pdf-to-images   PDF → ZIP of images
  POST /api/convert/image-to-pdf    Image → PDF
  POST /api/convert/xlsx-to-pdf     Excel → PDF
  POST /api/convert/pptx-to-pdf     PowerPoint → PDF

All endpoints accept multipart/form-data with a 'file' field.
Max upload: 10MB. Files are auto-deleted after download.
"""

import os
import uuid
import atexit
import shutil
from flask import Flask, request, send_file, jsonify, after_this_request
from flask_cors import CORS

# Import converters
from converters import pdf_to_word, word_to_pdf, pdf_to_image, image_to_pdf, office_to_pdf

# =============================================================================
# APP SETUP
# =============================================================================
app = Flask(__name__)
CORS(app)  # Allow frontend on any origin to call the API

# Config
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB max upload
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'outputs')

# Create temp directories
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


def cleanup_dirs():
    """Clean up temp directories on server shutdown."""
    for d in [UPLOAD_DIR, OUTPUT_DIR]:
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d, exist_ok=True)

atexit.register(cleanup_dirs)


# =============================================================================
# HELPERS
# =============================================================================
def save_upload(file_storage, allowed_extensions: tuple) -> str:
    """
    Save an uploaded file to the uploads directory.
    
    - Validates the file extension.
    - Generates a UUID filename to avoid conflicts.
    - Fully writes to disk before returning.
    
    Returns the absolute path to the saved file.
    """
    if not file_storage or file_storage.filename == '':
        raise ValueError("No file uploaded.")
    
    original_ext = os.path.splitext(file_storage.filename)[1].lower()
    if original_ext not in allowed_extensions:
        raise ValueError(
            f"Unsupported file type '{original_ext}'. "
            f"Allowed: {', '.join(allowed_extensions)}"
        )
    
    # UUID filename to avoid collisions
    safe_name = f"{uuid.uuid4().hex}{original_ext}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    
    # CRITICAL: Fully write to disk before returning
    file_storage.save(save_path)
    file_storage.close()
    
    # Verify the file was written
    if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
        raise RuntimeError("File upload failed — file is empty after save.")
    
    return save_path


def make_output_path(extension: str) -> str:
    """Generate a unique output path with the given extension."""
    return os.path.join(OUTPUT_DIR, f"{uuid.uuid4().hex}{extension}")


def send_and_cleanup(output_path: str, download_name: str, mimetype: str):
    """
    Send file as response and schedule cleanup of both input and output files.
    
    CRITICAL: Uses after_this_request to delete files AFTER the response
    has been fully sent. This prevents the bug where cleanup happens before
    the download completes.
    """
    @after_this_request
    def cleanup(response):
        # Auto-delete output after download
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        return response
    
    return send_file(
        output_path,
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype
    )


def error_response(message: str, status: int = 400):
    """Return a JSON error response."""
    return jsonify({'error': message}), status


# =============================================================================
# ERROR HANDLERS
# =============================================================================
@app.errorhandler(413)
def too_large(e):
    return error_response("File too large. Maximum size is 10 MB.", 413)


@app.errorhandler(500)
def server_error(e):
    return error_response("Internal server error.", 500)


# =============================================================================
# HEALTH CHECK
# =============================================================================
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'DocFlow Converter API'})


# =============================================================================
# CONVERSION ENDPOINTS
# =============================================================================

# ─── PDF → DOCX ──────────────────────────────────────────────────────────────
@app.route('/api/convert/pdf-to-docx', methods=['POST'])
def api_pdf_to_docx():
    input_path = None
    try:
        input_path = save_upload(request.files.get('file'), ('.pdf',))
        output_path = make_output_path('.docx')
        
        pdf_to_word.convert(input_path, output_path)
        
        # Clean up input immediately (output cleaned after download)
        os.remove(input_path)
        
        return send_and_cleanup(
            output_path,
            download_name='converted.docx',
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        return error_response(str(e), 500)
    except Exception as e:
        return error_response(f"Unexpected error: {str(e)}", 500)
    finally:
        # Ensure input cleanup even on error
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


# ─── DOCX → PDF ──────────────────────────────────────────────────────────────
@app.route('/api/convert/docx-to-pdf', methods=['POST'])
def api_docx_to_pdf():
    input_path = None
    try:
        input_path = save_upload(request.files.get('file'), ('.docx', '.doc'))
        output_path = make_output_path('.pdf')
        
        word_to_pdf.convert(input_path, output_path)
        
        os.remove(input_path)
        
        return send_and_cleanup(
            output_path,
            download_name='converted.pdf',
            mimetype='application/pdf'
        )
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        return error_response(str(e), 500)
    except Exception as e:
        return error_response(f"Unexpected error: {str(e)}", 500)
    finally:
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


# ─── PDF → Images ────────────────────────────────────────────────────────────
@app.route('/api/convert/pdf-to-images', methods=['POST'])
def api_pdf_to_images():
    input_path = None
    try:
        fmt = request.form.get('format', 'png').lower()
        if fmt not in ('png', 'jpg', 'jpeg'):
            fmt = 'png'
        
        input_path = save_upload(request.files.get('file'), ('.pdf',))
        output_path = make_output_path('.zip')
        
        pdf_to_image.convert(input_path, output_path, fmt=fmt)
        
        os.remove(input_path)
        
        return send_and_cleanup(
            output_path,
            download_name='pages.zip',
            mimetype='application/zip'
        )
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        return error_response(str(e), 500)
    except Exception as e:
        return error_response(f"Unexpected error: {str(e)}", 500)
    finally:
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


# ─── Image → PDF ─────────────────────────────────────────────────────────────
@app.route('/api/convert/image-to-pdf', methods=['POST'])
def api_image_to_pdf():
    input_path = None
    try:
        input_path = save_upload(
            request.files.get('file'),
            ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif')
        )
        output_path = make_output_path('.pdf')
        
        image_to_pdf.convert(input_path, output_path)
        
        os.remove(input_path)
        
        return send_and_cleanup(
            output_path,
            download_name='converted.pdf',
            mimetype='application/pdf'
        )
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        return error_response(str(e), 500)
    except Exception as e:
        return error_response(f"Unexpected error: {str(e)}", 500)
    finally:
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


# ─── Excel → PDF ─────────────────────────────────────────────────────────────
@app.route('/api/convert/xlsx-to-pdf', methods=['POST'])
def api_xlsx_to_pdf():
    input_path = None
    try:
        input_path = save_upload(request.files.get('file'), ('.xlsx', '.xls'))
        output_path = make_output_path('.pdf')
        
        office_to_pdf.convert(input_path, output_path)
        
        os.remove(input_path)
        
        return send_and_cleanup(
            output_path,
            download_name='converted.pdf',
            mimetype='application/pdf'
        )
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        return error_response(str(e), 500)
    except Exception as e:
        return error_response(f"Unexpected error: {str(e)}", 500)
    finally:
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


# ─── PowerPoint → PDF ────────────────────────────────────────────────────────
@app.route('/api/convert/pptx-to-pdf', methods=['POST'])
def api_pptx_to_pdf():
    input_path = None
    try:
        input_path = save_upload(request.files.get('file'), ('.pptx', '.ppt'))
        output_path = make_output_path('.pdf')
        
        office_to_pdf.convert(input_path, output_path)
        
        os.remove(input_path)
        
        return send_and_cleanup(
            output_path,
            download_name='converted.pdf',
            mimetype='application/pdf'
        )
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        return error_response(str(e), 500)
    except Exception as e:
        return error_response(f"Unexpected error: {str(e)}", 500)
    finally:
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


# ─── TXT → PDF ───────────────────────────────────────────────────────────────
@app.route('/api/convert/txt-to-pdf', methods=['POST'])
def api_txt_to_pdf():
    input_path = None
    try:
        input_path = save_upload(request.files.get('file'), ('.txt',))
        output_path = make_output_path('.pdf')
        
        # Use word_to_pdf's reportlab engine for text
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph
        from reportlab.lib.styles import getSampleStyleSheet
        
        with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        
        pdf = SimpleDocTemplate(output_path, pagesize=A4,
            leftMargin=25*mm, rightMargin=25*mm, topMargin=25*mm, bottomMargin=25*mm)
        styles = getSampleStyleSheet()
        story = []
        for line in text.split('\n'):
            safe = line.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') or '&nbsp;'
            story.append(Paragraph(safe, styles['Normal']))
        pdf.build(story)
        
        os.remove(input_path)
        
        return send_and_cleanup(output_path, download_name='converted.pdf', mimetype='application/pdf')
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return error_response(f"Unexpected error: {str(e)}", 500)
    finally:
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


# ─── PDF → TXT ──────────────────────────────────────────────────────────────
@app.route('/api/convert/pdf-to-txt', methods=['POST'])
def api_pdf_to_txt():
    input_path = None
    try:
        input_path = save_upload(request.files.get('file'), ('.pdf',))
        output_path = make_output_path('.txt')
        
        from pypdf import PdfReader
        reader = PdfReader(input_path)
        text = "\n\n".join(page.extract_text() for page in reader.pages if page.extract_text())
        
        if not text.strip():
            raise RuntimeError("No text found in PDF. It may be a scanned image.")
            
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(text)
            
        os.remove(input_path)
        
        return send_and_cleanup(output_path, download_name='extracted.txt', mimetype='text/plain')
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass

# ─── DOCX → TXT ─────────────────────────────────────────────────────────────
@app.route('/api/convert/docx-to-txt', methods=['POST'])
def api_docx_to_txt():
    input_path = None
    try:
        input_path = save_upload(request.files.get('file'), ('.docx',))
        output_path = make_output_path('.txt')
        
        import docx
        doc = docx.Document(input_path)
        text = "\n".join(para.text for para in doc.paragraphs)
        
        if not text.strip():
            raise RuntimeError("No text found in DOCX.")
            
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(text)
            
        os.remove(input_path)
        
        return send_and_cleanup(output_path, download_name='extracted.txt', mimetype='text/plain')
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        if input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass

# =============================================================================
# SERVE FRONTEND (index.html) from the same directory
# =============================================================================
@app.route('/')
def serve_frontend():
    return send_file(os.path.join(os.path.dirname(__file__), 'index.html'))


# =============================================================================
# RUN
# =============================================================================
if __name__ == '__main__':
    print("=" * 60)
    print("  DocFlow Converter API")
    print("  Frontend: http://localhost:5000")
    print("  API:      http://localhost:5000/api/health")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)
