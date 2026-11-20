"""Review continuous EEG and mark intervals in seconds from the start of acquisition."""
from math import ceil

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHeaderView,
                               QInputDialog, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import quality
from .channel_selection import ChannelPicker
from .common import fspin
from .widgets import btn, hbox, label


class SegmentWaveform(QWidget):
    selected = Signal(float, float)

    def __init__(self, raw, parent=None):
        super().__init__(parent)
        self.raw = raw
        self.sf = float(raw.info["sfreq"])
        self.duration = int(raw.n_times) / self.sf
        self.start, self.end = 0., self.duration
        self.names = []
        self.samples = np.empty((0, 0))
        self.lines = None
        self.amplitude = 100.
        self.rows = []
        self.drag = None
        self.setMinimumSize(250, 110)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName("原始脑电波形，横向拖动标记坏段")

    def show_page(self, start, span, names, amplitude=None):
        left = min(self.raw.n_times - 1, max(0, round(start * self.sf)))
        right = min(self.raw.n_times, left + max(1, round(span * self.sf)))
        self.start, self.end = left / self.sf, right / self.sf
        self.names = names
        self.samples = self.raw.get_data(picks=names, start=left, stop=right) * 1e6 if names else np.empty((0,0))
        self.amplitude = amplitude
        self.centers = []
        largest = 0.
        for row in self.samples:
            finite = row[np.isfinite(row)]
            center = float(np.median(finite)) if len(finite) else 0.
            self.centers.append(center)
            if len(finite):
                largest = max(largest, float(np.max(np.abs(finite-center))))
        if amplitude is None:
            self.amplitude = max(1., largest)
        self.lines = None
        self.drag = None
        self.update()

    def plot_rect(self):
        label_w = max((self.fontMetrics().horizontalAdvance(ch) for ch in self.names), default=35) + 12
        left = min(self.width() // 4, max(48, label_w))
        return QRectF(left, 8, max(1, self.width() - left - 15), max(1, self.height() - 38))

    def time_at(self, x):
        box = self.plot_rect()
        frac = max(0., min(1., (x - box.left()) / box.width()))
        return round((self.start + frac * (self.end-self.start)) * self.sf) / self.sf

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.plot_rect().contains(event.position()):
            value = self.time_at(event.position().x())
            self.drag = value, value
            self.update()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag:
            self.drag = self.drag[0], self.time_at(event.position().x())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag:
            lo, hi = sorted((self.drag[0], self.time_at(event.position().x())))
            self.drag = None
            if hi > lo:
                self.selected.emit(lo, hi)
            self.update()

    def resizeEvent(self, event):
        self.lines = None
        super().resizeEvent(event)

    def wave_lines(self, box):
        """Cache one envelope per channel so dragging an interval only repaints it."""
        if self.lines is not None:
            return self.lines
        lines = []
        size = self.samples.shape[1]
        band = box.height()/len(self.names)
        chunk = max(1, ceil(size / max(1, box.width())))
        for i, row in enumerate(self.samples):
            center = box.top() + (i+.5)*band
            scaled = np.clip((row-self.centers[i])/self.amplitude, -1., 1.) * (.44*band)
            line = QPolygonF()
            for left in range(0, size, chunk):
                group = scaled[left:left+chunk]
                if not np.all(np.isfinite(group)):
                    if not line.isEmpty():
                        lines.append(line)
                        line = QPolygonF()
                    continue
                # Keep both extrema when several samples share the same screen pixel.
                indexes = (0,) if chunk == 1 else sorted({int(np.argmin(group)), int(np.argmax(group))})
                for j in indexes:
                    line.append(QPointF(box.left() + (left+j)*box.width()/size, center-group[j]))
            if not line.isEmpty():
                lines.append(line)
        self.lines = lines
        return lines

    def paintEvent(self, _):
        painter = QPainter(self)
        palette = self.palette()
        box = self.plot_rect()
        painter.fillRect(self.rect(), palette.base())
        def xpos(t):
            return box.left() + (t-self.start) * box.width() / (self.end-self.start)
        for i in range(6):
            x = box.left() + i * box.width()/5
            t = self.start + i * (self.end-self.start)/5
            painter.setPen(palette.mid().color())
            painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            painter.setPen(palette.text().color())
            caption = f"{t:.2f}".rstrip("0").rstrip(".") + " s"
            text_w = painter.fontMetrics().horizontalAdvance(caption)
            painter.drawText(QPointF(min(self.width()-text_w-2, x-text_w/2), box.bottom()+18), caption)
        painter.save()
        painter.setClipRect(box)
        for start, end, _, checked in self.rows:
            if end > self.start and start < self.end:
                color = QColor(210, 82, 62, 62) if checked else QColor(209, 156, 32, 44)
                painter.fillRect(QRectF(xpos(start), box.top(), xpos(end)-xpos(start), box.height()), color)
        if self.drag:
            a, b = sorted(self.drag)
            painter.fillRect(QRectF(xpos(a), box.top(), xpos(b)-xpos(a), box.height()), QColor(83, 136, 208, 92))
        painter.restore()
        if self.samples.size:
            band = box.height()/len(self.names)
            for i, name in enumerate(self.names):
                center = box.top() + (i+.5)*band
                painter.setPen(palette.text().color())
                painter.drawText(QRectF(0, center-11, box.left()-5, 22), Qt.AlignRight | Qt.AlignVCenter, name)
            painter.setPen(QPen(palette.text().color(), 1))
            for line in self.wave_lines(box):
                painter.drawPolyline(line)
        if not np.isfinite(self.samples).any():
            painter.setPen(palette.text().color())
            painter.drawText(box, Qt.AlignCenter, "请选择要显示的通道" if not self.names else "这段波形缺少有效采样")


class SegmentEditor(QDialog):
    def __init__(self, page, raw, record, candidates, threshold, save, parser):
        super().__init__(page)
        self.setWindowTitle("标记坏段")
        available = page.screen().availableGeometry()
        self.resize(min(840, max(350, available.width()-32)), min(660, max(300, available.height()-65)))
        self.save_rows = save
        self.parse_rows = parser
        self.runner = page.ctx.runner
        self.raw = raw
        self.threshold = threshold
        self.scanning = False
        self.rows = [(a, b, why, True) for a, b, why in record.bad_segments]
        self.undo_stack = []
        self.add_candidates(candidates)
        self.wave = SegmentWaveform(raw, self)
        layout = QVBoxLayout(self)
        layout.addWidget(label("标记坏段", "H2"))
        layout.addWidget(label(record.id, "Sub", wrap=True))
        layout.addWidget(label("原始记录，从记录开头计时。横向拖选即可标记，保存后，分段会跳过与坏段重叠的试次。", "Sub", wrap=True))
        self.position = fspin(0, 0, self.wave.duration, 1, 3, " s")
        self.position.setAccessibleName("波形起点")
        self.span = QComboBox()
        self.span.setAccessibleName("每屏时长")
        for seconds in (2, 5, 10, 30, 60):
            self.span.addItem(f"{seconds} 秒/屏", seconds)
        self.span.setCurrentIndex(2)
        self.channels = QComboBox()
        self.channels.setAccessibleName("显示通道组")
        self.names = [ch for ch, kind in zip(raw.ch_names, raw.get_channel_types()) if kind == "eeg"]
        if not self.names:
            raise ValueError("当前记录没有可显示的 EEG 通道。")
        self.set_channels(self.names)
        self.scale = QComboBox()
        self.scale.setAccessibleName("共享纵向量程")
        self.scale.addItem("自动量程", None)
        for amp in (25, 50, 100, 200, 500):
            self.scale.addItem(f"±{amp} µV", amp)
        self.prev = btn("上一屏", lambda: self.shift(-1))
        self.next = btn("下一屏", lambda: self.shift(1))
        layout.addLayout(hbox(self.prev, self.next, label("起点"), self.position, self.span, "stretch"))
        layout.addLayout(hbox(btn("显示通道…", self.choose_channels), self.channels, self.scale, "stretch"))
        layout.addWidget(self.wave, 3)
        self.legend = label("", "Muted", wrap=True)
        layout.addWidget(self.legend)
        self.table = QTableWidget(0, 4)
        self.table.setAccessibleName("坏段清单")
        self.table.setMinimumHeight(60)
        self.table.setHorizontalHeaderLabels(["采用", "开始（秒）", "结束（秒）", "标记原因"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.detect = btn("自动检测候选", self.scan)
        self.delete_btn = btn("删除选中", self.delete_rows)
        self.undo_btn = btn("撤销", self.undo)
        layout.addLayout(hbox(self.detect, self.delete_btn, self.undo_btn,
                              btn("编辑区间文本…", self.text_edit), "stretch"))
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.addButton("保存坏段", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.wave.selected.connect(self.add_manual)
        self.position.valueChanged.connect(self.draw_page)
        self.span.currentIndexChanged.connect(self.draw_page)
        self.channels.currentIndexChanged.connect(self.draw_page)
        self.scale.currentIndexChanged.connect(self.draw_page)
        self.table.itemChanged.connect(self.toggle_row)
        self.table.itemSelectionChanged.connect(self.enable_delete)
        self.table.cellDoubleClicked.connect(lambda row, _: self.position.setValue(self.rows[row][0]))
        self.refresh()
        self.draw_page()

    def draw_page(self, *_):
        span = self.span.currentData()
        last = max(0., self.wave.duration - span)
        with QSignalBlocker(self.position):
            self.position.setMaximum(last)
        start = self.position.value()
        self.prev.setEnabled(start > 0)
        self.next.setEnabled(start < last-.0005)
        self.wave.show_page(start, span, self.channels.currentData() or [], self.scale.currentData())
        self.legend.setText(f"共享量程 ±{self.wave.amplitude:.3g} µV（各通道以本屏中位数居中） · 红色已选，黄色待复核")

    def set_channels(self, names):
        self.visible_names = list(names)
        with QSignalBlocker(self.channels):
            self.channels.clear()
            for i in range(0, len(names), 8):
                group = names[i:i+8]
                self.channels.addItem(group[0] + (" — " + group[-1] if len(group)>1 else ""), group)

    def choose_channels(self):
        picker = ChannelPicker(self, "选择波形显示通道", "勾选要一起查看的电极。每屏最多显示 8 根。",
                               self.names, ["eeg"]*len(self.names), self.visible_names)
        if picker.exec():
            self.set_channels(picker.selected_names())
            self.draw_page()

    def shift(self, direction):
        self.position.setValue(self.position.value() + direction*self.span.currentData())

    def refresh(self):
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(self.rows))
            for index, (a, b, why, keep) in enumerate(self.rows):
                flag = QTableWidgetItem()
                flag.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                flag.setCheckState(Qt.Checked if keep else Qt.Unchecked)
                self.table.setItem(index, 0, flag)
                for col, value in enumerate((f"{a:.6f}".rstrip("0").rstrip("."),
                                               f"{b:.6f}".rstrip("0").rstrip("."), why), 1):
                    self.table.setItem(index, col, QTableWidgetItem(value))
        self.wave.rows = self.rows
        self.wave.update()
        self.undo_btn.setEnabled(bool(self.undo_stack))
        self.enable_delete()

    def enable_delete(self):
        self.delete_btn.setEnabled(bool(self.table.selectionModel().selectedRows()))

    def change(self, rows):
        if rows != self.rows:
            self.undo_stack.append(self.rows)
            self.rows = rows
            self.refresh()

    def add_manual(self, start, end):
        row = (start, end, "手动", True)
        if row not in self.rows:
            self.change(sorted([*self.rows, row]))
        self.table.selectRow(self.rows.index(row))

    def add_candidates(self, candidates):
        known = {(a, b) for a, b, _, _ in self.rows}
        fresh = [(a, b, why, False) for a, b, why in candidates if (a, b) not in known]
        self.rows = sorted([*self.rows, *fresh]) if fresh else self.rows
        return len(fresh)

    def scan(self):
        if self.scanning or self.runner.busy:
            return
        self.scanning = True
        self.detect.setEnabled(False)
        self.detect.setText("正在检测…")
        def finished(rows):
            before = self.rows
            count = self.add_candidates(rows)
            if count:
                self.undo_stack.append(before)
            self.scanning = False
            self.detect.setEnabled(True)
            self.detect.setText("自动检测候选")
            self.legend.setText(f"本次新增 {count} 个候选。勾选要采用的区间，双击起点可定位波形。")
            self.refresh()
        def failed(_):
            self.scanning = False
            self.detect.setEnabled(True)
            self.detect.setText("自动检测候选")
        self.runner.run("检测原始波形的坏段候选", lambda: quality.inspect_raw(self.raw, self.threshold)[0], finished, failed)

    def delete_rows(self):
        indexes = {i.row() for i in self.table.selectionModel().selectedRows()}
        self.change([row for i, row in enumerate(self.rows) if i not in indexes])

    def toggle_row(self, item):
        if item.column() == 0:
            i = item.row()
            rows = self.rows.copy()
            rows[i] = (*rows[i][:3], item.checkState() == Qt.Checked)
            self.change(rows)

    def undo(self):
        if self.undo_stack:
            self.rows = self.undo_stack.pop()
            self.refresh()

    def text_edit(self):
        from PySide6.QtWidgets import QMessageBox
        initial = "\n".join(f"{a:.12g}\t{b:.12g}\t{why}" for a, b, why, keep in self.rows if keep)
        while True:
            initial, ok = QInputDialog.getMultiLineText(self, "编辑坏段区间",
                "每行一个区间，例如「2 秒到 3.5 秒，眨眼」，也可以粘贴开始、结束和原因三列。", initial)
            if not ok:
                return
            try:
                values = self.parse_rows(initial, self.wave.duration, self.wave.sf)
            except ValueError as exc:
                QMessageBox.warning(self, "区间格式", str(exc))
                continue
            confirmed = [(*r, True) for r in values]
            bounds = {(a,b) for a,b,_,_ in confirmed}
            self.change(sorted(confirmed + [r for r in self.rows if not r[3] and r[:2] not in bounds]))
            return

    def accept(self):
        if not self.scanning and self.save_rows([list(r[:3]) for r in self.rows if r[3]]):
            super().accept()

    def reject(self):
        if not self.scanning:
            super().reject()
