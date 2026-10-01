/**
 * 同程旅行 - 现金打卡签到
 * 功能：连续打卡拿现金 + 挑战奖励打卡 + 每日分享任务 + 自动领取可领奖励 + 新增打卡攒现金
 * 
 * 环境变量:
 *   TC_ACCOUNT      - 格式：备注#sectoken#openId#userKey（优先）
 *                    多账号：使用 & 或 换行 分割
 *   TC_APP_ID       - 小程序 AppId，默认 wx336dcaf6a1ecf632
 *   TC_GETCODE_PATH - getCode.py 路径，默认 ./getCode.py
 *   TC_PYTHON       - Python 命令（可选），如 python3 / py
 *   TC_SCENE        - 登录 scene，默认 1089
 * 
 * 自动模式（未配置 TC_ACCOUNT 时）依赖 getCode.py 的环境变量:
 *   WECHAT_SERVER / ADMIN_KEY / WX_ID
 * 
 *  
 */

// 小程序进同城找 https://wx.17u.cn/mytourapi/mytrip/newCardList

const https = require("https");
const http = require("http");
const zlib = require("zlib");
const { spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

// ============================================================
// 配置区
// ============================================================
const LOGIN_URL = "https://wx.17u.cn/wechatappapi/wxUser/login";
const LOGIN_URL_V2 = "https://wx.17u.cn/appapi/wxuser/login/2";
const DEFAULT_APP_ID = "wx336dcaf6a1ecf632";
const DEFAULT_SCENE = 1089;
const MILEAGE_TASK_SCHEME_GUID = "task-2025-nflygijg";
const MILEAGE_PULLDOWN_TASK_SCHEME_GUID = "task-2026-gtfdfdit";
const MILEAGE_TASK_DETAIL_GUID_TRAIN = "taskDetail-2025-hqishsam";
const MILEAGE_TASK_DETAIL_GUID_PULLDOWN_BUBBLE = "taskDetail-2026-whukmhvg";
const MILEAGE_TASK_DETAIL_GUID_PULLDOWN_LIST = "taskDetail-2026-jgeoebbm";

const MILEAGE_TASK_STATUS = {
  NOT_STARTED: 0,
  IN_PROGRESS: 1,
  REWARD_READY: 2,
  FINISHED: 3,
  ENDED: 4,
};

// ============================================================
// 工具函数
// ============================================================
function getEnv(n) { return process.env[n] || ""; }

function genApmat(openid) {
  const now = new Date();
  const pad = (n, l = 2) => String(n).padStart(l, "0");
  const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}`;
  const rand = String(Math.floor(Math.random() * 1000000)).padStart(6, "0");
  return `${openid}|${ts}|${rand}`;
}

function decodeResponseBuffer(buffer, headers = {}) {
  const encoding = String(headers["content-encoding"] || "").toLowerCase();
  try {
    if (encoding.includes("br") && typeof zlib.brotliDecompressSync === "function") {
      return zlib.brotliDecompressSync(buffer);
    }
    if (encoding.includes("gzip") || encoding.includes("deflate")) {
      return zlib.unzipSync(buffer);
    }
  } catch {
    return buffer;
  }
  return buffer;
}

function bufferToResponseText(buffer, headers = {}) {
  return decodeResponseBuffer(buffer, headers).toString("utf8");
}

function httpRequest(url, method, headers, body) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const isHttps = urlObj.protocol === "https:";
    const lib = isHttps ? https : http;
    const port = urlObj.port || (isHttps ? 443 : 80);
    const req = lib.request({
      hostname: urlObj.hostname,
      port,
      path: urlObj.pathname + urlObj.search,
      method,
      headers
    }, (res) => {
      const chunks = [];
      res.on("data", c => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)));
      res.on("end", () => {
        const data = bufferToResponseText(Buffer.concat(chunks), res.headers);
        try { resolve(JSON.parse(data)); }
        catch { resolve({ raw: data }); }
      });
    });
    req.on("error", reject);
    if (body && (method === "POST" || method === "PUT")) {
      const bodyStr = typeof body === "string" ? body : JSON.stringify(body);
      req.write(bodyStr);
    }
    req.end();
  });
}

// ============================================================
// 【关键】初始化 Cookie
// ============================================================
async function initCookie(sectoken) {
  const signInUrl = encodeURIComponent("https://wx.17u.cn/memberft/student/studentcard/signIn?refid=2000845210");
  const url = `https://wx.17u.cn/flight/getwxxcxopenid.html?sectoken=${encodeURIComponent(sectoken)}&url=${signInUrl}&wxAppScene=1089`;
  return new Promise((resolve) => {
    https.get(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.60(0x18003c32) NetType/4G Language/zh_CN miniProgram/wx336dcaf6a1ecf632",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
      }
    }, (res) => {
      const cookies = {};
      const setCookieHeaders = res.headers["set-cookie"] || [];
      for (const sc of setCookieHeaders) {
        const part = sc.split(";")[0].trim();
        const eqIdx = part.indexOf("=");
        if (eqIdx > 0) {
          const name = part.substring(0, eqIdx);
          const value = part.substring(eqIdx + 1);
          cookies[name] = value;
        }
      }
      res.resume();
      res.on("end", () => resolve(cookies));
    }).on("error", (err) => {
      warn("initCookie 请求失败:", err.message);
      resolve({});
    });
  });
}

// ============================================================
// 账号解析
// ============================================================
function parseAccountsFromEnv() {
  const raw = getEnv("TC_ACCOUNT");
  if (!raw) return [];

  const lines = raw
    .split("&")
    .map(item => item.trim())
    .join("\n")
    .split("\n")
    .map(line => line.trim())
    .filter(Boolean);

  return lines.map((line, i) => {
    const p = line.split("#");
    const remark = p[0]?.trim() || `账号${i + 1}`;
    const sectoken = p[1]?.trim();
    const openId = p[2]?.trim();
    const userKey = p[3]?.trim();

    if (!sectoken || !openId) {
      error(`账号${i + 1} 格式错误`);
      return null;
    }

    return {
      remark,
      index: i + 1,
      sectoken,
      openId,
      userKey
    };
  }).filter(Boolean);
}

function parseGetCodeJson(text) {
  const startTag = "__TC_GETCODE_JSON_START__";
  const endTag = "__TC_GETCODE_JSON_END__";
  const start = text.indexOf(startTag);
  const end = text.indexOf(endTag);
  if (start < 0 || end < 0 || end <= start) return null;

  const jsonText = text.slice(start + startTag.length, end).trim();
  if (!jsonText) return {};

  try {
    const parsed = JSON.parse(jsonText);
    return (parsed && typeof parsed === "object") ? parsed : {};
  } catch {
    return null;
  }
}

function runGetCodePy(appId) {
  const rawPath = getEnv("TC_GETCODE_PATH") || "getCode.py";
  const getCodePath = path.resolve(rawPath);
  if (!fs.existsSync(getCodePath)) {
    warn(`未找到 getCode.py: ${getCodePath}`);
    return {};
  }

  const pyScript = [
    "import importlib.util, json, pathlib, sys",
    "module_path = pathlib.Path(sys.argv[1]).resolve()",
    "app_id = sys.argv[2]",
    "spec = importlib.util.spec_from_file_location('tc_getcode_mod', str(module_path))",
    "mod = importlib.util.module_from_spec(spec)",
    "spec.loader.exec_module(mod)",
    "data = mod.get_wechat_codes(app_id)",
    "print('__TC_GETCODE_JSON_START__')",
    "print(json.dumps(data, ensure_ascii=False))",
    "print('__TC_GETCODE_JSON_END__')",
  ].join("\n");

  const customPy = getEnv("TC_PYTHON").trim();
  const candidates = [];
  if (customPy) candidates.push({ cmd: customPy, args: [] });
  candidates.push(
    { cmd: "python3", args: [] },
    { cmd: "python", args: [] },
    { cmd: "py", args: ["-3"] }
  );

  let lastError = "";
  for (const candidate of candidates) {
    try {
      const args = [...candidate.args, "-c", pyScript, getCodePath, appId];
      const ret = spawnSync(candidate.cmd, args, {
        encoding: "utf8",
        env: process.env,
        timeout: 120000,
        maxBuffer: 10 * 1024 * 1024,
      });
      if (ret.error) {
        if (ret.error.code === "ENOENT") {
          lastError = `${candidate.cmd} 不存在`;
          continue;
        }
        lastError = ret.error.message;
        continue;
      }

      const stdout = String(ret.stdout || "");
      const stderr = String(ret.stderr || "");
      const parsed = parseGetCodeJson(stdout);
      if (parsed) {
        info(`getCode.py 调用成功，Python: ${candidate.cmd}`);
        return parsed;
      }

      lastError = `exit=${ret.status}, stderr=${stderr.trim() || "空"}`;
    } catch (e) {
      lastError = e.message;
    }
  }

  warn(`调用 getCode.py 失败: ${lastError || "未知错误"}`);
  return {};
}

function findStringFieldDeep(root, aliases) {
  const targets = new Set(aliases.map(v => String(v).toLowerCase()));
  const stack = [root];
  const seen = new Set();

  while (stack.length) {
    const node = stack.pop();
    if (!node || typeof node !== "object" || seen.has(node)) continue;
    seen.add(node);

    for (const [key, val] of Object.entries(node)) {
      if (targets.has(String(key).toLowerCase())) {
        const s = val == null ? "" : String(val).trim();
        if (s) return s;
      }
      if (val && typeof val === "object") stack.push(val);
    }
  }
  return "";
}

function extractLoginTokens(resp) {
  const content = (resp && typeof resp === "object" && resp.content && typeof resp.content === "object")
    ? resp.content
    : resp;
  const sectoken = findStringFieldDeep(content, ["sectoken", "secToken", "tcUserToken", "userToken", "token"]);
  const openId = findStringFieldDeep(content, ["openId", "openid"]);
  const userKey = findStringFieldDeep(content, ["userKey", "unionId", "unionid"]);
  if (!sectoken || !openId) return null;
  return { sectoken, openId, userKey };
}

function extractTaskUserId(taskInfo) {
  return findStringFieldDeep(taskInfo, ["userId", "userid", "memberId", "uid"]);
}

function isTodayVisited(taskInfo) {
  return Number(taskInfo?.calendarInfo?.isTodayVisit) === 1;
}

const TASK_JOIN_STATE = {
  NOT_JOINED: 0,
  IN_PROGRESS: 1,
  ENDED: 2,
};

const TASK_USER_STATE = {
  NORMAL: 0,
  MISSED_CAN_RESIGN: 1,
  MISSED_CANNOT_RESIGN: 2,
};

const TASK_SHARE_STATE = {
  NO_NEED: -1,
  NOT_SHARED: 0,
  SHARED: 1,
};

const TASK_REWARD_TYPE = {
  WITHDRAW: 0,
  CLAIM_CHALLENGE: 1,
};

const TASK_REC_STATE = {
  NOT_AVAILABLE: 0,
  AVAILABLE: 1,
  RECEIVED: 2,
};

function hasTaskRewardRecord(taskInfo) {
  const recPrizeId = taskInfo?.recPrizeId;
  if (Array.isArray(recPrizeId)) return recPrizeId.length > 0;
  if (recPrizeId == null) return false;
  return String(recPrizeId).trim().length > 0;
}

function getTodayShareState(taskInfo) {
  return Number(taskInfo?.calendarInfo?.todayShareState ?? TASK_SHARE_STATE.NO_NEED);
}

function needsTodayShare(taskInfo) {
  return getTodayShareState(taskInfo) === TASK_SHARE_STATE.NOT_SHARED;
}

function isDailyTaskComplete(taskInfo) {
  return isTodayVisited(taskInfo) && !needsTodayShare(taskInfo);
}

function canCashWithdraw(taskInfo) {
  return Number(taskInfo?.sillSuccess) === 1;
}

function canClaimChallenge(taskInfo) {
  return Number(taskInfo?.prizeList?.[0]?.recState) === TASK_REC_STATE.AVAILABLE;
}

function isRewardReady(taskInfo, taskKind) {
  return taskKind === "challenge"
    ? canClaimChallenge(taskInfo)
    : canCashWithdraw(taskInfo);
}

function canResetTask(taskInfo) {
  return Number(taskInfo?.actDetail?.isAllowReset ?? 1) === 1;
}

function shouldClaimRewardBeforeReset(taskInfo, taskKind) {
  const joinState = Number(taskInfo?.userJoinState);
  const userState = Number(taskInfo?.calendarInfo?.userState);
  return isRewardReady(taskInfo, taskKind) && (
    joinState === TASK_JOIN_STATE.ENDED ||
    (joinState === TASK_JOIN_STATE.IN_PROGRESS && userState === TASK_USER_STATE.MISSED_CANNOT_RESIGN)
  );
}

function shouldStartTask(taskInfo, taskKind) {
  const joinState = Number(taskInfo?.userJoinState);
  const userState = Number(taskInfo?.calendarInfo?.userState);
  if (joinState === TASK_JOIN_STATE.NOT_JOINED) return true;
  if (!canResetTask(taskInfo)) return false;
  return (
    (joinState === TASK_JOIN_STATE.ENDED ||
      (joinState === TASK_JOIN_STATE.IN_PROGRESS && userState === TASK_USER_STATE.MISSED_CANNOT_RESIGN)) &&
    !isRewardReady(taskInfo, taskKind)
  );
}

function shortJson(data, maxLen = 260) {
  try {
    const text = JSON.stringify(data);
    return text.length > maxLen ? `${text.slice(0, maxLen)}...` : text;
  } catch {
    return String(data);
  }
}

function formatShareState(taskInfo) {
  const shareState = getTodayShareState(taskInfo);
  switch (shareState) {
    case TASK_SHARE_STATE.NO_NEED:
      return "无需分享";
    case TASK_SHARE_STATE.NOT_SHARED:
      return "⬜未分享";
    case TASK_SHARE_STATE.SHARED:
      return "✅已分享";
    default:
      return `未知(${shareState})`;
  }
}

function formatRewardState(taskInfo, taskKind) {
  if (taskKind === "challenge") {
    const recState = Number(taskInfo?.prizeList?.[0]?.recState ?? TASK_REC_STATE.NOT_AVAILABLE);
    if (recState === TASK_REC_STATE.AVAILABLE) return "✅可领取";
    if (recState === TASK_REC_STATE.RECEIVED) return "✅已领取";
    return "⬜暂不可领";
  }

  if (canCashWithdraw(taskInfo)) {
    const amount = taskInfo?.taskDetail?.bottomAmount ?? 0;
    return amount ? `✅可提现 ¥${amount}` : "✅可提现";
  }
  if (hasTaskRewardRecord(taskInfo)) return "✅已提现/已领取";
  return "⬜暂不可领";
}

function getChallengeTaskStatusText(status) {
  switch (Number(status)) {
    case 1:
      return "立即领取";
    case 2:
      return "去完成";
    case 3:
      return "领取奖励";
    case 4:
      return "已完成";
    case 5:
      return "领取奖励";
    case 6:
      return "挑战失败";
    case 7:
      return "活动结束";
    default:
      return `未知(${status ?? "?"})`;
  }
}

async function exchangeCodeToLogin(code, appId) {
  const sceneRaw = String(getEnv("TC_SCENE") || DEFAULT_SCENE).trim();
  const scene = Number.isFinite(Number(sceneRaw)) ? Number(sceneRaw) : DEFAULT_SCENE;
  const headers = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.60(0x18003c32) NetType/4G Language/zh_CN miniProgram/wx336dcaf6a1ecf632",
    "Origin": "https://wx.17u.cn",
    "Referer": `https://servicewechat.com/${appId}/`,
    "Connection": "keep-alive",
  };

  // 来自反编译代码的真实调用：/wechatappapi/wxUser/login + {code, scene}
  const payloads = [
    { code, scene },
    { code, scene, appId },
    { code, scene, appid: appId },
    { code, appId, appid: appId, refid: "2000845210" },
    { code, appId, refid: "2000845210" },
    { code, appid: appId, refid: "2000845210" },
    { code, appId },
    { code, appid: appId },
    { code }
  ];

  const urls = [LOGIN_URL, LOGIN_URL_V2];
  for (const url of urls) {
    for (const payload of payloads) {
      try {
        const resp = await httpRequest(url, "POST", headers, payload);
        const tokenData = extractLoginTokens(resp);
        if (tokenData) return tokenData;
        if (url === LOGIN_URL && resp && typeof resp === "object") {
          const msg = String(resp.retMsg || resp.msg || "").trim();
          const codeText = String(resp.retCode || resp.code || "").trim();
          if (msg || codeText) {
            warn(`登录接口返回: ${codeText || "无code"} ${msg || ""}`.trim());
          }
        }
      } catch {
        // ignore and try next payload/url
      }
    }
  }
  return null;
}

async function parseAccountsFromGetCode() {
  const appId = getEnv("TC_APP_ID") || DEFAULT_APP_ID;
  const codeMap = runGetCodePy(appId);
  const entries = Object.entries(codeMap || {});
  if (!entries.length) return [];

  info(`getCode.py 返回 ${entries.length} 个 code，开始换取登录态...`);
  const accounts = [];
  for (const [name, rawCode] of entries) {
    const code = String(rawCode || "").trim();
    if (!code) continue;

    const remark = (name || "").trim() || `自动账号${accounts.length + 1}`;
    info(`自动登录: ${remark}`);
    const loginData = await exchangeCodeToLogin(code, appId);
    if (!loginData) {
      warn(`${remark} 自动登录失败（code 可能已过期）`);
      continue;
    }

    accounts.push({
      remark,
      index: accounts.length + 1,
      sectoken: loginData.sectoken,
      openId: loginData.openId,
      userKey: loginData.userKey || ""
    });
    ok(`${remark} 自动登录成功`);
    await sleep(300);
  }
  return accounts;
}

async function parseAccounts() {
  const fromEnv = parseAccountsFromEnv();
  if (fromEnv.length) {
    info("账号来源：TC_ACCOUNT");
    return fromEnv;
  }

  info("未配置 TC_ACCOUNT，尝试调用 getCode.py 自动获取...");
  return parseAccountsFromGetCode();
}

// ============================================================
// H5 API请求
// ============================================================
function request(options, body) {
  return new Promise((resolve, reject) => {
    const req = https.request(options, (res) => {
      const chunks = [];
      res.on("data", c => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)));
      res.on("end", () => {
        const data = bufferToResponseText(Buffer.concat(chunks), res.headers);
        try { resolve({ status: res.statusCode, data: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, data, raw: true }); }
      });
    });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function post(path, payload, extraHeaders = {}) {
  const bodyStr = JSON.stringify(payload);
  const base = {
    "Host": "wx.17u.cn",
    "Content-Type": "application/json;charset=UTF-8",
    "Content-Length": Buffer.byteLength(bodyStr).toString(),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
  };
  const headers = Object.assign(base, extraHeaders);
  return request({ hostname: "wx.17u.cn", path, method: "POST", headers }, bodyStr);
}

async function get(path, extraHeaders = {}) {
  const base = {
    "Host": "wx.17u.cn",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
  };
  const headers = Object.assign(base, extraHeaders);
  return request({ hostname: "wx.17u.cn", path, method: "GET", headers });
}

function h5Headers(token, openid, unionid, cookieObj) {
  const h = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.60(0x18003c32) NetType/4G Language/zh_CN miniProgram/wx336dcaf6a1ecf632",
    "Referer": "https://wx.17u.cn/memberft/student/studentcard/signIn?refid=2000845210",
    "Origin": "https://wx.17u.cn",
    "platform": "WX_MP",
    "TC-PLATFORM-CODE": "WX_MP",
    "TC-USER-TOKEN": token,
    "userToken": token,
    "userTokenMode": "1",
    "accountSystem": "1",
    "TC-OS-TYPE": "1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
  };
  if (openid) h["openId"] = openid;
  if (unionid) h["userKey"] = unionid;
  if (cookieObj && Object.keys(cookieObj).length > 0) {
    h["Cookie"] = Object.entries(cookieObj).map(([k, v]) => k + "=" + v).join("; ");
  }
  return h;
}

function mileageHeaders(sectoken) {
  return {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.60(0x18003c32) NetType/4G Language/zh_CN miniProgram/wx336dcaf6a1ecf632",
    "Origin": "https://wx.17u.cn",
    "Referer": "https://wx.17u.cn/page/AC/sign/msindex/msindex",
    "platform": "WX_MP",
    "osType": 1,
    "secToken": sectoken,
    "TC-MALL-PLATFORM-CODE": "WX_MP",
    "TC-MALL-USER-TOKEN": sectoken,
  };
}

// ============================================================
// 日志函数
// ============================================================
const log = (...a) => console.log("[同程]", ...a);
const ok = (...a) => console.log("✅ [同程]", ...a);
const warn = (...a) => console.log("⚠️  [同程]", ...a);
const error = (...a) => console.log("❌ [同程]", ...a);
const info = (...a) => console.log("ℹ️  [同程]", ...a);
const sep = () => console.log("─".repeat(50));

// ============================================================
// H5打卡接口
// ============================================================
async function getActInfo(headers) {
  try {
    const r = await post("/platformflowpool/signTask/getActInfo", {}, headers);
    if (r.raw) { warn("getActInfo 非JSON:", String(r.data).substring(0, 150)); return null; }
    if (r.data.code === 0) return r.data.data;
    warn("getActInfo:", r.data.msg || r.data.code);
    return null;
  } catch (e) { error("getActInfo:", e.message); return null; }
}

async function actionVisit(userId, headers) {
  try {
    const r = await post("/platformflowpool/signTask/actionVisit", { userId }, headers);
    if (r.raw) { warn("actionVisit 非JSON:", String(r.data).substring(0, 100)); return null; }
    if (r.data && r.data.code === 0) {
      const d = r.data.data;
      info(`actionVisit 上报成功: 连续 ${d?.continueSignCount ?? "?"} 天, sign=${d?.sign}, visit=${d?.visit}`);
      return r.data.data;
    }
    warn(`actionVisit 返回: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) { error("actionVisit:", e.message); return null; }
}

async function actionReSign(userId, headers) {
  try {
    const r = await post("/platformflowpool/signTask/actionReSign", { userId }, headers);
    if (r.raw) { warn("actionReSign 非JSON:", String(r.data).substring(0, 100)); return null; }
    if (r.data && r.data.code === 0) {
      info(`actionReSign 返回成功: ${shortJson(r.data.data || {})}`);
      return r.data.data;
    }
    warn(`actionReSign 返回: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) { error("actionReSign:", e.message); return null; }
}

async function actionShare(userId, headers) {
  try {
    const r = await post("/platformflowpool/signTask/actionShare", { userId }, headers);
    if (r.raw) { warn("actionShare 非JSON:", String(r.data).substring(0, 100)); return null; }
    if (r.data && r.data.code === 0) {
      const d = r.data.data;
      info(`actionShare 返回成功: 连续 ${d?.continueSignCount ?? "?"} 天, sign=${d?.sign}, share=${d?.share}`);
      return d;
    }
    warn(`actionShare 返回: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) { error("actionShare:", e.message); return null; }
}

async function actionReward(userId, type, headers) {
  try {
    const r = await post("/platformflowpool/signTask/actionReward", { userId, type }, headers);
    if (r.raw) { warn("actionReward 非JSON:", String(r.data).substring(0, 120)); return null; }
    if (r.data && r.data.code === 0) {
      info(`actionReward 返回成功: type=${type}, data=${shortJson(r.data.data || {})}`);
      return r.data.data || {};
    }
    warn(`actionReward 返回: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) { error("actionReward:", e.message); return null; }
}

async function getTaskInfo(actId, headers) {
  try {
    const r = await post("/platformflowpool/signTask/getTaskInfo", { actId }, headers);
    if (r.raw) { warn("getTaskInfo 非JSON:", String(r.data).substring(0, 100)); return null; }
    if (r.data.code === 0) return r.data.data;
    warn(`getTaskInfo(${actId}):`, r.data.msg || r.data.code);
    return null;
  } catch (e) { error("getTaskInfo:", e.message); return null; }
}

async function doSignTask(actId, userId, headers) {
  try {
    const body = userId ? { actId, userId } : { actId };
    log(`startTask 请求: ${shortJson(body)}`);
    const r = await post("/platformflowpool/signTask/startTask", body, headers);
    if (r.raw) {
      warn("startTask 非JSON:", String(r.data).substring(0, 200));
      return { code: -1, msg: "非JSON响应" };
    }
    const data = r.data || {};
    if (Number(data.code) === 0) {
      info(`startTask 返回成功: ${data.msg || "OK"}`);
    } else {
      warn(`startTask 返回: ${data.code ?? "无code"} ${data.msg || ""}`.trim());
    }
    return data;
  } catch (e) { error("doSignTask:", e.message); return null; }
}

async function getRewardRecord(headers) {
  try {
    const r = await post("/platformflowpool/signTask/getRewardRecord", { pageNum: 1, pageSize: 10 }, headers);
    if (r.data && r.data.code === 0) return r.data.data;
    return null;
  } catch (e) { return null; }
}

async function listChallengeTasks(activityCode, headers) {
  try {
    const r = await post("/platformflowpool/taskApi/listTask", { activityCode }, headers);
    if (r.raw) { warn("listTask 非JSON:", String(r.data).substring(0, 120)); return null; }
    if (r.data?.code === 0) return r.data.data;
    warn(`listTask: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) { error("listTask:", e.message); return null; }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ============================================================
function logTaskSnapshot(actLabel, taskInfo, taskKind) {
  const cal = taskInfo?.calendarInfo;
  const userId = extractTaskUserId(taskInfo);
  info(`${actLabel} 连续天数: ${cal?.continueSignCount ?? "?"} 天`);
  info(`${actLabel} 今日访问: ${isTodayVisited(taskInfo) ? "✅已访问" : "⬜未访问"}`);
  info(`${actLabel} 今日分享: ${formatShareState(taskInfo)}`);
  info(`${actLabel} 今日完成: ${isDailyTaskComplete(taskInfo) ? "✅已完成" : "⬜未完成"}`);
  info(`${actLabel} 奖励状态: ${formatRewardState(taskInfo, taskKind)}`);
  info(`${actLabel} 单次奖励: ¥${taskInfo?.allNewAmount || taskInfo?.partNewAmount || 0}`);
  info(`${actLabel} 参与状态: join=${taskInfo?.userJoinState ?? "?"}, userState=${cal?.userState ?? "?"}`);
  if (userId) info(`${actLabel} userId: ${userId}`);
}

async function maybeClaimTaskReward(actId, actLabel, taskKind, taskInfo, headers) {
  if (!isRewardReady(taskInfo, taskKind)) return taskInfo;

  const userId = extractTaskUserId(taskInfo);
  if (!userId) {
    warn(`${actLabel}: 奖励可领但未获取到 userId，先不自动领取`);
    return taskInfo;
  }

  const rewardType = taskKind === "challenge"
    ? TASK_REWARD_TYPE.CLAIM_CHALLENGE
    : TASK_REWARD_TYPE.WITHDRAW;
  const actionText = taskKind === "challenge" ? "领取奖励" : "提现";
  log(`执行 ${actLabel} ${actionText}（actionReward）...`);
  await sleep(400);
  const rewardResult = await actionReward(userId, rewardType, headers);
  if (!rewardResult) return taskInfo;

  ok(`${actLabel} ${actionText}已提交`);
  await sleep(800);
  return (await getTaskInfo(actId, headers)) || taskInfo;
}

function printChallengeTaskList(taskListRsp) {
  const tasks = Array.isArray(taskListRsp?.values) ? taskListRsp.values : [];
  if (!tasks.length) {
    info("挑战附加任务: 暂无可展示任务");
    return;
  }

  log("\n🧩 挑战附加任务列表");
  tasks.forEach((task, idx) => {
    const detailUrl = task?.guid
      ? `https://m.17u.cn/activity/sspa/challengeTask/taskDetail?taskId=${encodeURIComponent(task.guid)}`
      : "";
    info(`  [${idx + 1}] ${task?.title || "未命名任务"} | ${task?.subTitle || "-"} | ${getChallengeTaskStatusText(task?.status)} | status=${task?.status ?? "?"}`);
    if (task?.guid) info(`      guid: ${task.guid}`);
    if (detailUrl) info(`      详情: ${detailUrl}`);
  });
  warn("挑战附加任务目前只定位到列表/详情页入口，暂未确认安全可用的直调完成接口，脚本先只展示。");
}

async function doSignByVisit(actId, actLabel, headers, taskKind = "cash") {
  log(`\n${actLabel} 打卡`);

  let taskInfo = await getTaskInfo(actId, headers);
  if (!taskInfo) {
    warn(`${actLabel}: getTaskInfo 失败，跳过`);
    return null;
  }

  logTaskSnapshot(actLabel, taskInfo, taskKind);

  if (shouldClaimRewardBeforeReset(taskInfo, taskKind)) {
    taskInfo = await maybeClaimTaskReward(actId, actLabel, taskKind, taskInfo, headers);
    logTaskSnapshot(`${actLabel}（领奖后）`, taskInfo, taskKind);
  }

  if (shouldStartTask(taskInfo, taskKind)) {
    log(`执行 ${actLabel} 开始/重启任务（startTask）...`);
    await sleep(400);
    await doSignTask(actId, "", headers);
    await sleep(800);
    taskInfo = await getTaskInfo(actId, headers);
    if (!taskInfo) {
      warn(`${actLabel}: startTask 后刷新任务失败`);
      return null;
    }
    logTaskSnapshot(`${actLabel}（开始后）`, taskInfo, taskKind);
  }

  let userId = extractTaskUserId(taskInfo);
  let cal = taskInfo.calendarInfo;

  if (Number(cal?.userState) === TASK_USER_STATE.MISSED_CAN_RESIGN) {
    if (!userId) {
      warn(`${actLabel}: 需要补签但未获取到 userId`);
      return taskInfo;
    }
    log(`执行 ${actLabel} 补签（actionReSign）...`);
    await sleep(400);
    await actionReSign(userId, headers);
    await sleep(800);
    taskInfo = await getTaskInfo(actId, headers);
    if (!taskInfo) {
      warn(`${actLabel}: 补签后刷新任务失败`);
      return null;
    }
    logTaskSnapshot(`${actLabel}（补签后）`, taskInfo, taskKind);
    userId = extractTaskUserId(taskInfo);
    cal = taskInfo.calendarInfo;
  }

  if (!userId) {
    if (isDailyTaskComplete(taskInfo)) {
      ok(`${actLabel}: 今日任务已完成`);
      return await maybeClaimTaskReward(actId, actLabel, taskKind, taskInfo, headers);
    }

    warn(`${actLabel}: 未获取到 userId，无法执行 actionVisit，返回摘要: ${shortJson({
      joinState: taskInfo?.userJoinState,
      calendarInfo: taskInfo?.calendarInfo || null,
      amount: taskInfo?.allNewAmount || taskInfo?.partNewAmount || 0,
      todayShareState: taskInfo?.calendarInfo?.todayShareState,
      keys: taskInfo && typeof taskInfo === "object" ? Object.keys(taskInfo).slice(0, 12) : []
    })}`);
    return taskInfo;
  }

  if (needsTodayShare(taskInfo)) {
    log(`执行 ${actLabel} 分享任务（actionShare）...`);
    await sleep(400);
    await actionShare(userId, headers);
    await sleep(800);
    taskInfo = await getTaskInfo(actId, headers);
    if (!taskInfo) {
      warn(`${actLabel}: 分享后刷新任务失败`);
      return null;
    }
    logTaskSnapshot(`${actLabel}（分享后）`, taskInfo, taskKind);
    userId = extractTaskUserId(taskInfo);
  }

  if (!isTodayVisited(taskInfo)) {
    log(`执行 ${actLabel} 打卡（actionVisit）...`);
    await sleep(500);
    const visitResult = await actionVisit(userId, headers);
    if (visitResult?.sign === true || visitResult?.visit === true) {
      ok(`${actLabel} 打卡已提交，连续 ${visitResult.continueSignCount ?? "?"} 天`);
    }
    await sleep(800);
    taskInfo = await getTaskInfo(actId, headers);
    if (!taskInfo) {
      warn(`${actLabel}: 访问后刷新任务失败`);
      return null;
    }
    logTaskSnapshot(`${actLabel}（访问后）`, taskInfo, taskKind);
    userId = extractTaskUserId(taskInfo);
  }

  if (userId && needsTodayShare(taskInfo)) {
    log(`执行 ${actLabel} 二次补分享（actionShare）...`);
    await sleep(400);
    await actionShare(userId, headers);
    await sleep(800);
    taskInfo = await getTaskInfo(actId, headers);
    if (!taskInfo) {
      warn(`${actLabel}: 二次分享后刷新任务失败`);
      return null;
    }
    logTaskSnapshot(`${actLabel}（二次分享后）`, taskInfo, taskKind);
  }

  if (isDailyTaskComplete(taskInfo)) {
    ok(`✅ ${actLabel} 今日任务已完成`);
  } else {
    warn(`${actLabel}: 今日任务仍未完成，返回摘要: ${shortJson({
      userId,
      joinState: taskInfo?.userJoinState,
      todayVisit: taskInfo?.calendarInfo?.isTodayVisit,
      todayShareState: taskInfo?.calendarInfo?.todayShareState,
      userState: taskInfo?.calendarInfo?.userState,
      rewardState: formatRewardState(taskInfo, taskKind),
      keys: taskInfo && typeof taskInfo === "object" ? Object.keys(taskInfo).slice(0, 12) : []
    })}`);
  }

  taskInfo = await maybeClaimTaskReward(actId, actLabel, taskKind, taskInfo, headers);
  if (isRewardReady(taskInfo, taskKind)) {
    warn(`${actLabel}: 奖励状态仍显示可领，请手动复核，摘要: ${shortJson({
      rewardState: formatRewardState(taskInfo, taskKind),
      joinState: taskInfo?.userJoinState,
      userState: taskInfo?.calendarInfo?.userState,
      recPrizeId: taskInfo?.recPrizeId ?? null
    })}`);
  } else if (taskKind === "cash" && hasTaskRewardRecord(taskInfo)) {
    ok(`${actLabel}: 提现/领奖状态已更新`);
  } else if (taskKind === "challenge" && Number(taskInfo?.prizeList?.[0]?.recState) === TASK_REC_STATE.RECEIVED) {
    ok(`${actLabel}: 奖励已领取`);
  }

  return taskInfo;
}

// ============================================================
// 执行现金打卡
// ============================================================
async function runCashSign(headers) {
  sep();
  log("=== 连续打卡拿现金签到 ===");

  const actInfo = await getActInfo(headers);
  if (!actInfo) {
    error("获取活动信息失败");
    return;
  }

  const cashActId = actInfo.cashAwardAct?.actId || "sign:2026:0758";
  const chalActId = actInfo.challengeAwardAct?.actId || "sign:2026:8366";
  info(`现金活动: ${cashActId}`);
  info(`挑战活动: ${chalActId}`);
  if (actInfo.challengeTaskId) info(`挑战附加任务ID: ${actInfo.challengeTaskId}`);

  await doSignByVisit(cashActId, "📍现金奖励", headers, "cash");
  await sleep(800);

  await doSignByVisit(chalActId, "🏆挑战奖励", headers, "challenge");

  await sleep(800);

  if (actInfo.challengeTaskId) {
    const challengeTasks = await listChallengeTasks(actInfo.challengeTaskId, headers);
    if (challengeTasks) printChallengeTaskList(challengeTasks);
    await sleep(800);
  }

  log("\n💰 近期奖励记录");
  const records = await getRewardRecord(headers);
  if (records && records.recordList?.length > 0) {
    records.recordList.slice(0, 5).forEach((r) => {
      info(`  [${r.calendar || ""}] 连续${r.continueDay}天 +¥${r.totalAmount || 0}`);
    });
  }
}

// ============================================================
// 同程里程 / 积分任务
// ============================================================
function getMileageTaskStatusText(status) {
  switch (Number(status)) {
    case MILEAGE_TASK_STATUS.NOT_STARTED:
      return "⬜未开始";
    case MILEAGE_TASK_STATUS.IN_PROGRESS:
      return "🕒进行中";
    case MILEAGE_TASK_STATUS.REWARD_READY:
      return "🎁待领奖";
    case MILEAGE_TASK_STATUS.FINISHED:
      return "✅已完成";
    case MILEAGE_TASK_STATUS.ENDED:
      return "⛔已结束";
    default:
      return `未知(${status ?? "?"})`;
  }
}

function getTodayDateRange() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const y = now.getFullYear();
  const m = pad(now.getMonth() + 1);
  const d = pad(now.getDate());
  return {
    startDate: `${y}-${m}-${d} 00:00:00`,
    endDate: `${y}-${m}-${d} 23:59:59`,
  };
}

function sortMileageTasksByStatus(tasks) {
  const order = (status) => {
    switch (Number(status)) {
      case MILEAGE_TASK_STATUS.REWARD_READY:
        return 1;
      case MILEAGE_TASK_STATUS.NOT_STARTED:
      case MILEAGE_TASK_STATUS.IN_PROGRESS:
        return 2;
      case MILEAGE_TASK_STATUS.FINISHED:
        return 3;
      default:
        return 4;
    }
  };
  return [...tasks].sort((a, b) => order(a?.status) - order(b?.status));
}

function getMileageTaskSchemeGuid(detailGuid, defaultSchemeGuid) {
  return detailGuid === MILEAGE_TASK_DETAIL_GUID_PULLDOWN_BUBBLE || detailGuid === MILEAGE_TASK_DETAIL_GUID_PULLDOWN_LIST
    ? MILEAGE_PULLDOWN_TASK_SCHEME_GUID
    : defaultSchemeGuid;
}

function isMileageClientOnlyTask(detailGuid) {
  return detailGuid === MILEAGE_TASK_DETAIL_GUID_TRAIN
    || detailGuid === MILEAGE_TASK_DETAIL_GUID_PULLDOWN_BUBBLE
    || detailGuid === MILEAGE_TASK_DETAIL_GUID_PULLDOWN_LIST;
}

function getMileageClientOnlyReason(detailGuid) {
  if (detailGuid === MILEAGE_TASK_DETAIL_GUID_TRAIN) {
    return "需要手动完成火车票查询";
  }
  if (detailGuid === MILEAGE_TASK_DETAIL_GUID_PULLDOWN_BUBBLE || detailGuid === MILEAGE_TASK_DETAIL_GUID_PULLDOWN_LIST) {
    return "依赖钱包下拉/客户端能力";
  }
  return "依赖客户端交互";
}

function logMileageTask(task, idx = null) {
  const title = task?.title || task?.taskTitle || task?.detailGuid || "未命名里程任务";
  const prefix = idx == null ? "-" : `[${idx + 1}]`;
  info(`  ${prefix} ${title} | ${getMileageTaskStatusText(task?.status)} | taskType=${task?.taskType ?? "?"} | prize=${task?.prizeTitle || "-"}`);
  if (task?.detailGuid) info(`      detailGuid: ${task.detailGuid}`);
  if (task?.xcxJumpUrl) info(`      跳转: ${task.xcxJumpUrl}`);
}

async function getMileageTopInfo(headers) {
  try {
    const r = await post("/wxmpsign/home/top", {}, headers);
    if (r.raw) {
      warn("里程首页信息 非JSON:", String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 200) return r.data.data;
    warn(`里程首页信息: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("getMileageTopInfo:", e.message);
    return null;
  }
}

async function getMileageSignInfo(headers) {
  try {
    const r = await post("/wxmpsign/sign/getSignInfo", {}, headers);
    if (r.raw) {
      warn("里程签到信息 非JSON:", String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 200) return r.data.data;
    warn(`里程签到信息: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("getMileageSignInfo:", e.message);
    return null;
  }
}

async function saveMileageSignInfo(headers) {
  try {
    const r = await post("/wxmpsign/sign/saveSignInfo", {}, headers);
    if (r.raw) {
      warn("里程签到执行 非JSON:", String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 200) return r.data.data;
    warn(`里程签到执行: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("saveMileageSignInfo:", e.message);
    return null;
  }
}

async function getShareMileagePoolInfo(headers) {
  try {
    const r = await get("/wxmpsign/share/mileage/getTodayTotalShareMileage", headers);
    if (r.raw) {
      warn("分享奖池信息 非JSON:", String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 200) return r.data.data;
    warn(`分享奖池信息: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("getShareMileagePoolInfo:", e.message);
    return null;
  }
}

async function getShareMileageConfig(headers) {
  try {
    const r = await get("/wxmpsign/share/mileage/getShareMileageConfig", headers);
    if (r.raw) {
      warn("分享活动配置 非JSON:", String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 200) return r.data.data;
    warn(`分享活动配置: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("getShareMileageConfig:", e.message);
    return null;
  }
}

async function getShareMileageParticipation(headers, startDate, endDate, limitAmount = 1) {
  try {
    const r = await post("/wxmpsign/share/mileage/getUserParticipationInfo", {
      startDate,
      endDate,
      limitAmount
    }, headers);
    if (r.raw) {
      warn("分享报名记录 非JSON:", String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 200) return r.data.data;
    warn(`分享报名记录: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("getShareMileageParticipation:", e.message);
    return null;
  }
}

async function participateShareMileage(headers) {
  try {
    const r = await post("/wxmpsign/share/mileage/participateInShareMileage", {}, headers);
    if (r.raw) {
      warn("分享打卡报名 非JSON:", String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 200) return r.data.data || {};
    warn(`分享打卡报名: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("participateShareMileage:", e.message);
    return null;
  }
}

async function finishYesterdayShareMileage(headers) {
  try {
    const check = await get("/wxmpsign/share/mileage/yesterdayParticipateInShareMileage", headers);
    if (check.raw) {
      warn("昨日分享奖励校验 非JSON:", String(check.data).substring(0, 120));
      return false;
    }
    if (Number(check.data?.code) !== 200 || !check.data?.data) return false;

    const reward = await get("/wxmpsign/share/mileage/finishSignInTime", headers);
    if (reward.raw) {
      warn("昨日分享奖励领取 非JSON:", String(reward.data).substring(0, 120));
      return false;
    }
    if (Number(reward.data?.code) === 200 && reward.data?.data) {
      ok("昨日分享打卡奖励已自动结算");
      return true;
    }
    return false;
  } catch (e) {
    error("finishYesterdayShareMileage:", e.message);
    return false;
  }
}

async function getMileageTaskList(headers, schemeGuid = MILEAGE_TASK_SCHEME_GUID) {
  try {
    const r = await post("/qiushiinnerapi/task/detailList", {
      detailGuid: "",
      pageNum: 1,
      pageSize: 999,
      schemeGuid
    }, headers);
    if (r.raw) {
      warn("里程任务列表 非JSON:", String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 0) return r.data.data;
    warn(`里程任务列表: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("getMileageTaskList:", e.message);
    return null;
  }
}

async function getMileageTaskDetail(detailGuid, schemeGuid, headers) {
  try {
    const r = await post("/qiushiinnerapi/task/detail", {
      detailGuid,
      schemeGuid
    }, headers);
    if (r.raw) {
      warn(`里程任务详情(${detailGuid}) 非JSON:`, String(r.data).substring(0, 120));
      return null;
    }
    if (Number(r.data?.code) === 0) {
      const data = r.data.data || {};
      return {
        task: Array.isArray(data.taskDetails) ? (data.taskDetails[0] || null) : null,
        taskScheme: data.taskScheme || null
      };
    }
    warn(`里程任务详情(${detailGuid}): ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) {
    error("getMileageTaskDetail:", e.message);
    return null;
  }
}

async function startMileageTask(detailGuid, schemeGuid, headers) {
  try {
    const r = await post("/qiushiinnerapi/task/startTask", {
      detailGuid,
      schemeGuid
    }, headers);
    if (r.raw) {
      warn(`里程任务开始(${detailGuid}) 非JSON:`, String(r.data).substring(0, 120));
      return false;
    }
    if (Number(r.data?.code) === 0) return true;
    warn(`里程任务开始(${detailGuid}): ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return false;
  } catch (e) {
    error("startMileageTask:", e.message);
    return false;
  }
}

async function finishMileageTask(detailGuid, schemeGuid, headers) {
  try {
    const r = await post("/qiushiinnerapi/task/finishTask", {
      detailGuid,
      schemeGuid
    }, headers);
    if (r.raw) {
      warn(`里程任务完成(${detailGuid}) 非JSON:`, String(r.data).substring(0, 120));
      return false;
    }
    if (Number(r.data?.code) === 0) return true;
    warn(`里程任务完成(${detailGuid}): ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return false;
  } catch (e) {
    error("finishMileageTask:", e.message);
    return false;
  }
}

async function sendMileageTaskPrize(detailGuid, schemeGuid, headers) {
  try {
    const r = await post("/qiushiinnerapi/task/sendTaskPrize", {
      detailGuid,
      schemeGuid
    }, headers);
    if (r.raw) {
      warn(`里程任务领奖(${detailGuid}) 非JSON:`, String(r.data).substring(0, 120));
      return false;
    }
    if (Number(r.data?.code) === 0) return true;
    warn(`里程任务领奖(${detailGuid}): ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return false;
  } catch (e) {
    error("sendMileageTaskPrize:", e.message);
    return false;
  }
}

async function processMileageTask(task, defaultSchemeGuid, headers) {
  if (!task?.detailGuid) return task;

  const title = task.title || task.detailGuid;
  const detailGuid = task.detailGuid;
  const schemeGuid = getMileageTaskSchemeGuid(detailGuid, defaultSchemeGuid);
  const refreshTask = async () => {
    const detail = await getMileageTaskDetail(detailGuid, schemeGuid, headers);
    return detail?.task || task;
  };

  if (Number(task?.taskType) !== 0) {
    warn(`${title}: 暂不支持 taskType=${task?.taskType ?? "?"}`);
    return task;
  }

  let current = task;
  let status = Number(current.status);

  if (status === MILEAGE_TASK_STATUS.REWARD_READY) {
    log(`执行里程任务领奖: ${title}`);
    if (await sendMileageTaskPrize(detailGuid, schemeGuid, headers)) {
      await sleep(600);
      current = await refreshTask();
    }
    return current;
  }

  if (isMileageClientOnlyTask(detailGuid)) {
    if (detailGuid === MILEAGE_TASK_DETAIL_GUID_TRAIN && status === MILEAGE_TASK_STATUS.NOT_STARTED) {
      log(`执行里程任务开始: ${title}`);
      if (await startMileageTask(detailGuid, schemeGuid, headers)) {
        await sleep(500);
        current = await refreshTask();
        status = Number(current.status);
      }
    }

    if (status === MILEAGE_TASK_STATUS.REWARD_READY) {
      log(`执行里程任务领奖: ${title}`);
      if (await sendMileageTaskPrize(detailGuid, schemeGuid, headers)) {
        await sleep(600);
        current = await refreshTask();
      }
      return current;
    }

    if (status === MILEAGE_TASK_STATUS.NOT_STARTED || status === MILEAGE_TASK_STATUS.IN_PROGRESS) {
      warn(`${title}: ${getMileageClientOnlyReason(detailGuid)}，脚本先不代做`);
    }
    return current;
  }

  if (status === MILEAGE_TASK_STATUS.NOT_STARTED) {
    log(`执行里程任务开始: ${title}`);
    if (await startMileageTask(detailGuid, schemeGuid, headers)) {
      await sleep(400);
      current = await refreshTask();
      status = Number(current.status);
    }
  }

  if (status === MILEAGE_TASK_STATUS.NOT_STARTED || status === MILEAGE_TASK_STATUS.IN_PROGRESS) {
    log(`执行里程任务完成: ${title}`);
    if (await finishMileageTask(detailGuid, schemeGuid, headers)) {
      await sleep(600);
      current = await refreshTask();
      status = Number(current.status);
    }
  }

  if (status === MILEAGE_TASK_STATUS.REWARD_READY) {
    log(`执行里程任务领奖: ${title}`);
    if (await sendMileageTaskPrize(detailGuid, schemeGuid, headers)) {
      await sleep(600);
      current = await refreshTask();
    }
  }

  return current;
}

async function inspectMileageSpecialTask(label, detailGuid, schemeGuid, headers) {
  const detail = await getMileageTaskDetail(detailGuid, schemeGuid, headers);
  const task = detail?.task;
  if (!task) return null;

  log(`\n${label}`);
  logMileageTask(task);
  const newTask = await processMileageTask(task, schemeGuid, headers);
  if (newTask && Number(newTask.status) !== Number(task.status)) {
    info(`${label} 状态更新: ${getMileageTaskStatusText(task.status)} -> ${getMileageTaskStatusText(newTask.status)}`);
  }
  return newTask;
}

async function runMileageTasks(sectoken) {
  sep();
  log("=== 同程里程 / 积分任务 ===");

  const headers = mileageHeaders(sectoken);
  const topInfo = await getMileageTopInfo(headers);
  if (topInfo?.remainCoin != null) info(`当前里程：${topInfo.remainCoin}`);

  let signInfo = await getMileageSignInfo(headers);
  if (!signInfo) {
    warn("未获取到里程签到信息，跳过后续里程任务");
    return;
  }

  info(`今日里程签到：${signInfo.todaySigned ? "✅已签到" : "⬜未签到"}`);
  info(`连续签到：${signInfo.periodContinuedSignDays ?? "?"} 天`);
  if (!signInfo.todaySigned) {
    const signResult = await saveMileageSignInfo(headers);
    if (signResult) {
      ok(`里程签到成功，本次获得 ${signResult.totalIncome || signResult.signMileage || 0} 里程`);
      await sleep(600);
      signInfo = await getMileageSignInfo(headers) || signInfo;
    }
  }

  const sharePoolInfo = await getShareMileagePoolInfo(headers);
  if (sharePoolInfo) {
    info(`分享奖池：${sharePoolInfo.totalMileage || 0} 里程，报名人数 ${sharePoolInfo.participateCount || 0}`);
  }

  const shareConfig = await getShareMileageConfig(headers);
  if (shareConfig) {
    info(`分享活动：最晚签到 ${shareConfig.lastestSignTime || "?"} 点，预计发放 ${shareConfig.sendMileageTime || "?"} 点`);
  }

  const todayRange = getTodayDateRange();
  const joinedRecords = await getShareMileageParticipation(headers, todayRange.startDate, todayRange.endDate, 1);
  const isJoined = Array.isArray(joinedRecords) && joinedRecords.length > 0;
  info(`分享打卡报名：${isJoined ? "✅已报名" : "⬜未报名"}`);
  if (!isJoined) {
    const joinResult = await participateShareMileage(headers);
    if (joinResult) ok("分享打卡任务已自动报名");
  }

  await finishYesterdayShareMileage(headers);
  await sleep(500);

  await inspectMileageSpecialTask("🚆 火车票查询任务", MILEAGE_TASK_DETAIL_GUID_TRAIN, MILEAGE_TASK_SCHEME_GUID, headers);
  await sleep(500);
  await inspectMileageSpecialTask("📥 钱包下拉任务", MILEAGE_TASK_DETAIL_GUID_PULLDOWN_BUBBLE, MILEAGE_PULLDOWN_TASK_SCHEME_GUID, headers);
  await sleep(500);
  await inspectMileageSpecialTask("📋 钱包列表任务", MILEAGE_TASK_DETAIL_GUID_PULLDOWN_LIST, MILEAGE_PULLDOWN_TASK_SCHEME_GUID, headers);
  await sleep(500);

  const taskListData = await getMileageTaskList(headers, MILEAGE_TASK_SCHEME_GUID);
  if (!taskListData) {
    warn("未获取到里程任务列表");
    return;
  }

  const schemeGuid = taskListData.taskScheme?.schemeGuid || MILEAGE_TASK_SCHEME_GUID;
  const tasks = sortMileageTasksByStatus(taskListData.taskDetails || []);
  if (!tasks.length) {
    info("里程任务列表：暂无任务");
    return;
  }

  log("\n🎯 里程任务列表");
  tasks.forEach((task, idx) => logMileageTask(task, idx));

  for (const task of tasks) {
    const title = task.title || task.detailGuid || "未命名里程任务";
    const beforeStatus = Number(task.status);
    const afterTask = await processMileageTask(task, schemeGuid, headers);
    if (afterTask && Number(afterTask.status) !== beforeStatus) {
      info(`${title} 状态更新: ${getMileageTaskStatusText(beforeStatus)} -> ${getMileageTaskStatusText(afterTask.status)}`);
    }
    await sleep(500);
  }

  await sleep(800);
  const finalTaskListData = await getMileageTaskList(headers, schemeGuid);
  const finalTasks = sortMileageTasksByStatus(finalTaskListData?.taskDetails || []);
  if (finalTasks.length) {
    log("\n📌 里程任务最终状态");
    finalTasks.forEach((task, idx) => logMileageTask(task, idx));
  }
}

// ============================================================
// 打卡攒现金任务
// ============================================================
async function checkStatus(sectoken, openid) {
  try {
    const headers = {
      "Host": "wx.17u.cn",
      "Connection": "keep-alive",
      "TCxcxVersion": "7.9.0",
      "TCPrivacy": "1",
      "content-type": "application/json",
      "TCReferer": "page%2Fhome%2Fwebview%2Fwebview",
      "sectoken": sectoken,
      "apmat": genApmat(openid),
      "TCSecTk": sectoken,
      "Accept-Encoding": "gzip,compress,br,deflate",
      "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.59(0x18003b2e) NetType/WIFI Language/zh_CN",
    };
    const r = await post("/appapi/wxuser/checkstatus", {}, headers);
    if (r.data?.data?.check === true) ok("checkStatus 校验通过");
    return true;
  } catch (e) { return false; }
}

async function getClockinInfo(headers) {
  try {
    const r = await post("/wxmpsign/clockin/getClockinInfo", { "channel": "923f1661d1e24aa399f0e4e6ba0b0fba" }, headers);
    if (r.raw) {
      warn("getClockinInfo 非JSON:", String(r.data).substring(0, 150));
      return null;
    }
    if (r.data?.code === 0) return r.data.data;
    warn(`getClockinInfo: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) { return null; }
}

async function doClockin(headers) {
  try {
    const r = await post("/wxmpsign/clockin/clockin", { "channel": "923f1661d1e24aa399f0e4e6ba0b0fba" }, headers);
    if (r.raw) {
      warn("clockin 非JSON:", String(r.data).substring(0, 150));
      return null;
    }
    if (r.data?.code === 0) {
      ok("打卡攒现金 打卡成功！获得：" + (r.data.data?.amount || "0") + " 元");
      return r.data?.data;
    }
    warn(`clockin: ${r.data?.code ?? "无code"} ${r.data?.msg || ""}`.trim());
    return null;
  } catch (e) { return null; }
}

async function runCashClockin(sectoken, openId, userKey, cookieObj) {
  sep();
  log("=== 【新增】打卡攒现金任务 ===");

  await checkStatus(sectoken, openId);
  await sleep(600);

  const mallOsType = "IOS";
  const osType = 1;
  const cashHeaders = {
    "TC-MALL-PLATFORM-CODE": "WX_MP",
    "TC-MALL-OS-TYPE": mallOsType,
    "accountSystem": "1",
    "platform": "WX_MP",
    "osType": osType,
    "secToken": sectoken,
    "TC-MALL-USER-TOKEN": sectoken,
    ...h5Headers(sectoken, openId, userKey, cookieObj)
  };

  const infoData = await getClockinInfo(cashHeaders);
  if (!infoData) { error("获取打卡信息失败"); return; }

  info(`当前金币：${infoData.clockinAmount}`);
  info(`今日是否已打卡：${infoData.isTodayClocked ? "✅已打卡" : "⬜未打卡"}`);
  if (infoData.isRiskUser === true) {
    warn("打卡攒现金 命中风控，当前账号被活动接口限制，跳过执行打卡");
    return;
  }
  if (infoData.isDailyValidTime === false) {
    warn("打卡攒现金 当前不在可打卡时段，跳过执行");
    return;
  }

  if (infoData.isTodayClocked) { ok("今日已打卡"); return; }

  await sleep(800);
  const clockinResult = await doClockin(cashHeaders);

  await sleep(800);
  const newInfo = await getClockinInfo(cashHeaders);
  if (newInfo?.isTodayClocked) ok("✅ 打卡攒现金 最终确认成功！");
  else warn(`打卡攒现金 未确认成功，接口返回: ${shortJson(clockinResult || newInfo || {})}`);
}

// ============================================================
// 处理单个账号
// ============================================================
async function processAccount(cfg) {
  console.log("\n" + "=".repeat(50));
  log(`账号 ${cfg.index}: ${cfg.remark}`);
  console.log("=".repeat(50));

  log("初始化会话 Cookie...");
  const cookieObj = await initCookie(cfg.sectoken);
  const headers = h5Headers(cfg.sectoken, cfg.openId, cfg.userKey, cookieObj);

  await runCashSign(headers);
  await runMileageTasks(cfg.sectoken);
  await runCashClockin(cfg.sectoken, cfg.openId, cfg.userKey, cookieObj);
  return { success: true };
}

// ============================================================
// 主函数
// ============================================================
async function main() {
  console.log("=".repeat(50));
  console.log("   同程旅行 - 手动/自动签到版");
  console.log("   " + new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }));
  console.log("=".repeat(50));
  console.log();

  const accts = await parseAccounts();
  if (!accts.length) {
    error("未获取到可用账号");
    info("手动模式：TC_ACCOUNT=备注#sectoken#openId#userKey");
    info("自动模式：配置 WECHAT_SERVER/ADMIN_KEY（按 getCode.py 要求）");
    process.exit(1);
  }

  info(`共 ${accts.length} 个账号`);
  console.log();

  for (const acct of accts) {
    try { await processAccount(acct); }
    catch (e) { error("处理账号异常:", e.message); }
  }

  sep();
  ok("所有任务完成！");
  sep();
}

main().catch(e => {
  error("Fatal error:", e.message);
  process.exit(1);
});
