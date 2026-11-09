"""界面主题：Catppuccin Mocha（https://catppuccin.com）。

界面 QSS、导航图标、日志、matplotlib 图表都从这里取色；换主题只需改 MOCHA 这一张表。
"""

# Catppuccin Mocha 官方色板
MOCHA = dict(
    crust="#11111b", mantle="#181825", base="#1e1e2e",
    surface0="#313244", surface1="#45475a", surface2="#585b70",
    overlay0="#6c7086", overlay1="#7f849c", overlay2="#9399b2",
    subtext0="#a6adc8", subtext1="#bac2de", text="#cdd6f4",
    lavender="#b4befe", blue="#89b4fa", sapphire="#74c7ec", sky="#89dceb", teal="#94e2d5",
    green="#a6e3a1", yellow="#f9e2af", peach="#fab387", maroon="#eba0ac", red="#f38ba8",
    mauve="#cba6f7", pink="#f5c2e7", flamingo="#f2cdcd", rosewater="#f5e0dc",
)
M = MOCHA

# 语义颜色（代码里只用这些名字）
C = dict(
    bg=M["base"],               # 主区背景
    rail=M["crust"],            # 左侧导航
    panel=M["mantle"],          # 右侧设置面板
    card="#262637",             # 卡片 / 图表底色（base 与 surface0 之间）
    field=M["surface0"],        # 输入框
    field_hover=M["surface1"],
    line=M["surface1"],         # 分隔线、边框
    text=M["text"],
    text2=M["subtext0"],
    muted=M["overlay1"],
    accent=M["mauve"],          # 主色
    on_accent=M["crust"],
    soft="#3a3150",             # 主色的浅底（次要按钮、标签）
    on_soft=M["mauve"],
    section=M["lavender"],      # 设置分组标题
    ok=M["green"],
    warn=M["peach"],
    danger=M["red"],
    nav_icon=M["overlay2"],
    log_bg=M["crust"],
    axis=M["overlay0"],         # 图里的 0 线
    sig=M["mauve"],             # 显著区间底色
    pos=M["yellow"],            # 时频图正向显著轮廓
    neg=M["teal"],              # 时频图负向显著轮廓
)

# 图表里各条件的颜色顺序
SERIES = [M["mauve"], M["green"], M["peach"], M["sky"], M["pink"], M["yellow"], M["blue"], M["red"]]

QSS = f"""
* {{ font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; color: {C['text']}; }}
QMainWindow, QWidget#Main {{ background: {C['bg']}; }}
QWidget#Rail {{ background: {C['rail']}; }}
QWidget#Side {{ background: {C['panel']}; }}
QToolTip {{ background: {C['field']}; color: {C['text']}; border: 1px solid {C['line']}; padding: 5px 9px; border-radius: 6px; }}

QPushButton {{ background: {C['field']}; border: none; border-radius: 10px; padding: 7px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {C['field_hover']}; }}
QPushButton:pressed {{ background: {M['surface2']}; }}
QPushButton:disabled {{ color: {C['muted']}; }}
QPushButton[kind="primary"] {{ background: {C['accent']}; color: {C['on_accent']}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {M['lavender']}; }}
QPushButton[kind="accent"] {{ background: {C['soft']}; color: {C['on_soft']}; font-weight: 600; }}
QPushButton[kind="accent"]:hover {{ background: #463c63; }}
QPushButton[kind="ghost"] {{ background: transparent; color: {C['text2']}; }}
QPushButton[kind="ghost"]:hover {{ color: {C['text']}; background: {C['field']}; }}
QPushButton[kind="danger"] {{ background: transparent; color: {C['danger']}; }}
QPushButton[kind="danger"]:hover {{ background: #3b2533; }}

QToolButton#RailBtn {{ background: transparent; border: none; border-radius: 12px; color: {C['nav_icon']};
                      padding: 6px 2px; font-size: 11px; }}
QToolButton#RailBtn:hover {{ background: {M['mantle']}; color: {C['text']}; }}
QToolButton#RailBtn:checked {{ background: {M['surface0']}; color: {C['accent']}; font-weight: 600; }}
QToolButton {{ border: none; background: transparent; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextBrowser {{
    background: {C['field']}; border: 1px solid transparent; border-radius: 8px; padding: 6px 9px;
    selection-background-color: {C['accent']}; selection-color: {C['on_accent']}; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {C['line']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {C['accent']}; }}
QLineEdit[placeholderText] {{ color: {C['text']}; }}
QAbstractSpinBox, QComboBox, QLineEdit {{ min-width: 40px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {C['field']}; border: 1px solid {C['line']}; border-radius: 8px;
    selection-background-color: {C['soft']}; selection-color: {C['on_soft']}; outline: none; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {M['surface2']}; background: {C['field']}; }}
QCheckBox::indicator:hover {{ border-color: {C['accent']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}

QFrame#Card {{ background: {C['card']}; border-radius: 14px; }}
QFrame#Metric {{ background: {C['card']}; border-radius: 12px; }}
QLabel#H1 {{ font-size: 20px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#Sub {{ color: {C['text2']}; font-size: 12px; }}
QLabel#Muted {{ color: {C['muted']}; font-size: 12px; }}
QLabel#MetricValue {{ font-size: 20px; font-weight: 600; color: {M['text']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {M['surface1']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {M['surface2']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {M['surface1']}; border-radius: 4px; min-width: 30px; }}

QTableWidget, QListWidget, QTreeWidget {{ background: {C['card']}; border: none; border-radius: 10px;
    gridline-color: {C['line']}; alternate-background-color: #2b2c3e; outline: none; }}
QTableWidget::item, QListWidget::item {{ padding: 4px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {C['field']}; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: {C['soft']}; color: {C['on_soft']}; }}
QHeaderView::section {{ background: {C['card']}; color: {C['text2']}; border: none;
    border-bottom: 1px solid {C['line']}; padding: 7px; font-weight: 600; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {C['text2']}; padding: 6px 14px; border-radius: 10px; margin-right: 4px; }}
QTabBar::tab:selected {{ background: {C['soft']}; color: {C['on_soft']}; }}
QProgressBar {{ background: {C['field']}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 3px; }}
QStatusBar {{ background: {C['bg']}; color: {C['text2']}; }}
QMenu {{ background: {C['field']}; border: 1px solid {C['line']}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['soft']}; color: {C['on_soft']}; }}
QMessageBox, QDialog {{ background: {C['bg']}; }}
QSplitter::handle {{ background: {C['bg']}; }}
"""


DIVERGING = [M["sky"], "#5d7bc9", C["card"], "#c86b86", M["peach"]]


def apply_mpl():
    import matplotlib as mpl
    from cycler import cycler
    from matplotlib.colors import LinearSegmentedColormap
    if "mocha_div" not in mpl.colormaps:
        mpl.colormaps.register(LinearSegmentedColormap.from_list("mocha_div", DIVERGING))
    mpl.rcParams.update({
        "figure.facecolor": C["card"], "axes.facecolor": C["card"], "savefig.facecolor": C["card"],
        "axes.edgecolor": M["surface2"], "axes.labelcolor": C["text"], "text.color": C["text"],
        "xtick.color": C["text2"], "ytick.color": C["text2"], "grid.color": C["line"],
        "legend.frameon": False, "legend.labelcolor": C["text"],
        "axes.prop_cycle": cycler(color=SERIES), "axes.titleweight": "bold", "axes.titlesize": 12,
        "axes.spines.top": False, "axes.spines.right": False,
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"],
        "axes.unicode_minus": False, "figure.dpi": 100, "lines.linewidth": 1.8,
        "image.cmap": "RdBu_r",
    })
