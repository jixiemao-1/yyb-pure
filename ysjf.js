/*
------------------------------------------
Author: anonymous
Date: 2026.08.15
Description: 影视飓风小程序签到（yyb呆呆 / Code 协议版）
Cron: 20 8 * * *
------------------------------------------
影视飓风小程序签到 v2.0.0（yyb 协议）

功能：自动执行影视飓风小程序签到，支持多账号执行。

配置说明：
1. 协议服务（账号 + 小程序 code）：
   - WECHAT_SERVER   必填，yyb呆呆地址（如 http://127.0.0.1:18273）
   - ADMIN_KEY       协议密钥（LICENSE_KEY / AUTH 也可）
   - WX_ID           可选。openid / 昵称 / 序号(1起) / all
                     多个用 & @ 换行；空或 all = 当前授权码下全部账号
   - WX_APPID        可选，默认 wx92782ef90ebc836d
   - 账号列表：./yybOpenid.loadTaskAccounts
   - 取 code：./yybOpenid.getYybCode

2. 兼容旧变量（不推荐）：
   - wx_server_url → 当作 WECHAT_SERVER
   - ysjf_openid   → 当作 WX_ID 筛选（仅当 WX_ID 未设时）

3. 依赖：
   - axios
   - 同目录 yybOpenid.js

4. 青龙任务建议：
   名称：影视飓风小程序签到
   命令：node ysjf.js
   定时：每天 1 次
------------------------------------------
*/

const axios = require("axios");
const fs = require("fs");
const path = require("path");
const {
  getAuth,
  loadTaskAccounts,
  getYybCode,
} = require("./yybOpenid");

// ==================== 配置 ====================
const WECHAT_SERVER = String(
  process.env.WECHAT_SERVER ||
    process.env.YYB_SERVER ||
    process.env.wx_server_url ||
    ""
)
  .trim()
  .replace(/\/+$/, "");
const ADMIN_KEY =
  process.env.ADMIN_KEY ||
  process.env.LICENSE_KEY ||
  process.env.AUTH ||
  process.env.wx_auth ||
  process.env.wx_code_token ||
  "";
const WX_ID_FILTER = String(
  process.env.WX_ID || process.env.wxid || process.env.ysjf_openid || ""
).trim();
const MINI_APP_ID = process.env.WX_APPID || "wx92782ef90ebc836d";

const CLIENT_ID = "4d65249d377b2c3ed8";
const CLIENT_SECRET = "1cdc05151d64f3a4a6ebd0e9de64422a";
const GRANT_TYPE = "yz_union";
const CLIENT_BIZ = "weapp_wsc";
const KDT_ID = "149536603";
const USER_VERSION = "2.226.7.101";
const PAGE_VERSION = "17";
const API_BASE = "https://h5.youzan.com";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) MicroMessenger/3.9.12 MiniProgramEnv/Windows WindowsWechat/WMPF";
const ENV_CHECK_FILE = path.join(__dirname, "env_check.json");

// ---- 运行环境 ----
class Env {
  constructor(name) {
    this.name = name;
    this.userIdx = 1;
    this.userList = [];
    this.startTime = Date.now();
    this.log(`============ ${name} ============`);
  }

  log(...args) {
    console.log(args.join(" "));
  }

  done() {
    const cost = ((Date.now() - this.startTime) / 1000).toFixed(2);
    this.log(`============ ${this.name} 执行结束，耗时 ${cost}s ============`);
  }
}

const $ = new Env("影视飓风小程序签到");

function ts() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function maskPhone(phone = "") {
  return String(phone).replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2");
}

function maskId(id = "") {
  const s = String(id || "");
  if (s.length < 10) return s || "未知";
  return `${s.slice(0, 6)}***${s.slice(-4)}`;
}

function pickToken(data = {}) {
  return data.accessToken || data.access_token || "";
}

function isTokenError(message) {
  return /access_token|token|登录|授权|invalid session|session/i.test(
    String(message || "")
  );
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function readJson(file, def = {}) {
  try {
    if (!fs.existsSync(file)) return def;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return def;
  }
}

function writeJson(file, data) {
  try {
    fs.writeFileSync(file, JSON.stringify(data, null, 2), "utf8");
  } catch {}
}

function getServerAuth() {
  return getAuth(ADMIN_KEY);
}

/** 仅信任 yyb呆呆；旧缓存 Niuzi 等会强制重检 */
async function detectProtocolType() {
  let envData = readJson(ENV_CHECK_FILE, {});
  if (envData.protocol_type === "yyb呆呆") {
    $.log(`[${ts()}] 从配置文件读取到协议服务类型: yyb呆呆`);
    return "yyb呆呆";
  }
  if (!WECHAT_SERVER) return "Unknown";
  let protocolType = "Unknown";
  const auth = getServerAuth();
  try {
    const r = await axios.get(`${WECHAT_SERVER}/api/accounts`, {
      timeout: 8000,
      validateStatus: () => true,
      headers: {
        "X-License-Key": auth,
        Authorization: `Bearer ${auth}`,
      },
    });
    if (
      r.status === 200 &&
      (Array.isArray(r.data?.accounts) || Array.isArray(r.data))
    ) {
      protocolType = "yyb呆呆";
    }
  } catch {}
  writeJson(ENV_CHECK_FILE, { protocol_type: protocolType });
  $.log(`[${ts()}] 当前使用的协议服务: ${protocolType}`);
  return protocolType;
}

async function loadAccounts() {
  if (!WECHAT_SERVER) {
    throw new Error("未配置 WECHAT_SERVER（或 wx_server_url）");
  }
  const auth = getServerAuth();
  if (!auth) {
    throw new Error("未配置 ADMIN_KEY / LICENSE_KEY / AUTH");
  }
  await detectProtocolType();
  const list = await loadTaskAccounts(WX_ID_FILTER || "all", WECHAT_SERVER, auth);
  const accounts = (list || [])
    .map((a) => ({
      openid: a.openid || a.wxid || "",
      wxid: a.wxid || a.openid || "",
      nickname: a.nickname || a.remark || "",
    }))
    .filter((a) => a.openid);
  if (!accounts.length) {
    throw new Error(
      "未获取到可用账号。请确认 yyb呆呆 已绑定微信，或检查 WX_ID / ysjf_openid 筛选"
    );
  }
  $.log(`[${ts()}] 共加载 ${accounts.length} 个账号`);
  return accounts;
}

class Task {
  constructor(account) {
    this.index = $.userIdx++;
    this.openid = String(account.openid || account.wxid || "").trim();
    this.nickname = String(account.nickname || "").trim();
    this.maskOpenid = maskId(this.openid);
    this.token = "";
    this.sessionId = "";
    this.cookie = "";
    this.kdtId = KDT_ID;
    this.userInfo = {};
    this.checkinId = "";
  }

  getLabel() {
    const nick =
      this.userInfo?.nick_name ||
      this.userInfo?.nickName ||
      this.nickname ||
      "";
    const mobile = maskPhone(this.userInfo?.mobile);
    const who = nick ? `${nick}${mobile ? ` ${mobile}` : ""}` : this.maskOpenid;
    return `👤 用户${this.index} [${who}]`;
  }

  async run() {
    try {
      await this.loginByWxCode();
      if (!this.token) return;

      const startPoints = await this.getPoints();
      if (startPoints !== undefined) {
        $.log(`${this.getLabel()} 💰 当前积分: 【${startPoints}】`);
      }

      await this.showCheckinPage();
      await this.doCheckin();

      const endPoints = await this.getPoints();
      if (startPoints !== undefined && endPoints !== undefined) {
        const delta = endPoints - startPoints;
        if (delta === 0) {
          $.log(`${this.getLabel()} 💰 积分: 【${endPoints}】`);
        } else {
          $.log(
            `${this.getLabel()} 💰 积分: 【${startPoints}】→【${endPoints}】 本次 ${delta >= 0 ? "+" : ""}${delta}积分`
          );
        }
      } else {
        $.log(`${this.getLabel()} ⚠️ 积分查询失败，无法汇总变化`);
      }
    } catch (e) {
      $.log(`${this.getLabel()} ❌ 执行失败: ${e.message || e}`);
    }
  }

  applyToken(data = {}) {
    this.token = pickToken(data);
    this.sessionId = data.sessionId || data.session_id || "";
    this.kdtId = String(data.kdtId || data.kdt_id || KDT_ID);
    this.cookie = data.cookie || "";
  }

  getHeaders(extra = {}) {
    const headers = {
      "User-Agent": USER_AGENT,
      Referer: `https://servicewechat.com/${MINI_APP_ID}/${PAGE_VERSION}/page-frame.html`,
      Accept: "*/*",
      "Extra-Data": JSON.stringify({
        sid: this.sessionId || "",
        version: USER_VERSION,
        clientType: "weapp-miniprogram",
        client: "weapp",
        bizEnv: "wsc",
      }),
      ...extra,
    };
    if (this.cookie) headers.Cookie = this.cookie;
    return headers;
  }

  getBaseParams(params = {}) {
    return {
      app_id: MINI_APP_ID,
      kdt_id: this.kdtId,
      access_token: this.token,
      ...params,
    };
  }

  async request({
    method = "GET",
    path: apiPath,
    params = {},
    data = {},
    skipToken = false,
  }) {
    const options = {
      method,
      url: `${API_BASE}${apiPath.startsWith("/") ? apiPath : `/${apiPath}`}`,
      headers: this.getHeaders(
        method === "POST" ? { "Content-Type": "application/json" } : {}
      ),
      timeout: 15000,
      validateStatus: () => true,
    };
    options.params = skipToken ? params : this.getBaseParams(params);
    if (method !== "GET") options.data = data;

    const { data: result, status, headers } = await axios.request(options);
    if (headers["set-cookie"]) {
      this.cookie = headers["set-cookie"]
        .map((item) => item.split(";")[0])
        .join("; ");
    }
    if (status !== 200) throw new Error(`HTTP ${status}: ${JSON.stringify(result)}`);
    if (!result || result.code !== 0) {
      throw new Error(result?.msg || JSON.stringify(result));
    }
    return result.data;
  }

  async getLoginCode(retries = 3) {
    const auth = getServerAuth();
    let lastErr = "";
    for (let i = 0; i < retries; i++) {
      try {
        const { code } = await getYybCode(
          WECHAT_SERVER,
          auth,
          MINI_APP_ID,
          this.openid
        );
        if (code) return String(code);
        lastErr = "code 为空";
      } catch (e) {
        lastErr = e.message || String(e);
      }
      if (i < retries - 1) {
        $.log(
          `${this.getLabel()} ⚠️ 取 code 失败(${i + 1}/${retries}): ${lastErr}，3s 后重试`
        );
        await sleep(3000);
      }
    }
    throw new Error(`yyb 取 code 失败: ${lastErr}`);
  }

  async authorize(code, data) {
    return this.request({
      method: "POST",
      path: "/wscshop/weapp/authorize.json",
      skipToken: true,
      data: { ...data, code },
    });
  }

  async loginByWxCode() {
    try {
      const code = await this.getLoginCode();
      let data;
      try {
        data = await this.authorize(code, {
          appId: MINI_APP_ID,
          clientId: CLIENT_ID,
          clientSecret: CLIENT_SECRET,
          grantType: GRANT_TYPE,
        });
      } catch (e) {
        data = await this.authorize(code, {
          appId: MINI_APP_ID,
          clientBiz: CLIENT_BIZ,
        });
      }
      this.applyToken(data);
      this.userInfo = data || {};
      if (!this.token) {
        $.log(`${this.getLabel()} ❌ 登录失败: 未拿到 accessToken`);
        return;
      }
      $.log(`${this.getLabel()} ✅ 登录成功`);
    } catch (e) {
      $.log(`${this.getLabel()} ❌ 登录失败: ${e.message || e}`);
    }
  }

  async showCheckinPage() {
    try {
      const data = await this.request({
        path: "/wscump/checkin/show_checkin_page_v2.json",
      });
      this.checkinId = data?.checkinId;
    } catch (e) {
      $.log(`${this.getLabel()} ⚠️ 获取签到活动失败: ${e.message || e}`);
      if (isTokenError(e.message || e)) {
        this.token = "";
        this.sessionId = "";
        this.cookie = "";
      }
    }
  }

  async doCheckin() {
    if (!this.checkinId) {
      $.log(`${this.getLabel()} ℹ️ 未获取到 checkinId，跳过签到`);
      return;
    }
    try {
      const data = await this.request({
        path: "/wscump/checkin/checkinV2.json",
        params: { checkinId: this.checkinId },
      });
      const awards = (data?.list || [])
        .map((item) => item?.infos?.title)
        .filter(Boolean)
        .join(", ");
      const desc = data?.desc ? ` ${data.desc}` : "";
      $.log(
        `${this.getLabel()} 📝 每日签到： 🎉 签到成功${desc}${awards ? ` 奖励:${awards}` : ""}`
      );
    } catch (e) {
      const message = String(e.message || e);
      if (/已达最大参与次数|已签到|重复签到/.test(message)) {
        $.log(`${this.getLabel()} 📅 每日签到： ⚠️ 今日已签到`);
        return;
      }
      $.log(`${this.getLabel()} ❌ 签到失败: ${message}`);
      if (isTokenError(message)) {
        this.token = "";
        this.sessionId = "";
        this.cookie = "";
      }
    }
  }

  async getPoints() {
    try {
      const data = await this.request({
        path: "/wscump/integral/user_points.json",
      });
      const points = data?.current_points ?? data?.real_points;
      return points;
    } catch (e) {
      $.log(`${this.getLabel()} ⚠️ 查询积分失败: ${e.message || e}`);
      return undefined;
    }
  }
}

!(async () => {
  if (!WECHAT_SERVER) {
    $.log("未配置 WECHAT_SERVER（可用 YYB_SERVER / wx_server_url）");
    return;
  }
  if (!getServerAuth()) {
    $.log("未配置 ADMIN_KEY / LICENSE_KEY / AUTH");
    return;
  }

  $.log(`[${ts()}] 协议服务: ${WECHAT_SERVER}`);
  $.log(`[${ts()}] 小程序 AppID: ${MINI_APP_ID}`);
  if (WX_ID_FILTER) $.log(`[${ts()}] 账号筛选: ${WX_ID_FILTER}`);
  else $.log(`[${ts()}] 账号筛选: all（当前授权码下全部）`);

  const accounts = await loadAccounts();
  for (const acc of accounts) {
    await new Task(acc).run();
  }
})()
  .catch((e) => $.log(e.message || e))
  .finally(() => $.done());
