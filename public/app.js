'use strict';
const $ = id => document.getElementById(id);
let countries = [];
let countrySearchEntries = [];
let busy = false;
let ready = false;
const paramsKey = 'getthelongchain-params-v1';

function status(message, kind = '') {
  $('status').textContent = message;
  $('status').className = `status ${kind}`;
}

function clearErrorDetails() {
  $('errorDetails').hidden = true;
  $('errorText').textContent = '';
}

function setBusy(value) {
  busy = value;
  $('generate').disabled = value || !ready;
  $('testProxy').disabled = value || !ready;
  $('generate').textContent = value ? '请求处理中…' : '生成结账链接 ↗';
}

function syncCountry() {
  $('currency').value = countries.find(c => c[0] === $('country').value)?.[1] || '';
}

function normalizeCountrySearch(value) {
  return value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();
}

function filterCountries() {
  const selected = $('country').value;
  const query = normalizeCountrySearch($('countrySearch').value);
  const words = query.split(/\s+/).filter(Boolean);
  const matches = countrySearchEntries.filter(entry => words.every(word => entry.search.includes(word)));
  // Put an exact country-code match first while retaining the other matches.
  matches.sort((a, b) => Number(b.code.toLowerCase() === query) - Number(a.code.toLowerCase() === query));
  const placeholder = new Option(matches.length ? (query ? '请选择匹配的国家 / 地区' : '请选择国家 / 地区') : '没有匹配的国家 / 地区', '');
  $('country').replaceChildren(placeholder);
  for (const entry of matches) $('country').appendChild(new Option(entry.label, entry.code));
  $('country').value = matches.some(entry => entry.code === selected) ? selected : '';
  if ($('country').value !== selected) syncCountry();
}

function credentials() {
  return {token: $('token').value.trim(), proxy: $('proxy').value.trim()};
}

async function api(path, body) {
  const response = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Access-Key': $('accessKey').value},
    body: JSON.stringify(body), cache: 'no-store'
  });
  let result;
  try { result = await response.json(); }
  catch { throw new Error('服务没有返回有效响应，请检查本地服务是否仍在运行。'); }
  if (!response.ok || !result.ok) {
    $('errorText').textContent = JSON.stringify({requestId: result.request_id || response.headers.get('X-Request-ID'), httpStatus: response.status, stage: result.stage,
      error: result.error, details: result.details}, null, 2);
    $('errorDetails').hidden = false;
    $('errorDetails').open = true;
    throw new Error(result.error || '请求失败，请稍后再试。');
  }
  return {...result, request_id: result.request_id || response.headers.get('X-Request-ID')};
}

function trustedUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && ['checkout.stripe.com', 'pay.openai.com', 'chatgpt.com'].includes(url.hostname)
      && !url.username && !url.password && (!url.port || url.port === '443');
  } catch { return false; }
}

$('openProxyGuide').addEventListener('click', () => {
  $('proxyGuide').showModal();
  document.body.classList.add('guide-open');
});
$('closeProxyGuide').addEventListener('click', () => $('proxyGuide').close());
$('proxyGuide').addEventListener('close', () => {
  document.body.classList.remove('guide-open');
  $('openProxyGuide').focus();
});
$('proxyGuide').addEventListener('click', event => {
  const bounds = $('proxyGuide').getBoundingClientRect();
  if (event.target === $('proxyGuide') && (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom)) $('proxyGuide').close();
});

$('country').addEventListener('change', syncCountry);
$('countrySearch').addEventListener('input', filterCountries);
$('countrySearch').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); $('country').focus(); }
  if (event.key === 'Escape') { $('countrySearch').value = ''; filterCountries(); }
});
$('clearSecrets').addEventListener('click', () => {
  for (const id of ['token', 'proxy', 'accessKey']) $(id).value = '';
  $('proxyResult').hidden = true;
  status('凭证已清空。');
});

async function copy(text, button) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = '已复制';
    setTimeout(() => { button.textContent = original; }, 1500);
  } catch { status('无法访问剪贴板，请选中内容后手动复制。', 'error'); }
}
$('copySession').addEventListener('click', () => copy('https://chatgpt.com/api/auth/session', $('copySession')));
$('copyLink').addEventListener('click', () => copy($('resultUrl').textContent, $('copyLink')));
$('copyError').addEventListener('click', () => copy($('errorText').textContent, $('copyError')));

$('proxy').addEventListener('input', () => { $('proxyResult').hidden = true; });
$('testProxy').addEventListener('click', async () => {
  if (busy || !ready) return;
  const proxy = $('proxy').value.trim();
  if (!proxy) { status('请先填写需要测试的代理地址。', 'error'); $('proxy').focus(); return; }
  clearErrorDetails();
  setBusy(true);
  $('testProxy').textContent = '测试中…';
  $('proxyResult').hidden = false;
  $('proxyResult').textContent = '正在测试互联网出口与 ChatGPT 连通性，每项最多等待 12 秒…';
  try {
    const data = await api('/api/proxy-test', {proxy});
    const lines = [];
    if (data.exit_ip) lines.push(`出口 IP：${data.exit_ip}${data.exit_country ? ` · ${data.exit_country}` : ''}`);
    for (const check of data.checks) lines.push(`${check.label}：${check.message}（${check.duration_ms} ms）`);
    if (data.request_id) lines.push(`请求编号：${data.request_id}`);
    $('proxyResult').textContent = lines.join('\n');
    status(data.message, data.passed ? 'success' : 'error');
  } catch (error) {
    $('proxyResult').textContent = error.message;
    status(error.message, 'error');
  } finally { setBusy(false); $('testProxy').textContent = '测试连接'; }
});

$('checkoutForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || !ready) return;
  clearErrorDetails();
  $('result').hidden = true;
  $('resultUrl').textContent = '';
  $('resultUrl').removeAttribute('href');
  $('openLink').removeAttribute('href');
  setBusy(true);
  status('正在创建 Team 结账链接，请等待。遇到超时不会自动重复创建。', 'loading');
  const body = {...credentials(), spaceMode: 'new',
    workspaceName: $('workspaceName').value.trim(),
    existingWorkspaceId: '',
    seatQuantity: Number($('seats').value), country: $('country').value, currency: $('currency').value,
    priceInterval: $('interval').value, promoCode: $('promo').value.trim()};
  try {
    const data = await api('/api/checkout', body);
    if (!trustedUrl(data.url)) throw new Error('链接域名校验失败。');
    $('resultUrl').textContent = data.url;
    $('resultUrl').href = data.url;
    $('openLink').href = data.url;
    $('resultMeta').textContent = `${body.country} / ${body.currency} · ${body.seatQuantity} 席 · ${body.priceInterval === 'month' ? '按月' : '按年'} · ${body.workspaceName}`;
    $('result').hidden = false;
    status('生成成功，请打开结账页核对金额与订阅信息。', 'success');
    try { localStorage.setItem(paramsKey, JSON.stringify({name: $('workspaceName').value, seats: body.seatQuantity, interval: body.priceInterval})); } catch {}
    $('result').scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } catch (error) { status(error.message, 'error'); }
  finally { setBusy(false); }
});

function showServerLocation(country, detected = false) {
  $('service').textContent = `服务器地址：${country}`;
  $('service').classList.toggle('online', detected);
  $('proxyHint').textContent = `不提供代理地址时，默认从${country}发送请求`;
}

async function loadServerLocation() {
  try {
    const response = await fetch('/api/server-location', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok || !data.ok || !data.detected || !data.country) throw new Error();
    showServerLocation(data.country, true);
  } catch { showServerLocation('无法检测'); }
}

async function init() {
  try {
    const response = await fetch('/api/health', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error();
    countries = data.countries;
    const englishNames = new Intl.DisplayNames(['en'], {type: 'region'});
    const aliases = {GB: 'UK 英国', AE: 'UAE 阿联酋', US: 'USA 美国', CN: '中国 中国大陆', KR: '韩国 南韩'};
    countrySearchEntries = countries.map(([code, currency, name]) => ({code,
      label: `${code} — ${name}${currency ? ` (${currency})` : ''}`,
      search: normalizeCountrySearch(`${code} ${name} ${englishNames.of(code)} ${aliases[code] || ''}`)}));
    filterCountries();
    for (const [code, name] of data.currencies) {
      const option = new Option(code, code);
      option.title = name;
      $('currency').appendChild(option);
    }
    $('keyField').hidden = !data.requireKey;
    $('accessKey').required = data.requireKey;
    ready = true;
    void loadServerLocation();
    try {
      const saved = JSON.parse(localStorage.getItem(paramsKey) || '{}');
      if (typeof saved.name === 'string') $('workspaceName').value = saved.name;
      if (Number.isInteger(saved.seats) && saved.seats >= 2 && saved.seats <= 999) $('seats').value = saved.seats;
      if (['month', 'year'].includes(saved.interval)) $('interval').value = saved.interval;
    } catch {}
  } catch {
    showServerLocation('无法检测');
    status('无法连接本地服务，请重新运行启动脚本后刷新页面。', 'error');
  }
  setBusy(false);
}
init();
