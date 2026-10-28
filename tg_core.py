import asyncio
import os
import re
import tempfile
import threading
import time

from telethon import TelegramClient, errors, utils
from telethon.tl import types
from telethon.tl.functions.channels import GetFullChannelRequest

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_BASE = os.path.join(APP_DIR, "tg_session")
THUMB_DIR = os.path.join(tempfile.gettempdir(), "tgdl_thumbs")
PREVIEW_DIR = os.path.join(tempfile.gettempdir(), "tgdl_preview")

DEFAULT_EXT = {
    "video": ".mp4",
    "photo": ".jpg",
    "audio": ".mp3",
    "voice": ".ogg",
    "document": ".bin",
}

DOWNLOAD_CHUNK = 512 * 1024


def _photo_size_bytes(size):
    if isinstance(size, types.PhotoSizeProgressive):
        return max(size.sizes) if size.sizes else 0
    if isinstance(size, types.PhotoSize):
        return size.size
    if isinstance(size, (types.PhotoCachedSize, types.PhotoStrippedSize)):
        return len(size.bytes)
    return 0


def _download_location(msg):
    """返回 (dc_id, InputFileLocation, 文件大小)。

    照片按最大的有效尺寸选择；dc_id 必须带上，否则跨数据中心文件会一直取不到数据。
    """
    media = getattr(msg, "media", None)
    photo = getattr(media, "photo", None)
    if isinstance(photo, types.Photo):
        sizes = [
            s for s in photo.sizes
            if not isinstance(s, (types.PhotoSizeEmpty, types.PhotoPathSize))
        ]
        if sizes:
            best = max(sizes, key=_photo_size_bytes)
            if isinstance(best, (types.PhotoCachedSize, types.PhotoStrippedSize)):
                return None, None, 0
            location = types.InputPhotoFileLocation(
                id=photo.id,
                access_hash=photo.access_hash,
                file_reference=photo.file_reference,
                thumb_size=best.type,
            )
            return getattr(photo, "dc_id", None), location, _photo_size_bytes(best)
    try:
        dc_id, location = utils.get_input_location(msg)
    except Exception:  # noqa: BLE001
        return None, None, 0
    size = (msg.file.size if getattr(msg, "file", None) else 0) or 0
    return dc_id, location, size



def thumb_file_path(dialog_id, mid):
    return os.path.join(THUMB_DIR, f"{abs(int(dialog_id))}_{mid}.jpg")


def build_proxy(cfg):
    """根据配置构造 Telethon 代理参数，未启用时返回 None。"""
    if not cfg.get("proxy_enabled"):
        return None
    try:
        port = int(cfg.get("proxy_port") or 7897)
    except (TypeError, ValueError):
        port = 7897
    return {
        "proxy_type": (cfg.get("proxy_type") or "socks5").lower(),
        "addr": cfg.get("proxy_host") or "127.0.0.1",
        "port": port,
        "rdns": True,
        "username": None,
        "password": None,
    }


def _clean(name, fallback, ext=""):
    name = (name or "").strip().replace("\\", "_")
    name = re.sub(r'[<>:"/|?*\x00-\x1f]', "_", name)
    name = name.strip(" .")
    if not name:
        name = fallback + (ext or "")
    if len(name) > 120:
        root, dot, suffix = name.rpartition(".")
        if dot and 0 < len(suffix) <= 10:
            name = root[:110] + "." + suffix
        else:
            name = name[:120]
    return name


def _default_ext(ftype):
    return DEFAULT_EXT.get(ftype, ".bin")


def _message_meta(m):
    """把 Telethon 消息转成界面需要的元数据。"""
    if m is None or m.id is None:
        return None
    has_file = m.file is not None
    text = (m.message or "").replace("\r", " ").replace("\n", " ").strip()
    if not has_file and not text:
        return None

    if m.video:
        ftype = "video"
    elif m.photo:
        ftype = "photo"
    elif m.audio:
        ftype = "audio"
    elif m.voice:
        ftype = "voice"
    elif m.document:
        ftype = "document"
    else:
        ftype = "text"

    size = (m.file.size if m.file else 0) or 0
    name = (m.file.name if m.file else "") or ""
    return {
        "id": m.id,
        "type": ftype,
        "size": size,
        "name": name,
        "caption": text[:90],
        "date": m.date.astimezone().strftime("%Y-%m-%d %H:%M"),
        "downloadable": bool(has_file and ftype != "text"),
    }


def _build_filename(m, ftype):
    name = (m.file.name if m.file else "") or ""
    ext = (m.file.ext if m.file else "") or ""
    if not name:
        name = f"{m.id}{ext or _default_ext(ftype)}"
    name = _clean(name, f"file_{m.id}", _default_ext(ftype))
    date = m.date.astimezone().strftime("%Y%m%d")
    return f"{date}_{m.id}_{name}"


_KNOWN_EXT_RE = re.compile(
    r"(?i)\.(?:rar|zip|7z|tar|gz|xz|zst|mp4|mkv|avi|mov|webm|flv|wmv|m4v|"
    r"mp3|flac|wav|ogg|opus|m4a|aac|jpg|jpeg|png|gif|webp|bmp|tif|tiff|"
    r"pdf|txt|docx?|xlsx?|pptx?|apk|exe|msi|iso|bin|srt|ass|epub|mobi)$"
)
_PART_SUFFIX_RE = re.compile(
    r"(?i)(?:[\s._\-–—]+)(?:part|pt|vol(?:ume)?|ep(?:isode)?|chapter|ch|disc|disk|cd)"
    r"[\s._\-–—]*\d{1,3}$"
)
_CN_PART_SUFFIX_RE = re.compile(
    r"(?:[\s._\-–—]*)第\s*[0-9一二三四五六七八九十百千]+\s*(?:部分|分卷|章节|卷|集|章|篇|期)$"
)
_BRACKET_NUM_RE = re.compile(r"(?:[\s._\-–—]*)[\(（\[【]\s*\d{1,3}\s*[\)）\]】]$")
_TRAIL_NUM_RE = re.compile(r"(?:[\s._\-–—]+)\d{1,3}$")
_ORIENT_SUFFIX_RE = re.compile(r"(?:[\s._\-–—]*)(?:前篇|后篇|前編|後編|上卷|下卷|上集|下集|上篇|下篇)$")
_GENERIC_PREFIXES = {
    "video", "videos", "file", "files", "document", "documents", "audio", "photo",
    "photos", "image", "images", "download", "downloads", "telegram", "untitled",
    "img", "dsc", "dscf", "vid", "pxl", "screenshot", "screen", "camera", "dcim",
    "视频", "文件", "图片", "音频", "压缩包", "未命名", "新建文件夹", "截图", "屏幕截图", "录屏",
}
_AD_MARKERS = (
    "打赏", "优惠", "限时", "会员", "加入", "购买", "淘宝", "t.me", "http", "https",
    "www.", "搜索引擎", "群组", "频道推广", "次卡", "售价", "福利", "活动",
)
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _filename_stem(m):
    name = (m.file.name if m.file else "") or ""
    if not name:
        return ""
    return _KNOWN_EXT_RE.sub("", name.strip()).strip()


def _strip_part_suffix(stem):
    text = stem.strip()
    changed = False
    for _ in range(4):
        new = text
        for regex in (
            _PART_SUFFIX_RE,
            _CN_PART_SUFFIX_RE,
            _BRACKET_NUM_RE,
            _TRAIL_NUM_RE,
            _ORIENT_SUFFIX_RE,
        ):
            new = regex.sub("", new)
        new = new.strip(" ._-–—")
        if not new or new == text:
            break
        text = new
        changed = True
    return text.strip(" ._-–—"), changed


def _looks_like_ad(text):
    if not text:
        return False
    if len(text) > 90:
        return True
    lower = text.lower()
    return any(marker in lower for marker in _AD_MARKERS)


def _derive_prefix(m):
    """从文件名（必要时用描述首行）提取用于分类的公共前缀。"""
    candidates = []
    stem = _filename_stem(m)
    if stem:
        candidates.append(stem)
    caption = (m.message or "").strip()
    if caption:
        first = caption.splitlines()[0].strip()
        if first and not _looks_like_ad(first):
            candidates.append(first[:100])
    for candidate in candidates:
        prefix, changed = _strip_part_suffix(candidate)
        if len(prefix) < 2:
            continue
        if len(prefix) < 3 and not re.search(r"[\u4e00-\u9fff]", prefix):
            continue
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", prefix):
            continue
        first_token = re.split(r"[_.\-\s]+", prefix.strip().lower())[0]
        if first_token in _GENERIC_PREFIXES:
            continue
        if _looks_like_ad(prefix):
            continue
        return prefix[:80], changed
    return "", False


def _prefix_key(text):
    return re.sub(r"[_.\-–—\s]+", " ", text.lower()).strip()


def _safe_folder(name):
    name = _clean(name, "")[:60].strip(" .")
    if not name:
        return ""
    if name.upper().split(".")[0] in _RESERVED_NAMES:
        name = "_" + name
    return name


def _album_prefix_map(msgs):
    """同一相册（grouped_id）共用一条可读标题，避免无描述的分图落回根目录。"""
    albums = {}
    for m in msgs:
        gid = getattr(m, "grouped_id", None)
        if gid:
            albums.setdefault(gid, []).append(m)
    mapping = {}
    for members in albums.values():
        ordered = sorted(
            members,
            key=lambda x: (0 if (x.message or "").strip() else 1, x.id),
        )
        prefix = ""
        for m in ordered:
            candidate, _ = _derive_prefix(m)
            if candidate:
                prefix = candidate
                break
        if prefix:
            for m in members:
                mapping[m.id] = prefix
    return mapping


def _time_cluster_prefix_map(msgs, window_seconds=600):
    """连续发布的一批图片共用最近的标题，处理“先发标题、再连发多图”的频道。"""
    dated = [m for m in msgs if getattr(m, "date", None) is not None]
    if len(dated) < 2:
        return {}
    dated.sort(key=lambda m: (m.date, m.id))
    clusters = []
    for m in dated:
        if clusters and (m.date - clusters[-1][-1].date).total_seconds() <= window_seconds:
            clusters[-1].append(m)
        else:
            clusters.append([m])
    mapping = {}
    for members in clusters:
        if len(members) < 2:
            continue
        prefix = ""
        ordered = sorted(
            members,
            key=lambda x: (0 if (x.message or "").strip() else 1, x.id),
        )
        for m in ordered:
            candidate, _ = _derive_prefix(m)
            if candidate:
                prefix = candidate
                break
        if not prefix:
            continue
        for m in members:
            own, _ = _derive_prefix(m)
            if not own:
                mapping[m.id] = prefix
    return mapping


def plan_download_folders(msgs, channel_folder, enabled=True):
    """按文件名公共前缀 / 相册标题 / 连续批次规划子目录。"""
    if not enabled:
        return {m.id: channel_folder for m in msgs}
    album_map = _album_prefix_map(msgs)
    time_map = _time_cluster_prefix_map(msgs)
    info = {}
    groups = {}
    for m in msgs:
        if m.id in album_map:
            prefix, changed = album_map[m.id], True
        elif m.id in time_map:
            prefix, changed = time_map[m.id], True
        else:
            prefix, changed = _derive_prefix(m)
        key = _prefix_key(prefix) if prefix else ""
        info[m.id] = (prefix, changed, key)
        if key:
            groups.setdefault(key, []).append(m.id)
    plan = {}
    for m in msgs:
        prefix, changed, key = info[m.id]
        if key and (changed or len(groups.get(key, [])) > 1):
            target = _safe_folder(prefix)
            plan[m.id] = os.path.join(channel_folder, target) if target else channel_folder
        else:
            plan[m.id] = channel_folder
    return plan


class TelegramService:
    """在独立线程里跑 asyncio，负责所有 Telegram 网络操作。"""

    def __init__(self, events, config):
        self.events = events
        self.config = dict(config)
        self.client = None
        self.loop = None
        self.thread = None
        self._pending = []
        self._login_gen = 0
        self._password_future = None
        self._authorized = False
        self._dialog_entities = {}
        self.messages = {}
        self._dl_tasks = []
        self._downloading = False
        self._stop_download = False
        self._thumb_sem = asyncio.Semaphore(4)
        self._connect_lock = None

    async def _ensure_connected(self, wait=90):
        """代理节点抖动会让 Telethon 掉线；断开后重建客户端，避免整个队列瞬间失败。"""
        if self.client is not None and self.client.is_connected():
            return True
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self.client is not None and self.client.is_connected():
                return True
            deadline = time.time() + wait
            self.post("log", "Telegram 连接已断开，正在重连…")
            first = True
            while time.time() < deadline:
                try:
                    if first and self.client is not None:
                        first = False
                        try:
                            await asyncio.wait_for(self.client.connect(), 20)
                        except Exception:  # noqa: BLE001
                            pass
                        if self.client.is_connected():
                            self.post("log", "Telegram 连接已恢复")
                            return True
                    old = self.client
                    if old is not None:
                        try:
                            await asyncio.wait_for(old.disconnect(), 10)
                        except Exception:  # noqa: BLE001
                            pass
                    client = self._new_client()
                    await asyncio.wait_for(client.connect(), 30)
                    if not await client.is_user_authorized():
                        self.post("log", "登录状态失效，请在页面重新扫码登录")
                        self.post("status", "登录已失效，需要重新扫码")
                        return False
                    self.client = client
                    self.post("log", "Telegram 连接已重建")
                    return True
                except Exception as exc:  # noqa: BLE001
                    self.post("log", f"重连失败，稍后重试：{exc}")
                await asyncio.sleep(3)
            self.post("log", f"重连 {wait} 秒仍未成功，请检查 Clash 节点")
            return False

    # ---------- 线程与事件 ----------
    def post(self, event, *args):
        self.events.put((event, args))

    def start(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        for coro in self._pending:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        self._pending.clear()
        self.loop.run_forever()

    def submit(self, coro):
        if self.loop is None:
            self._pending.append(coro)
            return
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def _done(f):
            try:
                f.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                self.post("log", f"[内部错误] {exc}")

        fut.add_done_callback(_done)

    def stop(self):
        async def _shutdown():
            try:
                if self.client:
                    await self.client.disconnect()
            except Exception:
                pass
            self.loop.stop()

        if self.loop:
            asyncio.run_coroutine_threadsafe(_shutdown(), self.loop)

    # ---------- 连接与登录 ----------
    def configure(self, cfg):
        self.config = dict(cfg)
        self.submit(self._connect())

    def _new_client(self):
        api_id = int(self.config.get("api_id") or 0)
        api_hash = (self.config.get("api_hash") or "").strip()
        return TelegramClient(
            SESSION_BASE,
            api_id,
            api_hash,
            proxy=build_proxy(self.config),
            device_model="Telegram Batch Downloader",
            system_version="Windows 10",
            app_version="1.0",
            lang_code="zh",
            system_lang_code="zh-CN",
            connection_retries=5,
            timeout=30,
            retry_delay=2,
        )

    async def _connect(self):
        self._login_gen += 1
        gen = self._login_gen
        self._authorized = False
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        try:
            api_id = int(self.config.get("api_id") or 0)
            api_hash = (self.config.get("api_hash") or "").strip()
            if not api_id or not api_hash:
                self.post("status", "请先在设置里填写 api_id / api_hash")
                return
            self.client = self._new_client()
            self.post("status", "正在连接 Telegram…")
            await self.client.connect()
            if await self.client.is_user_authorized():
                self._authorized = True
                me = await self.client.get_me()
                name = " ".join(x for x in [getattr(me, "first_name", ""), getattr(me, "last_name", "")] if x) or "已登录"
                self.post("login_ok", name)
                await self._load_dialogs()
                return
            await self._qr_login_loop(gen)
        except Exception as exc:  # noqa: BLE001
            self.post("log", f"连接失败：{exc}")
            self.post("status", "连接失败（检查代理或网络）")

    async def _qr_login_loop(self, gen):
        while gen == self._login_gen and not self._authorized:
            try:
                qr = await self.client.qr_login()
                if gen != self._login_gen:
                    return
                self.post("login_qr", qr.url)
                try:
                    await qr.wait(timeout=40)
                except asyncio.TimeoutError:
                    if gen == self._login_gen:
                        self.post("login_status", "二维码已过期，正在刷新…")
                    continue
                except errors.SessionPasswordNeededError:
                    if gen != self._login_gen:
                        return
                    self.post("need_password")
                    password = await self._wait_password()
                    if password is None:
                        continue
                    try:
                        await self.client.sign_in(password=password)
                    except errors.PasswordHashInvalidError:
                        self.post("login_status", "两步验证密码错误，请重新扫码")
                        continue
                if await self.client.is_user_authorized():
                    self._authorized = True
                    me = await self.client.get_me()
                    name = " ".join(x for x in [getattr(me, "first_name", ""), getattr(me, "last_name", "")] if x) or "已登录"
                    self.post("login_ok", name)
                break
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                self.post("log", f"登录异常：{exc}")
                await asyncio.sleep(3)
        if self._authorized:
            await self._load_dialogs()

    async def _wait_password(self):
        loop = asyncio.get_running_loop()
        self._password_future = loop.create_future()
        return await self._password_future

    def submit_password(self, password):
        if self.loop is None or self._password_future is None:
            return
        if self._password_future.done():
            return
        value = password if password else None
        self.loop.call_soon_threadsafe(self._password_future.set_result, value)

    # ---------- 频道与消息 ----------
    async def _load_dialogs(self):
        try:
            dialogs = await self.client.get_dialogs(limit=None)
            linked_parents = {}
            for dialog in dialogs:
                entity = dialog.entity
                if getattr(entity, "broadcast", False):
                    linked_id = getattr(entity, "linked_chat_id", None)
                    if linked_id:
                        title = getattr(dialog, "title", None) or getattr(entity, "title", "") or str(dialog.id)
                        linked_parents[linked_id] = {"id": dialog.id, "title": title}

            items = []
            seen = set()
            me = getattr(self.client, "me", None)

            for dialog in dialogs:
                entity = dialog.entity
                if getattr(dialog, "is_user", False):
                    if getattr(entity, "is_self", False):
                        items.append({"id": dialog.id, "title": "我的收藏", "kind": "收藏"})
                        self._dialog_entities[dialog.id] = entity
                        seen.add(dialog.id)
                    continue

                title = getattr(dialog, "title", None) or getattr(entity, "title", "") or str(dialog.id)
                entity_id = getattr(entity, "id", None)
                if entity_id in linked_parents:
                    parent = linked_parents[entity_id]
                    kind = "评论"
                    title = f"{parent['title']} · 评论"
                    item = {
                        "id": dialog.id,
                        "title": title,
                        "kind": kind,
                        "parent_id": parent["id"],
                        "parent_title": parent["title"],
                    }
                else:
                    broadcast = bool(getattr(entity, "broadcast", False))
                    kind = "频道" if broadcast else "群组"
                    item = {"id": dialog.id, "title": title, "kind": kind}
                    if broadcast:
                        item["has_comments"] = True

                if dialog.id in seen:
                    continue
                items.append(item)
                self._dialog_entities[dialog.id] = entity
                seen.add(dialog.id)

            if me is not None and me.id not in seen:
                items.append({"id": me.id, "title": "我的收藏", "kind": "收藏"})
                self._dialog_entities[me.id] = me

            def sort_key(item):
                kind = item["kind"]
                if kind == "收藏":
                    return (0, "", 0)
                if kind == "频道":
                    return (1, item["title"].lower(), 0)
                if kind == "评论":
                    parent = item.get("parent_title") or item["title"].rsplit(" · 评论", 1)[0]
                    return (1, parent.lower(), 1)
                return (2, item["title"].lower(), 0)

            items.sort(key=sort_key)
            self.post("dialogs", items)
            self.post("status", "已连接")
        except Exception as exc:  # noqa: BLE001
            self.post("log", f"读取频道失败：{exc}")

    def open_comments(self, dialog_id):
        self.submit(self._open_comments(dialog_id))

    async def _open_comments(self, dialog_id):
        entity = self._dialog_entities.get(dialog_id)
        if entity is None:
            try:
                entity = await self.client.get_entity(dialog_id)
                self._dialog_entities[dialog_id] = entity
            except Exception as exc:  # noqa: BLE001
                self.post("comment_fail", dialog_id, f"找不到频道：{exc}")
                return
        linked_id = getattr(entity, "linked_chat_id", None)
        if getattr(entity, "broadcast", False):
            try:
                full = await self.client(GetFullChannelRequest(channel=entity))
                linked_id = getattr(full.full_chat, "linked_chat_id", None) or linked_id
            except Exception:  # noqa: BLE001
                pass
        if not linked_id:
            self.post("comment_fail", dialog_id, "该频道没有关联的评论区")
            return
        try:
            linked = await self.client.get_entity(linked_id)
        except Exception as exc:  # noqa: BLE001
            self.post("comment_fail", dialog_id, f"无法打开评论区（可能未加入讨论组）：{exc}")
            return
        comment_id = utils.get_peer_id(linked)
        self._dialog_entities[comment_id] = linked
        parent_title = getattr(entity, "title", "") or str(dialog_id)
        self.post(
            "comment_dialog",
            {
                "id": comment_id,
                "title": f"{parent_title} · 评论",
                "kind": "评论",
                "parent_id": dialog_id,
                "parent_title": parent_title,
            },
        )

    def load_messages(self, dialog_id, offset_id=0, limit=100):
        self.submit(self._load_messages(dialog_id, offset_id, limit))

    async def _load_messages(self, dialog_id, offset_id, limit):
        entity = self._dialog_entities.get(dialog_id)
        if not entity:
            self.post("log", "找不到该频道，请刷新频道列表")
            return
        try:
            msgs = await self.client.get_messages(entity, limit=limit, offset_id=offset_id or 0)
            meta = []
            for m in msgs:
                data = _message_meta(m)
                if data is None:
                    continue
                self.messages[(dialog_id, m.id)] = m
                meta.append(data)
            next_offset = msgs[-1].id if msgs else 0
            self.post("messages", dialog_id, meta, next_offset, bool(msgs))
        except Exception as exc:  # noqa: BLE001
            self.post("log", f"读取消息失败：{exc}")
            self.post("messages", dialog_id, [], 0, False)

    # ---------- 定时追更检查 ----------
    def check_new(self, dialog_id, last_id=None):
        self.submit(self._check_new(dialog_id, last_id))

    async def _check_new(self, dialog_id, last_id):
        entity = self._dialog_entities.get(dialog_id)
        if entity is None:
            try:
                entity = await self.client.get_entity(dialog_id)
                self._dialog_entities[dialog_id] = entity
            except Exception as exc:  # noqa: BLE001
                self.post("log", f"追更：找不到频道 {dialog_id}：{exc}")
                self.post("auto_new", dialog_id, "", [], last_id, 0)
                return
        title = getattr(entity, "title", "") or ""
        try:
            if last_id is None:
                msgs = await self.client.get_messages(entity, limit=1)
                new_last = msgs[0].id if msgs else 0
                self.post("auto_new", dialog_id, title, [], new_last, 0)
                return
            msgs = await self.client.get_messages(entity, limit=100, min_id=last_id)
            new_last = max([m.id for m in msgs] + [last_id])
            items = []
            for m in msgs:
                data = _message_meta(m)
                if data is None or not data["downloadable"]:
                    continue
                self.messages[(dialog_id, m.id)] = m
                items.append(data)
            self.post("auto_new", dialog_id, title, items, new_last, len(msgs))
        except Exception as exc:  # noqa: BLE001
            self.post("log", f"追更检查失败：{exc}")
            self.post("auto_new", dialog_id, title, [], last_id, 0)

    # ---------- 缩略图 ----------
    def fetch_thumb(self, dialog_id, mid):
        self.submit(self._fetch_thumb(dialog_id, mid))

    async def _fetch_thumb(self, dialog_id, mid):
        msg = self.messages.get((dialog_id, mid))
        if msg is None:
            self.post("thumb_fail", mid)
            return
        try:
            os.makedirs(THUMB_DIR, exist_ok=True)
        except Exception:  # noqa: BLE001
            self.post("thumb_fail", mid)
            return
        path = thumb_file_path(dialog_id, mid)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            self.post("thumb", mid, path)
            return
        try:
            async with self._thumb_sem:
                result = await self.client.download_media(msg, file=path, thumb=-1)
            if result and os.path.exists(path) and os.path.getsize(path) > 0:
                self.post("thumb", mid, path)
            else:
                self.post("thumb_fail", mid)
        except Exception:  # noqa: BLE001
            self.post("thumb_fail", mid)

    # ---------- 大图 / 视频预览 ----------
    def preview_media(self, dialog_id, mid):
        self.submit(self._preview_media(dialog_id, mid))

    async def _preview_media(self, dialog_id, mid):
        msg = self.messages.get((dialog_id, mid))
        if msg is None:
            self.post("preview_fail", mid, "消息不存在")
            return
        meta = _message_meta(msg)
        if meta is None or not meta["downloadable"]:
            self.post("preview_fail", mid, "该消息没有可预览的文件")
            return
        try:
            os.makedirs(PREVIEW_DIR, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self.post("preview_fail", mid, f"无法创建预览目录：{exc}")
            return
        path = os.path.join(PREVIEW_DIR, _build_filename(msg, meta["type"]))
        if os.path.exists(path) and os.path.getsize(path) > 0:
            self.post("preview_ready", mid, path, meta["type"])
            return
        temp = path + ".part"
        last = [0.0]

        def progress(received, total):
            now = time.time()
            if now - last[0] > 0.2:
                last[0] = now
                self.post("preview_progress", mid, received, total or (msg.file.size if msg.file else 0) or 0)

        try:
            await self._download_with_retry(msg, path, progress, attempts=2)
            self.post("preview_ready", mid, path, meta["type"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.post("preview_fail", mid, str(exc))

    async def _download_with_retry(self, msg, path, progress_cb, attempts=3):
        temp = path + ".part"
        try:
            if os.path.exists(temp) and os.path.getsize(temp) == 0:
                os.remove(temp)
        except OSError:
            pass
        size = (msg.file.size if msg.file else 0) or 0
        last_error = None
        for attempt in range(attempts):
            try:
                await self._download_attempt(msg, temp, size, progress_cb)
                os.replace(temp, path)
                return
            except asyncio.CancelledError:
                raise
            except errors.FloodWaitError as exc:
                last_error = exc
                self.post("log", f"触发限流，等待 {exc.seconds}s 后继续…")
                await asyncio.sleep(exc.seconds + 1)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise last_error if last_error else RuntimeError("下载失败")

    async def _watch(self, coro, last, stall=45):
        """等待下载协程；超过 stall 秒没有任何进展就取消并抛错，防止断线后永久挂起。"""
        task = asyncio.create_task(coro)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=5)
                if task in done:
                    return task.result()
                if time.time() - last["time"] > stall:
                    task.cancel()
                    try:
                        await task
                    except BaseException:  # noqa: BLE001
                        pass
                    raise TimeoutError(f"{stall} 秒无进展")
        finally:
            if not task.done():
                task.cancel()

    async def _ranged_download(self, location, dc_id, temp, size, existing, progress_cb):
        """分块续传，供大文件使用。"""
        last = {"time": time.time()}

        async def run():
            sent = existing
            with open(temp, "ab") as fh:
                async for chunk in self.client.iter_download(
                    location,
                    offset=existing,
                    file_size=size,
                    request_size=DOWNLOAD_CHUNK,
                    dc_id=dc_id,
                ):
                    fh.write(chunk)
                    sent += len(chunk)
                    last["time"] = time.time()
                    progress_cb(sent, size)

        await self._watch(run(), last, 45)

    async def _download_attempt(self, msg, temp, size, progress_cb):
        if not await self._ensure_connected():
            raise RuntimeError("Telegram 连接不可用")
        existing = os.path.getsize(temp) if os.path.exists(temp) else 0
        if existing and size and existing >= size:
            return
        dc_id, location, location_size = _download_location(msg)
        if not size:
            size = location_size or (msg.file.size if getattr(msg, "file", None) else 0) or 0
        last = {"time": time.time()}

        def progress(received, total):
            last["time"] = time.time()
            progress_cb(received, total)

        if location is None or not size:
            if existing:
                self._remove_quietly(temp)
            await self._watch(
                self.client.download_media(msg, file=temp, progress_callback=progress), last, 60
            )
            return
        # 小文件（小说 txt 等）直接走默认下载，一次请求即可，避免额外补救援路径
        if size <= DOWNLOAD_CHUNK:
            if existing:
                self._remove_quietly(temp)
            await self._watch(
                self.client.download_media(msg, file=temp, progress_callback=progress), last, 30
            )
            return
        try:
            await self._ranged_download(location, dc_id, temp, size, existing, progress)
            if os.path.getsize(temp) == size:
                return
            raise RuntimeError("文件大小不一致")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.post("log", f"分块下载失败（{exc}），改用默认方式重试")
            self._remove_quietly(temp)
        await self._watch(
            self.client.download_media(msg, file=temp, progress_callback=progress), last, 60
        )

    # ---------- 下载 ----------
    def download(self, ids, out_dir, channel_title, concurrency=4, dialog_id=None):
        if self._downloading:
            self.post("log", "已有下载任务在运行，本次请求被忽略")
            return False
        self._downloading = True
        self._stop_download = False
        self.submit(self._download_many(ids, out_dir, channel_title, concurrency, dialog_id))
        return True

    async def _load_missing_messages(self, dialog_id, ids):
        """服务重启或页面较旧时缓存可能为空，按需从 Telegram 补齐消息。"""
        if dialog_id is None:
            return
        missing = [mid for mid in ids if (dialog_id, mid) not in self.messages]
        if not missing:
            return
        entity = self._dialog_entities.get(dialog_id)
        if entity is None:
            try:
                entity = await self.client.get_entity(dialog_id)
                self._dialog_entities[dialog_id] = entity
            except Exception as exc:  # noqa: BLE001
                self.post("log", f"无法读取频道，补齐消息失败：{exc}")
                return
        loaded = 0
        for start in range(0, len(missing), 100):
            chunk = missing[start:start + 100]
            try:
                fetched = await self.client.get_messages(entity, ids=chunk)
            except Exception as exc:  # noqa: BLE001
                self.post("log", f"补齐消息失败：{exc}")
                continue
            if not isinstance(fetched, list):
                fetched = [fetched]
            for msg in fetched:
                if msg is not None and getattr(msg, "id", None):
                    self.messages[(dialog_id, msg.id)] = msg
                    loaded += 1
        if loaded:
            self.post("log", f"已补齐 {loaded} 条消息")

    async def _download_many(self, ids, out_dir, channel_title, concurrency=4, dialog_id=None):
        stats = {"done": 0, "skipped": 0, "failed": len(ids), "stopped": 0}
        try:
            channel_folder = os.path.join(out_dir, _clean(channel_title, "Telegram"))
            try:
                os.makedirs(channel_folder, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                self.post("log", f"无法创建目录 {channel_folder}：{exc}")
                return

            if not await self._ensure_connected():
                self.post("log", "连接不可用，已停止本次下载")
                return
            await self._load_missing_messages(dialog_id, ids)
            msgs = [self.messages[(dialog_id, mid)] for mid in ids if (dialog_id, mid) in self.messages]
            if not msgs:
                self.post("log", "没有可下载的消息，请刷新页面后重新加载该频道再试")
                return
            stats["failed"] = max(0, len(ids) - len(msgs))
            self.post("log", f"开始下载 {len(msgs)} 个文件到 {channel_folder}")
            plan = plan_download_folders(
                msgs, channel_folder, bool(self.config.get("classify_enabled", True))
            )
            target_dirs = set(plan.values())
            subdirs = {os.path.basename(p) for p in target_dirs if p != channel_folder}
            if subdirs:
                self.post("log", f"自动分类：{len(subdirs)} 个子文件夹")

            sem = asyncio.Semaphore(max(1, min(64, int(concurrency or 8))))
            big_sem = asyncio.Semaphore(max(1, min(4, max(1, int(concurrency or 8)) // 6)))
            big_size = 8 * 1024 * 1024

            def size_of(m):
                return (m.file.size if getattr(m, "file", None) else 0) or 0

            # 小文件优先，大文件单独限流，避免几个几十 MB 的文件占满全部并发
            ordered = sorted(msgs, key=size_of)
            tasks = []
            for msg in ordered:
                folder = plan.get(msg.id, channel_folder)
                extra = big_sem if size_of(msg) > big_size else None
                tasks.append(
                    asyncio.create_task(
                        self._download_one(msg, folder, sem, channel_folder, extra)
                    )
                )
            self._dl_tasks = tasks
            results = await asyncio.gather(*tasks, return_exceptions=True)

            stats = {"done": 0, "skipped": 0, "failed": max(0, len(ids) - len(msgs)), "stopped": 0}
            for res in results:
                if isinstance(res, asyncio.CancelledError):
                    stats["stopped"] += 1
                elif isinstance(res, Exception):
                    stats["failed"] += 1
                elif res in stats:
                    stats[res] += 1
                else:
                    stats["failed"] += 1
        except asyncio.CancelledError:
            stats["stopped"] = max(stats["stopped"], len(ids) - stats["done"] - stats["skipped"] - stats["failed"])
            self.post("log", "下载已取消")
            raise
        except Exception as exc:  # noqa: BLE001
            self.post("log", f"下载失败：{exc}")
        finally:
            if self._stop_download:
                stats["stopped"] = max(
                    stats["stopped"], len(ids) - stats["done"] - stats["skipped"] - stats["failed"]
                )
            self._dl_tasks = []
            self._downloading = False
            self._stop_download = False
            self.post("dl_done", stats)

    async def _download_one(self, msg, folder, sem, legacy_folder=None, big_sem=None):
        if big_sem is not None:
            async with big_sem:
                async with sem:
                    return await self._download_body(msg, folder, legacy_folder)
        async with sem:
            return await self._download_body(msg, folder, legacy_folder)

    async def _download_body(self, msg, folder, legacy_folder=None):
        if self._stop_download:
            return "stopped"
        ftype = _message_meta(msg)["type"]
        filename = _build_filename(msg, ftype)
        path = os.path.join(folder, filename)
        size_hint = (msg.file.size if msg.file else 0) or 1
        if not (os.path.exists(path) and os.path.getsize(path) > 0) and legacy_folder and legacy_folder != folder:
            legacy_path = os.path.join(legacy_folder, filename)
            if os.path.exists(legacy_path) and os.path.getsize(legacy_path) > 0:
                path = legacy_path
        if os.path.exists(path) and os.path.getsize(path) > 0:
            self.post("dl_progress", msg.id, size_hint, size_hint)
            self.post("dl_file_done", msg.id, "已存在，跳过")
            return "skipped"
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self.post("dl_file_done", msg.id, f"失败：无法创建目录 {exc}")
            return "failed"
        last = [0.0]

        def progress(received, total):
            if size_hint <= DOWNLOAD_CHUNK:
                return
            now = time.time()
            if now - last[0] > 0.25 or (total and received >= total):
                last[0] = now
                self.post("dl_progress", msg.id, received, total or size_hint)

        try:
            attempts = 2 if size_hint <= DOWNLOAD_CHUNK else 3
            await self._download_with_retry(msg, path, progress, attempts=attempts)
            self.post("dl_progress", msg.id, size_hint, size_hint)
            self.post("dl_file_done", msg.id, "完成")
            return "done"
        except asyncio.CancelledError:
            self.post("dl_file_done", msg.id, "已停止，保留断点")
            raise
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "disconnected" in text.lower() or "connection" in text.lower():
                text = "网络连接中断，已自动重试仍失败"
            self.post("dl_file_done", msg.id, f"失败：{text}（保留断点）")
            return "failed"

    @staticmethod
    def _remove_quietly(path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def stop_download(self):
        self._stop_download = True
        if self.loop:
            def _cancel():
                for task in self._dl_tasks:
                    task.cancel()

            self.loop.call_soon_threadsafe(_cancel)
