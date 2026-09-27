from datetime import date
from threading import Event

import pytest

from coffee_service import jev_store, pipeline, refresh


def setup_cycle(monkeypatch, tmp_path, *, numeric_failure=False, worker_state='completed'):
    from coffee_service import news_incremental
    calls = []
    def numeric(mode, source, artifact, start, end, **kwargs):
        calls.append(('numeric', mode, start, end, kwargs))
        if numeric_failure:
            raise RuntimeError('secret must not be saved')
        return {'status': 'success'}
    def collect(data, end):
        calls.append(('collect', end))
        return {'source_status': 'success'}
    def classify(data, **kwargs):
        calls.append(('classify', kwargs))
        doc = jev_store.read_document(data/'requests.json')
        doc['worker_state'] = {'state': worker_state, 'next_attempt_epoch': refresh.time.time()+300,
                               'consecutive_failures': int(worker_state == 'retry_wait')}
        jev_store.write_document(data/'requests.json',doc)
        return 0
    monkeypatch.setattr(pipeline, 'run_pipeline', numeric)
    monkeypatch.setattr(news_incremental, 'collect_pending', collect)
    monkeypatch.setattr(refresh.news_backfill, 'run', classify)
    monkeypatch.setenv('AI_GATEWAY_API_KEY','fixture')
    return calls


def test_refresh_uses_all_numeric_sources_and_four_file_archive(monkeypatch,tmp_path):
    calls=setup_cycle(monkeypatch,tmp_path)
    result=refresh.run_cycle(tmp_path/'sources',tmp_path/'model',tmp_path/'jev',date(2014,7,1),end=date(2026,9,26))
    assert result['status']=='success'
    assert calls[0]==('numeric','incremental',date(2014,7,1),date(2026,9,26),{'database_url':None, 'jev_cache':tmp_path/'jev/responses.json'})
    assert [c[0] for c in calls]==['numeric','collect','classify']
    assert set(p.name for p in (tmp_path/'jev').iterdir())=={'news.json','requests.json','responses.json','sentiment.csv'}
    assert result['next_run_epoch']-refresh.time.time()==pytest.approx(refresh.WEEK,abs=1)


def test_numeric_failure_does_not_drop_news_and_is_secret_safe(monkeypatch,tmp_path):
    calls=setup_cycle(monkeypatch,tmp_path,numeric_failure=True)
    result=refresh.run_cycle(tmp_path/'sources',tmp_path/'model',tmp_path/'jev',date(2014,7,1))
    assert result['status']=='partial' and result['collection']=='success'
    assert result['next_run_epoch']-refresh.time.time()==pytest.approx(refresh.SOURCE_RETRY,abs=1)
    assert calls[-1][0]=='classify'
    assert 'secret' not in (tmp_path/'jev/requests.json').read_text()


def test_missing_gateway_key_collects_without_poisoning_worker_checkpoint(monkeypatch,tmp_path):
    calls=setup_cycle(monkeypatch,tmp_path)
    monkeypatch.delenv('AI_GATEWAY_API_KEY',raising=False)
    result=refresh.run_cycle(tmp_path/'sources',tmp_path/'model',tmp_path/'jev',date(2014,7,1))
    assert result['classification']=='not_configured'
    assert [c[0] for c in calls]==['numeric','collect']
    assert jev_store.read_document(tmp_path/'jev/requests.json')['worker_state']=={}


def test_stop_interrupts_gateway_wait_without_new_request(monkeypatch,tmp_path):
    calls=setup_cycle(monkeypatch,tmp_path,worker_state='waiting')
    class Stop(Event):
        def wait(self,timeout=None):
            assert 290 < timeout <= 300
            with refresh.source_lock(tmp_path/'sources'):
                pass
            self.set();return True
    result=refresh.run_cycle(tmp_path/'sources',tmp_path/'model',tmp_path/'jev',date(2014,7,1),stop=Stop())
    assert result['status']=='interrupted'
    assert len([c for c in calls if c[0]=='classify'])==1


def test_startup_runs_even_if_last_run_was_recent_then_waits_one_week(monkeypatch,tmp_path):
    calls=[]
    def cycle(*args,**kwargs):
        calls.append('cycle')
        return {'status':'success','next_run_epoch':refresh.time.time()+refresh.WEEK}
    monkeypatch.setattr(refresh,'run_cycle',cycle)
    class Stop(Event):
        def wait(self,timeout=None):
            assert refresh.WEEK-1 <= timeout <= refresh.WEEK
            self.set(); return True
    for _ in range(2):
        assert refresh.serve(tmp_path,tmp_path,tmp_path,date(2014,7,1),stop=Stop())==0
    assert calls==['cycle','cycle']


def test_competing_pipeline_fails_before_collecting(tmp_path):
    with refresh.source_lock(tmp_path):
        with pytest.raises(RuntimeError,match='already'):
            with refresh.source_lock(tmp_path):
                pass


def test_refresh_cli_dispatches_without_using_legacy_news_mode(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(refresh,'serve',lambda *args,**kwargs:calls.append((args,kwargs)) or 0)
    assert pipeline.main(['refresh','--once','--source-dir',str(tmp_path/'sources'),
                          '--jev-cache',str(tmp_path/'jev/responses.json')])==0
    assert calls[0][0][2]==tmp_path/'jev' and calls[0][1]['once']


def test_collected_article_reaches_raw_exchange_and_csv_then_restart_reuses_it(monkeypatch, tmp_path):
    import csv
    import json
    from coffee_service import news_incremental
    from test_news_backfill import Session

    data = tmp_path / 'jev'
    jev_store.initialize(data)
    document = jev_store.read_news(data)
    document['selection_metadata']['incremental'] = {'coverage_end': '2025-09-30'}
    jev_store.write_news(data, document)
    article = {'title': 'Brazil coffee crop falls 10 percent', 'url': 'https://example.test/crop',
               'published_at': '2025-10-01T12:00:00Z', 'source': 'google_news_rss'}
    class Frame:
        def to_json(self, **kwargs):
            return '[]'
    monkeypatch.setattr(news_incremental.news, 'fetch_wordpress', lambda *args: Frame())
    monkeypatch.setattr(news_incremental.sources, '_fetch_rss_range', lambda *args: [article])
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda *args, **kwargs: {'status': 'success'})
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'fixture')
    session = Session([200])
    run = refresh.news_backfill.run
    monkeypatch.setattr(refresh.news_backfill, 'run',
                        lambda path, **kwargs: run(path, transport=session, api_key='fixture', **kwargs))
    class NoSleep(Event):
        def wait(self, timeout=None):
            assert 290 < timeout <= 300
            return False
    for _ in range(2):
        result = refresh.run_cycle(tmp_path/'sources', tmp_path/'model', data,
                                   date(2014, 7, 1), end=date(2025, 10, 1), stop=NoSleep())
        assert result['status'] == 'success'
    assert session.calls == 1
    request = json.loads((data/'requests.json').read_text())['attempts'][0]
    response = json.loads((data/'responses.json').read_text())['attempts'][0]
    assert request['request_id'] == response['request_id']
    assert len(request['body']['questions']) == 2 and response['status'] == 200
    with (data/'sentiment.csv').open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]['selection_date'] == '2025-10-01'
    assert rows[0]['selected_for_research'] == 'True'
    assert rows[0]['available_at'] >= rows[0]['analyzed_at'] > rows[0]['research_available_at']
    assert {p.name for p in data.iterdir()} == {'news.json', 'requests.json', 'responses.json', 'sentiment.csv'}


def test_optional_numeric_failure_retries_in_one_hour(monkeypatch, tmp_path):
    setup_cycle(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda *args, **kwargs: {
        'status': 'success', 'source_failures': ['weather_br_sul_minas', 'cot']})
    result = refresh.run_cycle(tmp_path/'sources', tmp_path/'model', tmp_path/'jev', date(2014, 7, 1))
    assert result['numeric'] == 'partial' and result['status'] == 'partial'
    assert result['numeric_source_failures'] == ['weather_br_sul_minas', 'cot']
    assert result['next_run_epoch']-refresh.time.time() == pytest.approx(refresh.SOURCE_RETRY, abs=1)


def test_manual_news_cli_locks_numeric_phase_but_not_gateway_wait(monkeypatch, tmp_path):
    calls = []
    def numeric(*args, **kwargs):
        with pytest.raises(RuntimeError, match='already'):
            with refresh.source_lock(tmp_path):
                pass
        calls.append('numeric')
    def news(*args, **kwargs):
        with refresh.source_lock(tmp_path):
            calls.append('news')
        return {'status': 'success', 'run_id': 'fixture', 'price_rows': 0, 'prediction_rows': 0}
    monkeypatch.setattr(pipeline, 'run_pipeline', numeric)
    monkeypatch.setattr(pipeline, 'run_news_pipeline', news)
    assert pipeline.main(['news', '--source-dir', str(tmp_path), '--artifact', str(tmp_path/'legacy.pt')]) == 0
    assert calls == ['numeric', 'news']


@pytest.mark.parametrize('worker_state', ['stopped', 'retry_wait'])
def test_selected_forecast_follows_news_attempt_even_when_delayed(monkeypatch, tmp_path, worker_state):
    calls = setup_cycle(monkeypatch, tmp_path, worker_state=worker_state)
    state = refresh.run_cycle(tmp_path/'sources', tmp_path/'manifest.json', tmp_path/'jev',
                              date(2014, 7, 1), end=date(2026, 9, 26))
    assert [call[0] for call in calls] == ['collect', 'classify', 'numeric']
    assert state['numeric'] == 'success'
    assert state['classification'] == worker_state
    if worker_state == 'retry_wait':
        assert state['next_run_epoch']-refresh.time.time() == pytest.approx(300, abs=1)
