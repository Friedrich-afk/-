import os
import sys
import re
import threading
import queue
import time
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import sv_ttk
    HAS_SV_TTK = True
except ImportError:
    HAS_SV_TTK = False

# 尝试导入拖拽库，如果失败则回退到原生 tk.Tk
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
    BaseWindow = TkinterDnD.Tk
except ImportError:
    HAS_DND = False
    BaseWindow = tk.Tk


# -------------------------
# Utils & Config
# -------------------------
def default_output_dir() -> str:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent
    return str(base / "output")


def safe_name(s: str, max_len: int = 120) -> str:
    s = (s or "").strip()
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", s)
    s = re.sub(r"\s+", " ", s)
    return s[:max_len].rstrip(" .") or "untitled"


def ensure_dir(p: str) -> str:
    os.makedirs(p, exist_ok=True)
    return p


def open_in_explorer(path: str):
    try:
        if os.path.isdir(path):
            os.startfile(path)
        else:
            os.startfile(os.path.dirname(path))
    except Exception as e:
        messagebox.showwarning("提示", f"无法打开：{path}\n{e}")

# 配置持久化
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


# -------------------------
# PDF core (PyMuPDF)
# -------------------------
def get_pdf_toc(pdf_path: str):
    import fitz
    doc = fitz.open(pdf_path)
    toc = doc.get_toc(simple=True)
    page_count = doc.page_count
    doc.close()
    if not toc:
        return [], page_count, []
    
    available_levels = sorted({row[0] for row in toc})
    norm = [[int(r[0]), str(r[1]), int(r[2])] for r in toc]
    return norm, page_count, available_levels

def export_pdf_range(pdf_path: str, out_path: str, start_1b: int, end_1b: int, watermark: str = ""):
    import fitz
    doc = fitz.open(pdf_path)
    total = doc.page_count
    start_idx = min(max(start_1b - 1, 0), total - 1)
    end_idx = min(max(end_1b - 1, start_idx), total - 1)

    new_doc = fitz.open()
    new_doc.insert_pdf(doc, from_page=start_idx, to_page=end_idx)
    
    # 增加水印
    if watermark:
        for page in new_doc:
            rect = page.rect
            page.insert_text((rect.width/4, rect.height/2), watermark, fontsize=40, color=(0.8, 0.2, 0.2), fill_opacity=0.3, rotate=45)

    ensure_dir(os.path.dirname(out_path))
    new_doc.save(out_path)
    new_doc.close()
    doc.close()

def merge_pdfs(pdf_list: list, out_path: str):
    import fitz
    merged_doc = fitz.open()
    for pdf in pdf_list:
        doc = fitz.open(pdf)
        merged_doc.insert_pdf(doc)
        doc.close()
    ensure_dir(os.path.dirname(out_path))
    merged_doc.save(out_path)
    merged_doc.close()


# -------------------------
# GUI
# -------------------------
class App(BaseWindow):
    def __init__(self):
        super().__init__()
        self.title("MarkItDown GUI - 高级完整版")
        self.geometry("1200x800")
        
        # 共享状态
        self.cfg = load_config()
        self.out_dir = tk.StringVar(value=self.cfg.get("out_dir", default_output_dir()))
        self.debug_mode = tk.BooleanVar(value=self.cfg.get("debug_mode", False))
        self.auto_scroll = tk.BooleanVar(value=self.cfg.get("auto_scroll", True))
        self.global_status = tk.StringVar(value="就绪")
        
        # 背景图片状态及引用字典
        self.bg_images = self.cfg.get("bg_images", {})
        self.bg_widgets = {}
        
        self.busy = False
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker_thread = None
        self.q = queue.Queue()

        # 挂载现代主题
        if HAS_SV_TTK:
            theme = self.cfg.get("theme", "dark")
            sv_ttk.set_theme(theme)

        self._build_ui()
        self._load_state()
        self._poll_queue()
        
        self._log(f"程序启动。是否支持拖拽: {'是' if HAS_DND else '否(请安装 tkinterdnd2)'}")
        
        # 绑定关闭事件保存配置
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._save_state()
        self.destroy()

    def _load_state(self):
        self.md_use_plugins.set(self.cfg.get("md_use_plugins", False))
        self.md_merge_output.set(self.cfg.get("md_merge", False))
        self.md_regex_text.insert("1.0", self.cfg.get("md_regex", "pattern:::replacement\n"))
        self.split_level.set(self.cfg.get("split_level", 1))
        self.cmb_level.set(str(self.split_level.get()))
        self.split_watermark.set(self.cfg.get("split_watermark", ""))

    def _save_state(self):
        self.cfg["out_dir"] = self.out_dir.get()
        self.cfg["md_use_plugins"] = self.md_use_plugins.get()
        self.cfg["md_merge"] = self.md_merge_output.get()
        self.cfg["md_regex"] = self.md_regex_text.get("1.0", "end-1c")
        self.cfg["split_level"] = self.split_level.get()
        self.cfg["split_watermark"] = self.split_watermark.get()
        self.cfg["debug_mode"] = self.debug_mode.get()
        self.cfg["auto_scroll"] = self.auto_scroll.get()
        self.cfg["bg_images"] = self.bg_images
        if HAS_SV_TTK:
            self.cfg["theme"] = sv_ttk.get_theme()
        save_config(self.cfg)

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        self.root_frame = root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # 顶部全局控制区
        top_frm = ttk.Frame(root)
        top_frm.pack(fill="x", pady=(0, 10))
        
        ttk.Label(top_frm, text="全局输出目录：").pack(side="left")
        ttk.Entry(top_frm, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top_frm, text="📂 浏览...", command=self.pick_out_dir).pack(side="left")
        ttk.Button(top_frm, text="🗂️ 打开目录", command=self.open_out_dir).pack(side="left", padx=5)
        if HAS_SV_TTK:
            ttk.Button(top_frm, text="🌓 切换主题", command=self.toggle_theme).pack(side="left", padx=5)

        # 使用 PanedWindow 划分工作区和日志区，允许用户自由拖拽调节比例
        self.paned = ttk.PanedWindow(root, orient="vertical")
        self.paned.pack(fill="both", expand=True)

        # 上半部分：多标签页
        self.nb = ttk.Notebook(self.paned)
        self.paned.add(self.nb, weight=3)

        self.tab_md = ttk.Frame(self.nb, padding=10)
        self.tab_toc = ttk.Frame(self.nb, padding=10)
        self.tab_range = ttk.Frame(self.nb, padding=10)
        self.tab_merge = ttk.Frame(self.nb, padding=10)
        self.tab_appearance = ttk.Frame(self.nb, padding=10)

        # 标签页增加图标
        self.nb.add(self.tab_md, text="📄 转 Markdown")
        self.nb.add(self.tab_toc, text="📑 PDF TOC 拆分")
        self.nb.add(self.tab_range, text="✂️ PDF 范围/等分拆分")
        self.nb.add(self.tab_merge, text="🔗 PDF 合并")
        self.nb.add(self.tab_appearance, text="🎨 外观设置")

        self._build_md_tab(self.tab_md)
        self._build_toc_tab(self.tab_toc)
        self._build_range_tab(self.tab_range)
        self._build_merge_tab(self.tab_merge)
        self._build_appearance_tab(self.tab_appearance)

        # 下半部分：日志区
        log_frm = ttk.Frame(self.paned)
        self.paned.add(log_frm, weight=1)
        
        log_ctrl = ttk.Frame(log_frm)
        log_ctrl.pack(fill="x")
        ttk.Label(log_ctrl, text="运行日志：").pack(side="left")
        ttk.Checkbutton(log_ctrl, text="自动滚动", variable=self.auto_scroll).pack(side="left", padx=10)
        ttk.Checkbutton(log_ctrl, text="调试模式", variable=self.debug_mode).pack(side="left")
        ttk.Button(log_ctrl, text="💾 导出日志", command=self.export_log).pack(side="right")
        ttk.Button(log_ctrl, text="🗑️ 清空", command=lambda: self.txt_log.delete("1.0", "end")).pack(side="right", padx=5)

        self.txt_log = tk.Text(log_frm, height=8, wrap="word")
        self.txt_log.pack(fill="both", expand=True, pady=(5,0))
        # 日志富文本标签优化
        self.txt_log.tag_config("error", foreground="#ff4d4f")
        self.txt_log.tag_config("success", foreground="#52c41a")
        self.txt_log.tag_config("debug", foreground="#8c8c8c")

        # 全局底部状态栏与控制
        bot_frm = ttk.Frame(root)
        bot_frm.pack(fill="x", pady=(10, 0))
        
        self.btn_start = ttk.Button(bot_frm, text="▶ 开始当前任务", command=self.start_current_task)
        self.btn_start.pack(side="left")
        
        self.btn_pause = ttk.Button(bot_frm, text="⏸ 暂停", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=8)
        
        self.btn_stop = ttk.Button(bot_frm, text="⏹ 停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left")

        # 全局状态栏文字与进度条
        ttk.Label(bot_frm, textvariable=self.global_status, foreground="gray").pack(side="left", padx=15)

        self.global_progress = ttk.Progressbar(bot_frm, mode="determinate")
        self.global_progress.pack(side="right", fill="x", expand=True, padx=(5, 0))

        # 初始化背景层
        self.apply_all_backgrounds()

    def toggle_theme(self):
        if HAS_SV_TTK:
            current = sv_ttk.get_theme()
            sv_ttk.set_theme("light" if current == "dark" else "dark")

    # --- 右键菜单通用构建器 ---
    def _create_context_menu(self, tree, delete_cmd=None):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开所在文件夹", command=lambda: self._ctx_open_dir(tree))
        menu.add_separator()
        if delete_cmd:
            menu.add_command(label="移除选中项", command=delete_cmd)
        menu.add_command(label="清空列表", command=lambda: tree.delete(*tree.get_children()))
        
        def popup(event):
            if tree.selection():
                menu.post(event.x_root, event.y_root)
        tree.bind("<Button-3>", popup)
        
    def _ctx_open_dir(self, tree):
        sel = tree.selection()
        if not sel: return
        cols = tree["columns"]
        path = ""
        for col in ["in", "pdf", "out", "path"]:
            if col in cols:
                path = tree.set(sel[0], col)
                if path and os.path.exists(path):
                    break
        if path: open_in_explorer(path)

    # --- 🎨 外观与背景设置 Tab ---
    def _build_appearance_tab(self, parent):
        if not HAS_PIL:
            ttk.Label(parent, text="⚠️ 未安装 Pillow (PIL) 库，无法使用动态背景功能。\n请在命令行运行：pip install pillow", foreground="red").pack(pady=20)
            return

        ttk.Label(parent, text="您可以为窗口全局或单独的标签页设置自定义背景图 (窗口缩放时会自动高质量拉伸)。\n注意：Tkinter 原生按钮等组件不透明，背景图片主要在组件间隙的边缘与空白处透出。", foreground="gray").pack(anchor="w", pady=(0, 15))

        self.bg_vars = {}
        
        def make_bg_row(key, label_text):
            frm = ttk.Frame(parent)
            frm.pack(fill="x", pady=5)
            ttk.Label(frm, text=label_text, width=22).pack(side="left")
            
            var = tk.StringVar(value=self.bg_images.get(key, ""))
            self.bg_vars[key] = var
            ttk.Entry(frm, textvariable=var).pack(side="left", fill="x", expand=True, padx=5)
            
            def pick():
                p = filedialog.askopenfilename(filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.gif;*.bmp")])
                if p: var.set(p)
            
            def clear():
                var.set("")

            ttk.Button(frm, text="浏览...", command=pick).pack(side="left", padx=2)
            ttk.Button(frm, text="清除", command=clear).pack(side="left")

        make_bg_row("global", "全局背景 (底层大屏):")
        make_bg_row("md", "📄 转 Markdown 标签:")
        make_bg_row("toc", "📑 TOC 拆分 标签:")
        make_bg_row("range", "✂️ 范围拆分 标签:")
        make_bg_row("merge", "🔗 PDF 合并 标签:")

        ttk.Button(parent, text="💾 应用并保存背景设置", command=self.save_and_apply_bgs).pack(pady=20)

    def save_and_apply_bgs(self):
        for k, v in self.bg_vars.items():
            if v.get() and os.path.exists(v.get()):
                self.bg_images[k] = v.get()
            else:
                self.bg_images.pop(k, None)
        self._save_state()
        self.apply_all_backgrounds()
        self._log("背景设置已应用并生效！", "success")

    def apply_all_backgrounds(self):
        if not HAS_PIL: return
        mapping = {
            "global": self.root_frame,
            "md": self.tab_md,
            "toc": self.tab_toc,
            "range": self.tab_range,
            "merge": self.tab_merge
        }
        for key, widget in mapping.items():
            self.set_background(key, widget, self.bg_images.get(key))

    def set_background(self, bg_key, parent_widget, img_path):
        if bg_key in self.bg_widgets:
            self.bg_widgets[bg_key].destroy()
            del self.bg_widgets[bg_key]

        if not img_path or not os.path.exists(img_path):
            return

        try:
            original_img = Image.open(img_path)
        except Exception as e:
            self._log(f"无法加载图片 {img_path}: {e}", "error")
            return

        bg_label = tk.Label(parent_widget, bd=0)
        bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        bg_label.lower() 
        self.bg_widgets[bg_key] = bg_label

        def on_resize(event):
            if event.widget != parent_widget: return
            w, h = event.width, event.height
            if w <= 1 or h <= 1: return

            timer_key = f"_bg_timer_{bg_key}"
            if hasattr(self, timer_key):
                self.after_cancel(getattr(self, timer_key))

            def do_resize():
                try:
                    resized = original_img.resize((w, h), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(resized)
                    bg_label.config(image=photo)
                    bg_label.image = photo
                except Exception:
                    pass
            
            setattr(self, timer_key, self.after(80, do_resize))

        parent_widget.bind("<Configure>", on_resize, add="+")

    # --- MD Tab ---
    def _build_md_tab(self, parent):
        self.md_files = []
        self.md_use_plugins = tk.BooleanVar()
        self.md_merge_output = tk.BooleanVar()

        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(0, 5))
        ttk.Button(ctrl, text="📄 添加文件...", command=self.md_pick_files).pack(side="left")
        ttk.Checkbutton(ctrl, text="启用 MarkItDown 插件", variable=self.md_use_plugins).pack(side="left", padx=15)
        ttk.Checkbutton(ctrl, text="将结果合并为单一文件", variable=self.md_merge_output).pack(side="left")

        reg_frm = ttk.LabelFrame(parent, text="正则清洗规则 (每行一条: 正则表达式:::替换内容)")
        reg_frm.pack(fill="x", pady=5)
        self.md_regex_text = tk.Text(reg_frm, height=3)
        self.md_regex_text.pack(fill="x", padx=5, pady=5)

        self.md_tree = ttk.Treeview(parent, columns=("status", "in", "out"), show="headings", height=8)
        self.md_tree.heading("status", text="状态")
        self.md_tree.heading("in", text="输入文件")
        self.md_tree.heading("out", text="输出文件")
        self.md_tree.column("status", width=80, anchor="center")
        self.md_tree.column("in", width=400)
        self.md_tree.column("out", width=400)
        self.md_tree.pack(fill="both", expand=True)
        
        self._create_context_menu(self.md_tree, delete_cmd=lambda: self.md_tree.delete(*self.md_tree.selection()))
        
        if HAS_DND:
            self.md_tree.drop_target_register(DND_FILES)
            self.md_tree.dnd_bind('<<Drop>>', lambda e: self._handle_drop(e, self.md_tree, "md"))

    # --- TOC Tab ---
    def _build_toc_tab(self, parent):
        self.toc_pdfs = []
        self.split_level = tk.IntVar(value=1)
        self.split_watermark = tk.StringVar()

        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(0, 5))
        ttk.Button(ctrl, text="📑 添加 PDF...", command=self.toc_pick_files).pack(side="left")
        ttk.Button(ctrl, text="🔍 扫描目录加载", command=self.toc_scan).pack(side="left", padx=8)
        
        ttk.Label(ctrl, text="层级:").pack(side="left", padx=5)
        self.cmb_level = ttk.Combobox(ctrl, values=[1, 2, 3, 4], width=5, state="readonly")
        self.cmb_level.pack(side="left")
        self.cmb_level.bind("<<ComboboxSelected>>", lambda e: self.split_level.set(int(self.cmb_level.get())))
        
        ttk.Label(ctrl, text="加文字水印:").pack(side="left", padx=(15, 5))
        ttk.Entry(ctrl, textvariable=self.split_watermark, width=15).pack(side="left")

        self.toc_tree = ttk.Treeview(parent, columns=("check", "title", "pages", "pdf"), show="headings", height=8)
        self.toc_tree.heading("check", text="勾选")
        self.toc_tree.heading("title", text="章节名称")
        self.toc_tree.heading("pages", text="页码范围")
        self.toc_tree.heading("pdf", text="归属 PDF")
        self.toc_tree.column("check", width=50, anchor="center")
        self.toc_tree.column("title", width=400)
        self.toc_tree.column("pages", width=100, anchor="center")
        self.toc_tree.column("pdf", width=300)
        self.toc_tree.pack(fill="both", expand=True)

        self.toc_tree.bind("<Button-1>", self._toc_toggle_check)
        self._create_context_menu(self.toc_tree, delete_cmd=lambda: self.toc_tree.delete(*self.toc_tree.selection()))
        
        if HAS_DND:
            self.toc_tree.drop_target_register(DND_FILES)
            self.toc_tree.dnd_bind('<<Drop>>', lambda e: self._handle_drop(e, self.toc_tree, "toc"))

    def _toc_toggle_check(self, event):
        region = self.toc_tree.identify_region(event.x, event.y)
        if region == "cell":
            col = self.toc_tree.identify_column(event.x)
            if col == "#1":
                iid = self.toc_tree.identify_row(event.y)
                val = self.toc_tree.set(iid, "check")
                self.toc_tree.set(iid, "check", "☐" if val == "☑" else "☑")

    # --- Range Tab ---
    def _build_range_tab(self, parent):
        self.range_mode = tk.StringVar(value="custom")
        self.range_custom_val = tk.StringVar(value="1-5, 8-10")
        self.range_equal_val = tk.IntVar(value=10)
        
        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(0, 5))
        ttk.Button(ctrl, text="✂️ 添加 PDF...", command=lambda: self._pick_files(self.range_tree, "range")).pack(side="left")
        
        opt_frm = ttk.LabelFrame(parent, text="拆分规则")
        opt_frm.pack(fill="x", pady=5)
        
        f1 = ttk.Frame(opt_frm)
        f1.pack(fill="x", padx=5, pady=2)
        ttk.Radiobutton(f1, text="按指定范围提取 (如: 1-5, 8, 11-15)", variable=self.range_mode, value="custom").pack(side="left")
        ttk.Entry(f1, textvariable=self.range_custom_val, width=30).pack(side="left", padx=10)
        
        f2 = ttk.Frame(opt_frm)
        f2.pack(fill="x", padx=5, pady=2)
        ttk.Radiobutton(f2, text="等分拆分 (每 N 页一个文件)", variable=self.range_mode, value="equal").pack(side="left")
        ttk.Spinbox(f2, from_=1, to=9999, textvariable=self.range_equal_val, width=5).pack(side="left", padx=10)

        self.range_tree = ttk.Treeview(parent, columns=("status", "pdf", "info"), show="headings", height=6)
        self.range_tree.heading("status", text="状态")
        self.range_tree.heading("pdf", text="PDF 文件")
        self.range_tree.heading("info", text="拆分详情")
        self.range_tree.column("status", width=80, anchor="center")
        self.range_tree.column("pdf", width=400)
        self.range_tree.column("info", width=300)
        self.range_tree.pack(fill="both", expand=True)
        
        self._create_context_menu(self.range_tree, delete_cmd=lambda: self.range_tree.delete(*self.range_tree.selection()))
        if HAS_DND:
            self.range_tree.drop_target_register(DND_FILES)
            self.range_tree.dnd_bind('<<Drop>>', lambda e: self._handle_drop(e, self.range_tree, "range"))

    # --- Merge Tab ---
    def _build_merge_tab(self, parent):
        self.merge_name = tk.StringVar(value="Merged_Output.pdf")

        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(0, 5))
        ttk.Button(ctrl, text="🔗 添加 PDF...", command=lambda: self._pick_files(self.merge_tree, "merge")).pack(side="left")
        ttk.Button(ctrl, text="⬆️ 上移", command=lambda: self._move_item(self.merge_tree, -1)).pack(side="left", padx=5)
        ttk.Button(ctrl, text="⬇️ 下移", command=lambda: self._move_item(self.merge_tree, 1)).pack(side="left")
        
        ttk.Label(ctrl, text="输出文件名:").pack(side="left", padx=(20, 5))
        ttk.Entry(ctrl, textvariable=self.merge_name, width=20).pack(side="left")

        self.merge_tree = ttk.Treeview(parent, columns=("index", "pdf"), show="headings", height=8)
        self.merge_tree.heading("index", text="顺序")
        self.merge_tree.heading("pdf", text="PDF 文件")
        self.merge_tree.column("index", width=50, anchor="center")
        self.merge_tree.column("pdf", width=700)
        self.merge_tree.pack(fill="both", expand=True)
        
        self._create_context_menu(self.merge_tree, delete_cmd=lambda: self._remove_and_reindex(self.merge_tree))
        if HAS_DND:
            self.merge_tree.drop_target_register(DND_FILES)
            self.merge_tree.dnd_bind('<<Drop>>', lambda e: self._handle_drop(e, self.merge_tree, "merge"))

    # --- 通用交互助手 ---
    def _handle_drop(self, event, tree, mode):
        files = self.tk.splitlist(event.data)
        for f in files:
            if mode == "md":
                tree.insert("", "end", values=("等待", f, ""))
            elif mode == "toc" or mode == "range":
                if f.lower().endswith(".pdf"):
                    tree.insert("", "end", values=("等待", f, ""))
            elif mode == "merge":
                if f.lower().endswith(".pdf"):
                    idx = len(tree.get_children()) + 1
                    tree.insert("", "end", values=(idx, f))

    def _pick_files(self, tree, mode):
        ft = [("PDF", "*.pdf"), ("All", "*.*")] if mode != "md" else [("Docs", "*.pdf;*.docx;*.pptx;*.xlsx;*.txt"), ("All", "*.*")]
        paths = filedialog.askopenfilenames(title="选择文件", filetypes=ft)
        for p in paths:
            if mode == "merge":
                idx = len(tree.get_children()) + 1
                tree.insert("", "end", values=(idx, p))
            else:
                tree.insert("", "end", values=("等待", p, ""))

    def _move_item(self, tree, direction):
        sel = tree.selection()
        if not sel: return
        for iid in sel:
            idx = tree.index(iid)
            tree.move(iid, tree.parent(iid), idx + direction)
        self._reindex_tree(tree)

    def _remove_and_reindex(self, tree):
        for iid in tree.selection():
            tree.delete(iid)
        self._reindex_tree(tree)

    def _reindex_tree(self, tree):
        for i, iid in enumerate(tree.get_children(), 1):
            tree.set(iid, "index", i)

    # ---------------- 业务逻辑与多线程调度 ----------------
    def _set_status(self, msg):
        self.q.put(("status", msg))

    def _log(self, msg, level="info"):
        if level == "debug" and not self.debug_mode.get(): return
        ts = time.strftime("%H:%M:%S")
        self.q.put(("log", (f"[{ts}] {msg}", level)))
        if level != "debug":
            self._set_status(msg)

    def _poll_queue(self):
        try:
            while True:
                msg_type, data = self.q.get_nowait()
                if msg_type == "log":
                    txt, lvl = data
                    self.txt_log.insert("end", txt + "\n", lvl)
                    if self.auto_scroll.get():
                        self.txt_log.see("end")
                elif msg_type == "status":
                    self.global_status.set(data)
                elif msg_type == "progress":
                    self.global_progress["maximum"] = data[1]
                    self.global_progress["value"] = data[0]
                elif msg_type == "tree_update":
                    tree, iid, col, val = data
                    tree.set(iid, col, val)
                elif msg_type == "done":
                    self._set_busy(False)
                    self.global_status.set("就绪")
                    messagebox.showinfo("完成", data)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _set_busy(self, state):
        self.busy = state
        st_norm = "disabled" if state else "normal"
        st_stop = "normal" if state else "disabled"
        self.btn_start.config(state=st_norm)
        self.btn_pause.config(state=st_stop, text="⏸ 暂停")
        self.btn_stop.config(state=st_stop)

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        self._log("已请求停止任务...", "error")

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.btn_pause.config(text="⏸ 暂停")
            self._log("任务已继续。")
        else:
            self.pause_event.set()
            self.btn_pause.config(text="▶ 继续")
            self._log("任务已暂停。等待当前小步骤完成...")

    def _check_pause_stop(self):
        while self.pause_event.is_set():
            if self.stop_event.is_set(): return False
            time.sleep(0.5)
        return not self.stop_event.is_set()

    def start_current_task(self):
        if self.busy: return
        idx = self.nb.index("current")
        self.stop_event.clear()
        self.pause_event.clear()
        
        if idx == 0: target = self.task_md
        elif idx == 1: target = self.task_toc
        elif idx == 2: target = self.task_range
        elif idx == 3: target = self.task_merge
        else: return
        
        self._set_busy(True)
        self.global_progress["value"] = 0
        threading.Thread(target=self._worker_wrapper, args=(target,), daemon=True).start()

    def _worker_wrapper(self, func):
        try:
            func()
            if self.stop_event.is_set():
                self.q.put(("done", "任务被手动停止。"))
            else:
                self.q.put(("done", "任务执行完毕。"))
        except Exception as e:
            self._log(f"致命错误: {e}", "error")
            self.q.put(("done", f"发生错误：{e}"))
        finally:
            self.q.put(("progress", (0, 1)))

    # --- 任务：Markdown 转换 ---
    def md_pick_files(self):
        self._pick_files(self.md_tree, "md")

    def task_md(self):
        items = self.md_tree.get_children()
        total = len(items)
        if total == 0: return self._log("请先添加文件")
        
        self._log("正在初始化 MarkItDown 引擎...")
        from markitdown import MarkItDown
        md = MarkItDown(enable_plugins=self.md_use_plugins.get())
        
        rules_text = self.md_regex_text.get("1.0", "end").strip()
        rules = [line.split(":::", 1) for line in rules_text.split('\n') if ":::" in line]

        out_base = Path(self.out_dir.get())
        ensure_dir(out_base)
        
        merged_content = []
        
        for i, iid in enumerate(items, 1):
            if not self._check_pause_stop(): break
            
            in_path = self.md_tree.set(iid, "in")
            self.q.put(("tree_update", (self.md_tree, iid, "status", "处理中")))
            self._log(f"[MD] 正在转换: {Path(in_path).name}")
            
            try:
                result = md.convert(in_path)
                text = result.text_content
                
                for pat, repl in rules:
                    text = re.sub(pat, repl, text)
                
                if self.md_merge_output.get():
                    merged_content.append(f"\n\n# Source: {Path(in_path).name}\n\n{text}")
                    self.q.put(("tree_update", (self.md_tree, iid, "status", "合并缓存中")))
                else:
                    out_name = safe_name(Path(in_path).stem) + ".md"
                    out_path = out_base / out_name
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(text)
                    self.q.put(("tree_update", (self.md_tree, iid, "status", "✅ 成功")))
                    self.q.put(("tree_update", (self.md_tree, iid, "out", str(out_path))))
                    
            except Exception as e:
                self.q.put(("tree_update", (self.md_tree, iid, "status", "❌ 失败")))
                self._log(f"转换失败 {in_path}: {e}", "error")
                
            self.q.put(("progress", (i, total)))
            
        if self.md_merge_output.get() and merged_content and not self.stop_event.is_set():
            out_path = out_base / "Merged_Output.md"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("".join(merged_content))
            self._log(f"合并完成！已输出至: {out_path}", "success")

    # --- 任务：TOC 拆分 ---
    def toc_pick_files(self):
        paths = filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf")])
        for p in paths:
            if p not in self.toc_pdfs:
                self.toc_pdfs.append(p)
        self._log(f"已添加到缓冲区：{len(self.toc_pdfs)} 个PDF")

    def toc_scan(self):
        if not self.toc_pdfs: return self._log("未选择 PDF 文件。")
        lvl = self.split_level.get()
        self.toc_tree.delete(*self.toc_tree.get_children())
        
        def _scan():
            for pdf in self.toc_pdfs:
                self._log(f"扫描 TOC: {Path(pdf).name}")
                try:
                    toc, pages, _ = get_pdf_toc(pdf)
                    items = [r for r in toc if r[0] == lvl]
                    items.sort(key=lambda x: x[2])
                    
                    for i, row in enumerate(items):
                        start = max(1, row[2])
                        end = items[i+1][2] - 1 if i < len(items) - 1 else pages
                        end = max(start, min(end, pages))
                        
                        val = ("☑", row[1], f"{start}-{end}", pdf)
                        self.toc_tree.insert("", "end", values=val)
                except Exception as e:
                    self._log(f"扫描失败 {pdf}: {e}", "error")
            self._log("TOC 扫描完成，请在列表中取消勾选不需要的章节。", "success")
        threading.Thread(target=_scan, daemon=True).start()

    def task_toc(self):
        items = self.toc_tree.get_children()
        checked = [iid for iid in items if self.toc_tree.set(iid, "check") == "☑"]
        total = len(checked)
        if total == 0: return self._log("没有勾选任何需要拆分的章节。")

        out_base = Path(self.out_dir.get()) / "TOC_Split"
        ensure_dir(out_base)
        wm = self.split_watermark.get()

        for i, iid in enumerate(checked, 1):
            if not self._check_pause_stop(): break
            
            title = self.toc_tree.set(iid, "title")
            pages = self.toc_tree.set(iid, "pages")
            pdf = self.toc_tree.set(iid, "pdf")
            
            s_str, e_str = pages.split("-")
            
            out_name = f"{i:03d}_{safe_name(title)}.pdf"
            out_path = out_base / safe_name(Path(pdf).stem) / out_name
            
            self._log(f"[TOC] 导出: {title} ({pages})")
            try:
                export_pdf_range(pdf, str(out_path), int(s_str), int(e_str), watermark=wm)
                self.q.put(("tree_update", (self.toc_tree, iid, "check", "✅")))
            except Exception as e:
                self.q.put(("tree_update", (self.toc_tree, iid, "check", "❌")))
                self._log(f"导出失败: {e}", "error")
            
            self.q.put(("progress", (i, total)))

    # --- 任务：范围/等分拆分 ---
    def task_range(self):
        items = self.range_tree.get_children()
        total = len(items)
        if total == 0: return self._log("请先添加 PDF 文件")

        out_base = Path(self.out_dir.get()) / "Range_Split"
        ensure_dir(out_base)
        wm = self.split_watermark.get() 
        
        mode = self.range_mode.get()

        for i, iid in enumerate(items, 1):
            if not self._check_pause_stop(): break
            pdf = self.range_tree.set(iid, "pdf")
            self.q.put(("tree_update", (self.range_tree, iid, "status", "处理中")))
            
            try:
                import fitz
                doc = fitz.open(pdf)
                max_page = doc.page_count
                doc.close()
                
                ranges = []
                if mode == "equal":
                    step = self.range_equal_val.get()
                    for p in range(1, max_page + 1, step):
                        ranges.append((p, min(p + step - 1, max_page)))
                else:
                    parts = self.range_custom_val.get().split(',')
                    for pt in parts:
                        pt = pt.strip()
                        if '-' in pt:
                            s, e = pt.split('-')
                            ranges.append((int(s), int(e)))
                        elif pt.isdigit():
                            ranges.append((int(pt), int(pt)))
                
                pdf_stem = safe_name(Path(pdf).stem)
                for idx, (s, e) in enumerate(ranges, 1):
                    if not self._check_pause_stop(): break
                    out_path = out_base / f"{pdf_stem}_part{idx}_p{s}-{e}.pdf"
                    export_pdf_range(pdf, str(out_path), s, e, watermark=wm)
                
                self.q.put(("tree_update", (self.range_tree, iid, "status", "✅ 成功")))
                self.q.put(("tree_update", (self.range_tree, iid, "info", f"拆分为 {len(ranges)} 份")))
            except Exception as e:
                self.q.put(("tree_update", (self.range_tree, iid, "status", "❌ 失败")))
                self._log(f"拆分出错 {pdf}: {e}", "error")
            
            self.q.put(("progress", (i, total)))

    # --- 任务：合并 PDF ---
    def task_merge(self):
        items = self.merge_tree.get_children()
        if len(items) < 2: return self._log("合并至少需要2个 PDF 文件")
        
        pdfs = [self.merge_tree.set(iid, "pdf") for iid in items]
        out_path = Path(self.out_dir.get()) / safe_name(self.merge_name.get())
        
        self._log(f"开始合并 {len(pdfs)} 个文件...")
        try:
            merge_pdfs(pdfs, str(out_path))
            self._log(f"合并成功！输出至: {out_path}", "success")
        except Exception as e:
            self._log(f"合并失败: {e}", "error")

    # ---------------- 杂项功能 ----------------
    def pick_out_dir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d: self.out_dir.set(d)

    def open_out_dir(self):
        open_in_explorer(self.out_dir.get())
        
    def export_log(self):
        txt = self.txt_log.get("1.0", "end")
        out_path = Path(self.out_dir.get()) / "run_log.txt"
        try:
            ensure_dir(self.out_dir.get())
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(txt)
            messagebox.showinfo("成功", f"日志已导出至:\n{out_path}")
        except Exception as e:
            messagebox.showerror("错误", f"导出日志失败: {e}")

if __name__ == "__main__":
    app = App()
    app.mainloop()