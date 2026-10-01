import os
import random
import requests
import time
import traceback
from datetime import datetime
from typing import List, Tuple

from getCode import get_wechat_codes

MULTI_ACCOUNT_SPLIT = ["\n", "@"]
NOTIFY = os.getenv("LY_NOTIFY") or False


class AutoTask:
    def __init__(self, script_name: str):
        self.script_name = script_name
        self.wx_appid = "wx501990400906c9ff"
        self.host = "openapp.fmy90.com"
        self.user_phone = ""
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 "
            "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
            "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090b13) XWEB/9129"
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

    def code_login(self, session: requests.Session, code: str) -> bool:
        try:
            url = f"https://{self.host}/auth/wx/login"
            payload = {
                "code": code,
                "platformKey": "F2EE24892FBF66F0AFF8C0EB532A9394",
                "version": "V2.00.01",
                "vital": "",
                "partner_platform_key": "",
            }
            response = session.post(url, data=payload)
            response.raise_for_status()
            response_json = response.json()
            token = response_json.get("data", {}).get("token", "")
            if token:
                session.headers["authorization"] = f"bearer {token}"
                return True

            self.log(f"[登录] 失败: {response_json.get('message', '未知错误')}", level="error")
            return False
        except requests.RequestException as e:
            self.log(f"[登录] 网络错误: {e}\n{traceback.format_exc()}", level="error")
            return False

    def sign_in(self, session: requests.Session) -> bool:
        try:
            url = f"https://{self.host}/sign/new/do"
            payload = {
                "version": "V2.00.01",
                "platformKey": "F2EE24892FBF66F0AFF8C0EB532A9394",
                "mini_scene": 1008,
                "partner_ext_infos": "",
            }
            response = session.post(url, data=payload)
            response.raise_for_status()
            response_json = response.json()
            message = response_json.get("message", "")
            self.log(f"[签到] {message}")
            return "过期" not in message
        except requests.RequestException as e:
            self.log(f"[签到] 网络错误: {e}\n{traceback.format_exc()}", level="error")
            return False
        except Exception as e:
            self.log(f"[签到] 未知错误: {e}\n{traceback.format_exc()}", level="error")
            return False

    def step_exchange(self, session: requests.Session) -> bool:
        try:
            url = f"https://{self.host}/step/exchange"
            payload = {
                "steps": 10000,
                "version": "V2.00.01",
                "platformKey": "F2EE24892FBF66F0AFF8C0EB532A9394",
                "mini_scene": 1008,
                "partner_ext_infos": "",
            }
            response = session.post(url, data=payload)
            response.raise_for_status()
            response_json = response.json()
            message = response_json.get("message", "")
            self.log(f"[步数兑换] {message}")
            return "每天最多兑换" not in message
        except requests.RequestException as e:
            self.log(f"[步数兑换] 网络错误: {e}\n{traceback.format_exc()}", level="error")
            return False
        except Exception as e:
            self.log(f"[步数兑换] 未知错误: {e}\n{traceback.format_exc()}", level="error")
            return False

    def pool_bet(self, session: requests.Session) -> bool:
        try:
            url = f"https://{self.host}/active/pool/bet"
            payload = {
                "version": "V2.00.01",
                "platformKey": "F2EE24892FBF66F0AFF8C0EB532A9394",
                "mini_scene": 1008,
                "partner_ext_infos": "",
            }
            response = session.post(url, data=payload)
            response.raise_for_status()
            response_json = response.json()
            self.log(f"[奖池投注] {response_json.get('message', '')}")
            return True
        except requests.RequestException as e:
            self.log(f"[奖池投注] 网络错误: {e}\n{traceback.format_exc()}", level="error")
            return False
        except Exception as e:
            self.log(f"[奖池投注] 未知错误: {e}\n{traceback.format_exc()}", level="error")
            return False

    def pool_sign(self, session: requests.Session) -> bool:
        try:
            url = f"https://{self.host}/active/pool/sign"
            payload = {
                "version": "V2.00.01",
                "platformKey": "F2EE24892FBF66F0AFF8C0EB532A9394",
                "mini_scene": 1008,
                "partner_ext_infos": "",
            }
            response = session.post(url, data=payload)
            response.raise_for_status()
            response_json = response.json()
            self.log(f"[奖池签到] {response_json.get('message', '')}")
            return True
        except requests.RequestException as e:
            self.log(f"[奖池签到] 网络错误: {e}\n{traceback.format_exc()}", level="error")
            return False
        except Exception as e:
            self.log(f"[奖池签到] 未知错误: {e}\n{traceback.format_exc()}", level="error")
            return False

    def get_user_beans(self, session: requests.Session) -> int:
        try:
            url = f"https://{self.host}/user/new/beans/info"
            params = {
                "type": 1,
                "version": "V2.00.01",
                "platformKey": "F2EE24892FBF66F0AFF8C0EB532A9394",
                "mini_scene": 1008,
                "partner_ext_infos": "",
            }

            total_get_response = session.get(url, params=params)
            total_get_response_json = total_get_response.json()
            total_get_beans = total_get_response_json["data"]["totalCount"]

            params["type"] = 2
            total_use_response = session.get(url, params=params)
            total_use_response_json = total_use_response.json()
            total_use_beans = total_use_response_json["data"]["totalCount"]

            total_beans = total_get_beans - total_use_beans
            self.log(f"[豆子余额] {total_beans}")
            return total_beans
        except requests.RequestException as e:
            self.log(f"[豆子余额] 网络错误: {e}\n{traceback.format_exc()}", level="error")
            return 0
        except Exception as e:
            self.log(f"[豆子余额] 未知错误: {e}\n{traceback.format_exc()}", level="error")
            return 0

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

                session = requests.Session()
                session.headers["User-Agent"] = self.user_agent

                if code and self.code_login(session, code):
                    if self.sign_in(session):
                        time.sleep(random.randint(3, 5))
                        while self.step_exchange(session):
                            time.sleep(random.randint(3, 5))
                        self.pool_bet(session)
                        time.sleep(random.randint(3, 5))
                        self.pool_sign(session)
                        time.sleep(random.randint(3, 5))
                        self.get_user_beans(session)
                        time.sleep(random.randint(5, 10))

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
                header = "作者：临上\n"
                content = header + "\n" + "\n".join(self.log_msgs)
                notify.send(title, content)


if __name__ == "__main__":
    auto_task = AutoTask("飞蚂蚁")
    auto_task.run()
