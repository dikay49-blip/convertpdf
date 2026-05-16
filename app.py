"""
PDF Converter Web App
Author: Generated for production use
Security: Flask-Talisman, Flask-Limiter, CSRF, magic bytes validation
"""

import os
import uuid
import threading
import time
import logging
from pathlib import Path
from datetime import datetime

from flask import (
    Flask, render_template, request, send_file,
    jsonify, abort, redirect, url_for
)
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
import magic  # python-magic for file type validation

# ── Converters ──────────────────────────────────────────────────────────────
import img2pdf
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_LEFT
import docx
import weasyprint

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32).hex())
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB max upload
app.config['UPLOAD_FOLDER'] = Path('uploads')
app.config['UPLOAD_FOLDER'].mkdir(exist_ok=True)
app.config['WTF_CSRF_TIME_LIMIT'] = 3600

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)

# ── CSRF ─────────────────────────────────────────────────────────────────────
csrf = CSRFProtect(app)

# ── Rate Limiting ─────────────────────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# ── Security Headers (Talisman) ───────────────────────────────────────────────
csp = {
    'default-src': ["'self'"],
    'script-src': [
        "'self'",
        "'unsafe-inline'",  # needed for inline scripts (AdSense etc.)
        "https://pagead2.googlesyndication.com",
        "https://www.paypal.com",
        "https://www.paypalobjects.com",
        "https://cdn.jsdelivr.net",
    ],
    'style-src': [
        "'self'",
        "'unsafe-inline'",
        "https://fonts.googleapis.com",
        "https://cdn.jsdelivr.net",
    ],
    'font-src': [
        "'self'",
        "https://fonts.gstatic.com",
        "https://cdn.jsdelivr.net",
    ],
    'img-src': [
        "'self'",
        "data:",
        "https://pagead2.googlesyndication.com",
        "https://www.paypalobjects.com",
    ],
    'frame-src': [
        "https://www.paypal.com",
        "https://pagead2.googlesyndication.com",
    ],
    'connect-src': ["'self'"],
}

talisman = Talisman(
    app,
    force_https=os.environ.get('FORCE_HTTPS', 'false').lower() == 'true',
    strict_transport_security=True,
    strict_transport_security_max_age=31536000,
    content_security_policy=csp,
    referrer_policy='strict-origin-when-cross-origin',
    feature_policy={
        'geolocation': "'none'",
        'microphone': "'none'",
        'camera': "'none'",
    },
    session_cookie_secure=os.environ.get('FORCE_HTTPS', 'false').lower() == 'true',
    session_cookie_http_only=True,
    session_cookie_samesite='Lax',
)

# ── Allowed MIME types → mapped to converter ─────────────────────────────────
ALLOWED_MIME = {
    # Images
    'image/jpeg': 'image',
    'image/png': 'image',
    'image/gif': 'image',
    'image/webp': 'image',
    'image/bmp': 'image',
    'image/tiff': 'image',
    # Documents
    'text/plain': 'text',
    'text/html': 'html',
    'application/msword': 'docx',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    # CSV
    'text/csv': 'csv',
    'application/csv': 'csv',
}

ALLOWED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
    '.txt', '.html', '.htm',
    '.doc', '.docx',
    '.csv',
}

# ── Auto-delete scheduler ─────────────────────────────────────────────────────
def auto_delete_files():
    """Background thread: deletes uploaded/converted files older than 5 minutes."""
    while True:
        try:
            upload_folder = Path('uploads')
            now = time.time()
            for f in upload_folder.iterdir():
                if f.is_file():
                    age = now - f.stat().st_mtime
                    if age > 300:  # 5 minutes
                        f.unlink(missing_ok=True)
                        logger.info(f"Auto-deleted: {f.name}")
        except Exception as e:
            logger.error(f"Auto-delete error: {e}")
        time.sleep(60)  # check every minute


cleaner_thread = threading.Thread(target=auto_delete_files, daemon=True)
cleaner_thread.start()


# ── Validation helpers ────────────────────────────────────────────────────────
def validate_file(file_storage):
    """Validate file by extension AND magic bytes (MIME)."""
    filename = secure_filename(file_storage.filename or '')
    if not filename:
        return None, None, "Nom de fichier invalide."

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None, None, f"Extension non supportée: {ext}"

    # Read first 2KB for magic detection, then reset
    header = file_storage.read(2048)
    file_storage.seek(0)

    mime = magic.from_buffer(header, mime=True)
    if mime not in ALLOWED_MIME:
        return None, None, f"Type de fichier non supporté (MIME: {mime})."

    return filename, mime, None


def safe_path(folder: Path, filename: str) -> Path:
    """Prevent path traversal attacks."""
    target = (folder / filename).resolve()
    if not str(target).startswith(str(folder.resolve())):
        abort(400)
    return target


# ── Converters ────────────────────────────────────────────────────────────────
def convert_image_to_pdf(input_path: Path, output_path: Path):
    """Convert image(s) to PDF using img2pdf."""
    # Ensure image is in a compatible mode
    with Image.open(input_path) as img:
        if img.mode in ('RGBA', 'P', 'LA'):
            rgb_path = input_path.with_suffix('.rgb.jpg')
            img.convert('RGB').save(rgb_path, 'JPEG', quality=95)
            with open(rgb_path, 'rb') as f:
                pdf_bytes = img2pdf.convert(f)
            rgb_path.unlink(missing_ok=True)
        else:
            with open(input_path, 'rb') as f:
                pdf_bytes = img2pdf.convert(f)
    output_path.write_bytes(pdf_bytes)


def convert_text_to_pdf(input_path: Path, output_path: Path):
    """Convert plain text to PDF using ReportLab."""
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=16,
        textColor=colors.HexColor('#1a1a2e'),
    )
    content = []
    text = input_path.read_text(encoding='utf-8', errors='replace')
    for line in text.splitlines():
        line = line.strip()
        if line:
            content.append(Paragraph(line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), body_style))
            content.append(Spacer(1, 4))
        else:
            content.append(Spacer(1, 12))
    if not content:
        content.append(Paragraph("(Fichier vide)", body_style))
    doc.build(content)


def convert_html_to_pdf(input_path: Path, output_path: Path):
    """Convert HTML to PDF using WeasyPrint."""
    html_content = input_path.read_text(encoding='utf-8', errors='replace')
    weasyprint.HTML(string=html_content).write_pdf(str(output_path))


def convert_docx_to_pdf(input_path: Path, output_path: Path):
    """Convert DOCX to PDF via python-docx → HTML → WeasyPrint."""
    document = docx.Document(str(input_path))

    # Build minimal HTML from paragraphs
    html_parts = [
        "<!DOCTYPE html><html><head>",
        "<meta charset='utf-8'>",
        "<style>",
        "body{font-family:Georgia,serif;font-size:12pt;line-height:1.6;margin:2cm;color:#1a1a2e;}",
        "h1,h2,h3{margin-top:1.2em;} table{border-collapse:collapse;width:100%;}",
        "td,th{border:1px solid #ccc;padding:6px;}",
        "</style></head><body>",
    ]
    for para in document.paragraphs:
        text = para.text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        if para.style.name.startswith('Heading 1'):
            html_parts.append(f"<h1>{text}</h1>")
        elif para.style.name.startswith('Heading 2'):
            html_parts.append(f"<h2>{text}</h2>")
        elif para.style.name.startswith('Heading 3'):
            html_parts.append(f"<h3>{text}</h3>")
        elif text:
            html_parts.append(f"<p>{text}</p>")
    # Tables
    for table in document.tables:
        html_parts.append("<table>")
        for row in table.rows:
            html_parts.append("<tr>")
            for cell in row.cells:
                cell_text = cell.text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                html_parts.append(f"<td>{cell_text}</td>")
            html_parts.append("</tr>")
        html_parts.append("</table>")
    html_parts.append("</body></html>")

    html_string = "\n".join(html_parts)
    weasyprint.HTML(string=html_string).write_pdf(str(output_path))


def convert_csv_to_pdf(input_path: Path, output_path: Path):
    """Convert CSV to PDF using ReportLab tables."""
    import csv
    doc = SimpleDocTemplate(str(output_path), pagesize=A4,
                            leftMargin=1*cm, rightMargin=1*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    rows = []
    with open(input_path, newline='', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        styles = getSampleStyleSheet()
        doc.build([Paragraph("Fichier CSV vide.", styles['Normal'])])
        return

    table = Table(rows, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#16213e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f7fa')]),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    doc.build([table])


CONVERTERS = {
    'image': convert_image_to_pdf,
    'text': convert_text_to_pdf,
    'html': convert_html_to_pdf,
    'docx': convert_docx_to_pdf,
    'csv': convert_csv_to_pdf,
}


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/donate')
def donate():
    return render_template('donate.html')


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/convert', methods=['POST'])
@limiter.limit("10 per minute")
def convert():
    """Handle file upload and conversion."""
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier reçu.'}), 400

    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': 'Fichier vide ou sans nom.'}), 400

    # Validate
    filename, mime, error = validate_file(file)
    if error:
        return jsonify({'error': error}), 400

    conv_type = ALLOWED_MIME[mime]
    uid = uuid.uuid4().hex
    ext = Path(filename).suffix.lower()
    input_filename = f"{uid}_input{ext}"
    output_filename = f"{uid}_output.pdf"

    upload_folder = Path(app.config['UPLOAD_FOLDER'])
    input_path = safe_path(upload_folder, input_filename)
    output_path = safe_path(upload_folder, output_filename)

    try:
        file.save(str(input_path))
        logger.info(f"Uploaded: {filename} → {input_filename} (MIME: {mime})")

        # Convert
        converter = CONVERTERS[conv_type]
        converter(input_path, output_path)

        logger.info(f"Converted: {output_filename}")

        # Delete input immediately
        input_path.unlink(missing_ok=True)

        return jsonify({'download_id': uid, 'original_name': Path(filename).stem})

    except Exception as e:
        logger.error(f"Conversion error: {e}", exc_info=True)
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        return jsonify({'error': f'Erreur de conversion: {str(e)}'}), 500


@app.route('/download/<uid>')
@limiter.limit("20 per minute")
def download(uid):
    """Serve converted PDF then delete it."""
    # Validate UID (hex only, no path traversal)
    if not uid.isalnum() or len(uid) != 32:
        abort(400)

    output_filename = f"{uid}_output.pdf"
    output_path = safe_path(Path(app.config['UPLOAD_FOLDER']), output_filename)

    if not output_path.exists():
        abort(404)

    # Get original name from query param (optional)
    name = request.args.get('name', 'document')
    # Sanitize name
    name = secure_filename(name) or 'document'
    download_name = f"{name}.pdf"

    def delete_after_send(path):
        """Delete file after a short delay post-send."""
        time.sleep(5)
        Path(path).unlink(missing_ok=True)
        logger.info(f"Post-download deleted: {path}")

    t = threading.Thread(target=delete_after_send, args=(str(output_path),), daemon=True)
    t.start()

    return send_file(
        str(output_path),
        as_attachment=True,
        download_name=download_name,
        mimetype='application/pdf'
    )


# ── Context Processor ────────────────────────────────────────────────────────
@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}


# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(400)
def bad_request(e):
    return render_template('error.html', code=400, msg="Requête invalide."), 400

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, msg="Page introuvable."), 404

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'Fichier trop volumineux (max 20 Mo).'}), 413

@app.errorhandler(429)
def rate_limited(e):
    return jsonify({'error': 'Trop de requêtes. Veuillez patienter.'}), 429

@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500, msg="Erreur interne du serveur."), 500


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
