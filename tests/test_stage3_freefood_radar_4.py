"""FR4: conditional, versioned and inspectable refreshes; no network needed.

A 304 must preserve the last good snapshot byte for byte while moving
health forward. Transient fetch failures must be retried a bounded number
of times without duplicating UIDs. Every refresh must record explicit
source, model and schema lineage plus a stale/failure state and a model
hash that a status view can mark.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from radar.live import MODEL_CONFIG, model_config_hash, refresh
from radar.scrapers.sbengaged import FEED_URL, FeedError, fetch_calendar, parse_calendar

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
SCORER = lambda rows, root: {'test_model': True}


def fixture(name):
    return (FIXTURES / name).read_bytes()


def seed(tmp_path, when=NOW):
    assert refresh(tmp_path, now=when,
                   fetcher=lambda: (fixture('sbengaged_full.ics'), {}),
                   scorer=SCORER)
    return json.loads((tmp_path / 'data/sbengaged-events.json').read_text())


def snapshot_of(tmp_path):
    return json.loads((tmp_path / 'data/sbengaged-events.json').read_text())


def status_of(tmp_path):
    return json.loads((tmp_path / 'data/sbengaged-status.json').read_text())


def snapshot_bytes(tmp_path):
    return (tmp_path / 'data/sbengaged-events.json').read_bytes()


class FakeResponse:
    def __init__(self, status_code, headers=None, body=b'', url=FEED_URL, text=''):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.history = []
        self._body = body
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        yield self._body


class FakeSession:
    """Records every feed GET; robots always allows the feed."""

    def __init__(self, feed_responses):
        self.feed_responses = list(feed_responses)
        self.feed_calls = []
        self.feed_headers = []
        self._next = 0

    def get(self, url, headers=None, timeout=None, stream=False, allow_redirects=True):
        if url.endswith('/robots.txt'):
            return FakeResponse(200, {}, text='User-agent: *\nAllow: /\n',
                                url=url)
        self.feed_calls.append(url)
        self.feed_headers.append(dict(headers or {}))
        index = min(self._next, len(self.feed_responses) - 1)
        self._next += 1
        item = self.feed_responses[index]
        if isinstance(item, Exception):
            raise item
        return item


def test_stage3_freefood_radar_4_conditional_headers_sent_on_200():
    session = FakeSession([FakeResponse(
        200, {'Content-Type': 'text/calendar', 'ETag': '"v2"'},
        body=fixture('sbengaged_full.ics'))])
    raw, http = fetch_calendar(session=session,
                               previous={'etag': '"v1"', 'last_modified': 'Wed, 15 Oct 2026 12:00:00 GMT'},
                               backoff_seconds=0)
    assert raw == fixture('sbengaged_full.ics')
    assert session.feed_headers[0]['If-None-Match'] == '"v1"'
    assert session.feed_headers[0]['If-Modified-Since'] == 'Wed, 15 Oct 2026 12:00:00 GMT'
    assert http['conditional_request'] is True
    assert http['not_modified'] is False
    assert http['etag'] == '"v2"'


def test_stage3_freefood_radar_4_fetch_calendar_handles_304():
    session = FakeSession([FakeResponse(304, {'ETag': '"v1"'}, url=FEED_URL)])
    raw, http = fetch_calendar(session=session,
                               previous={'etag': '"v1"'},
                               backoff_seconds=0)
    assert raw is None
    assert http['not_modified'] is True
    assert http['etag'] == '"v1"'
    assert session.feed_headers[0]['If-None-Match'] == '"v1"'


def test_stage3_freefood_radar_4_304_preserves_snapshot_bytes_and_updates_health(tmp_path):
    good = seed(tmp_path)
    before = snapshot_bytes(tmp_path)
    assert len(good['events']) == 30

    def feed304(previous=None):
        assert previous is not None
        return None, {'not_modified': True, 'url': FEED_URL, 'final_url': FEED_URL,
                      'etag': previous.get('etag'), 'last_modified': previous.get('last_modified')}

    assert refresh(tmp_path, now=LATER, fetcher=feed304, scorer=SCORER) is True
    assert snapshot_bytes(tmp_path) == before
    status = status_of(tmp_path)
    assert status['success'] is True
    assert status['not_modified'] is True
    assert status['state'] == 'fresh'
    assert status['count_change'] == 'not_modified'
    assert status['selected_events'] == 30
    assert status['last_attempt_at'] == LATER.isoformat()
    assert status['last_successful_fetch_at'] == LATER.isoformat()
    assert status['error'] is None
    assert status['lineage']['model_sha256'] == good['lineage']['model_sha256']


def test_stage3_freefood_radar_4_304_without_previous_snapshot_fails(tmp_path):
    def feed304(previous=None):
        return None, {'not_modified': True, 'url': FEED_URL, 'final_url': FEED_URL}
    assert refresh(tmp_path, now=NOW, fetcher=feed304, scorer=SCORER) is False
    assert not (tmp_path / 'data/sbengaged-events.json').exists()
    assert status_of(tmp_path)['state'] == 'failed'


def test_stage3_freefood_radar_4_retries_do_not_duplicate_uids():
    raw_fixture = fixture('sbengaged_full.ics')
    session = FakeSession([
        FakeResponse(503, {'Content-Type': 'text/calendar'}),
        FakeResponse(503, {'Content-Type': 'text/calendar'}),
        FakeResponse(200, {'Content-Type': 'text/calendar'}, body=raw_fixture),
    ])
    raw, http = fetch_calendar(session=session, backoff_seconds=0)
    events, stats = parse_calendar(raw, NOW)
    assert http['attempts'] == 3
    assert len(session.feed_calls) == 3
    assert len(events) == 30
    assert len({e['uid'] for e in events}) == 30
    assert stats['selected_events'] == 30


def test_stage3_freefood_radar_4_retries_are_bounded():
    session = FakeSession([FakeResponse(503, {'Content-Type': 'text/calendar'})])
    try:
        fetch_calendar(session=session, backoff_seconds=0)
    except FeedError:
        pass
    else:
        raise AssertionError('expected FeedError after bounded retries')
    assert len(session.feed_calls) == 3


def test_stage3_freefood_radar_4_no_retry_on_404():
    session = FakeSession([FakeResponse(404, {'Content-Type': 'text/calendar'})])
    try:
        fetch_calendar(session=session, backoff_seconds=0)
    except FeedError:
        pass
    else:
        raise AssertionError('expected FeedError on 404')
    assert len(session.feed_calls) == 1


def test_stage3_freefood_radar_4_success_carries_lineage_state_and_model_hash(tmp_path):
    good = seed(tmp_path)
    lineage = good['lineage']
    assert lineage['schema_version'] == 1
    assert lineage['source_sha256'] == hashlib.sha256(fixture('sbengaged_full.ics')).hexdigest()
    assert lineage['source_url'] == FEED_URL
    assert len(lineage['model_sha256']) == 64
    status = status_of(tmp_path)
    assert status['state'] == 'fresh'
    assert status['success'] is True
    assert status['lineage'] == lineage
    assert status['model_sha256'] == lineage['model_sha256']


def test_stage3_freefood_radar_4_failure_keeps_last_good_and_marks_stale(tmp_path):
    good = seed(tmp_path)
    before = snapshot_bytes(tmp_path)

    def fail():
        raise FeedError('HTTP 503')

    assert refresh(tmp_path, now=LATER, fetcher=fail, scorer=SCORER) is False
    assert snapshot_bytes(tmp_path) == before
    status = status_of(tmp_path)
    assert status['state'] == 'stale'
    assert status['success'] is False
    assert '503' in status['error']
    assert status['last_successful_fetch_at'] == NOW.isoformat()
    assert status['lineage']['model_sha256'] == good['lineage']['model_sha256']


def test_stage3_freefood_radar_4_failure_without_snapshot_is_failed_state(tmp_path):
    def fail():
        raise FeedError('HTTP 503')

    assert refresh(tmp_path, now=NOW, fetcher=fail, scorer=SCORER) is False
    status = status_of(tmp_path)
    assert status['state'] == 'failed'
    assert status['last_successful_fetch_at'] is None


def test_stage3_freefood_radar_4_real_model_config_hash_is_immutable(tmp_path):
    from radar.live import score_snapshot
    repo_root = Path(__file__).resolve().parents[1]
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/labeled.csv').write_bytes((repo_root / 'data/labeled.csv').read_bytes())
    assert refresh(tmp_path, now=NOW,
                   fetcher=lambda: (fixture('sbengaged_full.ics'), {}),
                   scorer=score_snapshot) is True
    snapshot = snapshot_of(tmp_path)
    assert snapshot['model']['config'] == MODEL_CONFIG
    assert snapshot['model']['config_sha256'] == model_config_hash()
    assert snapshot['lineage']['model_sha256'] == model_config_hash()
    assert model_config_hash() == model_config_hash(MODEL_CONFIG)
