from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def location_client(monkeypatch):
    monkeypatch.setattr(server, 'LOCATION_CACHE', {'expires': 0, 'country_code': '', 'country': ''})
    with TestClient(server.app) as client:
        yield client


def mock_trace(monkeypatch, result):
    session = MagicMock()
    session.__enter__.return_value = session
    if isinstance(result, Exception):
        session.request.side_effect = result
    else:
        session.request.return_value = result
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(server.requests, 'Session', factory)
    return session, factory


def test_country_only_direct_detection_is_cached(location_client, monkeypatch, log_directory):
    session, factory = mock_trace(monkeypatch, MagicMock(status_code=200, text='ip=203.0.113.99\nloc=US\n'))
    data = location_client.get('/api/server-location').json()
    assert data == {'ok': True, 'detected': True, 'country_code': 'US', 'country': '美国'}
    assert location_client.get('/api/server-location').json() == data
    assert session.request.call_count == 1
    assert factory.call_args.kwargs['trust_env'] is False
    assert session.request.call_args.kwargs == {'timeout': 8, 'allow_redirects': False}
    assert '203.0.113.99' not in str(data)
    assert '203.0.113.99' not in (log_directory / 'runtime.log').read_text(encoding='utf-8')
    monkeypatch.setitem(server.LOCATION_CACHE, 'expires', 0)
    session.request.return_value = MagicMock(status_code=200, text='loc=JP\n')
    assert location_client.get('/api/server-location').json()['country'] == '日本'


@pytest.mark.parametrize('result', [
    MagicMock(status_code=200, text='ip=203.0.113.99\nloc=XX\n'),
    MagicMock(status_code=403, text='loc=US\n'),
    server.requests.RequestsError('private upstream error', code=28),
])
def test_location_failure_is_safe_and_cached(location_client, monkeypatch, result):
    session, _ = mock_trace(monkeypatch, result)
    expected = {'ok': True, 'detected': False, 'country_code': '', 'country': ''}
    assert location_client.get('/api/server-location').json() == expected
    assert location_client.get('/api/server-location').json() == expected
    assert session.request.call_count == 1


def test_health_does_not_wait_for_geolocation(location_client, monkeypatch):
    session, _ = mock_trace(monkeypatch, server.requests.RequestsError('timeout', code=28))
    assert location_client.get('/api/health').json()['ok'] is True
    session.request.assert_not_called()
