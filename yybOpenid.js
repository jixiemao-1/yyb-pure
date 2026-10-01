/**
 * yyb呆呆 直连取码公共层（无第三方依赖）
 * - 账号环境变量可空：空/all/* = 当前授权码下全部已绑定账号
 * - 标识支持：openid / 尾缀 / 控制台昵称 / 序号(1起)
 * - 仅需 WECHAT_SERVER + LICENSE_KEY(AUTH/ADMIN_KEY)
 */
const http = require("http");
const https = require("https");
const { URL } = require("url");

function getAuth(extra) {
  return String(
    extra ||
      process.env.LICENSE_KEY ||
      process.env.AUTH ||
      process.env.ADMIN_KEY ||
      process.env.wx_code_token ||
      ""
  ).trim();
}

function authHeaders(auth) {
  const a = getAuth(auth);
  const h = { "Content-Type": "application/json", Accept: "application/json" };
  if (a) {
    h["X-License-Key"] = a;
    h.Authorization = `Bearer ${a}`;
  }
  return h;
}

function requestJson(method, urlStr, { headers = {}, body = null, timeout = 20000 } = {}) {
  return new Promise((resolve, reject) => {
    let u;
    try {
      u = new URL(urlStr);
    } catch (e) {
      reject(new Error(`无效 URL: ${urlStr}`));
      return;
    }
    const lib = u.protocol === "https:" ? https : http;
    const data = body == null ? null : Buffer.from(JSON.stringify(body), "utf8");
    const req = lib.request(
      {
        protocol: u.protocol,
        hostname: u.hostname,
        port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname + u.search,
        method,
        headers: {
          ...headers,
          ...(data ? { "Content-Length": String(data.length) } : {}),
        },
        timeout,
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          let json = null;
          try {
            json = text ? JSON.parse(text) : {};
          } catch {
            json = { raw: text };
          }
          resolve({ status: res.statusCode || 0, data: json, text });
        });
      }
    );
    req.on("timeout", () => {
      req.destroy(new Error(`请求超时 ${timeout}ms: ${urlStr}`));
    });
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

function accountId(a) {
  return String(
    (a && (a.openid || a.wxid || a.wx_id || a.deviceId || a.account)) || ""
  ).trim();
}

function nickOf(a) {
  const n = String((a && (a.nickname || a.nick_name || a.deviceName)) || "").trim();
  if (n && n !== "ㅤ") return n;
  const id = accountId(a);
  return id ? `账号_${id.slice(-6)}` : "未知昵称";
}

function candidates(a) {
  const vals = [];
  for (const k of [
    "openid",
    "wxid",
    "wx_id",
    "deviceId",
    "unionid",
    "account",
    "nickname",
    "nick_name",
    "deviceName",
  ]) {
    const s = String((a && a[k]) || "").trim();
    if (s && !vals.includes(s)) vals.push(s);
  }
  return vals;
}

function matchScore(needle, account) {
  const n = String(needle || "").trim();
  if (!n) return 0;
  const nLow = n.toLowerCase();
  let best = 0;
  for (const cand of candidates(account)) {
    const cLow = cand.toLowerCase();
    if (cLow === nLow) return 100;
    if (n.length >= 6 && (cLow.endsWith(nLow) || nLow.endsWith(cLow))) best = Math.max(best, 80);
    else if (n.length >= 4 && (cLow.startsWith(nLow) || nLow.startsWith(cLow))) best = Math.max(best, 60);
    else if (
      n.length >= 2 &&
      (nLow.includes(cLow) || cLow.includes(nLow)) &&
      (["nickname", "nick_name", "deviceName"].some((k) => String(account[k] || "") === cand) ||
        n.length >= 8)
    ) {
      best = Math.max(best, 40);
    }
  }
  return best;
}

function formatBound(accounts, limit = 20) {
  if (!accounts || !accounts.length) return "(当前授权码下无绑定账号，请先在 yyb呆呆 扫码绑定)";
  return accounts
    .slice(0, limit)
    .map((a, i) => {
      const oid = accountId(a);
      const tail = oid.length > 8 ? oid.slice(-8) : oid;
      return `  [${i + 1}] ${nickOf(a)} openid=...${tail}`;
    })
    .join("\n");
}

async function fetchAccounts(server, auth) {
  const base = String(server || process.env.WECHAT_SERVER || process.env.YYB_SERVER || "").replace(
    /\/+$/,
    ""
  );
  if (!base) throw new Error("WECHAT_SERVER / YYB_SERVER 未配置");
  const a = getAuth(auth);
  if (!a) throw new Error("LICENSE_KEY / AUTH / ADMIN_KEY 未配置");
  const { data, status } = await requestJson("GET", `${base}/api/accounts`, {
    headers: authHeaders(a),
    timeout: 20000,
  });
  if (status === 401) throw new Error("授权码无效或未登录（401）");
  if (status === 403) throw new Error("授权码无效/禁用/过期（403）");
  if (status >= 400) throw new Error(`获取账号列表失败 HTTP ${status}: ${JSON.stringify(data)}`);
  const list = (data && data.accounts) || (Array.isArray(data) ? data : null);
  if (!Array.isArray(list)) throw new Error(`账号列表响应格式错误: ${JSON.stringify(data)}`);
  return list
    .filter((x) => x && typeof x === "object" && (x.status || "active") !== "error")
    .map((x) => {
      const oid = accountId(x);
      return {
        ...x,
        openid: oid,
        wxid: oid,
        wx_id: oid,
        deviceId: oid,
        nickname: nickOf({ ...x, openid: oid }),
      };
    })
    .filter((x) => x.openid);
}

function matchAccount(identifier, accounts) {
  const needle = String(identifier || "").trim();
  if (!needle) throw new Error("openid/账号标识为空");
  if (/^\d+$/.test(needle)) {
    const idx = parseInt(needle, 10);
    if (idx >= 1 && idx <= (accounts || []).length) return accounts[idx - 1];
    throw new Error(
      `序号超出范围: ${needle}（当前共 ${(accounts || []).length} 个已绑定账号）\n当前已绑定账号:\n${formatBound(accounts)}`
    );
  }
  const scored = [];
  for (const a of accounts || []) {
    const s = matchScore(needle, a);
    if (s > 0) scored.push([s, a]);
  }
  if (!scored.length) {
    throw new Error(
      `账号标识未绑定到当前授权码: ${needle}\n支持：openid / openid尾缀 / 控制台昵称 / 序号(1起) / all\n当前已绑定账号:\n${formatBound(accounts)}`
    );
  }
  scored.sort((x, y) => y[0] - x[0]);
  const top = scored[0][0];
  const tops = scored.filter((x) => x[0] === top).map((x) => x[1]);
  if (tops.length > 1 && top < 100) {
    const opts = tops
      .slice(0, 5)
      .map((a) => `${nickOf(a)}(${accountId(a).slice(-8)})`)
      .join(", ");
    throw new Error(`账号标识不唯一: ${needle}，匹配到多个账号: ${opts}；请改用完整 openid 或序号`);
  }
  return tops[0];
}

function parseSelectors(raw) {
  return String(raw || "")
    .split(/[\n&@]+/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => x.split("#")[0].trim())
    .filter(Boolean);
}

async function resolveAccounts(server, auth, selectors, accountsCache) {
  const accounts = accountsCache || (await fetchAccounts(server, auth));
  const list = Array.isArray(selectors)
    ? selectors.map((x) => String(x || "").trim()).filter(Boolean)
    : parseSelectors(selectors);
  if (!list.length || list.some((x) => ["all", "*", "全部", "auto"].includes(String(x).toLowerCase()))) {
    return accounts;
  }
  const out = [];
  const seen = new Set();
  for (const raw of list) {
    const a = matchAccount(raw, accounts);
    const oid = accountId(a);
    if (oid && !seen.has(oid)) {
      seen.add(oid);
      out.push(a);
    }
  }
  return out;
}

async function loadTaskAccounts(rawEnvValue, server, auth) {
  const base = String(server || process.env.WECHAT_SERVER || process.env.YYB_SERVER || "").replace(
    /\/+$/,
    ""
  );
  const a = getAuth(auth);
  const text = String(rawEnvValue || "").trim();
  let selectors = [];
  if (text && !/^(all|\*|全部|auto)$/i.test(text)) {
    const chunks = text.split(/[\n&@]+/);
    for (const chunk of chunks) {
      const pieces =
        chunk.includes("#") && !/[@&]/.test(chunk)
          ? chunk.trim().split(/\s+(?=[^#\s]+#)/)
          : [chunk.trim()];
      for (const line of pieces) {
        const id = line.split("#")[0].trim();
        if (id) selectors.push(id);
      }
    }
  }
  const list = await resolveAccounts(base, a, selectors);
  return list.map((x) => ({
    openid: accountId(x),
    wxid: accountId(x),
    nickname: nickOf(x),
    remark: nickOf(x),
    raw: x,
  }));
}

async function resolveOpenid(server, auth, identifier, accountsCache) {
  const accounts = accountsCache || (await fetchAccounts(server, auth));
  const a = matchAccount(identifier, accounts);
  const oid = accountId(a);
  if (!oid) throw new Error(`匹配到账号但无 openid: ${identifier}`);
  return { openid: oid, account: a, accounts, resolved: oid !== String(identifier || "").trim() };
}

async function getYybCode(server, auth, appid, identifier, accountsCache) {
  const base = String(server || process.env.WECHAT_SERVER || process.env.YYB_SERVER || "").replace(
    /\/+$/,
    ""
  );
  const a = getAuth(auth);
  const { openid, accounts, resolved } = await resolveOpenid(base, a, identifier, accountsCache);
  if (resolved) console.log(`账号标识已解析: ${identifier} -> ${openid}`);
  const { data, status } = await requestJson("POST", `${base}/api/yyb/get-code`, {
    headers: authHeaders(a),
    body: { openid, wxid: openid, appid, auth: a },
    timeout: 60000,
  });
  if (status >= 400 || (data && data.success === false)) {
    const err = (data && (data.error || data.detail || data.Message)) || `HTTP ${status}`;
    throw new Error(String(err));
  }
  const code =
    (data && data.code) ||
    (data && data.Data && data.Data.code) ||
    (data && data.data && data.data.code) ||
    "";
  if (!code || code === 0 || code === 200 || code === "0" || code === "200") {
    throw new Error(`响应中未找到 code: ${JSON.stringify(data)}`);
  }
  return { code: String(code), openid, data, accounts };
}

module.exports = {
  getAuth,
  authHeaders,
  fetchAccounts,
  matchAccount,
  parseSelectors,
  resolveAccounts,
  loadTaskAccounts,
  resolveOpenid,
  getYybCode,
  accountId,
  nickOf,
};
