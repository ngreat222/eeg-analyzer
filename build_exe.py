"""把脑电分析软件打包成 Windows 程序（PyInstaller，文件夹模式）。

用法（在本文件夹里）：
    D:\\miniconda\\python.exe build_exe.py                    只打包程序文件夹
    D:\\miniconda\\python.exe build_exe.py --installer        再做成一个安装程序 脑电分析软件-安装.exe
    D:\\miniconda\\python.exe build_exe.py --installer-only   只用已打包的文件夹做安装程序

输出：D:\\脑电分析软件\\脑电分析软件.exe（整个文件夹一起拷贝才能运行）。
打包放在 OneDrive 外面，避免几百 MB 的程序文件被同步。
"""
import os
import shutil
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.environ.get("EEG_DIST", r"D:\脑电分析软件")
WORK = os.environ.get("EEG_WORK", r"D:\脑电分析软件_build")
NAME = "脑电分析软件"

# 用不到、但会被 MNE 等库的可选导入顺带拉进来的大包
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "tensorflow", "keras", "jax", "jaxlib",
    "vtk", "vtkmodules", "pyvista", "pyvistaqt", "mayavi", "numba", "llvmlite",
    "IPython", "ipykernel", "ipywidgets", "jupyter", "jupyter_client", "jupyter_core", "notebook",
    "nbformat", "nbconvert", "PyQt5", "PyQt6", "PySide2", "plotly", "pyarrow", "dask", "bokeh",
    "sympy", "cupy", "tkinter", "_tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtMultimedia",
    "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "pytest", "sphinx", "docutils", "black", "pylint",
]

COLLECT_ALL = ["mne", "mne_qt_browser", "qtawesome", "neurora", "inverted_encoding", "pyqtgraph", "edfio",
               "eeglabio", "pybv", "h5io", "lazy_loader", "picard", "mffpy", "pymatreader", "curryreader", "antio",
               "pymef", "openpyxl", "xlrd", "xmltodict"]
HIDDEN = ["sklearn.svm", "sklearn.model_selection", "sklearn.decomposition", "sklearn.preprocessing",
          "sklearn.utils._typedefs", "sklearn.neighbors._partition_nodes", "scipy.io.matlab",
          "scipy.special.cython_special", "h5py", "skimage.measure", "pingouin", "qtpy",
          "matplotlib.backends.backend_qtagg", "matplotlib.backends.backend_agg"]


def _app_running():
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {NAME}.exe", "/NH"], capture_output=True,
                             text=True, errors="ignore").stdout
    except OSError:
        return False
    return f"{NAME}.exe" in out


def main():
    if _app_running():
        print(f"{NAME} 正在运行，请先关闭它再打包（否则旧文件被占用，打包会失败）。")
        return 1
    sep = os.pathsep
    args = [
        os.path.join(HERE, "main.py"),
        "--name", NAME,
        "--noconfirm", "--clean", "--windowed",
        "--distpath", DIST, "--workpath", WORK, "--specpath", WORK,
        "--icon", os.path.join(HERE, "assets", "icon.ico"),
        "--add-data", f"{os.path.join(HERE, 'sample_data')}{sep}sample_data",
        "--add-data", f"{os.path.join(HERE, 'assets')}{sep}assets",
        "--paths", HERE,
        "--contents-directory", "_internal",
    ]
    # PyInstaller 的 mne 钩子会漏掉部分标准电极模板（colin27 / fsaverage / spherical）和图标，显式补上
    import mne
    mne_dir = os.path.dirname(mne.__file__)
    for sub in (("channels", "data", "montages"), ("icons",)):
        args += ["--add-data", f"{os.path.join(mne_dir, *sub)}{sep}{'/'.join(('mne',) + sub)}"]
    for m in COLLECT_ALL:
        args += ["--collect-all", m]
    for m in HIDDEN:
        args += ["--hidden-import", m]
    for m in EXCLUDES:
        args += ["--exclude-module", m]
    PyInstaller.__main__.run(args)

    out = os.path.join(DIST, NAME)
    for f in ("使用说明.md",):
        src = os.path.join(HERE, f)
        if os.path.exists(src):
            shutil.copy(src, out)
    print(f"\n完成：{os.path.join(out, NAME + '.exe')}")
    if "--installer" in sys.argv:
        return make_installer(out)
    return 0


ISCC = [os.environ.get("ISCC", ""), r"D:\tools\InnoSetup\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe", r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"]
INSTALLER_OUT = os.environ.get("EEG_SETUP_OUT", r"D:\脑电分析软件_安装包")


def make_installer(dist=None):
    """把打包好的程序文件夹做成单个安装程序：脑电分析软件-安装.exe（需要 Inno Setup）。"""
    import subprocess
    sys.path.insert(0, HERE)
    from eegapp import __version__
    dist = dist or os.path.join(DIST, NAME)
    iscc = next((p for p in ISCC if p and os.path.exists(p)), None)
    if not iscc:
        print("没找到 Inno Setup 的 ISCC.exe，跳过安装包。可以从 https://jrsoftware.org 安装后再运行。")
        return 1
    r = subprocess.run([iscc, "/Q", f"/DMyAppVersion={__version__}", f"/DDistDir={dist}",
                        f"/DOutDir={INSTALLER_OUT}", os.path.join(HERE, "installer.iss")])
    if r.returncode == 0:
        print(f"安装包：{os.path.join(INSTALLER_OUT, NAME + '-安装.exe')}")
    return r.returncode


if __name__ == "__main__":
    if "--installer-only" in sys.argv:
        sys.exit(make_installer())
    sys.exit(main())
