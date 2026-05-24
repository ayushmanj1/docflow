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
import re
import uuid
import time
import atexit
import shutil
import tempfile
from collections import defaultdict
from flask import Flask, request, send_file, jsonify, after_this_request
from flask_cors import CORS

# Import converters
from converters import pdf_to_word, word_to_pdf, pdf_to_image, image_to_pdf, office_to_pdf

# =============================================================================
# APP SETUP
# =============================================================================
app = Flask(__name__)

# ─── CORS: Restrict to API routes only ───────────────────────────────────────
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)


# =============================================================================
# SECURITY POLICY 1: HTTP RESPONSE HEADERS
# Protects every user from XSS, clickjacking, MIME attacks, and more.
# =============================================================================
@app.after_request
def add_security_headers(response):
    # Prevent MIME-type sniffing — stops browsers from guessing file types
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Block clickjacking — site cannot be embedded in iframes on other sites
    response.headers['X-Frame-Options'] = 'DENY'
    # Legacy XSS filter for older browsers
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Control referrer information leakage to third parties
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Content Security Policy — whitelist ONLY trusted CDNs
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    # HSTS — force HTTPS for 1 year with preload
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
    # Permissions-Policy — disable dangerous browser APIs
    response.headers['Permissions-Policy'] = (
        'camera=(), microphone=(), geolocation=(), payment=(), usb=(), '
        'accelerometer=(), gyroscope=(), magnetometer=()'
    )
    # Prevent caching of API responses containing user data
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
    return response


# =============================================================================
# SECURITY POLICY 2: RATE LIMITING (per IP)
# Prevents abuse, DDoS, and brute-force attacks on conversion endpoints.
# 30 requests per minute per IP address.
# =============================================================================
_rate_limit_store = defaultdict(list)
RATE_LIMIT_MAX = 30
RATE_LIMIT_WINDOW = 60  # seconds

def rate_limit_check():
    """Check if the current IP has exceeded the rate limit."""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr) or 'unknown'
    ip = ip.split(',')[0].strip()  # Handle proxy chains
    now = time.time()
    # Purge expired entries
    _rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit_store[ip]) >= RATE_LIMIT_MAX:
        return False
    _rate_limit_store[ip].append(now)
    return True

@app.before_request
def enforce_rate_limit():
    """Block requests that exceed the rate limit."""
    if request.path.startswith('/api/convert'):
        if not rate_limit_check():
            return jsonify({'error': 'Too many requests. Please wait a minute and try again.'}), 429


# =============================================================================
# SECURITY POLICY 3: MAGIC BYTE VALIDATION
# Verifies that uploaded file content actually matches its extension.
# Prevents users from disguising malware as a .pdf or .docx.
# =============================================================================
MAGIC_BYTES = {
    '.pdf':  [b'%PDF'],
    '.docx': [b'PK\x03\x04'],
    '.doc':  [b'\xd0\xcf\x11\xe0'],
    '.xlsx': [b'PK\x03\x04'],
    '.xls':  [b'\xd0\xcf\x11\xe0'],
    '.pptx': [b'PK\x03\x04'],
    '.ppt':  [b'\xd0\xcf\x11\xe0'],
    '.png':  [b'\x89PNG'],
    '.jpg':  [b'\xff\xd8\xff'],
    '.jpeg': [b'\xff\xd8\xff'],
    '.bmp':  [b'BM'],
    '.tiff': [b'II\x2a\x00', b'MM\x00\x2a'],
    '.tif':  [b'II\x2a\x00', b'MM\x00\x2a'],
    '.webp': [b'RIFF'],
    '.txt':  [],  # No magic bytes for plain text
}

def validate_file_magic(file_path: str, extension: str) -> bool:
    """Validate file content matches its claimed extension using magic bytes."""
    signatures = MAGIC_BYTES.get(extension, None)
    if signatures is None:
        return False  # Unknown extension — reject
    if not signatures:
        return True   # No magic bytes to check (e.g. .txt)
    try:
        with open(file_path, 'rb') as f:
            header = f.read(8)
        return any(header.startswith(sig) for sig in signatures)
    except Exception:
        return False


# =============================================================================
# SECURITY POLICY 4: FILENAME SANITIZATION
# Strips path traversal attacks (../../etc/passwd) and dangerous characters.
# =============================================================================
DANGEROUS_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')

def sanitize_filename(filename: str) -> str:
    """Strip path traversal attempts and dangerous characters from filenames."""
    filename = os.path.basename(filename)
    filename = DANGEROUS_CHARS.sub('', filename)
    if not filename or filename.startswith('.'):
        filename = 'upload' + filename
    return filename[:200]  # Cap length to prevent buffer issues


# =============================================================================
# SECURITY POLICY 5: REQUEST VALIDATION
# Only allows POST with correct content type on conversion endpoints.
# =============================================================================
@app.before_request
def validate_request():
    """Reject suspicious or malformed requests."""
    if request.path.startswith('/api/convert'):
        # Only POST allowed
        if request.method != 'POST':
            return jsonify({'error': 'Method not allowed.'}), 405
        # Must be multipart form data
        if 'multipart/form-data' not in (request.content_type or ''):
            return jsonify({'error': 'Invalid content type. Use multipart/form-data.'}), 400


# =============================================================================
# CONFIG
# =============================================================================
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB max upload

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), 'docflow_uploads')
OUTPUT_DIR = os.path.join(tempfile.gettempdir(), 'docflow_outputs')

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
    Save an uploaded file to the uploads directory with full security validation.
    
    Security checks performed:
    1. Validates file is present and not empty
    2. Sanitizes filename (strips path traversal & dangerous chars)
    3. Validates extension against allowed list
    4. Generates UUID filename to prevent collisions
    5. Validates save path stays within UPLOAD_DIR (path traversal guard)
    6. Validates magic bytes match the claimed file type
    
    Returns the absolute path to the saved file.
    """
    if not file_storage or file_storage.filename == '':
        raise ValueError("No file uploaded.")
    
    # SECURITY: Sanitize filename
    safe_original = sanitize_filename(file_storage.filename)
    original_ext = os.path.splitext(safe_original)[1].lower()
    
    if original_ext not in allowed_extensions:
        raise ValueError(
            f"Unsupported file type '{original_ext}'. "
            f"Allowed: {', '.join(allowed_extensions)}"
        )
    
    # UUID filename to avoid collisions
    safe_name = f"{uuid.uuid4().hex}{original_ext}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    
    # SECURITY: Path traversal protection — ensure path stays within UPLOAD_DIR
    real_save = os.path.realpath(save_path)
    real_upload = os.path.realpath(UPLOAD_DIR)
    if not real_save.startswith(real_upload):
        raise ValueError("Invalid file path detected.")
    
    # Write file to disk
    file_storage.save(save_path)
    file_storage.close()
    
    # Verify the file was written
    if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
        raise RuntimeError("File upload failed — file is empty after save.")
    
    # SECURITY: Validate magic bytes match the claimed file type
    if not validate_file_magic(save_path, original_ext):
        os.remove(save_path)  # Delete suspicious file immediately
        raise ValueError(
            f"File content does not match the '{original_ext}' format. "
            "The file may be corrupted or disguised."
        )
    
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

@app.errorhandler(429)
def rate_limited(e):
    return error_response("Too many requests. Please wait and try again.", 429)

@app.errorhandler(404)
def not_found(e):
    return error_response("Resource not found.", 404)

@app.errorhandler(405)
def method_not_allowed(e):
    return error_response("Method not allowed.", 405)

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
    print("  Security: Rate limiting, CSP, HSTS, Magic Bytes active")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)
