from pydantic import BaseModel, Field


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
