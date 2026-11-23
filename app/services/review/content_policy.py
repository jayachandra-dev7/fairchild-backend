"""Content-safety review for the affiliate workflow (products and the text written for them).

Step 1 is free: the shared reject-keyword list (the same `ignore_keywords` the GPC automation uses).
Step 2 is one batched Claude call for whatever the keywords did not already decide. Claude failures block
(fail closed) so a product is never posted unreviewed; the UI offers an Override.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from app.db import automation_store
from app.schemas.automation import ProductAutomationSettings, parse_keywords
from app.services.automation import claude_review

logger = logging.getLogger(__name__)

POLICY_SYSTEM = (
    'You are the brand-safety reviewer for an affiliate deals page that posts on Facebook, Instagram and Pinterest. '
    'Meta flags pages for posts that use real people or personas, restricted goods and misleading claims. '
    'Decide for each item whether it is safe to post. Respond with a single JSON object and nothing else.'
)

POLICY_RULES = (
    'Be LENIENT. Almost everything on a normal deals page is fine. REJECT only when an item clearly:\n'
    '- uses the name, nickname, ring name or persona of a real wrestler/entertainer/public figure as the product\'s '
    'identity (for example "The Deadman", "The Undertaker"), or says a real person recommends or endorses it.\n'
    '- uses violent or threatening wording as the product theme (for example "kill", "murder").\n'
    '- is a firearm, ammunition, illegal drug, or sexual/adult-toy product.\n'
    '- makes a miracle, medical-cure or guaranteed weight-loss / before-and-after claim.\n'
    'ALWAYS APPROVE: sports-team and league apparel (including player jerseys), college and brand merchandise, '
    'officially licensed character items (Disney, Marvel, Nerf and similar), toys, kitchen knives, cookware, '
    'camping/hunting apparel, supplements and protein powder without cure claims, skincare and cosmetics, '
    'electronics, shoes and everyday goods. When unsure, APPROVE.'
)

_cache: dict[str, dict[str, Any]] = {}


def shared_keywords() -> list[str]:
    stored = automation_store.load_settings('product') or {}
    return parse_keywords(ProductAutomationSettings(**stored).ignore_keywords)


def save_shared_keywords(text: str) -> list[str]:
    """Writes only the shared word list; every other GPC product setting is left as it is."""
    stored = ProductAutomationSettings(**(automation_store.load_settings('product') or {})).model_dump()
    stored['ignore_keywords'] = ', '.join(parse_keywords(text))
    automation_store.save_settings('product', stored)
    return parse_keywords(stored['ignore_keywords'])


def keyword_hit(text: str, words: list[str]) -> str | None:
    hay = ' ' + ' '.join(re.sub(r'[^a-z0-9]+', ' ', text.lower()).split()) + ' '
    for w in words:
        needle = ' '.join(re.sub(r'[^a-z0-9]+', ' ', w.lower()).split())
        if needle and f' {needle} ' in hay:
            return w
    return None


def _key(item: dict[str, Any], words: list[str]) -> str:
    raw = '|'.join(str(item.get(k, '')) for k in ('title', 'brand', 'description', 'image_text', 'caption', 'url', 'price', 'discount', 'platform')) + '|' + ','.join(words)
    return hashlib.sha1(raw.encode()).hexdigest()


def _url_words(url: str) -> str:
    """Readable words from the product link (hosts and slugs often carry the product name, e.g. 'the-deadman-tee')."""
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', (url or '').lower().split('?')[0]).split())[:160]


def _text(item: dict[str, Any]) -> str:
    return ' '.join(str(item.get(k, '') or '') for k in ('title', 'brand', 'description', 'image_text', 'caption')) + ' ' + _url_words(str(item.get('url', '')))


def _prompt(items: list[dict[str, Any]], words: list[str]) -> str:
    rows = [
        {
            'id': it['id'], 'title': str(it.get('title', ''))[:160], 'brand': str(it.get('brand', ''))[:60],
            'description': str(it.get('description', ''))[:200], 'image_text': str(it.get('image_text', ''))[:60],
            'caption': str(it.get('caption', ''))[:300], 'platform': it.get('platform', ''), 'price': it.get('price'),
            'discount_percent': it.get('discount'), 'link_words': _url_words(str(it.get('url', ''))),
        }
        for it in items
    ]
    extra = f'\nThe owner also blocks these words and names (reject anything related to them): {", ".join(words)}.\n' if words else '\n'
    return (
        f'{POLICY_RULES}{extra}\nItems (JSON):\n{json.dumps(rows, ensure_ascii=False)}\n\n'
        'Return exactly: {"results":[{"id":"<id>","verdict":"approve"|"reject","reason":"short reason (empty when approved)"}]}'
    )


async def review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns one {id, verdict, reason, source} per input item, in order. source: keyword | claude | error."""
    words = shared_keywords()
    out: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    for it in items:
        iid = str(it['id'])
        hit = keyword_hit(_text(it), words)
        if hit:
            out[iid] = {'id': iid, 'verdict': 'reject', 'reason': f'Blocked word: "{hit}"', 'source': 'keyword'}
            continue
        cached = _cache.get(_key(it, words))
        if cached:
            out[iid] = {**cached, 'id': iid}
            continue
        pending.append(it)

    for start in range(0, len(pending), 20):
        batch = pending[start : start + 20]
        verdicts: dict[str, Any] = {}
        for attempt in range(2):  # one retry: a transient API hiccup should not block products
            try:
                text = await claude_review.ask_claude(_prompt(batch, words), max_tokens=120 + 60 * len(batch), temperature=0, system=POLICY_SYSTEM)
                verdicts = {str(r.get('id')): r for r in (claude_review.extract_json(text).get('results') or [])}
                break
            except Exception as exc:  # noqa: BLE001 - after the retry this fails closed
                logger.warning('content_review_failed', extra={'error': exc.__class__.__name__, 'attempt': attempt + 1})
        for it in batch:
            iid = str(it['id'])
            v = verdicts.get(iid)
            if v and str(v.get('verdict')).lower() in {'approve', 'reject'}:
                res = {'id': iid, 'verdict': str(v['verdict']).lower(), 'reason': str(v.get('reason', ''))[:200], 'source': 'claude'}
                _cache[_key(it, words)] = res
                if len(_cache) > 5000:
                    _cache.pop(next(iter(_cache)))
            else:
                res = {'id': iid, 'verdict': 'reject', 'reason': 'Could not be reviewed - try again or override', 'source': 'error'}
            out[iid] = res
    return [out[str(it['id'])] for it in items]
