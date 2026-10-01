// name:伊家乐享会
// cron:25 21 * * *

/**
 * 伊家乐享会每日任务
 * 变量：
 * 1. WECHAT_SERVER
 *    协议服务IP地址和端口（支持WeChatPadPro、iwechat、牛子协议）
 * 2. ADMIN_KEY
 *    与搭建时设置的ADMIN_KEY一致（仅WeChatPadPro或iwechat需要，牛子协议不需要）
 * 3. WX_ID
 *    可选，指定要执行任务的微信账号ID，多个用&分隔
 *    如果不设置则为所有在线账号执行任务
 *
 * 逻辑：
 * 1. 调用getCode.py模块自动获取所有在线账号的wx.login code
 * 2. code 登录获取 access-token
 * 3. 执行每日签到、每日分享
 * 4. 刷新积分、签到状态并走 sendNotify / 钉钉通知
 *
 */

const APP_NAME = '伊家乐享会';
const APPID = 'wxd606233dfaf91cae';
const HOST = 'https://msmarket.msx.digitalyili.com/gateway/api';
const TENANT_ID = '1820778859526668290';
const GATEWAY_DOMAIN = 'a1d5e5ea9-wx621112590b635086.sh.wxgateway.com';
const MINI_REFERER = `https://servicewechat.com/${APPID}/122/page-frame.html`;
const MOBILE_UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.58(0x18003a35) NetType/WIFI Language/zh_CN MiniProgramEnv/iOS';
const DEFAULT_SCENE = '1008';
const DEFAULT_SHARE_TIMES = 1;
const DEFAULT_SHARE_INTERVAL_MIN_MS = 10_000;
const DEFAULT_SHARE_INTERVAL_MAX_MS = 20_000;

let notifyMsg = '';

function log(msg) {
  console.log(msg);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

async function getCodesFromServer() {
  const wechatServer = (process.env.WECHAT_SERVER || '').trim().replace(/\/$/, '');
  const adminKey = (process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '').trim();
  const wxIdFilter = (process.env.WX_ID || '').trim();
  const targetWxIds = wxIdFilter ? wxIdFilter.split('&').map(s => s.trim()).filter(Boolean) : [];

  let protocolType = await detectProtocol(wechatServer, adminKey);
  log(`协议类型: ${protocolType}`);

  let onlineAccounts = [];
  if (protocolType === 'yyb呆呆' || protocolType === 'Niuzi') {
    onlineAccounts = await getNiuziAccounts(wechatServer, adminKey);
  } else if (protocolType === 'iwechat') {
    onlineAccounts = await getIwechatAccounts(wechatServer, adminKey);
  } else if (protocolType === 'WeChatPadPro') {
    onlineAccounts = await getWeChatPadProAccounts(wechatServer, adminKey);
  } else {
    throw new Error('未知的协议服务类型，请检查WECHAT_SERVER');
  }

  if (targetWxIds.length > 0) {
    onlineAccounts = onlineAccounts.filter(a => targetWxIds.includes(a.wxid));
    log(`根据WX_ID筛选后获得 ${onlineAccounts.length} 个账号`);
  }

  if (!onlineAccounts.length) {
    throw new Error('未获取到在线账号，请检查协议服务和账号状态');
  }

  const accounts = [];
  for (const acc of onlineAccounts) {
    try {
      const code = await getAppletCode(wechatServer, adminKey, protocolType, acc);
      accounts.push({ remark: acc.nickname || acc.wxid, code });
      log(`获取 ${acc.nickname || acc.wxid} 的Code成功`);
    } catch (e) {
      log(`获取 ${acc.nickname || acc.wxid} 的Code失败: ${e.message}`);
    }
  }
  return accounts;
}

async function detectProtocol(server, adminKey) {
  const auth = adminKey || process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '';
  try {
    const { status, data } = await requestJson(`${server}/api/accounts`, {
      headers: { 'X-License-Key': auth, Authorization: 'Bearer ' + auth },
    }, 8000);
    if (status === 200 && (Array.isArray(data?.accounts) || Array.isArray(data))) return 'yyb呆呆';
  } catch {}
  try {
    const { status, data } = await requestJson(`${server}/admin/GetAuthKey?key=${encodeURIComponent(auth)}`, {}, 5000);
    if (status === 200 && (data?.Code === 200 || Array.isArray(data?.Data))) return 'iwechat';
  } catch {}
  try {
    const { status, data } = await requestJson(`${server}/admin/GetAllDevices?key=${encodeURIComponent(auth)}`, {}, 5000);
    if (status === 200 && (data?.Code === 200 || Array.isArray(data?.Data))) return 'WeChatPadPro';
  } catch {}
  return 'Unknown';
}

async function getNiuziAccounts(server, adminKey = '') {
  try {
    const { loadTaskAccounts } = require('./yybOpenid');
    const list = await loadTaskAccounts(process.env.WX_ID || '', server, adminKey);
    return list.map(a => ({ wxid: a.wxid, openid: a.wxid, nickname: a.remark || a.nickname, license: adminKey || a.wxid }));
  } catch (e) {
    const auth = adminKey || process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '';
    const { data } = await requestJson(`${server}/api/accounts`, {
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
}

async function getIwechatAccounts(server, adminKey) {
  const { data } = await requestJson(`${server}/admin/GetAuthKey?key=${encodeURIComponent(adminKey)}`);
  if (!data || data.Code !== 200) return [];
  const list = Array.isArray(data.Data) ? data.Data : [];
  return list
    .filter(item => item.status === 1)
    .map(item => ({ wxid: item.wx_id || '', nickname: item.nick_name || '', license: item.license || item.authKey || '' }))
    .filter(item => item.wxid || item.license);
}

async function getWeChatPadProAccounts(server, adminKey) {
  const { data } = await requestJson(`${server}/admin/GetAllDevices?key=${encodeURIComponent(adminKey)}`);
  if (!data || data.Code !== 200) return [];
  const list = Array.isArray(data.Data) ? data.Data : [];
  return list
    .filter(item => item.status === 1)
    .map(item => ({ wxid: item.deviceId || '', nickname: item.deviceName || '', license: item.authKey || item.license || '' }))
    .filter(item => item.wxid || item.license);
}

async function getAppletCode(server, adminKey, protocolType, account) {
  if (protocolType === 'yyb呆呆' || protocolType === 'Niuzi') {
    const { getYybCode, getAuth } = require('./yybOpenid');
    const oid = account.openid || account.wxid;
    const { code } = await getYybCode(server, getAuth(adminKey), APPID, oid);
    return code;
  } else {
    const license = account.license;
    const { data } = await requestJson(`${server}/applet/JsLogin?key=${encodeURIComponent(license)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ AppId: APPID, Data: '', Opt: 1, PackageName: '', SdkName: '' }),
    });
    if (data && data.Code === 200 && data.Data && data.Data.Code) {
      return data.Data.Code;
    }
    throw new Error(`取code失败: ${JSON.stringify(data)}`);
  }
}

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

function getFakeSteps() {
  const raw = String(process.env.YJLXH_FAKE_STEPS || '').trim();
  if (!raw) return randomInt(50000, 60000);
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : randomInt(50000, 60000);
}

function getShareTimes() {
  const raw = String(process.env.YJLXH_SHARE_TIMES || '').trim();
  if (!raw) return DEFAULT_SHARE_TIMES;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : DEFAULT_SHARE_TIMES;
}

function getShareIntervalRange() {
  const minRaw = String(process.env.YJLXH_SHARE_INTERVAL_MIN_MS || '').trim();
  const maxRaw = String(process.env.YJLXH_SHARE_INTERVAL_MAX_MS || '').trim();
  const min = Number(minRaw || DEFAULT_SHARE_INTERVAL_MIN_MS);
  const max = Number(maxRaw || DEFAULT_SHARE_INTERVAL_MAX_MS);
  const safeMin = Number.isFinite(min) && min > 0 ? Math.floor(min) : DEFAULT_SHARE_INTERVAL_MIN_MS;
  const safeMax = Number.isFinite(max) && max > 0 ? Math.floor(max) : DEFAULT_SHARE_INTERVAL_MAX_MS;
  return safeMin <= safeMax ? { min: safeMin, max: safeMax } : { min: safeMax, max: safeMin };
}

function createGatewayCallId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function getGatewaySimHeaders() {
  const routeTag = String(process.env.YJLXH_ROUTE_TAG || GATEWAY_DOMAIN);
  const source = String(process.env.YJLXH_WX_SOURCE || 'wx_client');
  const callId = String(process.env.YJLXH_CALL_ID || createGatewayCallId());
  const timeoutMs = String(process.env.YJLXH_TIMEOUT_MS || '15000');

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
  return {
    Accept: 'application/json, text/plain, */*',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Content-Type': 'application/json',
    Origin: 'https://servicewechat.com',
    Referer: MINI_REFERER,
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
    ...parseJsonEnv('YJLXH_EXTRA_HEADERS_JSON'),
    ...extra,
  };
}

async function requestJson(url, options = {}, timeoutMs = 20_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { ...options, signal: controller.signal });
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
  if (typeof data === 'string') return data;
  if (typeof data.message === 'string' && data.message) return data.message;
  if (typeof data.msg === 'string' && data.msg) return data.msg;
  if (data.error) {
    if (typeof data.error === 'string') return data.error;
    if (typeof data.error.msg === 'string' && data.error.msg) return data.error.msg;
    if (typeof data.error.message === 'string' && data.error.message) return data.error.message;
    if (data.error.code !== undefined) return `错误码 ${data.error.code}`;
  }
  return JSON.stringify(data);
}

function isAlreadyDoneMessage(message) {
  return /已签到|已分享|已领取|已完成|今日已|已经|重复/.test(String(message || ''));
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


async function loginByCode(code) {
  const { data } = await apiRequest('/auth/account/login', 'POST', '', { jsCode: code });
  return data;
}

async function getUserInfo(token) {
  const { data } = await apiRequest('/auth/account/user/info', 'GET', token);
  return data;
}

async function getScore(token) {
  const { data } = await apiRequest('/member/point', 'GET', token);
  return data;
}

async function getTaskCenter(token) {
  const { data } = await apiRequest('/member/task/center', 'GET', token);
  return data;
}

async function getSignStatus(token) {
  const { data } = await apiRequest('/member/sign/status', 'GET', token);
  return data;
}

async function getSignConfig(token) {
  const { data } = await apiRequest('/member/sign/config', 'GET', token);
  return data;
}

async function dailySign(token) {
  const { data } = await apiRequest('/member/daily/sign', 'POST', token, {});
  return data;
}

async function dailyShare(token) {
  const { data } = await apiRequest('/member/share/content/points', 'GET', token);
  return data;
}

async function uploadWxRunData(token, encryptedData, iv) {
  const { data } = await apiRequest('/member/get/wx/steps', 'POST', token, { encryptedData, iv });
  return data;
}

async function exchangeSteps(token, steps) {
  const { data } = await apiRequest(`/member/steps/exchange/${encodeURIComponent(String(steps))}`, 'GET', token);
  return data;
}

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

function getWxRunTask(taskList) {
  const list = Array.isArray(taskList?.data) ? taskList.data : [];
  return list.find((item) => Number(item?.taskType) === 14) || null;
}

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

async function runSingleShare(token) {
  const shareResp = await dailyShare(token);
  if (isSuccess(shareResp)) {
    const data = shareResp.data;
    const msg =
      (data && typeof data === 'object' && (data.message || data.msg || data.tip)) ||
      (typeof data === 'string' ? data : '');
    return {
      status: 'success',
      message: msg ? `分享成功，${msg}` : '分享成功',
    };
  }

  const errorMsg = extractErrorMsg(shareResp);
  if (isAlreadyDoneMessage(errorMsg)) {
    return {
      status: 'already',
      message: '今日已分享',
    };
  }
  throw new Error(`分享失败: ${errorMsg}`);
}

async function runShareTask(token) {
  const shareTimes = getShareTimes();
  const intervalRange = getShareIntervalRange();
  let successCount = 0;
  let alreadyCount = 0;

  for (let i = 1; i <= shareTimes; i += 1) {
    const result = await runSingleShare(token);

    if (result.status === 'success') {
      successCount += 1;
    } else if (result.status === 'already') {
      alreadyCount += 1;
    }

    log(`📤 分享 ${i}/${shareTimes}：${result.message}`);

    if (i < shareTimes) {
      const waitMs = randomInt(intervalRange.min, intervalRange.max);
      log(`⏱️  等待 ${(waitMs / 1000).toFixed(0)} 秒后继续分享`);
      await sleep(waitMs);
    }
  }

  const parts = [`分享 ${shareTimes} 次`, `成功 ${successCount} 次`];
  if (alreadyCount > 0) parts.push(`今日已分享 ${alreadyCount} 次`);
  return parts.join('，');
}

async function runStepsTask(token, account, wxRunTask) {
  const fakeSteps = getFakeSteps();
  if (fakeSteps > 0) {
    const exchangeResp = await exchangeSteps(token, fakeSteps);
    if (!isSuccess(exchangeResp)) {
      const errorMsg = extractErrorMsg(exchangeResp);
      if (isAlreadyDoneMessage(errorMsg)) {
        return {
          message: `伪造步数模式：今日步数已兑换，步数 ${fakeSteps}`,
          steps: fakeSteps,
          exchangedPoints: 0,
          skipped: false,
        };
      }
      throw new Error(`伪造步数兑换失败: ${errorMsg}`);
    }

    const actualPoints = Number(exchangeResp.data?.stepsExchange?.bonusPoint || 0);
    return {
      message: `伪造步数兑换成功，步数 ${fakeSteps}${actualPoints > 0 ? `，+${actualPoints}积分` : ''}`,
      steps: fakeSteps,
      exchangedPoints: actualPoints,
      skipped: false,
    };
  }

  if (!account.encryptedData) {
    return {
      message: '新格式不支持微信运动数据上传，已跳过步数兑换',
      steps: 0,
      exchangedPoints: 0,
      skipped: true,
    };
  }

  if (!wxRunTask) {
    return {
      message: '任务中心未找到微信步数兑换任务，已跳过',
      steps: 0,
      exchangedPoints: 0,
      skipped: true,
    };
  }

  const wxRunResp = await uploadWxRunData(token, account.encryptedData, account.iv);
  if (!isSuccess(wxRunResp)) {
    throw new Error(`获取微信步数失败: ${extractErrorMsg(wxRunResp)}`);
  }

  const steps = Number(wxRunResp.data || 0);
  if (!steps || steps <= 0) {
    return {
      message: '今日微信步数为 0，已跳过兑换',
      steps,
      exchangedPoints: 0,
      skipped: true,
    };
  }

  const bonusPerK = Number(wxRunTask?.taskRole?.bonusPoint || 0);
  const bonusMax = Number(wxRunTask?.taskRole?.bonusPointMax || 0);
  const estimatedPoints = bonusPerK > 0 ? Math.floor(steps / 1000) * bonusPerK : 0;
  const expectedPoints = bonusMax > 0 ? Math.min(estimatedPoints, bonusMax) : estimatedPoints;

  const exchangeResp = await exchangeSteps(token, steps);
  if (!isSuccess(exchangeResp)) {
    const errorMsg = extractErrorMsg(exchangeResp);
    if (isAlreadyDoneMessage(errorMsg)) {
      return {
        message: `今日步数已兑换，步数 ${steps}`,
        steps,
        exchangedPoints: 0,
        skipped: false,
      };
    }
    throw new Error(`步数兑换失败: ${errorMsg}`);
  }

  const actualPoints = Number(exchangeResp.data?.stepsExchange?.bonusPoint || expectedPoints || 0);
  return {
    message: `步数兑换成功，步数 ${steps}${actualPoints > 0 ? `，+${actualPoints}积分` : ''}`,
    steps,
    exchangedPoints: actualPoints,
    skipped: false,
  };
}

async function runOne(account) {
  log(`\n================ ${account.remark} ================`);
  log(`🧩 ${account.remark} 使用getCode模块获取的code登录`);

  const code = account.code;
  const loginResp = await loginByCode(code);
  if (!isSuccess(loginResp) || !loginResp?.data?.accessToken) {
    throw new Error(`登录失败: ${extractErrorMsg(loginResp)}`);
  }

  const token = String(loginResp.data.accessToken);

  const [beforeUser, beforeScoreResp, taskCenter] = await Promise.all([
    getUserInfo(token),
    getScore(token),
    getTaskCenter(token),
  ]);

  if (!isSuccess(beforeUser)) {
    throw new Error(`查询用户信息失败: ${extractErrorMsg(beforeUser)}`);
  }
  if (!isSuccess(beforeScoreResp)) {
    throw new Error(`查询积分失败: ${extractErrorMsg(beforeScoreResp)}`);
  }
  if (!isSuccess(taskCenter)) {
    throw new Error(`查询任务中心失败: ${extractErrorMsg(taskCenter)}`);
  }

  const userInfo = beforeUser.data || {};
  const beforeScore = Number(beforeScoreResp.data || 0);
  const wxRunTask = getWxRunTask(taskCenter);

  const signResult = await runSignTask(token);
  await sleep(randomInt(800, 1500));
  const shareResult = await runShareTask(token);
  await sleep(randomInt(800, 1500));
  const stepsResult = await runStepsTask(token, account, wxRunTask);

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
    `分享结果：${shareResult}`,
    `步数结果：${stepsResult.message}`,
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

async function main() {
  const wechatServer = (process.env.WECHAT_SERVER || '').trim();
  if (!wechatServer) {
    throw new Error('未配置环境变量 WECHAT_SERVER');
  }

  log(`协议服务: ${wechatServer}`);
  const wxIdFilter = (process.env.WX_ID || '').trim();
  if (wxIdFilter) {
    log(`指定账号: ${wxIdFilter}`);
  }

  log('正在获取所有在线账号的Code...');
  const accounts = await getCodesFromServer();
  if (!accounts.length) {
    throw new Error('未获取到任何账号的Code，请检查WECHAT_SERVER和账号状态');
  }

  log(`共 ${accounts.length} 个账号`);

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
      const waitSeconds = randomInt(45, 90);
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
