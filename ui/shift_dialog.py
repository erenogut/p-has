from PyQt6.QtCore import Qt, QTime
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs import ask_confirm, show_warning
from ui.shift_store import (
    ShiftError,
    add_assignment,
    delete_person,
    get_person,
    kaynaklar_from_assignments,
    list_people,
    merge_cameras,
    overlap_warnings,
    upsert_person,
    validate_times,
)
from ui.theme import COLOR_BG, COLOR_BG_MID, apply_dark_title_bar


def _time_edit(hours, minutes):
    widget = QTimeEdit()
    widget.setDisplayFormat("HH:mm")
    widget.setTime(QTime(hours, minutes))
    widget.setButtonSymbols(QTimeEdit.ButtonSymbols.NoButtons)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setMinimumWidth(78)
    widget.setMinimumHeight(36)
    return widget


class AssignmentDialog(QDialog):
    def __init__(self, parent, cameras, get_zones, department="", kaynak_suggestions=None, kaynak=""):
        super().__init__(parent)
        self._cameras = cameras or []
        self._get_zones = get_zones
        self._department = department or ""
        self._kaynaklar = kaynaklar_from_assignments(
            [{"kaynak": name} for name in (kaynak_suggestions or [])]
        )
        self._kaynak = kaynak or ""
        self._result = None
        self.setWindowTitle("Atama ekle")
        self.setModal(True)
        self.setMinimumWidth(420)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowContextHelpButtonHint
        self.setWindowFlags(flags)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(12)

        title = QLabel("Atama ekle")
        title.setObjectName("titleLabel")
        root.addWidget(title)

        card = QFrame()
        card.setObjectName("card")
        form = QVBoxLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(8)

        form.addWidget(self._muted("Kamera"))
        self.camera_combo = QComboBox()
        self.camera_combo.setMaxVisibleItems(10)
        if not self._cameras:
            self.camera_combo.addItem("Kamera listesi boş")
            self.camera_combo.setEnabled(False)
        else:
            for camera in self._cameras:
                self.camera_combo.addItem(camera["name"], camera["id"])
        self.camera_combo.currentIndexChanged.connect(self._reload_zones)
        form.addWidget(self.camera_combo)

        form.addWidget(self._muted("Bölge"))
        self.zone_combo = QComboBox()
        self.zone_combo.setMaxVisibleItems(10)
        form.addWidget(self.zone_combo)
        self.zone_hint = QLabel("")
        self.zone_hint.setObjectName("mutedLabel")
        self.zone_hint.setWordWrap(True)
        form.addWidget(self.zone_hint)

        form.addWidget(self._muted("Departman"))
        self.dept_edit = QLineEdit()
        self.dept_edit.setMaxLength(80)
        self.dept_edit.setPlaceholderText("Örn. Kaynak, Depo")
        self.dept_edit.setText(self._department)
        form.addWidget(self.dept_edit)

        form.addWidget(self._muted("Kaynak"))
        self.kaynak_combo = QComboBox()
        self.kaynak_combo.setEditable(True)
        self.kaynak_combo.setMaxVisibleItems(10)
        self.kaynak_combo.lineEdit().setMaxLength(80)
        self.kaynak_combo.lineEdit().setPlaceholderText("Örn. Kaynak 3")
        for name in self._kaynaklar:
            self.kaynak_combo.addItem(name)
        if self._kaynak:
            self.kaynak_combo.setCurrentText(self._kaynak)
        else:
            self.kaynak_combo.setCurrentText("")
        form.addWidget(self.kaynak_combo)

        times = QHBoxLayout()
        start_box = QVBoxLayout()
        start_box.addWidget(self._muted("Başlangıç"))
        self.start_edit = _time_edit(8, 0)
        start_box.addWidget(self.start_edit)
        end_box = QVBoxLayout()
        end_box.addWidget(self._muted("Bitiş"))
        self.end_edit = _time_edit(16, 0)
        end_box.addWidget(self.end_edit)
        times.addLayout(start_box)
        times.addLayout(end_box)
        form.addLayout(times)
        root.addWidget(card)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("İptal")
        cancel.setObjectName("secondaryButton")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Ekle")
        ok.setObjectName("primaryButton")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)
        self._reload_zones()

    def _muted(self, text):
        label = QLabel(text)
        label.setObjectName("mutedLabel")
        return label

    def _reload_zones(self):
        self.zone_combo.clear()
        channel_id = self.camera_combo.currentData()
        if not channel_id:
            self.zone_hint.setText("Önce Ayarlar’dan kameraları bulun.")
            return
        zones = self._get_zones(channel_id) or {}
        items = []
        for zone_id, entry in zones.items():
            try:
                numeric = int(zone_id)
            except (TypeError, ValueError):
                continue
            name = entry.get("name") if isinstance(entry, dict) else f"Zone {numeric}"
            items.append((numeric, name or f"Zone {numeric}"))
        items.sort(key=lambda item: item[0])
        if not items:
            self.zone_hint.setText("Önce bu kameraya bölge çizin.")
            return
        self.zone_hint.setText("")
        for zone_id, name in items:
            self.zone_combo.addItem(name, zone_id)

    def _accept(self):
        channel_id = self.camera_combo.currentData()
        zone_id = self.zone_combo.currentData()
        if not channel_id:
            show_warning(self, "Kamera", "Kamera seçin.")
            return
        if zone_id is None:
            show_warning(self, "Bölge", "Önce bu kameraya bölge çizin.")
            return
        kaynak = self.kaynak_combo.currentText().strip()
        if not kaynak:
            show_warning(self, "Kaynak", "Kaynak yazın.")
            return
        start = self.start_edit.time().toString("HH:mm")
        end = self.end_edit.time().toString("HH:mm")
        try:
            start, end = validate_times(start, end)
        except ShiftError as exc:
            show_warning(self, "Saat", str(exc))
            return
        self._result = {
            "kanalId": str(channel_id),
            "kameraAdi": self.camera_combo.currentText(),
            "bolgeId": int(zone_id),
            "bolgeAdi": self.zone_combo.currentText(),
            "departman": self.dept_edit.text().strip(),
            "kaynak": kaynak,
            "start": start,
            "end": end,
        }
        self.accept()

    def assignment(self):
        return self._result

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_title_bar(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(COLOR_BG))
        grad.setColorAt(0.55, QColor(COLOR_BG_MID))
        grad.setColorAt(1, QColor(COLOR_BG))
        painter.fillRect(self.rect(), grad)
        super().paintEvent(event)


class ShiftDialog(QDialog):
    def __init__(self, parent, config, save_fn, cameras, get_zones):
        super().__init__(parent)
        self._config = config
        self._save_fn = save_fn
        self._cameras = merge_cameras(cameras, config)
        self._get_zones = get_zones
        self._draft = None
        self._dirty = False
        self._loading = False
        self._selected_id = None
        self.setWindowTitle("Vardiyalar")
        self.setMinimumSize(1100, 700)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowContextHelpButtonHint
        self.setWindowFlags(flags)
        self._build()
        self._reload_people()
        self._show_empty()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(12)

        title = QLabel("Vardiyalar")
        title.setObjectName("titleLabel")
        root.addWidget(title)
        hint = QLabel("Kişi ekleyin. Aynı bölgeye farklı kaynak için ayrı atama satırı ekleyin.")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_people_panel(), 0)
        body.addWidget(self._build_form_panel(), 1)
        root.addLayout(body, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.btn_save = QPushButton("Kaydet")
        self.btn_save.setObjectName("primaryButton")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setMinimumWidth(120)
        self.btn_save.clicked.connect(self._save_person)
        close = QPushButton("Kapat")
        close.setObjectName("secondaryButton")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setMinimumWidth(120)
        close.clicked.connect(self.close)
        buttons.addWidget(self.btn_save)
        buttons.addWidget(close)
        root.addLayout(buttons)

    def _build_people_panel(self):
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(260)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        label = QLabel("Kişiler")
        label.setStyleSheet("font-weight: 700;")
        layout.addWidget(label)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Kişi ara...")
        self.search.textChanged.connect(self._filter_people)
        layout.addWidget(self.search)

        self.people_list = QListWidget()
        self.people_list.setObjectName("peopleList")
        self.people_list.currentItemChanged.connect(self._on_person_changed)
        layout.addWidget(self.people_list, 1)

        self.btn_new = QPushButton("Yeni kişi")
        self.btn_new.setObjectName("secondaryButton")
        self.btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new.clicked.connect(self._new_person)
        self.btn_delete = QPushButton("Sil")
        self.btn_delete.setObjectName("secondaryButton")
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.clicked.connect(self._delete_person)
        layout.addWidget(self.btn_new)
        layout.addWidget(self.btn_delete)
        return card

    def _build_form_panel(self):
        self.stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_label = QLabel("Kişi seçin veya Yeni kişi ile ekleyin.")
        empty_label.setObjectName("mutedLabel")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setWordWrap(True)
        empty_layout.addStretch()
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch()
        self.stack.addWidget(empty)

        form = QFrame()
        form.setObjectName("card")
        layout = QVBoxLayout(form)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        layout.addWidget(self._muted("Ad"))
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(80)
        self.name_edit.setPlaceholderText("Örn. Personel 1")
        self.name_edit.textChanged.connect(self._on_name_changed)
        layout.addWidget(self.name_edit)

        layout.addWidget(self._muted("Departman"))
        self.dept_edit = QLineEdit()
        self.dept_edit.setMaxLength(80)
        self.dept_edit.setPlaceholderText("Örn. Kaynak, Depo")
        self.dept_edit.textChanged.connect(self._on_name_changed)
        layout.addWidget(self.dept_edit)

        header = QHBoxLayout()
        assignments_label = QLabel("Atamalar")
        assignments_label.setStyleSheet("font-weight: 700;")
        header.addWidget(assignments_label)
        header.addStretch()
        self.btn_add_assignment = QPushButton("Atama ekle")
        self.btn_add_assignment.setObjectName("secondaryButton")
        self.btn_add_assignment.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_assignment.clicked.connect(self._add_assignment)
        header.addWidget(self.btn_add_assignment)
        layout.addLayout(header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Kamera", "Bölge", "Kaynak", "Başlangıç", "Bitiş", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 118)
        self.table.setColumnWidth(4, 118)
        self.table.setColumnWidth(5, 80)
        self.table.verticalHeader().setDefaultSectionSize(48)
        layout.addWidget(self.table, 1)
        self.stack.addWidget(form)
        return self.stack

    def _muted(self, text):
        label = QLabel(text)
        label.setObjectName("mutedLabel")
        return label

    def _show_empty(self):
        self._draft = None
        self._selected_id = None
        self._dirty = False
        self.stack.setCurrentIndex(0)
        self.btn_delete.setEnabled(False)
        self._refresh_save_button()

    def _show_form(self):
        self.stack.setCurrentIndex(1)
        self.btn_delete.setEnabled(True)
        self._refresh_save_button()

    def _refresh_save_button(self):
        form_open = self.stack.currentIndex() == 1 and self._draft is not None
        if form_open and self._dirty:
            self.btn_save.setEnabled(True)
            self.btn_save.setText("Kaydet")
            self.btn_save.setObjectName("primaryButton")
        elif form_open and self._selected_id:
            self.btn_save.setEnabled(False)
            self.btn_save.setText("Kaydedildi")
            self.btn_save.setObjectName("secondaryButton")
        else:
            self.btn_save.setEnabled(False)
            self.btn_save.setText("Kaydet")
            self.btn_save.setObjectName("primaryButton")
        self.btn_save.style().unpolish(self.btn_save)
        self.btn_save.style().polish(self.btn_save)
        self.btn_save.update()

    def _reload_people(self, select_id=None):
        self._loading = True
        self.people_list.clear()
        for person in list_people(self._config):
            label = person["name"]
            if person.get("departman"):
                label = f"{person['name']}  ·  {person['departman']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, person["id"])
            self.people_list.addItem(item)
        self._filter_people()
        if select_id:
            self._select_person(select_id)
        self._loading = False

    def _select_person(self, person_id):
        for index in range(self.people_list.count()):
            item = self.people_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == person_id:
                self.people_list.setCurrentItem(item)
                return

    def _filter_people(self, _text=None):
        query = self.search.text().strip().casefold()
        for index in range(self.people_list.count()):
            item = self.people_list.item(index)
            item.setHidden(bool(query) and query not in item.text().casefold())

    def _confirm_discard(self):
        if not self._dirty:
            return True
        return ask_confirm(
            self,
            "Kaydedilmemiş değişiklik",
            "Değişiklikler kaybolacak. Devam edilsin mi?",
        )

    def _on_person_changed(self, current, _previous):
        if self._loading:
            return
        if current is None:
            return
        person_id = current.data(Qt.ItemDataRole.UserRole)
        if person_id == self._selected_id:
            return
        if not self._confirm_discard():
            self._loading = True
            self._select_person(self._selected_id)
            self._loading = False
            return
        self._load_person(person_id)

    def _load_person(self, person_id):
        person = get_person(self._config, person_id)
        if person is None:
            self._show_empty()
            return
        self._draft = person
        self._selected_id = person["id"]
        self._fill_form(person)
        self._dirty = False
        self._show_form()

    def _fill_form(self, person):
        self._loading = True
        self.name_edit.setText(person.get("name") or "")
        self.dept_edit.setText(person.get("departman") or "")
        self._fill_table(person.get("assignments") or [])
        self._loading = False

    def _fill_table(self, assignments):
        self.table.setRowCount(0)
        for assignment in assignments:
            self._append_row(assignment)

    def _append_row(self, assignment):
        row = self.table.rowCount()
        self.table.insertRow(row)
        camera = QTableWidgetItem(assignment.get("kameraAdi") or "")
        camera.setData(Qt.ItemDataRole.UserRole, assignment["kanalId"])
        zone = QTableWidgetItem(assignment.get("bolgeAdi") or "")
        zone.setData(Qt.ItemDataRole.UserRole, int(assignment["bolgeId"]))
        self.table.setItem(row, 0, camera)
        self.table.setItem(row, 1, zone)
        kaynak = QLineEdit(assignment.get("kaynak") or "")
        kaynak.setPlaceholderText("Zorunlu")
        kaynak.setMaxLength(80)
        kaynak.textChanged.connect(self._mark_dirty)
        self.table.setCellWidget(row, 2, kaynak)

        start = _time_edit(8, 0)
        end = _time_edit(16, 0)
        start_time = QTime.fromString(assignment["start"], "HH:mm")
        end_time = QTime.fromString(assignment["end"], "HH:mm")
        if start_time.isValid():
            start.setTime(start_time)
        if end_time.isValid():
            end.setTime(end_time)
        start.timeChanged.connect(self._mark_dirty)
        end.timeChanged.connect(self._mark_dirty)
        self.table.setCellWidget(row, 3, start)
        self.table.setCellWidget(row, 4, end)

        delete = QPushButton("Sil")
        delete.setObjectName("secondaryButton")
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.clicked.connect(lambda _checked=False, r=row: self._remove_row(r))
        self.table.setCellWidget(row, 5, delete)
        self.table.setRowHeight(row, 48)

    def _rebind_delete_buttons(self):
        for row in range(self.table.rowCount()):
            button = self.table.cellWidget(row, 5)
            if button is None:
                continue
            try:
                button.clicked.disconnect()
            except TypeError:
                pass
            button.clicked.connect(lambda _checked=False, r=row: self._remove_row(r))

    def _assignments_from_table(self):
        assignments = []
        for row in range(self.table.rowCount()):
            camera = self.table.item(row, 0)
            zone = self.table.item(row, 1)
            kaynak = self.table.cellWidget(row, 2)
            start = self.table.cellWidget(row, 3)
            end = self.table.cellWidget(row, 4)
            if camera is None or zone is None or start is None or end is None:
                continue
            assignments.append({
                "kanalId": str(camera.data(Qt.ItemDataRole.UserRole)),
                "kameraAdi": camera.text(),
                "bolgeId": int(zone.data(Qt.ItemDataRole.UserRole)),
                "bolgeAdi": zone.text(),
                "kaynak": kaynak.text().strip() if kaynak is not None else "",
                "start": start.time().toString("HH:mm"),
                "end": end.time().toString("HH:mm"),
            })
        return assignments

    def _sync_draft(self):
        if self._draft is None:
            self._draft = {"id": "", "name": "", "departman": "", "assignments": []}
        self._draft["name"] = self.name_edit.text()
        self._draft["departman"] = self.dept_edit.text()
        self._draft["assignments"] = self._assignments_from_table()
        self._draft["kaynaklar"] = kaynaklar_from_assignments(self._draft["assignments"])
        return self._draft

    def _mark_dirty(self, *_args):
        if self._loading:
            return
        self._dirty = True
        self._refresh_save_button()

    def _on_name_changed(self, _text):
        self._mark_dirty()

    def _new_person(self):
        if not self._confirm_discard():
            return
        self._loading = True
        self.people_list.clearSelection()
        self._loading = False
        self._draft = {"id": "", "name": "", "departman": "", "assignments": []}
        self._selected_id = None
        self._fill_form(self._draft)
        self._dirty = False
        self._show_form()
        self.name_edit.setFocus()
        self._refresh_save_button()

    def _add_assignment(self):
        if self._draft is None:
            return
        dialog = AssignmentDialog(
            self,
            self._cameras,
            self._get_zones,
            self.dept_edit.text().strip(),
            kaynaklar_from_assignments(self._assignments_from_table()),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        assignment = dialog.assignment()
        if assignment is None:
            return
        department = assignment.pop("departman", "")
        if department:
            self.dept_edit.setText(department)
        self._sync_draft()
        add_assignment(self._draft, assignment)
        self._fill_table(self._draft["assignments"])
        self._mark_dirty()

    def _remove_row(self, row):
        if self._draft is None:
            return
        self._sync_draft()
        assignments = list(self._draft.get("assignments") or [])
        if not (0 <= row < len(assignments)):
            return
        assignments.pop(row)
        self._draft["assignments"] = assignments
        self._draft["kaynaklar"] = kaynaklar_from_assignments(assignments)
        self._fill_table(assignments)
        self._rebind_delete_buttons()
        self._mark_dirty()

    def _save_person(self):
        if self._draft is None:
            return
        person = self._sync_draft()
        try:
            saved = upsert_person(self._config, person)
        except ShiftError as exc:
            show_warning(self, "Kayıt", str(exc))
            return
        warnings = overlap_warnings(self._config, saved)
        self._save_fn()
        self._draft = saved
        self._selected_id = saved["id"]
        self._dirty = False
        self._reload_people(saved["id"])
        self._fill_form(saved)
        self._refresh_save_button()
        if warnings:
            show_warning(
                self,
                "Saat çakışması",
                "Kayıt alındı. Aynı bölgede çakışan saatler:\n" + "\n".join(warnings),
            )

    def _delete_person(self):
        if self._draft is None:
            return
        name = (self.name_edit.text() or self._draft.get("name") or "bu kişi").strip()
        if not ask_confirm(self, "Kişiyi sil", f"{name} ve atamaları silinsin mi?"):
            return
        person_id = self._draft.get("id")
        if person_id:
            delete_person(self._config, person_id)
            self._save_fn()
        self._dirty = False
        self._reload_people()
        self._show_empty()

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_title_bar(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(COLOR_BG))
        grad.setColorAt(0.55, QColor(COLOR_BG_MID))
        grad.setColorAt(1, QColor(COLOR_BG))
        painter.fillRect(self.rect(), grad)
        super().paintEvent(event)
