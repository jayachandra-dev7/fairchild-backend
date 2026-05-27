from typing import Any

import base64
import httpx
import re


class RenderFormService:
    templates_url = 'https://get.renderform.io/api/v2/my-templates'
    render_url = 'https://get.renderform.io/api/v2/render'

    @classmethod
    async def list_templates(
        cls,
        *,
        api_key: str,
    ) -> Any:
        headers = {
            'X-API-KEY': api_key,
            'Content-Type': 'application/json',
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(cls.templates_url, headers=headers)
            response.raise_for_status()
            return response.json()

    @classmethod
    async def render_template(
        cls,
        *,
        api_key: str,
        template: str,
        data: dict[str, Any],
    ) -> Any:
        headers = {
            'X-API-KEY': api_key,
            'Content-Type': 'application/json',
        }
        payload = {
            'template': template,
            'data': data,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(cls.render_url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    @classmethod
    def to_data_url(cls, *, file_bytes: bytes, content_type: str) -> str:
        encoded = base64.b64encode(file_bytes).decode('ascii')
        media_type = content_type or 'application/octet-stream'
        return f'data:{media_type};base64,{encoded}'

    @classmethod
    def extract_rendered_image_url(cls, payload: Any) -> str | None:
        url_pattern = re.compile(r'^https?://', re.IGNORECASE)
        priority_keys = ('href', 'url', 'imageUrl', 'image_url', 'resultUrl', 'result_url', 'renderUrl', 'render_url')
        image_hints = ('.png', '.jpg', '.jpeg', '.webp', '/results/', 'cdn.renderform.io', 'get.renderform.io')

        def _is_image_url(value: str) -> bool:
            normalized = value.lower()
            return bool(url_pattern.match(value)) and any(hint in normalized for hint in image_hints)

        def _walk(node: Any) -> str | None:
            if isinstance(node, dict):
                for key in priority_keys:
                    value = node.get(key)
                    if isinstance(value, str) and _is_image_url(value):
                        return value
                for value in node.values():
                    found = _walk(value)
                    if found:
                        return found
            elif isinstance(node, list):
                for value in node:
                    found = _walk(value)
                    if found:
                        return found
            elif isinstance(node, str) and _is_image_url(node):
                return node
            return None

        return _walk(payload)
