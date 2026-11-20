"""Operate the controls of BIAP's current work page on the GUI thread."""
from functools import partial
from itertools import count
import os
from pathlib import Path
import re

from shiboken6 import isValid
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QItemSelectionModel, QPersistentModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QSpinBox, QTableWidget, QTabWidget, QWidget,
)

from ..core import events, workbench
from . import project_bridge as bridge
from .record_workflow import current_record


class WorkWindow:
    def __init__(self, panel):
        from .workspace_viewers import ViewerControls
        self.panel = panel
        self.host = panel.host
        self.project = panel.bound
        self.handles = {}
        self.item_controls = {}
        self.ids = count(1)
        self.dialogs = {}
        self.viewers = ViewerControls(self.host, self.project, self.confirm)

    def location(self):
        page = self.host.stack.currentWidget()
        key = next(name for name, item in self.host.pages.items() if item is page)
        record = current_record(self.host) if self.project else None
        return key, record, page.scope.currentData() if hasattr(page, "scope") else None

    def confirm(self, description):
        box = QMessageBox(QApplication.activeModalWidget() or self.host)
        box.setWindowTitle("助手操作预览")
        box.setTextFormat(Qt.PlainText)
        box.setText(description)
        apply = box.addButton("采用并继续", QMessageBox.AcceptRole)
        cancel = box.addButton("保留原状", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is apply

    def surface(self):
        modal = QApplication.activeModalWidget()
        if modal is not None and modal not in self.dialogs:
            if not self.can_fill(modal, self.host):
                raise ValueError("请先在当前窗口完成文件选择、授权或确认。")
            self.dialogs[modal] = {"manual": True}
            modal.finished.connect(lambda *_: self.dialogs.pop(modal, None))
        return modal or self.host.stack.currentWidget()

    def can_fill(self, dialog, opener):
        if not isinstance(dialog, QDialog) or isinstance(dialog, (QMessageBox, QFileDialog)):
            return False
        return self.owned_dialog(dialog, opener) and not any(
            edit.echoMode() != QLineEdit.Normal for edit in dialog.findChildren(QLineEdit))

    def owned_dialog(self, dialog, opener):
        owner = dialog.parentWidget()
        belongs = False
        while owner is not None:
            if owner is self.panel or isinstance(owner, (QMessageBox, QFileDialog)):
                return False
            belongs |= owner is opener
            owner = owner.parentWidget()
        return belongs and not any(word in dialog.windowTitle()
                                   for word in ("授权", "权限", "密码", "密钥", "模型连接", "操作预览"))

    def snapshot(self, offset=0):
        from .main_window import NAV_TEXT
        key, record, scope = location = self.location()
        page = self.surface()
        self.handles.clear()
        self.item_controls.clear()
        output = dict(项目=self.project.name if self.project else None, 页面=key, 当前记录=record, 分析范围=scope,
                      可打开页面={name: NAV_TEXT.get(name, name) for name in self.host.pages},
                      控件=[], 页面文字=[])
        if page in self.dialogs:
            output["对话框"] = page.windowTitle()
            output["可打开页面"] = {}
        elif hasattr(page, "group_picker"):
            output["参数组"] = [page.group_picker.itemText(i) for i in range(page.group_picker.count())]
        if page not in self.dialogs:
            output["子窗口"] = self.viewers.list()
        labels = {}
        for form in page.findChildren(QFormLayout):
            for i in range(form.rowCount()):
                lab = form.itemAt(i, QFormLayout.LabelRole)
                field = form.itemAt(i, QFormLayout.FieldRole)
                if not (lab and isinstance(lab.widget(), QLabel) and field and field.widget()):
                    continue
                field_widget = field.widget()
                label = lab.widget().text()
                labels[field_widget] = label
                for child in field_widget.findChildren(QWidget):
                    labels[child] = label
        attrs = {id(w): name for name, w in vars(page).items() if isinstance(w, QWidget)}

        def register(widget, title, op, value=None, **extra):
            control = str(next(self.ids))
            self.handles[control] = (location, widget, title, op, value, page)
            output["控件"].append(dict(id=control, 名称=title, 操作=op, 当前值=value, **extra))
            return control

        if isinstance(page, QFileDialog):
            state = self.dialogs[page]
            output["操作方向"] = self.file_direction(page)
            output["可引用路径"] = self.file_references()
            register(page, "选择路径", "set", state.get("paths", []), 输入="项目内可用相对路径，引用文件可用唯一的文件名。也可使用用户给出的完整路径，多选用 items。")
            register(page, "采用路径", "press")
            register(page, "取消选择", "press")
            return output

        def register_item(view, row, column, name, header):
            index = view.model().index(row, column)
            if not index.flags() & Qt.ItemIsEnabled:
                return
            check = index.data(Qt.CheckStateRole)
            if check is not None and index.flags() & Qt.ItemIsUserCheckable:
                handle = register(view, name + " · 勾选", "set", check == Qt.Checked.value,
                                  行号=row+1, 列名=header)
                self.item_controls[handle] = (QPersistentModelIndex(index), Qt.CheckStateRole)
            if isinstance(view, QTableWidget) and view.editTriggers() != QAbstractItemView.NoEditTriggers and index.flags() & Qt.ItemIsEditable:
                handle = register(view, name + " · 内容", "set", index.data(Qt.EditRole), 行号=row+1, 列名=header)
                self.item_controls[handle] = (QPersistentModelIndex(index), Qt.EditRole)

        for button in ([] if page in self.dialogs else self.host.workspace_buttons):
            if button.isEnabled():
                register(button, button.text(), "press", 位置="工作台侧栏")
        if self.host.log_btn.isChecked():
            output["运行日志"] = self.host.logw.toPlainText().splitlines()[-20:]
        for w in page.findChildren(QWidget):
            if not w.isVisibleTo(page):
                continue
            attr = attrs.get(id(w), "")
            title = labels.get(w) or w.accessibleName() or attr
            if not title and isinstance(page, QInputDialog):
                title = page.labelText()
            if isinstance(w, QLabel):
                if w.text() and len(output["页面文字"]) < 60:
                    output["页面文字"].append(w.text()[:320])
                continue
            if not w.isEnabled():
                continue
            if isinstance(w, QCheckBox):
                register(w, w.text() or title, "set", w.isChecked())
            elif isinstance(w, QAbstractButton):
                if hasattr(w, "menu") and w.menu():
                    for action in w.menu().actions():
                        if not action.isSeparator() and action.isEnabled():
                            register(action, w.text() + " / " + action.text(), "press")
                elif w.text() or w.toolTip():
                    parent = w.parentWidget()
                    role = {"按钮作用":parent.buttonRole(w).name} if isinstance(parent, QDialogButtonBox) else {}
                    register(w, w.text() or w.toolTip(), "press", **role)
            elif isinstance(w, QComboBox):
                register(w, title, "set", w.currentText(), 选项=[w.itemText(i) for i in range(offset, min(w.count(), offset+80))], 总数=w.count())
            elif isinstance(w, QTabWidget) and w.count():
                register(w, title or "选项卡", "set", w.tabText(w.currentIndex()),
                         选项=[w.tabText(i) for i in range(w.count()) if w.isTabEnabled(i)])
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                register(w, title + (f" ({attr})" if attr else ""), "set", w.value(), 单位=w.suffix(), 范围=[w.minimum(), w.maximum()])
            elif isinstance(w, QLineEdit):
                if not isinstance(w.parentWidget(), (QAbstractSpinBox, QComboBox)) and w.echoMode() == QLineEdit.Normal and not w.isReadOnly():
                    register(w, title or w.placeholderText(), "set", w.text())
            elif isinstance(w, QPlainTextEdit) and not w.isReadOnly():
                register(w, title or w.placeholderText(), "set", w.toPlainText())
            elif isinstance(w, QListWidget):
                values = [w.item(i).text() for i in range(offset, min(offset+80, w.count()))]
                if hasattr(w, "checked"):
                    register(w, title or "列表", "set", w.checked(), 选项=w.names()[offset:offset+80], 总数=w.count())
                else:
                    register(w, title or "列表", "select", w.currentRow()+1, 选项=values, 总数=w.count())
                    for row, name in enumerate(values, offset):
                        register_item(w, row, 0, name, title or "列表")
                if not w.isSortingEnabled() and w.dragDropMode() == QAbstractItemView.InternalMove:
                    order = [w.item(i).text() for i in range(w.count())]
                    handle = register(w, (title or "列表") + " · 调整顺序", "set", order,
                                      输入="items 填完整的新顺序，使用从 1 开始的原行号")
                    self.item_controls[handle] = ("order", None)
            elif isinstance(w, QTableWidget):
                visible = [i for i in range(w.rowCount()) if not w.isRowHidden(i)]
                page_rows = visible[offset:offset+40]
                rows = [[w.item(i, j).text()[:180] if w.item(i,j) else "" for j in range(w.columnCount())]
                        for i in page_rows]
                headers = [w.horizontalHeaderItem(j).text() if w.horizontalHeaderItem(j) else str(j+1)
                           for j in range(w.columnCount())]
                register(w, title or "表格", "select", sorted({i.row()+1 for i in w.selectedIndexes()}),
                         首行=page_rows[0]+1 if page_rows else None, 行=rows, 行号=[i+1 for i in page_rows],
                         列=headers, 总数=len(visible))
                for row, cells in zip(page_rows, rows):
                    for column, header in enumerate(headers):
                        name = f"第 {row+1} 行 · {header}" + (f" · {cells[column]}" if cells[column] else "")
                        register_item(w, row, column, name, header)
        return output

    def perform(self, args, reply):
        from .main_window import NAV_TEXT
        action = str(args.get("action", ""))
        if self.host.project is not self.project or (self.host.runner.busy and action not in ("inspect", "view")):
            raise ValueError("项目已切换或正在计算，请先完成当前操作。")
        surface = self.surface()
        if action.startswith("viewer_"):
            if QApplication.activeModalWidget() is not None:
                raise ValueError("请先完成当前对话框，再操作图像窗口。")
            reply.put(self.viewers.perform(args))
            return
        target = str(args.get("target", ""))
        value = args.get("items", args.get("value", ""))
        if action == "inspect":
            reply.put(self.snapshot(max(0, int(args.get("offset", 0)))))
            return
        self.host.work_action.setChecked(True)
        if action == "view":
            if isinstance(surface, QFileDialog):
                raise ValueError("请用 inspect 查看选定路径和文件操作方向。")
            image = surface.grab()
            if image.width() > 1440:
                image = image.scaledToWidth(1440, Qt.SmoothTransformation)
            data = QByteArray()
            buffer = QBuffer(data)
            if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer, "PNG"):
                raise ValueError("无法读取当前工作页的图像。")
            reply.put(bytes(data))
            return
        if action == "open":
            if surface in self.dialogs:
                raise ValueError("请先完成或关闭当前对话框。")
            key = next((key for key in self.host.pages if target in (key, NAV_TEXT.get(key))), None)
            if key is None:
                raise ValueError("找不到页面，请按 inspect 返回的页面名称打开。")
            page = self.host.pages[key]
            if value and (not hasattr(page, "group_picker") or page.group_picker.findText(str(value)) < 0):
                raise ValueError("该页面没有此参数组。")
            if self.host.stack.currentWidget() is not page:
                self.host.goto(key)
            if hasattr(page, "show_settings") and hasattr(page, "group_picker"):
                page.show_settings(bool(value))
                if value:
                    page.group_picker.setCurrentText(value)
            reply.put(self.snapshot())
            return
        entry = self.handles.get(target)
        if not entry or entry[0] != self.location() or entry[3] != action or entry[5] is not surface:
            raise ValueError("控件或当前记录已变化，请重新 inspect。")
        location, widget, title, op, before, page = entry
        root = self.host if widget in self.host.workspace_buttons else page
        if not widget.isEnabled() or (isinstance(widget, QWidget) and not widget.isVisibleTo(root)):
            raise ValueError("该控件当前不可操作，请重新 inspect。")
        if isinstance(page, QFileDialog):
            self.file_action(page, location, title, value, reply)
            return
        if action == "press":
            parent = widget.parentWidget() if isinstance(widget, QWidget) else None
            if page in self.dialogs and isinstance(parent, QDialogButtonBox):
                role = parent.buttonRole(widget)
                roles = QDialogButtonBox.ButtonRole
                if role in (roles.AcceptRole, roles.YesRole, roles.RejectRole, roles.NoRole):
                    if role in (roles.AcceptRole, roles.YesRole):
                        fields = self.snapshot()["控件"]
                        values = [f"{c['名称']}：{c['当前值']}" for c in fields
                                  if c["操作"] in ("set", "select") and c["id"] not in self.item_controls]
                        values.extend(self.dialogs[page].get("changes", {}).values())
                        if not self.approve(location, page.windowTitle(), "\n".join([*values, "采用以上内容并执行？"])):
                            reply.put({"状态":"用户未采用，对话框仍然打开"})
                            return
                    if self.surface() is not page or self.location() != location or self.host.project is not self.project:
                        raise ValueError("预览期间窗口已变化，请重新读取。")
                    state = self.dialogs[page]
                    manual = state.get("manual", False)
                    if not manual:
                        state["closing"] = reply
                    widget.click()
                    if page.isVisible():
                        state["closing"] = None
                        reply.put({"状态":"对话框仍然打开，请核对输入", "窗口":self.snapshot()})
                    elif manual:
                        # Let a user's original dialog.exec() callback finish before reading the page.
                        def after_close():
                            try:
                                if self.host.project is not self.project:
                                    raise ValueError("已切换到新项目，本轮操作已结束。")
                                reply.put({"状态":"已关闭对话框", "操作":title, "窗口":self.snapshot()})
                            except (RuntimeError, ValueError) as exc:
                                reply.put(exc)
                        QTimer.singleShot(0, after_close)
                    return
            navigation = widget in (getattr(page, "side_button", None), self.host.log_btn) or title in (
                "选择分析参数", "返回工作区", "按固定间隔生成…", "手动输入时间…", "从表格导入…", "从通道读取…")
            if not navigation and not title.startswith(("运行 ERP", "运行时频", "运行解码", "运行 RSA")):
                if not self.approve(location, title, "执行此操作。需要选择文件或确认内容时，请在 BIAP 对话框中完成。"):
                    reply.put({"状态":"用户未采用，请保留原状"})
                    return
                if self.host.project is not self.project or self.location() != location:
                    raise ValueError("预览期间记录或页面已切换，请重新读取。")
            self.press(widget, title, reply, root)
            return
        if op == "select":
            indexes = value if isinstance(value, list) else [value]
            indexes = [int(i)-1 for i in indexes]
            n = widget.rowCount() if isinstance(widget, QTableWidget) else widget.count()
            if not indexes or min(indexes) < 0 or max(indexes) >= n:
                raise ValueError("选择的行号超出范围，行号从 1 开始。")
            if isinstance(widget, QTableWidget) and any(widget.isRowHidden(i) for i in indexes):
                raise ValueError("目标行已被筛选隐藏，请先调整筛选条件。")
            widget.clearSelection()
            first = widget.model().index(indexes[0], 0)
            widget.selectionModel().setCurrentIndex(first, QItemSelectionModel.NoUpdate)
            for i in indexes:
                widget.selectionModel().select(widget.model().index(i, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows)
        elif target in self.item_controls:
            if not self.set_item(page, widget, location, title, before, value, self.item_controls[target]):
                reply.put({"状态":"用户未采用，控件保持原值", "控件":title, "原值":before})
                return
        else:
            if not self.set_value(page, widget, location, title, before, value):
                reply.put({"状态":"用户未采用，控件保持原值", "控件":title, "原值":before})
                return
        reply.put(self.snapshot())

    def approve(self, location, title, change):
        page, record, scope = location
        name = self.project.name if self.project else "BIAP"
        return self.confirm(f"{name} · {page} · {record or scope or '当前项目'}\n{title}\n{change}")

    @staticmethod
    def file_direction(dialog):
        if dialog.fileMode() == QFileDialog.Directory:
            return "选择写入目录" if any(s in dialog.windowTitle() for s in ("导出", "保存")) else "选择读取目录"
        return "写入文件" if dialog.acceptMode() == QFileDialog.AcceptSave else "读取文件"

    def file_references(self):
        paths = []
        if self.project:
            paths.append(self.project.folder)
            paths.extend(self.project.assistant_data.get("files", []))
            rid = current_record(self.host)
            if rid:
                subject, rec = self.project.get_rec(rid)
                paths.extend([rec.source, subject.locs])
                paths.extend(self.project.path(rel) for rel in (rec.clean_file, rec.events_file, subject.epochs_file) if rel)
        for kind, name in self.panel.refs:
            if kind == "attachment":
                paths.append(self.panel.files.get(name, {}).get("path"))
        return list(dict.fromkeys(os.path.normpath(p) for p in paths if p))

    def file_action(self, dialog, location, title, value, reply):
        state = self.dialogs[dialog]
        if title == "取消选择":
            state["closing"] = reply
            state["file_receipt"] = {"状态":"已取消文件选择"}
            dialog.reject()
            return
        if title == "选择路径":
            inputs = value if isinstance(value, list) else [value]
            if not inputs or dialog.fileMode() != QFileDialog.ExistingFiles and len(inputs) != 1:
                raise ValueError("请按当前窗口的单选或多选方式提供路径。")
            refs = self.file_references()
            known = {os.path.normcase(path) for path in refs}
            root = Path(self.project.folder).resolve() if self.project else None
            paths = []
            for item in inputs:
                candidate = Path(str(item))
                if not candidate.is_absolute():
                    matching = [p for p in refs if os.path.normcase(os.path.basename(p)) == os.path.normcase(str(item))]
                    if len(matching) > 1:
                        raise ValueError("有多个同名引用，请从返回的路径中选择。")
                    if matching:
                        candidate = Path(matching[0])
                    elif root:
                        candidate = root / candidate
                paths.append(os.path.normpath(str(candidate)))
            question = os.path.normcase(str(getattr(getattr(self.panel, "worker", None), "question", ""))).replace("\\", "/")
            for path in paths:
                normalized = os.path.normcase(path)
                # A supplied path must end at a word boundary, not halfway through another path.
                end = r"""(?=$|[\s，。；、,;！？”’"'）)\]】])"""
                supplied = re.search(r"(?<![a-zA-Z0-9_./:\\-])" + re.escape(normalized.replace("\\", "/")) + end, question)
                in_project = bool(root and Path(path).resolve().is_relative_to(root))
                if not os.path.isabs(path) or (normalized not in known and not supplied and not in_project):
                    raise ValueError("请使用本轮用户给出的完整路径或可引用路径，也可以在文件窗口手动选择。")
            if len(set(map(os.path.normcase, paths))) != len(paths):
                raise ValueError("同一路径只能选择一次。")
            field = dialog.findChild(QLineEdit, "fileNameEdit")
            if field is None or any('"' in p for p in paths):
                raise ValueError("该路径需要在文件窗口手动选择。")
            state["paths"] = []
            dialog.setDirectory(str(Path(paths[0]).parent))
            text = paths[0] if len(paths) == 1 else " ".join(f'"{path}"' for path in paths)
            field.setText(text)
            selected = [os.path.normcase(os.path.normpath(p)) for p in dialog.selectedFiles()]
            if selected != list(map(os.path.normcase, paths)):
                raise ValueError("文件框内的实际路径与请求不一致，请补全文件名和扩展名后重试。")
            state["paths"] = paths
            reply.put(self.snapshot())
            return

        paths = state.get("paths", [])
        selected = lambda: [os.path.normcase(os.path.normpath(p)) for p in dialog.selectedFiles()]
        if not paths or selected() != list(map(os.path.normcase, paths)):
            raise ValueError("路径尚未填写或已经改变，请重新填写后预览。")
        direction = self.file_direction(dialog)
        save = dialog.acceptMode() == QFileDialog.AcceptSave and dialog.fileMode() != QFileDialog.Directory
        def preview():
            lines, signatures = [], []
            for filename in paths:
                path = Path(filename)
                if dialog.fileMode() == QFileDialog.Directory:
                    if not path.is_dir():
                        raise ValueError("选定的目录不存在。")
                    effect = "使用此目录；选择目录本身不会覆盖文件"
                elif save:
                    if not path.parent.is_dir() or path.is_dir():
                        raise ValueError("保存位置须为已有目录中的文件名。")
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        signatures.append(None)
                        effect = "新建文件"
                    else:
                        signatures.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
                        effect = "将覆盖已有文件"
                else:
                    if not path.is_file():
                        raise ValueError("选定的文件不存在。")
                    effect = "打开读取"
                lines.append(f"{effect}：{filename}")
            return lines, signatures
        lines, signatures = preview()
        if not self.approve(location, direction, "\n".join(lines)):
            reply.put({"状态":"用户未采用，文件窗口仍然打开"})
            return
        if self.host.project is not self.project or self.location() != location or self.surface() is not dialog or (
                selected() != list(map(os.path.normcase, paths)) or preview()[1] != signatures):
            raise ValueError("确认期间记录、选定路径或覆盖目标已变化，请重新预览。")
        state["closing"] = reply
        state["file_receipt"] = {"方向":direction, "路径":list(paths), "预览":lines}
        # Overwrite was just explicitly reviewed. Leave the option untouched for manual file selections.
        previous = dialog.testOption(QFileDialog.DontConfirmOverwrite)
        try:
            if save:
                dialog.setOption(QFileDialog.DontConfirmOverwrite, True)
            dialog.accept()
        finally:
            if dialog.isVisible():
                dialog.setOption(QFileDialog.DontConfirmOverwrite, previous)
                state["closing"] = None
                state.pop("file_receipt", None)
                reply.put({"状态":"文件窗口仍然打开，请核对选择。"})

    def set_item(self, page, view, location, title, before, value, control):
        index, role = control
        if role is None:
            try:
                order = [int(i)-1 for i in value] if isinstance(value, list) else []
            except (TypeError, ValueError):
                order = []
            if sorted(order) != list(range(view.count())):
                raise ValueError("请用原行号列出每一项的新顺序。")
            source = [QPersistentModelIndex(view.model().index(i, 0)) for i in range(view.count())]
            value = [before[i] for i in order] if len(before) == len(source) else []

            def read():
                return [idx.data() for idx in source] if all(idx.row() == i and idx.isValid()
                        for i, idx in enumerate(source)) and view.count() == len(source) else None

            def apply():
                for dest, original in enumerate(order):
                    row = source[original].row()
                    if row != dest and not view.model().moveRow(view.rootIndex(), row, view.rootIndex(), dest):
                        raise ValueError("列表顺序已经改变，请重新读取。")
        else:
            def read():
                if not index.isValid() or not index.flags() & Qt.ItemIsEnabled:
                    raise ValueError("条目已变化，请重新读取。")
                if role == Qt.CheckStateRole:
                    if not index.flags() & Qt.ItemIsUserCheckable or index.data(role) is None:
                        raise ValueError("该条目当前不能勾选。")
                    return index.data(role) == Qt.Checked.value
                if not index.flags() & Qt.ItemIsEditable or view.editTriggers() == QAbstractItemView.NoEditTriggers:
                    raise ValueError("该单元格当前不可编辑。")
                return index.data(role)

            value = self.boolean(value) if role == Qt.CheckStateRole else str(value)

            def apply():
                model_value = (Qt.Checked.value if value else Qt.Unchecked.value) if role == Qt.CheckStateRole else value
                if not view.model().setData(index, model_value, role):
                    raise ValueError("窗口没有接受单元格修改。")

        if read() != before:
            raise ValueError("原值已变化，请重新读取。")
        if before == value:
            return True
        if not self.approve(location, title, f"{before} → {value}"):
            return False
        if self.host.project is not self.project or self.surface() is not page or self.location() != location or read() != before:
            raise ValueError("预览期间窗口或条目已变化，请重新读取。")
        apply()
        if page in self.dialogs:
            changes = self.dialogs[page].setdefault("changes", {})
            changes[(view, index, role)] = f"{title}：{before} → {value}"
        return True

    @staticmethod
    def boolean(value):
        if str(value).lower() in ("true", "是", "开", "开启", "勾选", "1"):
            return True
        if str(value).lower() in ("false", "否", "关", "关闭", "取消", "0"):
            return False
        raise ValueError("请说明开启还是关闭此选项。")

    def set_value(self, page, widget, location, title, before, value):
        if isinstance(widget, QTabWidget):
            matches = [i for i in range(widget.count()) if widget.tabText(i) == str(value) and widget.isTabEnabled(i)]
            if len(matches) != 1:
                raise ValueError("请从当前可用的选项卡中选择。")
            value, apply = str(value), partial(widget.setCurrentIndex, matches[0])
        elif isinstance(widget, QComboBox):
            text = str(value)
            options = [widget.itemText(i) for i in range(widget.count())]
            matches = [i for i in range(len(options)) if options[i] == text or str(widget.itemData(i)) == text]
            if not matches and not widget.isEditable():
                matches = [i for i, option in enumerate(options) if text and text.casefold() in option.casefold()]
            if not matches and widget.isEditable():
                value, apply = text, partial(widget.setEditText, text)
            elif len(matches) == 1:
                idx = matches[0]
                value, apply = options[idx], partial(widget.setCurrentIndex, idx)
            else:
                raise ValueError("选项不唯一或不存在，请查看候选项。")
        elif isinstance(widget, QCheckBox):
            value = self.boolean(value)
            apply = partial(widget.setChecked, value)
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            text = str(value).strip().removesuffix(widget.suffix().strip()).strip()
            if widget.suffix().strip() == "s" and not isinstance(widget, QSpinBox):
                # Use the same time parser as the native input field.
                value = events.time_seconds(value)
            else:
                value = int(text) if isinstance(widget, QSpinBox) else float(text)
            bridge.validate_control(widget, value)
            apply = partial(widget.setValue, value)
        elif isinstance(widget, QListWidget):
            value = value if isinstance(value, list) else [value]
            if not all(str(v) in widget.names() for v in value):
                raise ValueError("列表中没有指定选项。")
            value = list(map(str, value))
            def apply():
                for i, name in enumerate(widget.names()):
                    widget.item(i).setCheckState(Qt.Checked if name in value else Qt.Unchecked)
        else:
            value = str(value)
            apply = partial(widget.setPlainText if isinstance(widget, QPlainTextEdit) else widget.setText, value)
        if isinstance(widget, QListWidget):
            read = widget.checked
        elif isinstance(widget, QPlainTextEdit):
            read = widget.toPlainText
        elif isinstance(widget, QTabWidget):
            read = lambda: widget.tabText(widget.currentIndex())
        else:
            read = partial(bridge.read_control, widget)
        if read() != before:
            raise ValueError("控件值已经发生变化，请重新 inspect。")
        navigation = isinstance(widget, QTabWidget) or any(widget is getattr(page, name, None) for name in ("recs", "group_picker", "scope"))
        draft = page in self.dialogs and (isinstance(page, QInputDialog) or
                page.windowTitle() in ("按固定间隔生成事件", "手动输入事件", "从表格导入事件", "从通道读取事件"))
        if before != value:
            if not navigation and not draft and not self.approve(location, title, f"{before} → {value}"):
                return False
            if self.host.project is not self.project or self.surface() is not page or self.location() != location or read() != before:
                raise ValueError("预览期间页面或控件值已改变，请重新读取。")
            saved_field = self.saved_field(page, widget)
            workflow = getattr(self.panel, "workflow", None)
            if (workflow and workflow.record_analysis() and page is self.host.pages[workflow.analysis]
                    and widget is getattr(page, "conds", None)):
                saved_field = workflow.analysis, "条件"
            if saved_field:
                step, field = saved_field
                scope = workflow.record_analysis() if workflow and workflow.analysis == step else None
                state = bridge.session_state(self.host, record_analysis=scope)
                _, proposal = workbench.run_tool(state, [("step",step)], "edit_step",
                    {"target":step, "params":{field:value}, "title":title})
                self.panel.write_project([proposal], record_analysis=scope)
            else:
                pre = self.host.pages["预处理"]
                persist = page is pre and widget in (pre.bads, pre.locs, pre.rename, pre.excl,
                                                       pre.ica_n, pre.montage, pre.segment_limit)
                if persist and widget is pre.excl and pre._current() and pre.needs_component_review(pre._current()[1]):
                    raise ValueError("这次 ICA 分解已经改变，请在成分图中确认排除清单。")
                apply()
                if persist and not pre._save_pipe():
                    pre.on_show()
                    raise ValueError("工作窗口未能保存这次预处理修改。")
        return True

    def saved_field(self, page, widget):
        for step, fields in bridge.PRE.items():
            if page is self.host.pages["事件" if step == "事件分段" else "预处理"]:
                for label, _, attr in fields:
                    if getattr(page, attr) is widget:
                        return step, label
        for step, fields in bridge.ANALYSIS.items():
            if page is self.host.pages[step]:
                for label, attr in fields.items():
                    if getattr(page, attr) is widget:
                        return step, label

    def press(self, widget, title, reply, opener):
        # The same button may start a background job, open a native dialog, or act synchronously.
        runner = self.host.runner
        started = []
        state = {"dialog":None, "closing":None}
        def deliver(result):
            destination = state["closing"] if state["dialog"] is not None else reply
            if destination is not None:
                if isinstance(result, dict) and "file_receipt" in state:
                    result["文件选择"] = state["file_receipt"]
                destination.put(result)
                state["closing"] = None

        def inspect_opened():
            dialog = QApplication.activeModalWidget()
            old = state["dialog"]
            if not dialog or dialog is opener or dialog is old or old is not None and isValid(old) and old.isVisible():
                return
            destination = state["closing"] if old is not None else reply
            file_picker = isinstance(dialog, QFileDialog) and self.owned_dialog(dialog, opener)
            if destination is None or not (file_picker or self.can_fill(dialog, opener)):
                return
            self.dialogs.pop(old, None)
            state["dialog"], state["closing"] = dialog, None
            state.pop("paths", None)
            self.dialogs[dialog] = state
            destination.put({"状态":"已打开对话框，可以填写字段，采用前将显示预览", "操作":title, "窗口":self.snapshot()})

        def observe(task):
            if state["dialog"] is not None and isValid(state["dialog"]) and state["dialog"].isVisible():
                return
            started.append(task)
            task.observed = True
            task.signals.settled.connect(settled)
        def settled(result, error):
            if error:
                deliver(ValueError(error))
            else:
                receipt = {"状态":"已完成", "操作":title}
                if hasattr(result, "metrics"):
                    receipt.update(结果=result.title, 指标=result.metrics, 图表=[n for n, _ in result.figures])
                elif isinstance(result, str):
                    receipt["结果"] = result
                deliver(receipt)
        timer = QTimer()
        timer.setInterval(30)
        timer.timeout.connect(inspect_opened)
        timer.start()
        runner.task_started.connect(observe)
        attribute = Qt.AA_DontUseNativeDialogs
        native_disabled = QApplication.testAttribute(attribute)
        QApplication.setAttribute(attribute, True)
        try:
            (widget.click if isinstance(widget, QAbstractButton) else widget.trigger)()
        finally:
            QApplication.setAttribute(attribute, native_disabled)
            timer.stop()
            self.dialogs.pop(state["dialog"], None)
            runner.task_started.disconnect(observe)
        if not started:
            if self.host.project is not self.project:
                deliver({"状态":"已切换项目，本轮操作已结束", "当前项目":self.host.project.name if self.host.project else None})
            else:
                deliver({"状态":"已触发控件", "操作":title, "窗口":self.snapshot()})
