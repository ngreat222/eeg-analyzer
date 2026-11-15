"""Native workspace surfaces and project status graphics."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .. import theme
from ..compat import resource_path


class WorkspaceCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.overview = True
        self.photo = QPixmap(resource_path("assets", "atrium.jpg"))

    def set_overview(self, enabled):
        self.overview = enabled
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.C["bg"]))
        if self.photo.isNull():
            return
        size = self.photo.size().scaled(self.size(), Qt.KeepAspectRatioByExpanding)
        left = (self.width() - size.width()) / 2
        top = (self.height() - size.height()) / 2
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(QRectF(left, top, size.width(), size.height()), self.photo, QRectF(self.photo.rect()))
        if theme.MODE == "light":
            overlay = QColor(241, 246, 233, 65 if self.overview else 216)
        else:
            overlay = QColor(15, 24, 15, 210 if self.overview else 240)
        painter.fillRect(self.rect(), overlay)


class StagePath(QWidget):
    """Three workflow milestones, with separate units for recordings and participants."""
    def __init__(self):
        super().__init__()
        self.values = (0, 0, 0)
        self.setMinimumHeight(78)

    def set_counts(self, records, processed, ready):
        self.values = (records, processed, ready)
        self.setAccessibleName(f"原始记录 {records} 条，已预处理 {processed} 条，可分析 {ready} 人")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        points = [QPointF(18, min(height*.65, height-48)), QPointF(width*.50, min(height*.44, height-48)), QPointF(width-22, min(height*.23, height-48))]
        for i, (p, q) in enumerate(zip(points, points[1:])):
            path = QPainterPath(p)
            mid = (p.x()+q.x()) / 2
            path.cubicTo(mid, p.y(), mid, q.y(), q.x(), q.y())
            connected = self.values[i] and self.values[i+1]
            painter.setPen(QPen(QColor("#bad664" if connected else "#899477"), 2,
                                Qt.SolidLine if connected else Qt.DotLine))
            painter.drawPath(path)
        for i, (point, count, label, unit) in enumerate(zip(points, self.values,
                ("原始记录", "已预处理", "可分析"), ("条", "条", "人"))):
            filled = count > 0
            painter.setPen(QPen(QColor("#bdd669" if filled else "#899477"), 1.5))
            painter.setBrush(QColor("#bad665" if filled else "#303d28"))
            painter.drawEllipse(point, 5, 5)
            left = max(0, min(width-78, point.x()-39))
            box = QRectF(left, point.y()+8, 78, 21)
            align = (Qt.AlignLeft if i == 0 else Qt.AlignRight if i == 2 else Qt.AlignHCenter) | Qt.AlignVCenter
            painter.setPen(QColor("#f0f4df"))
            painter.drawText(box, align, label)
            painter.setPen(QColor("#c6d2b1"))
            painter.drawText(box.translated(0,17), align, f"{count} {unit}")
