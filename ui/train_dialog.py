import shutil
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from training.hard_frames import find_latest_hard_dir
from training.prepare import REQUIRED_CLASS, inspect_dataset, prepare_dataset
from ui.dialogs import show_error, show_info, show_warning
from ui.extract_dialog import ExtractFramesDialog
from ui.label_dialog import LabelDialog, list_images
from ui.theme import (
    COLOR_ACCENT,
    COLOR_BG,
    COLOR_BG_MID,
    COLOR_ERROR,
    apply_dark_title_bar,
)

HELP_HTML = f"""
<h3 style="margin-top:0;">Model Eğit — ne işe yarar?</h3>
<p>Bazı kameralarda insanlar kaçıyor veya yanlış kutulanıyorsa, o kameradan
<b>kutulanmış fotoğrafları</b> mevcut modele öğretiriz. D1 kamerasında daha önce
öğretilen bilgi modelin içinde durur; o klasörü tekrar yüklemeniz gerekmez.</p>

<h3>Adım adım ne yapacaksınız?</h3>
<ol>
<li>Aşağıdan <b>kötü tanıyan kamerayı</b> seçin (sadece not içindir).</li>
<li><b>Bugünün zor kareleri</b> takipte biriken belirsiz/kayıp anları açar.
İsterseniz videodan kare çıkarın veya klasör seçin. Kutuları çizin; sınıf otomatik <b>insan</b>.</li>
<li><b>Klasörü kontrol et</b> yeşil/uyarı metnini okuyun.</li>
<li>İsterseniz hızlı model kutusunu işaretleyin. Yanındaki <b>i</b> üzerine gelince fayda ve zararlar görünür.</li>
<li><b>Eğitimi başlat</b> deyin ve bitene kadar bekleyin (çoğunlukla 30–90 dk).</li>
</ol>

<h3>Klasör nasıl olmalı?</h3>
<p>İki şekil de olur:</p>
<ul>
<li>Aynı klasörde <b>kapı.jpg</b> ve yanında <b>kapı.txt</b> (isimler aynı olmalı), veya</li>
<li><b>images</b> klasöründe fotoğraflar, <b>labels</b> klasöründe aynı isimli .txt dosyaları
(Roboflow YOLO dışa aktarımı da budur).</li>
</ul>
<p>Sadece fotoğraf yüklemek yetmez. Kutusu (.txt) olmayan görsel eğitime girmez.</p>

<h3>Sınıf adı ne olmalı?</h3>
<p>İç etiketleyicide sınıf otomatik
<b style="color:{COLOR_ACCENT};">{REQUIRED_CLASS}</b> atanır.
Dışarıdan klasör getirirseniz ad yine tam olarak bu olmalı
(person, human, İnsan yazmayın).</p>
<p>80–200 <b>çeşitli</b> kare yeter. Aynı sahneden ardışık 1000 kare pek işe yaramaz.</p>

<h3>Dikkat</h3>
<ul>
<li>Canlı takip çalışırken eğitim açılmaz; önce takibi durdurun.</li>
<li>Eğitim bitince <b>Kaydet ve Başlat</b> ile takibi yeniden açın; yeni model o zaman yüklenir.</li>
<li>Eski model silinmez, <b>models/backups</b> klasörüne kopyalanır.</li>
</ul>
"""

ENGINE_WARNING = (
    "Hızlı model (best.engine) nedir?\n"
    "Aynı yeni modelin bu bilgisayarın ekran kartına özel, daha akıcı kopyasıdır. "
    "P-HAS önce onu açar.\n\n"
    "Faydası:\n"
    "- 2–4 kamerada takip daha akıcı olur, ekran kartı daha az zorlanır.\n"
    "- Yeni eğitilen best.pt ile hızlı kopya aynı olur; eski (eğitilmemiş) engine kullanılmaz.\n\n"
    "Zararı / riski:\n"
    "- Üretim eğitime ek 5–20 dakika sürer; bu sırada GPU meşguldür, takip açılamaz.\n"
    "- Kurulum bozulabilir (bellek dolu, sürücü, TensorRT). Zaman kaybı olur.\n"
    "- Bu dosya yalnızca BU ekran kartında çalışır. Başka PC’ye kopyalanırsa açılmaz veya hata verir.\n"
    "- Başarılı olursa eski engine silinir; yanlışlıkla işaretlerseniz eski hızlı kopyayı kaybedersiniz "
    "(best.pt yedeği durur).\n"
    "- Üretim başarısız olsa bile yeni eğitim best.pt olarak kalır; takip bir süre daha yavaş olabilir.\n\n"
    "Güvenli sıra: yeni hızlı model yazılmadan eski silinmez. Kutuyu işaretlemezseniz "
    "eski engine yedeğe alınır, program yeni best.pt ile açılır."
)


class ModelTrainDialog(QDialog):
    def __init__(self, parent, cameras, app_dir, is_busy=None):
        super().__init__(parent)
        self._cameras = cameras or []
        self._app_dir = Path(app_dir)
        self._is_busy = is_busy or (lambda: False)
        self._folder = ""
        self._dataset_ok = False
        self._process = None
        self._phase = None
        self._new_weights = None
        self._engine_path = None
        self._engine_fail = None
        self._export_engine = False
        self._stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.setWindowTitle("Model Eğit")
        self.setMinimumSize(760, 720)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowContextHelpButtonHint
        self.setWindowFlags(flags)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(10)

        title = QLabel("Model Eğit")
        title.setObjectName("titleLabel")
        root.addWidget(title)

        help_box = QTextBrowser()
        help_box.setOpenExternalLinks(False)
        help_box.setHtml(HELP_HTML)
        help_box.setMinimumHeight(200)
        help_box.setMaximumHeight(240)
        root.addWidget(help_box)

        step = QFrame()
        step.setObjectName("card")
        form = QVBoxLayout(step)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(8)

        form.addWidget(self._label("1. İnsanları kötü tanıyan kamerayı seçin"))
        self.camera_combo = QComboBox()
        self.camera_combo.setMaxVisibleItems(8)
        self.camera_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if self._cameras:
            for cam in self._cameras:
                name = cam.get("name") or f"Kamera {cam.get('id')}"
                self.camera_combo.addItem(f"{name}  (kanal {cam.get('id')})", cam)
        else:
            self.camera_combo.addItem("Listede kamera yok — önce Kameraları Bul / Yenile", None)
        form.addWidget(self.camera_combo)

        form.addWidget(self._label("2. Fotoğrafları hazırlayın"))
        self.folder_label = QLabel("Henüz klasör yok — videodan kare çıkarın veya klasör seçin")
        self.folder_label.setObjectName("mutedLabel")
        self.folder_label.setWordWrap(True)
        form.addWidget(self.folder_label)
        folder_row = QHBoxLayout()
        self.extract_btn = QPushButton("Videodan kare çıkar")
        self.extract_btn.setObjectName("secondaryButton")
        self.extract_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.extract_btn.clicked.connect(self._extract_frames)
        self.label_btn = QPushButton("Kareleri etiketle")
        self.label_btn.setObjectName("secondaryButton")
        self.label_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label_btn.clicked.connect(self._label_frames)
        self.pick_btn = QPushButton("Klasör seç…")
        self.pick_btn.setObjectName("secondaryButton")
        self.pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_btn.clicked.connect(self._pick_folder)
        folder_row.addWidget(self.extract_btn)
        folder_row.addWidget(self.label_btn)
        folder_row.addWidget(self.pick_btn)
        form.addLayout(folder_row)
        self.hard_btn = QPushButton("Bugünün zor kareleri")
        self.hard_btn.setObjectName("secondaryButton")
        self.hard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hard_btn.setToolTip(
            "Takip açıkken kaydedilen ham zor kareleri açar. "
            "Önce o günün klasörüne bakar; yoksa bu kameranın son kaydını alır."
        )
        self.hard_btn.clicked.connect(self._open_hard_frames)
        form.addWidget(self.hard_btn)

        form.addWidget(self._label("3. Klasörü kontrol edin"))
        check_row = QHBoxLayout()
        self.check_btn = QPushButton("Klasörü kontrol et")
        self.check_btn.setObjectName("secondaryButton")
        self.check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_btn.clicked.connect(self._check_folder)
        check_row.addWidget(self.check_btn)
        check_row.addStretch()
        form.addLayout(check_row)
        self.check_label = QLabel("Kontrol henüz yapılmadı.")
        self.check_label.setWordWrap(True)
        self.check_label.setObjectName("mutedLabel")
        form.addWidget(self.check_label)

        form.addWidget(self._label("4. İsteğe bağlı: hızlı model"))
        engine_row = QHBoxLayout()
        engine_row.setSpacing(8)
        self.engine_check = QCheckBox(
            "Eğitim bitince hızlı modeli de yenile (best.engine). Eski engine silinsin."
        )
        self.engine_info = QLabel("i")
        self.engine_info.setFixedSize(22, 22)
        self.engine_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.engine_info.setCursor(Qt.CursorShape.PointingHandCursor)
        self.engine_info.setToolTip(ENGINE_WARNING)
        self.engine_info.setToolTipDuration(60000)
        self.engine_info.setStyleSheet(
            f"QLabel {{ background: transparent; color: {COLOR_ACCENT}; "
            f"border: 1px solid {COLOR_ACCENT}; border-radius: 11px; "
            f"font-weight: 700; font-size: 13px; }}"
        )
        engine_row.addWidget(self.engine_check, 1)
        engine_row.addWidget(self.engine_info, 0, Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(engine_row)

        root.addWidget(step)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Eğitim başlayınca burada sade ilerleme yazılır…")
        self.log.setMinimumHeight(120)
        root.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton("İptal")
        self.cancel_btn.setObjectName("secondaryButton")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.start_btn = QPushButton("Eğitimi başlat")
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_train)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.start_btn)
        root.addLayout(buttons)

    def _label(self, text):
        widget = QLabel(text)
        widget.setStyleSheet(f"font-weight: 700; color: {COLOR_ACCENT};")
        return widget

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

    def closeEvent(self, event):
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            show_warning(
                self,
                "Eğitim sürüyor",
                "Önce İptal deyin veya işlemin bitmesini bekleyin. "
                "Hızlı model üretilirken pencereyi kapatmayın.",
            )
            event.ignore()
            return
        super().closeEvent(event)

    def _camera_name(self):
        camera = self.camera_combo.currentData()
        if camera:
            return camera.get("name") or f"Kamera {camera.get('id')}"
        return "kamera"

    def _set_folder(self, path, check=True):
        self._folder = str(path or "")
        self.folder_label.setText(self._folder or "Henüz klasör yok — videodan kare çıkarın veya klasör seçin")
        self._dataset_ok = False
        self.start_btn.setEnabled(False)
        if check and self._folder:
            self._check_folder()

    def _extract_frames(self):
        dialog = ExtractFramesDialog(self, self._app_dir, self._camera_name())
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.output_dir:
            return
        self._set_folder(dialog.output_dir, check=False)
        self.check_label.setText(
            f"{dialog.saved_count} kare yazıldı. Şimdi kutuları çizin; sınıf otomatik «{REQUIRED_CLASS}»."
        )
        self.check_label.setStyleSheet("")
        self._label_frames()

    def _label_frames(self):
        if not self._folder:
            show_warning(self, "Klasör yok", "Önce videodan kare çıkarın veya fotoğraflı klasörü seçin.")
            return
        if not list_images(self._folder):
            show_warning(
                self,
                "Fotoğraf yok",
                "Bu klasörde jpg/png yok. Önce Videodan kare çıkar deyin.",
            )
            return
        dialog = LabelDialog(self, self._folder)
        dialog.exec()
        self._check_folder()

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Etiketli fotoğraf klasörünü seçin")
        if not path:
            return
        self._set_folder(path)

    def _open_hard_frames(self):
        folder = find_latest_hard_dir(self._app_dir, self._camera_name())
        if not folder:
            show_warning(
                self,
                "Zor kare yok",
                "Bu kamera için henüz zor kare yok. "
                "Ayarlarda kutu açıkken takibi bir süre çalıştırın; "
                "model emin olmayınca ham fotoğraf datasets/zor altına yazılır.",
            )
            return
        self._set_folder(folder, check=False)
        self.check_label.setText(
            f"Zor kare klasörü: {folder}. Taslak kutuları kontrol edip düzeltin."
        )
        self.check_label.setStyleSheet("")
        self._label_frames()

    def _check_folder(self):
        if not self._folder:
            show_warning(self, "Klasör yok", "Önce etiketli fotoğrafların durduğu klasörü seçin.")
            return
        info = inspect_dataset(self._folder)
        self._dataset_ok = bool(info["ok"])
        self.start_btn.setEnabled(self._dataset_ok and self._process is None)
        if info["ok"]:
            names = ", ".join(info.get("class_names") or [REQUIRED_CLASS])
            text = (
                f"Tamam: {info['labeled_count']} etiketli fotoğraf bulundu. "
                f"Sınıf adı: {names}."
            )
            if info.get("warnings"):
                text += "\n" + "\n".join(info["warnings"])
            self.check_label.setText(text)
            self.check_label.setStyleSheet(f"color: {COLOR_ACCENT};")
        else:
            self.check_label.setText("\n".join(info["errors"]) or "Klasör uygun değil.")
            self.check_label.setStyleSheet(f"color: {COLOR_ERROR};")

    def _append_log(self, text):
        if not text:
            return
        self.log.appendPlainText(text)
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def _start_train(self):
        if self._is_busy():
            show_warning(
                self,
                "Takip açık",
                "Canlı takip çalışırken model eğitilemez. "
                "Önce takibi durdurun, sonra bu pencereyi tekrar açın.",
            )
            return
        if not self._dataset_ok:
            show_warning(self, "Kontrol gerekli", "Önce klasörü kontrol edin ve hataları düzeltin.")
            return
        weights = self._app_dir / "best.pt"
        if not weights.is_file():
            show_error(
                self,
                "Model yok",
                "best.pt bulunamadı. Eğitim mevcut modelin üzerine yazılır; "
                "bu dosya proje klasöründe olmalı.",
            )
            return
        camera = self.camera_combo.currentData()
        if not camera:
            show_warning(
                self,
                "Kamera seçin",
                "Listeden kötü tanıyan kamerayı seçin. Yoksa önce Kameraları Bul / Yenile deyin.",
            )
            return

        self._stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        work = self._app_dir / "training" / f"job_{self._stamp}"
        try:
            info = prepare_dataset(self._folder, work / "dataset")
        except Exception as exc:
            show_error(self, "Klasör hazırlanamadı", str(exc))
            return

        self._export_engine = self.engine_check.isChecked()
        self._new_weights = None
        self.start_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.extract_btn.setEnabled(False)
        self.label_btn.setEnabled(False)
        self.pick_btn.setEnabled(False)
        self.hard_btn.setEnabled(False)
        self._append_log(
            f"Kamera: {self.camera_combo.currentText()}\n"
            f"{info['train_count']} eğitim + {info['val_count']} kontrol fotoğrafı hazırlandı."
        )
        self._run_process(
            [
                sys.executable,
                str(self._app_dir / "training" / "run_train.py"),
                "--data",
                info["yaml_path"],
                "--weights",
                str(weights),
                "--project",
                str(work / "runs"),
                "--name",
                "fine-tune",
            ]
            + (["--export-engine"] if self._export_engine else []),
            work,
        )

    def _run_process(self, args, work_dir):
        self._phase = "train"
        process = QProcess(self)
        process.setWorkingDirectory(str(self._app_dir))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = process.processEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._on_output)
        process.finished.connect(self._on_finished)
        self._process = process
        process.start(args[0], args[1:])
        if not process.waitForStarted(8000):
            self._process = None
            self._unlock()
            show_error(self, "Başlatılamadı", "Eğitim süreci açılamadı. venv Python’unu kontrol edin.")

    def _on_output(self):
        if self._process is None:
            return
        raw = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            text = line.strip()
            if not text:
                continue
            if text.startswith("PHAS_EPOCH"):
                self._append_log("Öğrenme adımı " + text.split(" ", 1)[-1])
            elif text.startswith("PHAS_STATUS"):
                self._append_log(text.split(" ", 1)[-1])
            elif text.startswith("PHAS_WEIGHTS"):
                self._new_weights = text.split(" ", 1)[-1].strip()
                self._append_log("Yeni model dosyası hazır.")
            elif text.startswith("PHAS_ENGINE "):
                self._append_log("Hızlı model dosyası hazır.")
                self._engine_path = text.split(" ", 1)[-1].strip()
            elif text.startswith("PHAS_ENGINE_FAIL"):
                self._append_log("Hızlı model üretilemedi: " + text.split(" ", 1)[-1])
                self._engine_fail = text.split(" ", 1)[-1]
            elif text.startswith("PHAS_ERROR"):
                self._append_log(text.split(" ", 1)[-1])
            elif text.startswith("PHAS_DONE"):
                self._append_log("Eğitim süreci bitti.")

    def _on_finished(self, exit_code, _status):
        process = self._process
        self._process = None
        engine_path = getattr(self, "_engine_path", None)
        engine_fail = getattr(self, "_engine_fail", None)
        self._engine_path = None
        self._engine_fail = None
        if exit_code != 0 and not self._new_weights:
            self._unlock()
            show_error(
                self,
                "Eğitim tamamlanamadı",
                "Model değişmedi. Klasörü ve ekran kartını kontrol edip tekrar deneyin.",
            )
            return
        try:
            installed, engine_note = self._install_results(self._new_weights, engine_path, engine_fail)
        except Exception as exc:
            self._unlock()
            show_error(self, "Kayıt hatası", str(exc))
            return
        self._unlock()
        if installed:
            if engine_note == "ok":
                show_info(
                    self,
                    "Model güncellendi",
                    "Yeni model ve hızlı kopya kaydedildi.\n"
                    "Ayarlara dönüp Kaydet ve Başlat deyin; yeni model o zaman yüklenir.",
                )
            elif engine_note == "fail":
                show_error(
                    self,
                    "Hızlı model kurulamadı",
                    "Yeni eğitim best.pt olarak kaydedildi. Takip biraz yavaş olabilir. "
                    "Eski hızlı model yedekte duruyor, silinmedi.\n"
                    "Kaydet ve Başlat ile takibi yeniden açın.",
                )
            else:
                show_info(
                    self,
                    "Model güncellendi",
                    "Yeni model kaydedildi. Eski hızlı kopya yedeğe alındı; "
                    "program yeni eğitimi kullanacak.\n"
                    "Kaydet ve Başlat ile takibi yeniden açın.",
                )

    def _install_results(self, new_weights, engine_path, engine_fail):
        if not new_weights or not Path(new_weights).is_file():
            raise RuntimeError("Yeni model dosyası bulunamadı.")
        backups = self._app_dir / "models" / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        live_pt = self._app_dir / "best.pt"
        live_engine = self._app_dir / "best.engine"
        if live_pt.is_file():
            shutil.copy2(live_pt, backups / f"best_{self._stamp}.pt")
        shutil.copy2(new_weights, live_pt)

        engine_note = "skip"
        if live_engine.is_file():
            backup_engine = backups / f"best.engine_{self._stamp}"
            shutil.copy2(live_engine, backup_engine)
            live_engine.unlink()

        if self._export_engine:
            if engine_path and Path(engine_path).is_file():
                shutil.copy2(engine_path, live_engine)
                if self.engine_check.isChecked():
                    old = backups / f"best.engine_{self._stamp}"
                    if old.is_file():
                        old.unlink()
                engine_note = "ok"
            else:
                engine_note = "fail"
        return True, engine_note

    def _unlock(self):
        self.start_btn.setEnabled(self._dataset_ok)
        self.check_btn.setEnabled(True)
        self.extract_btn.setEnabled(True)
        self.label_btn.setEnabled(True)
        self.pick_btn.setEnabled(True)
        self.hard_btn.setEnabled(True)
        self._phase = None

    def _on_cancel(self):
        if self._process is None:
            self.reject()
            return
        if self._export_engine and self._new_weights:
            show_warning(
                self,
                "Hızlı model üretiliyor",
                "Bu aşamada kesmek risklidir. Mümkünse bitmesini bekleyin. "
                "Yine de durdurursanız yeni eğitim kaydedilebilir; hızlı model yarıda kalır.",
            )
        self._append_log("İptal isteniyor…")
        self._process.terminate()
        if not self._process.waitForFinished(4000):
            self._process.kill()
