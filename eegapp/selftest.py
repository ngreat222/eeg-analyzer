"""打包后的自检：脑电分析软件.exe --selftest 输出文件

在一个临时项目里走完整流程：原始数据预处理（含 ICA）、合成多被试项目、ERP / 时频 / 解码 / IEM / RSA、
报告导出、界面构建，确认打包时没有漏掉模块或数据文件。
"""
import os
import shutil
import tempfile
import time
import traceback


def report_review(app, folder):
    import csv
    from datetime import datetime
    from pathlib import Path
    from unittest.mock import patch
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QLabel
    from .core import analysis, project as pj
    from .ui import report_page
    from .ui.main_window import MainWindow
    from .ui.widgets import Section

    folder = Path(folder)
    p = pj.make_demo_project(str(folder / "report-project"), n_subs=2, n_per_cond=1, log=lambda _: None)
    w = MainWindow(settings=QSettings(str(folder / "report.ini"), QSettings.IniFormat))
    try:
        w.set_project(p, quiet=True)
        w.goto("ERP")
        page = w.pages["ERP"]
        page.scope.setCurrentIndex(page.scope.findData("sub01"))
        def compute():
            result = []
            task = page.go()
            task.observed = True
            task.signals.settled.connect(lambda res, err: result.append((res, err)))
            deadline = time.monotonic() + 60
            while not result and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert result and not result[0][1], result
            return result[0][0]
        compute()
        latest = compute()
        assert len(w.reports) == 1 and w.reports[0][1] is latest
        page.m0.setValue(.25)
        compute()
        page.scope.setCurrentIndex(page.scope.findData("sub02"))
        second_record = compute()
        assert len(w.reports) == 3 and second_record.params["范围"] == "sub02"
        w.goto("报告")
        report = w.pages["报告"]
        columns, rows = ["条件", "剔除,原因"], [["文本,图像", "眨眼\n漂移"]]
        stage = analysis.Result("分段", "本条记录分段", table=(columns, rows))
        steps = [(datetime.now(), stage), (datetime.now(), second_record)]
        with patch.object(report_page, "fig_pixmap", wraps=report_page.fig_pixmap) as draw:
            report.show_items(steps, "目标记录")
            assert draw.call_count == 0
            root = report.scroll.widget()
            assert any("按分段零点对齐" in lbl.text() for lbl in root.findChildren(QLabel))
            next(s for s in root.findChildren(Section) if s.toggle.text() == "参数与图表").toggle.click()
            assert draw.call_count == 1
        with patch.object(report_page.QFileDialog, "getExistingDirectory", return_value=str(folder)), \
                patch.object(report, "run", side_effect=lambda _, work, done: done(work())), \
                patch.object(report, "_done"), patch.object(report_page, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 17, 11, 30)
            report.export()
            first_file = report.refresh_last_export()
            report.show_items(steps[:1], "仅分段记录")
            report.export()
            last_file = report.refresh_last_export()
        assert first_file != last_file and first_file.is_file() and last_file.is_file()
        exported = first_file.read_text(encoding="utf-8")
        assert ("目标记录 · 分析报告" in exported and "按分段零点对齐" in exported
                and "1 项分析 · 1 个处理步骤" in exported and "sub01" not in exported)
        assert "仅分段记录 · 分析报告" in last_file.read_text(encoding="utf-8")
        with next((last_file.parent / "数据").glob("*.csv")).open(encoding="utf-8-sig", newline="") as fh:
            assert list(csv.reader(fh)) == [columns, *rows]
        other = pj.Project(str(folder / "another-project"))
        other.save()
        w.set_project(other, quiet=True)
        assert not w.reports and not report.displayed_items and report.refresh_last_export() is None
        w.set_project(p, quiet=True)
        assert len(w.reports) == len(report.displayed_items) == 3
        w.close()
        w = MainWindow(settings=QSettings(str(folder / "report.ini"), QSettings.IniFormat))
        w.set_project(pj.Project.load(p.folder), quiet=True)
        w.goto("报告")
        report = w.pages["报告"]
        assert not report.displayed_items and not report.last_export.isHidden()
        with patch.object(report_page.QDesktopServices, "openUrl", return_value=True) as open_html:
            report.last_export.click()
            assert Path(open_html.call_args.args[0].toLocalFile()) == last_file
    finally:
        w.close()
    return "相同设置刷新结果，保留不同被试和时间窗；记录报告按当前预览导出并独立于其他项目"


def ica_graph_review(app, folder):
    from pathlib import Path
    from unittest.mock import patch
    import mne
    import numpy as np
    from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QInputDialog, QListWidget, QMessageBox
    from . import compat
    from .core import project as pj
    from .ui.main_window import MainWindow

    project = pj.Project(str(Path(folder) / "ica-graph"))
    sample = compat.resource_path("sample_data", "eeglab_data.set")
    rec = pj.add_raw_files(project, [sample], subject="graph-review")[0]
    other = pj.add_raw_files(project, [sample], subject="other-record")[0]
    subject, _ = project.get_rec(rec.id)
    subject.locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
    subject.rename_from_locs = True
    project.pipeline = pj.Pipeline(ica_n="6", ica_auto_eog=False, notch=0)
    project.save()
    window = MainWindow(settings=QSettings(str(Path(folder) / "ica-graph.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.resize(1200, 800)
    window.show()
    window.set_project(project, quiet=True)
    window.goto("预处理", rec.id)
    page = window.pages["预处理"]

    def settled():
        end = time.monotonic() + 120
        while window.runner.busy and time.monotonic() < end:
            app.processEvents()
            time.sleep(.01)
        app.processEvents()
        assert not window.runner.busy

    def persisted():
        return pj.Project.load(project.folder).get_rec(rec.id)[1]

    def click_map(index, title=False):
        fig, axes = next((f, axes) for f, axes in page.component_maps if index in axes.values())
        ax = next(ax for ax, i in axes.items() if i == index)
        canvas = fig.canvas
        canvas.draw()
        box = ax.title.get_window_extent(canvas.get_renderer()) if title else ax.bbox
        x, y = box.get_points().mean(axis=0) / canvas.device_pixel_ratio
        QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(round(x), round(canvas.height() - y)))
        app.processEvents()

    def verify(marks):
        actual = persisted()
        assert actual.ica_manual and actual.reviewed_ica == pj.ica_identity(page.ica)
        assert set(actual.ica_exclude) == page.marked_components() == set(page.ica.exclude) == marks
        for _, axes in page.component_maps:
            assert all(("排除" in ax.title.get_text()) == (idx in marks) for ax, idx in axes.items())

    def open_wave(action):
        action()
        browser = window._windows[-1]
        browser.resize(1024, 720)
        browser.showNormal()
        browser.raise_()
        app.processEvents()
        if browser.mne.n_channels > 8:
            browser.change_nchan(step=8 - browser.mne.n_channels)
        return browser

    def click_channel(browser, index):
        name = str(browser.mne.ch_names[browser.mne.picks[index]])
        axis = browser.mne.channel_axis
        end = time.monotonic() + 8
        while name not in axis.ch_texts and time.monotonic() < end:
            axis.repaint()
            browser.mne.view.grab()
            QTest.qWait(25)
        assert name in axis.ch_texts, (name, list(axis.ch_texts))
        browser._click_ch_name(index, 1)

    def close_wave(browser):
        browser.close()
        app.processEvents()

    def process():
        page.process_one()
        settled()
        assert persisted().status == pj.REC_DONE
        with mne.io.read_raw_fif(project.path(rec.clean_file), verbose=False) as clean:
            return clean.get_data()

    try:
        with patch.object(QMessageBox, "warning") as warning, patch.object(QMessageBox, "information"):
            page.fit_ica()
            settled()
            click_map(1)
            verify({1})
            click_map(1, title=True)
            verify(set())
            browser = open_wave(page.browse_components)
            click_channel(browser, 1)
            click_map(2)
            close_wave(browser)
            verify({1, 2})
            browser = open_wave(page.browse_components)
            click_channel(browser, 1)
            click_channel(browser, 2)
            close_wave(browser)
            verify(set())
            # Processing must consume a still-open source window's final mark.
            browser = open_wave(page.browse_components)
            click_channel(browser, 1)
            process()
            assert browser not in window._windows and persisted().processed_ica["exclude"] == [1]
            click_map(1)
            keep = process()
            click_map(1)
            assert persisted().status == pj.REC_RAW
            assert np.max(np.abs(keep - process())) > 1e-9
            browser = open_wave(page.browse)
            channel = str(browser.mne.ch_names[browser.mne.picks[0]])
            click_channel(browser, 0)
            browser._fake_keypress("a")
            browser._fake_click((.31, .65), add_points=[(.37, .65), (.42, .65)], kind="drag")
            # A non-modal viewer still owns its original recording after a selection change.
            window.goto("预处理", other.id)
            close_wave(browser)
            actual = persisted()
            assert actual.bads == [channel] and len(actual.bad_segments) == 1
            assert actual.bad_segments[0][2] == "手动" and not other.bads and not other.bad_segments
            window.goto("预处理", rec.id)
            page.preview()
            settled()
            browser = open_wave(page.browse)
            click_channel(browser, 0)
            browser._fake_keypress("a")
            browser._fake_click((.36, .65), button=3)
            close_wave(browser)
            assert not persisted().bads and not persisted().bad_segments
            old_fit = persisted().reviewed_ica
            page.ica_n.setText("3")
            page.fit_ica()
            settled()
            assert page.needs_component_review(rec)
            page._save_pipe()
            assert persisted().reviewed_ica == old_fit
            page.confirm_components()
            verify(set())
            assert not page.needs_component_review(rec)
            page.excl.setText("IC-001")
            page.confirm_components()
            verify({1})
            page.excl.setText("1.7")
            page.confirm_components()
            assert warning.called and persisted().ica_exclude == [1]
            warning.reset_mock()
            page._select()
            # Direct edits persist even when leaving the page without starting a job.
            page.harm.click()
            harmonic = page.harm.isChecked()
            page.open_step("坏段")
            page.segment_limit.setFocus()
            page.segment_limit.stepUp()
            page.segment_limit.clearFocus()
            threshold = page.segment_limit.value()
            assert threshold > 150
            window.goto("事件")
            window.goto("预处理", rec.id)
            disk = pj.Project.load(project.folder)
            assert disk.pipeline.harmonics == harmonic and disk.pipeline.segment_limit_uv == threshold
            assert page.segment_limit.value() == threshold
            from .ui.segment_editor import SegmentEditor
            def mark_dialog(mode):
                if mode == "manual":
                    page.mark_segments_btn.click()
                else:
                    page.review_quality("detect")
                settled()
                editor = app.activeModalWidget()
                assert isinstance(editor, SegmentEditor)
                return editor

            editor = mark_dialog("manual")
            box = editor.wave.plot_rect()
            points = [QPoint(round(box.left()+frac*box.width()), round(box.center().y())) for frac in (.2,.35)]
            expected = [[editor.wave.time_at(point.x()) for point in points] + ["手动"]]
            QTest.mousePress(editor.wave, Qt.LeftButton, Qt.NoModifier, points[0])
            QTest.mouseMove(editor.wave, points[1])
            QTest.mouseRelease(editor.wave, Qt.LeftButton, Qt.NoModifier, points[1])
            assert editor.rows == [(*expected[0],True)] and not persisted().bad_segments
            editor.delete_btn.click()
            assert not editor.rows
            editor.undo_btn.click()
            editor.accept()
            assert persisted().bad_segments == expected
            editor = mark_dialog("detect")
            assert [list(r[:3]) for r in editor.rows if r[3]] == expected
            editor.reject()
            assert persisted().bad_segments == expected

            def edit_order(confirm):
                def review():
                    dialog = app.activeModalWidget()
                    assert isinstance(dialog, QDialog)
                    steps = dialog.findChild(QListWidget)
                    steps.insertItem(1, steps.takeItem(0))
                    dialog.accept() if confirm else dialog.reject()
                QTimer.singleShot(0, review)
                page.edit_order()

            order = list(disk.pipeline.order)
            edit_order(False)
            assert pj.Project.load(project.folder).pipeline.order == order
            edit_order(True)
            assert pj.Project.load(project.folder).pipeline.order[:2] == order[1::-1]
            with patch.object(QInputDialog, "getMultiLineText", return_value=("4秒至5秒，复核", True)):
                editor = mark_dialog("detect")
                editor.text_edit()
                editor.accept()
            assert persisted().bad_segments == [[4, 5, "复核"]]
            from .ui.preprocess_page import ChannelDialog

            def choose_channel(dialog):
                dialog._all(True)
                dialog.apply_all.setChecked(False)
                dialog.table.item(0, 0).setCheckState(Qt.Unchecked)
                return QDialog.Accepted

            with patch.object(ChannelDialog, "exec", return_value=QDialog.Rejected):
                page.check_channels()
                settled()
            assert persisted().non_eeg is None
            with patch.object(ChannelDialog, "exec", choose_channel):
                page.check_channels()
                settled()
            assert len(persisted().non_eeg) == 1 and other.non_eeg is None
            page.open_step("坏导")
            page.bads.setFocus()
            QTest.keyClicks(page.bads, "Fp1")
            QTest.keyClick(page.bads, Qt.Key_Return)
            assert persisted().bads == ["Fp1"] and not other.bads
            with patch.object(project, "save", side_effect=OSError("测试写入失败")):
                page.harm.click()
            assert warning.called and persisted().bads == ["Fp1"]
            assert page.harm.isChecked() == project.pipeline.harmonics == harmonic
            warning.reset_mock()
            # Hold these callbacks while changing records to exercise late worker results.
            callbacks = []
            with patch.object(page, "run", side_effect=lambda title, work, done: callbacks.append(done)):
                page.preview()
                page.detect_bads()
            window.goto("预处理", other.id)
            for callback in callbacks:
                callback(None)
            assert len(callbacks) == 2 and page.raw is None and not other.bads
            assert not warning.called, warning.call_args_list
    finally:
        for browser in list(window._windows):
            browser.close()
        window.close()
    return "头皮图与波形选择、撤销、跨记录保存及重新处理后的 FIF 均已核对"


def event_wave_review(app, folder):
    from pathlib import Path
    from unittest.mock import patch
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QMessageBox
    from . import compat
    from .core import events, project as pj
    from .ui.main_window import MainWindow

    folder = Path(folder)
    project = pj.Project(str(folder / "事件波形验收"))
    sample = compat.resource_path("sample_data", "eeglab_data.set")
    target, other = [pj.add_raw_files(project, [sample], subject=n)[0] for n in ("标记目标", "另一记录")]
    table = events.make([1, 2000], ["原标记", "窗口外"])
    table["实验编号"] = ["001", "002"]
    project.save_events(target, table)
    project.save_events(other, events.empty())
    window = MainWindow(settings=QSettings(str(folder / "event-wave.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.show()
    window.set_project(project, quiet=True)
    window.goto("事件", target.id)
    page = window.pages["事件"]

    def open_wave():
        window.goto("事件", target.id)
        page.mark_on_wave()
        app.processEvents()
        return window._windows[-1]

    def saved():
        reopened = pj.Project.load(project.folder)
        return reopened.events(reopened.get_rec(target.id)[1])

    try:
        with patch.object(page, "run", side_effect=lambda title, read, show: show(read())), \
                patch.object(QMessageBox, "information"), patch.object(QMessageBox, "warning") as warning:
            browser = open_wave()
            before = Path(project.json_path).read_bytes()
            browser.close()
            assert before == Path(project.json_path).read_bytes() and saved().equals(table)
            browser = open_wave()
            assert browser.mne.annotation_mode
            browser.mne.fig_annotation._add_description("拖选")
            browser._fake_click((.3, .65), add_points=[(.35, .65), (.41, .65)], kind="drag")
            assert "拖选" in browser.mne.inst.annotations.description
            window.goto("事件", other.id)
            browser.close()
            after = saved()
            assert list(after.description) == ["原标记", "拖选", "窗口外"] and list(after["实验编号"]) == ["001", "", "002"]
            assert project.events(other).empty and not warning.called
            browser = open_wave()
            external = events.merge(after, events.make([14], ["编辑期间新增"]))
            project.save_events(target, external)
            browser.mne.inst.annotations.append(18, 1, "过期图")
            browser.close()
            assert warning.called and "编辑期间新增" in list(saved().description) and "过期图" not in list(saved().description)
    finally:
        window.close()
    return "关闭不改动时逐字节保持原表；拖出的事件、附加列和窗口外行重开可读；旧窗口无法覆盖新事件"


def run(out_path):
    lines = []

    def check(name, fn):
        t0 = time.time()
        try:
            info = fn()
            lines.append(f"OK   {name} ({time.time() - t0:.1f}s) {info or ''}")
        except Exception:  # noqa: BLE001
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")

    from . import compat
    compat.setup()
    import mne
    mne.set_log_level("ERROR")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from .core import analysis, project as pj
    tmp = tempfile.mkdtemp(prefix="eeg_selftest_")
    quiet = lambda m: None  # noqa: E731
    st = {}

    def raw_pipeline():
        p = pj.Project(os.path.join(tmp, "raw"))
        r = pj.add_raw_files(p, [compat.resource_path("sample_data", "eeglab_data.set")])[0]
        s = p.get_rec(r.id)[0]
        s.locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
        s.rename_from_locs = True
        p.pipeline = pj.Pipeline(notch=60, tmin=-1, tmax=2, bl0=-0.5, bl1=0, reject_uv=200, ica_n="15")
        ep = pj.run_pipeline(p, s, log=quiet)
        p.save()
        return f"{len(ep)} epochs, ICA 去除 {r.ica_exclude}"
    check("原始数据预处理（读取、电极、滤波、ICA、分段）", raw_pipeline)
    check("ICA 地形图与波形人工标记写回", lambda: ica_graph_review(app, tmp))
    check("事件波形标记保存与重开", lambda: event_wave_review(app, tmp))

    def event_tools():
        from .core import events as ev
        import pandas as pd
        tab = pd.DataFrame({"latency": [1.0, 2.5, 4.0], "marker_value": [901, 902, 1]})
        df = ev.table_to_events(tab, ev.guess_columns(tab), 128, rec_duration=10)
        mixed = pd.DataFrame({"事件名称": ["目标", "反应"], "开始时间（毫秒）": [250, "01:02.5"],
                              "持续时间(s)": [1.5, "200ms"]})
        events = ev.table_to_events(mixed, ev.guess_columns(mixed), 128)
        assert list(events.onset) == [.25, 62.5] and list(events.duration) == [1.5, .2]
        manual = ev.parse_manual("250ms=10；01:02.5 反应")
        assert list(manual.onset) == [.25, 62.5] and list(manual.description) == ["10", "反应"]
        df, _ = ev.apply_rules(df, ev.parse_rules("9xx=刺激"))
        df = ev.merge(df, ev.fixed_interval(0, 6, 2, "片段"))
        return " ".join(f"{n}×{c}" for n, c, _ in ev.summary(df))
    check("事件工具（表格导入、改名、固定间隔）", event_tools)

    # ---- 界面里每个可选项都实际跑一遍，防止“选项能选、运行时缺组件”
    def optional_packages():
        import importlib
        need = ["picard", "sklearn", "mffpy", "pymatreader", "curryreader", "antio", "pymef", "h5py", "edfio",
                "eeglabio", "pybv", "openpyxl", "xlrd", "pingouin", "neurora", "inverted_encoding", "mne_qt_browser"]
        missing = []
        for m in need:
            try:
                importlib.import_module(m)
            except Exception as e:  # noqa: BLE001
                missing.append(f"{m}（{type(e).__name__}）")
        if missing:
            raise RuntimeError("缺少：" + "，".join(missing))
        return f"{len(need)} 个"
    check("可选组件都已打包", optional_packages)

    def cjk_fonts():
        """图表和界面都要找得到中文字体，否则中文会显示成方框（Windows 微软雅黑 / macOS 苹方、冬青黑体）。"""
        import matplotlib
        from matplotlib import font_manager as fm
        from PySide6.QtGui import QFontDatabase
        have = {f.name for f in fm.fontManager.ttflist}
        mpl_font = next((n for n in matplotlib.rcParams["font.sans-serif"] if n in have), None)
        if not mpl_font or mpl_font == "DejaVu Sans":
            raise RuntimeError("图表找不到中文字体")
        qt = set(QFontDatabase.families())
        qt_font = next((n for n in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB")
                        if n in qt), None)
        if not qt_font:
            raise RuntimeError("界面找不到中文字体")
        return f"图表 {mpl_font}，界面 {qt_font}"
    check("中文字体", cjk_fonts)

    def ica_methods():
        import numpy as np
        from .core import single
        rng = np.random.default_rng(0)
        info = mne.create_info([f"EEG{i}" for i in range(8)], 100.0, "eeg")
        raw = mne.io.RawArray(rng.laplace(size=(8, 3000)) * 1e-5, info, verbose=False)
        out = []
        for m in ("fastica", "infomax", "picard"):
            ica, _ = single.fit_ica(raw, 5, m, 1.0, 97, log=quiet)
            out.append(f"{m} {ica.n_components_}")
        return ", ".join(out)
    check("ICA 三种算法", ica_methods)

    def file_formats():
        import numpy as np
        import pandas as pd
        from .core import events as ev, reading
        rng = np.random.default_rng(1)
        info = mne.create_info(["Fz", "Cz", "Pz"], 128.0, "eeg")
        raw = mne.io.RawArray(rng.normal(size=(3, 1280)) * 1e-5, info, verbose=False)
        raw.set_annotations(mne.Annotations([1.0, 3.0], [0, 0], ["a", "b"]))
        d = os.path.join(tmp, "fmt")
        os.makedirs(d, exist_ok=True)
        ok = []
        for ext in ("edf", "vhdr", "set", "fif"):
            f = os.path.join(d, f"t_raw.{ext}" if ext == "fif" else f"t.{ext}")
            if ext == "fif":
                raw.save(f, overwrite=True, verbose=False)
            else:
                mne.export.export_raw(f, raw, overwrite=True, verbose=False)
            r, _ = reading.read_raw_any(f, log=quiet)
            ok.append(f"{ext}:{len(ev.from_raw(r))}")
        tab = pd.DataFrame({"time": [1.0, 2.0], "code": [1, 2]})
        for ext in ("xlsx", "csv", "txt"):
            f = os.path.join(d, f"ev.{ext}")
            if ext == "xlsx":
                tab.to_excel(f, index=False)
            else:
                tab.to_csv(f, index=False, sep="," if ext == "csv" else "\t")
            df = ev.table_to_events(ev.read_table(f), ev.guess_columns(ev.read_table(f)), 128, rec_duration=10)
            ok.append(f"{ext}:{len(df)}")
        return " ".join(ok)
    check("文件格式读写（EDF/BrainVision/EEGLAB/FIF，事件表 xlsx/csv/txt）", file_formats)

    def all_tests():
        import numpy as np
        from .core import stats
        rng = np.random.default_rng(2)
        mk = lambda k: [rng.normal(i * 0.3, 1, (12, 20)) for i in range(k)]  # noqa: E731
        n = 0
        for level in ("group", "trial"):
            for k, conds in ((1, mk(1)), (2, mk(2)), (4, mk(4))):
                for t in stats.tests_for(k, level):
                    for c in stats.CORRECTIONS:
                        fl = (2, 2) if "两因素" in t else None
                        stats.run_time_test(conds, t, c, n_iter=50, factor_levels=fl, log=quiet)
                        n += 1
        return f"{n} 种组合"
    check("所有统计检验 × 多重比较校正", all_tests)
    check("标准电极模板", lambda: len(mne.channels.make_standard_montage("standard_1020").ch_names))

    def demo():
        st["p"] = pj.make_demo_project(os.path.join(tmp, "demo"), n_subs=4, n_per_cond=48, log=quiet)
        return st["p"].summary()["n"]
    check("合成演示项目", demo)
    C = ["First", "Repetition"]
    check("ERP 全组 + cluster", lambda: analysis.erp(st["p"], analysis.GROUP, C, "Pz", (-0.2, 0.8), (0.3, 0.5),
                                                  "cluster 置换检验（配对，双侧）", "不校正", n_iter=100,
                                                  log=quiet).metrics[0][1])
    check("ERP 单被试 + FDR", lambda: len(analysis.erp(st["p"], "sub01", C, "Pz", (-0.2, 0.8), (0.3, 0.5),
                                                     "独立样本 t 检验", "FDR", log=quiet).figures))
    check("时频 + cluster", lambda: analysis.tfr(st["p"], analysis.GROUP, C, "Oz", 4, 30, 4, 2, (-0.4, -0.1),
                                               (-0.2, 0.8), (8, 12), (0.2, 0.6), n_iter=50, decim=2,
                                               log=quiet).metrics[0][1])

    def dec():
        dd = analysis.decoding_data(st["p"], analysis.GROUP, "朝向", [str(i * 45) for i in range(8)], log=quiet)
        out = []
        for m in ("逐时间点解码", "跨时间泛化", "反向编码模型 IEM"):
            r = analysis.decode(dd, m, 3, 10, 10, 3, 1, n_iter=50, num_perm=100, log=quiet)
            out.append(r.metrics[0][1])
        r = analysis.rsa(dd, "环形距离（如朝向、位置）", "", 10, 10, n_iter=50, log=quiet)
        out.append(r.metrics[0][1])
        st["res"] = r
        return " / ".join(out)
    check("解码、跨时间泛化、IEM、RSA", dec)

    def single_subject():
        import numpy as np
        from PySide6.QtCore import QSettings
        from .ui.main_window import MainWindow
        project = pj.make_demo_project(os.path.join(tmp, "single"), n_subs=1, n_per_cond=32, log=quiet)
        name = project.subjects[0].name
        window = MainWindow(settings=QSettings(os.path.join(tmp, "single.ini"), QSettings.IniFormat))
        try:
            window.set_project(project, quiet=True)
            for key in ("ERP", "时频", "解码", "RSA"):
                window.goto(key)
                page = window.pages[key]
                assert page.scope.count() == 1 and page.scope.currentData() == name, key
                if key in ("解码", "RSA"):
                    assert not page.group_statistics.isEnabled(), key
            dd = analysis.decoding_data(project, name, "朝向", [str(i * 45) for i in range(8)], log=quiet)
            for method, array in (("逐时间点解码", "accuracy"), ("反向编码模型 IEM", "mae"), ("RSA", "similarity")):
                if method == "RSA":
                    res = analysis.rsa(dd, "线性距离", "", 20, 20, log=quiet)
                else:
                    res = analysis.decode(dd, method, 2, 20, 20, 2, 1, num_perm=100, log=quiet)
                assert res.arrays[array].shape[0] == 1 and np.isfinite(res.arrays[array]).all(), method
                assert all("显著时间段" not in title for title, _, _ in res.metrics), method
        finally:
            window.close()
        return "四个分析页默认选中单人；解码、IEM、RSA 输出有限的个体指标"
    check("单被试分析范围与个体结果", single_subject)

    def retained_conditions():
        import numpy as np
        from unittest.mock import patch
        from PySide6.QtCore import QSettings, Qt
        from .ui.main_window import MainWindow

        project = pj.Project(os.path.join(tmp, "retained"))
        subject = pj.Subject("sub01")
        project.subjects.append(subject)
        conditions = ["文本/刺激", "视频/刺激"]
        ids = {"剔除/伪迹": 1, conditions[0]: 2, conditions[1]: 3}
        data = np.random.default_rng(42).normal(0, 1e-6, (3, 1, 201))
        info = mne.create_info(["Cz"], 100, "eeg")
        epochs = mne.EpochsArray(data, info, events=np.array([[0, 0, 1], [300, 0, 2], [600, 0, 3]]),
                                 event_id=ids, tmin=-0.5, verbose=False)
        epochs.drop([0], reason="BAD_artifact", verbose=False)
        project.store_epochs(subject, epochs)
        project.save()
        project = pj.Project.load(project.folder)
        saved = project.epochs(subject.name)
        assert "剔除/伪迹" in saved.event_id and len(saved["剔除/伪迹"]) == 0
        window = MainWindow(settings=QSettings(os.path.join(tmp, "retained.ini"), QSettings.IniFormat))
        try:
            window.set_project(project, quiet=True)
            for key, prefix in (("ERP", "erp_"), ("时频", "tfr_")):
                window.goto(key)
                page = window.pages[key]
                names = page.conds.names()
                assert set(conditions + ["文本", "视频", "刺激"]) <= set(names), names
                assert not {"剔除/伪迹", "剔除", "伪迹"}.intersection(names), names
                selection = page.conds.checked()
                assert len(selection) == 2, selection
                assert set(saved[selection[0]].selection).isdisjoint(saved[selection[1]].selection), selection
                for i, name in enumerate(names):
                    page.conds.item(i).setCheckState(Qt.Checked if name in conditions else Qt.Unchecked)
                page.ch.setText("Cz")
                with patch.object(page, "run", side_effect=lambda title, work, done: work()):
                    result = page.go()
                for condition in conditions:
                    values = result.arrays[prefix + condition]
                    assert values.shape[0] == 1 and np.isfinite(values).all(), (key, condition)
                assert "significant" not in result.arrays and not any(k.startswith("mask_") for k in result.arrays)
                assert all("nan" not in str(metric).lower() for metric in result.metrics), result.metrics
        finally:
            window.close()
        return "重新打开分段后，ERP 和时频均按保留试次选取条件并生成单试次结果"
    check("ERP 与时频剔除空条件", retained_conditions)

    def rsa_options():
        from .core import advanced
        out = []
        dd = analysis.decoding_data(st["p"], analysis.GROUP, "朝向", [str(i * 45) for i in range(8)], log=quiet)
        for kind in ("环形距离（如朝向、位置）", "线性距离", "类别是否相同（0/1）"):
            for dist in advanced.RDM_METHODS:
                r = analysis.rsa(dd, kind, "", 20, 20, distance=dist, n_iter=20, log=quiet)
                out.append(r.metrics[0][1])
        return f"{len(out)} 种组合"
    check("RSA 模型 × 距离", rsa_options)

    def ui():
        from PySide6.QtCore import QSettings, Qt
        from PySide6.QtWidgets import QLabel
        from . import theme
        from .ui import main_window
        from .ui.report_page import export_report
        w = main_window.MainWindow(settings=QSettings(os.path.join(tmp, "ui.ini"), QSettings.IniFormat))
        w.setAttribute(Qt.WA_DontShowOnScreen)
        w.resize(1180, 740)
        w.show()
        w.goto("解码")
        w.ai_action.setChecked(True)
        w.assistant.edit.setPlainText("尚未发送的草稿")
        w.assistant.say("你", "请查看解码步骤")
        start_theme = theme.MODE
        for expected in ("light" if start_theme == "dark" else "dark", start_theme):
            w.toggle_theme()
            for _ in range(4):
                app.processEvents()
            assert w.isVisible() and w.stack.currentWidget() is w.pages["解码"]
            assert theme.MODE == expected and w.settings.value("theme") == expected
            assert w.assistant.edit.toPlainText() == "尚未发送的草稿"
            assert "请查看解码步骤" in w.assistant.chat.toPlainText()
            for lab in w.findChildren(QLabel):
                if lab.isVisible() and lab.wordWrap() and lab.text():
                    assert lab.height() >= lab.heightForWidth(lab.width()), lab.text()
        w.ai_action.setChecked(False)
        w.project = st["p"]  # 不经过 set_project，避免改写用户的“上次打开的项目”
        w.refresh()
        for name, _, _ in main_window.NAV:
            w.goto(name)
        w.pages["ERP"].view.show_result(st["res"])
        path = export_report(os.path.join(tmp, "report"), "自检", [(__import__("datetime").datetime.now(),
                                                                     st["res"])])
        b = st["p"].epochs("sub01").plot(show=False, block=False)
        name = type(b).__name__
        b.close()
        w.close()
        return f"{len(main_window.NAV)} 页, 报告 {os.path.getsize(path) // 1024} KB, 浏览器 {name}"
    check("界面、报告导出、Qt 波形浏览器", ui)
    check("报告范围、方法说明与复算", lambda: report_review(app, tmp))
    from . import agent_selftest
    check("助手 ICA 图片复核与选择性保存（模拟模型回复）", lambda: agent_selftest.ica_review(app, tmp))
    check("助手操作真实波形窗并保存标记", lambda: agent_selftest.waveform_assistant(app, tmp))
    check("AI 助手项目操作（模拟模型回复）", lambda: agent_selftest.run(app, tmp))
    check("AI 工作流时频参数修正到导出（模拟模型回复）", lambda: agent_selftest.tfr_parameter_recovery(app, tmp))
    from . import workspace_selftest
    check("独立处理、按记录分段与 ERP", lambda: workspace_selftest.single_steps(app, tmp))
    check("预处理流程与结果回看", lambda: workspace_selftest.check_preprocess_views(app, tmp))
    check("分析与分段控件持久化", lambda: workspace_selftest.check_saved_controls(app, tmp))
    check("AI 修改表格、勾选通道与保存处理顺序", lambda: workspace_selftest.check_form_edits(app, tmp))
    check("AI 操作工作窗口与事件预览（模拟模型回复）", lambda: workspace_selftest.run(app, tmp))

    ok = all(line.startswith("OK") for line in lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + f"\n\n{'全部通过' if ok else '有失败项'}\n")
    shutil.rmtree(tmp, ignore_errors=True)
    app.quit()
    return 0 if ok else 1
