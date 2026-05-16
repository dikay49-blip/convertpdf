"""
PDF Converter Web App — version sans dépendances système
Librairies pures Python : filetype, xhtml2pdf, reportlab, img2pdf
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
    jsonify, abort, send_from_directory
)
import mimetypes
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename

import filetype
import img2pdf
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
import docx
from xhtml2pdf import pisa

app = Flask(__name__, static_folder=None)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32).hex())
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = Path('uploads')
app.config['UPLOAD_FOLDER'].mkdir(exist_ok=True)
app.config['WTF_CSRF_TIME_LIMIT'] = 3600

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

csrf = CSRFProtect(app)

limiter = Limiter(
    get_remote_address, app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

csp = {
    'default-src': ["'self'"],
    'script-src': ["'self'","'unsafe-inline'","https://pagead2.googlesyndication.com","https://www.paypal.com","https://www.paypalobjects.com","https://cdn.jsdelivr.net"],
    'style-src':  ["'self'","'unsafe-inline'","https://fonts.googleapis.com","https://cdn.jsdelivr.net"],
    'font-src':   ["'self'","https://fonts.gstatic.com","https://cdn.jsdelivr.net"],
    'img-src':    ["'self'","data:","https://pagead2.googlesyndication.com","https://www.paypalobjects.com"],
    'frame-src':  ["https://www.paypal.com","https://pagead2.googlesyndication.com"],
    'connect-src':["'self'"],
}

talisman = Talisman(
    app,
    force_https=os.environ.get('FORCE_HTTPS','false').lower()=='true',
    strict_transport_security=True,
    strict_transport_security_max_age=31536000,
    content_security_policy=csp,
    referrer_policy='strict-origin-when-cross-origin',
    session_cookie_secure=os.environ.get('FORCE_HTTPS','false').lower()=='true',
    session_cookie_http_only=True,
    session_cookie_samesite='Lax',
)

ALLOWED_MIME = {
    'image/jpeg':'image','image/png':'image','image/gif':'image',
    'image/webp':'image','image/bmp':'image','image/tiff':'image',
    'text/plain':'text','text/html':'html',
    'application/msword':'docx',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'docx',
    'text/csv':'csv','application/csv':'csv',
}

ALLOWED_EXTENSIONS = {'.jpg','.jpeg','.png','.gif','.webp','.bmp','.tiff','.txt','.html','.htm','.doc','.docx','.csv'}
TEXT_MIME_BY_EXT   = {'.txt':'text/plain','.html':'text/html','.htm':'text/html','.csv':'text/csv'}

def auto_delete_files():
    while True:
        try:
            now = time.time()
            for f in Path('uploads').iterdir():
                if f.is_file() and now - f.stat().st_mtime > 300:
                    f.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Auto-delete error: {e}")
        time.sleep(60)

threading.Thread(target=auto_delete_files, daemon=True).start()

def validate_file(file_storage):
    filename = secure_filename(file_storage.filename or '')
    if not filename:
        return None, None, "Nom de fichier invalide."
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None, None, f"Extension non supportée : {ext}"
    header = file_storage.read(2048)
    file_storage.seek(0)
    kind = filetype.guess(header)
    if kind:
        mime = kind.mime
    else:
        mime = TEXT_MIME_BY_EXT.get(ext)
        if not mime:
            return None, None, "Type de fichier non reconnu."
    if mime not in ALLOWED_MIME:
        return None, None, f"Type non supporté : {mime}"
    return filename, mime, None

def safe_path(folder, filename):
    target = (folder / filename).resolve()
    if not str(target).startswith(str(folder.resolve())):
        abort(400)
    return target

def convert_image_to_pdf(input_path, output_path):
    with Image.open(input_path) as img:
        if img.mode in ('RGBA','P','LA'):
            rgb = input_path.with_suffix('.rgb.jpg')
            img.convert('RGB').save(rgb,'JPEG',quality=95)
            with open(rgb,'rb') as f:
                output_path.write_bytes(img2pdf.convert(f))
            rgb.unlink(missing_ok=True)
        else:
            with open(input_path,'rb') as f:
                output_path.write_bytes(img2pdf.convert(f))

def convert_text_to_pdf(input_path, output_path):
    doc = SimpleDocTemplate(str(output_path),pagesize=A4,
        leftMargin=2*cm,rightMargin=2*cm,topMargin=2*cm,bottomMargin=2*cm)
    style = ParagraphStyle('B',parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica',fontSize=11,leading=16)
    content = []
    for line in input_path.read_text(encoding='utf-8',errors='replace').splitlines():
        line = line.strip()
        if line:
            content.append(Paragraph(line.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;'), style))
            content.append(Spacer(1,4))
        else:
            content.append(Spacer(1,12))
    if not content:
        content.append(Paragraph("(Fichier vide)", style))
    doc.build(content)

def _xhtml_to_pdf(html_string, output_path):
    with open(output_path,'wb') as f:
        result = pisa.CreatePDF(html_string, dest=f, encoding='utf-8')
    if result.err:
        raise RuntimeError(f"xhtml2pdf: {result.err}")

def convert_html_to_pdf(input_path, output_path):
    _xhtml_to_pdf(input_path.read_text(encoding='utf-8',errors='replace'), output_path)

def convert_docx_to_pdf(input_path, output_path):
    document = docx.Document(str(input_path))
    p = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         "<style>body{font-family:Arial,sans-serif;font-size:12pt;line-height:1.6;margin:1.5cm;color:#111;}",
         "h1{font-size:18pt;}h2{font-size:15pt;}h3{font-size:13pt;}",
         "table{border-collapse:collapse;width:100%;}td,th{border:1px solid #ccc;padding:5px;}</style></head><body>"]
    for para in document.paragraphs:
        t = para.text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        if para.style.name.startswith('Heading 1'): p.append(f"<h1>{t}</h1>")
        elif para.style.name.startswith('Heading 2'): p.append(f"<h2>{t}</h2>")
        elif para.style.name.startswith('Heading 3'): p.append(f"<h3>{t}</h3>")
        elif t: p.append(f"<p>{t}</p>")
    for table in document.tables:
        p.append("<table>")
        for row in table.rows:
            p.append("<tr>")
            for cell in row.cells:
                ct = cell.text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
                p.append(f"<td>{ct}</td>")
            p.append("</tr>")
        p.append("</table>")
    p.append("</body></html>")
    _xhtml_to_pdf("\n".join(p), output_path)

def convert_csv_to_pdf(input_path, output_path):
    import csv
    doc = SimpleDocTemplate(str(output_path),pagesize=A4,
        leftMargin=1*cm,rightMargin=1*cm,topMargin=2*cm,bottomMargin=2*cm)
    rows = []
    with open(input_path,newline='',encoding='utf-8',errors='replace') as f:
        for row in csv.reader(f):
            rows.append(row)
    if not rows:
        doc.build([Paragraph("CSV vide.",getSampleStyleSheet()['Normal'])])
        return
    table = Table(rows,repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#16213e')),
        ('TEXTCOLOR', (0,0),(-1,0),colors.white),
        ('FONTNAME',  (0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',  (0,0),(-1,-1),9),
        ('GRID',      (0,0),(-1,-1),0.5,colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f5f7fa')]),
        ('PADDING',   (0,0),(-1,-1),4),
    ]))
    doc.build([table])

CONVERTERS = {
    'image':convert_image_to_pdf,
    'text': convert_text_to_pdf,
    'html': convert_html_to_pdf,
    'docx': convert_docx_to_pdf,
    'csv':  convert_csv_to_pdf,
}

@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}
@app.route('/static/<path:filename>')
def static_files(filename):
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    mime, _ = mimetypes.guess_type(filename)
    response = send_from_directory(static_dir, filename)
    if mime:
        response.headers['Content-Type'] = mime
    return response
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
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier reçu.'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': 'Fichier vide.'}), 400
    filename, mime, error = validate_file(file)
    if error:
        return jsonify({'error': error}), 400
    conv_type = ALLOWED_MIME[mime]
    uid = uuid.uuid4().hex
    ext = Path(filename).suffix.lower()
    upload_folder = Path(app.config['UPLOAD_FOLDER'])
    input_path  = safe_path(upload_folder, f"{uid}_input{ext}")
    output_path = safe_path(upload_folder, f"{uid}_output.pdf")
    try:
        file.save(str(input_path))
        CONVERTERS[conv_type](input_path, output_path)
        input_path.unlink(missing_ok=True)
        return jsonify({'download_id': uid, 'original_name': Path(filename).stem})
    except Exception as e:
        logger.error(f"Conversion error: {e}", exc_info=True)
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        return jsonify({'error': f'Erreur : {str(e)}'}), 500

@app.route('/download/<uid>')
@limiter.limit("20 per minute")
def download(uid):
    if not uid.isalnum() or len(uid) != 32:
        abort(400)
    output_path = safe_path(Path(app.config['UPLOAD_FOLDER']), f"{uid}_output.pdf")
    if not output_path.exists():
        abort(404)
    name = secure_filename(request.args.get('name','document')) or 'document'
    def delete_later(p):
        time.sleep(5)
        Path(p).unlink(missing_ok=True)
    threading.Thread(target=delete_later,args=(str(output_path),),daemon=True).start()
    return send_file(str(output_path),as_attachment=True,download_name=f"{name}.pdf",mimetype='application/pdf')

@app.errorhandler(400)
def bad_request(e): return render_template('error.html',code=400,msg="Requête invalide."),400
@app.errorhandler(404)
def not_found(e): return render_template('error.html',code=404,msg="Page introuvable."),404
@app.errorhandler(413)
def too_large(e): return jsonify({'error':'Fichier trop volumineux (max 20 Mo).'}),413
@app.errorhandler(429)
def rate_limited(e): return jsonify({'error':'Trop de requêtes.'}),429
@app.errorhandler(500)
def server_error(e): return render_template('error.html',code=500,msg="Erreur interne."),500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)
