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
        file_bytes = await picture.read()
        return await cls.upload_media_bytes(
            domain=domain,
            wc_consumer_key=wc_consumer_key,
            wc_consumer_secret=wc_consumer_secret,
            filename=picture.filename or 'upload.bin',
            content_type=picture.content_type or 'application/octet-stream',
            file_bytes=file_bytes,
        )

    @classmethod
    async def upload_media_bytes(
        cls,
        *,
        domain: str,
        wc_consumer_key: str,
        wc_consumer_secret: str,
        filename: str,
        content_type: str,
        file_bytes: bytes,
    ) -> Any:
        base = cls._normalize_domain(domain)
        url = f'{base}/wp-json/wp/v2/media'
        files = {
            'file': (
                filename or 'upload.bin',
                file_bytes,
                content_type or 'application/octet-stream',
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
    async def upload_media_from_url(
        cls,
        *,
        domain: str,
        wc_consumer_key: str,
        wc_consumer_secret: str,
        image_url: str,
    ) -> Any:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(image_url)
            response.raise_for_status()
            content_type = response.headers.get('content-type', 'application/octet-stream')
            filename = image_url.split('/')[-1] or 'remote-image'
            return await cls.upload_media_bytes(
                domain=domain,
                wc_consumer_key=wc_consumer_key,
                wc_consumer_secret=wc_consumer_secret,
                filename=filename,
                content_type=content_type,
                file_bytes=response.content,
            )

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

    @classmethod
    async def list_product_categories(
        cls,
        *,
        domain: str,
        wc_consumer_key: str,
        wc_consumer_secret: str,
        per_page: int = 100,
        page: int = 1,
    ) -> Any:
        base = cls._normalize_domain(domain)
        url = f'{base}/wp-json/wc/v3/products/categories'

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                url,
                auth=(wc_consumer_key, wc_consumer_secret),
                params={
                    'per_page': per_page,
                    'page': page,
                },
            )
            response.raise_for_status()
            return response.json()
