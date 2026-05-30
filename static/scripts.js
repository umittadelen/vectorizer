// ── state ──────────────────────────────────────────────────────────────────
let currentFile  = null;
let currentSvg   = null;
let originalSrc  = null;
let bitmapSrc    = null;
let preBitmapSrc = null;
let previewMode  = 'original'; // 'original' | 'prebmp' | 'bitmap'
let isDrawingBitmap = false;
let bitmapDrawMode  = 'ink';   // 'ink' | 'erase' | 'pan'
let brushSize       = 1;
let bitmapEdited    = false;
let cursorSave      = null;  // { imageData, x, y } — saved pixels under cursor preview
let lastPaintPos    = null;  // last painted canvas coords (for Shift+click line)
let lastCursorPos   = null;  // last hover canvas coords (for Shift key live preview)
let cachedCanvasRect = null; // cached canvas bounding rect, invalidated on transform/resize
let undoStack = [];
let redoStack = [];
const MAX_UNDO = 30;
let debounceTimer = null;
let abortCtrl    = null;
let zoom = 1, panX = 0, panY = 0;
let panning = false, panStart = {x:0, y:0, ox:0, oy:0};

const DEFAULTS = {
  threshold: 100, blur: 0.0,
  brightness: 1.0, gamma: 1.0, contrast: 1.0, sharpen: 0,
  dilate: 0, erode: 0,
  alphamax: 1.3, opttolerance: 0.3, turdsize: 0, invert: false,
  corner_break: false,
};

// ── element refs ────────────────────────────────────────────────────────────
const viewports  = [0,1].map(i => document.getElementById('vp'   + i));
const wraps      = [0,1].map(i => document.getElementById('wrap' + i));
const imgs       = [0,1].map(i => document.getElementById('img'  + i));
const xhairs     = [0,1].map(i => document.getElementById('xh'   + i));
const phs        = [0,1].map(i => document.getElementById('ph'   + i));
const coordsEl   = document.getElementById('coords');
const zoomSlider = document.getElementById('zoom-slider');
const zoomVal    = document.getElementById('zoom-val');
const statusBar  = document.getElementById('status-bar');
const btnDownload        = document.getElementById('btn-download');
const btnDownloadCropped = document.getElementById('btn-download-cropped');
const btnReset   = document.getElementById('btn-reset');
const fileInput  = document.getElementById('file-input');
const uploadZone = document.getElementById('upload-zone');
const fileLabel  = document.getElementById('filename-label');
const ptabOrig   = document.getElementById('ptab-orig');
const ptabPrebmp = document.getElementById('ptab-prebmp');
const ptabBitmap = document.getElementById('ptab-bitmap');
const canvasBitmap  = document.getElementById('canvas-bitmap');
const ctxBitmap     = canvasBitmap.getContext('2d');
const bitmapToolbar = document.getElementById('bitmap-toolbar');
const btnUndoTool   = document.getElementById('btool-undo');
const btnRedoTool   = document.getElementById('btool-redo');
const bsizeBtns     = document.querySelectorAll('.bsize');

const ctrls = {
  threshold:    document.getElementById('ctrl-threshold'),
  blur:         document.getElementById('ctrl-blur'),
  brightness:   document.getElementById('ctrl-brightness'),
  gamma:        document.getElementById('ctrl-gamma'),
  contrast:     document.getElementById('ctrl-contrast'),
  sharpen:      document.getElementById('ctrl-sharpen'),
  dilate:       document.getElementById('ctrl-dilate'),
  erode:        document.getElementById('ctrl-erode'),
  alphamax:     document.getElementById('ctrl-alphamax'),
  opttolerance: document.getElementById('ctrl-opttolerance'),
  turdsize:     document.getElementById('ctrl-turdsize'),
  invert:       document.getElementById('ctrl-invert'),
  corner_break: document.getElementById('ctrl-corner-break'),
};
const vals = {
  threshold:    document.getElementById('val-threshold'),
  blur:         document.getElementById('val-blur'),
  brightness:   document.getElementById('val-brightness'),
  gamma:        document.getElementById('val-gamma'),
  contrast:     document.getElementById('val-contrast'),
  sharpen:      document.getElementById('val-sharpen'),
  dilate:       document.getElementById('val-dilate'),
  erode:        document.getElementById('val-erode'),
  alphamax:     document.getElementById('val-alphamax'),
  opttolerance: document.getElementById('val-opttolerance'),
  turdsize:     document.getElementById('val-turdsize'),
};

// ── sync displayed values ───────────────────────────────────────────────────
function syncVals() {
  vals.threshold   .textContent = ctrls.threshold.value;
  vals.blur        .textContent = parseFloat(ctrls.blur.value).toFixed(2);
  vals.brightness  .textContent = parseFloat(ctrls.brightness.value).toFixed(2);
  vals.gamma       .textContent = parseFloat(ctrls.gamma.value).toFixed(2);
  vals.contrast    .textContent = parseFloat(ctrls.contrast.value).toFixed(2);
  vals.sharpen     .textContent = parseFloat(ctrls.sharpen.value).toFixed(1);
  vals.dilate      .textContent = ctrls.dilate.value;
  vals.erode       .textContent = ctrls.erode.value;
  vals.alphamax    .textContent = parseFloat(ctrls.alphamax.value).toFixed(2);
  vals.opttolerance.textContent = parseFloat(ctrls.opttolerance.value).toFixed(2);
  vals.turdsize    .textContent = ctrls.turdsize.value;
}

function getParams() {
  return {
    threshold:    ctrls.threshold.value,
    blur:         ctrls.blur.value,
    brightness:   ctrls.brightness.value,
    gamma:        ctrls.gamma.value,
    contrast:     ctrls.contrast.value,
    sharpen:      ctrls.sharpen.value,
    dilate:       ctrls.dilate.value,
    erode:        ctrls.erode.value,
    alphamax:     ctrls.alphamax.value,
    opttolerance: ctrls.opttolerance.value,
    turdsize:     ctrls.turdsize.value,
    invert_colors: (!ctrls.invert.checked) ? 'true' : 'false',
    corner_break:  ctrls.corner_break.checked ? 'true' : 'false',
  };
}


// Update #vp1 background based on invert checkbox
function updateVp1Background() {
  const vp1 = document.getElementById('vp1');
  if (!ctrls.invert.checked) {
    vp1.style.background = 'oklch(0.10 0.004 295)'; // dark background
  } else {
    vp1.style.background = '#fff'; // white background
  }
}

// wire up live value display + debounced processing on every control change
Object.values(ctrls).forEach(ctrl => {
  ctrl.addEventListener('input', () => {
    syncVals();
    scheduleProcess();
    if (ctrl === ctrls.invert) updateVp1Background();
  });
});

// Also update on page load
updateVp1Background();

// ── reset defaults ──────────────────────────────────────────────────────────
btnReset.addEventListener('click', () => {
  resetSettings();
  scheduleProcess();
});

// ── file upload ─────────────────────────────────────────────────────────────
fileInput.addEventListener('change', e => {
  const f = e.target.files[0];
  if (!f) return;
  if (f.name.endsWith('.json')) { importProject(f); e.target.value = ''; return; }
  loadFile(f);
});
uploadZone.addEventListener('dragover', e => {
  e.preventDefault();
  uploadZone.classList.add('drag-over');
});
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', e => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (!f) return;
  if (f.name.endsWith('.json')) { importProject(f); return; }
  // Exclude vector/unsupported formats (SVG etc.) — Pillow can't open them
  if (f.type.startsWith('image/') && f.type !== 'image/svg+xml') loadFile(f);
  else if (!f.type.startsWith('image/')) setStatus('unsupported file type', 'error');
});

function resetSettings() {
  ctrls.threshold.value    = DEFAULTS.threshold;
  ctrls.blur.value         = DEFAULTS.blur;
  ctrls.brightness.value   = DEFAULTS.brightness;
  ctrls.gamma.value        = DEFAULTS.gamma;
  ctrls.contrast.value     = DEFAULTS.contrast;
  ctrls.sharpen.value      = DEFAULTS.sharpen;
  ctrls.dilate.value       = DEFAULTS.dilate;
  ctrls.erode.value        = DEFAULTS.erode;
  ctrls.alphamax.value     = DEFAULTS.alphamax;
  ctrls.opttolerance.value = DEFAULTS.opttolerance;
  ctrls.turdsize.value     = DEFAULTS.turdsize;
  ctrls.invert.checked       = DEFAULTS.invert;
  ctrls.corner_break.checked = DEFAULTS.corner_break;
  syncVals();
}
function loadFile(file) {
  resetSettings();
  currentFile = file;
  fileLabel.textContent = file.name;
  bitmapSrc = null;
  preBitmapSrc = null;
  bitmapEdited = false;
  previewMode = 'original';
  ptabOrig.classList.add('active');
  ptabPrebmp.classList.remove('active');
  ptabBitmap.classList.remove('active');
  ptabPrebmp.disabled = true;
  ptabBitmap.disabled = true;
  // Show original immediately from FileReader (no server round-trip needed)
  const reader = new FileReader();
  reader.onload = ev => { originalSrc = ev.target.result; showImg(0, originalSrc); };
  reader.readAsDataURL(file);
  process();
}

// ── show/hide images ────────────────────────────────────────────────────────
function showImg(idx, src) {
  imgs[idx].src = src;
  imgs[idx].style.display = 'block';
  phs[idx].style.display  = 'none';
}

// ── debounced process ───────────────────────────────────────────────────────
function scheduleProcess() {
  if (!currentFile) return;
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(process, 280);
}

// ── API call ────────────────────────────────────────────────────────────────
async function process() {
  if (!currentFile) return;
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = new AbortController();

  setStatus('processing\u2026', 'loading');

  const fd = new FormData();
  fd.append('image', currentFile);
  const p = getParams();
  for (const [k, v] of Object.entries(p)) fd.append(k, v);

  try {
    const resp = await fetch('/api/process', {
      method: 'POST',
      body: fd,
      signal: abortCtrl.signal,
    });
    const data = await resp.json();

    if (data.error) {
      setStatus(data.error, 'error');
      return;
    }

    bitmapSrc    = data.bitmap;
    preBitmapSrc = data.prebmp;
    ptabPrebmp.disabled = false;
    ptabBitmap.disabled = false;
    // Only reload canvas if the user hasn't made pixel edits — preserve edits across slider changes
    if (previewMode === 'bitmap' && !bitmapEdited) loadBitmapCanvas(bitmapSrc);
    else if (previewMode === 'prebmp') showImg(0, preBitmapSrc);
    showImg(1, data.svg);
    currentSvg = data.svg;
    btnDownload.disabled = false;
    btnDownloadCropped.disabled = false;
    const now = new Date();
    setStatus(`ready \u00B7 ${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}:${now.getSeconds().toString().padStart(2,'0')}`, 'ok');
  } catch (e) {
    if (e.name !== 'AbortError') setStatus('error: ' + e.message, 'error');
  }
}

// ── status bar ──────────────────────────────────────────────────────────────
function setStatus(msg, type) {
  statusBar.innerHTML = '';
  if (type === 'loading') {
    const sp = document.createElement('div');
    sp.className = 'spinner';
    statusBar.appendChild(sp);
  }
  const span = document.createElement('span');
  if (type === 'error') span.className = 'err';
  span.textContent = msg;
  statusBar.appendChild(span);
}

// ── download SVG ────────────────────────────────────────────────────────────
btnDownload.addEventListener('click', () => {
  if (!currentSvg) return;
  const a = document.createElement('a');
  a.href = currentSvg;
  const base = currentFile ? currentFile.name.replace(/\.[^.]+$/, '') : 'output';
  a.download = base + '.svg';
  a.click();
});

// ── download SVG (cropped — margins removed) ─────────────────────────────────
btnDownloadCropped.addEventListener('click', async () => {
  if (!currentSvg) return;
  btnDownloadCropped.disabled = true;
  setStatus('cropping\u2026', 'loading');
  try {
    // Decode SVG to access its XML
    const b64     = currentSvg.split(',')[1];
    const svgText = atob(b64);
    const parser  = new DOMParser();
    const doc     = parser.parseFromString(svgText, 'image/svg+xml');
    const svgEl   = doc.documentElement;
    const W = Math.round(parseFloat(svgEl.getAttribute('width')  || 100));
    const H = Math.round(parseFloat(svgEl.getAttribute('height') || 100));

    // Render SVG into an offscreen canvas (scale down for large images)
    const MAX_DIM = 2000;
    const scale   = Math.min(1, MAX_DIM / Math.max(W, H, 1));
    const cw      = Math.max(1, Math.round(W * scale));
    const ch      = Math.max(1, Math.round(H * scale));

    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = currentSvg; });

    const cv  = document.createElement('canvas');
    cv.width  = cw; cv.height = ch;
    const ctx = cv.getContext('2d');
    ctx.drawImage(img, 0, 0, cw, ch);
    const { data } = ctx.getImageData(0, 0, cw, ch);

    // Detect background type: transparent, black, or white — sample corners
    function pxAt(x, y) {
      const i = (y * cw + x) * 4;
      return [data[i], data[i+1], data[i+2], data[i+3]]; // r,g,b,a
    }
    const corners = [pxAt(0,0), pxAt(cw-1,0), pxAt(0,ch-1), pxAt(cw-1,ch-1)];
    let transpCorners = 0, darkCorners = 0;
    for (const [r, g, b, a] of corners) {
      if (a < 30) transpCorners++;
      else if (r < 128 && g < 128 && b < 128) darkCorners++;
    }
    const isTranspBg = transpCorners >= 2;
    const isBlackBg  = !isTranspBg && darkCorners >= 2;

    // Find the bounding box of all non-background pixels
    let x0 = cw, y0 = ch, x1 = -1, y1 = -1;
    for (let y = 0; y < ch; y++) {
      for (let x = 0; x < cw; x++) {
        const i = (y * cw + x) * 4;
        const r = data[i], g = data[i+1], b = data[i+2], a = data[i+3];
        let isBg;
        if (isTranspBg) isBg = a < 30;
        else if (isBlackBg) isBg = (r < 30 && g < 30 && b < 30);
        else isBg = (r > 225 && g > 225 && b > 225);
        if (!isBg) {
          if (x < x0) x0 = x; if (y < y0) y0 = y;
          if (x > x1) x1 = x; if (y > y1) y1 = y;
        }
      }
    }

    if (x0 > x1 || y0 > y1) {
      // Nothing found — fall back to full download
      const a = document.createElement('a');
      a.href = currentSvg;
      a.download = (currentFile ? currentFile.name.replace(/\.[^.]+$/, '') : 'output') + '.svg';
      a.click();
      setStatus('ready (nothing to crop)', 'ok');
      return;
    }

    // Map canvas coords back to SVG coordinate space (+1 px padding)
    const pad = 1;
    const sx0 = Math.max(0, Math.floor(x0 / scale) - pad);
    const sy0 = Math.max(0, Math.floor(y0 / scale) - pad);
    const sx1 = Math.min(W, Math.ceil (x1 / scale) + pad);
    const sy1 = Math.min(H, Math.ceil (y1 / scale) + pad);
    const sw  = sx1 - sx0, sh = sy1 - sy0;

    // Apply cropped viewBox and updated dimensions
    svgEl.setAttribute('viewBox', `${sx0} ${sy0} ${sw} ${sh}`);
    svgEl.setAttribute('width',  sw);
    svgEl.setAttribute('height', sh);

    const newSvg = new XMLSerializer().serializeToString(svgEl);
    const blob   = new Blob([newSvg], { type: 'image/svg+xml' });
    const url    = URL.createObjectURL(blob);
    const a      = document.createElement('a');
    a.href       = url;
    a.download   = (currentFile ? currentFile.name.replace(/\.[^.]+$/, '') : 'output') + '_cropped.svg';
    a.click();
    URL.revokeObjectURL(url);
    const now = new Date();
    setStatus(`cropped \u00B7 ${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}:${now.getSeconds().toString().padStart(2,'0')}`, 'ok');
  } catch (ex) {
    setStatus('crop error: ' + ex.message, 'error');
  } finally {
    btnDownloadCropped.disabled = false;
  }
});

// ── zoom / pan ──────────────────────────────────────────────────────────────
function applyTransform() {
  cachedCanvasRect = null;
  wraps.forEach(w => {
    w.style.transform       = `translate(${panX}px,${panY}px) scale(${zoom})`;
    w.style.transformOrigin = '0 0';
  });
  zoomSlider.value       = Math.round(zoom * 100);
  zoomVal.textContent    = Math.round(zoom * 100) + '%';
}

function zoomTo(newZoom, vpX, vpY) {
  const clamped = Math.max(0.05, Math.min(20, newZoom));
  const ratio = clamped / zoom;
  panX  = vpX - (vpX - panX) * ratio;
  panY  = vpY - (vpY - panY) * ratio;
  zoom  = clamped;
  applyTransform();
}

viewports.forEach((vp, idx) => {
  vp.addEventListener('wheel', e => {
    e.preventDefault();
    const rect  = vp.getBoundingClientRect();
    const delta = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    zoomTo(zoom * delta, e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });

  vp.addEventListener('pointerdown', e => {
    if (e.button !== 0 && e.button !== 1) return;
    // Let toolbar button clicks pass through unmodified
    if (e.target.closest('#bitmap-toolbar')) return;
    // pixel drawing: left-click only, on Bitmap tab, non-pan mode
    if (e.button === 0 && idx === 0 && previewMode === 'bitmap' && bitmapDrawMode !== 'pan') {
      isDrawingBitmap = true;
      vp.setPointerCapture(e.pointerId);
      const ir = canvasBitmap.getBoundingClientRect();
      if (ir.width > 0) {
        clearCursorPreview();
        captureUndoState();
        const cx = (e.clientX - ir.left) / ir.width  * canvasBitmap.width;
        const cy = (e.clientY - ir.top)  / ir.height * canvasBitmap.height;
        if (e.shiftKey && lastPaintPos)
          paintLine(lastPaintPos.cx, lastPaintPos.cy, cx, cy);
        else
          paintOnCanvas(cx, cy);
      }
      e.preventDefault();
      return;
    }
    panning  = true;
    vp.setPointerCapture(e.pointerId);
    panStart = { x: e.clientX, y: e.clientY, ox: panX, oy: panY };
    e.preventDefault();
  });
});

document.addEventListener('pointermove', e => {
  if (panning) {
    panX = panStart.ox + (e.clientX - panStart.x);
    panY = panStart.oy + (e.clientY - panStart.y);
    applyTransform();
  }
  if (isDrawingBitmap) {
    const ir = cachedCanvasRect ??= canvasBitmap.getBoundingClientRect();
    if (ir.width > 0)
      paintOnCanvas((e.clientX - ir.left) / ir.width  * canvasBitmap.width,
                    (e.clientY - ir.top)  / ir.height * canvasBitmap.height);
  }
  if (!isDrawingBitmap && previewMode === 'bitmap' && (bitmapDrawMode === 'ink' || bitmapDrawMode === 'erase')) {
    const ir = cachedCanvasRect ??= canvasBitmap.getBoundingClientRect();
    if (ir.width > 0) {
      const cx = (e.clientX - ir.left) / ir.width  * canvasBitmap.width;
      const cy = (e.clientY - ir.top)  / ir.height * canvasBitmap.height;
      lastCursorPos = { cx, cy };
      if (e.shiftKey && lastPaintPos) drawLinePreview(lastPaintPos.cx, lastPaintPos.cy, cx, cy);
      else drawCursorPreview(cx, cy);
    }
  }

  viewports.forEach((vp, i) => {
    const rect   = vp.getBoundingClientRect();
    const inside = e.clientX >= rect.left && e.clientX <= rect.right &&
                   e.clientY >= rect.top  && e.clientY <= rect.bottom;
    if (!inside) return;

    const lx = e.clientX - rect.left;
    const ly = e.clientY - rect.top;
    xhairs.forEach(xh => {
      xh.style.display = 'block';
      xh.style.left    = lx + 'px';
      xh.style.top     = ly + 'px';
    });

    // use canvas dimensions for coords when on Bitmap tab
    const ref = (i === 0 && previewMode === 'bitmap') ? canvasBitmap : imgs[i];
    const ir  = ref.getBoundingClientRect();
    if (ir.width > 0) {
      const px = Math.round((e.clientX - ir.left) / ir.width  * (ref.naturalWidth  || ref.width));
      const py = Math.round((e.clientY - ir.top)  / ir.height * (ref.naturalHeight || ref.height));
      coordsEl.textContent = `x: ${px}  y: ${py}  zoom: ${Math.round(zoom * 100)}%`;
    }
  });
});

document.addEventListener('pointerup', () => { panning = false; isDrawingBitmap = false; });
// Shift key: toggle between brush preview and line preview on the fly
function _refreshPreview(shiftKey) {
  if (!lastCursorPos || previewMode !== 'bitmap' || (bitmapDrawMode !== 'ink' && bitmapDrawMode !== 'erase')) return;
  if (shiftKey && lastPaintPos) drawLinePreview(lastPaintPos.cx, lastPaintPos.cy, lastCursorPos.cx, lastCursorPos.cy);
  else drawCursorPreview(lastCursorPos.cx, lastCursorPos.cy);
}
document.addEventListener('keydown', e => {
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
    if (e.key === 'Shift') _refreshPreview(true);
    return;
  }
  // Undo / Redo (work everywhere)
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'z') { e.preventDefault(); performUndo(); return; }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.shiftKey && e.key === 'z'))) { e.preventDefault(); performRedo(); return; }
  // Shift-line preview
  if (e.key === 'Shift') { _refreshPreview(true); return; }
  // Bitmap-tab shortcuts only
  if (previewMode !== 'bitmap') return;
  switch (e.key) {
    case 'i': case 'I': setToolMode('ink');   break;
    case 'e': case 'E': setToolMode('erase'); break;
    case 'p': case 'P': setToolMode('pan');   break;
    case ' ':           setToolMode('pan');   e.preventDefault(); break;
    case '[':           changeBrushSize(-1);  e.preventDefault(); break;
    case ']':           changeBrushSize(+1);  e.preventDefault(); break;
    case '1': brushSize = BRUSH_SIZES[0]; bsizeBtns.forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize)); clearCursorPreview(); if(lastCursorPos&&!isDrawingBitmap&&(bitmapDrawMode==='ink'||bitmapDrawMode==='erase')) drawCursorPreview(lastCursorPos.cx,lastCursorPos.cy); e.preventDefault(); break;
    case '2': brushSize = BRUSH_SIZES[1]; bsizeBtns.forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize)); clearCursorPreview(); if(lastCursorPos&&!isDrawingBitmap&&(bitmapDrawMode==='ink'||bitmapDrawMode==='erase')) drawCursorPreview(lastCursorPos.cx,lastCursorPos.cy); e.preventDefault(); break;
    case '3': brushSize = BRUSH_SIZES[2]; bsizeBtns.forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize)); clearCursorPreview(); if(lastCursorPos&&!isDrawingBitmap&&(bitmapDrawMode==='ink'||bitmapDrawMode==='erase')) drawCursorPreview(lastCursorPos.cx,lastCursorPos.cy); e.preventDefault(); break;
    case '4': brushSize = BRUSH_SIZES[3]; bsizeBtns.forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize)); clearCursorPreview(); if(lastCursorPos&&!isDrawingBitmap&&(bitmapDrawMode==='ink'||bitmapDrawMode==='erase')) drawCursorPreview(lastCursorPos.cx,lastCursorPos.cy); e.preventDefault(); break;
  }
});
document.addEventListener('keyup', e => { if (e.key === 'Shift') _refreshPreview(false); });
viewports.forEach(vp => {
  vp.addEventListener('pointerleave', () => {
    xhairs.forEach(x => x.style.display = 'none');
    clearCursorPreview();
  });
});

zoomSlider.addEventListener('input', () => {
  const vp   = viewports[0];
  const rect = vp.getBoundingClientRect();
  zoomTo(zoomSlider.value / 100, rect.width / 2, rect.height / 2);
});
document.getElementById('btn-fit').addEventListener('click', () => {
  zoom = 1; panX = 0; panY = 0; applyTransform();
});
window.addEventListener('resize', () => { cachedCanvasRect = null; });
document.getElementById('btn-1x').addEventListener('click', () => {
  const vp = viewports[0]; const rect = vp.getBoundingClientRect();
  zoomTo(1, rect.width / 2, rect.height / 2);
});

// ── preview tabs ────────────────────────────────────────────────────────────
function updateUndoRedoUI() {
  btnUndoTool.disabled = !undoStack.length;
  btnRedoTool.disabled = !redoStack.length;
}
function captureUndoState() {
  if (!canvasBitmap.width) return;
  undoStack.push(ctxBitmap.getImageData(0, 0, canvasBitmap.width, canvasBitmap.height));
  if (undoStack.length > MAX_UNDO) undoStack.shift();
  redoStack = [];
  updateUndoRedoUI();
}
function performUndo() {
  if (!undoStack.length) return;
  clearCursorPreview();
  redoStack.push(ctxBitmap.getImageData(0, 0, canvasBitmap.width, canvasBitmap.height));
  ctxBitmap.putImageData(undoStack.pop(), 0, 0);
  bitmapEdited = true; lastPaintPos = null;
  updateUndoRedoUI();
}
function performRedo() {
  if (!redoStack.length) return;
  clearCursorPreview();
  undoStack.push(ctxBitmap.getImageData(0, 0, canvasBitmap.width, canvasBitmap.height));
  if (undoStack.length > MAX_UNDO) undoStack.shift();
  ctxBitmap.putImageData(redoStack.pop(), 0, 0);
  bitmapEdited = true; lastPaintPos = null;
  updateUndoRedoUI();
}
const BRUSH_SIZES = [1, 2, 4, 8];
function setToolMode(mode) {
  bitmapDrawMode = mode;
  clearCursorPreview();
  document.querySelectorAll('.btool').forEach(b => b.classList.toggle('active', b.id === 'btool-' + mode));
  if (lastCursorPos && !isDrawingBitmap && (mode === 'ink' || mode === 'erase'))
    drawCursorPreview(lastCursorPos.cx, lastCursorPos.cy);
}
function changeBrushSize(dir) {
  const i = BRUSH_SIZES.indexOf(brushSize);
  brushSize = BRUSH_SIZES[Math.max(0, Math.min(BRUSH_SIZES.length - 1, i + dir))];
  bsizeBtns.forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize));
  clearCursorPreview();
  if (lastCursorPos && !isDrawingBitmap && (bitmapDrawMode === 'ink' || bitmapDrawMode === 'erase'))
    drawCursorPreview(lastCursorPos.cx, lastCursorPos.cy);
}
function loadBitmapCanvas(src) {
  const tmp = new Image();
  tmp.onload = () => {
    canvasBitmap.width  = tmp.naturalWidth;
    canvasBitmap.height = tmp.naturalHeight;
    ctxBitmap.drawImage(tmp, 0, 0);
    bitmapEdited = false;
    lastPaintPos = null;
    undoStack = []; redoStack = [];
    updateUndoRedoUI();
  };
  tmp.src = src;
}
function getBrushPixels(cx, cy) {
  const bx = Math.floor(cx), by = Math.floor(cy);
  if (brushSize === 1) return [[bx, by]];
  if (brushSize === 2) return [[bx-1,by-1],[bx,by-1],[bx-1,by],[bx,by]];
  const SHAPES = {
    4: [
      [0,1],[0,2],
      [1,0],[1,1],[1,2],[1,3],
      [2,0],[2,1],[2,2],[2,3],
      [3,1],[3,2]
    ],
    8: [
      [0,2],[0,3],[0,4],[0,5],
      [1,1],[1,2],[1,3],[1,4],[1,5],[1,6],
      [2,0],[2,1],[2,2],[2,3],[2,4],[2,5],[2,6],[2,7],
      [3,0],[3,1],[3,2],[3,3],[3,4],[3,5],[3,6],[3,7],
      [4,0],[4,1],[4,2],[4,3],[4,4],[4,5],[4,6],[4,7],
      [5,0],[5,1],[5,2],[5,3],[5,4],[5,5],[5,6],[5,7],
      [6,1],[6,2],[6,3],[6,4],[6,5],[6,6],
      [7,2],[7,3],[7,4],[7,5]
    ]
  };
  const half = brushSize >> 1, ox = bx - half, oy = by - half;
  return SHAPES[brushSize].map(([r, c]) => [ox + c, oy + r]);
}
function clearCursorPreview() {
  if (!cursorSave) return;
  ctxBitmap.putImageData(cursorSave.imageData, cursorSave.x, cursorSave.y);
  cursorSave = null;
}
function drawCursorPreview(cx, cy) {
  clearCursorPreview();
  if (!canvasBitmap.width) return;
  const pixels = getBrushPixels(cx, cy);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [px, py] of pixels) {
    if (px < x0) x0 = px; if (py < y0) y0 = py;
    if (px > x1) x1 = px; if (py > y1) y1 = py;
  }
  const sw = x1 - x0 + 1, sh = y1 - y0 + 1;
  cursorSave = { imageData: ctxBitmap.getImageData(x0, y0, sw, sh), x: x0, y: y0 };
  ctxBitmap.fillStyle = bitmapDrawMode === 'ink' ? 'oklch(0.80 0.24 155 / 0.75)' : 'oklch(0.65 0.26 25 / 0.75)';
  ctxBitmap.beginPath();
  for (const [px, py] of pixels) ctxBitmap.rect(px, py, 1, 1);
  ctxBitmap.fill();
}
function bresenhamLine(x0, y0, x1, y1) {
  const pts = [], dx = Math.abs(x1-x0), dy = -Math.abs(y1-y0);
  let sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1, err = dx + dy;
  for (;;) {
    pts.push([x0, y0]);
    if (x0 === x1 && y0 === y1) break;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
  return pts;
}
function _collectLinePx(x0, y0, x1, y1) {
  const offsets = getBrushPixels(0, 0);  // shape offsets computed once
  const all = [];
  for (const [lx, ly] of bresenhamLine(Math.round(x0), Math.round(y0), Math.round(x1), Math.round(y1)))
    for (const [ox, oy] of offsets) all.push([lx + ox, ly + oy]);
  return all;
}
function drawLinePreview(x0, y0, x1, y1) {
  clearCursorPreview();
  if (!canvasBitmap.width) return;
  const pixels = _collectLinePx(x0, y0, x1, y1);
  if (!pixels.length) return;
  let mx = Infinity, my = Infinity, Mx = -Infinity, My = -Infinity;
  for (const [px, py] of pixels) {
    if (px < mx) mx = px; if (py < my) my = py;
    if (px > Mx) Mx = px; if (py > My) My = py;
  }
  cursorSave = { imageData: ctxBitmap.getImageData(mx, my, Mx-mx+1, My-my+1), x: mx, y: my };
  ctxBitmap.fillStyle = bitmapDrawMode === 'ink' ? 'oklch(0.80 0.24 155 / 0.75)' : 'oklch(0.65 0.26 25 / 0.75)';
  ctxBitmap.beginPath();
  for (const [px, py] of pixels) ctxBitmap.rect(px, py, 1, 1);
  ctxBitmap.fill();
}
function paintLine(x0, y0, x1, y1) {
  clearCursorPreview();
  ctxBitmap.fillStyle = bitmapDrawMode === 'ink' ? '#ffffff' : '#000000';
  ctxBitmap.beginPath();
  for (const [px, py] of _collectLinePx(x0, y0, x1, y1)) ctxBitmap.rect(px, py, 1, 1);
  ctxBitmap.fill();
  bitmapEdited = true;
  lastPaintPos = { cx: Math.round(x1), cy: Math.round(y1) };
}
function paintOnCanvas(cx, cy) {
  clearCursorPreview();
  ctxBitmap.fillStyle = bitmapDrawMode === 'ink' ? '#ffffff' : '#000000';
  ctxBitmap.beginPath();
  for (const [px, py] of getBrushPixels(cx, cy)) ctxBitmap.rect(px, py, 1, 1);
  ctxBitmap.fill();
  bitmapEdited = true;
  lastPaintPos = { cx: Math.floor(cx), cy: Math.floor(cy) };
}
function setPreviewTab(mode) {
  previewMode = mode;
  ptabOrig  .classList.toggle('active', mode === 'original');
  ptabPrebmp.classList.toggle('active', mode === 'prebmp');
  ptabBitmap.classList.toggle('active', mode === 'bitmap');
  const onBitmap = mode === 'bitmap';
  if (!onBitmap) clearCursorPreview();
  bitmapToolbar.style.display = onBitmap ? 'flex'  : 'none';
  canvasBitmap.style.display  = onBitmap ? 'block' : 'none';
  imgs[0].style.display       = onBitmap ? 'none'  : (originalSrc ? 'block' : 'none');
  if (mode === 'original' && originalSrc)  showImg(0, originalSrc);
  if (mode === 'prebmp'   && preBitmapSrc) showImg(0, preBitmapSrc);
  if (mode === 'bitmap' && bitmapSrc && !bitmapEdited) loadBitmapCanvas(bitmapSrc);
}
ptabOrig  .addEventListener('click', () => setPreviewTab('original'));
ptabPrebmp.addEventListener('click', () => { if (preBitmapSrc) setPreviewTab('prebmp'); });
ptabBitmap.addEventListener('click', () => { if (bitmapSrc)    setPreviewTab('bitmap'); });

// ── bitmap editor toolbar ────────────────────────────────────────────────────
['ink', 'erase', 'pan'].forEach(id => {
  document.getElementById('btool-' + id).addEventListener('click', () => {
    bitmapDrawMode = id;
    clearCursorPreview();
    document.querySelectorAll('.btool').forEach(b => b.classList.toggle('active', b.id === 'btool-' + id));
  });
});
bsizeBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    brushSize = parseInt(btn.dataset.size);
    clearCursorPreview();
    bsizeBtns.forEach(b => b.classList.toggle('active', b === btn));
  });
});
btnUndoTool.addEventListener('click', performUndo);
btnRedoTool.addEventListener('click', performRedo);
document.getElementById('btool-reset').addEventListener('click', () => {
  if (bitmapSrc) loadBitmapCanvas(bitmapSrc);
});
document.getElementById('btool-retrace').addEventListener('click', () => {
  if (!bitmapSrc) return;
  setStatus('retracing\u2026', 'loading');
  canvasBitmap.toBlob(async blob => {
    try {
      const fd = new FormData();
      fd.append('bitmap', blob, 'bitmap.png');
      const p = getParams();
      fd.append('alphamax',      p.alphamax);
      fd.append('opttolerance',  p.opttolerance);
      fd.append('turdsize',      p.turdsize);
      fd.append('invert_colors', p.invert_colors);
      const resp = await fetch('/api/trace', { method: 'POST', body: fd });
      const data = await resp.json();
      if (data.error) { setStatus(data.error, 'error'); return; }
      showImg(1, data.svg);
      currentSvg = data.svg;
      btnDownload.disabled = false;
      btnDownloadCropped.disabled = false;
      setStatus('retraced \u2713', 'ok');
    } catch (ex) {
      setStatus('error: ' + ex.message, 'error');
    }
  }, 'image/png');
});

// ── init ────────────────────────────────────────────────────────────────────
syncVals();
applyTransform();

// ── project export / import ──────────────────────────────────────────────────
async function exportProject() {
  async function canvasToPng(c) {
    return new Promise(res => c.toBlob(b => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(b); }, 'image/png'));
  }
  async function imageDataToPng(id) {
    const tmp = document.createElement('canvas');
    tmp.width = id.width; tmp.height = id.height;
    tmp.getContext('2d').putImageData(id, 0, 0);
    return canvasToPng(tmp);
  }
  setStatus('exporting\u2026', 'loading');
  try {
    const project = {
      version: 1,
      params: getParams(),
      original: originalSrc || null,
      bitmap:   canvasBitmap.width ? await canvasToPng(canvasBitmap) : null,
      undoStack: await Promise.all(undoStack.map(imageDataToPng)),
      redoStack: await Promise.all(redoStack.map(imageDataToPng)),
    };
    const name = (currentFile ? currentFile.name.replace(/\.[^.]+$/, '') : 'project') + '.vectorize.json';
    const blob = new Blob([JSON.stringify(project)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    URL.revokeObjectURL(a.href);
    setStatus('exported \u2713', 'ok');
  } catch(ex) { setStatus('export error: ' + ex.message, 'error'); }
}
async function importProject(file) {
  setStatus('importing\u2026', 'loading');
  try {
    const project = JSON.parse(await file.text());
    // Restore params
    const p = project.params || {};
    const keys = ['threshold','blur','brightness','gamma','contrast','sharpen','dilate','erode','alphamax','opttolerance','turdsize'];
    keys.forEach(k => { if (p[k] !== undefined && ctrls[k]) ctrls[k].value = p[k]; });
    if (p.invert_colors !== undefined) ctrls.invert.checked      = p.invert_colors === 'true';
    if (p.corner_break  !== undefined) ctrls.corner_break.checked = p.corner_break === 'true';
    syncVals();
    // Restore original image + create synthetic File for pipeline
    if (project.original) {
      originalSrc = project.original;
      const [hdr, b64] = project.original.split(',');
      const mime = hdr.match(/:(.*?);/)[1];
      const ab = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      currentFile = new File([ab], 'imported', { type: mime });
      fileLabel.textContent = file.name.replace(/\.vectorize\.json$/, '');
      setPreviewTab('original');
    }
    // Restore bitmap canvas
    if (project.bitmap) {
      await new Promise((res, rej) => {
        const img = new Image();
        img.onload = () => {
          canvasBitmap.width = img.naturalWidth; canvasBitmap.height = img.naturalHeight;
          ctxBitmap.drawImage(img, 0, 0); res();
        };
        img.onerror = rej; img.src = project.bitmap;
      });
      bitmapSrc = project.bitmap;
      bitmapEdited = true;
      ptabBitmap.disabled = false;
    }
    // Restore undo/redo stacks
    async function pngToImageData(src) {
      return new Promise((res, rej) => {
        const img = new Image();
        img.onload = () => {
          const tmp = document.createElement('canvas');
          tmp.width = img.naturalWidth; tmp.height = img.naturalHeight;
          const ctx = tmp.getContext('2d'); ctx.drawImage(img, 0, 0);
          res(ctx.getImageData(0, 0, tmp.width, tmp.height));
        };
        img.onerror = rej; img.src = src;
      });
    }
    undoStack = await Promise.all((project.undoStack || []).map(pngToImageData));
    redoStack = await Promise.all((project.redoStack || []).map(pngToImageData));
    lastPaintPos = null;
    updateUndoRedoUI();
    // Re-run pipeline with restored params (won't overwrite bitmap since bitmapEdited=true)
    if (currentFile) scheduleProcess();
    setStatus('imported \u2713', 'ok');
  } catch(ex) { setStatus('import error: ' + ex.message, 'error'); }
}
document.getElementById('btn-export-project').addEventListener('click', exportProject);
document.getElementById('btn-import-project').addEventListener('change', e => {
  if (e.target.files[0]) { importProject(e.target.files[0]); e.target.value = ''; }
});

// ── info tooltips ────────────────────────────────────────────────────────────
function wrapWithInfoBtn(label, tipText) {
  const btn = document.createElement('span');
  btn.className = 'info-btn';
  btn.textContent = '\u24D8';  // ⓘ
  btn.dataset.tip = tipText;
  const g = document.createElement('span');
  g.className = 'ctrl-label-group';
  label.replaceWith(g);
  g.appendChild(label);
  g.appendChild(btn);
  return btn;
}
const ttEl = document.createElement('div');
ttEl.id = 'tooltip';
document.body.appendChild(ttEl);

document.querySelectorAll('.ctrl-desc').forEach(desc => {
  const tip = desc.textContent.trim();
  const parentRow = desc.closest('.ctrl-row');
  let btn;
  if (parentRow) {
    btn = wrapWithInfoBtn(parentRow.querySelector('.ctrl-label'), tip);
  } else {
    const prev = desc.previousElementSibling;
    if (prev && prev.classList.contains('toggle-row')) {
      btn = wrapWithInfoBtn(prev.querySelector('.ctrl-label'), tip);
    }
  }
  desc.remove();
});

function posTooltip(e) {
  const TW = ttEl.offsetWidth || 220, TH = ttEl.offsetHeight || 60;
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + TW > window.innerWidth  - 8) x = e.clientX - TW - 6;
  if (y + TH > window.innerHeight - 8) y = e.clientY - TH - 6;
  ttEl.style.left = x + 'px';
  ttEl.style.top  = y + 'px';
}
document.addEventListener('mouseover', e => {
  const btn = e.target.closest('.info-btn');
  if (!btn) return;
  ttEl.textContent = btn.dataset.tip;
  ttEl.classList.add('visible');
  posTooltip(e);
});
document.addEventListener('mouseout', e => {
  if (e.target.closest('.info-btn')) ttEl.classList.remove('visible');
});
document.addEventListener('mousemove', e => {
  if (e.target.closest('.info-btn')) posTooltip(e);
});