"""
花园种高粱游戏 Python 客户端
API Base: https://apimallwm.exijiu.com
"""

import time
import json
import base64
import logging
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from wxservice import WxService
from captcha import DdddOcr

logger = logging.getLogger(__name__)

BASE_URL = "https://apimallwm.exijiu.com"
MAIN_BASE_URL = "https://xcx.exijiu.com/anti-channeling/public/index.php/api/v2"
APPID = "wx489f950decfeb93e"


class AesCrypto:
    """对应前端 utils/AesCrypto.js 的 AES-CBC-PKCS7 加密

    JS 源码关键逻辑（aesEncryptData）：
        key = utf8.toBytes(encryptKey)   # 直接 UTF-8
        iv  = utf8.toBytes(iv)           # 直接 UTF-8
        plaintext = JSON.stringify(整个payload含业务参数+ts)

    addEncryptData 流程：
        t.ts = Date.now()
        a = JSON.stringify(t)            # t 包含业务参数 + ts
        t.encryptData = aesEncryptData(a, encryptKey, iv)
        t.version = version

    注意：webapi_getuserencryptkey 返回的 encrypt_key 是 base64 编码的16字节，
    需要先 base64 decode 再转回字符串传入，或直接传 bytes。
    iv 是16字符 hex 字符串，直接 UTF-8 编码为16字节。
    """

    def __init__(self, key_bytes: bytes, iv_bytes: bytes):
        """直接接受 bytes，避免编码歧义"""
        if len(key_bytes) not in (16, 24, 32):
            raise ValueError(f"AES key 长度必须是 16/24/32 字节，当前: {len(key_bytes)}  hex={key_bytes.hex()}")
        if len(iv_bytes) != 16:
            raise ValueError(f"AES iv 长度必须是 16 字节，当前: {len(iv_bytes)}")
        self.key = key_bytes
        self.iv  = iv_bytes

    def encrypt(self, payload: dict) -> str:
        """加密整个 payload dict（含业务参数+ts），返回 hex 字符串"""
        plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        return cipher.encrypt(pad(plaintext, AES.block_size)).hex()

    def decrypt(self, hex_str: str) -> dict:
        ciphertext = bytes.fromhex(hex_str)
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        return json.loads(unpad(cipher.decrypt(ciphertext), AES.block_size).decode("utf-8"))


class GardenClient:
    """
    花园种高粱游戏客户端

    两种使用方式：

    1. 手动传 token（抓包获取）：
        client = GardenClient(token="your_token_here")
        client.set_crypto(key="...", iv="...")  # 加密操作需要

    2. 通过 wxservice 自动登录（需要 WECHAT_SERVER）：
        client = GardenClient()
        client.auto_login(wxid="your_wxid", server_url="http://your-wx-server")
    """

    def __init__(self, token: str = None, ocr_server: str = None):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 MicroMessenger MiniProgram",
        })
        self.token = token
        self.crypto: AesCrypto | None = None
        self.wxid: str | None = None
        self._encrypt_version: int = 1
        self._crypto_set_time: float = 0
        self._crypto_key: str = ""
        self._crypto_iv: str = ""
        self._wx: WxService | None = None
        self._wx_appid: str = APPID
        self.ocr = DdddOcr(ocr_server) if ocr_server else None
        if token:
            self.session.headers["Authorization"] = token

    def set_token(self, token: str):
        self.token = token
        # garden API 用 Authorization 请求头，值直接是 token（不加 Bearer 前缀）
        self.session.headers["Authorization"] = token

    def set_crypto(self, key: str, iv: str, version: int = 3):
        """
        设置AES加密密钥。
        key: 原始字符串，直接 UTF-8 编码（与 JS utf8.toBytes() 一致）
             webapi_getuserencryptkey 返回的 base64 字符串直接用，不 decode
        iv:  原始字符串，直接 UTF-8 编码
             webapi_getuserencryptkey 返回的 hex 字符串直接用，不 decode
        """
        key_bytes = key.encode("utf-8")
        iv_bytes  = iv.encode("utf-8")
        self.crypto = AesCrypto(key_bytes, iv_bytes)
        self._encrypt_version = version
        self._crypto_set_time = time.time()
        self._crypto_key = key
        self._crypto_iv = iv

    def auto_login(self, wxid: str, server_url: str = None, appid: str = APPID, ocr_server: str = None) -> dict:
        """
        通过微信协议服务自动完成登录流程：
        1. 获取 wx.login() code → 换取 login_code（主系统）
        2. 再获取一个 code → 换取 garden authorized_token
        3. 尝试多种方式获取 encryptKey/iv：
           a. 环境变量 GARDEN_ENCRYPT_KEY / GARDEN_ENCRYPT_IV（最优先）
           b. webapi_getuserinfo → getAuth
           c. get_mobile encryptedData → getAuth

        ocr_server: ddddocr 服务地址，设置后遇到滑块验证自动处理
        """
        import logging as _log
        import os as _os
        logger = _log.getLogger(__name__)

        if ocr_server:
            self.ocr = DdddOcr(ocr_server)

        wx = WxService(server_url)
        self.wxid = wxid
        self._wx = wx
        self._wx_appid = appid

        # Step 1: 获取 login_code（主系统，baseUrl = xcx.exijiu.com）
        code_res1 = wx.get_wx_code(wxid, appid)
        if not code_res1["success"]:
            raise RuntimeError(f"获取code失败: {code_res1['error']}")

        main_login_url = f"{MAIN_BASE_URL}/auth/session?code={code_res1['code']}"
        resp = self.session.get(main_login_url, timeout=15)
        resp.raise_for_status()
        main_body = resp.json()
        logger.debug(f"main login response: {main_body}")
        if main_body.get("code") == 0:
            login_code = main_body["data"].get("login_code")
            if login_code:
                self.session.headers["login_code"] = login_code
                logger.info("login_code 获取成功")

        # Step 2: 再获取一个 code → garden authorized_token
        code_res2 = wx.get_wx_code(wxid, appid)
        if not code_res2["success"]:
            raise RuntimeError(f"获取garden code失败: {code_res2['error']}")

        login_result = self.login(code_res2["code"])
        token = login_result.get("authorized_token") or login_result.get("token") or login_result.get("access_token")
        if not token:
            raise RuntimeError(f"登录未返回token，响应: {login_result}")
        self.set_token(token)

        # Step 3: 获取加密密钥（多种方式）
        # 方式 a: 环境变量（最优先，适合抓包后手动填入）
        env_key = _os.environ.get("GARDEN_ENCRYPT_KEY", "")
        env_iv = _os.environ.get("GARDEN_ENCRYPT_IV", "")
        if env_key and env_iv:
            self.set_crypto(env_key, env_iv)
            logger.info("使用环境变量中的加密密钥")
            return {"token": token, "crypto_ready": True}

        # 方式 b: webapi_getuserencryptkey（直接获取 encryptKey/iv，最可靠）
        try:
            enc_key_res = wx.get_user_encrypt_key(wxid, appid)
            logger.debug(f"get_user_encrypt_key response: {enc_key_res}")
            if enc_key_res.get("success"):
                self.set_crypto(enc_key_res["encrypt_key"], enc_key_res["iv"],
                                version=enc_key_res.get("version", 3))
                logger.info(f"通过 webapi_getuserencryptkey 获取到加密密钥 version={enc_key_res.get('version')}")
                return {"token": token, "crypto_ready": True}
            logger.warning(f"webapi_getuserencryptkey 失败: {enc_key_res.get('error')}")
        except Exception as e:
            logger.warning(f"webapi_getuserencryptkey 异常: {e}")

        # 方式 c: get_sessionid → 用 session_key 直接加密（version=1，备选）
        try:
            sess_res = wx.get_session_id(wxid, appid)
            logger.debug(f"get_sessionid response: {sess_res}")
            if sess_res.get("success"):
                sk_hex = sess_res.get("session_key", "")
                if sk_hex and len(sk_hex) >= 32:
                    sk_str = sk_hex[:16]
                    iv_str = sk_hex[16:32]
                    self.set_crypto(sk_str, iv_str, version=1)
                    self._session_key_hex = sk_hex
                    logger.info("使用 session_key 作为加密密钥（version=1，备选）")
        except Exception as e:
            logger.debug(f"get_sessionid 异常: {e}")

        # 方式 d: webapi_getuserinfo → getAuth
        encrypted_data, iv = self._try_get_encrypted_data_via_userinfo(wx, wxid, appid, logger)
        if not encrypted_data or not iv:
            encrypted_data, iv = self._try_get_encrypted_data_via_mobile(wx, wxid, appid, logger)
        if encrypted_data and iv:
            self._try_get_auth(encrypted_data, iv, logger)

        return {"token": token, "crypto_ready": self.crypto is not None}

    def _try_get_encrypted_data_via_userinfo(self, wx, wxid: str, appid: str, logger) -> tuple:
        """尝试通过 webapi_getuserinfo 获取 encryptedData/iv"""
        try:
            res = wx.call_function(wxid, appid, json.dumps({
                "api_name": "webapi_getuserinfo",
                "data": {"lang": "zh_CN"},
                "with_credentials": True
            }))
            logger.debug(f"webapi_getuserinfo response: {res}")
            if res.get("success"):
                return res.get("encryptedData"), res.get("iv")
            logger.warning(f"webapi_getuserinfo 失败: {res.get('error')}，rawResponse={res.get('rawResponse')}")
        except Exception as e:
            logger.warning(f"webapi_getuserinfo 异常: {e}")
        return None, None

    def _try_get_encrypted_data_via_mobile(self, wx, wxid: str, appid: str, logger) -> tuple:
        """尝试通过 get_mobile 获取 encryptedData/iv"""
        try:
            res = wx.get_mobile(wxid, appid)
            logger.debug(f"get_mobile response: {res}")
            if res.get("success") and res.get("encryptedData") and res.get("iv"):
                logger.info("通过 get_mobile 获取到 encryptedData/iv")
                return res["encryptedData"], res["iv"]
            logger.warning(f"get_mobile 未返回有效数据: {res}")
        except Exception as e:
            logger.warning(f"get_mobile 异常: {e}")
        return None, None

    def _try_get_auth(self, encrypted_data: str, iv: str, logger):
        """调用 getAuth 接口尝试获取 encryptKey"""
        try:
            auth_result = self._get_raw("/garden/wechat/auth", {
                "encryptedData": encrypted_data,
                "iv": iv
            })
            logger.debug(f"getAuth response: {auth_result}")
            key = auth_result.get("encryptKey") or auth_result.get("encrypt_key")
            auth_iv = auth_result.get("iv")
            version = auth_result.get("version")
            if key and auth_iv:
                self.set_crypto(key, auth_iv, version=version or 2)
                logger.info(f"从 getAuth 获取到加密密钥 version={version}")
            else:
                logger.warning(f"getAuth 未返回 encryptKey，完整响应: {auth_result}")
        except Exception as e:
            logger.warning(f"getAuth 调用失败: {e}")

    def _refresh_crypto_if_needed(self):
        """密钥剩余有效期不足5分钟时自动刷新"""
        if not self.crypto or not self._wx or not self.wxid:
            return
        elapsed = time.time() - self._crypto_set_time
        if elapsed < 3300:  # 密钥有效期约3600秒，提前5分钟刷新
            return
        try:
            res = self._wx.get_user_encrypt_key(self.wxid, self._wx_appid)
            if res.get("success"):
                self.set_crypto(res["encrypt_key"], res["iv"], version=res.get("version", 3))
                logger.info("加密密钥已自动刷新")
            else:
                logger.warning(f"密钥刷新失败: {res.get('error')}")
        except Exception as e:
            logger.warning(f"密钥刷新异常: {e}")

    def _encrypt_payload(self, data: dict) -> dict:
        """
        对应 JS AesCrypto.addEncryptData(t)：
          t.ts = Date.now()
          a = JSON.stringify(t)          # 整个对象含业务参数+ts
          t.encryptData = aesEncrypt(a)
          t.version = version
        """
        if not self.crypto:
            return data or {}
        self._refresh_crypto_if_needed()
        result = dict(data) if data else {}
        result["ts"] = int(time.time() * 1000)
        # 加密整个 payload（含业务参数+ts），与 JS 一致
        result["encryptData"] = self.crypto.encrypt(result)
        result["version"] = getattr(self, "_encrypt_version", 3)
        return result

    def _handle_response(self, body: dict, retry_fn) -> dict:
        """统一处理响应，遇到 5008 滑块验证自动过验证后重试"""
        code = body.get("code") or body.get("err")
        if code == 0:
            return body.get("data")
        if code == 5001:
            logger.error(f"5001 加密校验失败，完整响应: {body}")
            raise RuntimeError(f"[5001] {body.get('msg')} (加密校验失败，请检查 encrypt_key/iv 是否正确)")
        if code == 5008:
            if self.ocr is None:
                raise RuntimeError("触发滑块验证(5008)，请设置 ocr_server 以自动处理")
            self._solve_slide_validate()
            return retry_fn()
        raise RuntimeError(f"[{code}] {body.get('msg')}")

    def _solve_slide_validate(self):
        """自动完成滑块验证"""
        info = self._get_raw("/garden/slide_validate/getValidateInfo")
        # info 通常包含 bg_url（背景图）和 slide_url（滑块图）
        bg_url = info.get("bg_url") or info.get("bgUrl") or info.get("background")
        slide_url = info.get("slide_url") or info.get("slideUrl") or info.get("slider")

        bg_bytes = requests.get(bg_url, timeout=10).content
        slide_bytes = requests.get(slide_url, timeout=10).content

        result = self.ocr.slide_comparison(bg_bytes, slide_bytes)
        # ddddocr 返回 target 坐标或 x 偏移
        x = result.get("target", [0])[0] if "target" in result else result.get("x", 0)

        self._post_raw("/garden/slide_validate/toValidate", {"x": x, "y": 0})

    def _get_raw(self, path: str, params: dict = None) -> dict:
        """不做错误处理的原始 GET，供内部使用"""
        resp = self.session.get(BASE_URL + path, params=params, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        return body.get("data") or body

    def _post_raw(self, path: str, data: dict = None) -> dict:
        resp = self.session.post(BASE_URL + path, json=data or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: dict = None) -> dict:
        url = BASE_URL + path
        logger.debug(f"GET {url}  params={params}")
        resp = self.session.get(url, params=params, timeout=15)
        logger.debug(f"status={resp.status_code}  body={resp.text[:300]}")
        resp.raise_for_status()
        body = resp.json()
        return self._handle_response(body, lambda: self._get(path, params))

    def _post(self, path: str, data: dict = None) -> dict:
        url = BASE_URL + path
        logger.debug(f"POST {url}  data={data}")
        resp = self.session.post(url, json=data or {}, timeout=15)
        logger.debug(f"status={resp.status_code}  body={resp.text[:300]}")
        if not resp.ok:
            logger.error(f"POST {path} 失败 {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        body = resp.json()
        return self._handle_response(body, lambda: self._post(path, data))

    # ------------------------------------------------------------------ #
    # 认证
    # ------------------------------------------------------------------ #

    def login(self, code: str) -> dict:
        """微信登录，传入 wx.login() 返回的 code"""
        return self._get("/garden/wechat/login", {"code": code})

    def get_auth(self, params: dict) -> dict:
        return self._get("/garden/wechat/auth", params)

    def get_phone(self, params: dict) -> dict:
        """注册/绑定手机号"""
        return self._get("/garden/wechat/register", params)

    # ------------------------------------------------------------------ #
    # 用户信息
    # ------------------------------------------------------------------ #

    def member_info(self, params: dict = None) -> dict:
        """获取我的花园会员信息"""
        return self._get("/garden/Gardenmemberinfo/getMemberInfo", params)

    def improve_info(self, member_id: int) -> dict:
        return self._get(f"/member/member/info2", member_id)

    def tasks(self) -> dict:
        """获取任务列表"""
        return self._get("/garden/tasks/index")

    def get_subscribe_prize(self) -> dict:
        return self._get("/garden/tasks/getSubscribePrize")

    def subscribe_message_status(self, params: dict = None) -> dict:
        return self._get("/garden/gardenmemberinfo/subscribeMessageStatus", params)

    def subscribe_message(self, data: dict) -> dict:
        return self._post("/garden/gardenmemberinfo/subscribeMessage", data)

    # ------------------------------------------------------------------ #
    # 签到 & 分享（需要加密）
    # ------------------------------------------------------------------ #

    def daily_sign(self, data: dict = None) -> dict:
        """每日签到"""
        return self._post("/garden/sign/dailySign", self._encrypt_payload(data or {}))

    def daily_share(self) -> dict:
        """每日分享任务"""
        return self._post("/garden/gardenmemberinfo/dailyShare", self._encrypt_payload({}))

    # ------------------------------------------------------------------ #
    # 高粱地块
    # ------------------------------------------------------------------ #

    def get_sorghum_list(self, params: dict = None) -> list:
        """获取我的高粱地块列表"""
        return self._get("/garden/sorghum/index", params)

    def get_detail_by_member_id(self, data: dict) -> dict:
        return self._post("/garden/sorghum/getDetailByMemberId", data)

    def seeds(self, data: dict) -> dict:
        """播种"""
        return self._post("/garden/sorghum/seed", self._encrypt_payload(data))

    def watering(self, data: dict) -> dict:
        """浇水"""
        return self._post("/garden/sorghum/watering", self._encrypt_payload(data))

    def manuring(self, data: dict) -> dict:
        """施肥"""
        return self._post("/garden/sorghum/manuring", self._encrypt_payload(data))

    def harvest(self, data: dict) -> dict:
        """收获高粱"""
        return self._post("/garden/sorghum/harvest", self._encrypt_payload(data))

    def harvest_all(self) -> dict:
        """一键收获所有"""
        return self._get("/garden/Sorghum/harvestAll", self._encrypt_payload({}))

    def extend(self, data: dict) -> dict:
        """延长种植周期"""
        return self._post("/garden/sorghum/extend", self._encrypt_payload(data))

    def can_i_extend(self, params: dict = None) -> dict:
        return self._get("/garden/sorghum/can_i_extend", params)

    def help_record(self, params: dict = None) -> list:
        """好友助力施肥记录"""
        return self._get("/garden/sorghum/friendHelpManureRecord", params)

    # ------------------------------------------------------------------ #
    # 酿酒
    # ------------------------------------------------------------------ #

    def wine_list(self, params: dict = None) -> list:
        """获取我的酒列表"""
        return self._get("/garden/Gardenmemberwine/index", params)

    def discharge_grain(self, params: dict = None) -> dict:
        """投粮酿酒
        抓包确认：POST form-urlencoded，参数 volumn=高粱斤数，不需要加密
        接口：/garden/gardenmemberwine/makeWine
        每200斤高粱+1块酒曲酿40L，最多5000斤
        """
        volumn = (params or {}).get("sorghum_volumn") or (params or {}).get("volumn", 0)
        resp = self.session.post(
            BASE_URL + "/garden/gardenmemberwine/makeWine",
            data={"volumn": volumn},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        return self._handle_response(body, lambda: self.discharge_grain(params))

    def harvest_wine(self, params: dict = None) -> dict:
        """收获酒"""
        return self._get("/garden/Gardenmemberwine/harvestWine", params)

    def steal_wine(self, params: dict = None) -> dict:
        """偷别人的酒"""
        return self._get("/garden/Gardenmemberwine/stealWine", params)

    def steal_record(self, params: dict = None) -> list:
        """偷酒记录"""
        return self._get("/garden/Gardenmemberwine/stealRecord", params)

    def exchange(self, data: dict) -> dict:
        """用积分/酒兑换商品"""
        return self._get("/garden/Gardenjifenshop/exchange", self._encrypt_payload(data))

    def exchange_wine(self, wine_vol: int) -> dict:
        """酒兑换积分：wine=升数，1L=1积分（抓包确认参数名为 wine）"""
        payload = self._encrypt_payload({"wine": wine_vol})
        return self._get("/garden/Gardenjifenshop/exchange", payload)

    def jifenshop_index(self) -> dict:
        """积分商城首页"""
        return self._get("/garden/Gardenjifenshop/index")

    # ------------------------------------------------------------------ #
    # 制曲（100斤小麦 → 10块酒曲）
    # ------------------------------------------------------------------ #

    def make_yeast(self, data: dict) -> dict:
        """制曲：100斤小麦→10块酒曲，POST form-urlencoded，参数 volumn=小麦斤数（100的倍数，最多1000）"""
        volumn = (data or {}).get("volumn", 0)
        resp = self.session.post(
            BASE_URL + "/garden/wheat/makeWineYeast",
            data={"volumn": volumn},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        return self._handle_response(body, lambda: self.make_yeast(data))

    # ------------------------------------------------------------------ #
    # 抽奖
    # ------------------------------------------------------------------ #

    def lottery_read(self, params: dict = None) -> dict:
        """获取抽奖信息"""
        return self._get("/garden/lottery/read", params)

    def before_draw(self, params: dict = None) -> dict:
        """抽奖前检查"""
        return self._get("/garden/lottery/beforeDraw", params)

    def draw(self, data: dict = None) -> dict:
        """执行抽奖"""
        return self._post("/garden/lottery/draw", data)

    def lottery_record(self, params: dict = None) -> list:
        """抽奖记录"""
        return self._get("/garden/lottery/lotteryRecord", params)

    def remain_free_draw_chance(self, params: dict = None) -> dict:
        """剩余免费抽奖次数"""
        return self._get("/garden/lottery/remainFreeDrawChance", params)

    def lottery_amount(self) -> dict:
        return self._get("/garden/lottery/lotteryAmount")

    def lottery_record_roll(self) -> list:
        """抽奖滚动记录"""
        return self._get("/garden/Lottery/lotteryRoll")

    # ------------------------------------------------------------------ #
    # 好友 & 社交
    # ------------------------------------------------------------------ #

    def add_friend_token(self, params: dict = None) -> dict:
        return self._get("/garden/friends/addFriendToken", params)

    def add_friend(self, data: dict) -> dict:
        return self._post("/garden/friends/add", data)

    def friend_list(self, params: dict = None) -> list:
        return self._get("/garden/friends/index", params)

    # ------------------------------------------------------------------ #
    # 排行榜 & 公告
    # ------------------------------------------------------------------ #

    def rank(self, params: dict = None) -> list:
        """省级排行榜"""
        return self._get("/garden/Gardenmemberinfo/getGardenWineProvinceRank", params)

    def notice(self) -> list:
        return self._get("/garden/notice/index")

    def real_scene(self) -> dict:
        return self._get("/garden/notice/realScene")

    def hot_activity(self, activity_id: int = None) -> dict:
        path = f"/garden/Notice/hotActivity?id={activity_id}" if activity_id else "/garden/Notice/hotActivity"
        return self._get(path)

    def introduction(self) -> dict:
        return self._get("/garden/Notice/introduction", {"id": 1, "name": "1", "content": "1"})

    def poptips(self, params: dict = None) -> dict:
        return self._get("/garden/poptips/random", params)

    def get_user_pianqu(self, province: str, city: str, district: str) -> dict:
        path = f"/garden/notice/getPianquActivity?province={province}&city={city}&district={district}"
        return self._post(path, {"province": province, "city": city, "district": district})

    # ------------------------------------------------------------------ #
    # 答题任务
    # ------------------------------------------------------------------ #

    def get_question_task(self) -> dict:
        return self._get("/garden/Gardenquestiontask/index")

    def answer_results(self, question_id: int, selected: str) -> dict:
        """提交答题结果
        抓包确认：GET 请求，answer 参数为 JSON 数组字符串
        ?answer=[{"itemid":27,"selected":"ABCD"}]&ts=...&encryptData=...&version=4
        """
        import json as _json
        answer_str = _json.dumps([{"itemid": question_id, "selected": selected}],
                                 separators=(",", ":"))
        enc = self._encrypt_payload({})  # 只加密空对象，得到 ts/encryptData/version
        params = {"answer": answer_str}
        params.update(enc)
        url = BASE_URL + "/garden/Gardenquestiontask/answerResults"
        resp = self.session.get(url, params=params, timeout=15)
        logger.debug("answerResults GET status=%s  body=%s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        body = resp.json()
        return self._handle_response(body, lambda: self.answer_results(question_id, selected))

    def git_questiontask(self) -> dict:
        return self._get("/wcrd/question/get")

    def answer_question(self, data: dict) -> dict:
        return self._post("/wcrd/question/answer", data)

    def is_share_frend(self) -> dict:
        return self._get("/wcrd/question/wcrdMemberInfo")

    def share_frends(self) -> dict:
        return self._get("/wcrd/question/share")

    def receive_inte(self) -> dict:
        return self._get("/wcrd/question/getPrize")

    def agree_rule(self) -> dict:
        return self._get("/wcrd/question/agree")

    # ------------------------------------------------------------------ #
    # 滑块验证
    # ------------------------------------------------------------------ #

    def get_validate_info(self) -> dict:
        return self._get("/garden/slide_validate/getValidateInfo")

    def to_validate(self, data: dict) -> dict:
        return self._post("/garden/slide_validate/toValidate", data)

    # ------------------------------------------------------------------ #
    # 商品 & 奖励
    # ------------------------------------------------------------------ #

    def goods(self) -> list:
        return self._get("/goods", {"tags": "garden_recommend_product"})

    def reward(self) -> dict:
        return self._get("/garden/realscene/reward")

    def banners(self, banner_type: str = None) -> list:
        path = f"/banners?type={banner_type}" if banner_type else "/banners"
        return self._get(path)
