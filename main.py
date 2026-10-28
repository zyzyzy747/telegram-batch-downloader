import json
import os
import queue
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog

import qrcode
import ttkbootstrap as ttkb
from PIL import Image, ImageTk

from tg_core import APP_DIR, TelegramService, thumb_file_path

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

FILTERS = [
    ("video", "视频", True),
    ("photo", "图片", True),
    ("document", "文件", True),
    ("audio", "音频", True),
    ("text", "文本", False),
]
TYPE_LABELS = {
    "video": "视频",
    "photo": "图片",
    "audio": "音频",
    "voice": "语音",
    "document": "文件",
    "text": "文本",
}
THEME_DARK = "catppuccin-dark"
THEME_LIGHT = "catppuccin-light"


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        messagebox.showerror("保存失败", f"无法写入配置：{exc}")


def load_history():
    hist = {}
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for dialog_id, ids in (data.get("dialogs") or {}).items():
                hist[int(dialog_id)] = set(int(x) for x in ids)
        except Exception:
            pass
    return hist


def save_history(hist):
    try:
        data = {"dialogs": {str(k): sorted(v) for k, v in hist.items()}}
        with open(HISTORY_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except Exception:
        pass


def load_subs():
    if os.path.exists(SUBS_PATH):
        try:
            with open(SUBS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_subs(subs):
    try:
        with open(SUBS_PATH, "w", encoding="utf-8") as fh:
            json.dump(subs, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def human_size(size):
    size = float(size or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[idx]}"


def make_qr_photo(url, pixels=280):
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    pil = image.get_image() if hasattr(image, "get_image") else image
    pil = pil.convert("RGB").resize((pixels, pixels), Image.NEAREST)
    return ImageTk.PhotoImage(pil)


class LoginDialog(ttkb.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("扫码登录")
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)

        body = ttkb.Frame(self, padding=22)
        body.pack(fill="both", expand=True)

        ttkb.Label(body, text="用手机 Telegram 扫描二维码", font=("Microsoft YaHei UI", 13, "bold")).pack()
        holder = ttkb.Frame(body, width=280, height=280)
        holder.pack(pady=14)
        holder.pack_propagate(False)
        self.qr_label = tk.Label(holder, bg="white", bd=0)
        self.qr_label.pack(fill="both", expand=True)
        self.status = ttkb.Label(body, text="正在生成二维码…")
        self.status.pack()
        ttkb.Label(
            body,
            text="手机 Telegram → 设置 → 设备 → 关联桌面设备 → 扫描二维码",
            foreground="#888888",
            wraplength=320,
            justify="center",
        ).pack(pady=(6, 14))

        btns = ttkb.Frame(body)
        btns.pack()
        ttkb.Button(btns, text="重新生成", bootstyle="secondary-outline", command=self.app.regenerate_qr).pack(side="left", padx=5)
        ttkb.Button(btns, text="关闭", bootstyle="primary", command=self._close).pack(side="left", padx=5)

    def set_qr(self, url):
        try:
            photo = make_qr_photo(url)
            self.qr_label.configure(image=photo)
            self.qr_label.image = photo
            self.status.configure(text="二维码已生成，等待扫码…")
        except Exception as exc:  # noqa: BLE001
            self.status.configure(text=f"二维码生成失败：{exc}")

    def set_status(self, text, color="#f59e0b"):
        self.status.configure(text=text, foreground=color)

    def _close(self):
        self.app.login_dialog = None
        self.destroy()


class SettingsDialog(ttkb.Toplevel):
    def __init__(self, app, first_run=False):
        super().__init__(app)
        self.app = app
        self.first_run = first_run
        self.title("设置")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()

        cfg = app.config_data
        body = ttkb.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        row = 0
        ttkb.Label(body, text="Telegram API", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        row += 1
        ttkb.Label(
            body,
            text="到 my.telegram.org → API development tools 创建应用，获取下面两项",
            foreground="#888888",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 10))
        row += 1

        self.api_id_var = tk.StringVar(value=str(cfg.get("api_id") or ""))
        self.api_hash_var = tk.StringVar(value=cfg.get("api_hash") or "")
        ttkb.Label(body, text="api_id").grid(row=row, column=0, sticky="w", pady=4)
        ttkb.Entry(body, textvariable=self.api_id_var, width=38).grid(row=row, column=1, columnspan=2, sticky="we", pady=4)
        row += 1
        ttkb.Label(body, text="api_hash").grid(row=row, column=0, sticky="w", pady=4)
        ttkb.Entry(body, textvariable=self.api_hash_var, width=38).grid(row=row, column=1, columnspan=2, sticky="we", pady=4)
        row += 1

        ttkb.Separator(body).grid(row=row, column=0, columnspan=3, sticky="we", pady=10)
        row += 1

        self.proxy_enabled = tk.BooleanVar(value=bool(cfg.get("proxy_enabled")))
        ttkb.Checkbutton(
            body, text="使用代理（Clash / TUN 之外的备用方式）", variable=self.proxy_enabled, bootstyle="round-toggle"
        ).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1

        proxy_row = ttkb.Frame(body)
        proxy_row.grid(row=row, column=0, columnspan=3, sticky="we", pady=6)
        self.proxy_type_var = tk.StringVar(value=cfg.get("proxy_type") or "socks5")
        self.proxy_host_var = tk.StringVar(value=cfg.get("proxy_host") or "127.0.0.1")
        self.proxy_port_var = tk.StringVar(value=str(cfg.get("proxy_port") or 7897))
        ttkb.Combobox(proxy_row, textvariable=self.proxy_type_var, values=["socks5", "http"], width=8, state="readonly").pack(side="left")
        ttkb.Label(proxy_row, text="  地址").pack(side="left")
        ttkb.Entry(proxy_row, textvariable=self.proxy_host_var, width=14).pack(side="left", padx=4)
        ttkb.Label(proxy_row, text="端口").pack(side="left")
        ttkb.Entry(proxy_row, textvariable=self.proxy_port_var, width=8).pack(side="left", padx=4)
        row += 1

        ttkb.Separator(body).grid(row=row, column=0, columnspan=3, sticky="we", pady=10)
        row += 1

        ttkb.Label(body, text="下载目录").grid(row=row, column=0, sticky="w", pady=4)
        self.dir_var = tk.StringVar(value=cfg.get("download_dir") or DEFAULT_CONFIG["download_dir"])
        ttkb.Entry(body, textvariable=self.dir_var, width=38).grid(row=row, column=1, sticky="we", pady=4)
        ttkb.Button(body, text="浏览…", bootstyle="secondary-outline", command=self._browse).grid(row=row, column=2, padx=(6, 0))
        row += 1

        self.classify_enabled = tk.BooleanVar(value=bool(cfg.get("classify_enabled", True)))
        ttkb.Checkbutton(
            body, text="自动分类（按文件名公共前缀建子文件夹）", variable=self.classify_enabled, bootstyle="round-toggle"
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        row += 1
        ttkb.Label(
            body,
            text="分卷、分 part 的文件会归入公共前缀命名的子文件夹；没有有效前缀的文件保留在频道目录。",
            foreground="#888888",
            wraplength=440,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 0))
        row += 1

        ttkb.Label(
            body,
            text="提示：开着 TUN 时可以不启用代理；只用系统代理时建议启用 socks5 127.0.0.1:7897。",
            foreground="#888888",
            wraplength=440,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        row += 1

        btns = ttkb.Frame(body)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttkb.Button(btns, text="保存", bootstyle="primary", command=self._save).pack(side="left", padx=5)
        ttkb.Button(btns, text="取消", bootstyle="secondary-outline", command=self._cancel).pack(side="left")

    def _browse(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get() or "E:\\")
        if path:
            self.dir_var.set(path)

    def _save(self):
        api_id_raw = self.api_id_var.get().strip()
        api_hash = self.api_hash_var.get().strip()
        if not api_id_raw.isdigit() or int(api_id_raw) <= 0:
            messagebox.showerror("api_id 无效", "api_id 应该是纯数字，请到 my.telegram.org 获取。", parent=self)
            return
        if len(api_hash) < 20:
            messagebox.showerror("api_hash 无效", "api_hash 看起来不完整，请检查。", parent=self)
            return
        try:
            port = int(self.proxy_port_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("端口无效", "代理端口必须是 1-65535 的数字。", parent=self)
            return

        cfg = dict(self.app.config_data)
        cfg.update(
            {
                "api_id": int(api_id_raw),
                "api_hash": api_hash,
                "proxy_enabled": bool(self.proxy_enabled.get()),
                "proxy_type": self.proxy_type_var.get(),
                "proxy_host": self.proxy_host_var.get().strip() or "127.0.0.1",
                "proxy_port": port,
                "download_dir": self.dir_var.get().strip() or DEFAULT_CONFIG["download_dir"],
                "classify_enabled": bool(self.classify_enabled.get()),
            }
        )
        self.app.config_data = cfg
        save_config(cfg)
        self.grab_release()
        self.destroy()
        self.app.apply_config()

    def _cancel(self):
        self.grab_release()
        self.destroy()
        if self.first_run:
            self.app.status_label.configure(text="请在「设置」里填写 api_id / api_hash 后连接")


class FilterDialog(ttkb.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("筛选消息")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        f = app.msg_filter
        body = ttkb.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        row = 0
        self.keyword_var = tk.StringVar(value=f.get("keyword", ""))
        ttkb.Label(body, text="关键词").grid(row=row, column=0, sticky="w", pady=4)
        ttkb.Entry(body, textvariable=self.keyword_var, width=32).grid(row=row, column=1, columnspan=3, sticky="we", pady=4)
        row += 1

        self.date_from = tk.StringVar(value=f.get("date_from", ""))
        self.date_to = tk.StringVar(value=f.get("date_to", ""))
        ttkb.Label(body, text="日期从").grid(row=row, column=0, sticky="w", pady=4)
        ttkb.Entry(body, textvariable=self.date_from, width=14).grid(row=row, column=1, sticky="w", pady=4)
        ttkb.Label(body, text="到").grid(row=row, column=2, sticky="e", padx=(10, 4))
        ttkb.Entry(body, textvariable=self.date_to, width=14).grid(row=row, column=3, sticky="w", pady=4)
        row += 1

        self.size_min = tk.StringVar(value=self._mb_text(f.get("size_min")))
        self.size_max = tk.StringVar(value=self._mb_text(f.get("size_max")))
        ttkb.Label(body, text="大小 MB ≥").grid(row=row, column=0, sticky="w", pady=4)
        ttkb.Entry(body, textvariable=self.size_min, width=14).grid(row=row, column=1, sticky="w", pady=4)
        ttkb.Label(body, text="≤").grid(row=row, column=2, sticky="e", padx=(10, 4))
        ttkb.Entry(body, textvariable=self.size_max, width=14).grid(row=row, column=3, sticky="w", pady=4)
        row += 1

        self.undownloaded = tk.BooleanVar(value=bool(f.get("undownloaded")))
        ttkb.Checkbutton(
            body, text="只看未下载", variable=self.undownloaded, bootstyle="round-toggle"
        ).grid(row=row, column=0, columnspan=4, sticky="w", pady=(10, 0))
        row += 1

        ttkb.Label(body, text="日期格式 YYYY-MM-DD，大小单位 MB，留空表示不限。", foreground="#888888").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )
        row += 1

        btns = ttkb.Frame(body)
        btns.grid(row=row, column=0, columnspan=4, sticky="e", pady=(14, 0))
        ttkb.Button(btns, text="应用", bootstyle="primary", command=self._apply).pack(side="left", padx=5)
        ttkb.Button(btns, text="重置", bootstyle="secondary-outline", command=self._reset).pack(side="left", padx=5)
        ttkb.Button(btns, text="取消", bootstyle="secondary-outline", command=self._close).pack(side="left")

    @staticmethod
    def _mb_text(value):
        if value is None:
            return ""
        return f"{value / (1024 * 1024):g}"

    def _collect(self):
        keyword = self.keyword_var.get().strip()
        date_from = self.date_from.get().strip()
        date_to = self.date_to.get().strip()
        for label, value in (("开始日期", date_from), ("结束日期", date_to)):
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    messagebox.showerror("日期格式错误", f"{label} 请使用 YYYY-MM-DD 格式。", parent=self)
                    return None
        if date_from and date_to and date_from > date_to:
            messagebox.showerror("日期范围错误", "开始日期不能晚于结束日期。", parent=self)
            return None

        sizes = {}
        for key, var in (("size_min", self.size_min), ("size_max", self.size_max)):
            text = var.get().strip()
            if not text:
                sizes[key] = None
                continue
            try:
                value = float(text)
                if value < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("大小格式错误", "大小请填数字（MB），留空表示不限。", parent=self)
                return None
            sizes[key] = int(value * 1024 * 1024)
        if sizes["size_min"] is not None and sizes["size_max"] is not None and sizes["size_min"] > sizes["size_max"]:
            messagebox.showerror("大小范围错误", "最小大小不能大于最大大小。", parent=self)
            return None
        return {
            "keyword": keyword,
            "date_from": date_from,
            "date_to": date_to,
            "size_min": sizes["size_min"],
            "size_max": sizes["size_max"],
            "undownloaded": bool(self.undownloaded.get()),
        }

    def _apply(self):
        data = self._collect()
        if data is None:
            return
        self.app.msg_filter = data
        self.app.update_filter_indicator()
        self.app._render_current_view()
        self._close()

    def _reset(self):
        self.keyword_var.set("")
        self.date_from.set("")
        self.date_to.set("")
        self.size_min.set("")
        self.size_max.set("")
        self.undownloaded.set(False)
        self._apply()

    def _close(self):
        self.grab_release()
        self.destroy()


class SubDialog(ttkb.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("定时追更")
        self.geometry("680x480")
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)

        body = ttkb.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.tree = ttkb.Treeview(
            body, columns=("title", "interval", "last", "status"), show="headings", selectmode="browse"
        )
        self.tree.heading("title", text="频道")
        self.tree.heading("interval", text="间隔")
        self.tree.heading("last", text="上次检查")
        self.tree.heading("status", text="状态")
        self.tree.column("title", width=300, anchor="w")
        self.tree.column("interval", width=90, anchor="center", stretch=False)
        self.tree.column("last", width=130, anchor="center", stretch=False)
        self.tree.column("status", width=70, anchor="center", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttkb.Scrollbar(body, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        add = ttkb.Frame(body)
        add.grid(row=1, column=0, columnspan=2, sticky="we", pady=(10, 0))
        ttkb.Label(add, text="频道").pack(side="left")
        self.channel_var = tk.StringVar()
        self.combo = ttkb.Combobox(add, textvariable=self.channel_var, width=36, state="readonly")
        self.combo.pack(side="left", padx=6)
        ttkb.Label(add, text="间隔").pack(side="left")
        self.interval_var = tk.StringVar(value="10")
        ttkb.Combobox(add, textvariable=self.interval_var, values=["5", "10", "30", "60"], width=5, state="readonly").pack(side="left", padx=6)
        ttkb.Label(add, text="分钟").pack(side="left")
        ttkb.Button(add, text="添加当前所选频道", bootstyle="primary", command=self._add).pack(side="left", padx=(10, 0))

        btns = ttkb.Frame(body)
        btns.grid(row=2, column=0, columnspan=2, sticky="we", pady=(10, 0))
        ttkb.Button(btns, text="启用 / 停用", bootstyle="secondary-outline", command=self._toggle).pack(side="left")
        ttkb.Button(btns, text="立即检查", bootstyle="info-outline", command=self._check_now).pack(side="left", padx=6)
        ttkb.Button(btns, text="删除", bootstyle="danger-outline", command=self._delete).pack(side="left")
        ttkb.Button(btns, text="关闭", bootstyle="secondary-outline", command=self._close).pack(side="right")

        self._preselect_current()
        self.refresh()

    def _preselect_current(self):
        if self.app.current_dialog_id:
            for item in self.app.channel_items:
                if item["id"] == self.app.current_dialog_id:
                    self.channel_var.set(f"{item['title']} — {item['id']}")
                    return

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for idx, sub in enumerate(self.app.subs):
            last = sub.get("last_check") or 0
            last_text = time.strftime("%m-%d %H:%M", time.localtime(last)) if last else "未检查"
            status = "启用" if sub.get("enabled") else "停用"
            self.tree.insert(
                "", "end", iid=str(idx),
                values=(sub.get("title", ""), f'{sub.get("interval_min", 10)} 分钟', last_text, status),
            )
        values = [f"{it['title']} — {it['id']}" for it in self.app.channel_items]
        self.combo.configure(values=values)
        if not self.channel_var.get() and values:
            self.channel_var.set(values[0])

    def _selected_sub(self):
        selection = self.tree.selection()
        if not selection:
            return None
        idx = int(selection[0])
        if 0 <= idx < len(self.app.subs):
            return self.app.subs[idx]
        return None

    def _add(self):
        text = self.channel_var.get().strip()
        if not text:
            messagebox.showinfo("提示", "请先选择一个频道。", parent=self)
            return
        try:
            dialog_id = int(text.rsplit("—", 1)[1].strip())
        except (IndexError, ValueError):
            return
        if any(sub["dialog_id"] == dialog_id for sub in self.app.subs):
            messagebox.showinfo("提示", "该频道已在追更列表中。", parent=self)
            return
        item = next((it for it in self.app.channel_items if it["id"] == dialog_id), None)
        title = item["title"] if item else str(dialog_id)
        try:
            interval = max(1, int(self.interval_var.get()))
        except ValueError:
            interval = 10
        sub = {
            "dialog_id": dialog_id,
            "title": title,
            "interval_min": interval,
            "last_id": None,
            "last_check": 0,
            "enabled": True,
        }
        self.app.subs.append(sub)
        save_subs(self.app.subs)
        self.refresh()
        self.app.run_sub_check(sub, force=True)

    def _toggle(self):
        sub = self._selected_sub()
        if not sub:
            return
        sub["enabled"] = not sub.get("enabled", True)
        save_subs(self.app.subs)
        self.refresh()

    def _check_now(self):
        sub = self._selected_sub()
        if not sub:
            return
        self.app.run_sub_check(sub, force=True)

    def _delete(self):
        sub = self._selected_sub()
        if not sub:
            return
        self.app.subs.remove(sub)
        save_subs(self.app.subs)
        self.refresh()

    def _close(self):
        self.app.sub_dialog = None
        self.destroy()


class ImageViewer(ttkb.Toplevel):
    def __init__(self, app, path, title="图片预览"):
        super().__init__(app)
        self.title(title)
        self.transient(app)
        try:
            img = Image.open(path).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", f"无法读取图片：{exc}", parent=self)
            self.destroy()
            return
        self.photo = ImageTk.PhotoImage(img)
        frame = ttkb.Frame(self)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(frame, bg="#111111", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar = ttkb.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar = ttkb.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
        hbar.grid(row=1, column=0, sticky="we")
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, img.width, img.height))
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))
        ttkb.Label(self, text=f"{img.width} × {img.height}　拖动可平移，滚轮可滚动", padding=6).pack(fill="x")
        win_w = max(520, min(img.width, 1200))
        win_h = max(420, min(img.height + 60, 860))
        self.geometry(f"{win_w}x{win_h}")


class App(ttkb.Window):
    def __init__(self):
        super().__init__(themename=THEME_DARK)
        self.title("Telegram 批量下载器")
        self.geometry("1280x800")
        self.minsize(1120, 680)

        self.config_data = load_config()
        self.events = queue.Queue()
        self.service = TelegramService(self.events, self.config_data)
        self.authorized = False
        self.login_dialog = None

        self.channel_items = []
        self.current_dialog_id = None
        self.current_channel_title = ""
        self.loaded_messages = []
        self.meta_by_id = {}
        self.selected = set()
        self.next_offset = 0
        self.load_all = False
        self.dl_state = {}
        self.downloading = False
        self.preview_mid = None
        self.preview_loading = False
        self.history = load_history()
        self.msg_filter = {
            "keyword": "",
            "date_from": "",
            "date_to": "",
            "size_min": None,
            "size_max": None,
            "undownloaded": False,
        }
        self.view_mode = "list"
        self.grid_cells = {}
        self.grid_images = {}
        self.thumb_requested = set()
        self.download_dialog_id = None
        self.download_meta = {}
        self.subs = load_subs()
        self.sub_dialog = None
        self.auto_checking = set()

        self._setup_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)
        self.service.start()
        self.after(300, self._bootstrap)
        self.after(5000, self._auto_tick)

    def _setup_style(self):
        self.style.configure("Treeview", rowheight=30)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
            except tk.TclError:
                pass

    def _build_ui(self):
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        header = ttkb.Frame(self, padding=(16, 12))
        header.grid(row=0, column=0, sticky="we")
        header.columnconfigure(1, weight=1)
        ttkb.Label(header, text="Telegram 批量下载器", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        self.account_label = ttkb.Label(header, text="未登录", foreground="#9ca3af")
        self.account_label.grid(row=0, column=1, sticky="e", padx=12)
        self.theme_btn = ttkb.Button(header, text="浅色", bootstyle="secondary-outline", command=self.toggle_theme)
        self.theme_btn.grid(row=0, column=2, padx=(0, 8))
        ttkb.Button(header, text="设置", bootstyle="secondary-outline", command=self.open_settings).grid(row=0, column=3)

        paned = ttkb.Panedwindow(self, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))

        # ---- 左侧频道 ----
        left = ttkb.Frame(paned, padding=(0, 0, 8, 0), width=280)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        ttkb.Label(left, text="频道 / 群组", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_channels())
        ttkb.Entry(left, textvariable=self.search_var).grid(row=1, column=0, sticky="we", pady=6)
        self.channel_tree = ttkb.Treeview(left, columns=("title", "kind"), show="headings", selectmode="browse")
        self.channel_tree.heading("title", text="名称")
        self.channel_tree.heading("kind", text="类型")
        self.channel_tree.column("title", width=210, anchor="w")
        self.channel_tree.column("kind", width=54, anchor="center", stretch=False)
        self.channel_tree.grid(row=2, column=0, sticky="nsew")
        ch_scroll = ttkb.Scrollbar(left, orient="vertical", command=self.channel_tree.yview)
        ch_scroll.grid(row=2, column=1, sticky="ns")
        self.channel_tree.configure(yscrollcommand=ch_scroll.set)
        self.channel_tree.bind("<<TreeviewSelect>>", self._on_channel_select)
        ttkb.Button(left, text="刷新频道列表", bootstyle="info-outline", command=self.refresh_dialogs).grid(row=3, column=0, sticky="we", pady=(6, 0))
        paned.add(left, weight=0)

        # ---- 中间消息 ----
        center = ttkb.Frame(paned)
        center.columnconfigure(0, weight=1)
        center.rowconfigure(2, weight=1)

        filters = ttkb.Frame(center)
        filters.grid(row=0, column=0, sticky="we")
        ttkb.Label(filters, text="显示").pack(side="left")
        self.filter_vars = {}
        for key, label, default in FILTERS:
            var = tk.BooleanVar(value=default)
            self.filter_vars[key] = var
            ttkb.Checkbutton(filters, text=label, variable=var, bootstyle="primary", command=self._render_current_view).pack(side="left", padx=4)
        ttkb.Label(filters, text="每页").pack(side="right", padx=(8, 4))
        self.page_size_var = tk.StringVar(value=str(self.config_data.get("page_size") or 200))
        ttkb.Combobox(
            filters, textvariable=self.page_size_var, values=["100", "200", "500", "1000"], width=6, state="readonly"
        ).pack(side="right")

        toolbar = ttkb.Frame(center)
        toolbar.grid(row=1, column=0, sticky="we", pady=6)
        ttkb.Button(toolbar, text="全选", bootstyle="secondary-outline", command=self.select_all_visible).pack(side="left")
        ttkb.Button(toolbar, text="取消", bootstyle="secondary-outline", command=self.clear_selection).pack(side="left", padx=4)
        self.download_btn = ttkb.Button(toolbar, text="下载选中", bootstyle="primary", command=self.start_download)
        self.download_btn.pack(side="left", padx=(12, 4))
        self.stop_btn = ttkb.Button(toolbar, text="停止", bootstyle="danger-outline", command=self.stop_download, state="disabled")
        self.stop_btn.pack(side="left")
        self.filter_btn = ttkb.Button(toolbar, text="筛选…", bootstyle="info-outline", command=self.open_filter)
        self.filter_btn.pack(side="left", padx=(10, 4))
        self.view_btn = ttkb.Button(toolbar, text="网格", bootstyle="secondary-outline", command=self.toggle_view)
        self.view_btn.pack(side="left")
        ttkb.Button(toolbar, text="追更…", bootstyle="secondary-outline", command=self.open_subs).pack(side="left", padx=(4, 0))
        ttkb.Button(toolbar, text="加载全部", bootstyle="secondary-outline", command=self.load_all_messages).pack(side="right", padx=(4, 0))
        ttkb.Button(toolbar, text="加载更多", bootstyle="secondary-outline", command=self.load_more).pack(side="right")
        self.selection_label = ttkb.Label(toolbar, text="已选 0 项", foreground="#9ca3af")
        self.selection_label.pack(side="right", padx=10)

        self.msg_tree = ttkb.Treeview(
            center, columns=("sel", "type", "date", "size", "text", "dl"), show="headings", selectmode="none"
        )
        self.msg_tree.heading("sel", text="选")
        self.msg_tree.heading("type", text="类型")
        self.msg_tree.heading("date", text="日期")
        self.msg_tree.heading("size", text="大小")
        self.msg_tree.heading("text", text="名称 / 内容")
        self.msg_tree.heading("dl", text="下载")
        self.msg_tree.column("sel", width=38, anchor="center", stretch=False)
        self.msg_tree.column("type", width=54, anchor="center", stretch=False)
        self.msg_tree.column("date", width=124, anchor="center", stretch=False)
        self.msg_tree.column("size", width=78, anchor="e", stretch=False)
        self.msg_tree.column("text", width=320, anchor="w")
        self.msg_tree.column("dl", width=54, anchor="center", stretch=False)
        self.msg_tree.tag_configure("downloaded", foreground="#22c55e")
        self.msg_tree.grid(row=2, column=0, sticky="nsew")
        self.msg_scroll = ttkb.Scrollbar(center, orient="vertical", command=self.msg_tree.yview)
        self.msg_scroll.grid(row=2, column=1, sticky="ns")
        self.msg_tree.configure(yscrollcommand=self.msg_scroll.set)
        self.msg_tree.bind("<Button-1>", self._on_msg_click)
        self.msg_tree.bind("<space>", self._on_msg_space)

        self.grid_frame = ttkb.Frame(center)
        self.grid_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self.grid_frame.grid_remove()
        self.grid_canvas = tk.Canvas(self.grid_frame, bg=self.style.colors.bg, highlightthickness=0)
        self.grid_canvas.pack(side="left", fill="both", expand=True)
        self.grid_scroll = ttkb.Scrollbar(self.grid_frame, orient="vertical", command=self.grid_canvas.yview)
        self.grid_scroll.pack(side="right", fill="y")
        self.grid_canvas.configure(yscrollcommand=self.grid_scroll.set)
        self.grid_canvas.bind("<MouseWheel>", lambda e: self.grid_canvas.yview_scroll(int(-e.delta / 120), "units"))

        bottom = ttkb.Frame(center)
        bottom.grid(row=3, column=0, columnspan=2, sticky="we", pady=(8, 0))
        bottom.columnconfigure(1, weight=1)
        ttkb.Label(bottom, text="保存到").grid(row=0, column=0, sticky="w")
        self.dir_var = tk.StringVar(value=self.config_data.get("download_dir") or DEFAULT_CONFIG["download_dir"])
        ttkb.Entry(bottom, textvariable=self.dir_var).grid(row=0, column=1, sticky="we", padx=6)
        ttkb.Button(bottom, text="浏览…", bootstyle="secondary-outline", command=self._browse_dir).grid(row=0, column=2)
        ttkb.Button(bottom, text="打开目录", bootstyle="secondary-outline", command=self._open_dir).grid(row=0, column=3, padx=(4, 0))

        self.progress_var = tk.DoubleVar(value=0)
        conc = ttkb.Frame(bottom)
        conc.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttkb.Label(conc, text="并发").pack(side="left")
        self.concurrency_var = tk.StringVar(value=str(self.config_data.get("concurrency") or 8))
        ttkb.Combobox(
            conc, textvariable=self.concurrency_var, values=["2", "4", "8", "16", "32", "64"], width=4, state="normal"
        ).pack(side="left", padx=(4, 0))
        self.progress = ttkb.Progressbar(bottom, variable=self.progress_var, maximum=100, bootstyle="success-striped")
        self.progress.grid(row=1, column=1, columnspan=2, sticky="we", pady=(8, 0))
        self.progress_label = ttkb.Label(bottom, text="0%", width=8, anchor="e")
        self.progress_label.grid(row=1, column=3, sticky="e", pady=(8, 0))

        # ---- 右侧预览 ----
        preview = ttkb.Frame(paned, padding=(12, 0, 0, 0), width=286)
        preview.columnconfigure(0, weight=1)
        preview.grid_propagate(False)
        ttkb.Label(preview, text="预览", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.thumb_holder = ttkb.Frame(preview, width=260, height=240)
        self.thumb_holder.grid(row=1, column=0, sticky="nwe")
        self.thumb_holder.grid_propagate(False)
        self.preview_image = ttkb.Label(self.thumb_holder, text="点击消息查看缩略图", anchor="center", justify="center")
        self.preview_image.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_name = ttkb.Label(preview, text="", wraplength=250, justify="left", font=("Microsoft YaHei UI", 10, "bold"))
        self.preview_name.grid(row=2, column=0, sticky="w", pady=(10, 2))
        self.preview_meta = ttkb.Label(preview, text="", foreground="#9ca3af", wraplength=250, justify="left")
        self.preview_meta.grid(row=3, column=0, sticky="w")
        self.preview_caption = ttkb.Label(preview, text="", wraplength=250, justify="left")
        self.preview_caption.grid(row=4, column=0, sticky="nw", pady=(6, 0))
        self.preview_action = ttkb.Button(preview, text="预览", bootstyle="info-outline", command=self._preview_action, state="disabled")
        self.preview_action.grid(row=5, column=0, sticky="we", pady=(12, 0))
        ttkb.Label(preview, text="点击缩略图可放大 / 播放", foreground="#9ca3af", wraplength=250).grid(
            row=6, column=0, sticky="w", pady=(6, 0)
        )
        self.preview_image.bind("<Button-1>", lambda _e: self._preview_action())
        self.thumb_holder.bind("<Button-1>", lambda _e: self._preview_action())
        paned.add(center, weight=1)
        paned.add(preview, weight=0)

        # ---- 日志与状态 ----
        log_frame = ttkb.LabelFrame(self, text="日志", padding=8)
        log_frame.grid(row=2, column=0, sticky="we", padx=14, pady=(0, 8))
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=5,
            wrap="none",
            relief="flat",
            bd=0,
            bg=self.style.colors.inputbg,
            fg=self.style.colors.fg,
            insertbackground=self.style.colors.fg,
        )
        self.log_text.grid(row=0, column=0, sticky="we")
        log_scroll = ttkb.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.status_label = ttkb.Label(self, text="就绪", anchor="w", padding=(16, 3))
        self.status_label.grid(row=3, column=0, sticky="we")

    # ---------- 主题 ----------
    def toggle_theme(self):
        current = self.style.theme_use()
        new_theme = THEME_LIGHT if current == THEME_DARK else THEME_DARK
        self.style.theme_use(new_theme)
        self.style.configure("Treeview", rowheight=30)
        self.theme_btn.configure(text="深色" if new_theme == THEME_LIGHT else "浅色")
        try:
            self.log_text.configure(
                bg=self.style.colors.inputbg,
                fg=self.style.colors.fg,
                insertbackground=self.style.colors.fg,
            )
            self.grid_canvas.configure(bg=self.style.colors.bg)
            self._render_current_view()
        except Exception:
            pass

    # ---------- 事件轮询 ----------
    def _poll_events(self):
        try:
            while True:
                event, args = self.events.get_nowait()
                handler = getattr(self, f"_on_{event}", None)
                if handler:
                    handler(*args)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _on_log(self, text):
        self.log(text)

    def _on_status(self, text):
        self.status_label.configure(text=text)

    def _on_login_status(self, text):
        if self.login_dialog and self.login_dialog.winfo_exists():
            self.login_dialog.set_status(text)

    def _on_login_qr(self, url):
        if not self.login_dialog or not self.login_dialog.winfo_exists():
            self.login_dialog = LoginDialog(self)
        self.login_dialog.set_qr(url)

    def _on_need_password(self):
        password = simpledialog.askstring("两步验证", "请输入 Telegram 两步验证密码：", show="*", parent=self)
        self.service.submit_password(password if password else "")

    def _on_login_ok(self, name):
        self.authorized = True
        self.account_label.configure(text=f"已登录：{name}", foreground="#22c55e")
        self.log(f"已登录：{name}")
        self.status_label.configure(text="已连接")
        if self.login_dialog and self.login_dialog.winfo_exists():
            self.login_dialog._close()

    def _on_dialogs(self, items):
        self.channel_items = items
        self._render_channels()

    # ---------- 定时追更 ----------
    def open_subs(self):
        if self.sub_dialog and self.sub_dialog.winfo_exists():
            self.sub_dialog.refresh()
            self.sub_dialog.lift()
            self.sub_dialog.focus_force()
            return
        self.sub_dialog = SubDialog(self)

    def _auto_tick(self):
        try:
            if self.authorized and not self.downloading:
                now = time.time()
                for sub in list(self.subs):
                    if not sub.get("enabled"):
                        continue
                    interval = max(1, int(sub.get("interval_min") or 10)) * 60
                    if now - float(sub.get("last_check") or 0) >= interval:
                        self.run_sub_check(sub)
        finally:
            self.after(60000, self._auto_tick)

    def run_sub_check(self, sub, force=False):
        if not self.authorized:
            self.log("追更：尚未登录")
            return
        if self.downloading:
            return
        dialog_id = sub.get("dialog_id")
        if dialog_id in self.auto_checking:
            return
        if force:
            sub["last_check"] = 0
        self.auto_checking.add(dialog_id)
        self.service.check_new(dialog_id, sub.get("last_id"))

    def _on_auto_new(self, dialog_id, title, items, new_last, count):
        self.auto_checking.discard(dialog_id)
        sub = next((s for s in self.subs if s.get("dialog_id") == dialog_id), None)
        if not sub:
            return
        if items and self.downloading:
            self.log(f"追更：{sub.get('title')} 有 {len(items)} 个新文件，当前正在下载，稍后重试")
            self._refresh_sub_dialog()
            return
        sub["last_id"] = new_last
        sub["last_check"] = time.time()
        save_subs(self.subs)
        if items:
            self._start_auto_download(sub, items)
        elif count:
            self.log(f"追更：{sub.get('title')} 有 {count} 条新消息，但没有可下载文件")
        self._refresh_sub_dialog()

    def _start_auto_download(self, sub, items):
        ids = [item["id"] for item in items]
        out_dir = self.dir_var.get().strip() or self.config_data.get("download_dir") or DEFAULT_CONFIG["download_dir"]
        try:
            concurrency = max(1, min(64, int(self.concurrency_var.get())))
        except (TypeError, ValueError):
            concurrency = 8
        self.download_meta = {item["id"]: item for item in items}
        self.dl_state = {item["id"]: (0, item.get("size") or 0) for item in items}
        self.download_dialog_id = sub["dialog_id"]
        self.progress_var.set(0)
        self.progress_label.configure(text="0%")
        self.downloading = True
        self.download_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_label.configure(text=f"追更下载：{sub.get('title')} · {len(ids)} 个文件")
        self.log(f"追更：开始下载 {sub.get('title')} 的 {len(ids)} 个新文件")
        self.service.download(ids, out_dir, sub.get("title", "Telegram"), concurrency, sub["dialog_id"])

    def _refresh_sub_dialog(self):
        if self.sub_dialog and self.sub_dialog.winfo_exists():
            self.sub_dialog.refresh()

    def _on_messages(self, dialog_id, meta, next_offset, has_more):
        if dialog_id != self.current_dialog_id:
            return
        self.loaded_messages.extend(meta)
        for item in meta:
            self.meta_by_id[item["id"]] = item
        if next_offset:
            self.next_offset = next_offset
        self._render_current_view()
        total = len(self.loaded_messages)
        if self.load_all and has_more and total < 5000:
            self.status_label.configure(text=f"加载全部中… 已加载 {total} 条")
            self.after(250, self._continue_load_all)
        else:
            self.load_all = False
            suffix = "" if has_more else "（没有更多了）"
            self.status_label.configure(text=f"已加载 {total} 条消息{suffix}")

    def _on_thumb(self, mid, path):
        if self.view_mode == "grid" and mid in self.grid_cells:
            self._grid_show_thumb(mid, path)
        if mid != self.preview_mid:
            return
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((250, 240), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.preview_image.configure(image=photo, text="")
            self.preview_image.image = photo
        except Exception:
            self._preview_placeholder("缩略图加载失败")

    def _on_thumb_fail(self, mid):
        if mid == self.preview_mid:
            self._preview_placeholder("该消息没有可用缩略图")

    def _on_preview_progress(self, mid, received, total):
        if mid != self.preview_mid:
            return
        if total:
            pct = min(100, int(received / total * 100))
            self._preview_placeholder(f"正在下载预览 {pct}%")
        else:
            self._preview_placeholder(f"正在下载预览 {human_size(received)}")

    def _on_preview_ready(self, mid, path, kind):
        if mid != self.preview_mid:
            return
        self.preview_loading = False
        self.preview_action.configure(state="normal")
        item = self.meta_by_id.get(mid) or {}
        if kind == "photo":
            ImageViewer(self, path, item.get("name") or "图片预览")
            self._preview_placeholder("已在新窗口打开大图")
        else:
            try:
                os.startfile(path)
                self._preview_placeholder("已下载，正在用系统播放器打开")
            except Exception as exc:  # noqa: BLE001
                self._preview_placeholder(f"打开失败：{exc}")
        self.log(f"预览已就绪：{os.path.basename(path)}")

    def _on_preview_fail(self, mid, error):
        if mid != self.preview_mid:
            return
        self.preview_loading = False
        self.preview_action.configure(state="normal")
        self._preview_placeholder(f"预览失败：{error}")

    def _on_dl_progress(self, mid, received, total):
        if mid in self.dl_state:
            self.dl_state[mid] = (received, total)
        self._update_progress()

    def _on_dl_file_done(self, mid, text):
        meta = self.download_meta.get(mid) or self.meta_by_id.get(mid) or {}
        name = meta.get("name") or f"消息 {mid}"
        self.log(f"{name}：{text}")
        if text in ("完成", "已存在，跳过"):
            dialog_id = self.download_dialog_id if self.download_dialog_id is not None else self.current_dialog_id
            self.history.setdefault(dialog_id, set()).add(mid)
            if dialog_id == self.current_dialog_id:
                self._update_downloaded_marker(mid)

    def _update_downloaded_marker(self, mid):
        if self.view_mode == "list":
            iid = str(mid)
            item = self.meta_by_id.get(mid)
            if item and self.msg_tree.exists(iid):
                self.msg_tree.item(iid, values=self._row_values(item), tags=("downloaded",))
            return
        cell = self.grid_cells.get(mid)
        if not cell:
            return
        x1, y1, x2, _y2 = self.grid_canvas.coords(cell["rect"])
        if "dl" not in cell:
            cell["dl"] = self.grid_canvas.create_text(
                x2 - 8, y1 + 8, anchor="ne", text="已下载", fill="#22c55e", tags=cell["tags"]
            )
        outline = self.style.colors.primary if mid in self.selected else "#22c55e"
        self.grid_canvas.itemconfigure(cell["rect"], outline=outline)

    def _on_dl_done(self, stats):
        self.downloading = False
        self.download_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        summary = f"完成 {stats.get('done', 0)} 个，跳过 {stats.get('skipped', 0)} 个，失败 {stats.get('failed', 0)} 个"
        if stats.get("stopped"):
            summary += f"，停止 {stats['stopped']} 个"
        self.log(summary)
        self.status_label.configure(text="下载结束：" + summary)
        if stats.get("done", 0) + stats.get("skipped", 0):
            self.progress_var.set(100)
        self.download_dialog_id = None
        self.download_meta = {}
        save_history(self.history)

    # ---------- 启动与配置 ----------
    def _bootstrap(self):
        if not self.config_data.get("api_id") or not self.config_data.get("api_hash"):
            self.status_label.configure(text="首次使用：请填写 api_id / api_hash")
            self.open_settings(first_run=True)
        else:
            self.apply_config()

    def apply_config(self):
        self.authorized = False
        self.account_label.configure(text="正在连接…", foreground="#9ca3af")
        self.channel_items = []
        self._render_channels()
        self.service.configure(self.config_data)

    def regenerate_qr(self):
        self.log("重新生成二维码…")
        self.service.configure(self.config_data)

    def open_settings(self, first_run=False):
        SettingsDialog(self, first_run=first_run)

    def refresh_dialogs(self):
        if not self.authorized:
            self.log("尚未登录，无法刷新频道列表")
            return
        self.log("刷新频道列表…")
        self.service.submit(self.service._load_dialogs())

    # ---------- 频道与消息 ----------
    def _render_channels(self):
        keyword = self.search_var.get().strip().lower()
        self.channel_tree.delete(*self.channel_tree.get_children())
        for item in self.channel_items:
            if keyword and keyword not in item["title"].lower():
                continue
            self.channel_tree.insert("", "end", iid=str(item["id"]), values=(item["title"], item["kind"]))

    def _on_channel_select(self, _event=None):
        selection = self.channel_tree.selection()
        if not selection:
            return
        dialog_id = int(selection[0])
        item = next((it for it in self.channel_items if it["id"] == dialog_id), None)
        if not item:
            return
        self.current_dialog_id = dialog_id
        self.current_channel_title = item["title"]
        self.loaded_messages = []
        self.meta_by_id = {}
        self.selected.clear()
        self.next_offset = 0
        self.load_all = False
        self.msg_tree.delete(*self.msg_tree.get_children())
        self.grid_canvas.delete("all")
        self.grid_cells = {}
        self.grid_images = {}
        self._clear_preview()
        self._update_selection_label()
        self.status_label.configure(text=f"正在加载「{item['title']}」…")
        self.service.load_messages(dialog_id, 0, self._page_size())

    def _page_size(self):
        try:
            return max(50, min(2000, int(self.page_size_var.get())))
        except (TypeError, ValueError):
            return 200

    def load_more(self):
        if not self.current_dialog_id or not self.next_offset:
            return
        self.load_all = False
        self.service.load_messages(self.current_dialog_id, self.next_offset, self._page_size())

    def load_all_messages(self):
        if not self.current_dialog_id:
            return
        self.load_all = True
        if self.next_offset:
            self.status_label.configure(text="加载全部中…")
            self.service.load_messages(self.current_dialog_id, self.next_offset, self._page_size())

    def _continue_load_all(self):
        if not self.load_all or not self.current_dialog_id:
            return
        if len(self.loaded_messages) >= 5000 or not self.next_offset:
            self.load_all = False
            return
        self.service.load_messages(self.current_dialog_id, self.next_offset, self._page_size())

    def _is_downloaded(self, mid, dialog_id=None):
        did = self.current_dialog_id if dialog_id is None else dialog_id
        return mid in self.history.get(did, set())

    def _visible(self, item):
        key = "audio" if item["type"] == "voice" else item["type"]
        var = self.filter_vars.get(key)
        if not (var and var.get()):
            return False
        f = self.msg_filter
        keyword = (f.get("keyword") or "").lower()
        if keyword:
            haystack = f'{item.get("name", "")} {item.get("caption", "")}'.lower()
            if keyword not in haystack:
                return False
        day = item["date"][:10]
        if f.get("date_from") and day < f["date_from"]:
            return False
        if f.get("date_to") and day > f["date_to"]:
            return False
        size = item.get("size") or 0
        if f.get("size_min") is not None and size < f["size_min"]:
            return False
        if f.get("size_max") is not None and size > f["size_max"]:
            return False
        if f.get("undownloaded") and self._is_downloaded(item["id"]):
            return False
        return True

    def _row_values(self, item):
        mark = "☑" if item["id"] in self.selected else "☐"
        label = TYPE_LABELS.get(item["type"], item["type"])
        text = item["name"] or item["caption"]
        downloaded = "✓" if self._is_downloaded(item["id"]) else ""
        return (mark, label, item["date"], human_size(item["size"]), text, downloaded)

    def _render_messages(self):
        self.msg_tree.delete(*self.msg_tree.get_children())
        for item in self.loaded_messages:
            if not self._visible(item):
                continue
            tags = ("downloaded",) if self._is_downloaded(item["id"]) else ()
            self.msg_tree.insert("", "end", iid=str(item["id"]), values=self._row_values(item), tags=tags)
        self._update_selection_label()

    def _render_current_view(self):
        if self.view_mode == "grid":
            self._render_grid()
        else:
            self._render_messages()

    def _render_grid(self):
        canvas = self.grid_canvas
        canvas.delete("all")
        self.grid_cells = {}
        self.grid_images = {}
        visible = [item for item in self.loaded_messages if self._visible(item)]
        width = canvas.winfo_width()
        if width <= 1:
            width = 700
        cell_w, cell_h = 184, 176
        cols = max(1, (width - 8) // cell_w)
        pad = 8
        limit = 300
        for idx, item in enumerate(visible[:limit]):
            row, col = divmod(idx, cols)
            x1 = pad + col * cell_w
            y1 = pad + row * cell_h
            x2 = x1 + cell_w - 10
            y2 = y1 + cell_h - 10
            mid = item["id"]
            selected = mid in self.selected
            downloaded = self._is_downloaded(mid)
            if downloaded and not selected:
                outline = "#22c55e"
            elif selected:
                outline = self.style.colors.primary
            else:
                outline = self.style.colors.border
            tags = (f"m{mid}",)
            rect = canvas.create_rectangle(
                x1, y1, x2, y2, outline=outline, width=2, fill=self.style.colors.inputbg, tags=tags
            )
            name = (item["name"] or item["caption"] or "")[:20]
            caption = f'{TYPE_LABELS.get(item["type"], item["type"])} · {human_size(item["size"])}'
            label = f"{name}\n{caption}" if name else caption
            canvas.create_text(
                x1 + 10, y2 - 8, anchor="sw", text=label, fill=self.style.colors.fg, width=cell_w - 22,
                font=("Microsoft YaHei UI", 9), tags=tags,
            )
            selected_mark = canvas.create_text(
                x1 + 10, y1 + 10, anchor="nw", text="☑" if selected else "☐",
                fill=self.style.colors.fg, font=("Microsoft YaHei UI", 12), tags=tags,
            )
            cell = {"rect": rect, "img": None, "selected": selected_mark, "tags": tags}
            if downloaded:
                cell["dl"] = canvas.create_text(
                    x2 - 8, y1 + 8, anchor="ne", text="已下载", fill="#22c55e", tags=tags
                )
            self.grid_cells[mid] = cell
            canvas.tag_bind(f"m{mid}", "<Button-1>", lambda _e, m=mid: self._on_grid_item(m))
            if item["type"] in ("photo", "video", "document"):
                path = thumb_file_path(self.current_dialog_id, mid)
                if os.path.exists(path):
                    self._grid_show_thumb(mid, path)
                else:
                    self._request_thumb(mid)
        rows = (min(len(visible), limit) + cols - 1) // cols
        height = max(rows * cell_h + pad * 2, canvas.winfo_height())
        canvas.configure(scrollregion=(0, 0, width, height))
        if len(visible) > limit:
            canvas.create_text(
                pad, rows * cell_h + pad, anchor="nw",
                text=f"仅显示前 {limit} 项（共 {len(visible)} 项），请用筛选缩小范围",
                fill="#9ca3af",
            )
        self._update_selection_label()

    def _grid_show_thumb(self, mid, path):
        cell = self.grid_cells.get(mid)
        if not cell:
            return
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((158, 112), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
        except Exception:
            return
        x1, y1, x2, _y2 = self.grid_canvas.coords(cell["rect"])
        cx = (x1 + x2) / 2
        cy = y1 + 64
        if cell.get("img"):
            self.grid_canvas.itemconfigure(cell["img"], image=photo)
        else:
            cell["img"] = self.grid_canvas.create_image(cx, cy, image=photo, tags=cell["tags"])
        self.grid_images[mid] = photo

    def _on_grid_item(self, mid):
        self._toggle(mid)
        self._preview(mid)
        cell = self.grid_cells.get(mid)
        if not cell:
            return
        selected = mid in self.selected
        self.grid_canvas.itemconfigure(cell["selected"], text="☑" if selected else "☐")
        if self._is_downloaded(mid):
            outline = self.style.colors.primary if selected else "#22c55e"
        else:
            outline = self.style.colors.primary if selected else self.style.colors.border
        self.grid_canvas.itemconfigure(cell["rect"], outline=outline)

    def _request_thumb(self, mid):
        key = (self.current_dialog_id, mid)
        if key in self.thumb_requested:
            return
        self.thumb_requested.add(key)
        self.service.fetch_thumb(self.current_dialog_id, mid)

    def toggle_view(self):
        if self.view_mode == "list":
            self.view_mode = "grid"
            self.msg_tree.grid_remove()
            self.msg_scroll.grid_remove()
            self.grid_frame.grid()
            self.view_btn.configure(text="列表")
        else:
            self.view_mode = "list"
            self.grid_frame.grid_remove()
            self.msg_tree.grid()
            self.msg_scroll.grid()
            self.view_btn.configure(text="网格")
        self.update_idletasks()
        self._render_current_view()

    def open_filter(self):
        FilterDialog(self)

    def update_filter_indicator(self):
        f = self.msg_filter
        active = bool(
            f.get("keyword")
            or f.get("date_from")
            or f.get("date_to")
            or f.get("size_min") is not None
            or f.get("size_max") is not None
            or f.get("undownloaded")
        )
        self.filter_btn.configure(text="筛选 ●" if active else "筛选…")

    def _on_msg_click(self, event):
        if self.msg_tree.identify_region(event.x, event.y) not in ("cell", "tree"):
            return
        iid = self.msg_tree.identify_row(event.y)
        if iid:
            mid = int(iid)
            self._toggle(mid)
            self._preview(mid)
        return "break"

    def _on_msg_space(self, _event):
        iid = self.msg_tree.focus()
        if iid:
            mid = int(iid)
            self._toggle(mid)
            self._preview(mid)
        return "break"

    def _toggle(self, mid):
        item = self.meta_by_id.get(mid)
        if not item or not item["downloadable"]:
            return
        if mid in self.selected:
            self.selected.discard(mid)
        else:
            self.selected.add(mid)
        iid = str(mid)
        if self.msg_tree.exists(iid):
            self.msg_tree.item(iid, values=self._row_values(item))
        self._update_selection_label()

    def select_all_visible(self):
        for item in self.loaded_messages:
            if self._visible(item) and item["downloadable"]:
                self.selected.add(item["id"])
        self._render_current_view()

    def clear_selection(self):
        self.selected.clear()
        self._render_current_view()

    def _update_selection_label(self):
        size = sum((self.meta_by_id.get(mid) or {}).get("size", 0) for mid in self.selected)
        self.selection_label.configure(text=f"已选 {len(self.selected)} 项 · {human_size(size)}")

    # ---------- 预览 ----------
    def _preview_placeholder(self, text):
        self.preview_image.configure(image="", text=text)
        self.preview_image.image = None

    def _clear_preview(self):
        self.preview_mid = None
        self.preview_loading = False
        self.preview_name.configure(text="")
        self.preview_meta.configure(text="")
        self.preview_caption.configure(text="")
        self.preview_action.configure(text="预览", state="disabled")
        self._preview_placeholder("点击消息查看缩略图")

    def _preview(self, mid):
        item = self.meta_by_id.get(mid)
        if not item:
            self._clear_preview()
            return
        self.preview_mid = mid
        self.preview_loading = False
        self.preview_name.configure(text=item["name"] or "（无文件名）")
        self.preview_meta.configure(
            text=f'{TYPE_LABELS.get(item["type"], item["type"])} · {human_size(item["size"])} · {item["date"]}'
        )
        self.preview_caption.configure(text=item["caption"] or "")
        actions = {
            "photo": "查看大图",
            "video": "播放视频",
            "audio": "播放音频",
            "voice": "播放音频",
            "document": "打开文件",
        }
        action_text = actions.get(item["type"])
        if action_text:
            self.preview_action.configure(text=action_text, state="normal")
        else:
            self.preview_action.configure(text="无预览", state="disabled")
        if item["type"] in ("photo", "video", "document"):
            self._preview_placeholder("正在加载缩略图…")
            self._request_thumb(mid)
        elif item["type"] in ("audio", "voice"):
            self._preview_placeholder("音频文件，无缩略图")
        else:
            self._preview_placeholder("文本消息，无预览")

    def _preview_action(self):
        if self.preview_loading:
            return
        mid = self.preview_mid
        item = self.meta_by_id.get(mid)
        if not item or not item["downloadable"]:
            return
        size = item.get("size") or 0
        if size > 100 * 1024 * 1024:
            if not messagebox.askyesno(
                "大文件预览",
                f"该文件 {human_size(size)}，预览需要先完整下载到临时目录。\n是否继续？",
                parent=self,
            ):
                return
        self.preview_loading = True
        self.preview_action.configure(state="disabled")
        self._preview_placeholder("正在下载预览…")
        self.log(f"加载预览：{item['name'] or f'消息 {mid}'}")
        self.service.preview_media(self.current_dialog_id, mid)

    # ---------- 下载 ----------
    def _browse_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get() or "E:\\")
        if path:
            self.dir_var.set(path)
            self.config_data["download_dir"] = path
            save_config(self.config_data)

    def _open_dir(self):
        path = self.dir_var.get().strip()
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def start_download(self):
        if not self.authorized:
            messagebox.showwarning("未登录", "请先完成 Telegram 扫码登录。", parent=self)
            return
        if self.downloading:
            return
        if not self.current_dialog_id:
            messagebox.showwarning("未选择频道", "请先在左侧选择一个频道或群组。", parent=self)
            return
        ids = [item["id"] for item in self.loaded_messages if item["id"] in self.selected and item["downloadable"]]
        if not ids:
            messagebox.showinfo("未选择内容", "请勾选要下载的消息（文本消息不能下载）。", parent=self)
            return
        out_dir = self.dir_var.get().strip()
        if not out_dir:
            messagebox.showwarning("缺少目录", "请选择保存目录。", parent=self)
            return
        try:
            raw_concurrency = int(self.concurrency_var.get())
        except (TypeError, ValueError):
            raw_concurrency = 8
        concurrency = max(1, min(64, raw_concurrency))
        if raw_concurrency != concurrency:
            self.concurrency_var.set(str(concurrency))
            self.log(f"并发已限制为 {concurrency}（过高会触发 Telegram 限流）")
        elif concurrency > 16:
            self.log(f"提示：并发 {concurrency} 较高，可能触发限流，建议 8-16")
        self.config_data["download_dir"] = out_dir
        self.config_data["page_size"] = self._page_size()
        self.config_data["concurrency"] = concurrency
        save_config(self.config_data)

        self.dl_state = {mid: (0, (self.meta_by_id.get(mid) or {}).get("size", 0)) for mid in ids}
        self.download_meta = {mid: (self.meta_by_id.get(mid) or {}) for mid in ids}
        self.download_dialog_id = self.current_dialog_id
        self.progress_var.set(0)
        self.progress_label.configure(text="0%")
        self.downloading = True
        self.download_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_label.configure(text=f"开始下载 {len(ids)} 个文件…")
        self.log(f"开始下载 {len(ids)} 个文件到 {out_dir}（并发 {concurrency}）")
        self.service.download(ids, out_dir, self.current_channel_title, concurrency, self.current_dialog_id)

    def stop_download(self):
        if not self.downloading:
            return
        self.status_label.configure(text="正在停止下载…")
        self.service.stop_download()

    def _update_progress(self):
        if not self.dl_state:
            return
        done = 0.0
        total = 0.0
        for received, size in self.dl_state.values():
            if size:
                done += min(received, size)
                total += size
            else:
                done += 1.0 if received else 0.0
                total += 1.0
        pct = (done / total * 100.0) if total else 0.0
        self.progress_var.set(pct)
        self.progress_label.configure(text=f"{pct:.1f}%")

    # ---------- 其他 ----------
    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.log_text.see("end")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 300:
            self.log_text.delete("1.0", f"{line_count - 300}.0")
        self.log_text.configure(state="disabled")

    def _on_close(self):
        try:
            self.service.stop()
        except Exception:
            pass
        self.after(150, self.destroy)


if __name__ == "__main__":
    app = App()
    app.mainloop()
