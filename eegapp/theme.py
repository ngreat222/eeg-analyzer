"""深色主题：界面 QSS 与 matplotlib 图表样式共用同一套颜色。"""

C = dict(
    bg="#1E1E1C",         # 主区背景
    rail="#26215C",       # 左侧导航（深紫）
    panel="#24233A",      # 右侧设置面板
    card="#2C2C2A",       # 卡片
    card2="#353532",      # 卡片里的输入框等
    line="#444441",       # 分隔线
    text="#F1EFE8",
    text2="#B4B2A9",
    muted="#888780",
    accent="#7F77DD",     # 紫色主色
    accent_soft="#3C3489",
    accent_text="#EEEDFE",
    mint="#5DCAA5",       # 主按钮
    mint_dark="#04342C",
    coral="#F0997B",
    pink="#ED93B1",
    amber="#EF9F27",
    danger="#F09595",
)

# 图表里各条件的颜色顺序
SERIES = ["#AFA9EC", "#5DCAA5", "#F0997B", "#ED93B1", "#EF9F27", "#85B7EB", "#97C459", "#D3D1C7"]

QSS = f"""
* {{ font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; color: {C['text']}; }}
QMainWindow, QWidget#Main {{ background: {C['bg']}; }}
QWidget#Rail {{ background: {C['rail']}; }}
QWidget#Side {{ background: {C['panel']}; }}
QToolTip {{ background: {C['card2']}; color: {C['text']}; border: 1px solid {C['line']}; padding: 4px 8px; }}

QPushButton {{ background: {C['card2']}; border: 1px solid {C['line']}; border-radius: 15px;
              padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ background: #403F3B; border-color: #5F5E5A; }}
QPushButton:pressed {{ background: #4A4945; }}
QPushButton:disabled {{ color: {C['muted']}; }}
QPushButton[kind="primary"] {{ background: {C['mint']}; color: {C['mint_dark']}; border: none; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: #73D4B3; }}
QPushButton[kind="accent"] {{ background: {C['accent_soft']}; color: {C['accent_text']}; border: none; }}
QPushButton[kind="accent"]:hover {{ background: #534AB7; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {C['text2']}; }}
QPushButton[kind="ghost"]:hover {{ color: {C['text']}; background: rgba(255,255,255,0.06); }}
QPushButton[kind="danger"] {{ background: transparent; border: 1px solid #791F1F; color: {C['danger']}; }}

QToolButton#RailBtn {{ background: transparent; border: none; border-radius: 12px; color: #AFA9EC;
                      padding: 6px 2px; font-size: 11px; }}
QToolButton#RailBtn:hover {{ background: rgba(255,255,255,0.07); color: {C['text']}; }}
QToolButton#RailBtn:checked {{ background: #534AB7; color: {C['accent_text']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextBrowser {{
    background: {C['card2']}; border: 1px solid {C['line']}; border-radius: 8px; padding: 5px 8px;
    selection-background-color: {C['accent']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {C['accent']}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {C['card2']}; border: 1px solid {C['line']};
    selection-background-color: {C['accent_soft']}; outline: none; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid #5F5E5A; background: {C['card2']}; }}
QCheckBox::indicator:checked {{ background: {C['mint']}; border-color: {C['mint']}; }}

QFrame#Card {{ background: {C['card']}; border-radius: 14px; }}
QFrame#Metric {{ background: {C['card']}; border-radius: 12px; }}
QLabel#H1 {{ font-size: 20px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#Sub {{ color: {C['text2']}; font-size: 12px; }}
QLabel#Muted {{ color: {C['muted']}; font-size: 12px; }}
QLabel#MetricValue {{ font-size: 20px; font-weight: 600; }}
QLabel#SectionTitle {{ color: #AFA9EC; font-size: 12px; font-weight: 600; letter-spacing: 1px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #5F5E5A; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #5F5E5A; border-radius: 4px; min-width: 30px; }}

QTableWidget, QListWidget, QTreeWidget {{ background: {C['card']}; border: none; border-radius: 10px;
    gridline-color: {C['line']}; alternate-background-color: #30302D; outline: none; }}
QTableWidget::item, QListWidget::item {{ padding: 4px; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: {C['accent_soft']}; color: {C['accent_text']}; }}
QHeaderView::section {{ background: {C['card']}; color: {C['text2']}; border: none;
    border-bottom: 1px solid {C['line']}; padding: 6px; font-weight: 600; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {C['text2']}; padding: 6px 14px; border-radius: 12px; margin-right: 4px; }}
QTabBar::tab:selected {{ background: {C['accent_soft']}; color: {C['accent_text']}; }}
QProgressBar {{ background: {C['card2']}; border: none; border-radius: 4px; max-height: 6px; }}
QProgressBar::chunk {{ background: {C['mint']}; border-radius: 4px; }}
QStatusBar {{ background: {C['bg']}; color: {C['text2']}; }}
QMenu {{ background: {C['card2']}; border: 1px solid {C['line']}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['accent_soft']}; }}
QMessageBox, QDialog {{ background: {C['bg']}; }}
QSplitter::handle {{ background: {C['bg']}; }}
"""


def apply_mpl():
    import matplotlib as mpl
    from cycler import cycler
    mpl.rcParams.update({
        "figure.facecolor": C["card"], "axes.facecolor": C["card"], "savefig.facecolor": C["card"],
        "axes.edgecolor": "#6b6a64", "axes.labelcolor": C["text"], "text.color": C["text"],
        "xtick.color": C["text2"], "ytick.color": C["text2"], "grid.color": C["line"],
        "legend.frameon": False, "legend.labelcolor": C["text"],
        "axes.prop_cycle": cycler(color=SERIES), "axes.titleweight": "bold", "axes.titlesize": 12,
        "axes.spines.top": False, "axes.spines.right": False,
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"],
        "axes.unicode_minus": False, "figure.dpi": 100, "lines.linewidth": 1.8,
        "image.cmap": "RdBu_r",
    })
