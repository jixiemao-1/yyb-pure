// - export WECHAT_SERVER='http:ip:8011'
// - WXIDXJ：wxid#备注
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const axios = require('axios');

// 多账号之间的延迟（毫秒）
const ACCOUNT_DELAY_MS = 10000;

const SCRIPT_NAME = '京东协议获取CK';
const APPID = 'wx73247c7819d61796';
const WECHAT_SERVER = (process.env.WECHAT_SERVER || 'http://172.17.0.7:8011').trim();
const WXIDXJ = (process.env.WXIDXJ || '').trim();
const QL_URL = (process.env.QL_URL || process.env.Ray_QingLongConfig__Url || 'http://127.0.0.1:5700').replace(/\/$/, '');
const QL_CLIENT_ID = process.env.QL_CLIENT_ID || process.env.Ray_QingLongConfig__ClientId || '';
const QL_CLIENT_SECRET = process.env.QL_CLIENT_SECRET || process.env.Ray_QingLongConfig__ClientSecret || '';
const ENV_NAME = process.env.JDXCX_ENV_NAME || 'JD_COOKIE';
const CACHE_FILE = path.join(__dirname, 'jd_kd_ck.json');
const CLIENT_VER = '2.0.2';
const JD_APPID = '599';
const SIGN_GSALT = 'sb2cwlYyaCSN1KUv5RHG3tmqxfEb8NKN';
const FINGER_BIZ_KEY = 'bce044c839bb9eb811aad5af18a629e199da4e13';
const REFERER = `https://servicewechat.com/${APPID}/864/page-frame.html`;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254186b) XWEB/19481';
const FINGER_TK_DEFAULT = 'L64RTJ562VJEYNEQN67XMUWSR4UFLOIQHJYZ3MWERRIKJGP24SDSBDS4I4AMVU24Y3Y7A4UPDICN2';
const FINGER_ALPHABET = '23IL<N01c7KvwZO56RSTAfghiFyzWJqVabGH4PQdopUrsCuX*xeBjkltDEmn89.-';

function log(msg) { console.log(`[${SCRIPT_NAME}] ${msg}`); }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function md5(s) { return crypto.createHash('md5').update(String(s), 'utf8').digest('hex'); }
function uuid() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function randHex(n) { return crypto.randomBytes(n).toString('hex'); }

function fingerEncode(obj) {
  const text = encodeURIComponent(JSON.stringify(obj));
  let out = '';
  let i = 0;
  do {
    const e = text.charCodeAt(i++);
    const r = text.charCodeAt(i++);
    const u = text.charCodeAt(i++);
    const a = e >> 2;
    const c = (3 & e) << 4 | r >> 4;
    let s = (15 & r) << 2 | u >> 6;
    let f = 63 & u;
    if (Number.isNaN(r)) s = f = 64;
    else if (Number.isNaN(u)) f = 64;
    out += FINGER_ALPHABET.charAt(a) + FINGER_ALPHABET.charAt(c) + FINGER_ALPHABET.charAt(s) + FINGER_ALPHABET.charAt(f);
  } while (i < text.length);
  return out + '/';
}

function parseAccounts(raw) {
  return String(raw || '')
    .split(/[\n@&]/)
    .map(s => s.trim())
    .filter(Boolean)
    .map(s => {
      const i = s.indexOf('#');
      return i < 0 ? { wxid: s, remark: s } : { wxid: s.slice(0, i).trim(), remark: s.slice(i + 1).trim() || s.slice(0, i).trim() };
    })
    .filter(x => x.wxid);
}

function loadCache() {
  try { return JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8')); } catch { return {}; }
}

function saveCache(cache) {
  fs.writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 2), 'utf8');
}

function qlHeaders(token) {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
}

function samePin(value, pin) {
  const v = String(value || '');
  const p = String(pin || '');
  if (!p) return false;
  const decodedPin = (() => {
    try { return decodeURIComponent(p); } catch { return p; }
  })();
  const encodedPin = encodeURIComponent(decodedPin);
  return v.includes(`pt_pin=${p}`) || v.includes(`pt_pin=${decodedPin}`) || v.includes(`pt_pin=${encodedPin}`);
}

let qlTokenCache = '';
async function qlToken() {
  if (qlTokenCache) return qlTokenCache;
  if (!QL_CLIENT_ID || !QL_CLIENT_SECRET) throw new Error('缺少青龙 QL_CLIENT_ID/QL_CLIENT_SECRET');
  const { data } = await axios.get(`${QL_URL}/open/auth/token`, {
    params: { client_id: QL_CLIENT_ID, client_secret: QL_CLIENT_SECRET },
    timeout: 10000,
    validateStatus: () => true
  });
  const token = data?.data?.token || '';
  if (!token) throw new Error(`青龙 token 获取失败: ${JSON.stringify(data).slice(0, 200)}`);
  qlTokenCache = token;
  return token;
}

async function qlWrite(freshList) {
  if (!freshList.length) return;
  const token = await qlToken();
  const headers = qlHeaders(token);
  const { data } = await axios.get(`${QL_URL}/open/envs`, {
    params: { searchValue: ENV_NAME },
    headers,
    timeout: 10000,
    validateStatus: () => true
  });
  const existing = data?.code === 200 && Array.isArray(data?.data) ? data.data.filter(e => e.name === ENV_NAME) : [];
  const usedOldIds = new Set();
  let updated = 0;
  let created = 0;

  for (const { account, pin, ck } of freshList) {
    let old = existing.find(e => {
      const id = e.id || e._id;
      return id && !usedOldIds.has(String(id)) && samePin(e.value, pin);
    });
    if (!old && account) {
      old = existing.find(e => {
        const id = e.id || e._id;
        return id && !usedOldIds.has(String(id)) && String(e.remarks || '').trim() === String(account).trim();
      });
    }

    if (old) {
      const id = old.id || old._id;
      usedOldIds.add(String(id));
      await axios.put(`${QL_URL}/open/envs`, {
        id,
        name: ENV_NAME,
        value: ck,
        remarks: account || old.remarks || '自动'
      }, { headers, timeout: 10000, validateStatus: () => true });
      updated++;
    } else {
      await axios.post(`${QL_URL}/open/envs`, [{
        name: ENV_NAME,
        value: ck,
        remarks: account || '自动'
      }], { headers, timeout: 10000, validateStatus: () => true });
      created++;
    }
  }

  log(`青龙 ${ENV_NAME}: 更新${updated}个，新建${created}个`);
}

async function wxPost(paths, body, timeout = 20000) {
  const base = WECHAT_SERVER.replace(/\/$/, '');
  let lastErr;
  for (const p of paths) {
    try {
      const { data } = await axios.post(`${base}${p}`, body, { timeout, headers: { 'Content-Type': 'application/json' } });
      return data;
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error('微信协议请求失败');
}

async function getWxCode(wxid) {
  const { getYybCode, getAuth } = require('./yybOpenid');
  const { code } = await getYybCode(WECHAT_SERVER, getAuth(), APPID, wxid);
  return code;
}

async function getEidToken(wxid) {
  const payload = { api_name: 'webapi_getuserinfo', data: { lang: 'zh_CN' }, with_credentials: true };
  try {
    const data = await wxPost(['/api/yyb/invoke-cloud', '/api/yyb/invoke-cloud'], { openid: wxid, wxid, appid: APPID, auth: process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '',  data: JSON.stringify(payload) }, 15000);
    const inner = data?.Data || data?.data || data || {};
    let decoded = inner;
    try {
      if (typeof inner?.data === 'string' && /^[A-Za-z0-9+/=]+$/.test(inner.data)) {
        decoded = JSON.parse(Buffer.from(inner.data, 'base64').toString('utf8'));
      } else if (typeof data?.respJson === 'string') {
        decoded = JSON.parse(data.respJson);
      }
    } catch {}
    return decoded.eid_token || decoded.eidToken || decoded.eid || '';
  } catch {
    return '';
  }
}

async function getFingerTk() {
  const now = Date.now();
  const env = {
    sv: '1.0.3.4',
    clist: now,
    vlv: '3.16.0',
    ve: '4.1.8.107',
    fs: -1,
    la: 'zh_CN',
    br: 'microsoft',
    mo: 'microsoft',
    pr: 1,
    pl: 'windows',
    sh: 780,
    sw: 414,
    sbh: '',
    sy: 'Windows 10',
    wh: 780,
    ww: 414,
    bl: '',
    nt: 'wifi',
    vid: APPID,
    bk: FINGER_BIZ_KEY,
    cliet: now,
    fp: randHex(16)
  };
  const resp = await axios.post(`https://we.jd.com/stone/1/${FINGER_TK_DEFAULT}`, fingerEncode(env), {
    timeout: 15000,
    validateStatus: () => true,
    headers: {
      'User-Agent': UA,
      Referer: REFERER,
      'Content-Type': 'application/json',
      Accept: '*/*'
    }
  });
  const tk = resp.data?.data?.tk || resp.data?.tk || '';
  if (!tk) throw new Error(`finger_tk 获取失败: status=${resp.status}, body=${JSON.stringify(resp.data).slice(0, 300)}`);
  return tk;
}

function signSilentAuth(data) {
  const extra = { cmd: 52, sub_cmd: 1, gsalt: SIGN_GSALT };
  const order = ['appid', 'wxappid', 'client_ver', 'ts', 'cmd', 'sub_cmd', 'gsalt'];
  const raw = order.map(k => {
    if (data[k] != null && data[k] !== '') return data[k];
    if (extra[k] != null) return extra[k];
    return '';
  }).join('');
  return md5(raw);
}

async function silentAuthLogin({ code, eidToken = '' }) {
  const ts = Math.floor(Date.now() / 1000);
  const data = {
    globalTokenSource: '',
    code,
    token: '',
    salt: '',
    user_data: '',
    user_iv: '',
    eid_token: eidToken || '',
    goToLogin: true,
    returnurl: '/pages/login/web-view/web-view',
    wxappid: APPID,
    appid: JD_APPID,
    client_ver: CLIENT_VER,
    ts
  };
  data.sign = signSilentAuth(data);
  const body = new URLSearchParams(data).toString();
  const resp = await axios.post('https://wxapplogin.m.jd.com/cgi-bin/jxpp/silentauthlogin', body, {
    timeout: 20000,
    validateStatus: () => true,
    headers: {
      'User-Agent': UA,
      Referer: REFERER,
      'Content-Type': 'application/x-www-form-urlencoded',
      cookie: 'guid=; pt_pin=; pt_key=; pt_token=',
      Accept: '*/*'
    }
  });
  const out = resp.data || {};
  if (resp.status !== 200 || out.err_code !== 0 || !out.pt_key || !out.pt_pin) {
    throw new Error(`silentauthlogin 失败: status=${resp.status} body=${JSON.stringify(out).slice(0, 400)}`);
  }
  return { data: out };
}

async function checkLopCookie(ptKey, ptPin) {
  const resp = await axios.post('https://lop-proxy.jd.com/vip/queryAccountInfo', [{ pin: 'uid' }], {
    timeout: 15000,
    validateStatus: () => true,
    headers: {
      Host: 'lop-proxy.jd.com',
      clientVersion: '1779421844000',
      client: 'WX-XCX',
      requestid: uuid(),
      'LOP-DN': 'logistics-mrd.jd.com',
      'Content-Type': 'application/json;charset=UTF-8',
      Cookie: `pt_key=${ptKey}; pin=${encodeURIComponent(ptPin)};`,
      sessiontraceid: uuid(),
      ClientInfo: JSON.stringify({ appName: 'c2c', client: 'm' }),
      'User-Agent': UA,
      'bff-client': 'MP',
      Referer: REFERER
    }
  });
  return { status: resp.status, data: resp.data };
}

async function refresh(account) {
  log(`刷新：${account.remark}`);
  const code = await getWxCode(account.wxid);
  log(`  · wx code: ${code.slice(0, 10)}...`);
  let eidToken = await getEidToken(account.wxid);
  if (!eidToken) eidToken = await getFingerTk();
  if (eidToken) log(`  · eid_token: ${eidToken.slice(0, 18)}...`);
  const { data } = await silentAuthLogin({ code, eidToken });
  const cred = {
    remark: account.remark,
    wxid: account.wxid,
    pt_key: data.pt_key,
    pt_pin: data.pt_pin,
    pin: data.pt_pin,
    guid: data.guid || '',
    expire_time: data.expire_time || 0,
    refresh_time: data.refresh_time || 0,
    ck: `pt_key=${data.pt_key};pt_pin=${data.pt_pin};`,
    lopCookie: `pt_key=${data.pt_key}; pin=${encodeURIComponent(data.pt_pin)};`,
    updatedAt: Date.now()
  };
  log(`  · CK: ${cred.ck}`);
  const check = await checkLopCookie(cred.pt_key, cred.pt_pin);
  log(`  · lop-proxy 校验 status=${check.status} code=${check.data?.code ?? ''} msg=${check.data?.msg || check.data?.message || ''}`);
  return cred;
}

async function main() {
  let accounts = parseAccounts(WXIDXJ);
  if (!accounts.length) {
    const { loadTaskAccounts } = require('./yybOpenid');
    const list = await loadTaskAccounts(WXIDXJ);
    accounts = list.map(a => ({ wxid: a.wxid, remark: a.remark }));
    console.log(`直连账号 ${accounts.length} 个: ${accounts.map(a => a.remark).join(' / ')}`);
  }
  const cache = loadCache();
  const results = [];
  const qlResults = [];
  for (let i = 0; i < accounts.length; i++) {
    const a = accounts[i];
    try {
      const cred = await refresh(a);
      cache[a.wxid] = cred;
      saveCache(cache);
      qlResults.push({ account: a.remark, pin: cred.pt_pin, ck: cred.ck });
      results.push({ remark: a.remark, ok: true, lopCookie: cred.lopCookie });
    } catch (e) {
      log(`❌ ${a.remark} 失败：${e.message}`);
      results.push({ remark: a.remark, ok: false, msg: e.message });
    }
    if (i < accounts.length - 1) {
      log(`等待 ${ACCOUNT_DELAY_MS / 1000}s 后处理下一个账号...`);
      await sleep(ACCOUNT_DELAY_MS);
    }
  }

  if (qlResults.length) {
    try {
      await qlWrite(qlResults);
    } catch (e) {
      log(`❌ 写入青龙失败：${e.message}`);
    }
  }

  log('\n========== lop-proxy Cookie ==========');
  for (const r of results) {
    if (r.ok) console.log(r.lopCookie);
    else log(`❌ ${r.remark} ${r.msg}`);
  }
}

main().catch(e => {
  console.error(e);
  process.exit(1);
});
