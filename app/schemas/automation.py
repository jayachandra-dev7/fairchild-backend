from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

DEFAULT_IGNORE_KEYWORDS = (
    'gun, rifle, pistol, firearm, ammo, ammunition, shotgun, silencer, switchblade, '
    'vape, e-cigarette, cigar, tobacco, kratom, cbd, weed, '
    'adult toy, sex, erotic, lingerie, '
    'prescription, gift card'
)
HARD_MAX_PRICE = 2000.0
TEMPLATE_DEFAULT = 'burly-bears-drink-shyly-1844'


def parse_keywords(text: str) -> list[str]:
    """Split the free-text ignore box on commas/newlines/semicolons; lowercase, de-duplicate."""
    seen: list[str] = []
    for part in re.split(r'[,\n;]+', text or ''):
        word = part.strip().lower()
        if word and word not in seen:
            seen.append(word)
    return seen


class ScheduleSettings(BaseModel):
    enabled: bool = False
    daily_time: str = Field(default='09:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    timezone: str = 'America/Denver'


class ProductAutomationSettings(ScheduleSettings):
    categories_per_day: int = Field(default=100, ge=1, le=5000)
    products_per_category: int = Field(default=2, ge=1, le=10)
    min_discount: float = Field(default=20, ge=0, le=95, description='Minimum discount percentage to accept a deal.')
    min_price: float = Field(default=5, ge=0)
    max_price: float = Field(default=500, ge=0, le=HARD_MAX_PRICE)
    ignore_keywords: str = Field(default=DEFAULT_IGNORE_KEYWORDS, description='Free text; comma/newline separated.')
    use_cj: bool = True
    use_impact: bool = True
    cj_advertiser_ids: list[str] = Field(default_factory=list, description='Empty = every joined advertiser.')
    impact_campaign_ids: list[str] = Field(default_factory=list, description='Empty = every active campaign.')
    claude_review: bool = True
    branded_image: bool = True  # always on: every product gets a branded image from the chosen template
    template_id: str = TEMPLATE_DEFAULT
    exclude_category_ids: list[int] = Field(default_factory=list)
    publish_status: str = Field(default='publish', pattern='^(publish|draft|pending)$')

    @field_validator('branded_image', mode='before')
    @classmethod
    def _always_branded(cls, _value: Any) -> bool:
        return True

    @field_validator('max_price')
    @classmethod
    def _cap(cls, value: float) -> float:
        return min(value, HARD_MAX_PRICE)


class BlogAutomationSettings(ScheduleSettings):
    daily_time: str = Field(default='12:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    blogs_per_day: int = Field(default=5, ge=1, le=50)
    min_discount: float = Field(default=0, ge=0, le=95, description='Only feature products discounted at least this much.')
    topic_hint: str = ''
    publish_status: str = Field(default='publish', pattern='^(publish|draft|pending)$')


class AutomationSettings(BaseModel):
    product: ProductAutomationSettings = Field(default_factory=ProductAutomationSettings)
    blog: BlogAutomationSettings = Field(default_factory=BlogAutomationSettings)


class RunRequest(BaseModel):
    dry_run: bool = False
    max_categories: int | None = Field(default=None, ge=1, le=5000)
    category_ids: list[int] | None = Field(default=None, description='Run only these leaf categories (testing).')


class Candidate(BaseModel):
    """A product from CJ or Impact, normalised."""

    source: str  # 'cj' | 'impact'
    source_id: str
    title: str
    description: str = ''
    brand: str = ''
    advertiser: str = ''
    image_url: str = ''
    buy_url: str = ''
    price: float = 0.0  # original / list price
    sale_price: float = 0.0  # current price
    discount: float = 0.0  # percent
    currency: str = 'USD'
    source_category: str = ''
    in_stock: bool = True
    adult: bool = False
    relevance: int = 0
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
