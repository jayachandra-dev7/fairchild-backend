import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db import automation_store
from app.main import app
from app.services.automation import claude_review
from app.services.review import content_policy


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(automation_store, 'DB_PATH', tmp_path / 'auto.db')
    content_policy._cache.clear()


def item(i, title, **kw):
    return {'id': i, 'title': title, 'brand': '', 'description': '', **kw}


def test_keyword_block_uses_shared_list_and_skips_claude(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError('Claude must not be called for a keyword hit')

    monkeypatch.setattr(claude_review, 'ask_claude', boom)
    content_policy.save_shared_keywords('deadman, undertaker')
    res = asyncio.run(content_policy.review_items([item('1', 'The Deadman Wrestling Tee')]))
    assert res[0]['verdict'] == 'reject' and res[0]['source'] == 'keyword' and 'deadman' in res[0]['reason']


def test_default_words_and_whole_word_matching(monkeypatch):
    async def fake(prompt, **k):
        return '{"results":[{"id":"2","verdict":"approve","reason":""}]}'

    monkeypatch.setattr(claude_review, 'ask_claude', fake)
    assert content_policy.keyword_hit('Legendary gunmetal watch', ['gun']) is None  # not a whole word
    assert content_policy.keyword_hit('Hand Gun case', ['gun']) == 'gun'
    res = asyncio.run(content_policy.review_items([item('2', 'Gunmetal Watch')]))
    assert res[0]['verdict'] == 'approve'


def test_claude_verdicts_cached_and_fail_closed(monkeypatch):
    calls = {'n': 0}

    async def fake(prompt, **k):
        calls['n'] += 1
        return '{"results":[{"id":"a","verdict":"reject","reason":"uses a wrestler persona"},{"id":"b","verdict":"approve","reason":""}]}'

    monkeypatch.setattr(claude_review, 'ask_claude', fake)
    items = [item('a', 'Phenom Legend Hoodie'), item('b', 'Plain Sneaker')]
    first = asyncio.run(content_policy.review_items(items))
    again = asyncio.run(content_policy.review_items(items))
    assert [r['verdict'] for r in first] == ['reject', 'approve'] == [r['verdict'] for r in again]
    assert calls['n'] == 1  # second pass served from the cache

    async def broken(prompt, **k):
        raise RuntimeError('api down')

    monkeypatch.setattr(claude_review, 'ask_claude', broken)
    res = asyncio.run(content_policy.review_items([item('c', 'Another Product')]))
    assert res[0]['verdict'] == 'reject' and res[0]['source'] == 'error'


def test_keywords_endpoint_shares_gpc_setting():
    client = TestClient(app)
    r = client.put('/api/v1/review/keywords', json={'keywords': 'Deadman\nundertaker, gun'})
    assert r.json()['data']['keywords'] == ['deadman', 'undertaker', 'gun']
    from app.services.automation import config_store

    assert config_store.load_all().product.ignore_keywords == 'deadman, undertaker, gun'
    assert client.get('/api/v1/review/keywords').json()['data']['keywords'] == ['deadman', 'undertaker', 'gun']
