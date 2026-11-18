"""Pull layer geometry for your RenderForm templates into app/data/renderform_templates.json.

Usage (from fairchild-backend/): python -m scripts.sync_renderform_templates
Keeps any hand-tuned `fit` / `font_file` already present for a template id.
"""
import json
from pathlib import Path

import httpx

from app.core.config import get_settings

OUT = Path(__file__).resolve().parents[1] / 'app' / 'data' / 'renderform_templates.json'
FONT_FILES = {
    'Anton': 'Anton-Regular.ttf',
    'Bungee': 'Bungee-Regular.ttf',
    'Rammetto One': 'RammettoOne-Regular.ttf',
}


def _rgba(value: str) -> list[int]:
    nums = [float(x) for x in value[value.find('(') + 1 : value.find(')')].split(',')]
    return [int(n) for n in nums[:3]]


def main() -> None:
    key = get_settings().RENDERFORM_API_KEY
    if not key:
        raise SystemExit('RENDERFORM_API_KEY is not set')
    headers = {'X-API-KEY': key}
    existing = json.loads(OUT.read_text()) if OUT.exists() else {}
    listing = httpx.get('https://get.renderform.io/api/v2/my-templates', headers=headers, timeout=30).json()
    out: dict = {}
    for item in listing:
        detail = httpx.get(
            f"https://get.renderform.io/api/v2/my-templates/{item['identifier']}", headers=headers, timeout=30
        ).json()
        comps: dict[str, dict[str, str]] = {}
        for prop in detail['properties']:
            comps.setdefault(prop['componentId'], {})[prop['property']] = prop['defaultValue']
        title, image = comps['title'], comps['image']
        family = title.get('fontFamily', '')
        prev = existing.get(item['identifier'], {})
        out[item['identifier']] = {
            'name': detail['name'],
            'width': detail['width'],
            'height': detail['height'],
            'quality': detail.get('quality', 90),
            'background': [255, 255, 255],
            'title': {
                'x': int(title['x']), 'y': int(title['y']), 'w': int(title['width']), 'h': int(title['height']),
                'font_family': family,
                'font_file': prev.get('title', {}).get('font_file') or FONT_FILES.get(family, ''),
                'color': _rgba(title.get('color', 'rgba(30,30,30,1)')),
                'default_text': title.get('text', ''),
                'baseline_ratio': prev.get('title', {}).get('baseline_ratio', 0.78),
            },
            'image': {
                'x': int(image['x']), 'y': int(image['y']), 'w': int(image['width']), 'h': int(image['height']),
                'fit': prev.get('image', {}).get('fit', 'contain'),
                'sample_src': image.get('src', ''),
            },
        }
    OUT.write_text(json.dumps(out, indent=2))
    print(f'Saved {len(out)} templates to {OUT}')


if __name__ == '__main__':
    main()
