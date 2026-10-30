"""Local, single-user Team checkout tool. Never confirms or submits payment."""

import hmac
import ipaddress
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, quote, unquote, urlsplit

from curl_cffi import requests
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app_logging import REQUEST_CONTEXT, event, remember_proxy, remember_secrets, safe_traceback, set_stage

ROOT = Path(__file__).resolve().parent
COUNTRY_DATA = json.loads((ROOT / 'data' / 'countries.json').read_text(encoding='utf-8'))
COUNTRIES = COUNTRY_DATA['countries']
CURRENCIES = COUNTRY_DATA['currencies']
CURRENCY_MAP = {code: currency for code, currency, _ in COUNTRIES}
CURRENCY_CODES = {code for code, _ in CURRENCIES}
TRUSTED_HOSTS = {'checkout.stripe.com', 'pay.openai.com', 'chatgpt.com'}
UPSTREAM = 'https://chatgpt.com'
CHECKOUT_LOCK = threading.Lock()
LOCATION_LOCK = threading.Lock()
LOCATION_CACHE = {'expires': 0, 'country_code': '', 'country': ''}


class ToolError(Exception):
    def __init__(self, message: str, status: int = 400, stage: str = 'validation', details: dict | None = None):
        self.message, self.status, self.stage = message, status, stage
        self.details = details


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: SecretStr = Field(min_length=1, max_length=65536)
    proxy: SecretStr = Field(default=SecretStr(''), max_length=2048)


class ProxyTestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    proxy: SecretStr = Field(min_length=1, max_length=2048)


class CheckoutInput(Credentials):
    spaceMode: Literal['new', 'existing'] = 'new'
    workspaceName: str = Field(default='My Team', min_length=1, max_length=100)
    existingWorkspaceId: str = Field(default='', max_length=36)
    seatQuantity: int = Field(default=2, ge=2, le=999, strict=True)
    country: str = Field(min_length=2, max_length=2)
    currency: str = Field(min_length=3, max_length=3)
    priceInterval: Literal['month', 'year'] = 'month'
    promoCode: str = Field(default='', max_length=2048)

    @field_validator('country', 'currency')
    @classmethod
    def uppercase(cls, value: str) -> str:
        return value.upper()


def normalize_token(value: str) -> str:
    value = value.strip()
    if value.startswith('{'):
        try:
            obj = json.loads(value)
        except ValueError:
            raise ToolError('Session JSON 格式不正确。') from None
        value = next((obj[k] for k in ('accessToken', 'access_token', 'token')
                      if isinstance(obj.get(k), str) and obj[k].strip()), '')
    if value.lower().startswith('bearer '):
        value = value[7:].strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', value):
        raise ToolError('未识别到 accessToken，请粘贴完整 JWT 或含 accessToken 的 Session JSON。')
    return value


def normalize_proxy(value: str) -> str:
    value = value.strip()
    if not value:
        return ''
    if '://' not in value:
        parts = value.split(':')
        if len(parts) == 4:
            host, port, user, password = parts
            # The Kookeey SOCKS5 tutorial exports host:port:user:password.
            # Explicit URL schemes always take precedence; other bare providers keep HTTP.
            provider = host.lower().rstrip('.')
            scheme = 'socks5' if provider == 'kookeey.info' or provider.endswith('.kookeey.info') else 'http'
            value = f'{scheme}://{quote(user, safe="")}:{quote(password, safe="")}@{host}:{port}'
        else:
            value = 'http://' + value
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {'http', 'https', 'socks5', 'socks5h', 'socks4', 'socks4a'}
                 and parsed.hostname and parsed.port and parsed.path in ('', '/')
                 and not parsed.query and not parsed.fragment
                 and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise ToolError('代理格式不正确。示例：http://127.0.0.1:7890 或 host:port:user:pass。')
    return value


def promo_code(value: str) -> str:
    value = value.strip()
    if re.match(r'^(?:www\.)?chatgpt\.com(?:[/?#]|$)', value, re.I):
        value = 'https://' + value
    elif value.startswith('//'):
        value = 'https:' + value
    if re.match(r'^https?://', value, re.I):
        try:
            parsed = urlsplit(value)
            params = {key.casefold(): entries for key, entries in parse_qs(parsed.query).items()}
        except ValueError:
            raise ToolError('优惠链接格式不正确，请检查链接或直接填写优惠码。') from None
        value = next((params[key][0] for key in ('promocode', 'promo_code', 'coupon') if params.get(key)), '')
        if not value:
            path_match = re.fullmatch(r'/(?:p|promo)/([^/]+)/?', parsed.path)
            if path_match:
                value = unquote(path_match[1])
        if not value:
            raise ToolError('优惠链接中没有识别到优惠码，请直接填写优惠码。')
    if value and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value):
        raise ToolError('优惠码只能包含字母、数字、横线和下划线。')
    return value


def trusted_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
        return (parts.scheme == 'https' and parts.hostname in TRUSTED_HOSTS
                and parts.port in (None, 443) and not parts.username and not parts.password
                and not any(ord(c) < 33 or ord(c) > 126 for c in value) and '\\' not in value)
    except ValueError:
        return False


def checkout_body(req: CheckoutInput) -> dict:
    if req.country not in CURRENCY_MAP:
        raise ToolError('国家／地区代码无效，请重新选择。')
    if req.currency not in CURRENCY_CODES:
        raise ToolError('货币代码无效，请重新选择。')
    team = {'workspace_name': req.workspaceName.strip(), 'price_interval': req.priceInterval,
            'seat_quantity': req.seatQuantity}
    if not team['workspace_name']:
        raise ToolError('请填写工作区名称。')
    if req.spaceMode == 'existing':
        try:
            team['existing_workspace_id'] = str(uuid.UUID(req.existingWorkspaceId))
        except ValueError:
            raise ToolError('已有工作区 ID 必须是有效 UUID。') from None
    elif req.existingWorkspaceId:
        raise ToolError('新建工作区时请清空已有工作区 ID。')
    body = {'entry_point': 'team_workspace_purchase_modal', 'plan_name': 'chatgptteamplan',
            'checkout_ui_mode': 'hosted', 'team_plan_data': team,
            'billing_details': {'country': req.country, 'currency': req.currency},
            'cancel_url': 'https://chatgpt.com/#team-pricing-seat-selection'}
    code = promo_code(req.promoCode)
    if code:
        body['promo_code'] = code
    return body


def new_session(req: Credentials):
    set_stage('session')
    token = normalize_token(req.token.get_secret_value())
    proxy = normalize_proxy(req.proxy.get_secret_value())
    remember_secrets(token)
    remember_proxy(proxy)
    session = requests.Session(impersonate='chrome', trust_env=False)
    session.headers.update({'Authorization': f'Bearer {token}', 'Origin': UPSTREAM,
                            'Referer': UPSTREAM + '/', 'Accept': 'application/json',
                            'oai-device-id': str(uuid.uuid4()), 'oai-language': 'zh-CN'})
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
    return session


def upstream_error_details(response, session, request_body: dict | None = None) -> dict:
    """Expose only error fields, removing credentials before truncating anything."""
    secrets = []
    for key, value in session.headers.items():
        if key.lower() in {'authorization', 'cookie', 'chatgpt-account-id'} and isinstance(value, str):
            secrets.extend([value, value.removeprefix('Bearer ')])
    for proxy in session.proxies.values():
        if isinstance(proxy, str):
            secrets.append(proxy)
            try:
                parsed = urlsplit(proxy)
                secrets.extend([unquote(parsed.username or ''), unquote(parsed.password or '')])
            except ValueError:
                pass
    def collect_private_values(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {'promo_code', 'workspace_name', 'existing_workspace_id'} and isinstance(value, str):
                    secrets.append(value)
                elif isinstance(value, dict):
                    collect_private_values(value)
    collect_private_values(request_body)

    def safe_text(text):
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'(?i)Bearer\s+\S+', '[REDACTED]', text)
        text = re.sub(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED]', text)
        text = re.sub(r'(?i)(?:https?|socks\w*)://\S+', '[URL REDACTED]', text)
        text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL REDACTED]', text)
        text = re.sub(r'\b[A-Za-z0-9_-]{30,}\b', '[REDACTED]', text)
        return ''.join(c for c in text if c.isprintable() or c in '\n\t')[:600]

    try:
        payload = response.json()
    except ValueError:
        return {}
    fields = []
    def walk(obj, depth=0):
        if depth > 5 or len(fields) >= 6:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {'message', 'code', 'error_code', 'param', 'msg', 'type', 'error', 'detail', 'errors'}:
                    if isinstance(value, str):
                        fields.append({'field': key, 'text': safe_text(value)})
                    elif isinstance(value, (dict, list)):
                        walk(value, depth + 1)
                    if len(fields) >= 6:
                        break
        elif isinstance(obj, list):
            for value in obj[:6]:
                walk(value, depth + 1)
    walk(payload)
    return {'upstream_status': response.status_code, 'errors': fields[:6]} if fields else {}


def upstream_json(session, method: str, url: str, stage: str, **kwargs) -> dict:
    set_stage(stage)
    started = time.perf_counter()
    target = 'stripe' if url.startswith('https://api.stripe.com/') else 'chatgpt'
    event('upstream_start', stage=stage, target=target, method=method)
    try:
        response = session.request(method, url, timeout=35, allow_redirects=False, **kwargs)
    except requests.RequestsError as exc:
        event('upstream_connection_error', level=logging.ERROR, stage=stage, target=target,
              duration_ms=round((time.perf_counter() - started) * 1000), exception_type=type(exc).__name__)
        raise ToolError('连接上游失败或超时，请检查网络与代理。请求未自动重试。', 502, stage) from None
    status = response.status_code
    event('upstream_response', stage=stage, target=target, status=status,
          duration_ms=round((time.perf_counter() - started) * 1000))
    if not 200 <= status < 300:
        messages = {400: '上游拒绝了参数，请检查账单地区、优惠码或已有工作区状态。',
                    401: '账号 accessToken 已失效，请重新获取 Session。',
                    403: '上游拒绝访问，请检查账号权限及代理出口。',
                    409: '当前账号或工作区状态冲突，请在 ChatGPT 中确认订阅状态。',
                    429: '上游请求过于频繁，请稍后再试。'}
        message = messages.get(status, '上游服务返回异常，请稍后再试。')
        details = upstream_error_details(response, session, kwargs.get('json'))
        excerpt = '；'.join(item['text'] for item in details.get('errors', []) if item['field'] in {'message', 'msg', 'detail', 'error', 'code', 'error_code'})
        if 'discount code is not eligible' in excerpt.lower():
            message = '已连接到 ChatGPT，但当前优惠码不符合使用条件。请核对优惠资格，或清空优惠码后重试。'
        suffix = f' 具体原因：{excerpt[:600]}' if excerpt else ''
        raise ToolError(f'{message}（上游 HTTP {status}）{suffix}', status if status in messages else 502, stage, details)
    try:
        payload = response.json()
    except ValueError:
        raise ToolError('上游未返回 JSON，可能需要浏览器验证或接口已变化。', 502, stage) from None
    if not isinstance(payload, dict):
        raise ToolError('上游响应结构发生变化。', 502, stage)
    return payload


def extract_checkout_url(payload: dict, proxy: str) -> str:
    for key in ('url', 'checkout_url', 'stripe_hosted_url'):
        if isinstance(payload.get(key), str) and payload[key]:
            return payload[key]
    session_id = payload.get('checkout_session_id') or payload.get('session_id')
    key = payload.get('publishable_key')
    if not (isinstance(session_id, str) and re.fullmatch(r'cs_[A-Za-z0-9_]+', session_id)
            and isinstance(key, str) and key.startswith('pk_')):
        raise ToolError('上游未返回完整结账链接或 Stripe 初始化参数；请勿手动拼接链接。', 502, 'checkout_url')
    # Use a separate session so the ChatGPT bearer token is never sent to Stripe.
    with requests.Session(impersonate='chrome', trust_env=False) as stripe:
        if proxy:
            stripe.proxies = {'http': proxy, 'https': proxy}
        data = upstream_json(stripe, 'POST', f'https://api.stripe.com/v1/payment_pages/{session_id}/init',
                             'stripe_init', data={'key': key, 'browser_locale': 'zh-CN',
                                                  'browser_timezone': 'Asia/Shanghai',
                                                  'redirect_type': 'url'})
    url = data.get('stripe_hosted_url') or data.get('url')
    if not isinstance(url, str) or not url:
        raise ToolError('Stripe 没有返回托管结账链接，接口可能已变化。', 502, 'stripe_init')
    return url


def workspace_list(payload: dict) -> list[dict]:
    accounts = payload.get('accounts')
    if not isinstance(accounts, dict):
        raise ToolError('工作区响应结构发生变化。', 502, 'workspaces')
    items = []
    for key, value in accounts.items():
        if not isinstance(value, dict) or not isinstance(value.get('account'), dict):
            continue
        account = value['account']
        if account.get('is_personal_account') is True or account.get('structure') == 'personal':
            continue
        if account.get('plan_type') not in {'team', 'business', 'chatgptteamplan'} and account.get('structure') != 'workspace':
            continue
        identifier = account.get('account_id') or key
        try:
            identifier = str(uuid.UUID(identifier))
        except (ValueError, TypeError, AttributeError):
            continue
        items.append({'id': identifier, 'name': str(account.get('name') or '未命名工作区'),
                      'role': str(account.get('account_user_role') or account.get('role') or '')})
    return items


def authorize(request: Request):
    expected = os.getenv('GETTHELONGCHAIN_ACCESS_KEY', '')
    provided = request.headers.get('X-Access-Key', '')
    if expected and not hmac.compare_digest(expected.encode(), provided.encode()):
        raise ToolError('访问口令不正确。', 401, 'auth')


@asynccontextmanager
async def lifespan(_app):
    event('service_started', country_count=len(COUNTRIES), currency_count=len(CURRENCIES))
    try:
        yield
    finally:
        event('service_stopped')


app = FastAPI(title='GetTheLongChain', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


async def request_logging(request: Request, call_next):
    request_id = str(uuid.uuid4())
    context_token = REQUEST_CONTEXT.set({'request_id': request_id, 'stage': 'request', 'secrets': []})
    request.state.request_id = request_id
    remember_secrets(request.headers.get('X-Access-Key'))
    # Never record query strings, headers, bodies, or arbitrary URL paths.
    known_paths = {'/', '/api/health', '/api/checkout', '/api/workspaces', '/api/proxy-test',
                   '/api/server-location', '/static/app.js', '/static/style.css', '/static/workspace.css'}
    path = request.url.path if request.url.path in known_paths else '[other]'
    started = time.perf_counter()
    event('request_start', method=request.method, path=path)
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            event('unhandled_error', level=logging.ERROR, stage=REQUEST_CONTEXT.get()['stage'],
                  exception_type=type(exc).__name__, traceback=safe_traceback(exc))
            response = JSONResponse({'ok': False, 'error': '服务内部出错，已记录到本地报错日志。',
                                     'request_id': request_id}, status_code=500)
        response.headers['X-Request-ID'] = request_id
        event('request_end', level=logging.ERROR if response.status_code >= 400 else logging.INFO,
              method=request.method, path=path, status=response.status_code,
              stage=REQUEST_CONTEXT.get()['stage'], duration_ms=round((time.perf_counter() - started) * 1000))
        return response
    finally:
        REQUEST_CONTEXT.reset(context_token)


@app.middleware('http')
async def local_boundary(request: Request, call_next):
    # Block cross-site requests and DNS rebinding even on a local-only service.
    host = request.url.hostname
    allowed = {'127.0.0.1', 'localhost', '::1', 'testserver'}
    if host not in allowed:
        return JSONResponse({'ok': False, 'error': '此版本仅支持本机访问。'}, status_code=403)
    origin = request.headers.get('origin')
    if request.method not in {'GET', 'HEAD'} and origin and origin != str(request.base_url).rstrip('/'):
        return JSONResponse({'ok': False, 'error': '不允许跨站提交。'}, status_code=403)
    if request.method == 'POST' and request.headers.get('content-type', '').split(';')[0] != 'application/json':
        return JSONResponse({'ok': False, 'error': '请求必须为 JSON。'}, status_code=415)
    response = await call_next(request)
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                             'Content-Security-Policy': "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"})
    return response


app.middleware('http')(request_logging)


@app.exception_handler(ToolError)
async def tool_error(_request, exc):
    request_id = getattr(_request.state, 'request_id', None)
    event('request_error', level=logging.ERROR, stage=exc.stage, status=exc.status,
          message=exc.message, details=exc.details)
    payload = {'ok': False, 'error': exc.message, 'stage': exc.stage, 'request_id': request_id}
    if exc.details:
        payload['details'] = exc.details
    return JSONResponse(payload, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, _exc):
    # Pydantic's default error body can include the original credentials.
    fields = sorted({str(part) for error in _exc.errors() for part in error.get('loc', ())
                     if part in CheckoutInput.model_fields})
    event('validation_error', level=logging.ERROR, stage='validation', status=422,
          error_count=len(_exc.errors()), fields=fields)
    return JSONResponse({'ok': False, 'error': '参数不正确，请检查必填项、席位数和字段长度。',
                         'stage': 'validation', 'request_id': getattr(_request.state, 'request_id', None)}, status_code=422)


@app.get('/')
def index():
    return FileResponse(ROOT / 'public' / 'index.html')


@app.get('/api/health')
def health():
    return {'ok': True, 'requireKey': bool(os.getenv('GETTHELONGCHAIN_ACCESS_KEY')),
            'countries': COUNTRIES, 'currencies': CURRENCIES}


@app.get('/api/server-location')
def server_location():
    # Use the same direct network settings as checkout without a proxy.
    # Only retain the country; never expose or log the trace body or exit IP.
    with LOCATION_LOCK:
        if time.monotonic() >= LOCATION_CACHE['expires']:
            code = ''
            try:
                with requests.Session(impersonate='chrome', trust_env=False) as session:
                    response = session.request('GET', 'https://www.cloudflare.com/cdn-cgi/trace',
                                               timeout=8, allow_redirects=False)
                    if response.status_code == 200:
                        trace = dict(line.split('=', 1) for line in response.text.splitlines() if '=' in line)
                        candidate = trace.get('loc', '').strip().upper()
                        if candidate in CURRENCY_MAP:
                            code = candidate
            except requests.RequestsError:
                pass
            country = next((name for country_code, _, name in COUNTRIES if country_code == code), '')
            LOCATION_CACHE.update(country_code=code, country=country,
                                  expires=time.monotonic() + (900 if code else 60))
        return {'ok': True, 'detected': bool(LOCATION_CACHE['country_code']),
                'country_code': LOCATION_CACHE['country_code'], 'country': LOCATION_CACHE['country']}


@app.post('/api/proxy-test', dependencies=[Depends(authorize)])
def proxy_test(req: ProxyTestInput):
    remember_secrets(req.proxy.get_secret_value())
    proxy = normalize_proxy(req.proxy.get_secret_value())
    if not proxy:
        raise ToolError('请先填写需要测试的代理地址。', stage='proxy_test')
    remember_proxy(proxy)
    set_stage('proxy_test')
    checks = []
    exit_ip = ''
    exit_country = ''
    targets = [('internet', '互联网出口', 'https://www.cloudflare.com/cdn-cgi/trace'),
               ('chatgpt', 'ChatGPT 连通性', 'https://chatgpt.com/api/auth/session')]
    # Never send the account token or checkout parameters in a proxy test.
    with requests.Session(impersonate='chrome', trust_env=False) as session:
        session.proxies = {'http': proxy, 'https': proxy}
        for target, label, url in targets:
            started = time.perf_counter()
            event('proxy_probe_start', target=target)
            check = {'target': target, 'label': label}
            try:
                response = session.request('GET', url, timeout=12, allow_redirects=False)
                code = response.status_code
                check['status'] = code
                if code == 407:
                    check.update(reachable=False, state='proxy_auth_failed', message='代理认证失败，请检查代理用户名和密码。')
                elif 200 <= code < 300 or (target == 'chatgpt' and code == 401):
                    check.update(reachable=True, state='connected', message='已通过代理连接成功。')
                    if target == 'chatgpt':
                        check['message'] = '已连接 ChatGPT；无登录凭证测试返回 401 属于正常结果，不代表账号 Token 失效。' if code == 401 else '已连接 ChatGPT；此测试不验证账号或优惠码。'
                    else:
                        trace = dict(line.split('=', 1) for line in response.text.splitlines() if '=' in line)
                        try:
                            exit_ip = str(ipaddress.ip_address(trace.get('ip', '')))
                        except ValueError:
                            pass
                        if re.fullmatch('[A-Z]{2}', trace.get('loc', '')):
                            exit_country = trace['loc']
                else:
                    check.update(reachable=True, state='http_rejected',
                                 message=f'收到 HTTP {code}，代理路径有响应，但目标访问未通过。403 可能需要浏览器验证，不能据此认定代理连接失败。' if code == 403 else f'代理路径收到 HTTP {code}，目标访问未通过。')
            except requests.RequestsError as exc:
                curl_code = getattr(exc, 'code', None)
                if not isinstance(curl_code, int):
                    curl_code = None
                messages = {5: ('proxy_dns_failed', '无法解析代理服务器地址。'),
                            6: ('target_dns_failed', '目标域名解析失败，请检查代理的 DNS 配置。'),
                            7: ('connection_failed', '无法连接代理服务器，请检查地址、端口及本机网络。'),
                            28: ('timeout', '连接超时，请检查代理是否可用。'),
                            35: ('tls_failed', 'TLS 握手失败，请检查代理协议和网络。'),
                            60: ('certificate_failed', '证书校验失败，请检查代理证书配置。')}
                state, message = messages.get(curl_code, ('connection_failed', '代理连接或隧道建立失败，请检查地址、协议和认证信息。'))
                if re.search(r'\b407\b', str(exc)):
                    state, message = 'proxy_auth_failed', '代理认证失败，请检查代理用户名和密码。'
                check.update(reachable=False, state=state, message=message, network_code=curl_code)
            check['duration_ms'] = round((time.perf_counter() - started) * 1000)
            checks.append(check)
            event('proxy_probe_result', level=logging.INFO if check['state'] == 'connected' else logging.ERROR,
                  **{key: check[key] for key in ('target', 'status', 'state', 'duration_ms', 'network_code') if key in check})
    all_passed = all(check['state'] == 'connected' for check in checks)
    return {'ok': True, 'passed': all_passed, 'checks': checks, 'exit_ip': exit_ip,
            'exit_country': exit_country, 'message': '代理测试通过。' if all_passed else '代理测试存在未通过项，请查看各项结果。'}


@app.post('/api/checkout', dependencies=[Depends(authorize)])
def checkout(req: CheckoutInput):
    remember_secrets(req.token.get_secret_value(), req.proxy.get_secret_value(), req.promoCode,
                     req.workspaceName, req.existingWorkspaceId)
    set_stage('validation')
    event('checkout_parameters', country=req.country, currency=req.currency, seats=req.seatQuantity,
          interval=req.priceInterval, space_mode=req.spaceMode,
          has_promo=bool(req.promoCode), has_proxy=bool(req.proxy.get_secret_value()))
    body = checkout_body(req)
    remember_secrets(body.get('promo_code'))
    if not CHECKOUT_LOCK.acquire(blocking=False):
        raise ToolError('已有提链请求正在执行，请等待完成。', 429, 'checkout')
    try:
        with new_session(req) as session:
            headers = {}
            if req.spaceMode == 'existing':
                headers['chatgpt-account-id'] = body['team_plan_data']['existing_workspace_id']
            result = upstream_json(session, 'POST', UPSTREAM + '/backend-api/payments/checkout',
                                   'checkout', json=body, headers=headers)
        url = extract_checkout_url(result, normalize_proxy(req.proxy.get_secret_value()))
        set_stage('checkout_url')
        if not trusted_url(url):
            raise ToolError('上游返回的链接不属于允许的 HTTPS 域名，已停止展示。', 502, 'checkout_url')
        event('checkout_success')
        return {'ok': True, 'url': url, 'trusted': True, 'country': req.country, 'currency': req.currency}
    finally:
        CHECKOUT_LOCK.release()


@app.post('/api/workspaces', dependencies=[Depends(authorize)])
def workspaces(req: Credentials):
    remember_secrets(req.token.get_secret_value(), req.proxy.get_secret_value())
    with new_session(req) as session:
        data = upstream_json(session, 'GET', UPSTREAM + '/backend-api/accounts/check/v4-2023-04-27',
                             'workspaces', params={'timezone_offset_min': -480})
    items = workspace_list(data)
    event('workspaces_success', count=len(items))
    return {'ok': True, 'workspaces': items}


app.mount('/static', StaticFiles(directory=ROOT / 'public'), name='static')
