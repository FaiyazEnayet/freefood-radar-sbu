"""FR7: feed operations runbook and the offline contributor path.

The runbook in SBENGAGED-INTEGRATION.md must cover the six operating
topics a maintainer needs (daily schedule, failed builds, last good
snapshots, source schema changes, data rights, model retraining
decisions). A new contributor must be able to run the suite and refresh
a fixture with no network; the real fetch stays an explicit opt-in, and
the published artifacts must carry no private authentication data.
"""
import importlib
import json
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'SBENGAGED-INTEGRATION.md'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def runbook():
    text = DOC.read_text(encoding='utf8')
    marker = '## Operations runbook'
    assert marker in text, 'SBENGAGED-INTEGRATION.md has no operations runbook'
    # Collapse line wrapping so phrase checks do not depend on column width.
    return re.sub(r'\s+', ' ', text.split(marker, 1)[1])


def test_stage3_freefood_radar_7_runbook_covers_daily_schedule():
    section = runbook()
    assert '11:17 UTC' in section
    assert 'daily' in section.lower()
    assert 'sbengaged.yml' in section


def test_stage3_freefood_radar_7_runbook_covers_failed_builds():
    section = runbook().lower()
    assert 'fail' in section
    assert 'sbengaged-status.json' in section
    # The uninvestigated failed refresh of Thu Oct 29, 2026 stays on the
    # books until someone works it; the runbook must not lose track of it.
    assert 'oct 29' in section or 'october 29' in section
    assert 'not yet been investigated' in section or 'uninvestigated' in section


def test_stage3_freefood_radar_7_runbook_covers_last_good_snapshots():
    section = runbook()
    assert 'data/sbengaged-events.json' in section
    assert 'data/sbengaged-status.json' in section
    assert 'last good' in section.lower()


def test_stage3_freefood_radar_7_runbook_covers_schema_changes():
    section = runbook().lower()
    assert 'schema' in section
    assert 'rrule' in section or 'recurrence' in section
    assert 'fail' in section  # schema surprises fail visibly, never silently


def test_stage3_freefood_radar_7_runbook_covers_data_rights():
    section = runbook().lower()
    assert 'public' in section
    assert 'withheld' in section
    assert 'credential' in section or 'cookie' in section  # none are stored


def test_stage3_freefood_radar_7_runbook_covers_retraining_decisions():
    section = runbook().lower()
    assert 'refit' in section
    assert 'seed-7' in section
    assert 'held-out' in section


def test_stage3_freefood_radar_7_fixture_refresh_runs_offline(tmp_path, monkeypatch):
    """A fixture refresh must complete with networking fully blocked."""
    from radar.live import refresh

    def no_network(*args, **kwargs):
        raise AssertionError('network access attempted during fixture refresh')

    monkeypatch.setattr(socket, 'create_connection', no_network)
    monkeypatch.setattr(socket.socket, 'connect', no_network)

    body = (FIXTURES / 'sbengaged_full.ics').read_bytes()
    ok = refresh(tmp_path, now=NOW,
                 fetcher=lambda: (body, {}),
                 scorer=lambda rows, root: {'test_model': True})
    assert ok
    snapshot = json.loads((tmp_path / 'data/sbengaged-events.json').read_text())
    assert snapshot['classification'] == 'public_source_snapshot'
    assert snapshot['events'], 'fixture refresh produced no events'
    assert (tmp_path / 'data/sbengaged-status.json').exists()


def test_stage3_freefood_radar_7_real_fetch_is_opt_in(monkeypatch):
    """Importing the integration must not fetch; fetching is an explicit call."""
    import requests
    import radar.live
    import radar.scrapers.sbengaged

    calls = []
    real_request = requests.sessions.Session.request

    def recording_request(self, *args, **kwargs):
        calls.append((args, kwargs))
        return real_request(self, *args, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, 'request', recording_request)
    try:
        importlib.reload(radar.scrapers.sbengaged)
        importlib.reload(radar.live)
    finally:
        monkeypatch.undo()
        importlib.reload(radar.scrapers.sbengaged)
        importlib.reload(radar.live)
    assert calls == [], f'import triggered HTTP requests: {calls}'


def test_stage3_freefood_radar_7_no_private_auth_data_in_artifacts():
    key_pattern = re.compile(r'cookie|password|passwd|authorization|bearer|api[-_ ]?key|secret', re.I)

    def walk(node, where=''):
        if isinstance(node, dict):
            for key, value in node.items():
                assert not key_pattern.search(str(key)), f'auth-shaped key at {where}: {key}'
                walk(value, f'{where}.{key}')
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f'{where}[{i}]')
        elif isinstance(node, str):
            assert not re.search(r'(?i)(password|passwd|cookie)\s*[:=]\s*\S+', node), \
                f'credential-shaped value at {where}'

    for name in ('sbengaged-events.json', 'sbengaged-status.json'):
        walk(json.loads((ROOT / 'data' / name).read_text(encoding='utf8')), name)
