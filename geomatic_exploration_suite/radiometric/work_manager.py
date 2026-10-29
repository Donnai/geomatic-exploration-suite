# -*- coding: utf-8 -*-
"""Work Manager tab for session-aware radiometric processing.

The manager keeps repeated QC runs isolated under:
    <processing_folder>/Sessions/<session_id>/

It intentionally does not move the user's original input data. It only redirects
outputs and auto-detection to the active session folder when a session is active.
"""
import os
import json
import shutil
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QInputDialog, QCheckBox
)
from qgis.PyQt.QtCore import Qt

try:
    from .core.paths import ensure_dir
except Exception:
    def ensure_dir(path):
        os.makedirs(path, exist_ok=True)
        return path


class WorkManagerTab(QWidget):
    """Central session/QC-run manager.

    It is deliberately lightweight. The main panel remains responsible for actual
    processing; this tab only controls where outputs go and records run labels.
    """

    def __init__(self, parent=None, state=None):
        super().__init__(parent)
        self.parent_plugin = parent
        self.state = state
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel('Work Manager - Sessions, QC Runs and Presets')
        title.setStyleSheet('font-weight:bold; font-size:14px;')
        layout.addWidget(title)
        intro = QLabel(
            'Use one session per processing attempt. This prevents repeated QC runs from mixing '
            'ratio rasters, combined maps and extracted polygons. Each session has its own output folders.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        base_group = QGroupBox('Base Processing Folder')
        base_row = QHBoxLayout(base_group)
        self.base_label = QLabel('(not set)')
        self.base_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        base_row.addWidget(QLabel('Base folder:'))
        base_row.addWidget(self.base_label, 1)
        set_base = QPushButton('Set Base Folder...')
        set_base.clicked.connect(self._set_base_folder)
        base_row.addWidget(set_base)
        refresh = QPushButton('Refresh Sessions')
        refresh.clicked.connect(self.refresh_session_list)
        base_row.addWidget(refresh)
        layout.addWidget(base_group)

        sess_group = QGroupBox('Session Management')
        sess_row = QHBoxLayout(sess_group)
        self.session_combo = QComboBox()
        self.session_combo.setMinimumWidth(320)
        self.session_combo.currentTextChanged.connect(self.switch_session)
        sess_row.addWidget(QLabel('Active session:'))
        sess_row.addWidget(self.session_combo, 1)
        for txt, cb in [
            ('New', self.new_session), ('Rename', self.rename_session),
            ('Clone', self.clone_session), ('Delete', self.delete_session)
        ]:
            b = QPushButton(txt); b.clicked.connect(cb); sess_row.addWidget(b)
        layout.addWidget(sess_group)

        qc_group = QGroupBox('QC Run Naming')
        qc_row = QHBoxLayout(qc_group)
        self.qc_run_name = QLineEdit()
        self.qc_run_name.setPlaceholderText('Example: QC01_NORMAL, QC02_HRAI_TUNED, QC03_RRAI_STRICT')
        qc_row.addWidget(QLabel('QC run label:'))
        qc_row.addWidget(self.qc_run_name, 1)
        apply = QPushButton('Apply')
        apply.clicked.connect(self.apply_qc_name)
        qc_row.addWidget(apply)
        layout.addWidget(qc_group)

        preset_group = QGroupBox('Workflow Presets')
        preset_row = QHBoxLayout(preset_group)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([
            'QC01 Normal/base - balanced RRAI-U and HRAI-KU',
            'QC02 Stable guard - P2 denominator and P2-P98 ratio clipping',
            'QC03 HRAI-KU tuned - source-side / alteration emphasis',
            'QC04 RRAI-U strict - uranium-enrichment core',
            'Custom / manual settings'
        ])
        preset_row.addWidget(QLabel('Preset:'))
        preset_row.addWidget(self.preset_combo, 1)
        apply_preset = QPushButton('Apply Preset')
        apply_preset.clicked.connect(self.apply_preset)
        preset_row.addWidget(apply_preset)
        save = QPushButton('Save Current Settings JSON')
        save.clicked.connect(self.save_current_settings)
        preset_row.addWidget(save)
        layout.addWidget(preset_group)

        self.require_session = QCheckBox('Require active session before processing')
        self.require_session.setChecked(True)
        layout.addWidget(self.require_session)

        self.session_table = QTableWidget()
        self.session_table.setColumnCount(4)
        self.session_table.setHorizontalHeaderLabels(['Folder', 'File', 'Modified', 'Path'])
        self.session_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(QLabel('Active Session Outputs'))
        layout.addWidget(self.session_table, 1)

        clean = QPushButton('Clean Temporary Files in Active Session')
        clean.clicked.connect(self.clean_temp_files)
        layout.addWidget(clean)

        self.refresh_from_state()

    def refresh_from_state(self):
        base = getattr(self.state, 'processing_folder', '') if self.state else ''
        self.base_label.setText(base or '(not set)')
        self.refresh_session_list()
        q = getattr(self.state, 'qc_run_label', '') if self.state else ''
        if q and not self.qc_run_name.text():
            self.qc_run_name.setText(q)

    def _set_base_folder(self):
        start = getattr(self.state, 'processing_folder', '') if self.state else ''
        folder = QFileDialog.getExistingDirectory(self, 'Select base radiometric processing folder', start)
        if folder and self.parent_plugin:
            self.parent_plugin.folder_edit.setText(folder)
            self.base_label.setText(folder)
            self.refresh_session_list()

    def _sessions_root(self):
        base = getattr(self.state, 'processing_folder', '') if self.state else ''
        if not base:
            return ''
        return ensure_dir(os.path.join(base, 'Sessions'))

    def _safe_name(self, name):
        safe = ''.join(c if c.isalnum() or c in ('_', '-', '.') else '_' for c in (name or '').strip())
        return safe or datetime.now().strftime('session_%Y%m%d_%H%M%S')

    def _session_folder(self, name=None):
        root = self._sessions_root()
        if not root:
            return ''
        sess = self._safe_name(name or self.session_combo.currentText())
        return os.path.join(root, sess)

    def _create_structure(self, folder):
        for sub in [
            '00_Stats_and_Logs', '00_Stats_Logs', '01_Preprocessed', '02_Filtered',
            '03_Ratio_Maps', '04_Normalised_Ratio_Maps', '04_Log_Ratio_Maps',
            '04_ZLog_Ratio_Maps', '05_Ratio_Threshold_Class_Maps',
            '06_Ratio_Map_Anomaly_Extraction', '08_Combined_Threshold_Maps',
            '09_Combined_Map_Anomaly_Extraction', 'workflow_settings', 'exports'
        ]:
            ensure_dir(os.path.join(folder, sub))

    def refresh_session_list(self):
        root = self._sessions_root()
        if not root:
            self.session_combo.clear(); return
        ensure_dir(root)
        sessions = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
        sessions.sort()
        current = getattr(self.state, 'processing_session', '') if self.state else ''
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        self.session_combo.addItems(sessions)
        if current in sessions:
            self.session_combo.setCurrentText(current)
        self.session_combo.blockSignals(False)
        if current in sessions:
            self.populate_session_table(current)
        elif sessions:
            self.session_combo.setCurrentIndex(0)
            self.switch_session(sessions[0])

    def new_session(self):
        if not self._sessions_root():
            QMessageBox.warning(self, 'No base folder', 'Set a base processing folder first.')
            return
        default = 'session_' + datetime.now().strftime('%Y%m%d_%H%M%S')
        name, ok = QInputDialog.getText(self, 'New session', 'Session name:', text=default)
        if not ok:
            return
        safe = self._safe_name(name)
        folder = self._session_folder(safe)
        if os.path.exists(folder):
            QMessageBox.warning(self, 'Session exists', f'The session already exists:\n{safe}')
            return
        ensure_dir(folder)
        self._create_structure(folder)
        self.refresh_session_list()
        self.session_combo.setCurrentText(safe)
        self.switch_session(safe)

    def switch_session(self, session_name):
        if not session_name or not self.state:
            return
        safe = self._safe_name(session_name)
        folder = self._session_folder(safe)
        if not os.path.isdir(folder):
            return
        self.state.processing_session = safe
        self.state.session_folder = folder
        if self.parent_plugin:
            try:
                self.parent_plugin._sync_folder_state()
            except Exception:
                pass
        self.populate_session_table(safe)

    def rename_session(self):
        old = self.session_combo.currentText()
        if not old:
            return
        new, ok = QInputDialog.getText(self, 'Rename session', 'New session name:', text=old)
        if not ok or not new:
            return
        new = self._safe_name(new)
        old_folder = self._session_folder(old)
        new_folder = self._session_folder(new)
        if os.path.exists(new_folder):
            QMessageBox.warning(self, 'Exists', f'Session already exists: {new}')
            return
        os.rename(old_folder, new_folder)
        if self.state:
            self.state.processing_session = new
            self.state.session_folder = new_folder
        self.refresh_session_list(); self.session_combo.setCurrentText(new)

    def clone_session(self):
        src = self.session_combo.currentText()
        if not src:
            return
        default = f'{src}_clone_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        name, ok = QInputDialog.getText(self, 'Clone session', 'New clone name:', text=default)
        if not ok:
            return
        dst_name = self._safe_name(name)
        src_folder = self._session_folder(src)
        dst_folder = self._session_folder(dst_name)
        if os.path.exists(dst_folder):
            QMessageBox.warning(self, 'Exists', f'Session already exists: {dst_name}')
            return
        shutil.copytree(src_folder, dst_folder)
        self.refresh_session_list(); self.session_combo.setCurrentText(dst_name)
        self.switch_session(dst_name)

    def delete_session(self):
        sess = self.session_combo.currentText()
        if not sess:
            return
        ans = QMessageBox.question(self, 'Delete session', f'Delete session and all outputs?\n\n{sess}', QMessageBox.Yes | QMessageBox.No)
        if ans == QMessageBox.Yes:
            shutil.rmtree(self._session_folder(sess), ignore_errors=True)
            if self.state and self.state.processing_session == sess:
                self.state.processing_session = ''
                self.state.session_folder = ''
            self.refresh_session_list()

    def apply_qc_name(self):
        name = self._safe_name(self.qc_run_name.text())
        if self.state:
            self.state.qc_run_label = name
        if self.parent_plugin and getattr(self.parent_plugin, 'stage4_widget', None) is not None:
            w = self.parent_plugin.stage4_widget
            if hasattr(w, 'run_id_edit'):
                w.run_id_edit.setText(name)
        QMessageBox.information(self, 'QC run label applied', f'QC run label set to: {name}')

    def apply_preset(self):
        preset = self.preset_combo.currentText()
        if not self.parent_plugin:
            return
        w = getattr(self.parent_plugin, 'stage4_widget', None)
        if w is not None:
            if 'HRAI-KU tuned' in preset:
                if hasattr(w, 'hrai_weight_preset'):
                    w.hrai_weight_preset.setCurrentIndex(3)
                if hasattr(w, 'hrai_combination_method'):
                    w.hrai_combination_method.setCurrentText('Weighted mean')
                if hasattr(w, 'run_id_edit'):
                    w.run_id_edit.setText('QC03_HRAI_KU_TUNED')
                self.qc_run_name.setText('QC03_HRAI_KU_TUNED')
            elif 'RRAI-U strict' in preset:
                if hasattr(w, 'run_id_edit'):
                    w.run_id_edit.setText('QC04_RRAI_U_STRICT')
                self.qc_run_name.setText('QC04_RRAI_U_STRICT')
            elif 'Stable guard' in preset:
                if hasattr(w, 'run_id_edit'):
                    w.run_id_edit.setText('QC02_STABLE_GUARD')
                self.qc_run_name.setText('QC02_STABLE_GUARD')
            else:
                if hasattr(w, 'run_id_edit'):
                    w.run_id_edit.setText('QC01_NORMAL')
                self.qc_run_name.setText('QC01_NORMAL')
        self.apply_qc_name()

    def save_current_settings(self):
        folder = self._session_folder()
        if not folder:
            QMessageBox.warning(self, 'No session', 'Create or select a session first.')
            return
        ensure_dir(os.path.join(folder, 'workflow_settings'))
        settings = {}
        if self.parent_plugin and hasattr(self.parent_plugin, '_collect_workflow_settings'):
            try:
                settings = self.parent_plugin._collect_workflow_settings()
            except Exception as e:
                settings = {'error_collecting_settings': str(e)}
        settings['processing_session'] = getattr(self.state, 'processing_session', '') if self.state else ''
        settings['session_folder'] = getattr(self.state, 'session_folder', '') if self.state else ''
        settings['qc_run_label'] = getattr(self.state, 'qc_run_label', '') if self.state else ''
        path = os.path.join(folder, 'workflow_settings', f'workflow_settings_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        QMessageBox.information(self, 'Settings saved', path)

    def populate_session_table(self, session_name=None):
        folder = self._session_folder(session_name)
        rows = []
        if folder and os.path.isdir(folder):
            for root, _, files in os.walk(folder):
                for f in files:
                    if f.lower().endswith(('.tif', '.tiff', '.gpkg', '.csv', '.json', '.png', '.txt')):
                        p = os.path.join(root, f)
                        rows.append((os.path.basename(root), f, datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d %H:%M'), p))
        rows.sort(key=lambda r: r[2], reverse=True)
        self.session_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                item = QTableWidgetItem(str(val))
                if j == 3:
                    item.setToolTip(str(val))
                self.session_table.setItem(i, j, item)

    def clean_temp_files(self):
        folder = self._session_folder()
        if not folder:
            return
        count = 0
        for root, _, files in os.walk(folder):
            for f in files:
                if f.startswith('temp_') or f.endswith(('.tmp', '.lock', '.aux.xml')):
                    try:
                        os.remove(os.path.join(root, f)); count += 1
                    except Exception:
                        pass
        QMessageBox.information(self, 'Cleanup complete', f'Removed {count} temporary/support files.')
