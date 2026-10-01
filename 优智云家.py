#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
优智云家动态 code 版

功能：
  1. 通过 getCode.py 模块获取微信小程序 code（支持多协议多账号）
  2. 使用 code 换 token（微信登录）
  3. 每日签到
  4. 青龙面板通知推送
  5. 品赞代理，业务请求优先代理，失败直连兜底

环境变量：
  WECHAT_SERVER     微信协议服务地址（getCode.py 使用）
  ADMIN_KEY         协议服务密钥（WeChatPadPro/iwechat需要）
  WX_ID             可选，指定账号ID，多个用&分隔
  PROXY_API         品赞代理提取 API，可选
  PROXY_TYPE        http / socks5，默认 http

依赖：
  pip install requests
  同目录下需要 getCode.py
"""

import json
import os
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests
from getCode import get_wechat_codes


APP_NAME = "优智云家品牌商城小程序"
APPID = "wxa61f98248d20178b"

PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()

PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

BASE_URL = "https://xapi.weimob.com"
LOGIN_URL = f"{BASE_URL}/fe/mapi/user/loginX"
SIGN_STATUS_URL = f"{BASE_URL}/api3/onecrm/mactivity/sign/misc/sign/activity/c/signMainInfo"
SIGN_SUBMIT_URL = f"{BASE_URL}/api3/onecrm/mactivity/sign/misc/sign/activity/core/c/sign"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541938) XWEB/19823"
)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def log_title() -> None:
    print(f"\n🏠 优智云家签到  |  {now_text()}")


def log_account_header(index: int, total: int, account_name: str) -> None:
    print(f"\n🧩 [{index}/{total}] {account_name}")


def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def parse_proxy_response(text: Any) -> Dict[str, Any] | None:
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        proxy_obj = None
        if isinstance(data.get("data"), list) and data["data"]:
            proxy_obj = data["data"][0]
        elif isinstance(data.get("data"), dict):
            proxy_obj = data["data"]
        elif data.get("ip") and data.get("port"):
            proxy_obj = data
        elif isinstance(data.get("result"), dict):
            proxy_obj = data["result"]
        if proxy_obj:
            host = proxy_obj.get("ip") or proxy_obj.get("host")
            port = proxy_obj.get("port")
            if host and port:
                return {"host": str(host), "port": int(port),
                        "username": proxy_obj.get("user") or proxy_obj.get("username") or "",
                        "password": proxy_obj.get("pass") or proxy_obj.get("password") or ""}
    except Exception:
        pass
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            return {"host": parts[0], "port": int(parts[1]),
                    "username": parts[2] if len(parts) > 2 else "",
                    "password": parts[3] if len(parts) > 3 else ""}
    return None


def build_proxy_dict(proxy_info: Dict[str, Any] | None) -> Dict[str, str] | None:
    if not proxy_info:
        return None
    host = proxy_info["host"]
    port = proxy_info["port"]
    username = proxy_info.get("username", "")
    password = proxy_info.get("password", "")
    auth = f"{quote(username)}:{quote(password)}@" if username and password else ""
    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{host}:{port}"
    return {"http": proxy_url, "https": proxy_url}


def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
    if not proxies:
        return False, ""
    try:
        response = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if response.status_code == 200:
            return True, response.json().get("origin", "")
    except Exception:
        pass
    return False, ""


def get_valid_proxy(account_name: str) -> Tuple[Dict[str, str] | None, str]:
    if not PROXY_API:
        return None, ""
    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            response = direct_session().get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(response.text)
            if not proxy_info:
                continue
            proxies = build_proxy_dict(proxy_info)
            ok, ip = validate_proxy(proxies)
            if ok:
                return proxies, ip
        except Exception:
            pass
        if index < PROXY_RETRY_TIMES:
            sleep(2)
    return None, ""


def request_with_proxy(method: str, url: str, *, proxies: Dict[str, str] | None = None, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    if proxies:
        try:
            return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception:
            if not ENABLE_DIRECT_FALLBACK:
                raise
    return direct_session().request(method, url, **kwargs)


def send_pushplus(title: str, content: str) -> None:
    try:
        import notify
        notify.send(title, content)
    except Exception:
        pass


def common_headers(token: str | None = None, extra_headers: Dict | None = None) -> Dict[str, str]:
    headers = {
        "Host": "xapi.weimob.com", "User-Agent": USER_AGENT, "Content-Type": "application/json",
        "Accept": "*/*", "Referer": f"https://servicewechat.com/{APPID}/109/page-frame.html",
        "Accept-Encoding": "gzip, deflate, br", "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if token:
        headers["X-WX-Token"] = token
    if extra_headers:
        headers.update(extra_headers)
    return headers


def extract_token(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ["token", "accessToken", "access_token", "jwt"]:
        val = data.get(key) or (data.get("data") or {}).get(key)
        if val and val != "null":
            return str(val)
    return None


def login_by_code(code: str, proxies: Dict[str, str] | None, account_name: str) -> Tuple[str | None, Dict[str, Any] | None]:
    try:
        payload = {"appid": APPID, "basicInfo": {"bosId": "4022115200359", "cid": "821033359", "tcode": "weimob", "vid": "6016741943359"},
                   "env": "production", "extendInfo": {"source": 1}, "is_pre_fetch_open": True, "parentVid": 0, "pid": "", "storeId": "", "code": code, "queryAuthConfig": True}
        response = request_with_proxy("POST", LOGIN_URL, headers=common_headers(), json=payload, proxies=proxies)
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:800]}
        if data.get("errcode") == 0:
            token = extract_token(data)
            if token:
                return token, data
        return None, data
    except Exception:
        return None, None


def check_sign_status(token: str, proxies: Dict[str, str] | None, account_name: str) -> Tuple[bool, Dict]:
    extra_headers = {"x-wmsdk-vid": "6016741943359", "x-biz-id": "146", "cloud-project-name": "fansquan",
                     "x-component-is": "onecrm/signgift", "cloud-bosid": "4022115200359", "weimob-bosId": "4022115200359"}
    payload = {"appid": APPID, "basicInfo": {"vid": 6016741943359, "vidType": 2, "bosId": 4022115200359, "productId": 146, "productInstanceId": 15532102359, "productVersionId": "10003", "merchantId": 2000230069359, "tcode": "weimob", "cid": 821033359}, "extendInfo": {"wxTemplateId": 7930}}
    try:
        response = request_with_proxy("POST", SIGN_STATUS_URL, headers=common_headers(token, extra_headers), json=payload, proxies=proxies)
        data = response.json()
        if data.get("errcode") == 0:
            sign_data = data.get("data", {})
            return sign_data.get("isSign", False), sign_data
        return False, {}
    except Exception:
        return False, {}


def submit_signin(token: str, proxies: Dict[str, str] | None, account_name: str) -> Tuple[bool, str, int]:
    extra_headers = {"x-wmsdk-vid": "6016741943359", "x-biz-id": "146", "cloud-project-name": "fansquan",
                      "x-component-is": "onecrm/signgift", "cloud-bosid": "4022115200359", "weimob-bosId": "4022115200359",
                      "parentrpcid": "a6e117c9d2dad0ad"}
    payload = {"appid": APPID, "basicInfo": {"vid": 6016741943359, "vidType": 2, "bosId": 4022115200359, "productId": 146, "productInstanceId": 15532102359, "productVersionId": "10003", "merchantId": 2000230069359, "tcode": "weimob", "cid": 821033359},
               "extendInfo": {"wxTemplateId": 8105, "analysis": [], "bosTemplateId": 1000002154, "childTemplateIds": [{"customId": 90004, "version": "crm@0.1.81"}, {"customId": 90002, "version": "ec@80.0"}, {"customId": 90006, "version": "hudong@0.0.251"}, {"customId": 90008, "version": "cms@0.0.524"}, {"customId": 90070, "version": "1.0.12"}],
                         "quickdeliver": {"enable": True}, "youshu": {"enable": False}, "source": 1, "channelsource": 5, "refer": "onecrm-signgift", "mpScene": 1005},
               "queryParameter": None, "i18n": {"language": "zh", "timezone": "8"}, "pid": "", "storeId": "", "customInfo": {"source": 0, "wid": 11983225884}}
    try:
        response = request_with_proxy("POST", SIGN_SUBMIT_URL, headers=common_headers(token, extra_headers), json=payload, proxies=proxies)
        data = response.json()

        if data.get("errcode") == 0:
            sign_data = data.get("data", {})
            is_sign = sign_data.get("isSign", False)
            reward_info = sign_data.get("rewardInfo", {})
            reward_name = reward_info.get("rewardName", "签到奖励")
            integral = reward_info.get("integral", 0) or reward_info.get("score", 0)

            if is_sign:
                return True, f"签到成功: {reward_name} +{integral}积分", int(integral) if integral else 0
            return True, "签到成功", 0
        errmsg = data.get("errmsg", "")
        if "重复签到" in str(errmsg):
            return True, "今日已签到", 0
        return False, str(errmsg), 0
    except Exception as exc:
        return False, str(exc), 0


def run_account(index: int, total: int, nick_name: str, code: str) -> Dict[str, Any]:
    result = {"remark": nick_name, "success": False, "proxyStatus": "未使用代理", "proxyIp": "-", "token": "-",
              "signMsg": "-", "earnedIntegral": "0", "error": ""}

    log_account_header(index, total, nick_name)
    proxies, proxy_ip = get_valid_proxy(nick_name)
    result["proxyStatus"] = "使用专属代理" if proxies else "使用直连"
    result["proxyIp"] = proxy_ip or "-"
    sleep(PROXY_FETCH_INTERVAL)

    sleep(random.randint(2, 5))

    token, raw_login = login_by_code(code, proxies, nick_name)
    if not token:
        result["error"] = "登录失败"
        return result

    result["token"] = mask(token)
    print(f"  🔑 登录成功 token: {mask(token)}")

    try:
        sleep(random.randint(1, 2))
        is_signed, sign_data = check_sign_status(token, proxies, nick_name)

        if is_signed:
            print(f"  [DEBUG] sign_data: {json.dumps(sign_data, ensure_ascii=False)[:500]}")
            earned = sign_data.get("rewardInfo", {}).get("integral", 0) or sign_data.get("totalScore", 0) or sign_data.get("continuousSignDays", 0)
            result["signMsg"] = "今日已签到"
            result["earnedIntegral"] = str(earned)
            print(f"  📋 今日已签到 +{earned}积分")
        else:
            sign_ok, sign_msg, earned = submit_signin(token, proxies, nick_name)
            result["signMsg"] = sign_msg
            result["earnedIntegral"] = str(earned)
            print(f"  ✅ {sign_msg}")

        sleep(random.randint(1, 2))
        result["success"] = True
        return result

    except Exception as exc:
        result["error"] = str(exc)
        print(f"  ❌ 异常: {exc}")
        return result


def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count
    total_earned = sum(int(item.get("earnedIntegral", 0)) for item in results if item.get("success"))

    content = f"🏠 优智云家签到\n\n"
    content += f"✅ 成功: {success_count}  |  ❌ 失败: {fail_count}\n"
    content += f"💰 总获得积分: {total_earned}\n"
    content += f"🕒 {now_text()}\n\n"

    for idx, res in enumerate(results, 1):
        icon = "✅" if res["success"] else "❌"
        sign_info = res.get("signMsg", "-")
        if res["success"]:
            content += f"{icon} {res['remark']}: {sign_info}\n"
        else:
            content += f"{icon} {res['remark']}: {res.get('error', '失败')}\n"

    return content


def main() -> None:
    log_title()

    print(f"📡 通过 getCode.py 获取所有在线账号的 Code...")
    codes = get_wechat_codes(APPID)

    if not codes:
        print("❌ 未获取到任何账号的 Code，请检查 WECHAT_SERVER / ADMIN_KEY / WX_ID 配置")
        return

    print(f"✅ 获取到 {len(codes)} 个账号的 Code")

    results: List[Dict[str, Any]] = []

    for index, (nick_name, code) in enumerate(codes.items(), 1):
        try:
            result = run_account(index, len(codes), nick_name, code)
            results.append(result)
        except Exception as exc:
            results.append({"remark": nick_name, "success": False,
                           "signMsg": "-", "earnedIntegral": "0", "error": str(exc)})
        if index < len(codes):
            sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count
    total_earned = sum(int(item.get("earnedIntegral", 0)) for item in results if item.get("success"))

    print(f"\n🏁 完成: ✅{success_count} ❌{fail_count} 💰{total_earned}积分  {now_text()}")
    send_pushplus("🏠 优智云家签到", build_notify(results))


if __name__ == "__main__":
    main()