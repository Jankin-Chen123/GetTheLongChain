import json
import logging
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import app_logging
import server


def records(directory, filename='runtime.log'):
    return [json.loads(line) for line in (directory / filename).read_text(encoding='utf-8').splitlines()]


def test_request_ids_and_request_lifecycle(log_directory):
    with TestClient(server.app) as client:
        first = client.get('/api/health')
        second = client.get('/?token=SECRET-QUERY')
    log = records(log_directory)
    first_id = first.headers['X-Request-ID']
    assert first_id != second.headers['X-Request-ID']
    assert {entry['event'] for entry in log if entry['request_id'] == first_id} == {'request_start', 'request_end'}
    assert all(entry['time'].endswith('+08:00') for entry in log)
    assert (log_directory / 'error.log').read_text(encoding='utf-8') == ''
    assert 'SECRET-QUERY' not in (log_directory / 'runtime.log').read_text(encoding='utf-8')


def test_upstream_errors_correlate_without_private_values(log_directory, monkeypatch):
    secret_token = 'eyJtestpayload.eyJtestclaims.signature'
    secret_password = 'PRIVATEPROXYPASSWORD'
    session = MagicMock()
    session.headers = {}
    session.proxies = {}
    session.__enter__.return_value = session
    response = MagicMock(status_code=400)
    response.json.return_value = {'error': {'code': 'invalid_promo_code',
        'message': f'No promotion for PRIVATECOUPON in PRIVATEWORKSPACE using {secret_password} {secret_token}'}}
    session.request.return_value = response
    monkeypatch.setattr(server.requests, 'Session', MagicMock(return_value=session))
    with TestClient(server.app) as client:
        result = client.post('/api/checkout', json={'token': secret_token, 'country': 'CO', 'currency': 'COP',
            'workspaceName': 'PRIVATEWORKSPACE', 'promoCode': 'PRIVATECOUPON',
            'proxy': f'http://proxyuser:{secret_password}@privateproxy.example:1234'})
    request_id = result.headers['X-Request-ID']
    assert result.json()['request_id'] == request_id
    log = [entry for entry in records(log_directory) if entry['request_id'] == request_id]
    assert any(entry['event'] == 'upstream_response' and entry['status'] == 400 for entry in log)
    assert any(entry['event'] == 'checkout_parameters' and entry['country'] == 'CO' for entry in log)
    for filename in ['runtime.log', 'error.log']:
        text = (log_directory / filename).read_text(encoding='utf-8')
        assert 'invalid_promo_code' in text
        for private in [secret_token, secret_password, 'PRIVATECOUPON', 'PRIVATEWORKSPACE', 'privateproxy.example']:
            assert private not in text


def test_unexpected_errors_have_safe_stack_and_no_exception_text(log_directory, monkeypatch):
    def fail(_req):
        raise RuntimeError('VERY-PRIVATE-EXCEPTION-MESSAGE')
    monkeypatch.setattr(server, 'checkout_body', fail)
    with TestClient(server.app) as client:
        result = client.post('/api/checkout', json={'token': 'private.token.value', 'country': 'US', 'currency': 'USD'})
    assert result.status_code == 500
    log = records(log_directory, 'error.log')
    error = next(entry for entry in log if entry['event'] == 'unhandled_error')
    assert error['exception_type'] == 'RuntimeError'
    assert any(frame['function'] == 'fail' for frame in error['traceback'])
    assert 'VERY-PRIVATE' not in (log_directory / 'error.log').read_text(encoding='utf-8')


def test_validation_and_boundary_rejections_are_logged(log_directory):
    with TestClient(server.app) as client:
        bad = client.post('/api/checkout', json={'token': 'PRIVATE-INVALID-TOKEN'})
        blocked = client.post('/api/checkout', json={}, headers={'Origin': 'https://evil.example'})
    assert bad.status_code == 422
    assert blocked.status_code == 403
    log = records(log_directory, 'error.log')
    assert any(entry['event'] == 'validation_error' for entry in log)
    assert any(entry['event'] == 'request_end' and entry['status'] == 403 for entry in log)
    assert 'PRIVATE-INVALID-TOKEN' not in (log_directory / 'error.log').read_text(encoding='utf-8')


def test_log_rotation_is_bounded(log_directory):
    app_logging.configure_logs(log_directory, max_bytes=500, backups=2)
    for index in range(25):
        app_logging.event('rotation_test', level=logging.ERROR, iteration=index, message='x' * 120)
    assert len(list(log_directory.glob('runtime.log*'))) <= 3
    assert len(list(log_directory.glob('error.log*'))) <= 3
    assert records(log_directory)[-1]['iteration'] == 24
