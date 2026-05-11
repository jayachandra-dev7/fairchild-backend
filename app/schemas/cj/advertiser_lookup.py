from typing import Any

from pydantic import BaseModel, Field


class CJAdvertiserLookupQuery(BaseModel):
    requestor_cid: str = Field(alias='requestor-cid', min_length=1)
    advertiser_ids: str = Field(alias='advertiser-ids', min_length=1)


class CJAdvertiserLookupPayload(BaseModel):
    raw: dict[str, Any]
