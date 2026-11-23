from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.schemas.common import ApiResponse
from app.services.review import content_policy

router = APIRouter(prefix='/review', tags=['Content review'])


class ReviewItem(BaseModel):
    id: str
    title: str = ''
    brand: str = ''
    description: str = ''
    image_text: str = ''
    caption: str = ''
    url: str = ''
    price: float | None = None
    discount: float | None = None
    platform: str = ''


class ReviewRequest(BaseModel):
    items: list[ReviewItem] = Field(max_length=100)


class KeywordsBody(BaseModel):
    keywords: str = Field(default='', max_length=4000, description='Comma/newline separated reject keywords.')


@router.get('/keywords', response_model=ApiResponse[dict])
def get_keywords() -> ApiResponse[dict]:
    return ApiResponse(data={'keywords': content_policy.shared_keywords()})


@router.put('/keywords', response_model=ApiResponse[dict])
def put_keywords(body: KeywordsBody) -> ApiResponse[dict]:
    return ApiResponse(data={'keywords': content_policy.save_shared_keywords(body.keywords)})


@router.post('/products', response_model=ApiResponse[list])
async def review_products(body: ReviewRequest) -> ApiResponse[list]:
    return ApiResponse(data=await content_policy.review_items([i.model_dump() for i in body.items]))
