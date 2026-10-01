# -*- coding: utf-8 -*-
# @Time     : 2025-12-25
# @Author   : 凉白开（修订版本）
# @Version  ：6.0
# @Desc     : 蒙娜丽莎小程序：自动获取token → 自动签到，一体化脚本（日志调试 + 精简推送）
#             使用getCode模块获取code，环境变量：WECHAT_SERVER、ADMIN_KEY、WX_ID（可选）

import os
import time
import random
import requests

try:
    from notify import send
except ImportError:
    print("未找到 notify.py，将仅在控制台输出日志。")
    def send(title, content):
        print(f"--- 通知 ---\n{title}\n{content}\n-------------")

from getCode import get_wechat_codes

MNLS_APP_ID = "wxce6a8f654e81b7a4"

# ======================================================
#                 第 1 部分：获取 code → tokenStr
# ======================================================
message_list = []

def get_customer_token(code):
    """调用 doAction 获取 CustomerID + tokenStr"""
    url = "https://mcs.monalisagroup.com.cn/member/doAction"
    headers = {
    "Host": "mcs.monalisagroup.com.cn",
    "Connection": "keep-alive",
    "xweb_xhr": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090c2d)XWEB/14315",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "*/*",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Referer": "https://servicewechat.com/wxce6a8f654e81b7a4/468/page-frame.html",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9"
}

    data = {
        "brand": "MON",
        "webChatName": "微信用户",
        "telephone": "",
        "code": code,
        "remarks": "",
        "operationType":"",
        "action": "addCustomer",
        "customerName": "微信用户",
        "storeID": "",
        "address": "-",
        "Province": "",
        "City": "",
        "Region": ""
    }
    # r = requests.post(url, headers=headers, data=data, timeout=15).json()
    # print(r)
    try:
        r = requests.post(url, headers=headers, data=data, timeout=15).json()
        # print(r)
        if "tokenStr" in r and r.get("resultInfo"):
            customer_id = r["resultInfo"][0]["CustomerID"]
            tokenStr = r["tokenStr"]
            print(f"[INFO] 获取成功：CustomerID={customer_id} tokenStr={tokenStr}")
            return f"{customer_id}#{tokenStr}"
    except Exception as e:
        print(f"[ERROR] 获取 tokenStr 失败：{e}")

    return None


# ======================================================
#                   第 2 部分：签到模块
# ======================================================
class MNLS:
    def __init__(self, index, account):
        self.index = index
        self.customerId, self.tokenStr = account.split("#")
        self.mobile = ""
        self.score = 0
        self.msg = ""

        self.headers = {
    "Host": "mcs.monalisagroup.com.cn",
    "Connection": "keep-alive",
    "xweb_xhr": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090c2d)XWEB/14315",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "*/*",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Referer": "https://servicewechat.com/wxce6a8f654e81b7a4/468/page-frame.html",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9"
}

    def hide_phone(self, phone):
        if not phone or len(phone) != 11:
            return phone
        return phone[:3] + "****" + phone[7:]

    def get_info(self):
        url = "https://mcs.monalisagroup.com.cn/member/doAction"
        # data = f"brand=MON&customerID={self.customerId}&action=getCustInfoByID"
        data = {
            "brand": "MON",
            "customerID": self.customerId,  # 假设self.customerId已在类中定义
            "action": "getCustInfoByID"
        }
        try:
            r = requests.post(url, headers=self.headers, data=data).json()
            if r.get("status") == 0:
                info = r["resultInfo"][0]
                self.mobile = self.hide_phone(info["Telephone"])
                self.score = info["Integral"]
                print(f"[INFO] 账号{self.index} 信息获取成功：手机号={self.mobile} 积分={self.score}")
                return True
        except Exception as e:
            print(f"[ERROR] 账号{self.index} 获取信息失败：{e}")
        return False

    def getCaptcha(self):
        url = "https://mcs.monalisagroup.com.cn/member/doAction"
        # data = f"brand=MON&action=generateCaptcha&tokenStr={self.tokenStr}"
        data = {
            "brand": "MON",
            "action": "generateCaptcha",
            "tokenStr": self.tokenStr  # 假设self.tokenStr已在类中定义
        }
        n=0
        while n < 6:
            try:
                r = requests.post(url, headers=self.headers, data=data).json()
                image = r["resultInfo"]
                print(f"[INFO] 账号{self.index} 获取验证码...")
                self.getocr(image)

                if hasattr(self, 'msg') and ("签到成功" in self.msg or "今天已经签到过了" in self.msg):
                    return
                else:
                    n += 1
                    if n < 6:
                        print(f"[INFO] 账号{self.index} 第{n}次重试签到流程...")
                        continue
                    else:
                        break

            except Exception as e:
                n += 1
                if n < 6:
                    print(f"[INFO] 账号{self.index} 第{n}次重试获取验证码...")
                    continue
                else:
                    self.msg = f"重试6次后仍然失败：{e}"
                    print(f"[ERROR] 账号{self.index} {self.msg}")

    def getocr(self,image):
        url = "http://192.144.207.114:7777/calculate"
        data = {"image": image}
        ocr_retry = 0
        while ocr_retry < 2:
            try:
                res = requests.post(url, json=data, timeout=10).json()
                result = res["result"]
                print(f"[INFO] 账号{self.index} 验证码计算结果：{result}")
                self.sign(result)
                return
            except Exception as e:
                ocr_retry += 1
                if ocr_retry < 2:
                    print(f"[INFO] 账号{self.index} OCR识别失败，第{ocr_retry}次重试...")
                    continue
                else:
                    raise Exception(f"OCR识别失败: {e}")

    def sign(self,i):
        url = "https://mcs.monalisagroup.com.cn/member/doAction"
        # data = (
        #     f"brand=MON&action=sign&CustomerID={self.customerId}&CustomerName=%E5%BE%AE%E4%BF%A1%E7%94%A8%E6%88%B7&"
        #     f"StoreID=0&OrganizationID=0&ItemType=002&Brand=MON&tokenStr={self.tokenStr}&correctAnswer={i}"
        # )
        data = {
            "brand": "MON",
            "action": "sign",
            "CustomerID": self.customerId,
            "CustomerName": "微信用户",
            "StoreID": "0",
            "OrganizationID": "0",
            "ItemType": "002",
            "Brand": "MON",
            "tokenStr": self.tokenStr,
            "correctAnswer": i  # 假设i是循环变量或已定义
        }
        try:
            r = requests.post(url, headers=self.headers, data=data).json()
            print(f"[DEBUG] 签到响应: {r}")
            if r.get("status") == 0:
                self.msg = f"签到成功，获得积分：{r.get('resultInfo')}"
            elif r.get("status") == 7:
                self.msg = "今天已经签到过了"
            else:
                self.msg = f"签到失败：{r}"
            print(f"[INFO] 账号{self.index} 签到状态：{self.msg}")
        except Exception as e:
            self.msg = f"签到异常：{e}"
            print(f"[ERROR] 账号{self.index} 签到异常：{e}")
            raise

    def run(self):
        self.get_info()
        self.getCaptcha()
        # self.sign()
        self.get_info()  # 更新积分
        return f"账号{self.index} → {self.msg}，积分：{self.score}"


# ======================================================
#                   主流程整合
# ======================================================
if __name__ == "__main__":
    print("\n===== 通过getCode模块获取小程序Code =====")
    try:
        codes = get_wechat_codes(MNLS_APP_ID)
    except Exception as e:
        print(f"❌ 获取Code失败: {e}")
        message_list.append(f"❌ 获取Code失败: {e}")
        print("\n".join(message_list))
        exit(0)

    if not codes:
        print("❌ 未获取到任何账号的Code")
        exit(0)

    account_list = []

    print("\n===== 开始获取 CustomerID#tokenStr =====")
    for nick_name, code in codes.items():
        print(f"[INFO] 处理账号: {nick_name}")
        account = get_customer_token(code)
        print(account)
        if account:
            account_list.append(account)
        time.sleep(random.uniform(1, 2))
    print(account_list)

    if not account_list:
        print("❌ 未获取到任何账号 tokenStr")
        exit(0)

    # ======================================================
    #                     进入自动签到
    # ======================================================
    print("\n===== 开始签到 =====")
    msg_final = []
    for idx, acc in enumerate(account_list, start=1):
        result = MNLS(idx, acc).run()
        msg_final.append(result)
        time.sleep(random.uniform(2, 4))

    # 控制台输出完整日志
    output = "\n".join(message_list + msg_final)
    print(output)

    # 最终推送：只包含签到结果和积分
    send("蒙娜丽莎自动签到结果", "\n".join(msg_final))
