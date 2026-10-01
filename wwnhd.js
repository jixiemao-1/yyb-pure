#!/usr/bin/env node
/**
 * 问问农
 *
 * 功能：
 * 1) wx.login code -> /users/wechat-pre-login -> loginInfoAtom(token)
 * 2) 查询 /v2/profile/{yaraUserId}/signin
 * 3) 自动任务（member task-center）
 *
 * 环境变量：
 * - WECHAT_SERVER       协议服务IP地址和端口（支持WeChatPadPro、iwechat、牛子协议）
 * - ADMIN_KEY           与搭建时设置的ADMIN_KEY一致（仅WeChatPadPro或iwechat需要，牛子协议不需要）
 * - WX_ID              可选，指定要执行任务的微信账号ID，多个用&分隔
 *                      如果不设置则为所有在线账号执行任务
 * - WWN_MAIN_APPID      默认 wx61a9d721d3396d1b
 * - WWN_MEMBER_APPID    默认 wxc5d513880ace81a4
 * - WWN_ACCOUNT_ID      默认 634f5f28a0e71c29500b0313
 * - WWN_ENABLE_TASKS    1开启自动任务(默认)，0仅登录+签到状态
 * - WWN_ENABLE_SHARE    1开启签到分享任务(默认)，0关闭
 * - WWN_SHARE_TIMES     分享任务调用次数，默认10
 * - WWN_SHARE_DELAY_MS  分享任务每次间隔毫秒，默认1200
 * - WWN_SHARE_RETRY     分享任务频控重试次数，默认3
 * - WWN_DEBUG           1开启调试日志(默认0)
 * - WWN_LOG_PLAIN       1纯文本日志(默认1更适配青龙)
 * - WWN_LOG_FULL_TOKEN  1日志打印完整token(默认0仅掩码显示)
 * - WWN_MAX_PER_TASK    单任务最多上报次数，默认5
 * - WWN_EVENT_WHITELIST 逗号分隔，任务事件白名单
 */

'use strict';

const fs = require('fs');
const pathMod = require('path');

const CACHE_NAME = 'wwn';
const CACHE_FILE = pathMod.join(__dirname, `${CACHE_NAME}.json`);

const BFF = 'https://fcc-prd-bff.yaradigitalfarming.cn';
const CONSUMER = 'https://consumer-api.quncrm.com';
const OAUTH_BASE = process.env.WWN_OAUTH_BASE || 'https://oauth.quncrm.com';

const MAIN_APPID = process.env.WWN_MAIN_APPID || 'wx61a9d721d3396d1b';
const MEMBER_APPID = process.env.WWN_MEMBER_APPID || 'wxc5d513880ace81a4';
const ACCOUNT_ID_DEFAULT = process.env.WWN_ACCOUNT_ID || '634f5f28a0e71c29500b0313';

const MAIJS_VERSION = process.env.WWN_MAIJS_VERSION || '1.50.0';
const APP_VERSION = process.env.WWN_APP_VERSION || '1.96.3.535f053';
const APP_NAME = process.env.WWN_APP_NAME || '群脉电商';
const ENV_VERSION = process.env.WWN_ENV_VERSION || 'release';

const ENABLE_TASKS = (process.env.WWN_ENABLE_TASKS || '1') !== '0';
const ENABLE_SIGN = (process.env.WWN_ENABLE_SIGN || '1') !== '0';
const ENABLE_SHARE = (process.env.WWN_ENABLE_SHARE || '1') !== '0';
const DEBUG_MODE = (process.env.WWN_DEBUG || '0') === '1';
const LOG_PLAIN = (process.env.WWN_LOG_PLAIN || '1') !== '0';
const LOG_FULL_TOKEN = (process.env.WWN_LOG_FULL_TOKEN || '0') === '1';
const SHARE_TIMES_RAW = Number(process.env.WWN_SHARE_TIMES || 10);
const SHARE_TIMES = Number.isFinite(SHARE_TIMES_RAW) && SHARE_TIMES_RAW > 0 ? Math.floor(SHARE_TIMES_RAW) : 10;
const SHARE_DELAY_MS_RAW = Number(process.env.WWN_SHARE_DELAY_MS || 1200);
const SHARE_DELAY_MS = Number.isFinite(SHARE_DELAY_MS_RAW) && SHARE_DELAY_MS_RAW >= 0 ? Math.floor(SHARE_DELAY_MS_RAW) : 1200;
const SHARE_RETRY_RAW = Number(process.env.WWN_SHARE_RETRY || 3);
const SHARE_RETRY = Number.isFinite(SHARE_RETRY_RAW) && SHARE_RETRY_RAW > 0 ? Math.floor(SHARE_RETRY_RAW) : 3;
const MAX_PER_TASK = Number(process.env.WWN_MAX_PER_TASK || 5);
const SIGN_TEMPLATE_IDS = (process.env.WWN_SIGN_TEMPLATE_IDS || 'r0RQspXnqWh9WaFblGjLwWXrcNjPceXbvYmfULrjvXE')
  .split(',').map(s => s.trim()).filter(Boolean);

const UA_BFF = 'Mozilla/5.0 MicroMessenger MiniProgram';
const UA_CONSUMER = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.70';

const DEFAULT_EVENT_WHITELIST = [
  'maievent-campaigncenter-signin',
  'c_click_nutrition_plan_card',
  'c_click_fertilizer_item',
  'c_click_exper_demo_card',
  'c_click_nutrient_story_cards',
  'c_view_post_page_detail',
  'c_click_share_button',
  'c_click_share_pdd_button',
];

const EVENT_WHITELIST = (process.env.WWN_EVENT_WHITELIST || DEFAULT_EVENT_WHITELIST.join(','))
  .split(',').map(s => s.trim()).filter(Boolean);

const TASK_CENTER_URL = 'member/pages/task-center/index?_pageUrl=pages%252Ftask-center%252Findex%253Fstatus%253Dpublished%2526pageId%253D65decd67c201da00527e4ac1&pageId=65decd67c201da00527e4ac1&status=published';

const fetchFn = globalThis.fetch || ((...args) => import('node-fetch').then(({ default: f }) => f(...args)));

const LOG_WIDTH = 72;

function hr(char = '─') {
  const ch = LOG_PLAIN ? '-' : char;
  console.log(ch.repeat(LOG_WIDTH));
}

function logWithIcon(icon, msg) {
  const line = String(msg ?? '').replace(/\s*\n+\s*/g, ' ').trim();
  if (LOG_PLAIN) {
    const tag = ({
      'ℹ️': '[I]',
      '✅': '[OK]',
      '⚠️': '[W]',
      '❌': '[E]',
    })[icon] || '[L]';
    console.log(`${tag} ${line}`);
    return;
  }
  console.log(`${icon} ${line}`);
}

function logInfo(msg) {
  logWithIcon('ℹ️', msg);
}

function logOk(msg) {
  logWithIcon('✅', msg);
}

function logWarn(msg) {
  logWithIcon('⚠️', msg);
}

function logErr(msg) {
  logWithIcon('❌', msg);
}

function section(title) {
  if (LOG_PLAIN) {
    console.log(`\n[${title}]`);
    return;
  }
  console.log(`\n━━━ ${title} ━━━`);
}

function maskMiddle(s, left = 12, right = 8) {
  const str = String(s || '');
  if (!str) return '';
  if (str.length <= left + right + 3) return str;
  return `${str.slice(0, left)}...${str.slice(-right)}`;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function loadCache() {
  try {
    return JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8')) || {};
  } catch {
    return {};
  }
}

function saveCache(cache) {
  try {
    fs.writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 2), 'utf8');
  } catch (e) {
    logWarn(`cache 写入失败: ${e.message || e}`);
  }
}

function pickCache(cache, key) {
  if (!cache || !key) return null;
  const v = cache[key];
  return (v && typeof v === 'object') ? v : null;
}

function putCache(cache, key, value) {
  if (!cache || !key || !value || typeof value !== 'object') return;
  cache[key] = {
    ...cache[key],
    ...value,
    updateTime: new Date().toISOString(),
  };
}

function nowISO() {
  const d = new Date();
  const tz = -d.getTimezoneOffset();
  const sign = tz >= 0 ? '+' : '-';
  const pad = n => String(Math.floor(Math.abs(n))).padStart(2, '0');
  const hh = pad(tz / 60), mm = pad(tz % 60);
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3,'0')}${sign}${hh}:${mm}`;
}

function uuidv4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

function decodeJwtPayload(token) {
  try {
    const parts = String(token || '').split('.');
    if (parts.length < 2) return null;
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    return JSON.parse(Buffer.from(padded, 'base64').toString('utf8'));
  } catch {
    return null;
  }
}

function fmtTs(ts) {
  try {
    const d = new Date(Number(ts) * 1000);
    // 固定按+08输出
    const p = n => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}-${p(d.getUTCMonth()+1)}-${p(d.getUTCDate())} ${p((d.getUTCHours()+8)%24)}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} +08:00`;
  } catch {
    return String(ts);
  }
}

function printTokenInfo(label, token) {
  const p = decodeJwtPayload(token);
  const iat = Number(p?.iat || 0);
  const exp = Number(p?.exp || 0);
  if (!iat || !exp) {
    logWarn(`${label} 非JWT或无法解码`);
    return;
  }
  const leftSec = exp - Math.floor(Date.now() / 1000);
  const leftMin = Math.max(0, Math.floor(leftSec / 60));
  if (DEBUG_MODE) {
    logInfo(`${label}: 剩余≈${leftMin}min (iat ${fmtTs(iat)} | exp ${fmtTs(exp)})`);
  } else {
    logInfo(`${label}: 剩余≈${leftMin}min`);
  }
}

async function httpJson(url, { method = 'GET', headers = {}, body, timeout = 20000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('timeout')), timeout);
  try {
    const resp = await fetchFn(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const text = await resp.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!resp.ok) {
      throw new Error(`${method} ${url} -> ${resp.status} ${text.slice(0, 400)}`);
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

let _protocolType = null;

async function detectProtocol(server, adminKey) {
  if (_protocolType) return _protocolType;
  const auth = adminKey || process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '';
  try {
    const data = await httpJson(`${server}/api/accounts`, {
      timeout: 8000,
      headers: { 'X-License-Key': auth, Authorization: 'Bearer ' + auth },
    });
    if (Array.isArray(data?.accounts) || Array.isArray(data)) {
      _protocolType = 'yyb呆呆';
      return _protocolType;
    }
  } catch {}
  try {
    const r1 = await httpJson(`${server}/admin/GetAuthKey?key=${encodeURIComponent(auth)}`, { timeout: 5000 });
    if (r1 && (r1.Code === 200 || Array.isArray(r1.Data))) {
      _protocolType = 'iwechat';
      return _protocolType;
    }
  } catch {}
  try {
    const r2 = await httpJson(`${server}/admin/GetAllDevices?key=${encodeURIComponent(auth)}`, { timeout: 5000 });
    if (r2 && (r2.Code === 200 || Array.isArray(r2.Data))) {
      _protocolType = 'WeChatPadPro';
      return _protocolType;
    }
  } catch {}
  _protocolType = 'Unknown';
  return _protocolType;
}

async function getOnlineAccounts(server, adminKey, protocolType) {
  if (protocolType === 'yyb呆呆' || protocolType === 'Niuzi') {
    try {
      const { loadTaskAccounts } = require('./yybOpenid');
      const list = await loadTaskAccounts(process.env.WX_ID || '', server, adminKey);
      return list.map(a => ({ wxid: a.wxid, openid: a.wxid, nickname: a.remark || a.nickname, license: adminKey || a.wxid }));
    } catch (e) {
      const auth = adminKey || process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '';
      const data = await httpJson(`${server}/api/accounts`, {
        headers: {
          'Content-Type': 'application/json',
          'X-License-Key': auth,
          'Authorization': 'Bearer ' + auth,
        }
      });
      const items = data?.accounts || data?.data || [];
      const list = Array.isArray(items) ? items : (items && typeof items === 'object' ? Object.values(items) : []);
      const accounts = [];
      for (const info of list) {
        if (!info || typeof info !== 'object') continue;
        const oid = info.openid || info.wxid || info.wx_id || '';
        const status = info.status;
        const online = status == null || status === '' || status === 'active' || status === 1 || status === '1' || info.survival === 1;
        if (!oid || !online) continue;
        accounts.push({ wxid: oid, openid: oid, nickname: info.nickname || oid, license: auth || oid });
      }
      return accounts;
    }
  } else if (protocolType === 'iwechat') {
    const data = await httpJson(`${server}/admin/GetAuthKey?key=${encodeURIComponent(adminKey)}`);
    if (!data || data.Code !== 200) return [];
    const list = Array.isArray(data.Data) ? data.Data : [];
    return list
      .filter(item => item.status === 1)
      .map(item => ({ wxid: item.wx_id || '', nickname: item.nick_name || '', license: item.license || item.authKey || '' }))
      .filter(item => item.wxid || item.license);
  } else if (protocolType === 'WeChatPadPro') {
    const data = await httpJson(`${server}/admin/GetAllDevices?key=${encodeURIComponent(adminKey)}`);
    if (!data || data.Code !== 200) return [];
    const list = Array.isArray(data.Data) ? data.Data : [];
    return list
      .filter(item => item.status === 1)
      .map(item => ({ wxid: item.deviceId || '', nickname: item.deviceName || '', license: item.authKey || item.license || '' }))
      .filter(item => item.wxid || item.license);
  }
  return [];
}

async function getWxCode(wxid, appid) {
  const server = String(process.env.WECHAT_SERVER || '').replace(/\/$/, '');
  if (!server) throw new Error('未设置 WECHAT_SERVER');
  const adminKey = (process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '').trim();

  const protocolType = await detectProtocol(server, adminKey);

  if (protocolType === 'yyb呆呆' || protocolType === 'Niuzi') {
    const { getYybCode, getAuth } = require('./yybOpenid');
    const { code } = await getYybCode(server, getAuth(adminKey), appid, wxid);
    return code;
  } else {
    const allAccounts = await getOnlineAccounts(server, adminKey, protocolType);
    const acc = allAccounts.find(a => a.wxid === wxid);
    const license = acc?.license || wxid;
    const url = `${server}/applet/JsLogin?key=${encodeURIComponent(license)}`;
    const ret = await httpJson(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: { AppId: appid, Data: '', Opt: 1, PackageName: '', SdkName: '' },
    });
    if (ret?.Code === 200 && ret?.Data?.Code) {
      return ret.Data.Code;
    }
    throw new Error(`wx.login失败: ${JSON.stringify(ret)}`);
  }
}

async function preLoginByCode(code) {
  const payload = {
    code,
    socialPlatform: 'wechat',
    launchOptions: { scene: 1001, query: {} },
  };
  const ret = await httpJson(`${BFF}/users/wechat-pre-login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'User-Agent': UA_BFF,
    },
    body: payload,
  });
  return ret?.data || ret;
}

async function bffSigninStatus(token, yaraUserId) {
  return httpJson(`${BFF}/v2/profile/${encodeURIComponent(yaraUserId)}/signin`, {
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'User-Agent': UA_BFF,
    },
  });
}

async function oauthWeapp(accountId, appid, code) {
  const url = `${OAUTH_BASE}/${accountId}/v2/weapp/oauth`;
  const payload = {
    scope: 'base',
    code,
    watermark: { appid },
    is_group: 'false',
  };
  const ret = await httpJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: payload,
  });

  const data = ret?.data && typeof ret.data === 'object' ? ret.data : ret;
  if (!data?.accessToken) {
    throw new Error(`获取consumer token失败: ${JSON.stringify(ret)}`);
  }
  return data;
}

function buildConsumerParams(clientId, extra = []) {
  const pairs = [
    ['maijsVersion', MAIJS_VERSION],
    ['clientId', clientId],
    ['appVersion', APP_VERSION],
    ['appName', APP_NAME],
    ['envVersion', ENV_VERSION],
    ...extra,
    ['clientTime', nowISO()],
  ];
  const usp = new URLSearchParams();
  for (const [k, v] of pairs) {
    if (Array.isArray(v)) v.forEach(x => usp.append(k, String(x)));
    else if (v !== undefined && v !== null) usp.append(k, String(v));
  }
  return usp.toString();
}

async function consumerReq({ method = 'GET', path, token, accountId, clientId, query = [], body }) {
  const url = `${CONSUMER}${path}?${buildConsumerParams(clientId, query)}`;
  const headers = {
    'X-Requested-With': 'XMLHttpRequest',
    'X-Access-Token': token,
    'X-Account-Id': accountId,
    'content-type': 'application/json; charset=utf-8',
    accept: 'application/json, text/plain, */*',
    'user-agent': UA_CONSUMER,
  };
  const resp = await fetchFn(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!resp.ok) throw new Error(`${method} ${path} -> ${resp.status} ${text.slice(0, 400)}`);
  return data;
}

async function validateBffSession(token, yaraUserId) {
  if (!token || !yaraUserId) return false;
  try {
    await bffSigninStatus(token, yaraUserId);
    return true;
  } catch {
    return false;
  }
}

async function validateConsumerSession(token, accountId) {
  if (!token) return false;
  try {
    const ret = await consumerReq({
      method: 'GET',
      path: '/v2/member',
      token,
      accountId: accountId || ACCOUNT_ID_DEFAULT,
      clientId: uuidv4(),
    });
    return !!ret?.id;
  } catch {
    return false;
  }
}

function currentMonthText() {
  const now = new Date(Date.now() + 8 * 3600 * 1000);
  return `${now.getUTCFullYear()}-${now.getUTCMonth() + 1}`;
}

async function consumerSigninStats(consumerToken, accountId, clientId, withMonth = true) {
  return consumerReq({
    method: 'GET',
    path: '/modules/campaigncenter/signin/stats',
    token: consumerToken,
    accountId,
    clientId,
    query: withMonth ? [['month', currentMonthText()]] : [],
  });
}

async function consumerSigninTodayRewards(consumerToken, accountId, clientId) {
  return consumerReq({
    method: 'GET',
    path: '/modules/campaigncenter/signin/today-rewards',
    token: consumerToken,
    accountId,
    clientId,
  });
}

async function consumerSigninDo(consumerToken, accountId, clientId, templateIds) {
  const path = '/modules/campaigncenter/signin';
  const url = `${CONSUMER}${path}?${buildConsumerParams(clientId, [])}`;
  const headers = {
    'X-Requested-With': 'XMLHttpRequest',
    'X-Access-Token': consumerToken,
    'X-Account-Id': accountId,
    'content-type': 'application/json; charset=utf-8',
    accept: 'application/json, text/plain, */*',
    'user-agent': UA_CONSUMER,
  };
  const body = {
    templateIds: Array.isArray(templateIds) ? templateIds : [],
  };

  const resp = await fetchFn(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });

  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }

  if (resp.ok) return { ok: true, already: false, data, status: resp.status };

  const msg = String(data?.message || data?.msg || '');
  if (resp.status === 400 && Number(data?.code) === 0 && /\u8bf7\u52ff\u91cd\u590d\u6253\u5361|\u91cd\u590d\u6253\u5361|\u5df2\u6253\u5361/.test(msg)) {
    return { ok: true, already: true, data, status: resp.status };
  }

  throw new Error(`POST ${path} -> ${resp.status} ${text.slice(0, 400)}`);
}

async function consumerSigninShare(consumerToken, accountId, clientId) {
  const path = '/modules/campaigncenter/signin/share';
  const url = `${CONSUMER}${path}?${buildConsumerParams(clientId, [])}`;
  const headers = {
    'X-Requested-With': 'XMLHttpRequest',
    'X-Access-Token': consumerToken,
    'X-Account-Id': accountId,
    'content-type': 'application/x-www-form-urlencoded',
    accept: 'application/json, text/plain, */*',
    'user-agent': UA_CONSUMER,
    origin: 'https://servicewechat.com',
    referer: `https://servicewechat.com/${MEMBER_APPID}/22/page-frame.html`,
  };

  const resp = await fetchFn(url, {
    method: 'POST',
    headers,
  });

  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }

  if (resp.ok) return { ok: true, already: false, data, status: resp.status };

  const msg = String(data?.message || data?.msg || data?.error || '');
  if (resp.status === 400 && Number(data?.code) === 0 && /\u64cd\u4f5c\u8fc7\u4e8e\u9891\u7e41|\u8fc7\u4e8e\u9891\u7e41|too\s*frequent/i.test(msg)) {
    return { ok: false, already: false, tooFrequent: true, data, status: resp.status };
  }
  if ((resp.status === 400 || resp.status === 409) && /已分享|重复|请勿重复|上限|already|limit/i.test(msg)) {
    return { ok: true, already: true, data, status: resp.status };
  }

  throw new Error(`POST ${path} -> ${resp.status} ${text.slice(0, 400)}`);
}

function summarizeRewardGroup(group) {
  const arr = Array.isArray(group) ? group : [];
  let score = 0;
  let growth = 0;
  for (const x of arr) {
    if (x?.type === 'score') score += Number(x?.scoreValue ?? x?.score ?? 0) || 0;
    if (x?.type === 'growth') growth += Number(x?.growthValue ?? x?.growth ?? 0) || 0;
  }
  const seg = [];
  if (score) seg.push(`score+${score}`);
  if (growth) seg.push(`growth+${growth}`);
  return seg.join(' ');
}

async function runCampaignSignin(consumerToken, accountId) {
  const clientId = uuidv4();

  const before = await consumerSigninStats(consumerToken, accountId, clientId, true);
  if (before?.hasSignedInToday) {
    const reward = await consumerSigninTodayRewards(consumerToken, accountId, clientId).catch(() => null);
    const rewardTxt = summarizeRewardGroup(reward?.rewardGroup || []);
    return `signin: already done (consecutive ${before?.consecutiveDays || 0}d)${rewardTxt ? ` ${rewardTxt}` : ''}`;
  }

  const signRet = await consumerSigninDo(consumerToken, accountId, clientId, SIGN_TEMPLATE_IDS);
  if (signRet.already) {
    const afterDup = await consumerSigninStats(consumerToken, accountId, clientId, true).catch(() => null);
    const days = afterDup?.consecutiveDays || before?.consecutiveDays || 0;
    return `signin: already done (${signRet.data?.message || 'duplicate'}) (consecutive ${days}d)`;
  }

  const rewardTxt = summarizeRewardGroup(signRet.data?.rewardGroup || []);

  const after = await consumerSigninStats(consumerToken, accountId, clientId, true).catch(() => null);
  if (after?.hasSignedInToday) {
    return `signin: success (consecutive ${after?.consecutiveDays || 0}d)${rewardTxt ? ` ${rewardTxt}` : ''}`;
  }

  return `signin: unconfirmed (${rewardTxt || JSON.stringify(signRet.data || {}).slice(0, 120)})`;
}

async function runCampaignShare(consumerToken, accountId) {
  const clientId = uuidv4();
  let okCount = 0;
  let scoreGain = 0;
  let growthGain = 0;
  let throttleCount = 0;
  let lastDaily = NaN;
  let lastTotal = NaN;

  for (let i = 0; i < SHARE_TIMES; i++) {
    let ret = null;
    for (let r = 0; r < SHARE_RETRY; r++) {
      ret = await consumerSigninShare(consumerToken, accountId, clientId);
      if (!ret?.tooFrequent) break;
      throttleCount++;
      if (r < SHARE_RETRY - 1) await sleep(Math.max(300, SHARE_DELAY_MS));
    }

    if (!ret) break;

    const data = ret?.data || {};
    const msg = String(data?.message || data?.msg || '');
    const already = ret.already || /已分享|重复|请勿重复|上限|already|limit/i.test(msg);

    const daily = Number(data?.dailySharedRewardCount);
    const total = Number(data?.totalSharedRewardCount);
    if (Number.isFinite(daily)) lastDaily = daily;
    if (Number.isFinite(total)) lastTotal = total;

    if (already) break;
    if (ret.tooFrequent) break;

    okCount++;
    for (const x of (Array.isArray(data?.rewardGroup) ? data.rewardGroup : [])) {
      if (x?.type === 'score') scoreGain += Number(x?.scoreValue ?? x?.score ?? 0) || 0;
      if (x?.type === 'growth') growthGain += Number(x?.growthValue ?? x?.growth ?? 0) || 0;
    }

    if (i < SHARE_TIMES - 1) await sleep(Math.max(200, SHARE_DELAY_MS));
  }

  const rewardTxt = [
    scoreGain ? `score+${scoreGain}` : '',
    growthGain ? `growth+${growthGain}` : '',
  ].filter(Boolean).join(' ');

  const cntText = [
    Number.isFinite(lastDaily) ? `daily=${lastDaily}` : '',
    Number.isFinite(lastTotal) ? `total=${lastTotal}` : '',
  ].filter(Boolean).join(', ');

  if (okCount <= 0) {
    if (throttleCount > 0) return `share: throttled (操作过于频繁，重试${throttleCount}次)`;
    return `share: already done${cntText ? ` (${cntText})` : ''}`;
  }

  return `share: success ${okCount}/${SHARE_TIMES}${rewardTxt ? ` ${rewardTxt}` : ''}${cntText ? ` (${cntText})` : ''}`;
}

function pickChannels(member) {
  const socials = Array.isArray(member?.socials) ? member.socials : [];
  const byName = (name) => socials.find(x => (x?.channelName || '').includes(name))?.channel || '';
  const memberCh = byName('会员');
  const wwnCh = byName('问问农');
  const originCh = member?.originFrom?.channel || '';
  return {
    mainChannelId: originCh || wwnCh || memberCh,
    memberChannelId: memberCh || wwnCh || originCh,
  };
}

function eventPayload(eventId, task, memberId) {
  const base1089 = { scene: 1089, utmSource: 'mp_1089' };

  if (eventId === 'maievent-campaigncenter-signin') {
    return {
      name: 'maievent-page-operate',
      properties: {
        url: TASK_CENTER_URL,
        scene: 1037,
        pageContent: '任务中心-页面',
        utmSource: 'mp_1037',
        action: '点击',
        content: '每日签到',
        extra: { taskId: task.id, memberId },
      },
      useMemberChannel: true,
    };
  }

  switch (eventId) {
    case 'c_click_nutrition_plan_card':
      return { name: eventId, properties: { ...base1089, url: 'pages/nutrition/nutrition' }, useMemberChannel: false };
    case 'c_click_fertilizer_item':
      return { name: eventId, properties: { ...base1089, url: 'pages/presentation/list/list', id: 22205 }, useMemberChannel: false };
    case 'c_click_exper_demo_card':
      return { name: eventId, properties: { ...base1089, url: 'pages/farm/farm_story/farm_story' }, useMemberChannel: false };
    case 'c_click_nutrient_story_cards':
      return { name: eventId, properties: { ...base1089, url: 'pages/nutri_story/nutri_story' }, useMemberChannel: false };
    case 'c_view_post_page_detail':
      return { name: eventId, properties: { ...base1089, url: 'pages/common_post_detail/common_post_detail?topicId=694bb28963579047bcdb7d18&source=mai' }, useMemberChannel: false };
    case 'c_click_share_button':
      return { name: eventId, properties: { ...base1089, url: 'pages/nutrition/nutrition' }, useMemberChannel: false };
    case 'c_click_share_pdd_button':
      return { name: eventId, properties: { ...base1089, url: 'pages/pdd/report/report' }, useMemberChannel: false };
    default:
      return null;
  }
}

function leftTimes(task) {
  const mr = task?.memberReward || {};
  if (mr.noLimit) return 1;
  const raw = Number(mr.rewardCount);
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  return Math.min(raw, MAX_PER_TASK);
}

async function autoTasks(consumerToken, accountId, clientId) {
  const member = await consumerReq({
    method: 'GET',
    path: '/v2/member',
    token: consumerToken,
    accountId,
    clientId,
  });

  const memberId = member?.id;
  if (!memberId) throw new Error(`获取member失败: ${JSON.stringify(member).slice(0, 300)}`);

  const beforeScore = Number(member?.annualAccumulatedScore || 0);
  const beforeGrowth = Number(member?.growth || 0);

  const channels = pickChannels(member);
  const mainChannelId = channels.mainChannelId;
  const memberChannelId = channels.memberChannelId;

  if (!mainChannelId && !memberChannelId) {
    throw new Error('无法识别 channelId');
  }

  const tasks = await consumerReq({
    method: 'GET',
    path: '/v2/memberTasks',
    token: consumerToken,
    accountId,
    clientId,
    query: [
      ['listCondition.page', 1],
      ['listCondition.perPage', 1000],
      ['listCondition.orderBy[]', 'createdAt'],
      ['types[]', ['task', 'scoreCampaignInformation', 'invitation']],
    ],
  });

  const items = Array.isArray(tasks?.items) ? tasks.items : [];
  const candidates = items.filter(t => {
    if (!t?.isEnabled) return false;
    const ev = t?.eventTrigger?.[0]?.eventId;
    return ev && EVENT_WHITELIST.includes(ev);
  });

  if (!candidates.length) {
    logWarn('没有可执行的自动任务（白名单命中为0）');
    return;
  }

  logInfo(`自动任务候选: ${candidates.length} 个`);

  let sent = 0;
  for (const t of candidates) {
    const ev = t?.eventTrigger?.[0]?.eventId;
    const times = leftTimes(t);
    if (times <= 0) {
      if (DEBUG_MODE) {
        if (ev === 'maievent-campaigncenter-signin') {
          try {
            const st = await consumerSigninStats(consumerToken, accountId, clientId, true);
            if (st?.hasSignedInToday) {
              logInfo(`${t.name} (${ev}) 今日已完成`);
            } else {
              logWarn(`${t.name} (${ev}) rewardCount=0，但签到状态显示未签到`);
            }
          } catch (e) {
            logWarn(`${t.name} (${ev}) rewardCount=0，且签到状态查询失败`);
          }
        } else {
          logInfo(`${t.name} (${ev}) 跳过：rewardCount=0（大概率已完成/达上限）`);
        }
      }
      continue;
    }

    const mapped = eventPayload(ev, t, memberId);
    if (!mapped) {
      if (DEBUG_MODE) logWarn(`${t.name} (${ev}) 没有事件模板，跳过`);
      continue;
    }

    const channelId = mapped.useMemberChannel
      ? (memberChannelId || mainChannelId)
      : (mainChannelId || memberChannelId);

    let taskOk = 0;
    let taskFail = 0;
    for (let i = 0; i < times; i++) {
      const body = {
        clientId,
        logs: [{
          name: mapped.name,
          properties: JSON.stringify(mapped.properties),
          id: uuidv4(),
          occurredAt: nowISO(),
        }],
        channelId,
      };

      const ret = await consumerReq({
        method: 'POST',
        path: '/v2/memberEventLogs',
        token: consumerToken,
        accountId,
        clientId,
        body,
      });

      const failed = Array.isArray(ret?.failedLogs) ? ret.failedLogs.length : 0;
      if (failed) {
        taskFail++;
        if (DEBUG_MODE) logWarn(`${t.name} [${ev}] ${i + 1}/${times} 上报失败`);
      } else {
        taskOk++;
        if (DEBUG_MODE) logOk(`${t.name} [${ev}] ${i + 1}/${times} 上报成功`);
      }
      sent++;
      await sleep(700);
    }

    if (!DEBUG_MODE) {
      if (taskOk > 0) {
        logOk(`${t.name} [${ev}] 上报成功 ${taskOk}/${times}${taskFail ? `, 失败${taskFail}` : ''}`);
      } else {
        logWarn(`${t.name} [${ev}] 全部失败 (${taskFail}/${times})`);
      }
    }
  }

  const after = await consumerReq({
    method: 'GET',
    path: '/v2/member',
    token: consumerToken,
    accountId,
    clientId,
  });

  const afterScore = Number(after?.annualAccumulatedScore || 0);
  const afterGrowth = Number(after?.growth || 0);

  section('自动任务汇总');
  logInfo(`事件上报数: ${sent}`);
  logInfo(`积分: ${beforeScore} -> ${afterScore} (Δ ${afterScore - beforeScore})`);
  logInfo(`成长值: ${beforeGrowth} -> ${afterGrowth} (Δ ${afterGrowth - beforeGrowth})`);
}

async function runOne(wxid, idx, cache) {
  hr('═');
  section(`账号 ${idx}`);

  let bffToken = '';
  let yaraUserId = '';
  let discourseUsername = '';
  let consumerToken = '';
  let accountId = ACCOUNT_ID_DEFAULT;
  let note = '';

  logInfo(`账号类型: wxid (${wxid})`);
  const cacheKey = `wxid:${wxid}`;
  const needConsumer = ENABLE_SIGN || ENABLE_TASKS || ENABLE_SHARE;

  const cached = pickCache(cache, cacheKey);
  let usedCache = false;

  if (cached) {
    const bffRequired = !!(cached.bffToken && cached.yaraUserId);
    const bffOk = !bffRequired || await validateBffSession(cached.bffToken, cached.yaraUserId);
    const consumerOk = !needConsumer || await validateConsumerSession(cached.consumerToken, cached.accountId || accountId);
    if (bffOk && consumerOk) {
      usedCache = true;
      bffToken = cached.bffToken || '';
      yaraUserId = cached.yaraUserId || '';
      discourseUsername = cached.discourseUsername || '';
      consumerToken = cached.consumerToken || '';
      accountId = cached.accountId || accountId;
      note = cached.note || '';
      logOk(`缓存命中: ${wxid}`);
    } else {
      logWarn(`缓存失效，准备刷新: ${wxid}`);
    }
  }

  if (!usedCache) {
    const codeMain = await getWxCode(wxid, MAIN_APPID);
    logInfo(`主小程序 code 获取成功`);

    const loginInfo = await preLoginByCode(codeMain);
    bffToken = loginInfo?.token || '';
    yaraUserId = loginInfo?.yaraUserId || '';
    discourseUsername = loginInfo?.discourseUsername || '';

    if (needConsumer) {
      const codeMember = await getWxCode(wxid, MEMBER_APPID);
      const oauthData = await oauthWeapp(accountId, MEMBER_APPID, codeMember);
      consumerToken = oauthData.accessToken;
      const p = decodeJwtPayload(consumerToken) || {};
      accountId = p.aid || accountId;
    }

    putCache(cache, cacheKey, {
      bffToken,
      yaraUserId,
      discourseUsername,
      consumerToken,
      accountId,
      note,
    });
    saveCache(cache);
    logOk(`缓存更新完成: ${wxid}`);
  }

  if (DEBUG_MODE) {
    logOk(`loginInfo: yaraUserId=${yaraUserId || '-'} | discourseUsername=${discourseUsername || '-'}`);
  } else {
    logOk(`loginInfo: yaraUserId=${yaraUserId || '-'}`);
  }
  if (bffToken && (DEBUG_MODE || LOG_FULL_TOKEN)) {
    logInfo(`BFF token: ${LOG_FULL_TOKEN ? bffToken : maskMiddle(bffToken, 20, 12)}`);
  }

  if (bffToken) printTokenInfo('BFF token', bffToken);
  if (consumerToken) printTokenInfo('Consumer token', consumerToken);
  if (note) logInfo(`备注: ${note}`);

  if (bffToken && yaraUserId) {
    const sign = await bffSigninStatus(bffToken, yaraUserId);
    logInfo(`BFF签到状态: ${sign?.signedIn ? '已签到' : '未签到'}`);
  } else if (bffToken && !yaraUserId) {
    logWarn('缺少 yaraUserId，跳过 BFF 签到状态查询');
  }

  if (ENABLE_SIGN) {
    if (!consumerToken) {
      logWarn('缺少 consumer token，跳过签到任务');
    } else {
      try {
        const signMsg = await runCampaignSignin(consumerToken, accountId);
        logOk(signMsg);
      } catch (e) {
        logErr(`签到失败: ${e.message || e}`);
      }
    }
  }

  if (ENABLE_SHARE) {
    if (!consumerToken) {
      logWarn('缺少 consumer token，跳过分享任务');
    } else {
      try {
        const shareMsg = await runCampaignShare(consumerToken, accountId);
        if (/^share:\s*throttled/i.test(shareMsg)) logWarn(shareMsg);
        else if (/^share:\s*already/i.test(shareMsg)) logInfo(shareMsg);
        else logOk(shareMsg);
      } catch (e) {
        logErr(`分享任务失败: ${e.message || e}`);
      }
    }
  }

  if (!ENABLE_TASKS) return;

  if (!consumerToken) {
    logWarn('缺少 consumer token，跳过自动任务');
    return;
  }

  section('自动任务');
  const clientId = uuidv4();
  await autoTasks(consumerToken, accountId, clientId);
}

(async () => {
  try {
    const wechatServer = (process.env.WECHAT_SERVER || '').trim();
    if (!wechatServer) {
      logErr('未设置环境变量 WECHAT_SERVER');
      process.exit(1);
    }

    const adminKey = (process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '').trim();
    const server = wechatServer.replace(/\/$/, '');
    const protocolType = await detectProtocol(server, adminKey);
    logInfo(`协议服务: ${wechatServer} (${protocolType})`);

    if (protocolType === 'Unknown') {
      logErr('未知的协议服务类型，请检查WECHAT_SERVER');
      process.exit(1);
    }

    let onlineAccounts = await getOnlineAccounts(server, adminKey, protocolType);
    const wxIdFilter = (process.env.WX_ID || '').trim();
    if (wxIdFilter) {
      const targetWxIds = wxIdFilter.split('&').map(s => s.trim()).filter(Boolean);
      onlineAccounts = onlineAccounts.filter(a => targetWxIds.includes(a.wxid));
      logInfo(`根据WX_ID筛选后获得 ${onlineAccounts.length} 个账号`);
    }

    if (!onlineAccounts.length) {
      logErr('未获取到在线账号，请检查协议服务和账号状态');
      process.exit(1);
    }

    const accounts = onlineAccounts.map(a => a.wxid);
    const cache = loadCache();
    hr('═');
    logInfo(`问问农任务开始: 账号 ${accounts.length} 个`);
    logInfo(`缓存文件: ${CACHE_NAME}.json`);
    logInfo(`日志模式: ${DEBUG_MODE ? 'DEBUG' : 'NORMAL'} | ${LOG_PLAIN ? 'PLAIN' : 'EMOJI'}`);

    for (let i = 0; i < accounts.length; i++) {
      try {
        await runOne(accounts[i], i + 1, cache);
      } catch (e) {
        logErr(`账号 ${i + 1} 执行失败: ${e.message || e}`);
      }
    }
    hr('═');
    logOk('全部账号执行完成');
  } catch (e) {
    logErr(`脚本异常: ${e.message || e}`);
  }
})();
