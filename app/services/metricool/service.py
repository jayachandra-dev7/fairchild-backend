from typing import Any

import httpx
from fastapi import UploadFile

from app.schemas.metricool.post import MetricoolSchedulerPostRequest


class MetricoolService:
    profiles_url = 'https://app.metricool.com/api/admin/simpleProfiles'
    upload_url = 'https://app.metricool.com/api/utils/upload'
    scheduler_url = 'https://app.metricool.com/api/v2/scheduler/posts'
    pinterest_boards_url = 'https://app.metricool.com/api/v2/scheduler/boards/pinterest'

    @classmethod
    def _parse_response(cls, response: httpx.Response) -> Any:
        content_type = response.headers.get('content-type', '').lower()
        if 'application/json' in content_type:
            return response.json()
        text = response.text.strip()
        if not text:
            return {}
        return {'raw_text': text}

    @classmethod
    async def get_simple_profiles(
        cls,
        *,
        token: str,
    ) -> Any:
        headers = {
            'X-Mc-Auth': token,
            'Content-Type': 'application/json',
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                cls.profiles_url,
                headers=headers,
            )
            response.raise_for_status()
            return cls._parse_response(response)

    @classmethod
    async def upload_media(
        cls,
        *,
        token: str,
        user_id: str,
        blog_id: str,
        picture: UploadFile,
    ) -> Any:
        headers = {'X-Mc-Auth': token}
        params = {'userId': user_id, 'blogId': blog_id}
        file_bytes = await picture.read()
        files = {
            'picture': (
                picture.filename or 'upload.bin',
                file_bytes,
                picture.content_type or 'application/octet-stream',
            )
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                cls.upload_url,
                params=params,
                headers=headers,
                files=files,
            )
            response.raise_for_status()
            return cls._parse_response(response)

    @classmethod
    async def create_scheduler_post(
        cls,
        *,
        token: str,
        user_id: str,
        blog_id: str,
        payload: MetricoolSchedulerPostRequest,
    ) -> Any:
        headers = {
            'X-Mc-Auth': token,
            'Content-Type': 'application/json',
        }
        params = {'userId': user_id, 'blogId': blog_id}

        payload_dict = payload.model_dump(by_alias=True)
        payload_dict = cls._sanitize_scheduler_payload(payload_dict)

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                cls.scheduler_url,
                params=params,
                headers=headers,
                json=payload_dict,
            )
            response.raise_for_status()
            return cls._parse_response(response)

    @classmethod
    def _sanitize_scheduler_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        descendants = payload.get('descendants')
        if isinstance(descendants, list):
            cleaned_descendants = [item for item in descendants if isinstance(item, dict)]
            payload['descendants'] = cleaned_descendants

        twitter_data = payload.get('twitterData')
        if isinstance(twitter_data, dict):
            tags = twitter_data.get('tags')
            if isinstance(tags, list):
                twitter_data['tags'] = [tag for tag in tags if isinstance(tag, dict)]
            reply_settings = twitter_data.get('replySettings')
            if isinstance(reply_settings, str) and not reply_settings.strip():
                twitter_data['replySettings'] = None

        smart_link_data = payload.get('smartLinkData')
        if isinstance(smart_link_data, dict):
            ids = smart_link_data.get('ids')
            if isinstance(ids, list):
                smart_link_data['ids'] = [item for item in ids if isinstance(item, str) and item.strip()]

        media_alt_text = payload.get('mediaAltText')
        if isinstance(media_alt_text, list):
            payload['mediaAltText'] = [item if (item is None or (isinstance(item, str) and item.strip())) else None for item in media_alt_text]

        return payload

    @classmethod
    async def get_scheduler_posts(
        cls,
        *,
        token: str,
        user_id: str,
        blog_id: str,
        start: str,
        end: str,
        timezone: str,
        extended_range: bool,
    ) -> Any:
        headers = {
            'X-Mc-Auth': token,
            'Content-Type': 'application/json',
        }
        params = {
            'start': start,
            'end': end,
            'timezone': timezone,
            'extendedRange': str(extended_range).lower(),
            'userId': user_id,
            'blogId': blog_id,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                cls.scheduler_url,
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            return cls._parse_response(response)

    @classmethod
    async def get_pinterest_boards(
        cls,
        *,
        token: str,
        user_id: str,
        blog_id: str,
    ) -> Any:
        headers = {
            'X-Mc-Auth': token,
            'Content-Type': 'application/json',
        }
        params = {
            'userId': user_id,
            'blogId': blog_id,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                cls.pinterest_boards_url,
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            return cls._parse_response(response)
