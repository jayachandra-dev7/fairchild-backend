"""The 'sellable' layer: price band, real discount, stock, ignore keywords, dedup, ranking."""
from __future__ import annotations

import random
from collections import Counter

from app.db import automation_store
from app.schemas.automation import HARD_MAX_PRICE, Candidate, ProductAutomationSettings, parse_keywords

MAX_BELIEVABLE_DISCOUNT = 90.0  # beyond this the 'original price' is usually fake or an error


def reject_reason(c: Candidate, cfg: ProductAutomationSettings, ignore: list[str]) -> str | None:
    if not c.title or not c.buy_url or not c.image_url:
        return 'missing title, link or image'
    if not c.in_stock:
        return 'out of stock'
    if c.adult:
        return 'flagged adult'
    if c.sale_price <= 0 or c.price <= c.sale_price:
        return 'no real discount'
    if c.discount < cfg.min_discount:
        return f'discount {c.discount:.0f}% below minimum {cfg.min_discount:.0f}%'
    if c.discount > MAX_BELIEVABLE_DISCOUNT:
        return f'discount {c.discount:.0f}% not believable'
    if c.sale_price < cfg.min_price:
        return f'price ${c.sale_price:.2f} below minimum'
    if c.sale_price > min(cfg.max_price, HARD_MAX_PRICE):
        return f'price ${c.sale_price:.2f} above maximum'
    haystack = f'{c.title} {c.brand} {c.source_category} {c.description[:300]}'.lower()
    for word in ignore:
        if word in haystack:
            return f'ignored keyword "{word}"'
    return None


def filter_and_rank(candidates: list[Candidate], cfg: ProductAutomationSettings) -> tuple[list[Candidate], Counter]:
    ignore = parse_keywords(cfg.ignore_keywords)
    rejected: Counter = Counter()
    kept: list[Candidate] = []
    seen_keys: set[tuple[str, str]] = set()

    already: dict[str, set[str]] = {}
    for source in {c.source for c in candidates}:
        already[source] = automation_store.seen_ids(source, [c.source_id for c in candidates if c.source == source])

    for c in candidates:
        key = (c.source, c.source_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if c.source_id in already.get(c.source, set()):
            rejected['already posted'] += 1
            continue
        reason = reject_reason(c, cfg, ignore)
        if reason:
            rejected[reason.split(' ')[0] if reason.startswith('discount') else reason] += 1
            continue
        kept.append(c)

    # Random pick among the deals that passed every filter (CJ and Impact mixed); only keyword relevance to the
    # category outranks chance. sort() is stable, so shuffling first randomises ties.
    random.shuffle(kept)
    kept.sort(key=lambda c: c.relevance, reverse=True)
    # Drop near-duplicate titles (same product from two networks / variants).
    unique: list[Candidate] = []
    titles: set[str] = set()
    for c in kept:
        norm = ' '.join(c.title.lower().split())[:60]
        if norm in titles:
            continue
        titles.add(norm)
        unique.append(c)
    return unique, rejected
