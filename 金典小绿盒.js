// name:金典小绿盒
// cron:5 0 8 * *

/**
 * 金典小绿盒 - 开盒抽奖 + 碎片收集
 *
 * 流程：
 *   1. 协议登录
 *   2. 查询活动状态（每月8号开盒日 lotteryStatus=1）
 *   3. 每日签到（会员中心 /member/daily/sign，非开盒日也可 +1 开盒次数）
 *   4. 浏览/扫码/奶卡等任务 → task/isPop 领奖
 *   5. 分享任务（shareTask + id13/14 userShareNew 配对 drawShareNew）
 *   6. 碎片互赠 → 开盒抽奖（lottery / newLottery）
 *
 * 变量：
 *   WECHAT_SERVER            协议服务地址
 *   ADMIN_KEY                协议管理密钥
 *   WX_ID                    可选，指定微信账号ID，多个用&分隔
 *   JINDIAN_PROXY            可选，代理地址
 *   JINDIAN_PROXY_API        可选，代理提取API
 *   JINDIAN_PROXY_RETRY      可选，WAF重试次数（默认5）
 *   JINDIAN_XLH_MAX_DRAW     最大开盒次数（默认 30）
 *
 * 缓存：jindian_cache.json
 */

const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');
const { URL } = require('url');
const axios = require('axios');
let HttpsProxyAgent = null;
try {
  HttpsProxyAgent = require('https-proxy-agent').HttpsProxyAgent || require('https-proxy-agent');
} catch (_) {
  HttpsProxyAgent = null;
}

// ── 常量 ──────────────────────────────────────────────────────────────────

const SCRIPT_NAME   = '金典小绿盒';
const APPID         = 'wxf32616183fb4511e';
const APP_KEY       = String(process.env.JINDIAN_XLH_APP_KEY || process.env.JINDIAN_APP_KEY || 'zd123a10187c995e97').trim();
const TENANT_ID     = '1718857849685876737';
const MS_BASE       = 'https://msmarket.msx.digitalyili.com';
const API_BASE      = 'https://wx-camp-hc-api-02.mscampapi.digitalyili.com/wx-camp-jdxlh/stage/';
const WECHAT_SERVER = String(process.env.WECHAT_SERVER || '').trim();
const ADMIN_KEY     = String(process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '').trim();
const WX_ID_FILTER  = String(process.env.WX_ID || '').trim();
const MAX_DRAW      = Math.max(1, Number(process.env.JINDIAN_XLH_MAX_DRAW || 30));
const PROXY_DIRECT  = String(process.env.JINDIAN_PROXY || '').trim();
const PROXY_API     = String(process.env.JINDIAN_PROXY_API || '').trim();
const PROXY_RETRY   = Math.max(1, Number(process.env.JINDIAN_PROXY_RETRY || 5));
const CACHE_FILE    = path.join(__dirname, 'jindian_cache.json');
const ENV_CHECK_FILE = path.join(__dirname, 'env_check.json');
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254186b) XWEB/19481';
const MS_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.34(0x18002230) NetType/WIFI Language/zh_CN';

// ── 工具 ──────────────────────────────────────────────────────────────────

const sleep = ms => new Promise(r => setTimeout(r, ms));
const rand  = (a, b) => Math.floor(Math.random() * (b - a + 1)) + a;
const pick  = arr => arr[rand(0, arr.length - 1)];
const log   = (s = '') => console.log(s);
const proxyState = { url: '', agent: null };

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = rand(0, i);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function createCallId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function readJson(file, def = {}) { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return def; } }
function writeJson(file, obj) { fs.writeFileSync(file, JSON.stringify(obj, null, 2), 'utf8'); }

function getMsg(d) {
  if (!d) return '未知';
  if (typeof d === 'string') return d.slice(0, 200);
  return d.msg || d.message || d.error?.msg || JSON.stringify(d).slice(0, 200);
}

function jwtExp(token) {
  try {
    const p = String(token || '').split('.')[1];
    const obj = JSON.parse(Buffer.from(p.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8'));
    return Number(obj.exp || 0) * 1000;
  } catch { return 0; }
}
function tokenValid(token) {
  const exp = jwtExp(token);
  return !!token && (!exp || exp - Date.now() > 5 * 60 * 1000);
}
function isTokenBad(data) {
  return data?.code === -1 || /TOKEN已失效|token.*失效|登录过期/i.test(getMsg(data));
}

// ── 代理 ─────────────────────────────────────────────────────────────────

function normalizeProxy(raw) {
  const s = String(raw || '').trim();
  if (!s) return '';
  const url = /^https?:\/\//i.test(s) ? s : `http://${s}`;
  try {
    const u = new URL(url);
    if (!u.hostname || !u.port) return '';
    return u.toString();
  } catch {
    return '';
  }
}
function extractProxy(text) {
  const s = String(text || '').trim();
  if (!s) return '';
  for (const line of s.split(/\r?\n/).map(x => x.trim()).filter(Boolean)) {
    const direct = normalizeProxy(line);
    if (direct) return direct;
  }
  const candidates = s.match(/(?:https?:\/\/)?(?:[^\s@"'<>]+@)?(?:\d{1,3}(?:\.\d{1,3}){3}|localhost|[a-z0-9-]+(?:\.[a-z0-9-]+)+):\d{2,5}/ig) || [];
  for (const item of candidates) {
    const proxy = normalizeProxy(item);
    if (proxy) return proxy;
  }
  return '';
}
async function fetchProxy() {
  if (PROXY_DIRECT) return normalizeProxy(PROXY_DIRECT);
  if (!PROXY_API) return '';
  try {
    const { data } = await axios.get(PROXY_API, { timeout: 12000, proxy: false, validateStatus: () => true });
    const proxy = extractProxy(typeof data === 'string' ? data : JSON.stringify(data));
    if (!proxy) log('⚠️ 代理API未返回有效 host:port，改用直连');
    return proxy;
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
  try {
    proxyState.url = p;
    proxyState.agent = new HttpsProxyAgent(p);
    log(`🌐 使用代理: ${p}`);
    return true;
  } catch (e) {
    proxyState.url = '';
    proxyState.agent = null;
    log(`⚠️ 代理地址无效，改用直连: ${p} (${e.message})`);
    return false;
  }
}
function isWafBlocked(x) {
  return /WAF|拦截|block-pages|status:403|status=403|ErrorCode:639|请求已中断/i.test(String(x || ''));
}

// ── 协议服务：自动获取在线账号 ───────────────────────────────────────────

async function detectProtocolType() {
  let envData = readJson(ENV_CHECK_FILE, {});
  if (envData.protocol_type === 'yyb呆呆') {
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

// ── HTTP ──────────────────────────────────────────────────────────────────

function request({ url, method = 'POST', headers = {}, body = null, timeout = 25000 }) {
  return new Promise(resolve => {
    let u; try { u = new URL(url); } catch { return resolve({ status: 0, data: null }); }
    const buf = body == null ? null : Buffer.from(JSON.stringify(body), 'utf8');
    if (buf) headers['Content-Length'] = buf.length;
    const mod = u.protocol === 'http:' ? http : https;
    const port = Number(u.port) || (u.protocol === 'http:' ? 80 : 443);
    const opts = { method, hostname: u.hostname, port, path: u.pathname + u.search, headers, timeout };
    if (proxyState.agent) opts.agent = proxyState.agent;
    const req = mod.request(opts, res => {
      const c = [];
      res.on('data', x => c.push(x));
      res.on('end', () => {
        const text = Buffer.concat(c).toString('utf8');
        let data = text;
        if (/json/i.test(res.headers['content-type'] || '')) { try { data = JSON.parse(text); } catch {} }
        resolve({ status: res.statusCode, data });
      });
    });
    req.on('timeout', () => req.destroy());
    req.on('error', () => resolve({ status: 0, data: null }));
    if (buf) req.write(buf);
    req.end();
  });
}

// ── 金典主站登录 ─────────────────────────────────────────────────────────

function msH(token = '') {
  return {
    Host: 'msmarket.msx.digitalyili.com',
    Connection: 'keep-alive',
    Accept: 'application/json, text/plain, */*',
    Origin: 'https://servicewechat.com',
    Referer: `https://servicewechat.com/${APPID}/815/page-frame.html`,
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'register-source': '',
    shareid: '',
    xweb_xhr: '1',
    scene: '1008',
    'access-token': token,
    'User-Agent': MS_UA,
    channel: 'copyUrl',
    'Content-Type': 'application/json',
    'tenant-id': TENANT_ID,
    'X-WX-HTTP-MODE': 'REROUTE',
    'X-WX-CONF-VERSION': '0',
    'x-wx-call-id': createCallId(),
    'x-wx-source': 'wx_client',
    'x-wx-appid': APPID,
    'x-envoy-expected-rq-timeout-ms': '15000',
  };
}

async function wxCode(wxid) {
  /* 账号环境变量可空 */
  const { getYybCode, getAuth } = require('./yybOpenid');
  const { code } = await getYybCode(WECHAT_SERVER, getAuth(), APPID, wxid);
  return String(code);
}

async function msLogin(jsCode) {
  let last = '';
  for (let i = 1; i <= PROXY_RETRY; i++) {
    const { status, data } = await request({ url: `${MS_BASE}/gateway/api/auth/account/login`, headers: msH(''), body: { jsCode } });
    const token = data?.data?.accessToken || data?.data?.access_token || data?.data?.token;
    if (token) return String(token);
    last = `ms登录失败(${status}): ${getMsg(data)}`;
    if (i < PROXY_RETRY && isWafBlocked(last)) {
      const ok = await switchProxy(true);
      log(ok ? `⚠️ ms登录WAF，切换代理重试 ${i}/${PROXY_RETRY}` : `⚠️ ms登录WAF，无可用代理 ${i}/${PROXY_RETRY}`);
      if (!ok) break;
      await sleep(rand(500, 1000));
      continue;
    }
    break;
  }
  throw new Error(last);
}

async function msAuthCode(msToken) {
  let last = '';
  for (let i = 1; i <= PROXY_RETRY; i++) {
    const { status, data } = await request({ url: `${MS_BASE}/developer/oauth2/buyer/authorize?app_key=${encodeURIComponent(APP_KEY)}`, method: 'GET', headers: msH(msToken) });
    const d = data?.data;
    const code = (typeof d === 'string' && /^[0-9a-f]{32}$/i.test(d) ? d : null)
      || d?.authorizationCode || d?.authorization_code || data?.authorization_code;
    if (code) return String(code);
    last = `授权码失败(${status}): ${getMsg(data)}`;
    if (i < PROXY_RETRY && isWafBlocked(last)) {
      const ok = await switchProxy(true);
      log(ok ? `⚠️ authorize WAF，切换代理重试 ${i}/${PROXY_RETRY}` : `⚠️ authorize WAF，无可用代理 ${i}/${PROXY_RETRY}`);
      if (!ok) break;
      await sleep(rand(500, 1000));
      continue;
    }
    break;
  }
  throw new Error(last);
}

function msOk(data) {
  return Boolean(data && (data.status === true || data.success === true || data.code === 0 || data.code === 200));
}

async function msApi(pathname, method = 'GET', msToken = '', body = null) {
  let last = '';
  for (let i = 1; i <= PROXY_RETRY; i++) {
    const { status, data } = await request({
      url: `${MS_BASE}/gateway/api${pathname}`,
      method,
      headers: msH(msToken),
      body: method.toUpperCase() === 'GET' ? undefined : (body ?? {}),
    });
    const blob = typeof data === 'string' ? data : JSON.stringify(data || {});
    if (status !== 403 && !isWafBlocked(blob)) return data;
    last = `会员接口WAF(${status}): ${pathname}`;
    if (i < PROXY_RETRY) {
      const ok = await switchProxy(true);
      log(ok ? `⚠️ ${pathname} WAF，切换代理重试 ${i}/${PROXY_RETRY}` : `⚠️ ${pathname} WAF，无可用代理 ${i}/${PROXY_RETRY}`);
      if (!ok) break;
      await sleep(rand(500, 1000));
      continue;
    }
    break;
  }
  throw new Error(last || `会员接口失败: ${pathname}`);
}

// ── 活动客户端 ────────────────────────────────────────────────────────────

class Client {
  constructor(acc, cache) {
    this.wxid = acc.wxid;
    this.remark = acc.remark;
    this.cache = cache;
    const c = cache[this.wxid] || {};
    this.token = c.jdxlhToken || '';
    this.msToken = c.msToken || '';
    this.openId = c.jdxlhOpenId || '';
    this.userInfo = {};
    this.activity = {};
    this.awards = [];
  }

  save() {
    const old = this.cache[this.wxid] || {};
    this.cache[this.wxid] = {
      ...old, wxid: this.wxid, remark: this.remark,
      msToken: this.msToken || old.msToken || '',
      jdxlhToken: this.token || '',
      jdxlhOpenId: this.openId || '',
      jdxlhUpdateAt: new Date().toLocaleString('zh-CN', { hour12: false }),
    };
    writeJson(CACHE_FILE, this.cache);
  }

  campH() {
    return {
      Host: 'wx-camp-hc-api-02.mscampapi.digitalyili.com', Connection: 'keep-alive',
      Authorization: this.token || '', 'User-Agent': UA,
      Accept: 'application/json, text/plain, */*', xweb_xhr: '1',
      'Content-Type': 'application/json',
      Referer: `https://servicewechat.com/${APPID}/815/page-frame.html`,
    };
  }

  async api(pathname, body = {}, retry = true) {
    const { status, data } = await request({ url: `${API_BASE}${pathname}`, headers: this.campH(), body });
    if (retry && (status === 401 || status === 403 || isTokenBad(data))) {
      log(`♻️ ${this.remark} token失效，重新登录`);
      this.token = '';
      await this.login(true);
      return this.api(pathname, body, false);
    }
    return data;
  }

  async login(force = false) {
    if (!force && tokenValid(this.token)) {
      log(`🔑 ${this.remark} 使用缓存CK`);
      return;
    }
    log(`🔄 ${this.remark} 协议登录...`);
    const jsCode = await wxCode(this.wxid);
    this.msToken = await msLogin(jsCode);
    const authCode = await msAuthCode(this.msToken);
    const d = await this.api('userLogin', { code: authCode }, false);
    if (d?.code !== 1 || !d?.data?.token) throw new Error(`活动登录失败: ${getMsg(d)}`);
    this.token = String(d.data.token);
    this.openId = d.data.openId || this.openId || '';
    this.save();
    log(`✅ ${this.remark} 登录成功 openId:${this.openId}`);
  }

  async getUserInfo() {
    const d = await this.api('qryUserInfo', {});
    if (d?.code === 1 && d.data) {
      this.userInfo = d.data;
      this.openId = d.data.openId || this.openId;
      this.save();
    }
    return this.userInfo;
  }

  async qryActivity() {
    const d = await this.api('qryActivity', {});
    if (d?.code === 1 && d.data) {
      this.activity = d.data;
    }
    return this.activity;
  }

  async taskList(pageType = 1) {
    const d = await this.api('task/list', { pageType });
    return Array.isArray(d?.data) ? d.data : [];
  }

  async browseTask(body = {}) {
    const d = await this.api('browseTask', body);
    return d;
  }

  async shareTask(body = {}) {
    const d = await this.api('shareTask', body);
    return d;
  }

  async handleTaskPop() {
    const d = await this.api('task/isPop', {});
    const arr = Array.isArray(d?.data) ? d.data : [];
    for (const p of arr) {
      if (p?.id) {
        await this.api('closePop', { popType: 4, userTaskId: p.id });
        log(`  🎁 ${this.remark} 领取任务奖励: ${p.taskName || p.id}`);
        await sleep(rand(300, 600));
      }
    }
    return arr.length;
  }

  async collectTasks() {
    const byId = new Map();
    for (let pt = 1; pt <= 3; pt++) {
      const tasks = await this.taskList(pt);
      for (const t of tasks) {
        if (t && t.id != null) byId.set(Number(t.id), t);
      }
      await sleep(rand(120, 260));
    }
    return [...byId.values()];
  }

  isBrowseTask(t) {
    const id = Number(t?.id);
    const name = String(t?.name || '');
    if ([8].includes(id)) return true;
    if (/浏览|访问|专区|活动页/.test(name)) return true;
    if ([1, 3, 5, 6, 7, 11, 12, 13, 14, 15].includes(id)) return false;
    return false;
  }

  isSharePairTask(t) {
    const id = Number(t?.id);
    const name = String(t?.name || '');
    return [13, 14].includes(id) || /携手|分享好友|邀请朋友/.test(name);
  }

  async getClaimPrizeId() {
    try {
      const pop = await this.api('debris/isPop', {});
      const arr = Array.isArray(pop?.data) ? pop.data : [];
      for (const p of arr) {
        const id = p?.claimPrizeId || p?.id;
        if (id) return id;
      }
    } catch {}
    try {
      const prizes = await this.qryPrize();
      for (const p of prizes) {
        if (p?.claimPrizeId) return p.claimPrizeId;
        if (p?.acquireDebris && p?.id) return p.id;
      }
    } catch {}
    if (this.userInfo?.claimPrizeId) return this.userInfo.claimPrizeId;
    if (this.activity?.claimPrizeId) return this.activity.claimPrizeId;
    return '';
  }

  async doBrowseTask(task = null) {
    // 小程序：离开 fourUrl 页且停留>10s 后调 browseTask()；协议侧可直接完成
    const act = this.activity || await this.qryActivity();
    const fourUrl = act?.fourUrl || '';
    if (fourUrl) log(`  🧭 ${this.remark} 浏览目标: ${fourUrl}`);
    await sleep(rand(1200, 2200));
    const body = task?.id ? { taskId: task.id } : {};
    const d = await this.browseTask(body);
    if (d?.code === 1) {
      log(`  📖 ${this.remark} 浏览任务完成${task ? `[${task.name || task.id}]` : ''}`);
      return true;
    }
    if (task?.id) {
      const d2 = await this.browseTask({});
      if (d2?.code === 1) {
        log(`  📖 ${this.remark} 浏览任务完成[${task.name || task.id}]`);
        return true;
      }
      log(`  ⚠️ ${this.remark} 浏览任务[${task.name || task.id}]: ${getMsg(d2 || d)}`);
      return false;
    }
    log(`  ⚠️ ${this.remark} 浏览任务: ${getMsg(d)}`);
    return false;
  }

  async userShareNew(claimPrizeId = '') {
    const body = claimPrizeId ? { id: claimPrizeId } : {};
    const d = await this.api('userShareNew', body);
    if (d?.code === 1) {
      const shareId = d.data?.id || '';
      log(`  🔗 ${this.remark} 分享成功 shareId:${shareId}${claimPrizeId ? ` claimPrizeId:${claimPrizeId}` : ''}`);
      return shareId;
    }
    // 无 claimPrizeId 失败时再试空参，兼容部分场次
    if (claimPrizeId) {
      const d2 = await this.api('userShareNew', {});
      if (d2?.code === 1) {
        const shareId = d2.data?.id || '';
        log(`  🔗 ${this.remark} 分享成功(空参) shareId:${shareId}`);
        return shareId;
      }
      log(`  ⚠️ ${this.remark} 分享: ${getMsg(d2 || d)}`);
      return null;
    }
    log(`  ⚠️ ${this.remark} 分享: ${getMsg(d)}`);
    return null;
  }

  async drawShareNew(shareId) {
    if (!shareId) return false;
    const d = await this.api('drawShareNew', { id: shareId });
    if (d?.code === 1) {
      log(`  🎁 ${this.remark} 领取分享奖励成功`);
      await this.handleTaskPop();
      return true;
    }
    log(`  ⚠️ ${this.remark} 领取分享: ${getMsg(d)}`);
    return false;
  }

  async createShareForPair() {
    await this.shareTask({});
    const claimPrizeId = await this.getClaimPrizeId();
    return this.userShareNew(claimPrizeId);
  }

  async qryPrize() {
    const d = await this.api('all/prize', {});
    if (d?.code === 1 && Array.isArray(d?.data)) return d.data;
    const d2 = await this.api('qryPrize', {});
    return Array.isArray(d2?.data) ? d2.data : [];
  }

  async debrisList(prizeId) {
    const d = await this.api('debris/list', { prizeId });
    return Array.isArray(d?.data) ? d.data : [];
  }

  async debrisGive(id) {
    const d = await this.api('debris/give', { id });
    if (d?.code === 1 && d.data) {
      log(`  🎁 ${this.remark} 赠送碎片成功: ${d.data.name || d.data.id}`);
      return d.data;
    }
    log(`  ⚠️ ${this.remark} 赠送碎片: ${getMsg(d)}`);
    return null;
  }

  async debrisDrawGive(id) {
    const d = await this.api('debris/drawGive', { id });
    if (d?.code === 1) {
      log(`  🎁 ${this.remark} 领取赠送碎片成功`);
      return true;
    }
    log(`  ⚠️ ${this.remark} 领取赠送碎片: ${getMsg(d)}`);
    return false;
  }

  async debrisIsPop() {
    const d = await this.api('debris/isPop', {});
    const arr = Array.isArray(d?.data) ? d.data : [];
    for (const p of arr) {
      if (p?.id && p.isFinishTask) {
        await this.api('closePop', { popType: 2, debrisExchangeId: p.id });
        log(`  🧩 ${this.remark} 碎片兑换完成: ${p.debrisName || p.id}`);
        await sleep(rand(300, 600));
      }
    }
    return arr;
  }

  async getGiveableDebris() {
    const prizes = await this.qryPrize();
    log(`  🔍 ${this.remark} 奖品列表: ${prizes.length}个`);
    const giveable = [];
    for (const p of prizes) {
      if (!p.id) continue;
      const list = await this.debrisList(p.id);
      for (const d of list) {
        if (d.residueNum > 0 && d.id) {
          log(`    ${this.remark} [${p.name || p.id}] 碎片${d.type}: 持有${d.residueNum} ✓可赠`);
          giveable.push({ prizeId: p.id, prizeName: p.name || p.id, debrisId: d.id, debrisType: d.type, residueNum: d.residueNum });
        }
      }
      await sleep(rand(200, 400));
    }
    if (!giveable.length) log(`  ${this.remark} 无碎片可赠送`);
    return giveable;
  }

  async scanLog() {
    const d = await this.api('scan/log', {});
    if (d?.code === 1 && d.data) {
      log(`  📦 ${this.remark} 扫码任务确认成功`);
      return true;
    }
    return false;
  }

  async finishNaiKa() {
    const d = await this.api('finishNaiKa', {});
    if (d?.code === 1 && d.data) {
      log(`  🥛 ${this.remark} 奶卡激活任务确认成功`);
      return true;
    }
    return false;
  }

  async ensureMsToken(force = false) {
    if (!force && tokenValid(this.msToken)) return this.msToken;
    const jsCode = await wxCode(this.wxid);
    this.msToken = await msLogin(jsCode);
    this.save();
    return this.msToken;
  }

  async dailySign() {
    await this.ensureMsToken(false);
    const beforeInfo = await this.getUserInfo();
    const beforeNum = Number(beforeInfo.lotteryNum || 0);

    let status;
    try {
      status = await msApi('/member/sign/status', 'GET', this.msToken);
    } catch (e) {
      await this.ensureMsToken(true);
      status = await msApi('/member/sign/status', 'GET', this.msToken);
    }

    if (status?.data?.signed) {
      log(`  ✅ ${this.remark} 每日签到: 今日已签 开盒:${beforeNum}`);
      return { signed: true, changed: false, lotteryNum: beforeNum };
    }

    let signResp;
    try {
      signResp = await msApi('/member/daily/sign', 'POST', this.msToken, {});
    } catch (e) {
      await this.ensureMsToken(true);
      signResp = await msApi('/member/daily/sign', 'POST', this.msToken, {});
    }

    if (!msOk(signResp) && !/已签到|今日已|已经|重复/.test(getMsg(signResp))) {
      throw new Error(`会员签到失败: ${getMsg(signResp)}`);
    }

    await sleep(rand(400, 800));
    try { await this.browseTask({ taskId: 1 }); } catch {}
    await this.handleTaskPop();
    const afterInfo = await this.getUserInfo();
    const afterNum = Number(afterInfo.lotteryNum || 0);
    const parts = ['签到成功'];
    if (afterNum > beforeNum) parts.push(`开盒+${afterNum - beforeNum}`);
    parts.push(`开盒:${afterNum}`);
    log(`  📅 ${this.remark} 每日签到: ${parts.join(' ')}`);
    return { signed: true, changed: true, lotteryNum: afterNum, beforeNum };
  }

  async lotteryOnce() {
    const d = await this.api('lottery', {});
    if (d?.code === 1 && d.data) {
      const name = d.data.awardName || d.data.prizeName || d.data.name || '未知';
      this.awards.push(name);
      log(`  🎉 ${this.remark} 开盒: ${name}`);
      return true;
    }
    log(`  ⚠️ ${this.remark} 开盒失败: ${getMsg(d)}`);
    return false;
  }

  async newLotteryOnce() {
    const d = await this.api('newLottery', {});
    if (d?.code === 1 && d.data) {
      const name = d.data.awardName || d.data.prizeName || d.data.name || '未知';
      this.awards.push(name);
      log(`  🎉 ${this.remark} 新开盒: ${name}`);
      return true;
    }
    log(`  ⚠️ ${this.remark} 新开盒: ${getMsg(d)}`);
    return false;
  }

  async lotteryAll() {
    let info = await this.getUserInfo();
    let num = Number(info.lotteryNum || 0);
    const act = this.activity;

    if (act.lotteryStatus === 0) {
      log(`  ⏰ ${this.remark} 未到开盒时间（每月8号）`);
      return 0;
    }

    log(`🎰 ${this.remark} 可开盒次数: ${num}`);
    if (num < 1) {
      log(`  ${this.remark} 无可用开盒次数`);
      return 0;
    }

    let cnt = 0;
    while (num > 0 && cnt < MAX_DRAW) {
      cnt++;
      const ok = await this.lotteryOnce();
      if (!ok) {
        const ok2 = await this.newLotteryOnce();
        if (!ok2) break;
      }
      await sleep(rand(800, 1500));
      info = await this.getUserInfo();
      num = Number(info.lotteryNum || 0);
    }
    return cnt;
  }
}

// ── 主逻辑 ────────────────────────────────────────────────────────────────

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
  log(`${SCRIPT_NAME} 开始，共 ${accounts.length} 个账号，app_key=${APP_KEY}`);

  if (PROXY_DIRECT || PROXY_API) await switchProxy(false);
  else log('🌐 代理模式: 直连（如 msmarket 403，请配置 JINDIAN_PROXY 或 JINDIAN_PROXY_API）');

  const cache = readJson(CACHE_FILE, {});
  const clients = accounts.map(a => new Client(a, cache));

  // ── 阶段一：登录 ───────────────────────────────────────────────────────
  log('\n══ 阶段一：登录 ══');
  for (const c of clients) {
    try {
      await c.login(false);
      const info = await c.getUserInfo();
      const act = await c.qryActivity();
      log(`📊 ${c.remark} 开盒:${info.lotteryNum || 0} 活动:${act.lotteryStatus === 1 ? '开盒日' : '非开盒日'} 状态:${act.status === 1 ? '进行中' : '未开始/已结束'}`);
    } catch (e) {
      log(`❌ ${c.remark} 登录失败: ${e.message}`);
      c._loginFailed = true;
    }
    await sleep(rand(1000, 2000));
  }

  const valid = clients.filter(c => !c._loginFailed);

  // ── 阶段二：完成任务 ──────────────────────────────────────────────────
  log('\n══ 阶段二：完成任务 ══');
  for (const c of valid) {
    try {
      try {
        await c.dailySign();
      } catch (e) {
        log(`  ⚠️ ${c.remark} 每日签到失败: ${e.message}`);
      }
      await sleep(rand(300, 600));

      const tasks = await c.collectTasks();
      let needSharePair = false;
      for (const t of tasks) {
        if (Number(t.taskStatus) === 1) continue;
        const id = Number(t.id);
        const name = String(t.name || '');

        if (id === 1 || /签到/.test(name)) continue;
        if ([5, 6, 7, 4].includes(id) || /邀请新用户|注册会员|完善会员|企微/.test(name)) {
          log(`  ⏭️ ${c.remark} 跳过人工任务[${name || id}]`);
          continue;
        }
        if (id === 11 || /赠送碎片/.test(name)) continue;
        if (c.isSharePairTask(t) || id === 15 || /分享|暖冬|福利专区/.test(name)) {
          needSharePair = true;
          continue;
        }
        if (c.isBrowseTask(t)) {
          await c.doBrowseTask(t);
          await sleep(rand(500, 1000));
          continue;
        }
        if (id === 3 || /扫码|开箱/.test(name)) {
          await c.scanLog();
          await sleep(rand(300, 600));
          continue;
        }
        if (id === 12 || /奶卡/.test(name)) {
          await c.finishNaiKa();
          await sleep(rand(300, 600));
          continue;
        }
      }

      try { await c.doBrowseTask(null); } catch {}
      c._needSharePair = needSharePair || tasks.some(t => c.isSharePairTask(t) && Number(t.taskStatus) !== 1);

      await c.handleTaskPop();
      const info = await c.getUserInfo();
      log(`  📊 ${c.remark} 任务后开盒次数: ${info.lotteryNum || 0}`);
    } catch (e) {
      log(`  ⚠️ ${c.remark} 任务异常: ${e.message}`);
    }
    await sleep(rand(500, 1000));
  }

  // ── 阶段三：分享任务（shareTask + id13/14 配对）───────────────────────
  log('\n══ 阶段三：分享任务 ══');
  for (const c of valid) {
    try {
      await c.shareTask({});
      log(`  📤 ${c.remark} shareTask 完成`);
    } catch (e) {
      log(`  ⚠️ ${c.remark} shareTask: ${e.message}`);
    }
    await sleep(rand(200, 500));
  }

  async function pairShare(A, B) {
    log(`  📌 ${A.remark} 分享 → ${B.remark} 领取`);
    let shareId = null;
    try {
      shareId = await A.createShareForPair();
    } catch (e) {
      log(`  ❌ ${A.remark} 分享异常: ${e.message}`);
    }
    await sleep(rand(500, 1000));
    if (shareId) {
      try { await B.drawShareNew(shareId); } catch (e) { log(`  ❌ ${B.remark} 领取异常: ${e.message}`); }
      await sleep(rand(400, 800));
    }
    await A.handleTaskPop();
    await B.handleTaskPop();
  }

  if (valid.length < 2) {
    log('  有效账号不足2个，仅完成 shareTask');
    for (const c of valid) {
      try { await c.createShareForPair(); } catch {}
    }
  } else {
    for (let i = 0; i < valid.length; i++) {
      const A = valid[i];
      const B = valid[(i + 1) % valid.length];
      await pairShare(A, B);
      await sleep(rand(600, 1200));
    }
  }

  // ── 阶段四：碎片互赠（全账户随机环形互赠）─────────────────────────────
  log('\n══ 阶段四：碎片互赠 ══');
  const giftOrder = shuffle(valid);
  if (giftOrder.length < 2) {
    log('  有效账号不足2个，跳过碎片互赠');
  } else {
    for (let i = 0; i < giftOrder.length; i++) {
      const A = giftOrder[i];
      const B = giftOrder[(i + 1) % giftOrder.length];
      log(`  📌 ${A.remark} → ${B.remark} 随机赠送`);
      try {
        const giveableA = await A.getGiveableDebris();
        if (giveableA.length) {
          const s = pick(giveableA);
          const giveResult = await A.debrisGive(s.debrisId);
          if (giveResult && giveResult.id) {
            await sleep(rand(500, 1000));
            await B.debrisDrawGive(giveResult.id);
            await sleep(rand(300, 600));
          }
        }
        await A.debrisIsPop();
        await B.debrisIsPop();
      } catch (e) {
        log(`  ⚠️ ${A.remark} → ${B.remark} 碎片赠送异常: ${e.message}`);
      }
      await sleep(rand(800, 1500));
    }
  }

  // ── 阶段五：开盒抽奖 ──────────────────────────────────────────────────
  log('\n══ 阶段五：开盒抽奖 ══');
  const summaries = [];
  for (const c of clients) {
    if (c._loginFailed) { summaries.push(`【${c.remark}】登录失败`); continue; }
    try {
      await c.getUserInfo();
      const cnt = await c.lotteryAll();
      const prizes = c.awards.length ? c.awards.join('、') : '无';
      summaries.push(`【${c.remark}】开盒${cnt}次 | ${prizes}`);
    } catch (e) {
      summaries.push(`【${c.remark}】开盒失败: ${e.message}`);
    }
    await sleep(rand(1000, 2000));
  }

  log('\n══ 汇总 ══');
  summaries.forEach(s => log(s));

  try {
    const notify = require('./sendNotify');
    await notify.sendNotify(SCRIPT_NAME, summaries.join('\n\n'));
  } catch {}
}

main().catch(e => { console.log('FATAL: ' + e.message); process.exit(1); });
