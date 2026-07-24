from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services.platform_auth_service import platform_auth_service
from app.utils.image_validation import ImageValidationError, validate_image_bytes


client = TestClient(app)


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new('RGB', (64, 64), color=color)
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def test_low_variance_image_is_tolerated_not_rejected() -> None:
    """Dark/low-variance but decodable images (e.g. white-background product shots) are valid.
    The brightness/variance check is a logged warning now, not a hard failure."""
    black_png = _png_bytes((0, 0, 0))
    white_png = _png_bytes((255, 255, 255))

    # Neither should raise: both decode cleanly even though variance is near zero.
    validate_image_bytes(image_bytes=black_png, content_type='image/png')
    validate_image_bytes(image_bytes=white_png, content_type='image/png')


def test_undecodable_bytes_still_rejected() -> None:
    with pytest.raises(ImageValidationError) as excinfo:
        validate_image_bytes(image_bytes=b'not-an-image', content_type='image/png')
    assert excinfo.value.code == 'IMAGE_INVALID_OR_BLANK'


def test_non_image_content_type_still_rejected() -> None:
    with pytest.raises(ImageValidationError):
        validate_image_bytes(image_bytes=_png_bytes((120, 120, 120)), content_type='text/html')


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
