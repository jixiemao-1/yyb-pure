#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美的微信小程序自动化脚本（自动获取积分版）

功能：
  1. 多账号自动登录（微信code -> 登录token）
  2. 本地缓存 token，自动验证有效性
  3. 查询多品牌积分
  4. 【新增】每日签到获取积分
  5. 【新增】查询活动列表并参与可获积分活动
   6. 【新增】领取权益/优惠券（可能含积分奖励）
  7. 【新增】查询积分未读消息提示
  8. 【新增】美粉任务进度上报（种草、订单、购买金额、绑定设备等）
  9. 积分达标 Push Plus 推送提醒

环境变量：
  WECHAT_SERVER    : 协议服务地址（getCode.py 使用，默认 http://192.168.5.16:8011）
  ADMIN_KEY        : 协议服务 ADMIN_KEY（getCode.py 使用，WeChatPadPro/iwechat 需要，牛子协议不需要）
  WX_ID            : 可选，指定要处理的账号（wxid/deviceId，多个用&分隔）；不设置则处理所有在线账号
  MyPushPlusToken  : Push Plus 推送 token（可选，不配置则不推送）
  BRAND_LIST       : 查询积分的品牌列表，逗号分隔（默认 1,2,3,5）
  APP_SECRET/API_KEY/APP_ID : 可选覆盖
  ENABLE_SIGNIN    : 1开启每日签到(默认1)，0关闭
  ENABLE_TASKS     : 1开启任务进度上报(默认1)，0关闭
  ENABLE_ACT       : 1开启活动参与(默认1)，0关闭
  SIGNIN_TIMES      : 签到尝试次数（默认3，应对多品牌连续签到）
"""

import os
import sys
import json
import time
import random
import hashlib
import socket
import requests
from datetime import datetime
from urllib.parse import urljoin, quote, urlparse

# 取 code 模块（同目录 getCode.py，自动识别 WeChatPadPro/iwechat/牛子协议）
try:
    from getCode import WeChatCodeGetter
except Exception as _e:
    WeChatCodeGetter = None
    print(f"⚠️ 导入 getCode 模块失败: {_e}")

# ==================== 固定配置 ====================
DEFAULT_WECHAT_SERVER = "http://192.168.5.16:8011"
DEFAULT_APP_ID = "ee07f27990db48109efcccd322d3a873"
DEFAULT_API_KEY = "b6db9d5cf2d449538d3a0dd5d77b2e35"
DEFAULT_APP_SECRET = "2646746f07bb46199aff49002e6dce81"
WX_MINI_APP_ID = "wx49a622805968d156"

# 基础接口地址
LOGIN_URL = "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/app/login/wx/mini/getLoginInfo.do"
MEMBER_INFO_URL = "https://mcsp.midea.com/api/mcsp_cms/mcsp-cmshop-bff-app/user/getMemberInfo"
SCORE_URL = "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/integral/getMultipleAccountScore.do"
SIGNIN_H5_URL = "https://mvip.midea.cn/my/score_welfare/index?channel=MDDZ"
MALL_LOGIN_INFO_URL = "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/app/login/getMallLoginInfo.do"
C4A_TOKEN_URL = "https://mcsp.midea.com/api/mcsp_cms/mcsp-cmshop-bff-app/user/getC4aToken"
MVIP_BASE_URL = "https://mvip.midea.cn"

# 活动中心接口基础URL（PROD环境）
ACTIVITY_HOST = "https://d.midea.com"
ACTIVITY_APIKEY_PROD = "3660663068894a0d9fea574c2673f3c0"
# 小程序源码中的活动接口主网关（utils/config.apiHost.PROD）
ACTIVITY_API_HOST = os.getenv("ACTIVITY_API_HOST", "https://mcsp-api.midea.com")
# 部分环境下 mcsp 主域活动接口会大量 404，默认关闭此兜底
ENABLE_MCSP_ACTIVITY_FALLBACK = (os.getenv("ENABLE_MCSP_ACTIVITY_FALLBACK", "0") == "1")

# 本地缓存文件
CACHE_FILE = "md_CK.json"
REMINDER_RECORD_FILE = "reminder_record.json"
SIGNIN_RECORD_FILE = "signin_record.json"  # 签到记录

# 重试配置
MAX_RETRY = 2
REQUEST_TIMEOUT = 15
RETRY_DELAY_MIN = 1
RETRY_DELAY_MAX = 3
try:
    HTTP_RETRY_TIMES = max(1, int(os.getenv("MIDEA_HTTP_RETRY", "3")))
except ValueError:
    HTTP_RETRY_TIMES = 3

# 功能开关
ENABLE_SIGNIN = (os.getenv("ENABLE_SIGNIN", "1") != "0")
ENABLE_TASKS = (os.getenv("ENABLE_TASKS", "1") != "0")
ENABLE_ACT = (os.getenv("ENABLE_ACT", "1") != "0")

SIGNIN_TIMES = max(1, int(os.getenv("SIGNIN_TIMES", "3")))
MIDEA_GOLD_GRADE_POINT_ID = os.getenv("MIDEA_GOLD_GRADE_POINT_ID", "206")
MIDEA_DIAMOND_GRADE_POINT_ID = os.getenv("MIDEA_DIAMOND_GRADE_POINT_ID", "100177")

# 活动门店查询固定定位（可选）：用于提升 shopGift 查询门店命中率
# 例：
#   ACTIVITY_LAT=23.12911
#   ACTIVITY_LON=113.26439
#   ACTIVITY_CITY_CODE=440100
# 默认模拟位置：广东省广州市（可用环境变量覆盖）
ACTIVITY_LAT = str(os.getenv("ACTIVITY_LAT", "23.12911") or "").strip()
ACTIVITY_LON = str(os.getenv("ACTIVITY_LON", "113.26439") or "").strip()
ACTIVITY_CITY_CODE = str(os.getenv("ACTIVITY_CITY_CODE", "440100") or "").strip()

# ==================== 辅助函数 ====================
def _looks_like_dns_error(exc):
    text = str(exc)
    markers = (
        "NameResolutionError",
        "Failed to resolve",
        "getaddrinfo failed",
        "Temporary failure in name resolution",
        "nodename nor servname provided",
    )
    return any(marker in text for marker in markers)


def _dns_state(url):
    host = urlparse(url).hostname or ""
    if not host:
        return ""
    try:
        return f"{host} 当前解析为 {socket.gethostbyname(host)}"
    except Exception as exc:
        return f"{host} 当前仍无法解析：{exc}"


def http_post(url, **kwargs):
    """POST 请求短重试，主要兜底 DNS/连接瞬时异常。"""
    retry_times = kwargs.pop("_retry_times", HTTP_RETRY_TIMES)
    try:
        retry_times = max(1, int(retry_times))
    except (TypeError, ValueError):
        retry_times = HTTP_RETRY_TIMES
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)

    last_exc = None
    host = urlparse(url).netloc or url
    for attempt in range(1, retry_times + 1):
        try:
            return requests.request("POST", url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < retry_times:
                reason = "DNS解析异常" if _looks_like_dns_error(exc) else "连接异常"
                delay = random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX)
                print(f"  [HTTP重试] {host} {reason} ({attempt}/{retry_times})，{delay:.1f} 秒后重试")
                time.sleep(delay)
                continue
            if _looks_like_dns_error(exc):
                state = _dns_state(url)
                if state:
                    print(f"  [HTTP诊断] {state}")
            raise
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retry_times:
                delay = random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX)
                print(f"  [HTTP重试] {host} 请求异常 ({attempt}/{retry_times})，{delay:.1f} 秒后重试")
                time.sleep(delay)
                continue
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError("HTTP请求未执行")


def get_env_config():
    """读取环境变量"""
    return {
        "wechat_server": os.getenv("WECHAT_SERVER", DEFAULT_WECHAT_SERVER),
        "push_token": os.getenv("MyPushPlusToken", ""),
        "app_secret": os.getenv("APP_SECRET", DEFAULT_APP_SECRET),
        "api_key": os.getenv("API_KEY", DEFAULT_API_KEY),
        "app_id": os.getenv("APP_ID", DEFAULT_APP_ID),
    }


def get_accounts(wechat_server):
    """从 getCode.py 自动获取所有在线账号，返回列表 [(标识, 备注), ...]

    标识为传给 get_wx_code 取 code 用的 key：
      - 牛子协议: wxid
      - WeChatPadPro/iwechat: wx_id / deviceId（get_wx_code 内部映射为 license）
    备注取账号昵称/设备名，便于日志展示。
    """
    getter = _get_code_getter(wechat_server)
    if getter is None:
        print("❌ getCode 模块不可用，无法获取账号")
        return []

    try:
        online = getter.get_online_accounts()
    except Exception as e:
        print(f"❌ 获取在线账号失败: {e}")
        return []

    accounts = []
    used_remarks = set()
    for i, (acc, _status) in enumerate(online, start=1):
        if getter.protocol_type in ("yyb呆呆", "Niuzi"):
            key = acc.get("wxid") or acc.get("wx_id") or ""
        else:
            key = acc.get("wx_id") or acc.get("deviceId") or ""
        if not key:
            continue

        remark = (
            acc.get("nick_name")
            or acc.get("nickname")
            or acc.get("deviceName")
            or ""
        ).strip()
        if not remark or remark == "ㅤ":
            remark = f"账号_{str(key)[-6:]}" if len(str(key)) >= 6 else f"账号_{i}"
        # 备注去重
        base = remark
        n = 1
        while remark in used_remarks:
            remark = f"{base}_{n}"
            n += 1
        used_remarks.add(remark)

        accounts.append((str(key), remark))

    return accounts


def load_cache():
    """读取本地缓存文件"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"读取缓存文件失败: {e}")
    return {}


def save_cache(cache):
    """保存缓存到本地文件"""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"缓存已更新: {CACHE_FILE}")
    except Exception as e:
        print(f"保存缓存失败: {e}")


def load_signin_record():
    """加载签到记录，避免重复签到"""
    if os.path.exists(SIGNIN_RECORD_FILE):
        try:
            with open(SIGNIN_RECORD_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_signin_record(record):
    """保存签到记录"""
    try:
        with open(SIGNIN_RECORD_FILE, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存签到记录失败: {e}")


def load_reminder_record():
    """加载上次推送记录"""
    if os.path.exists(REMINDER_RECORD_FILE):
        try:
            with open(REMINDER_RECORD_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_reminder_record(record):
    """保存本次推送记录"""
    try:
        with open(REMINDER_RECORD_FILE, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except:
        pass


# 全局 code 获取器（懒加载，复用 getCode.py 的协议探测结果）
_CODE_GETTER = None
_LICENSE_MAP = None  # wxid/deviceId -> license 映射（非牛子协议时使用）


def _get_code_getter(wechat_server):
    """懒加载 WeChatCodeGetter（getCode.py），复用协议探测结果。"""
    global _CODE_GETTER
    if _CODE_GETTER is not None:
        return _CODE_GETTER
    if WeChatCodeGetter is None:
        return None
    # getCode.py 通过环境变量 WECHAT_SERVER 读取服务地址，确保已设置
    if not os.getenv("WECHAT_SERVER"):
        os.environ["WECHAT_SERVER"] = wechat_server
    try:
        _CODE_GETTER = WeChatCodeGetter()
        print(f"  [getCode] 协议服务类型: {_CODE_GETTER.protocol_type}")
    except Exception as e:
        print(f"⚠️ 初始化 getCode 失败: {e}")
        _CODE_GETTER = None
    return _CODE_GETTER


def _build_license_map(getter):
    """为非牛子协议构建 wxid/deviceId -> license 映射。"""
    global _LICENSE_MAP
    if _LICENSE_MAP is not None:
        return _LICENSE_MAP
    _LICENSE_MAP = {}
    try:
        accounts = getter.get_auth_keys()
    except Exception as e:
        print(f"⚠️ 获取授权码列表失败: {e}")
        return _LICENSE_MAP
    for acc in accounts:
        license_val = acc.get("license") or acc.get("authKey")
        if not license_val:
            continue
        for key in (acc.get("wx_id"), acc.get("deviceId"), acc.get("wxid")):
            if key:
                _LICENSE_MAP[str(key)] = license_val
    return _LICENSE_MAP


def get_wx_code(wechat_server, wxid, retry=MAX_RETRY):
    """调用 getCode.py 模块获取 jsCode（自动识别 WeChatPadPro/iwechat/牛子协议）"""
    getter = _get_code_getter(wechat_server)
    if getter is None:
        print("  ❌ getCode 模块不可用")
        return None

    # 牛子协议: wxid 即可直接取 code
    # WeChatPadPro/iwechat: 需用 wxid/deviceId 映射到 license
    if getter.protocol_type in ("yyb呆呆", "Niuzi"):
        license_or_wxid = wxid
    else:
        lic_map = _build_license_map(getter)
        license_or_wxid = lic_map.get(str(wxid))
        if not license_or_wxid:
            # 兜底：直接把 wxid 当 license 试一次（部分场景 license==wxid）
            license_or_wxid = wxid

    error_msg = ""
    for attempt in range(1, retry + 1):
        try:
            code = getter.get_applet_code(WX_MINI_APP_ID, license_or_wxid)
            if code:
                return code
            error_msg = "返回空 code"
        except Exception as e:
            error_msg = str(e)
        if attempt < retry:
            delay = random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX)
            time.sleep(delay)
    if error_msg:
        print(f"  ❌ 取 code 失败: {error_msg}")
    return None


def midea_login(js_code, app_id, api_key, app_secret):
    """调用美的登录接口，返回 (ucAccessToken, openId, unionId, c4aUid, expireTime) 或 None"""
    headers = {
        "apikey": api_key,
        "appId": app_id,
        "appsecret": app_secret,
        "miniAppVersion": "3.0.243",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 MicroMessenger/7.0.20",
    }
    payload = {
        "jsCode": js_code,
        "loginMode": 1,
        "platformType": "WX_MEIDIDAOJIA_MINI"
    }
    try:
        resp = http_post(LOGIN_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("code")) == "000000":
            d = data["data"]
            return (
                d["ucAccessToken"],
                d["openId"],
                d.get("unionId"),
                d.get("c4aUid"),
                int(d["ucAccessTokenExpireTime"])
            )
        else:
            print(f"登录失败: {data.get('msg')}")
    except Exception as e:
        print(f"登录请求异常: {e}")
    return None


def generate_sign(head_params, app_secret):
    """生成签名（SHA384）"""
    params = {k: v for k, v in head_params.items() if k != "sign"}
    params["appsecret"] = app_secret
    sorted_items = sorted(params.items())
    sign_str = "&".join(f"{k}={v}" for k, v in sorted_items)
    return hashlib.sha384(sign_str.encode()).hexdigest()


def get_member_info(uc_access_token, open_id, app_id, api_key, app_secret, user_id="", mobile=""):
    """获取会员信息"""
    timestamp = str(int(time.time() * 1000))
    transaction_id = f"{timestamp}.{random.randint(100000000000000, 999999999999999)}"

    head_params = {
        "language": "CN",
        "originSystem": "cms-app-mini",
        "timeZone": "8",
        "userType": "C",
        "tenantCode": "",
        "userKey": uc_access_token,
        "timestamp": timestamp,
        "miniAppVersion": "3.0.243",
        "transactionId": transaction_id,
    }
    head_params["sign"] = generate_sign(head_params, app_secret)

    rest_params = {
        "brand": 1,
        "userId": user_id,
        "mobile": mobile,
        "openId": open_id,
        "_timeStamp": int(time.time() * 1000)
    }
    payload = {
        "headParams": head_params,
        "restParams": rest_params,
        "pagination": {}
    }

    headers = {
        "apikey": api_key,
        "appId": app_id,
        "appsecret": app_secret,
        "miniAppVersion": "3.0.243",
        "ucAccessToken": uc_access_token,
        "userKey": uc_access_token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 MicroMessenger/7.0.20",
    }

    try:
        resp = http_post(MEMBER_INFO_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("code")) == "000000":
            return data.get("data")
        else:
            print(f"获取会员信息失败: {data.get('msg')} (code={data.get('code')})")
    except Exception as e:
        print(f"会员信息请求异常: {e}")
    return None


def get_multiple_account_score(uc_access_token, uid, brand_list, app_id, api_key, app_secret):
    """查询多品牌积分"""
    headers = {
        "apikey": api_key,
        "appId": app_id,
        "appsecret": app_secret,
        "miniAppVersion": "3.0.243",
        "ucAccessToken": uc_access_token,
        "userKey": uc_access_token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 MicroMessenger/7.0.20",
    }
    payload = {
        "restParams": {
            "uid": uid,
            "accountBrandList": brand_list
        }
    }
    try:
        resp = http_post(SCORE_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("code")) == "000000":
            return data.get("data")
        else:
            print(f"查询积分失败: {data.get('msg')}")
    except Exception as e:
        print(f"积分请求异常: {e}")
    return None


def build_activity_headers(uc_token, api_key, app_id, app_secret):
    """构建活动中心请求头"""
    timestamp = str(int(time.time() * 1000))
    transaction_id = f"{timestamp}.{random.randint(100000000000000, 999999999999999)}"

    head_params = {
        "language": "CN",
        "originSystem": "cms-app-mini",
        "timeZone": "8",
        "userType": "C",
        "tenantCode": "",
        "userKey": uc_token,
        "timestamp": timestamp,
        "miniAppVersion": "3.0.243",
        "transactionId": transaction_id,
    }
    head_params["sign"] = generate_sign(head_params, app_secret)

    headers = {
        "apikey": api_key,
        "appId": app_id,
        "appsecret": app_secret,
        "miniAppVersion": "3.0.243",
        "ucAccessToken": uc_token,
        "userKey": uc_token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 MicroMessenger/7.0.20",
    }
    return headers, head_params


def post_mcsp_bff(
    uc_token,
    app_id,
    api_key,
    app_secret,
    path,
    rest_params=None,
    extra_payload=None,
    timeout=REQUEST_TIMEOUT
):
    """
    调用 mcsp 主站 bff 接口，统一返回:
      {"ok": bool, "data": ..., "code": "...", "msg": "...", "error": "...", "raw": {...}}
    """
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    payload = {
        "restParams": dict(rest_params or {}),
        "headParams": head_params
    }
    if extra_payload and isinstance(extra_payload, dict):
        for k, v in extra_payload.items():
            payload[k] = v
    if "_timeStamp" not in payload["restParams"]:
        payload["restParams"]["_timeStamp"] = int(time.time() * 1000)

    url = f"https://mcsp.midea.com/{path.lstrip('/')}"
    try:
        resp = http_post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return {
                "ok": False, "error": f"HTTP {resp.status_code}",
                "code": str(resp.status_code), "msg": "",
                "data": None, "url": url, "raw": {}
            }
        data = resp.json()
        code = str(data.get("code", ""))
        msg = str(data.get("msg", "") or "")
        return {
            "ok": code == "000000",
            "code": code,
            "msg": msg,
            "data": data.get("data"),
            "url": url,
            "raw": data
        }
    except Exception as e:
        return {
            "ok": False, "error": str(e),
            "code": "", "msg": "",
            "data": None, "url": url, "raw": {}
        }


def _build_mvip_headers(cookie=""):
    """构建 mvip.midea.cn 请求头。"""
    headers = {
        "Host": "mvip.midea.cn",
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; M2012K11AC Build/TKQ1.220829.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/132.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.53.2800",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": "https://servicewechat.com/wx03925a39ca94b161/437/page-frame.html",
        "Accept-Encoding": "gzip, deflate, br",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def get_mall_login_info(uc_token, app_id, api_key, app_secret):
    """
    获取线下会员登录信息（uid/sukey）。
    对应小程序: app/login/getMallLoginInfo.do
    """
    headers, _ = build_activity_headers(uc_token, api_key, app_id, app_secret)
    payload = {"ucAccessToken": uc_token}
    try:
        resp = http_post(MALL_LOGIN_INFO_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        code = str(data.get("code", ""))
        msg = data.get("msg", "")
        if code != "000000":
            return {"ok": False, "error": f"[{code}] {msg}"}
        d = data.get("data", {}) or {}
        uid = str(d.get("uid") or "")
        sukey = str(d.get("sukey") or "")
        if uid and sukey:
            return {"ok": True, "uid": uid, "sukey": sukey}
        return {"ok": False, "error": "uid/sukey为空"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_c4a_token(uc_token, app_id, api_key, app_secret):
    """
    获取 c4aToken。
    对应小程序: mcsp-cmshop-bff-app/user/getC4aToken
    """
    headers, _ = build_activity_headers(uc_token, api_key, app_id, app_secret)
    payload = {}
    try:
        resp = http_post(C4A_TOKEN_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return ""
        data = resp.json()
        code = str(data.get("code", ""))
        if code not in ("0", "000000"):
            return ""
        token = data.get("data", "")
        if isinstance(token, dict):
            token = token.get("token") or token.get("c4aToken") or ""
        return str(token or "")
    except Exception:
        return ""


def _cookie_header_from_session(session):
    """将 requests.Session 内的 midea 域 cookie 合并为 header 字符串。"""
    parts = []
    seen = set()
    for c in session.cookies:
        domain = str(getattr(c, "domain", "") or "")
        if "midea.cn" not in domain:
            continue
        item = f"{c.name}={c.value}"
        if item in seen:
            continue
        seen.add(item)
        parts.append(item)
    return "; ".join(parts)


def build_mvip_session(uc_token, open_id, app_id, api_key, app_secret):
    """
    构建 mvip 会话：
      通过 getMallLoginInfo + getC4AToken + mddaojiaredirect 自动换取 H5 会话。
    """
    session = requests.Session()

    mall_info = get_mall_login_info(uc_token, app_id, api_key, app_secret)
    if not mall_info.get("ok"):
        return {"ok": False, "error": f"getMallLoginInfo失败: {mall_info.get('error', '未知错误')}"}
    uid = mall_info.get("uid", "")
    sukey = mall_info.get("sukey", "")
    if not uid or not sukey:
        return {"ok": False, "error": "getMallLoginInfo未返回uid/sukey"}

    c4a_token = get_c4a_token(uc_token, app_id, api_key, app_secret)

    score_url = SIGNIN_H5_URL
    if c4a_token:
        sep = "&" if "?" in score_url else "?"
        score_url = f"{score_url}{sep}c4aToken={c4a_token}"

    redirect_qs = [
        f"uid={uid}",
        f"sukey={sukey}",
        f"rurl={quote(score_url, safe='')}",
        f"userKey={quote(str(uc_token or ''), safe='')}",
        f"wxOpenId={quote(str(open_id or ''), safe='')}",
        f"cmshopt={int(time.time() * 1000)}",
    ]
    redirect_url = (
        "https://m.midea.cn/next/userinfo/mddaojiaredirect"
        + "?" + "&".join(redirect_qs)
    )

    # 先写入核心cookie，再请求重定向，尽量拿到完整 H5 会话
    session.cookies.set("uid", uid, domain=".midea.cn")
    session.cookies.set("sukey", sukey, domain=".midea.cn")
    session.cookies.set("uid", uid, domain=".mvip.midea.cn")
    session.cookies.set("sukey", sukey, domain=".mvip.midea.cn")

    try:
        session.get(redirect_url, headers=_build_mvip_headers(), timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except Exception:
        pass

    final_cookie = _cookie_header_from_session(session)
    if not final_cookie:
        final_cookie = f"uid={uid}; sukey={sukey}"
    session.headers.update(_build_mvip_headers(final_cookie))
    return {
        "ok": True,
        "session": session,
        "cookie": final_cookie,
        "source": "auto_session",
        "hasC4aToken": bool(c4a_token),
    }


def _mvip_get(session, path, cookie=""):
    """请求 mvip GET 接口并返回 json。"""
    url = f"{MVIP_BASE_URL}{path}"
    headers = _build_mvip_headers(cookie or "")
    resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    if resp.status_code != 200:
        return {"errcode": -1, "errmsg": f"HTTP {resp.status_code}"}
    try:
        return resp.json()
    except Exception:
        return {"errcode": -1, "errmsg": "响应非JSON"}


def mvip_signin(uc_token, open_id, app_id, api_key, app_secret, alias=""):
    """
    参考 H5 签到链路：
      - /next/mucuserinfo/getmucuserinfo
      - /my/score/create_daily_score
      - /my/muc/get_growth_status
    """
    sess_info = build_mvip_session(uc_token, open_id, app_id, api_key, app_secret)
    if not sess_info.get("ok"):
        return {"status": "skip", "score": 0, "reason": sess_info.get("error", "会话构建失败")}

    session = sess_info["session"]
    cookie = sess_info.get("cookie", "")
    if not sess_info.get("hasC4aToken"):
        print("  [签到-H5] 未获取到c4aToken，已使用uid/sukey继续尝试")

    user_info = _mvip_get(session, "/next/mucuserinfo/getmucuserinfo", cookie=cookie)
    if user_info.get("errcode") == 0:
        ui = user_info.get("data", {}).get("userinfo", {}) or {}
        mobile = str(ui.get("Mobile", ""))
        level_name = str(ui.get("LevelName", ""))
        if mobile and len(mobile) >= 7:
            mobile_show = f"{mobile[:3]}***{mobile[-3:]}"
        else:
            mobile_show = mobile or "未知"
        if level_name or mobile_show:
            print(f"  [签到-H5] 会话有效: {level_name}({mobile_show})")
    else:
        umsg = str(user_info.get("errmsg") or user_info.get("msg") or "")
        if umsg:
            print(f"  [签到-H5] 会话校验返回: {umsg}")

    sign_data = _mvip_get(session, "/my/score/create_daily_score", cookie=cookie)
    errcode = sign_data.get("errcode", sign_data.get("errCode", ""))
    if str(errcode) == "0":
        print("  [签到-H5] 签到成功")
        growth = _mvip_get(session, "/my/muc/get_growth_status", cookie=cookie)
        if str(growth.get("errcode")) == "0":
            d = growth.get("data", {}) or {}
            print(f"  [签到-H5] 当前积分: {d.get('vipPoint', 0)} 成长值: {d.get('vipGrow', 0)}")
        return {"status": "ok", "score": 0, "via": "mvip"}

    msg = str(sign_data.get("errmsg") or sign_data.get("msg") or sign_data)
    # 兼容参考脚本行为：create_daily_score 非0通常表示“今日已签到/无需重复签到”
    # 这类场景不再回退到活动接口，避免误判为“未完成签到”。
    print(f"  [签到-H5] 今日已签到或无需重复签到: {msg}")
    return {"status": "already_signed", "score": 0, "via": "mvip", "reason": msg}


def _add_apikey_query(url, apikey):
    """给URL追加apikey参数（如果不存在）"""
    if not apikey or "apikey=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}apikey={apikey}"


def _build_activity_urls(path):
    """
    构建活动中心候选URL（含mcsp代理/直连 + 带apikey/不带apikey）
    反编译小程序显示活动中心接口标准路径为:
      /api/cms_api/activity-center-im-service/im-svr/im/*
    """
    clean = path.lstrip("/")
    base_urls = [
        f"{ACTIVITY_API_HOST.rstrip('/')}/{clean}",
        f"{ACTIVITY_HOST.rstrip('/')}/{clean}",
    ]
    if ENABLE_MCSP_ACTIVITY_FALLBACK:
        base_urls.append(f"https://mcsp.midea.com/{clean}")

    urls = []
    for base in base_urls:
        urls.append(_add_apikey_query(base, ACTIVITY_APIKEY_PROD))
        urls.append(base)

    # 按顺序去重
    uniq = []
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
    return uniq


def _is_already_msg(msg):
    s = str(msg or "").lower()
    return (
        "already" in s
        or "已" in s
        or "重复" in s
        or "领取过" in s
        or "无可领取" in s
    )


def _extract_point_value(raw):
    """从接口返回中尽量提取积分值（兼容 dict/list/number）。"""
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return 0

    if isinstance(raw, dict):
        for k in ("points", "point", "score", "integralValue", "pointValue", "claimPoints", "totalPoints"):
            v = raw.get(k)
            if v is None or v == "":
                continue
            try:
                return int(float(v))
            except Exception:
                continue

        total = 0
        found = False
        for v in raw.values():
            p = _extract_point_value(v)
            if p > 0:
                total += p
                found = True
        return total if found else 0

    if isinstance(raw, (list, tuple)):
        total = 0
        for item in raw:
            p = _extract_point_value(item)
            if p > 0:
                total += p
        return total

    try:
        return int(float(raw))
    except Exception:
        return 0


def _activity_identity(act):
    """兼容不同活动列表字段，提取活动ID/名称/类型"""
    actv_id = str(
        act.get("actvId")
        or act.get("id")
        or act.get("growthActvId")
        or ""
    )
    actv_name = (
        act.get("actvName")
        or act.get("name")
        or act.get("title")
        or act.get("actvTitle")
        or ""
    )
    actv_type = str(act.get("actvType") or act.get("type") or "")
    return actv_id, str(actv_name), actv_type


# ==================== 积分获取功能 ====================

def do_daily_signin(uc_token, open_id, app_id, api_key, app_secret, alias=""):
    """
    每日签到

    说明:
      - 小程序“签到”入口实际跳转到 H5（score_welfare），并非稳定的直接 API。
      - 这里仅尝试可识别的活动接口签到；若不可自动化，则明确提示手动签到，不再反复重试。
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    signin_rec = load_signin_record()

    # 检查今日是否已签到
    # 用 openId 做主键更稳定，避免备注变更导致重复签到记录
    rec_owner = open_id or alias or "unknown"
    rec_key = f"{rec_owner}_{today_str}"
    if signin_rec.get(rec_key, False):
        print(f"  [签到] 今日({today_str})已签到，跳过")
        return {"status": "already_signed", "score": 0}

    # 优先走 H5 真实签到接口链路（参考 mvip.midea.cn）
    h5_result = mvip_signin(
        uc_token, open_id, app_id, api_key, app_secret,
        alias=alias
    )
    if h5_result.get("status") in ("ok", "already_signed"):
        signin_rec[rec_key] = True
        save_signin_record(signin_rec)
        return {"status": h5_result.get("status"), "score": 0}
    if h5_result.get("status") == "skip" and h5_result.get("reason"):
        print(f"  [签到-H5] 自动会话失败: {h5_result.get('reason')}")

    total_score = 0
    mark_signed = False
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    activity_headers = dict(headers)
    activity_headers["apikey"] = ACTIVITY_APIKEY_PROD

    list_payload = {
        "restParams": {
            "status": "ing",
            "openId": open_id,
            "_timeStamp": int(time.time() * 1000)
        },
        "headParams": head_params
    }

    list_paths = [
        "api/cms_api/activity-center-im-service/im-svr/im/growth/user/actvList",
        "api/cms_bff/mcsp-uc-mvip-bff/im-svr/im/growth/user/actvList",
    ]

    items = []
    list_loaded = False
    last_error = ""
    last_url = ""

    for list_path in list_paths:
        for url in _build_activity_urls(list_path):
            try:
                last_url = url
                resp = http_post(url, json=list_payload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    continue
                data = resp.json()
                code = str(data.get("code", ""))
                msg = data.get("msg", "")
                if code != "000000":
                    last_error = f"[{code}] {msg}"
                    continue
                list_loaded = True
                items = data.get("data", [])
                if not isinstance(items, list):
                    items = []
                print(f"  [签到] 发现 {len(items)} 个进行中活动，查找签到类...")
                break
            except Exception as e:
                last_error = str(e)
        if list_loaded:
            break

    if not list_loaded:
        if last_url:
            print(f"  [签到] 查询活动列表失败: {last_error or '未知错误'} | {last_url}")
        else:
            print(f"  [签到] 查询活动列表失败: {last_error or '未知错误'}")
        return {"status": "error", "score": 0}

    sign_candidates = 0

    for act in items:
        actv_id, actv_name, actv_type = _activity_identity(act)

        if not actv_id:
            continue

        # 匹配签到类活动
        is_signin = (
            actv_type in ("sign_in", "dailySign", "qiandao", "checkIn")
            or "签到" in str(actv_name)
            or "sign" in str(actv_name).lower()
        )
        if not is_signin:
            continue

        sign_candidates += 1

        join_paths = [
            "api/cms_api/activity-center-im-service/im-svr/im/growth/joinAct",
            "api/cms_bff/mcsp-uc-mvip-bff/im-svr/im/growth/joinAct",
        ]
        joined = False
        join_error = ""

        for jp in join_paths:
            if joined:
                break
            for jurl in _build_activity_urls(jp):
                jpayload = {
                    "restParams": {
                        "actvId": actv_id,
                        "openId": open_id,
                        "_timeStamp": int(time.time() * 1000)
                    },
                    "headParams": head_params
                }
                try:
                    jresp = http_post(jurl, json=jpayload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
                    if jresp.status_code != 200:
                        join_error = f"HTTP {jresp.status_code}"
                        continue
                    jdata = jresp.json()
                    jcode = str(jdata.get("code", ""))
                    jmsg = jdata.get("msg", "")
                    if jcode == "000000":
                        reward = jdata.get("data", {})
                        score = reward.get("score", 0) or reward.get("point", 0) or reward.get("integralValue", 0) or 0
                        try:
                            score_int = int(float(score))
                        except Exception:
                            score_int = 0
                        total_score += score_int
                        mark_signed = True
                        joined = True
                        print(f"  [签到] 成功参与 '{actv_name}' 获得 {score_int} 积分")
                        break
                    if _is_already_msg(jmsg):
                        mark_signed = True
                        joined = True
                        print(f"  [签到] '{actv_name}' 今日已签过")
                        break
                    join_error = f"[{jcode}] {jmsg}"
                except Exception as je:
                    join_error = str(je)

        if not joined and join_error:
            print(f"  [签到] '{actv_name}' 参与失败: {join_error}")

        time.sleep(random.uniform(0.4, 0.8))

    # 仅在确认已签到/已参与后才记录，避免因接口异常导致误判“今日已签”
    if mark_signed:
        signin_rec[rec_key] = True
        save_signin_record(signin_rec)

    if total_score > 0:
        print(f"  [签到] 本次共获得 {total_score} 积分")
        return {"status": "ok", "score": total_score}
    if mark_signed:
        print("  [签到] 今日已签到")
        return {"status": "already_signed", "score": 0}

    if sign_candidates == 0:
        print("  [签到] 未发现可直接API签到项，当前账号签到入口为 H5 交互页")
    else:
        print("  [签到] 未获得新积分，签到可能需在 H5 页面手动完成")
    print(f"  [签到] 手动入口: {SIGNIN_H5_URL}")
    return {"status": "manual_required", "score": 0, "url": SIGNIN_H5_URL}


def _parse_date_yyyy_mm_dd(v):
    """将时间值尽量转成 yyyy-MM-dd。"""
    if v is None:
        return ""
    try:
        if isinstance(v, (int, float)):
            ts = float(v)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        s = str(v).strip()
        if not s:
            return ""
        if "T" in s:
            s = s.split("T", 1)[0]
        if " " in s:
            s = s.split(" ", 1)[0]
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        for fmt in ("%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass
    except Exception:
        return ""
    return ""


def _post_shopgift_api(uc_token, app_id, api_key, app_secret, path, rest_params):
    """调用 shopGift 相关 API，返回统一结构。"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    activity_headers = dict(headers)
    activity_headers["apikey"] = ACTIVITY_APIKEY_PROD

    payload = {
        "restParams": dict(rest_params or {}),
        "headParams": head_params
    }
    payload["restParams"]["_timeStamp"] = int(time.time() * 1000)

    last_error = ""
    last_url = ""
    for url in _build_activity_urls(path):
        try:
            last_url = url
            resp = http_post(url, json=payload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                continue
            data = resp.json()
            code = str(data.get("code", ""))
            msg = data.get("msg", "")
            if code == "000000":
                return {"ok": True, "data": data.get("data"), "url": url}
            last_error = f"[{code}] {msg}"
        except Exception as e:
            last_error = str(e)
    return {"ok": False, "error": last_error or "unknown", "url": last_url}


def _resolve_shopgift_geo(uc_token, app_id, api_key, app_secret):
    """
    解析 shopGift 所需定位参数：
      1) 优先使用环境变量 ACTIVITY_LAT/ACTIVITY_LON/ACTIVITY_CITY_CODE
      2) 如仅有经纬度无 cityCode，尝试调用 queryCoordinate 推断 cityCode
      3) 最终兜底 cityCode=14406（与小程序无定位默认一致）
    """
    lat = ACTIVITY_LAT
    lon = ACTIVITY_LON
    city_code = ACTIVITY_CITY_CODE

    if lat and lon and not city_code:
        q = _post_shopgift_api(
            uc_token, app_id, api_key, app_secret,
            "api/cms_api/activity-center-im-service/im-svr/shopGift/user/queryCoordinate",
            {"latitude": lat, "longitude": lon}
        )
        if q.get("ok"):
            d = q.get("data", {}) or {}
            city_code = str(d.get("cityCode") or "")

    if not city_code:
        city_code = "14406"
    return {"lat": lat, "lon": lon, "cityCode": city_code}


def _resolve_shopgift_join_params(uc_token, open_id, app_id, api_key, app_secret, act, alias=""):
    """
    当活动列表缺少 prizeId/shopCode 时，尝试通过 shopGift 详情链路补齐参数：
      queryShopList -> queryActDetail -> queryPrizeList -> queryActBookingDateRange
    """
    actv_id, _, _ = _activity_identity(act)
    if not actv_id:
        return {"ok": False, "reason": "缺少actvId"}

    share_id = str(act.get("shareId") or "")
    user_id = str(act.get("userId") or "")
    geo = _resolve_shopgift_geo(uc_token, app_id, api_key, app_secret)

    # 1) 店铺
    shop_code = str(act.get("shopCode") or act.get("storeCode") or "")
    if not shop_code:
        candidates = [
            {
                "pageIndex": 1,
                "pageSize": 10,
                "cityCode": geo.get("cityCode", ""),
                "actvId": actv_id,
                "lat": geo.get("lat", ""),
                "lon": geo.get("lon", ""),
                "userId": user_id or None
            },
            {
                "pageIndex": 1,
                "pageSize": 10,
                "cityCode": geo.get("cityCode", ""),
                "actvId": actv_id,
                "lat": "",
                "lon": "",
                "userId": user_id or None
            },
            {
                "pageIndex": 1,
                "pageSize": 10,
                "cityCode": "14406",
                "actvId": actv_id,
                "lat": "",
                "lon": "",
                "userId": user_id or None
            }
        ]
        seen_sign = set()
        for params in candidates:
            sign = json.dumps(params, ensure_ascii=False, sort_keys=True)
            if sign in seen_sign:
                continue
            seen_sign.add(sign)

            q_shop = _post_shopgift_api(
                uc_token, app_id, api_key, app_secret,
                "api/cms_api/activity-center-im-service/im-svr/shopGift/act/queryShopList",
                params
            )
            if not q_shop.get("ok"):
                continue
            sdata = q_shop.get("data", {}) or {}
            slist = sdata.get("shopList") if isinstance(sdata.get("shopList"), list) else []
            if slist:
                shop_code = str(slist[0].get("shopCode") or "")
                if shop_code:
                    break

    if not shop_code:
        return {"ok": False, "reason": "无法获取shopCode"}

    # 2) 活动详情（补 bookingBeginDate / shareId）
    booking_date = _parse_date_yyyy_mm_dd(act.get("bookingDate"))
    q_detail = _post_shopgift_api(
        uc_token, app_id, api_key, app_secret,
        "api/cms_api/activity-center-im-service/im-svr/shopGift/act/queryActDetail",
        {
            "actvId": actv_id,
            "shareId": share_id,
            "shopCode": shop_code
        }
    )
    if q_detail.get("ok"):
        d = q_detail.get("data", {}) or {}
        if not share_id:
            share_id = str(d.get("shareId") or "")
        if not booking_date:
            booking_date = _parse_date_yyyy_mm_dd(d.get("bookingBeginDate") or d.get("startTime"))

    # 3) 奖品
    prize_id = str(act.get("prizeId") or "")
    if not prize_id:
        q_prize = _post_shopgift_api(
            uc_token, app_id, api_key, app_secret,
            "api/cms_api/activity-center-im-service/im-svr/shopGift/user/queryPrizeList",
            {
                "actvId": actv_id,
                "shopCode": shop_code
            }
        )
        if q_prize.get("ok"):
            plist = q_prize.get("data", [])
            if isinstance(plist, list) and plist:
                stock_first = None
                for p in plist:
                    if str(p.get("hasStock", "")) == "1":
                        stock_first = p
                        break
                picked = stock_first or plist[0]
                prize_id = str(picked.get("id") or picked.get("prizeId") or "")

    if not prize_id:
        return {"ok": False, "reason": "无法获取prizeId"}

    # 4) 预约日期范围（优先最早可预约日期）
    if not booking_date:
        q_range = _post_shopgift_api(
            uc_token, app_id, api_key, app_secret,
            "api/cms_api/activity-center-im-service/im-svr/shopGift/act/queryActBookingDateRange",
            {"actvId": actv_id}
        )
        if q_range.get("ok"):
            r = q_range.get("data", {}) or {}
            booking_date = _parse_date_yyyy_mm_dd(r.get("earliestBookingDate") or r.get("latestBookingDate"))

    if not booking_date:
        booking_date = datetime.now().strftime("%Y-%m-%d")

    return {
        "ok": True,
        "actvId": actv_id,
        "shareId": share_id,
        "shopCode": shop_code,
        "prizeId": prize_id,
        "bookingDate": booking_date,
        "channel": "miniAPP",
        "openId": open_id
    }


def query_and_join_activities(uc_token, open_id, app_id, api_key, app_secret, alias=""):
    """
    查询所有可用活动并参与可获积分的活动
    包括：签到类、浏览类、分享类等
    """
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    activity_headers = dict(headers)
    activity_headers["apikey"] = ACTIVITY_APIKEY_PROD
    total_gained = 0
    joined_count = 0
    skipped_non_auto = 0
    skip_reasons = {}
    growth_actv_id = ""

    # 1) 查询活动列表：优先使用反编译源码中的活动中心路径
    payload = {
        "restParams": {
            "status": "ing",
            "openId": open_id,
            "_timeStamp": int(time.time() * 1000)
        },
        "headParams": head_params
    }
    list_paths = [
        "api/cms_api/activity-center-im-service/im-svr/im/growth/user/actvList",
        "api/cms_bff/mcsp-uc-mvip-bff/im-svr/im/growth/user/actvList",
    ]

    items = []
    list_loaded = False
    last_error = ""
    last_url = ""
    for list_path in list_paths:
        for url in _build_activity_urls(list_path):
            try:
                last_url = url
                resp = http_post(url, json=payload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    continue
                data = resp.json()
                code = str(data.get("code", ""))
                msg = data.get("msg", "")
                if code != "000000":
                    last_error = f"[{code}] {msg}"
                    continue
                list_loaded = True
                items = data.get("data", [])
                if not isinstance(items, list):
                    items = []
                break
            except Exception as e:
                last_error = str(e)
        if list_loaded:
            break

    if not list_loaded:
        if last_url:
            print(f"  [活动] 查询活动列表失败: {last_error or '未知错误'} | {last_url}")
        else:
            print(f"  [活动] 查询活动列表失败: {last_error or '未知错误'}")
        return {"status": "error", "score": 0, "growthActvId": ""}

    if not items:
        print("  [活动] 无进行中的活动")
        return {"status": "ok", "score": 0, "activities": [], "growthActvId": ""}

    print(f"  [活动] 发现 {len(items)} 个进行中活动")
    if ACTIVITY_LAT and ACTIVITY_LON:
        city_mark = ACTIVITY_CITY_CODE or "自动推断/默认"
        print(f"  [活动] 使用固定定位: lat={ACTIVITY_LAT}, lon={ACTIVITY_LON}, cityCode={city_mark}")

    growth_join_paths = [
        "api/cms_api/activity-center-im-service/im-svr/im/growth/joinAct",
        "api/cms_bff/mcsp-uc-mvip-bff/im-svr/im/growth/joinAct",
    ]
    # 该接口在源码中属于 shopGift 体系，需 prizeId/shopCode/bookingDate
    shopgift_join_paths = [
        "api/cms_api/activity-center-im-service/im-svr/shopGift/user/joinAct",
    ]

    def _mark_skip(reason):
        key = str(reason or "未知原因")
        skip_reasons[key] = skip_reasons.get(key, 0) + 1

    def _take_join_reward(jdata, name):
        nonlocal total_gained, joined_count
        reward = jdata.get("data", {})
        score = reward.get("score", 0) or reward.get("point", 0) or reward.get("integralValue", 0) or 0
        coins = reward.get("coins", 0) or 0
        try:
            score_int = int(float(score))
        except Exception:
            score_int = 0
        total_gained += score_int
        joined_count += 1
        if score_int > 0 or coins:
            print(f"  [活动] 参与'{name}' 获得 {score_int}积分")

    def _try_growth_join(actv_id, actv_name, share_id=""):
        last_err = ""
        for join_path in growth_join_paths:
            for join_url in _build_activity_urls(join_path):
                payload = {
                    "restParams": {
                        "actvId": actv_id,
                        "openId": open_id,
                        "shareId": share_id or "",
                        "_timeStamp": int(time.time() * 1000)
                    },
                    "headParams": head_params
                }
                try:
                    resp = http_post(join_url, json=payload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
                    if resp.status_code != 200:
                        last_err = f"HTTP {resp.status_code}"
                        continue
                    data = resp.json()
                    code = str(data.get("code", ""))
                    msg = data.get("msg", "")
                    if code == "000000":
                        _take_join_reward(data, actv_name)
                        return True, ""
                    if _is_already_msg(msg):
                        return True, ""
                    last_err = f"[{code}] {msg}"
                except Exception as e:
                    last_err = str(e)
        return False, last_err

    for act in items:
        actv_id, actv_name, actv_type = _activity_identity(act)

        if not actv_id:
            _mark_skip("缺少actvId")
            skipped_non_auto += 1
            continue
        if not growth_actv_id:
            growth_actv_id = actv_id

        # 签到类已在 do_daily_signin 单独处理
        if actv_type in ("sign_in", "dailySign", "qiandao", "checkIn"):
            continue

        share_id = str(act.get("shareId", "") or "")

        # 先尝试 growth 通用 join（部分活动可直接参与）
        joined, growth_err = _try_growth_join(actv_id, actv_name, share_id=share_id)
        if joined:
            time.sleep(random.uniform(0.3, 0.8))
            continue

        # shopGift 参与活动常需 prizeId/shopCode/bookingDate，优先用列表字段，不足时走详情链路补齐
        prize_list = act.get("prizeList") if isinstance(act.get("prizeList"), list) else []
        prize_id = act.get("prizeId") or (prize_list[0].get("id") if prize_list else "")
        shop_code = act.get("shopCode") or act.get("storeCode") or ""
        booking_date = _parse_date_yyyy_mm_dd(act.get("bookingDate"))

        if not prize_id or not shop_code or not booking_date:
            resolved = _resolve_shopgift_join_params(
                uc_token, open_id, app_id, api_key, app_secret, act, alias=alias
            )
            if resolved.get("ok"):
                prize_id = resolved.get("prizeId", prize_id)
                shop_code = resolved.get("shopCode", shop_code)
                booking_date = resolved.get("bookingDate", booking_date)
                if not share_id:
                    share_id = resolved.get("shareId", "")
            else:
                skipped_non_auto += 1
                reason = resolved.get("reason") or "shopGift参数不足"
                _mark_skip(reason)
                if growth_err:
                    _mark_skip(f"growthJoin失败:{growth_err}")
                continue

        joined = False
        join_error = ""

        for join_path in shopgift_join_paths:
            if joined:
                break
            for join_url in _build_activity_urls(join_path):
                join_payload = {
                    "restParams": {
                        "actvId": actv_id,
                        "shareId": share_id,
                        "shopCode": shop_code,
                        "prizeId": prize_id,
                        "bookingDate": booking_date,
                        "channel": act.get("channel", "miniAPP"),
                        "openId": open_id,
                        "_timeStamp": int(time.time() * 1000)
                    },
                    "headParams": head_params
                }
                try:
                    jresp = http_post(join_url, json=join_payload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
                    if jresp.status_code != 200:
                        join_error = f"HTTP {jresp.status_code}"
                        continue
                    jdata = jresp.json()
                    jcode = str(jdata.get("code", ""))
                    jmsg = jdata.get("msg", "")

                    if jcode == "000000":
                        _take_join_reward(jdata, actv_name)
                        joined = True
                        break

                    if _is_already_msg(jmsg):
                        joined = True
                        break

                    join_error = f"[{jcode}] {jmsg}"
                except Exception as e:
                    join_error = str(e)

        if not joined and join_error:
            print(f"  [活动] '{actv_name or actv_id}' 参与失败: {join_error}")
            _mark_skip(f"shopGiftJoin失败:{join_error}")
            if growth_err:
                _mark_skip(f"growthJoin失败:{growth_err}")
            skipped_non_auto += 1

        time.sleep(random.uniform(0.3, 0.8))

    if skipped_non_auto > 0:
        reason_parts = [f"{k}={v}" for k, v in skip_reasons.items()]
        if reason_parts:
            print(f"  [活动] {skipped_non_auto} 个活动未自动参与，原因: {', '.join(reason_parts)}")
        else:
            print(f"  [活动] {skipped_non_auto} 个活动需在小程序页面手动参与，已跳过自动join")
    print(f"  [活动] 共参与 {joined_count} 个活动，获得约 {total_gained} 积分")
    return {"status": "ok", "score": total_gained, "growthActvId": growth_actv_id}


def check_points_unread(uc_token, open_id, app_id, api_key, app_secret, alias=""):
    """查询积分未读消息/待领取状态"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    activity_headers = dict(headers)
    activity_headers["apikey"] = ACTIVITY_APIKEY_PROD

    payload = {
        "restParams": {
            "openId": open_id,
            "_timeStamp": int(time.time() * 1000)
        },
        "headParams": head_params
    }
    paths = [
        "api/cms_api/activity-center-im-service/im-svr/im/newProduct/getPointsUnreadInformation",
    ]

    last_error = ""
    last_url = ""
    got_404 = False
    got_non_404 = False
    for path in paths:
        for url in _build_activity_urls(path):
            try:
                last_url = url
                resp = http_post(url, json=payload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    if resp.status_code == 404:
                        got_404 = True
                    else:
                        got_non_404 = True
                    continue
                got_non_404 = True
                data = resp.json()
                code = str(data.get("code", ""))
                msg = data.get("msg", "")
                if code != "000000":
                    last_error = f"[{code}] {msg}"
                    continue

                info = data.get("data", {})
                has_unread = info.get("hasUnread", False)
                count = info.get("unreadCount", 0)
                if has_unread or count > 0:
                    print(f"  [积分提醒] 有 {count} 条未读积分消息")
                    return {"status": "has_unread", "count": count, "data": info}
                return {"status": "no_unread"}
            except Exception as e:
                last_error = str(e)

    if got_404 and not got_non_404:
        print("  [积分提醒] 接口返回 404（疑似已下线/改版），已跳过")
        return {"status": "skip"}

    if last_error.startswith("HTTP 404"):
        print("  [积分提醒] 接口返回 404（疑似已下线/改版），已跳过")
        return {"status": "skip"}

    if last_error:
        if last_url:
            print(f"  [积分提醒] 查询失败: {last_error} | {last_url}")
        else:
            print(f"  [积分提醒] 查询失败: {last_error}")
    return {"status": "ok"}


def claim_all_integrals(
    uc_token, uid, user_id, mobile, app_id, api_key, app_secret, alias=""
):
    """
    一键领取积分兜底（claimAll）
    部分场景 preview/pending 列表为空，但 claimAll 仍可成功领取。
    """
    if not uid:
        return {"status": "skip", "score": 0, "count": 0}

    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    payload = {
        "restParams": {
            "brand": 1,
            "uid": uid,
            "claimWay": 1
        },
        "headParams": head_params
    }
    if user_id:
        payload["restParams"]["userId"] = user_id
    if mobile:
        payload["restParams"]["mobile"] = mobile

    url = "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/integral/selfService/claimAll.do"
    try:
        resp = http_post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return {"status": "error", "score": 0, "count": 0}

        data = resp.json()
        code = str(data.get("code", ""))
        msg = data.get("msg", "")
        if code != "000000":
            if _is_already_msg(msg):
                return {"status": "already", "score": 0, "count": 0}
            return {"status": "error", "score": 0, "count": 0}

        d = data.get("data")
        points = _extract_point_value(d)
        if points > 0:
            print(f"  [待领积分] claimAll 兜底成功，到账 {points} 积分")
            return {"status": "ok", "score": points, "count": 1}
        return {"status": "empty", "score": 0, "count": 0}
    except Exception:
        return {"status": "error", "score": 0, "count": 0}


def claim_pending_integrals(
    uc_token, open_id, uid, user_id, mobile, app_id, api_key, app_secret, alias=""
):
    """
    自动领取“待领取积分”（购物积分等）
    小程序对应接口：
      - integral/selfService/previewAndWaitList.do
      - integral/selfService/claim.do
    """
    if not uid:
        return {"status": "skip", "score": 0}

    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)

    preview_payload = {
        "restParams": {
            "brand": 1,
            "uid": uid,
        },
        "headParams": head_params
    }
    if user_id:
        preview_payload["restParams"]["userId"] = user_id

    preview_urls = [
        "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/integral/selfService/previewAndWaitList.do",
        "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/integral/selfService/pendingList.do",
    ]

    pending_items = []
    last_error = ""
    for url in preview_urls:
        try:
            resp = http_post(url, json=preview_payload, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                continue
            data = resp.json()
            code = str(data.get("code", ""))
            msg = data.get("msg", "")
            if code != "000000":
                last_error = f"[{code}] {msg}"
                continue
            arr = data.get("data", [])
            if isinstance(arr, list):
                pending_items = arr
                break
        except Exception as e:
            last_error = str(e)

    if not pending_items:
        if last_error:
            print(f"  [待领积分] 查询失败: {last_error}，尝试 claimAll 兜底...")
        fallback = claim_all_integrals(
            uc_token, uid, user_id, mobile, app_id, api_key, app_secret, alias=alias
        )
        if fallback.get("status") != "error":
            if fallback.get("status") in ("empty", "already"):
                print("  [待领积分] 暂无可领取项")
            return fallback
        if not last_error:
            print("  [待领积分] 暂无可领取项")
        return {"status": "empty", "score": 0, "count": 0}

    # claimState=-1通常表示暂不可领取；其余状态尝试领取
    claimable = [x for x in pending_items if str(x.get("claimState", "")) != "-1"]
    coin_codes = []
    for item in claimable:
        code = item.get("coinCode")
        if code and code not in coin_codes:
            coin_codes.append(code)

    if not coin_codes:
        fallback = claim_all_integrals(
            uc_token, uid, user_id, mobile, app_id, api_key, app_secret, alias=alias
        )
        if fallback.get("status") != "error":
            if fallback.get("status") in ("empty", "already"):
                print("  [待领积分] 暂无可领取项")
            return fallback
        print("  [待领积分] 暂无可领取项")
        return {"status": "empty", "score": 0, "count": 0}

    claim_payload = {
        "restParams": {
            "brand": 1,
            "uid": uid,
            "claimWay": 1,   # 1=全部领取，2=单条/剩余
            "coinCodes": coin_codes
        },
        "headParams": head_params
    }
    if user_id:
        claim_payload["restParams"]["userId"] = user_id
    if mobile:
        claim_payload["restParams"]["mobile"] = mobile

    claim_url = "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/integral/selfService/claim.do"
    try:
        resp = http_post(claim_url, json=claim_payload, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            print(f"  [待领积分] 领取失败: HTTP {resp.status_code}")
            return {"status": "error", "score": 0, "count": 0}

        data = resp.json()
        code = str(data.get("code", ""))
        msg = data.get("msg", "")
        if code == "000000":
            d = data.get("data", {}) or {}
            points = _extract_point_value(d)
            print(f"  [待领积分] 成功领取 {len(coin_codes)} 笔，到账 {points} 积分")
            return {"status": "ok", "score": points, "count": len(coin_codes)}

        if _is_already_msg(msg):
            print("  [待领积分] 无可重复领取项")
            return {"status": "already", "score": 0, "count": 0}

        print(f"  [待领积分] 领取失败[{code}]: {msg}，尝试 claimAll 兜底...")
        fallback = claim_all_integrals(
            uc_token, uid, user_id, mobile, app_id, api_key, app_secret, alias=alias
        )
        if fallback.get("status") != "error":
            if fallback.get("status") in ("empty", "already"):
                print("  [待领积分] 暂无可领取项")
            return fallback
        return {"status": "error", "score": 0, "count": 0}
    except Exception as e:
        print(f"  [待领积分] 领取异常: {e}，尝试 claimAll 兜底...")
        fallback = claim_all_integrals(
            uc_token, uid, user_id, mobile, app_id, api_key, app_secret, alias=alias
        )
        if fallback.get("status") != "error":
            if fallback.get("status") in ("empty", "already"):
                print("  [待领积分] 暂无可领取项")
            return fallback
        return {"status": "error", "score": 0, "count": 0}


def report_task_progress(uc_token, open_id, uid, app_id, api_key, app_secret, alias=""):
    """
    上报任务进度 - 触发各类任务进度条更新
    包括：种草进度、订单任务进度、购买金额进度、设备绑定进度等
    这些上报可能会触发积分发放
    """
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)

    task_apis = [
        ("plantGrassProgressBar", "api/cms_bff/mcsp-uc-mvip-bff/mfans/task/plantGrassProgressBar.do", "种草进度"),
        ("orderTaskProgressBar", "api/cms_bff/mcsp-uc-mvip-bff/mfans/task/orderTaskProgressBar.do", "订单任务进度"),
        ("shareBuyTaskProgressBar", "api/cms_bff/mcsp-uc-mvip-bff/mfans/task/shareBuyTaskProgressBar.do", "分享购进度"),
        ("purchaseAmountProgressBar", "api/cms_bff/mcsp-uc-mvip-bff/mfans/task/purchaseAmountProgressBar.do", "消费金额进度"),
        ("applianceBindProgressBar", "api/cms_bff/mcsp-uc-mvip-bff/mfans/task/applianceBindProgressBar.do", "设备绑定进度"),
    ]

    reported = []

    for api_name, url, desc in task_apis:
        try:
            full_url = f"https://mcsp.midea.com/{url}" if not url.startswith("http") else url
            payload = {
                "restParams": {
                    "openId": open_id,
                    "uid": uid,
                    "_timeStamp": int(time.time() * 1000)
                },
                "headParams": head_params
            }

            resp = http_post(full_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
            data = resp.json()

            if str(data.get("code")) == "000000":
                result = data.get("data", {})
                # 检查是否有积分奖励返回
                reported.append(desc)
            # 非000000也可能是正常的（比如没有对应任务时不报错）
            elif "noTask" in str(data.get("msg", "")).lower() or "not found" in str(data.get("msg", "")).lower():
                pass  # 无此类任务，正常
            else:
                pass  # 忽略其他错误

        except Exception as e:
            print(f"  [任务进度] {desc} 上报异常: {e}")

        time.sleep(random.uniform(0.2, 0.5))

    if reported:
        print(f"  [任务进度] 已上报: {', '.join(reported)}")
    return {"status": "ok", "reported": reported}


def get_mfans_info_v2(uc_token, app_id, api_key, app_secret, alias=""):
    """查询美粉信息V2（含升级弹窗/保级弹窗提示）。"""
    result = post_mcsp_bff(
        uc_token, app_id, api_key, app_secret,
        "api/cms_bff/mcsp-uc-mvip-bff/member/getMfansInfoV2.do",
        rest_params={
            "brand": 1,
            "platform": "MIDEA_MINIPROGRAM_INTEST",
            "accountBrandList": [1, 3, 2, 5]
        }
    )
    if result.get("ok"):
        return {"status": "ok", "data": result.get("data", {}) or {}}
    err = result.get("error") or f"[{result.get('code', '')}] {result.get('msg', '')}".strip()
    return {"status": "error", "error": err}


def _popup_flag_on(v):
    """兼容弹窗开关字段，判断是否为“需要处理”状态。"""
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y", "popup", "show")


def handle_mfans_popup_rewards(uc_token, app_id, api_key, app_secret, alias=""):
    """
    自动处理美粉弹窗奖励：
      1) 邀请奖励 invitationReceive
      2) 升级确认 acquireMfansLevel
      3) 保级弹窗确认 mfansPopupUpdate(popUpType=2)
    """
    info = get_mfans_info_v2(uc_token, app_id, api_key, app_secret, alias=alias)
    if info.get("status") != "ok":
        print(f"  [美粉弹窗] 查询失败: {info.get('error', '未知错误')}")
        return {"status": "error", "score": 0}

    data = info.get("data", {}) or {}
    remind_acq = data.get("remindAcqMfansLevel", {})
    remind_maintain = data.get("remindMaintainLevel", {})
    if not isinstance(remind_acq, dict):
        remind_acq = {}
    if not isinstance(remind_maintain, dict):
        remind_maintain = {}

    total_points = 0
    actions = []

    # 1) 邀请奖励 / 升级弹窗
    acq_popup = _popup_flag_on(remind_acq.get("popup"))
    change_way = str(remind_acq.get("changeWay") or "")
    to_level_id = str(remind_acq.get("toMfansLevelId") or remind_acq.get("mfansLevelId") or "")

    if acq_popup:
        if change_way == "1":
            r = post_mcsp_bff(
                uc_token, app_id, api_key, app_secret,
                "api/cms_bff/mcsp-uc-mvip-bff/member/mfans/invitation/receive.do",
                rest_params={"brand": 1}
            )
            if r.get("ok"):
                p = _extract_point_value(r.get("data"))
                total_points += p
                actions.append("邀请奖励")
                print(f"  [美粉弹窗] 邀请奖励领取成功{f'，+{p}积分' if p > 0 else ''}")
            else:
                msg = r.get("error") or f"[{r.get('code', '')}] {r.get('msg', '')}".strip()
                if _is_already_msg(r.get("msg", "")):
                    print("  [美粉弹窗] 邀请奖励已领取")
                else:
                    print(f"  [美粉弹窗] 邀请奖励领取失败: {msg}")
        elif change_way == "2" and to_level_id:
            r = post_mcsp_bff(
                uc_token, app_id, api_key, app_secret,
                "api/cms_bff/mcsp-uc-mvip-bff/member/mfans/acquireMfansLevel.do",
                rest_params={"brand": 1, "toMfansLevelId": to_level_id}
            )
            if r.get("ok"):
                p = _extract_point_value(r.get("data"))
                total_points += p
                actions.append("升级奖励")
                print(f"  [美粉弹窗] 升级奖励处理成功{f'，+{p}积分' if p > 0 else ''}")
            else:
                msg = r.get("error") or f"[{r.get('code', '')}] {r.get('msg', '')}".strip()
                if _is_already_msg(r.get("msg", "")):
                    print("  [美粉弹窗] 升级奖励已处理")
                else:
                    print(f"  [美粉弹窗] 升级奖励处理失败: {msg}")

    # 2) 保级弹窗确认
    maintain_popup = _popup_flag_on(remind_maintain.get("popup"))
    if maintain_popup:
        r = post_mcsp_bff(
            uc_token, app_id, api_key, app_secret,
            "api/cms_bff/mcsp-uc-mvip-bff/member/mfans/mfansPopupUpdate.do",
            rest_params={"popUpType": 2}
        )
        if r.get("ok"):
            actions.append("保级弹窗确认")
            print("  [美粉弹窗] 保级弹窗已确认")
        else:
            msg = r.get("error") or f"[{r.get('code', '')}] {r.get('msg', '')}".strip()
            print(f"  [美粉弹窗] 保级弹窗确认失败: {msg}")

    if not actions:
        return {"status": "empty", "score": 0}
    return {"status": "ok", "score": total_points, "actions": actions}


def receive_maintain_rights(uc_token, app_id, api_key, app_secret, alias=""):
    """检测并领取待领取的美粉维系权益。"""
    check = post_mcsp_bff(
        uc_token, app_id, api_key, app_secret,
        "api/cms_bff/mcsp-uc-mvip-bff/rights/app/checkPendingReceiveMaintainRights.do",
        rest_params={}
    )
    if not check.get("ok"):
        msg = check.get("error") or f"[{check.get('code', '')}] {check.get('msg', '')}".strip()
        print(f"  [维系权益] 检测失败: {msg}")
        return {"status": "error", "score": 0}

    cdata = check.get("data", {}) or {}
    need_receive = bool(
        cdata.get("havingReveiveMfansRights")
        or cdata.get("havingReceiveMfansRights")
        or cdata.get("needReceive")
    )
    if not need_receive:
        return {"status": "empty", "score": 0}

    rec = post_mcsp_bff(
        uc_token, app_id, api_key, app_secret,
        "api/cms_bff/mcsp-uc-mvip-bff/rights/app/receiveMaintainRights.do",
        rest_params={}
    )
    if rec.get("ok"):
        p = _extract_point_value(rec.get("data"))
        if p > 0:
            print(f"  [维系权益] 领取成功，+{p}积分")
        else:
            print("  [维系权益] 领取成功")
        return {"status": "ok", "score": p}

    msg = rec.get("error") or f"[{rec.get('code', '')}] {rec.get('msg', '')}".strip()
    if _is_already_msg(rec.get("msg", "")):
        print("  [维系权益] 已领取或暂无可领取")
        return {"status": "already", "score": 0}
    print(f"  [维系权益] 领取失败: {msg}")
    return {"status": "error", "score": 0}


def claim_level_up_rewards(uc_token, app_id, api_key, app_secret, alias=""):
    """
    领取会员升级积分奖励（源码内 pointId）：
      - 金卡升级奖励: 206
      - 黑钻升级奖励: 100177
    """
    point_ids = [str(MIDEA_GOLD_GRADE_POINT_ID), str(MIDEA_DIAMOND_GRADE_POINT_ID)]
    check = post_mcsp_bff(
        uc_token, app_id, api_key, app_secret,
        "api/cms_bff/mcsp-uc-mvip-bff/app/task/levelUpRewardButton.do",
        rest_params={"brand": 1, "pointIdCheckList": point_ids}
    )
    if not check.get("ok"):
        msg = check.get("error") or f"[{check.get('code', '')}] {check.get('msg', '')}".strip()
        print(f"  [升级奖励] 查询失败: {msg}")
        return {"status": "error", "score": 0, "claimed": 0}

    rows = check.get("data", [])
    if not isinstance(rows, list):
        rows = []
    if not rows:
        return {"status": "empty", "score": 0, "claimed": 0}

    total_points = 0
    claimed = 0

    for row in rows:
        pid = str(row.get("pointId") or "")
        status = str(row.get("status") or "")
        # 小程序状态：1未达成，2可领取，3已领取，4已过期
        if status != "2" or not pid:
            continue

        rec = post_mcsp_bff(
            uc_token, app_id, api_key, app_secret,
            "api/cms_bff/mcsp-uc-mvip-bff/app/task/acquireLevelUpReward.do",
            rest_params={"brand": 1, "pointId": pid}
        )
        if rec.get("ok"):
            rdata = rec.get("data", {}) or {}
            ok_flag = rdata.get("status")
            points = _extract_point_value(rdata)
            if points <= 0:
                try:
                    points = int(float(row.get("pointValue", 0) or 0))
                except Exception:
                    points = 0
            if ok_flag is False:
                desc = str(rdata.get("desc") or "状态未通过")
                print(f"  [升级奖励] pointId={pid} 领取未通过: {desc}")
                continue
            claimed += 1
            total_points += max(0, points)
            print(f"  [升级奖励] pointId={pid} 领取成功{f'，+{points}积分' if points > 0 else ''}")
        else:
            msg = rec.get("error") or f"[{rec.get('code', '')}] {rec.get('msg', '')}".strip()
            if _is_already_msg(rec.get("msg", "")):
                print(f"  [升级奖励] pointId={pid} 已领取")
            else:
                print(f"  [升级奖励] pointId={pid} 领取失败: {msg}")

        time.sleep(random.uniform(0.2, 0.5))

    if claimed == 0:
        return {"status": "empty", "score": 0, "claimed": 0}
    return {"status": "ok", "score": total_points, "claimed": claimed}


def check_mfans_growth_task(uc_token, open_id, uid, app_id, api_key, app_secret, alias=""):
    """检查美粉成长营任务并尝试完成"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)

    try:
        url = "api/cms_bff/mcsp-uc-mvip-bff/mfans/task/checkMfansGrowthCampTask.do"
        full_url = f"https://mcsp.midea.com/{url}"
        payload = {
            "restParams": {
                "openId": open_id,
                "uid": uid,
                "_timeStamp": int(time.time() * 1000)
            },
            "headParams": head_params
        }

        resp = http_post(full_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        data = resp.json()

        if str(data.get("code")) == "000000":
            tasks = data.get("data", [])
            if isinstance(tasks, list):
                print(f"  [成长营任务] 发现 {len(tasks)} 个可完成任务")
                for task in tasks[:10]:  # 最多处理前10个
                    task_id = task.get("taskId", task.get("id", ""))
                    task_name = task.get("taskName", task.get("name", ""))
                    print(f"    - 任务: {task_name}")
        else:
            pass  # 无任务或已完成

    except Exception as e:
        print(f"  [成长营任务] 检查异常: {e}")

    return {"status": "ok"}


def take_batch_coupons(uc_token, open_id, app_id, api_key, app_secret, alias=""):
    """尝试领取可用优惠券/权益（部分可能有积分奖励）"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    
    try:
        url = "api/cms_bff/mcsp-uc-mvip-bff/rights/app/takeBatchCoupon.do"
        full_url = f"https://mcsp.midea.com/{url}"
        payload = {
            "restParams": {
                "openId": open_id,
                "_timeStamp": int(time.time() * 1000)
            },
            "headParams": head_params
        }
        
        resp = http_post(full_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        
        if str(data.get("code")) == "000000":
            coupons = data.get("data", [])
            if isinstance(coupons, list) and len(coupons) > 0:
                print(f"  [领券] 成功领取 {len(coupons)} 张优惠券/权益")
                return {"status": "ok", "count": len(coupons)}
            else:
                return {"status": "empty", "msg": "无可领取权益"}
        elif str(data.get("code")) == "000353":
            print(f"  [领券] 已领取过或不可领取")
            return {"status": "already", "msg": data.get("msg", "已领取")}
        else:
            return {"status": "error", "msg": data.get("msg", "未知")}
            
    except Exception as e:
        print(f"  [领券] 异常: {e}")
        return {"status": "error", "msg": str(e)}
    
    return {"status": "ok"}


def query_available_coupons(uc_token, open_id, app_id, api_key, app_secret, alias=""):
    """查询可用优惠券列表（了解有哪些可领）"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    
    try:
        url = "api/cms_bff/mcsp-uc-mvip-bff/rights/app/couponsAvailableList.do"
        full_url = f"https://mcsp.midea.com/{url}"
        payload = {
            "restParams": {
                "openId": open_id,
                "_timeStamp": int(time.time() * 1000)
            },
            "headParams": head_params
        }
        
        resp = http_post(full_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        
        if str(data.get("code")) == "000000":
            coupons = data.get("data", [])
            if isinstance(coupons, list):
                print(f"  [可用优惠券] 共 {len(coupons)} 张可用")
                for c in coupons[:5]:
                    name = c.get("couponName", c.get("name", ""))
                    print(f"    - {name}")
                return {"status": "ok", "count": len(coupons)}
        return {"status": "ok"}
            
    except Exception as e:
        pass
    
    return {"status": "ok"}


def get_mfans_rights_worth(uc_token, open_id, app_id, api_key, app_secret, alias=""):
    """查询美粉权益价值"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    
    try:
        url = "api/cms_bff/mcsp-uc-mvip-bff/app/rights/getMfansRightsWorth.do"
        full_url = f"https://mcsp.midea.com/{url}"
        payload = {
            "restParams": {
                "openId": open_id,
                "_timeStamp": int(time.time() * 1000)
            },
            "headParams": head_params
        }
        
        resp = http_post(full_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        
        if str(data.get("code")) == "000000":
            worth = data.get("data", {})
            value = worth.get("rightsValueAmount", worth.get("totalValue", 0))
            if value:
                print(f"  [权益价值] 当前美粉权益价值: {value}")
                return {"status": "ok", "value": value}
    except Exception as e:
        pass
    
    return {"status": "ok"}


def get_rights_v2(uc_token, open_id, app_id, api_key, app_secret, alias=""):
    """查询用户权益V2（各类权益详情）"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    
    try:
        url = "api/cms_bff/mcsp-uc-mvip-bff/rights/app/getRightsV2.do"
        full_url = f"https://mcsp.midea.com/{url}"
        payload = {
            "restParams": {
                "openId": open_id,
                "_timeStamp": int(time.time() * 1000)
            },
            "headParams": head_params
        }
        
        resp = http_post(full_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        
        if str(data.get("code")) == "000000":
            rights = data.get("data", {})
            if rights:
                print(f"  [权益详情] 已获取用户权益信息")
                # 检查是否有积分类型权益
                integral_count = 0
                if isinstance(rights, list):
                    for r in rights:
                        if r.get("rightsType") == "6":  # 积分类型
                            integral_count += 1
                    if integral_count > 0:
                        print(f"  [权益详情] 发现 {integral_count} 个积分类待领取权益")
                return {"status": "ok", "data": rights}
    except Exception as e:
        pass
    
    return {"status": "ok"}


def query_act_list(uc_token, open_id, app_id, api_key, app_secret, alias="", growth_actv_id=""):
    """查询活动中心活动列表（栏目级别）"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    activity_headers = dict(headers)
    activity_headers["apikey"] = ACTIVITY_APIKEY_PROD

    payload = {
        "restParams": {
            "pageIndex": 1,
            "pageSize": 20,
            "releaseStatus": 1,
            "_timeStamp": int(time.time() * 1000)
        },
        "headParams": head_params
    }
    if growth_actv_id:
        payload["restParams"]["growthActvId"] = growth_actv_id
    paths = [
        "api/cms_api/activity-center-im-service/im-svr/im/column/actList",
        "api/cms_api/activity-center-im-service/im-svr/im/column/queryMFansActivityList",
        "api/cms_bff/mcsp-uc-mvip-bff/im-svr/im/column/actList",
    ]

    last_error = ""
    last_url = ""
    for path in paths:
        for url in _build_activity_urls(path):
            try:
                last_url = url
                resp = http_post(url, json=payload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    continue
                data = resp.json()
                code = str(data.get("code", ""))
                msg = data.get("msg", "")
                if code != "000000":
                    last_error = f"[{code}] {msg}"
                    continue
                items = data.get("data", [])
                if isinstance(items, list):
                    print(f"  [活动列表] 发现 {len(items)} 个活动栏目")
                    return {"status": "ok", "count": len(items), "items": items}
            except Exception as e:
                last_error = str(e)

    if last_error:
        if last_url:
            print(f"  [活动列表] 查询失败: {last_error} | {last_url}")
        else:
            print(f"  [活动列表] 查询失败: {last_error}")
    return {"status": "ok"}


def get_award_list(uc_token, open_id, app_id, api_key, app_secret, alias="", growth_actv_id=""):
    """查询奖励列表（已获得/待领取的奖励）"""
    headers, head_params = build_activity_headers(uc_token, api_key, app_id, app_secret)
    activity_headers = dict(headers)
    activity_headers["apikey"] = ACTIVITY_APIKEY_PROD

    payload = {
        "restParams": {
            "pageIndex": 1,
            "pageSize": 30,
            "_timeStamp": int(time.time() * 1000)
        },
        "headParams": head_params
    }
    if growth_actv_id:
        payload["restParams"]["growthActvId"] = growth_actv_id
    paths = [
        "api/cms_api/activity-center-im-service/im-svr/im/growth/awardList",
        "api/cms_bff/mcsp-uc-mvip-bff/im-svr/im/growth/awardList",
    ]

    last_error = ""
    last_url = ""
    for path in paths:
        for url in _build_activity_urls(path):
            try:
                last_url = url
                resp = http_post(url, json=payload, headers=activity_headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    continue
                data = resp.json()
                code = str(data.get("code", ""))
                msg = data.get("msg", "")
                if code != "000000":
                    last_error = f"[{code}] {msg}"
                    continue
                awards = data.get("data", [])
                if not isinstance(awards, list):
                    awards = []

                total_points = 0
                for aw in awards:
                    pts = aw.get("point", aw.get("score", aw.get("points", 0)) or 0)
                    try:
                        total_points += int(float(pts))
                    except Exception:
                        pass

                print(f"  [奖励列表] 共 {len(awards)} 条记录，累计约 {total_points} 积分/奖励")
                return {"status": "ok", "count": len(awards), "totalPoints": total_points}
            except Exception as e:
                last_error = str(e)

    if last_error:
        if last_url:
            print(f"  [奖励列表] 查询失败: {last_error} | {last_url}")
        else:
            print(f"  [奖励列表] 查询失败: {last_error}")
    return {"status": "ok"}


def send_push_plus(token, title, content):
    """发送 Push Plus 消息"""
    if not token:
        return False
    url = "https://www.pushplus.plus/send"
    data = {
        "token": token,
        "title": title,
        "content": content,
        "template": "json"
    }
    try:
        resp = http_post(url, json=data, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("code") == 200:
                print("推送成功")
                return True
            else:
                print(f"推送失败: {result.get('msg')}")
        else:
            print(f"推送HTTP错误: {resp.status_code}")
    except Exception as e:
        print(f"推送异常: {e}")
    return False


# ==================== 主函数 ====================
def main():
    config = get_env_config()
    accounts = get_accounts(config["wechat_server"])
    if not accounts:
        print("未找到有效账号，请检查协议服务（WECHAT_SERVER/ADMIN_KEY）及在线账号")
        sys.exit(1)

    # 加载缓存和记录
    cache = load_cache()
    reminder_record = load_reminder_record()

    # 品牌列表
    brand_list_str = os.getenv("BRAND_LIST", "1,2,3,5")
    brand_list = [int(b.strip()) for b in brand_list_str.split(",") if b.strip().isdigit()]
    if not brand_list:
        brand_list = [1, 2, 3, 5]

    # 存储每个账号的处理结果
    account_results = []

    # ========== 输出：账号验证阶段标题 ==========
    print("=" * 61)
    print("账号验证阶段")
    print("=" * 61)
    print(f"共发现 {len(accounts)} 个账号")
    print(f"功能开关: 签到={'开' if ENABLE_SIGNIN else '关'} | 任务={'开' if ENABLE_TASKS else '关'} | 活动={'开' if ENABLE_ACT else '关'}")
    print("=" * 61)

    valid_count = 0
    total_points_gained = 0  # 所有账号本轮获得的积分总计

    for idx, (wxid, remark) in enumerate(accounts, start=1):
        display = remark or wxid
        print(f"\n[{idx}/{len(accounts)}] 账号：{display} (wxid: {wxid})")

        token_valid = False
        member_data = None
        uc_token = None
        open_id = None
        c4a_uid = None
        user_id = ""
        mobile = ""
        total_score = 0
        nickname = "未知"
        account_points = 0  # 本账号本轮获得积分

        # 检查缓存
        cached = cache.get(wxid)
        if cached:
            print("→ 从本地缓存读取 Authorization")
            uc_token = cached.get("ucAccessToken")
            open_id = cached.get("openId")
            c4a_uid = cached.get("c4aUid")
            user_id = cached.get("userId", "")
            mobile = cached.get("mobile", "")
            now_ms = int(time.time() * 1000)
            expire_time = int(cached.get("expireTime") or 0)
            if expire_time and now_ms >= expire_time:
                print("⚠️ 缓存token已过期，需要重新登录")
            else:
                member_data = get_member_info(
                    uc_token, open_id,
                    config["app_id"], config["api_key"], config["app_secret"],
                    user_id=user_id, mobile=mobile
                )
                if member_data:
                    token_valid = True
                    nickname = member_data.get("userCustomize", {}).get("nickName", "未知")
                    if not user_id and member_data.get("userId"):
                        cached["userId"] = member_data["userId"]
                        user_id = member_data["userId"]
                    if not mobile and member_data.get("mobile"):
                        cached["mobile"] = member_data["mobile"]
                        mobile = member_data["mobile"]
                    if not c4a_uid and member_data.get("uid"):
                        cached["c4aUid"] = member_data["uid"]
                        c4a_uid = member_data["uid"]
                    save_cache(cache)
                    print(f"✅ 缓存有效：用户名 {nickname}，积分 {member_data.get('vipPoint', '0')}")
                else:
                    print("⚠️ 缓存无效，需要重新登录")
        else:
            print("→ 无缓存，需要重新获取")

        if not token_valid:
            # 重新获取微信 code
            print("→ 正在获取微信 code...")
            js_code = get_wx_code(config["wechat_server"], wxid)
            if not js_code:
                print("❌ 获取微信 code 失败")
                account_results.append((idx, display, 0, False))
                continue

            # 登录
            login_result = midea_login(js_code, config["app_id"], config["api_key"], config["app_secret"])
            if not login_result:
                print("❌ 登录失败")
                account_results.append((idx, display, 0, False))
                continue
            uc_token, open_id, _, c4a_uid, expire_time = login_result

            # 获取会员信息
            member_data = get_member_info(
                uc_token, open_id,
                config["app_id"], config["api_key"], config["app_secret"]
            )
            if not member_data:
                print("❌ 获取会员信息失败")
                account_results.append((idx, display, 0, False))
                continue

            nickname = member_data.get("userCustomize", {}).get("nickName", "未知")
            user_id = member_data.get("userId", "")
            mobile = member_data.get("mobile", "")
            print(f"✅ 登录成功：用户名 {nickname}，积分 {member_data.get('vipPoint', '0')}")

            # 更新缓存
            cache[wxid] = {
                "remark": display, "wxid": wxid,
                "ucAccessToken": uc_token, "openId": open_id,
                "c4aUid": c4a_uid, "userId": user_id,
                "mobile": mobile,
                "expireTime": expire_time, "updateTime": int(time.time())
            }
            save_cache(cache)

        # ========== 查询基础积分 ==========
        if c4a_uid:
            score_data = get_multiple_account_score(uc_token, c4a_uid, brand_list,
                                                    config["app_id"], config["api_key"], config["app_secret"])
            if score_data:
                total_score = int(score_data.get("score", 0))
            else:
                total_score = 0
        else:
            total_score = 0

        # ========== 自动获取积分功能 ==========
        print(f"\n--- 开始自动获取积分 ---")

        step = 0
        total_steps = 4 + int(ENABLE_SIGNIN) + int(ENABLE_ACT) + int(ENABLE_TASKS)
        growth_actv_id = ""

        # 1. 每日签到
        if ENABLE_SIGNIN:
            step += 1
            print(f"\n[{step}/{total_steps}] 每日签到...")
            for sign_try in range(1, SIGNIN_TIMES + 1):
                signin_result = do_daily_signin(
                    uc_token, open_id,
                    config["app_id"], config["api_key"], config["app_secret"],
                    alias=display
                )
                if signin_result.get("score", 0) > 0:
                    account_points += signin_result["score"]
                if signin_result.get("status") in ("ok", "already_signed", "manual_required", "error"):
                    break
                if sign_try < SIGNIN_TIMES:
                    print(f"  [签到] 第 {sign_try} 次未完成，准备重试...")
                    time.sleep(random.uniform(0.8, 1.5))
            time.sleep(random.uniform(1, 2))

        # 2. 参与积分活动
        if ENABLE_ACT:
            step += 1
            print(f"\n[{step}/{total_steps}] 参与积分活动...")
            act_result = query_and_join_activities(
                uc_token, open_id,
                config["app_id"], config["api_key"], config["app_secret"],
                alias=display
            )
            growth_actv_id = act_result.get("growthActvId", "")
            if act_result.get("score", 0) > 0:
                account_points += act_result["score"]
            time.sleep(random.uniform(1, 2))

        # 3. 任务进度上报
        if ENABLE_TASKS:
            step += 1
            print(f"\n[{step}/{total_steps}] 任务进度上报...")
            task_result = report_task_progress(
                uc_token, open_id, c4a_uid,
                config["app_id"], config["api_key"], config["app_secret"],
                alias=display
            )

            # 额外检查成长营任务
            growth_result = check_mfans_growth_task(
                uc_token, open_id, c4a_uid,
                config["app_id"], config["api_key"], config["app_secret"],
                alias=display
            )

            # 领取可领升级积分奖励（如金卡/黑钻升级奖励）
            levelup_claim = claim_level_up_rewards(
                uc_token,
                config["app_id"], config["api_key"], config["app_secret"],
                alias=display
            )
            if levelup_claim.get("score", 0) > 0:
                account_points += levelup_claim["score"]
            time.sleep(random.uniform(1, 2))

        # 4. 领取可用权益/优惠券
        step += 1
        print(f"\n[{step}/{total_steps}] 查询并领取权益...")

        # 先处理美粉弹窗奖励（邀请/升级/保级弹窗）
        popup_reward = handle_mfans_popup_rewards(
            uc_token,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display
        )
        if popup_reward.get("score", 0) > 0:
            account_points += popup_reward["score"]
        time.sleep(random.uniform(0.3, 0.8))

        # 领取维系权益（若有）
        maintain_claim = receive_maintain_rights(
            uc_token,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display
        )
        if maintain_claim.get("score", 0) > 0:
            account_points += maintain_claim["score"]
        time.sleep(random.uniform(0.3, 0.8))
        
        # 先查询可用优惠券
        coupon_query = query_available_coupons(
            uc_token, open_id,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display
        )
        time.sleep(random.uniform(0.5, 1))
        
        # 尝试批量领取
        coupon_result = take_batch_coupons(
            uc_token, open_id,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display
        )
        time.sleep(random.uniform(0.5, 1))

        # 领取待领积分（购物积分等）
        pending_claim = claim_pending_integrals(
            uc_token, open_id, c4a_uid, user_id, mobile,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display
        )
        if pending_claim.get("score", 0) > 0:
            account_points += pending_claim["score"]
        time.sleep(random.uniform(0.5, 1))
        
        # 查询权益详情（可能含积分类权益）
        rights_v2 = get_rights_v2(
            uc_token, open_id,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display
        )
        time.sleep(random.uniform(0.5, 1))

        # 5. 查询美粉权益价值
        step += 1
        print(f"\n[{step}/{total_steps}] 查询美粉权益价值...")
        rights_worth = get_mfans_rights_worth(
            uc_token, open_id,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display
        )
        time.sleep(random.uniform(0.5, 1))

        # 6. 查询活动列表和奖励记录
        step += 1
        print(f"\n[{step}/{total_steps}] 查询活动与奖励...")
        act_list = query_act_list(
            uc_token, open_id,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display,
            growth_actv_id=growth_actv_id
        )
        time.sleep(random.uniform(0.5, 1))
        
        award_list = get_award_list(
            uc_token, open_id,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display,
            growth_actv_id=growth_actv_id
        )

        # 7. 检查积分未读消息
        step += 1
        print(f"\n[{step}/{total_steps}] 检查积分未读消息...")
        points_tip = check_points_unread(
            uc_token, open_id,
            config["app_id"], config["api_key"], config["app_secret"],
            alias=display
        )

        # 汇总本轮获得积分
        print(f"\n--- {display} 本轮积分获取汇总 ---")
        print(f"  基础积分: {total_score}")
        print(f"  活动获得: {account_points}")
        if account_points > 0:
            total_points_gained += account_points
            print(f"  🎉 本轮新增积分: +{account_points}")

        # 判断是否达标
        is_eligible = (total_score > 1000)
        account_results.append((idx, display, total_score, is_eligible))
        valid_count += 1

    # ========== 汇总输出 ==========
    print("\n" + "=" * 30)
    print(f"有效账号：{valid_count} 个")
    if total_points_gained > 0:
        print(f"全部账号本轮新获得积分: +{total_points_gained}")
    print("=" * 30)

    # 输出详细汇总表
    print("\n【执行结果汇总】")
    print(f"{'序号':<4} {'账号':<12} {'总积分':<8} {'达标状态':<6}")
    print("-" * 40)
    eligible_accounts = []
    for ridx, rname, rscore, religible in account_results:
        status = "达标" if religible else "-"
        print(f"{ridx:<4} {rname:<12} {rscore:<8} {status:<6}")
        if religible:
            eligible_accounts.append((rname, rscore))
    print("=" * 40)

    # ========== 兑换提醒（聚合推送） ==========
    if eligible_accounts and config["push_token"]:
        current_sign = "|".join([f"{rname}_{rscore}" for rname, rscore in eligible_accounts])
        last_sign = reminder_record.get("last_sign", "")
        if current_sign == last_sign:
            print("\n本次达标账号与上次相同，不再重复推送")
        else:
            name_list = "、".join([rname for rname, _ in eligible_accounts])
            detail_lines = []
            for i, (rname, rscore) in enumerate(eligible_accounts, start=1):
                detail_lines.append(f"[{i}/{len(eligible_accounts)}] {rname} 总积分：{rscore}")
            detail_text = "\n".join(detail_lines)
            content = f"【兑换提醒】{name_list} 兑换\n\n{detail_text}"
            title = "美的积分兑换提醒"
            print("\n发送 Push Plus 提醒...")
            success = send_push_plus(config["push_token"], title, content)
            if success:
                reminder_record["last_sign"] = current_sign
                save_reminder_record(reminder_record)
    elif not eligible_accounts:
        print("\n没有达标账号，不发送推送")
    elif not config["push_token"]:
        print("\n未配置 MyPushPlusToken，跳过推送")


if __name__ == "__main__":
    main()
