"""深色主题下的界面组件。"""
import io

import qtawesome as qta
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..theme import C


def icon(name, color=None):
    return qta.icon(name, color=color or C["text2"])


def btn(text, slot=None, kind=None, icon_name=None, tip=None):
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon_name:
        col = {"primary": C["mint_dark"], "accent": C["accent_text"]}.get(kind, C["text2"])
        b.setIcon(icon(icon_name, col))
        b.setIconSize(QSize(16, 16))
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text, obj=None, wrap=False, rich=False):
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.RichText if rich else Qt.PlainText)
    return lab


def hbox(*ws, spacing=8, margins=(0, 0, 0, 0)):
    h = QHBoxLayout()
    h.setSpacing(spacing)
    h.setContentsMargins(*margins)
    for w in ws:
        if w == "stretch":
            h.addStretch(1)
        elif isinstance(w, int):
            h.addSpacing(w)
        else:
            h.addWidget(w)
    return h


def chip(text, fg=C["accent_text"], bg=C["accent_soft"], icon_name=None):
    lab = QLabel(text)
    lab.setTextFormat(Qt.PlainText)
    lab.setStyleSheet(f"background:{bg};color:{fg};border-radius:10px;padding:3px 10px;font-size:12px;")
    return lab


class Card(QFrame):
    def __init__(self, title=None, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)
        if title:
            self.title = label(title, "H2")
            self.head = hbox(self.title, "stretch")
            self.lay.addLayout(self.head)
        if subtitle:
            self.lay.addWidget(label(subtitle, "Sub", wrap=True))

    def add(self, w):
        if isinstance(w, QWidget):
            self.lay.addWidget(w)
        else:
            self.lay.addLayout(w)
        return w


class Metric(QFrame):
    def __init__(self, name, value, hint=""):
        super().__init__()
        self.setObjectName("Metric")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lay.addWidget(label(name, "Sub"))
        v = label(value, "MetricValue", wrap=True)
        lay.addWidget(v)
        if hint:
            lay.addWidget(label(hint, "Muted", wrap=True))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)


class MetricGrid(QWidget):
    def __init__(self, cols=3):
        super().__init__()
        self.cols = cols
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)

    def set(self, metrics):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, m in enumerate(metrics):
            self.grid.addWidget(Metric(*m), i // self.cols, i % self.cols)
        self.setVisible(bool(metrics))


class Section(QWidget):
    """右侧设置面板里可折叠的一组参数。"""

    def __init__(self, title, expanded=True):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setStyleSheet(f"QToolButton{{border:none;background:transparent;color:#AFA9EC;"
                                  f"font-weight:600;font-size:12px;padding:4px 0;}}")
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.toggled.connect(self._toggled)
        lay.addWidget(self.toggle)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setContentsMargins(0, 0, 0, 6)
        self.form.setSpacing(8)
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        lay.addWidget(self.body)
        self._toggled(expanded)

    def _toggled(self, on):
        self.body.setVisible(on)
        self.toggle.setIcon(icon("mdi6.chevron-down" if on else "mdi6.chevron-right", "#AFA9EC"))

    def row(self, text, w):
        if text is None:
            self.form.addRow(w)
        else:
            lab = QLabel(text)
            lab.setStyleSheet(f"color:{C['text2']};")
            self.form.addRow(lab, w)
        return w

    def widget(self, w):
        self.form.addRow(w)
        return w


class CheckList(QListWidget):
    """可勾选的列表（条件、事件、类别等）。"""
    changed = Signal()

    def __init__(self, max_h=150):
        super().__init__()
        self.setMaximumHeight(max_h)
        self.itemChanged.connect(lambda _: self.changed.emit())

    def set_items(self, items, checked=None, default_all=True):
        prev = set(self.checked())
        self.blockSignals(True)
        self.clear()
        keep = set(checked) if checked is not None else (prev & set(map(str, items)))
        for it in items:
            li = QListWidgetItem(str(it))
            li.setFlags(li.flags() | Qt.ItemIsUserCheckable)
            on = str(it) in keep or (not keep and default_all)
            li.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.addItem(li)
        self.blockSignals(False)
        self.changed.emit()

    def checked(self):
        return [self.item(i).text() for i in range(self.count()) if self.item(i).checkState() == Qt.Checked]


class FigureCard(Card):
    def __init__(self, title, fig):
        super().__init__(title)
        self.fig = fig
        canvas = FigureCanvasQTAgg(fig)
        w, h = fig.get_size_inches()
        canvas.setMinimumHeight(int(min(max(h / max(w, 0.1) * 640, 260), 620)))
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas = canvas
        b1 = QToolButton()
        b1.setIcon(icon("mdi6.magnify-plus-outline"))
        b1.setToolTip("放大查看（可缩放、平移）")
        b1.clicked.connect(self.zoom)
        b2 = QToolButton()
        b2.setIcon(icon("mdi6.download-outline"))
        b2.setToolTip("保存图片")
        b2.clicked.connect(self.save)
        for b in (b1, b2):
            b.setStyleSheet("QToolButton{border:none;background:transparent;padding:4px;border-radius:8px;}"
                            "QToolButton:hover{background:rgba(255,255,255,0.08);}")
            b.setCursor(Qt.PointingHandCursor)
            self.head.addWidget(b)
        self.add(canvas)
        canvas.draw_idle()

    def save(self):
        p, _ = QFileDialog.getSaveFileName(self, "保存图片", f"{self.title.text()}.png",
                                           "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if p:
            self.fig.savefig(p, dpi=200, bbox_inches="tight")

    def zoom(self):
        d = QDialog(self)
        d.setWindowTitle(self.title.text())
        d.resize(1100, 760)
        lay = QVBoxLayout(d)
        c = FigureCanvasQTAgg(self.fig)
        lay.addWidget(NavigationToolbar2QT(c, d))
        lay.addWidget(c)
        d.finished.connect(lambda _: (self.fig.set_canvas(self.canvas), self.canvas.draw_idle()))
        d.exec()


class EmptyState(QWidget):
    def __init__(self, icon_name, title, body, buttons=()):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        ic = QLabel()
        ic.setPixmap(icon(icon_name, "#534AB7").pixmap(64, 64))
        ic.setAlignment(Qt.AlignCenter)
        lay.addWidget(ic)
        t = label(title, "H1")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        b = label(body, "Sub", wrap=True)
        b.setAlignment(Qt.AlignCenter)
        b.setMaximumWidth(520)
        lay.addWidget(b, 0, Qt.AlignHCenter)
        if buttons:
            lay.addSpacing(10)
            lay.addLayout(hbox("stretch", *buttons, "stretch"))
        lay.addStretch(2)


class ResultView(QScrollArea):
    """分析页中间的结果区：标题、参数、指标卡、说明、图。"""

    def __init__(self, empty: QWidget):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.empty = empty
        self.setWidget(empty)

    def show_result(self, res):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 8, 12)
        lay.setSpacing(12)
        lay.addWidget(label(res.title, "H1"))
        if res.params:
            chips = QWidget()
            fl = QHBoxLayout(chips)
            fl.setContentsMargins(0, 0, 0, 0)
            fl.setSpacing(6)
            for k, v in list(res.params.items())[:7]:
                fl.addWidget(chip(f"{k} · {v}", C["text2"], C["card"]))
            fl.addStretch(1)
            lay.addWidget(chips)
        if res.metrics:
            g = MetricGrid(3)
            g.set(res.metrics)
            lay.addWidget(g)
        for n in res.notes:
            import html as _h
            lay.addWidget(label(f"<span style='color:{C['amber']}'>●</span>&nbsp; {_h.escape(n)}", "Sub", wrap=True,
                                rich=True))
        figs = res.figures
        i = 0
        while i < len(figs):
            t, f = figs[i]
            w_, h_ = f.get_size_inches()
            # 两张偏方的小图并排放
            if i + 1 < len(figs) and w_ / h_ < 1.4 and figs[i + 1][1].get_size_inches()[0] / \
                    figs[i + 1][1].get_size_inches()[1] < 1.4:
                row = QHBoxLayout()
                row.setSpacing(12)
                row.addWidget(FigureCard(t, f))
                row.addWidget(FigureCard(*figs[i + 1]))
                lay.addLayout(row)
                i += 2
            else:
                lay.addWidget(FigureCard(t, f))
                i += 1
        if res.table:
            from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
            head, rows = res.table
            tb = QTableWidget(len(rows), len(head))
            tb.setHorizontalHeaderLabels(head)
            tb.verticalHeader().setVisible(False)
            tb.setAlternatingRowColors(True)
            for r, row in enumerate(rows):
                for c, v in enumerate(row):
                    tb.setItem(r, c, QTableWidgetItem(str(v)))
            tb.horizontalHeader().setStretchLastSection(True)
            tb.setMinimumHeight(min(40 + 30 * len(rows), 360))
            card = Card(res.table_title, "可以选中后 Ctrl+C 复制到表格软件")
            card.add(tb)
            lay.addWidget(card)
        lay.addStretch(1)
        self._swap(w)
        self.verticalScrollBar().setValue(0)

    def _swap(self, w):
        old = self.takeWidget()
        if old is not None and old is not self.empty:
            old.deleteLater()
        self.setWidget(w)

    def reset(self):
        self._swap(self.empty)


def _render(fig, buf, **kw):
    """用独立的 Agg 画布导出图片：界面里的画布可能已随结果页销毁。"""
    import shiboken6
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    old = fig.canvas
    FigureCanvasAgg(fig)
    try:
        fig.savefig(buf, **kw)
    finally:
        if isinstance(old, FigureCanvasQTAgg) and shiboken6.isValid(old):
            fig.set_canvas(old)


def fig_pixmap(fig, width=360):
    buf = io.BytesIO()
    _render(fig, buf, format="png", dpi=50)
    pm = QPixmap()
    pm.loadFromData(buf.getvalue())
    return pm.scaledToWidth(width, Qt.SmoothTransformation)


def fig_png_bytes(fig, dpi=110):
    buf = io.BytesIO()
    _render(fig, buf, format="png", dpi=dpi, bbox_inches="tight")
    return buf.getvalue()


def checkbox(text, on=False):
    c = QCheckBox(text)
    c.setChecked(on)
    return c
