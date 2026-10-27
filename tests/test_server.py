from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import server

TOKEN = 'eyJtestpayload.eyJtestclaims.signature'
WS_ID = 'b87687b6-64c9-42a9-9898-2cc52c3e0e03'
URL = 'https://checkout.stripe.com/c/pay/cs_test_123#secret'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('GETTHELONGCHAIN_ACCESS_KEY', raising=False)
    with TestClient(server.app) as client:
        yield client


def body(**changes):
    return {'token': TOKEN, 'country': 'US', 'currency': 'USD', **changes}


def response(data, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = data
    return mock


def mock_transport(monkeypatch, *responses):
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.side_effect = responses
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(server.requests, 'Session', factory)
    return session, factory


def test_new_team_checkout_payload_and_no_promo(client, monkeypatch):
    session, _ = mock_transport(monkeypatch, response({'url': URL}))
    result = client.post('/api/checkout', json=body(workspaceName='Study Team', seatQuantity=3))
    assert result.status_code == 200
    assert result.json()['url'] == URL
    sent = session.request.call_args.kwargs
    assert sent['json']['plan_name'] == 'chatgptteamplan'
    assert sent['json']['team_plan_data'] == {'workspace_name': 'Study Team', 'price_interval': 'month', 'seat_quantity': 3}
    assert 'promo_code' not in sent['json']
    assert sent['allow_redirects'] is False


def test_existing_workspace_and_promo(client, monkeypatch):
    session, _ = mock_transport(monkeypatch, response({'url': URL}))
    result = client.post('/api/checkout', json=body(spaceMode='existing', existingWorkspaceId=WS_ID,
                                                  priceInterval='year', promoCode='https://chatgpt.com/?promoCode=HELLO_2026'))
    assert result.status_code == 200
    sent = session.request.call_args.kwargs
    assert sent['headers']['chatgpt-account-id'] == WS_ID
    assert sent['json']['team_plan_data']['existing_workspace_id'] == WS_ID
    assert sent['json']['promo_code'] == 'HELLO_2026'


def test_stripe_fallback_isolates_bearer(client, monkeypatch):
    chat = MagicMock()
    stripe = MagicMock()
    chat.__enter__.return_value = chat
    stripe.__enter__.return_value = stripe
    chat.request.return_value = response({'checkout_session_id': 'cs_test_123', 'publishable_key': 'pk_test_123'})
    stripe.request.return_value = response({'stripe_hosted_url': URL})
    monkeypatch.setattr(server.requests, 'Session', MagicMock(side_effect=[chat, stripe]))
    result = client.post('/api/checkout', json=body())
    assert result.status_code == 200
    assert chat.headers.update.call_args.args[0]['Authorization'] == f'Bearer {TOKEN}'
    stripe.headers.update.assert_not_called()
    assert 'headers' not in stripe.request.call_args.kwargs
    assert stripe.request.call_args.args[1].startswith('https://api.stripe.com/')


@pytest.mark.parametrize('status', [400, 401, 403, 409, 429, 500, 302])
def test_upstream_errors_no_retry_or_raw_leak(client, monkeypatch, status):
    session, _ = mock_transport(monkeypatch, response({'token': TOKEN}, status))
    result = client.post('/api/checkout', json=body())
    assert result.status_code == (status if status in {400, 401, 403, 409, 429} else 502)
    assert TOKEN not in result.text
    assert session.request.call_count == 1


@pytest.mark.parametrize('changes', [
    {'seatQuantity': 1}, {'seatQuantity': 2.5}, {'seatQuantity': '2'},
    {'country': 'ZZ', 'currency': 'USD'}, {'currency': 'ZZZ'}, {'spaceMode': 'existing', 'existingWorkspaceId': 'bad-id'},
    {'token': '{"sessionToken":"SECRET-SENTINEL"}'}, {'token': {'SECRET-SENTINEL': 1}},
    {'token': 'not-a-token'}, {'proxy': 'file:///etc/passwd'}, {'promoCode': '<script>'},
])
def test_bad_input_stays_local_and_redacted(client, monkeypatch, changes):
    session, factory = mock_transport(monkeypatch)
    result = client.post('/api/checkout', json=body(**changes))
    assert result.status_code in {400, 422}
    assert TOKEN not in result.text
    assert 'SECRET-SENTINEL' not in result.text
    session.request.assert_not_called()


@pytest.mark.parametrize('url', [
    'https://checkout.stripe.com.evil.example/pay', 'http://pay.openai.com/pay',
    'https://evil.example/?url=https://pay.openai.com', 'https://user:pass@pay.openai.com/pay',
    'https://pay.openai.com:8443/pay', 'javascript:alert(1)', 'https://checkout.stripe.com\\@evil.example/',
])
def test_untrusted_urls_are_rejected(client, monkeypatch, url):
    mock_transport(monkeypatch, response({'url': url}))
    result = client.post('/api/checkout', json=body())
    assert result.status_code == 502
    assert 'url' not in result.json()


def test_missing_url_does_not_fabricate_or_retry(client, monkeypatch):
    session, _ = mock_transport(monkeypatch, response({'checkout_session_id': 'cs_test_123'}))
    assert client.post('/api/checkout', json=body()).status_code == 502
    assert session.request.call_count == 1


def test_workspaces_filter_personal_accounts(client, monkeypatch):
    mock_transport(monkeypatch, response({'accounts': {
        'personal': {'account': {'structure': 'personal', 'plan_type': 'plus'}},
        WS_ID: {'account': {'account_id': WS_ID, 'name': 'Study', 'plan_type': 'team', 'account_user_role': 'owner'}},
    }}))
    result = client.post('/api/workspaces', json={'token': TOKEN})
    assert result.json()['workspaces'] == [{'id': WS_ID, 'name': 'Study', 'role': 'owner'}]


def test_auth_origin_host_and_security_headers(client, monkeypatch):
    monkeypatch.setenv('GETTHELONGCHAIN_ACCESS_KEY', 'private-key')
    assert client.post('/api/checkout', json=body()).status_code == 401
    assert client.post('/api/checkout', json=body(), headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/health', headers={'Host': 'evil.example'}).status_code == 403
    assert client.post('/api/checkout', content='not json').status_code == 415
    assert client.get('/').headers['Cache-Control'] == 'no-store'
    assert "frame-ancestors 'none'" in client.get('/').headers['Content-Security-Policy']


def test_concurrent_checkout_is_rejected(client):
    server.CHECKOUT_LOCK.acquire()
    try:
        assert client.post('/api/checkout', json=body()).status_code == 429
    finally:
        server.CHECKOUT_LOCK.release()


def test_token_and_proxy_normalization():
    assert server.normalize_token('Bearer ' + TOKEN) == TOKEN
    assert server.normalize_token('{"accessToken":"' + TOKEN + '"}') == TOKEN
    assert server.normalize_proxy('127.0.0.1:7890') == 'http://127.0.0.1:7890'
    assert server.normalize_proxy('host.test:1234:user:pa@ss') == 'http://user:pa%40ss@host.test:1234'


def test_transport_failure_does_not_leak_proxy(client, monkeypatch):
    mock_transport(monkeypatch, server.requests.RequestsError('proxy password SECRET-SENTINEL'))
    result = client.post('/api/checkout', json=body())
    assert result.status_code == 502
    assert 'SECRET-SENTINEL' not in result.text


def test_full_country_and_currency_catalog(client):
    health = client.get('/api/health').json()
    countries = {code: currency for code, currency, _ in health['countries']}
    currencies = {code for code, _ in health['currencies']}
    assert len(countries) == 250
    assert countries['CN'] == 'CNY'
    assert countries['BG'] == 'EUR'
    assert countries['KE'] == 'KES'
    assert countries['AQ'] == ''
    assert all(not currency or currency in currencies for currency in countries.values())


@pytest.mark.parametrize('country,currency', [('CN', 'CNY'), ('KE', 'KES'), ('IS', 'ISK'), ('AQ', 'USD'), ('US', 'EUR')])
def test_extended_regions_and_manual_currency(client, monkeypatch, country, currency):
    session, _ = mock_transport(monkeypatch, response({'url': URL}))
    result = client.post('/api/checkout', json=body(country=country, currency=currency))
    assert result.status_code == 200
    assert session.request.call_args.kwargs['json']['billing_details'] == {'country': country, 'currency': currency}


def test_upstream_detailed_error_is_visible_and_redacted(client, monkeypatch):
    private_name = 'MyPrivateWorkspace'
    private_promo = 'PRIVATE_PROMO'
    private_password = 'PROXY-PASSWORD-SECRET'
    upstream = {'error': {'code': 'invalid_promo_code', 'message':
        f'Promotion invalid for {private_name}: {private_promo}; Bearer {TOKEN}; '
        f'proxy {private_password}; user@example.com'}, 'input': {'token': TOKEN}}
    session, _ = mock_transport(monkeypatch, response(upstream, 400))
    # The mock dict must retain the headers like a real requests session does.
    session.headers = {}
    session.proxies = {}
    result = client.post('/api/checkout', json=body(workspaceName=private_name, promoCode=private_promo,
                                                  proxy=f'http://user:{private_password}@127.0.0.1:7890'))
    assert result.status_code == 400
    assert 'invalid_promo_code' in result.json()['error']
    assert result.json()['details']['upstream_status'] == 400
    for private in [TOKEN, private_name, private_promo, private_password, 'user@example.com']:
        assert private not in result.text
    assert session.request.call_count == 1


def test_upstream_validation_error_does_not_echo_input(client, monkeypatch):
    session, _ = mock_transport(monkeypatch, response({'detail': [
        {'type': 'invalid_enum', 'msg': 'Unsupported currency COP', 'input': TOKEN},
    ]}, 400))
    result = client.post('/api/checkout', json=body())
    assert 'Unsupported currency COP' in result.json()['error']
    assert TOKEN not in result.text


def test_non_json_upstream_error_remains_generic(client, monkeypatch):
    mock_response = response({}, 400)
    mock_response.json.side_effect = ValueError('not json')
    mock_transport(monkeypatch, mock_response)
    result = client.post('/api/checkout', json=body())
    assert result.status_code == 400
    assert 'details' not in result.json()


@pytest.mark.parametrize('promo', [
    'EXAMPLE_CODE_2026',
    'https://chatgpt.com/p/EXAMPLE_CODE_2026',
    'https://chatgpt.com/p/EXAMPLE_CODE_2026/?source=test#offer',
    'chatgpt.com/p/EXAMPLE_CODE_2026',
    'chatgpt.com/?promoCode=EXAMPLE_CODE_2026',
    'https://chatgpt.com/?promoCode=EXAMPLE_CODE_2026',
    'www.chatgpt.com/?promoCode=EXAMPLE_CODE_2026',
    'https://chatgpt.com/?PROMOCODE=EXAMPLE_CODE_2026',
    'https://chatgpt.com/promo/EXAMPLE_CODE_2026',
    '//chatgpt.com/p/EXAMPLE_CODE_2026',
    '  https://chatgpt.com/p/EXAMPLE_CODE_2026  ',
])
def test_promo_links_forward_only_extracted_code(client, monkeypatch, promo):
    session, _ = mock_transport(monkeypatch, response({'url': URL}))
    result = client.post('/api/checkout', json=body(promoCode=promo))
    assert result.status_code == 200
    assert session.request.call_args.kwargs['json']['promo_code'] == 'EXAMPLE_CODE_2026'


@pytest.mark.parametrize('promo', ['https://chatgpt.com/p/', 'https://chatgpt.com/?promoCode=',
                                  'https://chatgpt.com/p/first/second', 'https://chatgpt.com/p/a%2Fb'])
def test_invalid_promo_links_stay_local(client, monkeypatch, promo):
    session, _ = mock_transport(monkeypatch)
    assert client.post('/api/checkout', json=body(promoCode=promo)).status_code == 400
    session.request.assert_not_called()
