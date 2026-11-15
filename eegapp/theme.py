"""BIAP 的明暗主题、组件样式及科学图表配色。

启动时 compat.setup() 按用户设置调用 use("dark" / "light")；P、C、SERIES 原地更新，
所以各模块 `from ..theme import C` 拿到的始终是当前主题的颜色。主窗口在切换外观时同步刷新现有控件。
"""

DARK = dict(
    ink="#172019", base="#1c241b", raised="#273025", field="#2e372b", hover="#384333",
    line="#3d4939", line2="#627058", alt="#2a3327", text="#f4f4e8", text2="#d2d8c7",
    muted="#b0bba1", clay="#546d31", clay_hi="#657d3f", clay_soft="#37472e",
    clay_text="#d0e5a4", green="#74cdb7", yellow="#efbf77", red="#ffada6",
    danger_bg="#48323a", blue="#7ebdf1", div_lo="#426887", div_hi="#90644d",
    icon_hover="rgba(255,255,255,0.08)",
)

LIGHT = dict(
    ink="#f4f6ed", base="#eff3e9", raised="#ffffff", field="#ffffff", hover="#e7ecdf",
    line="#d5dccd", line2="#bcc5b1", alt="#f4f5ec", text="#20251d", text2="#414b3c",
    muted="#596551", clay="#4a632d", clay_hi="#3a5123", clay_soft="#e3ebd3",
    clay_text="#405627", green="#526a2c", yellow="#94600d", red="#ac403c",
    danger_bg="#fcebea", blue="#337baa", div_lo="#bdd5e9", div_hi="#edc6a8",
    icon_hover="rgba(0,0,0,0.06)",
)

SERIES_DARK = ["#71ccda", "#efb477", "#97a8ff", "#94cf9b", "#eb98ba", "#c2a6e6"]
SERIES_LIGHT = ["#146983", "#b85822", "#525ca5", "#327653", "#ac4775", "#77549b"]

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
        log_bg=P["raised"], axis=P["line2"], sig=P["clay"], pos=P["yellow"], neg=P["blue"],
        icon_hover=P["icon_hover"],
    )
    SERIES[:] = SERIES_LIGHT if MODE == "light" else SERIES_DARK
    DIVERGING[:] = [P["blue"], P["div_lo"], C["card"], P["div_hi"], SERIES[1]]
    BROWSER = MODE
    QSS = _qss()



SANS = '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Helvetica Neue"'
MONO = '"Consolas", "Menlo", "SF Mono", "Microsoft YaHei UI", "PingFang SC"'


def _qss():
    light = MODE == "light"
    glass = "rgba(253,253,247,226)" if light else "rgba(39,48,37,235)"
    rail = "rgba(251,253,247,235)" if light else "rgba(33,43,32,242)"
    edge = "rgba(255,255,255,185)" if light else P["line2"]
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
    QLabel#H1 {{ font-size: 25px; font-weight: 700; }}
    QLabel#H2 {{ font-size: 14px; font-weight: 600; }}
    QLabel#Brand {{ color: #f1f6fa; font-family: "Segoe UI", "Helvetica Neue"; font-size: 23px; font-weight: 700; }}
    QLabel#Sub {{ color: {C['text2']}; font-size: 12px; }}
    QLabel#Muted {{ color: {C['muted']}; font-size: 12px; }}
    QLabel#Caption {{ color: {C['muted']}; font-size: 11px; }}
    QLabel#MetricValue {{ font-size: 22px; font-weight: 600; }}
    
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
    QWidget#Main {{ background: transparent; }}
    QWidget#Rail {{ background: {rail}; border: 1px solid {edge}; border-radius: 22px; }}
    QWidget#Rail QPushButton {{ color: {C['text2']}; border: none; border-radius: 12px; padding: 7px 10px; text-align: left; }}
    QWidget#Rail QPushButton:hover {{ background: {P['hover']}; }}
    QWidget#Rail QPushButton:checked {{ background: {P['hover']}; color: {C['text']}; font-weight: 600; }}
    QWidget#Rail QPushButton#NewProject {{ color: #27311e; border-radius: 14px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #aad4a3,stop:0.54 #e7edb1,stop:1 #ffc87b); }}
    QWidget#Rail QLabel#Brand {{ color: {C['text']}; }}
    QWidget#Side, QFrame#Card, QFrame#Metric, QFrame#EmptySheet {{ background: {glass}; border: 1px solid {edge}; border-radius: 17px; }}
    QLabel#EmptyIcon {{ background: {C['soft']}; border-radius: 12px; }}
    QLabel#HeroTitle {{ font-size: 52px; font-weight: 400; color: {C['text']}; }}
    QLabel#HeroProject {{ font-size: 49px; font-weight: 500; color: {"#4c632e" if light else "#cae1a3"}; }}
    QLabel#HeroTitle[compact="true"], QLabel#HeroProject[compact="true"] {{ font-size: 33px; }}
    QPushButton#JourneyNext {{ background: rgba(235,247,199,24); border: 1px solid rgba(228,239,210,54); border-radius: 13px; text-align: left; color: #f3f6e5; padding: 9px 13px; }}
    QPushButton#JourneyNext:hover {{ background: rgba(235,247,199,42); }}
    QWidget#Rail QPushButton[kind="sectionNav"] {{ text-align: left; padding: 8px 4px; background: transparent; font-weight: 600; color: {C['text2']}; }}
    QWidget#Rail QPushButton[kind="sectionNav"]:hover {{ background: {P['hover']}; }}
    QLabel#ProjectContext {{ color: {C['muted']}; font-size: 12px; }}
    QLabel#WorkspaceTitle {{ font-size: 33px; font-weight: 500; color: {C['on_soft']}; }}
    QFrame#SettingsSheet {{ background: {glass}; border: 1px solid {edge}; border-radius: 18px; }}
    QFrame#StepLink {{ text-align: left; border: 1px solid transparent; border-bottom-color: {C['line']}; background: transparent; padding: 0; border-radius: 4px; }}
    QFrame#StepLink:hover {{ background: {P['hover']}; }}
    QFrame#StepLink:focus {{ border-color: {C['accent']}; }}
    QFrame#ContextCard {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #536042,stop:1 #273521); border-radius: 16px; min-width: 104px; }}
    QFrame#ContextCard QLabel {{ color: #ebf2d8; }}
    QFrame#ContextCard QLabel#ContextValue {{ color: #fffef3; font-size: 23px; font-weight: 500; }}
    QFrame#FlowHeader {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #34432a,stop:1 #4b5638); border-radius: 12px; }}
    QFrame#FlowHeader QLabel {{ color: #f2f5e5; }}
    QFrame#Journey {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #495236,stop:0.52 #303c29,stop:1 #202c1d); border: 1px solid #5b6749; border-radius: 22px; }}
    QFrame#Journey QLabel {{ color: #e4ebd3; }}
    QFrame#Journey QLabel#Percentage {{ color: #fcfdec; font-family: "Segoe UI", "Helvetica Neue"; font-size: 44px; font-weight: 400; }}
    QFrame#Journey QProgressBar {{ background: #62704f; max-height: 4px; }}
    QFrame#Journey QProgressBar::chunk {{ background: #c8e36d; }}
    QFrame#SignalSummary {{ background: qlineargradient(x1:0.2,y1:0,x2:0.8,y2:1,stop:0 #a9d2ed,stop:0.49 #d3e7de,stop:1 #f7e7a5); border-radius: 19px; border: 1px solid #e3eccf; }}
    QFrame#SignalSummary QLabel {{ color: #304032; }}
    QFrame#SignalSummary QLabel#Readout {{ font-size: 27px; font-weight: 600; color: #202e21; }}
    QToolBar {{ background: {C['bg']}; border: none; spacing: 5px; padding: 5px 12px 2px 12px; }}
    QToolBar QToolButton {{ color: {C['text2']}; background: {C['card']}; padding: 5px 10px; border-radius: 12px; }}
    QToolBar QToolButton:hover, QToolBar QToolButton:checked {{ color: {C['on_soft']}; background: {C['soft']}; }}
    QHeaderView::section {{ background: {P['alt']}; color: {C['text2']}; }}
    QTableView::item:selected, QListWidget::item:selected, QListWidget#SubList::item:selected {{ background: {C['soft']}; color: {C['on_soft']}; }}
    QPushButton:focus, QToolButton:focus {{ border: 1px solid {C['accent']}; }}
    QDockWidget::title {{ background: {C['card']}; color: {C['text']}; padding: 9px 12px; }}
    QSplitter::handle:horizontal {{ width: 2px; }}
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
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti",
                            "Arial Unicode MS", "Noto Sans CJK SC", "DejaVu Sans"],
        "axes.unicode_minus": False, "figure.dpi": 100, "lines.linewidth": 1.7,
        "image.cmap": "RdBu_r",
    })


use(MODE)
