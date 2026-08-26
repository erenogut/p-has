from datetime import timedelta

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs import show_error
from ui.report_data import (
    MEASURE_HINT,
    chart_camera_choices,
    filter_recording_rows,
    format_duration,
    format_occupancy_pct,
    kaynak_report,
    person_report,
    recording_choice_rows,
    report_summary_line,
    session_zone_charts,
)
from ui.shift_store import get_person, list_kaynak_locations, list_kaynak_names, person_choices
from ui.theme import (
    COLOR_BG,
    COLOR_BG_MID,
    COLOR_BORDER,
    COLOR_MUTED,
    COLOR_PANEL,
    COLOR_TEXT,
    apply_dark_title_bar,
)

ZONE_COLORS = [
    QColor("#3DDFD0"),
    QColor("#7AA2F7"),
    QColor("#F0C674"),
    QColor("#E07A5F"),
    QColor("#9B8AFB"),
    QColor("#81B29A"),
]


class ReportLoadWorker(QThread):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self._loader = loader

    def run(self):
        try:
            self.finished_ok.emit(self._loader())
        except Exception as exc:
            self.failed.emit(str(exc))


class OccupancyTimeChart(QWidget):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._name = ""
        self._points = []
        self._plot = []
        self._bin_seconds = 5
        self._start = None
        self._end = None
        self.setMinimumHeight(180)
        self.setMaximumHeight(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_zone(self, zone, start=None, end=None):
        self._name = zone.get("name") or "Bölge"
        self._points = list(zone.get("points") or [])
        self._bin_seconds = int(zone.get("bin_seconds") or 5)
        self._start = start or (self._points[0][0] if self._points else None)
        self._end = end
        if self._end is None and self._points:
            self._end = self._points[-1][0] + timedelta(seconds=self._bin_seconds)
        self._plot = []
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(COLOR_PANEL))

        left, top, right, bottom = 40, 18, self.width() - 16, self.height() - 28
        self._plot = []
        if not self._points or not self._start or not self._end or self._end <= self._start:
            painter.setPen(QColor(COLOR_MUTED))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(
                self.rect().adjusted(16, 16, -16, -16),
                Qt.AlignmentFlag.AlignCenter,
                "Bu kayıtta zaman serisi yok.\nYeni takip duruşundan sonra çıkar.",
            )
            return

        t0 = self._start
        t1 = self._end
        span = max((t1 - t0).total_seconds(), 1.0)
        width = max(1, right - left)
        height = max(1, bottom - top)

        def x_at(when):
            return left + width * ((when - t0).total_seconds() / span)

        def y_at(value):
            return bottom - height * (1 if value else 0)

        painter.setPen(QColor(COLOR_BORDER))
        painter.drawLine(left, top, left, bottom)
        painter.drawLine(left, bottom, right, bottom)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor(COLOR_MUTED))
        painter.drawText(8, top - 2, 28, 16, Qt.AlignmentFlag.AlignRight, "1")

        fill = QColor(self._color)
        fill.setAlpha(70)
        for index, (when, value) in enumerate(self._points):
            nxt = self._points[index + 1][0] if index + 1 < len(self._points) else t1
            x1 = x_at(when)
            x2 = x_at(nxt)
            y = y_at(value)
            self._plot.append(((x1 + x2) / 2, y, when, value))
            if value:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRect(int(x1), int(top), max(1, int(x2 - x1)), int(bottom - top))
            painter.setPen(QPen(self._color, 2))
            painter.drawLine(int(x1), int(y), int(x2), int(y))

        painter.setPen(QPen(self._color, 2))
        for index in range(1, len(self._points)):
            prev_when, prev_val = self._points[index - 1]
            when, value = self._points[index]
            if prev_val == value:
                continue
            x = int(x_at(when))
            painter.drawLine(x, int(y_at(prev_val)), x, int(y_at(value)))

        step = self._tick_step(span)
        stamp_fmt = "%H:%M:%S" if span <= 180 else "%H:%M"
        painter.setPen(QColor(COLOR_MUTED))
        painter.setFont(QFont("Segoe UI", 8))
        last_x = -999
        cursor = t0
        while cursor <= t1:
            x = int(x_at(cursor))
            if x - last_x >= 48 or cursor == t0 or cursor >= t1:
                painter.drawLine(x, bottom, x, bottom + 3)
                painter.drawText(
                    x - 28,
                    bottom + 5,
                    56,
                    16,
                    Qt.AlignmentFlag.AlignHCenter,
                    cursor.strftime(stamp_fmt),
                )
                last_x = x
            cursor += timedelta(seconds=step)

    def _tick_step(self, span):
        if span <= 120:
            return 15
        if span <= 180:
            return 30
        if span <= 600:
            return 60
        if span <= 900:
            return 120
        if span <= 3600:
            return 300
        if span <= 4 * 3600:
            return 600
        return 1800

    def mouseMoveEvent(self, event):
        pos = event.position()
        nearest = None
        best = 18
        for x, _y, when, value in self._plot:
            distance = abs(pos.x() - x)
            if distance < best:
                best = distance
                stamp = when.strftime("%H:%M:%S") if when else ""
                nearest = f"{self._name}  {stamp}  {'dolu' if value else 'boş'}"
        if nearest:
            QToolTip.showText(event.globalPosition().toPoint(), nearest, self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)


def _muted(text):
    label = QLabel(text)
    label.setObjectName("mutedLabel")
    label.setWordWrap(True)
    return label


class ZoneReportCard(QFrame):
    def __init__(self, zone, start, end, color, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title_text = zone.get("name") or "Bölge"
        pct_text = format_occupancy_pct(zone.get("pct"))
        if pct_text:
            title_text = f"{title_text}  ·  {pct_text.replace('Alan ', '')}"
        title = QLabel(title_text)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        title.setWordWrap(True)
        layout.addWidget(title)
        if zone.get("kaynak"):
            kaynak_text = f"Kaynak: {zone['kaynak']}"
            if zone.get("same_zone_kaynak"):
                kaynak_text += " · aynı bölge"
            layout.addWidget(_muted(kaynak_text))
        location = "  ·  ".join(
            part for part in (zone.get("location_camera"), zone.get("location_zone")) if part
        )
        if location:
            layout.addWidget(_muted(location))
        if zone.get("person_name"):
            layout.addWidget(_muted(f"Kişi: {zone['person_name']}"))

        chart = OccupancyTimeChart(color)
        chart.set_zone(zone, start, end)
        layout.addWidget(chart, 1)

        footer = QWidget()
        foot = QVBoxLayout(footer)
        foot.setContentsMargins(0, 6, 0, 0)
        foot.setSpacing(6)

        foot.addWidget(_muted("Planlanan vardiya"))
        planned = zone.get("planned") or []
        location_people = zone.get("location_people") or []
        if not planned and location_people:
            names = QLabel(", ".join(location_people))
            names.setWordWrap(True)
            names.setStyleSheet("font-weight: 600;")
            foot.addWidget(names)
        elif not planned:
            empty = QLabel("Bu saatte vardiya tanımsız")
            empty.setWordWrap(True)
            foot.addWidget(empty)
        else:
            for person in planned:
                name = QLabel(person.get("name") or "Kişi")
                name.setWordWrap(True)
                name.setStyleSheet("font-weight: 600;")
                foot.addWidget(name)
                detail_parts = []
                if person.get("departman"):
                    detail_parts.append(person["departman"])
                hours = f"{person.get('start')}–{person.get('end')}" if person.get("start") else ""
                if hours:
                    detail_parts.append(hours)
                if detail_parts:
                    detail = _muted(" · ".join(detail_parts))
                    foot.addWidget(detail)

        foot.addSpacing(10)
        foot.addWidget(_muted("Ölçüm"))
        if zone.get("emphasize") in ("duration", "pct") or zone.get("kaynak") or zone.get("person_name"):
            foot.addWidget(_muted(MEASURE_HINT))
        occupied = zone.get("occupied_s")
        empty_s = zone.get("empty_s")
        pct_text = format_occupancy_pct(zone.get("pct"))
        emphasize = zone.get("emphasize")
        if emphasize == "duration" and occupied is not None:
            duration = QLabel(f"Bölgede dolu süre  {format_duration(occupied)}")
            duration.setWordWrap(True)
            duration.setStyleSheet("font-size: 15px; font-weight: 700;")
            foot.addWidget(duration)
            if empty_s is not None:
                foot.addWidget(_muted(f"Boş {format_duration(empty_s)}"))
            if pct_text:
                foot.addWidget(_muted(pct_text))
        else:
            if pct_text:
                pct = QLabel(pct_text)
                pct.setWordWrap(True)
                pct.setStyleSheet("font-size: 15px; font-weight: 700;")
                foot.addWidget(pct)
            else:
                foot.addWidget(_muted("Doluluk yüzdesi yok"))
            if occupied is not None or empty_s is not None:
                duration = QLabel(
                    f"Dolu {format_duration(occupied)} · Boş {format_duration(empty_s)}"
                )
                duration.setWordWrap(True)
                foot.addWidget(duration)

        layout.addWidget(footer)


class ReportDialog(QDialog):
    def __init__(self, parent, loader, people_loader=None, persist_fn=None):
        super().__init__(parent)
        self._loader = loader
        self._people_loader = people_loader or (lambda: {})
        self._persist_fn = persist_fn
        self._bundle = {"occupancy": [], "sessions": [], "visits": [], "workers": []}
        self._worker = None
        self._mode = "camera"
        self._restoring = False
        self._all_recordings = []
        self.setWindowTitle("Raporlar")
        self.setMinimumSize(1100, 680)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel("Raporlar")
        title.setObjectName("titleLabel")
        root.addWidget(title)
        hint = QLabel("Önce neye bakacağını seç, sonra gün ve kaydı seç.")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._label("Ne bakmak istiyorsun?"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("Kamera", "camera")
        self.type_combo.addItem("Kaynaklar", "kaynak")
        self.type_combo.addItem("Kişiler", "person")
        self.type_combo.setMinimumWidth(160)
        row.addWidget(self.type_combo)
        self.item_label = self._label("Kamera")
        row.addWidget(self.item_label)
        self.item_combo = QComboBox()
        self.item_combo.setMinimumWidth(240)
        row.addWidget(self.item_combo, 1)
        root.addLayout(row)

        rec_row = QHBoxLayout()
        rec_row.setSpacing(10)
        rec_row.addWidget(self._label("Gün"))
        self.period_combo = QComboBox()
        self.period_combo.addItem("Tümü", "all")
        self.period_combo.addItem("Bugün", "today")
        self.period_combo.addItem("Dün", "yesterday")
        self.period_combo.addItem("Bu hafta", "week")
        self.period_combo.setMinimumWidth(120)
        rec_row.addWidget(self.period_combo)
        self.recording_search = QLineEdit()
        self.recording_search.setPlaceholderText("Kayıt ara (saat veya tarih)")
        self.recording_search.setMinimumWidth(180)
        rec_row.addWidget(self.recording_search, 1)
        rec_row.addWidget(self._label("Kayıt"))
        self.recording_combo = QComboBox()
        self.recording_combo.setMinimumWidth(280)
        rec_row.addWidget(self.recording_combo, 2)
        root.addLayout(rec_row)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 700;")
        root.addWidget(self.summary)
        self.measure_hint = QLabel(MEASURE_HINT)
        self.measure_hint.setObjectName("mutedLabel")
        self.measure_hint.setWordWrap(True)
        self.measure_hint.hide()
        root.addWidget(self.measure_hint)

        self.status = QLabel("Kayıtlar yükleniyor...")
        self.status.setObjectName("mutedLabel")
        root.addWidget(self.status)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.host = QWidget()
        self.charts = QVBoxLayout(self.host)
        self.charts.setContentsMargins(0, 0, 0, 0)
        self.charts.setSpacing(16)
        self.scroll.setWidget(self.host)
        root.addWidget(self.scroll, 1)

        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        self.item_combo.currentIndexChanged.connect(self._on_item_changed)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        self.recording_search.textChanged.connect(self._on_search_changed)
        self.recording_combo.currentIndexChanged.connect(self._on_recording_changed)

    def _label(self, text):
        label = QLabel(text)
        label.setObjectName("mutedLabel")
        return label

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_title_bar(self)
        if not self._bundle.get("sessions") and not self._bundle.get("occupancy"):
            self._load()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(COLOR_BG))
        grad.setColorAt(0.55, QColor(COLOR_BG_MID))
        grad.setColorAt(1, QColor(COLOR_BG))
        painter.fillRect(self.rect(), grad)
        super().paintEvent(event)

    def _load(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.status.setText("Kayıtlar yükleniyor...")
        self._worker = ReportLoadWorker(self._loader, self)
        self._worker.finished_ok.connect(self.apply_bundle)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_fail(self, message):
        self.status.setText("Kayıtlar okunamadı.")
        show_error(self, "Rapor", f"SQL okunamadı:\n{message}")

    def apply_bundle(self, bundle):
        self._bundle = bundle or {"occupancy": [], "sessions": [], "visits": [], "workers": []}
        self._restoring = True
        self._restore_prefs()
        self._mode = self.type_combo.currentData() or "camera"
        self._fill_item_combo()
        self._restore_item()
        self._fill_recordings()
        self._restore_recording()
        self._restoring = False
        self._render_charts()
        if self.item_combo.currentData():
            self.status.setText("")
        else:
            self.status.setText("Kayıt bulunamadı.")

    def _prefs(self):
        return {
            "type": self.type_combo.currentData() or "camera",
            "item": self.item_combo.currentData() or "",
            "recording": self.recording_combo.currentData() or "",
            "period": self.period_combo.currentData() or "all",
        }

    def _saved_prefs(self):
        config = self._people_loader() or {}
        raw = config.get("REPORT_UI") if isinstance(config, dict) else {}
        return raw if isinstance(raw, dict) else {}

    def _select_combo(self, combo, value):
        if value is None or value == "":
            return False
        for index in range(combo.count()):
            if str(combo.itemData(index) or "") == str(value):
                combo.setCurrentIndex(index)
                return True
        return False

    def _restore_prefs(self):
        prefs = self._saved_prefs()
        if not prefs:
            return
        self._select_combo(self.type_combo, prefs.get("type"))
        self._select_combo(self.period_combo, prefs.get("period"))

    def _persist(self):
        if self._restoring or not self._persist_fn:
            return
        self._persist_fn(self._prefs())

    def _on_type_changed(self):
        if self._restoring:
            return
        self._mode = self.type_combo.currentData() or "camera"
        self._fill_item_combo()
        self._fill_recordings()
        self._render_charts()
        self._persist()

    def _on_item_changed(self):
        if self._restoring:
            return
        self._fill_recordings()
        self._render_charts()
        self._persist()

    def _on_period_changed(self):
        if self._restoring:
            return
        current = self.recording_combo.currentData()
        self._fill_recordings()
        self._select_combo(self.recording_combo, current)
        self._render_charts()
        self._persist()

    def _on_search_changed(self):
        if self._restoring:
            return
        current = self.recording_combo.currentData()
        self._fill_recordings()
        self._select_combo(self.recording_combo, current)
        self._render_charts()

    def _on_recording_changed(self):
        if self._restoring:
            return
        self._render_charts()
        self._persist()

    def _restore_item(self):
        self._select_combo(self.item_combo, self._saved_prefs().get("item"))

    def _restore_recording(self):
        self._select_combo(self.recording_combo, self._saved_prefs().get("recording"))

    def _fill_combo(self, combo, items, empty_label):
        combo.blockSignals(True)
        combo.clear()
        if not items:
            combo.addItem(empty_label, "")
        else:
            for value, label in items:
                combo.addItem(label, value)
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _fill_item_combo(self):
        mode = self.type_combo.currentData() or "camera"
        config = self._people_loader() or {}
        camera_map = config.get("CAMERAS") if isinstance(config, dict) else {}
        if mode == "kaynak":
            self.item_label.setText("Kaynak")
            names = list_kaynak_names(config)
            items = [(name, name) for name in names]
            self._fill_combo(self.item_combo, items, "Önce vardiyada kaynak ekleyin")
        elif mode == "person":
            self.item_label.setText("Kişi")
            items = [(person_id, name) for person_id, name in person_choices(config)]
            self._fill_combo(self.item_combo, items, "Önce vardiyada kişi ekleyin")
        else:
            self.item_label.setText("Kamera")
            items = chart_camera_choices(self._bundle, camera_map)
            self._fill_combo(self.item_combo, items, "Kamera yok")

    def _fill_recordings(self):
        mode = self.type_combo.currentData() or "camera"
        config = self._people_loader() or {}
        camera_map = config.get("CAMERAS") if isinstance(config, dict) else {}
        value = self.item_combo.currentData()
        if mode == "kaynak":
            channels = [item["kanalId"] for item in list_kaynak_locations(config, value)] if value else []
            rows = recording_choice_rows(self._bundle, channels, camera_map) if channels else []
        elif mode == "person":
            person = get_person(config, value) if value else None
            channels = [item["kanalId"] for item in (person or {}).get("assignments") or []]
            rows = recording_choice_rows(self._bundle, channels, camera_map) if channels else []
        else:
            rows = recording_choice_rows(self._bundle, value) if value else []
        self._all_recordings = rows
        items = filter_recording_rows(
            rows,
            self.period_combo.currentData() or "all",
            self.recording_search.text(),
        )
        empty = "Bu aralıkta kayıt yok" if rows else "Kayıt yok"
        self._fill_combo(self.recording_combo, items, empty)

    def _clear_charts(self):
        while self.charts.count():
            item = self.charts.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _placeholder(self, text):
        label = QLabel(text)
        label.setObjectName("mutedLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        self.charts.addWidget(label, 1)

    def _places_text(self, items, camera_key="kameraAdi", zone_key="bolgeAdi"):
        parts = []
        for item in items or []:
            camera = item.get(camera_key) or ""
            zone = item.get(zone_key) or ""
            text = " / ".join(part for part in (camera, zone) if part)
            if text and text not in parts:
                parts.append(text)
        return "  ·  ".join(parts)

    def _set_header(self, text, show_hint=False):
        self.summary.setText(text or "")
        self.measure_hint.setVisible(bool(show_hint))

    def _render_charts(self):
        self._clear_charts()
        people = self._people_loader()
        mode = self.type_combo.currentData() or "camera"
        value = self.item_combo.currentData()
        sid = self.recording_combo.currentData()
        item_name = self.item_combo.currentText() if value else ""
        name = item_name
        recording_label = self.recording_combo.currentText() if sid else ""
        config = people if isinstance(people, dict) else {}

        if mode == "kaynak":
            locations = list_kaynak_locations(config, value) if value else []
            place = self._places_text(locations)
            if not value:
                self._set_header("")
                self._placeholder("Önce vardiyada kaynak ekleyin, sonra buradan seçin.")
                return
            if not sid:
                self._set_header(item_name)
                self.measure_hint.hide()
                if self._all_recordings:
                    self._placeholder(
                        f"{item_name} vardiyada {place or 'atanmış bir bölgede'}. "
                        "Seçilen günde / aramada bu kamerada kayıt yok. Tümü veya başka gün deneyin."
                    )
                elif place:
                    self._placeholder(
                        f"{item_name} vardiyada {place}. Bu kamerada henüz duruş yok."
                    )
                else:
                    self._placeholder(f"{item_name} için vardiyada kamera / bölge yok.")
                return
            payload = kaynak_report(self._bundle, sid, people, value)
            if not payload.get("zones"):
                self._set_header(report_summary_line(mode, item_name, recording_label, payload), True)
                hours = payload.get("shift_hours") or ""
                if payload.get("outside_shift"):
                    self._placeholder(
                        f"Bu kayıt {item_name} için vardiya dışında"
                        + (f" ({hours})." if hours else ".")
                    )
                else:
                    self._placeholder(
                        f"{item_name} vardiyada {place or 'atanmış bir bölgede'}. "
                        "Bu kayıtta o bölge yok — başka kayıt seçin."
                    )
                self.charts.addStretch(1)
                return
        elif mode == "person":
            person = get_person(config, value) if value else None
            assignments = (person or {}).get("assignments") or []
            place = self._places_text(assignments)
            name = (person or {}).get("name") or item_name
            if not value:
                self._set_header("")
                self._placeholder("Önce vardiyada kişi ekleyin, sonra buradan seçin.")
                return
            if not sid:
                self._set_header(name)
                self.measure_hint.hide()
                if self._all_recordings:
                    self._placeholder(
                        f"{name} vardiyada {place or 'atanmış kameralarda'}. "
                        "Seçilen günde / aramada bu kameralarda kayıt yok. Tümü veya başka gün deneyin."
                    )
                elif place:
                    self._placeholder(
                        f"{name} vardiyada {place}. Bu kameralarda henüz duruş yok."
                    )
                else:
                    self._placeholder(f"{name} için vardiyada atama yok.")
                return
            payload = person_report(self._bundle, sid, people, value)
            others = payload.get("other_assignments") or []
            if others:
                extra = QLabel(
                    f"{name} bu kayıttaki bölgeler aşağıda. "
                    + self._places_text(others)
                    + " bu kayıtta yok — o kamera için başka kayıt seç."
                )
                extra.setWordWrap(True)
                extra.setObjectName("mutedLabel")
                self.charts.addWidget(extra)
            if not payload.get("zones"):
                self._set_header(report_summary_line(mode, name, recording_label, payload), True)
                hours = payload.get("shift_hours") or ""
                if payload.get("outside_shift"):
                    self._placeholder(
                        f"Bu kayıt {name} için vardiya dışında"
                        + (f" ({hours})." if hours else ".")
                    )
                else:
                    self._placeholder(
                        f"{name} vardiyada {place or 'atanmış bölgelerde'}. "
                        "Bu kayıtta kişinin bölgesi yok."
                    )
                self.charts.addStretch(1)
                return
        else:
            if not sid:
                self._set_header("")
                if self._all_recordings:
                    self._placeholder("Seçilen günde / aramada kayıt yok. Tümü veya başka gün deneyin.")
                else:
                    self._placeholder("Kamera ve kayıt seçin.")
                return
            payload = session_zone_charts(self._bundle, sid, people)
            if not payload.get("zones"):
                self._set_header(report_summary_line(mode, item_name, recording_label, payload))
                self._placeholder("Bu kayıtta bölge yok.")
                return

        self._set_header(
            report_summary_line(mode, item_name if mode != "person" else name, recording_label, payload),
            mode in ("kaynak", "person"),
        )
        start = payload.get("start")
        end = payload.get("end")
        zones = payload.get("zones") or []
        if mode == "person" and payload.get("combined"):
            card = ZoneReportCard(payload["combined"], start, end, QColor("#C3A6FF"))
            self.charts.addWidget(card)
        if mode == "camera" and len(zones) > 1:
            grid_host = QWidget()
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(16)
            for index, zone in enumerate(zones):
                card = ZoneReportCard(zone, start, end, ZONE_COLORS[index % len(ZONE_COLORS)])
                grid.addWidget(card, index // 2, index % 2)
            self.charts.addWidget(grid_host)
        else:
            for index, zone in enumerate(zones):
                card = ZoneReportCard(zone, start, end, ZONE_COLORS[index % len(ZONE_COLORS)])
                self.charts.addWidget(card)
        self.charts.addStretch(1)

    def closeEvent(self, event):
        self._persist()
        super().closeEvent(event)
