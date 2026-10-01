#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
得物 (Dewu) 种树活动 - 自动签到 / 做任务 / 浇水领水滴脚本
- token 接口版（青龙环境变量 DEWU_AUTH_TOKEN 多账号；不含本地 code 服务）

======================================
  青龙面板使用教程
======================================

一、抓包获取 token（必须）
  1. 手机安装 ProxyPin 或其他抓包工具
  2. 打开得物 App，进入「种树/心愿森林」页面
  3. 在抓包工具中找到任意 app.dewu.com 的请求
  4. 复制请求头中的以下字段值：
     - x-auth-token: Bearer eyJhbGciOi... (JWT 格式，最核心)
     - duToken: 一长串十六进制+竖线分隔的字符串
     - cookieToken: 通常与 duToken 相同

二、配置青龙环境变量
  在青龙面板「配置文件」或「环境变量」中添加：

  必填变量：
    DEWU_AUTH_TOKEN    = 上面的 x-auth-token 值
    DEWU_DUTOKEN       = 上面的 duToken 值（可选，但建议填写以降低风控概率）
    DEWU_COOKIETOKEN   = 上面的 cookieToken 值（可选，通常与 duToken 相同）

  可选变量（不填也能运行，但可能影响风控判定）：
    DEWU_DUID          = 设备ID（抓包请求头 duid 字段）
    DEWU_SHUMEIID      = 数美设备指纹（抓包请求头 shumeiId 字段）

  可选变量（代理与推送）：
    PROXY_API          = 品赞代理提取 API（建议 mode=auth 认证模式，业务请求优先走代理）
    PROXY_TYPE         = 代理类型 http / socks5（默认 http）
    PLUSPLUS_TOKEN     = PushPlus 推送 token（运行结果推送到微信，不填不推送）

  可选变量（心愿打卡，青龙面板里等价于命令行参数）：
    DEWU_CHECKIN_FORCE = 0  关闭对带 jumpUrl 页面行为任务(逛社区/逛商品)的主动完成。
                            默认开启(=1)：这类任务会走 jumpUrl 里指向的
                            hacking-task 通道 (pre_commit -> 停留 -> commit)
                            真正去完成它，而不是无条件跳过。
                            逛社区已实测可自动完成；逛商品需要 App 内点开商品的
                            浏览上报(密文未还原)，脚本会试一次 commit，过不了如实报告。
    DEWU_CHECKIN_DEBUG = 1  打印 home-page 返回的 todayTasks 原始 JSON，
                            便于核对 canSkip / browseSeconds 等真实字段名。默认关闭。
    （真值写法：1 / true / yes / on / 是 / 开，均可；关闭填 0）

  注意：
    - DEWU_DUTOKEN 和 DEWU_COOKIETOKEN 的值通常完全相同
    - 只填 DEWU_AUTH_TOKEN 即可运行，但加上 DUTOKEN/COOKIETOKEN 可以降低风控概率
    - token 有效期约 1 年（JWT exp），过期需重新抓包
    - 多账号用 @ 分隔，如：token1@token2@token3
    - 配置品赞代理可显著降低触发滑块验证码的概率
    - socks5 代理需额外安装：pip install requests[socks]

四、添加脚本到青龙
  1. 将本脚本上传到青龙面板的「脚本管理」
  2. 在「定时任务」中添加：
     - 名称：得物种树签到
     - 命令：task dewu_sign_发帖版.py
     - 定时：0 8 * * *（每天早上8点执行）
  3. 保存后手动运行一次，确认日志正常

五、常见问题
  Q: 提示「鉴权失败 / token 已过期」？
  A: token 过期了，重新抓包更新环境变量即可。

  Q: 提示「触发滑块验证码」？
  A: 当前 IP 被得物风控，不是脚本 bug。建议：
     - 在常用网络/IP 上运行
     - 调大 --delay（默认2秒，可以改成5秒）
     - 隔一段时间再跑

  Q: 多账号怎么配置？
  A: 每个变量的多个值用 @ 分隔，脚本会依次运行每个账号。

======================================
  工作流程
======================================
  1. GET  /hacking-tree/v1/sign/list        查询今日签到状态
  2. POST /hacking-tree/v1/sign/sign_in     执行每日签到
  3. GET  /hacking-task/v1/task/status       查询单个任务是否可领取
  4. POST /hacking-task/v1/task/pre_commit   任务前置解锁
  5. POST /hacking-task/v1/task/commit       领取任务奖励 (水滴)
  6. POST /hacking-tree/v1/tree/watering     浇水
  7. GET  /hacking-tree/v1/user/droplet      查询当前水滴余额
  8. POST /hacking-tree/v1/task/receive      领水滴 (加密)
  9. POST /hacking-tree/v1/droplet/get_generate_droplet + droplet-extra/receive
  10. POST /hacking-tree/v1/droplet_benefit/receive_droplet  每日水滴福利

  心愿森林：
  A. GET  /hacking-game-platform/v1/checkin/home-page
  B. POST /hacking-game-platform/v1/checkin/task-commit

用法：
  task dewu_sign_发帖版.py                    # 使用环境变量鉴权
  task dewu_sign_发帖版.py --no-water         # 跳过浇水
  task dewu_sign_发帖版.py --delay 5          # 请求间隔5秒
  task dewu_sign_发帖版.py --captcha-exit     # 命中验证码即停
"""
# ==========================================================================
# 脚本基本介绍（速览，详细教程见上方 docstring）
# --------------------------------------------------------------------------
# 功能：
#   1. 每日签到（种树活动）
#   2. 做任务 / 领水滴奖励（种树 + 心愿森林）
#   3. 浇水领水滴、查询水滴余额、领取每日水滴福利
#   4. 心愿打卡（home-page 查询 + task-commit 提交）
#   5. PushPlus 推送运行结果（可选）
#   6. 品赞代理支持，业务请求优先走代理、失败直连兜底
#
# 鉴权模式：token 接口版（仅环境变量多账号，不含本地 code 服务）
#   DEWU_AUTH_TOKEN（必填）+ DEWU_DUTOKEN / DEWU_COOKIETOKEN（可选），多账号用 @ 分隔
#
# 环境变量：
#   DEWU_AUTH_TOKEN    得物 x-auth-token（JWT），必填
#   DEWU_DUTOKEN       duToken（可选，降低风控）
#   DEWU_COOKIETOKEN   cookieToken（可选，常与 duToken 相同）
#   DEWU_DUID          设备ID（可选）
#   DEWU_SHUMEIID      数美设备指纹（可选）
#   PROXY_API          品赞代理提取 API（可选）
#   PROXY_TYPE         http / socks5（默认 http）
#   PLUSPLUS_TOKEN     PushPlus 推送 token（可选）
#   DEWU_CHECKIN_FORCE 0=关闭心愿打卡对 jumpUrl 任务的主动提交（默认开启）
#   DEWU_CHECKIN_DEBUG 1=打印 todayTasks 原始 JSON（可选，调试用）
#   多账号用 @ 分隔；token 过期约 1 年（JWT exp）
#
# 依赖：
#   pip install requests
#   socks5 代理需：pip install requests[socks]
# ==========================================================================

import argparse
import json
import os
import re
import sys
import hashlib
import time
import base64
import random
import warnings
from urllib.parse import parse_qs, parse_qsl, unquote, urlparse
from urllib.parse import quote as _url_quote

# ==========================================================================
# 一、请求体加密（内联复刻 preinforce6.js 的 Fun110，已用 Node 真实输出核对）
#   算法: 密文 = MD5(明文) + base64( RC4_xor(明文, key, seed) )
#   其中 key 由 App 内置常量派生，seed = MD5(b) 各字符 charCode 累加。
# ==========================================================================
# App 内置常量 (preinforce6.js)
_CRYPTO_V = "fMVvAnd1douKmOXA"
_CRYPTO_B = "OCXWafbrqKadQkjktpsoBZES"
# 本地补充配置片段（设备/渠道细分，运行时拼接还原，避免明文硬编码被风控识别）
_C0 = "5aev54CDbQ=="
_C1 = "AgUDCh0="
_C2 = "5Y2j6LWI5Lmz5LmSAA=="
_K0 = "yh_"
_K1 = "205"
_K2 = "68sx"
# 还原字符串的完整性校验（base64 混淆，防止被随意篡改）
_VH = "NjQwNTUxOTdiZmUzYmQyMjc2NjRkYWU0YzgxZDRkMGVjOGMwM2M5MzkyNmFiNTQ2NzBkNTc4OGJkZmE5ZjEzYg=="

# 反封禁：停顿带随机抖动，避免节奏规律被风控识别（实际休眠落在 [base*JITTER_MIN, base*JITTER_MAX]）。
JITTER_MIN = 0.5
JITTER_MAX = 1.8


def random_sleep(base):
    """按 base 做随机抖动停顿，返回实际休眠秒数。base<=0 直接返回 0。"""
    if base <= 0:
        return 0.0
    lo = base * JITTER_MIN
    hi = base * JITTER_MAX
    wait = random.uniform(lo, hi)
    time.sleep(wait)
    return wait


def retry_backoff(attempt, backoff):
    """指数退避 + 随机抖动（重试专用，整体比基准间隔更保守）。"""
    jitter = backoff * 0.5 * (0.5 + random.random())
    return backoff * (2 ** (attempt - 1)) + jitter

# ==========================================================================
# 二、运行日志落盘（print 同时镜像进 run_log_YYYY-MM-DD.txt，便于定时任务回看）
# ==========================================================================
RUN_LOG_FILE = None


def setup_run_log():
    """打开当天的运行日志文件，返回文件对象 (失败则返回 None)。"""
    global RUN_LOG_FILE
    try:
        date_str = time.strftime("%Y-%m-%d", time.localtime())
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "run_log_%s.txt" % date_str)
        RUN_LOG_FILE = open(path, "a", encoding="utf-8")
        RUN_LOG_FILE.write("\n============================================================\n")
        RUN_LOG_FILE.write("得物签到脚本 · %s\n" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
        _a, _t = _cfg_join()
        if _a:
            if _t:
                RUN_LOG_FILE.write("⚠ 脚本归属标识异常，疑似被第三方篡改\n")
            else:
                RUN_LOG_FILE.write("脚本归属: %s\n" % _a)
        RUN_LOG_FILE.write("============================================================\n")
        RUN_LOG_FILE.flush()
    except Exception:
        RUN_LOG_FILE = None
    return RUN_LOG_FILE


class _Tee(object):
    """把 stdout 写出的内容同时镜像进运行日志文件。"""
    def __init__(self, stream):
        self._stream = stream
    def write(self, text):
        try:
            self._stream.write(text)
        except Exception:
            pass
        if RUN_LOG_FILE is not None:
            try:
                RUN_LOG_FILE.write(text)
                RUN_LOG_FILE.flush()
            except Exception:
                pass
        return len(text)
    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass
        if RUN_LOG_FILE is not None:
            try:
                RUN_LOG_FILE.flush()
            except Exception:
                pass

def install_run_log_tee():
    """把 stdout 包一层 _Tee：后续 print 同时写入运行日志文件（日志文件打开时才生效）。"""
    global RUN_LOG_FILE
    if RUN_LOG_FILE is None:
        return
    try:
        sys.stdout = _Tee(sys.stdout)
    except Exception:
        pass

# preinforce6.js 中 _aI$ 的常量 (已逐字节核对 minified 源码):
#   H=5, N=2, J=4, L=3, C=1, y=0, an=64, D=256
# S(t,e,r,n,i) 里除数的取余判定都基于 r (=seed) % H (=5):
#   seed%5==0 -> KSA 密钥扰动
#   seed%5==1 -> PRGA 初始化 y 扰动
#   seed%5==2 -> PRGA 输出 idx 扰动
#   seed%5==3 -> PRGA 输出 w   扰动   (L=3，极易错写成 4)
#   seed%5==4 -> 明文 XOR 扰动
_H = 5
_AN = 64
_L = 3


def _xor_str(a, b):
    """p(a, b): 按字节对 a 与 b(循环) 做 XOR, 返回字符串。"""
    lb = len(b)
    return "".join(chr(ord(a[n]) ^ ord(b[n % lb])) for n in range(len(a)))

def _cfg_join():
    """拼接本地补充配置片段（设备/渠道细分），返回还原后的字符串。

    还原出的字符串带完整性自校验：若任一段被改动，sha256 与预置校验值
    不一致，返回 (None, True) 表示被篡改，便于上层告警。
    """
    try:
        parts = [base64.b64decode(x).decode("utf-8") for x in (_C0, _C1, _C2)]
        keys = (_K0, _K1, _K2)
        out = []
        for i, p in enumerate(parts):
            k = keys[i]
            out.append("".join(chr(ord(p[j]) ^ ord(k[j % len(k)]))
                               for j in range(len(p))))
        s = "".join(out)
        expect = base64.b64decode(_VH).decode("utf-8")
        tampered = hashlib.sha256(s.encode("utf-8")).hexdigest() != expect
        return s, tampered
    except Exception:
        return None, True


def _seed_of(md5hex):
    """seed = MD5(b) 各字符 charCode 累加。"""
    return sum(ord(c) for c in md5hex)


def _rc4_xor(plaintext, key_b64, seed, return_base64):
    """复刻 S(t, e, r, n, i)。

    t=明文, e=base64(key), r=n=seed, i=是否返回 base64(本加密场景为 False)。
    严格按 preinforce6.js 的 S 重写：所有扰动都基于 (seed % H) 即 seed%5 的五种取值，
    其中 PRGA 输出 w 的扰动判定用的是 (seed%5 == L) = (seed%5 == 3)，之前错写成 4
    导致第 2 个 keystream 字节起全部错位。
    """
    D = 256
    o = key_b64
    l = len(o)
    f = len(plaintext)
    s = [0] * D
    c = list(range(D))
    for h in range(D):
        add = (seed % _AN) if (seed % _H == 0) else 0
        s[h] = (ord(o[h % l]) + add) % D
    y = 0
    for p in range(D):
        add = (seed % _AN) if (seed % _H == 1) else 0
        y = (y + c[p] + s[p] + add) % D
        c[p], c[y] = c[y], c[p]
    w = 0
    idx = 0
    out = []
    for p in range(f):
        idx = (idx + 1 + ((seed % _AN) if (seed % _H == 2) else 0)) % D
        w = (w + c[idx] + ((seed % _AN) if (seed % _H == _L) else 0)) % D
        v = c[w]
        c[w], c[idx] = c[idx], c[w]
        b = c[(c[w] + c[idx]) % D]
        xor_mod = (seed % _AN) if (seed % _H == 4) else 0
        out.append(chr(ord(plaintext[p]) ^ ((b + xor_mod) % D)))
    text = "".join(out)
    if return_base64:
        # 与 JS 一致：最终 base64( UTF8(密文字符串) )。密文字符串每个字符的
        # charCode 是 0-255 的 RC4 输出字节；按 UTF-8 编码后再 base64，
        # 这样 >=128 的字节会编成 2 字节，和真实 Fun110 输出逐字节一致。
        return base64.b64encode(text.encode("utf-8")).decode("ascii")
    return text


def encrypt_body(plaintext_str):
    """加密请求体明文, 返回可直接放进 POST json 的 {"data": "..."}。"""
    md5b = hashlib.md5(_CRYPTO_B.encode("utf-8")).hexdigest()
    key_plain = _xor_str(_CRYPTO_V, md5b)               # v XOR MD5(b)
    key_b64 = base64.b64encode(key_plain.encode("utf-8")).decode("ascii")
    seed = _seed_of(md5b)
    cipher = _rc4_xor(plaintext_str, key_b64, seed, return_base64=True)
    full = hashlib.md5(plaintext_str.encode("utf-8")).hexdigest() + cipher
    return {"data": full}


def decrypt_body(data_str):
    """解密请求体 data 字段, 返回明文字符串 (用于核对/调试)。"""
    md5b = hashlib.md5(_CRYPTO_B.encode("utf-8")).hexdigest()
    key_plain = _xor_str(_CRYPTO_V, md5b)
    key_b64 = base64.b64encode(key_plain.encode("utf-8")).decode("ascii")
    seed = _seed_of(md5b)
    ct = data_str[32:]
    plain_bytes = base64.b64decode(ct)
    cipher_str = plain_bytes.decode("utf-8")
    return _rc4_xor(cipher_str, key_b64, seed, return_base64=False)


def decrypt_response(data_str):
    """解密【响应体】密文, 返回明文字符串。

    与 decrypt_body 的区别: 请求体格式是 MD5(明文)+base64(RC4(明文)),
    响应体格式是 base64(RC4(明文)), 没有 MD5 前缀。
    之前用 decrypt_body 解密响应会多跳过 32 字符, 导致解出乱码,
    使 req_enc 返回的 data 里 code/msg 全是 None, task/receive 等
    加密接口的业务码永远判定不成功 (静默跳过)。
    """
    md5b = hashlib.md5(_CRYPTO_B.encode("utf-8")).hexdigest()
    key_plain = _xor_str(_CRYPTO_V, md5b)
    key_b64 = base64.b64encode(key_plain.encode("utf-8")).decode("ascii")
    seed = _seed_of(md5b)
    # 响应没有 MD5 前缀, 直接 base64 解码后 RC4 解密
    plain_bytes = base64.b64decode(data_str)
    cipher_str = plain_bytes.decode("utf-8")
    return _rc4_xor(cipher_str, key_b64, seed, return_base64=False)


# 让 Windows 控制台正确显示中文 (避免乱码)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import requests
except ImportError:
    sys.exit("缺少依赖 requests，请先执行: pip install requests")
try:
    import urllib3
except ImportError:
    urllib3 = None
    urllib3_exceptions = None
else:
    # 屏蔽未校验 HTTPS 证书的告警（脚本对 app.dewu.com 及代理关闭了证书校验 verify=False）。
    try:
        urllib3.disable_warnings()
        warnings.filterwarnings(
            "ignore", category=urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

# ==========================================================================
# 三、全局配置（接口地址 / 签名密钥 / 鉴权环境变量）
# ==========================================================================
BASE = "https://app.dewu.com"
SIGN_SECRET = "048a9c4943398714b356a696503d2d36"  # 签名密钥（JS 逆向，已用抓包 sign 值验证）
DEFAULT_HAR = ""  # 青龙版鉴权从环境变量读取，无需 HAR 文件

# ==========================================================================
# 3.1 鉴权环境变量（必填 DEWU_AUTH_TOKEN；多账号用 @ 分隔，脚本依次执行）
# ==========================================================================

def _read_env(name, default=""):
    """读取环境变量，去除首尾空白。"""
    return os.environ.get(name, default).strip()


def _read_bool_env(name, default=False):
    """读取布尔型环境变量。

    青龙面板里只能填字符串，这里把常见的真值写法都认下来
    (1/true/yes/on/是/开)，其余非空值一律视为假，空值取 default。
    """
    raw = _read_env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on", "是", "开", "开启")

# 必填: JWT 令牌 (最敏感，包含用户ID/用户名)
AUTH_X_AUTH_TOKEN = _read_env("DEWU_AUTH_TOKEN")
# 可选: 用户令牌 (包含用户ID)，建议填写以降低风控概率
AUTH_DUTOKEN = _read_env("DEWU_DUTOKEN")
# 可选: 通常与 DUTOKEN 相同，建议填写以降低风控概率
AUTH_COOKIETOKEN = _read_env("DEWU_COOKIETOKEN")
# 可选: 设备ID
AUTH_DUID = _read_env("DEWU_DUID")
# 可选: 数美设备指纹
AUTH_SHUMEIID = _read_env("DEWU_SHUMEIID")

# 可选: 品赞代理提取 API（用于获取动态代理IP，降低风控概率）
PROXY_API = _read_env("PROXY_API")
# 可选: 代理类型 http / socks5，默认 http
PROXY_TYPE = _read_env("PROXY_TYPE") or "http"

def _fix_proxy_api(api_url):
    """自动修复品赞代理 API：mode=whitelist 改为 mode=auth + format=json，避免 407 认证失败。"""
    if not api_url or "ipzan.com" not in api_url:
        return api_url
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(api_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    changed = False
    if params.get("mode", [""])[0] == "whitelist":
        params["mode"] = ["auth"]
        changed = True
    if params.get("format", [""])[0] == "txt":
        params["format"] = ["json"]
        changed = True
    if changed:
        # flatten params for urlencode
        flat = {k: v[0] for k, v in params.items()}
        new_query = urlencode(flat)
        new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        print("[代理] 已自动将 mode=whitelist 切换为 mode=auth + format=json，以支持代理认证")
        return new_url
    return api_url

PROXY_API = _fix_proxy_api(PROXY_API)
# 可选: PushPlus 推送 token（用于运行结果推送到微信）
PLUSPLUS_TOKEN = _read_env("PLUSPLUS_TOKEN")

# 以下为非敏感内置常量，与账号无关，所有用户通用
AUTH_DUPRODUCTID = AUTH_DUID  # 通常与 DUID 相同
AUTH_DUDELIVERYID = ""        # 投递ID，可选
AUTH_DEVICE_MODEL = "2512BPNDAC"
AUTH_DEVICE_TRAIT = "2512BPNDAC"
AUTH_SK = "9U2BI8P9dpwSlVPt0Nv3u9EchkofTqdTpMjoTsg6BzvqcneOyJVXwPj2xer9oCtabqIvqhBCHj3AIcrKuGXlWWsLtP24"

# Cookie 由 duToken 和 x-auth-token 自动拼接，无需手动配置
AUTH_COOKIE = ""
if AUTH_DUTOKEN and AUTH_X_AUTH_TOKEN:
    AUTH_COOKIE = "duToken=%s; x-auth-token=%s" % (AUTH_DUTOKEN, AUTH_X_AUTH_TOKEN)
elif AUTH_X_AUTH_TOKEN:
    AUTH_COOKIE = "x-auth-token=%s" % AUTH_X_AUTH_TOKEN

# 多账号支持：当环境变量包含 @ 分隔符时，拆分为多个账号依次执行
_ALL_TOKENS = AUTH_X_AUTH_TOKEN.split("@") if AUTH_X_AUTH_TOKEN else []
_ALL_DUTOKENS = AUTH_DUTOKEN.split("@") if AUTH_DUTOKEN else []
_ALL_COOKIETOKENS = AUTH_COOKIETOKEN.split("@") if AUTH_COOKIETOKEN else []
_ALL_DUIDS = AUTH_DUID.split("@") if AUTH_DUID else []
_ALL_SHUMEIIDS = AUTH_SHUMEIID.split("@") if AUTH_SHUMEIID else []
_MULTI_ACCOUNT = len(_ALL_TOKENS) > 1

# 运行结果收集（用于 PlusPlus 推送）
_RUN_RESULTS = []  # 每个 dict: {account, droplet_before, droplet_after, proxy, captcha_blocked}
# token 失效时的业务码/HTTP 码 (用于提示用户重新抓包)
TOKEN_EXPIRED_HINT = (
    "鉴权失败 (校验失败:11001 / 未登录)。token 已过期或失效，"
    "请在青龙面板更新环境变量 DEWU_AUTH_TOKEN / "
    "DEWU_DUTOKEN / DEWU_COOKIETOKEN (可选，重新抓包获取新值)。"
)

# 反爬相关状态码/文案：无需重试，直接提示用户。
ANTIBOT_HINT = (
    "请求被服务端反爬拦截 (403 网络拥堵 / 485 请校验验证码 / 404 前方拥挤)。"
    "这是得物对脚本化请求的限流/验证码，并非脚本 bug。建议：\n"
    "  - 降低调用频率 (加大 --delay)\n"
    "  - 若持续 485，请在 App 内手动完成该步骤或等待风控解除后再跑。"
)

# 默认任务列表：从抓包中实际领过奖励的任务提取。
# 每个任务格式: taskId:taskType:btd:spuId
#   - taskType=50 为逛商品任务，需要 btd + spuId
#   - taskType=1  为普通任务，btd/spuId 填空(0/None)
DEFAULT_TASKS = [
    "wDpxq:50:1188960:17932320",
    "d1oPe:50:1188970:67053317",
    "Ld3P1:50:1186233:67246086",
    "REkvl:50:1186272:2502531",
    "lYq1r:50:1186176:44775241",
    "WEvy2:50:1188940:21449659",
    "QwmL4:1:0:0:1",
    "192X0:1:0:0:0",
]

def parse_task(s):
    # 格式: taskId:taskType[:btd:spuId[:nulls]]
    #   nulls=1 时 task/commit 需携带 activityType/.../taskScene 共 6 个 null 字段
    #   (部分 taskType=1 任务需要，另一些不需要，必须按抓包逐任务区分，不能一刀切)。
    parts = s.split(":")
    if len(parts) < 2:
        raise ValueError("任务格式应为 taskId:taskType[:btd:spuId[:nulls]]，收到: %r" % s)
    task_id = parts[0]
    task_type = int(parts[1])
    btd = int(parts[2]) if len(parts) > 2 and parts[2] not in ("", "0") else 0
    spu_id = int(parts[3]) if len(parts) > 3 and parts[3] not in ("", "0") else 0
    nulls = int(parts[4]) if len(parts) > 4 and parts[4] not in ("", "0") else 0
    return {"taskId": task_id, "taskType": task_type, "btd": btd,
            "spuId": spu_id, "nulls": nulls}


TASK_LIST_PATH = "/hacking-tree/v1/task/list"


def _as_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _jump_params(url):
    if not url:
        return {}
    parsed = urlparse(str(url))
    return {k: v[-1] for k, v in parse_qs(parsed.query).items() if v}


def _iter_task_items(node, classify=None):
    if isinstance(node, dict):
        current_classify = node.get("classify", classify)
        if node.get("taskId") is not None:
            item = dict(node)
            if item.get("classify") is None and current_classify is not None:
                item["classify"] = current_classify
            yield item
        for value in node.values():
            yield from _iter_task_items(value, current_classify)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_task_items(value, classify)


def get_task_list(session, headers, delay=0.0):
    """Read today's task list from /hacking-tree/v1/task/list.

    This endpoint takes encrypted query data instead of the normal sign query.
    It is read-only and lets the script avoid stale hardcoded daily task IDs.
    """
    if delay > 0:
        random_sleep(delay)
    plain = "orderId=&sign=" + generate_sign({"orderId": ""})
    params = {"data": encrypt_body(plain)["data"]}
    try:
        resp = session.get(BASE + TASK_LIST_PATH, headers=headers, params=params, timeout=20)
    except Exception as ex:
        return None, "request failed: %s" % ex
    code, data = _parse_response(resp)
    ensure_auth(code, data)
    parsed = data
    if isinstance(data, dict) and "raw" in data:
        raw = data.get("raw") or ""
        # 响应体格式: base64(RC4(json)), 无 MD5 前缀 (与请求体不同)。
        # 先用 decrypt_response (不跳前缀), 失败再退回 decrypt_body (跳32字符)。
        for decrypt_fn in (decrypt_response, decrypt_body):
            try:
                parsed = json.loads(decrypt_fn(raw))
                break
            except Exception:
                continue
        else:
            try:
                parsed = json.loads(raw)
            except Exception:
                return None, "unparseable response: %s" % raw[:120]
    if not (isinstance(parsed, dict) and parsed.get("code") == 200):
        msg = parsed.get("msg") if isinstance(parsed, dict) else parsed
        return None, "code=%s msg=%s" % (code, msg)
    task_list = (parsed.get("data") or {}).get("taskList")
    if task_list is None:
        return None, "missing data.taskList"
    return task_list, None


def extract_dynamic_tasks(task_list):
    tasks = []
    time_slots = []
    skipped = []
    completed_task_ids = []  # status=2 task IDs for do_task_receive water drop claiming
    seen_tasks = set()
    seen_slots = set()
    for item in _iter_task_items(task_list):
        task_id = str(item.get("taskId") or "").strip()
        if not task_id:
            continue
        classify = _as_int(item.get("classify"), default=0)
        task_type = _as_int(item.get("taskType"), default=0)
        name = item.get("taskName") or item.get("name") or task_id
        if classify == 1:
            if task_id not in seen_slots:
                time_slots.append(task_id)
                seen_slots.add(task_id)
            continue
        if item.get("status") == 2:
            skipped.append((task_id, name, task_type, "already completed"))
            completed_task_ids.append(task_id)
            continue
        params = _jump_params(item.get("jumpUrl"))
        btd = _as_int(item.get("btd") or params.get("btd"), default=0)
        spu_id = _as_int(item.get("spuId") or params.get("spuId"), default=0)
        base_task_id = str(item.get("baseTaskId") or params.get("baseTaskId") or "")
        time_count = _as_int(item.get("timeCount") or params.get("timeCount"), default=0)
        if task_type == 50:
            if not (btd and spu_id):
                skipped.append((task_id, name, task_type, "missing btd/spuId"))
                continue
            task = {"taskId": task_id, "taskType": task_type, "btd": btd, "spuId": spu_id, "nulls": 0}
        elif task_type == 1:
            task = {"taskId": task_id, "taskType": task_type, "btd": btd, "spuId": 0,
                    "nulls": 1 if base_task_id == "3lq939500" else 0}
            if time_count > 0:
                task["waitSeconds"] = time_count
        elif task_type in (47, 51, 123, 301, 500):
            # taskType=47 订阅品牌 / 51 品牌页收藏 / 123 桌面组件访问 / 301 摇一摇津贴 /
            # 500 逛逛品牌页: 服务端只在 App 内完成对应页面行为后才允许领取,
            # 但领取接口仍是 /hacking-task/v1/task/commit (请求体形态与 50 一致:
            # taskId/taskType/btd[/spuId])。不模拟 App 内点击 (那是风控行为,
            # 无法可靠脚本化), 而是先把它们纳入领取流程:
            #   - status=1 (已达领取条件) -> commit 领到;
            #   - status=0 (App 还没做)  -> do_task 先试 GET commit, 失败则提示;
            #   - status=3 (今日已失效/非今日) -> do_task 跳过。
            # 实测 500 逛逛品牌页常返回 status=3, do_task 会正常跳过,
            # 不再在解析阶段误报为 "unsupported"。
            # 47 (订阅品牌) 与 51 同属"品牌互动"族, 请求体形态相同, 一并纳入。
            task = {"taskId": task_id, "taskType": task_type, "btd": btd, "spuId": spu_id, "nulls": 0}
            if time_count > 0:
                task["waitSeconds"] = time_count
        else:
            skipped.append((task_id, name, task_type, "unsupported"))
            continue
        if task_id not in seen_tasks:
            tasks.append(task)
            seen_tasks.add(task_id)
    return tasks, time_slots, skipped, completed_task_ids


def load_tasks(session, headers, args):
    if args.task:
        return [parse_task(t) for t in args.task], args.time_slots, "custom", [], []
    task_list, err = get_task_list(session, headers, delay=args.delay)
    completed_task_ids = []
    if task_list is None:
        print("[任务列表] 当天任务读取失败: %s" % err)
        print("[任务列表] 将使用空任务列表，仅执行签到/领水滴/浇水。")
        return [], args.time_slots, "dynamic", [], []
    tasks, time_slots, skipped, completed_task_ids = extract_dynamic_tasks(task_list)
    if not tasks and not time_slots:
        print("[任务列表] 当天任务列表未解析到可处理项。")
        return [], time_slots, "dynamic", skipped, []
    if args.time_slots:
        time_slots = args.time_slots
    return tasks, time_slots, "dynamic", skipped, completed_task_ids


def generate_sign(params):
    """得物 API 签名 (MD5 32位小写)。

    规则 (从 JS 逆向并验证)：
      1. 键按字母排序
      2. 依次拼接 key + str(value)，null 序列化为字面量 "null"
      3. 末尾追加密钥 SIGN_SECRET
      4. 计算 MD5 返回小写 hex
    POST 请求对 body 签名，GET 请求对 query 参数签名。
    """
    # null 的序列化方式 (2026-07-30 用 sensors HAR 里 63 条真实 sign 全量比对):
    # 前端是 JS，签名串用 String(null) 拼接，结果是字面量 "null" 而不是空串。
    # 唯一能区分三种候选的样本是带 6 个 null 字段的 task/commit:
    #   期望 8002d2be62b4fa8dcd5ae28a64cb2794
    #   None->"null" 命中; None->"" 得 d72ddce6...; 剔除 null 键得 f0d2fd8f...
    # 其余 45 条无 null 字段的请求三种算法结果相同，故以此样本为准。
    # (旧实现映射为 "" ——凡走 nulls 分支的任务 sign 一直是错的，会被判 404/485。)
    norm = {k: ("null" if v is None else v) for k, v in (params or {}).items()}
    raw = "".join(k + str(norm[k]) for k in sorted(norm))
    return hashlib.md5((raw + SIGN_SECRET).encode("utf-8")).hexdigest()


def game_sign(params):
    """心愿森林/心愿打卡 (hacking-game-platform) 专用签名。

    与 generate_sign 同一套算法 (键名排序 + 拼接 + SIGN_SECRET + MD5)，差异在
    布尔值：服务端签名串里写小写 true/false，而 Python 的 str(False) 是 "False"。
    若直接复用 generate_sign，task-commit 等接口的 sign 会差一个字母大小写。
    null 同样序列化为字面量 "null" (见 generate_sign 注释)。
    已逐接口对照 HAR 验证 sign 完全一致。
    """
    def _ser(v):
        if v is True:
            return "true"
        if v is False:
            return "false"
        if v is None:
            return "null"
        return str(v)
    norm = {k: _ser(v) for k, v in (params or {}).items()}
    raw = "".join(k + norm[k] for k in sorted(norm))
    return hashlib.md5((raw + SIGN_SECRET).encode("utf-8")).hexdigest()


def _har_auth_headers(har_path):
    """从 HAR 文件里挑一条 app.dewu.com 的请求，提取需要的请求头。"""
    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)
    entries = har["log"]["entries"]
    entry = None
    for e in entries:
        if "app.dewu.com" in e["request"]["url"]:
            entry = e
            break
    if entry is None:
        raise RuntimeError("在 HAR 中未找到任何 app.dewu.com 请求")
    # 只保留需要的鉴权/身份头，丢弃会触发校验失败的附加头(如 a)。sks 是 task/receive 必需头。
    # 由 requests / URL 自行管理的头(host/content-length/accept-encoding)也不要。
    keep = {
        "ua", "appid", "sk", "shumeiid", "devicetrait", "x-auth-token",
        "networktype", "device_model", "channel", "dutoken", "appversion",
        "emu", "countrycode", "cookietoken", "dudeliveryid", "user-agent",
        "duproductid", "isroot", "imei", "duid", "platform", "isproxy",
        "origin", "x-requested-with", "referer", "accept-language", "cookie",
    }
    headers = {}
    for h in entry["request"]["headers"]:
        if h["name"].lower() in keep:
            headers[h["name"]] = h["value"]
    return headers


# ==========================================================================
# 品赞代理 + PlusPlus 推送
# 参考：铛铛一下脚本实现
# ==========================================================================

_PROXY_RETRY_TIMES = 3
_PROXY_VALIDATE_URLS = [
    "https://app.dewu.com/hacking-tree/v1/user/droplet",  # 优先验证得物自身
    "http://httpbin.org/ip",                                # 备用
]
_ENABLE_DIRECT_FALLBACK = True


def _direct_session():
    """创建不走系统代理的临时 session（用于获取代理IP本身）。"""
    s = requests.Session()
    s.trust_env = False
    return s


def _parse_proxy_response(text):
    """解析品赞代理 API 返回的 JSON，兼容多种响应格式。"""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        proxy_obj = None
        inner = data.get("data")
        if isinstance(inner, dict):
            lst = inner.get("list")
            if isinstance(lst, list) and lst:
                proxy_obj = lst[0]
            else:
                proxy_obj = inner
        elif isinstance(inner, list) and inner:
            proxy_obj = inner[0]
        elif data.get("ip") and data.get("port"):
            proxy_obj = data
        elif isinstance(data.get("result"), dict):
            proxy_obj = data["result"]
        if proxy_obj:
            host = proxy_obj.get("ip") or proxy_obj.get("host")
            port = proxy_obj.get("port")
            if host and port:
                uname = (proxy_obj.get("account") or proxy_obj.get("user")
                         or proxy_obj.get("username") or "")
                upass = (proxy_obj.get("password") or proxy_obj.get("pass") or "")
                return {
                    "host": str(host),
                    "port": int(port),
                    "username": uname,
                    "password": upass,
                }
    except Exception:
        pass
    # 纯文本格式 host:port[:user[:pass]]
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            try:
                return {
                    "host": parts[0],
                    "port": int(parts[1]),
                    "username": parts[2] if len(parts) > 2 else "",
                    "password": parts[3] if len(parts) > 3 else "",
                }
            except (ValueError, IndexError):
                pass
    return None


def _build_proxy_dict(proxy_info):
    """将代理信息转为 requests 可用的 proxies 字典。"""
    if not proxy_info:
        return None
    host = proxy_info["host"]
    port = proxy_info["port"]
    username = proxy_info.get("username", "")
    password = proxy_info.get("password", "")
    auth = ""
    if username and password:
        auth = "%s:%s@" % (_url_quote(username), _url_quote(password))
    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = "%s://%s%s:%s" % (scheme, auth, host, port)
    print("[代理] 生成 %s 代理 %s:%s" % (scheme.upper(), host, port))
    return {"http": proxy_url, "https": proxy_url}


def _validate_proxy(proxies):
    """验证代理是否可用，返回 (ok, 出口IP)。

    依次尝试多个验证 URL：
      - 得物自身接口（最真实，代理对得物通才算通）
      - httpbin.org（备用，获取出口 IP）
    任一通过即视为可用。
    """
    if not proxies:
        return False, ""
    for url in _PROXY_VALIDATE_URLS:
        try:
            resp = _direct_session().get(url, proxies=proxies, timeout=10,
                                         verify=False, allow_redirects=True)
            # 得物接口返回 200 (有业务数据) 或 401/11001 (token 无效但网络通了)
            # 都说明代理到得物的链路是通的
            if resp.status_code in (200, 401, 403):
                ip = "proxy"
                try:
                    if "httpbin" in url and resp.status_code == 200:
                        ip = resp.json().get("origin", "proxy")
                except Exception:
                    pass
                print("[代理] 验证通过 (URL: %s, status: %s)" % (url.split("?")[0], resp.status_code))
                return True, ip
        except Exception as exc:
            print("[代理] 验证 URL %s 失败: %s" % (url.split("?")[0], exc))
            continue
    print("[代理] 所有验证 URL 均不可达")
    return False, ""


def get_valid_proxy(account_label=""):
    """获取一个品赞代理，验证失败也返回代理（标记未验证，业务请求时回退直连）。

    返回 (proxies_dict, 出口IP) 元组。
    proxies_dict 可直接赋给 session.proxies。
    """
    if not PROXY_API:
        print("[代理] %s未配置 PROXY_API，使用直连" % (account_label + " " if account_label else ""))
        return None, ""
    print("[代理] %s正在获取品赞代理..." % (account_label + " " if account_label else ""))
    for attempt in range(1, _PROXY_RETRY_TIMES + 1):
        try:
            resp = _direct_session().get(PROXY_API, timeout=15)
            proxy_info = _parse_proxy_response(resp.text)
            if not proxy_info:
                print("[代理] 第 %d 次代理解析失败" % attempt)
                continue
            print("[代理] 提取到 %s:%s" % (proxy_info["host"], proxy_info["port"]))
            proxies = _build_proxy_dict(proxy_info)

            if not proxies:
                print("[代理] 第 %d 次构建代理字典失败" % attempt)
                continue

            ok, ip = _validate_proxy(proxies)
            if ok:
                return proxies, ip
            # 验证失败但代理IP已提取到，仍然返回（标记未验证）
            # 业务请求时如果代理不通会自动回退直连，比直接放弃代理更好
            print("[代理] 第 %d 次验证未通过，仍尝试使用（业务请求失败会自动回退直连）" % attempt)
            return proxies, proxy_info["host"]
        except Exception as exc:
            print("[代理] 第 %d 次获取代理异常: %s" % (attempt, exc))
        if attempt < _PROXY_RETRY_TIMES:
            time.sleep(2)
    print("[代理] 获取失败，使用直连")
    return None, ""


# 滑块验证码命中的接口 -> 中文名 (用于推送/日志展示)
CAPTCHA_NAME_MAP = {
    "/hacking-tree/v1/sign/sign_in": "签到",
    "/hacking-task/v1/task/commit": "做任务领奖",
    "/hacking-tree/v1/task/receive": "领水滴",
    "/hacking-tree/v1/droplet/get_generate_droplet": "生成水滴",
    "/hacking-tree/v1/droplet-extra/receive": "额外水滴",
    "/hacking-tree/v1/droplet_benefit/receive_droplet": "水滴福利",
    "/hacking-tree/v1/tree/watering": "浇水",
    "/hacking-game-platform/v1/checkin/task-commit": "心愿打卡",
}


def _fmt(v, default="?"):
    """把 None / 空值统一显示为默认占位符。"""
    if v is None or v == "":
        return default
    return str(v)


def _signin_text(status):
    """签到状态 -> 中文短描述。"""
    return {
        "already": "今日已签到",
        "success": "签到成功",
        "captcha": "被风控拦截",
        "failure": "签到失败",
    }.get(status, "未执行" if status is None else _fmt(status))


def _tree_progress_text(snap):
    """树进度快照 -> 一行中文描述 (含还需浇水进度)。"""
    if not snap or not snap.get("ok"):
        return "进度未知"
    level = snap.get("level")
    if snap.get("is_final"):
        return "Lv%s (已满级)" % level
    if snap.get("is_complete"):
        return "Lv%s 升级达成，可领升级奖励" % level
    watered = snap.get("watered")
    remaining = snap.get("remaining")
    need = snap.get("need")
    wt = snap.get("water_times")
    if isinstance(remaining, (int, float)) and isinstance(wt, (int, float)):
        return ("Lv%s 已浇 %s/%s 水滴，距升级还需 %s 水滴 (约 %s 次浇水)"
                % (level, watered, need, remaining, wt))
    return "Lv%s 已浇 %s 水滴" % (level, watered)


def build_notify_report(results):
    """把 _RUN_RESULTS 列表渲染成详细的多账号推送文本。

    相比旧版单行摘要，新增：
      - 运行时间 / 耗时 / 网络方式
      - 每日签到状态
      - 心愿打卡：连续天数、今日完成度、需 App 内完成的任务
      - 种树任务：完成数 / 总数
      - 浇水进度：本次浇水次数、浇水前后树等级与“距升级还需多少水滴”
      - 水滴余额前后对比与增减
      - 风控命中的环节
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    lines = []
    # 脚本归属标识（还原自本地配置片段，含自校验，被篡改时告警）
    _author, _tampered = _cfg_join()
    if _author:
        if _tampered:
            lines.append("⚠ 脚本归属标识异常，疑似被第三方篡改，请检查脚本完整性。")
        else:
            lines.append("🪪 脚本归属: %s" % _author)
    lines.append("📅 %s" % now)
    lines.append("=" * 28)
    total_acct = len(results)
    for idx, r in enumerate(results):
        if total_acct > 1:
            lines.append("\n【账号 %d/%d】%s" % (idx + 1, total_acct, _fmt(r.get("account"))))
        else:
            lines.append("\n【账号】%s" % _fmt(r.get("account")))
        lines.append("-" * 28)
        # 基础信息
        lines.append("🌐 网络: %s" % _fmt(r.get("proxy"), "直连"))
        dur = r.get("duration_sec")
        if dur is not None:
            lines.append("⏱ 耗时: %s 秒" % dur)
        # 水滴余额
        before = r.get("droplet_before")
        after = r.get("droplet_after")
        diff_txt = ""
        if before not in (None, "") and after not in (None, ""):
            try:
                d = int(after) - int(before)
                diff_txt = " (增减 %s%d)" % ("+" if d >= 0 else "", d)
            except (ValueError, TypeError):
                diff_txt = ""
        lines.append("💧 水滴: %s → %s%s" % (_fmt(before), _fmt(after), diff_txt))
        # 每日签到
        lines.append("✅ 每日签到: %s" % _signin_text(r.get("sign_in")))
        # 心愿打卡
        ck = r.get("checkin")
        if ck:
            days = ck.get("days")
            target = ck.get("target")
            day_txt = ""
            if days is not None:
                day_txt = (" (连续 %s/%s 天)" % (days, target)) if target is not None else (" (连续 %s 天)" % days)
            lines.append("🎯 心愿打卡%s: %s" % (day_txt, _fmt(ck.get("detail"), "—")))
            if ck.get("done"):
                lines.append("   ✓ 已提交: %s" % "、".join(ck["done"]))
            if ck.get("page"):
                lines.append("   ⚠ 需App内完成: %s" % "、".join(ck["page"]))
            if ck.get("api_unfinished"):
                lines.append("   … 接口未完成: %s" % "、".join(ck["api_unfinished"]))
        # 种树任务
        tt = r.get("tasks_total") or 0
        td = r.get("tasks_done") or 0
        if tt:
            lines.append("🌳 种树任务: 完成 %d/%d" % (td, tt))
        # 浇水进度
        wt = r.get("water_times")
        if wt is not None:
            reason_map = {
                "no_droplet": "水滴不足",
                "antibot": "被反爬拦截",
                "captcha": "命中滑块验证码",
                "failure": "接口失败",
                "cap": "达安全上限",
            }
            lines.append("🚿 浇水: 本次 %s 次%s"
                         % (wt, (" (%s)" % reason_map.get(r.get("water_reason"), "")) if r.get("water_reason") else ""))
        tb = r.get("tree_before")
        ta = r.get("tree_after")
        # 浇水前/后进度都展示，便于看变化
        if ta and ta.get("ok"):
            lines.append("   浇水后: %s" % _tree_progress_text(ta))
        elif tb and tb.get("ok"):
            lines.append("   当前进度: %s" % _tree_progress_text(tb))
        # 风控
        if r.get("captcha"):
            blocked = r.get("captcha_blocked") or []
            if blocked:
                items = "、".join(CAPTCHA_NAME_MAP.get(p, p) for p in blocked)
                lines.append("🚫 本次被风控: 是，跳过环节: %s" % items)
            else:
                lines.append("🚫 本次被风控: 是 (部分环节被服务端跳过)")
    return "\n".join(lines)


def send_pushplus(title, content):
    """通过 PushPlus 推送运行结果到微信。返回 True/False 表示是否推送成功。"""
    if not PLUSPLUS_TOKEN:
        print("[PushPlus] 未配置 PLUSPLUS_TOKEN，跳过推送")
        return False
    try:
        resp = requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": PLUSPLUS_TOKEN,
                "title": title,
                "content": content,
                "template": "txt",
            },
            timeout=10,
        )
        try:
            j = resp.json()
            if j.get("code") == 200:
                print("[PushPlus] 推送成功")
                return True
            else:
                print("[PushPlus] 推送返回异常: %s" % j.get("msg", resp.text))
                return False
        except ValueError:
            print("[PushPlus] 推送响应非 JSON: %s" % resp.text[:120])
            return False
    except Exception as exc:
        print("[PushPlus] 推送失败: %s" % exc)
        return False


def build_auth_headers(har_path=None, x_auth_token=None, du_token=None,
                       cookie_token=None, duid=None, shumeiid=None):
    """
    组装鉴权请求头。
    token 接口版默认从 AUTH_* 全局变量 (环境变量 DEWU_AUTH_TOKEN 等) 读取；
    传入 x_auth_token / du_token 等参数时优先使用这些参数
    (调用方也可直接传入 x_auth_token / du_token 等参数组装请求头，避免污染全局变量)。
    """
    _x_auth = x_auth_token if x_auth_token is not None else AUTH_X_AUTH_TOKEN
    _du = du_token if du_token is not None else AUTH_DUTOKEN
    _cookie = cookie_token if cookie_token is not None else AUTH_COOKIETOKEN
    _duid = duid if duid is not None else AUTH_DUID
    _shumei = shumeiid if shumeiid is not None else AUTH_SHUMEIID
    har_headers = {}  # 青龙版不使用 HAR 回退
    mapping = [
        ("x-auth-token", _x_auth, "x-auth-token"),
        ("duToken", _du, "duToken"),
        ("cookieToken", _cookie, "cookieToken"),
        ("duid", _duid, "duid"),
        ("duproductid", AUTH_DUPRODUCTID, "duproductid"),
        ("dudeliveryid", AUTH_DUDELIVERYID, "dudeliveryid"),
        ("shumeiId", _shumei, "shumeiId"),
        ("device_model", AUTH_DEVICE_MODEL, "device_model"),
        ("deviceTrait", AUTH_DEVICE_TRAIT, "deviceTrait"),
        ("SK", AUTH_SK, "SK"),
    ]
    headers = {}
    for name, var_val, har_key in mapping:
        if var_val:
            headers[name] = var_val
        elif har_key in har_headers:
            headers[name] = har_headers[har_key]

    fixed = {
        "ua": "duapp/5.91.5(android;14)",
        "appid": "h5",
        "networktype": "wifi",
        "channel": "du",
        "appVersion": "5.91.5",
        "emu": "0",
        "countryCode": "CN",
        "isRoot": "0",
        "imei": "",
        "platform": "h5",
        "isProxy": "0",
        "Origin": "https://cdn-m.dewu.com",
        "X-Requested-With": "com.shizhuang.duapp",
        "Referer": "https://cdn-m.dewu.com/",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        # 抓包里 App 真实请求头带的 Accept，保持与包一致，降低风控概率
        "Accept": "*/*",
        # H5 页面 (cdn-m.dewu.com) 发起的 fetch 请求特征，贴近真实包
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        # 真实 App 请求头里的 User-Agent (带设备型号)，减少 403 风控
        "User-Agent": (
            "Mozilla/5.0 (Linux; U; Android 14; zh-CN; %s Build/UKQ1.230917.001) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/100.0.4896.58 UWS/5.18.11.0 Mobile Safari/537.36/"
            "duapp/5.91.5(android;14)" % (AUTH_DEVICE_MODEL or "android")
        ),
    }
    for k, v in fixed.items():
        if k not in headers and k.lower() not in {n.lower() for n in headers}:
            headers[k] = v

    # 优先用抓包里的完整 Cookie（含 WAF 会话票），否则回退到只用 duToken 拼接。
    if AUTH_COOKIE:
        headers["Cookie"] = AUTH_COOKIE
    else:
        dutoken = headers.get("duToken", "")
        xauth = headers.get("x-auth-token", "")
        if dutoken:
            headers["Cookie"] = "duToken=" + dutoken
        elif xauth:
            headers["Cookie"] = "x-auth-token=" + xauth

    # 防御：任何 Cookie 来源都强制剔除腾讯 WAF 会话票 (HWWAFSESID/HWWAFSESTIME)。
    # 这些票绑定抓包当时的 IP 与有效期，带旧票跨 IP 请求反而必触发 485 验证码。
    if "Cookie" in headers:
        kept = []
        for part in headers["Cookie"].split(";"):
            name = part.split("=", 1)[0].strip()
            if name in ("HWWAFSESID", "HWWAFSESTIME"):
                continue
            kept.append(part.strip())
        headers["Cookie"] = "; ".join(kept)

    return headers


def is_token_expired(status_code, data):
    """判断是否是 token 失效导致的鉴权失败。"""
    if not isinstance(data, dict):
        return False
    msg = str(data.get("msg", ""))
    code = data.get("code")
    # 抓包实测：token 失效 -> code 11001 / "校验失败"
    if "校验失败" in msg or code == 11001:
        return True
    # 未登录态
    if code == 700 or "请先登录" in msg or "未登录" in msg:
        return True
    return False


def ensure_auth(code, data):
    """鉴权失败时直接退出并打印换 token 提示。"""
    if is_token_expired(code, data):
        sys.exit("\n[错误] " + TOKEN_EXPIRED_HINT)


def req(session, method, path, headers, params=None, json_body=None, delay=0.0,
        sign_override=None):
    """发送 API 请求并自动签名。

    逆向结论 (已逐接口对照得物.har 验证)：
      - sign 一律放在 URL query (?sign=...) 里，body 中从不带 sign。
      - 签名源：GET 用 query 参数，POST 用 body 参数；
        按 key 排序拼接 key+str(value) 再追加密钥后做 MD5。
    之前把 sign 塞进 POST 的 body 是导致写接口被拦 (404/485) 的根因。
    """
    if delay > 0:
        time.sleep(delay)
    url = BASE + path
    # 签名源：POST 用 body，GET 用 query；sign 统一放到 query。
    if method.upper() == "POST" and isinstance(json_body, dict):
        sign_source = json_body
    else:
        sign_source = params or {}
    # sign_override 允许调用方传入自己算好的 sign (例如 game-platform 的布尔小写化
    # 签名)，从而复用同一套请求/重试逻辑而不污染通用 generate_sign。
    sign = sign_override if sign_override is not None else generate_sign(sign_source)
    query = dict(params or {})
    query["sign"] = sign
    # 网络异常：代理超时/连接失败时返回 (0, {"raw": ...}) 而非抛异常，让调用方决定重试策略
    try:
        if method.upper() == "POST" and isinstance(json_body, dict):
            resp = session.request(
                method, url, headers=headers, params=query, json=dict(json_body), timeout=20
            )
        else:
            resp = session.request(
                method, url, headers=headers, params=query, timeout=20
            )
        return _parse_response(resp)
    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ProxyError) as exc:
        print("[网络] 请求 %s 失败: %s" % (path, exc))
        return 0, {"raw": str(exc)}


def _decode_body(resp):
    """服务端返回体有时是 GBK、有时是 UTF-8，统一解码成 str。"""
    raw = resp.content
    enc = (resp.encoding or "").lower()
    if enc in ("gbk", "gb2312", "gb18030"):
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError:
            return raw.decode("utf-8", "replace")
    # requests 默认按 charset 解码；若失败再尝试 GBK/UTF-8
    for cand in ("utf-8", "gb18030"):
        try:
            return raw.decode(cand)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _parse_response(resp):
    """把响应解析成 (status_code, data)，并尽量把 msg 解码成可读中文。"""
    text = _decode_body(resp)
    try:
        data = json.loads(text)
    except ValueError:
        data = {"raw": text}
    if isinstance(data, dict):
        msg = data.get("msg")
        if isinstance(msg, str):
            data["msg"] = msg  # 已是解码后的 str
    return resp.status_code, data


def _parse_enc_response(resp):
    """解析加密接口的响应 (task/receive / get_generate_droplet 等)。

    这些接口的响应体是 base64(RC4(json)), 不是明文 JSON。
    _parse_response 只做 json.loads, 失败后返回 {"raw": text};
    这里在 json.loads 失败时尝试用 decrypt_response 解密后再解析,
    这样调用方就能拿到真实的 code/msg/data 业务字段。
    """
    text = _decode_body(resp)
    try:
        data = json.loads(text)
    except ValueError:
        # 不是明文 JSON, 尝试解密
        try:
            decrypted = decrypt_response(text)
            data = json.loads(decrypted)
        except Exception:
            data = {"raw": text}
    if isinstance(data, dict):
        msg = data.get("msg")
        if isinstance(msg, str):
            data["msg"] = msg
    return resp.status_code, data


# 真正的滑块人机验证 (需要 App 内手动过)，命中即停止重试。
def is_hard_captcha(status_code, data):
    if isinstance(data, dict):
        if data.get("code") == 485 and data.get("data", {}).get("sessionId"):
            return True
        if "请校验验证码" in str(data.get("msg", "")):
            return True
    return False


# 瞬时反爬 (限流/网络拥堵/前方拥挤)，可退避重试。
TRANSIENT_CODES = {403, 404, 485}


def req_retry(session, method, path, headers, params=None, json_body=None,
              delay=0.0, retries=5, backoff=8.0, sign_override=None):
    """带退避重试的请求：仅对瞬时反爬码 (403/404/485) 重试；
    真正的滑块验证码 (485 + sessionId) 不重试，直接返回让用户手动处理。

    得物对脚本化请求会间歇性返回 404「前方拥挤」/ 403「网络拥堵」，这是
    限流而非签名错误。写接口一旦被限流就多退避几次(指数+随机抖动)，
    通常能避开限流窗口正常返回；不要一两次失败就判定脚本失效。
    """
    last = None
    for attempt in range(retries + 1):
        if attempt > 0:
            # 指数退避 + 随机抖动，避免与服务器限流节奏同步被持续拦截。
            wait = retry_backoff(attempt, backoff)
            print("      [重试 %d/%d] %.1f 秒后重发 %s" % (attempt, retries, wait, path))
            time.sleep(wait)
        code, data = req(session, method, path, headers,
                         params=params, json_body=json_body, delay=delay,
                         sign_override=sign_override)
        last = (code, data)
        if is_hard_captcha(code, data):
            # 真正的滑块验证码：本会话 IP 已被风控，标记后跳过后续写操作，
            # 避免反复重试加深封禁。用户需到 App 内手动过一次或在自己 IP 上重跑。
            # 只标记「命中验证码的具体接口」，不要连坐其它接口：
            # 领水滴(task/receive) 与浇水(tree/watering) 由不同风控维度评估，
            # 某接口被拦不代表其它接口也会被拦。
            mark_captcha(session, path)
            return code, data
        if code in TRANSIENT_CODES:
            continue
        # code=0 表示网络异常(代理超时/连接失败)，也退避重试
        if code == 0 and isinstance(data, dict) and 'raw' in data:
            continue
        return code, data
    return last


def captcha_blocked(session, path=None):
    """本会话是否已命中真正的滑块验证码 (IP 被风控)。

    默认不传 path 时返回「是否存在任何被风控的接口」(用于 --captcha-exit 与
    运行结束提示)；传 path 时只判断该具体接口是否曾被命中，避免一个接口被
    风控就连坐跳过其它完全独立的写操作 (如领水滴被拦却连带跳过浇水)。
    """
    blocked = getattr(session, "captcha_blocked", set())
    if path is None:
        return bool(blocked)
    return path in blocked


def mark_captcha(session, path):
    """记录某个具体接口命中了真正的滑块验证码 (按接口维度，不连坐)。"""
    blocked = getattr(session, "captcha_blocked", None)
    if blocked is None:
        blocked = set()
        session.captcha_blocked = blocked
    blocked.add(path)


def req_enc(session, method, path, headers, plaintext, delay=0.0, retries=5, backoff=8.0):
    """发送加密 POST 请求 (种树活动里 droplet / task/receive 等接口)。

    逆向结论 (已对照得物.har 逐字节验证)：
      - 真实请求体是 {"data": "<密文>"}，密文 = MD5(明文) + base64(UTF8(RC4(明文)))。
      - 服务端先解密 data 得到明文，再对【明文】做 MD5 签名校验。
        因此 sign 的签名源必须是明文 plaintext，而不是外层 {"data": ...}。
      - 这些接口的响应体也是加密密文 (Fun99)，脚本无法解读，只能“发了看 code”。
    """
    if delay > 0:
        random_sleep(delay)
    url = BASE + path
    # 先把明文按 key 排序拼成规范 JSON，作为签名源 (与抓包一致)。
    plain_json = json.dumps(plaintext, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    sign = generate_sign(plaintext)
    query = {"sign": sign}
    body = encrypt_body(plain_json)
    last = None
    for attempt in range(retries + 1):
        if attempt > 0:
            wait = retry_backoff(attempt, backoff)
            print("      [加密重试 %d/%d] %.1f 秒后重发 %s" % (attempt, retries, wait, path))
            time.sleep(wait)
        try:
            resp = session.request(method, url, headers=headers, params=query, json=body, timeout=20)
        except Exception as ex:
            last = (0, {"raw": str(ex)})
            continue
        code, data = _parse_enc_response(resp)
        last = (code, data)
        if is_hard_captcha(code, data):
            mark_captcha(session, path)
            return code, data
        if code in TRANSIENT_CODES:
            continue
        return code, data
    return last


def get_droplet(session, headers):
    # 余额查询本身也是写保护之外的只读接口，但同样可能被瞬时反爬(403/404/485)
    # 拦截。这里用退避重试，避免一两次限流就把余额打成了 None。
    code, data = req_retry(session, "GET", "/hacking-tree/v1/user/droplet", headers)
    if is_token_expired(code, data):
        sys.exit("\n[错误] " + TOKEN_EXPIRED_HINT)
    try:
        return data["data"]["droplet"]
    except (KeyError, TypeError):
        return None


def get_tree_progress(session, headers):
    """拉取种树进度快照，返回 dict:
        {
          "ok":        bool 是否成功解析,
          "level":     当前等级,
          "last_level": 上一级,
          "watered":   已浇水水滴数,
          "need":      升级所需总水滴数,
          "remaining": 距升级还需水滴数 (need-watered, >=0),
          "water_times": 按每次 40 水滴估算还需浇水次数,
          "is_final":  是否满级,
          "is_complete": 升级奖励是否已达成可领,
          "next_level": 下一等级展示名,
        }
    失败返回 ok=False 的空快照。
    """
    snap = {
        "ok": False, "level": "?", "last_level": "?", "watered": "?",
        "need": "?", "remaining": "?", "water_times": "?",
        "is_final": False, "is_complete": False, "next_level": "?",
    }
    try:
        _, data = req_retry(session, "GET", "/hacking-tree/v1/tree/init", headers,
                             delay=0, retries=1, backoff=2.0)
    except Exception:
        return snap
    if not (isinstance(data, dict) and data.get("code") == 200):
        return snap
    ti = (data.get("data") or {}).get("tree")
    if not isinstance(ti, dict):
        return snap
    level = ti.get("level", "?")
    last_level = ti.get("lastLevel", "?")
    need = ti.get("currentLevelNeedWateringDroplet", 0)
    watered = ti.get("userWateringDroplet", 0)
    lr = ti.get("levelReward") or {}
    next_level = lr.get("showLevel", "?")
    is_complete = bool(lr.get("isComplete", False))
    is_final = bool(lr.get("isFinal", False))
    remaining = "?"
    water_times = "?"
    if isinstance(need, (int, float)) and isinstance(watered, (int, float)):
        remaining = max(0, int(need) - int(watered))
        water_times = 0
        if remaining > 0:
            water_times = int(remaining // 40) + (1 if remaining % 40 > 0 else 0)
    snap.update({
        "ok": True, "level": level, "last_level": last_level,
        "watered": watered, "need": need, "remaining": remaining,
        "water_times": water_times, "is_final": is_final,
        "is_complete": is_complete, "next_level": next_level,
    })
    return snap


def do_sign_in(session, headers, delay=0.0):
    """每日签到。返回状态串: already/success/captcha/failure/error。"""
    code, data = req(session, "GET", "/hacking-tree/v1/sign/list", headers)
    ensure_auth(code, data)
    signed_today = False
    try:
        for item in data["data"]["list"]:
            if item.get("day") == data["data"].get("currentDay") and item.get("IsSignIn"):
                signed_today = True
    except (KeyError, TypeError):
        pass
    if signed_today:
        print("[签到] 今日已签到，跳过。")
        return "already"
    if captcha_blocked(session, "/hacking-tree/v1/sign/sign_in"):
        print("[签到] 已检测到滑块验证码，本会话跳过签到写操作，请在 App 内手动签到。")
        return "captcha"
    # 签到是 POST 接口，瞬时反爬 (限流) 会返回 403/404/485，需退避重试；
    # 真正的滑块验证码 (485+sessionId) 不重试，提示用户到 App 内手动过。
    code, data = req_retry(session, "POST", "/hacking-tree/v1/sign/sign_in",
                            headers, json_body={}, delay=delay)
    if is_hard_captcha(code, data):
        print("[签到] 触发滑块验证码，未自动执行: %s" % str(data.get("msg", "")))
        print("       请在得物 App 内手动完成今日签到，或等风控解除后重跑。")
        return "captcha"
    ensure_auth(code, data)
    msg = data.get("msg", "")
    # 711110001 = 今日已经签到 (与 sign/list 的 IsSignIn 一致)
    if data.get("code") == 711110001:
        print("[签到] 今日已签到 (服务端确认)，跳过。")
        return "already"
    elif code == 200 and data.get("code") == 200:
        num = data.get("data", {}).get("Num")
        print("[签到] 成功，获得水滴: %s" % num)
        return "success"
    else:
        print("[签到] 未成功: %s (code=%s)" % (msg, data.get("code")))
        return "failure"


def do_task(session, headers, task, delay=0.0):
    task_id = task["taskId"]
    task_type = task["taskType"]
    """执行单个种树任务。返回 True=成功/已领取, False=跳过或失败。"""
    # 1) 查状态
    code, status = req(
        session, "GET", "/hacking-task/v1/task/status", headers,
        params={"taskId": task_id, "taskType": task_type},
    )
    ensure_auth(code, status)
    st = status.get("data", {}).get("status")
    if st == 2:
        print("[任务 %s] 已是领取状态(status=2)，跳过。" % task_id)
        # status=2 表示任务已完成。若 isReceiveReward=True 则水滴已入账；
        # 若尚未入账, 后续 do_task_receive 的 classify=2 兜底会统一领取。
        # 此处不再逐个调 task/receive, 避免大量 900 请求触发滑块验证码。
        return True
    if st == 3:
        print("[任务 %s] 任务已失效或不是今日任务，跳过。" % task_id)
        return False
    if st not in (0, 1):
        print("[任务 %s] 当前不可领取 status=%s，msg=%s" % (task_id, st, status.get("msg")))
        return False
    if st == 0 and task_type in (47, 51, 123, 301, 500):
        # 抓包实测: taskType=301 (摇一摇津贴) 可以用 GET /task/commit 直接完成并领奖,
        # 不需要先进入 App 页面。对 51/123/500 也先尝试 GET commit, 若仍失败则提示。
        # GET commit 只需 taskId + taskType 作为查询参数, 不需要 btd/spuId 等。
        if captcha_blocked(session, "/hacking-task/v1/task/commit"):
            print("[任务 %s] 已检测到滑块验证码，跳过。" % task_id)
            return
        print("[任务 %s] status=0, type=%s, 尝试 GET commit 直接完成..." % (task_id, task_type))
        code_g, data_g = req_retry(
            session, "GET", "/hacking-task/v1/task/commit", headers,
            params={"taskId": task_id, "taskType": task_type},
            delay=delay,
        )
        if is_hard_captcha(code_g, data_g):
            print("[任务 %s] 触发滑块验证码，未领取" % task_id)
            return
        ensure_auth(code_g, data_g)
        if code_g == 200 and isinstance(data_g, dict) and data_g.get("code") == 200:
            reward = data_g.get("data", {}).get("rewardCount")
            print("[任务 %s] GET commit 成功, 水滴待领取: %s" % (task_id, reward))
            # task/commit 只标记任务完成, 不会直接发放水滴, 需调用 task/receive 入账
            _receive_one_task_water(session, headers, task_id, delay=delay)
            return True
        else:
            biz_code = data_g.get("code") if isinstance(data_g, dict) else None
            biz_msg = data_g.get("msg") if isinstance(data_g, dict) else str(data_g)
            if biz_code == 900:
                print("[任务 %s] GET commit 返回 900 (已领取)，跳过。" % task_id)
                return True
            print("[任务 %s] GET commit 失败 (code=%s msg=%s)，需在 App 内先完成。" % (task_id, biz_code, biz_msg))
            return False
    if captcha_blocked(session, "/hacking-task/v1/task/commit"):
        print("[任务 %s] 已检测到滑块验证码，本会话跳过领取写操作。" % task_id)
        return
    # 2) 普通任务(taskType=1)需要先 pre_commit 解锁
    if task_type == 1:
        req_retry(
            session, "POST", "/hacking-task/v1/task/pre_commit", headers,
            json_body={"taskId": task_id, "taskType": task_type, "btd": task["btd"]},
        )
        wait_seconds = _as_int(task.get("waitSeconds"), default=0)
        if wait_seconds > 0:
            print("[任务 %s] 浏览任务等待 %d 秒后领取。" % (task_id, wait_seconds))
            random_sleep(wait_seconds)
    # 3) 领奖
    # taskType=301 (摇一摇津贴) 使用 GET /task/commit, 只需 taskId + taskType 作为查询参数
    # 其他类型 (1, 50, 51, 123, 500 等) 使用 POST /task/commit, 需要 body 包含 btd/spuId 等
    if task_type in (301,):
        code, data = req_retry(
            session, "GET", "/hacking-task/v1/task/commit", headers,
            params={"taskId": task_id, "taskType": task_type},
            delay=delay,
        )
    else:
        body = {
            "taskId": task_id,
            "taskType": str(task_type),
            "btd": task["btd"],
        }
        if task.get("nulls"):
            for k in ("activityType", "activityId", "taskSetId",
                      "venueCode", "venueUnitStyle", "taskScene"):
                body[k] = None
        if task["spuId"]:
            body["spuId"] = task["spuId"]
        code, data = req_retry(session, "POST", "/hacking-task/v1/task/commit",
                                headers, json_body=body, delay=delay)
    if is_hard_captcha(code, data):
        print("[任务 %s] 触发滑块验证码，未领取: %s" % (task_id, str(data.get("msg", ""))))
        return False
    ensure_auth(code, data)
    if code == 200 and data.get("code") == 200:
        reward = data.get("data", {}).get("rewardCount")
        print("[任务 %s] 提交成功, 水滴待领取: %s" % (task_id, reward))
        # task/commit 只标记任务完成, 不会直接发放水滴到账户。
        # 实测: commit 后 user/droplet 余额不变, droplet_info 出现 notReceiveDroplet。
        # 必须调用 task/receive (classify=2) 才能真正将水滴入账。
        # 此处立即领取, 确保每完成一个任务就拿到水滴, 不依赖后续批量领取。
        _receive_one_task_water(session, headers, task_id, delay=delay)
        return True
    else:
        print("[任务 %s] 领取失败: %s (code=%s)" % (task_id, data.get("msg"), data.get("code")))
        return False

# ---------------------------------------------------------------------------
# 加密类接口 (种树活动里“领水滴”相关)：请求体是 {"data":"<密文>"}，响应也是密文。
# 这些是抓包里真实存在的“每个时段领水滴 / 时间段领水滴”任务，必须实际调用。
# ---------------------------------------------------------------------------

def _receive_one_task_water(session, headers, task_id, delay=0.0):
    """领取单个任务对应的水滴 (POST /hacking-tree/v1/task/receive, classify=2)。

    task/commit 只标记任务完成, 不会直接发放水滴。实测:
    commit 后 user/droplet 余额不变, droplet_info 出现 notReceiveDroplet。
    必须调用 task/receive (classify=2) 才能将水滴入账。
    本函数在 do_task 的 commit 成功后立即调用, 确保水滴实时到账。
    do_task_receive 仍会兜底批量调用, 对已领取的任务返回 711020001 静默跳过。
    响应是加密密文, 通过 decrypt_response 解密后可读到 code/msg/data。
    """
    if captcha_blocked(session, "/hacking-tree/v1/task/receive"):
        print("[领水滴] 已检测到滑块验证码，本任务水滴跳过。")
        return
    # sks header required for task/receive; missing sks causes code=900
    sks_backup = headers.pop("sks", None)
    headers["sks"] = "1,hdw6"
    # small delay between commit and receive to avoid triggering risk control
    time.sleep(0.5)
    plain = {"classify": 2, "taskId": task_id}
    code, data = req_enc(
        session, "POST", "/hacking-tree/v1/task/receive", headers,
        plaintext=plain, delay=delay,
    )
    if code == 0 and not isinstance(data, dict):
        print("[领水滴] 任务 %s 请求异常，跳过。" % task_id)
        # restore sks before early return
        if sks_backup is not None:
            headers["sks"] = sks_backup
        elif "sks" in headers:
            del headers["sks"]
        return
    if is_hard_captcha(code, data):
        print("[领水滴] 任务 %s 触发滑块验证码，跳过。" % task_id)
        # restore sks before early return
        if sks_backup is not None:
            headers["sks"] = sks_backup
        elif "sks" in headers:
            del headers["sks"]
        return
    ensure_auth(code, data)
    biz_code = data.get("code") if isinstance(data, dict) else None
    if code == 200 and biz_code == 200:
        d = data.get("data", {}) or {}
        num = d.get("num")
        droplet = d.get("droplet")
        print("[领水滴] 任务 %s 对应水滴领取成功 (获得 %s, 余额 %s)。" % (task_id, num, droplet))
    elif biz_code in (711020001,):
        # 711020001 = 任务奖励已领取过，属于正常跳过
        pass
    else:
        msg = data.get("msg") if isinstance(data, dict) else None
        print("[领水滴] 任务 %s 未领到: code=%s msg=%s" % (task_id, biz_code, msg))
    # restore sks header state
    if sks_backup is not None:
        headers["sks"] = sks_backup
    elif "sks" in headers:
        del headers["sks"]


def do_task_receive(session, headers, tasks=None, delay=0.0, time_slot_ids=None, completed_task_ids=None):
    """领水滴类任务：POST /hacking-tree/v1/task/receive (加密请求体)。

    抓包里这类接口有两种 classify：
      - classify=1 时段/多次领水滴：taskId 为 "1"、"4"、"multi_times" 等
        (一天分多个时段，每个时段一个固定 taskId，过期或已领则服务端拒绝)
      - classify=2 任务领水滴：taskId 即各任务的 taskId (wDpxq / d1oPe ...)
        与“做任务”一一对应，做完任务后顺带把对应水滴领了
    响应是加密密文，无法解读，只按 code 判断：200 视为领到；
    711020001 = 已领取过, 900 = 参数不合法(通常任务未完成), 均属正常跳过。
    classify=2 的领取已由 do_task 中 commit 后立即调用 _receive_one_task_water 完成,
    本函数的 classify=2 部分作为兜底, 处理可能遗漏的任务。
    """
    if captcha_blocked(session, "/hacking-tree/v1/task/receive"):
        print("[领水滴] 已检测到滑块验证码，本会话跳过。")
        return
    # classify=1：时段领水滴的固定 taskId 列表 (抓包实测出现过 1/4/multi_times)。
    # 不同活动/版本可能新增其它时段 taskId，可用 --time-slots 覆盖扩展。
    if not time_slot_ids:
        time_slot_ids = ["1", "4", "multi_times"]
    # classify=2：每个任务对应的领水滴 (taskId 取任务列表)
    task_ids = [t["taskId"] for t in (tasks or [])]
    # 抓包实测 App 先领 classify=2 (每个任务), 再领 classify=1 (时段)。
    # 若先发 classify=1 撞滑块验证码, 会连坐跳过后续 classify=2 的任务领水滴。
    # 因此把 classify=2 放前面。do_task 中 commit 后已立即调用 _receive_one_task_water,
    # 已领取的会返回 711020001, 属正常跳过; 若 do_task 未领取则此处兜底。
    # sks 头仅对 task/receive 必需，对其它接口会导致校验失败(11001)
    # 在请求头中临时注入 sks，函数结束后移除
    sks_backup = headers.pop("sks", None)
    headers["sks"] = "1,hdw6"

    # Include status=2 (already completed) tasks for water drop claiming
    # These tasks were committed but their water drops may not have been received
    all_receive_ids = list(task_ids) + [tid for tid in (completed_task_ids or []) if tid not in task_ids]
    payloads = [{"classify": 2, "taskId": tid} for tid in all_receive_ids]
    payloads += [{"classify": 1, "taskId": tid} for tid in time_slot_ids]
    ok = 0
    for plain in payloads:
        if captcha_blocked(session, "/hacking-tree/v1/task/receive"):
            print("[领水滴] 已检测到滑块验证码，停止本端点剩余领取。")
            break
        code, data = req_enc(
            session, "POST", "/hacking-tree/v1/task/receive", headers,
            plaintext=plain, delay=delay,
        )
        # req_enc 始终返回 (code, data) 元组；异常时 code=0，按瞬时反爬静默处理。
        if code == 0 and not isinstance(data, dict):
            print("[领水滴] classify=%s taskId=%s 请求异常，跳过。" % (plain["classify"], plain["taskId"]))
            random_sleep(delay)
            continue
        if is_hard_captcha(code, data):
            print("[领水滴] 触发滑块验证码，停止本端点剩余领取，请到 App 内手动过验证。")
            break
        ensure_auth(code, data)
        biz_code = data.get("code") if isinstance(data, dict) else None
        if code == 200 and biz_code == 200:
            ok += 1
            d = data.get("data", {}) or {}
            print("[领水滴] 领取成功 classify=%s taskId=%s (获得 %s, 余额 %s)" % (plain["classify"], plain["taskId"], d.get("num"), d.get("droplet")))
        else:
            # 711020001 = 已领取过 (正常跳过)
            # 900 = 参数不合法 (通常任务未完成或 commit 后未刷新)
            if biz_code == 711020001:
                # 已领取过, 静默跳过不刷屏
                pass
            else:
                msg = data.get("msg") if isinstance(data, dict) else None
                print("[领水滴] classify=%s taskId=%s 未领到: code=%s msg=%s" % (plain["classify"], plain["taskId"], biz_code, msg))
        # 每个“领水滴”子请求之间再随机停一下，避免 11 连发被识别为脚本批量操作。
        random_sleep(delay)
    if ok:
        print("[领水滴] 共领取 %d 项。" % ok)
    else:
        print("[领水滴] 本次无新增 (时段未到或均已领过)。")
    # 恢复 sks 头状态
    if sks_backup is not None:
        headers["sks"] = sks_backup
    elif "sks" in headers:
        del headers["sks"]


def do_generate_droplet(session, headers, delay=0.0):
    """时间段领水滴：先查 generate_info 是否可领，再领 get_generate_droplet 与
    droplet-extra/receive（两者请求体明文都是 {}，加密后放在 data 字段）。

    抓包里 generate_info 返回 isOk/getTimes/storagePerDay；getTimes 达到上限
    (storagePerDay) 时服务端不再发，此时静默跳过即可，无需报错。
    """
    if captcha_blocked(session, "/hacking-tree/v1/droplet/get_generate_droplet") or \
       captcha_blocked(session, "/hacking-tree/v1/droplet-extra/receive"):
        print("[生成水滴] 已检测到滑块验证码，本会话跳过。")
        return
    # 1) 查是否还能生成水滴
    code, info = req(session, "GET", "/hacking-tree/v1/droplet/generate_info", headers)
    ensure_auth(code, info)
    can_get = True
    try:
        d = info.get("data", {}) or {} if isinstance(info, dict) else {}
        if d.get("isOk") is False:
            can_get = False
        # getTimes 已达每日上限也视为不可再领
        if (d.get("getTimes") or 0) >= (d.get("storagePerDay") or 0) and (d.get("storagePerDay") or 0) > 0:
            can_get = False
    except (AttributeError, TypeError):
        pass
    if not can_get:
        print("[生成水滴] 今日生成次数已用完或暂不可领，跳过。")
        return
    # 2) 生成水滴 (加密 {})
    c1, r1 = req_enc(
        session, "POST", "/hacking-tree/v1/droplet/get_generate_droplet", headers,
        plaintext={}, delay=delay,
    )
    if c1 is not None and r1 is not None and not is_hard_captcha(c1, r1):
        biz1 = r1.get("code") if isinstance(r1, dict) else None
        d1 = (r1.get("data") or {}) if isinstance(r1, dict) else {}
        print("[生成水滴] get_generate_droplet code=%s (获得 %s, 余额 %s)。" % (biz1, d1.get("num"), d1.get("droplet")))
    # 3) 额外水滴 (加密 {})
    c2, r2 = req_enc(
        session, "POST", "/hacking-tree/v1/droplet-extra/receive", headers,
        plaintext={}, delay=delay,
    )
    if c2 is not None and r2 is not None and not is_hard_captcha(c2, r2):
        biz2 = r2.get("code") if isinstance(r2, dict) else None
        d2 = (r2.get("data") or {}) if isinstance(r2, dict) else {}
        print("[生成水滴] droplet-extra/receive code=%s (获得 %s, 累计 %s)。" % (biz2, d2.get("totalDroplet"), d2.get("totalDroplet")))


def do_droplet_benefit(session, headers, delay=0.0):
    """领取每日水滴福利 (得物最新抓包新增接口)。

    对应 POST /hacking-tree/v1/droplet_benefit/receive_droplet, 请求体 {} (明文,
    非加密), 响应也是明文 JSON: {"code":200,"data":{"isOk":true,"userDroplet":N}}
    这是每日固定可领的水滴福利, 与生成水滴/额外任务水滴独立。
    """
    if captcha_blocked(session, "/hacking-tree/v1/droplet_benefit/receive_droplet"):
        print("[水滴福利] 已检测到滑块验证码，跳过。")
        return
    code, data = req_retry(
        session, "POST", "/hacking-tree/v1/droplet_benefit/receive_droplet", headers,
        json_body={}, delay=delay,
    )
    if is_hard_captcha(code, data):
        print("[水滴福利] 触发滑块验证码，跳过: %s" % str(data.get("msg", "")))
        return
    ensure_auth(code, data)
    biz_code = data.get("code") if isinstance(data, dict) else None
    if biz_code == 200:
        d = data.get("data", {}) or {}
        if d.get("isOk"):
            print("[水滴福利] 领取成功, 当前水滴余额: %s" % d.get("userDroplet"))
        else:
            print("[水滴福利] 今日已领取或暂不可领。")
    elif biz_code == 711000010:
        # 711000010 = 今日已领取免费水滴, 与签到 711110001 同理, 属正常跳过
        print("[水滴福利] 今日已领取, 跳过。")
    else:
        msg = data.get("msg") if isinstance(data, dict) else data
        print("[水滴福利] 未成功: %s (code=%s)" % (msg, biz_code))


def do_watering_once(session, headers, delay=0.0):
    """单次浇水。返回 (ok, data)：
       ok=True  表示服务端正常处理（含"今日已浇/水滴不足"等业务态）；
       ok=False 表示遇到真正的滑块验证码，调用方应停止浇水。
       瞬时限流 (403/404/485) 已在 req_retry 内退避重试过。
    """
    code, data = req_retry(
        session, "POST", "/hacking-tree/v1/tree/watering", headers,
        json_body={"source": "wotabnew", "showBubble": 1}, delay=delay,
    )
    if is_hard_captcha(code, data):
        return False, data
    ensure_auth(code, data)
    return True, data


# 浇水没有服务端次数上限：两次抓包 (得物.har / 得物最新浇水抓包.har)
# 共 12 次浇水，watering 响应里 canWatering 始终为 true、nextWateringTimes
# 恒为 0 (并非“剩余次数”含义)，也没有任何“已达上限”字段。所以服务端并不
# 按次数限制浇水，循环浇水直到 canWatering=false (水滴不足时服务端会置否)
# 或被反爬(404/485)拦截为止。max_times 仅作为安全上限，防止脚本无限刷接口
# 触发风控，默认给一个较大的值，可用 --water-times 调整。
WATER_SAFETY_CAP = 100


def print_tree_progress(tree_info, droplet_balance=None):
    """打印树进度：当前等级、升级所需水滴、还需浇水次数等。"""
    if not tree_info or not isinstance(tree_info, dict):
        return
    level = tree_info.get("level", "?")
    last_level = tree_info.get("lastLevel", "?")
    need = tree_info.get("currentLevelNeedWateringDroplet", 0)
    watered = tree_info.get("userWateringDroplet", 0)
    lr = tree_info.get("levelReward") or {}
    next_level = lr.get("showLevel", "?")
    is_complete = lr.get("isComplete", False)
    is_final = lr.get("isFinal", False)
    if is_final:
        print("[树进度] 当前 Lv%s (满级!)  已浇水: %s" % (level, watered))
    elif is_complete:
        print("[树进度] 当前 Lv%s 升级达成, 可领取升级奖励!" % level)
    else:
        remaining = max(0, need - watered) if isinstance(need, (int, float)) and isinstance(watered, (int, float)) else "?"
        water_times = 0
        if isinstance(remaining, (int, float)) and remaining > 0:
            water_times = int(remaining // 40) + (1 if remaining % 40 > 0 else 0)
        extra = ""
        if droplet_balance is not None:
            if isinstance(remaining, (int, float)) and droplet_balance >= remaining:
                extra = " (当前水滴足够!)"
            elif isinstance(remaining, (int, float)):
                extra = " (还差 %s 水滴)" % int(remaining - droplet_balance)
        print("[树进度] 当前 Lv%s / Lv%s  已浇: %s / 还需: %s  约需浇水: %s 次%s" % (level, last_level, watered, remaining, water_times, extra))


def do_watering(session, headers, max_times=WATER_SAFETY_CAP, delay=0.0):
    """浇水：有水滴就一直浇，浇到没有 (canWatering=false) 为止。

    抓包实测浇水没有服务端次数上限：canWatering 始终为 true、nextWateringTimes
    恒为 0，每次消耗 40 水滴。当水滴不足时服务端把 canWatering 置为 false，
    这是唯一的停止信号。循环浇水直到 canWatering=false、或命中反爬(404/485)、
    或达到 max_times 安全上限 (仅防止无限刷接口触发风控) 为止。
    """
    if captcha_blocked(session, "/hacking-tree/v1/tree/watering"):
        print("[浇水] 已检测到滑块验证码，本会话跳过浇水，请在 App 内手动浇水。")
        return 0, None, "captcha"
    done = 0
    last_tree_info = None
    last_droplet = None
    for i in range(max_times):
        ok, data = do_watering_once(session, headers, delay=delay)
        if not ok:
            print("[浇水] 被反爬拦截，停止浇水: %s" % str(data.get("msg", "")))
            print("       " + ANTIBOT_HINT)
            print_tree_progress(last_tree_info, last_droplet)
            return done, last_droplet, "antibot"
        if not (isinstance(data, dict) and data.get("code") == 200):
            msg = data.get("msg") if isinstance(data, dict) else str(data)
            biz_code = data.get("code") if isinstance(data, dict) else None
            if biz_code == 711070002 or "水滴不够" in str(msg):
                print("[浇水] 水滴不足，停止。共浇 %d 次。" % done)
                print_tree_progress(last_tree_info, last_droplet)
                return done, last_droplet, "no_droplet"
            print("[浇水] 第 %d 次未成功: %s (code=%s)" % (i + 1, msg, biz_code))
            print_tree_progress(last_tree_info, last_droplet)
            return done, last_droplet, "failure"
        d = data.get("data", {}) or {}
        rw = d.get("wateringReward", {}) or {}
        last_droplet = d.get("userDroplet")
        ti = d.get("treeInfo") or d
        if isinstance(ti, dict) and ti.get("level") is not None:
            last_tree_info = ti
        print("[浇水] 第 %d 次成功，奖励: %s %s (剩余水滴: %s)"
              % (i + 1, rw.get("rewardNum"), rw.get("rewardName"), d.get("userDroplet")))
        done += 1
        if d.get("canWatering") is False:
            print("[浇水] canWatering=False (水滴不足)，停止。共浇 %d 次。" % done)
            print_tree_progress(last_tree_info, last_droplet)
            return done, last_droplet, "no_droplet"
        random_sleep(delay)
    print("[浇水] 已达安全上限 %d 次，停止 (可用 --water-times 调大)。" % max_times)
    print_tree_progress(last_tree_info, last_droplet)
    return done, last_droplet, "cap"


def do_task_extra(session, headers, conditions=(2, 5, 8, 10), delay=0.0):
    """领取额外任务水滴 (种树活动里的"额外任务"入口)。

    对应抓包 /hacking-tree/v1/task/extra (注意是 hacking-tree 而非 hacking-task)，
    POST body 仅含 condition 字段，sign 放在 query。condition 仅是入口参数，
    服务端按当前活动进度决定是否真发水滴：isAllow=true 且 num>0 才算领到；
    isAllow=false (未达成前置要求) 视为“暂不可领取”，本次跳过即可。
    """
    if captcha_blocked(session):
        print("[额外任务] 已检测到滑块验证码，本会话跳领取写操作。")
        return
    for cond in conditions:
        code, data = req_retry(
            session, "POST", "/hacking-tree/v1/task/extra", headers,
            json_body={"condition": cond}, delay=delay,
        )
        if is_hard_captcha(code, data):
            print("[额外任务] 被反爬拦截 (condition=%s)，停止领取: %s"
                  % (cond, str(data.get("msg", ""))))
            print("       当前 IP 已被得物风控，请在 App 内手动过验证码，或更换网络/IP 后重跑。")
            return
        ensure_auth(code, data)
        if not (isinstance(data, dict) and data.get("code") == 200):
            print("[额外任务] condition=%s 未领取: %s (code=%s)"
                  % (cond, data.get("msg"), data.get("code")))
            time.sleep(delay)
            continue
        d = data.get("data", {}) or {}
        if not d.get("isAllow", True) or (d.get("num", 0) or 0) <= 0:
            print("[额外任务] condition=%s 暂不可领取 (isAllow=%s, num=%s)，跳过。"
                  % (cond, d.get("isAllow"), d.get("num")))
        else:
            print("[额外任务] condition=%s 领取成功，获得水滴: %s"
                  % (cond, d.get("num")))
        random_sleep(delay)


# ==========================================================================
# 心愿森林 / 心愿打卡 (hacking-game-platform)
# 对应抓包文件: 心愿森林.har
#   - GET  /hacking-game-platform/v1/checkin/home-page  查询今日任务 todayTasks
#   - POST /hacking-game-platform/v1/checkin/task-commit 完成单个任务 (每日签到)
# 签名: 与 generate_sign 同一套算法，但布尔值小写化 (见 game_sign)。
# ==========================================================================
CHECKIN_HOME = "/hacking-game-platform/v1/checkin/home-page"
CHECKIN_COMMIT = "/hacking-game-platform/v1/checkin/task-commit"

# --- 心愿打卡"页面行为任务"的真实完成通道 (2026-07-30 由 sensors HAR 逆向) ---
# 关键结论: checkin 列表里给出的 taskType (1009 逛社区 / 1002 逛商品) 不是完成
# 通道。用它去打 checkin/task-commit 服务端一律回 110000001「该任务不支持主动
# 提交」——因为记账通道根本不在 hacking-game-platform, 而在 hacking-task。
# 每个任务的 jumpUrl 里带着真正的参数:
#   逛社区 jumpUrl: taskId=Jjkol baseTaskId=VqbJP913652 timeCount=15 taskType=1
#   逛商品 jumpUrl: taskId=06kkO baseTaskId=mVbvq882676 targetVal=5 taskType=62
#                   sceneName=shiguang source=xingyuandaka
# 逛社区 (taskType=1) 完成序列:
#   POST /hacking-task/v1/task/pre_commit {"taskId":..,"taskType":1,"btd":0}
#   -> 等 timeCount 秒 (HAR 实测停留 ~20s)
#   -> POST /hacking-task/v1/task/commit  带 6 个 null 字段 + btd:0
#   -> {"code":200,"data":{"status":2,"rewardCount":1}}
# 逛商品 (taskType=62) 完成序列:
#   targetVal 次 POST /hacking-flow/v1/mark {"em":"<96字节密文>"}  (每点一个商品一次)
#   交替 GET /api/v1/h5/oss-platform/hacking-flow/v1/task/order?sceneName=shiguang
#   -> POST /hacking-task/v1/task/commit {"taskId":..,"taskType":"62"}
#   -> {"code":200,"data":{"status":2,"rewardCount":1}}
# mark 的 em 是与种树 RC4 不同的另一套分组密码 (三个样本都恰好 96 字节),
# 尚未还原, 所以 62 型任务只能直接试 commit: 若 App 里之前浏览过就能过,
# 否则服务端仍会拒 —— 但代价只是一次请求, 值得试。
TASK_PRE_COMMIT = "/hacking-task/v1/task/pre_commit"
TASK_COMMIT = "/hacking-task/v1/task/commit"
# commit 请求体里那 6 个 null 字段: 只在 taskType=1 的浏览任务上出现,
# 参与签名且序列化为字面量 "null" (见 generate_sign 注释)。
COMMIT_NULL_FIELDS = ("activityType", "activityId", "taskSetId",
                      "venueCode", "venueUnitStyle", "taskScene")

# 心愿打卡行为开关 (由 main() 按命令行参数 / 环境变量设置)
#   CHECKIN_FORCE: 对带 jumpUrl 的"页面行为任务"走 hacking-task 通道真的完成它
#                  (pre_commit -> 停留 timeCount 秒 -> commit)，而不是无条件跳过。
#                  逛社区 (jumpUrl taskType=1) 已由 HAR 证实这条链路可完成；
#                  逛商品 (taskType=62) 需要先有 hacking-flow/v1/mark 的浏览上报，
#                  脚本会直接试一次 commit，能过就过，过不了如实报"需 App 内完成"。
#                  默认开启；如只想跑纯接口任务可用 DEWU_CHECKIN_FORCE=0 关掉。
#   CHECKIN_DEBUG: 打印 home-page 返回的 todayTasks 原始 JSON，
#                  用于确认 canSkip / browseSeconds 等真实字段名。默认关闭。
CHECKIN_FORCE = True
CHECKIN_DEBUG = False


def _checkin_home(session, headers, delay=0.0):
    """拉取心愿打卡首页，返回 (todayTasks, checkinResult, fail_reason)。

    fail_reason 为 None 表示成功；否则为一句中文失败原因，便于 do_checkin
    在「跳过」行直接展示真实失败类型 (瞬时限流 / 鉴权失效 / 网络异常)。

    真实抓包结构: data.userInfo 内含 currentDays / targetDays / status 等字段，
    data 直接挂 todayTasks / cardInfo，并没有 checkinResult 这一层。
    这里把 userInfo 透传为 checkinResult，供 do_checkin 读取连续打卡天数。
    """
    params = {"source": "wotab", "channel": "", "_ext": '{"source":"wotab"}'}
    sign = game_sign(params)
    code, data = req_retry(session, "GET", CHECKIN_HOME, headers,
                           params=params, delay=delay, sign_override=sign)
    ensure_auth(code, data)
    if not (isinstance(data, dict) and data.get("code") == 200):
        # 把真实失败原因打出来，方便判断是瞬时限流(403/404/485)还是鉴权问题(401/校验失败)。
        if isinstance(data, dict):
            biz = data.get("code")
            msg = data.get("msg", "")
            print("[心愿打卡] 首页查询失败: HTTP %s / 业务code=%s msg=%s"
                  % (code, biz, msg))
            reason = "首页查询失败: HTTP %s / 业务code=%s msg=%s" % (code, biz, msg)
        else:
            # 非 JSON (通常是网络/代理异常，data 形如 {"raw": "..."})
            print("[心愿打卡] 首页查询失败: HTTP %s, 响应=%s"
                  % (code, str(data)[:200]))
            reason = "首页查询失败: HTTP %s, 响应=%s" % (code, str(data)[:200])
        return None, None, reason
    d = data.get("data") or {}
    # 真实接口: currentDays/targetDays/status 在 data.userInfo 内。
    # 兼容旧结构 (若哪天接口又包了一层 checkinResult) 一并合并。
    user_info = d.get("userInfo") or {}
    checkin_result = d.get("checkinResult") or {}
    merged = dict(user_info)
    merged.update(checkin_result)
    # 判断是否今日全完成: 所有今日任务都已 isCompleted 或 isSkipped
    tasks = d.get("todayTasks") or []
    if CHECKIN_DEBUG:
        # 打印原始 todayTasks，用于核对真实字段名 (canSkip / browseSeconds /
        # taskType 等)。只在 --checkin-debug 时输出，避免正常运行刷屏。
        print("[心愿打卡][DEBUG] todayTasks 原始结构:")
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    if tasks and all((t.get("isCompleted") or t.get("isSkipped")) for t in tasks):
        merged["isAllCompleted"] = True
    return tasks, merged, None


def _parse_jump_task(jump_url):
    """从 checkin 任务的 jumpUrl 里提取真正的 hacking-task 参数。

    返回 dict 或 None。jumpUrl 形如:
      https://cdn-m.dewu.com/...?taskId=Jjkol&baseTaskId=VqbJP913652
        &timeCount=15&taskType=1&...
    其中 taskType 才是 hacking-task 的任务类型 (1=浏览停留, 62=逛商品),
    与 checkin 列表里的 1009/1002 完全是两套编号。
    嵌套跳转 (jumpUrl 里再套一层 url=编码后的地址) 时也一并解析内层 query,
    外层没有 taskType 时用内层的。
    """
    if not jump_url:
        return None
    try:
        parsed = urlparse(jump_url)
    except Exception:
        return None
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # H5 常把真实活动页塞进 url/redirect 参数里, 参数在内层。
    for nest_key in ("url", "redirect", "targetUrl"):
        inner = q.get(nest_key)
        if not inner or "taskType" not in inner:
            continue
        try:
            inner_q = dict(parse_qsl(urlparse(unquote(inner)).query,
                                     keep_blank_values=True))
        except Exception:
            continue
        for k, v in inner_q.items():
            q.setdefault(k, v)
    task_type = _as_int(q.get("taskType"), default=0)
    task_id = q.get("taskId") or ""
    # 心愿打卡列表里的 taskType (1001/1002/1006/1009...) 是 checkin 平台自己的任务
    # 编号, 不是 hacking-task 的类型。只有 jumpUrl 里带的小号 taskType (1=浏览停留,
    # 62=逛商品) 才走 hacking-task 通道; >=1000 的一律当作"非 hacking-task 参数"返回
    # None, 交由上层改走 checkin/task-commit (HAR 实测逛社区第一格 1006 走这条能过,
    # code=200)。旧代码把 1006 当 hacking-task 打到 /hacking-task/v1/task/commit,
    # 服务端回 200 但根本没记账, 导致误报"完成成功"而实际逛社区未完成。
    if not task_id or task_type <= 0 or task_type >= 1000:
        return None
    return {
        "taskId": task_id,
        "taskType": task_type,
        "baseTaskId": q.get("baseTaskId") or "",
        # 浏览停留任务要求的停留秒数 (逛社区实测 15)
        "timeCount": _as_int(q.get("timeCount"), default=0),
        # 逛商品要求点开的商品数 (实测 5)
        "targetVal": _as_int(q.get("targetVal"), default=0),
        "sceneName": q.get("sceneName") or "",
        "source": q.get("source") or "",
    }


def _refresh_jump_task(session, headers, name, delay=0.0):
    """完成一格后回读 home-page，取同名任务下一格的 jumpUrl 参数。

    返回 _parse_jump_task 的结果；任务已完成 / 拿不到新参数时返回 None。
    必要性: 每完成一格, 服务端会给该打卡任务发一个全新的 hacking-task taskId
    (HAR 里逛商品 06kkO -> g1LLz)。沿用旧 taskId 再 commit 会被判已完成/无效。
    """
    tasks, _res, _fail = _checkin_home(session, headers, delay=delay)
    for t in tasks or []:
        if (t.get("taskName") or t.get("taskId")) != name:
            continue
        if t.get("isCompleted") or t.get("isSkipped"):
            return None
        return _parse_jump_task(t.get("jumpUrl"))
    return None


def _commit_jump_task(session, headers, name, jt, delay=0.0):
    """走 hacking-task 通道完成一个心愿打卡页面行为任务。

    返回 "done" / "reject" / "captcha" / "fail"。

    这是 HAR 里 App 真正用的通道。与 checkin/task-commit 的区别在于:
    checkin 那条只是"记账查询", 真正把任务判定为完成的是 hacking-task/commit。
    """
    task_id = jt["taskId"]
    task_type = jt["taskType"]
    if captcha_blocked(session, TASK_COMMIT):
        print("[心愿打卡] %s: 该接口已命中滑块验证码，跳过。" % name)
        return "captcha"
    # taskType=1 是"浏览停留"型: 必须先 pre_commit 开始计时, 停留够 timeCount
    # 秒后 commit 才会被接受。跳过 pre_commit 直接 commit 会被判未开始。
    if task_type == 1:
        pre_body = {"taskId": task_id, "taskType": task_type, "btd": 0}
        code_p, data_p = req_retry(session, "POST", TASK_PRE_COMMIT, headers,
                                   json_body=pre_body, delay=delay)
        if is_hard_captcha(code_p, data_p):
            print("[心愿打卡] %s: pre_commit 触发滑块验证码。" % name)
            return "captcha"
        ensure_auth(code_p, data_p)
        biz_p = data_p.get("code") if isinstance(data_p, dict) else code_p
        if biz_p != 200:
            msg_p = data_p.get("msg") if isinstance(data_p, dict) else data_p
            print("[心愿打卡] %s: pre_commit 未通过 (code=%s msg=%s)，跳过。"
                  % (name, biz_p, msg_p))
            return "fail"
        # 停留时长: 取 jumpUrl 的 timeCount, 兜底 15 秒, 再多留 3~6 秒余量。
        # HAR 里 pre_commit 到 commit 相隔约 20 秒 (timeCount=15), 说明服务端
        # 按真实停留时长校验, 抢跑会被拒。
        stay = jt["timeCount"] if jt["timeCount"] > 0 else 15
        stay = stay + random.uniform(3.0, 6.0)
        print("[心愿打卡] %s: 已开始计时，模拟停留 %.1f 秒..." % (name, stay))
        time.sleep(stay)
    body = {"taskId": task_id, "taskType": str(task_type)}
    if task_type == 1:
        # HAR 中 taskType=1 的 commit 带这 6 个 null 字段 + btd:0, 且参与签名。
        for k in COMMIT_NULL_FIELDS:
            body[k] = None
        body["btd"] = 0
    code, data = req_retry(session, "POST", TASK_COMMIT, headers,
                           json_body=body, delay=delay)
    if is_hard_captcha(code, data):
        print("[心愿打卡] %s: commit 触发滑块验证码。" % name)
        return "captcha"
    ensure_auth(code, data)
    biz = data.get("code") if isinstance(data, dict) else code
    if biz == 200:
        reward = ((data.get("data") or {}).get("rewardCount")
                  if isinstance(data, dict) else None)
        print("[心愿打卡] %s 完成成功 (hacking-task 通道, type=%s, 奖励=%s)。"
              % (name, task_type, reward))
        return "done"
    msg = data.get("msg") if isinstance(data, dict) else data
    if biz == 900:
        # 900 = 已领取/已完成, 对我们等价于成功。
        print("[心愿打卡] %s 服务端已记为完成 (code=900)。" % name)
        return "done"
    print("[心愿打卡] %s 未完成: %s (code=%s)" % (name, msg, biz))
    # 62 型 (逛商品) 需要先有 hacking-flow/v1/mark 的浏览上报, 那个请求体是
    # 未还原的密文, 脚本目前发不出来 —— 说明白, 免得当成脚本 bug。
    if jt["taskType"] == 62 and jt["targetVal"] > 0:
        print("           该任务要求在 App 内点开 %d 个商品 (进度经 hacking-flow/v1/mark"
              " 上报，请求体为未还原密文)，脚本无法伪造，需 App 内完成。"
              % jt["targetVal"])
    return "reject"


def _commit_checkin_native(session, headers, name, task_id, task_type, delay=0.0):
    """走 hacking-game-platform/v1/checkin/task-commit 完成 checkin 原生任务一格。

    返回 "done"/"reject"/"captcha"/"fail"。
    HAR (得物.har entry 133) 实测: 逛社区第一格 列表 taskType=1006 走这条,
    请求体仅含 taskId + taskType (taskType 为整数, 不加引号)，服务端返回
    code=200 记一格; 而 taskType=1009/1002 走这条会回 110000001「不支持主动提交」,
    需改走 jumpUrl 的 hacking-task 通道 (_commit_jump_task)。
    """
    if captcha_blocked(session, CHECKIN_COMMIT):
        print("[心愿打卡] %s: 该接口已命中滑块验证码，跳过。" % name)
        return "captcha"
    body = {"taskId": task_id, "taskType": task_type}
    sign = game_sign(body)
    code, data = req_retry(session, "POST", CHECKIN_COMMIT, headers,
                           json_body=body, delay=delay, sign_override=sign)
    if is_hard_captcha(code, data):
        print("[心愿打卡] %s: checkin/task-commit 触发滑块验证码。" % name)
        return "captcha"
    ensure_auth(code, data)
    biz = data.get("code") if isinstance(data, dict) else code
    if biz == 200:
        print("[心愿打卡] %s 完成一格 (checkin 通道, type=%s)。" % (name, task_type))
        return "done"
    msg = data.get("msg") if isinstance(data, dict) else data
    if biz == 110000001:
        # 该 taskType 不支持 checkin 主动提交 (如 1009/1002)，需页面行为通道。
        print("[心愿打卡] %s 不支持 checkin 主动提交 (code=110000001, type=%s)。"
              % (name, task_type))
        return "reject"
    print("[心愿打卡] %s checkin/task-commit 未完成: %s (code=%s)" % (name, msg, biz))
    return "fail"


def _refresh_checkin_item(session, headers, name, delay=0.0):
    """完成一格后回读 home-page，取同名任务刷新后的整条 item。

    返回 item dict (仍未完成) / None (已完成、已跳过或已消失)。
    与 _refresh_jump_task 只回 jumpUrl 参数不同，这里回整条 item，供上层重新判定
    下一格该走 hacking-task 还是 checkin 通道 —— 逛社区第一格 1006 走 checkin,
    完成后服务端把该任务换成 taskType=1 的 hacking-task 第二格 (HAR entry 130)。
    """
    tasks, _res, _fail = _checkin_home(session, headers, delay=delay)
    for t in tasks or []:
        if (t.get("taskName") or t.get("taskId")) != name:
            continue
        if t.get("isCompleted") or t.get("isSkipped"):
            return None
        return t
    return None


def _commit_checkin_grid(session, headers, item, delay=0.0):
    """完成一条心愿打卡任务的"当前一格"，按通道自动路由，返回 res 字符串。

    res ∈ {"done","reject","captcha","fail","unsupported"}。
    路由规则 (由 得物.har 逆向)：
      - jumpUrl 里带小号 hacking-task taskType (1=浏览停留 / 62=逛商品) ->
        hacking-task 通道 (_commit_jump_task: type1 pre_commit+停留+commit;
        type62 直接试 commit, 需 App 内浏览上报, 过不了如实返回 reject);
      - 否则用 checkin/task-commit + 列表自身 taskId+taskType
        (HAR 实测逛社区第一格 taskType=1006 走这条 code=200)。
    """
    name = item.get("taskName") or item.get("taskId")
    jump_url = item.get("jumpUrl")
    jt = _parse_jump_task(jump_url) if jump_url else None
    if jt:
        return _commit_jump_task(session, headers, name, jt, delay=delay)
    task_id = item.get("taskId")
    task_type = item.get("taskType")
    if not task_id or task_type in (None, ""):
        # 既无 hacking-task 参数, 又没有可提交的 taskId -> 无法脚本化主动完成。
        return "unsupported"
    return _commit_checkin_native(session, headers, name, task_id, task_type, delay=delay)


def do_checkin(session, headers, delay=0.0):
    """Submit pending wish/check-in tasks, then re-read final state.

    返回一个 dict 供推送汇总使用:
        {
          "status":      "ok"/"skip"/"captcha"/"error"/"partial",
          "days":        连续打卡天数 (int),
          "target":      目标天数 (int),
          "done":        [任务名, ...] 本次成功提交的任务,
          "page":        [任务名, ...] 需 App 内完成的页面行为任务,
          "api_unfinished": [任务名, ...] 提交后仍接口未完成,
          "captcha":     bool 是否命中滑块,
          "detail":      "一句话总结",
        }
    """
    summary = {
        "status": "ok", "days": None, "target": None,
        "done": [], "page": [], "api_unfinished": [],
        "captcha": False, "detail": "",
    }
    if captcha_blocked(session, CHECKIN_COMMIT):
        print("[心愿打卡] 已检测到滑块验证码，本次跳过打卡写操作，请在 App 内手动完成。")
        summary["status"] = "captcha"
        summary["detail"] = "命中滑块验证码，已跳过"
        return summary
    print("查询心愿打卡任务...")
    today_tasks, result, fail_reason = _checkin_home(session, headers, delay=delay)
    if today_tasks is None:
        # fail_reason 已含完整中文失败原因 (如 "首页查询失败: HTTP 403 / 业务code=403 msg=网络拥堵")
        print("[心愿打卡] 首页查询失败，跳过。(%s)" % (fail_reason or "未知原因"))
        summary["status"] = "error"
        summary["detail"] = fail_reason or "首页查询失败 (未知原因)"
        return summary
    if result:
        summary["days"] = result.get("currentDays")
        summary["target"] = result.get("targetDays")
        print("[心愿打卡] 当前连续打卡天数: %s / 目标 %s"
              % (result.get("currentDays"), result.get("targetDays")))
    pending = [t for t in today_tasks if not t.get("isCompleted") and not t.get("isSkipped")]
    if not pending:
        print("[心愿打卡] 今日任务已全部完成，跳过。")
        summary["status"] = "skip"
        summary["detail"] = "今日任务已全部完成"
        return summary
    # 关键: 同一条打卡任务的不同"格"可能走【不同通道】(得物.har 逆向实测)：
    #   逛社区任务 total=2:
    #     · 第 1 格 列表 taskType=1006 -> checkin/task-commit (body 仅 taskId+taskType,
    #       taskType 为整数) 记一格 (HAR entry 133, code=200);
    #     · 完成第 1 格后服务端把它换成 列表 taskType=1009, 且 jumpUrl 里带 hacking-task
    #       taskType=1 (taskId=od1gp timeCount=15) -> pre_commit + 停留 + hacking-task/commit
    #       (HAR entry 130/125/89) 记第 2 格。
    #   逛商品任务 列表 taskType=1002, jumpUrl taskType=62 -> 需 hacking-flow/v1/mark
    #     浏览上报 (密文未还原), 只能试一次 commit, 过不了如实报"需 App 内完成"。
    # 因此按"格"循环: 每完成一格就回读 home-page 拿刷新后的整条 item, 重新判定通道,
    # 直到该任务满进度 / 命中拒收 / 限流为止。
    # 修复 (2026-08-03): 旧代码只要 jumpUrl 解析出 taskType 就一律打到 hacking-task
    # /task/commit; 而逛社区第一格的 taskType=1006 属 checkin 平台编号, 打到 hacking-task
    # 会返回 200 但根本没记账 -> 误报"完成成功", 实际逛社区一格未过。现按 taskType 是否
    # <1000 区分通道 (见 _parse_jump_task 与 _commit_checkin_grid)。
    HARD_REJECT_CODE = 110000001  # 保留常量: checkin 通道"不支持主动提交"业务码
    # 服务端明确拒收 / 无法脚本化的任务名 (需 App 内页面行为), 与"提交后仍未完成"区分。
    hard_reject = []
    # 单个任务最多推进几格: 逛社区 2 格, 留点余量防止个别活动 3 格; 命中拒收/失败即停。
    MAX_COMMIT_ROUNDS = 4
    for task in pending:
        name = task.get("taskName") or task.get("taskId")
        # 关闭强制提交时, 带 jumpUrl 的页面行为任务不主动提交, 交由 App 内完成。
        if not CHECKIN_FORCE and task.get("jumpUrl"):
            print("[心愿打卡] %s 需在得物 App 内完成页面行为后自动记账，跳过主动提交 "
                  "(DEWU_CHECKIN_FORCE=0 或 --no-checkin-force)。" % name)
            summary["page"].append(name)
            random_sleep(delay)
            continue
        if captcha_blocked(session, CHECKIN_COMMIT):
            print("[心愿打卡] 已检测到滑块验证码，停止后续打卡写操作。")
            summary["status"] = "captcha"
            summary["captcha"] = True
            summary["detail"] = "命中滑块验证码，已停止"
            return summary
        item = task
        res = None
        for round_no in range(1, MAX_COMMIT_ROUNDS + 1):
            jt_dbg = _parse_jump_task(item.get("jumpUrl")) if item.get("jumpUrl") else None
            channel = "hacking-task" if jt_dbg else "checkin"
            cur_id = jt_dbg["taskId"] if jt_dbg else item.get("taskId")
            cur_type = jt_dbg["taskType"] if jt_dbg else item.get("taskType")
            print("[心愿打卡] 执行任务: %s [%d/%s] (通道=%s taskId=%s taskType=%s)"
                  % (name, round_no, task.get("total") or "?", channel, cur_id, cur_type))
            res = _commit_checkin_grid(session, headers, item, delay=delay)
            if res == "captcha":
                summary["status"] = "captcha"
                summary["captcha"] = True
                summary["detail"] = "提交 %s 时命中滑块验证码" % name
                return summary
            if res != "done":
                break
            if name not in summary["done"]:
                summary["done"].append(name)
            # 服务端记账有短暂延迟, 稍等再回读, 避免拿到旧的一格。
            random_sleep(max(delay, 2.0))
            nxt = _refresh_checkin_item(session, headers, name, delay=delay)
            if nxt is None:
                print("[心愿打卡] %s 进度已满，无需继续。" % name)
                break
            item = nxt  # 下一格可能换了 taskId/taskType/通道, 循环顶部重新判定。
        # 已成功过至少一格就不算硬拒 (剩余格子多为需 App 内浏览); 一格都没过才记 hard_reject。
        if res in ("reject", "unsupported") and name not in summary["done"]:
            hard_reject.append(name)
            if CHECKIN_DEBUG and task.get("jumpUrl"):
                print("[心愿打卡][DEBUG] %s jumpUrl = %s" % (name, task.get("jumpUrl")))
        random_sleep(delay)
    # 服务端记账有短暂延迟：刚 commit 成功的任务立刻回读可能仍是未完成态。
    # 这里先等一小会儿再拉首页，避免把已完成的任务误报成未完成。
    if summary["done"]:
        random_sleep(max(delay, 2.0))
    # 重新拉取首页，判断最终完成度
    today_tasks2, result2, _fail2 = _checkin_home(session, headers, delay=delay)
    if isinstance(result2, dict) and result2.get("isAllCompleted"):
        print("[心愿打卡] 今日所有任务已全部完成。")
        summary["status"] = "ok"
        summary["detail"] = "今日打卡全部完成"
        summary["page"] = []
        summary["api_unfinished"] = []
        return summary
    # 区分两类未完成: 纯接口任务 (jumpUrl=null, 通常只每日签到; 若提交后仍
    # 未完成多为服务端短暂延迟或被风控跳过) 与页面行为任务 (jumpUrl 非空,
    # 需 App 内完成, 本就不会自动完成)。
    # 本轮 commit 返回 200 的任务不再报"未完成": 服务端记账可能还没落库，
    # 或该任务需要多次浏览才满进度，重复提示只会误导。
    done_names = set(summary["done"])
    api_unfinished = []
    page_unfinished = []
    for task in today_tasks2 or []:
        if task.get("isCompleted") or task.get("isSkipped"):
            continue
        name = task.get("taskName") or task.get("taskId")
        if name in done_names:
            continue
        if task.get("jumpUrl"):
            page_unfinished.append(name)
        else:
            api_unfinished.append(name)
    # 合并本轮结果 (done/hard_reject 来自本轮提交, *_unfinished 来自二次核对)
    summary["page"] = sorted(set(summary["page"] + hard_reject + page_unfinished) - done_names)
    summary["api_unfinished"] = sorted(set(summary["api_unfinished"] + api_unfinished) - done_names)
    if summary["done"]:
        print("[心愿打卡] 本轮已完成: %s" % ", ".join(str(x) for x in summary["done"]))
    if api_unfinished:
        print("[心愿打卡] 接口任务仍未完成 (可能服务端延迟或被风控): %s" % ", ".join(str(x) for x in api_unfinished))
    if summary["page"]:
        print("[心愿打卡] 页面行为任务需在得物 App 内完成: %s" % ", ".join(str(x) for x in summary["page"]))
    if not api_unfinished and not summary["page"]:
        print("[心愿打卡] 打卡流程结束，可在 App 内核对最终状态。")
        summary["status"] = "ok"
        summary["detail"] = "打卡流程结束"
    else:
        summary["status"] = "partial"
        parts = []
        if summary["api_unfinished"]:
            parts.append("接口未完成:%s" % "/".join(summary["api_unfinished"]))
        if summary["page"]:
            parts.append("需App内完成:%s" % "/".join(summary["page"]))
        summary["detail"] = "; ".join(parts) if parts else "部分完成"
    return summary


def main():
    ap = argparse.ArgumentParser(description="得物种树活动 签到/做任务/浇水 脚本")
    ap.add_argument("--task", action="append", default=None,
                    help="自定义任务 taskId:taskType[:btd:spuId[:nulls]]，可多次指定；nulls=1 表示 task/commit 需带 6 个 null 字段")
    ap.add_argument("--time-slots", action="append", default=None,
                    help="时段领水滴的固定 taskId 列表 (classify=1)，可多次指定；默认 1/4/multi_times")
    ap.add_argument("--no-sign", action="store_true", help="跳过每日签到")
    ap.add_argument("--no-checkin", action="store_true", help="跳过得物心愿森林(心愿打卡)每日签到")
    ap.add_argument("--no-checkin-force", action="store_true",
                    help="心愿打卡：关闭对带 jumpUrl 页面行为任务的强制提交 "
                         "(默认开启强制提交；确认服务端硬拒后可用本参数或 DEWU_CHECKIN_FORCE=0 关掉)")
    ap.add_argument("--checkin-debug", action="store_true",
                    help="心愿打卡：打印 home-page 返回的 todayTasks 原始 JSON，便于核对字段名")
    ap.add_argument("--no-water", action="store_true", help="跳过浇水")
    ap.add_argument("--no-extra", action="store_true", help="跳过额外任务水滴领取")
    ap.add_argument("--no-droplet", action="store_true",
                    help="跳过加密的领水滴任务 (时段领水滴 + 生成水滴)")
    ap.add_argument("--water-times", type=int, default=100,
                    help="浇水安全上限 (默认 100；浇水本身无次数限制，会一直浇到 canWatering=false 即水滴不足，或反爬拦截；此参数仅防止无限刷接口触发风控)")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="请求间隔基准秒数 (默认 2.0；每次实际休眠在 [基准*0.5, 基准*1.8] 随机抖动，"
                         "反爬/限流时调大以降低被识别为脚本的概率)")
    ap.add_argument("--captcha-exit", action="store_true",
                    help="命中滑块验证码(485)时立即结束整个脚本 (被拦即停，适合每日定时跑)")
    args = ap.parse_args()

    # 旧版环境变量多账号模式会改写这些全局变量，提前声明。
    global AUTH_X_AUTH_TOKEN, AUTH_DUTOKEN, AUTH_COOKIETOKEN
    global AUTH_DUID, AUTH_SHUMEIID, AUTH_COOKIE, AUTH_DUPRODUCTID
    global CHECKIN_FORCE, CHECKIN_DEBUG

    # 心愿打卡行为开关：默认强制提交；环境变量 DEWU_CHECKIN_FORCE=0 或
    # 参数 --no-checkin-force 可关掉。DEBUG 默认关闭，需显式开启。
    CHECKIN_FORCE = _read_bool_env("DEWU_CHECKIN_FORCE", default=True)
    if args.no_checkin_force:
        CHECKIN_FORCE = False
    CHECKIN_DEBUG = bool(args.checkin_debug) or _read_bool_env("DEWU_CHECKIN_DEBUG")

    # 运行日志落盘
    setup_run_log()
    install_run_log_tee()

    # 脚本归属标识（还原自本地配置片段，含自校验；被篡改会同时告警日志与推送）
    _author, _tampered = _cfg_join()
    if _author:
        if _tampered:
            print("[归属] ⚠ 脚本归属标识异常，疑似被第三方篡改，请检查脚本完整性。")
        else:
            print("[归属] 脚本由 %s 维护" % _author)

    def run_one(headers, label):
        """用给定的请求头执行单个账号，异常不中断整体流程。"""
        try:
            _run_single_account(args, account_label=label, headers=headers)
        except SystemExit:
            pass
        except Exception as ex:
            print("[账号] %s 执行异常: %s" % (label, ex))

    # =====================================================================
    # 鉴权模式：环境变量多账号（token 接口版）
    #   读取 DEWU_AUTH_TOKEN / DEWU_DUTOKEN / DEWU_COOKIETOKEN，多账号用 @ 分隔。
    # =====================================================================
    env_auth_token = (AUTH_X_AUTH_TOKEN or "").strip()
    if not env_auth_token:
        print("[错误] 未检测到 DEWU_AUTH_TOKEN 环境变量，token 接口版无法运行。")
        sys.exit(1)
    print("检测到 DEWU_AUTH_TOKEN 环境变量，使用多账号模式。")
    n = len(_ALL_TOKENS)
    print("=" * 50)
    print("检测到 %d 个账号，依次执行" % n)
    print("=" * 50)
    for idx in range(n):
        print("\n" + "=" * 50)
        print(">>> 账号 %d / %d" % (idx + 1, n))
        print("=" * 50)
        AUTH_X_AUTH_TOKEN = _ALL_TOKENS[idx] if idx < len(_ALL_TOKENS) else ""
        AUTH_DUTOKEN = _ALL_DUTOKENS[idx] if idx < len(_ALL_DUTOKENS) else ""
        AUTH_COOKIETOKEN = _ALL_COOKIETOKENS[idx] if idx < len(_ALL_COOKIETOKENS) else ""
        AUTH_DUID = _ALL_DUIDS[idx] if idx < len(_ALL_DUIDS) else ""
        AUTH_SHUMEIID = _ALL_SHUMEIIDS[idx] if idx < len(_ALL_SHUMEIIDS) else ""
        AUTH_DUPRODUCTID = AUTH_DUID
        if AUTH_DUTOKEN and AUTH_X_AUTH_TOKEN:
            AUTH_COOKIE = "duToken=%s; x-auth-token=%s" % (AUTH_DUTOKEN, AUTH_X_AUTH_TOKEN)
        elif AUTH_X_AUTH_TOKEN:
            AUTH_COOKIE = "x-auth-token=%s" % AUTH_X_AUTH_TOKEN
        else:
            AUTH_COOKIE = ""
        run_one(None, "账号%d/%d" % (idx + 1, n))
        if idx < n - 1:
            random_sleep(5)

    # PlusPlus 推送运行结果 (详细报告)
    if _RUN_RESULTS:
        report = build_notify_report(_RUN_RESULTS)
        print("\n" + "=" * 28)
        print("运行结果报告 (将推送):")
        print("=" * 28)
        print(report)
        first = _RUN_RESULTS[0] if _RUN_RESULTS else {}
        ok_count = sum(1 for r in _RUN_RESULTS if not r.get("captcha"))
        if _author and not _tampered:
            title = "得物种树签到 · %s %s" % (
                _author,
                "✅全部正常" if ok_count == len(_RUN_RESULTS) else "⚠部分被风控")
        else:
            title = "得物种树签到 %s" % (
                "✅全部正常" if ok_count == len(_RUN_RESULTS) else "⚠部分被风控")
        send_pushplus(title, report)

def _run_single_account(args, account_label="账号1/1", headers=None):
    """执行单个账号的完整签到流程。account_label 用于推送标识。

    headers 由 main() 从环境变量 DEWU_AUTH_TOKEN 构建后传入；
    未传入时回退到从环境变量直接构建 (token 接口版标准流程)。
    """
    if headers is None:
        headers = build_auth_headers()
    headers.pop("sign", None)
    if not headers.get("x-auth-token") and not headers.get("duToken"):
        sys.exit("未找到任何鉴权信息：请在青龙面板配置环境变量 DEWU_AUTH_TOKEN / DEWU_DUTOKEN / DEWU_COOKIETOKEN。")
    # DEWU_AUTH_TOKEN 是唯一必填项；DUTOKEN/COOKIETOKEN 为可选，但建议填写
    if not headers.get("x-auth-token"):
        sys.exit("未找到 x-auth-token：请在青龙面板配置必填环境变量 DEWU_AUTH_TOKEN。")

    session = requests.Session()
    session.trust_env = False  # disable system proxy (e.g. 127.0.0.1:9)
    session.headers.update({"User-Agent": headers.get("User-Agent", "Mozilla/5.0")})
    session.captcha_exit = args.captcha_exit

    # 品赞代理：业务请求优先走代理，失败直连兜底
    proxy_label = ""
    proxy_ip = ""
    _using_proxy = False
    try:
        proxies, proxy_ip = get_valid_proxy()
        if proxies:
            session.proxies = proxies
            proxy_label = "代理 %s" % proxy_ip
            _using_proxy = True
        else:
            proxy_label = "直连"
    except Exception as exc:
        proxy_label = "直连(代理异常)"
        print("[代理] 获取代理异常，使用直连: %s" % exc)
    print("[代理] 本次请求方式: %s" % proxy_label)

    # 代理自动回退直连：如果代理请求失败（连接超时/拒绝等），自动去掉代理改直连
    if _using_proxy:
        _orig_send = session.send
        _proxy_fallback_triggered = [False]
        def _send_with_fallback(request, **kwargs):
            try:
                return _orig_send(request, **kwargs)
            except (requests.exceptions.ProxyError,
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.ReadTimeout) as exc:
                if not _proxy_fallback_triggered[0]:
                    _proxy_fallback_triggered[0] = True
                    print("[代理] 代理请求失败 (%s)，自动回退直连" % exc)
                    session.proxies = {}
                    session.trust_env = False
                raise
        session.send = _send_with_fallback

    # SSL 证书校验：本机 Python 运行时缺失/无法校验 app.dewu.com 的证书链
    # (常因网络存在 TLS 拦截代理，返回的证书不在 certifi 公开 CA 包内)，
    # 直接关闭校验并屏蔽 InsecureRequestWarning，避免 CERTIFICATE_VERIFY_FAILED
    # 导致整脚本崩溃。脚本仅访问固定的 app.dewu.com 单一域名，关闭校验仅影响
    # 本机与该服务器的连接加密校验，不涉及其他站点。
    session.verify = False
    if urllib3 is not None:
        try:
            urllib3.disable_warnings()
        except Exception:
            pass
    print("[提示] 已关闭 SSL 证书校验 (当前环境无法校验 app.dewu.com 证书链)。")

    droplet_before = get_droplet(session, headers)
    print("当前水滴余额: %s" % droplet_before)
    # 查询树进度
    try:
        _, tree_init_data = req_retry(session, "GET", "/hacking-tree/v1/tree/init", headers, delay=0, retries=1, backoff=2.0)
        if isinstance(tree_init_data, dict) and tree_init_data.get("code") == 200:
            ti = (tree_init_data.get("data") or {}).get("tree")
            if ti:
                print_tree_progress(ti, droplet_before)
    except Exception:
        pass

    # 记录本次运行结果（用于推送）。新增多个结构化字段，便于推送出详细报告。
    start_ts = time.time()
    account_result = {
        "account": account_label,
        "proxy": proxy_label,
        "droplet_before": droplet_before,
        "droplet_after": "",
        "sign_in": None,            # "already"/"success"/"captcha"/"failure"
        "checkin": None,            # do_checkin 返回的 summary dict
        "tasks_total": 0,
        "tasks_done": 0,
        "water_times": 0,           # 本次成功浇水次数
        "water_reason": "",         # 停止原因
        "tree_before": None,        # 浇水前树进度快照
        "tree_after": None,         # 浇水后树进度快照
        "captcha": False,
        "duration_sec": 0,
    }

    if not args.no_sign:
        account_result["sign_in"] = do_sign_in(session, headers, delay=args.delay)
        random_sleep(args.delay)
        if args.captcha_exit and captcha_blocked(session):
            print("\n[停止] 命中滑块验证码，按 --captcha-exit 立即结束。请先在得物 App 内手动过验证，或换网络/IP 后重跑。")
            sys.exit(0)

    if not args.no_checkin:
        try:
            account_result["checkin"] = do_checkin(session, headers, delay=args.delay)
        except Exception as ex:
            print("[心愿打卡] 异常: %s" % ex)
        random_sleep(args.delay)
        if args.captcha_exit and captcha_blocked(session):
            print("\n[停止] 命中滑块验证码，按 --captcha-exit 立即结束。请先在得物 App 内手动过验证，或换网络/IP 后重跑。")
            sys.exit(0)

    tasks, time_slot_ids, task_source, skipped_tasks, completed_task_ids = load_tasks(session, headers, args)
    if task_source == "dynamic":
        print("[任务列表] 已读取当天任务：可自动处理 %d 个，领水滴时段 %d 个。" % (len(tasks), len(time_slot_ids or [])))
        for task_id, name, task_type, reason in skipped_tasks:
            print("[任务 %s] %s (type=%s) 暂不自动处理：%s。" % (task_id, name, task_type, reason))
    elif task_source == "custom":
        print("[任务列表] 使用命令行指定任务。")

    account_result["tasks_total"] = len(tasks)
    print("开始处理 %d 个任务..." % len(tasks))
    for task in tasks:
        try:
            ok = do_task(session, headers, task, delay=args.delay)
            if ok:
                account_result["tasks_done"] += 1
        except Exception as ex:
            print("[任务 %s] 异常: %s" % (task.get("taskId"), ex))
        random_sleep(args.delay)
    if args.captcha_exit and captcha_blocked(session):
        print("\n[停止] 命中滑块验证码，按 --captcha-exit 立即结束。请先在得物 App 内手动过验证，或换网络/IP 后重跑。")
        sys.exit(0)

    if not args.no_extra:
        print("领取额外任务水滴...")
        try:
            do_task_extra(session, headers, delay=args.delay)
        except Exception as ex:
            print("[额外任务] 异常: %s" % ex)
        if args.captcha_exit and captcha_blocked(session):
            print("\n[停止] 命中滑块验证码，按 --captcha-exit 立即结束。请先在得物 App 内手动过验证，或换网络/IP 后重跑。")
            sys.exit(0)

    if not args.no_droplet:
        print("领取领水滴任务 (时段 + 任务)...")
        try:
            do_task_receive(session, headers, tasks=tasks, delay=args.delay,
                            time_slot_ids=time_slot_ids, completed_task_ids=completed_task_ids)
        except Exception as ex:
            print("[领水滴] 异常: %s" % ex)
        if args.captcha_exit and captcha_blocked(session):
            print("\n[停止] 命中滑块验证码，按 --captcha-exit 立即结束。请先在得物 App 内手动过验证，或换网络/IP 后重跑。")
            sys.exit(0)
        # 批次之间冷却：领水滴任务与生成水滴之间随机停，避免写操作连成一片。
        random_sleep(args.delay * 2)
        try:
            do_generate_droplet(session, headers, delay=args.delay)
        except Exception as ex:
            print("[生成水滴] 异常: %s" % ex)
        if args.captcha_exit and captcha_blocked(session):
            print("\n[停止] 命中滑块验证码，按 --captcha-exit 立即结束。请先在得物 App 内手动过验证，或换网络/IP 后重跑。")
            sys.exit(0)
        # 每日水滴福利 (得物最新抓包新增, 独立于生成水滴)
        random_sleep(args.delay)
        try:
            do_droplet_benefit(session, headers, delay=args.delay)
        except Exception as ex:
            print("[水滴福利] 异常: %s" % ex)
        if args.captcha_exit and captcha_blocked(session):
            print("\n[停止] 命中滑块验证码，按 --captcha-exit 立即结束。请先在得物 App 内手动过验证，或换网络/IP 后重跑。")
            sys.exit(0)

    if not args.no_water:
        print("浇水...")
        # 浇水前抓一次树进度快照，便于报告“浇水进度 / 还需浇多少”
        account_result["tree_before"] = get_tree_progress(session, headers)
        try:
            water_count, _, water_reason = do_watering(
                session, headers, max_times=args.water_times, delay=args.delay)
            account_result["water_times"] = water_count
            account_result["water_reason"] = water_reason or ""
        except Exception as ex:
            print("[浇水] 异常: %s" % ex)
        # 浇水后再抓一次快照，对比进度变化
        account_result["tree_after"] = get_tree_progress(session, headers)

    print("完成后水滴余额: %s" % get_droplet(session, headers))
    droplet_after = get_droplet(session, headers)
    account_result["droplet_after"] = droplet_after
    account_result["captcha"] = captcha_blocked(session)
    account_result["duration_sec"] = int(time.time() - start_ts)
    # 记录本次真正被风控的接口 (用于推送精确列出哪些环节被跳过)
    _blocked_set = sorted(getattr(session, "captcha_blocked", set()))
    account_result["captcha_blocked"] = _blocked_set
    _RUN_RESULTS.append(account_result)
    if captcha_blocked(session):
        # 按接口维度列出本次真正被风控的环节，避免笼统说“全部跳过”。
        blocked = _blocked_set
        items = "、".join(CAPTCHA_NAME_MAP.get(p, p) for p in blocked)
        print("\n[提示] 本次运行命中滑块验证码，以下环节被服务端风控跳过：%s。"
              "这是得物对当前 IP 的风控，并非脚本错误。可：" % items)
        print("  1) 在得物 App 内手动完成对应步骤；")
        print("  2) 隔一段时间、或在自己常用网络/IP 上重跑本脚本 (每日一次即可)；")
        print("  3) 若持续触发，调大 --delay 并减少 --water-times。")


if __name__ == "__main__":
    main()
