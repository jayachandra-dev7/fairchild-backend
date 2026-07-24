from typing import Any

import httpx


class ClaudeService:
    base_url = 'https://api.anthropic.com/v1/messages'

    @classmethod
    async def generate_with_fallback(
        cls,
        *,
        api_key: str,
        model_candidates: list[str],
        prompt: str,
        max_tokens: int,
        temperature: float,
        system: str | None = None,
        response_json: bool = False,
    ) -> dict[str, Any]:
        headers = {
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        }

        if response_json:
            # Prefill the assistant turn with an open brace so the model is forced to
            # continue a JSON object. The API returns only the continuation after '{',
            # so the leading brace is prepended back onto the extracted text below.
            messages = [
                {'role': 'user', 'content': prompt},
                {'role': 'assistant', 'content': '{'},
            ]
        else:
            messages = [{'role': 'user', 'content': prompt}]

        last_error = ''
        async with httpx.AsyncClient(timeout=60.0) as client:
            for model in model_candidates:
                payload = {
                    'model': model,
                    'max_tokens': max_tokens,
                    'temperature': temperature,
                    'messages': messages,
                }
                if system:
                    payload['system'] = system
                response = await client.post(cls.base_url, headers=headers, json=payload)

                if response.status_code < 400:
                    data = response.json()
                    text = ''.join(
                        part.get('text', '')
                        for part in data.get('content', [])
                        if part.get('type') == 'text'
                    ).strip()
                    if response_json:
                        text = '{' + text
                    return {
                        'model': model,
                        'text': text,
                        'raw': data,
                    }

                body = response.text
                last_error = body
                if response.status_code == 404 and 'model:' in body:
                    continue
                response.raise_for_status()

        raise RuntimeError(f'No compatible Claude model found for this API key. Last error: {last_error}')
