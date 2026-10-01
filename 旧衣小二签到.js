
const { spawn } = require("child_process");

function normalizeWxId(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    return raw.split("#")[0].trim();
}

function getSingleCodeFromPython(appid, wxid) {
    return new Promise((resolve, reject) => {
        const python = process.env.PYTHON || process.env.PYTHON_PATH || "python3";
        const pyCode = [
            "import json, sys",
            "from getCode import get_single_code",
            "code = get_single_code(sys.argv[1], sys.argv[2])",
            "print('__CODE_JSON__' + json.dumps({'code': code}, ensure_ascii=False))",
        ].join("; ");
        const child = spawn(python, ["-c", pyCode, appid, wxid], {
            cwd: __dirname,
            env: {
                ...process.env,
                WX_ID: wxid,
            },
        });
        let stdout = "";
        let stderr = "";
        child.stdout.on("data", data => { stdout += data.toString(); });
        child.stderr.on("data", data => { stderr += data.toString(); });
        child.on("error", err => {
            if (err && err.code === "ENOENT") {
                reject(new Error(`找不到Python解释器: ${python}，青龙请确认已安装python3，或设置环境变量 PYTHON=/usr/bin/python3`));
                return;
            }
            reject(err);
        });
        child.on("close", code => {
            if (code !== 0) {
                reject(new Error((stderr || stdout || `getCode.py exited with ${code}`).trim()));
                return;
            }
            const line = stdout.split(/\r?\n/).reverse().find(v => v.startsWith("__CODE_JSON__"));
            if (!line) {
                reject(new Error(`getCode.py 未返回code: ${stdout || stderr}`));
                return;
            }
            try {
                resolve(JSON.parse(line.slice("__CODE_JSON__".length)).code);
            } catch (e) {
                reject(new Error(`解析getCode.py返回失败: ${e.message || e}`));
            }
        });
    });
}

class WeChatServer {
    constructor(config) { this.config = config; }
    async getCode(wxid) {
        try {
            const actualWxid = normalizeWxId(wxid);
            // 优先直连 yybOpenid；失败再回退 python getCode
            try {
                const { getYybCode, getAuth } = require("./yybOpenid");
                const ret = await getYybCode(this.config.url, getAuth(), this.config.appid, actualWxid);
                return { data: { status: true, code: ret.code, data: { code: ret.code }, openid: ret.openid } };
            } catch (e1) {
                const code = await getSingleCodeFromPython(this.config.appid, actualWxid);
                return { data: { status: true, code, data: { code } } };
            }
        } catch (e) {
            return { data: { status: false, msg: e.message || String(e) } };
        }
    }
}

class Env {
    constructor(name) { this.name = name; this.userList = []; this.userIdx = 1; this.logs = []; const originalLog = console.log; console.log = (...args) => { this.logs.push(args.join(" ")); originalLog.apply(console, args); }; }
    log(...args) { console.log(...args); this.logs.push(args.join(" ")); }
    checkEnv(ckName) {
        const val = process.env[ckName];
        if (val) this.userList = val.split(/[\n&@]+/).map(normalizeWxId).filter(Boolean);
        else this.userList = [];
    }
    async done() { try { const notify = require('./sendNotify'); await notify.sendNotify(this.name, this.logs.join('\n')); } catch(e) { console.log('通知发送失败', e); } }
}
/*
------------------------------------------
@Author: sm
@Date: 2026.05.31
@Description: 旧衣小二签到
cron: 33 8 * * *
------------------------------------------
变量：
  WECHAT_SERVER / LICENSE_KEY  必需
  WXIDXJ  可选。支持：openid / 控制台昵称 / 序号1起 / all
          多个用 @ & 换行；空或 all = 当前授权码下全部账号
WXIDXJ 示例：
  bncr@孤独呢小怪兽@.
  1@2@3
  all
*/


const $ = new Env("旧衣小二签到");
const axios = require("axios");
const fs = require("fs");
const path = require("path");


const MINI_APP_ID = "wx426d52c8130b8559";
const PAGE_VERSION = "5";
const API_BASE = "https://jiuyixiaoer.fzjingzhou.com";
const TOKEN_CACHE_FILE = path.join(__dirname, "jyxe_token_cache.json");
const USER_AGENT = "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Mobile MicroMessenger/8.0.50 MiniProgramEnv/android";
const GUEST_TOKEN = "wek2020123456788wek";

let ckName = "WXIDXJ";

const wechat = new WeChatServer({
    url: process.env.WECHAT_SERVER || "http://192.168.6.222:8011",
    appid: MINI_APP_ID,
    WXIDXJ: process.env.WXIDXJ || "",
});

function readTokenCache() {
    try {
        if (!fs.existsSync(TOKEN_CACHE_FILE)) return {};
        return JSON.parse(fs.readFileSync(TOKEN_CACHE_FILE, "utf8")) || {};
    } catch (e) {
        return {};
    }
}

function writeTokenCache(cache) {
    try {
        fs.writeFileSync(TOKEN_CACHE_FILE, JSON.stringify(cache, null, 2), "utf8");
    } catch (e) {
        $.log(`写入token缓存失败: ${e.message || e}`);
    }
}

function formBody(data = {}) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(data)) {
        if (value !== undefined && value !== null) params.append(key, String(value));
    }
    return params;
}

function isTokenError(message) {
    return /token|登录|验证失败|9999|401|403|expire|过期|失效/i.test(String(message || ""));
}

class Task {
    constructor(openid) {
        this.index = $.userIdx++;
        this.openid = normalizeWxId(openid);
        this.session = {};
    }

    async run() {
        const cached = this.getCachedToken();
        if (cached) {
            this.session = cached;
            $.log(`账号[${this.index}] 使用缓存token`);
            if (!(await this.checkToken())) {
                this.removeCachedToken();
                $.log(`账号[${this.index}] 缓存token失效，重新登录`);
            }
        }

        if (!this.session.token) {
            try { await this.loginByWxCode(); } catch (e) { $.log(`账号[${this.index}] 登录失败: ${e.message || e}`); }
            if (!this.session.token) return;
        }

        await this.doSign();
        this.saveCachedToken();
    }

    getCachedToken() {
        const cache = readTokenCache();
        return cache[this.openid] || null;
    }

    saveCachedToken() {
        if (!this.session.token) return;
        const cache = readTokenCache();
        cache[this.openid] = {
            token: this.session.token,
            userInfo: this.session.userInfo || {},
            newOrder: this.session.newOrder || null,
            updatedAt: new Date().toISOString(),
        };
        writeTokenCache(cache);
    }

    removeCachedToken() {
        const cache = readTokenCache();
        if (cache[this.openid]) {
            delete cache[this.openid];
            writeTokenCache(cache);
        }
        this.session = {};
    }

    headers() {
        return {
            "content-type": "application/x-www-form-urlencoded",
            "platform": "MP-WEIXIN",
            "User-Agent": USER_AGENT,
            "Referer": `https://servicewechat.com/${MINI_APP_ID}/${PAGE_VERSION}/page-frame.html`,
        };
    }

    async request(apiPath, data = {}, options = {}) {
        const token = options.noauth ? GUEST_TOKEN : (this.session.token || GUEST_TOKEN);
        const res = await axios.post(`${API_BASE}${apiPath}`, formBody({
            ...(data || {}),
            token,
        }), {
            headers: this.headers(),
            timeout: 20000,
            validateStatus: () => true,
        });

        if (res.status !== 200) throw new Error(`HTTP ${res.status}`);
        if (Number(res.data?.code) !== 1000) {
            const error = new Error(res.data?.msg || `接口错误: ${res.data?.code || "unknown"}`);
            error.data = res.data;
            throw error;
        }
        return res.data;
    }

    async getLoginCode() {
        const { data } = await wechat.getCode(this.openid);
        const code = data?.code || data?.data?.code;
        if (!code) throw new Error(`wx_server 未返回code: ${JSON.stringify(data)}`);
        return code;
    }

    async loginByWxCode() {
        try {
            const code = await this.getLoginCode();
            const res = await this.request("/api/login/getWxMiniProgramSessionKey", {
                code,
                gdtVid: "",
            }, { noauth: true });
            const data = res.data || {};
            this.session = {
                token: data.token || res.token || "",
                userInfo: data.personInfo || {},
                newOrder: data.newOrder || null,
            };
            if (!this.session.token) throw new Error("登录未返回token");
            this.saveCachedToken();
            $.log(`账号[${this.index}] 登录成功`);
        } catch (e) {
            $.log(`账号[${this.index}] 登录失败: ${e.message || e}`);
        }
    }

    async checkToken() {
        try {
            const res = await this.request("/api/Person/index");
            if (res.data) this.session.userInfo = res.data;
            return true;
        } catch (e) {
            return false;
        }
    }

    async doSign() {
        try {
            const res = await this.request("/api/Person/sign");
            const beans = res.data;
            $.log(`账号[${this.index}] 签到成功${beans !== undefined ? `，获得${beans}环保币` : ""}`);
        } catch (e) {
            const message = e.message || e;
            if (/已签到|今日已|重复|已经签到/.test(String(message))) {
                $.log(`账号[${this.index}] 今日已签到`);
                return;
            }
            $.log(`账号[${this.index}] 签到失败: ${message}`);
            if (isTokenError(message)) this.removeCachedToken();
        }
    }
}

!(async () => {
    $.checkEnv(ckName);
    try {
        const { loadTaskAccounts } = require("./yybOpenid");
        const list = await loadTaskAccounts(process.env[ckName] || "");
        if (!list.length) { $.log("yyb 当前授权码下无已绑定账号"); return; }
        $.userList = list.map(a => a.wxid);
        $.log(`直连账号 ${$.userList.length} 个: ${list.map(a => a.remark).join(" / ")}`);
    } catch (e) {
        if (!$.userList.length) { $.log(`解析账号失败: ${e.message || e}`); return; }
        $.log(`账号解析回退: ${e.message || e}`);
    }
    for (const openid of $.userList) {
        await new Task(openid).run();
    }
})()
    .catch((e) => $.log(e.message || e))
    .finally(() => $.done());

