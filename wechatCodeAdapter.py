"""
微信 Code 适配器（yyb呆呆 格式）

统一走：
  GET  /api/accounts
  POST /api/yyb/get-code

环境变量（兼容旧名）：
  WECHAT_SERVER / YYB_SERVER
  LICENSE_KEY / AUTH / ADMIN_KEY / wx_code_token
  WX_CODE_URL  可填完整 get-code URL，或服务根地址
"""

from __future__ import annotations

import os
import traceback
from typing import Any, Optional

import requests


class WechatCodeAdapter:
    def __init__(self, wx_appid: str, log=None, **kwargs):
        self.wx_appid = wx_appid
        self.log = log or (lambda msg, level="info": print(msg))
        self.wx_protocol_type = 0  # 0 = yyb呆呆

        server = (
            kwargs.get("server")
            or kwargs.get("wechat_server")
            or os.getenv("WECHAT_SERVER")
            or os.getenv("YYB_SERVER")
            or ""
        ).strip().rstrip("/")

        code_url = (
            kwargs.get("wx_code_url")
            or os.getenv("WX_CODE_URL")
            or ""
        ).strip()

        if code_url:
            # 允许直接给完整 get-code 地址
            if code_url.rstrip("/").endswith("/api/yyb/get-code"):
                self.wx_code_url = code_url
                self.server = code_url.split("/api/yyb/get-code")[0].rstrip("/")
            elif "/api/v1/wx" in code_url or "/api/yyb/" in code_url:
                # 旧牛子 URL / 已是 yyb 路径 → 取服务根
                if "/api/yyb/" in code_url:
                    self.server = code_url.split("/api/yyb/")[0].rstrip("/")
                else:
                    self.server = code_url.split("/api/v1/wx")[0].rstrip("/")
                self.wx_code_url = f"{self.server}/api/yyb/get-code"
            else:
                self.server = code_url.rstrip("/")
                self.wx_code_url = f"{self.server}/api/yyb/get-code"
        elif server:
            if not server.lower().startswith(("http://", "https://")):
                server = f"http://{server}"
            self.server = server
            self.wx_code_url = f"{self.server}/api/yyb/get-code"
        else:
            self.server = ""
            self.wx_code_url = ""

        self.wx_code_token = (
            kwargs.get("token")
            or kwargs.get("license_key")
            or kwargs.get("wx_code_token")
            or os.getenv("LICENSE_KEY")
            or os.getenv("AUTH")
            or os.getenv("ADMIN_KEY")
            or os.getenv("wx_code_token")
            or ""
        ).strip()

        self.wx_accounts_list = []
        self._init_all_accounts()

    def get_protocol_type(self) -> int:
        return 0

    def dict_keys_to_lower(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {str(k).lower(): self.dict_keys_to_lower(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.dict_keys_to_lower(i) for i in obj]
        return obj

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.wx_code_token:
            h["X-License-Key"] = self.wx_code_token
            h["Authorization"] = f"Bearer {self.wx_code_token}"
        return h

    def _init_all_accounts(self):
        self.wx_accounts_list = self.get_auth_keys() or []

    def get_auth_keys(self):
        if not self.server:
            self.log("[获取账号] 未配置服务地址", level="error")
            return []
        try:
            url = f"{self.server}/api/accounts"
            resp = requests.get(url, headers=self._headers(), timeout=15)
            resp.raise_for_status()
            data = resp.json()
            accounts = data.get("accounts") if isinstance(data, dict) else data
            if not isinstance(accounts, list):
                self.log(f"[获取账号] 响应异常: {data}", level="error")
                return []
            out = []
            for a in accounts:
                oid = a.get("openid") or a.get("wxid") or a.get("wx_id") or ""
                if not oid:
                    continue
                item = dict(a)
                item["openid"] = oid
                item["wxid"] = oid
                item["wx_id"] = oid
                item["deviceId"] = oid
                item["authKey"] = self.wx_code_token
                item["license"] = self.wx_code_token
                out.append(item)
            return out
        except Exception as e:
            self.log(f"[获取账号] 发生错误: {e}\n{traceback.format_exc()}", level="error")
            return []

    def get_all_devices(self):
        return self.get_auth_keys()

    def get_target_key_by_wxid(self, all_keys, wx_id):
        for key in all_keys or []:
            _wx_id = key.get("deviceId") or key.get("wx_id") or key.get("wxid") or key.get("openid")
            if _wx_id == wx_id:
                return key.get("authKey") or key.get("license") or self.wx_code_token
        return self.wx_code_token

    def _match_score(self, needle: str, account: dict) -> int:
        n = (needle or "").strip().lower()
        if not n:
            return 0
        cands = []
        for k in ("openid", "wxid", "wx_id", "deviceId", "unionid", "nickname", "nick_name", "deviceName"):
            v = str(account.get(k) or "").strip()
            if v and v not in cands:
                cands.append(v)
        best = 0
        for cand in cands:
            c = cand.lower()
            if c == n:
                return 100
            if len(n) >= 6 and (c.endswith(n) or n.endswith(c)):
                best = max(best, 80)
            elif len(n) >= 4 and (c.startswith(n) or n.startswith(c)):
                best = max(best, 60)
            elif len(n) >= 2 and (n in c or c in n):
                if cand in (
                    str(account.get("nickname") or ""),
                    str(account.get("nick_name") or ""),
                    str(account.get("deviceName") or ""),
                ) or len(n) >= 8:
                    best = max(best, 40)
        return best

    def resolve_openid(self, wx_id: str) -> str:
        needle = (wx_id or "").strip()
        if not needle:
            raise ValueError("openid/账号标识为空")
        accounts = self.wx_accounts_list or self.get_auth_keys() or []
        self.wx_accounts_list = accounts
        scored = []
        for a in accounts:
            s = self._match_score(needle, a)
            if s > 0:
                scored.append((s, a))
        if not scored:
            lines = []
            for i, a in enumerate(accounts[:20], 1):
                oid = a.get("openid") or a.get("wxid") or ""
                nick = a.get("nickname") or a.get("nick_name") or oid[-6:]
                tail = oid[-8:] if len(oid) > 8 else oid
                lines.append(f"  [{i}] {nick} openid=...{tail}")
            detail = "\n".join(lines) if lines else "(当前授权码下无绑定账号)"
            raise Exception(
                f"账号标识未绑定到当前授权码: {needle}\n"
                f"yyb呆呆 取码必须使用本授权码下已绑定的 openid（或可匹配的昵称/openid尾缀）。\n"
                f"当前已绑定账号:\n{detail}"
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0][0]
        tops = [a for s, a in scored if s == top]
        if len(tops) > 1 and top < 100:
            opts = ", ".join(
                f"{(a.get('nickname') or (a.get('openid') or '')[-6:])}({(a.get('openid') or '')[-8:]})"
                for a in tops[:5]
            )
            raise Exception(f"账号标识不唯一: {needle}，匹配到多个账号: {opts}；请改用完整 openid")
        oid = tops[0].get("openid") or tops[0].get("wxid") or ""
        if oid and oid != needle:
            self.log(f"[微信授权] 账号标识已解析: {needle} -> {oid}")
        return oid

    def get_code(self, wx_id: str):
        """获取指定 openid/wxid 的 code。"""
        return self.get_code_yyb(wx_id)

    def get_code_yyb(self, wx_id: str):
        try:
            if not self.wx_code_url:
                self.log("[微信授权] 未配置 WX_CODE_URL / WECHAT_SERVER", level="error")
                return False
            openid = self.resolve_openid(wx_id)
            payload = {
                "openid": openid,
                "wxid": openid,
                "appid": self.wx_appid,
                "auth": self.wx_code_token,
            }
            response = requests.post(
                self.wx_code_url,
                headers=self._headers(),
                json=payload,
                timeout=30,
            )
            if response.status_code >= 400:
                try:
                    data = response.json()
                except Exception:
                    data = {"error": response.text}
                self.log(f"[微信授权] 失败: {data.get('error') or data.get('detail') or data}", level="error")
                return False
            data = response.json()
            if data.get("success") is False:
                self.log(f"[微信授权] 失败: {data.get('error') or data}", level="error")
                return False
            code = data.get("code") or (data.get("data") or {}).get("code")
            if code:
                return code
            # 兼容旧壳
            low = self.dict_keys_to_lower(data)
            code = (low.get("data") or {}).get("code") or low.get("code")
            if code and code not in (0, 200, "0", "200"):
                return code
            self.log(f"[微信授权] 失败，响应: {data}", level="error")
            return False
        except requests.RequestException as e:
            self.log(f"[微信授权] 网络错误: {e}\n{traceback.format_exc()}", level="error")
            return False
        except Exception as e:
            self.log(f"[微信授权] 未知错误: {e}\n{traceback.format_exc()}", level="error")
            return False

    # 兼容旧协议分发入口
    def get_code_1(self, wx_id):
        return self.get_code_yyb(wx_id)

    def get_code_2(self, wx_id):
        return self.get_code_yyb(wx_id)

    def get_code_3(self, wx_id):
        return self.get_code_yyb(wx_id)

    def get_code_4(self, wx_id):
        return self.get_code_yyb(wx_id)

    def get_code_5(self, wx_id):
        return self.get_code_yyb(wx_id)
