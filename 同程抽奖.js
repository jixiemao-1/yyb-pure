/**
 * 同程旅行 · 打卡任务 + 积分抽奖（getCode.py 自动登录）
 * 适用于青龙面板 (Node.js)
 *
 * 活动入口: #小程序://同程旅行/hdzLEBSUiDmYoZH
 * 说明:
 *   抽奖接口 idenId 不是小程序 openId，而是 h5 openid
 *   获取链路（与 同程旅行.js 相同调用方式，不改 getCode.py）:
 *     python -c importlib 加载 getCode.py → get_wechat_codes(appid)
 *     → GET https://wx.17u.cn/flight/getopenid.html?url=...&code=...&state=123
 *     → 302 Location/cookie 里的 openid 即 idenId
 *
 * 环境变量（优先小写，兼容大写）:
 *   WECHAT_SERVER / ADMIN_KEY / WX_ID  交给 getCode.py
 *   GETCODE_PATH / getcode_path       getCode.py 路径，默认同目录
 *   GETCODE_PYTHON / getcode_python   python 解释器，默认 python3/python
 *   wx_nick / tongcheng_nick          可选；按昵称筛选账号，多个 ,&|
 *   tongcheng_lottery_notify          默认 1；0 关闭推送
 *   tongcheng_lottery_cookie_file     idenId 缓存，默认 tc_lottery_cookie.json
 *   tc_lottery / TC_LOTTERY           是否抽奖，默认 1
 *   tc_lottery_max / TC_LOTTERY_MAX   单账号最多抽几次，默认 0=不限制
 *   tc_nick / TC_NICK                 活动昵称，默认 同程用户
 *   tc_icon / TC_ICON                 可选
 *   qywx_am / QYWX_AM                 仅 sendNotify 不可用时作兜底
 *
 * 依赖: axios + 同目录 getCode.py（需 WECHAT_SERVER）
 * cron 建议: 10 9 * * *
 */

"use strict";

const axios = require("axios");
const dns = require("dns");
const https = require("https");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
// 注意：不要 const { URL } = require("url")，会覆盖全局 URL，导致 axios/undici 报 URL is not defined

try {
  dns.setDefaultResultOrder("ipv4first");
} catch (_) {}

const NAME = "同程旅行抽奖";
const TZ = "Asia/Shanghai";

// 抽奖页 h5 公众号 appid（不是小程序 wx336dcaf6a1ecf632）
const H5_APPID = "wx3827070276e49e30";
const ACTIVITY_URL =
  "https://wx.17u.cn/cvgzt/20250718signin/index?refid=1000";

// 与 同程旅行.js 一致：不改 getCode.py，只 import 调用 get_wechat_codes
const GETCODE_PATH = (
  process.env.GETCODE_PATH ||
  process.env.getcode_path ||
  process.env.getcode_py ||
  process.env.GETCODE_PY ||
  path.join(__dirname, "getCode.py")
).trim();

const GETCODE_PYTHON = (
  process.env.GETCODE_PYTHON ||
  process.env.getcode_python ||
  process.env.PYTHON ||
  (process.platform === "win32" ? "python" : "python3")
).trim();

const NICK_FILTER = (
  process.env.wx_nick ||
  process.env.tongcheng_nick ||
  process.env.WX_NICK ||
  process.env.TONGCHENG_NICK ||
  ""
).trim();

const Notify = Number(
  process.env.tongcheng_lottery_notify ??
    process.env.tongcheng_notify ??
    process.env.Notify ??
    1
);

const CACHE_FILE = (
  process.env.tongcheng_lottery_cookie_file ||
  process.env.TONGCHENG_LOTTERY_COOKIE_FILE ||
  "tc_lottery_cookie.json"
).trim() || "tc_lottery_cookie.json";

const CONFIG = {
  host: "cvg.17usoft.com",
  protocol: "https",
  pid: 501,
  refId: "1000",
  headers: {
    "User-Agent":
      "Mozilla/5.0 (Linux; Android 16; PJZ110) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/121.0.0.0 Mobile Safari/537.36 XWEB/1210117 MMWEBSDK/20240404 MMWEBID/5830 MicroMessenger/8.0.49.2600(0x28003137) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android",
    "Content-Type": "application/json",
    Origin: "https://wx.17u.cn",
    Referer: "https://wx.17u.cn/",
    Accept: "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
  },
  timeout: 15000,
};

const httpsAgent = new https.Agent({ keepAlive: true, family: 4 });

// ---------- utils ----------
function envGet(...keys) {
  for (const k of keys) {
    const v = process.env[k];
    if (v !== undefined && String(v).trim() !== "") return String(v).trim();
  }
  return "";
}

function envBool(val, defaultTrue = true) {
  if (val === undefined || val === null || String(val).trim() === "") return defaultTrue;
  return !["0", "false", "off", "no", "n"].includes(String(val).trim().toLowerCase());
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function rand(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function maskId(id) {
  const s = String(id || "");
  if (s.length <= 10) return s.slice(0, 2) + "***";
  return `${s.slice(0, 6)}...${s.slice(-4)}`;
}

function nowStr() {
  return new Date().toLocaleString("zh-CN", { timeZone: TZ, hour12: false });
}

function short(value, max = 220) {
  if (value === undefined || value === null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? text.slice(0, max) + "..." : text;
}

function parseNickFilter(raw) {
  return String(raw || "")
    .split(/[\n&,;|]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// 兼容旧名，防止青龙半同步文件还在调 parseOpenidFilter
const parseOpenidFilter = parseNickFilter;

function pickNick(account, index) {
  // 兼容旧调用 pickNick(account, openid, index)
  if (arguments.length >= 3) index = arguments[2];
  return (
    account.nickname ||
    account.nick ||
    account.name ||
    account.remark ||
    (account.code ? String(account.code).slice(0, 8) : "") ||
    `账号${index}`
  );
}

// ---------- cache ----------
function cachePath() {
  return path.isAbsolute(CACHE_FILE) ? CACHE_FILE : path.join(__dirname, CACHE_FILE);
}

function loadCache() {
  try {
    const p = cachePath();
    if (!fs.existsSync(p)) return {};
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    if (data && typeof data === "object" && !Array.isArray(data)) return data;
  } catch (e) {
    console.log(`[cache] 读取失败: ${e.message || e}`);
  }
  return {};
}

function saveCache(cache) {
  try {
    const p = cachePath();
    fs.writeFileSync(p, JSON.stringify(cache || {}, null, 2), "utf8");
    console.log(`[cache] 已写入 ${p} · ${Object.keys(cache || {}).length} 条`);
  } catch (e) {
    console.log(`[cache] 写入失败: ${e.message || e}`);
  }
}

// ---------- notify ----------
let sendNotifyFn = null;
try {
  sendNotifyFn = require("./sendNotify").sendNotify;
} catch (_) {
  try {
    sendNotifyFn = require("../sendNotify").sendNotify;
  } catch (__) {
    sendNotifyFn = null;
  }
}

async function sendQywxAm(title, content) {
  const am = envGet("qywx_am", "QYWX_AM");
  if (!am) return false;
  const parts = am.split(",").map((x) => x.trim());
  if (parts.length < 4) {
    console.log("⚠️ qywx_am 格式错误，需要 corpid,corpsecret,touser,agentid");
    return false;
  }
  const [corpid, corpsecret, touser, agentid] = parts;
  try {
    const tokenRes = await axios.get(
      `https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=${encodeURIComponent(
        corpid
      )}&corpsecret=${encodeURIComponent(corpsecret)}`,
      { timeout: 10000, httpsAgent }
    );
    const token = tokenRes.data && tokenRes.data.access_token;
    if (!token) {
      console.log("⚠️ 企微 token 获取失败:", tokenRes.data);
      return false;
    }
    const pushRes = await axios.post(
      `https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=${token}`,
      {
        touser,
        msgtype: "text",
        agentid: Number(agentid),
        text: { content: `${title}\n${content}` },
        safe: 0,
      },
      { timeout: 10000, httpsAgent }
    );
    if (pushRes.data && pushRes.data.errcode === 0) return true;
    console.log("⚠️ 企微推送失败:", pushRes.data);
    return false;
  } catch (e) {
    console.log("⚠️ 企微推送异常:", e.message);
    return false;
  }
}

async function notify(title, content) {
  if (!(Notify > 0)) {
    console.log("ℹ️ 推送关闭");
    return;
  }
  // 优先只走青龙 sendNotify（config.sh 里已有 QYWX_AM）。
  // 以前 sendNotify 成功后还会再 sendQywxAm，导致企微同一条推两次。
  if (sendNotifyFn) {
    try {
      await sendNotifyFn(title, content);
      console.log("✅ 推送已发送 · sendNotify");
      return;
    } catch (e) {
      console.log("⚠️ sendNotify 失败:", e.message);
    }
  }
  const qy = await sendQywxAm(title, content);
  if (qy) {
    console.log("✅ 推送已发送 · qywx_am 兜底");
    return;
  }
  console.log("ℹ️ 未配置推送或推送失败，仅控制台输出");
}

// ---------- http ----------
async function activityRequest(method, apiPath, data) {
  try {
    const res = await axios({
      method,
      url: `${CONFIG.protocol}://${CONFIG.host}${apiPath}`,
      headers: CONFIG.headers,
      data,
      timeout: CONFIG.timeout,
      validateStatus: () => true,
      httpsAgent,
    });
    return res.data;
  } catch (e) {
    console.log(`请求网络错误 [${method} ${apiPath}]: ${e.message}`);
    return null;
  }
}

function basePayload(idenId, nick, icon, extra = {}) {
  return {
    idenId,
    pid: CONFIG.pid,
    refId: CONFIG.refId,
    nick,
    icon,
    ...extra,
  };
}

// ---------- getCode.py（与 同程旅行.js 相同：importlib + 标记 JSON，不改 getCode.py）----------
function extractMarkedJson(stdout) {
  const text = String(stdout || "");
  const begin = "GETCODE_JSON_BEGIN";
  const end = "GETCODE_JSON_END";
  const i = text.indexOf(begin);
  const j = text.indexOf(end);
  if (i < 0 || j < 0 || j <= i) return null;
  const raw = text.slice(i + begin.length, j).trim();
  try {
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
}

/**
 * 调用 getCode.get_wechat_codes(appid)
 * 返回 [{ nick, code, name }]
 */
function getAccountsWithCode(appid) {
  if (!fs.existsSync(GETCODE_PATH)) {
    throw new Error(`找不到 getCode.py: ${GETCODE_PATH}`);
  }

  const py = `
import importlib.util, json, sys
path = sys.argv[1]
appid = sys.argv[2]
spec = importlib.util.spec_from_file_location("getCode", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
codes = mod.get_wechat_codes(appid) or {}
print("GETCODE_JSON_BEGIN")
print(json.dumps(codes, ensure_ascii=False))
print("GETCODE_JSON_END")
`.trim();

  const res = spawnSync(GETCODE_PYTHON, ["-c", py, GETCODE_PATH, String(appid)], {
    encoding: "utf8",
    env: process.env,
    timeout: 120000,
    maxBuffer: 8 * 1024 * 1024,
    windowsHide: true,
  });

  if (res.error) {
    throw new Error(`运行 getCode.py 失败: ${res.error.message}`);
  }

  const stdout = String(res.stdout || "");
  const stderr = String(res.stderr || "").trim();
  if (stdout) {
    // getCode 内部 print 的在线状态等也透传
    process.stdout.write(stdout.endsWith("\n") ? stdout : stdout + "\n");
  }
  if (stderr) {
    process.stderr.write(stderr.endsWith("\n") ? stderr : stderr + "\n");
  }

  const codes = extractMarkedJson(stdout);
  if (!codes || typeof codes !== "object") {
    const hint = stderr || short(stdout) || `exit=${res.status}`;
    throw new Error(`getCode.py 未返回有效 JSON: ${hint}`);
  }

  let list = Object.entries(codes).map(([nick, code]) => ({
    nick,
    name: nick,
    nickname: nick,
    code: String(code || ""),
  }));
  list = list.filter((a) => a.code);
  if (!list.length) {
    throw new Error("getCode.py 未返回任何有效 code（检查 WECHAT_SERVER 与在线号）");
  }

  // 内联筛选，避免依赖函数名不同步
  const filter = String(NICK_FILTER || "")
    .split(/[\n&,;|]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (filter.length) {
    const set = new Set(filter);
    list = list.filter((a) => set.has(String(a.nick || a.name || "")));
    if (!list.length) {
      throw new Error(`wx_nick 过滤后无匹配账号: ${filter.join(",")}`);
    }
    console.log(`按 wx_nick 过滤后 ${list.length} 个账号`);
  } else {
    console.log(`getCode 返回 ${list.length} 个账号 code`);
  }
  return list;
}

function extractIdenFromRedirect(loc, setCookies) {
  let idenId = "";
  let token = "";
  if (loc) {
    try {
      const u = new URL(loc, "https://wx.17u.cn");
      idenId = u.searchParams.get("code") || "";
      token = u.searchParams.get("token") || "";
    } catch (_) {}
  }
  const cookies = Array.isArray(setCookies) ? setCookies : setCookies ? [setCookies] : [];
  for (const c of cookies) {
    const s = String(c || "");
    if (!idenId) {
      const m = s.match(/(?:^|;\s*|,?\s*)(?:WxUser|cookieOpenSource|CooperateWxUser)=[^;]*openid=([^&;]+)/i)
        || s.match(/openid=([^&;]+)/i);
      if (m) idenId = decodeURIComponent(m[1]);
    }
    if (!token) {
      const m = s.match(/(?:^|[;&])token=([^&;]+)/i);
      if (m) token = decodeURIComponent(m[1]);
    }
  }
  return { idenId, token };
}

async function exchangeIdenId(code) {
  const url =
    "https://wx.17u.cn/flight/getopenid.html?url=" +
    encodeURIComponent(ACTIVITY_URL) +
    `&code=${encodeURIComponent(code)}&state=123`;

  try {
    const res = await axios({
      method: "GET",
      url,
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Linux; Android 16; PJZ110) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/121.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.71",
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      },
      timeout: 20000,
      maxRedirects: 0,
      validateStatus: (s) => s >= 200 && s < 400,
      httpsAgent,
    });

    const loc = res.headers.location || res.headers.Location || "";
    const setCookie = res.headers["set-cookie"] || res.headers["Set-Cookie"] || [];
    let body = "";
    if (typeof res.data === "string") body = res.data;
    if (!loc && body) {
      const m = body.match(/href=["']([^"']+)["']/i);
      if (m) {
        const href = m[1].replace(/&/g, "&");
        return extractIdenFromRedirect(href, setCookie);
      }
    }
    return extractIdenFromRedirect(loc, setCookie);
  } catch (e) {
    // axios 对 302 有时仍抛错（取决于版本/配置）
    if (e.response) {
      const loc = e.response.headers.location || e.response.headers.Location || "";
      const setCookie =
        e.response.headers["set-cookie"] || e.response.headers["Set-Cookie"] || [];
      let body = e.response.data;
      if (typeof body !== "string") body = "";
      if (!loc && body) {
        const m = body.match(/href=["']([^"']+)["']/i);
        if (m) {
          const href = m[1].replace(/&/g, "&");
          return extractIdenFromRedirect(href, setCookie);
        }
      }
      return extractIdenFromRedirect(loc, setCookie);
    }
    throw e;
  }
}

async function probeIdenId(idenId, nick, icon) {
  const res = await activityRequest(
    "POST",
    "/activity/checkin/getIndexInfo",
    basePayload(idenId, nick, icon)
  );
  return !!(res && res.code === 1000);
}

async function resolveIdenId(account, index, nick, icon, cache) {
  const accountNick = String(account.nick || account.nickname || account.name || "").trim();
  const key = accountNick || `idx_${index}`;
  const hit = cache[key];

  if (hit && hit.idenId) {
    const ok = await probeIdenId(hit.idenId, nick, icon);
    if (ok) {
      console.log(`   使用缓存 idenId ${maskId(hit.idenId)}`);
      return {
        idenId: hit.idenId,
        token: hit.token || "",
        fromCache: true,
        key,
      };
    }
    console.log(`   缓存 idenId 失效，重新登录`);
  }

  const code = String(account.code || "").trim();
  if (!code) {
    throw new Error("账号缺少 code（getCode.py 未返回）");
  }
  console.log(`   使用 getCode.py code ${String(code).slice(0, 8)}****`);

  const exchanged = await exchangeIdenId(code);
  if (!exchanged.idenId || exchanged.idenId === "0") {
    throw new Error(`getopenid 未返回 idenId: ${short(exchanged)}`);
  }

  const ok = await probeIdenId(exchanged.idenId, nick, icon);
  if (!ok) {
    throw new Error(`idenId 探活失败 ${maskId(exchanged.idenId)}`);
  }

  cache[key] = {
    ref: key,
    nick: accountNick,
    idenId: exchanged.idenId,
    token: exchanged.token || "",
    h5_appid: H5_APPID,
    source: "getCode.py",
    updated_at: new Date().toISOString().replace("T", " ").slice(0, 19),
  };
  globalThis.__tc_lottery_dirty = true;

  return {
    idenId: exchanged.idenId,
    token: exchanged.token || "",
    fromCache: false,
    key,
  };
}

// ---------- activity api ----------
async function getTaskList(idenId, nick, icon) {
  const res = await activityRequest(
    "POST",
    "/activity/checkin/getClockinTaskInfo",
    basePayload(idenId, nick, icon)
  );
  if (res && res.code === 1000) return res.data.taskList || [];
  if (res) console.log(`   任务列表失败: ${res.message || res.code}`);
  return [];
}

async function completeTaskAction(idenId, nick, icon, taskType, rewardPoints) {
  await sleep(rand(800, 1800));
  const res = await activityRequest(
    "POST",
    "/activity/checkin/completeClockinTask",
    basePayload(idenId, nick, icon, { taskType, rewardPoints })
  );
  if (res && res.code === 1000 && res.data && res.data.taskId) return res.data.taskId;
  if (res) console.log(`   提交任务失败: ${res.message || res.code}`);
  return null;
}

async function collectReward(idenId, nick, icon, completeTaskId) {
  if (!completeTaskId) return false;
  await sleep(rand(500, 1200));
  const res = await activityRequest(
    "POST",
    "/activity/checkin/collectClockinTaskRewardPoints",
    basePayload(idenId, nick, icon, { completeTaskId })
  );
  return !!(res && res.code === 1000);
}

async function getLotteryInfo(idenId, nick, icon) {
  const res = await activityRequest(
    "POST",
    "/activity/checkin/getAttendLotteryInfo",
    basePayload(idenId, nick, icon)
  );
  if (res && res.code === 1000) {
    return {
      qsPlayId: res.data.qsPlayId,
      lotteryCostPoints: res.data.lotteryCostPoints,
      prizeList:
        (res.data.lotteryPrizeInfo &&
          res.data.lotteryPrizeInfo.playList &&
          res.data.lotteryPrizeInfo.playList[0] &&
          res.data.lotteryPrizeInfo.playList[0].prizeList) ||
        [],
    };
  }
  if (res) console.log(`   抽奖信息失败: ${res.message || res.code}`);
  return null;
}

async function getIndexInfo(idenId, nick, icon) {
  const res = await activityRequest(
    "POST",
    "/activity/checkin/getIndexInfo",
    basePayload(idenId, nick, icon)
  );
  if (res && res.code === 1000) return res.data;
  if (res) console.log(`   首页信息失败: ${res.message || res.code}`);
  return null;
}

async function performLotteryOnce(idenId, nick, icon, qsPlayId) {
  return activityRequest(
    "POST",
    "/activity/checkin/performLottery",
    basePayload(idenId, nick, icon, { qsPlayId })
  );
}

// ---------- account flow ----------
async function processAccount(account, index, total, opts, cache) {
  const name = pickNick(account, index);
  const nick = opts.nick;
  const icon = opts.icon;
  const lines = [];
  const result = {
    name,
    idenId: "",
    taskOk: 0,
    taskSkip: 0,
    taskFail: 0,
    lottery: [],
    points: null,
    error: "",
  };

  console.log(`\n========== [${index}/${total}] ${name} ==========`);
  lines.push(`【${name}】`);

  try {
    console.log(">>> [0/3] 登录换 idenId");
    const login = await resolveIdenId(account, index, nick, icon, cache);
    const idenId = login.idenId;
    result.idenId = maskId(idenId);
    lines[0] = `【${name}】${maskId(idenId)}${login.fromCache ? "(缓存)" : ""}`;
    console.log(`   idenId=${maskId(idenId)} ${login.fromCache ? "(cache)" : "(fresh)"}`);

    // 1. tasks
    console.log(">>> [1/3] 每日任务");
    const tasks = await getTaskList(idenId, nick, icon);
    if (!tasks.length) {
      console.log("   未获取到任务列表");
      lines.push("任务: 无列表");
    } else {
      for (const task of tasks) {
        const {
          type,
          title,
          couldComplete,
          rewardPoints,
          completeTimesToday,
          maxCompleteTimesPerDay,
        } = task;

        if (completeTimesToday >= maxCompleteTimesPerDay) {
          console.log(`   跳过 ${title} (今日已完成)`);
          result.taskSkip++;
          continue;
        }
        if (!couldComplete) {
          console.log(`   不可用 ${title}`);
          continue;
        }

        console.log(`   执行 ${title} (+${rewardPoints})`);
        const taskId = await completeTaskAction(
          idenId,
          nick,
          icon,
          type,
          rewardPoints
        );
        if (!taskId) {
          result.taskFail++;
          continue;
        }
        const ok = await collectReward(idenId, nick, icon, taskId);
        if (ok) {
          console.log(`   成功 ${title}`);
          result.taskOk++;
        } else {
          console.log(`   领奖失败 ${title}`);
          result.taskFail++;
        }
      }
      lines.push(
        `任务: 成功${result.taskOk}/跳过${result.taskSkip}/失败${result.taskFail}`
      );
      console.log(
        `✓ 任务统计: 成功 ${result.taskOk}, 跳过 ${result.taskSkip}, 失败 ${result.taskFail}`
      );
    }

    // 2. lottery prep
    console.log(">>> [2/3] 抽奖信息");
    let lotteryInfo = null;
    if (opts.doLottery) {
      lotteryInfo = await getLotteryInfo(idenId, nick, icon);
      if (!lotteryInfo || !lotteryInfo.qsPlayId) {
        console.log("   未拿到 qsPlayId");
        lines.push("抽奖: 无令牌");
      }
    } else {
      console.log("   已关闭抽奖 (tc_lottery=0)");
      lines.push("抽奖: 已关闭");
    }

    // 3. lottery
    console.log(">>> [3/3] 执行抽奖");
    if (opts.doLottery && lotteryInfo && lotteryInfo.qsPlayId) {
      let indexInfo = await getIndexInfo(idenId, nick, icon);
      if (!indexInfo) {
        console.log("   无法获取积分，跳过抽奖");
        lines.push("抽奖: 积分查询失败");
      } else {
        let points = Number(indexInfo.points || 0);
        const cost = Number(
          indexInfo.lotteryCostPoints || lotteryInfo.lotteryCostPoints || 100
        );
        console.log(`   当前积分 ${points}, 单次 ${cost}`);

        let count = 0;
        while (points >= cost) {
          if (opts.lotteryMax > 0 && count >= opts.lotteryMax) {
            console.log(`   已达上限 ${opts.lotteryMax} 次`);
            break;
          }
          await sleep(rand(1000, 2500));
          const res = await performLotteryOnce(
            idenId,
            nick,
            icon,
            lotteryInfo.qsPlayId
          );
          if (res && res.code === 1000 && res.data && res.data.prizeId) {
            count++;
            const prizeId = res.data.prizeId;
            const prize = (lotteryInfo.prizeList || []).find(
              (p) => p.prizeId === prizeId
            );
            const prizeName = prize
              ? prize.prizeTitle
              : `未知奖品(${prizeId})`;
            console.log(`   第${count}次: ${prizeName}`);
            result.lottery.push(prizeName);

            const fresh = await getIndexInfo(idenId, nick, icon);
            if (fresh) {
              points = Number(fresh.points || 0);
            } else {
              console.log("   积分刷新失败，停止抽奖");
              break;
            }
          } else {
            const errMsg = (res && (res.message || res.msg)) || "无响应";
            console.log(`   抽奖失败: ${errMsg}`);
            if (String(errMsg).includes("好友")) {
              console.log("   可能需要小程序内加好友/关注后再试");
              lines.push("抽奖: 需加好友");
            }
            break;
          }
        }

        if (count > 0) {
          lines.push(`抽奖: ${count}次 → ${result.lottery.join(" / ")}`);
          console.log(`★ 共抽 ${count} 次`);
        } else if (!lines.some((x) => x.includes("抽奖:"))) {
          if (points < cost) {
            lines.push(`抽奖: 积分不足(${points}<${cost})`);
          } else {
            lines.push("抽奖: 0次");
          }
        }
      }
    }

    const finalInfo = await getIndexInfo(idenId, nick, icon);
    if (finalInfo) {
      result.points = finalInfo.points;
      console.log(`★ 最终积分: ${finalInfo.points}`);
      lines.push(`积分: ${finalInfo.points}`);
    }
  } catch (e) {
    result.error = e.message || String(e);
    console.log(`💥 账户异常: ${result.error}`);
    lines.push(`异常: ${result.error}`);
  }

  return { result, lines };
}

// ---------- main ----------
async function main() {
  console.log(`================ ${NAME}(getCode.py) 启动 ${nowStr()} ================`);
  console.log(`getCode: ${GETCODE_PATH}`);
  console.log(`python: ${GETCODE_PYTHON}`);
  console.log(`h5 appid: ${H5_APPID}`);
  if (!process.env.WECHAT_SERVER && !process.env.wechat_server) {
    console.log("⚠️ 未检测到 WECHAT_SERVER，getCode.py 可能会失败");
  }

  const opts = {
    nick: envGet("tc_nick", "TC_NICK") || "同程用户",
    icon:
      envGet("tc_icon", "TC_ICON") ||
      "https://file.40017.cn/huochepiao/activity/20200521supplies/img/defaultImg-fs8.png",
    doLottery: envBool(envGet("tc_lottery", "TC_LOTTERY"), true),
    lotteryMax: Number(envGet("tc_lottery_max", "TC_LOTTERY_MAX") || 0) || 0,
  };

  console.log(">>> importlib 调用 get_wechat_codes 获取 h5 code...");
  const accounts = getAccountsWithCode(H5_APPID);
  if (!accounts.length) {
    throw new Error("未获取到有效账号");
  }

  console.log(
    `账号数: ${accounts.length} | 抽奖: ${opts.doLottery ? "开" : "关"}${
      opts.lotteryMax > 0 ? ` | 上限: ${opts.lotteryMax}` : ""
    }`
  );

  const cache = loadCache();
  globalThis.__tc_lottery_dirty = false;

  const allLines = [];
  let okAccounts = 0;
  let failCount = 0;

  for (let i = 0; i < accounts.length; i++) {
    const { result, lines } = await processAccount(
      accounts[i],
      i + 1,
      accounts.length,
      opts,
      cache
    );
    allLines.push(lines.join("\n"));
    if (result.error) failCount++;
    else okAccounts++;

    if (i < accounts.length - 1) {
      const wait = rand(3000, 7000);
      console.log(`\n等待 ${Math.round(wait / 1000)}s 后处理下一账号...`);
      await sleep(wait);
    }
  }

  if (globalThis.__tc_lottery_dirty) {
    saveCache(cache);
  }

  const summary = [
    `${NAME} ${nowStr()}`,
    `完成 ${okAccounts}/${accounts.length}` + (failCount ? ` · 失败 ${failCount}` : ""),
    "",
    ...allLines,
  ]
    .join("\n\n")
    .trim()
    .slice(0, 1500);

  console.log("\n================ 汇总 ================");
  console.log(summary);

  let title = "🎯 同程抽奖";
  if (failCount > 0 && okAccounts > 0) title += "（部分失败）";
  else if (failCount > 0) title += "（失败）";
  await notify(title, summary);
  console.log("\n================ 全部结束 ================");
}

main().catch(async (err) => {
  console.error("💥 主进程出错:", err);
  try {
    await notify("🎯 同程抽奖（失败）", `脚本异常: ${err.message || err}`);
  } catch (_) {}
  process.exitCode = 1;
});
