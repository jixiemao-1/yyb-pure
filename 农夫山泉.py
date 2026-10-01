#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
青龙用法:

1. 依赖管理里安装:
   requests
   pycryptodome
   qrcode[pil]

2. 环境变量:
   NF_CODES          必填，瓶盖链接，多个用换行或 @ 分隔
   WECHAT_SERVER     可选/默认 http://192.168.10.3:8080，微信协议服务
   WX_CODE_URL       可选，兼容旧变量；会当作 WECHAT_SERVER 使用
   nfsq / NFSQ       可选，指定微信账号 wxid/账号名，多个用 & 分隔；不填使用接口返回的全部在线账号
   NF_LAT / NF_LNG   可选，经纬度，不填用 HAR 里的坐标
   NF_DEVICE_TOKEN   可选，不填传空

3. 青龙定时任务命令:
   python3 nongfu_cap_redeem.py
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import qrcode
import requests

def __yyb_load_accounts(raw_env_value: str = ""):
    """账号环境变量可空：空/all=yyb 当前授权码下全部已绑定账号。"""
    import os as _os
    import re as _re
    from getCode import make_getter
    server = (_os.getenv("WECHAT_SERVER") or _os.getenv("YYB_SERVER") or "").strip()
    getter = make_getter(server)
    text = (raw_env_value or "").strip()
    selectors = None
    if text and text.lower() not in ("all", "*", "全部", "auto"):
        selectors = [x.split("#")[0].strip() for x in _re.split(r"[\n&@]+", text) if x.strip()]
        selectors = [x for x in selectors if x] or None
    accounts = getter.resolve_accounts(selectors)
    out = []
    for a in accounts:
        oid = getter._account_id(a)
        nick = getter._nick(a)
        out.append({"openid": oid, "wxid": oid, "nickname": nick, "remark": nick, "raw": a})
    return out

from Crypto.Cipher import AES


APP_ID = "wx3723729dc4ac916c"
ACTIVITY_TYPE = "S1"
DEFAULT_WECHAT_SERVER = "http://192.168.10.3:8080"
DEFAULT_SIGN_KEY = "RaJ3EvQLCqhIE5In4CTIfA=="
DEFAULT_AES_KEY = "SJyooRdCFtqVz2aLUDNz9A=="
BASE_URL = "https://crm-app.yst.com.cn/api-b2b-market-draw-front"
MALL_URL = "https://api-mall.yst.com.cn/ecu-mall"
REFERER = f"https://servicewechat.com/{APP_ID}/167/page-frame.html"
DEVICE_PROFILES = [
    {
        "brand": "iPhone",
        "model": "iPhone 15 Pro Max",
        "system": "iOS 18.5",
        "platform": "ios",
        "wechat_version": "8.0.58",
        "sdk_version": "3.8.12",
        "ua": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
            "MicroMessenger/8.0.58(0x18003a2f) NetType/WIFI Language/zh_CN"
        ),
    },
    {
        "brand": "iPhone",
        "model": "iPhone 14 Pro",
        "system": "iOS 17.6.1",
        "platform": "ios",
        "wechat_version": "8.0.56",
        "sdk_version": "3.8.10",
        "ua": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
            "MicroMessenger/8.0.56(0x18003831) NetType/WIFI Language/zh_CN"
        ),
    },
    {
        "brand": "iPhone",
        "model": "iPhone 13",
        "system": "iOS 16.7.10",
        "platform": "ios",
        "wechat_version": "8.0.54",
        "sdk_version": "3.8.8",
        "ua": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_10 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
            "MicroMessenger/8.0.54(0x1800362f) NetType/4G Language/zh_CN"
        ),
    },
    {
        "brand": "Xiaomi",
        "model": "23013RK75C",
        "system": "Android 14",
        "platform": "android",
        "wechat_version": "8.0.58",
        "sdk_version": "3.8.12",
        "ua": (
            "Mozilla/5.0 (Linux; Android 14; 23013RK75C Build/UKQ1.230804.001; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/126.0.6478.134 "
            "Mobile Safari/537.36 XWEB/1260093 MMWEBSDK/20240501 "
            "MicroMessenger/8.0.58.2840(0x28003A5D) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64"
        ),
    },
    {
        "brand": "HONOR",
        "model": "PGT-AN10",
        "system": "Android 13",
        "platform": "android",
        "wechat_version": "8.0.56",
        "sdk_version": "3.8.10",
        "ua": (
            "Mozilla/5.0 (Linux; Android 13; PGT-AN10 Build/HONORPGT-AN10; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.172 "
            "Mobile Safari/537.36 XWEB/1240133 MMWEBSDK/20240404 "
            "MicroMessenger/8.0.56.2800(0x28003837) WeChat/arm64 Weixin NetType/5G Language/zh_CN ABI/arm64"
        ),
    },
]


def choose_device_profile() -> dict[str, str]:
    profile = dict(random.choice(DEVICE_PROFILES))
    return profile


def now_ms() -> str:
    return str(int(time.time() * 1000))


def stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def remove_inserted_characters(text: str, positions: list[int]) -> str:
    out: list[str] = []
    hit = 0
    for idx, char in enumerate(text):
        if hit < len(positions) and idx == hit + positions[hit]:
            hit += 1
            continue
        out.append(char)
    return "".join(out)


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad = block_size - len(data) % block_size
    return data + bytes([pad]) * pad


def aes_ecb_encrypt_base64(plain_text: str, b64_key: str) -> str:
    key = base64.b64decode(b64_key)
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted = cipher.encrypt(pkcs7_pad(plain_text.encode("utf-8")))
    return base64.b64encode(encrypted).decode("ascii")


def encrypted_param(
    payload: dict[str, Any],
    sign_key: str = DEFAULT_SIGN_KEY,
    aes_key: str = DEFAULT_AES_KEY,
    open_id: str | None = None,
) -> dict[str, str]:
    data = dict(payload)
    if open_id and not data.get("openId"):
        data["openId"] = open_id
    data["timeMillis"] = now_ms()

    normalized = {
        key: stringify(value)
        for key, value in data.items()
        if value is not None and value != ""
    }
    ordered = {key: normalized[key] for key in sorted(normalized)}
    sign_src = "&".join(f"{key}={value}" for key, value in ordered.items())
    sign_src = f"{sign_src}&key={sign_key}" if sign_src else f"key={sign_key}"
    ordered["sign"] = hashlib.md5(sign_src.encode("utf-8")).hexdigest().upper()
    plain = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    return {"param": aes_ecb_encrypt_base64(plain, aes_key)}


def extract_code_from_response(data: Any) -> str | None:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return None

    candidates = [
        data.get("code"),
        data.get("Code"),
        data.get("Data", {}).get("Code") if isinstance(data.get("Data"), dict) else None,
        data.get("Data", {}).get("code") if isinstance(data.get("Data"), dict) else None,
        data.get("data", {}).get("code") if isinstance(data.get("data"), dict) else None,
        data.get("result", {}).get("code") if isinstance(data.get("result"), dict) else None,
    ]
    for item in candidates:
        if isinstance(item, str) and item:
            return item
    return None


@dataclass
class RedeemResult:
    account: str | None
    wxid: str | None
    scan_code: str
    success: bool
    code: str | None
    message: str | None
    reward_type: str | None
    winner_amount: Any
    winner_amount_yuan: Any
    code_used: Any
    raw: dict[str, Any]
    exchange_status: Any = None
    exchange_time_cn: Any = None
    expire_time_cn: Any = None
    first_scan_time_cn: Any = None
    cust_name: Any = None
    exchange_expire_time: Any = None
    receive_success: Any = None
    receive_message: Any = None
    receive_raw: dict[str, Any] | None = None
    mobile: Any = None
    exchange_qr_path: str | None = None


class NongfuClient:
    def __init__(
        self,
        wechat_server: str,
        wxid: str | None = None,
        account_name: str | None = None,
        timeout: int = 20,
    ) -> None:
        self.wechat_server = wechat_server
        self.wxid = wxid
        self.account_name = account_name or wxid
        self.timeout = timeout
        self.device_profile = choose_device_profile()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "content-type": "application/json",
                "User-Agent": self.device_profile["ua"],
                "Referer": REFERER,
            }
        )
        self.auth_token: str | None = None
        self.open_id: str | None = None
        self.union_id: str | None = None
        self.key_a: str | None = None
        self.key_b: str | None = None
        self.resolved_wxid: str | None = wxid

    def get_wx_code(self) -> str:
        from getCode import make_getter

        getter = make_getter(self.wechat_server)
        if self.wxid:
            return getter.get_applet_code(APP_ID, self.wxid)
        codes = getter.get_codes_for_all_online_accounts(APP_ID)
        if not codes:
            raise RuntimeError("getCode.py 未返回可用 code，请检查 yyb呆呆 账号是否已绑定")
        return next(iter(codes.values()))

    def niuzi_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(
            f"{self.wechat_server.rstrip('/')}/api/yyb/{path.lstrip('/')}",
            json=payload,
            timeout=self.timeout,
        )
        return self.parse_response(resp)

    def resolve_wxid(self) -> str:
        if self.resolved_wxid:
            return self.resolved_wxid
        resp = self.session.get(
            f"{self.wechat_server.rstrip('/')}/api/accounts",
            timeout=self.timeout,
        )
        data = self.parse_response(resp)
        accounts = data.get("data") or {}
        if not isinstance(accounts, dict):
            raise RuntimeError(f"牛子账号列表格式异常: {data}")

        candidates: list[dict[str, Any]] = []
        for key, info in accounts.items():
            if not isinstance(info, dict):
                continue
            item = dict(info)
            item.setdefault("wxid", key)
            if str(item.get("survival", "")).lower() in {"1", "true", "online"}:
                candidates.append(item)
        if not candidates:
            raise RuntimeError("牛子协议没有在线账号，无法获取手机号授权 code")

        if self.wxid:
            for item in candidates:
                aliases = {
                    str(item.get("wxid") or ""),
                    str(item.get("wx_id") or ""),
                    str(item.get("account") or ""),
                    str(item.get("display_name") or ""),
                    str(item.get("real_wxid") or ""),
                    str(item.get("nickname") or ""),
                    str(item.get("nick_name") or ""),
                    str(item.get("nickName") or ""),
                }
                if self.wxid in aliases:
                    self.resolved_wxid = item.get("wxid") or item.get("wx_id")
                    return str(self.resolved_wxid)
            raise RuntimeError(f"未找到 nfsq={self.wxid} 对应的在线账号")

        self.resolved_wxid = candidates[0].get("wxid") or candidates[0].get("wx_id")
        return str(self.resolved_wxid)

    def get_phone_code(self) -> dict[str, Any]:
        wxid = self.resolve_wxid()
        payload = {
            "wxid": wxid,
            "appid": APP_ID,
            "data": '{"api_name":"webapi_getuserwxphone","with_credentials":true}',
            "opt": 1,
        }
        data = self.niuzi_post("app/get/all/mobile", payload)
        if data.get("Code") not in (0, "0", None) and data.get("code") not in (0, "0", None):
            raise RuntimeError(data.get("Message") or data.get("message") or f"获取手机号失败: {data}")
        item = self.extract_mobile_item(data)
        phone_code = item.get("code") or item.get("phone_code") or item.get("phoneCode")
        if not phone_code:
            raise RuntimeError(f"手机号接口未返回 getPhoneNumber code: {data}")
        return {
            "code": phone_code,
            "mobile": item.get("mobile") or item.get("phone") or item.get("purePhoneNumber"),
            "raw": data,
        }

    @staticmethod
    def extract_mobile_item(data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("Data") or data.get("data") or {}
        if isinstance(payload, dict):
            phones = payload.get("ALLMobile") or payload.get("allMobile") or []
            if phones and isinstance(phones, list) and isinstance(phones[0], dict):
                return phones[0]
            if isinstance(payload.get("Data"), str):
                try:
                    inner = json.loads(payload["Data"])
                    custom = inner.get("custom_phone_list") or []
                    if custom and isinstance(custom, list) and isinstance(custom[0], dict):
                        item = custom[0]
                        if not item.get("code") and item.get("data"):
                            try:
                                item["code"] = json.loads(item["data"]).get("code", "")
                            except Exception:
                                pass
                        return item
                except Exception:
                    pass
            return payload
        return {}

    def api_headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "User-Agent": self.device_profile["ua"],
            "Referer": REFERER,
        }
        if self.auth_token:
            headers["X-AUTH-TOKEN"] = self.auth_token
        return headers

    def mall_login(self) -> str:
        code = self.get_wx_code()
        resp = self.session.post(
            f"{MALL_URL}/thirdparty/v1/users/thirdparty/login",
            json={"authCode": code, "appType": 6},
            headers=self.api_headers(),
            timeout=self.timeout,
        )
        data = self.parse_response(resp)
        session_id = (data.get("data") or {}).get("sessionId")
        if not session_id:
            raise RuntimeError(f"商城登录未返回 sessionId: {data}")
        self.auth_token = session_id
        return session_id

    def post_business(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(
            f"{BASE_URL}{path}",
            json=encrypted_param(payload, open_id=self.open_id),
            headers=self.api_headers(),
            timeout=self.timeout,
        )
        return self.parse_response(resp)

    def parse_response(self, resp: requests.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"响应不是 JSON: HTTP {resp.status_code} {resp.text[:300]}") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {data}")
        return data

    def refresh_openid_and_keys(self, scan_code: str) -> dict[str, Any]:
        code = self.get_wx_code()
        data = self.post_business(
            "/consumer/getOpenId",
            {
                "scanCode": scan_code,
                "code": code,
                "activityType": ACTIVITY_TYPE,
            },
        )
        info = data.get("data") or {}
        if not info.get("openId"):
            raise RuntimeError(f"getOpenId 未返回 openId: {data}")
        self.open_id = info.get("openId")
        self.union_id = info.get("unionId")
        self.key_a = info.get("a")
        self.key_b = info.get("b")
        return info

    def draw_prize(
        self,
        scan_code: str,
        latitude: str,
        longitude: str,
        device_token: str | None = None,
        retry_on_key_expired: bool = True,
    ) -> RedeemResult:
        if not self.auth_token:
            self.mall_login()
        self.refresh_openid_and_keys(scan_code)
        data = self._draw_once(scan_code, latitude, longitude, device_token)
        if str(data.get("code")) == "2002" and retry_on_key_expired:
            self.refresh_openid_and_keys(scan_code)
            data = self._draw_once(scan_code, latitude, longitude, device_token)
        result = self.to_result(scan_code, data)
        result.account = self.account_name
        result.wxid = self.wxid
        return result

    def _draw_once(
        self,
        scan_code: str,
        latitude: str,
        longitude: str,
        device_token: str | None,
    ) -> dict[str, Any]:
        if not self.open_id or not self.union_id or not self.key_a or not self.key_b:
            raise RuntimeError("缺少 openId/unionId/keyA/keyB")

        sign_key = remove_inserted_characters(self.key_a, [1, 3, 5])
        aes_key = remove_inserted_characters(self.key_b, [1, 3, 5])
        inner_payload = {
            "scanCode": scan_code,
            "latitude": latitude,
            "longitude": longitude,
            "unionId": self.union_id,
            "deviceToken": device_token or "",
        }
        security_param = encrypted_param(
            inner_payload,
            sign_key=sign_key,
            aes_key=aes_key,
            open_id=self.open_id,
        )["param"]
        return self.post_business(
            "/consumer/drawPrize",
            {
                "openId": self.open_id,
                "securityParam": security_param,
                "activityType": ACTIVITY_TYPE,
            },
        )

    def receive_red_packet(self, scan_code: str) -> dict[str, Any]:
        if not self.open_id or not self.union_id:
            raise RuntimeError("缺少 openId/unionId，不能领取红包")
        phone = self.get_phone_code()
        receive_data = self.post_business(
            "/consumer/receive/redPacket",
            {
                "code": phone["code"],
                "scanCode": scan_code,
                "openId": self.open_id,
                "unionId": self.union_id,
                "activityType": ACTIVITY_TYPE,
                "remark": "",
                "sdkVersion": self.device_profile["sdk_version"],
                "brand": self.device_profile["brand"],
                "model": self.device_profile["model"],
                "syst": self.device_profile["system"],
                "platform": self.device_profile["platform"],
                "vers": self.device_profile["wechat_version"],
                "timeMills": int(time.time() * 1000),
            },
        )
        receive_data["_mobile"] = phone.get("mobile")
        return receive_data

    @staticmethod
    def to_result(scan_code: str, data: dict[str, Any]) -> RedeemResult:
        body = data.get("data") or {}
        return RedeemResult(
            account=None,
            wxid=None,
            scan_code=scan_code,
            success=bool(data.get("success")),
            code=str(data.get("code")) if data.get("code") is not None else None,
            message=data.get("message") or data.get("messageTitle"),
            reward_type=body.get("rewardType"),
            winner_amount=body.get("winnerAmount"),
            winner_amount_yuan=body.get("winnerAmountYuanStr"),
            code_used=body.get("codeUsed"),
            raw=data,
            exchange_status=body.get("exchangeStatus"),
            exchange_time_cn=body.get("exchangeTimeCn"),
            expire_time_cn=body.get("expireTimeCn"),
            first_scan_time_cn=body.get("firstScanTimeCn"),
            cust_name=body.get("custName"),
            exchange_expire_time=body.get("exchangeExpireTime"),
        )


def load_scan_codes(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    env_codes = (
        os.getenv("NF_CODES")
        or os.getenv("NONGFU_CODES")
        or os.getenv("NONGFU_CAP_CODES")
        or ""
    ).strip()
    if env_codes:
        chunks = [env_codes]
        for sep in ["\n", "@", "&"]:
            chunks = [part for chunk in chunks for part in chunk.split(sep)]
        values.extend(item.strip() for item in chunks if item.strip())

    for item in args.scan_code or []:
        values.append(item.strip())
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                values.append(line)
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def load_niuzi_accounts(wechat_server: str, wxid_filter: str | None, timeout: int) -> list[dict[str, str]]:
    server = wechat_server.rstrip("/")
    resp = requests.get(f"{server}/api/accounts", timeout=timeout)
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"牛子账号列表不是 JSON: HTTP {resp.status_code} {resp.text[:300]}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"牛子账号列表 HTTP {resp.status_code}: {data}")

    raw_accounts = data.get("data") or {}
    if not isinstance(raw_accounts, dict):
        raise RuntimeError(f"牛子账号列表格式异常: {data}")

    wanted = {
        item.strip()
        for item in (wxid_filter or "").split("&")
        if item.strip()
    }
    accounts: list[dict[str, str]] = []
    for key, info in raw_accounts.items():
        if not isinstance(info, dict):
            continue
        survival = str(info.get("survival", "")).lower()
        if survival not in {"1", "true", "online"}:
            continue
        wxid = str(info.get("wxid") or info.get("wx_id") or key)
        name = str(
            info.get("display_name")
            or info.get("account")
            or info.get("nickname")
            or info.get("nick_name")
            or wxid
        )
        aliases = {
            wxid,
            name,
            str(info.get("wx_id") or ""),
            str(info.get("account") or ""),
            str(info.get("display_name") or ""),
            str(info.get("real_wxid") or ""),
            str(info.get("nickname") or ""),
            str(info.get("nick_name") or ""),
            str(info.get("nickName") or ""),
            str(key),
        }
        if wanted and not (wanted & aliases):
            continue
        accounts.append({"wxid": wxid, "name": name})

    if not accounts:
        hint = f"nfsq={wxid_filter} " if wxid_filter else ""
        raise RuntimeError(f"{hint}没有匹配到在线牛子账号")
    random.shuffle(accounts)
    return accounts


def save_qr_images(scan_codes: list[str], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for idx, code in enumerate(scan_codes, 1):
        save_qr_image(code, output_dir / f"cap_{idx:03d}.png")


def save_qr_image(content: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = qrcode.make(content)
    img.save(path)
    return path


def reward_name(reward_type: str | None) -> str:
    names = {
        "noSurprise": "没中奖",
        "exchange": "换购奖励",
        "redPacket": "红包",
        "bankPayment": "银行打款",
    }
    return names.get(reward_type or "", reward_type or "unknown")


def exchange_status_name(status: Any) -> str:
    names = {
        0: "未兑换",
        1: "已兑换",
        2: "支付中",
        3: "已过期",
    }
    try:
        key = int(status)
    except (TypeError, ValueError):
        return str(status) if status not in (None, "") else ""
    return f"{names.get(key, '未知状态')}({key})"


def receive_status_name(status: Any) -> str:
    names = {
        0: "未领取",
        1: "已领取",
        2: "处理中",
        3: "已过期",
    }
    try:
        key = int(status)
    except (TypeError, ValueError):
        return str(status) if status not in (None, "") else ""
    return f"{names.get(key, '未知状态')}({key})"


def reward_extra_text(item: RedeemResult) -> str:
    body = item.raw.get("data") if isinstance(item.raw, dict) else {}
    body = body if isinstance(body, dict) else {}
    prize_name = next(
        (
            body.get(key)
            for key in [
                "rewardName",
                "prizeName",
                "goodsName",
                "commodityName",
                "couponName",
                "titleText",
            ]
            if body.get(key)
        ),
        None,
    )
    parts: list[str] = []
    if prize_name:
        parts.append(f"奖品={prize_name}")
    if item.reward_type == "exchange" and item.exchange_status not in (None, ""):
        parts.append(f"换购状态={exchange_status_name(item.exchange_status)}")
    if item.reward_type == "redPacket" and item.exchange_status not in (None, ""):
        parts.append(f"红包状态={receive_status_name(item.exchange_status)}")
    if item.reward_type == "bankPayment" and item.exchange_status not in (None, ""):
        parts.append(f"打款状态={receive_status_name(item.exchange_status)}")
    if item.receive_success is not None:
        parts.append(f"领取={'成功' if item.receive_success else '失败'}")
    if item.receive_message:
        parts.append(f"领取信息={item.receive_message}")
    if item.mobile:
        parts.append(f"手机号={item.mobile}")
    if item.first_scan_time_cn:
        parts.append(f"开奖时间={item.first_scan_time_cn}")
    if item.exchange_time_cn:
        parts.append(f"兑奖时间={item.exchange_time_cn}")
    if item.expire_time_cn:
        parts.append(f"兑奖截止={item.expire_time_cn}")
    if item.exchange_expire_time and not item.expire_time_cn:
        parts.append(f"换购截止={item.exchange_expire_time}")
    if item.cust_name:
        parts.append(f"兑奖门店={item.cust_name}")
    return " ".join(parts)


def should_receive_red_packet(item: RedeemResult) -> bool:
    if not item.success or item.reward_type != "redPacket":
        return False
    try:
        if int(item.exchange_status) == 1:
            return False
    except (TypeError, ValueError):
        pass
    return True


def item_get(item: RedeemResult | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(item, RedeemResult):
        return getattr(item, key, default)
    if isinstance(item, dict):
        return item.get(key, default)
    return default


def parse_draw_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    text = text.replace("年", "-").replace("月", "-").replace("日", " ")
    text = " ".join(text.split())
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text[: len(datetime.now().strftime(fmt))], fmt)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(float(text) / (1000 if len(text) >= 13 else 1))
    except (TypeError, ValueError, OSError):
        return None


def draw_date(item: RedeemResult | dict[str, Any]) -> date | None:
    dt = parse_draw_time(item_get(item, "first_scan_time_cn"))
    return dt.date() if dt else None


def account_key_from_item(item: RedeemResult | dict[str, Any]) -> str:
    return str(item_get(item, "wxid") or item_get(item, "account") or "").strip()


def load_history_results(output_dir: Path) -> list[dict[str, Any]]:
    if not output_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(output_dir.glob("nongfu_results_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list):
            rows.extend(item for item in data if isinstance(item, dict))
    return rows


def today_scan_counts(
    items: list[RedeemResult | dict[str, Any]],
    target_day: date | None = None,
) -> Counter:
    target_day = target_day or date.today()
    counts: Counter = Counter()
    for item in items:
        key = account_key_from_item(item)
        if key and draw_date(item) == target_day:
            counts[key] += 1
    return counts


def build_scan_plan(
    scan_codes: list[str],
    clients: list[NongfuClient],
    existing_counts: Counter,
    daily_limit: int,
) -> tuple[list[tuple[int, str, NongfuClient]], list[str], Counter]:
    counts = Counter(existing_counts)
    plan: list[tuple[int, str, NongfuClient]] = []
    skipped: list[str] = []
    cursor = 0
    for original_idx, scan_code in enumerate(scan_codes, 1):
        picked: NongfuClient | None = None
        for offset in range(len(clients)):
            client = clients[(cursor + offset) % len(clients)]
            key = client.wxid or client.account_name or ""
            if counts[key] < daily_limit:
                picked = client
                cursor = (cursor + offset + 1) % len(clients)
                break
        if not picked:
            skipped.append(scan_code)
            continue
        key = picked.wxid or picked.account_name or ""
        counts[key] += 1
        plan.append((original_idx, scan_code, picked))
    return plan, skipped, counts


def summarize_today_by_draw_time(
    items: list[RedeemResult | dict[str, Any]],
    target_day: date | None = None,
) -> str:
    target_day = target_day or date.today()
    today_items = [item for item in items if draw_date(item) == target_day]
    reward_counter = Counter(str(item_get(item, "reward_type") or "unknown") for item in today_items)
    account_counter = Counter(account_key_from_item(item) for item in today_items if account_key_from_item(item))
    win_count = sum(
        1
        for item in today_items
        if item_get(item, "success") and item_get(item, "reward_type") not in (None, "", "noSurprise")
    )
    red_packet_count = sum(1 for item in today_items if item_get(item, "reward_type") == "redPacket")
    amount = 0.0
    for item in today_items:
        try:
            amount += float(item_get(item, "winner_amount_yuan") or 0)
        except (TypeError, ValueError):
            pass

    lines = [
        f"今日开奖统计({target_day.isoformat()}):",
        f"- 开奖记录: {len(today_items)}",
        f"- 中奖次数: {win_count}",
        f"- 红包次数: {red_packet_count}",
        f"- 红包金额合计: {amount:.2f}",
    ]
    if account_counter:
        lines.append("- 每号开奖次数:")
        for key, count in account_counter.most_common():
            lines.append(f"  {key}: {count}")
    if reward_counter:
        lines.append("- 今日结果分布:")
        for key, count in reward_counter.most_common():
            lines.append(f"  {reward_name(key)}({key}): {count}")
    return "\n".join(lines)


def quota_report(clients: list[NongfuClient], counts: Counter, daily_limit: int) -> str:
    lines = [f"今日扫码次数额度(按本地开奖日志统计，上限 {daily_limit}/号):"]
    for client in clients:
        key = client.wxid or client.account_name or ""
        used = int(counts.get(key, 0))
        remain = max(0, daily_limit - used)
        lines.append(f"- {client.account_name} ({key}): 已用 {used}，剩余 {remain}")
    return "\n".join(lines)


def write_outputs(results: list[RedeemResult], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"nongfu_results_{ts}.csv"
    json_path = output_dir / f"nongfu_results_{ts}.json"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "account",
                "wxid",
                "scan_code",
                "success",
                "code",
                "message",
                "reward_type",
                "winner_amount",
                "winner_amount_yuan",
                "code_used",
                "exchange_status",
                "exchange_time_cn",
                "expire_time_cn",
                "first_scan_time_cn",
                "cust_name",
                "exchange_expire_time",
                "receive_success",
                "receive_message",
                "mobile",
                "exchange_qr_path",
            ],
        )
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "account": item.account,
                    "wxid": item.wxid,
                    "scan_code": item.scan_code,
                    "success": item.success,
                    "code": item.code,
                    "message": item.message,
                    "reward_type": item.reward_type,
                    "winner_amount": item.winner_amount,
                    "winner_amount_yuan": item.winner_amount_yuan,
                    "code_used": item.code_used,
                    "exchange_status": item.exchange_status,
                    "exchange_time_cn": item.exchange_time_cn,
                    "expire_time_cn": item.expire_time_cn,
                    "first_scan_time_cn": item.first_scan_time_cn,
                    "cust_name": item.cust_name,
                    "exchange_expire_time": item.exchange_expire_time,
                    "receive_success": item.receive_success,
                    "receive_message": item.receive_message,
                    "mobile": item.mobile,
                    "exchange_qr_path": item.exchange_qr_path,
                }
            )

    json_path.write_text(
        json.dumps([item.__dict__ for item in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return csv_path, json_path


def summarize(results: list[RedeemResult]) -> str:
    total = len(results)
    ok = sum(1 for item in results if item.success)
    reward_counter = Counter(item.reward_type or "unknown" for item in results)
    win_count = sum(
        1
        for item in results
        if item.success and item.reward_type and item.reward_type != "noSurprise"
    )
    amount = 0.0
    for item in results:
        try:
            amount += float(item.winner_amount_yuan or 0)
        except (TypeError, ValueError):
            pass

    lines = [
        "农夫瓶盖扫码结果",
        f"总数: {total}",
        f"成功请求: {ok}",
        f"中奖次数: {win_count}",
        f"红包金额合计: {amount:.2f}",
        "结果分布:",
    ]
    for key, count in reward_counter.most_common():
        lines.append(f"- {reward_name(key)}({key}): {count}")
    lines.append("明细:")
    for idx, item in enumerate(results, 1):
        msg = item.message or ""
        amount_text = item.winner_amount_yuan or item.winner_amount or ""
        qr_text = f" qr={item.exchange_qr_path}" if item.exchange_qr_path else ""
        extra_text = reward_extra_text(item)
        extra_text = f" {extra_text}" if extra_text else ""
        account_text = f" account={item.account}" if item.account else ""
        lines.append(
            f"{idx}. {reward_name(item.reward_type)}{account_text} amount={amount_text} "
            f"used={item.code_used} ok={item.success}{qr_text}{extra_text} {msg}"
        )
    return "\n".join(lines)


def build_pushplus_html(text: str, results: list[RedeemResult]) -> str:
    parts = [f"<pre>{html.escape(text)}</pre>"]
    exchange_items = [
        item for item in results
        if item.reward_type == "exchange" and item.exchange_qr_path
    ]
    if exchange_items:
        parts.append("<hr><h3>换购奖励二维码</h3>")
    for idx, item in enumerate(exchange_items, 1):
        path = Path(item.exchange_qr_path or "")
        if not path.exists():
            continue
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        extra = reward_extra_text(item)
        parts.append(
            "<div style=\"margin:12px 0;\">"
            f"<p><b>换购奖励 #{idx}</b></p>"
            f"<p>{html.escape(extra)}</p>"
            f"<p>{html.escape(item.scan_code)}</p>"
            f"<img src=\"data:image/png;base64,{b64}\" "
            "style=\"width:260px;max-width:100%;height:auto;\" />"
            "</div>"
        )
    return "\n".join(parts)


def push_message(title: str, content: str, results: list[RedeemResult] | None = None) -> None:
    timeout = 10
    pushed = False

    if os.getenv("PUSH_PLUS_TOKEN"):
        template = "html" if results else "txt"
        push_content = build_pushplus_html(content, results) if results else content
        response = requests.post(
            "http://www.pushplus.plus/send",
            json={
                "token": os.environ["PUSH_PLUS_TOKEN"],
                "title": title,
                "content": push_content,
                "template": template,
            },
            timeout=timeout,
        )
        print(f"PushPlus HTML推送响应: {response.text[:300]}")
        pushed = True

    try:
        from notify import send

        if not pushed:
            send(title, content)
            pushed = True
    except Exception:
        pass

    if os.getenv("SERVERCHAN_SENDKEY"):
        key = os.environ["SERVERCHAN_SENDKEY"]
        requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": content},
            timeout=timeout,
        )
    if os.getenv("BARK_URL"):
        base = os.environ["BARK_URL"].rstrip("/")
        requests.get(f"{base}/{quote(title)}/{quote(content)}", timeout=timeout)
    if os.getenv("PUSH_WEBHOOK"):
        requests.post(
            os.environ["PUSH_WEBHOOK"],
            json={"title": title, "content": content},
            timeout=timeout,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="农夫瓶盖码登录并扫码领奖")
    parser.add_argument("-c", "--scan-code", action="append", help="瓶盖码链接，可重复传入")
    parser.add_argument("-f", "--file", help="瓶盖码链接文本，每行一个")
    parser.add_argument(
        "--wechat-server",
        default=os.getenv("WECHAT_SERVER") or os.getenv("WX_CODE_URL") or DEFAULT_WECHAT_SERVER,
        help="getCode.py 使用的牛子协议服务地址",
    )
    parser.add_argument("--wxid", default=os.getenv("nfsq") or os.getenv("NFSQ"))
    parser.add_argument("--lat", default=os.getenv("NF_LAT", "30.17882541232639"))
    parser.add_argument("--lng", default=os.getenv("NF_LNG", "120.5893896484375"))
    parser.add_argument("--device-token", default=os.getenv("NF_DEVICE_TOKEN"))
    parser.add_argument("--output-dir", default="农夫山泉")
    parser.add_argument("--qr-dir", default="nongfu_qrcodes")
    parser.add_argument("--save-qr", action="store_true", help="save QR images for manual checking")
    parser.add_argument("--no-push", action="store_true", help="不推送结果")
    parser.add_argument("--no-receive-redpacket", action="store_true", help="只统计红包，不自动领取")
    parser.add_argument("--daily-scan-limit", type=int, default=int(os.getenv("NF_DAILY_SCAN_LIMIT", "5")), help="每个微信号每天最多扫码次数")
    parser.add_argument("--check-quota", action="store_true", help="按 S1 瓶盖活动本地开奖日志查看今日剩余扫码次数，不消耗瓶盖码")
    parser.add_argument("--delay-min", type=float, default=float(os.getenv("NF_DELAY_MIN", "5")), help="每个码之间最小随机等待秒数")
    parser.add_argument("--delay-max", type=float, default=float(os.getenv("NF_DELAY_MAX", "10")), help="每个码之间最大随机等待秒数")
    parser.add_argument("--timeout", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scan_codes = load_scan_codes(args)
    if not scan_codes and not args.check_quota:
        print("未提供瓶盖码链接。用法: python nongfu_cap_redeem.py -c HTTPS://NFSQ.CC/S1/xxx")
        return 2

    for code in scan_codes:
        parsed = urlparse(code)
        if not parsed.scheme or not parsed.netloc:
            print(f"瓶盖码链接格式异常: {code}", file=sys.stderr)
            return 2

    output_dir = Path(args.output_dir)
    if args.save_qr:
        save_qr_images(scan_codes, Path(args.qr_dir))

    try:
        accounts = load_niuzi_accounts(args.wechat_server, args.wxid, args.timeout)
    except Exception as exc:
        print(f"账号池初始化失败: {exc}", file=sys.stderr)
        return 2

    clients = [
        NongfuClient(
            wechat_server=args.wechat_server,
            wxid=account["wxid"],
            account_name=account["name"],
            timeout=args.timeout,
        )
        for account in accounts
    ]
    print(f"账号池: {len(clients)} 个在线账号")

    history_results = load_history_results(output_dir)
    existing_counts = today_scan_counts(history_results)
    daily_limit = max(1, int(args.daily_scan_limit))
    if args.check_quota:
        print(quota_report(clients, existing_counts, daily_limit))
        print()
        print(summarize_today_by_draw_time(history_results))
        return 0
    print(f"今日每号扫码上限: {daily_limit}")
    if existing_counts:
        print("今日历史开奖次数:")
        for client in clients:
            key = client.wxid or client.account_name or ""
            print(f"  {client.account_name}: {existing_counts.get(key, 0)}/{daily_limit}")

    scan_plan, skipped_codes, planned_counts = build_scan_plan(
        scan_codes,
        clients,
        existing_counts,
        daily_limit,
    )
    if skipped_codes:
        print(f"因所有账号今日达到上限，跳过 {len(skipped_codes)} 个瓶盖码")
    if not scan_plan:
        print(summarize_today_by_draw_time(history_results))
        return 0

    results: list[RedeemResult] = []
    for run_idx, (idx, scan_code, client) in enumerate(scan_plan, 1):
        key = client.wxid or client.account_name or ""
        print(
            f"[{run_idx}/{len(scan_plan)}] 原序号{idx} 账号: {client.account_name} "
            f"今日计划={planned_counts.get(key, 0)}/{daily_limit} 扫码: {scan_code}"
        )
        try:
            result = client.draw_prize(
                scan_code=scan_code,
                latitude=str(args.lat),
                longitude=str(args.lng),
                device_token=args.device_token,
            )
        except Exception as exc:
            result = RedeemResult(
                account=client.account_name,
                wxid=client.wxid,
                scan_code=scan_code,
                success=False,
                code=None,
                message=str(exc),
                reward_type="error",
                winner_amount=None,
                winner_amount_yuan=None,
                code_used=None,
                raw={"error": str(exc)},
            )

        if result.success and result.reward_type == "exchange":
            qr_path = output_dir / "exchange_qrcodes" / f"exchange_{idx:03d}.png"
            result.exchange_qr_path = str(save_qr_image(scan_code, qr_path))

        if not args.no_receive_redpacket and should_receive_red_packet(result):
            try:
                receive_data = client.receive_red_packet(scan_code)
                result.receive_raw = receive_data
                result.receive_success = bool(receive_data.get("success"))
                result.receive_message = receive_data.get("message") or receive_data.get("messageTitle")
                result.mobile = receive_data.get("_mobile")
                receive_body = receive_data.get("data") or {}
                if isinstance(receive_body, dict):
                    result.exchange_status = receive_body.get("exchangeStatus", result.exchange_status)
                    result.exchange_time_cn = receive_body.get("exchangeTimeCn", result.exchange_time_cn)
                    result.winner_amount = receive_body.get("winnerAmount", result.winner_amount)
                    result.winner_amount_yuan = receive_body.get(
                        "winnerAmountYuanStr",
                        result.winner_amount_yuan,
                    )
                if result.receive_success and result.exchange_status in (None, "", 0, "0"):
                    result.exchange_status = 1
            except Exception as exc:
                result.receive_success = False
                result.receive_message = str(exc)

        results.append(result)
        amount_text = result.winner_amount_yuan or result.winner_amount or "-"
        status_text = "成功" if result.success else "失败"
        extra_text = reward_extra_text(result)
        message_text = result.message or "-"
        print(f"  结果: {reward_name(result.reward_type)} | 金额={amount_text} | 请求={status_text}")
        if extra_text:
            print(f"  详情: {extra_text}")
        if message_text != "-":
            print(f"  消息: {message_text}")
        if result.exchange_qr_path:
            print(f"     换购奖励二维码: {result.exchange_qr_path}")
        if run_idx < len(scan_plan):
            delay_min = max(0.0, float(args.delay_min))
            delay_max = max(delay_min, float(args.delay_max))
            delay = random.uniform(delay_min, delay_max)
            if delay > 0:
                print(f"     等待 {delay:.1f}s 后继续")
                time.sleep(delay)

    csv_path, json_path = write_outputs(results, output_dir)
    report = summarize(results)
    today_report = summarize_today_by_draw_time(history_results + results)
    report = f"{report}\n\n{today_report}"
    print("\n" + report)
    print(f"\nCSV: {csv_path}")
    print(f"JSON: {json_path}")

    if not args.no_push:
        try:
            push_message("农夫瓶盖扫码结果", report, results)
        except Exception as exc:
            print(f"推送失败: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    if not (os.getenv("nfsq") or os.getenv("NFSQ") or "").strip():
        try:
            _accs = __yyb_load_accounts("")
            if _accs:
                os.environ["nfsq"] = "@".join(a["wxid"] for a in _accs)
                print(f"直连拉号 {len(_accs)} 个: {' / '.join(a['remark'] for a in _accs)}")
        except Exception as _e:
            print(f"直连拉号失败: {_e}")

    raise SystemExit(main())
