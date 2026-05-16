/**
 * PDFix — main.js
 * Handles: drag & drop, file validation, AJAX upload, progress, download
 */

// ── Navbar mobile toggle ───────────────────────────────────────────────────
function toggleMenu() {
  const m = document.getElementById('mobileMenu');
  if (m) m.classList.toggle('open');
}

// ── File icons map ─────────────────────────────────────────────────────────
const FILE_ICONS = {
  jpg: 'bi-file-earmark-image-fill',
  jpeg: 'bi-file-earmark-image-fill',
  png: 'bi-file-earmark-image-fill',
  gif: 'bi-file-earmark-image-fill',
  webp: 'bi-file-earmark-image-fill',
  bmp: 'bi-file-earmark-image-fill',
  tiff: 'bi-file-earmark-image-fill',
  txt: 'bi-file-earmark-text-fill',
  html: 'bi-filetype-html',
  htm: 'bi-filetype-html',
  docx: 'bi-file-earmark-word-fill',
  doc: 'bi-file-earmark-word-fill',
  csv: 'bi-file-earmark-spreadsheet-fill',
};

function getFileIcon(filename) {
  const ext = filename.split('.').pop().toLowerCase();
  return FILE_ICONS[ext] || 'bi-file-earmark-fill';
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' o';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' Ko';
  return (bytes / (1024 * 1024)).toFixed(1) + ' Mo';
}

// ── DOM refs ───────────────────────────────────────────────────────────────
const dropZone    = document.getElementById('dropZone');
const fileInput   = document.getElementById('fileInput');
const filePreview = document.getElementById('filePreview');
const fileRemove  = document.getElementById('fileRemove');
const fileName    = document.getElementById('fileName');
const fileSize    = document.getElementById('fileSize');
const fileIcon    = document.getElementById('filePreviewIcon');
const convertBtn  = document.getElementById('convertBtn');
const btnText     = convertBtn?.querySelector('.btn-text');
const btnLoading  = convertBtn?.querySelector('.btn-loading');
const progressWrap= document.getElementById('progressWrap');
const progressBar = document.getElementById('progressBar');
const resultBox   = document.getElementById('resultBox');
const downloadLink= document.getElementById('downloadLink');
const errorBox    = document.getElementById('errorBox');
const errorMsg    = document.getElementById('errorMsg');
const csrfToken   = document.getElementById('csrf_token');

let selectedFile = null;

// ── File selection ─────────────────────────────────────────────────────────
function onFileSelected(file) {
  if (!file) return;

  const maxSize = 20 * 1024 * 1024; // 20 MB
  if (file.size > maxSize) {
    showError('Fichier trop volumineux. Maximum : 20 Mo.');
    return;
  }

  selectedFile = file;
  const ext = file.name.split('.').pop().toLowerCase();
  const allowed = ['jpg','jpeg','png','gif','webp','bmp','tiff','txt','html','htm','docx','doc','csv'];
  if (!allowed.includes(ext)) {
    showError(`Extension non supportée : .${ext}`);
    return;
  }

  hideError();
  hideResult();

  // Show preview
  fileName.textContent = file.name;
  fileSize.textContent = formatSize(file.size);
  fileIcon.innerHTML = `<i class="bi ${getFileIcon(file.name)}" style="font-size:2rem;color:var(--accent)"></i>`;
  filePreview.style.display = 'flex';
  convertBtn.disabled = false;
  dropZone.style.display = 'none';
}

fileInput?.addEventListener('change', (e) => {
  if (e.target.files[0]) onFileSelected(e.target.files[0]);
});

fileRemove?.addEventListener('click', () => {
  resetConverter();
});

// ── Drag & Drop ────────────────────────────────────────────────────────────
if (dropZone) {
  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
  });
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const file = e.dataTransfer?.files[0];
    if (file) {
      fileInput.files = e.dataTransfer.files;
      onFileSelected(file);
    }
  });
}

// ── Conversion ─────────────────────────────────────────────────────────────
convertBtn?.addEventListener('click', async () => {
  if (!selectedFile) return;

  // UI: loading state
  btnText.style.display = 'none';
  btnLoading.style.display = 'inline-flex';
  convertBtn.disabled = true;
  progressWrap.style.display = 'block';
  hideError();
  hideResult();

  const formData = new FormData();
  formData.append('file', selectedFile);

  // CSRF token
  const csrf = csrfToken?.value || '';

  try {
    const response = await fetch('/convert', {
      method: 'POST',
      headers: {
        'X-CSRFToken': csrf,
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: formData,
    });

    const data = await response.json();

    if (!response.ok || data.error) {
      throw new Error(data.error || `Erreur HTTP ${response.status}`);
    }

    // Build download URL
    const stem = selectedFile.name.replace(/\.[^.]+$/, '');
    const dlUrl = `/download/${data.download_id}?name=${encodeURIComponent(stem)}`;

    // Show result
    downloadLink.href = dlUrl;
    downloadLink.setAttribute('download', stem + '.pdf');
    resultBox.style.display = 'flex';

    // Trigger download automatically
    const a = document.createElement('a');
    a.href = dlUrl;
    a.download = stem + '.pdf';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

  } catch (err) {
    showError(err.message || 'Une erreur est survenue. Veuillez réessayer.');
  } finally {
    btnText.style.display = 'inline-flex';
    btnLoading.style.display = 'none';
    convertBtn.disabled = false;
    progressWrap.style.display = 'none';
    progressBar.style.width = '0';
  }
});

// ── Helpers ────────────────────────────────────────────────────────────────
function showError(msg) {
  errorMsg.textContent = msg;
  errorBox.style.display = 'flex';
}
function hideError() {
  errorBox.style.display = 'none';
}
function hideResult() {
  resultBox.style.display = 'none';
}
function resetConverter() {
  selectedFile = null;
  fileInput.value = '';
  filePreview.style.display = 'none';
  dropZone.style.display = 'block';
  convertBtn.disabled = true;
  hideError();
  hideResult();
}

// ── Keyboard accessibility ─────────────────────────────────────────────────
dropZone?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') fileInput?.click();
});
