#!/usr/bin/env python3
"""
Live Vectorize Inspector — Flask server.

  pip install flask Pillow
  python server.py
  → opens http://localhost:5000 automatically

Adjust all sliders live; the two-panel viewer (Original / Vector SVG) updates
automatically.

"""

import io
import base64
import tempfile
import os
import re
import subprocess
import webbrowser
import threading
import pathlib

import argparse
import sys

from flask import Flask, request, jsonify
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

# ── potrace binary: prefer a copy next to this script, fall back to system PATH ──
_here = pathlib.Path(__file__).parent
_local_potrace = _here / ("potrace.exe" if os.name == "nt" else "potrace")
POTRACE_PATH = str(_local_potrace) if _local_potrace.exists() else "potrace"

# ── host/port: read from .ip file if present, otherwise default to localhost ──
_ip_file = _here / ".ip"
if _ip_file.exists():
    _addr = _ip_file.read_text().strip()
    _host, _port = (_addr.rsplit(":", 1) + ["8080"])[:2] if ":" in _addr else (_addr, "8080")
    _port = int(_port)
else:
    _host, _port = "127.0.0.1", 8080
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)

DEFAULTS = {
    "brightness":   1.0,
    "gamma":        1.0,
    "contrast":     1.0,
    "sharpen":      0.0,
    "dilate":       0,
    "erode":        0,
    "threshold":    100,
    "blur":         0.0,
    "corner_break":  False,
    "alphamax":     1.3,
    "opttolerance": 0.3,
    "turdsize":     0,
    "invert_colors": True,
}


# ── core vectorize logic ───────────────────────────────────────────────────────

def run_vectorize(img_bytes: bytes, params: dict):
    """
    Pipeline: brightness → gamma → contrast → sharpen
              → dilate/erode → blur → invert → threshold → potrace.
    Returns the SVG string.
    """
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
    except UnidentifiedImageError:
        raise RuntimeError("Unsupported image format. Use PNG, JPEG, BMP, GIF, TIFF, or WebP.")
    W, H = img.size

    # ── tonal adjustments ─────────────────────────────────────────────────────
    if params["brightness"] != 1.0:
        img = ImageEnhance.Brightness(img).enhance(params["brightness"])

    if params["gamma"] != 1.0:
        g = params["gamma"]
        img = img.point(lambda p: int(((p / 255.0) ** g) * 255))

    if params["contrast"] != 1.0:
        img = ImageEnhance.Contrast(img).enhance(params["contrast"])

    # ── edge sharpening ────────────────────────────────────────────────────────
    if params["sharpen"] > 0:
        img = img.filter(ImageFilter.UnsharpMask(
            radius=2, percent=int(params["sharpen"] * 100), threshold=3
        ))

    # ── morphological ops on grayscale (before threshold) ─────────────────────
    # MaxFilter expands bright (white) areas → thickens lines
    for _ in range(int(params["dilate"])):
        img = img.filter(ImageFilter.MaxFilter(3))
    # MinFilter shrinks bright areas → thins lines / removes isolated dots
    for _ in range(int(params["erode"])):
        img = img.filter(ImageFilter.MinFilter(3))

    if params["blur"] > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=params["blur"]))

    # ── capture pre-bitmap (grayscale after all adjustments, before threshold) ─
    _prebmp_buf = io.BytesIO()
    img.save(_prebmp_buf, format="PNG")
    prebmp_b64 = "data:image/png;base64," + base64.b64encode(_prebmp_buf.getvalue()).decode()

    img_inv = ImageOps.invert(img)
    threshold = params["threshold"]
    img_bw = img_inv.point(lambda p: 255 if p > threshold else 0, "1")

    # ── break thin junction connections (hourglass / bowtie fix) ─────────────
    # Binary morphological opening: erode ink then dilate back.
    # Removes any ink connection thinner than ~1 pixel (diagonal or wider)
    # while preserving larger ink regions.
    if params.get("corner_break", False):
        # MaxFilter on 0=ink image shrinks ink (erode); MinFilter expands it back (dilate)
        img_l   = img_bw.convert("L")
        eroded  = img_l.filter(ImageFilter.MaxFilter(3))
        img_bw  = eroded.filter(ImageFilter.MinFilter(3)).convert("1")

    # ── capture pre-potrace bitmap for browser preview ──────────────────────
    _bmp_buf = io.BytesIO()
    ImageOps.invert(img_bw.convert("L")).save(_bmp_buf, format="PNG")
    bitmap_b64 = "data:image/png;base64," + base64.b64encode(_bmp_buf.getvalue()).decode()

    pbm_fd, pbm_path = tempfile.mkstemp(suffix=".pbm")
    svg_fd, svg_path = tempfile.mkstemp(suffix=".svg")
    os.close(pbm_fd)
    os.close(svg_fd)

    try:
        img_bw.save(pbm_path)
        cmd = [
            POTRACE_PATH, pbm_path, "-s",
            "-o", svg_path,
            "--alphamax",     str(params["alphamax"]),
            "--opttolerance", str(params["opttolerance"]),
            "--turdsize",     str(params["turdsize"]),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "potrace returned non-zero exit code")

        with open(svg_path, "r", encoding="utf-8") as f:
            svg = f.read()
    finally:
        for p in (pbm_path, svg_path):
            try:
                os.unlink(p)
            except OSError:
                pass

    # ── clean up SVG ──────────────────────────────────────────────────────────
    svg = svg.replace(
        '<?xml version="1.0" standalone="no"?>\n'
        '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 20010904//EN"\n'
        ' "http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd">\n',
        '<?xml version="1.0" encoding="UTF-8"?>\n',
    )
    svg = re.sub(
        r'<svg version="1\.0"(.*?)>',
        lambda m: m.group(0)
            .replace('version="1.0"', 'version="1.1"')
            .replace("pt", ""),
        svg,
        flags=re.DOTALL,
    )

    if params["invert_colors"]:
        bg = f'<rect width="{W}" height="{H}" fill="#000000"/>\n'
        svg = svg.replace('fill="#000000" stroke="none">', 'fill="#ffffff" stroke="none">')
        svg = svg.replace("<metadata>", bg + "<metadata>", 1)

    return svg, bitmap_b64, prebmp_b64


# ── HTML ───────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Vectorize Live</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:      #0d0d0f;
  --panel:   #141416;
  --side:    #0f0f11;
  --border:  #252528;
  --text:    #ccc;
  --dim:     #666;
  --green:   #3ddc84;
  --red:     #ff4f4f;
  --blue:    #4f9eff;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  font-size: 13px;
  height: 100vh;
  display: flex;
  overflow: hidden;
}

/* ── sidebar ─────────────────────────────────────────────────────────────── */
.sidebar {
  width: 268px;
  min-width: 268px;
  background: var(--side);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.sidebar-title {
  padding: 14px 16px 10px;
  font-size: .72rem;
  letter-spacing: .20em;
  text-transform: uppercase;
  color: #6a6a7e;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

/* upload */
.upload-zone {
  margin: 12px 12px 6px;
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 18px 12px;
  text-align: center;
  color: var(--dim);
  cursor: pointer;
  transition: border-color .2s, color .2s, background .2s;
  font-size: .68rem;
  line-height: 1.7;
  flex-shrink: 0;
}
.upload-zone:hover, .upload-zone.drag-over {
  border-color: var(--green);
  color: var(--green);
  background: rgba(61,220,132,.04);
}
.upload-zone input { display: none; }
.upload-icon { font-size: 1.5rem; display: block; margin-bottom: 2px; }
#filename-label {
  display: block;
  margin: 4px 12px 6px;
  font-size: .6rem;
  color: var(--dim);
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* controls */
.controls-section {
  padding: 10px 14px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 11px;
}

.section-label {
  font-size: .64rem;
  letter-spacing: .16em;
  text-transform: uppercase;
  color: #4a4a5c;
  margin-bottom: -4px;
}

.ctrl-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ctrl-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.ctrl-label { color: #8a8a9e; font-size: .72rem; text-transform: uppercase; letter-spacing: .07em; }
.ctrl-val   { color: var(--green); font-size: .76rem; min-width: 40px; text-align: right; font-weight: bold; }

/* ── info tooltip ─────────────────────────────────────────────────────────── */
#tooltip {
  position: fixed;
  z-index: 999;
  background: #1c1c21;
  border: 1px solid #2e2e36;
  color: #9a9ab0;
  font-size: .74rem;
  line-height: 1.55;
  padding: 8px 11px;
  border-radius: 6px;
  max-width: 230px;
  pointer-events: none;
  opacity: 0;
  transition: opacity .12s;
  box-shadow: 0 6px 20px rgba(0,0,0,.6);
}
#tooltip.visible { opacity: 1; }
.ctrl-label-group { display: flex; align-items: center; gap: 4px; }
.info-btn {
  font-size: .65rem;
  color: #3a3a4c;
  cursor: default;
  user-select: none;
  line-height: 1;
  transition: color .12s;
  flex-shrink: 0;
}
.info-btn:hover { color: var(--green); }

input[type=range] {
  -webkit-appearance: none;
  width: 100%;
  height: 3px;
  background: var(--border);
  border-radius: 2px;
  outline: none;
  cursor: pointer;
}
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: var(--green);
  cursor: pointer;
  transition: transform .1s;
}
input[type=range]::-webkit-slider-thumb:hover { transform: scale(1.25); }
input[type=range]::-moz-range-thumb {
  width: 13px; height: 13px;
  border-radius: 50%;
  background: var(--green);
  border: none; cursor: pointer;
}

/* toggle */
.toggle-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 2px 0;
}
.toggle {
  position: relative;
  width: 34px;
  height: 18px;
  flex-shrink: 0;
}
.toggle input { display: none; }
.toggle-track {
  position: absolute;
  inset: 0;
  background: var(--border);
  border-radius: 9px;
  cursor: pointer;
  transition: background .2s;
}
.toggle input:checked + .toggle-track { background: var(--green); }
.toggle-thumb {
  position: absolute;
  top: 2px; left: 2px;
  width: 14px; height: 14px;
  border-radius: 50%;
  background: #fff;
  pointer-events: none;
  transition: left .15s;
}
.toggle input:checked ~ .toggle-thumb { left: 18px; }

/* actions */
.actions {
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
.btn {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--panel);
  color: var(--dim);
  font-family: inherit;
  font-size: .68rem;
  cursor: pointer;
  text-align: center;
  transition: border-color .2s, color .2s;
}
.btn:hover:not(:disabled) { border-color: var(--green); color: var(--green); }
.btn:disabled { opacity: .35; cursor: default; }
.btn-primary { border-color: var(--green); color: var(--green); }

/* status */
.status {
  padding: 7px 14px;
  font-size: .62rem;
  color: var(--dim);
  min-height: 30px;
  display: flex;
  align-items: center;
  gap: 7px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
  margin-top: auto;
}
.spinner {
  width: 11px; height: 11px;
  border: 2px solid var(--border);
  border-top-color: var(--green);
  border-radius: 50%;
  animation: spin .7s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }
.err { color: var(--red); }

/* ── main ────────────────────────────────────────────────────────────────── */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

/* topbar */
.topbar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 7px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.topbar h1 {
  font-size: .68rem;
  letter-spacing: .2em;
  text-transform: uppercase;
  color: var(--dim);
}
.zoom-ctrl {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.zoom-ctrl label { color: var(--dim); font-size: .68rem; }
#zoom-slider { width: 90px; }
#zoom-val { color: var(--green); font-size: .72rem; min-width: 34px; }
.btn-sm {
  padding: 3px 8px;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: var(--panel);
  color: var(--dim);
  font-family: inherit;
  font-size: .63rem;
  cursor: pointer;
}
.btn-sm:hover { border-color: var(--green); color: var(--green); }

/* panels */
.panels {
  display: flex;
  flex: 1;
  gap: 1px;
  background: var(--border);
  overflow: hidden;
}
.panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--panel);
  min-width: 0;
}
.panel-title {
  padding: 5px 12px;
  font-size: .6rem;
  letter-spacing: .15em;
  text-transform: uppercase;
  color: var(--dim);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.ptab {
  background: none;
  border: none;
  font-family: inherit;
  font-size: .6rem;
  letter-spacing: .15em;
  text-transform: uppercase;
  color: var(--dim);
  cursor: pointer;
  padding: 0;
  opacity: .4;
  transition: opacity .15s, color .15s;
}
.ptab.active { color: var(--text); opacity: 1; }
.ptab:disabled { opacity: .18; cursor: default; }
.ptab:not(:disabled):not(.active):hover { opacity: .75; }

/* ── bitmap pixel editor toolbar ────────────────────────────────────────── */
.bitmap-toolbar {
  position: absolute;
  top: 0; left: 0; right: 0;
  z-index: 10;
  display: none;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.btool, .bsize {
  padding: 3px 7px;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: var(--side);
  color: var(--dim);
  font-family: inherit;
  font-size: .62rem;
  cursor: pointer;
  transition: border-color .15s, color .15s;
  white-space: nowrap;
}
.btool:hover, .bsize:hover { border-color: var(--green); color: var(--green); }
.btool.active, .bsize.active {
  border-color: var(--green);
  color: var(--green);
  background: rgba(61,220,132,.10);
}
.btool-sep { width: 1px; height: 14px; background: var(--border); margin: 0 2px; flex-shrink: 0; }
.btool-label { font-size: .59rem; color: #4a4a5c; letter-spacing: .08em; text-transform: uppercase; }
.btool-action {
  padding: 3px 9px;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: var(--side);
  color: var(--dim);
  font-family: inherit;
  font-size: .62rem;
  cursor: pointer;
  transition: border-color .15s, color .15s;
  margin-left: auto;
}
.btool-action:hover { border-color: var(--green); color: var(--green); }
.btool-action.primary { border-color: var(--green); color: var(--green); }
.btool-action.primary:hover { background: rgba(61,220,132,.10); }
.btool-action:disabled { opacity: .25; cursor: default; pointer-events: none; }
.viewport {
  flex: 1;
  overflow: hidden;
  position: relative;
  cursor: crosshair;
}
.img-wrap {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transform-origin: 0 0;
}
.img-wrap img, .img-wrap canvas {
  max-width: 100%; max-height: 100%;
  object-fit: contain;
  display: block;
  image-rendering: pixelated;
  user-select: none;
}
.img-wrap img { pointer-events: none; }
.placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #303035;
  font-size: .68rem;
  letter-spacing: .12em;
  pointer-events: none;
}

/* crosshair */
.xhair {
  position: absolute;
  pointer-events: none;
  display: none;
}
.xhair::before, .xhair::after {
  content: '';
  position: absolute;
  background: rgba(255,255,255,.35);
}
.xhair::before { width: 1px; height: 200vh; top: -100vh; left: 0; transform: translateX(-50%); }
.xhair::after  { height: 1px; width: 200vw;  left: -100vw; top: 0; transform: translateY(-50%); }

/* footer */
footer {
  padding: 3px 16px;
  border-top: 1px solid var(--border);
  font-size: .62rem;
  color: var(--dim);
  display: flex;
  gap: 16px;
  flex-shrink: 0;
}
#coords { color: var(--text); }
</style>
</head>
<body>

<!-- ── sidebar ──────────────────────────────────────────────────────────── -->
<div class="sidebar">
  <div class="sidebar-title">Vectorize Live</div>

  <label class="upload-zone" id="upload-zone">
    <input type="file" id="file-input" accept="image/png,image/jpeg,image/gif,image/bmp,image/webp,image/tiff,image/x-tiff,.json">
    <span class="upload-icon">⬆</span>
    Drop image here<br>or click to select
  </label>
  <span id="filename-label">no file loaded</span>

  <div class="controls-section">
    <div class="section-label">Image</div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Brightness</span>
        <span class="ctrl-val" id="val-brightness">1.00</span>
      </div>
      <input type="range" id="ctrl-brightness" min="0.2" max="3" step="0.05" value="1">
      <span class="ctrl-desc">Overall lightness. Below 1 darkens the image; above 1 brightens it. Applied first before any other step.</span>
    </div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Gamma</span>
        <span class="ctrl-val" id="val-gamma">1.00</span>
      </div>
      <input type="range" id="ctrl-gamma" min="0.2" max="3" step="0.05" value="1">
      <span class="ctrl-desc">Mid-tone curve. Below 1 lifts faint grey lines (reveals hidden detail); above 1 pushes midtones darker (kills grey noise).</span>
    </div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Contrast</span>
        <span class="ctrl-val" id="val-contrast">1.00</span>
      </div>
      <input type="range" id="ctrl-contrast" min="0.2" max="4" step="0.05" value="1">
      <span class="ctrl-desc">Boosts separation between ink and paper before threshold. Helps junction areas stay defined without blending into each other.</span>
    </div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Sharpen</span>
        <span class="ctrl-val" id="val-sharpen">0</span>
      </div>
      <input type="range" id="ctrl-sharpen" min="0" max="5" step="0.1" value="0">
      <span class="ctrl-desc">Unsharp mask applied before threshold. Reinforces thin lines and cyclic junctions so potrace traces them as distinct shapes rather than blending them.</span>
    </div>
    <div class="section-label">Bitmap</div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Dilate</span>
        <span class="ctrl-val" id="val-dilate">0</span>
      </div>
      <input type="range" id="ctrl-dilate" min="0" max="5" step="1" value="0">
      <span class="ctrl-desc">Expand bright (white) areas N pixels. Thickens lines and closes small gaps between strokes before threshold.</span>
    </div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Erode</span>
        <span class="ctrl-val" id="val-erode">0</span>
      </div>
      <input type="range" id="ctrl-erode" min="0" max="5" step="1" value="0">
      <span class="ctrl-desc">Shrink bright (white) areas N pixels. Thins lines and removes isolated 3&times;3 dot clusters before tracing.</span>
    </div>
    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Threshold</span>
        <span class="ctrl-val" id="val-threshold">100</span>
      </div>
      <input type="range" id="ctrl-threshold" min="0" max="255" step="1" value="100">
      <span class="ctrl-desc">Pixels brighter than this become ink. Lower = more ink captured; higher = only the brightest lines.</span>
    </div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Pre-blur</span>
        <span class="ctrl-val" id="val-blur">0.00</span>
      </div>
      <input type="range" id="ctrl-blur" min="0" max="3" step="0.05" value="0">
      <span class="ctrl-desc">Gaussian blur before threshold. Softens jagged edges so lines trace smoother. 0 = off.</span>
    </div>

    <div class="toggle-row">
      <span class="ctrl-label">Break Corner Joins</span>
      <label class="toggle">
        <input type="checkbox" id="ctrl-corner-break">
        <span class="toggle-track"></span>
        <span class="toggle-thumb"></span>
      </label>
    </div>
    <span class="ctrl-desc" style="margin-top:-6px">Removes pixels where two regions only touch diagonally. Separates hourglass / bowtie junctions into distinct traced paths.</span>

    <div class="section-label">Potrace</div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Alphamax</span>
        <span class="ctrl-val" id="val-alphamax">1.30</span>
      </div>
      <input type="range" id="ctrl-alphamax" min="0" max="1.3333" step="0.01" value="1.3">
      <span class="ctrl-desc">Curve smoothness (0&ndash;1.333). 0 = all sharp corners; 1.333 = maximum rounding.</span>
    </div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Opt Tolerance</span>
        <span class="ctrl-val" id="val-opttolerance">0.30</span>
      </div>
      <input type="range" id="ctrl-opttolerance" min="0" max="2" step="0.01" value="0.3">
      <span class="ctrl-desc">Curve simplification. Higher = fewer nodes, smoother. Lower = follows lines more tightly.</span>
    </div>

    <div class="ctrl-row">
      <div class="ctrl-header">
        <span class="ctrl-label">Turd Size</span>
        <span class="ctrl-val" id="val-turdsize">0</span>
      </div>
      <input type="range" id="ctrl-turdsize" min="0" max="100" step="1" value="0">
      <span class="ctrl-desc">Remove blobs smaller than N pixels. Kills noise/speckles. 0 = keep everything.</span>
    </div>

    <div class="section-label">Output</div>

    <div class="toggle-row">
      <span class="ctrl-label">Invert Colors</span>
      <label class="toggle">
        <input type="checkbox" id="ctrl-invert" checked>
        <span class="toggle-track"></span>
        <span class="toggle-thumb"></span>
      </label>
    </div>
    <span class="ctrl-desc" style="margin-top:-6px">On = white lines on black. Off = black lines on white (easier to print).</span>
  </div>

  <div class="actions">
    <button class="btn btn-primary" id="btn-download" disabled>&#8595; Download SVG</button>
    <button class="btn" id="btn-download-cropped" disabled>&#8595; Cropped SVG</button>
    <button class="btn" id="btn-export-project" title="Export project (params + images + undo history) as JSON">&#8599; Export</button>
    <label class="btn" title="Import a saved .json project file">&#8601; Import<input type="file" id="btn-import-project" accept=".json" style="display:none"></label>
    <button class="btn" id="btn-reset">Reset Defaults</button>
  </div>

  <div class="status" id="status-bar">
    <span id="status-text">Load an image to start</span>
  </div>
</div>

<!-- ── main ─────────────────────────────────────────────────────────────── -->
<div class="main">
  <div class="topbar">
    <h1>Inspector</h1>
    <div class="zoom-ctrl">
      <label>Zoom</label>
      <input type="range" id="zoom-slider" min="5" max="2000" value="100" step="5">
      <span id="zoom-val">100%</span>
      <button class="btn-sm" id="btn-fit">Fit</button>
      <button class="btn-sm" id="btn-1x">100%</button>
    </div>
  </div>

  <div class="panels">
    <div class="panel">
      <div class="panel-title">
        <button class="ptab active" id="ptab-orig">Original</button>
        <button class="ptab" id="ptab-prebmp" disabled title="Grayscale after all adjustments, before threshold">Pre-bitmap</button>
        <button class="ptab" id="ptab-bitmap" disabled title="Binary (2-color) bitmap sent to potrace">Bitmap</button>
      </div>
      <div class="viewport" id="vp0">
        <div class="bitmap-toolbar" id="bitmap-toolbar">
          <button class="btool active" id="btool-ink"   title="Paint ink — add to traced area"      >&#9999; Ink</button>
          <button class="btool"        id="btool-erase" title="Erase ink — remove from traced area" >&#9003; Erase</button>
          <button class="btool"        id="btool-pan"   title="Pan / scroll mode"                   >&#10021; Pan</button>
          <div class="btool-sep"></div>
          <span class="btool-label">Size</span>
          <button class="bsize active" data-size="1">1</button>
          <button class="bsize"        data-size="2">2</button>
          <button class="bsize"        data-size="4">4</button>
          <button class="bsize"        data-size="8">8</button>
          <div class="btool-sep"></div>
          <button class="btool-action" id="btool-undo" title="Undo (Ctrl+Z)" disabled>&#8617; Undo</button>
          <button class="btool-action" id="btool-redo" title="Redo (Ctrl+Y / Ctrl+Shift+Z)" disabled>&#8618; Redo</button>
          <div class="btool-sep"></div>
          <button class="btool-action"         id="btool-reset"   title="Reset to server-generated bitmap">Reset</button>
          <button class="btool-action primary" id="btool-retrace" title="Re-run potrace with edited bitmap">&#8635; Retrace SVG</button>
        </div>
        <div class="img-wrap" id="wrap0">
          <img id="img0" src="" alt="" style="display:none">
          <canvas id="canvas-bitmap" style="display:none;"></canvas>
        </div>
        <div class="xhair"      id="xh0"></div>
        <div class="placeholder" id="ph0">no image</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-title">Vector SVG</div>
      <div class="viewport" id="vp1">
        <div class="img-wrap" id="wrap1">
          <img id="img1" src="" alt="" style="display:none">
        </div>
        <div class="xhair"      id="xh1"></div>
        <div class="placeholder" id="ph1">awaiting</div>
      </div>
    </div>


  </div>

  <footer>
    <span id="coords">hover over panels</span>
    <span>scroll to zoom &middot; drag to pan</span>
  </footer>
</div>

<script>
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
  alphamax: 1.3, opttolerance: 0.3, turdsize: 0, invert: true,
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
    invert_colors: ctrls.invert.checked ? 'true' : 'false',
    corner_break:  ctrls.corner_break.checked ? 'true' : 'false',
  };
}

// wire up live value display + debounced processing on every control change
Object.values(ctrls).forEach(ctrl => {
  ctrl.addEventListener('input', () => {
    syncVals();
    scheduleProcess();
  });
});

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
  if (type === 'loading') {
    statusBar.innerHTML = '<div class="spinner"></div><span>' + msg + '</span>';
  } else {
    statusBar.innerHTML = '<span class="' + (type === 'error' ? 'err' : '') + '">' + msg + '</span>';
  }
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
  wraps.forEach(w => {
    w.style.transform       = `translate(${panX}px,${panY}px) scale(${zoom})`;
    w.style.transformOrigin = '0 0';
    w.style.width  = '100%';
    w.style.height = '100%';
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
    const ir = canvasBitmap.getBoundingClientRect();
    if (ir.width > 0)
      paintOnCanvas((e.clientX - ir.left) / ir.width  * canvasBitmap.width,
                    (e.clientY - ir.top)  / ir.height * canvasBitmap.height);
  }
  if (!isDrawingBitmap && previewMode === 'bitmap' && (bitmapDrawMode === 'ink' || bitmapDrawMode === 'erase')) {
    const ir = canvasBitmap.getBoundingClientRect();
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
    case '1': brushSize = BRUSH_SIZES[0]; document.querySelectorAll('.bsize').forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize)); clearCursorPreview(); if(lastCursorPos&&!isDrawingBitmap&&(bitmapDrawMode==='ink'||bitmapDrawMode==='erase')) drawCursorPreview(lastCursorPos.cx,lastCursorPos.cy); e.preventDefault(); break;
    case '2': brushSize = BRUSH_SIZES[1]; document.querySelectorAll('.bsize').forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize)); clearCursorPreview(); if(lastCursorPos&&!isDrawingBitmap&&(bitmapDrawMode==='ink'||bitmapDrawMode==='erase')) drawCursorPreview(lastCursorPos.cx,lastCursorPos.cy); e.preventDefault(); break;
    case '3': brushSize = BRUSH_SIZES[2]; document.querySelectorAll('.bsize').forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize)); clearCursorPreview(); if(lastCursorPos&&!isDrawingBitmap&&(bitmapDrawMode==='ink'||bitmapDrawMode==='erase')) drawCursorPreview(lastCursorPos.cx,lastCursorPos.cy); e.preventDefault(); break;
    case '4': brushSize = BRUSH_SIZES[3]; document.querySelectorAll('.bsize').forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize)); clearCursorPreview(); if(lastCursorPos&&!isDrawingBitmap&&(bitmapDrawMode==='ink'||bitmapDrawMode==='erase')) drawCursorPreview(lastCursorPos.cx,lastCursorPos.cy); e.preventDefault(); break;
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
document.getElementById('btn-1x').addEventListener('click', () => {
  const vp = viewports[0]; const rect = vp.getBoundingClientRect();
  zoomTo(1, rect.width / 2, rect.height / 2);
});

// ── preview tabs ────────────────────────────────────────────────────────────
function updateUndoRedoUI() {
  const u = document.getElementById('btool-undo');
  const r = document.getElementById('btool-redo');
  if (u) u.disabled = !undoStack.length;
  if (r) r.disabled = !redoStack.length;
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
  document.querySelectorAll('.bsize').forEach(b => b.classList.toggle('active', parseInt(b.dataset.size) === brushSize));
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
  ctxBitmap.fillStyle = bitmapDrawMode === 'ink' ? 'rgba(61,220,132,0.75)' : 'rgba(255,85,85,0.75)';
  for (const [px, py] of pixels) ctxBitmap.fillRect(px, py, 1, 1);
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
  const all = [];
  for (const [lx, ly] of bresenhamLine(Math.round(x0), Math.round(y0), Math.round(x1), Math.round(y1)))
    for (const p of getBrushPixels(lx, ly)) all.push(p);
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
  ctxBitmap.fillStyle = bitmapDrawMode === 'ink' ? 'rgba(61,220,132,0.75)' : 'rgba(255,85,85,0.75)';
  for (const [px, py] of pixels) ctxBitmap.fillRect(px, py, 1, 1);
}
function paintLine(x0, y0, x1, y1) {
  clearCursorPreview();
  ctxBitmap.fillStyle = bitmapDrawMode === 'ink' ? '#ffffff' : '#000000';
  for (const [px, py] of _collectLinePx(x0, y0, x1, y1)) ctxBitmap.fillRect(px, py, 1, 1);
  bitmapEdited = true;
  lastPaintPos = { cx: x1, cy: y1 };
}
function paintOnCanvas(cx, cy) {
  clearCursorPreview();
  ctxBitmap.fillStyle = bitmapDrawMode === 'ink' ? '#ffffff' : '#000000';
  for (const [px, py] of getBrushPixels(cx, cy)) ctxBitmap.fillRect(px, py, 1, 1);
  bitmapEdited = true;
  lastPaintPos = { cx, cy };
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
document.querySelectorAll('.bsize').forEach(btn => {
  btn.addEventListener('click', () => {
    brushSize = parseInt(btn.dataset.size);
    clearCursorPreview();
    document.querySelectorAll('.bsize').forEach(b => b.classList.toggle('active', b === btn));
  });
});
document.getElementById('btool-undo').addEventListener('click', performUndo);
document.getElementById('btool-redo').addEventListener('click', performRedo);
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
</script>
</body>
</html>"""


# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML


@app.route("/api/process", methods=["POST"])
def process():
    try:
        f = request.files.get("image")
        if not f:
            return jsonify({"error": "No image uploaded"}), 400

        img_bytes = f.read()
        if not img_bytes:
            return jsonify({"error": "Uploaded file is empty"}), 400

        def fp(key, cast=float):
            return cast(request.form.get(key, DEFAULTS[key]))

        params = {
            "threshold":    fp("threshold",    int),
            "blur":         fp("blur"),
            "brightness":   fp("brightness"),
            "gamma":        fp("gamma"),
            "contrast":     fp("contrast"),
            "sharpen":      fp("sharpen"),
            "dilate":       fp("dilate",       int),
            "erode":        fp("erode",        int),
            "alphamax":     fp("alphamax"),
            "opttolerance": fp("opttolerance"),
            "turdsize":     fp("turdsize",     int),
            "invert_colors": request.form.get("invert_colors", "true") == "true",
            "corner_break":  request.form.get("corner_break",  "false") == "true",
        }

        svg, bitmap_b64, prebmp_b64 = run_vectorize(img_bytes, params)

        return jsonify({
            "svg": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode(),
            "bitmap": bitmap_b64,
            "prebmp": prebmp_b64,
        })

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500


@app.route("/api/trace", methods=["POST"])
def trace_only():
    """Re-run potrace on a user-edited bitmap PNG (skips all preprocessing)."""
    try:
        f = request.files.get("bitmap")
        if not f:
            return jsonify({"error": "No bitmap uploaded"}), 400
        img_bytes = f.read()

        def fp(key, cast=float):
            return cast(request.form.get(key, DEFAULTS[key]))

        params = {
            "alphamax":     fp("alphamax"),
            "opttolerance": fp("opttolerance"),
            "turdsize":     fp("turdsize", int),
            "invert_colors": request.form.get("invert_colors", "true") == "true",
        }

        # Display bitmap is WHITE=ink, BLACK=paper — invert so potrace sees BLACK=ink
        img_display = Image.open(io.BytesIO(img_bytes)).convert("L")
        W, H = img_display.size
        img_bw = ImageOps.invert(img_display).point(lambda p: 255 if p > 128 else 0, "1")

        pbm_fd, pbm_path = tempfile.mkstemp(suffix=".pbm")
        svg_fd, svg_path = tempfile.mkstemp(suffix=".svg")
        os.close(pbm_fd)
        os.close(svg_fd)

        try:
            img_bw.save(pbm_path)
            cmd = [
                POTRACE_PATH, pbm_path, "-s",
                "-o", svg_path,
                "--alphamax",     str(params["alphamax"]),
                "--opttolerance", str(params["opttolerance"]),
                "--turdsize",     str(params["turdsize"]),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "potrace returned non-zero exit code")
            with open(svg_path, "r", encoding="utf-8") as fh:
                svg = fh.read()
        finally:
            for p in (pbm_path, svg_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

        svg = svg.replace(
            '<?xml version="1.0" standalone="no"?>\n'
            '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 20010904//EN"\n'
            ' "http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd">\n',
            '<?xml version="1.0" encoding="UTF-8"?>\n',
        )
        svg = re.sub(
            r'<svg version="1\.0"(.*?)>',
            lambda m: m.group(0)
                .replace('version="1.0"', 'version="1.1"')
                .replace("pt", ""),
            svg,
            flags=re.DOTALL,
        )
        if params["invert_colors"]:
            bg = f'<rect width="{W}" height="{H}" fill="#000000"/>\n'
            svg = svg.replace('fill="#000000" stroke="none">', 'fill="#ffffff" stroke="none">')
            svg = svg.replace("<metadata>", bg + "<metadata>", 1)

        return jsonify({
            "svg": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode(),
        })

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500


# ── entry point ────────────────────────────────────────────────────────────────

# ── CLI mode ────────────────────────────────────────────────────────────────────────────────

def run_cli():
    """python server.py input.png output.svg [--param value ...]"""
    parser = argparse.ArgumentParser(
        description="Vectorize line art to SVG using potrace.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input",  help="Input image (PNG, JPEG, …)")
    parser.add_argument("output", help="Output SVG path")

    INT_KEYS = {"threshold", "dilate", "erode", "turdsize"}
    for key, val in DEFAULTS.items():
        if key == "invert_colors":
            parser.add_argument("--no-invert", action="store_true",
                                help="Black on white (don't invert colors)")
        elif key == "corner_break":
            parser.add_argument("--corner-break", action="store_true",
                                help="Break diagonal-only corner connections (hourglass fix)")
        elif key in INT_KEYS:
            parser.add_argument(f"--{key}", type=int,   default=val)
        else:
            parser.add_argument(f"--{key}", type=float, default=val)

    args = parser.parse_args()
    input_path, output_path = args.input, args.output

    params = {k: getattr(args, k) for k in DEFAULTS if k not in ("invert_colors", "corner_break")}
    params["invert_colors"] = not args.no_invert
    params["corner_break"]  = args.corner_break

    print(f"Loading {input_path} …")
    with open(input_path, "rb") as fh:
        img_bytes = fh.read()

    svg = run_vectorize(img_bytes, params)

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"Saved {output_path} ({os.path.getsize(output_path) / 1024:.1f} KB)")


# ── entry point ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # First positional arg looks like a file → CLI mode; otherwise → server mode
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        run_cli()
    else:
        url = f"http://{_host}:{_port}"
        print(f"Starting Vectorize Live  \u2192  {url}")
        print("Press Ctrl+C to stop.\n")
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        app.run(host=_host, port=_port, debug=False, threaded=True)
