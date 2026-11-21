"""Product fingerprints: tiny keys that recognise 'the same product' even when a network lists it under a new id.

The same item shows up under several advertiser/campaign ids (Impact: product_4595_101529191-3943173 and
product_895_3943173 are one product) and in several categories, so the network id alone is not enough.
Two products are the same when ANY of core_id / title_key / image_key / desc_key matches.
"""
from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import urlparse

from app.schemas.automation import Candidate


class Fingerprint(NamedTuple):
    core_id: str | None
    title_key: str | None
    image_key: str | None
    desc_key: str | None

    def keys(self) -> list[str]:
        return [f'{kind}:{v}' for kind, v in zip(('core', 'title', 'image', 'desc'), self) if v]


def core_id(source: str, source_id: str) -> str | None:
    """The product part of the network id, without the advertiser/campaign prefix."""
    sid = (source_id or '').strip()
    tail = sid.split(':')[-1] if source == 'cj' else re.split(r'[-_]', sid)[-1]
    return f'{source}:{tail}' if tail.isdigit() and len(tail) >= 5 else None


def title_key(title: str) -> str | None:
    key = ' '.join(re.sub(r'[^a-z0-9]+', ' ', (title or '').lower()).split())[:80]
    return key if len(key) >= 8 else None


def image_key(url: str) -> str | None:
    name = urlparse(url or '').path.rsplit('/', 1)[-1].rsplit('.', 1)[0].lower()
    return name if len(name) >= 12 else None  # short names ("1", "main") are shared by unrelated products


def desc_key(description: str) -> str | None:
    """Brands list size/colour variants with a new title but the same description text."""
    text = ' '.join(re.sub(r'<[^>]+>', ' ', description or '').lower().split())
    text = ' '.join(re.sub(r'[^a-z0-9]+', ' ', text).split())[:150]
    return text if len(text) >= 60 else None  # very short descriptions are too generic to compare


def fingerprint(c: Candidate) -> Fingerprint:
    return Fingerprint(core_id(c.source, c.source_id), title_key(c.title), image_key(c.image_url), desc_key(c.description))
