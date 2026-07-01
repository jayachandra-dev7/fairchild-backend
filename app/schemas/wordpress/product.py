from typing import Any

from pydantic import BaseModel, Field


class WooProductImage(BaseModel):
    id: int


class WooProductCategory(BaseModel):
    id: int


class WooProductMetaData(BaseModel):
    key: str = Field(min_length=1)
    value: Any


class WooProductCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(default='external')
    status: str = Field(default='draft')
    featured: bool = Field(default=True)
    catalog_visibility: str = Field(default='visible')
    description: str = Field(default='')
    short_description: str = Field(default='')
    external_url: str = Field(default='')
    button_text: str = Field(default='Buy Now')
    regular_price: str = Field(default='')
    sale_price: str = Field(default='')
    categories: list[WooProductCategory] = Field(default_factory=list)
    images: list[WooProductImage] = Field(default_factory=list)
    meta_data: list[WooProductMetaData] = Field(default_factory=list)
