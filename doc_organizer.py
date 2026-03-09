import os
import sys
import re
import csv
import threading
import queue
import time
import json
import subprocess
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

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
# PDF Core - 高阶学术书签解析模块
# -------------------------
_GENERIC_META_PATTERNS = [r"^microsoft word", r"^adobe", r"^untitled", r"^cambridge core", r"^pdf"]

def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def natural_pdf_sort_key(p: Path):
    m = re.match(r"^(\d+)(?:\.(\d+))?", p.stem)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2) or 0)
        return (major, minor, p.name.lower())
    return (999999, 0, p.name.lower())

def title_from_filename(path: Path) -> str:
    """
    针对剑桥大学完美适配的文件名清洗逻辑
    例如: "01.0_pp_i_iv_Front_matter.pdf" -> "Front matter"
    """
    stem = path.stem
    # 剥离前面的大标号，如 01.0_
    stem = re.sub(r"^\d+(?:\.\d+)?_", "", stem)
    # 剥离前面的页码区间，如 pp_i_iv_ 或 pp_1_20_
    stem = re.sub(r"^pp_[^_]+_[^_]+_", "", stem, flags=re.IGNORECASE)
    # 将下划线替换为空格
    title = stem.replace("_", " ")
    title = clean_spaces(title)
    return title

def looks_generic_title(title: str, path: Path) -> bool:
    t = clean_spaces(title).strip().lower()
    if not t: return True
    if t == path.stem.lower() or t == path.name.lower(): return True
    if t.isdigit(): return True # 防止纯数字或页码被当作标题
    for pat in _GENERIC_META_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE): return True
    return False

def guess_title_from_first_page(doc) -> str:
    """视觉字体大小分析法：寻找本页中最大字号的文本区块。"""
    if doc.page_count == 0: return ""
    try:
        page = doc[0]
        blocks = page.get_text("dict").get("blocks", [])
    except: return ""

    spans = []
    for b in blocks:
        if b.get("type") == 0:  # text block
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    text = clean_spaces(s.get("text", ""))
                    if text:
                        spans.append({
                            "text": text,
                            "size": s.get("size", 0)
                        })

    if not spans: return ""

    valid_spans = []
    for s in spans:
        t = s["text"]
        if len(t) < 2: continue
        if re.fullmatch(r"[\divxlcdm]+", t, flags=re.IGNORECASE): continue
        if re.fullmatch(r"\[\s*[\divxlcdm]+\s*\]", t, flags=re.IGNORECASE): continue
        valid_spans.append(s)

    if not valid_spans: return ""

    max_size = max(s["size"] for s in valid_spans)

    title_parts = []
    for s in spans:
        if abs(s["size"] - max_size) < 0.5:
            t = s["text"]
            if not re.fullmatch(r"[\divxlcdm]+", t, flags=re.IGNORECASE) and not re.fullmatch(r"\[\s*[\divxlcdm]+\s*\]", t, flags=re.IGNORECASE):
                title_parts.append(t)

    candidate = clean_spaces(" ".join(title_parts)).strip("-–—:;,. ")
    if 4 <= len(candidate) <= 200:
        return candidate
    return ""

def title_from_pdf(path: Path) -> str:
    import fitz
    
    is_cambridge = bool(re.match(r"^\d+(?:\.\d+)?_pp_", path.stem, flags=re.IGNORECASE))
    
    doc = None
    try:
        doc = fitz.open(str(path))
        
        # 1. 尝试元数据
        meta_title = clean_spaces((doc.metadata or {}).get("title", ""))
        if meta_title and not looks_generic_title(meta_title, path):
            if len(meta_title) < 100:
                return meta_title
                
        # 2. 视觉字体分析
        guessed = guess_title_from_first_page(doc)
        if guessed:
            return guessed
            
        # 3. 降级到文件名
        return title_from_filename(path)
    except Exception:
        return title_from_filename(path)
    finally:
        if doc is not None:
            doc.close()

def execute_merge_with_instructions(instructions: list, out_path: str):
    import fitz
    merged = fitz.open()
    toc = []
    current_page = 1

    for row in instructions:
        if row["type"] == "virtual":
            toc.append([row["level"], row["title"], current_page])
        elif row["type"] == "pdf":
            file_path = row["file"]
            src = fitz.open(file_path)
            if row.get("title"):
                toc.append([row["level"], row["title"], current_page])
            merged.insert_pdf(src)
            current_page += src.page_count
            src.close()

    if toc:
        merged.set_toc(toc)

    ensure_dir(os.path.dirname(out_path))
    merged.save(out_path, garbage=4, deflate=True)
    merged.close()

def convert_to_pdfa(input_pdf: str, output_pdf: str):
    """通过系统调用的 Ghostscript 将普通 PDF 转换为 PDF/A-2b 归档格式"""
    gs_cmds = ['gswin64c', 'gswin32c', 'gs']
    gs_path = None
    for cmd in gs_cmds:
        if shutil.which(cmd):
            gs_path = cmd
            break
            
    if not gs_path:
        raise FileNotFoundError("未在系统中找到 Ghostscript (gs/gswin64c)。如需生成 PDF/A 格式，请先安装 Ghostscript 并将其添加到系统环境变量中。")

    # 执行转换为 PDF/A 的命令
    cmd = [
        gs_path,
        '-dPDFA=2',
        '-dBATCH',
        '-dNOPAUSE',
        '-dUseCIEColor',
        '-sProcessColorModel=DeviceRGB',
        '-sDEVICE=pdfwrite',
        '-sPDFACompatibilityPolicy=1',
        f'-sOutputFile={output_pdf}',
        input_pdf
    ]
    
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
    res = subprocess.run(cmd, startupinfo=startupinfo, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Ghostscript 转换报错: {res.stderr}")

def load_rows_from_csv(csv_path: Path):
    rows = []
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"type", "file", "title", "level"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV 缺少关键列: {', '.join(sorted(missing))}")
        for i, row in enumerate(reader, start=2):
            r_type = clean_spaces(row.get("type", "pdf")).lower() or "pdf"
            r_file = clean_spaces(row.get("file", ""))
            r_title = clean_spaces(row.get("title", ""))
            r_level_txt = clean_spaces(str(row.get("level", "1")))
            
            if not r_title: continue # 跳过空标题
            level = max(1, int(r_level_txt) if r_level_txt.isdigit() else 1)
            
            if r_type not in {"pdf", "virtual"}: r_type = "pdf"
            rows.append({"type": r_type, "file": r_file, "title": r_title, "level": level})
    return rows


# -------------------------
# PDF core (Basic Splitting)
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
    
    if watermark:
        for page in new_doc:
            rect = page.rect
            page.insert_text((rect.width/4, rect.height/2), watermark, fontsize=40, color=(0.8, 0.2, 0.2), fill_opacity=0.3, rotate=45)

    ensure_dir(os.path.dirname(out_path))
    new_doc.save(out_path)
    new_doc.close()
    doc.close()


# -------------------------
# GUI
# -------------------------
class App(BaseWindow):
    def __init__(self):
        super().__init__()
        self.title("文献整理工具箱 (Doc Organizer)")
        self.geometry("1200x800")
        
        # 共享状态
        self.cfg = load_config()
        self.out_dir = tk.StringVar(value=self.cfg.get("out_dir", default_output_dir()))
        self.debug_mode = tk.BooleanVar(value=self.cfg.get("debug_mode", False))
        self.auto_scroll = tk.BooleanVar(value=self.cfg.get("auto_scroll", True))
        self.global_status = tk.StringVar(value="就绪")
        
        self.busy = False
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker_thread = None
        self.q = queue.Queue()

        if HAS_SV_TTK:
            theme = self.cfg.get("theme", "dark")
            sv_ttk.set_theme(theme)

        self._build_ui()
        self._load_state()
        self._poll_queue()
        
        self._log(f"程序启动。是否支持拖拽: {'是' if HAS_DND else '否(请安装 tkinterdnd2)'}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._save_state()
        self.destroy()

    def _load_state(self):
        self.md_use_plugins.set(self.cfg.get("md_use_plugins", False))
        self.md_merge_output.set(self.cfg.get("md_merge", False))
        self.md_regex_text.insert("1.0", self.cfg.get("md_regex", "pattern:::replacement\n"))
        
        # TOC/拆分状态
        self.split_level.set(self.cfg.get("split_level", 1))
        self.cmb_level.set(str(self.split_level.get()))
        self.split_watermark.set(self.cfg.get("split_watermark", ""))
        self.split_active_mode.set(self.cfg.get("split_active_mode", "toc"))
        self._toggle_split_view()
            
        # 合并状态
        self.merge_mode.set(self.cfg.get("merge_mode", "filename"))
        self.merge_pdfa.set(self.cfg.get("merge_pdfa", False))

    def _save_state(self):
        self.cfg["out_dir"] = self.out_dir.get()
        self.cfg["md_use_plugins"] = self.md_use_plugins.get()
        self.cfg["md_merge"] = self.md_merge_output.get()
        self.cfg["md_regex"] = self.md_regex_text.get("1.0", "end-1c")
        self.cfg["split_level"] = self.split_level.get()
        self.cfg["split_watermark"] = self.split_watermark.get()
        self.cfg["debug_mode"] = self.debug_mode.get()
        self.cfg["auto_scroll"] = self.auto_scroll.get()
        
        self.cfg["split_active_mode"] = self.split_active_mode.get()
        self.cfg["merge_mode"] = self.merge_mode.get()
        self.cfg["merge_pdfa"] = self.merge_pdfa.get()
            
        if HAS_SV_TTK:
            self.cfg["theme"] = sv_ttk.get_theme()
        save_config(self.cfg)

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        self.root_frame = root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top_frm = ttk.Frame(root)
        top_frm.pack(fill="x", pady=(0, 10))
        
        ttk.Label(top_frm, text="全局输出目录：").pack(side="left")
        ttk.Entry(top_frm, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top_frm, text="📂 浏览...", command=self.pick_out_dir).pack(side="left")
        ttk.Button(top_frm, text="🗂️ 打开目录", command=self.open_out_dir).pack(side="left", padx=5)
        if HAS_SV_TTK:
            ttk.Button(top_frm, text="🌓 切换主题", command=self.toggle_theme).pack(side="left", padx=5)

        self.paned = ttk.PanedWindow(root, orient="vertical")
        self.paned.pack(fill="both", expand=True)

        self.nb = ttk.Notebook(self.paned)
        self.paned.add(self.nb, weight=3)

        self.tab_md = ttk.Frame(self.nb, padding=10)
        self.tab_split = ttk.Frame(self.nb, padding=10)
        self.tab_merge = ttk.Frame(self.nb, padding=10)

        self.nb.add(self.tab_md, text="📄 转 Markdown")
        self.nb.add(self.tab_split, text="✂️ PDF TOC/范围 拆分 ")
        self.nb.add(self.tab_merge, text="🔗 PDF 合并")

        self._build_md_tab(self.tab_md)
        self._build_split_tab(self.tab_split)
        self._build_merge_tab(self.tab_merge)

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
        self.txt_log.tag_config("error", foreground="#ff4d4f")
        self.txt_log.tag_config("success", foreground="#52c41a")
        self.txt_log.tag_config("debug", foreground="#8c8c8c")

        bot_frm = ttk.Frame(root)
        bot_frm.pack(fill="x", pady=(10, 0))
        
        self.btn_start = ttk.Button(bot_frm, text="▶ 开始当前任务", command=self.start_current_task)
        self.btn_start.pack(side="left")
        
        self.btn_pause = ttk.Button(bot_frm, text="⏸ 暂停", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=8)
        
        self.btn_stop = ttk.Button(bot_frm, text="⏹ 停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left")

        ttk.Label(bot_frm, textvariable=self.global_status, foreground="gray").pack(side="left", padx=15)

        self.global_progress = ttk.Progressbar(bot_frm, mode="determinate")
        self.global_progress.pack(side="right", fill="x", expand=True, padx=(5, 0))

    def toggle_theme(self):
        if HAS_SV_TTK:
            current = sv_ttk.get_theme()
            sv_ttk.set_theme("light" if current == "dark" else "dark")

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

    # --- MD Tab ---
    def _build_md_tab(self, parent):
        self.md_files = []
        self.md_use_plugins = tk.BooleanVar()
        self.md_merge_output = tk.BooleanVar()

        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(0, 5))
        ttk.Button(ctrl, text="📄 添加文件...", command=self.md_pick_files).pack(side="left")
        ttk.Button(ctrl, text="🗑️ 清空列表", command=lambda: self.md_tree.delete(*self.md_tree.get_children())).pack(side="left", padx=5)
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

    # --- 统一拆分 Tab ---
    def _build_split_tab(self, parent):
        self.split_active_mode = tk.StringVar(value="toc")
        
        mode_frm = ttk.Frame(parent)
        mode_frm.pack(fill="x", pady=(0, 5))
        ttk.Label(mode_frm, text="选择拆分模式:").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(mode_frm, text="📑 按 TOC 书签拆分", variable=self.split_active_mode, value="toc", command=self._toggle_split_view).pack(side="left", padx=5)
        ttk.Radiobutton(mode_frm, text="✂️ 按 指定范围/等分 拆分", variable=self.split_active_mode, value="range", command=self._toggle_split_view).pack(side="left", padx=5)
        
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=5)
        
        self.frm_toc = ttk.Frame(parent)
        self.frm_range = ttk.Frame(parent)
        
        self._build_toc_tab(self.frm_toc)
        self._build_range_tab(self.frm_range)
        self._toggle_split_view()

    def _toggle_split_view(self):
        if self.split_active_mode.get() == "toc":
            self.frm_range.pack_forget()
            self.frm_toc.pack(fill="both", expand=True)
        else:
            self.frm_toc.pack_forget()
            self.frm_range.pack(fill="both", expand=True)

    def _build_toc_tab(self, parent):
        self.toc_pdfs = []
        self.split_level = tk.IntVar(value=1)
        self.split_watermark = tk.StringVar()

        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(0, 5))
        ttk.Button(ctrl, text="📑 添加 PDF...", command=self.toc_pick_files).pack(side="left")
        ttk.Button(ctrl, text="🔍 扫描目录加载", command=self.toc_scan).pack(side="left", padx=8)
        ttk.Button(ctrl, text="🗑️ 清空列表", command=lambda: self.toc_tree.delete(*self.toc_tree.get_children())).pack(side="left")
        
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

    def _build_range_tab(self, parent):
        self.range_mode = tk.StringVar(value="custom")
        self.range_custom_val = tk.StringVar(value="1-5, 8-10")
        self.range_equal_val = tk.IntVar(value=10)
        
        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(0, 5))
        ttk.Button(ctrl, text="✂️ 添加 PDF...", command=lambda: self._pick_files(self.range_tree, "range")).pack(side="left")
        ttk.Button(ctrl, text="🗑️ 清空列表", command=lambda: self.range_tree.delete(*self.range_tree.get_children())).pack(side="left", padx=5)
        
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

    # --- 高级 Merge Tab ---
    def _build_merge_tab(self, parent):
        self.merge_name = tk.StringVar(value="Merged_Academic_Book.pdf")
        self.merge_mode = tk.StringVar(value="filename")
        self.merge_pdfa = tk.BooleanVar(value=False)

        # 顶部工具栏
        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(0, 5))
        ttk.Button(ctrl, text="🔗 添加单个 PDF...", command=lambda: self._pick_files(self.merge_tree, "merge")).pack(side="left")
        ttk.Button(ctrl, text="📁 添加文件夹 (自动数字排序)...", command=self.merge_add_folder).pack(side="left", padx=8)
        
        # 新增的清空按钮，极其显眼
        ttk.Button(ctrl, text="🗑️ 清空列表", command=lambda: self.merge_tree.delete(*self.merge_tree.get_children())).pack(side="left")
        
        ttk.Button(ctrl, text="⬆️ 上移", command=lambda: self._move_item(self.merge_tree, -1)).pack(side="left", padx=(15, 5))
        ttk.Button(ctrl, text="⬇️ 下移", command=lambda: self._move_item(self.merge_tree, 1)).pack(side="left")
        
        ttk.Label(ctrl, text="输出文件名:").pack(side="left", padx=(20, 5))
        ttk.Entry(ctrl, textvariable=self.merge_name, width=25).pack(side="left")

        # 核心合并模式与模板功能 (优化后的 Grid 紧凑布局)
        opt_frm = ttk.LabelFrame(parent, text="合并与书签模式配置")
        opt_frm.pack(fill="x", pady=(5, 10))
        
        # 第一行
        ttk.Radiobutton(opt_frm, text="1. 普通无书签", variable=self.merge_mode, value="normal").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        ttk.Radiobutton(opt_frm, text="2. 纯净文件名提取书签 (适配剑桥出版社)", variable=self.merge_mode, value="filename").grid(row=0, column=1, sticky="w", padx=10, pady=5)
        ttk.Checkbutton(opt_frm, text="🛡️ 合并为 PDF/A 归档格式 (需安装 Ghostscript)", variable=self.merge_pdfa).grid(row=0, column=2, sticky="w", padx=(20, 10), pady=5)
        
        # 第二行 (导出 CSV 模板被移到这里对齐，不再撑破窗口宽度)
        ttk.Radiobutton(opt_frm, text="3. 智能字体排版扫描提取 (适合无规律的乱码名)", variable=self.merge_mode, value="smart").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        ttk.Radiobutton(opt_frm, text="4. CSV 高阶层级挂载 (带 Virtual Nodes)", variable=self.merge_mode, value="csv").grid(row=1, column=1, sticky="w", padx=10, pady=5)
        ttk.Button(opt_frm, text="📥 导出 CSV 模板", command=self.export_merge_csv).grid(row=1, column=2, sticky="w", padx=(20, 10), pady=5)

        # 树状列表
        self.merge_tree = ttk.Treeview(parent, columns=("index", "status", "pdf"), show="headings", height=8)
        self.merge_tree.heading("index", text="顺序")
        self.merge_tree.heading("status", text="提取状态")
        self.merge_tree.heading("pdf", text="PDF 文件")
        self.merge_tree.column("index", width=50, anchor="center")
        self.merge_tree.column("status", width=100, anchor="center")
        self.merge_tree.column("pdf", width=600)
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
                    tree.insert("", "end", values=(idx, "等待", f))

    def _pick_files(self, tree, mode):
        ft = [("PDF", "*.pdf"), ("All", "*.*")] if mode != "md" else [("Docs", "*.pdf;*.docx;*.pptx;*.xlsx;*.txt"), ("All", "*.*")]
        paths = filedialog.askopenfilenames(title="选择文件", filetypes=ft)
        for p in paths:
            if mode == "merge":
                idx = len(tree.get_children()) + 1
                tree.insert("", "end", values=(idx, "等待", p))
            else:
                tree.insert("", "end", values=("等待", p, ""))

    def merge_add_folder(self):
        folder = filedialog.askdirectory(title="选择包含 PDF 章节的文件夹")
        if not folder: return
        folder_path = Path(folder)
        pdfs = [p for p in folder_path.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
        
        pdfs.sort(key=natural_pdf_sort_key)
        for p in pdfs:
            idx = len(self.merge_tree.get_children()) + 1
            self.merge_tree.insert("", "end", values=(idx, "等待", str(p)))
        self._log(f"已成功加载文件夹下 {len(pdfs)} 个 PDF，并按前缀数字重新排序。")

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
                elif msg_type == "clear_tree":
                    # 用于在任务完成后自动清空列表的专用信号
                    tree = data
                    tree.delete(*tree.get_children())
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
        
        if idx == 0: 
            target = self.task_md
        elif idx == 1: 
            if self.split_active_mode.get() == "range":
                target = self.task_range
            else:
                target = self.task_toc
        elif idx == 2: 
            target = self.task_merge
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
            
            try:
                result = md.convert(in_path)
                text = result.text_content
                for pat, repl in rules: text = re.sub(pat, repl, text)
                
                if self.md_merge_output.get():
                    merged_content.append(f"\n\n# Source: {Path(in_path).name}\n\n{text}")
                    self.q.put(("tree_update", (self.md_tree, iid, "status", "合并缓存中")))
                else:
                    out_path = out_base / (safe_name(Path(in_path).stem) + ".md")
                    with open(out_path, "w", encoding="utf-8") as f: f.write(text)
                    self.q.put(("tree_update", (self.md_tree, iid, "status", "✅ 成功")))
                    self.q.put(("tree_update", (self.md_tree, iid, "out", str(out_path))))
            except Exception as e:
                self.q.put(("tree_update", (self.md_tree, iid, "status", "❌ 失败")))
                self._log(f"转换失败 {in_path}: {e}", "error")
            self.q.put(("progress", (i, total)))
            
        if self.md_merge_output.get() and merged_content and not self.stop_event.is_set():
            out_path = out_base / "Merged_Output.md"
            with open(out_path, "w", encoding="utf-8") as f: f.write("".join(merged_content))
            self._log(f"合并完成！已输出至: {out_path}", "success")

    # --- 任务：TOC/等分 拆分 ---
    def toc_pick_files(self):
        paths = filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf")])
        for p in paths:
            if p not in self.toc_pdfs: self.toc_pdfs.append(p)
        self._log(f"已添加到缓冲区：{len(self.toc_pdfs)} 个PDF")

    def toc_scan(self):
        if not self.toc_pdfs: return self._log("未选择 PDF 文件。")
        lvl = self.split_level.get()
        self.toc_tree.delete(*self.toc_tree.get_children())
        
        def _scan():
            for pdf in self.toc_pdfs:
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
            
            try:
                export_pdf_range(pdf, str(out_path), int(s_str), int(e_str), watermark=wm)
                self.q.put(("tree_update", (self.toc_tree, iid, "check", "✅")))
            except Exception as e:
                self.q.put(("tree_update", (self.toc_tree, iid, "check", "❌")))
                self._log(f"导出失败: {e}", "error")
            self.q.put(("progress", (i, total)))

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
                    for pt in self.range_custom_val.get().split(','):
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


    # --- 终极核心：学术 PDF 智能合并 ---
    def export_merge_csv(self):
        items = self.merge_tree.get_children()
        if not items:
            return messagebox.showwarning("提示", "列表为空，无法生成模板。请先添加 PDF 文件。")

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", 
            filetypes=[("CSV", "*.csv")], 
            initialfile="bookmarks_template.csv"
        )
        if not save_path: return

        self._set_busy(True)
        def _export():
            try:
                rows = []
                for i, iid in enumerate(items, 1):
                    if not self._check_pause_stop(): return
                    pdf = Path(self.merge_tree.set(iid, "pdf"))
                    self.q.put(("tree_update", (self.merge_tree, iid, "status", "提取标题...")))
                    
                    # 导出模板时默认也采用“纯净文件名提取”以保持最快速度，用户也可手动修改 CSV
                    title = title_from_filename(pdf)
                    rows.append({"type": "pdf", "file": pdf.name, "title": title, "level": 1})
                    
                    self.q.put(("tree_update", (self.merge_tree, iid, "status", "已生成模板项")))
                    self.q.put(("progress", (i, len(items))))

                with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=["type", "file", "title", "level"])
                    writer.writeheader()
                    writer.writerows(rows)
                self.q.put(("done", f"CSV 模板已成功导出至:\n{save_path}\n\n您可以使用 Excel 打开它进行以下操作：\n1. 修改 title 列调整书签名称\n2. 插入新行 (type 写 virtual, file 留空) 来创建父级目录\n3. 修改 level 调整层级关系\n\n修改保存后，选择 [CSV 高阶层级挂载合并] 模式即可执行。"))
            except Exception as e:
                self._log(f"导出 CSV 失败: {e}", "error")
                self.q.put(("done", f"导出失败: {e}"))
        threading.Thread(target=_export, daemon=True).start()

    def task_merge(self):
        items = self.merge_tree.get_children()
        mode = self.merge_mode.get()

        if mode != "csv" and len(items) < 2:
            return self._log("非 CSV 模式合并至少需要在列表内提供 2 个 PDF 文件")

        out_path = Path(self.out_dir.get()) / safe_name(self.merge_name.get())
        ensure_dir(out_path.parent)

        instructions = []
        
        if mode == "normal":
            for iid in items:
                pdf = self.merge_tree.set(iid, "pdf")
                instructions.append({"type": "pdf", "file": pdf, "title": "", "level": 1})

        elif mode == "filename":
            self._log("正在执行：纯净文件名提取模式 (剑桥特供)...")
            for i, iid in enumerate(items, 1):
                if not self._check_pause_stop(): return
                pdf = self.merge_tree.set(iid, "pdf")
                title = title_from_filename(Path(pdf))
                self.q.put(("tree_update", (self.merge_tree, iid, "status", title[:15] + "...")))
                instructions.append({"type": "pdf", "file": pdf, "title": title, "level": 1})
                self.q.put(("progress", (i, len(items))))

        elif mode == "smart":
            self._log("正在执行：智能字体扫描提取模式...")
            for i, iid in enumerate(items, 1):
                if not self._check_pause_stop(): return
                pdf = self.merge_tree.set(iid, "pdf")
                self.q.put(("tree_update", (self.merge_tree, iid, "status", "智能提取中...")))
                title = title_from_pdf(Path(pdf))
                self.q.put(("tree_update", (self.merge_tree, iid, "status", title[:15] + "...")))
                instructions.append({"type": "pdf", "file": pdf, "title": title, "level": 1})
                self.q.put(("progress", (i, len(items))))

        elif mode == "csv":
            csv_path = filedialog.askopenfilename(title="选择您编辑好的 CSV 书签模板", filetypes=[("CSV Files", "*.csv")])
            if not csv_path:
                return self._log("已取消：未选择 CSV 模板文件。")

            self._log("正在解析外部 CSV 模板...")
            
            lookup = {Path(self.merge_tree.set(iid, "pdf")).name: self.merge_tree.set(iid, "pdf") for iid in items}
            csv_dir = Path(csv_path).parent

            try:
                rows = load_rows_from_csv(Path(csv_path))
                for r in rows:
                    if r["type"] == "virtual":
                        instructions.append(r)
                    else:
                        fname = r["file"]
                        if fname in lookup:
                            r["file"] = lookup[fname]
                        else:
                            cand = csv_dir / fname
                            if cand.exists():
                                r["file"] = str(cand)
                            else:
                                raise FileNotFoundError(f"未在列表或同级目录下找到 PDF: {fname}")
                        instructions.append(r)
            except Exception as e:
                return self._log(f"CSV 解析错误中止: {e}", "error")

        self._log(f"开始组合物理文件 ({len([i for i in instructions if i['type']=='pdf'])} 个文档)...")
        try:
            execute_merge_with_instructions(instructions, str(out_path))
            self._log(f"标准 PDF 合并成功！临时输出至: {out_path}", "success")
            
            # 若勾选了 PDF/A，触发二次转换
            if self.merge_pdfa.get():
                self._log("正在执行高阶操作：转为 PDF/A 归档格式，请稍候...")
                temp_path = out_path.with_name(out_path.stem + "_temp.pdf")
                os.rename(out_path, temp_path)
                try:
                    convert_to_pdfa(str(temp_path), str(out_path))
                    os.remove(temp_path)
                    self._log("🌟 PDF/A 归档格式转换成功！", "success")
                except Exception as e:
                    self._log(f"PDF/A 转换失败。已保留标准 PDF。(错误: {e})", "error")
                    if temp_path.exists() and not out_path.exists():
                        os.rename(temp_path, out_path) # 恢复文件
            
            # ---> 核心优化：合并成功后自动清空当前工作区，方便下一次合并
            self.q.put(("clear_tree", self.merge_tree))
                        
        except Exception as e:
            self._log(f"合并过程中发生错误: {e}", "error")

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