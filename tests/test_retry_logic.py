import asyncio

import httpx

from app.utils.retry import run_with_retry


def _build_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request('GET', 'https://example.com')
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError(message='error', request=request, response=response)


def test_retry_on_transient_502_then_success() -> None:
    state = {'attempts': 0}

    async def op():
        state['attempts'] += 1
        if state['attempts'] < 3:
            raise _build_status_error(502)
        return 'ok'

    result = asyncio.run(run_with_retry(operation=op, step='test', max_attempts=3))
    assert result == 'ok'
    assert state['attempts'] == 3


def test_no_retry_on_422() -> None:
    state = {'attempts': 0}

    async def op():
        state['attempts'] += 1
        raise _build_status_error(422)

    try:
        asyncio.run(run_with_retry(operation=op, step='test', max_attempts=3))
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 422
    else:
        raise AssertionError('Expected HTTPStatusError for 422')

    assert state['attempts'] == 1
