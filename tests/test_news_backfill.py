from datetime import datetime, timezone
from email.utils import format_datetime
import json

import pytest

from coffee_service import jev, jev_store, news_backfill as worker


def article():
    row = jev._article_fields({'url': 'https://example.com/coffee', 'title': 'Brazil coffee crop falls',
                               'published_at': '2025-10-01T12:00:00Z'})
    row.update(selection_date='2025-10-01', selection_available_at='2025-10-02T04:00:00Z')
    return row


def setup_store(path):
    jev_store.initialize(path)
    news = jev_store.read_document(path/'news.json')
    news['selections'] = {'historical': [article()], 'recent': [article()]}
    jev_store.write_document(path/'news.json', news)


class Session:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.calls = 0

    def post(self, url, **kwargs):
        self.calls += 1
        status = next(self.statuses)
        answers = {}
        for name in kwargs['json']['questions']:
            answers[name] = ({'type': 'choice', 'choice': 'bullish', 'confidence': .7,
                              'probabilities': {'bullish': .7, 'bearish': .1, 'neutral': .1, 'uncertain': .1}}
                             if name.endswith('price_pressure') else {'type': 'noul', 'noul': .9})
        body = {'answers': answers, 'usage': {'input_tokens': 100},
                'provider_metadata': {'gateway': {'cost': '.001'}}} if status == 200 else {'error': {'code': 'limited'}}
        class Response:
            status_code = status
            headers = {'Retry-After': '60'}
            text = json.dumps(body)
            def json(self):
                return body
        return Response()


def test_retry_cadence_capture_and_reuse_without_sidecar_files(tmp_path, monkeypatch):
    setup_store(tmp_path)
    session = Session([429, 200])
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 0
    state = jev_store.read_document(tmp_path/'requests.json')['worker_state']
    assert state['last_response']['status'] == 429 and state['interval_seconds'] == 60
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 0
    assert session.calls == 1
    future = worker.time.time() + 61
    monkeypatch.setattr(worker.time, "time", lambda: future)
    state['next_attempt_epoch'] = 0
    state['last_response']['at'] = '2020-01-01T00:00:00Z'
    doc = jev_store.read_document(tmp_path/'requests.json'); doc['worker_state'] = state
    jev_store.write_document(tmp_path/'requests.json', doc)
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 0
    state = jev_store.read_document(tmp_path/'requests.json')['worker_state']
    assert state['interval_seconds'] == 300 and state['next_attempt_epoch'] > worker.time.time()+290
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 0
    assert session.calls == 2
    assert len(jev_store.read_analyses(tmp_path)) == 1
    assert set(p.name for p in tmp_path.iterdir()) == {'news.json', 'requests.json', 'responses.json', 'sentiment.csv'}
    assert len(jev_store.read_document(tmp_path/'responses.json')['attempts']) == 2


def test_budget_stop_is_persisted_and_not_retried(tmp_path):
    import pytest
    setup_store(tmp_path)
    session = Session([402])
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 1
    with pytest.raises(RuntimeError, match='Inspect requests.json'):
        worker.run(tmp_path, once=True, transport=session, api_key='fixture')
    assert session.calls == 1


def test_request_bytes_and_retry_after_contract():
    rows = [{**article(), 'title': f'Brazil coffee crop falls {i}'} for i in range(150)]
    batch = worker.pack_batch(rows)
    assert 20 < len(batch) < len(rows)
    assert len(json.dumps(jev._batch_payload(batch), ensure_ascii=False).encode()) <= jev.MAX_BATCH_BYTES
    assert len(json.dumps(jev._batch_payload(batch+[rows[len(batch)]]), ensure_ascii=False).encode()) > jev.MAX_BATCH_BYTES
    assert worker.retry_delay('NaN') == worker.retry_delay('-1') == 60
    assert worker.retry_delay('7200') == 7200
    date = format_datetime(datetime.fromtimestamp(10000, timezone.utc), usegmt=True)
    assert worker.retry_delay(date, now=1000) == 9000


def test_directory_lock_survives_atomic_json_replacement(tmp_path):
    import pytest
    setup_store(tmp_path)
    with jev._cache_lock(tmp_path/'responses.json'):
        jev._write_records(tmp_path/'responses.json', {'schema_version': 1, 'analyses': [], 'attempts': []})
        with pytest.raises(jev.JevError, match='already'):
            with jev._cache_lock(tmp_path/'responses.json'):
                pass


def test_saved_success_is_recovered_without_resending(tmp_path):
    setup_store(tmp_path)
    session = Session([200])
    capture = jev_store.CaptureSession(tmp_path, session)
    capture.articles = [article()]
    jev.classify_articles(capture.articles, session=capture, api_key='fixture')
    assert jev_store.read_analyses(tmp_path) == []
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 0
    assert session.calls == 1
    assert len(jev_store.read_analyses(tmp_path)) == 1


def test_unanswered_request_stops_before_new_spending(tmp_path):
    setup_store(tmp_path)
    doc = jev_store.read_document(tmp_path/'requests.json')
    doc['attempts'].append({'request_id': 'interrupted'})
    jev_store.write_document(tmp_path/'requests.json', doc)
    session = Session([])
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 1
    assert session.calls == 0
    assert jev_store.read_document(tmp_path/'requests.json')['worker_state']['state'] == 'cost_unknown'


def test_service_collection_uses_unified_archive_and_reuses_selection(tmp_path):
    from datetime import date
    data = tmp_path/'archive'
    candidates = tmp_path/'candidates.json'
    candidates.write_text(json.dumps([article()]))
    session = Session([200])
    rows, status = jev.collect_and_classify(data/'responses.json', date(2025,10,1),
        date(2025,10,2), limit=1, source='yahoo', candidates_path=candidates, session=session, api_key='fixture')
    assert len(rows) == 1 and session.calls == 1
    assert status['classification_status'] == 'success'
    again, status = jev.collect_and_classify(data/'responses.json', date(2025,10,1),
        date(2025,10,2), limit=1, source='yahoo', candidates_path=candidates, session=session, api_key='fixture')
    assert again == rows and status['api_attempts'] == 0 and session.calls == 1
    assert len(list(data.iterdir())) == 4


def test_wait_releases_archive_lock(tmp_path, monkeypatch):
    import pytest
    setup_store(tmp_path)
    session = Session([429])
    class StopWait(Exception):
        pass
    def sleep(seconds):
        assert seconds > 0
        with jev._cache_lock(tmp_path/'responses.json'):
            pass
        raise StopWait
    monkeypatch.setattr(worker.time, 'sleep', sleep)
    with pytest.raises(StopWait):
        worker.run(tmp_path, transport=session, api_key='fixture')
    assert session.calls == 1


@pytest.mark.parametrize('metadata', [
    {'gateway': {'cost': 'NaN'}}, {'gateway': {'cost': -1}},
    {'gateway': {'cost': True}}, {'gateway': 'invalid'}, 'invalid',
    False, [], 0, {'gateway': False}, {'gateway': []}, {'gateway': 0},
])
def test_invalid_cost_stops_but_keeps_raw_response(tmp_path, metadata):
    setup_store(tmp_path)
    class BadCostSession(Session):
        def post(self, *args, **kwargs):
            response = super().post(*args, **kwargs)
            response.json()['provider_metadata'] = metadata
            return response
    session = BadCostSession([200])
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 1
    assert len(jev_store.read_document(tmp_path/'responses.json')['attempts']) == 1
    assert jev_store.read_analyses(tmp_path) == []
    assert jev_store.read_document(tmp_path/'requests.json')['worker_state']['state'] == 'stopped'


@pytest.mark.parametrize('status', [401, 402, 403])
def test_saved_denied_response_stops_after_crash(tmp_path, status):
    setup_store(tmp_path)
    session = Session([status])
    capture = jev_store.CaptureSession(tmp_path, session)
    capture.articles = [article()]
    with pytest.raises(jev.JevStopError):
        jev.classify_articles(capture.articles, session=capture, api_key='fixture')
    assert worker.run(tmp_path, once=True, transport=session, api_key='fixture') == 1
    assert session.calls == 1
    state = jev_store.read_document(tmp_path/'requests.json')['worker_state']
    assert state['state'] == 'stopped' and str(status) in state['error']
