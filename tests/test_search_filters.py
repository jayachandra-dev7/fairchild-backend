import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.cj.ads_query import CJAdsProductsQueryRequest
from app.services.cj.ads_query_service import CJAdsQueryService
from app.services.impact.campaign_service import ImpactCampaignService
from app.services.platform_auth_service import platform_auth_service


client = TestClient(app)


def _cj_request(**overrides) -> CJAdsProductsQueryRequest:
    payload = {'company_id': '123', 'pid': '456'}
    payload.update(overrides)
    return CJAdsProductsQueryRequest(**payload)


# --- Impact: Query string construction -------------------------------------------------


def test_query_built_from_numeric_bounds() -> None:
    params = ImpactCampaignService._build_filter_params(min_discount=30, min_price=20, max_price=50)

    assert params['Query'] == 'DiscountPercentage>30 AND CurrentPrice>20 AND CurrentPrice<50'


def test_query_omits_absent_bounds_and_drops_trailing_zeros() -> None:
    assert ImpactCampaignService._build_filter_params(min_discount=40)['Query'] == 'DiscountPercentage>40'
    assert ImpactCampaignService._build_filter_params(max_price=19.99)['Query'] == 'CurrentPrice<19.99'
    assert 'Query' not in ImpactCampaignService._build_filter_params()


def test_string_fields_are_never_emitted_into_query() -> None:
    """Impact's parser accepts numeric conditions only; text matching stays on `keyword`."""
    params = ImpactCampaignService._build_filter_params(
        sort_by='Name',
        sort_order='ASC',
        min_discount=30,
    )

    assert params['Query'] == 'DiscountPercentage>30'
    for forbidden in ('Name', 'Category', 'Manufacturer', 'StockAvailability', '~'):
        assert forbidden not in params['Query']


def test_sort_params_are_normalized() -> None:
    params = ImpactCampaignService._build_filter_params(sort_by='discountpercentage', sort_order='desc')

    assert params['SortBy'] == 'DiscountPercentage'
    assert params['SortOrder'] == 'DESC'


def test_no_promotions_param_is_ever_emitted() -> None:
    """PromotionIds has no working server-side form: it 400s on ItemSearch and no-ops on Items."""
    params = ImpactCampaignService._build_filter_params(sort_by='Name', min_discount=10)

    assert 'PromotionIds' not in params
    assert 'PromotionIds' not in params.get('Query', '')


# --- Impact: sortBy allowlist ----------------------------------------------------------


@pytest.mark.parametrize('bad_sort', ['Name~boots', 'DROP TABLE', 'CurrentPrice>10', ''])
def test_sort_by_allowlist_rejects_unknown_fields(bad_sort: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        ImpactCampaignService._build_filter_params(sort_by=bad_sort)

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail['code'] == 'INVALID_SORT_FIELD'


def test_sort_order_allowlist_rejects_unknown_values() -> None:
    with pytest.raises(HTTPException) as excinfo:
        ImpactCampaignService._build_filter_params(sort_order='SIDEWAYS')

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail['code'] == 'INVALID_SORT_ORDER'


def test_sort_by_rejection_surfaces_as_422_from_endpoint() -> None:
    platform_auth_service.set_credentials('impact', {'account_sid': 'sid', 'auth_token': 'token'})

    response = client.get(
        '/api/v1/impact/catalogs/895/items',
        params={'sortBy': 'Name~boots', 'limit': 5},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload['success'] is False
    assert payload['error']['code'] == 'INVALID_SORT_FIELD'
    assert payload['error']['retryable'] is False


# --- CJ: selection set and arguments ---------------------------------------------------


def test_next_page_omitted_from_selection_set_when_sorting() -> None:
    query = CJAdsQueryService._build_products_query(_cj_request(sort_by='PRICE', sort_order='DESC'))

    assert 'nextPage' not in query
    assert 'sortBy: PRICE' in query
    assert 'sortOrder: DESC' in query


def test_next_page_present_in_selection_set_without_sorting() -> None:
    query = CJAdsQueryService._build_products_query(_cj_request())

    assert 'totalCount count limit nextPage' in query
    assert 'sortBy' not in query


def test_new_arguments_are_formatted_correctly() -> None:
    query = CJAdsQueryService._build_products_query(
        _cj_request(discount_percentage=30, low_price=20, high_price=99.95, brand='Nike')
    )

    assert 'discountPercentage: 30' in query
    assert 'lowPrice: 20' in query
    assert 'highPrice: 99.95' in query
    assert 'brand: "Nike"' in query


def test_new_arguments_omitted_when_unset() -> None:
    query = CJAdsQueryService._build_products_query(_cj_request())

    for argument in ('discountPercentage', 'lowPrice', 'highPrice', 'brand', 'sortOrder'):
        assert argument not in query


@pytest.mark.parametrize('field,value', [('sort_by', 'DISCOUNT'), ('sort_order', 'RANDOM')])
def test_cj_sort_validation_rejects_unknown_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _cj_request(**{field: value})


def test_cj_sort_values_are_normalized() -> None:
    request = _cj_request(sort_by='price', sort_order='asc')

    assert request.sort_by == 'PRICE'
    assert request.sort_order == 'ASC'


@pytest.mark.parametrize('field,value', [('discount_percentage', 101), ('low_price', -1), ('high_price', -5)])
def test_cj_numeric_bounds_enforced(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        _cj_request(**{field: value})


def test_invalid_cj_sort_by_returns_422_envelope() -> None:
    """A custom validator's ValueError lands in pydantic's `ctx`; the error envelope must
    still serialize, rather than falling through to the 500 handler."""
    platform_auth_service.set_token('cj', 'dummy-token')

    response = client.post(
        '/api/v1/cj/ads/products/query',
        json={'company_id': '1', 'pid': '2', 'sort_by': 'DISCOUNT'},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload['success'] is False
    assert payload['error']['code'] == 'VALIDATION_ERROR'
    assert any('sort_by' in str(item.get('loc', '')) for item in payload['error']['details'])
