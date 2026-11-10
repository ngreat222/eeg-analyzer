"""界面主题：暖灰底色 + 陶土橙强调色，标题用衬线体（参考 Claude 桌面应用的观感）。有深色和亮色两套。

启动时 compat.setup() 按用户设置调用 use("dark" / "light")；P、C、SERIES 原地更新，
所以各模块 `from ..theme import C` 拿到的始终是当前主题的颜色。换主题后要重启软件才完全生效。
"""

DARK = dict(
    ink="#1a1918",          # 侧栏
    base="#262624",         # 主区背景
    raised="#2d2c2a",       # 卡片、图表底色
    field="#30302e",        # 输入框
    hover="#383734",
    line="#3b3a36",         # 分隔线、边框
    line2="#4a4944",
    alt="#2a2927",          # 表格隔行
    text="#f5f4ee",
    text2="#c2c0b6",
    muted="#8f8d86",
    clay="#d97757",         # 强调色
    clay_hi="#e0896b",
    clay_soft="#3a2a23",
    clay_text="#eba68c",
    green="#8fbf7f",
    yellow="#e3b664",
    red="#e5735f",
    danger_bg="#3a2622",
    blue="#7aa6d6",
    div_lo="#3f5d80",
    div_hi="#9a4f39",
    icon_hover="rgba(255,255,255,0.08)",
)

LIGHT = dict(
    ink="#f0eee6",
    base="#faf9f5",
    raised="#ffffff",
    field="#ffffff",
    hover="#efede6",
    line="#e6e3da",
    line2="#d2cfc4",
    alt="#f8f7f3",
    text="#1f1e1d",
    text2="#57554f",
    muted="#8a8780",
    clay="#c96442",
    clay_hi="#b8573a",
    clay_soft="#f6e4da",
    clay_text="#a4482a",
    green="#3f7d3a",
    yellow="#a66b00",
    red="#c2412d",
    danger_bg="#fbe9e5",
    blue="#3b6ea8",
    div_lo="#9dbbdc",
    div_hi="#e3a58c",
    icon_hover="rgba(0,0,0,0.06)",
)

SERIES_DARK = ["#d97757", "#7aa6d6", "#8fbf7f", "#c39bd3", "#e3b664", "#5fb3ad", "#d98aa6", "#a8a497"]
SERIES_LIGHT = ["#c96442", "#3b6ea8", "#4f8a3f", "#8e5bb0", "#b8860b", "#2f8f87", "#b8527a", "#6f6c63"]

MODE = "dark"
P = dict(DARK)
C = {}
SERIES = list(SERIES_DARK)
DIVERGING = []
QSS = ""
BROWSER = "dark"          # MNE 波形浏览器的主题


def use(mode):
    """切换主题：原地更新 P、C、SERIES 等，并重建 QSS。"""
    global MODE, QSS, BROWSER
    MODE = "light" if mode == "light" else "dark"
    P.clear()
    P.update(LIGHT if MODE == "light" else DARK)
    C.clear()
    C.update(
        bg=P["base"], rail=P["ink"], panel=P["base"], card=P["raised"], field=P["field"],
        field_hover=P["hover"], line=P["line"], text=P["text"], text2=P["text2"], muted=P["muted"],
        accent=P["clay"], on_accent="#ffffff", soft=P["clay_soft"], on_soft=P["clay_text"],
        section=P["muted"], ok=P["green"], warn=P["yellow"], danger=P["red"], nav_icon=P["text2"],
        log_bg=P["ink"], axis=P["line2"], sig=P["clay"], pos=P["yellow"], neg=P["blue"],
        icon_hover=P["icon_hover"],
    )
    SERIES[:] = SERIES_LIGHT if MODE == "light" else SERIES_DARK
    DIVERGING[:] = [P["blue"], P["div_lo"], C["card"], P["div_hi"], P["clay_hi"]]
    BROWSER = MODE
    QSS = _qss()


SERIF = '"Cambria", "Noto Serif SC", "Source Han Serif SC", "SimSun"'
SANS = '"Microsoft YaHei UI", "Microsoft YaHei"'


def _qss():
    return f"""
    * {{ font-family: {SANS}; font-size: 13px; color: {C['text']}; }}
    QMainWindow, QWidget#Main {{ background: {C['bg']}; }}
    QWidget#Rail {{ background: {C['rail']}; }}
    QWidget#Side {{ background: {C['panel']}; border-left: 1px solid {C['line']}; }}
    QToolTip {{ background: {P['field']}; color: {C['text']}; border: 1px solid {C['line']}; padding: 5px 9px; border-radius: 6px; }}
    
    QPushButton {{ background: transparent; border: 1px solid {P['line2']}; border-radius: 8px; padding: 6px 13px; min-height: 18px; }}
    QPushButton:hover {{ background: {P['hover']}; }}
    QPushButton:pressed {{ background: {P['line']}; }}
    QPushButton:disabled {{ color: {C['muted']}; border-color: {C['line']}; }}
    QPushButton[kind="primary"] {{ background: {C['accent']}; border-color: {C['accent']}; color: {C['on_accent']}; font-weight: 600; }}
    QPushButton[kind="primary"]:hover {{ background: {P['clay_hi']}; border-color: {P['clay_hi']}; }}
    QPushButton[kind="accent"] {{ background: {P['field']}; border-color: {P['line2']}; }}
    QPushButton[kind="accent"]:hover {{ background: {P['hover']}; }}
    QPushButton[kind="ghost"] {{ border-color: transparent; color: {C['text2']}; }}
    QPushButton[kind="ghost"]:hover {{ color: {C['text']}; background: {P['hover']}; }}
    QPushButton[kind="danger"] {{ border-color: transparent; color: {C['danger']}; }}
    QPushButton[kind="danger"]:hover {{ background: {P['danger_bg']}; }}
    QPushButton[kind="nav"] {{ border: none; border-radius: 8px; text-align: left; padding: 7px 10px; color: {C['text2']}; }}
    QPushButton[kind="nav"]:hover {{ background: {P['raised']}; color: {C['text']}; }}
    QPushButton[kind="nav"]:checked {{ background: {P['field']}; color: {C['text']}; }}
    QToolButton {{ border: none; background: transparent; }}
    
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextBrowser {{
        background: {P['field']}; border: 1px solid {C['line']}; border-radius: 8px; padding: 6px 9px;
        selection-background-color: {C['accent']}; selection-color: {C['on_accent']}; }}
    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {P['line2']}; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {C['accent']}; }}
    QAbstractSpinBox, QComboBox, QLineEdit {{ min-width: 40px; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {P['field']}; border: 1px solid {C['line']}; border-radius: 8px;
        selection-background-color: {P['hover']}; selection-color: {C['text']}; outline: none; padding: 4px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {P['line2']}; background: {P['field']}; }}
    QCheckBox::indicator:hover {{ border-color: {C['accent']}; }}
    QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}
    
    QFrame#Card {{ background: {C['card']}; border: 1px solid {C['line']}; border-radius: 12px; }}
    QFrame#Metric {{ background: {C['card']}; border: 1px solid {C['line']}; border-radius: 10px; }}
    QLabel#H1 {{ font-family: {SERIF}; font-size: 23px; }}
    QLabel#H2 {{ font-size: 14px; font-weight: 600; }}
    QLabel#Brand {{ font-family: {SERIF}; font-size: 18px; }}
    QLabel#Sub {{ color: {C['text2']}; font-size: 12px; }}
    QLabel#Muted {{ color: {C['muted']}; font-size: 12px; }}
    QLabel#Caption {{ color: {C['muted']}; font-size: 11px; }}
    QLabel#MetricValue {{ font-family: {SERIF}; font-size: 21px; }}
    
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {P['line2']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {C['muted']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {P['line2']}; border-radius: 4px; min-width: 30px; }}
    
    QTableWidget, QListWidget, QTreeWidget {{ background: {C['card']}; border: 1px solid {C['line']}; border-radius: 10px;
        gridline-color: {C['line']}; alternate-background-color: {P['alt']}; outline: none; }}
    QTableWidget::item, QListWidget::item {{ padding: 4px; border-radius: 6px; }}
    QListWidget::item:hover {{ background: {P['hover']}; }}
    QTableWidget::item:selected, QListWidget::item:selected {{ background: {P['hover']}; color: {C['text']}; }}
    QListWidget#SubList {{ background: transparent; border: none; }}
    QListWidget#SubList::item {{ padding: 6px 8px; margin: 1px 0; }}
    QListWidget#SubList::item:selected {{ background: {P['field']}; }}
    QHeaderView {{ background: {C['card']}; border: none; }}
    QTableCornerButton::section {{ background: {C['card']}; border: none; }}
    QTableView::indicator, QListView::indicator {{ width: 15px; height: 15px; border-radius: 4px;
        border: 1px solid {P['line2']}; background: {P['field']}; }}
    QTableView::indicator:checked, QListView::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}
    QHeaderView::section {{ background: {C['card']}; color: {C['muted']}; border: none;
        border-bottom: 1px solid {C['line']}; padding: 7px; font-weight: 600; font-size: 12px; }}
    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ background: transparent; color: {C['text2']}; padding: 6px 14px; border-radius: 8px; margin-right: 4px; }}
    QTabBar::tab:selected {{ background: {P['field']}; color: {C['text']}; }}
    QProgressBar {{ background: {P['field']}; border: none; border-radius: 2px; max-height: 4px; }}
    QProgressBar::chunk {{ background: {C['accent']}; border-radius: 2px; }}
    QStatusBar {{ background: {C['bg']}; color: {C['muted']}; border-top: 1px solid {C['line']}; }}
    QStatusBar QLabel {{ color: {C['muted']}; font-size: 12px; }}
    QMenu {{ background: {P['field']}; border: 1px solid {C['line']}; padding: 4px; border-radius: 8px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {P['hover']}; }}
    QMessageBox, QDialog {{ background: {C['bg']}; }}
    QSplitter::handle {{ background: {C['line']}; }}
    """





def apply_mpl():
    import matplotlib as mpl
    from cycler import cycler
    from matplotlib.colors import LinearSegmentedColormap
    # 名字沿用旧版，图表代码里引用的是这个名字
    mpl.colormaps.register(LinearSegmentedColormap.from_list("mocha_div", DIVERGING), force=True)
    mpl.rcParams.update({
        "figure.facecolor": C["card"], "axes.facecolor": C["card"], "savefig.facecolor": C["card"],
        "axes.edgecolor": P["line2"], "axes.labelcolor": C["text2"], "text.color": C["text"],
        "xtick.color": C["muted"], "ytick.color": C["muted"], "grid.color": C["line"],
        "legend.frameon": False, "legend.labelcolor": C["text2"],
        "axes.prop_cycle": cycler(color=SERIES), "axes.titleweight": "normal", "axes.titlesize": 12,
        "axes.spines.top": False, "axes.spines.right": False,
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"],
        "axes.unicode_minus": False, "figure.dpi": 100, "lines.linewidth": 1.7,
        "image.cmap": "RdBu_r",
    })


use(MODE)
