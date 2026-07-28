import asyncio
import json

import httpx

from app.schemas.wordpress.post import WordPressPostCreateRequest
from app.services.wordpress.service import WordPressService


class _CaptureTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.url: str | None = None
        self.auth_header: str | None = None
        self.body: dict | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.url = str(request.url)
        self.auth_header = request.headers.get('authorization')
        self.body = json.loads(request.content)
        return httpx.Response(201, json={'id': 42, 'status': self.body.get('status')})


def _run(monkeypatch, request: WordPressPostCreateRequest):
    transport = _CaptureTransport()
    original = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs['transport'] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', _factory)
    result = asyncio.run(
        WordPressService.create_post(
            domain='localshop.com',
            wc_consumer_key='user',
            wc_consumer_secret='app pass',
            payload=request,
        )
    )
    return result, transport


def test_create_post_targets_core_posts_endpoint(monkeypatch) -> None:
    req = WordPressPostCreateRequest(title='Hello', content='<p>Body</p>', status='publish', categories=[1])
    result, sent = _run(monkeypatch, req)

    assert sent.url == 'https://localshop.com/wp-json/wp/v2/posts'
    assert sent.auth_header is not None and sent.auth_header.startswith('Basic ')
    assert result == {'id': 42, 'status': 'publish'}


def test_categories_sent_as_flat_id_array(monkeypatch) -> None:
    req = WordPressPostCreateRequest(title='T', content='<p>x</p>', categories=[1, 5])
    _, sent = _run(monkeypatch, req)

    # Core WP API expects [1, 5], not [{"id": 1}, {"id": 5}].
    assert sent.body['categories'] == [1, 5]


def test_optional_none_fields_are_omitted(monkeypatch) -> None:
    req = WordPressPostCreateRequest(title='T', content='<p>x</p>')
    _, sent = _run(monkeypatch, req)

    assert 'slug' not in sent.body
    assert 'featured_media' not in sent.body
    assert sent.body['status'] == 'draft'  # safe default


def test_featured_media_forwarded_when_set(monkeypatch) -> None:
    req = WordPressPostCreateRequest(title='T', content='<p>x</p>', featured_media=99, slug='my-post')
    _, sent = _run(monkeypatch, req)

    assert sent.body['featured_media'] == 99
    assert sent.body['slug'] == 'my-post'
