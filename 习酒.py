import os, time, base64, logging, requests
from datetime import datetime, timedelta
from xj_client import GardenClient, APPID

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

"""
========================================
环境变量配置说明
========================================

必填：
  WECHAT_SERVER   协议服务IP地址和端口
                  示例：http://192.168.1.100:8080

  ADMIN_KEY       与搭建时设置的ADMIN_KEY一致
                  （仅WeChatPadPro或iwechat需要，牛子协议不需要）

选填：
  WX_ID           指定要执行任务的微信账号ID，多个用 & 分隔
                  对应iwechat接口的wx_id字段或WeChatPadPro接口的deviceId字段或牛子协议的wxid字段
                  示例：wxid_abc123&wxid_xyz456
                  如果不设置则对所有有效账号执行任务

  OCR_SERVER      滑块验证码识别服务地址（ddddocr）
                  默认：xzxxn777 / ddddocr
                  不设则遇到滑块验证时报错

  GARDEN_SEED_TYPE  播种作物类型
                  0 = 自动判断（默认）：酒曲充足种高粱，不足种小麦
                  1 = 强制种高粱
                  2 = 强制种小麦

  GARDEN_AUTO_EXCHANGE  自动兑换积分开关
                  0 = 关闭（默认）
                  1 = 开启，有酒时自动兑换积分（1L=1积分）

选填： 自动判断更新青龙面板定时任务的执行时间

     #  修改脚本  59行的配置信息
     #  脚本同文件夹放青龙面板自带的notify.py推送脚本
     #  青龙定时任务名称 改为 习酒  或修改 第102行任务搜索名
      #  当遇到 5001 加密校验失败时 脚本会自动删除缓存文件xijiutoken.json
========================================
"""

# ==================== 脚本配置 ====================
DEFAULT_INTERVAL_SECS = 300  # 默认执行间隔（秒），未获取到地块收获时间时 与下次定时时间间隔 
# ==================== 配置结束 ====================


def update_ql_cron_time(schedule):
    """更新青龙面板定时任务的执行时间"""
    import http.client
    import json

    # 配置信息
    host = "192.168.10.3"   # 青龙登陆IP 示例 192.168.1.179
    port = 5700              # 青龙端口
    client_id = "KZGvcH2-4qJV"   # 青龙client_id   给权限 定时任务 配置文件 脚本管理
    client_secret = "H8mSPzv6_N8Sw5kcZfNSKX8j"  # 青龙client_secret

    try:
        # 1. 通过 OpenAPI 获取 token（仅需 client_id / client_secret）
        log.info("🔑 通过 OpenAPI 获取青龙 token...")
        conn = http.client.HTTPConnection(host, port, timeout=10)
        conn.request(
            "GET",
            f"/open/auth/token?client_id={client_id}&client_secret={client_secret}",
        )
        res = conn.getresponse()
        data = res.read().decode("utf-8")
        j = json.loads(data)
        if j.get("code") != 200:
            log.error(f"❌ OpenAPI 获取 token 失败: {j}")
            conn.close()
            return False
        token = j["data"]["token"]
        log.info("✅ 获取青龙 token 成功")
        conn.close()

        # 2. 获取所有任务
        log.info("📋 获取青龙面板任务列表...")
        auth_headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        conn = http.client.HTTPConnection(host, port)
        conn.request("GET", "/open/crons", "", auth_headers)
        res = conn.getresponse()
        data = res.read().decode("utf-8")
        result = json.loads(data)
        conn.close()

        # 3. 查找并修改习酒1任务
        all_tasks = result["data"]["data"]
        found = False

        for task in all_tasks:
            if task["name"] == "习酒":
                task_id = task["id"]
                log.info(f"✅ 找到 习酒 任务：ID={task_id}")

                # 4. 发送修改请求
                conn = http.client.HTTPConnection(host, port)
                payload = json.dumps({
                    "id": task_id,
                    "name": task["name"],
                    "command": task["command"],
                    "schedule": schedule,
                    "labels": task["labels"]
                })
                conn.request("PUT", "/open/crons", payload, auth_headers)
                res = conn.getresponse()
                res_data = res.read().decode("utf-8")
                conn.close()

                log.info("✅ 青龙面板定时任务更新成功")
                log.info(f"✅ 新执行时间：{schedule}")
                found = True
                return True

        if not found:
            log.warning("❌ 未找到习酒1任务")
            return False

    except Exception as e:
        log.error(f"更新青龙面板定时任务失败: {e}")
        return False


PLOT_STATUS = {-1: "未解锁", 0: "空地", 1: "已播种", 2: "生长中", 10: "可收获", 11: "可收获(熟透)"}
CROP_TYPE = {1: "高粱", 2: "小麦"}
# 酒坛状态：0/1=空坛可投粮，2=已酿好可收获，3=酿造中，4=已酿好可收获(同2)
WINE_STATUS = {0: "空坛", 1: "空坛", 2: "已酿好", 3: "酿造中", 4: "已酿好"}

# 收获后自动播种类型：1=高粱，2=小麦，0=自动判断（默认）
# 可通过环境变量 GARDEN_SEED_TYPE=1/2 强制指定，不设则自动判断
_SEED_TYPE_FORCE = int(os.environ.get("GARDEN_SEED_TYPE", "0"))

# 自动兑换积分开关：默认关闭，设置 GARDEN_AUTO_EXCHANGE=1 开启
_AUTO_EXCHANGE = os.environ.get("GARDEN_AUTO_EXCHANGE", "0") == "1"

# 跳过酿酒的日期：每月这些日期不投粮酿酒
_SKIP_WINE_DAYS = {25, 26, 27, 28, 29, 30, 31}


def can_make_wine() -> bool:
    """根据当前日期判断是否允许酿酒"""
    return datetime.now().day not in _SKIP_WINE_DAYS


def decide_seed_type(sorghum: int, wheat: int, wine_yeast: int, active_plots: int) -> int:
    """
    自动决策种什么：
    - 每块地每轮产100斤，200斤高粱+1块酒曲酿40L酒
    - 酒曲需求：active_plots块地每轮产 active_plots*100 斤高粱，需要 active_plots*100/200 块酒曲
    - 储备目标：酒曲能覆盖未来5轮高粱产量
    - 酒曲充足 → 种高粱；酒曲不足 → 种小麦（100斤小麦→10块酒曲）
    """
    if _SEED_TYPE_FORCE in (1, 2):
        return _SEED_TYPE_FORCE
    plots = max(active_plots, 1)
    # 未来5轮高粱产量需要的酒曲数
    yeast_needed = (plots * 100 * 5) // 200  # 每200斤高粱消耗1块酒曲
    if wine_yeast >= yeast_needed:
        return 1  # 酒曲够，种高粱
    else:
        return 2  # 酒曲不足，种小麦补酒曲


def send_notify(title, content):
    try:
        import sys
        sys.path.insert(0, "/ql/data/scripts")
        from notify import send
        send(title, content)
    except ImportError:
        log.warning("未找到青龙面板自带的notify.py，跳过推送")
    except Exception as e:
        log.warning(f"推送失败: {e}")


def fmt_remaining(s):
    try:
        delta = datetime.strptime(s, "%Y-%m-%d %H:%M:%S") - datetime.now()
        t = int(delta.total_seconds())
        if t <= 0: return "已成熟"
        h, r = divmod(t, 3600)
        return "%dh%02dm" % (h, r // 60)
    except Exception:
        return s or "未知"


def fmt_remaining_from_seconds(secs):
    """将秒数格式化为易读的时间字符串"""
    if secs <= 0:
        return "已成熟"
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    if h > 0:
        return "%dh%02dm%02ds" % (h, m, s)
    elif m > 0:
        return "%dm%02ds" % (m, s)
    else:
        return "%ds" % s


def is_ready(s, tolerance_seconds=120):
    """判断是否可以收获，允许提前 tolerance_seconds 秒（避免整点运行时差几秒错过）"""
    try:
        return datetime.now() >= datetime.strptime(s, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=tolerance_seconds)
    except Exception:
        return True


def run(client, do_daily=True):
    plot_summary_lines = []  # 收集地块状态用于通知
    min_harvest_secs = None  # 追踪最短剩余收获时间（秒）
    # 1. 会员信息
    log.info("获取会员信息...")
    info = client.member_info()
    log.info("  昵称: %s  积分: %s  水: %s  肥: %s  高粱: %s斤  小麦: %s斤  酒曲: %s块  酒: %sL" % (
        info.get("nick_name"), info.get("integration"), info.get("water"),
        info.get("manure"), info.get("sorghum"), info.get("wheat"),
        info.get("wine_yeast"), info.get("wine")))

    if do_daily:
        # 2. 每日签到
        log.info("每日签到...")
        try:
            r = client.daily_sign()
            log.info("  签到: 水+%s 肥+%s  %s" % (r.get("water", 0), r.get("manure", 0), r.get("tips", "")))
        except RuntimeError as e:
            log.warning("  签到: %s" % e)

        # 3. 每日分享（最多3次）
        log.info("每日分享...")
        for i in range(3):
            try:
                r = client.daily_share()
                w, m = r.get("water", 0), r.get("manure", 0)
                if not r.get("isTodayFirstShare") and w == 0 and m == 0:
                    log.info("  分享已达上限"); break
                log.info("  第%d次分享: 水+%s 肥+%s" % (i + 1, w, m))
                time.sleep(1)
            except RuntimeError as e:
                log.warning("  分享: %s" % e); break
    else:
        log.info("签到/分享今日已完成，跳过")

    # 4. 地块处理
    log.info("查看地块...")
    plots = client.get_sorghum_list() or []
    active = [p for p in plots if p.get("status", -1) != -1]
    log.info("  共 %d 块地，已解锁 %d 块" % (len(plots), len(active)))

    # 开垦新土地
    try:
        ss = len(active) + 1
        log.info(f"尝试开垦地块第{ss}块田地")
        rr = client.extend({"serial_number": ss})
        log.info(f" 开垦新地块成功!")
    except RuntimeError as e:
        err_str = str(e)
        if "4041" in err_str:
            log.warning("开垦新地块：收酒数量不足，无法开垦(跳过)")
        elif "5001" in err_str or "加密校验失败" in err_str:
            log.warning(f"开垦新地块失败：{e}")
            raise  # 重新抛出5001错误，让主程序捕获并删除缓存
        else:
            log.warning(f"开垦新地块失败：{e}")
    except Exception as e:
        log.warning(f"开垦新地块异常：{e}")

    # 自动决策种植类型（基于当前库存）
    seed_type = decide_seed_type(
        sorghum=int(info.get("sorghum") or 0),
        wheat=int(info.get("wheat") or 0),
        wine_yeast=int(info.get("wine_yeast") or 0),
        active_plots=len(active),
    )
    log.info("  种植策略: %s (酒曲%s块，已解锁%d块地)" % (
        CROP_TYPE.get(seed_type), info.get("wine_yeast"), len(active)))

    for plot in plots:
        pid = plot.get("id")
        status = plot.get("status", -1)
        if status == -1 or not pid: continue
        crop = CROP_TYPE.get(plot.get("type", 1), "作物")
        sn = plot.get("serial_number", "?")
        ct = plot.get("crop_time", "")
        wn, mn = plot.get("water_num", 0), plot.get("manure_num", 0)
        water = int(info.get("water") or 0)
        manure = int(info.get("manure") or 0)

        if (status in (10, 11) and is_ready(ct)) or (status == 2 and is_ready(ct)):
            # 时间到了（含2分钟容差）-> 收获，失败则等待重试
            log.info("  地块%s(%s) [可收获] -> 收获" % (sn, crop))
            plot_summary_lines.append("🌾 地块%s(%s): 已收获并重新播种" % (sn, crop))
            harvested = False
            # 最多等待130秒（容差2分钟+10秒缓冲），每5秒重试一次
            for attempt in range(27):
                try:
                    r = client.harvest({"id": pid})
                    got = int(r.get("volumn") or r.get("sorghum") or r.get("wheat") or 0)
                    if got > 0:
                        log.info("    收获成功: +%s斤" % got)
                    else:
                        log.info("    收获成功(0斤)")
                    harvested = True
                    break
                except RuntimeError as e:
                    err_str = str(e)
                    if "5001" in err_str or "加密校验失败" in err_str:
                        log.warning("    收获失败: %s" % e)
                        raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                    if attempt < 5 and ("未成熟" in err_str or "not mature" in err_str.lower() or "时间" in err_str):
                        log.info("    未成熟，等待5秒重试(%d/5)..." % (attempt + 1))
                        time.sleep(5)
                    else:
                        log.warning("    收获失败: %s" % e)
                        break
            time.sleep(1)
            if harvested:
                log.info("    自动播种: %s" % CROP_TYPE.get(seed_type))
                try:
                    client.seeds({"id": pid, "type": seed_type})
                    log.info("    播种成功")
                    time.sleep(1)
                    if water > 0:
                        try:
                            client.watering({"id": pid}); log.info("    浇水成功"); water -= 1
                        except RuntimeError as e:
                            err_str = str(e)
                            if "5001" in err_str or "加密校验失败" in err_str:
                                log.warning("    浇水: %s" % e)
                                raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                            log.warning("    浇水: %s" % e)
                        time.sleep(1)
                    else:
                        log.info("    水滴不足，跳过浇水")
                    if manure > 0:
                        try:
                            client.manuring({"id": pid}); log.info("    施肥成功"); manure -= 1
                        except RuntimeError as e:
                            err_str = str(e)
                            if "5001" in err_str or "加密校验失败" in err_str:
                                log.warning("    施肥: %s" % e)
                                raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                            log.warning("    施肥: %s" % e)
                except RuntimeError as e:
                    err_str = str(e)
                    if "5001" in err_str or "加密校验失败" in err_str:
                        log.warning("    播种失败: %s" % e)
                        raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                    log.warning("    播种失败: %s" % e)
                time.sleep(1)

        elif status in (10, 11, 2) and not is_ready(ct):
            # 生长中（未到时间）-> 浇水+施肥
            try:
                remaining_secs = max(0, int((datetime.strptime(ct, "%Y-%m-%d %H:%M:%S") - datetime.now()).total_seconds())) if ct else 999
            except Exception:
                remaining_secs = 999
            # 更新最短收获时间
            if min_harvest_secs is None or remaining_secs < min_harvest_secs:
                min_harvest_secs = remaining_secs
            # 剩余不足2分钟，直接等待到成熟后收获
            if remaining_secs <= 120 and remaining_secs > 0:
                log.info("  地块%s(%s) [即将成熟] 剩余%d秒，等待后收获" % (sn, crop, remaining_secs))
                time.sleep(remaining_secs + 2)
                try:
                    r = client.harvest({"id": pid})
                    got = int(r.get("volumn") or r.get("sorghum") or r.get("wheat") or 0)
                    log.info("    等待后收获成功: +%s斤" % got)
                    plot_summary_lines.append("🌾 地块%s(%s): 等待后收获+%s斤并重新播种" % (sn, crop, got))
                    time.sleep(1)
                    client.seeds({"id": pid, "type": seed_type})
                    log.info("    播种成功")
                    time.sleep(1)
                    if water > 0:
                        try:
                            client.watering({"id": pid}); log.info("    浇水成功"); water -= 1
                        except RuntimeError as e:
                            err_str = str(e)
                            if "5001" in err_str or "加密校验失败" in err_str:
                                log.warning("    浇水: %s" % e)
                                raise  # 重新抛出5001错误
                            log.warning("    浇水: %s" % e)
                        time.sleep(1)
                    if manure > 0:
                        try:
                            client.manuring({"id": pid}); log.info("    施肥成功"); manure -= 1
                        except RuntimeError as e:
                            err_str = str(e)
                            if "5001" in err_str or "加密校验失败" in err_str:
                                log.warning("    施肥: %s" % e)
                                raise  # 重新抛出5001错误
                            log.warning("    施肥: %s" % e)
                except RuntimeError as e:
                    err_str = str(e)
                    if "5001" in err_str or "加密校验失败" in err_str:
                        log.warning("    等待后收获失败: %s" % e)
                        raise  # 重新抛出5001错误
                    log.warning("    等待后收获失败: %s" % e)
                time.sleep(1)
                continue
            log.info("  地块%s(%s) [生长中] 剩余: %s  浇水%s次 施肥%s次" % (
                sn, crop, fmt_remaining(ct), wn, mn))
            plot_summary_lines.append("🌱 地块%s(%s): 还需 %s" % (sn, crop, fmt_remaining(ct)))
            if water > 0:
                try:
                    client.watering({"id": pid}); log.info("    浇水成功"); water -= 1
                except RuntimeError as e:
                    err_str = str(e)
                    if "5001" in err_str or "加密校验失败" in err_str:
                        log.warning("    浇水: %s" % e)
                        raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                    log.warning("    浇水: %s" % e)
            else:
                log.info("    水滴不足，跳过浇水")
            time.sleep(1)
            if manure > 0:
                try:
                    client.manuring({"id": pid}); log.info("    施肥成功"); manure -= 1
                except RuntimeError as e:
                    err_str = str(e)
                    if "5001" in err_str or "加密校验失败" in err_str:
                        log.warning("    施肥: %s" % e)
                        raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                    log.warning("    施肥: %s" % e)
            else:
                log.info("    肥料不足，跳过施肥")
            time.sleep(1)

        elif status == 0:
            log.info("  地块%s [空地] -> 播种%s" % (sn, CROP_TYPE.get(seed_type)))
            plot_summary_lines.append("🟫 地块%s: 空地已播种%s" % (sn, CROP_TYPE.get(seed_type)))
            try:
                client.seeds({"id": pid, "type": seed_type})
                log.info("    播种成功")
                time.sleep(1)
                if water > 0:
                    try:
                        client.watering({"id": pid}); log.info("    浇水成功"); water -= 1
                    except RuntimeError as e:
                        err_str = str(e)
                        if "5001" in err_str or "加密校验失败" in err_str:
                            log.warning("    浇水: %s" % e)
                            raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                        log.warning("    浇水: %s" % e)
                else:
                    log.info("    水滴不足，跳过浇水")
                time.sleep(1)
                if manure > 0:
                    try:
                        client.manuring({"id": pid}); log.info("    施肥成功"); manure -= 1
                    except RuntimeError as e:
                        err_str = str(e)
                        if "5001" in err_str or "加密校验失败" in err_str:
                            log.warning("    施肥: %s" % e)
                            raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                        log.warning("    施肥: %s" % e)
                else:
                    log.info("    肥料不足，跳过施肥")
            except RuntimeError as e:
                err_str = str(e)
                if "5001" in err_str or "加密校验失败" in err_str:
                    log.warning("    播种失败: %s" % e)
                    raise  # 重新抛出5001错误，让主程序捕获并删除缓存
                log.warning("    播种失败: %s" % e)
            time.sleep(1)

        else:
            log.info("  地块%s(%s) [%s]" % (sn, crop, PLOT_STATUS.get(status, "状态%s" % status)))
            plot_summary_lines.append("❓ 地块%s(%s): %s" % (sn, crop, PLOT_STATUS.get(status, "状态%s" % status)))

    # 5. 酒坛处理
    log.info("查看酒坛...")
    wines = client.wine_list() or []
    info = client.member_info() or info
    sorghum = int(info.get("sorghum") or 0)
    wheat = int(info.get("wheat") or 0)
    wine_yeast = int(info.get("wine_yeast") or 0)
    wine_vol = int(info.get("wine") or 0)
    log.info("  共 %d 个酒坛  库存: 高粱%s斤 小麦%s斤 酒曲%s块 酒%sL" % (
        len(wines), sorghum, wheat, wine_yeast, wine_vol))

    # 没有酒坛时直接尝试制酒（makeWine 会自动创建酒坛）
    if not wines:
        can_put = min((sorghum // 200) * 200, 5000, wine_yeast * 200)
        if can_put >= 200:
            if not can_make_wine():
                log.info("  无酒坛，今日(%d号)跳过酿酒" % datetime.now().day)
            else:
                log.info("  无酒坛 -> 制酒 %s斤高粱 (消耗酒曲%s块)" % (can_put, can_put // 200))
                try:
                    r = client.discharge_grain({"volumn": can_put})
                    log.info("    制酒成功: %s" % r)
                    sorghum -= can_put
                    wine_yeast -= can_put // 200
                except RuntimeError as e:
                    log.warning("    制酒失败: %s" % e)
                time.sleep(1)
        elif sorghum < 200:
            log.info("  无酒坛，高粱不足(有%s斤，需>=200斤)" % sorghum)
        else:
            log.info("  无酒坛，酒曲不足(有%s块)" % wine_yeast)

    for wine in wines:
        wid = wine.get("id")
        wst = wine.get("status")
        vol = int(wine.get("volumn") or 0)
        cur = int(wine.get("crrent_volumn") or 0)
        ct = wine.get("crop_time", "")

        if wst in (2, 4) and vol > 0:
            # 已酿好，收获
            log.info("  酒坛%s [已酿好 %sL] -> 收获" % (wid, vol))
            try:
                r = client.harvest_wine({"id": wid})
                got = r.get("wine") or r.get("volumn") or ""
                log.info("    收获成功%s" % (": +%sL" % got if got else ""))
                info = client.member_info() or info
                sorghum = int(info.get("sorghum") or 0)
                wine_vol = int(info.get("wine") or 0)
                wine_yeast = int(info.get("wine_yeast") or 0)
            except RuntimeError as e:
                log.warning("    收获失败: %s" % e)
                time.sleep(1)
                continue
            time.sleep(1)
            # 收获后立即投粮酿酒
            can_put = min((sorghum // 200) * 200, 5000, wine_yeast * 200)
            if can_put >= 200:
                if not can_make_wine():
                    log.info("    今日(%d号)跳过投粮" % datetime.now().day)
                else:
                    log.info("    立即投粮: %s斤高粱 (消耗酒曲%s块)" % (can_put, can_put // 200))
                    try:
                        r = client.discharge_grain({"volumn": can_put})
                        log.info("    投粮成功: %s" % r)
                        sorghum -= can_put
                        wine_yeast -= can_put // 200
                    except RuntimeError as e:
                        log.warning("    投粮失败: %s" % e)
                    time.sleep(1)
            else:
                if sorghum < 200:
                    log.info("    高粱不足(有%s斤)，跳过投粮" % sorghum)
                else:
                    log.info("    酒曲不足(有%s块)，跳过投粮" % wine_yeast)

        elif wst == 3:
            if is_ready(ct):
                log.info("  酒坛%s [酿造完成 %sL] -> 收获" % (wid, vol))
                try:
                    r = client.harvest_wine({"id": wid})
                    got = r.get("wine") or r.get("volumn") or ""
                    log.info("    收获成功%s" % (": +%sL" % got if got else ""))
                    info = client.member_info() or info
                    sorghum = int(info.get("sorghum") or 0)
                    wine_vol = int(info.get("wine") or 0)
                    wine_yeast = int(info.get("wine_yeast") or 0)
                except RuntimeError as e:
                    log.warning("    收获失败: %s" % e)
                    time.sleep(1)
                    continue
                time.sleep(1)
                # 收获后立即投粮
                can_put = min((sorghum // 200) * 200, 5000, wine_yeast * 200)
                if can_put >= 200:
                    if not can_make_wine():
                        log.info("    今日(%d号)跳过投粮" % datetime.now().day)
                    else:
                        log.info("    立即投粮: %s斤高粱 (消耗酒曲%s块)" % (can_put, can_put // 200))
                        try:
                            r = client.discharge_grain({"volumn": can_put})
                            log.info("    投粮成功: %s" % r)
                            sorghum -= can_put
                            wine_yeast -= can_put // 200
                        except RuntimeError as e:
                            log.warning("    投粮失败: %s" % e)
                        time.sleep(1)
                else:
                    if sorghum < 200:
                        log.info("    高粱不足(有%s斤)，跳过投粮" % sorghum)
                    else:
                        log.info("    酒曲不足(有%s块)，跳过投粮" % wine_yeast)
            else:
                log.info("  酒坛%s [酿造中 %sL] 剩余: %s" % (wid, vol, fmt_remaining(ct)))

        elif wst in (0, 1) or (wst == 4 and vol == 0):
            # 空坛，尝试投粮（每200斤高粱+1块酒曲酿40L，最多5000斤）
            max_by_sorghum = (min(sorghum, 5000) // 200) * 200
            max_by_yeast = (wine_yeast * 200)
            can_put = min(max_by_sorghum, max_by_yeast)
            if can_put >= 200:
                if not can_make_wine():
                    log.info("  酒坛%s [空坛] 今日(%d号)跳过投粮" % (wid, datetime.now().day))
                else:
                    log.info("  酒坛%s [空坛] -> 投粮 %s斤高粱 (消耗酒曲%s块)" % (
                        wid, can_put, can_put // 200))
                    try:
                        r = client.discharge_grain({"id": wid, "volumn": can_put})
                        log.info("    投粮成功: %s" % r)
                        sorghum -= can_put
                        wine_yeast -= can_put // 200
                    except RuntimeError as e:
                        log.warning("    投粮失败: %s" % e)
                    time.sleep(1)
            else:
                if sorghum < 200:
                    log.info("  酒坛%s [空坛] 跳过: 高粱不足(有%s斤，需>=200斤)" % (wid, sorghum))
                else:
                    log.info("  酒坛%s [空坛] 跳过: 酒曲不足(有%s块)" % (wid, wine_yeast))

        else:
            log.info("  酒坛%s [状态:%s]" % (wid, wst))

    # 6. 制曲（100斤小麦→10块酒曲）
    wheat = int((client.member_info() or info).get("wheat") or 0)
    if wheat >= 100:
        put_wheat = min((wheat // 100) * 100, 1000)
        log.info("制曲：%s斤小麦 -> 预计+%s块酒曲" % (put_wheat, put_wheat // 100 * 10))
        try:
            r = client.make_yeast({"volumn": put_wheat})
            log.info("  制曲成功: %s" % r)
            # 制曲后刷新库存，尝试投粮
            info2 = client.member_info() or {}
            sorghum2 = int(info2.get("sorghum") or 0)
            wine_yeast2 = int(info2.get("wine_yeast") or 0)
            can_put = min((sorghum2 // 200) * 200, 5000, wine_yeast2 * 200)
            if can_put >= 200:
                if not can_make_wine():
                    log.info("  制曲后今日(%d号)跳过投粮" % datetime.now().day)
                else:
                    log.info("  制曲后投粮: %s斤高粱 (消耗酒曲%s块)" % (can_put, can_put // 200))
                    try:
                        r2 = client.discharge_grain({"volumn": can_put})
                        log.info("    投粮成功: %s" % r2)
                    except RuntimeError as e:
                        log.warning("    投粮失败: %s" % e)
                    time.sleep(1)
        except RuntimeError as e:
            log.warning("  制曲失败: %s" % e)
        time.sleep(1)
    elif wheat > 0:
        log.info("小麦 %s 斤不足100斤，跳过制曲" % wheat)

    # 7. 酒兑换积分（1L=1积分，1L起兑）
    wine_vol = int((client.member_info() or info).get("wine") or 0)
    if wine_vol >= 1:
        if _AUTO_EXCHANGE:
            log.info("酒兑换积分：%sL -> +%s积分" % (wine_vol, wine_vol))
            try:
                r = client.exchange_wine(wine_vol)
                log.info("  兑换成功: %s" % r)
            except RuntimeError as e:
                log.warning("  兑换失败: %s" % e)
            time.sleep(1)
        else:
            log.info("酒 %sL 未兑换（自动兑换已关闭，设置 GARDEN_AUTO_EXCHANGE=1 开启）" % wine_vol)

    # 8. 答题（每天一次）
    if do_daily:
        log.info("花园答题...")
        try:
            questions = client.get_question_task() or []
            todo = [q for q in questions if q.get("id") and q.get("answer")]
            log.info("  共 %d 道题，待答 %d 道" % (len(questions), len(todo)))
            for q in todo:
                qid, answer = q.get("id"), q.get("answer", "")
                log.info("  [%s] %s  答案: %s" % (qid, q.get("title", "")[:25], answer))
                try:
                    time.sleep(3)
                    r = client.answer_results(qid, answer)
                    log.info("    答题成功: %s" % r)
                except RuntimeError as e:
                    log.warning("    答题失败: %s" % e)
                time.sleep(1)
        except RuntimeError as e:
            log.warning("  获取题目失败: %s" % e)

    # 9. 抽奖（每天一次）
    if do_daily:
        log.info("检查抽奖...")
        try:
            chance = client.remain_free_draw_chance()
            free_count = int((chance or {}).get("remainFreeDrawChance", 0))
            log.info("  剩余免费次数: %d" % free_count)
            for i in range(free_count):
                try:
                    r = client.draw()
                    prize = r.get("prize_name") or r.get("name") or r.get("prize") or str(r)
                    log.info("  第%d次: %s" % (i + 1, prize))
                except RuntimeError as e:
                    log.warning("  抽奖失败: %s" % e); break
                time.sleep(2)
        except RuntimeError as e:
            log.warning("  抽奖: %s" % e)

    # 10. 汇总
    try:
        info = client.member_info()
        summary = "积分:%s  水:%s  高粱:%s斤  酒曲:%s块  酒:%sL" % (
            info.get("integration"), info.get("water"),
            info.get("sorghum"), info.get("wine_yeast"), info.get("wine"))
        log.info("任务完成 | " + summary)
        if plot_summary_lines:
            summary += "\n\n📋 地块状态:\n" + "\n".join(plot_summary_lines)
        return summary, min_harvest_secs
    except Exception:
        log.info("今日任务完成")
        if plot_summary_lines:
            return "今日任务完成\n\n📋 地块状态:\n" + "\n".join(plot_summary_lines), min_harvest_secs
        return "今日任务完成", min_harvest_secs


if __name__ == "__main__":
    import json as _json
    import pathlib

    WX_SERVER = os.environ.get("WECHAT_SERVER", "")
    ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
    OCR_SERVER = os.environ.get("OCR_SERVER", "")
    WX_ID_FILTER = os.environ.get("WX_ID", "")
    WXIDS = []
    if WX_ID_FILTER:
        for wxid in WX_ID_FILTER.split("&"):
            wxid = wxid.strip()
            if wxid:
                WXIDS.append((wxid, wxid))
    else:
        # 未指定 WX_ID，从协议服务自动拉取所有有效账号
        try:
            from getCode import WeChatCodeGetter
            getter = WeChatCodeGetter()
            accounts = getter.get_auth_keys()
            for account in accounts:
                wxid = account.get('wxid') or account.get('wx_id') or account.get('deviceId', '')
                remark = account.get('nickname') or account.get('nick_name') or account.get('deviceName') or wxid
                if wxid:
                    WXIDS.append((wxid, remark))
            log.info("从协议服务获取到 %d 个账号" % len(WXIDS))
        except Exception as e:
            log.error("从协议服务获取账号列表失败: %s" % e)
    CACHE_FILE = pathlib.Path(__file__).parent / "xijiutoken.json"

    def load_cache():
        try: return _json.loads(CACHE_FILE.read_text())
        except Exception: return {}

    def save_cache(c):
        CACHE_FILE.write_text(_json.dumps(c, ensure_ascii=False, indent=2))

    def token_valid(token):
        try:
            p = token.split(".")[1]
            p += "=" * (4 - len(p) % 4)
            return _json.loads(base64.b64decode(p).decode()).get("expireTime", 0) > time.time() + 300
        except Exception:
            return False

    def delete_cache_file():
        """删除本地缓存文件 xijiutoken.json"""
        try:
            if CACHE_FILE.exists():
                CACHE_FILE.unlink()
                log.info("✅ 已删除缓存文件: %s" % CACHE_FILE)
                return True
            else:
                log.info("缓存文件不存在，无需删除")
                return True
        except Exception as e:
            log.error("删除缓存文件失败: %s" % e)
            return False

    cache = load_cache()
    notify_lines = []
    all_min_harvests = []  # 收集所有账号的最短收获时间

    def process_account(wxid, force_login=False):
        """处理单个账号，返回 (summary, min_harvest)；登录/加密失败时抛出 RuntimeError"""
        client = GardenClient(ocr_server=OCR_SERVER or None)
        cached_token = "" if force_login else cache.get(wxid, "")

        if cached_token and token_valid(cached_token):
            log.info("使用缓存 token")
            client.set_token(cached_token)
            from wxservice import WxService
            wx = WxService(WX_SERVER)
            client.wxid, client._wx, client._wx_appid = wxid, wx, APPID
            try:
                enc = wx.get_user_encrypt_key(wxid, APPID)
                if enc.get("success"):
                    client.set_crypto(enc["encrypt_key"], enc["iv"], version=enc.get("version", 3))
                    log.info("加密密钥已获取 version=%s" % enc.get("version"))
                else:
                    log.warning("获取加密密钥失败: %s，重新登录" % enc.get("error"))
                    cached_token = ""
            except Exception as e:
                log.warning("获取加密密钥异常: %s，重新登录" % e)
                cached_token = ""

        if not cached_token or not token_valid(cached_token):
            log.info("token 无效或已过期，重新登录...")
            result = client.auto_login(wxid=wxid, server_url=WX_SERVER, ocr_server=OCR_SERVER or None)
            log.info("登录: token=%s  加密=%s" % (
                "已获取" if result.get("token") else "失败",
                "就绪" if result.get("crypto_ready") else "未就绪"))
            if not result.get("token"):
                raise RuntimeError("登录失败")
            cache[wxid] = client.token
            save_cache(cache)

        if not client.crypto:
            raise RuntimeError("加密未就绪")

        today = datetime.now().strftime("%Y-%m-%d")
        do_daily = cache.get(wxid + "_daily") != today
        summary, min_harvest = run(client, do_daily=do_daily)
        if do_daily:
            cache[wxid + "_daily"] = today
            save_cache(cache)
        return summary, min_harvest

    for wxid, remark in WXIDS:
        log.info("=" * 40)
        log.info("处理账号: %s" % remark)

        summary = None
        min_harvest = None
        last_err = None
        for attempt in range(2):
            try:
                summary, min_harvest = process_account(wxid, force_login=(attempt > 0))
                last_err = None
                break
            except RuntimeError as e:
                err_str = str(e)
                last_err = err_str
                if attempt == 0 and ("5001" in err_str or "加密校验失败" in err_str):
                    log.error("账号 %s 5001 加密校验失败: %s" % (wxid, err_str))
                    log.info("删除缓存 + 重新登录 + 重跑...")
                    delete_cache_file()
                    cache.pop(wxid, None)
                    cache.pop(wxid + "_daily", None)
                    time.sleep(2)
                    continue
                log.error("账号 %s 执行异常: %s" % (wxid, err_str))
                break
            except Exception as e:
                last_err = str(e)
                log.error("账号 %s 执行异常: %s" % (wxid, e), exc_info=True)
                break

        if summary is not None:
            notify_lines.append("👤 %s\n%s" % (remark, summary))
            # 收集最短收获时间
            if min_harvest is not None:
                all_min_harvests.append((remark, min_harvest))
        elif last_err is not None:
            notify_lines.append("👤 %s\n❌ 执行异常: %s" % (remark, last_err))

        time.sleep(3)



    # 计算并打印下次执行时间，生成推送用的汇总信息
    harvest_summary = ""
    ql_update_info = ""
    if all_min_harvests:
        overall_min_secs = min(harvest for _, harvest in all_min_harvests)
        log.info("=" * 40)
        log.info("📊 所有账号地块最短剩余收获时间:")
        harvest_lines = []
        for remark, secs in sorted(all_min_harvests, key=lambda x: x[1]):
            next_time = datetime.now() + timedelta(seconds=secs)
            log.info(f"  👤 {remark}: {fmt_remaining_from_seconds(secs)} (预计 {next_time.strftime('%H:%M:%S')} 成熟)")
            harvest_lines.append(f"  👤 {remark}: {fmt_remaining_from_seconds(secs)} (预计 {next_time.strftime('%H:%M:%S')} 成熟)")
        next_run_secs = overall_min_secs + 120
        next_run_time = datetime.now() + timedelta(seconds=next_run_secs)
        cron_schedule = "%d %d * * *" % (next_run_time.minute, next_run_time.hour)
        log.info(f"📅 下次执行时间: {next_run_time.strftime('%Y-%m-%d %H:%M:%S')} (间隔 {fmt_remaining_from_seconds(next_run_secs)})")
        log.info(f"⏰ 生成的 cron 表达式: {cron_schedule}")
        ql_result = update_ql_cron_time(cron_schedule)
        harvest_summary = "📊 所有账号地块最短剩余收获时间:\n" + "\n".join(harvest_lines)
    else:
        next_run_time = datetime.now() + timedelta(seconds=DEFAULT_INTERVAL_SECS)
        cron_schedule = "%d %d * * *" % (next_run_time.minute, next_run_time.hour)
        log.info(f"⚠️ 未获取到地块收获时间，使用默认间隔: {fmt_remaining_from_seconds(DEFAULT_INTERVAL_SECS)}")
        log.info(f"📅 下次执行时间: {next_run_time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"⏰ 生成的 cron 表达式: {cron_schedule}")
        ql_result = update_ql_cron_time(cron_schedule)
        harvest_summary = "⚠️ 未获取到地块收获时间，使用默认间隔"

    if notify_lines:
        # 添加收获时间汇总到推送末尾
        if harvest_summary:
            notify_lines.append(harvest_summary)
        # 添加青龙面板更新信息
        if ql_result:
            notify_lines.append(f"⏰ 下次执行时间: {next_run_time.strftime('%Y-%m-%d %H:%M:%S')}\n✅ 青龙面板定时任务更新成功\n✅ 新执行时间: {cron_schedule}")
        send_notify("习酒协议", "\n\n".join(notify_lines))
