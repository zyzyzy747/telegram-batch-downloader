import base64
import io
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser

import qrcode
from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from tg_core import (
    APP_DIR,
    PREVIEW_DIR,
    THUMB_DIR,
    TelegramService,
    _build_filename,
    _clean,
    _message_meta,
    plan_download_folders,
    thumb_file_path,
)

WEB_DIR = os.path.join(APP_DIR, "web")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
HISTORY_PATH = os.path.join(APP_DIR, "download_history.json")
SUBS_PATH = os.path.join(APP_DIR, "subscriptions.json")

DEFAULT_CONFIG = {
    "api_id": 0,
    "api_hash": "",
    "proxy_enabled": True,
    "proxy_type": "socks5",
    "proxy_host": "127.0.0.1",
    "proxy_port": 7897,
    "download_dir": r"E:\Download\Telegram",
    "page_size": 200,
    "concurrency": 8,
    "classify_enabled": True,
}


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    data = load_json(CONFIG_PATH, {})
    if isinstance(data, dict):
        cfg.update(data)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def load_history():
    data = load_json(HISTORY_PATH, {})
    hist = {}
    for dialog_id, ids in (data.get("dialogs") or {}).items():
        hist[int(dialog_id)] = set(int(x) for x in ids)
    return hist


def save_history(hist):
    data = {"dialogs": {str(k): sorted(v) for k, v in hist.items()}}
    with open(HISTORY_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def load_subs():
    data = load_json(SUBS_PATH, [])
    return data if isinstance(data, list) else []


def save_subs(subs):
    with open(SUBS_PATH, "w", encoding="utf-8") as fh:
        json.dump(subs, fh, ensure_ascii=False, indent=2)


def qr_data_url(url):
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    pil = image.get_image() if hasattr(image, "get_image") else image
    buffer = io.BytesIO()
    pil.convert("RGB").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class AppState:
    def __init__(self):
        self.config = load_config()
        self.events = queue.Queue()
        self.service = TelegramService(self.events, self.config)
        self.subs = load_subs()
        self.history = load_history()
        self.subscribers = set()
        self.lock = threading.Lock()
        self.authorized = False
        self.account = ""
        self.dialogs = []
        self.downloading = False
        self.active_dialog_id = None
        self.qr = None
        self.preview_paths = {}
        self.preview_target = None
        self.checking = set()

    def start(self):
        self.service.start()
        threading.Thread(target=self._dispatch, daemon=True).start()
        threading.Thread(target=self._scheduler, daemon=True).start()
        if self.config.get("api_id") and self.config.get("api_hash"):
            self.service.configure(self.config)

    # ---------- SSE ----------
    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subscribers.discard(q)

    def broadcast(self, event_type, data):
        payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            try:
                q.put_nowait(payload)
            except Exception:
                pass

    # ---------- service 事件转发 ----------
    def _dispatch(self):
        while True:
            event, args = self.events.get()
            try:
                self._handle_event(event, args)
            except Exception as exc:  # noqa: BLE001
                self.broadcast("log", {"text": f"服务事件处理失败：{exc}"})

    def _handle_event(self, event, args):
        if event == "login_qr":
            url = args[0]
            image = qr_data_url(url)
            self.qr = {"url": url, "image": image}
            self.broadcast("login_qr", self.qr)
        elif event == "login_ok":
            self.authorized = True
            self.account = args[0]
            self.qr = None
            self.broadcast("login_ok", {"account": self.account})
        elif event == "login_status":
            self.broadcast("login_status", {"text": args[0]})
        elif event == "need_password":
            self.broadcast("need_password", {})
        elif event == "dialogs":
            self.dialogs = args[0]
            self.broadcast("dialogs", {"items": self.dialogs})
        elif event == "comment_dialog":
            item = args[0]
            existing = next((d for d in self.dialogs if d.get("id") == item.get("id")), None)
            if existing:
                existing.update(item)
            else:
                parent_id = item.get("parent_id")
                index = next(
                    (i for i, d in enumerate(self.dialogs) if d.get("id") == parent_id),
                    -1,
                )
                if index >= 0:
                    self.dialogs.insert(index + 1, item)
                else:
                    self.dialogs.append(item)
            self.broadcast("comment_dialog", item)
        elif event == "comment_fail":
            self.broadcast("comment_fail", {"dialog_id": args[0], "error": args[1]})
        elif event == "messages":
            dialog_id, items, next_offset, has_more = args
            self.broadcast(
                "messages",
                {"dialog_id": dialog_id, "items": items, "next_offset": next_offset, "has_more": has_more},
            )
        elif event == "thumb":
            self.broadcast("thumb", {"mid": args[0]})
        elif event == "thumb_fail":
            self.broadcast("thumb_fail", {"mid": args[0]})
        elif event == "preview_progress":
            self.broadcast("preview_progress", {"mid": args[0], "received": args[1], "total": args[2]})
        elif event == "preview_ready":
            mid, path, kind = args
            dialog_id = None
            if self.preview_target and self.preview_target[1] == mid:
                dialog_id = self.preview_target[0]
            if dialog_id is None:
                dialog_id = self.active_dialog_id or 0
            if path.startswith(PREVIEW_DIR):
                self.preview_paths[(dialog_id, mid)] = path
            self.broadcast("preview_ready", {"mid": mid, "kind": kind, "dialog_id": dialog_id})
        elif event == "preview_fail":
            self.broadcast("preview_fail", {"mid": args[0], "error": args[1]})
        elif event == "dl_progress":
            self.broadcast("dl_progress", {"mid": args[0], "received": args[1], "total": args[2]})
        elif event == "dl_file_done":
            mid, text = args
            if text in ("完成", "已存在，跳过") and self.active_dialog_id is not None:
                self.history.setdefault(self.active_dialog_id, set()).add(mid)
                self.broadcast("history_update", {"dialog_id": self.active_dialog_id, "mid": mid})
            self.broadcast("dl_file_done", {"mid": mid, "text": text})
        elif event == "dl_done":
            stats = args[0]
            self.downloading = False
            self.broadcast("dl_done", stats)
            self.active_dialog_id = None
            save_history(self.history)
        elif event == "auto_new":
            self._handle_auto_new(*args)
        elif event == "log":
            self.broadcast("log", {"text": args[0]})
        elif event == "status":
            self.broadcast("status", {"text": args[0]})

    # ---------- 追更 ----------
    def _handle_auto_new(self, dialog_id, title, items, new_last, count):
        self.checking.discard(dialog_id)
        sub = next((s for s in self.subs if s.get("dialog_id") == dialog_id), None)
        payload = {"dialog_id": dialog_id, "title": title, "items": items, "new_last": new_last, "count": count}
        if sub is None:
            self.broadcast("auto_new", payload)
            return
        if items and self.downloading:
            self.broadcast("auto_new", {**payload, "deferred": True})
            return
        sub["last_id"] = new_last
        sub["last_check"] = time.time()
        save_subs(self.subs)
        self.broadcast("subs", {"subs": self.subs})
        if items:
            ids = [item["id"] for item in items]
            self.start_download(dialog_id, title, ids)
        self.broadcast("auto_new", payload)

    def _scheduler(self):
        while True:
            try:
                if self.authorized and not self.downloading:
                    now = time.time()
                    for sub in list(self.subs):
                        if not sub.get("enabled"):
                            continue
                        interval = max(1, int(sub.get("interval_min") or 10)) * 60
                        if now - float(sub.get("last_check") or 0) < interval:
                            continue
                        dialog_id = sub.get("dialog_id")
                        if dialog_id in self.checking:
                            continue
                        self.checking.add(dialog_id)
                        self.service.check_new(dialog_id, sub.get("last_id"))
            except Exception:
                pass
            time.sleep(30)

    def dialog_folder_title(self, dialog_id, fallback):
        """评论区的下载目录并入所属频道，避免单独建「频道 · 评论」文件夹。"""
        item = next((d for d in self.dialogs if d.get("id") == dialog_id), None)
        if not item:
            return fallback
        parent_title = item.get("parent_title")
        if parent_title:
            return parent_title
        title = item.get("title") or fallback
        if item.get("kind") == "评论" and title.endswith(" · 评论"):
            return title[: -len(" · 评论")]
        return title

    def start_download(self, dialog_id, title, ids):
        if self.downloading or not ids:
            return False
        concurrency = max(1, min(64, int(self.config.get("concurrency") or 8)))
        out_dir = self.config.get("download_dir") or DEFAULT_CONFIG["download_dir"]
        self.downloading = True
        self.active_dialog_id = dialog_id
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass
        self.broadcast("download_started", {"dialog_id": dialog_id, "title": title, "count": len(ids)})
        folder_title = self.dialog_folder_title(dialog_id, title or "Telegram")
        ok = self.service.download(ids, out_dir, folder_title, concurrency, dialog_id)
        if not ok:
            self.downloading = False
        return ok

    def safe_config(self):
        return {
            "api_id_set": bool(self.config.get("api_id")),
            "api_hash_set": bool(self.config.get("api_hash")),
            "api_id": self.config.get("api_id") or 0,
            "proxy_enabled": bool(self.config.get("proxy_enabled")),
            "proxy_type": self.config.get("proxy_type") or "socks5",
            "proxy_host": self.config.get("proxy_host") or "127.0.0.1",
            "proxy_port": int(self.config.get("proxy_port") or 7897),
            "download_dir": self.config.get("download_dir") or DEFAULT_CONFIG["download_dir"],
            "concurrency": int(self.config.get("concurrency") or 8),
            "page_size": int(self.config.get("page_size") or 200),
            "classify_enabled": bool(self.config.get("classify_enabled", True)),
        }


STATE = AppState()
app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/api/state")
def api_state():
    return jsonify(
        {
            "configured": bool(STATE.config.get("api_id") and STATE.config.get("api_hash")),
            "authorized": STATE.authorized,
            "account": STATE.account,
            "config": STATE.safe_config(),
            "dialogs": STATE.dialogs,
            "subs": STATE.subs,
            "downloading": STATE.downloading,
            "qr": STATE.qr,
        }
    )


@app.get("/api/events")
def api_events():
    q = STATE.subscribe()

    def generate():
        try:
            yield ": connected\n\n".encode("utf-8")
            while True:
                try:
                    payload = q.get(timeout=15)
                    yield f"data: {payload}\n\n".encode("utf-8")
                except queue.Empty:
                    yield ": ping\n\n".encode("utf-8")
        finally:
            STATE.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/connect")
def api_connect():
    STATE.authorized = False
    STATE.account = ""
    STATE.dialogs = []
    STATE.service.configure(STATE.config)
    return jsonify({"ok": True})


@app.post("/api/password")
def api_password():
    data = request.get_json(force=True) or {}
    STATE.service.submit_password(data.get("password") or "")
    return jsonify({"ok": True})


@app.post("/api/messages")
def api_messages():
    data = request.get_json(force=True) or {}
    try:
        dialog_id = int(data["dialog_id"])
        offset_id = int(data.get("offset_id") or 0)
        limit = max(10, min(2000, int(data.get("limit") or STATE.config.get("page_size") or 200)))
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "参数无效"}), 400
    STATE.service.load_messages(dialog_id, offset_id, limit)
    return jsonify({"ok": True})


@app.post("/api/open_comments")
def api_open_comments():
    data = request.get_json(force=True) or {}
    try:
        dialog_id = int(data["dialog_id"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "参数无效"}), 400
    STATE.service.open_comments(dialog_id)
    return jsonify({"ok": True})


@app.post("/api/thumb")
def api_thumb():
    data = request.get_json(force=True) or {}
    try:
        dialog_id = int(data["dialog_id"])
        mid = int(data["mid"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False}), 400
    STATE.service.fetch_thumb(dialog_id, mid)
    return jsonify({"ok": True})


@app.get("/api/thumb_image")
def api_thumb_image():
    try:
        dialog_id = int(request.args.get("dialog_id", "0"))
        mid = int(request.args.get("mid", "0"))
    except ValueError:
        return "", 400
    path = thumb_file_path(dialog_id, mid)
    if not os.path.exists(path):
        return "", 404, {"Cache-Control": "no-store"}
    return send_file(path, mimetype="image/jpeg", conditional=True, max_age=86400)


@app.post("/api/preview")
def api_preview():
    data = request.get_json(force=True) or {}
    try:
        dialog_id = int(data["dialog_id"])
        mid = int(data["mid"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False}), 400
    STATE.preview_target = (dialog_id, mid)
    STATE.service.preview_media(dialog_id, mid)
    return jsonify({"ok": True})


@app.get("/api/preview_media")
def api_preview_media():
    try:
        dialog_id = int(request.args.get("dialog_id", "0"))
        mid = int(request.args.get("mid", "0"))
    except ValueError:
        return "", 400
    path = STATE.preview_paths.get((dialog_id, mid))
    if not path or not os.path.exists(path):
        return "", 404
    if not os.path.abspath(path).startswith(os.path.abspath(PREVIEW_DIR)):
        return "", 403
    return send_file(path, conditional=True, max_age=86400)


@app.post("/api/download")
def api_download():
    data = request.get_json(force=True) or {}
    try:
        dialog_id = int(data["dialog_id"])
        ids = [int(x) for x in data.get("ids") or []]
        title = data.get("title") or "Telegram"
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "参数无效"}), 400
    if not ids:
        return jsonify({"ok": False, "error": "没有选择文件"}), 400
    if STATE.downloading:
        return jsonify({"ok": False, "error": "已有下载任务正在进行"}), 409
    out_dir = (data.get("download_dir") or "").strip() or STATE.config.get("download_dir")
    try:
        concurrency = max(1, min(64, int(data.get("concurrency") or STATE.config.get("concurrency") or 8)))
    except (TypeError, ValueError):
        concurrency = 8
    STATE.config["download_dir"] = out_dir
    STATE.config["concurrency"] = concurrency
    save_config(STATE.config)
    ok = STATE.start_download(dialog_id, title, ids)
    if not ok:
        return jsonify({"ok": False, "error": "已有下载任务在进行，请先停止或等待完成"}), 409
    return jsonify({"ok": True})


@app.post("/api/stop")
def api_stop():
    STATE.service.stop_download()
    return jsonify({"ok": True})


@app.post("/api/reorganize")
def api_reorganize():
    data = request.get_json(force=True) or {}
    if STATE.downloading:
        return jsonify({"ok": False, "error": "请先停止当前下载，再整理文件"}), 409
    try:
        dialog_id = int(data["dialog_id"])
        ids = [int(x) for x in data.get("ids") or []]
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "参数无效"}), 400
    if not ids:
        return jsonify({"ok": False, "error": "当前频道还没有加载消息"}), 400

    dry_run = bool(data.get("dry_run"))
    dialog = next((d for d in STATE.dialogs if d.get("id") == dialog_id), None)
    title = (dialog or {}).get("title") or str(dialog_id)
    out_dir = STATE.config.get("download_dir") or DEFAULT_CONFIG["download_dir"]
    channel_folder = os.path.join(out_dir, _clean(STATE.dialog_folder_title(dialog_id, title), "Telegram"))
    if not os.path.isdir(channel_folder):
        return jsonify({"ok": False, "error": "频道下载目录不存在"}), 404

    msgs = [STATE.service.messages[i] for i in ids if i in STATE.service.messages]
    plan = plan_download_folders(msgs, channel_folder, True)
    targets = {}
    for m in msgs:
        meta = _message_meta(m)
        if not meta or not meta["downloadable"]:
            continue
        targets[_build_filename(m, meta["type"])] = plan.get(m.id, channel_folder)

    moved = 0
    skipped = 0
    cleaned = 0
    for root, dirs, files in os.walk(channel_folder, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            if name.endswith(".part"):
                try:
                    if os.path.getsize(path) == 0:
                        cleaned += 1
                        if not dry_run:
                            os.remove(path)
                except OSError:
                    pass
                continue
            target = targets.get(name)
            if not target:
                continue
            if os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(target)):
                skipped += 1
                continue
            try:
                target_path = os.path.join(target, name)
                if os.path.exists(target_path):
                    skipped += 1
                    continue
                if not dry_run:
                    os.makedirs(target, exist_ok=True)
                    shutil.move(path, target_path)
                moved += 1
            except OSError:
                skipped += 1
        if dry_run:
            continue
        for sub in dirs:
            sub_path = os.path.join(root, sub)
            try:
                if not os.listdir(sub_path):
                    os.rmdir(sub_path)
            except OSError:
                pass
    return jsonify(
        {
            "ok": True,
            "moved": moved,
            "skipped": skipped,
            "cleaned": cleaned,
            "matched": len(targets),
            "messages": len(msgs),
            "dry_run": dry_run,
        }
    )


@app.post("/api/refresh_dialogs")
def api_refresh_dialogs():
    STATE.service.submit(STATE.service._load_dialogs())
    return jsonify({"ok": True})


@app.get("/api/history")
def api_history():
    try:
        dialog_id = int(request.args.get("dialog_id", "0"))
    except ValueError:
        return jsonify({"mids": []})
    return jsonify({"mids": sorted(STATE.history.get(dialog_id, set()))})


@app.get("/api/subs")
def api_subs_get():
    return jsonify({"subs": STATE.subs})


@app.post("/api/subs")
def api_subs_post():
    data = request.get_json(force=True) or {}
    action = data.get("action")
    if action == "add":
        try:
            dialog_id = int(data["dialog_id"])
            title = data.get("title") or str(dialog_id)
            interval = max(1, int(data.get("interval_min") or 10))
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "参数无效"}), 400
        if any(sub.get("dialog_id") == dialog_id for sub in STATE.subs):
            return jsonify({"ok": False, "error": "该频道已在追更列表"}), 409
        sub = {
            "dialog_id": dialog_id,
            "title": title,
            "interval_min": interval,
            "last_id": None,
            "last_check": 0,
            "enabled": True,
        }
        STATE.subs.append(sub)
        save_subs(STATE.subs)
        STATE.broadcast("subs", {"subs": STATE.subs})
        if STATE.authorized and not STATE.downloading:
            STATE.checking.add(dialog_id)
            STATE.service.check_new(dialog_id, None)
        return jsonify({"ok": True, "subs": STATE.subs})
    sub = None
    if data.get("dialog_id") is not None:
        try:
            did = int(data["dialog_id"])
            sub = next((s for s in STATE.subs if s.get("dialog_id") == did), None)
        except ValueError:
            sub = None
    if sub is None:
        return jsonify({"ok": False, "error": "未找到追更项"}), 404
    if action == "toggle":
        sub["enabled"] = not sub.get("enabled", True)
    elif action == "delete":
        STATE.subs.remove(sub)
    elif action == "check":
        if STATE.authorized and not STATE.downloading:
            STATE.checking.add(sub["dialog_id"])
            STATE.service.check_new(sub["dialog_id"], sub.get("last_id"))
    else:
        return jsonify({"ok": False, "error": "未知操作"}), 400
    save_subs(STATE.subs)
    STATE.broadcast("subs", {"subs": STATE.subs})
    return jsonify({"ok": True, "subs": STATE.subs})


@app.post("/api/settings")
def api_settings():
    data = request.get_json(force=True) or {}
    cfg = dict(STATE.config)
    api_id = str(data.get("api_id") or "").strip()
    api_hash = str(data.get("api_hash") or "").strip()
    if api_id:
        if not api_id.isdigit():
            return jsonify({"ok": False, "error": "api_id 必须是数字"}), 400
        cfg["api_id"] = int(api_id)
    if api_hash:
        cfg["api_hash"] = api_hash
    try:
        port = int(data.get("proxy_port") or cfg.get("proxy_port") or 7897)
    except ValueError:
        port = 7897
    cfg.update(
        {
            "proxy_enabled": bool(data.get("proxy_enabled", cfg.get("proxy_enabled"))),
            "proxy_type": data.get("proxy_type") or cfg.get("proxy_type") or "socks5",
            "proxy_host": (data.get("proxy_host") or cfg.get("proxy_host") or "127.0.0.1").strip(),
            "proxy_port": port,
            "download_dir": (data.get("download_dir") or cfg.get("download_dir") or DEFAULT_CONFIG["download_dir"]).strip(),
            "concurrency": max(1, min(64, int(data.get("concurrency") or cfg.get("concurrency") or 8))),
            "page_size": max(50, min(2000, int(data.get("page_size") or cfg.get("page_size") or 200))),
            "classify_enabled": bool(data.get("classify_enabled", cfg.get("classify_enabled", True))),
        }
    )
    if not cfg.get("api_id") or not cfg.get("api_hash"):
        return jsonify({"ok": False, "error": "请填写 api_id 和 api_hash"}), 400
    STATE.config = cfg
    save_config(cfg)
    STATE.authorized = False
    STATE.account = ""
    STATE.dialogs = []
    STATE.service.configure(cfg)
    return jsonify({"ok": True})


@app.post("/api/open_folder")
def api_open_folder():
    path = STATE.config.get("download_dir") or DEFAULT_CONFIG["download_dir"]
    try:
        os.makedirs(path, exist_ok=True)
        os.startfile(path)
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


def find_port(start=8765, end=8785):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def detect_existing():
    for port in range(8765, 8786):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=0.8) as resp:
                if resp.status == 200:
                    return port
        except Exception:
            continue
    return None


def open_app_window(url):
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for edge in edge_paths:
        if os.path.exists(edge):
            try:
                subprocess.Popen([edge, f"--app={url}", "--window-size=1440,900"])
                return
            except Exception:
                pass
    webbrowser.open(url)


if __name__ == "__main__":
    existing = detect_existing()
    if existing:
        open_app_window(f"http://127.0.0.1:{existing}")
        raise SystemExit
    STATE.start()
    port = find_port()
    url = f"http://127.0.0.1:{port}"
    threading.Timer(1.2, open_app_window, args=(url,)).start()
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)
