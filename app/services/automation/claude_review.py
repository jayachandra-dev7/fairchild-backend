"""Claude layer: reviews shortlisted deals for a category and writes the WordPress copy."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import get_settings
from app.schemas.automation import Candidate
from app.services.claude.service import ClaudeService
from app.utils.retry import run_with_retry

logger = logging.getLogger(__name__)

SYSTEM = (
    'You are the merchandising reviewer for an affiliate deals website. You decide which discounted products '
    'genuinely belong in a given product category and are appropriate to sell to a general audience, and you write '
    'accurate, non-hyped product copy. Use ONLY facts present in the product data; never invent specifications, '
    'ratings or claims. Respond with a single JSON object and nothing else.'
)


def extract_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction: strips code fences and scans for the first balanced object."""
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`')
        cleaned = cleaned[cleaned.find('{') :] if '{' in cleaned else cleaned
    start = cleaned.find('{')
    if start < 0:
        raise ValueError('no JSON object in model output')
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : i + 1])
    raise ValueError('unterminated JSON object in model output')


def models() -> list[str]:
    return [m.strip() for m in get_settings().AUTOMATION_CLAUDE_MODELS.split(',') if m.strip()]


async def ask_claude(prompt: str, *, max_tokens: int, temperature: float = 0.3, system: str = SYSTEM, pdf_base64: str | None = None) -> str:
    api_key = get_settings().CLAUDE_API_KEY
    if not api_key:
        raise RuntimeError('CLAUDE_API_KEY is not configured.')
    result = await run_with_retry(
        step='claude_generate',
        operation=lambda: ClaudeService.generate_with_fallback(
            api_key=api_key,
            model_candidates=models(),
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            response_json=False,
            pdf_base64=pdf_base64,
            disable_thinking=True,  # these are extraction/classification tasks; thinking only adds billed output tokens
        ),
    )
    return str(result['text'])


def build_prompt(leaf: dict[str, Any], candidates: list[Candidate], want: int, branded: bool) -> str:
    items = [
        {
            'id': c.source_id,
            'title': c.title,
            'brand': c.brand,
            'advertiser': c.advertiser,
            'list_price': c.price,
            'sale_price': c.sale_price,
            'discount_percent': c.discount,
            'source_category': c.source_category,
            'description': c.description[:500],
        }
        for c in candidates
    ]
    image_rule = (
        '- "image_text": 2-4 word punchy overlay for the product image, max 28 characters, no emojis, no price.\n'
        if branded
        else ''
    )
    return (
        f'Target store category: "{leaf["path"]}"\n\n'
        f'Candidate discounted products (JSON):\n{json.dumps(items, ensure_ascii=False)}\n\n'
        f'Select up to {want} products, best first, that clearly belong in the target category, are real sellable '
        'consumer products, and are safe for a general audience (reject weapons, adult, tobacco/vape, prescription, '
        'counterfeit-looking or mismatched items, and bundles/accessories that do not fit the category). '
        'If fewer qualify, return fewer; if none qualify return an empty list.\n\n'
        'For every approved product write:\n'
        '- "title": clean product name, max 70 characters, no ALL CAPS, no seller spam.\n'
        '- "short_description": 1-2 plain sentences (no HTML).\n'
        '- "description": 2 short HTML paragraphs (<p>...</p>), 60-120 words total, factual.\n'
        f'{image_rule}\n'
        'Return exactly:\n'
        '{"approved":[{"id":"<candidate id>","title":"...","short_description":"...","description":"<p>..</p>"'
        + (',"image_text":"..."' if branded else '')
        + '}],"rejected":[{"id":"<candidate id>","reason":"short reason"}]}'
    )


async def review_candidates(
    leaf: dict[str, Any], candidates: list[Candidate], want: int, branded: bool
) -> tuple[list[tuple[Candidate, dict[str, Any]]], list[dict[str, Any]]]:
    """Returns ([(candidate, copy)], rejected). Raises on Claude/parse failure (caller fails closed)."""
    prompt = build_prompt(leaf, candidates, want, branded)
    text = await ask_claude(prompt, max_tokens=3500)
    data = extract_json(text)
    by_id = {c.source_id: c for c in candidates}
    approved: list[tuple[Candidate, dict[str, Any]]] = []
    for entry in data.get('approved') or []:
        cand = by_id.get(str(entry.get('id')))
        if cand and entry.get('title'):
            approved.append((cand, entry))
    return approved, list(data.get('rejected') or [])


def fallback_copy(c: Candidate) -> dict[str, Any]:
    """Copy used when Claude review is switched off in settings."""
    desc = ' '.join(c.description.split())[:300]
    return {
        'id': c.source_id,
        'title': c.title[:70],
        'short_description': desc[:160],
        'description': f'<p>{desc}</p>' if desc else '',
        'image_text': c.title[:28].upper(),
    }


_kw_cache: dict[int, list[str]] = {}


async def suggest_keywords(leaf: dict[str, Any]) -> list[str]:
    """A few concrete shopping search phrases for a store category (cached per process).

    Category names like "Tech & Gadgets" or "Beauty" make poor product searches on their own. Falls back to the
    plain category name if Claude fails.
    """
    plain = leaf['name']
    if leaf['id'] in _kw_cache:
        return _kw_cache[leaf['id']]
    out = [plain]
    try:
        text = await ask_claude(
            f'Store category path: "{leaf["path"]}"\n\n'
            'Give up to 4 short product search phrases (1-3 words each) that a shopper would type into a retailer '
            'search box to find products that clearly belong in this category. Be specific and concrete (e.g. '
            '"wireless earbuds", not "gadgets"). No brand names, no punctuation.\n'
            'Return exactly: {"keywords":["...","..."]}',
            max_tokens=200,
            temperature=0.2,
            system='You write retail search queries. Respond with a single JSON object and nothing else.',
        )
        found = [str(k).strip() for k in (extract_json(text).get('keywords') or []) if str(k).strip()]
        out = list(dict.fromkeys([k for k in found if len(k) <= 40][:4] + [plain]))
    except Exception:  # noqa: BLE001 - keywords are an optimisation, never a reason to fail the category
        logger.warning('keyword_suggestion_failed', extra={'category': leaf['id']})
    _kw_cache[leaf['id']] = out
    return out
