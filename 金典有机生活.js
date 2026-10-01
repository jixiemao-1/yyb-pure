// name:金典
// cron:35 21 * * *

/**
 * 金典每日签到任务
 * 变量：
 *   WECHAT_SERVER            协议服务地址
 *   ADMIN_KEY                协议管理密钥（WeChatPadPro/iwechat需要，牛子协议不需要）
 *   WX_ID                    可选，指定微信账号ID，多个用&分隔
 *   JINDIAN_APPID            可选，覆盖默认 appid
 *   JINDIAN_PROXY            可选，代理地址
 *   JINDIAN_PROXY_API        可选，代理提取API
 *   JINDIAN_PROXY_RETRY      可选，WAF重试次数（默认5）
 *
 * 逻辑：
 * 1. 从协议服务自动获取在线账号
 * 2. 读取 jdlck.txt 缓存，按备注匹配，有效则跳过 code 登录
 * 3. 缓存失效则走 code → 登录 → 更新缓存
 * 4. 执行每日签到
 * 5. 刷新积分、签到状态并走 sendNotify 通知
 */

const fs = require('fs');
const path = require('path');
const axios = require('axios');
let HttpsProxyAgent = null;
try {
  HttpsProxyAgent = require('https-proxy-agent').HttpsProxyAgent || require('https-proxy-agent');
} catch (_) {
  HttpsProxyAgent = null;
}

const APP_NAME = '金典';
const CACHE_FILE = path.join(__dirname, 'jdlck.txt');
const ENV_CHECK_FILE = path.join(__dirname, 'env_check.json');

const DEFAULT_APPID = 'wxf32616183fb4511e';
const HOST = 'https://msmarket.msx.digitalyili.com/gateway/api';
const GATEWAY_DOMAIN = 'a1d5e7a41-wx621112590b635086.sh.wxgateway.com';
const TENANT_ID = '1718857849685876737';
const WECHAT_SERVER = String(process.env.WECHAT_SERVER || '').trim();
const ADMIN_KEY = String(process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '').trim();
const WX_ID_FILTER = String(process.env.WX_ID || '').trim();
const PROXY_DIRECT = String(process.env.JINDIAN_PROXY || '').trim();
const PROXY_API = String(process.env.JINDIAN_PROXY_API || '').trim();
const PROXY_RETRY = Math.max(1, Number(process.env.JINDIAN_PROXY_RETRY || 5));

const MOBILE_UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.58(0x18003a35) NetType/WIFI Language/zh_CN MiniProgramEnv/iOS';
const DEFAULT_SCENE = '1008';

let notifyMsg = '';
const proxyState = { url: '', agent: null };

function getAppid() {
  return String(process.env.JINDIAN_APPID || DEFAULT_APPID).trim();
}

function log(msg) {
  console.log(msg);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function readJson(file, def = {}) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return def; }
}
function writeJson(file, obj) {
  fs.writeFileSync(file, JSON.stringify(obj, null, 2), 'utf8');
}

// ── 代理 ─────────────────────────────────────────────────────────────────

function normalizeProxy(raw) {
  const s = String(raw || '').trim();
  if (!s) return '';
  return /^https?:\/\//i.test(s) ? s : `http://${s}`;
}
function extractProxy(text) {
  const s = String(text || '').trim();
  if (!s) return '';
  const line = s.split(/\r?\n/).map(x => x.trim()).find(Boolean) || '';
  const m = line.match(/((?:https?:\/\/)?[^\s]+:\d+)/i);
  return normalizeProxy(m ? m[1] : line);
}
async function fetchProxy() {
  if (PROXY_DIRECT) return normalizeProxy(PROXY_DIRECT);
  if (!PROXY_API) return '';
  try {
    const { data } = await axios.get(PROXY_API, { timeout: 12000, proxy: false, validateStatus: () => true });
    return extractProxy(typeof data === 'string' ? data : JSON.stringify(data));
  } catch (e) {
    log(`⚠️ 获取代理失败: ${e.message}`);
    return '';
  }
}
async function switchProxy(force = false) {
  if (!force && proxyState.url) return true;
  const p = await fetchProxy();
  if (!p) return false;
  if (!HttpsProxyAgent) {
    log('⚠️ 未安装 https-proxy-agent，无法使用代理');
    return false;
  }
  proxyState.url = p;
  proxyState.agent = new HttpsProxyAgent(p);
  log(`🌐 使用代理: ${p}`);
  return true;
}
function isWafBlocked(x) {
  return /WAF|拦截|block-pages|status:403|status=403|ErrorCode:639|请求已中断/i.test(String(x || ''));
}

// ── 协议服务：自动获取在线账号（与 getCode.py 同逻辑）──────────────────

async function detectProtocolType() {
  let envData = readJson(ENV_CHECK_FILE, {});
  if (envData.protocol_type && envData.protocol_type !== 'Unknown' && envData.protocol_type !== 'iwechat' && envData.protocol_type !== 'WeChatPadPro') {
    log(`从配置文件读取到协议服务类型: ${envData.protocol_type}`);
    return envData.protocol_type;
  }

  const base = WECHAT_SERVER.replace(/\/$/, '');
  let protocolType = 'Unknown';
  const auth = ADMIN_KEY || process.env.LICENSE_KEY || process.env.AUTH || '';

  try {
    const r3 = await axios.get(`${base}/api/accounts`, {
      timeout: 8000, validateStatus: () => true,
      headers: { 'X-License-Key': auth, Authorization: 'Bearer ' + auth },
    });
    if (r3.status === 200 && (Array.isArray(r3.data?.accounts) || Array.isArray(r3.data))) protocolType = 'yyb呆呆';
  } catch {}
  if (protocolType === 'Unknown') {
    try {
      const r1 = await axios.get(`${base}/admin/GetAuthKey`, { params: { key: auth }, timeout: 5000, validateStatus: () => true });
      if (r1.status === 200 && (r1.data?.Code === 200 || Array.isArray(r1.data?.Data))) protocolType = 'iwechat';
    } catch {}
  }
  if (protocolType === 'Unknown') {
    try {
      const r2 = await axios.get(`${base}/admin/GetAllDevices`, { params: { key: auth }, timeout: 5000, validateStatus: () => true });
      if (r2.status === 200 && (r2.data?.Code === 200 || Array.isArray(r2.data?.Data))) protocolType = 'WeChatPadPro';
    } catch {}
  }

  writeJson(ENV_CHECK_FILE, { protocol_type: protocolType });
  log(`当前使用的协议服务: ${protocolType}`);
  return protocolType;
}

async function getOnlineAccounts() {
  if (!WECHAT_SERVER) throw new Error('未设置 WECHAT_SERVER 环境变量');
  const base = WECHAT_SERVER.replace(/\/$/, '');
  const protocolType = await detectProtocolType();
  let accounts = [];

  if (protocolType === 'yyb呆呆' || protocolType === 'Niuzi') {
    const { loadTaskAccounts } = require('./yybOpenid');
    const list = await loadTaskAccounts(process.env.WX_ID || '');
    accounts = list.map(a => ({ wxid: a.wxid, remark: a.remark || a.nickname || a.wxid }));
  } else if (protocolType === 'iwechat') {
    const { data } = await axios.get(`${base}/admin/GetAuthKey`, { params: { key: ADMIN_KEY }, timeout: 60000, validateStatus: () => true });
    if (!Array.isArray(data)) throw new Error('获取授权码响应格式错误');
    for (const acc of data) {
      if (acc.status === 1) {
        accounts.push({ wxid: acc.wx_id || acc.deviceId || '', remark: acc.nick_name || acc.deviceName || acc.wx_id || '' });
      }
    }
  } else if (protocolType === 'WeChatPadPro') {
    const { data } = await axios.get(`${base}/admin/GetAllDevices`, { params: { key: ADMIN_KEY }, timeout: 60000, validateStatus: () => true });
    let devices = [];
    if (data && data.Data && data.Data.devices) devices = data.Data.devices;
    else if (Array.isArray(data)) devices = data;
    else if (data && data.data) devices = Array.isArray(data.data) ? data.data : [data.data];
    else if (data && data.devices) devices = data.devices;
    for (const acc of devices) {
      if (acc.status === 1) {
        accounts.push({ wxid: acc.deviceId || acc.wx_id || '', remark: acc.nick_name || acc.deviceName || '' });
      }
    }
  } else {
    throw new Error('未知的协议服务类型');
  }

  accounts = accounts.filter(a => a.wxid);

  if (WX_ID_FILTER) {
    const targetIds = WX_ID_FILTER.split('&').map(s => s.trim()).filter(Boolean);
    accounts = accounts.filter(a => targetIds.includes(a.wxid));
    log(`根据WX_ID筛选后获得${accounts.length}个账号`);
  }

  log(`从协议服务获取到${accounts.length}个在线账号`);
  return accounts;
}

// ── Token 缓存（jdlck.txt，格式：token#备注，每行一个）──────────────────

function readTokenCache() {
  try {
    const content = fs.readFileSync(CACHE_FILE, 'utf8');
    const map = {};
    for (const line of content.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const idx = trimmed.indexOf('#');
      if (idx < 1) continue;
      const token = trimmed.slice(0, idx).trim();
      const remark = trimmed.slice(idx + 1).trim();
      if (token && remark) map[remark] = token;
    }
    return map;
  } catch {
    return {};
  }
}

function writeTokenCache(map) {
  const content = Object.entries(map)
    .map(([remark, token]) => `${token}#${remark}`)
    .join('\n');
  fs.writeFileSync(CACHE_FILE, content, 'utf8');
}

let tokenCache = readTokenCache();

function getCachedToken(remark) {
  return tokenCache[remark] || null;
}

function setCachedToken(remark, token) {
  tokenCache[remark] = token;
  writeTokenCache(tokenCache);
}

function removeCachedToken(remark) {
  delete tokenCache[remark];
  writeTokenCache(tokenCache);
}

// ── 工具函数 ──────────────────────────────────────────────────────────────

function toQueryString(obj = {}) {
  return Object.entries(obj)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&');
}

function parseJsonEnv(name) {
  const raw = String(process.env[name] || '').trim();
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (e) {
    log(`⚠️ ${name} 不是合法 JSON，已忽略: ${e.message}`);
    return {};
  }
}

function createGatewayCallId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function getGatewaySimHeaders() {
  const APPID = getAppid();
  const routeTag = String(process.env.JINDIAN_ROUTE_TAG || GATEWAY_DOMAIN);
  const source = String(process.env.JINDIAN_WX_SOURCE || 'wx_client');
  const callId = createGatewayCallId();
  const timeoutMs = String(process.env.JINDIAN_TIMEOUT_MS || '15000');

  return {
    'X-WX-HTTP-MODE': 'REROUTE',
    'X-WX-CONF-VERSION': '0',
    'x-wx-call-id': callId,
    'x-wx-route-tag': routeTag,
    'x-wx-source': source,
    'x-wx-appid': APPID,
    'x-envoy-expected-rq-timeout-ms': timeoutMs,
  };
}

function buildHeaders(token = '', extra = {}) {
  const APPID = getAppid();
  return {
    Accept: 'application/json, text/plain, */*',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Content-Type': 'application/json',
    Origin: 'https://servicewechat.com',
    Referer: `https://servicewechat.com/${APPID}/release/page-frame.html`,
    'User-Agent': MOBILE_UA,
    'access-token': String(token || ''),
    'atv-page': '',
    'forward-appid': '',
    'register-source': '',
    scene: DEFAULT_SCENE,
    'source-type': '',
    'tenant-id': TENANT_ID,
    xweb_xhr: '1',
    ...getGatewaySimHeaders(),
    ...parseJsonEnv('JINDIAN_EXTRA_HEADERS_JSON'),
    ...extra,
  };
}

async function requestJson(url, options = {}, timeoutMs = 20_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const fetchOpts = { ...options, signal: controller.signal };
    if (proxyState.agent) fetchOpts.agent = proxyState.agent;
    const resp = await fetch(url, fetchOpts);
    const text = await resp.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
    return { status: resp.status, data, text };
  } finally {
    clearTimeout(timer);
  }
}

function isSuccess(data) {
  return Boolean(data && (data.status === true || data.success === true));
}

function extractErrorMsg(data) {
  if (!data) return '未知错误';
  if (typeof data === 'string') return data.slice(0, 300);
  if (typeof data.message === 'string' && data.message) return data.message;
  if (typeof data.msg === 'string' && data.msg) return data.msg;
  if (data.error) {
    if (typeof data.error === 'string') return data.error;
    if (typeof data.error.msg === 'string' && data.error.msg) return data.error.msg;
    if (typeof data.error.message === 'string' && data.error.message) return data.error.message;
    if (data.error.code !== undefined) return `错误码 ${data.error.code}`;
  }
  return JSON.stringify(data).slice(0, 300);
}

function isAlreadyDoneMessage(message) {
  return /已签到|已领取|已完成|今日已|已经|重复/.test(String(message || ''));
}

async function apiRequest(pathname, method = 'GET', token = '', payload = null, extraHeaders = {}) {
  const isGet = method.toUpperCase() === 'GET';
  const query = isGet && payload ? toQueryString(payload) : '';
  const url = `${HOST}${pathname}${query ? `?${query}` : ''}`;
  const options = {
    method,
    headers: buildHeaders(token, extraHeaders),
  };
  if (!isGet) {
    options.body = JSON.stringify(payload ?? {});
  }
  return requestJson(url, options);
}

async function apiRequestWithRetry(pathname, method = 'GET', token = '', payload = null, extraHeaders = {}) {
  let last = '';
  for (let i = 1; i <= PROXY_RETRY; i++) {
    const result = await apiRequest(pathname, method, token, payload, extraHeaders);
    if (result.status !== 403 && !isWafBlocked(result.text || JSON.stringify(result.data))) {
      return result;
    }
    last = `WAF拦截(${result.status})`;
    if (i < PROXY_RETRY) {
      const ok = await switchProxy(true);
      log(ok ? `⚠️ ${pathname} WAF，切换代理重试 ${i}/${PROXY_RETRY}` : `⚠️ ${pathname} WAF，但未配置可用代理 ${i}/${PROXY_RETRY}`);
      if (!ok) break;
      await sleep(randomInt(500, 1000));
      continue;
    }
    break;
  }
  return { status: 403, data: last, text: last };
}

// ── API ──────────────────────────────────────────────────────────────────

async function getWxCode(wxid) {
  /* 账号环境变量可空 */
  const { getYybCode, getAuth } = require('./yybOpenid');
  const { code } = await getYybCode(WECHAT_SERVER, getAuth(), getAppid(), wxid);
  return String(code).trim();
}

async function loginByCode(code) {
  let last = '';
  for (let i = 1; i <= PROXY_RETRY; i++) {
    const { data, text, status } = await apiRequest('/auth/account/login', 'POST', '', { jsCode: code });
    if (status !== 403 && !isWafBlocked(text || JSON.stringify(data))) {
      return data;
    }
    last = `登录WAF拦截(${status})`;
    if (i < PROXY_RETRY) {
      const ok = await switchProxy(true);
      log(ok ? `⚠️ 登录WAF，切换代理重试 ${i}/${PROXY_RETRY}` : `⚠️ 登录WAF，但未配置可用代理 ${i}/${PROXY_RETRY}`);
      if (!ok) break;
      await sleep(randomInt(500, 1000));
      continue;
    }
    break;
  }
  throw new Error(last);
}

async function getUserInfo(token) {
  const { data } = await apiRequestWithRetry('/auth/account/user/info', 'GET', token);
  return data;
}

async function getScore(token) {
  const { data } = await apiRequestWithRetry('/member/point', 'GET', token);
  return data;
}

async function getSignStatus(token) {
  const { data } = await apiRequestWithRetry('/member/sign/status', 'GET', token);
  return data;
}

async function getSignConfig(token) {
  const { data } = await apiRequestWithRetry('/member/sign/config', 'GET', token);
  return data;
}

async function dailySign(token) {
  const { data } = await apiRequestWithRetry('/member/daily/sign', 'POST', token, {});
  return data;
}

// ── 格式化 ────────────────────────────────────────────────────────────────

function maskMobile(mobile) {
  const text = String(mobile || '');
  return text.replace(/^(\d{3})\d{4}(\d{4})$/, '$1****$2');
}

function formatBonusParts(items = []) {
  return items.filter(Boolean).join('，') || '无';
}

function getDailySignRewardText(config) {
  const daily = config?.dailySignConfig || {};
  const cont = config?.continuousSignConfig || {};
  return {
    daily: formatBonusParts([
      Number(daily.bonusPoint) ? `${daily.bonusPoint}积分` : '',
      Number(daily.bonusGrowth) ? `${daily.bonusGrowth}成长值` : '',
    ]),
    continuous: formatBonusParts([
      Number(cont.bonusPoint) ? `${cont.bonusPoint}积分` : '',
      Number(cont.bonusGrowth) ? `${cont.bonusGrowth}成长值` : '',
    ]),
  };
}

// ── 任务 ──────────────────────────────────────────────────────────────────

async function runSignTask(token) {
  const beforeStatus = await getSignStatus(token);
  if (!isSuccess(beforeStatus)) {
    throw new Error(`查询签到状态失败: ${extractErrorMsg(beforeStatus)}`);
  }

  const configResp = await getSignConfig(token);
  const rewardText = isSuccess(configResp)
    ? getDailySignRewardText(configResp.data)
    : { daily: '未知', continuous: '未知' };

  if (beforeStatus.data?.signed) {
    return {
      message: '今日已签到',
      signed: true,
      signedDays: Number(beforeStatus.data?.signedDays || 0),
      rewardText,
      changed: false,
    };
  }

  const signResp = await dailySign(token);
  if (!isSuccess(signResp)) {
    const errorMsg = extractErrorMsg(signResp);
    if (isAlreadyDoneMessage(errorMsg)) {
      const afterStatus = await getSignStatus(token);
      return {
        message: '今日已签到',
        signed: Boolean(afterStatus?.data?.signed),
        signedDays: Number(afterStatus?.data?.signedDays || beforeStatus.data?.signedDays || 0),
        rewardText,
        changed: false,
      };
    }
    throw new Error(`签到失败: ${errorMsg}`);
  }

  const daily = signResp.data?.dailySign || {};
  const continuation = signResp.data?.continuationSign || {};
  const messageParts = ['签到成功'];
  if (Number(daily.bonusPoint)) messageParts.push(`+${daily.bonusPoint}积分`);
  if (Number(daily.bonusGrowth)) messageParts.push(`+${daily.bonusGrowth}成长值`);
  if (Number(continuation.bonusPoint)) messageParts.push(`连签额外+${continuation.bonusPoint}积分`);
  if (Number(continuation.bonusGrowth)) messageParts.push(`连签额外+${continuation.bonusGrowth}成长值`);

  const afterStatus = await getSignStatus(token);
  return {
    message: messageParts.join('，'),
    signed: Boolean(afterStatus?.data?.signed),
    signedDays: Number(afterStatus?.data?.signedDays || beforeStatus.data?.signedDays || 0),
    rewardText,
    changed: true,
  };
}

async function runOne(account) {
  log(`\n================ ${account.remark} ================`);

  let token = getCachedToken(account.remark);
  if (token) {
    log(`🔑 ${account.remark} 使用缓存 token`);
    const testResp = await getUserInfo(token);
    if (!isSuccess(testResp)) {
      log(`⚠️ ${account.remark} 缓存 token 已失效，重新登录`);
      removeCachedToken(account.remark);
      token = null;
    } else {
      log(`✅ ${account.remark} 缓存 token 有效`);
    }
  }

  if (!token) {
    log(`🧩 ${account.remark} 使用微信 code 服务登录`);
    const code = await getWxCode(account.wxid);
    const loginResp = await loginByCode(code);
    if (!isSuccess(loginResp) || !loginResp?.data?.accessToken) {
      throw new Error(`登录失败: ${extractErrorMsg(loginResp)}`);
    }
    token = String(loginResp.data.accessToken);
    setCachedToken(account.remark, token);
    log(`💾 ${account.remark} token 已缓存`);
  }

  const [beforeUser, beforeScoreResp] = await Promise.all([
    getUserInfo(token),
    getScore(token),
  ]);

  if (!isSuccess(beforeUser)) {
    throw new Error(`查询用户信息失败: ${extractErrorMsg(beforeUser)}`);
  }
  if (!isSuccess(beforeScoreResp)) {
    throw new Error(`查询积分失败: ${extractErrorMsg(beforeScoreResp)}`);
  }

  const userInfo = beforeUser.data || {};
  const beforeScore = Number(beforeScoreResp.data || 0);

  const signResult = await runSignTask(token);

  const [afterUser, afterScoreResp, afterSignStatus] = await Promise.all([
    getUserInfo(token),
    getScore(token),
    getSignStatus(token),
  ]);

  if (!isSuccess(afterUser)) {
    throw new Error(`刷新用户信息失败: ${extractErrorMsg(afterUser)}`);
  }
  if (!isSuccess(afterScoreResp)) {
    throw new Error(`刷新积分失败: ${extractErrorMsg(afterScoreResp)}`);
  }
  if (!isSuccess(afterSignStatus)) {
    throw new Error(`刷新签到状态失败: ${extractErrorMsg(afterSignStatus)}`);
  }

  const afterInfo = afterUser.data || {};
  const afterScore = Number(afterScoreResp.data || 0);

  const line = [
    `【${account.remark}】${afterInfo.nickname || afterInfo.nickName || userInfo.nickname || userInfo.nickName || ''} ${maskMobile(afterInfo.mobile || userInfo.mobile || '')}`.trim(),
    `签到结果：${signResult.message}`,
    `签到状态：${afterSignStatus.data?.signed ? '已签到' : '未签到'}`,
    `签到天数：${Number(afterSignStatus.data?.signedDays || signResult.signedDays || 0)}`,
    `签到奖励：日签${signResult.rewardText.daily}${signResult.rewardText.continuous !== '无' ? `；连签${signResult.rewardText.continuous}` : ''}`,
    `当前积分：${afterScore}`,
    `积分变化：${beforeScore}->${afterScore}`,
  ].join('\n');

  log(line);
  return line;
}

async function sendNotify(title, content) {
  try {
    const notify = require('./sendNotify');
    await notify.sendNotify(title, content);
  } catch (e) {
    log(`⚠️ 通知发送失败: ${e.message}`);
  }
}

async function __ensureAccounts(raw, parseFn) {
  let accounts = typeof parseFn === 'function' ? parseFn(raw) : [];
  if (!accounts || !accounts.length) {
    const { loadTaskAccounts } = require('./yybOpenid');
    const list = await loadTaskAccounts(raw || '');
    accounts = list.map(a => ({ wxid: a.wxid, remark: a.remark, note: a.remark }));
    console.log(`直连账号 ${accounts.length} 个`);
  }
  return accounts;
}
async function main() {
  const accounts = await getOnlineAccounts();
  if (!accounts.length) throw new Error('未获取到在线账号，请检查 WECHAT_SERVER 配置');

  log(`${APP_NAME} 开始，共 ${accounts.length} 个账号`);

  if (PROXY_DIRECT || PROXY_API) await switchProxy(false);
  else log('🌐 代理模式: 直连（如 msmarket 403，请配置 JINDIAN_PROXY 或 JINDIAN_PROXY_API）');

  for (let i = 0; i < accounts.length; i += 1) {
    const account = accounts[i];
    try {
      const line = await runOne(account);
      notifyMsg += `${line}\n\n`;
    } catch (e) {
      const line = `【${account.remark}】失败: ${e.message}`;
      log(line);
      notifyMsg += `${line}\n\n`;
    }

    if (i < accounts.length - 1) {
      const waitSeconds = randomInt(4, 8);
      log(`⏳ ${account.remark} 执行完成，等待 ${waitSeconds} 秒后处理下一个账号`);
      await sleep(waitSeconds * 1000);
    }
  }

  if (notifyMsg.trim()) {
    await sendNotify(APP_NAME, notifyMsg.trim());
  }
}

main().catch((e) => {
  console.error('FATAL:', e && (e.stack || e.message || JSON.stringify(e)));
  process.exit(1);
});
