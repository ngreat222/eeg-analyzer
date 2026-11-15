"""Apply a palette in place without rebuilding controls or losing editor state."""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QTableWidget, QWidget
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.text import Text

from .. import theme
from .widgets import ICON_SPECS, icon


def restyle_figure(fig, colors):
    fig.set_facecolor(theme.C["card"])
    for ax in fig.axes:
        ax.set_facecolor(theme.C["card"])
        ax.tick_params(colors=theme.C["muted"])
        ax.xaxis.label.set_color(theme.C["text2"])
        ax.yaxis.label.set_color(theme.C["text2"])
        for spine in ax.spines.values():
            spine.set_edgecolor(theme.C["axis"])
    for artist in fig.findobj(Text):
        color = artist.get_color()
        if isinstance(color, str) and color.lower() in colors:
            artist.set_color(colors[color.lower()])


def apply_window_theme(window, mode):
    from .main_window import _check_qss
    original = dict(theme.P)
    theme.use(mode)
    theme.apply_mpl()
    colors = {v.lower(): theme.P[k] for k, v in original.items() if v.startswith("#")}
    window.setUpdatesEnabled(False)
    try:
        window.setStyleSheet(theme.QSS + _check_qss())
        for widget in window.findChildren(QWidget):
            if widget is window.assistant:
                continue
            css = widget.styleSheet()
            if css:
                widget.setStyleSheet(re.sub(r"#[0-9a-fA-F]{6}\b",
                    lambda m: colors.get(m[0].lower(), m[0]), css))
            glyph = widget.property("themeGlyph")
            if isinstance(widget, QLabel) and glyph:
                name, role, size = glyph
                widget.setPixmap(icon(name, theme.C[role]).pixmap(int(size), int(size)))
        for control in [*window.findChildren(QAbstractButton), *window.findChildren(QAction)]:
            spec = ICON_SPECS.get(control.icon().cacheKey())
            if spec:
                name, fg, active = spec
                fg = theme.C["on_accent"] if control.property("kind") == "primary" else colors.get(fg, fg)
                control.setIcon(icon(name, fg, colors.get(active, active)))
        for table in window.findChildren(QTableWidget):
            # Only status/annotation cells have explicit foreground brushes.
            for item in table.findItems("", Qt.MatchContains):
                brush = item.foreground()
                if brush.style() != Qt.NoBrush and brush.color().name() in colors:
                    item.setForeground(QColor(colors[brush.color().name()]))
        if window.assistant is not None:
            window.assistant.apply_theme()
        light = theme.MODE == "light"
        window.theme_button.setText("切换到深色" if light else "切换到亮色")
        window.theme_button.setIcon(icon("mdi6.weather-night" if light else "mdi6.white-balance-sunny"))
        canvases = window.findChildren(FigureCanvasQTAgg)
        figures = {canvas.figure for canvas in canvases}
        figures.update(fig for _, report in window.reports for _, fig in report.figures)
        for fig in figures:
            restyle_figure(fig, colors)
        for canvas in canvases:
            canvas.draw_idle()
        report_page = window.pages["报告"]
        for picker in report_page.findChildren(QComboBox):
            picker.currentIndexChanged.emit(picker.currentIndex())
        window.work_area.widget().update()
    finally:
        window.setUpdatesEnabled(True)
