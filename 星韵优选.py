
import requests
import json
import time
import random
import datetime
import re
import os
try:
    from getCode import WeChatCodeGetter
except ModuleNotFoundError:
    WeChatCodeGetter = None

APP_ID = os.environ.get("XYYX_APPID", "wxc86c9aecdb67f876")
REFERER_VERSION = os.environ.get("XYYX_REFERER_VERSION", "12")
DEFAULT_REFERER = f"https://servicewechat.com/{APP_ID}/{REFERER_VERSION}/page-frame.html"
WITHDRAW_TX_TYPE = os.environ.get("XYYX_WITHDRAW_TYPE", "jifen")  # 支持: jifen(积分) / yongj(佣金) / hongb(红包)

def env_bool(name, default=True):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")

def env_number(name, default, minimum=None):
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(value)
        if minimum is not None:
            number = max(minimum, number)
        return number
    except Exception:
        return default

def to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default

def is_truthy_status(value):
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "2", "true", "success")

class BuiltinYybCodeGetter:
    protocol_type = "yyb呆呆"

    def __init__(self):
        self.wechat_server = (os.environ.get("WECHAT_SERVER") or "").strip()
        self.wx_id_filter = os.environ.get("WX_ID")
        if not self.wechat_server:
            raise ValueError("环境变量 WECHAT_SERVER 未设置")
        if not self.wechat_server.lower().startswith(("http://", "https://")):
            self.wechat_server = f"http://{self.wechat_server}"
        self.target_wx_ids = []
        if self.wx_id_filter:
            self.target_wx_ids = [x.strip() for x in self.wx_id_filter.split("&") if x.strip()]

    def _filter_accounts(self, accounts):
        if not self.target_wx_ids:
            return accounts
        targets = set(self.target_wx_ids)
        return [
            account for account in accounts
            if (account.get("wxid") or account.get("wx_id") or account.get("account")) in targets
        ]

    def get_online_accounts(self):
        url = self.wechat_server.rstrip("/") + "/api/accounts"
        headers = {}
        auth = os.environ.get("LICENSE_KEY") or os.environ.get("AUTH") or os.environ.get("ADMIN_KEY") or ""
        if auth:
            headers["X-License-Key"] = auth
            headers["Authorization"] = f"Bearer {auth}"
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        result = response.json()
        data = result.get("accounts") or result.get("data") or result.get("Data") or result
        accounts = []

        if isinstance(data, dict):
            values = data.values()
        elif isinstance(data, list):
            values = data
        else:
            values = []

        for item in values:
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            online = (
                status in (None, "", "active", 1, "1")
                or item.get("survival") == 1
                or item.get("online") is True
                or item.get("Success") is True
            )
            if not online:
                continue
            wxid = item.get("openid") or item.get("wxid") or item.get("wx_id") or item.get("account") or item.get("license")
            if not wxid:
                continue
            item["openid"] = wxid
            item["wxid"] = wxid
            item["license"] = auth or wxid
            item["authKey"] = auth or wxid
            accounts.append(item)

        accounts = self._filter_accounts(accounts)
        return [(account, {"loginState": 1}) for account in accounts]

    def get_applet_code(self, app_id, wxid):
        from getCode import make_getter
        return make_getter(self.wechat_server).get_applet_code(app_id, wxid)

def generate_bound_ua(token):
    rd = random.Random(token) 
    os_type = rd.choice(["Android", "iOS"])
    if os_type == "Android":
        android_ver = rd.choice(["10", "11", "12", "13", "14"])
        chrome_ver = f"{rd.randint(86, 120)}.0.{rd.randint(4000, 6000)}.{rd.randint(100, 200)}"
        phone_model = rd.choice(["SM-G9810", "V2055A", "M2012K11AC", "PADT00", "KB2000", "MI 10"])
        return (f"Mozilla/5.0 (Linux; Android {android_ver}; {phone_model} Build/QP1A.190711.020; wv) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/{chrome_ver} "
                f"MicroMessenger/8.0.45.2400(0x28002B3D) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64")
    else:
        ios_ver = rd.choice(["15_0", "16_2", "17_1"])
        return (f"Mozilla/5.0 (iPhone; CPU iPhone OS {ios_ver} like Mac OS X) "
                f"AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
                f"MicroMessenger/8.0.46(0x18002e2f) NetType/WIFI Language/zh_CN")

class GzPengRu:
    def __init__(self, token="", index=1, wxid="", account_name=""):
        self.token = token
        self.index = index
        self.wxid = wxid
        self.account_name = account_name or wxid or f"account_{index}"
        self.ua = generate_bound_ua(token or self.account_name)
        self.headers = {
            "Host": "gzpengru.weimbo.com",
            "Connection": "keep-alive",
            "3rdSession": self.token,
            "content-type": "application/json",
            "User-Agent": self.ua,
            "Referer": DEFAULT_REFERER
        }
        self.base_url = "https://gzpengru.weimbo.com/api/index.php?ackey=GZYTAPPLET"
        self.next_run_time = 0
        self.is_sign_completed = False
        self.is_video_completed = False
        self.is_all_done = False
        self.video_remaining = 0

    def log(self, content):
        time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{time_str}] [账号{self.index} {self.account_name}] {content}")

    def set_token(self, token):
        self.token = token or ""
        self.headers["3rdSession"] = self.token

    def post_request(self, payload):
        try:
            time.sleep(random.uniform(0.5, 1.5))
            response = requests.post(self.base_url, headers=self.headers, json=payload, timeout=10)
            return response.json()
        except Exception as e:
            self.log(f"请求异常: {e}")
            return None

    def login_with_code(self, code):
        data = self.post_request({"action": "WxLogin", "code": code})
        if not isinstance(data, dict) or not data.get("Status"):
            msg = data.get("Data", data.get("Message", data)) if isinstance(data, dict) else data
            self.log(f"code登录失败: {msg}")
            return False
        session = (data.get("Data") or {}).get("r3dkey")
        if not session:
            self.log(f"code登录失败: 响应缺少 Data.r3dkey: {data}")
            return False
        self.set_token(session)
        self.log("code登录成功，已获取3rdSession")
        return True

    def get_alert_info(self):
        """源码: getAlertInfo — 提现前置检查(是否需要完善信息等)"""
        payload = {"action": "getAlertInfo", "act_name": "tixian", "act_val": WITHDRAW_TX_TYPE}
        data = self.post_request(payload)
        if data and data.get("Status"):
            alert_info = data.get("Data", {})
            if alert_info:
                self.log(f"提现前置信息: {alert_info}")
            return alert_info
        return {}

    def get_withdrawal_info(self):
        """源码: withdrawalInfo — 获取提现信息(余额/限额/通道/商户转账状态)"""
        payload = {"action": "withdrawalInfo", "tx_ty": WITHDRAW_TX_TYPE}
        data = self.post_request(payload)
        if not data or not data.get("Status") or not isinstance(data.get("Data"), dict):
            msg = data.get("Message", data) if isinstance(data, dict) else data
            self.log(f"提现信息获取失败: {msg}")
            return None

        info = data.get("Data", {})
        now_yj = to_float(info.get("now_yj", info.get("cmoney", 0)))
        min_money = to_float(info.get("min_money", 0))
        max_money = to_float(info.get("max_money", 0))
        web_tidao = info.get("web_tidao", [0, 0, 0])
        web_tidao_in = info.get("web_tidao_in", -1)
        status = info.get("status", False)

        channels = []
        if isinstance(web_tidao, list):
            ch_names = ["微信零钱", "收款码", "微信转账"]
            for i, v in enumerate(web_tidao):
                if v and i < len(ch_names):
                    channels.append(ch_names[i])

        mch_id = info.get("mchId", "")
        if mch_id:
            state_text = "商户转账待确认"
        else:
            state_text = "可提" if status else "未开放"

        self.log(f"提现监控: 可提 {now_yj:.2f} 元 | 限额 {min_money:.2f}~{max_money:.2f} 元 | 通道 {web_tidao_in}({'/'.join(channels) or '未知'}) | 状态 {state_text}")
        return info

    def is_transfer_success(self, info):
        transfer_result = info.get("transfer_result") or {}
        return str(transfer_result.get("state", "")).upper() == "SUCCESS"

    def get_transfer_package(self, data):
        if not isinstance(data, dict):
            return ""
        return (
            data.get("package")
            or data.get("payPackage")
            or data.get("packageInfo")
            or data.get("pkg")
            or ""
        )

    def confirm_merchant_transfer(self, transfer_data):
        if not isinstance(transfer_data, dict):
            self.log(f"商家确认收款失败: 无效转账数据 {transfer_data}")
            return False

        mch_id = transfer_data.get("mchId") or transfer_data.get("mchid") or transfer_data.get("payMchId")
        pay_package = self.get_transfer_package(transfer_data)
        if not mch_id or not pay_package:
            self.log(f"商家确认收款失败: 缺少mchId/package {transfer_data}")
            return False

        wechat_server = (os.environ.get("WECHAT_SERVER") or "").strip()
        if not wechat_server:
            self.log("商家确认收款失败: 未配置WECHAT_SERVER")
            return False
        if not wechat_server.lower().startswith(("http://", "https://")):
            wechat_server = f"http://{wechat_server}"

        body = {
            "appid": APP_ID,
            "wxid": self.wxid or self.account_name,
            "mchId": mch_id,
            "payPackage": pay_package
        }
        url = wechat_server.rstrip("/") + "/api/yyb/pay/merchant-transfer/confirm"

        try:
            response = requests.post(url, json=body, timeout=90)
            try:
                result = response.json()
            except Exception:
                result = {"status_code": response.status_code, "text": response.text}
        except Exception as e:
            self.log(f"商家确认收款请求异常: {e}")
            return False

        data = result.get("Data") if isinstance(result, dict) else {}
        ok = False
        if isinstance(result, dict):
            ok = (
                result.get("Success") is True
                or result.get("status") is True
                or result.get("ok") is True
                or result.get("Code") == 0
                or (isinstance(data, dict) and data.get("ok") is True)
            )

        if ok:
            self.log(f"商家确认收款成功: mchId={mch_id}")
            return True

        msg = result.get("Message", result) if isinstance(result, dict) else result
        self.log(f"商家确认收款失败: {msg}")
        return False

    def is_withdraw_ready(self, info, min_amount=0):
        """源码: formcheck — 客户端校验逻辑复现"""
        now_yj = to_float(info.get("now_yj", info.get("cmoney", 0)))
        min_money = max(to_float(info.get("min_money", 0)), to_float(min_amount, 0))
        max_money = to_float(info.get("max_money", 0))
        status = info.get("status", False)

        if not status:
            return False
        if now_yj <= 0 or now_yj + 1e-9 < min_money:
            return False
        if max_money > 0 and now_yj > max_money:
            pass  # 超出单笔限额时仍可提(取max_money)
        return True

    def calc_withdraw_amount(self, info):
        """根据源码 applyAll/formcheck 计算实际提现金额"""
        now_yj = to_float(info.get("now_yj", info.get("cmoney", 0)))
        max_money = to_float(info.get("max_money", 0))
        if max_money > 0 and now_yj > max_money:
            return max_money
        return now_yj

    def apply_withdrawal(self, info):
        """源码: formSubmit → addWithdrawalApply"""
        cmoney = self.calc_withdraw_amount(info)
        img_list = info.get("img_list")
        if not isinstance(img_list, list):
            img_list = ["", ""]

        web_tidao_in = info.get("web_tidao_in", -1)
        web_tidao = info.get("web_tidao", [0, 0, 0])
        if web_tidao_in == -1 and isinstance(web_tidao, list):
            for i, v in enumerate(web_tidao):
                if v and i != 1:  # 跳过收款码通道(需要图片)
                    web_tidao_in = i
                    break
            if web_tidao_in == -1:
                for i, v in enumerate(web_tidao):
                    if v:
                        web_tidao_in = i
                        break

        payload = {
            "action": "addWithdrawalApply",
            "uimg": img_list,
            "cmoney": cmoney,
            "web_tidao_in": web_tidao_in,
            "tx_ty": WITHDRAW_TX_TYPE
        }
        data = self.post_request(payload)
        if not isinstance(data, dict):
            self.log(f"提现提交失败: {data}")
            return False

        status = data.get("Status")
        msg = data.get("Data", data.get("Message", data))

        if status == 1 or status is True:
            self.log(f"提现成功: {msg}")
            return True
        elif status == 2:
            mch_id = msg.get("mchId", "") if isinstance(msg, dict) else ""
            if mch_id:
                self.log(f"提现已发起(商户转账模式): mchId={mch_id}，开始调用yyb确认收款")
                return self.confirm_merchant_transfer(msg)
            else:
                self.log(f"提现状态2: {msg}")
            return True

        err_msg = msg
        if isinstance(msg, dict):
            err_msg = msg.get("return_msg", msg)
        if "转账已发起" in str(err_msg):
            self.log(f"提现已发起: {err_msg}")
            return True

        self.log(f"提现提交失败: {err_msg}")
        return False

    def get_withdrawal_log(self, tag=0, page=0):
        """源码: withdrawalLogList — 提现记录查询"""
        payload = {
            "action": "withdrawalLogList",
            "tag": tag,
            "tx_ty": WITHDRAW_TX_TYPE,
            "page": page
        }
        data = self.post_request(payload)
        if data and data.get("Status"):
            log_data = data.get("Data", {})
            logarr = log_data.get("logarr", [])
            if logarr == "no_data" or not logarr:
                self.log("提现记录: 暂无记录")
                return []
            for item in logarr[:3]:
                amount = item.get("amoney", "?")
                memo = item.get("amemo", "")
                status_text = item.get("astatus_text", item.get("astatus", ""))
                create_time = item.get("acreatetime", "")
                self.log(f"  提现记录: {amount}元 | {status_text} | {memo} | {create_time}")
            return logarr
        return []

    def get_user_info(self):
        payload = {"action": "userInfoData"}
        data = self.post_request(payload)
        if data and data.get("Status"):
            user_data = data.get("Data", {})
            user_name = user_data.get("user", {}).get("name", "未知")
            jifen = user_data.get("u_money", {}).get("jifen", 0)
            self.log(f"用户: {user_name} | 当前积分: {jifen}")
            return True
        else:
            self.log("Token失效")
            return False

    def check_task_progress(self):
        payload = {"action": "getIntegralInfo", "type": "jifen"}
        data = self.post_request(payload)
        
        sign_str = "未知"
        video_str = "0/3"
        
        if data and data.get("Status"):
            adv_arr = data.get("Data", {}).get("adv_arr", [])
            for task in adv_arr:
                title = task.get("title", "")
                if task.get("id") == 2:
                    match = re.search(r'\((\d+)/(\d+)\)', title)
                    if match:
                        curr, total = int(match.group(1)), int(match.group(2))
                        sign_str = f"{curr}/{total}"
                        if curr >= total:
                            self.is_sign_completed = True
                        else:
                            self.is_sign_completed = False
                
                elif task.get("id") == 3:
                    match = re.search(r'\((\d+)/(\d+)\)', title)
                    if match:
                        curr, total = int(match.group(1)), int(match.group(2))
                        video_str = f"{curr}/{total}"
                        self.video_remaining = max(0, total - curr)
                        if curr >= total:
                            self.is_video_completed = True
                        else:
                            self.is_video_completed = False

            if self.is_sign_completed and self.is_video_completed:
                self.log(f"🎉 今日所有任务已完成 (打卡:{sign_str} 视频:{video_str})")
                self.is_all_done = True
            else:
                self.log(f"📊 当前进度: 打卡[{sign_str}] 视频[{video_str}]")
            
            return True
        return False

    def execute_video_ad_task(self):
        if self.is_video_completed:
            self.log("🎬 视频任务: 今日已全部完成，跳过")
            return

        times = self.video_remaining if self.video_remaining > 0 else 2
        for i in range(times):
            payload_ad = {"action": "IntegralGiveReward"}
            res = self.post_request(payload_ad)

            if res and res.get("Status"):
                msg = res.get("Data", "")
                self.log(f"🎬 视频任务: ✅ {msg}")
            else:
                msg = res.get("Message", "未知错误") if res else "请求失败"
                self.log(f"🎬 视频任务: 已达上限或失败({msg})")
                self.is_video_completed = True
                break

    def process_cycle(self):
        if not self.check_task_progress():
            return 60

        if self.is_all_done:
            return -1

        if not self.is_video_completed:
            self.execute_video_ad_task()
            self.check_task_progress()

        if self.is_all_done:
            return -1

        if self.is_sign_completed:
            if self.is_video_completed:
                return -1
            else:
                return 60

        payload_status = {"action": "getIntegralInfo", "type": "sign"}
        data_status = self.post_request(payload_status)
        
        wait_seconds = 60 

        if data_status and data_status.get("Status"):
            status_data = data_status.get("Data", {})
            sign_time = status_data.get("sign_time", 0) 
            qiands = status_data.get("qiands", "未知")
            
            if sign_time > 0:
                self.log(f"📍 打卡状态: {qiands} | 冷却中: {sign_time}秒")
                wait_seconds = sign_time + 5 
            else:
                self.log("📍 冷却归零，执行打卡...")
                payload_sign = {"action": "userQiandao"}
                data_sign = self.post_request(payload_sign)
                
                if data_sign and data_sign.get("Status"):
                    res = data_sign.get("Data", {})
                    add_jf = res.get("add_jf", 0)
                    new_jf = res.get("user_jf", 0)
                    self.log(f"✅ 打卡成功! +{add_jf}分 | 总分: {new_jf}")
                    return 1 
                else:
                    msg = data_sign.get("Message", "未知") if data_sign else "无响应"
                    self.log(f"❌ 打卡失败: {msg}")
                    wait_seconds = 60 
        
        return wait_seconds

    def check_and_run(self):
        now = time.time()
        if now >= self.next_run_time:
            if self.get_user_info():
                wait_s = self.process_cycle()
                
                if wait_s == -1:
                    self.log("🏆 该账号今日任务全部完成，停止运行。")
                    return True
                
                self.next_run_time = now + wait_s
                next_str = datetime.datetime.fromtimestamp(self.next_run_time).strftime('%H:%M:%S')
                self.log(f"本轮结束，下次运行: {next_str}")
            else:
                self.next_run_time = now + 3600 
                self.log("账号Token异常，暂停1小时")
        return False

def monitor_withdrawals(apps):
    """
    提现监控 — 基于反编译源码 withdrawal.js 完整流程:
    1. getAlertInfo: 前置检查(是否需完善资料)
    2. withdrawalInfo: 获取余额/限额/通道/商户转账状态
    3. formcheck: 客户端校验(金额/通道/图片)
    4. addWithdrawalApply: 提交提现
    5. Status==2 时为商户转账模式(wx.requestMerchantTransfer), 调用yyb确认接口完成收款
    6. withdrawalLogList: 提现记录查询
    """
    if not env_bool("XYYX_WITHDRAW_ENABLE", True):
        print("提现监控已关闭: XYYX_WITHDRAW_ENABLE=0")
        return

    pending = {app.index: app for app in apps}
    if not pending:
        return

    interval = env_number("XYYX_WITHDRAW_INTERVAL_SEC", 60, 5)
    max_wait = env_number("XYYX_WITHDRAW_MAX_WAIT_SEC", 30, 0)
    min_amount = env_number("XYYX_WITHDRAW_MIN_AMOUNT", 1, 0)
    require_all = env_bool("XYYX_WITHDRAW_REQUIRE_ALL", True)
    show_log = env_bool("XYYX_WITHDRAW_SHOW_LOG", False)
    start_time = time.time()

    print("\n" + "=" * 40)
    print(f"进入提现监控，共 {len(pending)} 个账号")
    print(f"提现类型: {WITHDRAW_TX_TYPE} | 监控间隔: {int(interval)}秒 | 最大监控: {int(max_wait)}秒")
    print(f"全部可提后提交: {require_all} | 最低金额: {min_amount}")
    print("=" * 40)

    for index, app in list(pending.items()):
        alert_info = app.get_alert_info()
        if alert_info and isinstance(alert_info, dict) and alert_info.get("alert_info"):
            app.log(f"前置检查: 需要完善资料才能提现，请在小程序中操作")
            pending.pop(index, None)

    if show_log:
        for app in apps:
            app.get_withdrawal_log()

    while pending:
        ready = []
        waiting = []

        for index, app in list(pending.items()):
            info = app.get_withdrawal_info()
            if not info:
                waiting.append(app)
                continue

            if app.is_transfer_success(info):
                app.log("提现已到账，本账号监控完成")
                pending.pop(index, None)
                continue

            if info.get("mchId"):
                app.log("商户转账待确认，开始调用yyb确认收款")
                if app.confirm_merchant_transfer(info):
                    pending.pop(index, None)
                else:
                    waiting.append(app)
                continue

            if app.is_withdraw_ready(info, min_amount):
                ready.append((index, app, info))
            else:
                now_yj = to_float(info.get("now_yj", info.get("cmoney", 0)))
                min_money = to_float(info.get("min_money", 0))
                if not info.get("status"):
                    app.log("提现通道未开放，跳过")
                    pending.pop(index, None)
                else:
                    waiting.append(app)

        if ready and (not require_all or not waiting):
            for index, app, info in ready:
                if app.apply_withdrawal(info):
                    pending.pop(index, None)
        elif ready and waiting:
            print(f"已有 {len(ready)} 个账号可提，仍有 {len(waiting)} 个账号未可提，继续监控。")

        if not pending:
            print("\n" + "=" * 40)
            print("所有账号提现监控完成，脚本退出。")
            print("=" * 40)
            return

        elapsed = time.time() - start_time
        if max_wait > 0 and elapsed >= max_wait:
            print(f"提现监控达到最大等待时间，剩余 {len(pending)} 个账号未完成。")
            return

        sleep_time = interval
        if max_wait > 0:
            sleep_time = min(interval, max(1, max_wait - elapsed))
        print(f"--- 提现监控待机: 剩余 {len(pending)} 个账号，{int(sleep_time)}秒后重试 ---")
        time.sleep(max(1, sleep_time))

def withdraw_once(apps):
    if not apps:
        return

    min_amount = env_number("XYYX_WITHDRAW_MIN_AMOUNT", 1, 0)
    print("\n" + "=" * 40)
    print(f"直接提现一次，共 {len(apps)} 个账号 | 类型: {WITHDRAW_TX_TYPE} | 最低金额: {min_amount}")
    print("=" * 40)

    for app in apps:
        alert_info = app.get_alert_info()
        if alert_info and isinstance(alert_info, dict) and alert_info.get("alert_info"):
            app.log("前置检查未通过: 需要先在小程序完善资料")
            continue

        info = app.get_withdrawal_info()
        if not info:
            continue

        if app.is_transfer_success(info):
            app.log("提现已到账，跳过")
            continue

        if info.get("mchId"):
            app.log("已有商户转账待确认，开始调用yyb确认收款")
            app.confirm_merchant_transfer(info)
            continue

        if not app.is_withdraw_ready(info, min_amount):
            now_yj = to_float(info.get("now_yj", info.get("cmoney", 0)))
            min_money = max(to_float(info.get("min_money", 0)), to_float(min_amount, 0))
            app.log(f"暂不可提现: 可提={now_yj:.2f}, 最低={min_money:.2f}, status={info.get('status')}")
            continue

        app.apply_withdrawal(info)

def get_account_name(account, index):
    for key in ("nickname", "nick_name", "nickName", "account", "display_name", "deviceName"):
        value = account.get(key)
        if value:
            return str(value)
    wxid = account.get("wxid") or account.get("wx_id") or account.get("license") or account.get("authKey")
    if wxid:
        return str(wxid)
    return f"账号{index}"

def get_account_code_key(getter, account):
    if getter.protocol_type in ("yyb呆呆", "Niuzi"):
        return (
            account.get("wxid")
            or account.get("wx_id")
            or account.get("license")
            or account.get("authKey")
        )
    return account.get("license") or account.get("authKey")

def build_apps_from_codes():
    try:
        getter_cls = WeChatCodeGetter or BuiltinYybCodeGetter
        getter = getter_cls()
        online_accounts = getter.get_online_accounts()
    except Exception as e:
        print(f"初始化getCode失败: {e}")
        return []

    if not online_accounts:
        print("getCode未获取到在线账号")
        return []

    apps = []
    print(f"开始通过getCode获取星韵优选code: appid={APP_ID}")
    for raw_index, (account, _) in enumerate(online_accounts, 1):
        account_name = get_account_name(account, raw_index)
        code_key = get_account_code_key(getter, account)
        wxid = (
            account.get("wxid")
            or account.get("wx_id")
            or account.get("license")
            or account.get("authKey")
            or code_key
            or account_name
        )
        if not code_key:
            print(f"[账号{raw_index} {account_name}] 缺少wxid/license，跳过")
            continue

        try:
            code = getter.get_applet_code(APP_ID, code_key)
        except Exception as e:
            print(f"[账号{raw_index} {account_name}] 获取code失败: {e}")
            continue

        app = GzPengRu(index=len(apps) + 1, wxid=str(wxid), account_name=account_name)
        if app.login_with_code(code):
            apps.append(app)

    return apps

def main():
    print("-" * 30)
    print("-" * 30)
    print("=== 星韵优选脚本启动 ===")

    apps = build_apps_from_codes()
    all_apps = apps[:]

    if not apps:
        print("未获取到可用账号，脚本退出")
        return

    if env_bool("XYYX_WITHDRAW_ONCE", True):
        withdraw_once(all_apps)
        return

    task_completed = False
    while True:
        try:
            now = time.time()
            min_next_run = float('inf')
            active_apps = []

            for app in apps:
                is_finished = False
                if now >= app.next_run_time:
                    is_finished = app.check_and_run()
                
                if not is_finished:
                    active_apps.append(app)
                    if app.next_run_time < min_next_run:
                        min_next_run = app.next_run_time
            
            apps = active_apps

            if not apps:
                print("\n" + "="*40)
                print("🎉 所有账号今日任务均已完成，准备进入提现监控。")
                print("="*40)
                task_completed = True
                break

            sleep_time = min_next_run - time.time()
            if sleep_time < 0: 
                sleep_time = 0
            
            if sleep_time > 10:
                print(f"--- 系统待机: 等待 {int(sleep_time)} 秒 ---")
            
            time.sleep(max(1, sleep_time))
            
        except KeyboardInterrupt:
            print("\n用户手动停止脚本")
            break
        except Exception as e:
            print(f"主循环错误: {e}")
            time.sleep(30)

    if task_completed:
        monitor_withdrawals(all_apps)

if __name__ == "__main__":
    main()
