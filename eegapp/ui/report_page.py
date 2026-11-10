"""报告页：本次运行过的所有分析自动收进来，可以一键导出 HTML 报告 + 图片 + 数据。"""
import base64
import html
import os
from datetime import datetime

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from ..theme import C
from .base import BasePage
from .common import log
from .widgets import Card, EmptyState, btn, chip, fig_pixmap, fig_png_bytes, hbox, label


class ReportPage(BasePage):
    title = "报告"
    subtitle = "每次运行的分析都会自动收在这里，导出后可直接放进组会汇报或论文附录"

    def __init__(self, ctx):
        super().__init__(ctx, with_side=False)
        bar = hbox(btn("导出报告", self.export, "primary", "mdi6.file-export-outline"),
                   btn("清空", self.clear, "ghost", "mdi6.broom"), "stretch")
        self.body.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.empty = EmptyState("mdi6.file-chart-outline", "报告还是空的",
                                "到 ERP、时频、解码或 RSA 页运行一次分析，结果会自动出现在这里。")
        self.scroll.setWidget(self.empty)
        self.body.addWidget(self.scroll, 1)

    def on_show(self):
        super().on_show()
        items = self.ctx.reports
        old = self.scroll.takeWidget()
        if old is not None and old is not self.empty:
            old.deleteLater()
        if not items:
            self.scroll.setWidget(self.empty)
            return
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 8, 8)
        lay.setSpacing(12)
        for when, res in reversed(items):
            card = Card(res.title, when.strftime("%H:%M:%S") + "　·　" + "　".join(
                f"{k}：{v}" for k, v in list(res.params.items())[:4]))
            if res.metrics:
                row = QHBoxLayout()
                row.setSpacing(6)
                for m in res.metrics[:5]:
                    row.addWidget(chip(f"{m[0]}：{m[1]}", C["on_soft"], C["soft"]))
                row.addStretch(1)
                card.add(row)
            thumbs = QHBoxLayout()
            thumbs.setSpacing(10)
            for t, f in res.figures[:4]:
                lab = QLabel()
                lab.setPixmap(fig_pixmap(f, 260))
                lab.setToolTip(t)
                thumbs.addWidget(lab)
            thumbs.addStretch(1)
            card.add(thumbs)
            lay.addWidget(card)
        lay.addStretch(1)
        self.scroll.setWidget(w)

    def clear(self):
        if self.ctx.reports and QMessageBox.question(self, "清空", "清空报告里的全部结果？") == QMessageBox.Yes:
            self.ctx.reports.clear()
            self.on_show()

    def export(self):
        items = self.ctx.reports
        if not items:
            QMessageBox.information(self, "报告", "还没有分析结果。")
            return
        start = self.project.folder if self.project else os.path.expanduser("~/Desktop")
        folder = QFileDialog.getExistingDirectory(self, "选择导出位置", start)
        if not folder:
            return
        out = os.path.join(folder, f"报告_{datetime.now():%Y%m%d_%H%M%S}")
        name = self.project.name if self.project else "脑电分析"
        self.run("导出报告", lambda: export_report(out, name, items), lambda p: self._done(p))

    def _done(self, path):
        log(f"报告已导出：{path}")
        r = QMessageBox.question(self, "导出完成", f"已导出到：\n{os.path.dirname(path)}\n\n现在打开报告吗？")
        if r == QMessageBox.Yes:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def export_report(out, project_name, items):
    os.makedirs(os.path.join(out, "图"), exist_ok=True)
    os.makedirs(os.path.join(out, "数据"), exist_ok=True)
    parts = []
    for n, (when, res) in enumerate(items, 1):
        sec = [f"<section><h2>{n}. {html.escape(res.title)}</h2>",
               f"<p class='meta'>{when:%Y-%m-%d %H:%M:%S}</p>"]
        if res.params:
            sec.append("<div class='chips'>" + "".join(
                f"<span>{html.escape(str(k))}：{html.escape(str(v))}</span>" for k, v in res.params.items()) + "</div>")
        if res.metrics:
            sec.append("<div class='metrics'>" + "".join(
                f"<div class='m'><div class='k'>{html.escape(a)}</div><div class='v'>{html.escape(b)}</div>"
                f"<div class='h'>{html.escape(c)}</div></div>" for a, b, c in res.metrics) + "</div>")
        for note in res.notes:
            sec.append(f"<p class='note'>{html.escape(note)}</p>")
        for j, (t, f) in enumerate(res.figures, 1):
            png = fig_png_bytes(f, 130)
            fn = f"{n:02d}_{j}_{_safe(t)}.png"
            with open(os.path.join(out, "图", fn), "wb") as fh:
                fh.write(png)
            sec.append(f"<figure><img src='data:image/png;base64,{base64.b64encode(png).decode()}'>"
                       f"<figcaption>{html.escape(t)}</figcaption></figure>")
        if res.table:
            head, rows = res.table
            sec.append("<table><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in head) + "</tr>" + "".join(
                "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in r) + "</tr>" for r in rows) + "</table>")
            with open(os.path.join(out, "数据", f"{n:02d}_{_safe(res.title)}.csv"), "w", encoding="utf-8-sig") as fh:
                fh.write(",".join(head) + "\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n")
        if res.arrays:
            np.savez(os.path.join(out, "数据", f"{n:02d}_{_safe(res.title)}.npz"),
                     **{k: np.asarray(v) for k, v in res.arrays.items()})
        sec.append("</section>")
        parts.append("\n".join(sec))
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(project_name)} 分析报告</title>
<style>
body{{margin:0;background:{C['bg']};color:{C['text']};font-family:"Microsoft YaHei UI","PingFang SC","Hiragino Sans GB",sans-serif;}}
main{{max-width:1080px;margin:0 auto;padding:32px 20px 60px}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:{C['text2']};margin:0 0 28px}}
section{{background:{C['card']};border-radius:16px;padding:20px 24px;margin:0 0 20px}}
h2{{font-size:18px;margin:0}} .meta{{color:{C['muted']};font-size:12px;margin:4px 0 12px}}
.chips span{{display:inline-block;background:{C['field']};color:{C['text2']};border-radius:10px;padding:3px 10px;margin:0 6px 6px 0;font-size:12px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:12px 0}}
.m{{background:{C['field']};border-radius:12px;padding:10px 14px}} .k{{color:{C['text2']};font-size:12px}}
.v{{font-size:19px;font-weight:600;margin:2px 0}} .h{{color:{C['muted']};font-size:11px}}
.note{{color:{C['warn']};font-size:13px}}
figure{{margin:14px 0}} img{{max-width:100%;border-radius:10px}} figcaption{{color:{C['text2']};font-size:12px;margin-top:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}}
th,td{{border-bottom:1px solid {C['line']};padding:6px 8px;text-align:left}} th{{color:{C['text2']}}}
</style></head><body><main>
<h1>{html.escape(project_name)} · 分析报告</h1>
<p class="sub">生成时间 {datetime.now():%Y-%m-%d %H:%M} · 共 {len(items)} 项分析 · 图片在“图”文件夹，数值在“数据”文件夹</p>
{''.join(parts)}
</main></body></html>"""
    path = os.path.join(out, "报告.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path


def _safe(s):
    import re
    return re.sub(r"[\\/:*?\"<>|\s·]+", "_", s)[:60]
