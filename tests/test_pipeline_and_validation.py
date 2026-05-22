from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services.platform_auth_service import platform_auth_service


client = TestClient(app)


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new('RGB', (64, 64), color=color)
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def test_black_image_detection_rejects_request() -> None:
    platform_auth_service.set_token('renderform', 'dummy-token')
    black_png = _png_bytes((0, 0, 0))

    response = client.post(
        '/api/v1/renderform/render/upload',
        files={'image': ('black.png', black_png, 'image/png')},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload['success'] is False
    assert payload['error']['code'] == 'IMAGE_INVALID_OR_BLANK'
    assert payload['error']['retryable'] is False
    assert payload['error']['step'] == 'renderform_render'


def test_url_like_keyword_rejected_with_standard_error() -> None:
    platform_auth_service.set_credentials(
        'impact',
        {
            'account_sid': 'sid',
            'auth_token': 'token',
        },
    )

    response = client.get('/api/v1/impact/catalogs/item-search', params={'keyword': 'https://example.com'})

    assert response.status_code == 422
    payload = response.json()
    assert payload['success'] is False
    assert payload['error']['code'] == 'INVALID_KEYWORD_FORMAT'
    assert payload['error']['message'] == 'Enter related keyword (2-3 words). URLs are not supported for keyword search.'
    assert payload['error']['retryable'] is False
    assert payload['error']['step'] == 'impact_item_search'


def test_standardized_error_envelope_shape() -> None:
    response = client.post('/api/v1/wordpress/media/upload')
    assert response.status_code == 401

    payload = response.json()
    assert payload['success'] is False
    assert 'error' in payload
    assert isinstance(payload['error'], dict)
    assert 'code' in payload['error']
    assert 'message' in payload['error']
