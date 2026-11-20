"""报告页：本次运行过的所有分析自动收进来，可以一键导出 HTML 报告 + 图片 + 数据。"""
import base64
import csv
import hashlib
import html
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import numpy as np
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QComboBox, QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from ..theme import C
from .base import BasePage
from .common import log
from .widgets import Card, EmptyState, Section, btn, fig_pixmap, fig_png_bytes, hbox, label


PROCESS_KINDS = {"预处理", "分段", "事件"}


def method_summary(res):
    p = res.params
    if res.kind == "ERP" and {"条件", "通道", "指标时间窗"} <= p.keys():
        return (f"把「{p['条件']}」各条件的试次按分段零点对齐，分别平均 {p['通道']} 的脑电信号。"
                f"用 {p['指标时间窗']} 内的平均振幅描述该时段的反应，单位为 µV。")
    if res.kind == "时频" and {"通道", "基线"} <= p.keys():
        return (f"用 Morlet 小波估计 {p['通道']} 的功率随时间和频率的变化。"
                f"各条件图以 {p['基线'].split('（')[0]} 为基线，正 dB 表示功率高于基线，负 dB 表示低于基线。")
    if res.kind == "RSA" and {"距离", "模型"} <= p.keys():
        return (f"在每个时间窗用 {p['距离']} 距离量化类别间的脑电差异，再与「{p['模型']}」模型比较。"
                "Spearman 相关越接近 1，表示两者对类别差异大小的排序越一致。")
    if res.kind == "解码" and {"方法", "标签"} <= p.keys():
        if p["方法"] == "反向编码模型 IEM":
            return (f"从脑电活动重建「{p['标签']}」的特征值，再与打乱标签后的重建误差比较。"
                    "正的 ΔMAE 表示实际标签的平均绝对误差较小。")
        if p["方法"] == "跨时间泛化":
            return (f"用一个时间窗的脑电信号学习区分「{p['标签']}」的类别，在其他时间窗测试。"
                    "矩阵对角线表示训练和测试在相同时间窗进行，其他位置显示跨时间的正确率。")
        return (f"在每个时间窗使用交叉验证，由脑电信号预测「{p['标签']}」的类别。"
                "正确率表示分对的比例，可与相同类别数下的机会水平比较。")
    return ""


def _result_card(when, res):
    card = Card()
    card.add(label(res.title, "H2", wrap=True))
    card.add(label(when.strftime("%H:%M:%S") + "　" + str(res.params.get("记录") or res.params.get("范围", "")),
                   "Sub", wrap=True))
    summary = method_summary(res)
    if summary:
        card.add(label(summary, wrap=True))
    if res.metrics:
        card.add(label("　·　".join(f"{k} {v}" for k, v, _ in res.metrics), "H2", wrap=True))
    for note in res.notes:
        card.add(label(note, "Sub", wrap=True))
    if res.params or res.figures:
        details = Section("参数与图表", expanded=False)
        if res.params:
            details.widget(label("　·　".join(f"{k} {v}" for k, v in res.params.items()), "Sub", wrap=True))
        for k, _, hint in res.metrics:
            if hint:
                details.widget(label(f"{k}　{hint}", "Sub", wrap=True))
        if res.figures:
            picker = details.widget(QComboBox())
            picker.addItems([title for title, _ in res.figures])
            preview = details.widget(QLabel())
            preview.setAlignment(Qt.AlignCenter)

            def display(index):
                if not details.toggle.isChecked():
                    preview.clear()
                    return
                image = fig_pixmap(res.figures[index][1], 940)
                preview.setPixmap(image.scaled(720, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))

            def opened(on):
                if on and not preview.pixmap():
                    display(picker.currentIndex())

            picker.currentIndexChanged.connect(display)
            details.toggle.toggled.connect(opened)
        card.add(details)
    return card


class ReportPage(BasePage):
    title = "分析报告"
    subtitle = "未导出的结果保留到关闭软件。导出后可在这里重新打开 HTML 报告。"

    def __init__(self, ctx):
        super().__init__(ctx, with_side=False)
        self.displayed_items = []
        self.record_name = None
        self.workflow_project = None
        self.project_results = btn("项目全部结果", lambda: self.show_items(self.ctx.reports), "ghost")
        self.project_results.hide()
        self.last_export = btn("打开上次导出", self.open_last_export, "ghost", "mdi6.file-find-outline")
        self.last_export.hide()
        self.clear_button = btn("清空", self.clear, "ghost", "mdi6.broom")
        bar = hbox(btn("导出报告", self.export, "primary", "mdi6.file-export-outline"),
                   self.last_export, self.project_results, self.clear_button, "stretch")
        self.body.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.empty = EmptyState("mdi6.file-chart-outline", "当前会话还没有分析结果",
                                "到 ERP、时频、解码或 RSA 页运行一次分析，结果会自动出现在这里。")
        self.scroll.setWidget(self.empty)
        self.body.addWidget(self.scroll, 1)

    def on_show(self):
        super().on_show()
        self.refresh_last_export()
        if self.record_name is not None and self.project is self.workflow_project:
            self.show_items(self.displayed_items, self.record_name)
        else:
            self.show_items(self.ctx.reports)

    def show_items(self, items, record_name=None):
        self.displayed_items = list(items)
        self.record_name = record_name
        self.workflow_project = self.project if record_name is not None else None
        self.project_results.setVisible(record_name is not None)
        self.clear_button.setVisible(record_name is None)
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
        if record_name:
            lay.addWidget(label(record_name, "H2", wrap=True))
        stages = []
        for when, res in reversed(self.displayed_items):
            if res.kind in PROCESS_KINDS:
                stages.append((when, res))
            else:
                lay.addWidget(_result_card(when, res))
        if stages:
            history = Section(f"处理记录（{len(stages)} 步）", expanded=False)
            for when, res in reversed(stages):
                history.widget(_result_card(when, res))
            lay.addWidget(history)
        lay.addStretch(1)
        self.scroll.setWidget(w)

    def clear(self):
        if self.ctx.reports and QMessageBox.question(self, "清空", "清空报告里的全部结果？") == QMessageBox.Yes:
            self.ctx.reports.clear()
            self.on_show()

    @staticmethod
    def _export_key(project):
        folder = os.path.normcase(os.path.realpath(project.folder))
        return "report_exports/" + hashlib.sha256(os.fsencode(folder)).hexdigest()

    def remember_export(self, project, path):
        if project is None:
            return
        self.ctx.settings.setValue(self._export_key(project), str(Path(path).resolve()))
        self.ctx.settings.sync()
        if self.project is project:
            self.refresh_last_export()

    def refresh_last_export(self):
        path = None
        if self.project:
            saved = self.ctx.settings.value(self._export_key(self.project), "")
            if saved and Path(saved).is_file():
                path = Path(saved)
            else:
                # Also recover reports made in the project's standard export folder by an older version.
                directory = Path(self.project.folder) / "报告"
                path = max((p for p in directory.glob("*/报告.html") if p.is_file()),
                           key=lambda p: p.parent.name, default=None)
        self.last_export.setVisible(path is not None)
        self.last_export.setToolTip(str(path) if path else "")
        return path

    def open_last_export(self):
        path = self.refresh_last_export()
        if path is None:
            QMessageBox.information(self, "打开报告", "找不到该项目上次导出的报告文件。")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.information(self, "打开报告", f"请用浏览器打开这个文件：\n{path}")

    def export(self):
        items = list(self.displayed_items)
        if not items:
            QMessageBox.information(self, "报告", "还没有分析结果。")
            return
        start = self.project.folder if self.project else os.path.expanduser("~/Desktop")
        folder = QFileDialog.getExistingDirectory(self, "选择导出位置", start)
        if not folder:
            return
        project = self.project
        name = self.record_name or (project.name if project else "脑电分析")
        def done(path):
            self.remember_export(project, path)
            self._done(path)
        self.run("导出报告", lambda: export_new_report(folder, name, items, prefix="报告_"), done)

    def _done(self, path):
        log(f"报告已导出：{path}")
        r = QMessageBox.question(self, "导出完成", f"已导出到：\n{os.path.dirname(path)}\n\n现在打开报告吗？")
        if r == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def export_new_report(parent, project_name, items, prefix=""):
    parent = Path(parent)
    with TemporaryDirectory(prefix=".biap-", dir=parent) as staging:
        folder = Path(staging) / "report"
        html_file = Path(export_report(folder, project_name, items))
        if not html_file.is_file() or html_file.stat().st_size == 0:
            raise ValueError("报告文件未生成。")
        destination = parent / f"{prefix}{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
        folder.rename(destination)
        return str(destination / html_file.name)


def export_report(out, project_name, items):
    os.makedirs(os.path.join(out, "图"), exist_ok=True)
    os.makedirs(os.path.join(out, "数据"), exist_ok=True)
    parts, stages = [], []
    for n, (when, res) in enumerate(items, 1):
        sec = [f"<section><h2>{html.escape(res.title)}</h2>",
               f"<p class='meta'>{when:%Y-%m-%d %H:%M:%S}</p>"]
        summary = method_summary(res)
        if summary:
            sec.append(f"<p>{html.escape(summary)}</p>")
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
            with open(os.path.join(out, "数据", f"{n:02d}_{_safe(res.title)}.csv"), "w",
                      encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(head)
                writer.writerows(rows)
        if res.arrays:
            np.savez(os.path.join(out, "数据", f"{n:02d}_{_safe(res.title)}.npz"),
                     **{k: np.asarray(v) for k, v in res.arrays.items()})
        sec.append("</section>")
        (stages if res.kind in PROCESS_KINDS else parts).append("\n".join(sec))
    counts = f"{len(parts)} 项分析"
    if stages:
        counts += f" · {len(stages)} 个处理步骤"
        parts.append(f"<details><summary>处理记录（{len(stages)} 步）</summary>{''.join(stages)}</details>")
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(project_name)} 分析报告</title>
<style>
body{{margin:0;background:{C['bg']};color:{C['text']};font-family:"Microsoft YaHei UI","PingFang SC","Hiragino Sans GB",sans-serif;}}
main{{max-width:1080px;margin:0 auto;padding:32px 20px 60px}}
h1{{font-size:32px;font-weight:550;margin:0 0 8px;color:{C['on_soft']}}} .sub{{color:{C['text2']};margin:0 0 28px}}
section{{border:1px solid {C['line']};background:{C['card']};border-radius:18px;padding:20px 24px;margin:0 0 20px}}
h2{{font-size:18px;margin:0}} .meta{{color:{C['muted']};font-size:12px;margin:4px 0 12px}}
.chips span{{display:inline-block;background:{C['field']};color:{C['text2']};border-radius:10px;padding:3px 10px;margin:0 6px 6px 0;font-size:12px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:12px 0}}
.m{{background:{C['field']};border-radius:12px;padding:10px 14px}} .k{{color:{C['text2']};font-size:12px}}
.v{{font-size:19px;font-weight:600;margin:2px 0}} .h{{color:{C['muted']};font-size:11px}}
.note{{color:{C['warn']};font-size:13px}}
@media print{{body{{background:white;color:black}} section,figure{{break-inside:avoid}}}}
figure{{margin:14px 0}} img{{max-width:100%;border-radius:10px}} figcaption{{color:{C['text2']};font-size:12px;margin-top:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}}
th,td{{border-bottom:1px solid {C['line']};padding:6px 8px;text-align:left}} th{{color:{C['text2']}}}
</style></head><body><main>
<h1>{html.escape(project_name)} · 分析报告</h1>
<p class="sub">生成时间 {datetime.now():%Y-%m-%d %H:%M} · {counts} · 图片在“图”文件夹，数值在“数据”文件夹</p>
{''.join(parts)}
</main></body></html>"""
    path = os.path.join(out, "报告.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path


def _safe(s):
    import re
    return re.sub(r"[\\/:*?\"<>|\s·]+", "_", s)[:60]
