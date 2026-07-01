import asyncio

from app.schemas.wordpress.product import WooProductCreateRequest
from app.services.wordpress.service import WordPressService


def test_wordpress_product_categories_are_forwarded(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, bool]:
            return {'ok': True}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, auth=None, headers=None, json=None):
            captured['url'] = url
            captured['auth'] = auth
            captured['headers'] = headers
            captured['json'] = json
            return FakeResponse()

    monkeypatch.setattr('app.services.wordpress.service.httpx.AsyncClient', FakeClient)

    request = WooProductCreateRequest(
        name='Test Affiliate Product',
        categories=[{'id': 123}],
        images=[{'id': 456}],
        meta_data=[{'key': 'vendor', 'value': 'Nike'}],
    )

    result = asyncio.run(
        WordPressService.create_product(
            domain='loclshop.com',
            wc_consumer_key='ck_xxx',
            wc_consumer_secret='cs_xxx',
            payload=request,
        )
    )

    assert result == {'ok': True}
    assert captured['url'] == 'https://loclshop.com/wp-json/wc/v3/products'
    assert captured['json']['categories'] == [{'id': 123}]
    assert captured['json']['images'] == [{'id': 456}]
    assert captured['json']['meta_data'] == [{'key': 'vendor', 'value': 'Nike'}]
