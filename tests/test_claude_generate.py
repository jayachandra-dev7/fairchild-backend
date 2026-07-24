import asyncio
import json

import httpx

from app.schemas.claude.generate import ClaudeGenerateRequest
from app.services.claude.service import ClaudeService


class _CaptureTransport(httpx.AsyncBaseTransport):
    """Captures the outbound request body and returns a canned Anthropic response."""

    def __init__(self, text: str) -> None:
        self.captured: dict | None = None
        self._text = text

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.captured = json.loads(request.content)
        body = {'content': [{'type': 'text', 'text': self._text}]}
        return httpx.Response(200, json=body)


def _run(monkeypatch, *, response_text, **overrides):
    """Invoke generate_with_fallback with a capturing transport; return (result, sent_body)."""
    transport = _CaptureTransport(response_text)
    original = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs['transport'] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', _factory)

    payload = {'prompt': 'Give me JSON', 'modelCandidates': ['claude-sonnet-4-5']}
    payload.update(overrides)
    request = ClaudeGenerateRequest(**payload)
    result = asyncio.run(
        ClaudeService.generate_with_fallback(
            api_key='key',
            model_candidates=request.model_candidates,
            prompt=request.prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            system=request.system,
            response_json=request.response_json,
        )
    )
    return result, transport.captured


def test_response_json_prefills_assistant_and_prepends_brace(monkeypatch) -> None:
    result, sent = _run(monkeypatch, response_text='"name": "widget"}', responseJson=True)

    # Assistant turn is prefilled with an open brace.
    assert sent['messages'][-1] == {'role': 'assistant', 'content': '{'}
    # The returned text is a valid JSON object starting with '{'.
    assert result['text'].startswith('{')
    assert json.loads(result['text']) == {'name': 'widget'}


def test_default_mode_sends_single_user_turn_unchanged(monkeypatch) -> None:
    result, sent = _run(monkeypatch, response_text='plain prose reply')

    assert sent['messages'] == [{'role': 'user', 'content': 'Give me JSON'}]
    assert 'system' not in sent
    assert result['text'] == 'plain prose reply'


def test_system_prompt_forwarded_when_provided(monkeypatch) -> None:
    _, sent = _run(monkeypatch, response_text='ok', system='You are terse.')

    assert sent['system'] == 'You are terse.'


def test_system_omitted_when_absent(monkeypatch) -> None:
    _, sent = _run(monkeypatch, response_text='ok')

    assert 'system' not in sent


def test_response_json_alias_parses() -> None:
    request = ClaudeGenerateRequest(prompt='x', responseJson=True)
    assert request.response_json is True
