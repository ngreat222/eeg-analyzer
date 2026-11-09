"""启动时的环境设置：必须在导入 mne / matplotlib / neurora 之前调用 setup()。"""
import os
import sys
import warnings


def setup():
    # Qt 绑定统一用 PySide6（MNE 的 Qt 波形浏览器通过 qtpy 读取这个变量）
    os.environ.setdefault("QT_API", "pyside6")
    os.environ.setdefault("MNE_BROWSER_BACKEND", "qt")
    # 打包后没有控制台，tqdm 等库往 None 写会崩溃，先放一个占位流，界面启动后再接到日志面板
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    import matplotlib

    # 静态图在后台线程里用 Agg 画，再嵌进 Qt 画布；交互式波形浏览器用 mne-qt-browser
    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    matplotlib.rcParams["figure.dpi"] = 100

    # inverted_encoding 0.2.3 仍调用已从新版 scipy 移除的 scipy.signal.gaussian
    import scipy.signal
    import scipy.signal.windows
    if not hasattr(scipy.signal, "gaussian"):
        scipy.signal.gaussian = scipy.signal.windows.gaussian

    warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    # mne-qt-browser 关闭窗口时 PySide6 的无害提示
    warnings.filterwarnings("ignore", message=".*Failed to disconnect.*", category=RuntimeWarning)

    import mne
    mne.set_log_level("INFO")
    try:
        mne.viz.set_browser_backend("qt", verbose=False)
    except Exception:  # 没装 mne-qt-browser 时退回 matplotlib 浏览器
        mne.viz.set_browser_backend("matplotlib", verbose=False)


def resource_path(*parts):
    """程序自带资源（示例数据等）的路径，兼容 PyInstaller 打包后的目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)
