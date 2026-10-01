/*
------------------------------------------
可口可乐小程序动态 code 签到
APPID: wxa5811e0426a94686

环境变量:
  WECHAT_SERVER    协议服务IP地址和端口（支持WeChatPadPro、iwechat、牛子协议）
  ADMIN_KEY        与搭建时设置的ADMIN_KEY一致（仅WeChatPadPro或iwechat需要，牛子协议不需要）
  WX_ID            可选，指定要执行任务的微信账号ID，多个用&分隔
                   如果不设置则为所有在线账号执行任务
  PLUSPLUS_TOKEN   PushPlus token，可选
  UNICOM_PROXY_API        品赞代理提取 API，可选
  PROXY_TYPE       http / socks5，默认 http
  IPZAN_CONFIG     品赞自动加白名单，推荐格式:
                   套餐购买编号#你的登录密码#你的套餐提取密匙#你的签名秘钥#1

依赖:
  npm install axios http-proxy-agent https-proxy-agent socks-proxy-agent
------------------------------------------
*/

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");
const axios = require("axios");
let SocksProxyAgent, HttpsProxyAgent, HttpProxyAgent;

delete process.env.HTTP_PROXY;
delete process.env.HTTPS_PROXY;
delete process.env.http_proxy;
delete process.env.https_proxy;

const APPID = "wxa5811e0426a94686";
const WECHAT_SERVER = (process.env.WECHAT_SERVER || "").trim().replace(/\/$/, "");
const ADMIN_KEY = (process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || "").trim();
const WX_ID_FILTER = (process.env.WX_ID || "").trim();

const PLUSPLUS_TOKEN = process.env.PLUSPLUS_TOKEN || "";

const UNICOM_PROXY_API = process.env.UNICOM_PROXY_API || "";
const PROXY_TYPE = (process.env.PROXY_TYPE || "http").toLowerCase();
const PROXY_RETRY_TIMES = 3;
const PROXY_VALIDATE_URL = "http://httpbin.org/ip";
const PROXY_FETCH_INTERVAL = 3000;
const ENABLE_DIRECT_FALLBACK = true;
const debug = false;
const SCRIPT_VERSION = "2026.08.15.2";
let qlNotifyModulePath;
let proxyRuntimeReady = false;
const ipzanWhitelistCache = new Set();
let ipzanWhitelistNoopLogged = false;

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541923) XWEB/19823";

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function random(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}

function mask(value) {
    value = String(value || "");
    if (value.length <= 12) return value;
    return `${value.slice(0, 6)}...${value.slice(-6)}`;
}

function logTitle(accountCount = 0) {
    console.log("\n╔══════════════════════════════════════════════╗");
    console.log("║ 🥤 可口可乐动态 code 签到                   ║");
    console.log(`║ 🕒 ${new Date().toLocaleString("zh-CN")}                 ║`);
    console.log(`║ 🔢 账号数量: ${accountCount}                              ║`);
    console.log("╚══════════════════════════════════════════════╝\n");
}

function logAccount(index, total, server) {
    console.log("\n┌──────────────────────────────────────────────┐");
    console.log(`│ 🧩 账号 ${index} / ${total}`);
    console.log(`│ 🌍 来源 ${server}`);
    console.log("└──────────────────────────────────────────────┘");
}

function isValidProxyHost(host) {
    host = String(host || "").trim();
    if (!host) return false;

    if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
        return host.split(".").every(part => {
            const num = Number(part);
            return Number.isInteger(num) && num >= 0 && num <= 255;
        });
    }

    return /^[a-zA-Z0-9.-]+$/.test(host);
}

function isValidProxyPort(port) {
    return Number.isInteger(port) && port > 0 && port <= 65535;
}

function parseIpzanConfig() {
    const raw = String(process.env.IPZAN_CONFIG || "").trim();

    if (raw) {
        try {
            const data = JSON.parse(raw);
            return {
                no: String(data.no || "").trim(),
                password: String(data.password || "").trim(),
                fetchKey: String(data.fetchKey || data.fetch_key || "").trim(),
                signKey: String(data.signKey || data.sign_key || "").trim(),
                replace: String(data.replace ?? "1").trim() || "1"
            };
        } catch (e) {}

        if (raw.includes("=") && raw.includes("&")) {
            const params = new URLSearchParams(raw);
            return {
                no: String(params.get("no") || "").trim(),
                password: String(params.get("password") || "").trim(),
                fetchKey: String(params.get("fetchKey") || params.get("fetch_key") || "").trim(),
                signKey: String(params.get("signKey") || params.get("sign_key") || "").trim(),
                replace: String(params.get("replace") || "1").trim() || "1"
            };
        }

        const parts = raw.split(/[#|&]/).map(item => item.trim());
        return {
            no: parts[0] || "",
            password: parts[1] || "",
            fetchKey: parts[2] || "",
            signKey: parts[3] || "",
            replace: parts[4] || "1"
        };
    }

    return {
        no: String(process.env.IPZAN_NO || "").trim(),
        password: String(process.env.IPZAN_PASSWORD || "").trim(),
        fetchKey: String(process.env.IPZAN_FETCH_KEY || "").trim(),
        signKey: String(process.env.IPZAN_SIGN_KEY || "").trim(),
        replace: String(process.env.IPZAN_REPLACE || "1").trim() || "1"
    };
}

const IPZAN_SETTINGS = parseIpzanConfig();

function extractIpv4(text) {
    const match = String(text || "").match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/);
    if (!match) return "";
    return isValidProxyHost(match[0]) ? match[0] : "";
}

function isIpzanWhitelistConfigReady() {
    return Boolean(
        IPZAN_SETTINGS.no
        && IPZAN_SETTINGS.password
        && IPZAN_SETTINGS.fetchKey
        && IPZAN_SETTINGS.signKey
    );
}

function pickAesAlgorithm(keyBuffer) {
    const size = keyBuffer.length;
    if (size === 16) return "aes-128-ecb";
    if (size === 24) return "aes-192-ecb";
    if (size === 32) return "aes-256-ecb";
    throw new Error(`品赞签名秘钥长度无效，当前为 ${size} 字节，仅支持 16/24/32 字节`);
}

function buildIpzanSign(timestamp) {
    const plainText = `${IPZAN_SETTINGS.password}:${IPZAN_SETTINGS.fetchKey}:${timestamp}`;
    const keyBuffer = Buffer.from(IPZAN_SETTINGS.signKey, "utf8");
    const algorithm = pickAesAlgorithm(keyBuffer);
    const cipher = crypto.createCipheriv(algorithm, keyBuffer, null);
    cipher.setAutoPadding(true);
    return Buffer.concat([
        cipher.update(plainText, "utf8"),
        cipher.final()
    ]).toString("hex");
}

async function addIpzanWhitelist(ip) {
    if (!isValidProxyHost(ip)) {
        console.log(`⚠️ [白名单] 无法识别待添加 IP: ${ip || "-"}`);
        return false;
    }

    if (ipzanWhitelistCache.has(ip)) {
        console.log(`ℹ️ [白名单] ${ip} 本次运行已尝试添加，等待代理生效`);
        return true;
    }

    if (!isIpzanWhitelistConfigReady()) {
        console.log("⚠️ [白名单] 未配置 IPZAN_CONFIG，无法自动加白");
        return false;
    }

    try {
        const timestamp = Math.floor(Date.now() / 1000);
        const sign = buildIpzanSign(timestamp);
        console.log(`🌐 [白名单] 正在自动添加 ${ip} 到品赞白名单`);

        const { data } = await axios({
            method: "POST",
            url: "https://service.ipzan.com/whiteList-add",
            timeout: 15000,
            proxy: false,
            data: {
                no: IPZAN_SETTINGS.no,
                ip,
                sign,
                replace: IPZAN_SETTINGS.replace || "1"
            }
        });

        const message = data?.message || data?.msg || JSON.stringify(data);
        const success = data?.code === 0
            || data?.success === true
            || /成功|success|已在白名单|已存在/i.test(message);

        if (success) {
            ipzanWhitelistCache.add(ip);
            console.log(`✅ [白名单] ${ip} 添加成功`);
            return true;
        }

        console.log(`⚠️ [白名单] ${ip} 添加失败: ${message}`);
        return false;
    } catch (e) {
        console.log(`❌ [白名单] ${ip} 添加异常: ${e.message}`);
        return false;
    }
}

function parseIpzanWhitelistError(rawText) {
    const text = String(rawText || "").trim();
    let message = text;

    try {
        const data = JSON.parse(text);
        message = String(data?.message || data?.msg || text).trim();
    } catch (e) {}

    if (!message || !/白名单|加入到.*白名单/.test(message)) {
        if (!/白名单|加入到.*白名单/.test(text)) {
            return null;
        }
        message = text;
    }

    return {
        ip: extractIpv4(message || text),
        message
    };
}

function parseProxyResponse(text) {
    if (typeof text !== "string") {
        text = JSON.stringify(text);
    }

    text = text.trim();
    if (!text) return null;

    try {
        const data = JSON.parse(text);
        let proxyObj = null;

        if (data.data && Array.isArray(data.data) && data.data.length > 0) {
            proxyObj = data.data[0];
        } else if (data.data && typeof data.data === "object") {
            proxyObj = data.data;
        } else if (data.ip && data.port) {
            proxyObj = data;
        } else if (data.result && data.result.ip && data.result.port) {
            proxyObj = data.result;
        }

        if (proxyObj) {
            const port = Number(proxyObj.port);
            const host = String(proxyObj.ip || proxyObj.host || "").trim();
            if (!isValidProxyHost(host) || !isValidProxyPort(port)) {
                return null;
            }

            return {
                host,
                port,
                username: proxyObj.user || proxyObj.username || "",
                password: proxyObj.pass || proxyObj.password || ""
            };
        }
    } catch (e) {}

    const line = text.split(/\r?\n/).find(item => item.trim());
    if (line && !/^[{\[]/.test(line.trim())) {
        const match = line.trim().match(/^([a-zA-Z0-9.-]+):(\d{1,5})(?::([^:\r\n]*))?(?::([^:\r\n]*))?$/);
        if (match) {
            const host = match[1].trim();
            const port = Number(match[2]);
            if (isValidProxyHost(host) && isValidProxyPort(port)) {
                return {
                    host,
                    port,
                    username: match[3] || "",
                    password: match[4] || ""
                };
            }
        }

        const lineParts = line.trim().split(/\s+/);
        if (lineParts.length > 0) {
            const matchFromFirstToken = lineParts[0].match(/^([a-zA-Z0-9.-]+):(\d{1,5})(?::([^:\r\n]*))?(?::([^:\r\n]*))?$/);
            if (matchFromFirstToken) {
                const host = matchFromFirstToken[1].trim();
                const port = Number(matchFromFirstToken[2]);
                if (isValidProxyHost(host) && isValidProxyPort(port)) {
                    return {
                        host,
                        port,
                        username: matchFromFirstToken[3] || "",
                        password: matchFromFirstToken[4] || ""
                    };
                }
            }
        }

    }

    return null;
}

function buildProxyAgent(proxyInfo) {
    if (!proxyInfo) return null;

    const { host, port, username, password } = proxyInfo;
    let auth = "";

    if (username && password) {
        auth = `${encodeURIComponent(username)}:${encodeURIComponent(password)}@`;
    }

    try {
        if (PROXY_TYPE === "socks5") {
            if (!SocksProxyAgent) {
                console.log(`⚠️ [代理] SOCKS5 代理模块未加载，跳过代理配置`);
                return null;
            }

            const proxyUrl = `socks5://${auth}${host}:${port}`;
            console.log(`🛠️ [代理] 生成 SOCKS5 代理 ${host}:${port}`);
            return {
                httpAgent: new SocksProxyAgent(proxyUrl),
                httpsAgent: new SocksProxyAgent(proxyUrl)
            };
        }

        if (!HttpsProxyAgent || !HttpProxyAgent) {
            console.log(`⚠️ [代理] HTTP 代理模块未加载，跳过代理配置`);
            return null;
        }

        const proxyUrl = `http://${auth}${host}:${port}`;
        console.log(`🛠️ [代理] 生成 HTTP 代理 ${host}:${port}`);
        return {
            httpAgent: new HttpProxyAgent(proxyUrl),
            httpsAgent: new HttpsProxyAgent(proxyUrl)
        };
    } catch (e) {
        console.log(`❌ [代理] 生成代理失败: ${e.message}`);
        return null;
    }
}

async function validateProxy(agent) {
    if (!agent) return { ok: false, ip: "" };

    try {
        const res = await axios({
            method: "get",
            url: PROXY_VALIDATE_URL,
            timeout: 15000,
            ...agent
        });

        if (res.status === 200) {
            const ip = res.data?.origin || "未知";
            console.log(`✅ [代理] 验证通过，出口 IP: ${ip}`);
            return { ok: true, ip };
        }
    } catch (e) {
        console.log(`⚠️ [代理] 验证失败: ${e.message}`);
    }

    return { ok: false, ip: "" };
}

async function getValidProxy(accountName) {
    if (!UNICOM_PROXY_API) {
        console.log(`⚠️ [代理] ${accountName} 未配置 UNICOM_PROXY_API，使用直连`);
        return { agent: null, ip: "" };
    }

    if (!proxyRuntimeReady) {
        console.log(`⚠️ [代理] ${accountName} 代理依赖未就绪，跳过代理并使用直连`);
        return { agent: null, ip: "" };
    }

    console.log(`🌐 [代理] ${accountName} 正在获取品赞代理...`);
    const whitelistHandledIps = new Set();
    let whitelistTriggered = false;

    for (let i = 1; i <= PROXY_RETRY_TIMES; i++) {
        try {
            const res = await axios.get(UNICOM_PROXY_API, {
                timeout: 15000,
                proxy: false
            });

            const proxyInfo = parseProxyResponse(res.data);

            if (!proxyInfo) {
                const rawText = typeof res.data === "string" ? res.data.trim() : JSON.stringify(res.data);
                const whitelistInfo = parseIpzanWhitelistError(rawText);
                if (whitelistInfo?.ip) {
                    whitelistTriggered = true;
                    console.log(`⚠️ [白名单] 检测到品赞白名单限制: ${whitelistInfo.message}`);
                    if (!whitelistHandledIps.has(whitelistInfo.ip)) {
                        whitelistHandledIps.add(whitelistInfo.ip);
                        const added = await addIpzanWhitelist(whitelistInfo.ip);
                        if (added) {
                            console.log("⏳ [白名单] 等待 2s 后重新提取代理");
                            await sleep(2000);
                            continue;
                        }
                    }
                }
                console.log(`⚠️ [代理] 第 ${i} 次解析失败，返回内容: ${rawText.slice(0, 120)}`);
                continue;
            }

            console.log(`✅ [代理] 提取到 ${proxyInfo.host}:${proxyInfo.port}`);
            if (isIpzanWhitelistConfigReady() && !whitelistTriggered && !ipzanWhitelistNoopLogged) {
                ipzanWhitelistNoopLogged = true;
                console.log("ℹ️ [白名单] 当前服务器 IP 已在品赞白名单，无需自动添加");
            }

            const agent = buildProxyAgent(proxyInfo);
            const valid = await validateProxy(agent);

            if (valid.ok) {
                return { agent, ip: valid.ip };
            }
        } catch (e) {
            console.log(`⚠️ [代理] 第 ${i} 次获取异常: ${e.message}`);
        }

        if (i < PROXY_RETRY_TIMES) {
            await sleep(2000);
        }
    }

    console.log("⚠️ [代理] 获取失败，使用直连");
    return { agent: null, ip: "" };
}

async function requestWithProxy(config, proxyAgent, server) {
    if (proxyAgent) {
        try {
            return await axios({
                timeout: 30000,
                ...config,
                ...proxyAgent
            });
        } catch (e) {
            console.log(`⚠️ [代理] ${server} 代理请求失败: ${e.message}`);

            if (!ENABLE_DIRECT_FALLBACK) {
                throw e;
            }

            console.log("🔁 [兜底] 切换直连重试");
        }
    }

    return await axios({
        timeout: 30000,
        proxy: false,
        ...config
    });
}

async function sendPushPlus(title, content) {
    if (!PLUSPLUS_TOKEN) {
        console.log("⚠️ [PushPlus] 未配置 PLUSPLUS_TOKEN，跳过推送");
        return;
    }

    try {
        await axios.post(
            "https://www.pushplus.plus/send",
            {
                token: PLUSPLUS_TOKEN,
                title,
                content,
                template: "txt"
            },
            {
                timeout: 10000,
                proxy: false
            }
        );

        console.log("✅ [PushPlus] 推送成功");
    } catch (e) {
        console.log(`❌ [PushPlus] 推送失败: ${e.message}`);
    }
}

function resolveQingLongNotifyPath() {
    if (qlNotifyModulePath !== undefined) {
        return qlNotifyModulePath || null;
    }

    const candidates = [
        path.resolve(__dirname, "sendNotify"),
        path.resolve(__dirname, "../sendNotify"),
        "/ql/data/scripts/sendNotify",
        "/ql/data/config/sendNotify"
    ];

    for (const modulePath of candidates) {
        try {
            const resolvedPath = require.resolve(modulePath);
            if (fs.existsSync(resolvedPath)) {
                qlNotifyModulePath = resolvedPath;
                console.log(`✅ [通知] 已定位青龙通知模块: ${resolvedPath}`);
                return qlNotifyModulePath;
            }
        } catch (e) {}
    }

    qlNotifyModulePath = false;
    console.log("⚠️ [通知] 未找到 sendNotify.js，将尝试使用 PushPlus");
    return null;
}

async function sendQingLongNotify(modulePath, title, content) {
    return await new Promise((resolve) => {
        const childScript = `
process.on("uncaughtException", (err) => {
    console.error("[sendNotify child] uncaughtException:", err && err.stack ? err.stack : err);
    process.exit(1);
});
process.on("unhandledRejection", (err) => {
    console.error("[sendNotify child] unhandledRejection:", err && err.stack ? err.stack : err);
    process.exit(1);
});
(async () => {
    try {
        try {
            const undici = require("undici");
            const originalRequest = undici.request.bind(undici);
            undici.request = (url, options = {}) => {
                const headers = options && options.headers;
                if (headers && typeof headers === "object" && !Array.isArray(headers)) {
                    const normalizedHeaders = {};
                    for (const [key, value] of Object.entries(headers)) {
                        normalizedHeaders[String(key).toLowerCase()] = value;
                    }
                    options = {
                        ...options,
                        headers: normalizedHeaders
                    };
                }
                return originalRequest(url, options);
            };
        } catch (patchErr) {
            console.error("[sendNotify child] undici patch warning:", patchErr && patchErr.message ? patchErr.message : patchErr);
        }

        const notifyModule = require(process.env.QL_NOTIFY_MODULE);
        const sendNotify = typeof notifyModule === "function" ? notifyModule : notifyModule?.sendNotify;
        if (typeof sendNotify !== "function") {
            throw new Error("sendNotify 导出不存在");
        }
        await sendNotify(process.env.QL_NOTIFY_TITLE || "", process.env.QL_NOTIFY_CONTENT || "");
        process.exit(0);
    } catch (err) {
        console.error(err && err.stack ? err.stack : err);
        process.exit(1);
    }
})();
`;

        const child = spawn(process.execPath, ["-e", childScript], {
            cwd: process.cwd(),
            env: {
                ...process.env,
                QL_NOTIFY_MODULE: modulePath,
                QL_NOTIFY_TITLE: title,
                QL_NOTIFY_CONTENT: content
            },
            stdio: ["ignore", "pipe", "pipe"]
        });

        let stdout = "";
        let stderr = "";
        let finished = false;
        const timeout = setTimeout(() => {
            if (finished) return;
            finished = true;
            child.kill("SIGKILL");
            resolve({
                ok: false,
                error: "通知子进程执行超时"
            });
        }, 60000);

        child.stdout.on("data", (chunk) => {
            stdout += String(chunk);
        });

        child.stderr.on("data", (chunk) => {
            stderr += String(chunk);
        });

        child.on("error", (err) => {
            if (finished) return;
            finished = true;
            clearTimeout(timeout);
            resolve({
                ok: false,
                error: err.message
            });
        });

        child.on("close", (code) => {
            if (finished) return;
            finished = true;
            clearTimeout(timeout);
            resolve({
                ok: code === 0,
                code,
                stdout: stdout.trim(),
                error: stderr.trim()
            });
        });
    });
}

async function sendNotifyMessage(title, content) {
    const modulePath = resolveQingLongNotifyPath();

    if (modulePath) {
        const notifyResult = await sendQingLongNotify(modulePath, title, content);
        if (notifyResult.ok) {
            console.log("✅ [通知] 青龙通知推送成功");
            if (notifyResult.stdout) {
                console.log(notifyResult.stdout);
            }
            return;
        }

        console.log(`❌ [通知] 青龙通知推送失败: ${notifyResult.error || `退出码 ${notifyResult.code}`}`);
    }

    await sendPushPlus(title, content);
}

async function detectProtocol(server, adminKey) {
    const auth = adminKey || process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '';
    try {
        const resp = await axios.get(`${server}/api/accounts`, {
            timeout: 8000, proxy: false, validateStatus: () => true,
            headers: { 'X-License-Key': auth, Authorization: 'Bearer ' + auth },
        });
        if (resp.status === 200 && (Array.isArray(resp.data?.accounts) || Array.isArray(resp.data))) return "yyb呆呆";
    } catch (e) {}
    try {
        const resp = await axios.get(`${server}/admin/GetAuthKey?key=${encodeURIComponent(auth)}`, { timeout: 5000, proxy: false, validateStatus: () => true });
        if (resp.status === 200 && (resp.data?.Code === 200 || Array.isArray(resp.data?.Data))) return "iwechat";
    } catch (e) {}
    try {
        const resp = await axios.get(`${server}/admin/GetAllDevices?key=${encodeURIComponent(auth)}`, { timeout: 5000, proxy: false, validateStatus: () => true });
        if (resp.status === 200 && (resp.data?.Code === 200 || Array.isArray(resp.data?.Data))) return "WeChatPadPro";
    } catch (e) {}
    return "Unknown";
}

async function getOnlineAccounts(server, adminKey, protocolType) {
    if (protocolType === "yyb呆呆" || protocolType === "Niuzi") {
        try {
            const { loadTaskAccounts } = require('./yybOpenid');
            const list = await loadTaskAccounts(process.env.WX_ID || '', server, adminKey);
            return list.map(a => ({ wxid: a.wxid, openid: a.wxid, nickname: a.remark || a.nickname, license: adminKey || a.wxid }));
        } catch (e) {
            const auth = adminKey || process.env.LICENSE_KEY || process.env.AUTH || process.env.ADMIN_KEY || '';
            const { data } = await axios.get(`${server}/api/accounts`, {
                timeout: 20000,
                proxy: false,
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
    } else if (protocolType === "iwechat") {
        const { data } = await axios.get(`${server}/admin/GetAuthKey?key=${adminKey}`, { timeout: 20000, proxy: false });
        if (!data || data.Code !== 200 || !Array.isArray(data.Data)) return [];
        return data.Data
            .filter(item => item.status === 1 && (item.wx_id || item.license))
            .map(item => ({ wxid: item.wx_id || "", nickname: item.nick_name || "", license: item.license || item.authKey || "" }));
    } else if (protocolType === "WeChatPadPro") {
        const { data } = await axios.get(`${server}/admin/GetAllDevices?key=${adminKey}`, { timeout: 20000, proxy: false });
        if (!data || data.Code !== 200 || !Array.isArray(data.Data)) return [];
        return data.Data
            .filter(item => item.status === 1 && (item.deviceId || item.authKey))
            .map(item => ({ wxid: item.deviceId || "", nickname: item.deviceName || "", license: item.authKey || item.license || "" }));
    }
    return [];
}

async function getCode(account, protocolType, remark) {
    const wxid = account.wxid;
    console.log(`🔐 [授权] ${remark || wxid} 请求 code...`);

    try {
        if (protocolType === "yyb呆呆" || protocolType === "Niuzi") {
            try {
                const { getYybCode, getAuth } = require('./yybOpenid');
                const auth = getAuth(ADMIN_KEY || account.license || '');
                const { code } = await getYybCode(WECHAT_SERVER, auth, APPID, wxid);
                console.log(`✅ [授权] ${remark || wxid} code 获取成功`);
                return code;
            } catch (e) {
                console.log(`❌ [授权] ${remark || wxid} code 获取失败: ${e.message || e}`);
                return null;
            }
        } else {
            const licenseKey = account.license || account.wxid || "";
            const { data } = await axios.post(`${WECHAT_SERVER}/applet/JsLogin?key=${licenseKey}`, { AppId: APPID, Data: "", Opt: 1, PackageName: "", SdkName: "" }, { timeout: 30000, proxy: false, headers: { 'Content-Type': 'application/json' } });
            if (data?.Code === 200 && data?.Data?.Code) {
                console.log(`✅ [授权] ${remark || wxid} code 获取成功`);
                return data.Data.Code;
            }
            console.log(`❌ [授权] ${remark || wxid} code 获取失败: ${JSON.stringify(data)}`);
            return null;
        }
    } catch (e) {
        console.log(`❌ [授权] ${remark || wxid} code 请求失败: ${e.message}`);
        return null;
    }
}

async function getUserToken(code, proxyAgent, server) {
    const config = {
        method: "GET",
        url: `https://member-api.icoke.cn/api/sp-portal/store/icoke/wechat/loginNoCache/${code}`,
        headers: {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "xweb_xhr": "1",
            "Content-Type": "application/json",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://servicewechat.com/wxa5811e0426a94686/496/page-frame.html",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }
    };

    try {
        const { data } = await requestWithProxy(config, proxyAgent, server);

        if (data?.jwtString) {
            console.log(`✅ [登录] token 获取成功: ${mask(data.jwtString)}`);
            return {
                token: data.jwtString,
                raw: data
            };
        }

        console.log(`❌ [登录] token 获取失败: ${data?.message || JSON.stringify(data)}`);
        return {
            token: null,
            raw: data
        };
    } catch (e) {
        console.log(`❌ [登录] token 请求异常: ${e.message}`);
        return {
            token: null,
            raw: null
        };
    }
}

async function getUserInfo(token, proxyAgent, server) {
    const config = {
        method: "GET",
        url: "https://member-api.icoke.cn/api/icoke-customer/icoke/mini/customer/main/points",
        headers: {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "authorization": token,
            "content-type": "application/json",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "xweb_xhr": "1",
            "Referer": "https://servicewechat.com/wxa5811e0426a94686/421/page-frame.html",
            "Referrer-Policy": "unsafe-url"
        }
    };

    try {
        const { data } = await requestWithProxy(config, proxyAgent, server);
        console.log(`💰 [积分] 当前快乐瓶: ${data?.point ?? "-"}`);
        return data;
    } catch (e) {
        console.log(`⚠️ [积分] 查询异常: ${e.message}`);
        return null;
    }
}

async function addSign(token, proxyAgent, server) {
    const config = {
        method: "GET",
        url: "https://member-api.icoke.cn/api/icoke-sign/icoke/mini/sign/main/sign",
        headers: {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "authorization": token,
            "content-type": "application/json",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "xweb_xhr": "1",
            "Referer": "https://servicewechat.com/wxa5811e0426a94686/421/page-frame.html",
            "Referrer-Policy": "unsafe-url"
        }
    };

    try {
        const { data } = await requestWithProxy(config, proxyAgent, server);

        if (data?.success === true) {
            const msg = `签到成功，获得 ${data.point ?? "-"} 快乐瓶`;
            console.log(`✅ [签到] ${msg}`);
            return {
                success: true,
                message: msg,
                raw: data
            };
        }

        const msg = data?.message || data?.msg || JSON.stringify(data);
        console.log(`❌ [签到] 签到失败: ${msg}`);

        return {
            success: false,
            message: msg,
            raw: data
        };
    } catch (e) {
        console.log(`❌ [签到] 请求异常: ${e.message}`);
        return {
            success: false,
            message: e.message,
            raw: null
        };
    }
}

async function runAccount(index, total, account, protocolType) {
    const { wxid, remark } = account;
    const displayName = remark || account.nickname || wxid;
    const result = {
        server: displayName,
        success: false,
        proxyStatus: "未使用代理",
        proxyIp: "-",
        token: "-",
        beforePoint: "-",
        signMsg: "-",
        afterPoint: "-",
        error: ""
    };

    console.log("\n┌──────────────────────────────────────────────┐");
    console.log(`│ 🧩 账号 ${index} / ${total}`);
    console.log(`│ 🌍 ${displayName}`);
    console.log("└──────────────────────────────────────────────┘");

    const proxy = await getValidProxy(displayName);
    const proxyAgent = proxy.agent;
    result.proxyStatus = proxyAgent ? "使用专属代理" : "使用直连";
    result.proxyIp = proxy.ip || "-";

    await sleep(PROXY_FETCH_INTERVAL);

    const delay = random(500, 1000);
    console.log(`⏳ [延迟] 启动延迟 ${(delay / 1000).toFixed(1)}s`);
    await sleep(delay);

    const code = await getCode(account, protocolType, displayName);
    if (!code) {
        result.error = "获取 code 失败";
        return result;
    }

    const login = await getUserToken(code, proxyAgent, displayName);
    if (!login.token) {
        result.error = "获取 token 失败";
        return result;
    }

    result.token = mask(login.token);

    const beforeInfo = await getUserInfo(login.token, proxyAgent, displayName);
    result.beforePoint = beforeInfo?.point ?? "-";

    await sleep(random(2000, 5000));

    const sign = await addSign(login.token, proxyAgent, displayName);
    result.signMsg = sign.message;

    await sleep(random(2000, 5000));

    const afterInfo = await getUserInfo(login.token, proxyAgent, displayName);
    result.afterPoint = afterInfo?.point ?? "-";

    result.success = sign.success || String(sign.message).includes("已") || String(sign.message).includes("重复");

    if (!result.success) {
        result.error = sign.message;
    }

    return result;
}

function buildNotify(results) {
    const successCount = results.filter(item => item.success).length;
    const failCount = results.length - successCount;

    let content = `🥤 可口可乐四账号签到结果

━━━━━━━━━━━━━━━━━━━━
🏁 总结：${successCount} 成功 / ${failCount} 失败
🕒 时间：${new Date().toLocaleString("zh-CN")}
━━━━━━━━━━━━━━━━━━━━
`;

    results.forEach((res, index) => {
        const icon = res.success ? "✅" : "❌";

        content += `
🧩 账号 ${index + 1}
🌍 来源：${res.server}
🌐 代理：${res.proxyStatus}
📡 出口IP：${res.proxyIp}
🔐 Token：${res.token}
💰 签到前快乐瓶：${res.beforePoint}
📝 签到结果：${res.signMsg}
💰 签到后快乐瓶：${res.afterPoint}
${icon} 结果：${res.success ? "成功" : "失败"}
`;

        if (!res.success) {
            content += `❌ 原因：${res.error}\n`;
        }

        content += "━━━━━━━━━━━━━━━━━━━━\n";
    });

    return content;
}

(async () => {
    console.log(`ℹ️ [版本] 可口可乐脚本版本: ${SCRIPT_VERSION}`);
    if (UNICOM_PROXY_API) {
        try {
            if (PROXY_TYPE === "socks5") {
                const socksModule = await import('socks-proxy-agent');
                SocksProxyAgent = socksModule.SocksProxyAgent;
                proxyRuntimeReady = true;
                console.log("✅ [依赖] SOCKS5 代理模块加载成功");
            } else {
                const [httpsModule, httpModule] = await Promise.all([
                    import('https-proxy-agent'),
                    import('http-proxy-agent')
                ]);
                HttpsProxyAgent = httpsModule.HttpsProxyAgent;
                HttpProxyAgent = httpModule.HttpProxyAgent;
                proxyRuntimeReady = true;
                console.log("✅ [依赖] HTTP 代理模块加载成功");
            }
        } catch (e) {
            proxyRuntimeReady = false;
            console.log(`⚠️ [依赖] 代理模块加载失败: ${e.message}`);
            console.log("⚠️ [依赖] 将使用直连模式运行");
        }

        if (isIpzanWhitelistConfigReady()) {
            console.log(`ℹ️ [白名单] 已启用品赞自动加白，套餐编号: ${mask(IPZAN_SETTINGS.no)}`);
            console.log(`ℹ️ [白名单] 自动替换模式: ${IPZAN_SETTINGS.replace || "1"}`);
        } else {
            console.log("ℹ️ [白名单] 未检测到有效的 IPZAN_CONFIG，白名单提示时将仅走直连");
        }
    } else {
        console.log("ℹ️ [代理] 未配置 UNICOM_PROXY_API，跳过代理模块加载");
    }

    if (!WECHAT_SERVER) {
        console.log("❌ [主程序] 未设置环境变量 WECHAT_SERVER");
        return;
    }

    console.log(`🌐 [协议] 服务地址: ${WECHAT_SERVER}`);
    const protocolType = await detectProtocol(WECHAT_SERVER, ADMIN_KEY);
    console.log(`🔌 [协议] 检测类型: ${protocolType}`);

    if (protocolType === "Unknown") {
        console.log("❌ [主程序] 未知的协议服务类型，请检查WECHAT_SERVER");
        return;
    }

    let onlineAccounts = await getOnlineAccounts(WECHAT_SERVER, ADMIN_KEY, protocolType);
    if (WX_ID_FILTER) {
        const targetIds = WX_ID_FILTER.split("&").map(s => s.trim()).filter(Boolean);
        onlineAccounts = onlineAccounts.filter(a => targetIds.includes(a.wxid));
        console.log(`📋 [配置] 根据WX_ID筛选后获得 ${onlineAccounts.length} 个账号`);
    }

    const accounts = onlineAccounts.map(acc => ({
        wxid: acc.wxid,
        remark: acc.nickname || acc.wxid,
        nickname: acc.nickname || acc.wxid,
        license: acc.license || "",
    }));

    if (accounts.length === 0) {
        console.log("❌ [主程序] 未获取到在线账号，退出执行");
        return;
    }

    console.log("\n╔══════════════════════════════════════════════╗");
    console.log("║ 🥤 可口可乐动态 code 签到                   ║");
    console.log(`║ 🕒 ${new Date().toLocaleString("zh-CN")}`);
    console.log(`║ 🔢 账号数量: ${accounts.length}`);
    console.log("╚══════════════════════════════════════════════╝");

    const results = [];

    for (let i = 0; i < accounts.length; i++) {
        const account = accounts[i];
        const displayName = account.remark || account.wxid;
        try {
            const res = await runAccount(i + 1, accounts.length, account, protocolType);
            results.push(res);
        } catch (e) {
            console.log(`❌ [主程序] ${displayName} 执行异常: ${e.message}`);
            results.push({
                server: displayName,
                success: false,
                proxyStatus: "-",
                proxyIp: "-",
                token: "-",
                beforePoint: "-",
                signMsg: "-",
                afterPoint: "-",
                error: e.message
            });
        }

        if (i < accounts.length - 1) {
            console.log("⏳ [间隔] 等待 2s 后处理下一个账号");
            await sleep(2000);
        }
    }

    const successCount = results.filter(item => item.success).length;
    const failCount = results.length - successCount;

    console.log("\n╔══════════════════════════════════════════════╗");
    console.log("║ 🏁 可口可乐任务执行完成                     ║");
    console.log(`║ ✅ 成功: ${successCount}`);
    console.log(`║ ❌ 失败: ${failCount}`);
    console.log(`║ 🕒 ${new Date().toLocaleString("zh-CN")}`);
    console.log("╚══════════════════════════════════════════════╝");

    await sendNotifyMessage("🥤 可口可乐签到完成", buildNotify(results));

})().catch(e => {
    console.log(`❌ [全局异常] ${e.message}`);
});
