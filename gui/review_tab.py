from gpp3323.i18n import tr
import csv
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk

from gui.curve_panel import CurvePanel
from gui.load_data import read_samples, rename_dataset


class ReviewTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.root_path = Path(__file__).resolve().parents[1] / 'data'
        self.entries = {}
        self.cache = {}
        top = ttk.Frame(self)
        top.pack(fill='x')
        ttk.Button(top, text=tr('選擇資料根目錄'), command=self.choose_root).pack(side='left')
        ttk.Button(top, text=tr('重新整理'), command=self.refresh).pack(side='left', padx=5)
        self.path_var = tk.StringVar(value=str(self.root_path))
        ttk.Label(top, textvariable=self.path_var).pack(side='left', fill='x')
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True, pady=(8, 0))
        sidebar = ttk.Frame(panes, width=280)
        panes.add(sidebar, weight=0)
        ttk.Label(sidebar, text=tr('Ctrl / Shift 多選測試資料夾或舊 CSV')).pack(anchor='w')
        self.tree = ttk.Treeview(sidebar, show='tree', selectmode='extended')
        self.tree.column('#0', width=300)
        scroll = ttk.Scrollbar(sidebar, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.load_selection)
        self.rename_button = ttk.Button(sidebar, text=tr('重新命名選取的資料夾'), command=self.rename, state='disabled')
        self.rename_button.pack(fill='x', pady=5)
        self.status = tk.StringVar()
        ttk.Label(sidebar, textvariable=self.status, wraplength=290).pack(fill='x')
        self.plot = CurvePanel(panes, choose_power_source=True)
        panes.add(self.plot, weight=1)
        self.refresh()

    def choose_root(self):
        path = filedialog.askdirectory(parent=self, initialdir=str(self.root_path))
        if path:
            self.root_path = Path(path)
            self.path_var.set(path)
            self.refresh()

    def refresh(self, select_path=None):
        previous = {self.entries[i] for i in self.tree.selection() if i in self.entries}
        if select_path:
            previous = {select_path}
        self.tree.delete(*self.tree.get_children())
        self.entries.clear()
        self.cache.clear()
        try:
            files = sorted(self.root_path.rglob('*.csv')) if self.root_path.exists() else []
            paths = sorted({p.parent if p.parent != self.root_path else p for p in files})
            for index, path in enumerate(paths):
                iid = str(index)
                self.entries[iid] = path
                self.tree.insert('', 'end', iid=iid, text=str(path.relative_to(self.root_path)))
                if path in previous:
                    self.tree.selection_add(iid)
        except OSError as exc:
            messagebox.showerror(tr('讀取目錄失敗'), str(exc), parent=self)
        self.load_selection()

    def load_selection(self, _event=None):
        selected = [self.entries[i] for i in self.tree.selection() if i in self.entries]
        self.rename_button.configure(state='normal' if len(selected) == 1 and selected[0].is_dir() else 'disabled')
        series, errors = [], []
        for path in selected:
            files = sorted(path.glob('*.csv')) if path.is_dir() else [path]
            for file in files:
                try:
                    stamp = (file.stat().st_mtime_ns, file.stat().st_size)
                    if file not in self.cache or self.cache[file][0] != stamp:
                        self.cache[file] = (stamp, read_samples(file))
                    samples = self.cache[file][1]
                    label = str(path.relative_to(self.root_path))
                    if len(files) > 1:
                        label += '/' + file.stem
                    for channel in sorted({s.channel for s in samples}):
                        series.append((f'{label} / CH{channel}', [s for s in samples if s.channel == channel]))
                except (OSError, ValueError, UnicodeError, csv.Error) as exc:
                    errors.append(f'{file.name}：{exc}')
        self.plot.set_series(series, align_zero=True)
        self.status.set(tr('已選 {0} 組，顯示 {1} 條測試曲線。', f'{len(selected)}', f'{len(series)}') +
                        (tr('\n無法讀取：\n') + '\n'.join(errors) if errors else ''))

    def rename(self):
        selected = self.tree.selection()
        if len(selected) != 1:
            return
        path = self.entries[selected[0]]
        if not path.is_dir():
            return
        name = simpledialog.askstring(tr('重新命名資料夾'), tr('修改實際資料夾名稱：'), initialvalue=path.name, parent=self)
        if name is None:
            return
        try:
            target = rename_dataset(path, name)
        except (OSError, ValueError) as exc:
            messagebox.showerror(tr('重新命名失敗'), str(exc), parent=self)
        else:
            self.refresh(select_path=target)
