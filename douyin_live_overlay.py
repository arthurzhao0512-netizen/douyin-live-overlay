# -*- coding: utf-8 -*-
"""
抖音弹幕悬浮窗（只显示弹幕）

- 在后台用 Edge 打开你的直播间网页（不出现在任务栏），监听网页自己收到的弹幕，只取「昵称：内容」
- 窗口透明、置顶、鼠标穿透，不会误触；不出现在任务栏 / Alt+Tab
- 右下角托盘图标：右键可以显示/隐藏、移动位置、重新连接、改设置、退出
- Ctrl+Alt+D  显示/隐藏
- Ctrl+Alt+E  进入/退出「移动模式」（可以拖动窗口，退出时自动记住位置）
- 所有设置在同目录的 overlay_config.json 里，改完在托盘菜单点「重新加载设置」

运行：双击 start.bat（不会留下黑窗口）
第一次使用 / 直播设了「部分人可见」：先双击 login.bat 登录一次抖音
出问题时看同目录的 overlay_log.txt
"""
import ctypes
import gzip
import hashlib
import io
import json
import re
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "overlay_config.json")
PROFILE = os.path.join(HERE, "edge_profile")   # 专用的 Edge 登录档案（和你平时用的 Edge 分开）
LOG = os.path.join(HERE, "overlay_log.txt")
EMOJI_DIR = os.path.join(HERE, "emoji_cache")    # 抖音表情图片缓存（只下载一次）
EMOJI_INDEX = os.path.join(EMOJI_DIR, "index.json")
emoji_files = {}   # "[微笑]" -> emoji_cache 里的文件名；礼物图标的 key 是 "gift:礼物id"
avatar_store = {}  # 头像地址的 key -> 图片数据（只放在内存里）

DEFAULTS = {
    "room_url": "",            # 直播间网址，第一次运行时会问一次，之后自动记住
    "x": 40, "y": 600,         # 窗口左下角位置（移动模式里拖动后自动保存）
    "width": 380,              # 窗口宽度（像素）
    "max_lines": 8,            # 最多显示几条
    "expire_seconds": 30,      # 每条弹幕显示多少秒后消失（0 = 不消失）
    "keep_recent": True,       # 开启后：最近几条弹幕一直显示，不会超时消失（托盘菜单可以开关）
    "keep_recent_count": 3,    # 一直显示最近几条
    "font_file": "msyhbd.ttc",   # 字体文件（C:\Windows\Fonts 里的文件名），默认微软雅黑粗体
    "font_size": 13,
    "nick_color": "#7FD4FF",   # 昵称颜色
    "text_color": "#FFFFFF",   # 内容颜色
    "outline_color": "#000000",
    "outline": 2,              # 描边粗细（0 = 不描边）
    "shadow": True,           # 文字下面加一层柔和阴影
    "show_gifts": True,        # 在弹幕里显示礼物「xx 送出 小心心 ×5」（托盘菜单可以开关）
    "gift_color": "#FFC95C",   # 礼物文字颜色
    "companion_path": "",      # 直播伴侣启动程序的路径（留空会自动从开始菜单找）
    "capture_open": False,    # 「直播捕获窗口」：普通窗口，给直播伴侣 / OBS 的「窗口捕获」用
    "capture_bg": "#00FF00",   # 捕获窗口背景色：绿幕 #00FF00（可抠图）/ 深色 #1E1E1E / 黑色 #000000
    "capture_geom": "420x620+200+200",   # 捕获窗口大小和位置（自动记住）
    "capture_wait_live": False,   # true：开播前捕获窗口先保持普通，开播后才藏（万一直播伴侣找不到透明窗口时用）
    "capture_no_taskbar": False,  # 实验：任务栏里不显示两个捕获窗口（只删按钮，不改窗口属性）
    "capture_hidden": False,   # 把捕获窗口藏到屏幕外：自己看不到，直播伴侣照样能捕获（托盘菜单开关）
    "effect_gifts": {"大啤酒": "beer", "美味烧鸡": "chicken"},   # 礼物名 -> 全屏特效（beer=干杯 chicken=开饭）
    "effect_screen": True,     # 在你自己的屏幕上也播放特效（托盘菜单可以开关）
    "effect_screen_size": 1.0, # 你屏幕上特效的大小（占屏幕高度的比例）：0.3 小 / 0.45 中 / 0.65 大 / 1.0 全屏
    "effect_screen_pos": "center",  # 你屏幕上特效的位置：top-right / top-left / bottom-right / bottom-left / center
    "effect_capture_open": False,  # 「特效捕获窗口」：给直播伴侣捕获，铺满画面、抠掉绿色
    "effect_geom": "960x540+240+160",
    "effect_fps": 24,
    "show_viewers": True,      # 在弹幕下面显示「在线人数 + 观众头像」（托盘菜单可以开关）
    "viewers_max": 12,         # 最多显示几个观众头像，多出来的显示 +N
    "viewer_color": "#FFE08A", # 在线人数的文字颜色
    "show_browser": False,     # 调试用：True 时能看到后台网页
    "debug": False,            # 调试用：True 时在日志里记录所有消息类型和 websocket 地址
}

APP_NAME = "抖音弹幕悬浮窗"
CAPTURE_GRACE_S = 15      # 捕获窗口刚打开的这么多秒里先保持普通（不透明、压在最底层），让直播伴侣抓住它，再变透明
OFFLINE_RESET_S = 10      # 下播（弹幕连接断开）超过这么多秒：捕获窗口恢复普通，等下次开播再藏（直播伴侣重开时要找窗口）
COMPANION_GRACE_S = 20    # 发现直播伴侣刚启动后，捕获窗口先恢复普通这么多秒（它启动时要按名字找窗口，透明窗口会被它跳过）
NEW_VIEWER_SECONDS = 10   # 新进来的观众头像亮黄圈多少秒
SILENT_SECONDS = 120   # 弹幕连接开着但这么多秒没有任何消息（被挤掉等情况），就刷新重连

# 快捷键 / 托盘菜单命令
CMD_TOGGLE, CMD_EDIT, CMD_RECONNECT, CMD_RELOAD, CMD_KEEP, CMD_VIEWERS, CMD_GIFTS, CMD_QUIT = 1, 2, 3, 4, 5, 6, 7, 9
CMD_CAPTURE = 10
CMD_CAPTURE_HIDE = 11
CMD_TEST = 12
CMD_NO_TASKBAR = 16
CMD_FX_TEST, CMD_FX_CAPTURE, CMD_FX_SCREEN = 13, 14, 15
CAPTURE_BGS = [("绿幕（可抠图）", "#00FF00"), ("深色", "#1E1E1E"), ("黑色", "#000000")]
FX_SIZES = [("小", 0.3), ("中", 0.45), ("大", 0.65), ("全屏", 1.0)]
FX_POS = [("右上角", "top-right"), ("左上角", "top-left"), ("右下角", "bottom-right"), ("左下角", "bottom-left"),
          ("居中", "center")]


def keep_recent_count(cfg):
    try:
        return max(0, int(cfg.get("keep_recent_count", 3))) if cfg.get("keep_recent") else 0
    except (TypeError, ValueError):
        return 0

msg_q = queue.Queue()   # ("chat", 昵称, 内容) / ("status", 文本, 持续秒数 或 None=一直显示)
cmd_q = queue.Queue()   # 给悬浮窗的命令
stop_event = threading.Event()
reconnect_event = threading.Event()
tray_ref = {"icon": None}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def message_box(text, error=True):
    user32.MessageBoxW(None, text, APP_NAME, 0x10 if error else 0x40)


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            status(f"设置文件 overlay_config.json 格式有误，先用默认设置（{e}）", 10)
    # 旧版设置里 effect_gifts 是礼物名列表（都是干杯特效）：升级成 {礼物名: 特效}，并补上新加的特效
    eg = cfg.get("effect_gifts")
    if isinstance(eg, list):
        new = {name: "beer" for name in eg}
        for k, v in DEFAULTS["effect_gifts"].items():
            new.setdefault(k, v)
        cfg["effect_gifts"] = new
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("保存设置失败：", e)


def status(text, secs=None):
    print(time.strftime("%H:%M:%S"), text)
    msg_q.put(("status", text, secs))
    icon = tray_ref["icon"]
    if icon is not None:
        try:
            icon.title = (APP_NAME + "\n" + text)[:120]
        except Exception:
            pass


# ---------------- 极简 protobuf 解析（不需要 .proto 文件） ----------------
def _varint(b, i):
    r = shift = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << shift
        if c < 0x80:
            return r, i
        shift += 7


def pb(b):
    out, i, n = {}, 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, t = key >> 3, key & 7
        if t == 0:
            v, i = _varint(b, i)
        elif t == 1:
            v, i = b[i:i + 8], i + 8
        elif t == 2:
            ln, i = _varint(b, i)
            v, i = b[i:i + ln], i + ln
        elif t == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError("bad wire type")
        out.setdefault(f, []).append(v)
    return out


def _s(d, f):
    v = d.get(f, [b""])[0]
    return v.decode("utf-8", "ignore") if isinstance(v, bytes) else ""


def _int(d, f, default=0):
    v = d.get(f, [default])[0]
    return v if isinstance(v, int) else default


def parse_gift(raw):
    """WebcastGiftMessage: 2=礼物id 4=每组个数 5=连击次数 7=送礼人 9=连击结束 11=连击组id 15=礼物 29=总个数
       礼物(15): 1=图片(1=地址列表) 12=钻石价值 16=礼物名"""
    try:
        g = pb(raw)
        gift = pb(g[15][0]) if 15 in g else {}
        user = pb(g[7][0]) if 7 in g else {}
        gid = _int(g, 2) or _int(gift, 5)
        name = _s(gift, 16) or _s(gift, 2) or "礼物"
        group = max(1, _int(g, 4, 1))
        repeat = max(1, _int(g, 5, 1))
        total = _int(g, 29) or repeat * group
        urls = []
        if 1 in gift:
            img = pb(gift[1][0])
            urls = [u.decode("utf-8", "ignore") for u in img.get(1, []) if isinstance(u, bytes)]
        uid = str(_int(user, 1)) or _s(user, 38)
        return {"uid": uid, "nick": _s(user, 3), "gift_id": gid, "name": name, "count": total,
                "diamond": _int(gift, 12), "combo_id": _int(g, 11), "urls": urls}
    except Exception:
        return None


def parse_frame(payload, debug=False, methods=None, extra=None):
    """PushFrame(8=payload) -> gzip -> Response(1=messages) -> Message(1=method, 2=payload)
       WebcastChatMessage: 2=user(3=nickname), 3=content
       （字段号已用真实直播间数据验证过）"""
    chats = []
    if not isinstance(payload, (bytes, bytearray)):
        return chats
    try:
        data = pb(payload).get(8, [b""])[0]
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        for m in pb(data).get(1, []):
            msg = pb(m)
            method = _s(msg, 1)
            if methods is not None:
                methods.append(method)
            if debug:
                print("  消息类型:", method)
            if method == "WebcastGiftMessage" and extra is not None:
                g = parse_gift(msg.get(2, [b""])[0])
                if g:
                    extra.setdefault("gifts", []).append(g)
                continue
            if method == "WebcastRoomStatsMessage" and extra is not None:
                rs = pb(msg.get(2, [b""])[0])     # 4 = 「12在线观众」 5 = 12（人少的直播间主要靠这个）
                if "在线" in _s(rs, 4) and isinstance(rs.get(5, [None])[0], int):
                    extra["online"] = rs[5][0]
                continue
            if method == "WebcastRoomUserSeqMessage" and extra is not None:
                seq = pb(msg.get(2, [b""])[0])     # 3 = 当前在线人数
                if isinstance(seq.get(3, [None])[0], int):
                    extra["online"] = seq[3][0]
                continue
            if method != "WebcastChatMessage":
                continue
            chat = pb(msg.get(2, [b""])[0])
            content = _s(chat, 3)
            nick = _s(pb(chat[2][0]), 3) if 2 in chat else ""
            if content:
                chats.append((nick, content))
    except Exception as e:
        if debug:
            print("  解析出错:", e)
    return chats


# ---------------- 把后台 Edge 窗口从任务栏 / Alt+Tab 里藏起来 ----------------
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def child_pids(root_pid):
    """root_pid 的所有子孙进程（浏览器是本程序 -> playwright -> msedge 这样启动的）"""
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    parent = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            parent[e.th32ProcessID] = e.th32ParentProcessID
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    found, changed = {root_pid}, True
    while changed:
        changed = False
        for pid, ppid in parent.items():
            if ppid in found and pid not in found:
                found.add(pid)
                changed = True
    found.discard(root_pid)
    return found


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def hide_browser_windows():
    pids = child_pids(os.getpid())
    if not pids:
        return
    hwnds = []

    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    for h in hwnds:
        st = user32.GetWindowLongW(h, -20)
        new = (st | 0x80 | 0x08000000) & ~0x40000     # TOOLWINDOW | NOACTIVATE, 去掉 APPWINDOW
        if new != st:
            user32.ShowWindow(h, 0)                      # 先隐藏再显示，任务栏才会刷新
            user32.SetWindowLongW(h, -20, new)
            user32.SetWindowPos(h, 0, -32000, -32000, 0, 0, 0x0001 | 0x0004 | 0x0010)  # 移到屏幕外
            user32.ShowWindow(h, 4)                      # SW_SHOWNOACTIVATE


# ---------------- 后台浏览器：监听直播间 websocket ----------------
BROWSER_ARGS = [
    "--mute-audio",
    # 让后台网页的定时器照常跑（弹幕心跳靠它），否则可能被 Edge 节流导致掉线
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    # 省资源：关掉用不到的功能
    "--disable-extensions", "--disable-component-extensions-with-background-pages",
    "--disable-features=Translate,OptimizationHints,MediaRouter,EdgeCollections,msEdgeShopping,"
    "msUndersideButton,AutofillServerCommunication",
    "--no-first-run", "--no-default-browser-check", "--hide-crash-restore-bubble",
]
LEAN_ARGS = [   # 只用于看不见的后台网页（登录窗口要显示二维码，不能用）
    # 实测最省的几项：不用显卡、合并渲染进程、不解码图片（弹幕多时 CPU 约 63% -> 10% 单核）
    "--disable-gpu", "--disable-site-isolation-trials", "--renderer-process-limit=1",
    "--blink-settings=imagesEnabled=false",
]
BG_SIZE = {"width": 400, "height": 300}   # 后台网页窗口尺寸，越小越省

# 注入到直播网页：关掉所有动画、隐藏画面，网页就几乎不用画东西了
PAGE_CSS = ("*,*::before,*::after{animation:none!important;transition:none!important}"
            "video,canvas,img,svg{display:none!important}")
PAGE_INIT = """(() => {
  const add = () => { const s = document.createElement('style'); s.textContent = %s;
                      (document.head || document.documentElement).appendChild(s); };
  if (document.documentElement) add(); else document.addEventListener('DOMContentLoaded', add);
})()""" % json.dumps(PAGE_CSS)


def open_browser(p, visible):
    args = list(BROWSER_ARGS)
    if not visible:
        args += LEAN_ARGS
        args += ["--window-position=-32000,-32000", "--window-size=%d,%d" % (BG_SIZE["width"], BG_SIZE["height"])]
    last = None
    for ch in ("msedge", "chrome"):
        try:
            return p.chromium.launch_persistent_context(
                PROFILE, channel=ch, headless=False, args=args,
                no_viewport=visible, viewport=None if visible else BG_SIZE)
        except Exception as e:
            last = e
    msg = str(last)
    if "ProcessSingleton" in msg or "lock" in msg.lower() or "already" in msg.lower():
        raise RuntimeError("登录档案被占用：login.bat 的登录窗口可能还开着，请先关掉它再试。")
    raise RuntimeError("打不开 Edge 浏览器。请确认电脑上装了 Microsoft Edge。\n详细信息：" + msg[:300])


def load_emoji_index():
    try:
        with open(EMOJI_INDEX, encoding="utf-8") as f:
            idx = json.load(f)
        emoji_files.update({k: v for k, v in idx.items() if os.path.exists(os.path.join(EMOJI_DIR, v))})
    except Exception:
        pass


def download_emojis(ctx, pending, batch=15):
    """每次最多下载 batch 个，避免卡住后台循环"""
    got = 0
    for name in list(pending)[:batch]:
        urls = pending.pop(name)
        for u in urls:
            try:
                resp = ctx.request.get(u, timeout=10000)
                if not resp.ok:
                    continue
                body = resp.body()
                fn = hashlib.md5(name.encode("utf-8")).hexdigest() + ".img"
                os.makedirs(EMOJI_DIR, exist_ok=True)
                with open(os.path.join(EMOJI_DIR, fn), "wb") as f:
                    f.write(body)
                emoji_files[name] = fn
                got += 1
                break
            except Exception:
                continue
    if got:
        try:
            with open(EMOJI_INDEX, "w", encoding="utf-8") as f:
                json.dump(emoji_files, f, ensure_ascii=False)
        except Exception:
            pass
        msg_q.put(("avatars", got))       # 让悬浮窗重画，新下载的图马上显示
        if not pending:
            print(f"表情 / 礼物图标下载完成，共 {len(emoji_files)} 个")


def download_avatars(ctx, pending, batch=10):
    got = 0
    for key in list(pending)[:batch]:
        urls = pending.pop(key)
        for u in urls:
            try:
                resp = ctx.request.get(u, timeout=8000)
                if resp.ok:
                    avatar_store[key] = resp.body()
                    got += 1
                    break
            except Exception:
                continue
    if got:
        msg_q.put(("avatars", got))


def has_login(ctx):
    try:
        return any(c["name"] == "sessionid" and c["value"] for c in ctx.cookies("https://www.douyin.com"))
    except Exception:
        return False


def fetcher(cfg):
    debug = cfg["debug"]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        status("缺少 playwright 组件，请用 start.bat 启动（会自动安装）")
        return
    try:
        with sync_playwright() as p:
            ctx = open_browser(p, visible=cfg["show_browser"])
            if not cfg["show_browser"]:
                hide_browser_windows()
            ctx.add_init_script(PAGE_INIT)

            # 不加载视频/图片/字体，省网速和 CPU（已验证不影响弹幕）
            def route(r):
                u = r.request.url
                if r.request.resource_type in ("media", "image", "font") or ".flv" in u or ".m3u8" in u:
                    r.abort()
                else:
                    r.continue_()
            ctx.route("**/*", route)

            st = {"ws_open": False, "last_change": time.time(), "got_any": False, "crashed": False,
                  "ws": None, "last_msg": time.time(), "silent_count": 0}
            recent_methods = {}

            def on_close(ws):
                if ws is not st["ws"]:       # 刷新网页时旧连接晚到的「断开」，不用管
                    return
                st["ws_open"] = False
                msg_q.put(("live", False))
                st["last_change"] = time.time()
                if not stop_event.is_set():
                    status("弹幕连接断开了，正在重连…", 6)

            def on_frame(payload):
                methods, extra = [], {}
                chats = parse_frame(payload, debug, methods, extra)
                if "online" in extra:
                    if extra["online"] != st.get("last_online"):
                        st["last_online"] = extra["online"]
                        print(time.strftime("%H:%M:%S"), "抖音推送在线人数：", extra["online"])
                    msg_q.put(("online", extra["online"]))
                for g in extra.get("gifts", []):
                    key = "gift:%s" % g["gift_id"]
                    if g["urls"] and key not in emoji_files:
                        emoji_pending[key] = g["urls"]       # 礼物图标和表情一样下载缓存
                    print(f"[礼物] {g['nick']} 送出 {g['name']} ×{g['count']}")
                    msg_q.put(("gift", g))
                if methods:                  # 收到任何消息（在线人数、点赞等）都说明连接是活的
                    st["last_msg"] = time.time()
                    st["silent_count"] = 0
                    for m in methods:
                        recent_methods[m] = recent_methods.get(m, 0) + 1
                for nick, text in chats:
                    print(f"[弹幕] {nick}：{text}")
                    msg_q.put(("chat", nick, text))

            def on_ws(ws):
                if debug:
                    print("websocket:", ws.url[:120])
                if "webcast" in ws.url and "push" in ws.url:
                    st["ws"] = ws
                    st["ws_open"] = True
                    st["got_any"] = True
                    st["last_change"] = st["last_msg"] = time.time()
                    status("弹幕已连接", 4)
                    msg_q.put(("live", True))      # 弹幕连接只有开播时才连得上
                    ws.on("framereceived", on_frame)
                    ws.on("close", lambda *_: on_close(ws))
            def on_crash(*_):
                st["crashed"] = True

            # 网页打开时会自己请求一份「抖音表情列表」，截下来，图片下载到 emoji_cache
            emoji_pending = {}

            avatar_pending = {}

            def on_audience(r):
                """网页定时请求的「在线观众榜」：取出昵称和头像"""
                try:
                    data = (r.json() or {}).get("data") or {}
                except Exception:
                    return
                me_user = (data.get("self_info") or {}).get("user") or {}
                me = str(me_user.get("id_str") or me_user.get("id") or "")
                viewers = []
                self_in = False
                for rk in data.get("ranks") or []:
                    u = rk.get("user") or {}
                    uid = str(u.get("id_str") or u.get("id") or "")
                    if uid and uid == me:
                        self_in = True
                    if not uid or uid == me:
                        continue
                    thumb = u.get("avatar_thumb") or {}
                    urls = thumb.get("url_list") or []
                    key = thumb.get("uri") or (urls[0] if urls else "")
                    name = u.get("nickname") or ""
                    viewers.append({"id": uid + "|" + name, "name": name, "avatar": key, "urls": urls})
                # 抖音对没有权限的人会把昵称打码成「张***」、id 统一成 111111
                masked = bool(viewers) and all(v["name"].endswith("***") for v in viewers)
                meta = {"has_more": bool(data.get("has_more")), "self_in": self_in, "total": data.get("total"),
                        "invisible": int(data.get("invisible_total") or 0),
                        "user_count_desc": data.get("user_count_desc"), "t": time.time()}
                sig = (len(viewers), masked, meta["has_more"], self_in, meta["total"], meta["user_count_desc"])
                if sig != st.get("last_aud"):
                    st["last_aud"] = sig
                    print(time.strftime("%H:%M:%S"), "在线观众榜：", len(viewers), "人（不含自己）",
                          "| 自己在榜上" if self_in else "| 自己不在榜上", "| 打码" if masked else "",
                          "| has_more", meta["has_more"], "| total", meta["total"], "| desc", meta["user_count_desc"],
                          "| 我是", me_user.get("nickname"))
                if not masked:      # 打码时头像也是马赛克，不用下载
                    for v in viewers[:60]:
                        if v["avatar"] and v["urls"] and v["avatar"] not in avatar_store:
                            avatar_pending[v["avatar"]] = v["urls"]
                msg_q.put(("viewers", viewers, masked, meta))
                if debug:
                    print("在线观众榜：", len(viewers), "人", "（已打码）" if masked else "")

            def on_response(r):
                if "/webcast/ranklist/audience/" in r.url:
                    on_audience(r)
                    return
                if "/web/emoji/list" not in r.url:
                    return
                try:
                    data = r.json()
                except Exception:
                    return
                for e in data.get("emoji_list") or []:
                    name = e.get("display_name")
                    urls = (e.get("emoji_url") or {}).get("url_list") or []
                    if name and urls and name not in emoji_files:
                        emoji_pending[name] = urls
                if emoji_pending:
                    print(f"收到抖音表情列表，需要下载 {len(emoji_pending)} 个表情图片")

            def new_page(old=None):
                pg = ctx.new_page()
                pg.on("websocket", on_ws)
                pg.on("crash", on_crash)
                pg.on("response", on_response)
                for o in list(ctx.pages):
                    if o is not pg:
                        try:
                            o.close()
                        except Exception:
                            pass
                return pg

            page = new_page()

            logged_in = has_login(ctx)
            if not logged_in:
                print("还没登录抖音。直播如果设了「部分人可见」，必须先双击 login.bat 登录一次。")

            def go():
                status("正在打开直播间…")
                st["last_change"] = st["last_msg"] = time.time()
                st["ws"], st["ws_open"] = None, False
                try:
                    page.goto(cfg["room_url"], wait_until="commit", timeout=60000)
                except Exception as e:
                    print("打开网页时出错（会自动重试）：", str(e)[:200])

            go()
            WAIT = 40          # 多少秒没连上就提示并刷新
            SILENT = SILENT_SECONDS
            n = 0
            while not stop_event.is_set():
                page.wait_for_timeout(1000)
                n += 1
                if not cfg["show_browser"] and (n < 30 or n % 5 == 0):
                    hide_browser_windows()       # Edge 偶尔会新开窗口，持续检查
                if emoji_pending:
                    download_emojis(ctx, emoji_pending)
                if avatar_pending:
                    download_avatars(ctx, avatar_pending)
                if st["crashed"]:
                    st["crashed"] = False
                    st["ws_open"] = False
                    status("后台网页崩溃了，正在重新打开…", 6)
                    page = new_page()
                    go()
                    continue
                if reconnect_event.is_set():
                    reconnect_event.clear()
                    go()
                    continue
                if not st["ws_open"] and time.time() - st["last_change"] > WAIT:
                    if st["got_any"]:
                        status("弹幕断开一段时间了，刷新网页重连…", 6)
                    elif logged_in:
                        status("还没收到弹幕：直播间可能没开播。开播后会自动连上（每 40 秒重试）")
                    else:
                        status("还没收到弹幕：可能没开播，或直播仅部分人可见——请先退出本程序，运行 login.bat 登录")
                    go()
                elif st["ws_open"] and time.time() - st["last_msg"] > SILENT:
                    # 连接开着却一直没有任何消息：多半是同一个抖音账号在别处看直播，把这里「挤掉」了
                    st["silent_count"] += 1
                    print("连接没断但", SILENT, "秒没收到任何消息。之前收到过的消息类型：",
                          dict(sorted(recent_methods.items(), key=lambda kv: -kv[1])[:8]))
                    recent_methods.clear()
                    if st["silent_count"] >= 2:
                        status("连续收不到弹幕：同一个抖音账号在手机等地方看直播会把这里挤掉。"
                               "建议用小号运行 login.bat 登录。正在继续重试…")
                    else:
                        status("很久没收到消息，可能被挤掉了，刷新网页重连…", 8)
                    go()
            try:
                ctx.close()
            except Exception:
                pass
    except RuntimeError as e:
        status(str(e))
    except Exception as e:
        if stop_event.is_set():
            return
        if "closed" in str(e).lower() or "Target" in str(e):
            status("后台浏览器被关掉了，弹幕停止。请在托盘菜单里退出后重新运行 start.bat")
        else:
            traceback.print_exc()
            status("后台出错了：" + str(e)[:120] + "（请退出后重新运行 start.bat）")


# ---------------- 全局快捷键（Windows RegisterHotKey） ----------------
def hotkeys():
    MOD = 0x0001 | 0x0002 | 0x4000  # Alt | Ctrl | NoRepeat
    bad = []
    if not user32.RegisterHotKey(None, CMD_TOGGLE, MOD, ord("D")):
        bad.append("Ctrl+Alt+D")
    if not user32.RegisterHotKey(None, CMD_EDIT, MOD, ord("E")):
        bad.append("Ctrl+Alt+E")
    if bad:
        status("快捷键 " + "、".join(bad) + " 被别的程序占用了，可以用右下角托盘菜单代替", 15)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            cmd_q.put(msg.wParam)


# ---------------- 右下角托盘图标 ----------------
def make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 8, 60, 46), radius=10, fill=(254, 44, 85, 255))   # 对话气泡
    d.polygon([(16, 44), (30, 44), (14, 58)], fill=(254, 44, 85, 255))
    for i, w in enumerate((40, 30)):                                           # 两行“弹幕”
        y = 18 + i * 12
        d.rounded_rectangle((12, y, 12 + w, y + 6), radius=3, fill=(255, 255, 255, 255))
    return img


def run_tray():
    try:
        import pystray
    except ImportError:
        status("缺少托盘组件 pystray，请用 start.bat 启动（会自动安装）。现在可以用快捷键操作。", 15)
        return

    def send(c):
        return lambda icon, item: cmd_q.put(c)

    def open_settings(icon, item):
        subprocess.Popen(["notepad.exe", CONFIG])

    def open_log(icon, item):
        subprocess.Popen(["notepad.exe", LOG])

    menu = pystray.Menu(
        pystray.MenuItem("显示 / 隐藏弹幕  (Ctrl+Alt+D)", send(CMD_TOGGLE), default=True),
        pystray.MenuItem("移动位置  (Ctrl+Alt+E)", send(CMD_EDIT)),
        pystray.MenuItem(lambda item: "一直显示最近 %s 条" % (tray_ref.get("cfg") or {}).get("keep_recent_count", 3),
                         send(CMD_KEEP), checked=lambda item: bool((tray_ref.get("cfg") or {}).get("keep_recent"))),
        pystray.MenuItem("直播捕获窗口（给直播伴侣捕获）", send(CMD_CAPTURE),
                         checked=lambda item: bool((tray_ref.get("cfg") or {}).get("capture_open"))),
        pystray.MenuItem("特效捕获窗口（礼物全屏特效给直播伴侣）", send(CMD_FX_CAPTURE),
                         checked=lambda item: bool((tray_ref.get("cfg") or {}).get("effect_capture_open"))),
        pystray.MenuItem("我屏幕上特效的大小", pystray.Menu(*[
            pystray.MenuItem(label, (lambda v: lambda i, it: cmd_q.put(("fx_size", v)))(v),
                             checked=(lambda v: lambda item: abs(float((tray_ref.get("cfg") or {}).get("effect_screen_size", 1.0)) - v) < 0.01)(v),
                             radio=True)
            for label, v in FX_SIZES])),
        pystray.MenuItem("我屏幕上特效的位置", pystray.Menu(*[
            pystray.MenuItem(label, (lambda v: lambda i, it: cmd_q.put(("fx_pos", v)))(v),
                             checked=(lambda v: lambda item: (tray_ref.get("cfg") or {}).get("effect_screen_pos", "center") == v)(v),
                             radio=True)
            for label, v in FX_POS])),
        pystray.MenuItem("我的屏幕也播放礼物特效", send(CMD_FX_SCREEN),
                         checked=lambda item: bool((tray_ref.get("cfg") or {}).get("effect_screen", True))),
        pystray.MenuItem("测试大啤酒特效", lambda i, it: cmd_q.put(("fx_test", "beer"))),
        pystray.MenuItem("测试美味烧鸡特效", lambda i, it: cmd_q.put(("fx_test", "chicken"))),
        pystray.MenuItem("任务栏里不显示捕获窗口（实验）", send(CMD_NO_TASKBAR),
                         checked=lambda item: bool((tray_ref.get("cfg") or {}).get("capture_no_taskbar"))),
        pystray.MenuItem("藏起捕获窗口（原地变透明，直播照常）", send(CMD_CAPTURE_HIDE),
                         checked=lambda item: bool((tray_ref.get("cfg") or {}).get("capture_hidden"))),
        pystray.MenuItem("捕获窗口背景色", pystray.Menu(*[
            pystray.MenuItem(label, (lambda col: lambda i, it: cmd_q.put(("capture_bg", col)))(col),
                             checked=(lambda col: lambda item: (tray_ref.get("cfg") or {}).get("capture_bg", "").upper() == col)(col),
                             radio=True)
            for label, col in CAPTURE_BGS])),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("显示礼物", send(CMD_GIFTS),
                         checked=lambda item: bool((tray_ref.get("cfg") or {}).get("show_gifts"))),
        pystray.MenuItem("显示在线观众", send(CMD_VIEWERS),
                         checked=lambda item: bool((tray_ref.get("cfg") or {}).get("show_viewers"))),
        pystray.MenuItem("发一条测试弹幕（只在本机显示）", send(CMD_TEST)),
        pystray.MenuItem("重新连接直播间", lambda i, it: reconnect_event.set()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开设置文件", open_settings),
        pystray.MenuItem("重新加载设置", send(CMD_RELOAD)),
        pystray.MenuItem("查看运行日志", open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", send(CMD_QUIT)),
    )
    icon = pystray.Icon("douyin_live_overlay", make_icon_image(), APP_NAME, menu)
    tray_ref["icon"] = icon
    icon.run()


# ---------------- 文字渲染（Pillow：抗锯齿 + 平滑描边 + 柔和阴影） ----------------
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
# 主字体里没有的字（emoji、花体字母等）依次用这些字体补： (文件, ttc 序号, 是否彩色 emoji)
FALLBACK_FONTS = [("seguiemj.ttf", 0, True), ("seguisym.ttf", 0, False),
                  ("cambria.ttc", 1, False), ("msyh.ttc", 0, False), ("simhei.ttf", 0, False)]


EMOJI_RE = re.compile(r"\[[^\[\]\s]{1,10}\]")


def _font_path(name):
    return name if os.path.isabs(name) else os.path.join(FONT_DIR, name)


class TextRenderer:
    def __init__(self, cfg, dpi, small=False):
        from PIL import ImageFont
        self.cfg = cfg
        self.scale = dpi / 96.0
        self.px = max(8, round(float(cfg["font_size"]) * dpi / 72.0))
        self.stroke = max(0, round(float(cfg["outline"]) * self.scale))
        self.shadow = bool(cfg.get("shadow", True))
        self.fonts = []          # [(字体, 是否彩色emoji, “缺字”时的字形)]
        for name, idx, color in [(cfg.get("font_file") or "msyhbd.ttc", 0, False)] + FALLBACK_FONTS:
            try:
                f = ImageFont.truetype(_font_path(name), self.px, index=idx)
            except Exception:
                continue
            self.fonts.append((f, color, self._mask(f, "\uffff")))
        if not self.fonts:
            raise RuntimeError("找不到字体文件：" + str(cfg.get("font_file")))
        a, d = self.fonts[0][0].getmetrics()
        self.ascent = a
        self.line_h = round((a + d) * 1.08) + self.stroke
        self.pad = round(6 * self.scale) + self.stroke
        self.gap = round(4 * self.scale)
        self.esz = round(self.px * 1.3)           # 抖音表情图片大小
        self.egap = max(1, round(self.scale))
        self._pick_cache, self._w_cache, self._emoji_cache, self._avatar_cache = {}, {}, {}, {}
        if not small:   # 观众栏昵称用的小号字
            sc = dict(cfg)
            sc["font_size"] = float(cfg["font_size"]) * 0.75
            sc["outline"] = max(1, float(cfg["outline"]) * 0.75) if float(cfg["outline"]) else 0
            self.small = TextRenderer(sc, dpi, small=True)

    @staticmethod
    def _mask(f, ch):
        try:
            m = f.getmask(ch)
            return bytes(m) + repr(m.size).encode()
        except Exception:
            return None

    def _pick(self, ch):
        """给这个字选一个真的有这个字的字体"""
        r = self._pick_cache.get(ch)
        if r is None:
            r = 0
            if not ch.isspace():
                for i, (f, _, notdef) in enumerate(self.fonts):
                    m = self._mask(f, ch)
                    if m is not None and m != notdef:
                        r = i
                        break
            self._pick_cache[ch] = r
        return r

    def _width(self, fi, s):
        w = self._w_cache.get((fi, s))
        if w is None:
            w = self._w_cache[(fi, s)] = self.fonts[fi][0].getlength(s)
        return w

    @staticmethod
    def _clusters(text):
        """拆成一个个「字」。组合 emoji（带肤色、👨‍👩‍👧 这种）没法合成一个图，只保留第一个 emoji，
           变体符/肤色/连接符直接去掉，免得显示成方块"""
        out, skip_next = [], False
        for ch in text:
            o = ord(ch)
            if o in (0xFE0F, 0xFE0E, 0x20E3) or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F:
                continue
            if o == 0x200D:            # 连接符：后面连着的那个 emoji 也不要了
                skip_next = True
                continue
            if skip_next:
                skip_next = False
                continue
            if out and 0x0300 <= o <= 0x036F:   # 组合附加符号跟着前一个字
                out[-1] += ch
            else:
                out.append(ch)
        return out

    def _emoji(self, name):
        """抖音表情 [xxx] 的图片（已缩放好），没有就返回 None"""
        im = self._emoji_cache.get(name)
        if im is None:
            fn = emoji_files.get(name)
            if not fn:
                return None            # 还没下载到：先不缓存，下载好以后会自动显示
            try:
                from PIL import Image
                im = Image.open(os.path.join(EMOJI_DIR, fn)).convert("RGBA")
                im = im.resize((self.esz, self.esz), Image.LANCZOS)
            except Exception:
                im = False
            self._emoji_cache[name] = im
        return im or None

    @staticmethod
    def _clean(text):
        return text.replace("\n", " ")

    def _layout_text(self, text, color, x, indent, right, runs, line):
        for cl in self._clusters(text):
            fi = self._pick(cl[0])
            w = self._width(fi, cl)
            if x + w > right and x > indent + 0.5:
                line, x = line + 1, indent
            last = runs[-1] if runs else None
            if last and last[3] == fi and last[0] == line and last[4] == color and abs(last[5] - x) < 0.01:
                runs[-1] = (line, last[1], last[2] + cl, fi, color, x + w)
            else:
                runs.append((line, x, cl, fi, color, x + w))
            x += w
        return x, line

    def _layout(self, text, color, x, indent, right, runs, line):
        """把 text 从 (x, line) 开始排，超过 right 就换行到 indent。返回新的 (x, line)
           抖音表情 [xxx] 换成图片（字体序号记为 -1）"""
        text = text.replace("\n", " ")
        pos = 0
        for m in EMOJI_RE.finditer(text):
            name = m.group(0)
            if self._emoji(name) is None:
                continue
            x, line = self._layout_text(text[pos:m.start()], color, x, indent, right, runs, line)
            w = self.esz + 2 * self.egap
            if x + w > right and x > indent + 0.5:
                line, x = line + 1, indent
            runs.append((line, x + self.egap, name, -1, color, x + w))
            x += w
            pos = m.end()
        return self._layout_text(text[pos:], color, x, indent, right, runs, line)

    def render(self, rows, width, edit):
        """rows: [("text", 文字, 颜色) 或 ("chat", 昵称, 内容)]，返回 RGBA 图片"""
        from PIL import Image, ImageDraw, ImageFilter
        cfg, pad = self.cfg, self.pad
        right = width - pad
        avail = right - pad
        runs, line, row_start_lines = [], 0, []
        for kind, a, b in rows:
            row_start_lines.append(line)
            if kind == "text":
                _, line = self._layout(a, b, pad, pad, right, runs, line)
            elif kind == "gift":              # a = 礼物信息
                x = pad
                key = "gift:%s" % a["gift_id"]
                if self._emoji(key) is not None:
                    w = self.esz + 2 * self.egap
                    runs.append((line, x + self.egap, key, -1, cfg["gift_color"], x + w))
                    x += w + self.egap
                x, line = self._layout_text(self._clean(a["nick"] or "匿名"), cfg["nick_color"], x, pad, right, runs, line)
                x, line = self._layout_text(" 送出 ", cfg["text_color"], x, pad, right, runs, line)
                x, line = self._layout_text("%s ×%d" % (a["name"], a["count"]), cfg["gift_color"], x, pad, right, runs, line)
            else:
                nick = (a or "匿名") + "："
                x, l2 = self._layout(nick, cfg["nick_color"], pad, pad, right, runs, line)
                if l2 == line and x - pad <= avail * 0.45:   # 昵称不长：内容接在后面，换行后和内容对齐
                    _, line = self._layout(b, cfg["text_color"], x, x, right, runs, line)
                else:                                       # 昵称太长：内容另起一行
                    _, line = self._layout(b, cfg["text_color"], pad, pad, right, runs, l2 + 1)
            line += 1
        # 每一行的 y 坐标：条与条之间多加一点间距
        line_y, y = {}, pad
        starts = set(row_start_lines)
        for ln in range(line):
            if ln in starts and ln > 0:
                y += self.gap
            line_y[ln] = y
            y += self.line_h
        height = max(1, y + pad)

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if edit:
            ImageDraw.Draw(img).rounded_rectangle(
                (0, 0, width - 1, height - 1), radius=round(8 * self.scale),
                fill=(30, 30, 30, 200), outline=(255, 216, 102, 255), width=max(1, round(self.scale)))
        if not runs:
            return img

        def draw_pass(layer, stroke_only):
            d = ImageDraw.Draw(layer)
            for ln, x, s, fi, color, _ in runs:
                if fi == -1:              # 抖音表情图片
                    if not stroke_only:
                        top = line_y[ln] + (self.line_h - self.esz) // 2 + self.stroke // 2
                        layer.alpha_composite(self._emoji(s), (round(x), max(0, top)))
                    continue
                f, is_color, _ = self.fonts[fi]
                pos = (x, line_y[ln] + self.stroke + self.ascent)
                if stroke_only:
                    if self.stroke:
                        d.text(pos, s, font=f, fill=cfg["outline_color"], anchor="ls",
                               stroke_width=self.stroke, stroke_fill=cfg["outline_color"])
                elif is_color:
                    d.text(pos, s, font=f, anchor="ls", embedded_color=True)
                else:
                    d.text(pos, s, font=f, fill=color, anchor="ls")

        outline = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_pass(outline, True)
        if self.shadow:   # 柔和阴影：描边的模糊版本，往右下偏一点
            r = max(1, round(1.5 * self.scale))
            a = outline.getchannel("A").filter(ImageFilter.GaussianBlur(r))
            a = a.point(lambda v: v * 0.75)
            sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
            sh.putalpha(a)
            off = max(1, round(self.scale))
            img.alpha_composite(sh, (off, off))
        img.alpha_composite(outline)
        fill = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_pass(fill, False)
        img.alpha_composite(fill)
        return img


    # ---------- 观众栏 ----------
    def _fit(self, text, maxw):
        """把文字截短到 maxw 宽，返回 [(x, 字, 字体序号)] 和总宽度"""
        out, x = [], 0.0
        cls = self._clusters(text)
        ell_w = self._width(0, "…")
        for i, cl in enumerate(cls):
            fi = self._pick(cl[0])
            w = self._width(fi, cl)
            rest = i < len(cls) - 1
            if x + w + (ell_w if rest else 0) > maxw:
                out.append((x, "…", 0))
                x += ell_w
                break
            out.append((x, cl, fi))
            x += w
        return out, x

    def _draw_runs(self, layer_o, layer_f, runs, x0, baseline, color):
        from PIL import ImageDraw
        do, df = ImageDraw.Draw(layer_o), ImageDraw.Draw(layer_f)
        oc = self.cfg["outline_color"]
        for x, s, fi in runs:
            f, is_color, _ = self.fonts[fi]
            pos = (x0 + x, baseline)
            if self.stroke:
                do.text(pos, s, font=f, fill=oc, anchor="ls", stroke_width=self.stroke, stroke_fill=oc)
            if is_color:
                df.text(pos, s, font=f, anchor="ls", embedded_color=True)
            else:
                df.text(pos, s, font=f, fill=color, anchor="ls")

    def _avatar(self, key, name, size, ring):
        from PIL import Image, ImageDraw
        ck = (key, size, ring)
        im = self._avatar_cache.get(ck)
        if im is not None:
            return im
        data = avatar_store.get(key)
        base = None
        if data:
            try:
                base = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
            except Exception:
                base = None
        if base is None:                         # 还没下载到：灰色圆 + 名字第一个字
            base = Image.new("RGBA", (size, size), (90, 90, 90, 255))
            ch = (name or "?")[0]
            fi = self._pick(ch)
            ImageDraw.Draw(base).text((size / 2, size / 2), ch, font=self.fonts[fi][0], fill=(255, 255, 255, 255),
                                      anchor="mm")
        ss = 4                                   # 4 倍超采样画圆，边缘平滑
        mask = Image.new("L", (size * ss, size * ss), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * ss - 1, size * ss - 1), fill=255)
        mask = mask.resize((size, size), Image.LANCZOS)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(base, (0, 0), mask)
        rw = max(1, round((2.5 if ring else 1.2) * self.scale))
        ring_img = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        col = (255, 214, 64, 255) if ring else (255, 255, 255, 200)
        ImageDraw.Draw(ring_img).ellipse((0, 0, size * ss - 1, size * ss - 1), outline=col, width=rw * ss)
        out.alpha_composite(ring_img.resize((size, size), Image.LANCZOS))
        if data:                                 # 没下载到的不缓存，下载好以后自动换成真头像
            if len(self._avatar_cache) > 400:
                self._avatar_cache.clear()
            self._avatar_cache[ck] = out
        return out

    def render_viewers(self, width, online, viewers, masked, highlight, edit):
        """在线人数 + 观众头像。没有任何数据时返回 None"""
        from PIL import Image, ImageDraw, ImageFilter
        if online is None and not viewers:
            return None
        cfg, pad = self.cfg, self.pad
        small = self.small
        n_online = online if online is not None else len(viewers)
        lines = [("在线 %s 人" % n_online, cfg.get("viewer_color", "#FFE08A"))]
        if masked:
            lines.append(("观众信息被抖音隐藏，需用主播账号登录", "#B0B0B0"))
        asz = round(self.px * 2.0)
        cell = max(asz + round(10 * self.scale), round(asz * 1.55))
        avail = width - 2 * pad
        cols = max(1, int(avail // cell))
        show = [] if masked else list(viewers)
        vmax = max(0, int(cfg.get("viewers_max", 12)))
        extra = 0
        if len(show) > vmax:
            extra = len(show) - (vmax - 1) if vmax > 0 else len(show)
            show = show[:max(0, vmax - 1)]
        cells = len(show) + (1 if extra else 0)
        rows = (cells + cols - 1) // cols if cells else 0
        name_h = small.line_h
        row_h = asz + round(3 * self.scale) + name_h
        head_h = len(lines) * self.line_h
        height = pad + head_h + (round(4 * self.scale) + rows * row_h if rows else 0) + pad
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if edit:
            ImageDraw.Draw(img).rounded_rectangle(
                (0, 0, width - 1, height - 1), radius=round(8 * self.scale),
                fill=(30, 30, 30, 200), outline=(255, 216, 102, 255), width=max(1, round(self.scale)))
        lo = Image.new("RGBA", img.size, (0, 0, 0, 0))
        lf = Image.new("RGBA", img.size, (0, 0, 0, 0))
        y = pad
        for text, color in lines:
            runs, _ = self._fit(text, avail)
            self._draw_runs(lo, lf, runs, pad, y + self.stroke + self.ascent, color)
            y += self.line_h
        y += round(4 * self.scale)
        av = Image.new("RGBA", img.size, (0, 0, 0, 0))
        for i in range(cells):
            r, c = divmod(i, cols)
            cx = pad + c * cell + cell // 2
            top = y + r * row_h
            if i < len(show):
                v = show[i]
                av.alpha_composite(self._avatar(v["avatar"], v["name"], asz, v["id"] in highlight),
                                   (cx - asz // 2, top))
                label, color = v["name"], cfg["text_color"]
            else:
                circ = Image.new("RGBA", (asz, asz), (0, 0, 0, 0))
                ImageDraw.Draw(circ).ellipse((0, 0, asz - 1, asz - 1), fill=(60, 60, 60, 220))
                av.alpha_composite(circ, (cx - asz // 2, top))
                runs, w = small._fit("+%d" % extra, asz)
                small._draw_runs(lo, lf, runs, cx - w / 2, top + asz / 2 + small.ascent / 2 - small.stroke, "#FFFFFF")
                continue
            runs, w = small._fit(label, cell - round(2 * self.scale))
            small._draw_runs(lo, lf, runs, cx - w / 2, top + asz + round(3 * self.scale) + small.stroke + small.ascent,
                             color)
        if self.shadow:
            rr = max(1, round(1.5 * self.scale))
            a = lo.getchannel("A").filter(ImageFilter.GaussianBlur(rr)).point(lambda v: v * 0.75)
            sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
            sh.putalpha(a)
            off = max(1, round(self.scale))
            img.alpha_composite(sh, (off, off))
        img.alpha_composite(av)
        img.alpha_composite(lo)
        img.alpha_composite(lf)
        return img


# ---------------- 逐像素半透明窗口（UpdateLayeredWindow） ----------------
class _BLEND(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


_u32 = ctypes.WinDLL("user32")
_gdi = ctypes.WinDLL("gdi32")
_u32.GetDC.restype = wintypes.HDC
_u32.GetDC.argtypes = [wintypes.HWND]
_u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                     ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                     wintypes.DWORD, ctypes.POINTER(_BLEND), wintypes.DWORD]
_gdi.CreateCompatibleDC.restype = wintypes.HDC
_gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi.CreateDIBSection.restype = wintypes.HBITMAP
_gdi.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BIH), wintypes.UINT,
                                  ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi.SelectObject.restype = wintypes.HGDIOBJ
_gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi.DeleteDC.argtypes = [wintypes.HDC]


def update_layered(hwnd, img, x, y):
    """把 RGBA 图片贴到窗口上（带逐像素透明度）"""
    from PIL import Image, ImageChops
    w, h = img.size
    data = img.convert("RGBa").tobytes("raw", "BGRa")      # 预乘 alpha 的 BGRA（Pillow 内置，很快）
    screen = _u32.GetDC(None)
    mem = _gdi.CreateCompatibleDC(screen)
    bih = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = _gdi.CreateDIBSection(screen, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
    try:
        ctypes.memmove(bits, data, len(data))
        old = _gdi.SelectObject(mem, bmp)

        def ulw():
            return _u32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wintypes.POINT(x, y)),
                                            ctypes.byref(wintypes.SIZE(w, h)), mem,
                                            ctypes.byref(wintypes.POINT(0, 0)), 0,
                                            ctypes.byref(_BLEND(0, 0, 255, 1)), 2)
        ok = ulw()
        if not ok:
            # Tk 有时会给窗口设「整体透明度」，和逐像素透明冲突；把分层属性关掉再打开就能重置
            st = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, st & ~0x80000)
            user32.SetWindowLongW(hwnd, -20, st | 0x80000)
            ok = ulw()
        _gdi.SelectObject(mem, old)
        return ok
    finally:
        _gdi.DeleteObject(bmp)
        _gdi.DeleteDC(mem)
        _u32.ReleaseDC(None, screen)


# ---------------- 悬浮窗 ----------------
class Overlay:
    def __init__(self, cfg):
        import tkinter as tk
        self.cfg = cfg
        self.root = tk.Tk()
        self.root.withdraw()       # 先完全不显示：要让两个捕获窗口最先出现（见下面）
        # 一出生就是「工具窗口」：直播伴侣按名字找不到捕获窗口时，会退而求其次抓同一个程序的其他窗口，
        # 工具窗口会被它跳过，这样就不会抓到这个悬浮窗（变成一条细线）
        self.root.attributes("-toolwindow", True)
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.dpi = self.root.winfo_fpixels("1i")
        self.lines = deque()
        self.online = None          # 在线人数
        self.viewers = []           # 在线观众 [{"id", "name", "avatar"}]
        self.viewers_masked = False
        self.viewers_meta = None    # 观众榜的附加信息（是否还有更多、隐身人数、什么时候拿到的）
        self.seen_ids = None        # 见过的观众（第一次拿到名单时不算「新来的」）
        self.new_until = {}         # 新来的观众 -> 黄圈显示到什么时候
        self.companion_since = None # 最近一次发现直播伴侣启动的时间（启动后的一段时间捕获窗口要保持普通）
        self.room_live = False      # 已经开播（弹幕连接接通过）
        self._offline_since = time.time()
        self._ready_for = "unset"   # 捕获窗口已经可以藏起来了（对应哪一次直播伴侣启动）
        self._companion_up = None
        self.cap = None             # 直播捕获窗口
        self.fxcap = None           # 特效捕获窗口
        self.fx_current = None      # 正在播放的特效
        self.fx_queue = []          # 排队中的特效（最多 3 个）
        self.fx_renderer = FxRenderer()
        self.fx_screen = None       # 自己屏幕上的特效窗口
        self._fx_running = False
        self.apply_config()
        self.status = ""
        self.status_until = None
        self.visible = True
        self.edit = False
        self.x = int(cfg["x"])
        self.bottom = int(cfg["y"])   # 记住的是窗口底边位置：新弹幕往上长
        self.h = 1
        # 两个捕获窗口必须最先出现：直播伴侣重启 / 本程序重启后，它会按名字重新找窗口，
        # 名字对不上时会退而求其次抓同一个程序的其他窗口。如果这个悬浮窗先冒出来，就会被它抓去（变成一条细线），
        # 而且绑上以后不再重新找。所以：捕获窗口先在后台建好、同时显示，然后才显示悬浮窗
        if cfg.get("capture_open"):
            self.cap = CaptureWindow(self, show=False)
        if cfg.get("effect_capture_open"):
            self.fxcap = EffectCaptureWindow(self, show=False)
        for w in (self.cap, self.fxcap):
            if w is not None:
                w.show()
        self.root.geometry(f"1x1+{self.x}+{self.bottom - 1}")
        self.root.deiconify()
        self.root.update()
        self.hwnd = user32.GetParent(self.root.winfo_id())
        self.set_clickthrough(True)
        self.root.withdraw()          # 让「不显示在任务栏」的设置生效
        self.root.deiconify()
        self.root.bind("<ButtonPress-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._drag)
        self.render()
        self.root.after(100, self.tick)
        self.root.after(2000, self.keep_on_top)
        self.root.after(500, self.watch_companion)

    def apply_config(self):
        self.W = round(int(self.cfg["width"]) * self.dpi / 96.0)
        self.tr = TextRenderer(self.cfg, self.dpi)
        self.lines = deque(self.lines, maxlen=max(1, int(self.cfg["max_lines"])))

    def reload_config(self):
        new = load_config()
        for k in ("room_url", "show_browser", "debug", "x", "y"):
            new[k] = self.cfg[k]       # 直播间等要重启才生效；位置以当前为准
        self.cfg.clear()
        self.cfg.update(new)
        try:
            self.apply_config()
            status("设置已重新加载（换直播间需要退出后重新运行）", 5)
        except Exception as e:
            status("设置有误：" + str(e)[:80], 10)

    def set_clickthrough(self, on):
        st = user32.GetWindowLongW(self.hwnd, -20)
        st |= 0x80000 | 0x80 | 0x08000000      # LAYERED | TOOLWINDOW(不进任务栏/Alt+Tab) | NOACTIVATE(不抢焦点)
        st &= ~0x40000                          # 去掉 APPWINDOW
        st = st | 0x20 if on else st & ~0x20    # TRANSPARENT = 鼠标穿透
        user32.SetWindowLongW(self.hwnd, -20, st)

    def keep_on_top(self):
        # 有些游戏会把自己顶到最上面，定时把悬浮窗再放回最上层（不抢焦点）
        if self.visible:
            user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        self.root.after(2000, self.keep_on_top)

    def _start_drag(self, e):
        self._dx, self._dy = e.x_root - self.x, e.y_root - (self.bottom - self.h)

    def _drag(self, e):
        if self.edit:
            self.x = e.x_root - self._dx
            self.bottom = e.y_root - self._dy + self.h
            self.root.geometry(f"+{self.x}+{self.bottom - self.h}")

    def toggle_edit(self):
        self.edit = not self.edit
        self.set_clickthrough(not self.edit)
        if not self.edit:
            self.cfg["x"], self.cfg["y"] = self.x, self.bottom
            save_config(self.cfg)
            status("位置已保存", 3)
        self.render()

    def toggle_visible(self):
        self.visible = not self.visible
        if self.visible:
            self.root.deiconify()
            self.set_clickthrough(not self.edit)
            self.render()
            self.keep_on_top()
        else:
            self.root.withdraw()

    def quit(self):
        for w in (self.cap, self.fxcap):
            if w is not None:
                w.remember()
        save_config(self.cfg)
        stop_event.set()
        icon = tray_ref["icon"]
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def capture_ready(self):
        """捕获窗口可以变透明了吗？——要等直播伴侣已经找好窗口。
           直播伴侣只在启动、加载场景时按名字找一次窗口，那一刻窗口必须是普通（不透明）的。
           开播前一定已经找过了，所以：开播了（弹幕连接接通）+ 直播伴侣运行满 20 秒，就算准备好。
           同一次直播伴侣运行期间，准备好以后就不再变回去"""
        since = self.companion_since
        if not self.room_live and self._offline_since and time.time() - self._offline_since > OFFLINE_RESET_S:
            if self._ready_for != "unset":
                print(time.strftime("%H:%M:%S"), "已下播：捕获窗口恢复普通，等下次开播再藏起")
            self._ready_for = "unset"            # 真的下播了（不是短暂断线）：下次开播前直播伴侣可能会重开
            return False
        if self._ready_for == since:
            return True
        ok = self.room_live and (since is None or time.time() - since >= COMPANION_GRACE_S)
        if ok:
            self._ready_for = since
        return ok

    def watch_companion(self):
        """每 3 秒看一下直播伴侣：它（重新）启动时，让捕获窗口先恢复普通一会儿，方便它重新找到"""
        try:
            up = process_running(COMPANION_EXE)
            if up and self._companion_up is False:
                self.companion_since = time.time()
                print(time.strftime("%H:%M:%S"), "发现直播伴侣启动了：捕获窗口先保持普通 %d 秒，让它找到窗口" % COMPANION_GRACE_S)
                for w in (self.cap, self.fxcap):
                    if w is not None and w.hwnd:
                        w._periodic()
            elif up and self._companion_up is None:
                self.companion_since = time.time()      # 我们启动时它已经在运行：也给一点时间
            self._companion_up = up
        except Exception as e:
            print("检查直播伴侣出错：", e)
        self.root.after(3000, self.watch_companion)

    def tick(self):
        changed = False
        while not cmd_q.empty():
            c = cmd_q.get()
            if c == CMD_TOGGLE:
                self.toggle_visible()
            elif c == CMD_EDIT:
                if not self.visible:
                    self.toggle_visible()
                self.toggle_edit()
            elif c == CMD_RELOAD:
                self.reload_config()
                changed = True
            elif c == CMD_KEEP:
                self.cfg["keep_recent"] = not self.cfg.get("keep_recent")
                save_config(self.cfg)
                n = int(self.cfg.get("keep_recent_count", 3))
                status(f"已开启：一直显示最近 {n} 条" if self.cfg["keep_recent"] else "已关闭：弹幕到时间就消失", 4)
                changed = True
                try:
                    tray_ref["icon"].update_menu()   # 刷新菜单上的勾
                except Exception:
                    pass
            elif c == CMD_CAPTURE:
                if self.cap is None:
                    self.cap = CaptureWindow(self)
                    self.cfg["capture_open"] = True
                    status("已打开直播捕获窗口：在直播伴侣里用「窗口捕获」选它", 6)
                else:
                    self.cap.close()
                changed = True
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
                save_config(self.cfg)
            elif isinstance(c, tuple) and c[0] == "fx_test":
                self.trigger_fx({"uid": "test%d" % time.time(), "nick": "测试", "count": 1}, c[1])
            elif c == CMD_FX_CAPTURE:
                if self.fxcap is None:
                    self.fxcap = EffectCaptureWindow(self)
                    self.cfg["effect_capture_open"] = True
                    status("已打开特效捕获窗口：在直播伴侣里窗口捕获它、铺满画面、抠掉绿色", 8)
                else:
                    self.fxcap.close()
                save_config(self.cfg)
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
            elif c == CMD_FX_SCREEN:
                self.cfg["effect_screen"] = not self.cfg.get("effect_screen", True)
                save_config(self.cfg)
                if not self.cfg["effect_screen"] and self.fx_screen is not None:
                    self.fx_screen.hide()
                status("我的屏幕：播放礼物特效" if self.cfg["effect_screen"] else "我的屏幕：不播放礼物特效", 4)
                changed = True
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
            elif c == CMD_TEST:
                # 只放进本地显示，不会发到直播间：用来检查悬浮窗 / 捕获窗口 / 直播伴侣预览
                stamp = time.strftime("%H:%M:%S")
                msg_q.put(("chat", "测试", "这是一条测试弹幕 " + stamp + " [比心]"))
                msg_q.put(("gift", {"uid": "test", "nick": "测试", "gift_id": 0, "name": "小心心",
                                    "count": int(time.time()) % 9 + 1, "diamond": 1, "combo_id": int(time.time()),
                                    "urls": []}))
            elif c == CMD_NO_TASKBAR:
                self.cfg["capture_no_taskbar"] = not self.cfg.get("capture_no_taskbar")
                save_config(self.cfg)
                for w in (self.cap, self.fxcap):
                    if w is not None:
                        taskbar_tab(w.hwnd, not self.cfg["capture_no_taskbar"])
                status("任务栏里已去掉捕获窗口（如果直播里弹幕或特效没了，再点一次恢复）"
                       if self.cfg["capture_no_taskbar"] else "任务栏里已恢复捕获窗口", 8)
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
            elif c == CMD_CAPTURE_HIDE:
                self.cfg["capture_hidden"] = not self.cfg.get("capture_hidden")
                for w in (self.cap, self.fxcap):
                    if w is not None:
                        w.set_hidden(self.cfg["capture_hidden"])
                save_config(self.cfg)
                if not self.cfg["capture_hidden"]:
                    status("捕获窗口已显示出来", 5)
                elif not self.cfg.get("capture_wait_live", False) or self.capture_ready():
                    status("捕获窗口已藏起：原地变透明、鼠标能穿过（直播照常）", 5)
                else:
                    status("捕获窗口会在开播后自动变透明（开播前要让直播伴侣先找到它们）", 8)
                changed = True
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
            elif isinstance(c, tuple) and c[0] in ("fx_size", "fx_pos"):
                key = "effect_screen_size" if c[0] == "fx_size" else "effect_screen_pos"
                self.cfg[key] = c[1]
                save_config(self.cfg)
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
                # 放一次给你看效果（只在你自己屏幕上播，不进直播画面）
                self.trigger_fx({"uid": "preview%d" % time.time(), "nick": "预览", "count": 1}, "beer", screen_only=True)
            elif isinstance(c, tuple) and c[0] == "capture_bg":
                self.cfg["capture_bg"] = c[1]
                save_config(self.cfg)
                if self.cap is not None:
                    self.cap.set_bg(c[1])
                changed = True
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
            elif c == CMD_GIFTS:
                self.cfg["show_gifts"] = not self.cfg.get("show_gifts")
                save_config(self.cfg)
                if not self.cfg["show_gifts"]:
                    self.lines = deque([ln for ln in self.lines if ln[3] is None], maxlen=self.lines.maxlen)
                status("已显示礼物" if self.cfg["show_gifts"] else "已隐藏礼物", 3)
                changed = True
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
            elif c == CMD_VIEWERS:
                self.cfg["show_viewers"] = not self.cfg.get("show_viewers")
                save_config(self.cfg)
                status("已显示在线观众" if self.cfg["show_viewers"] else "已隐藏在线观众", 3)
                changed = True
                try:
                    tray_ref["icon"].update_menu()
                except Exception:
                    pass
            elif c == CMD_QUIT:
                self.quit()
                return
        now = time.time()
        while not msg_q.empty():
            item = msg_q.get()
            kind = item[0]
            if kind == "status":
                self.status = item[1]
                self.status_until = now + item[2] if item[2] else None
            elif kind == "chat":
                self.lines.append((item[1], item[2], now, None))
            elif kind == "gift":
                g = item[1]
                kind = effect_map(self.cfg).get(g.get("name"))
                if kind:
                    self.trigger_fx(g, kind)
                if not self.cfg.get("show_gifts", True):
                    continue
                # 连击：同一个人连送同一种礼物，只更新那一行的数量，不刷屏
                key = (g["uid"], g["gift_id"], g["combo_id"] or g["gift_id"])
                for i, ln in enumerate(self.lines):
                    if ln[3] is not None and ln[3]["key"] == key and now - ln[2] < 60:
                        old = ln[3]
                        g["count"] = max(g["count"], old["count"])
                        g["key"] = key
                        del self.lines[i]
                        break
                g["key"] = key
                self.lines.append((g["nick"], "", now, g))
            elif kind == "online":
                if item[1] == self.online:
                    continue
                self.online = item[1]
            elif kind == "viewers":
                self.viewers, self.viewers_masked = item[1], item[2]
                self.viewers_meta = item[3] if len(item) > 3 else None
                ids = {v["id"] for v in self.viewers}
                if self.seen_ids is not None and not self.viewers_masked:
                    for i in ids - self.seen_ids:
                        self.new_until[i] = now + NEW_VIEWER_SECONDS
                self.seen_ids = (self.seen_ids or set()) | ids
                # 新来的排在最前面，一眼能看到
                self.viewers.sort(key=lambda v: v["id"] not in self.new_until)
            elif kind == "avatars":
                pass
            elif kind == "live":
                if item[1] and not self.room_live:
                    print(time.strftime("%H:%M:%S"), "已开播：直播伴侣早就找好窗口了，捕获窗口可以藏起来了")
                self.room_live = bool(item[1])
                self._offline_since = None if self.room_live else time.time()
                continue
            changed = True
        for i in [i for i, t in self.new_until.items() if now > t]:
            del self.new_until[i]
            changed = True
        exp = float(self.cfg["expire_seconds"] or 0)
        keep = keep_recent_count(self.cfg)      # 最近几条一直留着，不会因为超时消失
        if exp:
            while len(self.lines) > keep and now - self.lines[0][2] > exp:
                self.lines.popleft()
                changed = True
        if self.status and self.status_until and now > self.status_until:
            self.status = ""
            changed = True
        if changed:
            self.render()
        self.root.after(150, self.tick)

    def trigger_fx(self, g, kind="beer", screen_only=False):
        """收到特效礼物：同一个人连击同一种就更新数量；其他的排队（最多 3 个）"""
        uid, nick, count = g.get("uid", ""), g.get("nick", ""), g.get("count", 1)
        cur = self.fx_current
        if cur is not None and cur.uid == uid and cur.kind == kind and not cur.done():
            cur.bump(count)
            return
        for q in self.fx_queue:
            if q.uid == uid and q.kind == kind:
                q.count = max(q.count, int(count))
                return
        fx = BeerFx(nick, count, uid, kind)
        fx.screen_only = screen_only
        if cur is None:
            self.fx_current = fx
        elif len(self.fx_queue) < 3:
            self.fx_queue.append(fx)
        if not self._fx_running:
            self._fx_running = True
            self.root.after(0, self._fx_step)

    def _fx_step(self):
        t_start = time.perf_counter()
        fx = self.fx_current
        if fx is not None and fx.done():
            fx = self.fx_current = None
            if self.fx_queue:
                fx = self.fx_current = self.fx_queue.pop(0)
                fx.t0 = time.time()
        if fx is None:                          # 播完了：收起屏幕特效，捕获窗口恢复纯绿
            self._fx_running = False
            if self.fx_screen is not None:
                self.fx_screen.hide()
            if self.fxcap is not None:
                self.fxcap.update()
            return
        try:
            if self.cfg.get("effect_screen", True):
                if self.fx_screen is None:
                    self.fx_screen = ScreenFxWindow(self)
                x, y, w, h = self.fx_screen.stage()
                self.fx_screen.show_frame(self.fx_renderer.frame(fx, w, h), x, y)
            if self.fxcap is not None:
                self.fxcap.update()
        except Exception as e:
            print("特效播放出错：", e)
        fps = max(5, min(60, int(self.cfg.get("effect_fps", 24))))
        spent = (time.perf_counter() - t_start) * 1000
        self.root.after(max(1, int(1000 / fps - spent)), self._fx_step)

    def display_online(self):
        """显示的在线人数。抖音推送的人数有时不会及时变（比如最后一个人走了还停在 1），
           所以观众榜完整（没打码、没有下一页、2 分钟内拿到的）时，直接用榜上的实际人数"""
        m = self.viewers_meta
        if m and not self.viewers_masked and not m.get("has_more") and time.time() - m.get("t", 0) < 120:
            return len(self.viewers) + int(m.get("invisible") or 0)
        return self.online

    def compose(self, width, edit):
        """把弹幕、礼物、观众栏画成一张 RGBA 图（悬浮窗和捕获窗口共用）"""
        rows = []
        if edit:
            rows.append(("text", "移动模式：拖动窗口，Ctrl+Alt+E 退出", "#FFD866"))
        if self.status and edit is not None:      # 状态提示只在悬浮窗上显示，不进直播画面
            rows.append(("text", self.status, "#D0D0D0"))
        rows += [("gift", g, None) if g is not None else ("chat", n, t) for n, t, _, g in self.lines]
        if edit and not self.lines:
            rows.append(("text", "（弹幕会显示在这里）", "#909090"))
        img = self.tr.render(rows, width, bool(edit))
        if self.cfg.get("show_viewers"):
            vimg = self.tr.render_viewers(width, self.display_online(), self.viewers, self.viewers_masked,
                                          set(self.new_until), bool(edit))
            if vimg is not None:     # 观众栏放在最下面，位置固定；弹幕在它上面往上长
                from PIL import Image
                gap = round(4 * self.dpi / 96.0)
                if img.size[1] <= 1:
                    img = vimg
                else:
                    both = Image.new("RGBA", (width, img.size[1] + gap + vimg.size[1]), (0, 0, 0, 0))
                    both.alpha_composite(img, (0, 0))
                    both.alpha_composite(vimg, (0, img.size[1] + gap))
                    img = both
        return img

    def render(self):
        if self.cap is not None:
            self.cap.update()
        if not self.visible:
            return
        img = self.compose(self.W, self.edit)
        self.h = img.size[1]
        y = self.bottom - self.h
        self.root.geometry(f"{self.W}x{self.h}+{self.x}+{y}")   # 让 Tk 知道窗口实际大小（拖动要用）
        update_layered(self.hwnd, img, self.x, self.bottom - self.h)


# ---------------- 礼物全屏特效：大啤酒「干杯冲屏」 ----------------
def _ease_out_back(x, s=1.70158):
    x = min(max(x, 0.0), 1.0) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def _ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def _clamp01(x):
    return min(max(x, 0.0), 1.0)


FX_TEXTS = {   # 特效种类 -> (第一行, 大字, emoji)
    "beer": ("请大家喝大啤酒！", "干杯！", "🍻"),
    "chicken": ("请大家吃美味烧鸡！", "开饭啦！", "🍗"),
}


def effect_map(cfg):
    """设置里的 effect_gifts：新格式是 {礼物名: 特效}，旧格式是 [礼物名]（都当干杯）"""
    v = cfg.get("effect_gifts") or {}
    if isinstance(v, list):
        return {name: "beer" for name in v}
    return {k: (x if x in FX_TEXTS else "beer") for k, x in v.items()}


class BeerFx:
    """一次特效播放（哪种特效、谁送的、送了几个、什么时候开始）"""
    DURATION = 3.4
    CLINK = 0.72          # 碰杯 / 烧鸡落盘的时刻（秒）

    def __init__(self, nick, count, uid="", kind="beer"):
        import random
        self.kind = kind if kind in FX_TEXTS else "beer"
        self.nick, self.count, self.uid = nick or "神秘人", max(1, int(count)), uid
        self.t0 = time.time()
        rnd = random.Random(hash((self.nick, self.t0)))
        # 气泡：从碰杯处往上飘，左右摇摆
        self.bubbles = [(rnd.uniform(-1, 1), rnd.uniform(0.15, 1.0), rnd.uniform(0, 0.9), rnd.uniform(0, 6.28),
                         rnd.choice((0, 1, 2, 3))) for _ in range(46)]

    def t(self, now=None):
        return (now or time.time()) - self.t0

    def done(self, now=None):
        return self.t(now) >= self.DURATION

    def bump(self, count):
        """连击：更新数量，让文字多停留一会儿"""
        self.count = max(self.count, int(count))
        if self.t() > 1.6:
            self.t0 = time.time() - 1.6


class FxRenderer:
    """按需要的尺寸画特效帧；啤酒杯、气泡、星星等小图按尺寸缓存，避免每帧重画"""

    BEER_TOP, BEER_BOTTOM = (255, 206, 72), (232, 132, 10)
    INK = (74, 40, 6, 255)          # 描边的深棕色
    FOAM = (255, 253, 244, 255)

    def __init__(self):
        self._cache = {}

    # ---------- 小图 ----------
    def _mug(self, u):
        key = ("mug", round(u, 3))
        if key in self._cache:
            return self._cache[key]
        from PIL import Image, ImageDraw
        ss = 2                                     # 2 倍超采样，边缘平滑
        s = u * ss
        bw, bh = 250 * s, 300 * s                  # 杯身
        hw = 95 * s                                # 把手
        foam_h = 95 * s
        ow = 11 * s                                # 描边粗细
        W, H = int(bw + hw + 90 * s), int(bh + foam_h + 90 * s)
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        x0, y0 = 50 * s, foam_h + 50 * s          # 上面、左边留够空间，泡沫不会被切掉
        x1, y1 = x0 + bw, y0 + bh
        r = 34 * s
        # 把手：外圈描边色，里面挖空
        hx0, hy0, hx1, hy1 = x1 - 30 * s, y0 + 55 * s, x1 + hw, y1 - 70 * s
        d.rounded_rectangle((hx0, hy0, hx1, hy1), radius=60 * s, fill=self.INK)
        d.rounded_rectangle((hx0 + ow, hy0 + ow, hx1 - ow, hy1 - ow), radius=50 * s, fill=(255, 236, 190, 255))
        d.rounded_rectangle((hx0 + 34 * s, hy0 + 34 * s, hx1 - 34 * s, hy1 - 34 * s), radius=28 * s, fill=self.INK)
        d.rounded_rectangle((hx0 + 34 * s + ow, hy0 + 34 * s + ow, hx1 - 34 * s - ow, hy1 - 34 * s - ow),
                            radius=20 * s, fill=(0, 0, 0, 0))
        # 杯身：描边 + 啤酒渐变
        d.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=self.INK)
        grad = Image.new("RGBA", (int(bw), int(bh)))
        gd = ImageDraw.Draw(grad)
        for yy in range(int(bh)):
            k = yy / max(1, bh - 1)
            col = tuple(int(a + (b - a) * k) for a, b in zip(self.BEER_TOP, self.BEER_BOTTOM)) + (255,)
            gd.line((0, yy, bw, yy), fill=col)
        mask = Image.new("L", grad.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((ow, ow, bw - ow, bh - ow), radius=r - ow, fill=255)
        img.paste(grad, (int(x0), int(y0)), mask)
        # 杯底加厚
        d.rounded_rectangle((x0 + ow, y1 - 38 * s, x1 - ow, y1 - ow), radius=r - ow, fill=(214, 112, 6, 255))
        # 玻璃反光、杯里的小气泡：画在单独一层再叠上去（直接画半透明色会把啤酒「挖空」）
        hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        hd = ImageDraw.Draw(hl)
        hd.rounded_rectangle((x0 + 30 * s, y0 + 40 * s, x0 + 58 * s, y1 - 60 * s), radius=14 * s,
                             fill=(255, 250, 225, 140))
        hd.rounded_rectangle((x0 + 72 * s, y0 + 40 * s, x0 + 84 * s, y0 + 150 * s), radius=6 * s,
                             fill=(255, 250, 225, 105))
        for bx, by, br in ((0.62, 0.30, 9), (0.75, 0.52, 6), (0.55, 0.70, 7), (0.80, 0.78, 5), (0.68, 0.15, 5)):
            cx, cy, rr = x0 + bw * bx, y0 + bh * by, br * s
            hd.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=(255, 244, 190, 190))
        img.alpha_composite(hl)
        # 泡沫：一堆圆，先画大一圈的描边色，再画白色
        blobs = [(0.12, 0.78, 44), (0.32, 0.52, 54), (0.55, 0.45, 58), (0.78, 0.55, 50), (0.95, 0.80, 40),
                 (0.22, 0.95, 40), (0.70, 0.95, 44), (0.04, 0.98, 34), (0.97, 0.98, 34), (0.46, 0.92, 44)]
        drips = [(0.10, 1.30, 20), (0.86, 1.18, 17)]
        for pass_ in (0, 1):
            for bx, by, br in blobs + drips:
                cx, cy = x0 + bw * bx, y0 - foam_h + foam_h * by
                rr = br * s + (ow if pass_ == 0 else 0)
                d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=self.INK if pass_ == 0 else self.FOAM)
            if pass_ == 0:     # 泡沫底边和杯口接上
                d.rectangle((x0, y0 - 8 * s, x1, y0 + 12 * s), fill=self.INK)
        d.rectangle((x0 + ow, y0 - 14 * s, x1 - ow, y0 + 2 * s), fill=self.FOAM)
        # 泡沫高光
        d.ellipse((x0 + bw * 0.30, y0 - foam_h * 0.70, x0 + bw * 0.42, y0 - foam_h * 0.56), fill=(255, 255, 255, 255))
        img = img.resize((W // ss, H // ss), Image.LANCZOS)
        # 旋转中心：杯身中心（不含把手），记下来
        pivot = ((x0 + bw / 2) / ss, (y0 + bh / 2) / ss)
        self._cache[key] = (img, pivot)
        return img, pivot

    def _bubble(self, u, kind):
        key = ("bub", round(u, 3), kind)
        if key in self._cache:
            return self._cache[key]
        from PIL import Image, ImageDraw
        ss = 3
        r = (10, 16, 24, 34)[kind] * u * ss
        n = int(2 * r + 4 * ss)
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        c = n / 2
        d.ellipse((c - r, c - r, c + r, c + r), fill=(255, 255, 255, 70), outline=(255, 255, 255, 235),
                  width=max(1, int(r * 0.16)))
        d.ellipse((c - r * 0.55, c - r * 0.6, c - r * 0.15, c - r * 0.2), fill=(255, 255, 255, 230))
        img = img.resize((max(1, n // ss), max(1, n // ss)), Image.LANCZOS)
        self._cache[key] = img
        return img

    def _star(self, size):
        size = max(4, int(size))
        key = ("star", size)
        if key in self._cache:
            return self._cache[key]
        from PIL import Image, ImageDraw
        ss = 3
        n = size * ss
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        c, R, r = n / 2, n / 2, n * 0.11
        pts = [(c, c - R), (c + r, c - r), (c + R, c), (c + r, c + r), (c, c + R), (c - r, c + r), (c - R, c), (c - r, c - r)]
        d.polygon(pts, fill=(255, 244, 170, 255))
        d.ellipse((c - r * 1.4, c - r * 1.4, c + r * 1.4, c + r * 1.4), fill=(255, 255, 255, 255))
        img = img.resize((size, size), Image.LANCZOS)
        self._cache[key] = img
        return img

    def _text(self, fx, u):
        key = ("txt", round(u, 3), fx.kind, fx.nick, fx.count)
        if key in self._cache:
            return self._cache[key]
        from PIL import Image, ImageDraw, ImageFont
        def font(px):
            for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf"):
                try:
                    return ImageFont.truetype(_font_path(name), px)
                except Exception:
                    pass
            return ImageFont.load_default()
        nick = fx.nick if len(fx.nick) <= 12 else fx.nick[:11] + "…"
        line_tail, word, emoji = FX_TEXTS[fx.kind]
        line1 = f"{nick} {line_tail}" + (f" ×{fx.count}" if fx.count > 1 else "")
        f1, f2 = font(max(10, int(54 * u))), font(max(12, int(130 * u)))
        st1, st2 = max(2, int(7 * u)), max(3, int(12 * u))
        # 第一行
        b1 = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), line1, font=f1, stroke_width=st1)
        t1 = Image.new("RGBA", (b1[2] - b1[0] + 4, b1[3] - b1[1] + 4), (0, 0, 0, 0))
        ImageDraw.Draw(t1).text((2 - b1[0], 2 - b1[1]), line1, font=f1, fill=(255, 255, 255, 255),
                                stroke_width=st1, stroke_fill=self.INK)
        # 第二行大字 + emoji
        b2 = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), word, font=f2, stroke_width=st2)
        emo_px = int(120 * u)
        emo = None
        try:
            ef = ImageFont.truetype(_font_path("seguiemj.ttf"), 109)   # 彩色 emoji 只在 109 号字下最清楚，再缩放
            eb = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), emoji, font=ef, embedded_color=True)
            emo = Image.new("RGBA", (eb[2] - eb[0] + 4, eb[3] - eb[1] + 4), (0, 0, 0, 0))
            ImageDraw.Draw(emo).text((2 - eb[0], 2 - eb[1]), emoji, font=ef, embedded_color=True)
            emo = emo.resize((emo_px, int(emo_px * emo.size[1] / emo.size[0])), Image.LANCZOS)
        except Exception:
            emo = None
        w2 = b2[2] - b2[0] + 4 + (emo.size[0] + int(14 * u) if emo else 0)
        h2 = max(b2[3] - b2[1] + 4, emo.size[1] if emo else 0)
        t2 = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
        # 金色渐变字：先画描边，再用渐变填充
        ImageDraw.Draw(t2).text((2 - b2[0], (h2 - (b2[3] - b2[1])) // 2 - b2[1]), word, font=f2,
                                fill=self.INK, stroke_width=st2, stroke_fill=self.INK)
        mask = Image.new("L", t2.size, 0)
        ImageDraw.Draw(mask).text((2 - b2[0], (h2 - (b2[3] - b2[1])) // 2 - b2[1]), word, font=f2, fill=255)
        grad = Image.new("RGBA", t2.size)
        gd = ImageDraw.Draw(grad)
        for yy in range(t2.size[1]):
            k = yy / max(1, t2.size[1] - 1)
            gd.line((0, yy, t2.size[0], yy), fill=(255, int(246 - 70 * k), int(160 - 150 * k), 255))
        t2.paste(grad, (0, 0), mask)
        if emo:
            t2.alpha_composite(emo, (w2 - emo.size[0], (h2 - emo.size[1]) // 2))
        self._cache[key] = (t1, t2)
        if len(self._cache) > 80:
            for k in [k for k in self._cache if k[0] == "txt"][:-6]:
                del self._cache[k]
        return t1, t2

    # ---------- 烧鸡 ----------
    def _plate(self, u):
        key = ("plate", round(u, 3))
        if key in self._cache:
            return self._cache[key]
        from PIL import Image, ImageDraw
        ss = 2
        s = u * ss
        pw, ph, ow = 620 * s, 170 * s, 10 * s
        W, H = int(pw + 40 * s), int(ph + 60 * s)
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        x0, y0 = 20 * s, 20 * s
        d.ellipse((x0, y0 + 18 * s, x0 + pw, y0 + ph + 18 * s), fill=(200, 205, 215, 255))         # 盘子侧面
        d.ellipse((x0 - ow, y0 - ow, x0 + pw + ow, y0 + ph + ow), fill=self.INK)
        d.ellipse((x0, y0, x0 + pw, y0 + ph), fill=(255, 255, 255, 255))
        d.ellipse((x0 + 70 * s, y0 + 26 * s, x0 + pw - 70 * s, y0 + ph - 26 * s), fill=(236, 241, 248, 255))
        # 番茄片点缀（不用绿色：直播画面里绿色会被抠掉）
        for tx, ty in ((0.10, 0.52), (0.88, 0.50), (0.18, 0.78), (0.80, 0.78)):
            cx, cy, r = x0 + pw * tx, y0 + ph * ty, 30 * s
            d.ellipse((cx - r - ow * 0.7, cy - r * 0.62 - ow * 0.7, cx + r + ow * 0.7, cy + r * 0.62 + ow * 0.7), fill=self.INK)
            d.ellipse((cx - r, cy - r * 0.62, cx + r, cy + r * 0.62), fill=(235, 64, 52, 255))
            d.ellipse((cx - r * 0.6, cy - r * 0.35, cx + r * 0.6, cy + r * 0.35), fill=(255, 140, 110, 255))
        img = img.resize((W // ss, H // ss), Image.LANCZOS)
        self._cache[key] = img
        return img

    def _chicken(self, u):
        key = ("chicken", round(u, 3))
        if key in self._cache:
            return self._cache[key]
        from PIL import Image, ImageDraw
        ss = 2
        s = u * ss
        ow = 11 * s
        W, H = int(720 * s), int(560 * s)
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        ink = self.INK
        cx, cy = W / 2, 380 * s           # 身体中心（上面留出骨头的空间）
        bw, bh = 430 * s, 260 * s

        def blob(box, fill, outline=True):
            if outline:
                d.ellipse((box[0] - ow, box[1] - ow, box[2] + ow, box[3] + ow), fill=ink)
            d.ellipse(box, fill=fill)

        # 翅膀（在身体后面）
        for side in (-1, 1):
            wx = cx + side * bw * 0.46
            blob((wx - 70 * s, cy - 40 * s, wx + 70 * s, cy + 70 * s), (196, 104, 30, 255))
        # 身体：描边 + 金黄到焦糖的渐变
        d.ellipse((cx - bw / 2 - ow, cy - bh / 2 - ow, cx + bw / 2 + ow, cy + bh / 2 + ow), fill=ink)
        grad = Image.new("RGBA", (int(bw), int(bh)))
        gd = ImageDraw.Draw(grad)
        top, bot = (247, 186, 72), (186, 92, 24)
        for yy in range(int(bh)):
            k = yy / max(1, bh - 1)
            gd.line((0, yy, bw, yy), fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bot)) + (255,))
        mask = Image.new("L", grad.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, bw - 1, bh - 1), fill=255)
        img.paste(grad, (int(cx - bw / 2), int(cy - bh / 2)), mask)
        # 胸脯中间的隆起和高光、烤出来的焦点
        hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        hd = ImageDraw.Draw(hl)
        hd.ellipse((cx - bw * 0.30, cy - bh * 0.42, cx + bw * 0.05, cy - bh * 0.12), fill=(255, 236, 170, 150))
        hd.ellipse((cx - bw * 0.24, cy - bh * 0.38, cx - bw * 0.12, cy - bh * 0.28), fill=(255, 255, 240, 220))
        for px_, py_, r in ((0.15, -0.05, 9), (0.28, 0.12, 7), (-0.05, 0.18, 8), (0.05, -0.25, 6), (-0.30, 0.05, 7)):
            hd.ellipse((cx + bw * px_ - r * s, cy + bh * py_ - r * s, cx + bw * px_ + r * s, cy + bh * py_ + r * s),
                       fill=(140, 62, 12, 170))
        img.alpha_composite(hl)
        # 两只鸡腿：从身体前面斜着翘起来，末端露出骨头
        for side in (-1, 1):
            lx, ly = cx + side * bw * 0.33, cy + bh * 0.16
            leg = Image.new("RGBA", (int(460 * s), int(460 * s)), (0, 0, 0, 0))   # 够大，转了以后骨头两端不会被切掉
            ld = ImageDraw.Draw(leg)
            c = 230 * s
            # 骨头（往上伸）
            ld.rounded_rectangle((c - 22 * s - ow, c - 150 * s - ow, c + 22 * s + ow, c - 20 * s), radius=20 * s, fill=ink)
            for kx in (-26, 26):
                ld.ellipse((c + kx * s - 30 * s - ow, c - 172 * s - ow, c + kx * s + 30 * s + ow, c - 112 * s + ow), fill=ink)
            ld.rounded_rectangle((c - 22 * s, c - 150 * s, c + 22 * s, c - 20 * s), radius=16 * s, fill=(255, 246, 226, 255))
            for kx in (-26, 26):
                ld.ellipse((c + kx * s - 30 * s, c - 172 * s, c + kx * s + 30 * s, c - 112 * s), fill=(255, 246, 226, 255))
            # 腿肉
            ld.ellipse((c - 72 * s - ow, c - 78 * s - ow, c + 72 * s + ow, c + 105 * s + ow), fill=ink)
            ld.ellipse((c - 72 * s, c - 78 * s, c + 72 * s, c + 105 * s), fill=(214, 120, 36, 255))
            ld.ellipse((c - 46 * s, c - 58 * s, c - 4 * s, c + 2 * s), fill=(250, 190, 90, 255))
            leg = leg.rotate(-38 * side, resample=Image.BICUBIC, center=(c, c))     # 往外翻：左腿骨头朝左上，右腿朝右上
            img.alpha_composite(leg, (int(lx - c), int(ly - c)))
        img = img.resize((W // ss, H // ss), Image.LANCZOS)
        self._cache[key] = img
        return img

    def _steam(self, u, kind):
        key = ("steam", round(u, 3), kind)
        if key in self._cache:
            return self._cache[key]
        from PIL import Image, ImageDraw, ImageFilter
        w, h = int((46, 64, 82)[kind] * u), int((80, 110, 140)[kind] * u)
        pad = int(18 * u) + 2
        img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (255, 255, 255, 0))
        a = Image.new("L", img.size, 0)
        ImageDraw.Draw(a).ellipse((pad, pad, pad + w, pad + h), fill=105)
        a = a.filter(ImageFilter.GaussianBlur(max(1, int(10 * u))))
        img.putalpha(a)
        self._cache[key] = img
        return img

    def _chicken_scene(self, fx, img, t, u, cx, H):
        """盘子从下面滑上来，烧鸡从上面掉进盘子、弹两下，然后冒热气。返回特效中心的 y（星星、文字用）"""
        from PIL import Image
        import math
        plate = self._plate(u)
        chick = self._chicken(u)
        sink = _ease_out(_clamp01((t - 2.75) / 0.65)) * 140 * u
        rise = _ease_out_back(t / 0.45, 1.3)
        plate_y = H + 260 * u + (H * 0.80 - (H + 260 * u)) * rise + sink         # 盘子中心
        land = BeerFx.CLINK
        rest_y = plate_y - 150 * u                                                 # 烧鸡落定后的中心
        if t < 0.2:
            chick_y = None
        elif t < land:
            k = (t - 0.2) / (land - 0.2)
            chick_y = -300 * u + (rest_y + 300 * u) * (k * k)                      # 越掉越快
        else:
            tb = t - land
            hop = 0
            if tb < 0.26:
                hop = math.sin(tb / 0.26 * math.pi) * 70 * u
            elif tb < 0.42:
                hop = math.sin((tb - 0.26) / 0.16 * math.pi) * 22 * u
            chick_y = rest_y - hop
        img.alpha_composite(plate, (int(cx - plate.size[0] / 2), int(plate_y - plate.size[1] / 2)))
        if chick_y is not None:
            sq = 1.0
            tb = t - land
            if 0 <= tb < 0.18:                                                     # 落地压扁一下
                sq = 1 - 0.16 * math.sin(tb / 0.18 * math.pi)
            elif t < land:
                sq = 1.06                                                          # 下落时拉长一点
            if abs(sq - 1) > 0.005:
                cw, ch = chick.size
                c2 = chick.resize((int(cw * (2 - sq)), int(ch * sq)), Image.BILINEAR)
            else:
                c2 = chick
            bottom = chick_y + chick.size[1] * 0.5
            img.alpha_composite(c2, (int(cx - c2.size[0] / 2), int(bottom - c2.size[1])))
        # 热气：落盘后从烧鸡上面冒出来，左右飘、慢慢变淡
        tc = t - land
        if tc > 0.05:
            for spread, speed, delay, phase, kind in fx.bubbles[:24]:
                bt = tc - delay * 1.2
                if bt <= 0:
                    continue
                life = 1.6
                k = bt % life / life
                y = rest_y - 170 * u - k * (260 + 160 * speed) * u
                x = cx + spread * 170 * u + math.sin(bt * 3 + phase) * 30 * u
                st = self._steam(u, kind % 3)
                alpha = math.sin(k * math.pi)
                if alpha < 0.05:
                    continue
                st2 = st.copy()
                st2.putalpha(st.getchannel("A").point(lambda v, f=alpha: int(v * f)))
                img.alpha_composite(st2, (int(x - st.size[0] / 2), int(y - st.size[1] / 2)))
        return rest_y - 60 * u

    # ---------- 一帧 ----------
    def frame(self, fx, W, H, now=None):
        """画出 fx 在 now 时刻、W×H 大小的一帧（RGBA，透明背景）"""
        from PIL import Image
        import math
        t = fx.t(now)
        u = H / 1080.0
        cx = W / 2
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fade = 1.0 - _clamp01((t - 2.75) / 0.65)          # 最后 0.65 秒整体淡出
        if fade <= 0:
            return img

        if fx.kind == "chicken":
            burst_y = self._chicken_scene(fx, img, t, u, cx, H)
            self._burst_and_text(fx, img, t, u, cx, H, burst_y, bubbles=False)
            if fade < 1:
                img.putalpha(img.getchannel("A").point(lambda v: int(v * fade)))
            return img

        # 两个杯子：从左下 / 右下冲上来，0.55~0.72 秒倾斜靠拢碰杯，然后弹开一点
        mug, (px, py) = self._mug(u)
        rise = _ease_out_back(t / 0.55, 1.4)
        y_center = H + 400 * u + (H * 0.60 - (H + 400 * u)) * rise
        sink = _ease_out(_clamp01((t - 2.75) / 0.65)) * 140 * u
        lean = _clamp01((t - 0.50) / (BeerFx.CLINK - 0.50))
        back = _clamp01((t - BeerFx.CLINK) / 0.25)
        bounce = math.sin(back * math.pi) * (1 - back * 0.5)
        sep = 330 * u - 170 * u * _ease_out(lean) + 50 * u * bounce
        tilt = 24 * _ease_out(lean) - 10 * bounce
        for side in (-1, 1):
            m = mug if side == 1 else mug.transpose(Image.FLIP_LEFT_RIGHT)
            pvx = px if side == 1 else m.size[0] - px
            ang = round(tilt * (1 if side == -1 else -1) * 2) / 2   # 左杯向右倒，右杯向左倒（按 0.5° 取整，好缓存）
            rkey = ("rot", round(u, 3), side, ang)
            rm = self._cache.get(rkey)
            if rm is None:                                    # 碰杯后角度不变，后面 2 秒多都直接用缓存
                rm = m.rotate(ang, resample=Image.BICUBIC, expand=True, center=(pvx, py))
                rot_keys = [k for k in self._cache if k[0] == "rot"]
                if len(rot_keys) > 40:
                    for k in rot_keys[:20]:
                        del self._cache[k]
                self._cache[rkey] = rm
            # 旋转后中心位置的偏移（expand 后图片变大，杯身中心挪到新图中心附近）
            ox = rm.size[0] / 2 - (m.size[0] / 2 - pvx)
            oy = rm.size[1] / 2 - (m.size[1] / 2 - py)
            mx = cx + side * sep - (-1 if side == 1 else 1) * 0
            img.alpha_composite(rm, (int(mx - ox), int(y_center + sink - oy)))

        self._burst_and_text(fx, img, t, u, cx, H, y_center - 210 * u, bubbles=True)
        if fade < 1:
            a = img.getchannel("A").point(lambda v: int(v * fade))
            img.putalpha(a)
        return img

    def _burst_and_text(self, fx, img, t, u, cx, H, clink_y, bubbles):
        """碰杯 / 落盘那一刻的星星、（啤酒的）气泡、上方的文字"""
        from PIL import Image
        import math
        tc = t - BeerFx.CLINK
        if 0 <= tc < 0.9:
            k = tc / 0.9
            for i in range(12):
                a = i / 12 * 6.283 + 0.26
                dist = (60 + 330 * _ease_out(k)) * u
                size = (54 if i % 2 == 0 else 34) * u * (1 - k * 0.8)
                if size < 3:
                    continue
                st = self._star(size)
                if k > 0.6:
                    st = st.copy()
                    st.putalpha(st.getchannel("A").point(lambda v, f=(1 - (k - 0.6) / 0.4): int(v * f)))
                img.alpha_composite(st, (int(cx + math.cos(a) * dist - st.size[0] / 2),
                                         int(clink_y + math.sin(a) * dist * 0.8 - st.size[1] / 2)))
            if tc < 0.25:      # 中间一闪
                fl = self._star(int(260 * u * (1 - tc / 0.25 * 0.5)))
                img.alpha_composite(fl, (int(cx - fl.size[0] / 2), int(clink_y - fl.size[1] / 2)))

        # 气泡：碰杯后从中间往上飘，左右摇摆
        if tc > 0 and bubbles:
            for spread, speed, delay, phase, kind in fx.bubbles:
                bt = tc - delay
                if bt <= 0:
                    continue
                y = clink_y - bt * (380 + 520 * speed) * u
                if y < -60 * u:
                    continue
                x = cx + spread * (140 + 520 * _ease_out(bt / 1.2)) * u + math.sin(bt * 5 + phase) * 26 * u
                b = self._bubble(u, kind)
                img.alpha_composite(b, (int(x - b.size[0] / 2), int(y - b.size[1] / 2)))

        # 文字：第一行从上面滑下来，「干杯！」碰杯时弹出来
        t1, t2 = self._text(fx, u)
        k1 = _ease_out((t - 0.30) / 0.4)
        if k1 > 0:
            y1 = int(H * 0.13 - (1 - k1) * 120 * u)
            img.alpha_composite(t1, (int(cx - t1.size[0] / 2), max(0, y1)))
        if tc > 0:
            pop = _ease_out_back(tc / 0.35, 2.6)
            sc = max(0.05, pop)
            w2, h2 = max(1, int(t2.size[0] * sc)), max(1, int(t2.size[1] * sc))
            tt = t2.resize((w2, h2), Image.BILINEAR) if (w2, h2) != t2.size else t2
            yy = int(H * 0.13 + t1.size[1] + 10 * u + (t2.size[1] - h2) / 2)
            img.alpha_composite(tt, (int(cx - w2 / 2), yy))


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class ScreenFxWindow:
    """在你自己屏幕上播放特效：透明、置顶、鼠标穿透，不进任务栏；不播放时隐藏"""

    def __init__(self, ov):
        import tkinter as tk
        self.ov = ov
        self.win = tk.Toplevel(ov.root)
        self.win.withdraw()
        self.win.attributes("-toolwindow", True)     # 同样：别让直播伴侣抓到这个窗口
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry("1x1+0+0")
        self.win.update_idletasks()
        self.hwnd = user32.GetParent(self.win.winfo_id())
        st = user32.GetWindowLongW(self.hwnd, -20)
        user32.SetWindowLongW(self.hwnd, -20, (st | 0x80000 | 0x80 | 0x08000000 | 0x20) & ~0x40000)
        self.win.withdraw()
        self.showing = False
        self.rect = None

    def stage(self):
        """特效舞台：悬浮窗所在显示器上的一块（宽约 1.3 倍高）。大小、位置按设置"""
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        mon = user32.MonitorFromWindow(self.ov.hwnd, 2)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        user32.GetMonitorInfoW(mon, ctypes.byref(mi))
        r = mi.rcMonitor
        mw, mh = r.right - r.left, r.bottom - r.top
        cfg = self.ov.cfg
        try:
            k = min(1.0, max(0.15, float(cfg.get("effect_screen_size", 1.0))))
        except (TypeError, ValueError):
            k = 1.0
        h = int(mh * k)
        w = min(mw, int(h * 1.3))
        pos = cfg.get("effect_screen_pos", "center") if k < 1.0 else "center"
        margin = int(mh * 0.02) if k < 1.0 else 0
        x = r.left + (mw - w) // 2
        y = r.top + (mh - h) // 2
        if "left" in pos:
            x = r.left + margin
        elif "right" in pos:
            x = r.right - w - margin
        if "top" in pos:
            y = r.top + margin
        elif "bottom" in pos:
            y = r.bottom - h - margin
        return x, y, w, h

    def show_frame(self, img, x, y):
        bb = img.getbbox()                     # 只贴有画面的那一块，省一半以上的开销
        if not bb:
            self.hide()
            return
        img = img.crop(bb)
        x, y = x + bb[0], y + bb[1]
        if not self.showing:
            self.win.deiconify()
            self.win.update_idletasks()
            self.hwnd = user32.GetParent(self.win.winfo_id())     # 第一次真正显示后才有可靠的窗口句柄
            st = user32.GetWindowLongW(self.hwnd, -20)
            user32.SetWindowLongW(self.hwnd, -20, (st | 0x80000 | 0x80 | 0x08000000 | 0x20) & ~0x40000)
            self.showing = True
        self.win.geometry("%dx%d+%d+%d" % (img.size[0], img.size[1], x, y))
        update_layered(self.hwnd, img, x, y)
        user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def hide(self):
        if self.showing:
            self.win.withdraw()
            self.showing = False


_taskbar_list = None


user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]


def taskbar_tab(hwnd, show):
    """用 ITaskbarList 直接在任务栏上加 / 删这个窗口的按钮（不用隐藏窗口）"""
    global _taskbar_list
    try:
        ole32 = ctypes.windll.ole32
        if _taskbar_list is None:
            ole32.CoInitialize(None)

            class GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

            def guid(s):
                import uuid
                u = uuid.UUID(s)
                g = GUID(u.time_low, u.time_mid, u.time_hi_version)
                g.d[:] = list(u.bytes[8:])
                return g
            clsid = guid("56FDF344-FD6D-11D0-958A-006097C9A090")   # CLSID_TaskbarList
            iid = guid("56FDF342-FD6D-11D0-958A-006097C9A090")     # IID_ITaskbarList
            ptr = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr))
            if hr != 0 or not ptr:
                return
            _taskbar_list = ptr
            vt = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vt[3])(ptr)                  # HrInit
        ptr = _taskbar_list
        vt = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)(vt[4] if show else vt[5])  # AddTab / DeleteTab
        fn(ptr, hwnd)
    except Exception as e:
        print("任务栏按钮切换失败：", e)


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


_u = ctypes.WinDLL("user32")
_g = ctypes.WinDLL("gdi32")
_k = ctypes.WinDLL("kernel32")
_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_u.DefWindowProcW.restype = _LRESULT
_u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_u.RegisterClassExW.restype = wintypes.ATOM
_u.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
_u.CreateWindowExW.restype = wintypes.HWND
_u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                               wintypes.HINSTANCE, wintypes.LPVOID]
_u.BeginPaint.restype = wintypes.HDC
_u.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
_u.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
_u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u.AdjustWindowRectEx.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            wintypes.UINT]
_u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
_u.UpdateWindow.argtypes = [wintypes.HWND]
_u.DestroyWindow.argtypes = [wintypes.HWND]
_u.IsWindow.argtypes = [wintypes.HWND]
_u.LoadCursorW.restype = wintypes.HANDLE
_u.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
_u.LoadIconW.restype = wintypes.HICON
_u.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
_u.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
_g.CreateSolidBrush.restype = wintypes.HBRUSH
_g.CreateSolidBrush.argtypes = [wintypes.DWORD]
_g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_g.SetDIBitsToDevice.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                 ctypes.c_void_p, wintypes.UINT]
_k.GetModuleHandleW.restype = wintypes.HMODULE
_k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

_native_windows = {}          # hwnd -> 窗口对象（窗口过程按句柄找到对应对象）


def _native_wndproc(hwnd, msg, wp, lp):
    obj = _native_windows.get(hwnd)
    if obj is not None:
        try:
            r = obj._on_message(msg, wp, lp)
        except Exception as e:
            print("捕获窗口消息处理出错：", e)
            r = None
        if r is not None:
            return r
    return _u.DefWindowProcW(hwnd, msg, wp, lp)


_native_wndproc_ref = _WNDPROC(_native_wndproc)      # 要一直留着引用，不然会被回收
_registered_classes = set()


def _register_class(name):
    if name in _registered_classes:
        return
    wc = _WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(wc)
    wc.style = 0x0001 | 0x0002            # CS_VREDRAW | CS_HREDRAW
    wc.lpfnWndProc = ctypes.cast(_native_wndproc_ref, ctypes.c_void_p)
    wc.hInstance = _k.GetModuleHandleW(None)
    wc.hIcon = _u.LoadIconW(None, ctypes.c_void_p(32512))       # IDI_APPLICATION
    wc.hCursor = _u.LoadCursorW(None, ctypes.c_void_p(32512))   # IDC_ARROW
    wc.lpszClassName = name
    if not _u.RegisterClassExW(ctypes.byref(wc)):
        raise RuntimeError("注册窗口类型失败：" + name)
    _registered_classes.add(name)


def _parse_geom(s, default):
    """"WxH+X+Y" -> (w, h, x, y)；W/H 是画面（客户区）大小，X/Y 是窗口左上角"""
    m = re.match(r"^\s*(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)\s*$", s or "")
    if not m:
        m = re.match(r"^\s*(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)\s*$", default)
    w, h = int(m.group(1)), int(m.group(2))
    x = int(m.group(3).replace("+-", "-").replace("+", ""))
    y = int(m.group(4).replace("+-", "-").replace("+", ""))
    return w, h, x, y


class CaptureWindow:
    """给直播伴侣「窗口捕获」用的窗口：内容和悬浮窗一样，背景是纯色（绿幕可抠图）。
       用 Windows 原生窗口 + 自己独有的窗口类型（不是 Tk 的 TkTopLevel）：
       直播伴侣重启后只按「窗口类型 + 程序名」找回窗口（不看标题），类型独有才不会找错窗口"""
    TITLE = "抖音弹幕 - 直播捕获"
    CLASS_NAME = "DouyinDanmakuCapture"
    GEOM_KEY, OPEN_KEY, ATTR = "capture_geom", "capture_open", "cap"
    DEFAULT_GEOM = "420x620+200+200"
    STYLE = 0x00CF0000                 # WS_OVERLAPPEDWINDOW（有标题栏、能拉大小）
    EXSTYLE = 0x00040000               # WS_EX_APPWINDOW

    def _bg_color(self):
        return self.ov.cfg.get("capture_bg") or "#00FF00"

    def __init__(self, ov, show=True):
        self.ov = ov
        self.bg = self._bg_color()
        self.hidden = False
        self._offscreen = False
        self._born = time.time()
        self._bits = None
        self._bmi = None
        self._size = (0, 0)
        self._alive = True
        _register_class(self.CLASS_NAME)
        w, h, x, y = _parse_geom(ov.cfg.get(self.GEOM_KEY), self.DEFAULT_GEOM)
        r = wintypes.RECT(0, 0, w, h)
        _u.AdjustWindowRectEx(ctypes.byref(r), self.STYLE, False, self.EXSTYLE)
        self.hwnd = _u.CreateWindowExW(self.EXSTYLE, self.CLASS_NAME, self.TITLE, self.STYLE, x, y,
                                       r.right - r.left, r.bottom - r.top, None, None,
                                       _k.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise RuntimeError("创建捕获窗口失败")
        _native_windows[self.hwnd] = self
        self.update()
        if show:
            self.show()

    # ---------- 原生窗口的基本操作 ----------
    def client_size(self):
        r = wintypes.RECT()
        _u.GetClientRect(self.hwnd, ctypes.byref(r))
        return max(1, r.right - r.left), max(1, r.bottom - r.top)

    def exists(self):
        return self._alive and bool(_u.IsWindow(self.hwnd))

    def _on_message(self, msg, wp, lp):
        if msg == 0x000F:                                  # WM_PAINT
            ps = _PAINTSTRUCT()
            hdc = _u.BeginPaint(self.hwnd, ctypes.byref(ps))
            if self._bits is not None:
                w, h = self._size
                _g.SetDIBitsToDevice(hdc, 0, 0, w, h, 0, 0, 0, h, self._bits, ctypes.byref(self._bmi), 0)
            _u.EndPaint(self.hwnd, ctypes.byref(ps))
            return 0
        if msg == 0x0014:                                  # WM_ERASEBKGND：自己画，不用系统擦背景
            return 1
        if msg == 0x0005:                                  # WM_SIZE：大小变了，重画
            self.ov.root.after_idle(self.update)
            return None
        if msg == 0x0010:                                  # WM_CLOSE：点了右上角 ×
            self.ov.root.after_idle(self.close)
            return 0
        if msg == 0x0002:                                  # WM_DESTROY
            self._alive = False
            _native_windows.pop(self.hwnd, None)
            return 0
        return None

    def present(self, img):
        """把一张 RGB/RGBA 图贴到窗口上（大小应等于客户区大小）"""
        w, h = img.size
        data = img.convert("RGB").tobytes("raw", "BGRX")
        self._bits = ctypes.create_string_buffer(data, len(data))
        bmi = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self._bmi = bmi
        self._size = (w, h)
        if self.exists():
            _u.InvalidateRect(self.hwnd, None, False)
            _u.UpdateWindow(self.hwnd)

    def show(self):
        """显示出来（不抢焦点），按设置藏起，开始定时刷新"""
        _u.ShowWindow(self.hwnd, 4)                        # SW_SHOWNOACTIVATE
        self._born = time.time()
        if self.ov.cfg.get("capture_hidden"):
            self.set_hidden(True)
        self.ov.root.after(300, self._keep_fresh)

    # ---------- 藏起 / 任务栏 ----------
    def _grace_over(self):
        """窗口刚打开的一段时间先保留任务栏按钮、保持普通，让直播伴侣找到这个新窗口"""
        return time.time() - self._born >= CAPTURE_GRACE_S

    def _settled(self):
        """可以变透明 / 删任务栏按钮了吗？
           默认：马上就可以（捕获窗口的窗口类型是独有的，直播伴侣按类型 + 程序名就能找回，不受透明影响）。
           设置 capture_wait_live = true 时用旧规则：窗口打开满 15 秒，并且开播以后才藏"""
        if not self.ov.cfg.get("capture_wait_live", False):
            return True
        return self._grace_over() and self.ov.capture_ready()

    def _periodic(self):
        """每 3 秒：藏起时，直播伴侣找窗口的那段时间保持普通（不透明、最底层），之后变透明；按设置删任务栏按钮"""
        settled = self._settled()
        if self.hidden:
            self._apply_invisible(settled)
            self._push_bottom()
        if self.ov.cfg.get("capture_no_taskbar"):
            taskbar_tab(self.hwnd, not settled)

    def _apply_invisible(self, on):
        """原地变成「肉眼看不见」：整体不透明度 1/255 + 鼠标穿透。
           实测（Windows 窗口捕获接口）：这样捕获到的仍是完整、不透明、实时的画面。
           不能移到屏幕外：窗口一离开屏幕，捕获就拿不到新画面（直播里卡住）"""
        st = user32.GetWindowLongW(self.hwnd, -20)
        if on:
            want = st | 0x80000 | 0x20                   # LAYERED | TRANSPARENT(鼠标穿透)
            if want != st:
                user32.SetWindowLongW(self.hwnd, -20, want)
            user32.SetLayeredWindowAttributes(self.hwnd, 0, 1, 2)      # LWA_ALPHA = 1/255
        elif st & 0x80000:
            user32.SetLayeredWindowAttributes(self.hwnd, 0, 255, 2)
            user32.SetWindowLongW(self.hwnd, -20, st & ~0x80000 & ~0x20)

    def _push_bottom(self):
        _u.SetWindowPos(self.hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # HWND_BOTTOM

    def set_hidden(self, on):
        """「藏起」= 原地变成几乎完全透明 + 鼠标穿透（看不见、点不到），并压到最底层。
           开播前（直播伴侣可能还在找窗口）保持普通，见 _settled"""
        if on == self.hidden:
            return
        self.hidden = on
        if on:
            self._apply_invisible(self._settled())
            self._push_bottom()
        else:
            self._apply_invisible(False)
            # 放回最上面一下，方便你看到 / 调整，然后恢复普通层级
            _u.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # HWND_TOPMOST
            _u.SetWindowPos(self.hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # HWND_NOTOPMOST

    def _keep_fresh(self):
        """每 0.3 秒重画一次（捕获端只有窗口重画时才拿到新画面）；每 3 秒检查藏起 / 任务栏"""
        if not self.exists():
            return
        try:
            _u.InvalidateRect(self.hwnd, None, False)
            _u.UpdateWindow(self.hwnd)
            self._fresh_n = getattr(self, "_fresh_n", 0) + 1
            if self._fresh_n % 10 == 0:
                self._periodic()
        except Exception as e:
            print("捕获窗口刷新出错：", e)
        self.ov.root.after(300, self._keep_fresh)

    # ---------- 画面 ----------
    def set_bg(self, col):
        self.bg = col
        self.update()

    def update(self, frame_img=None):
        from PIL import Image
        if not self.exists():
            return
        w, h = self.client_size()
        try:
            content = self.ov.compose(w, None)     # None = 直播画面：不带移动模式、不带状态提示
        except Exception as e:
            print("捕获窗口绘制出错：", e)
            return
        bg = Image.new("RGBA", (w, h), self.bg)
        ch = content.size[1]
        if ch > h:                                 # 内容太高就裁掉上面（最旧的）
            content = content.crop((0, ch - h, content.size[0], ch))
            ch = h
        bg.alpha_composite(content.crop((0, 0, min(w, content.size[0]), ch)), (0, h - ch))   # 贴底边
        self.present(bg)

    # ---------- 位置记忆 / 关闭 ----------
    def remember(self):
        try:
            r = wintypes.RECT()
            _u.GetWindowRect(self.hwnd, ctypes.byref(r))
            w, h = self.client_size()
            if r.left < user32.GetSystemMetrics(76) - 50:      # 在屏幕外（旧版藏起留下的）：只记大小
                old = self.ov.cfg.get(self.GEOM_KEY) or self.DEFAULT_GEOM
                pos = old[old.index("+"):] if "+" in old else "+200+200"
                self.ov.cfg[self.GEOM_KEY] = "%dx%d%s" % (w, h, pos)
                return
            self.ov.cfg[self.GEOM_KEY] = "%dx%d+%d+%d" % (w, h, r.left, r.top)
        except Exception:
            pass

    def close(self):
        if not self._alive:
            return
        self.remember()
        self.ov.cfg[self.OPEN_KEY] = False
        save_config(self.ov.cfg)
        self._alive = False
        _native_windows.pop(self.hwnd, None)
        try:
            _u.DestroyWindow(self.hwnd)
        except Exception:
            pass
        setattr(self.ov, self.ATTR, None)
        try:
            tray_ref["icon"].update_menu()
        except Exception:
            pass


class EffectCaptureWindow(CaptureWindow):
    """特效捕获窗口：平时是一块纯绿幕，收到礼物时播放特效。在直播伴侣里铺满画面、抠掉绿色"""
    TITLE = "大啤酒特效 - 抖音弹幕"
    CLASS_NAME = "DouyinBeerFxCapture"
    GEOM_KEY, OPEN_KEY, ATTR = "effect_geom", "effect_capture_open", "fxcap"
    DEFAULT_GEOM = "960x540+240+160"

    def _bg_color(self):
        return "#00FF00"

    def _keep_fresh(self):
        """空闲时也每秒重新贴一次绿幕图，确保捕获端一开始就能拿到画面"""
        if self.exists() and self.ov.fx_current is None:
            self._idle_tick = getattr(self, "_idle_tick", 0) + 1
            if self._idle_tick % 3 == 0:
                self.update()
        CaptureWindow._keep_fresh(self)

    def update(self, frame_img=None):
        from PIL import Image
        if not self.exists():
            return
        w, h = self.client_size()
        fx = self.ov.fx_current
        bg = Image.new("RGB", (w, h), self.bg)
        if fx is not None and not getattr(fx, "screen_only", False):
            try:
                f = self.ov.fx_renderer.frame(fx, w, h)
                bg.paste(f, (0, 0), f)
            except Exception as e:
                print("特效绘制出错：", e)
        self.present(bg)


def normalize_room(url):
    url = url.strip()
    if url and not url.startswith("http"):
        url = "https://live.douyin.com/" + url.strip("/")
    return url


def ask_room(cfg):
    if cfg.get("room_url"):
        return True
    import tkinter as tk
    from tkinter import simpledialog
    r = tk.Tk()
    r.withdraw()
    url = simpledialog.askstring(APP_NAME, "第一次使用：请输入你的直播间网址或房间号\n（例如 https://live.douyin.com/123456）", parent=r)
    r.destroy()
    url = normalize_room(url or "")
    if not url:
        return False
    cfg["room_url"] = url
    save_config(cfg)
    return True


def login_mode():
    """打开一个看得见的 Edge 窗口，让用户登录抖音，登录信息存到 edge_profile 文件夹"""
    from playwright.sync_api import sync_playwright
    print("即将打开 Edge 窗口，请在里面登录你的抖音账号（扫码即可）。")
    print("登录成功后，直接关闭那个 Edge 窗口就行。")
    with sync_playwright() as p:
        ctx = open_browser(p, visible=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://www.douyin.com/", wait_until="commit", timeout=60000)
        except Exception:
            pass
        told = False
        while True:
            try:
                if all(pg.is_closed() for pg in ctx.pages):
                    break
                ctx.pages[0].wait_for_timeout(1500)
                if not told and has_login(ctx):
                    print("\n✔ 已检测到登录成功！现在可以关闭 Edge 窗口，然后运行 start.bat。")
                    told = True
            except Exception:
                break
        try:
            ok = has_login(ctx)
            ctx.close()
        except Exception:
            ok = told
    print("登录状态：", "已登录 ✔" if ok or told else "没有检测到登录，弹幕可能拿不到（公开直播不受影响）")


def single_instance():
    kernel32.CreateMutexW(None, False, "Local\\DouyinLiveOverlay")
    return kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


# ---------------- 和直播伴侣一起启动 / 一起退出 ----------------
COMPANION_EXE = "直播伴侣.exe"


def process_running(exe_name):
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == exe_name.lower():
                return True
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return False


def find_companion(cfg):
    """直播伴侣的启动程序：先看设置里的 companion_path，再看开始菜单里的「直播伴侣」快捷方式"""
    p = cfg.get("companion_path") or ""
    if p and os.path.exists(p):
        return p
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "$s=New-Object -ComObject WScript.Shell; Get-ChildItem \"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\","
          "\"$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\",\"$env:USERPROFILE\\Desktop\",\"$env:PUBLIC\\Desktop\" "
          "-Recurse -Filter '直播伴侣.lnk' -EA SilentlyContinue | Select-Object -First 1 | % { $s.CreateShortcut($_.FullName).TargetPath }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             encoding="utf-8", creationflags=0x08000000, timeout=20).stdout.strip()
    except Exception:
        out = ""
    if out and os.path.exists(out):
        cfg["companion_path"] = out
        save_config(cfg)
        return out
    return ""


def launch_companion(cfg):
    if process_running(COMPANION_EXE):
        return True
    path = find_companion(cfg)
    if not path:
        message_box("没找到直播伴侣。\n请在 overlay_config.json 里把 companion_path 改成"
                    "「直播伴侣 Launcher.exe」的完整路径。")
        return False
    # 直播伴侣要求管理员权限：用 ShellExecute 打开（和双击图标一样，会弹出 Windows 的确认框）
    shell32 = ctypes.WinDLL("shell32")
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.LPCWSTR, ctypes.c_int]
    r = shell32.ShellExecuteW(None, "open", path, None, os.path.dirname(path), 1) or 0
    if r > 32:
        return True
    if r == 5:      # 在 Windows 确认框里点了「否」
        print("没有同意以管理员身份打开直播伴侣")
        return False
    message_box("打开直播伴侣失败（错误码 %d）。\n可以先手动打开直播伴侣，再点「抖音弹幕悬浮窗」。" % r)
    return False


def follow_companion():
    """等直播伴侣打开；之后它一关，悬浮窗也退出"""
    t0 = time.time()
    while not process_running(COMPANION_EXE):          # 启动器要先检查更新，可能要等一会儿
        if time.time() - t0 > 300 or stop_event.is_set():
            print("5 分钟内没看到直播伴侣，悬浮窗不再跟随它退出")
            return
        time.sleep(3)
    print("已检测到直播伴侣，它关闭时悬浮窗会一起退出")
    missing = 0
    while not stop_event.is_set():
        time.sleep(3)
        missing = 0 if process_running(COMPANION_EXE) else missing + 1
        if missing >= 2:                                 # 连续 6 秒都不在，才算真的关了
            print("直播伴侣已关闭，悬浮窗自动退出")
            cmd_q.put(CMD_QUIT)
            return


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if "--login" in sys.argv:
        login_mode()
        return
    # 没有黑窗口（pythonw 运行）时，把输出写进日志文件
    if sys.stdout is None or "--log" in sys.argv:
        f = open(LOG, "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    with_companion = "--with-companion" in sys.argv
    follow = False
    if with_companion:      # 「直播伴侣（带弹幕）」快捷方式：先把直播伴侣打开
        follow = launch_companion(load_config())   # 没打开成功就不跟随，悬浮窗照常用
    if not single_instance():
        if not with_companion:
            message_box("弹幕悬浮窗已经在运行了。\n看看屏幕右下角的托盘图标（可能在 ^ 里面）。", error=False)
        return
    cfg = load_config()
    for a in sys.argv[1:]:
        if not a.startswith("--"):
            cfg["room_url"] = normalize_room(a)
    if not ask_room(cfg):
        return
    save_config(cfg)     # 把所有可调设置写进文件，方便修改
    load_emoji_index()   # 之前下载过的抖音表情
    tray_ref["cfg"] = cfg
    print("直播间：", cfg["room_url"])
    ft = threading.Thread(target=fetcher, args=(cfg,), daemon=True)
    ft.start()
    threading.Thread(target=hotkeys, daemon=True).start()
    threading.Thread(target=run_tray, daemon=True).start()
    if follow:
        threading.Thread(target=follow_companion, daemon=True).start()
    Overlay(cfg).root.mainloop()
    stop_event.set()
    ft.join(8)       # 给后台浏览器几秒钟正常关闭


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        message_box("弹幕悬浮窗出错了，已退出：\n\n" + str(e)[:300] +
                    "\n\n详细信息在 overlay_log.txt 里，可以截图发给帮你写程序的人。")
