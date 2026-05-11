from typing import Any
import xml.etree.ElementTree as ET

import httpx


class CJAdvertiserLookupService:
    base_url = 'https://advertiser-lookup.api.cj.com/v2/advertiser-lookup'

    @classmethod
    async def fetch_advertisers(
        cls,
        *,
        bearer_token: str,
        requestor_cid: str,
        advertiser_ids: str,
    ) -> dict[str, Any]:
        headers = {'Authorization': f'Bearer {bearer_token}'}
        params = {
            'requestor-cid': requestor_cid,
            'advertiser-ids': advertiser_ids,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(cls.base_url, headers=headers, params=params)
            response.raise_for_status()
            content_type = response.headers.get('content-type', '')
            if 'application/json' in content_type.lower():
                return {'content_type': content_type, 'json': response.json()}

            if 'xml' in content_type.lower():
                return {'content_type': content_type, 'xml': response.text}

            return {
                'content_type': content_type,
                'raw_text': response.text,
            }

    @classmethod
    def xml_to_json(cls, xml_text: str) -> dict[str, Any]:
        root = ET.fromstring(xml_text)
        return {root.tag: cls._element_to_obj(root)}

    @classmethod
    def _element_to_obj(cls, element: ET.Element) -> Any:
        children = list(element)
        if not children and not element.attrib:
            text = (element.text or '').strip()
            return text if text else None

        result: dict[str, Any] = {}
        for attr_key, attr_value in element.attrib.items():
            result[f'@{attr_key}'] = attr_value

        grouped: dict[str, list[Any]] = {}
        for child in children:
            grouped.setdefault(child.tag, []).append(cls._element_to_obj(child))

        for tag, values in grouped.items():
            result[tag] = values[0] if len(values) == 1 else values

        text = (element.text or '').strip()
        if text:
            result['#text'] = text

        return result
