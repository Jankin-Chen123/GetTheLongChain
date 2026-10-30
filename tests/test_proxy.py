from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('GETTHELONGCHAIN_ACCESS_KEY', raising=False)
    with TestClient(server.app) as client:
        yield client


def transport(monkeypatch, *responses):
    session = MagicMock()
    session.__enter__.return_value = session
    session.headers = {}
    session.request.side_effect = responses
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(server.requests, 'Session', factory)
    return session, factory


def response(status, text=''):
    return MagicMock(status_code=status, text=text)


def test_proxy_success_without_account_credentials(client, monkeypatch, log_directory):
    session, factory = transport(monkeypatch, response(200, 'ip=203.0.113.2\nloc=CO\n'), response(401))
    data = client.post('/api/proxy-test', json={'proxy': 'proxy.test:1234:PRIVATEUSER:PRIVATEPASSWORD'}).json()
    assert data['passed'] is True
    assert data['exit_ip'] == '203.0.113.2'
    assert data['exit_country'] == 'CO'
    assert data['checks'][1]['state'] == 'connected'
    assert factory.call_args.kwargs['trust_env'] is False
    assert session.proxies['https'] == 'http://PRIVATEUSER:PRIVATEPASSWORD@proxy.test:1234'
    assert 'Authorization' not in session.headers
    assert all(call.args[0] == 'GET' and call.kwargs['allow_redirects'] is False for call in session.request.call_args_list)
    text = (log_directory / 'runtime.log').read_text(encoding='utf-8')
    assert 'PRIVATEPASSWORD' not in text and '203.0.113.2' not in text


@pytest.mark.parametrize('code,state', [(5, 'proxy_dns_failed'), (6, 'target_dns_failed'),
    (7, 'connection_failed'), (28, 'timeout'), (35, 'tls_failed'), (60, 'certificate_failed')])
def test_proxy_network_failures_are_classified(client, monkeypatch, code, state):
    error = server.requests.RequestsError('PRIVATEPASSWORD', code=code)
    session, _ = transport(monkeypatch, error, error)
    data = client.post('/api/proxy-test', json={'proxy': '127.0.0.1:7890'}).json()
    assert not data['passed']
    assert all(check['state'] == state for check in data['checks'])
    assert 'PRIVATEPASSWORD' not in str(data)
    assert session.request.call_count == 2


def test_proxy_auth_failure_and_chatgpt_block_are_not_confused(client, monkeypatch):
    transport(monkeypatch, response(407), response(403))
    data = client.post('/api/proxy-test', json={'proxy': '127.0.0.1:7890'}).json()
    assert not data['passed']
    assert data['checks'][0]['state'] == 'proxy_auth_failed'
    assert data['checks'][1]['reachable'] is True
    assert data['checks'][1]['state'] == 'http_rejected'


def test_proxy_tunnel_auth_exception(client, monkeypatch):
    error = server.requests.RequestsError('CONNECT failed 407 password PRIVATEPASSWORD', code=56)
    transport(monkeypatch, error, error)
    data = client.post('/api/proxy-test', json={'proxy': '127.0.0.1:7890'}).json()
    assert data['checks'][0]['state'] == 'proxy_auth_failed'
    assert 'PRIVATEPASSWORD' not in str(data)


@pytest.mark.parametrize('body', [{'proxy': ''}, {'proxy': '   '}, {'proxy': 'file:///etc/passwd'},
                                 {'proxy': '127.0.0.1:7890', 'token': 'private-token'}])
def test_invalid_proxy_stays_local(client, monkeypatch, body):
    session, _ = transport(monkeypatch)
    assert client.post('/api/proxy-test', json=body).status_code in {400, 422}
    session.request.assert_not_called()


def test_proxy_test_requires_configured_access_key(client, monkeypatch):
    monkeypatch.setenv('GETTHELONGCHAIN_ACCESS_KEY', 'private-access-key')
    assert client.post('/api/proxy-test', json={'proxy': '127.0.0.1:7890'}).status_code == 401


def test_discount_ineligible_explains_connection_succeeded(client, monkeypatch):
    upstream = response(400)
    upstream.json.return_value = {'detail': 'Discount code is not eligible'}
    transport(monkeypatch, upstream)
    data = client.post('/api/checkout', json={'token': 'valid.shape.token', 'country': 'CO', 'currency': 'COP'}).json()
    assert '已连接到 ChatGPT' in data['error']
    assert '清空优惠码' in data['error']

@pytest.mark.parametrize('raw, expected', [
    ('gate.kookeey.info:1000:demo-user:demo-pass', 'socks5://demo-user:demo-pass@gate.kookeey.info:1000'),
    ('gate.kookeey.info:1000:demo@user:demo/pass', 'socks5://demo%40user:demo%2Fpass@gate.kookeey.info:1000'),
    ('proxy.example.com:1000:demo-user:demo-pass', 'http://demo-user:demo-pass@proxy.example.com:1000'),
    ('fakekookeey.info:1000:demo-user:demo-pass', 'http://demo-user:demo-pass@fakekookeey.info:1000'),
    ('http://demo-user:demo-pass@gate.kookeey.info:1000', 'http://demo-user:demo-pass@gate.kookeey.info:1000'),
])
def test_tutorial_proxy_format_and_explicit_protocol(raw, expected):
    assert server.normalize_proxy(raw) == expected
