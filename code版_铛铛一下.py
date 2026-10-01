import os
import random
import requests
import time
import traceback
from datetime import datetime
from typing import List, Optional, Tuple

from getCode import get_wechat_codes

DEFAULT_WITHDRAW_BALANCE = 0.3
MULTI_ACCOUNT_SPLIT = ["\n", "@"]
MULTI_ACCOUNT_PROXY = False
NOTIFY = os.getenv("LY_NOTIFY") or False


class AutoTask:
    def __init__(self, script_name: str):
        self.script_name = script_name
        self.wx_appid = "wxe378d2d7636c180e"
        self.proxy_url = os.getenv("PROXY_API_URL")
        self.host = "vues.dd1x.cn"
        self.user_agent = (
            "Mozilla/5.0 (Linux; Android 12; M2012K11AC Build/SKQ1.220303.001; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/134.0.6998.136 "
            "Mobile Safari/537.36 XWEB/1340129 MMWEBSDK/20240301 MMWEBID/9871 "
            "MicroMessenger/8.0.48.2580(0x28003036) WeChat/arm64 Weixin NetType/WIFI "
            "Language/zh_CN ABI/arm64 MiniProgramEnv/android"
        )
        self.log_msgs: List[str] = []

    def log(self, msg: str, level: str = "info") -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{now}] [{level.upper()}] {msg}"
        self.log_msgs.append(line)
        print(line)

    def _parse_target_wx_ids(self) -> List[str]:
        soy_wxid_data = (os.getenv("soy_wxid_data") or "").strip()
        if not soy_wxid_data:
            return []

        split_char = None
        for sep in MULTI_ACCOUNT_SPLIT:
            if sep in soy_wxid_data:
                split_char = sep
                break

        raw_items = [soy_wxid_data] if not split_char else soy_wxid_data.split(split_char)

        wx_ids: List[str] = []
        for item in raw_items:
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                item = item.split("=", 1)[1].strip()
            if item:
                wx_ids.append(item)
        return wx_ids

    def get_account_codes(self) -> List[Tuple[str, str]]:
        target_wx_ids = self._parse_target_wx_ids()
        original_wx_id = os.getenv("WX_ID")
        changed_wx_id = False

        try:
            if target_wx_ids:
                os.environ["WX_ID"] = "&".join(target_wx_ids)
                changed_wx_id = True
                self.log(f"检测到 soy_wxid_data，已转为 WX_ID 过滤: {os.environ['WX_ID']}")

            account_code_map = get_wechat_codes(self.wx_appid)
            if not account_code_map:
                self.log("未获取到可用账号 code", level="warning")
                return []
            return list(account_code_map.items())
        except Exception as e:
            self.log(f"获取 code 失败: {e}\n{traceback.format_exc()}", level="error")
            return []
        finally:
            if changed_wx_id:
                if original_wx_id is None:
                    os.environ.pop("WX_ID", None)
                else:
                    os.environ["WX_ID"] = original_wx_id

    def get_proxy(self) -> Optional[str]:
        if not self.proxy_url:
            self.log("[获取代理] 未设置 PROXY_API_URL，不使用代理", level="warning")
            return None

        response = requests.get(self.proxy_url)
        proxy = response.text.strip()
        self.log(f"[获取代理] {proxy}")
        return proxy

    def check_proxy(self, proxy: str, session: requests.Session) -> bool:
        try:
            url = f"http://{self.host}/api/v2/get_sign_list"
            session.headers["Token"] = ""
            response = session.get(url, timeout=5)
            if response.status_code == 200:
                self.log(f"[检查代理] {proxy} 可用")
                return True
            self.log(f"[检查代理] 不可用: {response.text}")
            return False
        except Exception:
            return False

    def wxlogin(self, session: requests.Session, code: str) -> bool:
        try:
            url = f"https://{self.host}/wechat/login"
            params = {"code": code, "channelId": 154}
            response = session.get(url, params=params)
            response.raise_for_status()
            response_json = response.json()
            if response_json["code"] == 0:
                tel = response_json["data"]["tel"]
                tel = tel[:3] + "****" + tel[-4:]
                self.log(f"[登录] 成功，当前账号 {tel}")
                session.headers["Token"] = response_json["data"]["token"]
                return True

            self.log(f"[登录] 失败: {response_json['msg']}", level="error")
            return False
        except requests.RequestException as e:
            self.log(f"[登录] 网络错误: {e}\n{traceback.format_exc()}", level="error")
            return False
        except Exception as e:
            self.log(f"[登录] 未知错误: {e}\n{traceback.format_exc()}", level="error")
            return False

    def sign_in(self, session: requests.Session) -> bool:
        try:
            url = f"https://{self.host}/api/v2/sign_join"
            response = session.get(url)
            response.raise_for_status()
            response_json = response.json()
            if response_json["code"] == 0:
                self.log("[签到] 成功")
                return True

            self.log(f"[签到] {response_json['msg']}", level="error")
            return False
        except Exception as e:
            self.log(f"[签到] 异常: {e}\n{traceback.format_exc()}", level="error")
            return False

    def add_lottery_count(self, session: requests.Session) -> bool:
        try:
            url = f"https://{self.host}/front/activity/add_lottery_count"
            response = session.get(url)
            response.raise_for_status()
            response_json = response.json()
            if response_json["code"] == 0:
                self.log("[增加抽奖次数] 成功")
                return True
            if "达到上限" in response_json["msg"]:
                self.log(f"[增加抽奖次数] {response_json['msg']}", level="warning")
                return False

            self.log(f"[增加抽奖次数] 失败: {response_json['msg']}", level="error")
            return False
        except Exception as e:
            self.log(f"[增加抽奖次数] 异常: {e}\n{traceback.format_exc()}", level="error")
            return False

    def update_lottery_result(self, session: requests.Session) -> bool:
        try:
            url = f"https://{self.host}/front/activity/update_lottery_result"
            response = session.get(url, params={"id": 3438615})
            response.raise_for_status()
            response_json = response.json()
            if response_json["code"] == 0:
                self.log(f"[抽奖] 获得 {response_json['data']['goodName']}")
                return True

            self.log(f"[抽奖] {response_json['msg']}", level="error")
            return False
        except Exception as e:
            self.log(f"[抽奖] 异常: {e}\n{traceback.format_exc()}", level="error")
            return False

    def get_withdrawal_trade_list(self, session: requests.Session) -> Optional[Tuple[float, list]]:
        try:
            url = f"https://{self.host}/api/h/get_withdrawal_trade_list"
            response = session.get(url)
            response.raise_for_status()
            response_json = response.json()
            if response_json["code"] == 0 and response_json.get("data"):
                balance = float(response_json["data"][0]["money"])
                self.log(f"[余额] {balance} 元")
                return balance, response_json["data"]

            self.log(f"[获取提现数据] 失败: {response_json.get('msg', '未知错误')}", level="error")
            return None
        except Exception as e:
            self.log(f"[获取提现数据] 异常: {e}\n{traceback.format_exc()}", level="error")
            return None

    def withdraw(self, session: requests.Session, balance: float, withdrawal_trade_list: list) -> bool:
        try:
            url = f"https://{self.host}/api/h/withdrawal"
            payload = {
                "totalMoney": balance,
                "type": 1,
                "withdrawalDetailPojoList": withdrawal_trade_list,
            }
            response = session.post(url, json=payload)
            response.raise_for_status()
            response_json = response.json()
            if response_json["code"] == 0:
                self.log(f"[提现] {response_json['msg']}")
                return True

            self.log(f"[提现] {response_json['msg']}", level="error")
            return False
        except Exception as e:
            self.log(f"[提现] 异常: {e}\n{traceback.format_exc()}", level="error")
            return False

    def run(self) -> None:
        try:
            self.log(f"《{self.script_name}》开始执行任务")
            account_codes = self.get_account_codes()
            if not account_codes:
                self.log("没有可执行账号，任务结束", level="warning")
                return

            for index, (account_name, code) in enumerate(account_codes, 1):
                self.log("")
                self.log(f"------ [账号{index}] {account_name} 开始执行任务 ------")

                if MULTI_ACCOUNT_PROXY:
                    proxy = self.get_proxy()
                    if proxy:
                        session = requests.Session()
                        session.proxies.update({"http": f"http://{proxy}", "https": f"http://{proxy}"})
                        while not self.check_proxy(proxy, session):
                            proxy = self.get_proxy()
                            if not proxy:
                                break
                            session.proxies.update({"http": f"http://{proxy}", "https": f"http://{proxy}"})
                    else:
                        session = requests.Session()
                else:
                    session = requests.Session()

                session.headers["User-Agent"] = self.user_agent

                if code and self.wxlogin(session, code):
                    time.sleep(random.randint(1, 3))
                    self.sign_in(session)
                    time.sleep(random.randint(1, 3))

                    update_lottery_result_result = self.update_lottery_result(session)
                    while update_lottery_result_result:
                        time.sleep(random.randint(3, 5))
                        update_lottery_result_result = self.update_lottery_result(session)

                    add_lottery_count_result = self.add_lottery_count(session)
                    while add_lottery_count_result:
                        self.update_lottery_result(session)
                        time.sleep(random.randint(3, 5))
                        add_lottery_count_result = self.add_lottery_count(session)

                    withdrawal_data = self.get_withdrawal_trade_list(session)
                    if withdrawal_data:
                        balance, withdrawal_trade_list = withdrawal_data
                        if balance >= DEFAULT_WITHDRAW_BALANCE:
                            self.withdraw(session, balance, withdrawal_trade_list)
                            time.sleep(random.randint(1, 3))
                        else:
                            self.log(
                                f"[提现] 余额不足 {DEFAULT_WITHDRAW_BALANCE} 元，不执行提现",
                                level="warning",
                            )
                            time.sleep(random.randint(1, 3))

                self.log(f"------ [账号{index}] {account_name} 执行任务完成 ------")
        except Exception as e:
            self.log(f"《{self.script_name}》执行异常: {e}\n{traceback.format_exc()}", level="error")
        finally:
            if NOTIFY:
                if not os.path.exists("notify.py"):
                    url = "https://raw.githubusercontent.com/whyour/qinglong/refs/heads/develop/sample/notify.py"
                    response = requests.get(url)
                    with open("notify.py", "w", encoding="utf-8") as f:
                        f.write(response.text)
                    import notify  # type: ignore
                else:
                    import notify  # type: ignore

                title = f"{self.script_name} 运行日志"
                header = "作者：临上\n\n"
                content = header + "\n".join(self.log_msgs)
                notify.send(title, content)


if __name__ == "__main__":
    auto_task = AutoTask("铛铛一下")
    auto_task.run()
