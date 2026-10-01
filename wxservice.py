"""
微信协议服务封装（yyb呆呆 格式）

对接：
  POST /api/yyb/get-code
  POST /api/yyb/invoke-cloud
  POST /api/yyb/get-phone
  POST /api/yyb/get-userinfo
  POST /api/yyb/cloud-call-function
  GET  /api/accounts

环境变量：
  WECHAT_SERVER / YYB_SERVER
  LICENSE_KEY / AUTH / ADMIN_KEY
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import requests

WECHAT_SERVER = (
    os.environ.get("WECHAT_SERVER")
    or os.environ.get("YYB_SERVER")
    or "http://localhost:8080"
)
LICENSE_KEY = (
    os.environ.get("LICENSE_KEY")
    or os.environ.get("AUTH")
    or os.environ.get("ADMIN_KEY")
    or ""
)


class WxService:
    def __init__(self, server_url: str = None, license_key: str = None):
        base = (server_url or WECHAT_SERVER or "").rstrip("/")
        if base and not base.lower().startswith(("http://", "https://")):
            base = f"http://{base}"
        self.base = base
        self.license_key = (license_key or LICENSE_KEY or "").strip()
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"
        if self.license_key:
            self.session.headers["X-License-Key"] = self.license_key
            self.session.headers["Authorization"] = f"Bearer {self.license_key}"

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"{self.base}{path}"

    def _body(self, body: dict) -> dict:
        out = dict(body or {})
        if self.license_key and "auth" not in out:
            out["auth"] = self.license_key
        # yyb 用 openid；兼容旧脚本 wxid，并解析为已绑定 openid
        raw = out.get("openid")
        if not raw:
            for k in ("wxid", "wx_id", "account", "deviceId"):
                if out.get(k):
                    raw = out[k]
                    break
        if raw:
            try:
                out["openid"] = self.resolve_openid(str(raw))
            except Exception:
                out["openid"] = str(raw).strip()
        return out

    def _post(self, path: str, body: dict, timeout: int = 30) -> dict:
        resp = self.session.post(self._url(path), json=self._body(body), timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            data = {"success": False, "error": resp.text, "status_code": resp.status_code}
        if isinstance(data, dict) and "success" not in data and resp.status_code >= 400:
            data = {
                "success": False,
                "error": data.get("error") or data.get("detail") or f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "raw": data,
            }
        return data if isinstance(data, dict) else {"success": False, "error": str(data)}

    def _get(self, path: str, timeout: int = 30) -> dict:
        resp = self.session.get(self._url(path), timeout=timeout)
        try:
            data = resp.json()
        except Exception:
            data = {"success": False, "error": resp.text, "status_code": resp.status_code}
        return data if isinstance(data, dict) else {"success": False, "error": str(data)}

    def list_accounts(self) -> list:
        data = self._get("/api/accounts")
        accounts = data.get("accounts") if isinstance(data, dict) else None
        if accounts is None and isinstance(data, list):
            accounts = data
        if not isinstance(accounts, list):
            return []
        out = []
        for a in accounts:
            if not isinstance(a, dict):
                continue
            if (a.get("status") or "active") == "error":
                continue
            oid = a.get("openid") or a.get("wxid") or a.get("wx_id") or a.get("deviceId") or ""
            if not oid:
                continue
            item = dict(a)
            item["openid"] = oid
            item["wxid"] = oid
            item["wx_id"] = oid
            item["deviceId"] = oid
            out.append(item)
        return out

    def _account_candidates(self, account: dict) -> list:
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
                if cand in (
                    str(account.get("nickname") or ""),
                    str(account.get("nick_name") or ""),
                    str(account.get("deviceName") or ""),
                ) or len(n) >= 8:
                    best = max(best, 40)
        return best

    def resolve_openid(self, identifier: str, accounts: Optional[list] = None) -> str:
        needle = (identifier or "").strip()
        if not needle:
            raise ValueError("openid/账号标识为空")
        if accounts is None:
            accounts = self.list_accounts()
        if needle.isdigit():
            idx = int(needle)
            if 1 <= idx <= len(accounts):
                oid = accounts[idx - 1].get("openid") or ""
                if oid:
                    print(f"账号标识已解析: {needle} -> {oid}")
                    return oid
            raise Exception(f"序号超出范围: {needle}（当前共 {len(accounts)} 个已绑定账号）")
        scored = []
        for a in accounts:
            s = self._match_score(needle, a)
            if s > 0:
                scored.append((s, a))
        if not scored:
            lines = []
            for i, a in enumerate(accounts[:20], 1):
                oid = a.get("openid") or ""
                nick = a.get("nickname") or a.get("nick_name") or oid[-6:]
                tail = oid[-8:] if len(oid) > 8 else oid
                lines.append(f"  [{i}] {nick} openid=...{tail}")
            detail = "\n".join(lines) if lines else "(当前授权码下无绑定账号)"
            raise Exception(
                f"账号标识未绑定到当前授权码: {needle}\n"
                f"支持：openid / openid尾缀 / 控制台昵称 / 序号(1起) / all\n"
                f"当前已绑定账号:\n{detail}"
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0][0]
        tops = [a for s, a in scored if s == top]
        if len(tops) > 1 and top < 100:
            opts = ", ".join(
                f"{(a.get('nickname') or a.get('openid', '')[-6:])}({(a.get('openid') or '')[-8:]})"
                for a in tops[:5]
            )
            raise Exception(f"账号标识不唯一: {needle}，匹配到多个账号: {opts}；请改用完整 openid")
        oid = tops[0].get("openid") or ""
        if oid and oid != needle:
            print(f"账号标识已解析: {needle} -> {oid}")
        return oid

    def get_wx_code(self, wxid: str, appid: str) -> dict:
        """获取小程序 code（对应 wx.login()）。支持 openid/昵称/尾缀自动解析。"""
        try:
            openid = self.resolve_openid(wxid)
        except Exception as e:
            return {"success": False, "error": str(e)}
        data = self._post("/api/yyb/get-code", {"openid": openid, "appid": appid})
        if data.get("success") and data.get("code"):
            return {"success": True, "code": data["code"], "appid": appid, "openid": openid}
        return {
            "success": False,
            "error": data.get("error") or data.get("detail") or "获取code失败",
            "raw": data,
        }

    def get_openid(self, wxid: str, appid: str) -> dict:
        """yyb 账号 openid 即登录态标识；补充 userinfo 昵称头像。"""
        info = self.get_userinfo(wxid, appid)
        nick, head = "", ""
        if info.get("success"):
            raw = info.get("rawData") or ""
            try:
                obj = json.loads(raw) if isinstance(raw, str) and raw else {}
                nick = obj.get("nickName") or obj.get("nickname") or ""
                head = obj.get("avatarUrl") or obj.get("headImgUrl") or ""
            except Exception:
                pass
        return {
            "success": True,
            "openid": wxid,
            "name": nick,
            "url": head,
            "sign": info.get("signature") or "",
            "raw": info,
        }

    def get_mobile(self, wxid: str, appid: str) -> dict:
        data = self._post("/api/yyb/get-phone", {"openid": wxid, "appid": appid, "param2": ""})
        if not data.get("success"):
            return {"success": False, "error": data.get("error") or "获取手机号失败", "raw": data}
        return {
            "success": True,
            "mobile": data.get("mobile") or "",
            "encryptedData": data.get("encryptedData") or "",
            "iv": data.get("iv") or "",
            "code": data.get("code") or "",
            "cloudId": data.get("cloudId") or "",
            "customPhoneList": data.get("customPhoneList") or [],
            "respJson": data.get("respJson"),
            "raw": data,
        }

    def operate_wx_data(self, wxid: str, appid: str, data_str: str) -> dict:
        """通用云操作 operateWXData（旧 invoke-cloud）。"""
        if isinstance(data_str, dict):
            data_str = json.dumps(data_str, ensure_ascii=False)
        data = self._post(
            "/api/yyb/invoke-cloud",
            {"openid": wxid, "appid": appid, "param2": data_str or "", "data": data_str or ""},
        )
        if not data.get("success"):
            return {"success": False, "error": data.get("error") or "操作失败", "rawResponse": data}
        out = {
            "success": True,
            "respJson": data.get("respJson"),
            "rawData": data.get("respJson") or data,
            "raw": data,
        }
        for k in ("encryptedData", "iv", "signature", "code", "mobile"):
            if data.get(k) is not None:
                out[k] = data.get(k)
        # 尝试从 respJson 抽 encryptedData/iv
        try:
            parsed = data.get("respJson")
            if isinstance(parsed, str) and parsed:
                parsed = json.loads(parsed)
            if isinstance(parsed, dict):
                out.setdefault("encryptedData", parsed.get("encryptedData"))
                out.setdefault("iv", parsed.get("iv"))
                out.setdefault("rawData", parsed)
        except Exception:
            pass
        return out

    def call_function(self, wxid: str, appid: str, data_str: Any = None, **kwargs) -> dict:
        """
        兼容两种旧用法：
        1) call_function(wxid, appid, json.dumps({api_name:...}))  → 走 invoke-cloud
        2) call_function(wxid, appid, functionName=..., functionData=...) → 走 cloud-call-function
        """
        fn = kwargs.get("functionName") or kwargs.get("function_name") or ""
        fdata = kwargs.get("functionData")
        if fdata is None:
            fdata = kwargs.get("function_data")
        env = kwargs.get("cloudEnv") or kwargs.get("cloud_env") or ""

        payload_obj = None
        if isinstance(data_str, dict):
            payload_obj = data_str
        elif isinstance(data_str, str) and data_str.strip():
            try:
                payload_obj = json.loads(data_str)
            except Exception:
                payload_obj = None

        # 有 api_name → operateWXData
        if isinstance(payload_obj, dict) and payload_obj.get("api_name"):
            return self.operate_wx_data(wxid, appid, json.dumps(payload_obj, ensure_ascii=False))

        if not fn and isinstance(payload_obj, dict):
            fn = payload_obj.get("functionName") or payload_obj.get("function_name") or ""
            if fdata is None:
                fdata = payload_obj.get("functionData") or payload_obj.get("function_data") or payload_obj.get("data") or {}
            env = env or payload_obj.get("cloudEnv") or payload_obj.get("cloud_env") or ""

        if not fn:
            # 旧脚本有时把整包当 data 传 call_function，尽量走 invoke-cloud
            if data_str:
                return self.operate_wx_data(wxid, appid, data_str if isinstance(data_str, str) else json.dumps(data_str, ensure_ascii=False))
            return {"success": False, "error": "functionName required"}

        if fdata is None:
            fdata = {}
        data = self._post(
            "/api/yyb/cloud-call-function",
            {
                "openid": wxid,
                "appid": appid,
                "functionName": fn,
                "functionData": fdata,
                "cloudEnv": env,
            },
        )
        if not data.get("success"):
            return {"success": False, "error": data.get("error") or "调用云函数失败", "rawResponse": data}
        return {
            "success": True,
            "data": data.get("data"),
            "respJson": data.get("respJson"),
            "encryptedData": data.get("encryptedData"),
            "iv": data.get("iv"),
            "signature": data.get("signature"),
            "rawData": data.get("data") or data,
            "raw": data,
        }

    def get_user_encrypt_key(self, wxid: str, appid: str) -> dict:
        """通过 invoke-cloud + webapi_getuserencryptkey 获取 encrypt_key。"""
        payload = {
            "api_name": "webapi_getuserencryptkey",
            "data": {},
            "with_credentials": True,
        }
        data = self.operate_wx_data(wxid, appid, json.dumps(payload, ensure_ascii=False))
        if not data.get("success"):
            return data

        key_data = {}
        raw = data.get("rawData") or data.get("respJson") or data.get("raw") or {}
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)
            if isinstance(raw, dict):
                inner = raw.get("data", raw)
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except Exception:
                        pass
                if isinstance(inner, dict):
                    key_data = inner
        except Exception:
            pass

        encrypt_key = key_data.get("encrypt_key") or key_data.get("encryptKey")
        iv = key_data.get("iv")
        if not encrypt_key:
            return {"success": False, "error": "encrypt_key not found", "raw": data}
        return {
            "success": True,
            "encrypt_key": encrypt_key,
            "iv": iv,
            "version": key_data.get("version", 3),
            "expire_in": key_data.get("expire_in"),
            "raw": data,
        }

    def get_userinfo(self, wxid: str, appid: str) -> dict:
        data = self._post("/api/yyb/get-userinfo", {"openid": wxid, "appid": appid})
        if not data.get("success"):
            return {"success": False, "error": data.get("error") or "获取用户信息失败", "raw": data}
        return {
            "success": True,
            "rawData": data.get("rawData") or "",
            "signature": data.get("signature") or "",
            "encryptedData": data.get("encryptedData") or "",
            "iv": data.get("iv") or "",
            "cloudId": data.get("cloudId") or "",
            "respJson": data.get("respJson"),
            "raw": data,
        }

    def get_session_id(self, wxid: str, appid: str) -> dict:
        """yyb 无独立 sessionid 接口；尝试 get-userinfo / encrypt-key 作为兼容占位。"""
        info = self.get_userinfo(wxid, appid)
        if info.get("success"):
            return {
                "success": True,
                "session_key": info.get("signature") or "",
                "raw": info,
                "note": "yyb呆呆无 sessionid 接口，返回 userinfo 兼容字段",
            }
        return {"success": False, "error": info.get("error") or "sessionid unavailable", "raw": info}

    def get_app_code(self, wxid: str, appid: str) -> dict:
        return self.get_wx_code(wxid, appid)

    def update_step(self, wxid: str, number: int) -> dict:
        return {"success": False, "error": "yyb呆呆不支持 update_step"}

    def status(self) -> dict:
        data = self._get("/api/accounts")
        if data.get("success") is False and data.get("error"):
            return {"success": False, "error": data.get("error")}
        accounts = data.get("accounts") if isinstance(data, dict) else None
        if accounts is None:
            return {"success": False, "error": data.get("error") or "获取账号失败", "raw": data}
        # 兼容旧 status 形态（yyb 使用 /api/accounts）
        mapped = []
        for a in accounts:
            oid = a.get("openid") or ""
            mapped.append({
                "wx_id": oid,
                "wxid": oid,
                "openid": oid,
                "nick_name": a.get("nickname") or "",
                "deviceId": oid,
                "deviceName": a.get("nickname") or "",
                "license": self.license_key,
                "authKey": self.license_key,
                "status": a.get("status") or "active",
            })
        return {"success": True, "data": mapped, "status": True, "accounts": accounts, "raw": data}
