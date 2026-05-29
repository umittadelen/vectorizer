import io
import base64
import tempfile
import os
import re
import subprocess
import webbrowser
import pathlib

import argparse
import sys

from flask import Flask, request, jsonify, render_template
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


# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template('index.html')


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
            "dilate":       max(0, min(20, fp("dilate", int))),
            "erode":        max(0, min(20, fp("erode",  int))),
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

    svg, _, _ = run_vectorize(img_bytes, params)

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
        app.run(host=_host, port=_port, debug=True, threaded=True)
