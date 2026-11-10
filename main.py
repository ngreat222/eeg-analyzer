"""脑电分析软件入口。直接运行：D:\\miniconda\\python.exe main.py"""
import multiprocessing
import sys


def main():
    multiprocessing.freeze_support()
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        from eegapp import selftest
        sys.exit(selftest.run(sys.argv[2]))
    from eegapp import compat
    compat.setup()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QSplashScreen
    from PySide6.QtGui import QColor, QFont, QPixmap

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("脑电分析软件")
    icon = compat.resource_path("assets", "icon.icns" if sys.platform == "darwin" else "icon.ico")
    import os
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))

    # 对话框里的“确定 / 取消”等按钮用中文
    from PySide6.QtCore import QLibraryInfo, QTranslator
    tr = QTranslator(app)
    if tr.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)

    pm = QPixmap(440, 170)
    from eegapp.theme import C
    pm.fill(QColor(C["rail"]))
    splash = QSplashScreen(pm)
    f = QFont()
    f.setFamilies(["Microsoft YaHei UI", "PingFang SC", "Hiragino Sans GB"])
    f.setPointSize(12)
    splash.setFont(f)
    splash.showMessage("脑电分析软件\n正在加载分析引擎 …", Qt.AlignCenter, QColor(C["text"]))
    splash.show()
    app.processEvents()

    from eegapp.ui.common import install_log_capture
    from eegapp.ui.main_window import MainWindow
    install_log_capture()
    w = MainWindow()
    w.show()
    splash.finish(w)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
