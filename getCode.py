#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信小程序登录 Code 获取模块（yyb呆呆 格式）

对接：D:\\ai写脚本输出目录\\yyb呆呆
  - GET  /api/accounts
  - POST /api/yyb/get-code
  - POST /api/yyb/get-codes

环境变量：
  WECHAT_SERVER / YYB_SERVER  服务地址，如 http://127.0.0.1:8000
  LICENSE_KEY / AUTH / ADMIN_KEY  授权码（ADMIN_KEY 兼容旧变量名）
  WX_ID  可选，指定 openid/wxid，多个用 & 分隔；不设则取全部账号
"""

from __future__ import annotations

import os
import pathlib
from typing import Dict, List, Optional, Tuple

import requests


class WeChatCodeGetter:
    """yyb呆呆 Code 获取器。对外仍保留旧类名，方便现有脚本 import。"""

    def __init__(self):
        self.wechat_server = (
            os.getenv("WECHAT_SERVER")
            or os.getenv("YYB_SERVER")
            or ""
        ).strip().rstrip("/")
        self.license_key = (
            os.getenv("LICENSE_KEY")
            or os.getenv("AUTH")
            or os.getenv("ADMIN_KEY")
            or ""
        ).strip()
        self.wx_id_filter = (os.getenv("WX_ID") or "").strip()
        self.script_dir = pathlib.Path(__file__).parent.absolute()
        self.protocol_type = "yyb呆呆"

        if not self.wechat_server:
            raise ValueError("环境变量 WECHAT_SERVER / YYB_SERVER 未设置")
        if not self.wechat_server.lower().startswith(("http://", "https://")):
            self.wechat_server = f"http://{self.wechat_server}"
        if not self.license_key:
            raise ValueError("环境变量 LICENSE_KEY / AUTH / ADMIN_KEY 未设置（yyb呆呆授权码）")

        self.target_wx_ids: List[str] = []
        if self.wx_id_filter:
            self.target_wx_ids = [x.strip() for x in self.wx_id_filter.split("&") if x.strip()]
            print(f"检测到 WX_ID，将筛选账号: {', '.join(self.target_wx_ids)}")

        print(f"当前协议服务: yyb呆呆 @ {self.wechat_server}")

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-License-Key": self.license_key,
            "Authorization": f"Bearer {self.license_key}",
        }

    def _auth_body(self, extra: Optional[dict] = None) -> dict:
        body = {"auth": self.license_key}
        if extra:
            body.update(extra)
        return body

    def _account_id(self, account: dict) -> str:
        return (
            account.get("openid")
            or account.get("wxid")
            or account.get("wx_id")
            or account.get("deviceId")
            or ""
        )

    def _nick(self, account: dict, fallback: str = "") -> str:
        nick = (
            account.get("nickname")
            or account.get("nick_name")
            or account.get("deviceName")
            or ""
        ).strip()
        if nick and nick != "ㅤ":
            return nick
        aid = self._account_id(account)
        if aid:
            return f"账号_{aid[-6:]}"
        return fallback or "未知昵称"

    def get_auth_keys(self) -> List[dict]:
        """获取账号列表（兼容旧方法名）。"""
        url = f"{self.wechat_server}/api/accounts"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=30)
            if resp.status_code == 401:
                raise Exception("授权码无效或未登录（401）")
            if resp.status_code == 403:
                raise Exception("授权码无效/禁用/过期（403）")
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise Exception(f"获取账号列表失败: {e}") from e

        accounts = data.get("accounts") if isinstance(data, dict) else None
        if accounts is None and isinstance(data, list):
            accounts = data
        if not isinstance(accounts, list):
            raise Exception(f"账号列表响应格式错误: {data}")

        valid = []
        for a in accounts:
            if not isinstance(a, dict):
                continue
            if (a.get("status") or "active") == "error":
                continue
            aid = self._account_id(a)
            if not aid:
                continue
            # 统一字段，兼容旧脚本读取
            item = dict(a)
            item.setdefault("openid", aid)
            item.setdefault("wxid", aid)
            item.setdefault("wx_id", aid)
            item.setdefault("deviceId", aid)
            item.setdefault("license", self.license_key)
            item.setdefault("authKey", self.license_key)
            item.setdefault("nick_name", self._nick(item))
            item.setdefault("nickname", item["nick_name"])
            valid.append(item)

        filtered = self._filter_accounts_by_wx_id(valid)
        print(f"通过 /api/accounts 获取到 {len(filtered)} 个有效账号")
        return filtered

    def _filter_accounts_by_wx_id(self, accounts: List[dict]) -> List[dict]:
        if not self.target_wx_ids:
            return accounts
        out = []
        seen = set()
        for raw in self.target_wx_ids:
            try:
                a = self._match_account(raw, accounts)
            except Exception:
                continue
            aid = self._account_id(a)
            if aid and aid not in seen:
                seen.add(aid)
                out.append(a)
        return out

    def _account_candidates(self, account: dict) -> List[str]:
        vals = []
        for k in (
            "openid",
            "wxid",
            "wx_id",
            "deviceId",
            "unionid",
            "nickname",
            "nick_name",
            "deviceName",
        ):
            v = account.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s and s not in vals:
                vals.append(s)
        return vals

    def _match_score(self, needle: str, account: dict) -> int:
        """匹配分：精确 > 尾缀/前缀 > 昵称包含；0=不匹配。"""
        n = (needle or "").strip()
        if not n:
            return 0
        n_low = n.lower()
        best = 0
        for cand in self._account_candidates(account):
            c_low = cand.lower()
            if c_low == n_low:
                return 100
            if len(n) >= 6 and (c_low.endswith(n_low) or n_low.endswith(c_low)):
                best = max(best, 80)
            elif len(n) >= 4 and (c_low.startswith(n_low) or n_low.startswith(c_low)):
                best = max(best, 60)
            elif len(n) >= 2 and (n_low in c_low or c_low in n_low):
                # 昵称/短标识模糊匹配，分最低，避免误伤
                if cand in (
                    str(account.get("nickname") or ""),
                    str(account.get("nick_name") or ""),
                    str(account.get("deviceName") or ""),
                ) or len(n) >= 8:
                    best = max(best, 40)
        return best

    def _format_bound_accounts(self, accounts: List[dict], limit: int = 20) -> str:
        if not accounts:
            return "(当前授权码下无绑定账号，请先在 yyb呆呆 扫码绑定)"
        lines = []
        for i, a in enumerate(accounts[:limit], 1):
            oid = self._account_id(a)
            nick = self._nick(a)
            tail = oid[-8:] if len(oid) > 8 else oid
            lines.append(f"  [{i}] {nick} openid=...{tail}")
        if len(accounts) > limit:
            lines.append(f"  ... 共 {len(accounts)} 个")
        return "\n".join(lines)

    def _load_all_accounts(self) -> List[dict]:
        prev = self.target_wx_ids
        self.target_wx_ids = []
        try:
            return self.get_auth_keys()
        finally:
            self.target_wx_ids = prev

    def _match_account(self, identifier: str, accounts: Optional[List[dict]] = None) -> dict:
        """把标识解析成已绑定账号。支持 openid/尾缀/昵称/控制台序号(1起)。"""
        needle = (identifier or "").strip()
        if not needle:
            raise ValueError("openid/账号标识为空")
        if accounts is None:
            accounts = self._load_all_accounts()

        # 控制台序号：1 / 2 / 3 ... 按 /api/accounts 返回顺序
        if needle.isdigit():
            idx = int(needle)
            if 1 <= idx <= len(accounts):
                return accounts[idx - 1]
            raise Exception(
                f"序号超出范围: {needle}（当前共 {len(accounts)} 个已绑定账号）\n"
                f"当前已绑定账号:\n{self._format_bound_accounts(accounts)}"
            )

        scored: List[Tuple[int, dict]] = []
        for a in accounts:
            s = self._match_score(needle, a)
            if s > 0:
                scored.append((s, a))
        if not scored:
            raise Exception(
                f"账号标识未绑定到当前授权码: {needle}\n"
                f"支持：openid / openid尾缀 / 控制台昵称 / 序号(1起) / all\n"
                f"当前已绑定账号:\n{self._format_bound_accounts(accounts)}"
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0][0]
        tops = [a for s, a in scored if s == top]
        if len(tops) > 1 and top < 100:
            opts = ", ".join(
                f"{self._nick(a)}({self._account_id(a)[-8:]})" for a in tops[:5]
            )
            raise Exception(
                f"账号标识不唯一: {needle}，匹配到多个账号: {opts}；请改用完整 openid 或序号"
            )
        return tops[0]

    def resolve_accounts(self, selectors: Optional[List[str]] = None) -> List[dict]:
        """
        直连取码：把环境变量里的标识列表解析成账号。
        selectors 为空 / all / * → 当前授权码下全部已绑定账号。
        """
        accounts = self._load_all_accounts()
        if not selectors:
            return accounts
        cleaned = []
        for s in selectors:
            t = (s or "").strip()
            if not t:
                continue
            if t.lower() in ("all", "*", "全部", "auto"):
                return accounts
            cleaned.append(t)
        if not cleaned:
            return accounts
        out = []
        seen = set()
        for raw in cleaned:
            a = self._match_account(raw, accounts)
            oid = self._account_id(a)
            if oid and oid not in seen:
                seen.add(oid)
                out.append(a)
        return out

    def resolve_openid(self, identifier: str, accounts: Optional[List[dict]] = None) -> str:
        """解析并返回已绑定 openid。"""
        a = self._match_account(identifier, accounts)
        oid = self._account_id(a)
        if not oid:
            raise Exception(f"匹配到账号但无 openid: {identifier}")
        if oid != identifier.strip():
            print(f"账号标识已解析: {identifier} -> {oid} ({self._nick(a)})")
        return oid

    def get_login_status(self, license_or_openid: str = "") -> dict:
        """兼容旧接口：yyb呆呆账号在线以 /api/accounts 为准。"""
        return {"loginState": 1, "onlineTime": 0, "device": license_or_openid or ""}

    def get_online_accounts(self) -> List[Tuple[dict, dict]]:
        accounts = self.get_auth_keys()
        return [(a, self.get_login_status(self._account_id(a))) for a in accounts]

    def print_online_status(self):
        online = self.get_online_accounts()
        print(f"当前有 {len(online)} 个账号")
        for account, status in online:
            print(f"{self._nick(account)} {status.get('onlineTime', '')}")

    def get_applet_code(self, app_id: str, license_or_openid: str) -> str:
        """为指定 openid 取 code。第二参数兼容旧 license/wxid/昵称，自动解析为已绑定 openid。"""
        raw = (license_or_openid or "").strip()
        if not raw:
            raise ValueError("openid required")
        openid = self.resolve_openid(raw)
        url = f"{self.wechat_server}/api/yyb/get-code"
        payload = self._auth_body({"openid": openid, "appid": app_id})
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=60)
            data = resp.json() if resp.text else {}
        except requests.RequestException as e:
            raise Exception(f"请求小程序 Code 失败: {e}") from e
        except Exception as e:
            raise Exception(f"小程序 Code 响应解析失败: {e}") from e

        if resp.status_code >= 400:
            err = data.get("error") or data.get("detail") or f"HTTP {resp.status_code}: {data}"
            if "not bound" in str(err).lower() or "未绑定" in str(err):
                raise Exception(
                    f"{err}；请求 openid={openid}（原始标识={raw}）。"
                    f"请确认 WXIDXJ/WX_ID 使用 yyb呆呆 当前授权码下已绑定的 openid"
                )
            raise Exception(err)
        if data.get("success") is False:
            raise Exception(data.get("error") or "获取小程序 Code 失败")
        code = data.get("code") or (data.get("data") or {}).get("code")
        if not code:
            raise Exception(f"响应中未找到 code: {data}")
        return str(code)

    def get_codes_for_all_online_accounts(self, app_id: str) -> Dict[str, str]:
        """昵称 -> code。优先走批量 get-codes，失败再逐个 get-code。"""
        online = self.get_online_accounts()
        if not online:
            return {}

        openids = []
        nick_map: Dict[str, str] = {}
        used_nicks = set()
        for i, (account, _) in enumerate(online, 1):
            openid = self._account_id(account)
            if not openid:
                continue
            nick = self._nick(account, fallback=f"账号_{i}")
            base = nick
            c = 1
            while nick in used_nicks:
                nick = f"{base}_{c}"
                c += 1
            used_nicks.add(nick)
            openids.append(openid)
            nick_map[openid] = nick

        codes: Dict[str, str] = {}
        # 批量
        try:
            url = f"{self.wechat_server}/api/yyb/get-codes"
            payload = self._auth_body({"accounts": openids, "appid": app_id})
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=120)
            data = resp.json() if resp.text else {}
            results = data.get("results") or []
            if results:
                for item in results:
                    oid = item.get("openid") or ""
                    nick = nick_map.get(oid) or oid
                    if item.get("success") and item.get("code"):
                        codes[nick] = str(item["code"])
                        print(f"获取 {nick} 的 Code 成功: {item['code']}")
                    else:
                        print(f"获取 {nick} 的 Code 失败: {item.get('error') or data}")
                if codes:
                    return codes
        except Exception as e:
            print(f"批量 get-codes 失败，改为逐个获取: {e}")

        # 逐个
        for openid in openids:
            nick = nick_map.get(openid, openid)
            try:
                code = self.get_applet_code(app_id, openid)
                codes[nick] = code
                print(f"获取 {nick} 的 Code 成功: {code}")
            except Exception as e:
                print(f"获取 {nick} 的 Code 失败: {e}")
        return codes


def get_wechat_codes(app_id: str) -> Dict[str, str]:
    """获取所有在线微信账号的小程序登录 Code。返回 昵称->code。"""
    return WeChatCodeGetter().get_codes_for_all_online_accounts(app_id)


def print_online_status():
    WeChatCodeGetter().print_online_status()


def get_single_code(app_id: str, openid: str) -> str:
    """为指定 openid（或可解析的昵称/openid尾缀）获取小程序登录 Code。"""
    return WeChatCodeGetter().get_applet_code(app_id, openid)


def make_getter(wechat_server: str = "", license_key: str = "") -> WeChatCodeGetter:
    """给脚本内嵌调用用：可不经环境变量完整初始化。"""
    g = WeChatCodeGetter.__new__(WeChatCodeGetter)
    g.wechat_server = (
        wechat_server
        or os.getenv("WECHAT_SERVER")
        or os.getenv("YYB_SERVER")
        or ""
    ).strip().rstrip("/")
    if g.wechat_server and not g.wechat_server.lower().startswith(("http://", "https://")):
        g.wechat_server = f"http://{g.wechat_server}"
    g.license_key = (
        license_key
        or os.getenv("LICENSE_KEY")
        or os.getenv("AUTH")
        or os.getenv("ADMIN_KEY")
        or ""
    ).strip()
    g.admin_key = g.license_key
    g.wx_id_filter = ""
    g.target_wx_ids = []
    g.script_dir = pathlib.Path(__file__).parent.absolute()
    g.protocol_type = "yyb呆呆"
    if not g.wechat_server:
        raise ValueError("环境变量 WECHAT_SERVER / YYB_SERVER 未设置")
    if not g.license_key:
        raise ValueError("环境变量 LICENSE_KEY / AUTH / ADMIN_KEY 未设置（yyb呆呆授权码）")
    return g
