"""Free Pillow re-implementation of the RenderForm templates (background + product image + title text)."""
from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

APP_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_PATH = APP_DIR / 'data' / 'renderform_templates.json'
PREVIEWS_DIR = APP_DIR / 'assets' / 'previews'
FONT_DIR = APP_DIR / 'assets' / 'fonts'


class RenderError(Exception):
    """Raised when a branded image cannot be produced (unknown template, bad image, missing font)."""


@lru_cache
def load_templates() -> dict[str, dict[str, Any]]:
    if not TEMPLATES_PATH.exists():
        raise RenderError(f'{TEMPLATES_PATH} not found. Run `python -m scripts.sync_renderform_templates`.')
    return json.loads(TEMPLATES_PATH.read_text(encoding='utf-8'))


def get_template(template_id: str) -> dict[str, Any]:
    templates = load_templates()
    if template_id in templates:
        return templates[template_id]
    # Allow selecting by display name too (e.g. "Template 1").
    for tpl in templates.values():
        if tpl['name'].lower() == template_id.strip().lower():
            return tpl
    raise RenderError(f'Unknown template "{template_id}". Known: {", ".join(t["name"] for t in templates.values())}')


def preview_path(template_id: str) -> Path | None:
    if template_id not in load_templates():
        return None
    path = PREVIEWS_DIR / f'{template_id}.jpg'
    return path if path.is_file() else None


def _display_order(name: str) -> tuple[int, str]:
    # "Template 1", "Template 2" first (the first one is the frontend's default selection), then the rest (Kumon...).
    return (0 if name.lower().startswith('template') else 1, name)


def list_templates() -> list[dict[str, Any]]:
    return [
        {
            'identifier': tid,
            'name': t['name'],
            'width': t['width'],
            'height': t['height'],
            'outputFormat': 'jpeg',
            'preview': f'/renderpython/previews/{tid}.jpg',
        }
        for tid, t in sorted(load_templates().items(), key=lambda kv: _display_order(kv[1]['name']))
    ]


@lru_cache(maxsize=64)
def _font(file_name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / file_name
    try:
        return ImageFont.truetype(str(path), size)
    except OSError as exc:
        raise RenderError(f'Font file missing or unreadable: {path}') from exc


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top, top


def _wrap_two_lines(text: str) -> list[str] | None:
    words = text.split()
    if len(words) < 2:
        return None
    best: tuple[int, list[str]] | None = None
    for i in range(1, len(words)):
        a, b = ' '.join(words[:i]), ' '.join(words[i:])
        diff = abs(len(a) - len(b))
        if best is None or diff < best[0]:
            best = (diff, [a, b])
    return best[1] if best else None


HEIGHT_FACTOR = 0.864  # RenderForm caps font size at ~86% of the title box height (measured on the account templates)
LINE_SPACING = 1.1


def _fit_text(draw: ImageDraw.ImageDraw, text: str, spec: dict[str, Any]) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """Largest font size that fits the box width (and the height cap), on one line or - if one line would be tiny - two."""
    box_w, box_h = spec['w'], spec['h']
    font_file = spec['font_file']

    def fits(lines: list[str], size: int) -> bool:
        font = _font(font_file, size)
        if max(_text_size(draw, ln, font)[0] for ln in lines) > box_w:
            return False
        factor = HEIGHT_FACTOR if len(lines) == 1 else 1.0
        return size * (1 + LINE_SPACING * (len(lines) - 1)) <= box_h * factor + 0.5

    def best_size(lines: list[str]) -> int:
        lo, hi, ans = 8, box_h, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(lines, mid):
                ans, lo = mid, mid + 1
            else:
                hi = mid - 1
        return ans

    one = [text]
    size_one = best_size(one)
    chosen, size = one, size_one
    two = _wrap_two_lines(text)
    if two and size_one < box_h * 0.45:
        size_two = best_size(two)
        if size_two > size_one:
            chosen, size = two, size_two
    if size < 8:
        raise RenderError('Title text does not fit the template box.')
    return chosen, _font(font_file, size)


def render_template(template_id: str, title: str, image_bytes: bytes) -> bytes:
    tpl = get_template(template_id)
    try:
        photo = Image.open(io.BytesIO(image_bytes))
        photo = ImageOps.exif_transpose(photo)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f'Product image could not be decoded: {exc}') from exc

    # Flatten transparency onto white (product PNGs are often transparent).
    if photo.mode in ('RGBA', 'LA', 'P'):
        photo = photo.convert('RGBA')
        flat = Image.new('RGB', photo.size, tuple(tpl['background']))
        flat.paste(photo, mask=photo.getchannel('A'))
        photo = flat
    else:
        photo = photo.convert('RGB')

    canvas = Image.new('RGB', (tpl['width'], tpl['height']), tuple(tpl['background']))
    img = tpl['image']
    box_w, box_h = img['w'], img['h']
    if img.get('fit', 'contain') == 'cover':
        fitted = ImageOps.fit(photo, (box_w, box_h), Image.LANCZOS)
    else:
        scale = min(box_w / photo.width, box_h / photo.height)
        fitted = photo.resize((max(1, round(photo.width * scale)), max(1, round(photo.height * scale))), Image.LANCZOS)
    px = img['x'] + (box_w - fitted.width) // 2
    py = img['y'] + (box_h - fitted.height) // 2
    canvas.paste(fitted, (px, py))  # negative offsets are clipped by Pillow, like the template's off-canvas image

    spec = tpl['title']
    text = (title or spec.get('default_text') or '').strip()
    if text:
        draw = ImageDraw.Draw(canvas)
        lines, font = _fit_text(draw, text, spec)
        # First baseline sits at a per-template fraction of the box height (measured from RenderForm output).
        y = spec['y'] + spec.get('baseline_ratio', 0.78) * spec['h']
        for line in lines:
            left, _, right, _ = draw.textbbox((0, 0), line, font=font, anchor='ls')
            x = spec['x'] + (spec['w'] - (right - left)) / 2 - left
            draw.text((round(x), round(y)), line, font=font, fill=tuple(spec['color']), anchor='ls')
            y += font.size * LINE_SPACING

    out = io.BytesIO()
    canvas.save(out, format='JPEG', quality=int(tpl.get('quality', 90)), optimize=True)
    return out.getvalue()
