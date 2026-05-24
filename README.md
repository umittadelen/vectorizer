# Vectorizer

A single-file, local-first tool that converts raster images into clean SVG vector art using [potrace](http://potrace.sourceforge.net/). All settings update live in the browser with no page reloads.

## Features

- **Live preview** — 13 adjustable parameters regenerate the SVG in real time (~280 ms debounce)
- **3-tab viewer** — Original · Pre-threshold bitmap · Editable B&W bitmap, side-by-side with the SVG output
- **Pixel editor** — Draw or erase directly on the B&W bitmap before retracing
  - Ink, Erase, and Pan tools
  - 4 brush sizes (1 × 1, 2 × 2, 4 × 4 and 8 × 8 px with circular shapes for larger sizes)
  - Shift + click draws a straight line from the last position with a live preview
  - Colour-coded cursor preview (green = ink, red = erase)
- **Undo / Redo** — Up to 30 steps on the bitmap canvas
- **Zoom & pan** — Mouse-wheel zoom centred on the cursor, middle-click pan, crosshair guides
- **Project save / load** — Export the full session (all slider values + original image + bitmap edits + undo/redo history) as a single `.json` file; re-import by dragging it onto the drop zone or picking it from the file dialog
- **Download SVG** — Clean, normalized SVG output named after the source file
- **CLI mode** — `python server.py input.png output.svg [options]`

## Requirements

| Dependency | Minimum version |
|---|---|
| Python | 3.9 |
| [Flask](https://flask.palletsprojects.com/) | 2.0 |
| [Pillow](https://pillow.readthedocs.io/) | 9.0 |
| [potrace](http://potrace.sourceforge.net/) | 1.16 |

### Installing potrace

| OS | Method |
|---|---|
| **Windows** | Download `potrace.exe` from [potrace.sourceforge.net](http://potrace.sourceforge.net/#downloading) and place it in the project folder, **or** add it to your `PATH` |
| **macOS** | `brew install potrace` |
| **Linux** | `sudo apt install potrace` (Debian/Ubuntu) · `sudo dnf install potrace` (Fedora) |

> The script automatically detects `potrace.exe` / `potrace` placed next to `server.py` and prefers it over the system `PATH`.

## Quick start

```bash
git clone https://github.com/umittadelen/vectorizer.git
cd vectorizer

pip install flask Pillow

# Place potrace.exe (Windows) or ensure potrace is on PATH (macOS/Linux)

python server.py        # browser opens http://localhost:5000 automatically
```

Drop or pick any raster image (PNG, JPEG, BMP, GIF, TIFF, WebP) to begin.

## CLI usage

```bash
python server.py input.png output.svg
python server.py input.png output.svg --threshold 128 --blur 0.5 --no-invert
```

Run `python server.py --help` for the full parameter list.

## Sliders & parameters

| Parameter | Range | Description |
|---|---|---|
| Threshold | 0 – 255 | Ink/paper cut-off after all tonal adjustments |
| Blur | 0 – 5 | Gaussian blur radius applied before thresholding |
| Brightness | 0.1 – 3 | Overall lightness multiplier |
| Gamma | 0.1 – 3 | Gamma curve correction |
| Contrast | 0.1 – 3 | Contrast multiplier |
| Sharpen | 0 – 5 | Unsharp-mask strength |
| Dilate | 0 – 10 | Expand ink regions (morphological dilation) |
| Erode | 0 – 10 | Shrink ink regions (morphological erosion) |
| Alpha max | 0 – 1.34 | potrace corner-smoothing threshold |
| Opt tolerance | 0 – 1 | potrace curve-optimisation tolerance |
| Turd size | 0 – 100 | potrace minimum speckle area to suppress |
| Invert | toggle | Swap ink and paper colours before tracing |
| Corner break | toggle | Morphological opening that removes diagonal-only pixel connections |

## Keyboard shortcuts

| Key | Action |
|---|---|
| `I` | Ink tool |
| `E` | Erase tool |
| `P` / `Space` | Pan tool |
| `1` `2` `3` `4` | Brush size 1 / 2 / 4 / 8 px |
| `[` `]` | Cycle brush size smaller / larger |
| `Shift` + click | Draw a straight line from the last painted position |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` / `Ctrl+Shift+Z` | Redo |

> Tool and brush shortcuts (`I` / `E` / `P` / `Space` / `1`–`4` / `[` / `]`) are only active while the **Bitmap** tab is selected.

## Project export / import

Click **↗ Export** in the sidebar to save a `.vectorize.json` file containing:

- All slider values
- The original source image (base64)
- The current bitmap canvas state
- The full undo and redo stacks

To restore a session, drag the `.json` file onto the drop zone, pick it via the file dialog, or click **↙ Import**.

## License

MIT — see [LICENSE](LICENSE)
