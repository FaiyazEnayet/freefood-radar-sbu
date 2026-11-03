"""P12: secure fetch boundary and final-host policy proof; no network needed.

The SB Engaged feed is announced on the publisher host and redirects to the
CampusGroups static host. These tests mock the whole HTTP chain and pin the
fetch boundary: every hop must stay on an approved HTTPS host without
embedded credentials, the origin robots policy must be retrievable and
permit the feed, a host the chain lands on is checked against its own
robots policy when it publishes one, redirect loops are contract failures
rather than transient errors, and cookies are never retained.
"""
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import radar.scrapers.sbengaged as sbengaged
from radar.scrapers.sbengaged import FEED_URL, FeedError, fetch_calendar

ORIGIN = 'sbengaged.stonybrook.edu'
STATIC = 'static-prod-us-east-1.campusgroups.com'
STATIC_FEED = f'https://{STATIC}/ical/ical_stonybrook.ics'
ICS = (b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//tests//EN\r\n'
       b'BEGIN:VEVENT\r\nUID:one\r\nDTSTART:20261018T160000Z\r\n'
       b'DTEND:20261018T180000Z\r\nSUMMARY:Free pizza\r\nEND:VEVENT\r\n'
       b'END:VCALENDAR\r\n')
# Mirrors the live publisher policy shape: the feed path is allowed, a few
# private path prefixes are disallowed.
ORIGIN_ROBOTS = ('User-agent: *\nDisallow: /upload/\nDisallow: /downloads/\n'
                 'Disallow: /mobile_ws/v17/\n')


class FakeResponse:
    def __init__(self, status_code, headers=None, body=b'', url=FEED_URL,
                 text='', history=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.history = history or []
        self._chunks = chunks if chunks is not None else [body]
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        yield from self._chunks


def hop(status, url):
    return FakeResponse(status, {}, url=url)


class FakeCookieJar:
    def __init__(self):
        self.stored = set()

    def clear(self):
        self.stored.clear()


class FakeSession:
    """Routes robots reads per host and replays a scripted feed chain.

    A response carrying a Set-Cookie header stores the cookie in the jar,
    the way a real session would, so tests can prove the fetch drops it.
    """

    def __init__(self, feed_items, origin_robots=None, host_robots=None):
        self.feed_items = list(feed_items)
        self.origin_robots = origin_robots if origin_robots is not None else FakeResponse(
            200, {}, text=ORIGIN_ROBOTS, url=f'https://{ORIGIN}/robots.txt')
        # Hosts not listed here answer 403 for robots.txt: no published
        # policy (the static file host answers a missing-key 403).
        self.host_robots = host_robots or {}
        self.cookies = FakeCookieJar()
        self.feed_calls = 0
        self._next = 0

    def get(self, url, headers=None, timeout=None, **kwargs):
        if url.endswith('/robots.txt'):
            host = url.split('/')[2]
            item = (self.origin_robots if host == ORIGIN
                    else self.host_robots.get(host, FakeResponse(403, {}, url=url)))
            if isinstance(item, Exception):
                raise item
            return item
        self.feed_calls += 1
        index = min(self._next, len(self.feed_items) - 1)
        self._next += 1
        item = self.feed_items[index]
        if isinstance(item, Exception):
            raise item
        if 'Set-Cookie' in item.headers:
            self.cookies.stored.add(item.headers['Set-Cookie'])
        return item


def good_feed(url=FEED_URL, history=None, headers=None, **kwargs):
    return FakeResponse(200, headers or {'Content-Type': 'text/calendar'},
                        body=ICS, url=url, history=history, **kwargs)


def test_stage3_freefood_radar_12_direct_feed_on_announced_host_works():
    session = FakeSession([good_feed()])
    raw, info = fetch_calendar(session=session, backoff_seconds=0)
    assert raw == ICS
    assert info['final_url'] == FEED_URL
    assert info['attempts'] == 1


@pytest.mark.parametrize('status', [301, 302, 307, 308])
def test_stage3_freefood_radar_12_redirect_statuses_via_approved_host_work(status):
    session = FakeSession([good_feed(url=STATIC_FEED, history=[hop(status, FEED_URL)])])
    raw, info = fetch_calendar(session=session, backoff_seconds=0)
    assert raw == ICS
    assert info['final_url'] == STATIC_FEED


@pytest.mark.parametrize('status', [301, 302, 307])
def test_stage3_freefood_radar_12_redirect_to_unapproved_host_fails_closed(status):
    evil = 'https://evil.example.com/ical/feed.ics'
    session = FakeSession([good_feed(url=evil, history=[hop(status, FEED_URL)])])
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


def test_stage3_freefood_radar_12_unapproved_middle_hop_fails_closed():
    chain = [hop(301, FEED_URL), hop(302, 'https://evil.example.com/hop')]
    session = FakeSession([good_feed(url=STATIC_FEED, history=chain)])
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


def test_stage3_freefood_radar_12_https_downgrade_in_chain_fails_closed():
    chain = [hop(302, 'http://sbengaged.stonybrook.edu/ical/stonybrook/ical_stonybrook.ics')]
    session = FakeSession([good_feed(history=chain)])
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


def test_stage3_freefood_radar_12_redirect_url_with_embedded_credentials_fails_closed():
    credentialed = 'https://user:secret@sbengaged.stonybrook.edu/ical/stonybrook/ical_stonybrook.ics'
    session = FakeSession([good_feed(url=credentialed, history=[hop(302, FEED_URL)])])
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


@pytest.mark.parametrize('content_type', ['text/html; charset=utf-8',
                                          'application/javascript',
                                          'text/plain'])
def test_stage3_freefood_radar_12_non_calendar_content_type_fails_closed(content_type):
    session = FakeSession([FakeResponse(200, {'Content-Type': content_type},
                                        body=b'<html>Sign in</html>')])
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


def test_stage3_freefood_radar_12_oversized_stream_fails_closed(monkeypatch):
    monkeypatch.setattr(sbengaged, 'MAX_BYTES', 100)
    chunks = [b'x' * 60, b'y' * 60]
    session = FakeSession([FakeResponse(200, {'Content-Type': 'text/calendar'},
                                        chunks=chunks)])
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


@pytest.mark.parametrize('status', [404, 500, 503])
def test_stage3_freefood_radar_12_origin_robots_unavailable_fails_closed(status):
    session = FakeSession([good_feed()], origin_robots=FakeResponse(
        status, {}, url=f'https://{ORIGIN}/robots.txt'))
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)
    assert session.feed_calls == 0


@pytest.mark.parametrize('failure', [requests.exceptions.ConnectTimeout('slow'),
                                     requests.exceptions.ReadTimeout('slow'),
                                     requests.exceptions.ConnectionError('down')])
def test_stage3_freefood_radar_12_origin_robots_transport_failure_fails_closed(failure):
    session = FakeSession([good_feed()], origin_robots=failure)
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)
    assert session.feed_calls == 0


def test_stage3_freefood_radar_12_origin_robots_disallow_fails_closed():
    session = FakeSession([good_feed()], origin_robots=FakeResponse(
        200, {}, text='User-agent: *\nDisallow: /ical/\n',
        url=f'https://{ORIGIN}/robots.txt'))
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)
    assert session.feed_calls == 0


def test_stage3_freefood_radar_12_robots_redirect_off_approved_hosts_fails_closed():
    robots = FakeResponse(200, {}, text=ORIGIN_ROBOTS,
                          url='https://evil.example.com/robots.txt',
                          history=[hop(302, f'https://{ORIGIN}/robots.txt')])
    session = FakeSession([good_feed()], origin_robots=robots)
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)
    assert session.feed_calls == 0


def test_stage3_freefood_radar_12_redirect_loop_fails_without_retry():
    session = FakeSession([requests.exceptions.TooManyRedirects('loop')])
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)
    assert session.feed_calls == 1


def test_stage3_freefood_radar_12_landing_host_robots_disallow_fails_closed():
    host_robots = {STATIC: FakeResponse(200, {}, text='User-agent: *\nDisallow: /\n',
                                        url=f'https://{STATIC}/robots.txt')}
    session = FakeSession([good_feed(url=STATIC_FEED, history=[hop(302, FEED_URL)])],
                          host_robots=host_robots)
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


@pytest.mark.parametrize('failure', [requests.exceptions.ConnectionError('down'),
                                     FakeResponse(503, {}, url=f'https://{STATIC}/robots.txt')])
def test_stage3_freefood_radar_12_landing_host_robots_unavailable_fails_closed(failure):
    session = FakeSession([good_feed(url=STATIC_FEED, history=[hop(302, FEED_URL)])],
                          host_robots={STATIC: failure})
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


def test_stage3_freefood_radar_12_landing_host_without_robots_file_still_works():
    # The static host publishes no robots.txt (missing-key 403): under the
    # robots access rules that is no published restriction, not a policy.
    host_robots = {STATIC: FakeResponse(404, {}, url=f'https://{STATIC}/robots.txt')}
    session = FakeSession([good_feed(url=STATIC_FEED, history=[hop(302, FEED_URL)])],
                          host_robots=host_robots)
    raw, _ = fetch_calendar(session=session, backoff_seconds=0)
    assert raw == ICS


def test_stage3_freefood_radar_12_same_host_redirect_to_disallowed_path_fails_closed():
    target = f'https://{ORIGIN}/downloads/ical_stonybrook.ics'
    session = FakeSession([good_feed(url=target, history=[hop(302, FEED_URL)])])
    with pytest.raises(FeedError):
        fetch_calendar(session=session, backoff_seconds=0)


def test_stage3_freefood_radar_12_cookies_are_never_retained():
    response = good_feed(headers={'Content-Type': 'text/calendar',
                                  'Set-Cookie': 'session=abc; Path=/'})
    session = FakeSession([response])
    raw, info = fetch_calendar(session=session, backoff_seconds=0)
    assert raw == ICS
    assert session.cookies.stored == set()
    assert not any('cookie' in key.lower() for key in info)
