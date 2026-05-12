from typing import Any

import httpx
from fastapi import UploadFile

from app.schemas.wordpress.product import WooProductCreateRequest


class WordPressService:
    @classmethod
    def _normalize_domain(cls, domain: str) -> str:
        normalized = domain.strip().rstrip('/')
        if not normalized.startswith('http://') and not normalized.startswith('https://'):
            normalized = f'https://{normalized}'
        return normalized

    @classmethod
    async def upload_media(
        cls,
        *,
        domain: str,
        wc_consumer_key: str,
        wc_consumer_secret: str,
        picture: UploadFile,
    ) -> Any:
        base = cls._normalize_domain(domain)
        url = f'{base}/wp-json/wp/v2/media'
        file_bytes = await picture.read()
        files = {
            'file': (
                picture.filename or 'upload.bin',
                file_bytes,
                picture.content_type or 'application/octet-stream',
            )
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                auth=(wc_consumer_key, wc_consumer_secret),
                files=files,
            )
            response.raise_for_status()
            return response.json()

    @classmethod
    async def create_product(
        cls,
        *,
        domain: str,
        wc_consumer_key: str,
        wc_consumer_secret: str,
        payload: WooProductCreateRequest,
    ) -> Any:
        base = cls._normalize_domain(domain)
        url = f'{base}/wp-json/wc/v3/products'
        headers = {'Content-Type': 'application/json'}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                auth=(wc_consumer_key, wc_consumer_secret),
                headers=headers,
                json=payload.model_dump(),
            )
            response.raise_for_status()
            return response.json()
