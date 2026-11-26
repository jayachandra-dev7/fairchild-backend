import asyncio
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from app.core.config import get_settings
from app.db import automation_store
from app.schemas.automation import Candidate, ProductAutomationSettings, ScheduleSettings, parse_keywords
from app.services.automation import categories, claude_review, filters, links, product_job, sourcing
from app.workers.scheduler import is_due


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    async def plain_keywords(leaf):
        return [leaf['name']]

    monkeypatch.setattr(claude_review, 'suggest_keywords', plain_keywords)

    async def no_dead(urls):  # never touch the network from tests
        return set()

    async def no_cats(creds=None):
        return []

    monkeypatch.setattr(links, 'dead_urls', no_dead)
    monkeypatch.setattr(links, 'store_categories', no_cats)
    monkeypatch.setattr(automation_store, 'DB_PATH', tmp_path / 'auto.db')


RAW = [
    {'id': 1, 'name': 'Animals &amp; Pet Supplies', 'slug': 'a', 'parent': 0},
    {'id': 2, 'name': 'Cat Supplies', 'slug': 'b', 'parent': 1},
    {'id': 3, 'name': 'Cat Food', 'slug': 'c', 'parent': 2},
    {'id': 4, 'name': 'Cat Toys', 'slug': 'd', 'parent': 2},
    {'id': 5, 'name': 'Uncategorized', 'slug': 'u', 'parent': 0},
]


def test_build_snapshot_tree_and_leaves():
    snap = categories.build_snapshot(RAW)
    by_id = {c['id']: c for c in snap['categories']}
    assert by_id[1]['name'] == 'Animals & Pet Supplies'  # entity decoded
    assert by_id[3]['path'] == 'Animals & Pet Supplies > Cat Supplies > Cat Food'
    assert by_id[3]['path_ids'] == [1, 2, 3] and by_id[3]['depth'] == 3
    assert {c['id'] for c in snap['categories'] if c['is_leaf']} == {3, 4, 5}
    assert snap['leaf_count'] == 3


def test_snapshot_file_is_loadable():
    snap = categories.load_snapshot(force=True)
    assert snap['total'] == len(snap['categories']) > 1000
    assert all(c['is_leaf'] == (c['id'] not in {x['parent'] for x in snap['categories']}) for c in snap['categories'][:200])


def cand(**kw):
    base = dict(source='impact', source_id='x1', title='Soft Cat Bed', buy_url='https://b', image_url='https://i', price=100.0, sale_price=70.0, discount=30.0)
    base.update(kw)
    return Candidate(**base)


def test_filters_price_discount_keywords_and_dedup():
    cfg = ProductAutomationSettings(min_discount=20, min_price=5, max_price=500, ignore_keywords='gun, vape')
    ok = cand(source_id='ok')
    pricey = cand(source_id='p', sale_price=900.0, price=1200.0, discount=25.0)
    shallow = cand(source_id='s', price=100.0, sale_price=95.0, discount=5.0)
    banned = cand(source_id='b', title='Toy gun for cats')
    oos = cand(source_id='o', in_stock=False)
    fake = cand(source_id='f', price=1000.0, sale_price=50.0, discount=95.0)
    automation_store.record_product(source='impact', source_id='seen', wp_product_id=1, media_id=1, category_id=3, name='n', permalink='', image_url='', price='1', sale_price='1')
    seen = cand(source_id='seen')
    kept, rejected = filters.filter_and_rank([ok, pricey, shallow, banned, oos, fake, seen, ok], cfg)
    assert [c.source_id for c in kept] == ['ok']
    assert rejected['already posted'] == 1


def test_hard_price_ceiling_even_if_setting_is_huge():
    cfg = ProductAutomationSettings.model_construct(**{**ProductAutomationSettings().model_dump(), 'max_price': 99999.0})
    assert filters.reject_reason(cand(sale_price=2500.0, price=3500.0, discount=29.0), cfg, []) is not None


def test_parse_keywords():
    assert parse_keywords('Gun, vape\nammo; GUN') == ['gun', 'vape', 'ammo']


def test_normalizers():
    leaf = {'name': 'Cat Food', 'path': 'Animals > Cat Food'}
    imp = sourcing.normalize_impact(
        {'Id': 'p1', 'Name': 'Cat Food 5lb', 'CurrentPrice': '17.99', 'OriginalPrice': '20.99', 'DiscountPercentage': '14', 'StockAvailability': 'InStock',
         'Category': 'Animals > Cat Food > Dry', 'Url': 'u', 'ImageUrl': 'i', 'CampaignName': 'Petco'}, leaf)
    assert imp and imp.discount == 14 and imp.in_stock and imp.relevance >= 2
    assert sourcing.normalize_impact({'Id': 'x', 'Name': 'n', 'CurrentPrice': '10', 'OriginalPrice': '10'}, leaf) is None
    cj = sourcing.normalize_cj(
        {'id': '9', 'advertiserId': '1', 'title': 'Cat Food', 'price': {'amount': '20'}, 'salePrice': {'amount': '15'},
         'linkCode': {'clickUrl': 'c'}, 'imageLink': 'i', 'availability': 'IN_STOCK'}, leaf)
    assert cj and cj.discount == 25.0 and cj.source_id == '1:9' and cj.buy_url == 'c'


def test_cj_keyword_cannot_break_graphql():
    assert '"' not in sourcing._clean_keyword('Men\'s "Classic" Watches')


def test_extract_json_variants():
    assert claude_review.extract_json('```json\n{"a": 1}\n```') == {'a': 1}
    assert claude_review.extract_json('Sure! {"a": {"b": "}"}} trailing') == {'a': {'b': '}'}}
    with pytest.raises(ValueError):
        claude_review.extract_json('no json here')


def test_is_due_logic():
    tz = ZoneInfo('America/Denver')
    cfg = ScheduleSettings(enabled=True, daily_time='09:00', timezone='America/Denver')
    assert not is_due(cfg, datetime(2026, 1, 1, 8, 59, tzinfo=tz), [])
    assert is_due(cfg, datetime(2026, 1, 1, 9, 0, tzinfo=tz), [])
    assert is_due(cfg, datetime(2026, 1, 1, 15, 0, tzinfo=tz), [])  # catch-up after a restart
    assert not is_due(cfg, datetime(2026, 1, 1, 15, 0, tzinfo=tz), [{'status': 'completed', 'started_at': '2026-01-01T16:00:00+00:00'}])
    assert not is_due(ScheduleSettings(enabled=False), datetime(2026, 1, 1, 15, 0, tzinfo=tz), [])


def test_failed_scheduled_run_is_retried_every_five_minutes_for_two_hours():
    tz = ZoneInfo('America/Denver')
    cfg = ScheduleSettings(enabled=True, daily_time='09:00', timezone='America/Denver')
    failed = lambda hh, mm: {'status': 'failed', 'started_at': datetime(2026, 1, 1, hh, mm, tzinfo=tz).astimezone(ZoneInfo('UTC')).isoformat()}
    assert not is_due(cfg, datetime(2026, 1, 1, 9, 3, tzinfo=tz), [failed(9, 0)])  # too soon
    assert is_due(cfg, datetime(2026, 1, 1, 9, 5, tzinfo=tz), [failed(9, 0)])
    assert is_due(cfg, datetime(2026, 1, 1, 10, 59, tzinfo=tz), [failed(9, 0), failed(10, 54)])
    assert not is_due(cfg, datetime(2026, 1, 1, 11, 10, tzinfo=tz), [failed(11, 0)])  # past the 2h window
    interrupted = {'status': 'interrupted', 'started_at': failed(9, 0)['started_at']}
    assert is_due(cfg, datetime(2026, 1, 1, 9, 6, tzinfo=tz), [interrupted])
    # any run that completed (or partly completed, or was stopped on purpose) ends the day's job
    for status in ('completed', 'partial', 'cancelled', 'running'):
        assert not is_due(cfg, datetime(2026, 1, 1, 9, 30, tzinfo=tz), [failed(9, 0), {'status': status, 'started_at': failed(9, 10)['started_at']}])


def test_cursor_rotation_wraps_and_advances(monkeypatch):
    monkeypatch.setattr(categories, 'leaf_categories', lambda exclude=None: [{'id': i, 'name': str(i), 'path': str(i)} for i in range(1, 6)])
    cfg = ProductAutomationSettings(categories_per_day=3)
    first, cur, total = product_job.pick_categories(cfg, max_categories=None, category_ids=None)
    assert [c['id'] for c in first] == [1, 2, 3] and cur == 0 and total == 5
    automation_store.set_state('product_cursor', '3')
    second, cur, _ = product_job.pick_categories(cfg, max_categories=None, category_ids=None)
    assert [c['id'] for c in second] == [4, 5, 1] and cur == 3


def test_product_job_creates_dedups_and_self_heals(monkeypatch):
    leaf = {'id': 3, 'name': 'Cat Food', 'path': 'Animals > Cat Food'}
    monkeypatch.setattr(product_job, 'pick_categories', lambda cfg, **kw: ([leaf], 0, 1))
    cur = {'c': cand(source_id='a1', title='Cat Food A')}

    async def fake_fetch(l, cfg, page=0, **kw):
        return ([cur['c']] if page == 0 else []), []

    async def fake_review(l, cands, want, branded):
        return [(cur['c'], {'title': 'Cat Food A', 'short_description': 's', 'description': '<p>d</p>'})], []

    created = []

    class FakeWP:
        calls = 0

        @staticmethod
        async def upload_media_bytes(**kw):
            return {'id': 77}

        @staticmethod
        async def create_product(**kw):
            FakeWP.calls += 1
            if FakeWP.calls == 1:  # first attempt: stale category id -> heal -> retry
                import httpx

                req = httpx.Request('POST', 'https://x')
                raise httpx.HTTPStatusError('bad', request=req, response=httpx.Response(400, request=req, text='{"code":"woocommerce_rest_term_invalid"}'))
            created.append(kw['payload'])
            return {'id': 500, 'permalink': 'https://shop/p/500'}

    async def fake_prepare(cand_, copy, cfg):
        return b'img', 'a.jpg', 'image/jpeg'

    async def fake_sync():
        return {'total': 1}

    monkeypatch.setattr(sourcing, 'fetch_candidates', fake_fetch)
    monkeypatch.setattr(claude_review, 'review_candidates', fake_review)
    monkeypatch.setattr(product_job, 'WordPressService', FakeWP)
    monkeypatch.setattr(product_job, '_prepare_image', fake_prepare)
    monkeypatch.setattr(categories, 'sync_snapshot', fake_sync)
    monkeypatch.setattr(categories, 'category_by_id', lambda i: leaf)
    from app.services.platform_auth_service import platform_auth_service

    monkeypatch.setattr(platform_auth_service, '_credentials', {'wordpress': {'domain': 'd', 'wc_consumer_key': 'k', 'wc_consumer_secret': 's'}})

    cfg = ProductAutomationSettings(products_per_category=1)
    run_id = automation_store.create_run(kind='product', trigger='manual', dry_run=False, local_date='2026-01-01')
    status, summary = asyncio.run(product_job.run_product_job(run_id, cfg, dry_run=False))
    assert status == 'completed' and summary['created'] == 1 and summary['errors'] == 0
    assert created[0].categories[0].id == 3 and created[0].sale_price == '70.00'
    assert automation_store.is_seen('impact', 'a1')
    assert automation_store.get_state('product_cursor') == '0'

    # dry run writes nothing (a fresh product, since a1 is now deduplicated)
    FakeWP.calls = 99
    cur['c'] = cand(source_id='a2', title='Cat Food B')
    run2 = automation_store.create_run(kind='product', trigger='manual', dry_run=True, local_date='2026-01-01')
    s2, sum2 = asyncio.run(product_job.run_product_job(run2, cfg, dry_run=True))
    assert s2 == 'completed' and sum2['would_create'] == 1 and sum2['created'] == 0
    assert not automation_store.is_seen('impact', 'a2') and len(created) == 1


def test_python_renderer_dimensions_and_fit():
    from app.services.image import python_renderer as pr

    buf = io.BytesIO()
    Image.new('RGB', (600, 400), (200, 30, 30)).save(buf, 'PNG')
    for tid in pr.load_templates():
        out = pr.render_template(tid, 'A VERY LONG PRODUCT TITLE THAT MUST SHRINK TO FIT THE BOX', buf.getvalue())
        assert Image.open(io.BytesIO(out)).size == (999, 1079)
    with pytest.raises(pr.RenderError):
        pr.render_template('nope', 'x', buf.getvalue())
    with pytest.raises(pr.RenderError):
        pr.render_template(next(iter(pr.load_templates())), 'x', b'not an image')


def test_renderer_dispatch_honours_flag(monkeypatch):
    from app.services.image import renderer

    seen = []

    async def py(**kw):
        seen.append('python')
        return b'p'

    async def rf(**kw):
        seen.append('renderform')
        return b'r'

    monkeypatch.setattr(renderer, 'render_with_python', py)
    monkeypatch.setattr(renderer, 'render_with_renderform', rf)
    monkeypatch.setattr(get_settings(), 'USE_RENDERFORM', False)
    assert asyncio.run(renderer.render_branded_image(template='t', title='x', image_url='u')) == b'p'
    monkeypatch.setattr(get_settings(), 'USE_RENDERFORM', True)
    assert asyncio.run(renderer.render_branded_image(template='t', title='x', image_url='u')) == b'r'
    assert seen == ['python', 'renderform']


def test_list_runs_carries_first_created_url_and_prunes_to_100():
    rid = automation_store.create_run(kind='product', trigger='manual', dry_run=False, local_date='2026-01-01')
    automation_store.add_run_item(rid, status='error', message='x')
    automation_store.add_run_item(rid, status='created', message='ok', ref_id=1, ref_url='https://shop.test/p/1')
    automation_store.add_run_item(rid, status='created', message='ok2', ref_id=2, ref_url='https://shop.test/p/2')
    automation_store.finish_run(rid, 'completed', {})
    assert automation_store.list_runs('product', 5)[0]['first_url'] == 'https://shop.test/p/1'
    for _ in range(105):
        r = automation_store.create_run(kind='blog', trigger='manual', dry_run=True, local_date='2026-01-01')
        automation_store.finish_run(r, 'completed', {})
    assert len(automation_store.list_runs('blog', 100)) == 100
    assert len(automation_store.list_runs('product', 100)) == 1


def test_cancel_run_stops_task_and_marks_cancelled(monkeypatch):
    from app.workers import runner

    async def slow(run_id, cfg, **kw):
        await asyncio.sleep(30)

    monkeypatch.setattr(product_job, 'run_product_job', slow)

    async def scenario():
        run_id = runner.start_run('product', trigger='manual')
        await asyncio.sleep(0.05)
        assert runner.cancel_run(run_id) is True
        await asyncio.sleep(0.1)
        return run_id

    run_id = asyncio.run(scenario())
    run = automation_store.get_run(run_id)
    assert run['status'] == 'cancelled'
    assert runner.cancel_run(run_id) is False


def test_claude_service_retries_without_temperature(monkeypatch):
    import httpx
    from app.services.claude.service import ClaudeService

    calls = []

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            calls.append(dict(json))
            if 'temperature' in json:
                return httpx.Response(400, text='`temperature` is deprecated for this model.', request=httpx.Request('POST', url))
            return httpx.Response(200, json={'content': [{'type': 'text', 'text': 'ok'}]}, request=httpx.Request('POST', url))

    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    out = asyncio.run(ClaudeService.generate_with_fallback(api_key='k', model_candidates=['m'], prompt='p', max_tokens=5, temperature=0.3))
    assert out['text'] == 'ok' and 'temperature' not in calls[-1] and len(calls) == 2


def test_category_keeps_reviewing_until_products_per_category_is_met(monkeypatch):
    leaf = {'id': 3, 'name': 'Cat Food', 'path': 'Animals > Cat Food'}
    monkeypatch.setattr(product_job, 'pick_categories', lambda cfg, **kw: ([leaf], 0, 1))
    pool = [cand(source_id=f'c{i}', title=f'Cat Food {i}') for i in range(6)]
    calls = []

    async def fake_fetch(l, cfg, page=0, **kw):
        return (pool if page == 0 else []), []

    async def stingy_review(l, cands, want, branded):
        calls.append(len(cands))
        c = cands[0]  # Claude only likes one of the batch each time
        return [(c, {'title': c.title, 'short_description': 's', 'description': '<p>d</p>'})], [{'id': x.source_id, 'reason': 'meh'} for x in cands[1:]]

    monkeypatch.setattr(sourcing, 'fetch_candidates', fake_fetch)
    monkeypatch.setattr(claude_review, 'review_candidates', stingy_review)
    cfg = ProductAutomationSettings(products_per_category=2)
    run_id = automation_store.create_run(kind='product', trigger='manual', dry_run=True, local_date='2026-01-01')
    status, summary = asyncio.run(product_job.run_product_job(run_id, cfg, dry_run=True))
    assert summary['would_create'] == 2 and len(calls) == 2 and status == 'completed'


def test_job_moves_on_to_more_categories_until_the_target_is_met(monkeypatch):
    A = {'id': 1, 'name': 'A', 'path': 'A'}
    B = {'id': 2, 'name': 'B', 'path': 'B'}
    C = {'id': 3, 'name': 'C', 'path': 'C'}
    D = {'id': 4, 'name': 'D', 'path': 'D'}
    monkeypatch.setattr(product_job, 'pick_categories', lambda cfg, **kw: ([A, B], 0, 4))
    monkeypatch.setattr(product_job, 'extension_leaves', lambda cfg, cursor, planned: [C, D])
    stock = {1: 1, 2: 0, 3: 1, 4: 5}  # usable products per category: A has 1, B none, C 1, D plenty
    pages = []

    async def fake_fetch(leaf, cfg, page=0, **kw):
        pages.append((leaf['id'], page))
        if page > 1:
            return [], []
        n = stock[leaf['id']] if page == 0 else 1  # page 2 of a category yields one extra
        return [cand(source_id=f"{leaf['id']}-{page}-{i}", title=f"{leaf['name']} item {page}{i}") for i in range(n)], []

    async def approve_all(leaf, cands, want, branded):
        return [(c, {'title': c.title, 'short_description': 's', 'description': '<p>d</p>'}) for c in cands], []

    monkeypatch.setattr(sourcing, 'fetch_candidates', fake_fetch)
    monkeypatch.setattr(claude_review, 'review_candidates', approve_all)
    cfg = ProductAutomationSettings(products_per_category=2)  # 2 planned categories x 2 = target 4
    run_id = automation_store.create_run(kind='product', trigger='manual', dry_run=True, local_date='2026-01-01')
    status, summary = asyncio.run(product_job.run_product_job(run_id, cfg, dry_run=True))
    assert summary['target'] == 4 and summary['would_create'] == 4 and summary['shortfall'] == 0
    assert any(p == (1, 1) for p in pages)  # went to page 2 of the same category before giving up on it


def test_category_searches_as_many_pages_as_exist(monkeypatch):
    leaf = {'id': 9, 'name': 'Deep', 'path': 'Deep'}
    monkeypatch.setattr(product_job, 'pick_categories', lambda cfg, **kw: ([leaf], 0, 1))
    pages = []

    async def fake_fetch(l, cfg, page=0, **kw):
        pages.append(page)
        if page >= 10:  # results end after 10 pages
            return [], []
        return [cand(source_id=f'p{page}', title=f'Deep {page}')], []

    async def reject_all_but_last(l, cands, want, branded):
        ok = [(c, {'title': c.title, 'short_description': 's', 'description': '<p>d</p>'}) for c in cands if c.source_id == 'p9']
        return ok, [{'id': c.source_id, 'reason': 'no'} for c in cands if c.source_id != 'p9']

    monkeypatch.setattr(sourcing, 'fetch_candidates', fake_fetch)
    monkeypatch.setattr(claude_review, 'review_candidates', reject_all_but_last)
    cfg = ProductAutomationSettings(products_per_category=1)
    run_id = automation_store.create_run(kind='product', trigger='manual', dry_run=True, local_date='2026-01-01')
    _, summary = asyncio.run(product_job.run_product_job(run_id, cfg, dry_run=True))
    assert summary['would_create'] == 1 and max(pages) >= 9


def test_category_stops_when_source_ignores_page_offset(monkeypatch):
    leaf = {'id': 9, 'name': 'Loop', 'path': 'Loop'}
    monkeypatch.setattr(product_job, 'pick_categories', lambda cfg, **kw: ([leaf], 0, 1))
    monkeypatch.setattr(product_job, 'extension_leaves', lambda cfg, cursor, planned: [])
    calls = []

    async def same_every_page(l, cfg, page=0, **kw):
        calls.append(page)
        return [cand(source_id='only', title='Loop item')], []

    async def reject(l, cands, want, branded):
        return [], [{'id': c.source_id, 'reason': 'no'} for c in cands]

    monkeypatch.setattr(sourcing, 'fetch_candidates', same_every_page)
    monkeypatch.setattr(claude_review, 'review_candidates', reject)
    run_id = automation_store.create_run(kind='product', trigger='manual', dry_run=True, local_date='2026-01-01')
    asyncio.run(product_job.run_product_job(run_id, ProductAutomationSettings(products_per_category=1), dry_run=True))
    assert len(calls) <= 3


def test_rotation_continues_after_last_category_and_wraps(monkeypatch):
    leaves = [{'id': i, 'name': f'L{i}', 'path': f'L{i}'} for i in (10, 20, 30, 40, 50)]
    monkeypatch.setattr(categories, 'leaf_categories', lambda excluded=None: list(leaves))
    cfg = ProductAutomationSettings(categories_per_day=2)
    first, cursor, total = product_job.pick_categories(cfg, max_categories=None, category_ids=None)
    assert [c['id'] for c in first] == [10, 20] and cursor == 0 and total == 5
    automation_store.set_state('product_last_category_id', '20')
    second, _, _ = product_job.pick_categories(cfg, max_categories=None, category_ids=None)
    assert [c['id'] for c in second] == [30, 40]
    automation_store.set_state('product_last_category_id', '50')  # end of list -> wraps to the start
    third, _, _ = product_job.pick_categories(cfg, max_categories=None, category_ids=None)
    assert [c['id'] for c in third] == [10, 20]
    # a re-sync that inserts/removes categories keeps going after the same id
    leaves[:] = [c for c in leaves if c['id'] != 30] + [{'id': 25, 'name': 'new', 'path': 'new'}]
    leaves.sort(key=lambda c: c['id'])
    automation_store.set_state('product_last_category_id', '20')
    after_resync, _, _ = product_job.pick_categories(cfg, max_categories=None, category_ids=None)
    assert [c['id'] for c in after_resync] == [25, 40]


def test_scheduler_tick_really_starts_a_scheduled_run(monkeypatch):
    from app.schemas.automation import AutomationSettings
    from app.services.automation import config_store
    from app.workers import runner, scheduler

    settings = AutomationSettings()
    settings.product.enabled = True
    settings.product.daily_time = '00:00'
    settings.blog.enabled = False
    monkeypatch.setattr(config_store, 'load_all', lambda: settings)
    ran = []

    async def quick(run_id, cfg, **kw):
        ran.append(run_id)
        return 'completed', {'created': 0}

    monkeypatch.setattr(product_job, 'run_product_job', quick)

    async def scenario():
        await scheduler.tick()  # APScheduler awaits coroutine jobs on the loop
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
    assert len(ran) == 1
    run = automation_store.get_run(ran[0])
    assert run['trigger'] == 'schedule' and run['status'] == 'completed'
    assert 'product' not in runner._running


def test_tick_is_a_coroutine_function():
    import inspect
    from app.workers import scheduler

    assert inspect.iscoroutinefunction(scheduler.tick)


def test_start_run_off_the_event_loop_leaves_no_stuck_state():
    from app.workers import runner

    with pytest.raises(RuntimeError):
        runner.start_run('product', trigger='schedule')
    assert 'product' not in runner._running and not automation_store.is_kind_running('product')


def test_earlier_cancelled_or_old_runs_never_block_the_schedule():
    from datetime import timezone
    from app.workers import scheduler

    tz = ZoneInfo('Asia/Kolkata')
    cfg = ScheduleSettings(enabled=True, daily_time='13:07', timezone='Asia/Kolkata')
    now = datetime(2026, 11, 18, 13, 7, 30, tzinfo=tz)

    def run_at(h, m, status, trigger='schedule'):
        rid = automation_store.create_run(kind='product', trigger=trigger, dry_run=False, local_date='2026-11-18')
        started = datetime(2026, 11, 18, h, m, tzinfo=tz).astimezone(timezone.utc).isoformat(timespec='seconds')
        with automation_store._conn() as c:
            c.execute('UPDATE runs SET started_at = ?, status = ? WHERE id = ?', (started, status, rid))

    run_at(12, 52, 'cancelled')                # earlier today, before the 13:07 slot
    run_at(13, 0, 'completed', trigger='manual')
    assert scheduler.is_due(cfg, now, scheduler.slot_attempts('product', cfg, now))  # still runs at 13:07
    run_at(13, 7, 'running')                    # once started for this slot, it does not start twice
    assert not scheduler.is_due(cfg, now, scheduler.slot_attempts('product', cfg, now))


def test_each_keyword_gets_up_to_100_reviews_and_every_keyword_is_tried(monkeypatch):
    leaf = {'id': 1, 'name': 'Junk', 'path': 'Junk'}
    monkeypatch.setattr(product_job, 'pick_categories', lambda cfg, **kw: ([leaf], 0, 1))
    monkeypatch.setattr(product_job, 'extension_leaves', lambda cfg, cursor, planned: [])

    async def three_keywords(l):
        return ['k1', 'k2', 'k3']

    monkeypatch.setattr(claude_review, 'suggest_keywords', three_keywords)
    reviewed = {'k1': 0, 'k2': 0, 'k3': 0}
    searched = []

    async def fake_fetch(l, cfg, page=0, keyword=None, **kw):
        if keyword not in searched:
            searched.append(keyword)
        if page >= 40:
            return [], []
        return [cand(source_id=f'{keyword}-{page}-{i}', title=f'{keyword} {page}{i}') for i in range(10)], []

    async def review(l, cands, want, branded):
        kw = cands[0].source_id.split('-')[0]
        reviewed[kw] += len(cands)
        if kw == 'k3':  # only the third keyword finds something Claude likes
            return [(c, {'title': c.title, 'short_description': 's', 'description': '<p>d</p>'}) for c in cands[:want]], []
        return [], [{'id': c.source_id, 'reason': 'unrelated'} for c in cands]

    monkeypatch.setattr(sourcing, 'fetch_candidates', fake_fetch)
    monkeypatch.setattr(claude_review, 'review_candidates', review)
    cfg = ProductAutomationSettings(products_per_category=1)
    run_id = automation_store.create_run(kind='product', trigger='manual', dry_run=True, local_date='2026-01-01')
    _, summary = asyncio.run(product_job.run_product_job(run_id, cfg, dry_run=True))
    assert reviewed['k1'] == 100 and reviewed['k2'] == 100  # hopeless keywords are capped at 100 products each
    assert searched == ['k1', 'k2', 'k3'] and summary['would_create'] == 1  # ...and the next keyword still finds one


def test_cj_paging_uses_next_page_cursor_not_offset(monkeypatch):
    leaf = {'id': 5, 'name': 'Tech', 'path': 'Tech'}
    cfg = ProductAutomationSettings(use_impact=False)
    seen_cursors = []

    async def fake_fetch_cj(l, c, kw, cursor):
        seen_cursors.append(cursor)
        nxt = {None: 'tok1', 'tok1': 'tok2', 'tok2': None}[cursor]
        return [cand(source_id=f'x{len(seen_cursors)}')], nxt

    monkeypatch.setattr(sourcing, '_fetch_cj', fake_fetch_cj)
    state = {}
    for _ in range(4):
        asyncio.run(sourcing.fetch_candidates(leaf, cfg, state=state, keyword='tech'))
    assert seen_cursors == [None, 'tok1', 'tok2']  # fourth call: CJ exhausted, not queried again


def test_only_one_run_of_a_kind_can_start_even_across_workers():
    first = automation_store.try_create_run(kind='product', trigger='schedule', dry_run=False, local_date='2026-01-01')
    assert first is not None
    assert automation_store.try_create_run(kind='product', trigger='schedule', dry_run=False, local_date='2026-01-01') is None
    assert automation_store.try_create_run(kind='blog', trigger='schedule', dry_run=False, local_date='2026-01-01') is not None
    automation_store.finish_run(first, 'completed', {})
    assert automation_store.try_create_run(kind='product', trigger='schedule', dry_run=False, local_date='2026-01-01') is not None


def test_stale_detection_ignores_live_runs_and_closes_dead_ones():
    live = automation_store.try_create_run(kind='product', trigger='schedule', dry_run=False, local_date='2026-01-01')
    assert automation_store.mark_stale_runs_interrupted(300) == 0  # fresh heartbeat: another worker's live run is left alone
    with automation_store._conn() as c:
        c.execute("UPDATE runs SET updated_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (live,))
    assert automation_store.mark_stale_runs_interrupted(300) == 1
    assert automation_store.get_run(live)['status'] == 'interrupted'
    # a log line / progress save is a heartbeat
    live2 = automation_store.try_create_run(kind='product', trigger='schedule', dry_run=False, local_date='2026-01-01')
    with automation_store._conn() as c:
        c.execute("UPDATE runs SET updated_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (live2,))
    automation_store.add_run_item(live2, status='info', message='still working')
    assert automation_store.mark_stale_runs_interrupted(300) == 0


def test_category_resync_is_written_to_the_persistent_folder_not_the_repo(monkeypatch, tmp_path):
    from app.services.platform_auth_service import platform_auth_service

    async def fake_raw(**kw):
        return RAW

    monkeypatch.setattr(categories, '_fetch_all_raw', fake_raw)
    monkeypatch.setattr(platform_auth_service, '_credentials', {'wordpress': {'domain': 'd', 'wc_consumer_key': 'k', 'wc_consumer_secret': 's'}})
    bundled_before = categories.SNAPSHOT_PATH.read_bytes()
    asyncio.run(categories.sync_snapshot())
    assert (tmp_path / 'wp_categories.json').exists()
    assert categories.SNAPSHOT_PATH.read_bytes() == bundled_before  # repo file untouched
    assert categories.load_snapshot(force=True)['total'] == len(RAW)  # and the runtime copy is what gets loaded
    categories._cache = None


async def _fake_blog_category(kw):
    return 11


def test_custom_blog_generate_and_publish(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.automation import blog_job
    from app.services.wordpress.service import WordPressService

    async def fake_ask(prompt, **kw):
        assert 'gift ideas for runners' in prompt
        return '{"title":"Gifts for Runners","excerpt":"Short.","content":"<p>Hi</p>"}'

    seen = {}

    async def fake_create(**kw):
        seen['payload'] = kw['payload']
        return {'id': 7, 'link': 'https://x/p', 'status': kw['payload'].status}

    monkeypatch.setattr(blog_job.claude_review, 'ask_claude', fake_ask)
    monkeypatch.setattr(WordPressService, 'create_post', staticmethod(fake_create))
    monkeypatch.setattr(blog_job.platform_auth_service, 'get_credentials', lambda _n: {'domain': 'd', 'wc_consumer_key': 'k', 'wc_consumer_secret': 's'})
    client = TestClient(app)
    blog_job._blog_category_id.clear()
    monkeypatch.setattr(blog_job, 'blog_category_id', _fake_blog_category)
    r = client.post('/api/v1/automation/blog/custom/generate', json={'prompt': 'gift ideas for runners'})
    assert r.status_code == 200 and r.json()['data']['title'] == 'Gifts for Runners'
    r = client.post('/api/v1/automation/blog/custom/publish', json={'title': 'T', 'content': '<p>x</p>', 'status': 'draft'})
    assert r.status_code == 200 and r.json()['data']['link'] == 'https://x/p' and seen['payload'].status == 'draft' and seen['payload'].categories == [11]
    assert client.post('/api/v1/automation/blog/custom/publish', json={'title': 'T', 'content': 'x', 'status': 'bogus'}).status_code == 422


# ---------------------------------------------------------------- duplicate check

def _impact(sid, title='Tactacam Reveal No Trespassing Sign 3-Pack', image='https://cdn.x/img/tactacam-sign-3pack.jpg'):
    return Candidate(source='impact', source_id=sid, title=title, buy_url='https://b/x', image_url=image, price=50, sale_price=30, discount=40)


def test_core_id_matches_across_advertiser_ids():
    from app.services.automation import dedupe

    ids = ['product_4595_101529191-3943173', 'product_895_3943173', 'product_10636_3943173', 'product_10637_101529191-3943173']
    assert {dedupe.core_id('impact', i) for i in ids} == {'impact:3943173'}
    assert dedupe.core_id('cj', '6511865:51153491702') == 'cj:51153491702'
    assert dedupe.image_key('https://x/1.jpg') is None  # too generic to trust


def test_filter_rejects_same_product_under_new_id():
    from app.services.automation import dedupe

    first = _impact('product_895_3943173')
    fp = dedupe.fingerprint(first)
    automation_store.record_product(
        source='impact', source_id=first.source_id, wp_product_id=1, media_id=None, category_id=3, name='x', permalink=None,
        image_url=None, price='50', sale_price='30', core_id=fp.core_id, title_key=fp.title_key, image_key=fp.image_key,
    )
    cfg = ProductAutomationSettings()
    same_core = _impact('product_4595_101529191-3943173', title='Totally different words here', image='https://o/other-picture-name.jpg')
    same_title = _impact('product_1_1111111', image='https://o/another-picture-name.jpg')
    fresh = _impact('product_2_2222222', title='Garmin Forerunner Watch', image='https://o/garmin-forerunner.jpg')
    kept, rejected = filters.filter_and_rank([same_core, same_title, fresh], cfg)
    assert [c.source_id for c in kept] == ['product_2_2222222']
    assert rejected['already posted (same product)'] == 2


def test_filter_drops_duplicates_inside_one_batch():
    cfg = ProductAutomationSettings()
    a, b = _impact('product_895_3943173'), _impact('product_4595_101529191-3943173')
    kept, _ = filters.filter_and_rank([a, b], cfg)
    assert len(kept) == 1


def test_history_is_capped_and_old_details_dropped(monkeypatch):
    monkeypatch.setattr(automation_store, 'KEEP_SEEN_PRODUCTS', 5)
    monkeypatch.setattr(automation_store, 'KEEP_LOG_LINES', 3)
    run_id = automation_store.try_create_run(kind='product', trigger='manual', dry_run=True, local_date='2026-01-01')
    for i in range(10):
        automation_store.add_run_item(run_id, status='info', message=f'line {i}')
        automation_store.record_product(
            source='cj', source_id=f'1:{i}0000', wp_product_id=i, media_id=1, category_id=3, name=f'n{i}', permalink='p',
            image_url=None, price='1', sale_price='1', core_id=f'cj:{i}0000', created_at=f'2026-01-{i + 1:02d}T00:00:00+00:00',
        )
    automation_store.finish_run(run_id, 'completed', {})
    with automation_store._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM run_items').fetchone()[0] == 3
        assert conn.execute('SELECT COUNT(*) FROM seen_products').fetchone()[0] == 5
        assert conn.execute('SELECT MIN(created_at) FROM seen_products').fetchone()[0].startswith('2026-01-06')
        assert conn.execute('SELECT COUNT(*) FROM seen_products WHERE name IS NOT NULL').fetchone()[0] == 0  # older than 30 days: keys only


def test_seed_from_wordpress_learns_ids_and_titles(monkeypatch):
    import httpx

    from app.services.automation import existing_products
    from app.services.platform_auth_service import platform_auth_service

    monkeypatch.setattr(platform_auth_service, 'get_credentials', lambda _n: {'domain': 'shop.test', 'wc_consumer_key': 'k', 'wc_consumer_secret': 's'})
    page = [{'id': 9, 'name': 'Nite Ize Wraptor Mount', 'date_created_gmt': '2026-11-20T10:00:00',
             'meta_data': [{'key': 'automation_source', 'value': 'impact'}, {'key': 'automation_source_id', 'value': 'product_895_2512178'}]}]

    def handler(request):
        return httpx.Response(200, json=page if request.url.params['page'] == '1' else [])

    real = httpx.AsyncClient
    monkeypatch.setattr(existing_products.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    class R:
        def log(self, *a, **k): pass

    assert asyncio.run(existing_products.seed(R())) == 1
    assert asyncio.run(existing_products.seed(R())) == 0  # already known: nothing re-added
    assert automation_store.seen_fingerprint_keys(['core:impact:2512178']) == {'core:impact:2512178'}


def test_size_variants_with_new_title_are_the_same_product():
    from app.services.automation import dedupe

    desc = 'Breathable mesh running shoe with cushioned midsole and durable rubber outsole for daily training.'
    a = Candidate(source='cj', source_id='1:10000001', title='Acme Runner - Size 8', description=desc, buy_url='b', image_url='https://a/x1.jpg', price=90, sale_price=60, discount=33)
    b = Candidate(source='cj', source_id='1:10000002', title='Acme Runner Trail Edition 9', description=desc, buy_url='b', image_url='https://other/y2.jpg', price=90, sale_price=60, discount=33)
    fp = dedupe.fingerprint(a)
    automation_store.record_product(source='cj', source_id=a.source_id, wp_product_id=1, media_id=None, category_id=3, name='x', permalink=None,
                                    image_url=None, price='90', sale_price='60', core_id=fp.core_id, title_key=fp.title_key, image_key=fp.image_key, desc_key=fp.desc_key)
    kept, rejected = filters.filter_and_rank([b], ProductAutomationSettings())
    assert kept == [] and rejected['already posted (same product)'] == 1
    assert len(filters.filter_and_rank([a.model_copy(update={'source_id': '1:10000003'}), b], ProductAutomationSettings())[0]) <= 1


def test_blogs_per_day_has_no_upper_cap():
    from app.schemas.automation import BlogAutomationSettings

    assert BlogAutomationSettings(blogs_per_day=500).blogs_per_day == 500
    with pytest.raises(ValueError):
        BlogAutomationSettings(blogs_per_day=0)


def test_blog_category_is_found_then_created_and_cached():
    import httpx

    from app.services.automation import blog_job

    blog_job._blog_category_id.clear()
    posted = []

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json=[])  # no "Blog" category yet
        posted.append(request.url.path)
        return httpx.Response(201, json={'id': 77})

    real = httpx.AsyncClient
    blog_job.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        kw = {'domain': 'shop.test', 'wc_consumer_key': 'k', 'wc_consumer_secret': 's'}
        assert asyncio.run(blog_job.blog_category_id(kw)) == 77
        assert asyncio.run(blog_job.blog_category_id(kw)) == 77  # cached: no second create
        assert len(posted) == 1
    finally:
        blog_job.httpx.AsyncClient = real
        blog_job._blog_category_id.clear()


# ---------------------------------------------------------------- content calendar
from datetime import date as _date, datetime as _dt, timezone as _tz  # noqa: E402

from app.services.automation import calendar_job  # noqa: E402


def test_calendar_add_skips_same_title_and_date():
    rows = [{'title': 'A post', 'keyword': 'k', 'publish_date': '2026-11-26'}]
    assert automation_store.calendar_add_many('b1', rows) == (1, 0)
    assert automation_store.calendar_add_many('b2', rows) == (0, 1)  # re-upload of the same plan adds nothing
    assert automation_store.calendar_add_many('b2', [{'title': 'A post', 'keyword': '', 'publish_date': '2026-11-27'}]) == (1, 0)
    assert len(automation_store.calendar_list()) == 2


def test_spread_dates_weekdays_five_per_week():
    rows = [{'title': f't{i}', 'publish_date': ''} for i in range(7)]
    rows[0]['publish_date'] = '2026-12-31'  # Thursday
    calendar_job.spread_dates(rows, start=None, per_week=5)
    days = [_date.fromisoformat(r['publish_date']) for r in rows]
    assert days[1] == _date(2027, 1, 1)  # starts the day after the last dated row
    assert all(d.weekday() < 5 for d in days)
    assert len({d.isocalendar()[:2] for d in days[1:]}) >= 2


def test_publish_moment_timezone_dst_and_past():
    now = _dt(2026, 11, 25, 12, 0, tzinfo=_tz.utc)
    assert calendar_job.publish_moment('2026-11-26', '09:00', 'America/New_York', now=now) == ('future', '2026-11-26T14:00:00')  # EST = UTC-5
    assert calendar_job.publish_moment('2026-07-01', '09:00', 'America/New_York', now=_dt(2026, 6, 1, tzinfo=_tz.utc)) == ('future', '2026-07-01T13:00:00')  # EDT = UTC-4
    assert calendar_job.publish_moment('2026-11-25', '09:00', 'America/New_York', now=_dt(2026, 11, 25, 15, 0, tzinfo=_tz.utc)) == ('publish', None)  # already passed
    with pytest.raises(ValueError):
        calendar_job.publish_moment('2026-11-26', '09:00', 'Not/AZone', now=now)


def test_needs_fact_check_flags_local_topics():
    assert calendar_job.needs_fact_check({'title': 'Best Christmas Activities for Families in Orlando', 'keyword': ''})
    assert not calendar_job.needs_fact_check({'title': 'Best Home Organization Products Under $50', 'keyword': 'home organization'})


def test_parse_plan_fills_missing_dates_and_dedupes(monkeypatch):
    async def fake(prompt, **kw):
        return ('{"guidelines":"800 words","articles":['
                '{"title":"One","keyword":"k1","publish_date":"2026-11-26"},'
                '{"title":"one","keyword":"dup","publish_date":null},'
                '{"title":"Two","keyword":"k2","publish_date":null}]}')

    monkeypatch.setattr(claude_review, 'ask_claude', fake)
    out = asyncio.run(calendar_job.parse_plan(filename='plan.txt', data=b'x', text=None, start=None, per_week=5))
    assert [r['title'] for r in out['articles']] == ['One', 'Two']
    assert out['articles'][1]['publish_date'] == '2026-11-27' and out['guidelines'] == '800 words'


def test_generate_one_sets_error_and_keeps_row(monkeypatch):
    automation_store.calendar_add_many('b', [{'title': 'T', 'keyword': 'k', 'publish_date': '2026-12-01'}])
    pid = automation_store.calendar_list()[0]['id']

    async def no_products(*a, **k):
        return []

    async def boom(*a, **k):
        raise RuntimeError('claude down')

    monkeypatch.setattr(calendar_job, 'find_live_products', no_products)
    monkeypatch.setattr(claude_review, 'ask_claude', boom)
    row = asyncio.run(calendar_job.generate_one(pid))
    assert row['state'] == 'error' and 'claude down' in row['error']

    async def ok(*a, **k):
        return '{"excerpt":"ex","content":"<p>Hello</p>"}'

    monkeypatch.setattr(claude_review, 'ask_claude', ok)
    row = asyncio.run(calendar_job.generate_one(pid))
    assert row['state'] == 'generated' and 'affiliate' in row['content'].lower()  # disclosure appended


def test_schedule_one_creates_then_updates_same_post(monkeypatch):
    automation_store.calendar_add_many('b', [{'title': 'T', 'keyword': 'k', 'publish_date': '2099-12-01'}])
    pid = automation_store.calendar_list()[0]['id']
    automation_store.calendar_update(pid, state='approved', content='<p>x</p>', excerpt='e')
    calls = []

    async def blog_cat(kw):
        return 7

    async def create(**kw):
        calls.append(('create', kw['payload']))
        return {'id': 55, 'link': 'https://x/p'}

    async def update(**kw):
        calls.append(('update', kw['post_id']))
        return {'id': kw['post_id'], 'link': 'https://x/p'}

    monkeypatch.setattr(calendar_job, '_wp_kw', lambda: {'domain': 'd', 'wc_consumer_key': 'k', 'wc_consumer_secret': 's'})
    monkeypatch.setattr(calendar_job.blog_job, 'blog_category_id', blog_cat)
    monkeypatch.setattr(calendar_job.WordPressService, 'create_post', staticmethod(create))
    monkeypatch.setattr(calendar_job.WordPressService, 'update_post', staticmethod(update))
    asyncio.run(calendar_job.schedule_one(pid, '09:00', 'America/New_York'))
    assert calls[0][0] == 'create' and calls[0][1].status == 'future' and calls[0][1].categories == [7]
    assert calls[0][1].date_gmt == '2099-12-01T14:00:00'
    asyncio.run(calendar_job.schedule_one(pid, '10:00', 'America/New_York'))  # re-schedule: no duplicate post
    assert calls[1] == ('update', 55)
    assert automation_store.calendar_get(pid)['state'] == 'scheduled'


def test_publish_now_and_per_article_time(monkeypatch):
    automation_store.calendar_add_many('b', [{'title': 'T', 'keyword': 'k', 'publish_date': '2099-12-01'}])
    pid = automation_store.calendar_list()[0]['id']
    automation_store.calendar_update(pid, state='generated', content='<p>x</p>', excerpt='e', publish_time='16:30')
    seen = []

    async def blog_cat(kw):
        return 7

    async def create(**kw):
        seen.append(kw['payload'])
        return {'id': 9, 'link': 'https://x/p'}

    monkeypatch.setattr(calendar_job, '_wp_kw', lambda: {'domain': 'd', 'wc_consumer_key': 'k', 'wc_consumer_secret': 's'})
    monkeypatch.setattr(calendar_job.blog_job, 'blog_category_id', blog_cat)
    monkeypatch.setattr(calendar_job.WordPressService, 'create_post', staticmethod(create))
    with pytest.raises(ValueError):  # a plain schedule still needs approval
        asyncio.run(calendar_job.schedule_one(pid, '09:00', 'America/New_York'))
    row = asyncio.run(calendar_job.schedule_one(pid, '09:00', 'America/New_York', now=True))  # publish now needs no approval
    assert seen[0].status == 'publish' and row['scheduled_for'] == 'now' and row['state'] == 'scheduled'
    async def update(**kw):
        seen.append(kw['payload'])
        return {'id': kw['post_id'], 'link': 'https://x/p'}

    monkeypatch.setattr(calendar_job.WordPressService, 'update_post', staticmethod(update))
    automation_store.calendar_update(pid, state='approved')
    asyncio.run(calendar_job.schedule_one(pid, '09:00', 'America/New_York'))
    # the article's own 16:30 time (EST = 21:30 UTC) wins over the global 09:00
    assert seen[1].status == 'future' and seen[1].date_gmt == '2099-12-01T21:30:00'


def test_parse_plan_passes_editor_instructions(monkeypatch):
    seen = {}

    async def fake(prompt, **kw):
        seen['p'] = prompt
        return '{"guidelines":"","articles":[{"title":"One","keyword":"","publish_date":"2026-12-07"}]}'

    monkeypatch.setattr(claude_review, 'ask_claude', fake)
    out = asyncio.run(calendar_job.parse_plan(filename='p.txt', data=b'x', text=None, start=None, per_week=5, instructions='Start Dec 7, Mon/Wed/Fri only'))
    assert 'Start Dec 7, Mon/Wed/Fri only' in seen['p'] and out['articles'][0]['publish_date'] == '2026-12-07'


def test_calendar_delete_batch_only_removes_that_plan():
    automation_store.calendar_add_many('keep', [{'title': 'K', 'keyword': '', 'publish_date': '2026-12-01'}])
    automation_store.calendar_add_many('drop', [{'title': 'D1', 'keyword': '', 'publish_date': '2026-12-02'}, {'title': 'D2', 'keyword': '', 'publish_date': '2026-12-03'}])
    automation_store.set_state('calendar_guidelines:drop', 'g')
    assert automation_store.calendar_delete_batch('drop') == 2
    assert [r['batch'] for r in automation_store.calendar_list()] == ['keep']
    assert automation_store.get_state('calendar_guidelines:drop') is None


# ---------------------------------------------------------------- link safety
CATS = [
    {'name': 'Dog Toys', 'link': 'https://shop.test/product-category/dog-toys/', 'count': 5},
    {'name': 'Home Decor', 'link': 'https://shop.test/product-category/home-decor/', 'count': 9},
]
CREDS = {'domain': 'shop.test', 'wc_consumer_key': 'k', 'wc_consumer_secret': 's'}


def _patch_links(monkeypatch, dead):
    async def fake_dead(urls):
        return {u for u in urls if u in dead}

    async def fake_cats(creds=None):
        return CATS

    monkeypatch.setattr(links, 'dead_urls', fake_dead)
    monkeypatch.setattr(links, 'store_categories', fake_cats)


def test_sanitize_replaces_dead_link_with_closest_category(monkeypatch):
    _patch_links(monkeypatch, {'https://shop.test/best-dog-toys/'})
    html = '<p>See <a href="/best-dog-toys/">our dog toys</a> and <a href="https://ok.test/x">fine</a>.</p>'
    out, changes = asyncio.run(links.sanitize_links(html, creds=CREDS))
    assert 'href="https://shop.test/product-category/dog-toys/"' in out and 'href="https://ok.test/x"' in out
    assert changes[0]['replacement'].endswith('/dog-toys/')
    assert asyncio.run(links.sanitize_links(out, creds=CREDS))[1] == []  # second pass changes nothing


def test_sanitize_unwraps_when_no_category_matches_and_keeps_allowed_and_mailto(monkeypatch):
    _patch_links(monkeypatch, {'https://shop.test/zzz/', 'https://shop.test/p/1/'})
    html = '<a href="/zzz/">mystery</a> <a href="https://shop.test/p/1/">Allowed</a> <a href="mailto:a@b.c">mail</a>'
    out, _ = asyncio.run(links.sanitize_links(html, creds=CREDS, allowed={'https://shop.test/p/1/'}))
    assert out.startswith('mystery ') and 'href="https://shop.test/p/1/"' in out and 'mailto:a@b.c' in out


def test_is_dead_only_for_404_410_and_unreachable():
    import httpx

    def handler(request):
        code = {'/gone': 404, '/old': 410, '/blocked': 403, '/ok': 200}.get(request.url.path, 200)
        return httpx.Response(code)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return [await links._is_dead(c, f'https://x.test{p}') for p in ('/gone', '/old', '/blocked', '/ok')]

    assert asyncio.run(run()) == [True, True, False, False]


def test_category_hint_lists_real_urls(monkeypatch):
    _patch_links(monkeypatch, set())
    hint = asyncio.run(links.category_hint('dog toys guide'))
    assert 'never invent' in hint and hint.index('Dog Toys') < hint.index('Home Decor')


def test_daily_blog_drops_dead_product_permalink(monkeypatch):
    from app.schemas.automation import BlogAutomationSettings
    from app.services.automation import blog_job

    cat = {'id': 1, 'path': 'Pets > Dog Toys', 'name': 'Dog Toys'}
    prods = [
        {'name': 'Live Toy', 'price': '10', 'sale_price': '5', 'permalink': 'https://shop.test/live/', 'media_id': None},
        {'name': 'Draft Toy', 'price': '10', 'sale_price': '5', 'permalink': 'https://shop.test/?p=9', 'media_id': None},
    ]
    seen = {}

    async def fake_dead(urls):
        return {u for u in urls if '?p=9' in u}

    async def fake_ask(prompt, **kw):
        seen['prompt'] = prompt
        return '{"title":"T","excerpt":"e","content":"<p>x</p>"}'

    monkeypatch.setattr(links, 'dead_urls', fake_dead)
    monkeypatch.setattr(blog_job, 'pick_topics', lambda cfg: [(cat, prods)])
    monkeypatch.setattr(claude_review, 'ask_claude', fake_ask)
    run_id = automation_store.create_run(kind='blog', trigger='manual', dry_run=True, local_date='2026-01-01')
    asyncio.run(blog_job.run_blog_job(run_id, BlogAutomationSettings(), dry_run=True))
    assert 'Live Toy' in seen['prompt'] and 'Draft Toy' not in seen['prompt']
