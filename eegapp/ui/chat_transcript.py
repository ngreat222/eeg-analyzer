"""Native conversation bubbles and an expandable record of real tool calls."""
import json
import math
from time import monotonic

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QTextBrowser, QVBoxLayout, QWidget)

from .widgets import WrappedLabel


class MessageBody(QTextBrowser):
    def __init__(self, text, markdown):
        super().__init__()
        self.setObjectName("MessageBody")
        self.setFrameShape(QFrame.NoFrame)
        self.setOpenLinks(False)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        if markdown:
            self.textCursor().insertMarkdown(text, QTextDocument.MarkdownFeature.MarkdownNoHTML)
        else:
            self.setPlainText(text)
        self.document().documentLayout().documentSizeChanged.connect(self.fit)
        self.fit()

    def loadResource(self, resource_type, url):
        return None

    def fit(self, *_):
        self.document().setTextWidth(max(1, self.viewport().width()))
        height = math.ceil(self.document().size().height()) + 3
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class Activity(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Activity")
        self.started = monotonic()
        self.action = "等待模型回复"
        self.entries = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        self.header = QPushButton()
        self.header.setObjectName("ActivityHeading")
        self.header.setCheckable(True)
        layout.addWidget(self.header)
        self.details = QWidget()
        self.rows = QVBoxLayout(self.details)
        self.rows.setContentsMargins(4, 0, 4, 4)
        self.details.hide()
        self.header.toggled.connect(self.details.setVisible)
        self.header.toggled.connect(self.refresh_heading)
        layout.addWidget(self.details)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(330)
        self.tick()

    def tick(self):
        elapsed = monotonic() - self.started
        dots = "·" * (1 + int(elapsed * 3) % 3)
        self.header.setText(f"{self.action} {dots}   {int(elapsed)} 秒")

    def record(self, event):
        phase = event["phase"]
        if phase == "waiting":
            self.action = "等待模型回复" if not self.entries else "正在整理工具结果"
        elif phase == "start":
            self.action = event["label"] + " " + str(event["args"].get("target", ""))
        else:
            target = str(event.get("args", {}).get("target", ""))
            result = event["result"]
            outcome = "失败" if phase == "error" else result.get("status", "完成")
            title = f"{event['label']}  {target}  ·  {outcome}"
            toggle = QPushButton(title)
            toggle.setObjectName("ActivityHeading")
            toggle.setCheckable(True)
            self.rows.addWidget(toggle)
            detail = WrappedLabel()
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.PlainText)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detail.setText(json.dumps({"参数": event.get("args", {}), "结果": result},
                                      ensure_ascii=False, indent=2))
            detail.hide()
            self.rows.addWidget(detail)
            toggle.toggled.connect(detail.setVisible)
            self.entries.append(title)
        self.tick()

    def finish(self, error=False):
        self.timer.stop()
        status = "请求失败" if error else "执行记录"
        self.summary = f"{status}  ·  {len(self.entries)} 次操作  ·  {int(monotonic()-self.started)} 秒"
        self.refresh_heading()
        if not self.entries:
            self.setVisible(error)

    def refresh_heading(self):
        if hasattr(self, "summary"):
            arrow = "▾" if self.header.isChecked() else "▸"
            self.header.setText(f"{arrow}  {self.summary}")


class ChatTranscript(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.page = QWidget()
        self.page.setObjectName("Transcript")
        self.rows = QVBoxLayout(self.page)
        self.rows.setContentsMargins(4, 8, 4, 12)
        self.rows.setAlignment(Qt.AlignTop)
        self.rows.setSpacing(10)
        self.rows.addStretch(1)
        self.setWidget(self.page)
        self.follow = True
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(lambda value: setattr(self, "follow", value >= bar.maximum()-12))
        bar.rangeChanged.connect(lambda _, end: bar.setValue(end) if self.follow else None)

    def message(self, who, text, markdown=False):
        mine = who == "你"
        row = QHBoxLayout()
        card = QFrame()
        card.setObjectName("UserBubble" if mine else "ReplyBubble")
        box = QVBoxLayout(card)
        box.setContentsMargins(13, 9, 13, 10)
        box.setSpacing(4)
        author = QLabel(who)
        author.setTextFormat(Qt.PlainText)
        author.setObjectName("MessageAuthor")
        box.addWidget(author)
        box.addWidget(MessageBody(text, markdown))
        if mine:
            row.addStretch(1)
        row.addWidget(card, 5)
        if not mine:
            row.addStretch(1)
        self.rows.insertLayout(self.rows.count()-1, row)

    def add_activity(self):
        activity = Activity()
        self.rows.insertWidget(self.rows.count()-1, activity)
        return activity

    def review_button(self, count, callback):
        button = QPushButton(f"预览修改（{count} 项）")
        button.setObjectName("ReviewChanges")
        button.clicked.connect(callback)
        self.rows.insertWidget(self.rows.count()-1, button, 0, Qt.AlignLeft)
        return button

    def clear(self):
        def empty(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().hide()
                    item.widget().deleteLater()
                elif item.layout() is not None:
                    empty(item.layout())
                    item.layout().deleteLater()
        empty(self.rows)
        self.rows.addStretch(1)
        self.follow = True

    def toPlainText(self):
        return "\n".join(w.toPlainText() for w in self.page.findChildren(MessageBody))
