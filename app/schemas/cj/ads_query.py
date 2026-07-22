from pydantic import BaseModel, Field, field_validator

# CJ's GraphQL SortBy enum only contains these two members. There is no discount sort:
# use `discount_percentage` as a minimum threshold and sort the returned page client-side.
CJ_SORT_FIELDS = ('LAST_UPDATED', 'PRICE')
CJ_SORT_ORDERS = ('ASC', 'DESC')


class CJAdsProductsQueryRequest(BaseModel):
    company_id: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    advertiser_countries: list[str] = Field(default_factory=list)
    availability: str = Field(default='IN_STOCK', min_length=1)
    partner_status: str = Field(default='JOINED')
    partner_ids: list[str] = Field(default_factory=list)
    pid: str = Field(min_length=1)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    page: str | None = None
    discount_percentage: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description='Minimum discount percentage, not an exact match. Products discounted by at least this much are returned.',
        examples=[30],
    )
    low_price: float | None = Field(
        default=None,
        ge=0,
        description='Minimum product price.',
        examples=[20],
    )
    high_price: float | None = Field(
        default=None,
        ge=0,
        description='Maximum product price.',
        examples=[100],
    )
    brand: str | None = Field(
        default=None,
        description='Restrict results to a single brand name.',
        examples=['Nike'],
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            f"Sort field. Allowed values: {', '.join(CJ_SORT_FIELDS)}. "
            'Setting this omits nextPage from the response: CJ rejects sorting combined with cursor pagination.'
        ),
        examples=['PRICE'],
    )
    sort_order: str | None = Field(
        default=None,
        description=f"Sort direction. Allowed values: {', '.join(CJ_SORT_ORDERS)}. Only meaningful together with sort_by.",
        examples=['DESC'],
    )

    @field_validator('sort_by')
    @classmethod
    def _validate_sort_by(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in CJ_SORT_FIELDS:
            raise ValueError(f"sort_by must be one of {', '.join(CJ_SORT_FIELDS)}")
        return normalized

    @field_validator('sort_order')
    @classmethod
    def _validate_sort_order(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in CJ_SORT_ORDERS:
            raise ValueError(f"sort_order must be one of {', '.join(CJ_SORT_ORDERS)}")
        return normalized
