"""Source-contract and failure behavior tests; no network needed."""
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from radar.scrapers.sbengaged import parse_calendar, FeedError
from radar.live import refresh

NOW=datetime(2026,10,17,12,tzinfo=timezone.utc)
def calendar(*events):
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//tests//EN\r\n'+''.join(events)+'END:VCALENDAR\r\n').encode()
def event(uid='one',extra='',location='SAC 302',url='https://sbengaged.stonybrook.edu/rsvp?id=1'):
    return f'BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:20261018T160000Z\r\nDTEND:20261018T180000Z\r\nSUMMARY:Free pizza\r\nDESCRIPTION:Join us\\nFood provided\r\nLOCATION:{location}\r\nURL:{url}\r\n{extra}END:VEVENT\r\n'

def test_public_event_timezone_and_withheld_location():
    rows,stats=parse_calendar(calendar(event(location='Sign in to download the location')),NOW)
    assert stats['selected_events']==1
    assert rows[0]['location']=='' and rows[0]['location_visibility']=='withheld_by_source'
    assert rows[0]['food_verified'] is False and rows[0]['food_probability'] is None
    assert rows[0]['starts_at'].endswith('+00:00')

def test_newest_uid_cancellation_supersedes_older_record():
    old=event(extra='SEQUENCE:1\r\nLAST-MODIFIED:20261015T000000Z\r\n')
    cancelled=event(extra='SEQUENCE:2\r\nLAST-MODIFIED:20261016T000000Z\r\nSTATUS:CANCELLED\r\n')
    rows,stats=parse_calendar(calendar(old,cancelled),NOW)
    assert rows==[] and stats['cancelled']==1

def test_timezone_daylight_saving_and_folded_text():
    raw=event().replace('DTSTART:20261018T160000Z','DTSTART;TZID=America/New_York:20261018T120000').replace('DTEND:20261018T180000Z','DTEND;TZID=America/New_York:20261018T140000').replace('SUMMARY:Free pizza','SUMMARY:Free pizza and\r\n  snacks')
    rows,_=parse_calendar(calendar(raw),NOW)
    assert rows[0]['starts_at'].endswith('-04:00') and rows[0]['title']=='Free pizza and snacks'

def test_all_day_and_expired_events():
    raw=event().replace('DTSTART:20261018T160000Z','DTSTART;VALUE=DATE:20261018').replace('DTEND:20261018T180000Z','DTEND;VALUE=DATE:20261019')
    rows,_=parse_calendar(calendar(raw,event(uid='expired').replace('20261018','20261016')),NOW)
    assert len(rows)==1 and rows[0]['all_day'] is True

@pytest.mark.parametrize('raw',[b'<html>Sign in</html>',b'BEGIN:VCALENDAR\nnot valid',calendar(event(extra='RRULE:FREQ=DAILY\r\n'))])
def test_invalid_or_changed_feed_fails_loudly(raw):
    with pytest.raises(FeedError):parse_calendar(raw,NOW)

def test_rejects_external_or_unsafe_links():
    rows,stats=parse_calendar(calendar(event(url='javascript:alert(1)')),NOW)
    assert rows==[] and stats['invalid_records']==1

def test_failed_refresh_preserves_last_good_snapshot(tmp_path):
    p=tmp_path/'data';p.mkdir();good={'fetched_at':'2026-10-16T12:00:00+00:00','events':[{'id':'keep'}]}
    (p/'sbengaged-events.json').write_text(json.dumps(good))
    def fail():raise FeedError('HTTP 503')
    assert refresh(tmp_path,now=NOW,fetcher=fail) is False
    assert json.loads((p/'sbengaged-events.json').read_text())==good
    status=json.loads((p/'sbengaged-status.json').read_text())
    assert status['success'] is False and '503' in status['error']

def test_successful_refresh_never_substitutes_seed_events(tmp_path):
    assert refresh(tmp_path,now=NOW,fetcher=lambda:(calendar(event()),{}),scorer=lambda rows,root:{'test_model':True})
    data=json.loads((tmp_path/'data/sbengaged-events.json').read_text())
    assert data['classification']=='public_source_snapshot' and len(data['events'])==1
    assert data['events'][0]['source']=='sbengaged'

# --- FR2: revision precedence against its iTIP source (RFC 5546 2.1.5 on
# RFC 5545 3.8.7.4: the highest SEQUENCE obsoletes every lower revision,
# regardless of timestamps), and explicit recurrence-instance identity ---

def test_stage3_freefood_radar_2_cancellation_with_higher_sequence_and_older_timestamp_wins():
    active=event(extra='SEQUENCE:2\r\nLAST-MODIFIED:20261016T000000Z\r\n')
    cancelled=event(extra='SEQUENCE:5\r\nLAST-MODIFIED:20261015T000000Z\r\nSTATUS:CANCELLED\r\n')
    for feed in (calendar(active,cancelled),calendar(cancelled,active)):
        rows,stats=parse_calendar(feed,NOW)
        assert rows==[] and stats['cancelled']==1

def test_stage3_freefood_radar_2_winning_revision_content_follows_sequence_not_timestamp():
    stale=event(extra='SEQUENCE:2\r\nLAST-MODIFIED:20261016T000000Z\r\n').replace('Free pizza','Stale title')
    current=event(extra='SEQUENCE:5\r\nLAST-MODIFIED:20261015T000000Z\r\n').replace('Free pizza','Current title')
    for feed in (calendar(stale,current),calendar(current,stale)):
        rows,_=parse_calendar(feed,NOW)
        assert [row['title'] for row in rows]==['Current title']

def test_stage3_freefood_radar_2_recurrence_failure_names_the_instance_identity():
    with pytest.raises(FeedError) as caught:
        parse_calendar(calendar(event(uid='series',extra='RRULE:FREQ=WEEKLY\r\n')),NOW)
    assert 'series' in str(caught.value)

def test_stage3_freefood_radar_2_recurrence_id_override_is_not_merged_with_its_master():
    override=event(uid='series',extra='RECURRENCE-ID:20261018T160000Z\r\nSTATUS:CANCELLED\r\n')
    with pytest.raises(FeedError) as caught:
        parse_calendar(calendar(event(uid='series'),override),NOW)
    message=str(caught.value)
    assert 'RECURRENCE-ID' in message and 'series' in message

def test_stage3_freefood_radar_2_recurrence_feed_fails_the_refresh_and_keeps_the_snapshot(tmp_path):
    p=tmp_path/'data';p.mkdir();good={'fetched_at':'2026-10-16T12:00:00+00:00','events':[{'id':'keep'}]}
    (p/'sbengaged-events.json').write_text(json.dumps(good))
    recurring=calendar(event(extra='RRULE:FREQ=WEEKLY\r\n'))
    assert refresh(tmp_path,now=NOW,fetcher=lambda:(recurring,{}),scorer=lambda rows,root:{'test_model':True}) is False
    assert json.loads((p/'sbengaged-events.json').read_text())==good
    status=json.loads((p/'sbengaged-status.json').read_text())
    assert status['success'] is False and 'Recurring' in status['error']
