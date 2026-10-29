# -*- coding: utf-8 -*-
"""Tabbed dock UI for the Radiometric Analysis plugin."""
import os
import csv
import json
import shutil
import numpy as np
from osgeo import gdal

import processing
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QFont, QIcon, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QMessageBox, QLineEdit, QFileDialog, QTabWidget, QGroupBox, QGridLayout,
    QSizePolicy, QScrollArea, QComboBox, QCheckBox, QDoubleSpinBox,
    QSpinBox, QTextEdit, QTextBrowser, QDialog, QProgressBar, QApplication, QDockWidget, QToolButton, QStyle
)
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer, QgsWkbTypes, QgsMapLayer,
    QgsProcessingFeedback
)

from .algs.registry import resolve_algorithm_id_by_step
from .threshold_review_dialog import ThresholdReviewDialog
from .combined_review_dialog import CombinedAnalysisDialog
from .algs import alg_01_preprocessing_data_conditioning as prealg
from .algs.utils import write_stats_csv, append_session_log, ensure_stats_log_structure
from .core.paths import ensure_dir, make_available_path
from .core.raster_io import read_raster, write_raster, safe_divide, safe_divide_chunked
from .core.raster_stats import array_stats
from .core.styling import load_raster_pseudocolor_quantile
from .validation_tools import run_synthetic_validation, validate_processing_folder

from .state import RadiometricWorkflowState
from .work_manager import WorkManagerTab
try:
    from .ui.widgets import CollapsibleBox
except Exception:
    class CollapsibleBox(QGroupBox):
        def __init__(self, title='', checked=True):
            super().__init__(title)
            self.setCheckable(True)
            self.setChecked(checked)
            self.toggled.connect(self._on_toggled)
        def setContentLayout(self, layout):
            self.setLayout(layout)
            self._on_toggled(self.isChecked())
        def _on_toggled(self, checked):
            lay = self.layout()
            if not lay:
                return
            for i in range(lay.count()):
                item = lay.itemAt(i)
                w = item.widget()
                if w:
                    w.setVisible(checked)
                elif item.layout():
                    self._set_layout_visible(item.layout(), checked)
        def _set_layout_visible(self, layout, visible):
            for i in range(layout.count()):
                item = layout.itemAt(i)
                w = item.widget()
                if w:
                    w.setVisible(visible)
                elif item.layout():
                    self._set_layout_visible(item.layout(), visible)
try:
    from .ui.theme import apply_radiometric_theme, LIGHT, DARK
except Exception:
    LIGHT = 'light'; DARK = 'dark'
    def apply_radiometric_theme(widget, theme=LIGHT):
        return None

try:
    from .ui.guides import (
        MAIN_GUIDE, DATA_QC_GUIDE, PRE_GUIDE, RATIO_GUIDE, STAGE2_GUIDE, STAGE3_GUIDE,
        MAIN_GUIDE_SECTIONS, DATA_QC_GUIDE_SECTIONS, PRE_GUIDE_SECTIONS, RATIO_GUIDE_SECTIONS,
        STAGE2_GUIDE_SECTIONS, STAGE3_GUIDE_SECTIONS
    )
except Exception:
    MAIN_GUIDE = 'Radiometric Analysis Suite workflow guide.'
    DATA_QC_GUIDE = 'Input raster QC guide.'
    PRE_GUIDE = 'Pre-processing / data conditioning guide.'
    RATIO_GUIDE = 'Ratio map generation guide.'
    STAGE2_GUIDE = 'Ratio map analysis and anomaly extraction guide.'
    STAGE3_GUIDE = 'Combined analysis and anomaly extraction guide.'
    MAIN_GUIDE_SECTIONS = [("Guide", MAIN_GUIDE)]
    DATA_QC_GUIDE_SECTIONS = [("Guide", DATA_QC_GUIDE)]
    PRE_GUIDE_SECTIONS = [("Guide", PRE_GUIDE)]
    RATIO_GUIDE_SECTIONS = [("Guide", RATIO_GUIDE)]
    STAGE2_GUIDE_SECTIONS = [("Guide", STAGE2_GUIDE)]
    STAGE3_GUIDE_SECTIONS = [("Guide", STAGE3_GUIDE)]

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
except Exception:
    FigureCanvas = None
    Figure = None


gdal.UseExceptions()


class RadiometricAnalysisPanel(QWidget):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.state = RadiometricWorkflowState()
        self.stage3_widget = None
        self.stage4_widget = None
        self.current_theme = LIGHT
        self.setWindowTitle('Radiometric Analysis Suite')
        self.setObjectName('RadiometricAnalysisPanel')
        try:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        except Exception:
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        header_frame = QFrame()
        header_frame.setObjectName('RadiometricHeader')
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(10, 6, 10, 6)
        title = QLabel('Radiometric Analysis Suite')
        title.setObjectName('RadiometricTitle')
        f = QFont(); f.setPointSize(11); f.setBold(True); title.setFont(f)
        header.addWidget(title)
        header.addStretch(1)
        self._add_theme_controls(header)
        self._add_window_control_buttons(header)
        root.addWidget(header_frame)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.North)
        root.addWidget(self.tabs, 1)

        self.main_tab = self._make_main_tab()
        self.work_manager_tab = WorkManagerTab(self, state=self.state)
        self.data_qc_tab = self._make_data_qc_tab()
        self.stage1_tab = self._make_ratio_generation_stage_tab()
        self.stage2_tab = self._make_stage2_analysis_tab()
        self.stage3_tab = self._make_stage3_combined_tab()

        self.tabs.addTab(self._scroll_wrap(self.main_tab), 'Radiometric Analysis')
        self.tabs.addTab(self._scroll_wrap(self.work_manager_tab), 'Work Manager')
        self.tabs.addTab(self._scroll_wrap(self.data_qc_tab), 'Data Analysis and QC')
        self.tabs.addTab(self._scroll_wrap(self.stage1_tab), 'Stage 1 - Ratio Maps')
        self.tabs.addTab(self._scroll_wrap(self.stage2_tab), 'Stage 2 - Ratio Analysis')
        self.tabs.addTab(self._scroll_wrap(self.stage3_tab), 'Stage 3 - Combined')
        self._set_tab_icons()

        self._apply_theme()


    def _icon(self, name):
        """Return a bundled SVG icon, falling back to a standard Qt icon."""
        try:
            path = os.path.join(os.path.dirname(__file__), 'ui', 'icons', name + '.svg')
            if os.path.exists(path):
                return QIcon(path)
        except Exception:
            pass
        try:
            return self.style().standardIcon(QStyle.SP_FileIcon)
        except Exception:
            return QIcon()

    def _set_tab_icons(self):
        """Add small left-side icons while keeping text labels visible."""
        try:
            icon_names = ['radiometric', 'qc', 'qc', 'ratio', 'threshold', 'combine']
            for idx, name in enumerate(icon_names):
                self.tabs.setTabIcon(idx, self._icon(name))
            self.tabs.setIconSize(self.tabs.iconSize())
        except Exception:
            pass

    # ------------------------------------------------------------------ common UI
    def _scroll_wrap(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _folder_row(self):
        row = QHBoxLayout()
        row.addWidget(QLabel('Processing folder:'))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText('Choose one folder for the full radiometric workflow')
        self.folder_edit.textChanged.connect(self._processing_folder_changed)
        row.addWidget(self.folder_edit, 1)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self.browse_processing_folder)
        row.addWidget(browse)
        return row

    def browse_processing_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select radiometric processing folder', self.state.processing_folder)
        if folder:
            self.folder_edit.setText(folder)

    def _active_processing_folder(self):
        """Return the current output folder.

        If Work Manager has an active session, processing outputs are routed
        to that isolated session folder. Otherwise the base processing folder
        is used.
        """
        sess = getattr(self.state, 'session_folder', '')
        if sess and os.path.isdir(sess):
            return sess
        return self.state.processing_folder or self.folder_edit.text().strip()

    def _ensure_work_session(self):
        """Create/select a Work Manager session when required."""
        wm = getattr(self, 'work_manager_tab', None)
        try:
            require = wm is not None and getattr(wm, 'require_session', None) is not None and wm.require_session.isChecked()
        except Exception:
            require = False
        if require and (not getattr(self.state, 'session_folder', '') or not os.path.isdir(getattr(self.state, 'session_folder', ''))):
            wm.new_session()
        return bool(self._active_processing_folder())

    def _open_processing_folder(self):
        folder = self.state.processing_folder or self.folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, 'Processing folder required', 'Set the processing folder first.')
            return
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _refresh_project_folder(self):
        folder = self.state.processing_folder or self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, 'Processing folder required', 'Set a valid processing folder first.')
            return
        self._auto_load_data_from_processing_folder_main()
        try:
            self._load_project_layers_from_processing_folder()
        except Exception:
            pass
        QMessageBox.information(self, 'Project refreshed', 'The plugin inputs and QGIS layer tree were refreshed from the processing folder.')

    def _run_synthetic_validation(self):
        folder = self.state.processing_folder or self.folder_edit.text().strip()
        if not folder:
            folder = QFileDialog.getExistingDirectory(self, 'Select a folder for the validation project')
            if not folder:
                return
            self.folder_edit.setText(folder)
        try:
            report_path, sample_dir = run_synthetic_validation(folder)
            self._refresh_project_folder()
            QMessageBox.information(self, 'Synthetic validation complete', 'Validation sample rasters and expected outputs were created.\n\nReport:\n' + report_path + '\n\nSample rasters:\n' + sample_dir)
        except Exception as e:
            QMessageBox.critical(self, 'Synthetic validation failed', str(e))

    def _validate_processing_folder(self):
        folder = self.state.processing_folder or self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            folder = QFileDialog.getExistingDirectory(self, 'Select processing folder to validate')
            if not folder:
                return
            self.folder_edit.setText(folder)
        try:
            report_path, csv_path, summary = validate_processing_folder(folder)
            msg = 'Independent raster validation complete.\n\nPASS: {PASS}\nFAIL: {FAIL}\nNOT FOUND: {NOT_FOUND}\nERROR: {ERROR}\n\nReport:\n{report}\n\nCSV:\n{csv}'.format(report=report_path, csv=csv_path, **summary)
            QMessageBox.information(self, 'Processing folder validation complete', msg)
        except Exception as e:
            QMessageBox.critical(self, 'Processing folder validation failed', str(e))

    def _processing_folder_changed(self, text):
        self.state.processing_folder = text.strip()
        try:
            if hasattr(self, 'work_manager_tab'):
                self.work_manager_tab.refresh_from_state()
        except Exception:
            pass
        self._sync_folder_state()

    def _sync_folder_state(self):
        try:
            self._auto_load_input_rasters_from_folder(set_combos=True, silent=True)
        except Exception:
            pass
        try:
            self._auto_load_filtered_rasters_from_folder(set_combos=True, silent=True)
        except Exception:
            pass
        self._detect_filtered_inputs(update_labels=True)
        self._detect_ratio_outputs(update_state=True)
        try:
            self._update_qc_data_inputs()
        except Exception:
            pass
        active_folder = self._active_processing_folder()
        for widget in (self.stage3_widget, self.stage4_widget, getattr(self, 'crossplot_widget', None)):
            if widget is not None:
                widget.processing_folder = active_folder
                if hasattr(widget, 'folder_label'):
                    widget.folder_label.setText(active_folder or '(not set)')
                try:
                    widget._auto_from_processing_folder()
                except Exception:
                    pass

    def _auto_load_data_from_processing_folder_main(self):
        """Refresh all tab inputs from the current processing folder.

        This keeps the processing folder as the automatic source of truth while
        still allowing users to manually override by choosing loaded QGIS layers
        in each tab.
        """
        folder = self.state.processing_folder or self.folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, 'Processing folder required', 'Please set a processing folder first.')
            return
        if not os.path.isdir(folder):
            QMessageBox.warning(self, 'Processing folder not found', 'The processing folder does not exist:\n\n' + folder)
            return
        self.state.processing_folder = folder
        self.folder_edit.setText(folder)
        input_found = self._auto_load_input_rasters_from_folder(set_combos=True, silent=True)
        filtered_found = self._auto_load_filtered_rasters_from_folder(set_combos=True, silent=True)
        ratio_found = self._detect_ratio_outputs(update_state=True)
        try:
            self._update_qc_data_inputs()
        except Exception:
            pass
        active_folder = self._active_processing_folder()
        for widget in (self.stage3_widget, self.stage4_widget, getattr(self, 'crossplot_widget', None)):
            if widget is not None:
                widget.processing_folder = active_folder
                try:
                    widget._auto_from_processing_folder()
                except Exception:
                    pass
        msg = [
            'Auto-load from processing folder complete.',
            '',
            'Input U/K/Th: ' + ('found' if all(input_found.values()) else 'partly found / missing'),
            'Filtered U/K/Th: ' + ('found' if all(filtered_found.values()) else 'partly found / missing'),
            'Ratio maps: ' + ('found' if ratio_found else 'not found yet'),
            '',
            'You can still override inputs manually by selecting loaded QGIS raster layers in the relevant tabs.'
        ]
        QMessageBox.information(self, 'Auto-load complete', '\n'.join(msg))

    def _show_text_window(self, title, text):
        """Open a single scrollable guide/theory document with a topic index.

        Section titles are rendered as chapter headings. The topic list at the
        top links down to each section instead of using separate collapsible
        boxes, so the guide reads like one continuous document.
        """
        import html
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(900, 720)
        layout = QVBoxLayout(dlg)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setReadOnly(True)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        if isinstance(text, (list, tuple)) and text and isinstance(text[0], (list, tuple)):
            sections = []
            for idx, section in enumerate(text):
                try:
                    sec_title, sec_body = section[0], section[1]
                except Exception:
                    sec_title, sec_body = 'Guide', str(section)
                sections.append((str(sec_title).strip() or 'Guide', str(sec_body).strip()))
        else:
            sections = [('Guide', str(text).strip())]

        topic_links = []
        body_parts = []
        for idx, (sec_title, sec_body) in enumerate(sections):
            anchor = 'section_%03d' % idx
            topic_links.append('<li><a href="#%s">%s</a></li>' % (anchor, html.escape(sec_title)))
            safe_body = html.escape(sec_body).replace('\n', '<br>')
            body_parts.append(
                '<h2 id="{anchor}" style="color:#9f2f2f; margin-top:22px;">{title}</h2>'
                '<div style="line-height:1.45;">{body}</div>'
                '<p style="margin-top:10px;"><a href="#top">Back to topics</a></p>'.format(
                    anchor=anchor, title=html.escape(sec_title), body=safe_body
                )
            )

        doc = """
        <html><body id="top" style="font-family:Segoe UI, Arial, sans-serif; font-size:10pt;">
        <h1 style="margin-top:0; color:#222;">{title}</h1>
        <h3 style="color:#555; margin-bottom:4px;">Topics</h3>
        <ul>{links}</ul>
        <hr>
        {body}
        </body></html>
        """.format(title=html.escape(title), links=''.join(topic_links), body=''.join(body_parts))
        browser.setHtml(doc)
        layout.addWidget(browser, 1)

        close = QPushButton('Close')
        close.clicked.connect(dlg.accept)
        layout.addWidget(close)
        dlg.exec_()

    def _guide_button(self, title, text):
        btn = QPushButton('User Guide and Theory')
        try:
            btn.setIcon(self._icon('guide'))
        except Exception:
            pass
        btn.clicked.connect(lambda: self._show_text_window(title, text))
        return btn

    def _project_raster_combo(self):
        combo = QComboBox()
        combo.addItem('(Select loaded raster layer)', '')
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsRasterLayer) and lyr.isValid():
                combo.addItem(lyr.name(), lyr.source())
        return combo

    def _project_polygon_combo(self, include_none=True):
        combo = QComboBox()
        if include_none:
            combo.addItem('(No mask polygon)', '')
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.isValid():
                try:
                    if lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                        combo.addItem(lyr.name(), lyr.source())
                except Exception:
                    pass
        return combo

    def _refresh_raster_combos(self):
        for attr in ('u_combo', 'k_combo', 'th_combo'):
            old = getattr(self, attr, None)
            if old is None:
                continue
            cur = old.currentData()
            old.blockSignals(True)
            old.clear(); old.addItem('(Select loaded raster layer)', '')
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsRasterLayer) and lyr.isValid():
                    old.addItem(lyr.name(), lyr.source())
            ix = old.findData(cur)
            if ix >= 0: old.setCurrentIndex(ix)
            old.blockSignals(False)
        if hasattr(self, 'mask_combo'):
            cur = self.mask_combo.currentData()
            self.mask_combo.blockSignals(True)
            self.mask_combo.clear(); self.mask_combo.addItem('(No mask polygon)', '')
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                    self.mask_combo.addItem(lyr.name(), lyr.source())
            ix = self.mask_combo.findData(cur)
            if ix >= 0: self.mask_combo.setCurrentIndex(ix)
            self.mask_combo.blockSignals(False)

    def _raster_selector_row(self, label, combo_attr, file_attr):
        row = QGridLayout()
        row.addWidget(QLabel(label), 0, 0)
        combo = self._project_raster_combo()
        setattr(self, combo_attr, combo)
        row.addWidget(combo, 0, 1)
        # File-path overrides were removed from the dock UI. The workflow now
        # auto-loads rasters from the project folder into QGIS, and users can
        # override by selecting any raster layer already loaded in the QGIS project.
        edit = QLineEdit()
        edit.setVisible(False)
        setattr(self, file_attr, edit)
        note = QLabel('Auto-filled from project folder where possible; otherwise select a loaded QGIS raster layer.')
        note.setWordWrap(True)
        row.addWidget(note, 1, 1)
        return row

    def _selected_raster_source(self, combo, edit=None):
        val = (combo.currentData() or '').strip() if combo is not None else ''
        if val:
            return val
        if edit is not None:
            return edit.text().strip()
        return ''

    def _find_project_radiometric_layers(self):
        """Best-effort classification of loaded project rasters as U/K/Th.

        This is intentionally conservative: it prefers layer names/filenames that
        clearly contain uranium/potassium/thorium style tokens and avoids ratio
        outputs. The user can still manually override using the dropdown/file
        browser rows.
        """
        found = {'U': '', 'K': '', 'TH': ''}
        scored = {'U': [], 'K': [], 'TH': []}
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsRasterLayer) or not lyr.isValid():
                continue
            name = (lyr.name() or '')
            src = (lyr.source() or '')
            low = (name + ' ' + os.path.basename(src)).lower()
            if 'ratio' in low or 'rrai' in low or 'combined' in low or 'norm' in low:
                continue
            def add(key, score):
                scored[key].append((score, name.lower(), src))
            if any(t in low for t in ('uranium', ' e u', ' eu', 'u_nasvd', 'u-nasvd', 'u smooth', 'u_smooth', 'u_meanf', 'u_medianf', 'u_gaussianf')):
                add('U', 100)
            elif os.path.basename(src).lower().startswith(('u_', 'u-', 'u.')) or name.lower().startswith(('u_', 'u-', 'u ')):
                add('U', 80)
            if any(t in low for t in ('potassium', 'pctk', '%k', ' k ', 'k_nasvd', 'k-nasvd', 'k smooth', 'k_smooth', 'k_meanf', 'k_medianf', 'k_gaussianf')):
                add('K', 100)
            elif os.path.basename(src).lower().startswith(('k_', 'k-', 'k.')) or name.lower().startswith(('k_', 'k-', 'k ')):
                add('K', 80)
            if any(t in low for t in ('thorium', ' eth', 'th_nasvd', 'th-nasvd', 'th smooth', 'th_smooth', 'th_meanf', 'th_medianf', 'th_gaussianf')):
                add('TH', 100)
            elif os.path.basename(src).lower().startswith(('th_', 'th-', 'th.')) or name.lower().startswith(('th_', 'th-', 'th ')):
                add('TH', 80)
        for key in found:
            if scored[key]:
                scored[key].sort(reverse=True)
                found[key] = scored[key][0][2]
        return found

    def _set_combo_or_file(self, combo, edit, path):
        if not path:
            return
        ix = combo.findData(path) if combo is not None else -1
        if ix >= 0:
            combo.setCurrentIndex(ix)
            if edit is not None:
                edit.clear()
        elif combo is not None and os.path.exists(path):
            # Add direct project-folder raster path to the dropdown if it was not
            # already present as a loaded layer.
            combo.addItem(os.path.splitext(os.path.basename(path))[0], path)
            combo.setCurrentIndex(combo.findData(path))
            if edit is not None:
                edit.clear()
        elif edit is not None:
            edit.setText(path)

    def _classify_radiometric_path(self, path):
        low = os.path.basename(path).lower().replace('-', '_').replace(' ', '_')
        # Avoid selecting derived rasters as raw inputs.
        if any(t in low for t in ('ratio', 'rrai', 'combined', 'norm', 'class', 'mask', 'threshold')):
            return None
        if 'uranium' in low or low.startswith('u_') or '_u_' in low or low.startswith('u.'):
            return 'U'
        if 'potassium' in low or low.startswith('k_') or '_k_' in low or low.startswith('k.'):
            return 'K'
        if 'thorium' in low or low.startswith('th_') or '_th_' in low or low.startswith('th.'):
            return 'TH'
        return None

    def _find_folder_radiometric_rasters(self, subfolder):
        folder = self._active_processing_folder()
        found = {'U': '', 'K': '', 'TH': ''}
        if not folder:
            return found
        root = os.path.join(folder, subfolder)
        if not os.path.isdir(root):
            return found
        candidates = {'U': [], 'K': [], 'TH': []}
        for r, _, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(('.tif', '.tiff', '.vrt', '.img', '.asc')):
                    continue
                path = os.path.join(r, f)
                role = self._classify_radiometric_path(path)
                if role:
                    candidates[role].append((os.path.getmtime(path), path))
        for role, vals in candidates.items():
            if vals:
                vals.sort(reverse=True)
                found[role] = vals[0][1]
        return found

    def _radiometric_group_parts_from_path(self, path):
        known = [
            '00_Data_QC', '01_Input_Data', '02_Preprocessed',
            '03_Ratio_Maps', '04_Normalised_Ratio_Maps',
            '05_Ratio_Threshold_Class_Maps', '06_Ratio_Map_Anomaly_Extraction',
            '07_Combined_Maps', '08_Combined_Threshold_Maps',
            '09_Combined_Map_Anomaly_Extraction', '10_Project_Settings'
        ]
        norm = os.path.normpath(str(path))
        parts = norm.split(os.sep)
        for i, part in enumerate(parts):
            if part in known:
                return parts[i:-1]
        parent = os.path.basename(os.path.dirname(norm))
        return [parent] if parent else []

    def _add_layer_to_group(self, layer, path):
        """Add a layer under Radiometric Processing, mirroring project folders."""
        if layer is None or not layer.isValid():
            return None
        root = QgsProject.instance().layerTreeRoot()
        # Standardise top-level QGIS group name. Older builds sometimes created
        # 'Radiometric_Processing'; reuse/rename it rather than creating two roots.
        target = root.findGroup('Radiometric Processing')
        if target is None:
            old_target = root.findGroup('Radiometric_Processing')
            if old_target is not None:
                old_target.setName('Radiometric Processing')
                target = old_target
            else:
                target = root.addGroup('Radiometric Processing')
        for part in self._radiometric_group_parts_from_path(path):
            if not part:
                continue
            child = target.findGroup(part)
            if child is None:
                child = target.addGroup(part)
            target = child
        if layer.id() not in QgsProject.instance().mapLayers():
            QgsProject.instance().addMapLayer(layer, False)
        try:
            for child in target.children():
                if hasattr(child, 'layer') and child.layer() and child.layer().id() == layer.id():
                    return layer
        except Exception:
            pass
        target.addLayer(layer)
        return layer

    def _add_raster_to_project_if_needed(self, path, layer_name=None):
        if not path or not os.path.exists(path):
            return None
        # Avoid duplicate layers from the exact same source.
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsRasterLayer) and lyr.isValid() and (lyr.source() == path):
                return lyr
        lyr = QgsRasterLayer(path, layer_name or os.path.splitext(os.path.basename(path))[0])
        if lyr.isValid():
            self._add_layer_to_group(lyr, path)
            return lyr
        return None

    def _auto_load_input_rasters_from_folder(self, set_combos=True, silent=False):
        found = self._find_folder_radiometric_rasters('01_Input_Data')
        for role, path in found.items():
            if path:
                self._add_raster_to_project_if_needed(path, 'Input_' + role)
        self._refresh_raster_combos()
        if set_combos and hasattr(self, 'u_combo'):
            self._set_combo_or_file(self.u_combo, self.u_file, found.get('U', ''))
            self._set_combo_or_file(self.k_combo, self.k_file, found.get('K', ''))
            self._set_combo_or_file(self.th_combo, self.th_file, found.get('TH', ''))
        if not silent:
            msg = 'Input folder auto-load complete.'
            if not all(found.values()):
                msg += '\n\nSome U/K/Th rasters were not found in 01_Input_Data. You can still select loaded QGIS raster layers manually.'
            QMessageBox.information(self, 'Input raster auto-load', msg)
        return found

    def _auto_load_filtered_rasters_from_folder(self, set_combos=True, silent=False):
        found = self._detect_filtered_inputs(update_labels=False, show_message=False, force_folder=True)
        for role, path in found.items():
            if path:
                self._add_raster_to_project_if_needed(path, 'Filtered_' + role)
        self._refresh_ratio_raster_combos()
        if set_combos and hasattr(self, 'ratio_u_combo'):
            self._set_combo_or_file(self.ratio_u_combo, None, found.get('U', ''))
            self._set_combo_or_file(self.ratio_k_combo, None, found.get('K', ''))
            self._set_combo_or_file(self.ratio_th_combo, None, found.get('TH', ''))
        if not silent:
            msg = 'Filtered raster auto-load complete. Latest matching filtered rasters are selected where available.'
            if not all(found.values()):
                msg += '\n\nSome filtered U/K/Th rasters were not found. Run Pre-processing first or select loaded QGIS raster layers manually.'
            QMessageBox.information(self, 'Filtered raster auto-load', msg)
        return found

    def _auto_stage1_inputs_from_project(self):
        # First use the workflow input folder; then fill any missing roles from loaded QGIS layers.
        found = self._auto_load_input_rasters_from_folder(set_combos=False, silent=True)
        fallback = self._find_project_radiometric_layers()
        for role in ('U', 'K', 'TH'):
            if not found.get(role) and fallback.get(role):
                found[role] = fallback[role]
        self._refresh_raster_combos()
        self._set_combo_or_file(self.u_combo, self.u_file, found.get('U', ''))
        self._set_combo_or_file(self.k_combo, self.k_file, found.get('K', ''))
        self._set_combo_or_file(self.th_combo, self.th_file, found.get('TH', ''))
        missing = [k for k, v in found.items() if not v]
        if missing:
            QMessageBox.information(self, 'Auto-load input rasters', 'Auto-loaded what could be detected from the project folder/QGIS project. Please manually select: ' + ', '.join(missing))
        else:
            QMessageBox.information(self, 'Auto-load input rasters', 'Detected and selected U, K and Th rasters.')

    def _auto_qc_inputs_from_project(self):
        self._refresh_qc_raster_combos()
        found = self._find_project_radiometric_layers()
        self._set_combo_or_file(self.qc_u_combo, self.qc_u_file, found.get('U', ''))
        self._set_combo_or_file(self.qc_k_combo, self.qc_k_file, found.get('K', ''))
        self._set_combo_or_file(self.qc_th_combo, self.qc_th_file, found.get('TH', ''))
        missing = [k for k, v in found.items() if not v]
        if missing:
            QMessageBox.information(self, 'Auto-load from project', 'Auto-loaded what could be detected. Please manually select: ' + ', '.join(missing))
        else:
            QMessageBox.information(self, 'Auto-load from project', 'Detected and selected U, K and Th rasters from the current QGIS project.')

    def _copy_input_raster_to_project(self, src, role):
        """Copy the selected source raster into the workflow input folder.

        Only local filesystem rasters are copied. Provider strings such as WMS,
        VRT subdatasets, or database layers are left in place and logged.
        """
        folder = self._active_processing_folder()
        if not folder or not src or not os.path.exists(src):
            return src
        input_dir = ensure_dir(os.path.join(folder, '01_Input_Data'))
        base = os.path.basename(src)
        dst = os.path.join(input_dir, base)
        if os.path.abspath(src) == os.path.abspath(dst):
            return dst
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
        return dst

    def _add_theme_controls(self, layout):
        """Add a compact light/dark theme toggle to the top header."""
        theme_label = QLabel('Theme')
        theme_label.setObjectName('ThemeLabel')
        layout.addWidget(theme_label)

        self.theme_toggle_btn = QToolButton(self)
        self.theme_toggle_btn.setObjectName('ThemeButton')
        self.theme_toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.theme_toggle_btn.setCheckable(True)
        self.theme_toggle_btn.setChecked(False)
        self.theme_toggle_btn.setText('Light')
        self.theme_toggle_btn.setToolTip('Toggle between light and dark radiometric themes')
        try:
            self.theme_toggle_btn.setIcon(QApplication.style().standardIcon(QStyle.SP_DialogYesButton))
        except Exception:
            pass
        self.theme_toggle_btn.toggled.connect(self._toggle_theme)
        layout.addWidget(self.theme_toggle_btn)

    def _toggle_theme(self, checked):
        self.current_theme = DARK if checked else LIGHT
        self._apply_theme()

    def _apply_theme(self):
        """Apply the selected theme to the panel and refresh the toggle label."""
        try:
            apply_radiometric_theme(self, self.current_theme)
        except Exception:
            pass
        try:
            if self.current_theme == DARK:
                self.theme_toggle_btn.setText('Dark')
                self.theme_toggle_btn.setToolTip('Dark theme active. Click to switch to light mode.')
            else:
                self.theme_toggle_btn.setText('Light')
                self.theme_toggle_btn.setToolTip('Light theme active. Click to switch to dark mode.')
        except Exception:
            pass

    def _add_window_control_buttons(self, layout):
        """Add dock/window controls to the persistent top bar."""
        style = QApplication.style()

        def make_btn(text, tooltip, icon, callback):
            btn = QToolButton(self)
            btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            try:
                btn.setIcon(style.standardIcon(icon))
            except Exception:
                pass
            btn.clicked.connect(callback)
            return btn

        self.float_window_btn = make_btn(
            'Float',
            'Detach the Radiometric Analysis Suite from the QGIS side panel.',
            QStyle.SP_TitleBarNormalButton,
            self._float_plugin_window
        )
        self.maximise_window_btn = make_btn(
            'Maximise',
            'Float and maximise the Radiometric Analysis Suite window.',
            QStyle.SP_TitleBarMaxButton,
            self._maximise_plugin_window
        )
        self.restore_window_btn = make_btn(
            'Restore',
            'Restore the Radiometric Analysis Suite to its previous size/docked state.',
            QStyle.SP_TitleBarNormalButton,
            self._restore_plugin_window
        )
        self.minimise_window_btn = make_btn(
            'Hide',
            'Minimise the floating window, or hide it if it is docked.',
            QStyle.SP_TitleBarMinButton,
            self._minimise_plugin_window
        )

        for btn in (self.float_window_btn, self.maximise_window_btn, self.restore_window_btn, self.minimise_window_btn):
            layout.addWidget(btn)

    # ------------------------------------------------------------------ tabs
    def _make_main_tab(self):
        """Build the home page as a compact project/workflow control panel."""
        w = QWidget()
        root = QVBoxLayout(w)

        # --------------------------------------------------------------
        # Project Management
        # --------------------------------------------------------------
        project_box = CollapsibleBox('Project Management', checked=True)
        project_layout = QVBoxLayout()

        folder_group = QGroupBox('Processing / Project Folder')
        folder_layout = QVBoxLayout(folder_group)
        folder_note = QLabel(
            'Set the root radiometric processing folder. This folder controls where project data, '
            'settings, intermediate rasters, threshold maps and anomaly polygons are stored.'
        )
        folder_note.setWordWrap(True)
        folder_layout.addWidget(folder_note)
        folder_layout.addLayout(self._folder_row())
        folder_buttons = QHBoxLayout()
        open_folder_btn = QPushButton('Open Project Folder')
        open_folder_btn.clicked.connect(self._open_processing_folder)
        refresh_folder_btn = QPushButton('Refresh Project')
        refresh_folder_btn.clicked.connect(self._refresh_project_folder)
        folder_buttons.addWidget(open_folder_btn)
        folder_buttons.addWidget(refresh_folder_btn)
        folder_buttons.addStretch(1)
        folder_layout.addLayout(folder_buttons)
        project_layout.addWidget(folder_group)

        settings_group = QGroupBox('Settings Management')
        settings_layout = QGridLayout(settings_group)
        settings_note = QLabel(
            'Save or load a workflow settings JSON. The plugin also auto-saves the current working '
            'settings into the project settings folder during processing.'
        )
        settings_note.setWordWrap(True)
        settings_layout.addWidget(settings_note, 0, 0, 1, 2)
        save_settings = QPushButton('Save Settings')
        save_settings.clicked.connect(self._save_workflow_settings)
        load_settings = QPushButton('Load Settings')
        load_settings.clicked.connect(self._load_workflow_settings)
        settings_layout.addWidget(save_settings, 1, 0)
        settings_layout.addWidget(load_settings, 1, 1)
        project_layout.addWidget(settings_group)

        utilities_group = QGroupBox('Project Utilities')
        utilities_layout = QGridLayout(utilities_group)
        utilities_note = QLabel(
            'Use these buttons to populate the dock from an existing processing folder and to load '
            'project outputs into the QGIS Layers panel using the radiometric folder structure.'
        )
        utilities_note.setWordWrap(True)
        utilities_layout.addWidget(utilities_note, 0, 0, 1, 2)
        auto_load_btn = QPushButton('Auto-load Data for Processing')
        auto_load_btn.setMinimumHeight(30)
        auto_load_btn.clicked.connect(self._auto_load_data_from_processing_folder_main)
        add_project_btn = QPushButton('Add Data to QGIS Project')
        add_project_btn.setMinimumHeight(30)
        add_project_btn.clicked.connect(self._load_project_layers_from_processing_folder)
        utilities_layout.addWidget(auto_load_btn, 1, 0)
        utilities_layout.addWidget(add_project_btn, 1, 1)
        project_layout.addWidget(utilities_group)

        validation_group = QGroupBox('Validation / Test Mode')
        validation_layout = QGridLayout(validation_group)
        validation_note = QLabel('Create a 50 x 50 synthetic K, Th and U raster set with known expected normalisation, ratio, z-log and combined-index outputs.')
        validation_note.setWordWrap(True)
        validation_layout.addWidget(validation_note, 0, 0, 1, 2)
        validation_btn = QPushButton('Run Synthetic Raster Validation')
        validation_btn.setMinimumHeight(30)
        validation_btn.clicked.connect(self._run_synthetic_validation)
        validation_layout.addWidget(validation_btn, 1, 0, 1, 2)
        validate_folder_btn = QPushButton('Validate Processing Folder')
        validate_folder_btn.setMinimumHeight(30)
        validate_folder_btn.clicked.connect(self._validate_processing_folder)
        validation_layout.addWidget(validate_folder_btn, 2, 0, 1, 2)
        project_layout.addWidget(validation_group)

        project_box.setContentLayout(project_layout)
        root.addWidget(project_box)

        # --------------------------------------------------------------
        # Workflow / Auto Run
        # --------------------------------------------------------------
        workflow_box = CollapsibleBox('Workflow', checked=True)
        workflow_layout = QVBoxLayout()

        stages_group = QGroupBox('Workflow Stages')
        stages_grid = QGridLayout(stages_group)
        rows = [
            ('QC', 'Data Analysis and QC', 'Review raster quality, statistics, histograms and crossplots from the processing folder.', 1),
            ('1', 'Ratio Map Generation', 'Condition U/K/Th rasters and generate the core radiometric ratio maps.', 2),
            ('2', 'Ratio Map Analysis and Anomaly Extraction', 'Normalise ratio maps, review thresholds and extract ratio anomalies.', 3),
            ('3', 'Combined Analysis and Anomaly Extraction', 'Build combined radiometric indices, review thresholds and extract combined anomalies.', 4),
        ]
        for r, (num, name, desc, tab_idx) in enumerate(rows):
            lab = QLabel(f'<b>{name}</b><br>{desc}')
            lab.setWordWrap(True)
            stages_grid.addWidget(lab, r, 0)
            b = QPushButton('Go to ' + ('Data Analysis and QC' if num == 'QC' else 'Stage ' + num))
            b.clicked.connect(lambda checked=False, idx=tab_idx: self.tabs.setCurrentIndex(idx))
            stages_grid.addWidget(b, r, 1)
        workflow_layout.addWidget(stages_group)

        auto_group = QGroupBox('Auto Run')
        auto_layout = QGridLayout(auto_group)
        self.autorun_combo = QComboBox()
        self.autorun_combo.addItems([
            'All - run full workflow including polygon extraction',
            'Everything except polygon extraction',
            'Stage 1 only - preprocessing and ratio maps',
            'Stage 1 + Stage 2 threshold maps only',
            'Stage 1 + Stage 2 + Stage 3 combined threshold maps only',
            'Polygon extraction only - use current threshold settings'
        ])
        auto_layout.addWidget(QLabel('Run preset:'), 0, 0)
        auto_layout.addWidget(self.autorun_combo, 0, 1)
        run_auto = QPushButton('Auto Run Selected Workflow')
        run_auto.setMinimumHeight(34)
        run_auto.clicked.connect(self._run_auto_workflow)
        auto_layout.addWidget(run_auto, 1, 0, 1, 2)
        self.autorun_progress = QProgressBar()
        self.autorun_progress.setRange(0, 100)
        self.autorun_progress.setValue(0)
        self.autorun_progress.setFormat('Idle')
        auto_layout.addWidget(self.autorun_progress, 2, 0, 1, 2)
        note = QLabel(
            'Auto Run uses the current settings in each tab. It validates required inputs first and stops '
            'before running if something is missing or inconsistent.'
        )
        note.setWordWrap(True)
        auto_layout.addWidget(note, 3, 0, 1, 2)
        workflow_layout.addWidget(auto_group)

        workflow_box.setContentLayout(workflow_layout)
        root.addWidget(workflow_box)

        root.addStretch(1)
        guide_row = QHBoxLayout()
        guide_row.addStretch(1)
        guide_row.addWidget(self._guide_button('Radiometric Analysis Suite - User Guide and Theory', MAIN_GUIDE_SECTIONS))
        root.addLayout(guide_row)
        return w


    # ------------------------------------------------------------------ dock window controls
    def _own_dock_widget(self):
        """Return the QDockWidget containing this panel, if available."""
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QDockWidget):
                return w
            w = w.parentWidget()
        return None

    def _float_plugin_window(self):
        dock = self._own_dock_widget()
        if dock is None:
            QMessageBox.warning(self, 'Window controls', 'Could not find the Radiometric Analysis dock widget.')
            return
        if not hasattr(self, '_dock_restore_geometry'):
            try:
                self._dock_restore_geometry = dock.saveGeometry()
                self._dock_restore_floating = dock.isFloating()
            except Exception:
                self._dock_restore_geometry = None
                self._dock_restore_floating = False
        dock.setFloating(True)
        dock.showNormal()
        dock.resize(max(dock.width(), 1100), max(dock.height(), 800))
        dock.raise_()
        dock.activateWindow()

    def _maximise_plugin_window(self):
        dock = self._own_dock_widget()
        if dock is None:
            QMessageBox.warning(self, 'Window controls', 'Could not find the Radiometric Analysis dock widget.')
            return
        try:
            self._dock_restore_geometry = dock.saveGeometry()
            self._dock_restore_floating = dock.isFloating()
        except Exception:
            self._dock_restore_geometry = None
            self._dock_restore_floating = dock.isFloating()
        dock.setFloating(True)
        dock.showMaximized()
        dock.raise_()
        dock.activateWindow()

    def _restore_plugin_window(self):
        dock = self._own_dock_widget()
        if dock is None:
            QMessageBox.warning(self, 'Window controls', 'Could not find the Radiometric Analysis dock widget.')
            return
        dock.showNormal()
        try:
            if getattr(self, '_dock_restore_geometry', None):
                dock.restoreGeometry(self._dock_restore_geometry)
            dock.setFloating(bool(getattr(self, '_dock_restore_floating', False)))
        except Exception:
            dock.setFloating(False)
            dock.resize(900, 700)
        dock.show()
        dock.raise_()
        dock.activateWindow()

    def _minimise_plugin_window(self):
        dock = self._own_dock_widget()
        if dock is None:
            QMessageBox.warning(self, 'Window controls', 'Could not find the Radiometric Analysis dock widget.')
            return
        if dock.isFloating():
            dock.showMinimized()
        else:
            dock.hide()



    # ------------------------------------------------------------------ settings save/load
    def _widget_setting_value(self, obj):
        """Return a JSON-safe value for common Qt input widgets."""
        try:
            if hasattr(obj, 'isChecked') and callable(obj.isChecked):
                return bool(obj.isChecked())
            if hasattr(obj, 'currentText') and callable(obj.currentText):
                return obj.currentText()
            if hasattr(obj, 'value') and callable(obj.value):
                return obj.value()
            if hasattr(obj, 'text') and callable(obj.text):
                return obj.text()
        except Exception:
            return None
        return None

    def _apply_widget_value(self, obj, value):
        """Apply a JSON value to a common Qt input widget, if compatible."""
        if obj is None or value is None:
            return
        try:
            if hasattr(obj, 'setChecked') and isinstance(value, bool):
                obj.setChecked(value)
            elif hasattr(obj, 'setCurrentIndex') and hasattr(obj, 'findText'):
                idx = obj.findText(str(value))
                if idx >= 0:
                    obj.setCurrentIndex(idx)
            elif hasattr(obj, 'setValue') and isinstance(value, (int, float)):
                obj.setValue(value)
            elif hasattr(obj, 'setText'):
                obj.setText(str(value))
        except Exception:
            pass

    def _collect_named_widget_settings(self, target, names):
        data = {}
        for name in names:
            obj = getattr(target, name, None)
            value = self._widget_setting_value(obj)
            if value is not None:
                data[name] = value
        return data

    def _apply_named_widget_settings(self, target, values):
        if target is None or not isinstance(values, dict):
            return
        for name, value in values.items():
            self._apply_widget_value(getattr(target, name, None), value)

    def _collect_stage2_settings(self):
        names = [
            'enable_clip','clip_low','clip_high','calc_mode','calc_high_pct','calc_vhigh_pct',
            'calc_low_pct','calc_vlow_pct','calc_high_sigma','calc_vhigh_sigma',
            'uth_high','uth_vhigh','uk_high','uk_vhigh','ku_high','ku_vhigh','kth_low','kth_vlow',
            'chk_show_raw','chk_show_analysis','chk_manual_axis','axis_min','axis_max',
            'chk_overlay_mean','chk_overlay_median','chk_overlay_std','chk_overlay_percentiles',
            'chk_overlay_removed','chk_overlay_highlow','chk_overlay_very','preview_ratio','preview_level',
            'preview_mask_mode','preview_mask_colour','preview_opacity','extraction_mode','extract_level',
            'polygon_method','use_min_pixels','min_pixels','enable_trend','trend_method','trend_kernel',
            'trend_iterations','majority_fraction','min_area','smooth_polygons','smooth_iterations',
            'smooth_offset','smooth_max_angle'
        ]
        return self._collect_named_widget_settings(self.stage3_widget, names) if self.stage3_widget is not None else {}

    def _collect_stage3_settings(self):
        names = [
            'norm_method','norm_low_pct','norm_high_pct','gen_norm_maps','gen_log_maps','gen_zlog_maps',
            'crossplot_source','crossplot_log_x','crossplot_log_y','crossplot_sample',
            'chk_generate_rrai','rrai_combination_method','rrai_weight_preset','rrai_w_uth','rrai_w_second','rrai_w_kth',
            'chk_generate_hrai','hrai_combination_method','hrai_weight_preset','hrai_w_uth','hrai_w_second','hrai_w_kth',
            'chk_generate_hri','hri_combination_method','hri_weight_preset','hri_w_uth','hri_w_second','hri_w_kth',
            'calc_mode','high_pct','vhigh_pct','high_sigma','vhigh_sigma','high_val','vhigh_val',
            'chk_s3_show_raw','chk_s3_show_analysis','chk_s3_removed','chk_s3_high','chk_s3_very',
            'chk_s3_mean','chk_s3_median','chk_s3_std','chk_s3_percentiles','chk_s3_manual_axis',
            's3_axis_min','s3_axis_max','extraction_index','extract_level','polygon_method','min_area',
            'use_min_pixels','min_pixels','enable_trend','trend_method','trend_kernel','trend_iterations',
            'majority_fraction','smooth_polygons','smooth_iterations','smooth_offset','smooth_max_angle'
        ]
        return self._collect_named_widget_settings(self.stage4_widget, names) if self.stage4_widget is not None else {}

    def _collect_workflow_settings(self):
        """Collect the current workflow settings, including the newer UI sections.

        The file format is intentionally tolerant: missing values are ignored on
        load so older settings files and future UI changes do not break the dock.
        """
        qc_checks = []
        try:
            qc_checks = [bool(cb.isChecked()) for cb in getattr(self, 'qc_path_checks', [])]
        except Exception:
            qc_checks = []
        st = {
            'plugin_version': '3.55.15',
            'settings_schema': 2,
            'theme': getattr(self, 'current_theme', LIGHT),
            'processing_folder': self.state.processing_folder or self.folder_edit.text().strip(),
            'data_analysis_qc': {
                'qc_mode': self._widget_setting_value(getattr(self, 'qc_mode_combo', None)),
                'qc_include_flags': qc_checks,
                'hist_raster': self._widget_setting_value(getattr(self, 'hist_raster_combo', None)),
                'hist_bins': self._widget_setting_value(getattr(self, 'hist_bins', None)),
                'hist_sampling_mode': self._widget_setting_value(getattr(self, 'hist_sampling_mode', None)),
                'hist_sample_max': self._widget_setting_value(getattr(self, 'hist_sample_max', None)),
            },
            'stage1_preprocessing': self._collect_named_widget_settings(self, [
                'filter_method_combo','filter_size_combo','invalid_le_zero_check','pre_load_outputs'
            ]),
            'stage1_ratio_generation': self._collect_named_widget_settings(self, [
                'denominator_guard_mode','min_denominator','denominator_percentile','clip_ratio_extremes',
                'clip_ratio_low','clip_ratio_high','style_low','style_high','load_ratios'
            ]),
            'stage2': self._collect_stage2_settings(),
            'stage3': self._collect_stage3_settings(),
            'autorun': {
                'preset': self._widget_setting_value(getattr(self, 'autorun_combo', None)),
            }
        }
        return st

    def _save_workflow_settings(self):
        folder = self.state.processing_folder or self.folder_edit.text().strip() or os.path.expanduser('~')
        default = os.path.join(folder, 'radiometric_analysis_settings_v3_55_15.json') if os.path.isdir(folder) else 'radiometric_analysis_settings_v3_55_15.json'
        path, _ = QFileDialog.getSaveFileName(self, 'Save Radiometric Analysis Settings', default, 'JSON settings (*.json)')
        if not path:
            return
        if not path.lower().endswith('.json'):
            path += '.json'
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._collect_workflow_settings(), f, indent=2)
            QMessageBox.information(self, 'Settings saved', 'Settings saved to:\n\n' + path)
        except Exception as e:
            QMessageBox.critical(self, 'Save settings failed', str(e))

    def _load_workflow_settings(self):
        folder = self.state.processing_folder or self.folder_edit.text().strip() or os.path.expanduser('~')
        path, _ = QFileDialog.getOpenFileName(self, 'Load Radiometric Analysis Settings', folder, 'JSON settings (*.json)')
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            theme = data.get('theme')
            if theme in (LIGHT, DARK):
                self.current_theme = theme
                if hasattr(self, 'theme_toggle_btn'):
                    self.theme_toggle_btn.blockSignals(True)
                    self.theme_toggle_btn.setChecked(theme == DARK)
                    self.theme_toggle_btn.blockSignals(False)
                self._apply_theme()

            pf = data.get('processing_folder') or ''
            if pf:
                self.folder_edit.setText(pf)

            dq = data.get('data_analysis_qc', {})
            self._apply_widget_value(getattr(self, 'qc_mode_combo', None), dq.get('qc_mode'))
            self._apply_widget_value(getattr(self, 'hist_bins', None), dq.get('hist_bins'))
            self._apply_widget_value(getattr(self, 'hist_sampling_mode', None), dq.get('hist_sampling_mode'))
            self._apply_widget_value(getattr(self, 'hist_sample_max', None), dq.get('hist_sample_max'))
            try:
                self._update_qc_data_inputs()
                for cb, val in zip(getattr(self, 'qc_path_checks', []), dq.get('qc_include_flags', [])):
                    cb.setChecked(bool(val))
            except Exception:
                pass
            try:
                # Apply histogram raster by visible text after the folder refreshes.
                self._refresh_histogram_raster_combo()
                self._apply_widget_value(getattr(self, 'hist_raster_combo', None), dq.get('hist_raster'))
            except Exception:
                pass

            self._apply_named_widget_settings(self, data.get('stage1_preprocessing', {}))
            self._apply_named_widget_settings(self, data.get('stage1_ratio_generation', {}))
            self._apply_named_widget_settings(self.stage3_widget, data.get('stage2', {}))
            self._apply_named_widget_settings(self.stage4_widget, data.get('stage3', {}))
            ar = data.get('autorun', {})
            self._apply_widget_value(getattr(self, 'autorun_combo', None), ar.get('preset'))

            # Re-run safe auto-detection so loaded settings pick up current output names.
            try:
                self._sync_folder_state()
            except Exception:
                pass
            QMessageBox.information(self, 'Settings loaded', 'Settings loaded from:\n\n' + path)
        except Exception as e:
            QMessageBox.critical(self, 'Load settings failed', str(e))


    # ------------------------------------------------------------------ auto-run workflow
    def _autorun_error(self, tab_index, title, message):
        try:
            self.tabs.setCurrentIndex(tab_index)
        except Exception:
            pass
        QMessageBox.warning(self, title, message)

    def _set_progress(self, bar_name, value, text=None):
        bar = getattr(self, bar_name, None)
        if bar is not None:
            try:
                bar.setValue(int(value))
                if text is not None:
                    bar.setFormat(str(text))
                QApplication.processEvents()
            except Exception:
                pass

    def _validate_auto_workflow(self, mode_text):
        """Validate required settings before starting an automated workflow."""
        if not self.state.processing_folder:
            return (False, 0, 'Processing folder required', 'Set the Processing folder on the Radiometric Analysis tab first.')

        # Stage 1 needs U/K/Th selected or available in the input folder.
        try:
            self._auto_load_input_rasters_from_folder(set_combos=True, silent=True)
        except Exception:
            pass
        u = self._selected_raster_source(getattr(self, 'u_combo', None), getattr(self, 'u_file', None))
        k = self._selected_raster_source(getattr(self, 'k_combo', None), getattr(self, 'k_file', None))
        th = self._selected_raster_source(getattr(self, 'th_combo', None), getattr(self, 'th_file', None))
        if 'Polygon extraction only' not in mode_text and (not u or not k or not th):
            return (False, 2, 'Missing input rasters', 'Stage 1 needs Uranium, Potassium and Thorium rasters. Put them in 01_Input_Data or select loaded QGIS raster layers in Stage 1.')

        # Ratio display quantiles and optional clipping settings.
        if hasattr(self, 'style_low') and self.style_low.value() >= self.style_high.value():
            return (False, 2, 'Invalid ratio display range', 'In Stage 1 / Ratio Map Generation, the display lower quantile must be less than the display upper quantile.')
        if hasattr(self, 'clip_ratio_extremes') and self.clip_ratio_extremes.isChecked():
            if self.clip_ratio_low.value() >= self.clip_ratio_high.value():
                return (False, 2, 'Invalid ratio clipping range', 'In Stage 1 / Ratio Map Generation, the clipping low percentile must be less than the clipping high percentile.')

        # For polygon-only mode, require existing threshold/combined widgets and generated inputs.
        if 'Polygon extraction only' in mode_text:
            if self.stage3_widget is None:
                return (False, 3, 'Stage 2 not available', 'Stage 2 threshold/anomaly widget is not loaded.')
        return (True, None, '', '')

    def _run_auto_workflow(self):
        mode = self.autorun_combo.currentText() if hasattr(self, 'autorun_combo') else 'All - run full workflow including polygon extraction'
        ok, tab, title, msg = self._validate_auto_workflow(mode)
        if not ok:
            self._autorun_error(tab, title, msg)
            return

        do_stage1 = 'Polygon extraction only' not in mode
        do_stage2_threshold = ('Stage 2 threshold' in mode) or ('Everything except' in mode) or mode.startswith('All') or ('Stage 3 threshold' in mode)
        do_stage2_polygons = mode.startswith('All') or mode.startswith('Polygon extraction only')
        do_stage3_combined = ('Stage 3 threshold' in mode) or ('Everything except' in mode) or mode.startswith('All')
        do_stage3_threshold = do_stage3_combined
        do_stage3_polygons = mode.startswith('All') or mode.startswith('Polygon extraction only')

        try:
            self._set_progress('autorun_progress', 0, 'Starting Auto Run...')
            if do_stage1:
                self.tabs.setCurrentIndex(2)
                self._set_progress('autorun_progress', 5, 'Running Stage 1 preprocessing...')
                self.run_preprocessing()
                self._set_progress('autorun_progress', 25, 'Running Stage 1 ratio maps...')
                self.run_ratio_generation()

            if do_stage2_threshold or do_stage2_polygons:
                self._set_progress('autorun_progress', 50, 'Running Stage 2 ratio analysis...')
                self.tabs.setCurrentIndex(3)
                if self.stage3_widget is None:
                    raise RuntimeError('Stage 2 widget is not available.')
                self.stage3_widget.processing_folder = self.state.processing_folder
                if hasattr(self.stage3_widget, '_auto_from_processing_folder'):
                    self.stage3_widget._auto_from_processing_folder()
                if do_stage2_threshold:
                    fn = getattr(self.stage3_widget, '_generate_threshold_maps', None) or getattr(self.stage3_widget, '_generate_threshold_map', None)
                    if callable(fn):
                        fn()
                    else:
                        raise RuntimeError('Stage 2 threshold map generator is not available in this build.')
                if do_stage2_polygons:
                    fn = getattr(self.stage3_widget, '_extract_polygons', None)
                    if callable(fn):
                        fn()
                    else:
                        raise RuntimeError('Stage 2 polygon extraction is not available in this build.')

            if do_stage3_combined or do_stage3_threshold or do_stage3_polygons:
                self._set_progress('autorun_progress', 75, 'Running Stage 3 combined analysis...')
                self.tabs.setCurrentIndex(4)
                if self.stage4_widget is None:
                    raise RuntimeError('Stage 3 combined-analysis widget is not available.')
                self.stage4_widget.processing_folder = self.state.processing_folder
                if hasattr(self.stage4_widget, '_auto_from_processing_folder'):
                    self.stage4_widget._auto_from_processing_folder()
                if do_stage3_combined:
                    fn = getattr(self.stage4_widget, '_generate_combined', None)
                    if callable(fn):
                        fn()
                    else:
                        raise RuntimeError('Stage 3 combined-map generator is not available in this build.')
                if do_stage3_threshold:
                    load_fn = getattr(self.stage4_widget, '_load_and_plot', None)
                    if callable(load_fn):
                        load_fn()
                    fn = getattr(self.stage4_widget, '_generate_threshold_map', None) or getattr(self.stage4_widget, '_generate_threshold_maps', None)
                    if callable(fn):
                        fn()
                    else:
                        raise RuntimeError('Stage 3 threshold map generator is not available in this build.')
                if do_stage3_polygons:
                    fn = getattr(self.stage4_widget, '_extract_polygons', None)
                    if callable(fn):
                        fn()
                    else:
                        raise RuntimeError('Stage 3 polygon extraction is not available in this build.')

            self._set_progress('autorun_progress', 100, 'Auto Run complete')
            QMessageBox.information(self, 'Auto Run complete', 'Selected workflow finished. Check the log boxes and QGIS layer groups for outputs.')
        except Exception as e:
            QMessageBox.critical(self, 'Auto Run stopped', 'Auto Run stopped before completion. Check the current tab settings and logs.\n\n' + str(e))


    def _load_project_layers_from_processing_folder(self):
        """Load existing raster/vector outputs from the processing folder into QGIS groups.

        Layers are grouped under a fixed top-level group called "Radiometric Processing"
        and then nested by the folders beneath the selected processing folder. This is
        intended for re-opening an existing radiometric project and reconstructing the
        layer tree without re-running processing.
        """
        folder = getattr(self.state, 'processing_folder', '') or self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, 'Processing folder required', 'Please set a valid processing folder first.')
            return

        raster_exts = ('.tif', '.tiff', '.vrt', '.img', '.asc')
        vector_exts = ('.gpkg', '.shp', '.geojson')
        skip_dirs = {'README', 'Radiometric_Project_Settings', 'settings_history', '__pycache__'}

        files = []
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
            for name in names:
                low = name.lower()
                if low.endswith(raster_exts) or low.endswith(vector_exts):
                    files.append(os.path.join(root, name))

        if not files:
            QMessageBox.information(self, 'No layers found', 'No raster or vector layers were found in the processing folder.')
            return

        root_node = QgsProject.instance().layerTreeRoot()
        main_group = root_node.findGroup('Radiometric Processing')
        if main_group is None:
            old_main_group = root_node.findGroup('Radiometric_Processing')
            if old_main_group is not None:
                old_main_group.setName('Radiometric Processing')
                main_group = old_main_group
            else:
                main_group = root_node.addGroup('Radiometric Processing')

        def get_group(parts):
            g = main_group
            for part in parts:
                if not part:
                    continue
                child = g.findGroup(part)
                if child is None:
                    child = g.addGroup(part)
                g = child
            return g

        loaded = 0
        skipped = 0
        for path in sorted(files):
            rel_dir = os.path.relpath(os.path.dirname(path), folder)
            parts = [] if rel_dir in ('.', '') else rel_dir.split(os.sep)
            target = get_group(parts)
            name = os.path.splitext(os.path.basename(path))[0]
            low = path.lower()

            exists = False
            for lyr in QgsProject.instance().mapLayers().values():
                try:
                    if os.path.normcase(os.path.abspath(lyr.source().split('|')[0])) == os.path.normcase(os.path.abspath(path)):
                        exists = True
                        break
                except Exception:
                    pass
            if exists:
                skipped += 1
                continue

            if low.endswith(raster_exts):
                layer = QgsRasterLayer(path, name)
            else:
                layer = QgsVectorLayer(path, name, 'ogr')

            if not layer.isValid():
                skipped += 1
                continue

            QgsProject.instance().addMapLayer(layer, False)
            target.addLayer(layer)
            loaded += 1

        QMessageBox.information(self, 'Project layers loaded', f'Loaded {loaded} layer(s) from the processing folder. Skipped {skipped} existing or invalid layer(s).')



    def _make_data_qc_tab(self):
        """Data Analysis and QC area with separate QC/statistics, histogram and crossplot subtabs."""
        w = QWidget()
        root = QVBoxLayout(w)

        h = QHBoxLayout()
        heading = QLabel('Data Analysis and QC')
        heading.setObjectName('SectionHeading')
        heading.setStyleSheet('font-weight:bold; font-size:14px;')
        heading.setWordWrap(True)
        h.addWidget(heading, 1)
        h.addWidget(self._guide_button('Data Analysis and QC - User Guide and Theory', DATA_QC_GUIDE_SECTIONS))
        root.addLayout(h)

        analysis_tabs = QTabWidget()
        analysis_tabs.setTabPosition(QTabWidget.North)
        root.addWidget(analysis_tabs, 1)

        # ------------------------------------------------------------------
        # Sub-tab 1: QC and Statistics. This tab now only runs/statistically
        # reports QC. It does not redraw the histogram tab.
        # ------------------------------------------------------------------
        qc_tab = QWidget(); qc_root = QVBoxLayout(qc_tab)
        data_to_qc = CollapsibleBox('Data to QC', checked=True)
        dtl = QGridLayout()
        self.qc_mode_combo = QComboBox()
        self.qc_mode_combo.addItems([
            'Input Data',
            'Pre-processed Data',
            'Ratio Maps',
            'Normalised Ratio Maps',
            'RRAI Maps',
        ])
        self.qc_mode_combo.currentIndexChanged.connect(self._update_qc_data_inputs)
        dtl.addWidget(QLabel('Select data type:'), 0, 0)
        dtl.addWidget(self.qc_mode_combo, 0, 1)
        note = QLabel('QC rasters are detected automatically from the radiometric processing folder. Use the checkboxes below to include or exclude each detected raster from the QC run.')
        note.setWordWrap(True)
        dtl.addWidget(note, 1, 0, 1, 3)

        # Nested subsection within Data to QC, per requested layout.
        inputs_group = QGroupBox('Input Data to QC - Radiometric Rasters')
        il = QGridLayout(inputs_group)
        self.qc_path_checks = []
        self.qc_path_labels = []
        self.qc_path_edits = []
        for row in range(4):
            chk = QCheckBox()
            chk.setChecked(True)
            chk.setToolTip('Include this raster in the QC run')
            lab = QLabel('')
            edit = QLineEdit('No data available')
            edit.setReadOnly(True)
            edit.setMinimumWidth(420)
            lab.setVisible(False)
            edit.setVisible(False)
            chk.setVisible(False)
            il.addWidget(chk, row, 0)
            il.addWidget(lab, row, 1)
            il.addWidget(edit, row, 2)
            self.qc_path_checks.append(chk)
            self.qc_path_labels.append(lab)
            self.qc_path_edits.append(edit)
        dtl.addWidget(inputs_group, 2, 0, 1, 3)
        data_to_qc.setContentLayout(dtl)
        qc_root.addWidget(data_to_qc)

        run_row = QHBoxLayout()
        run = QPushButton('Run Data QC'); run.setObjectName('PrimaryButton'); run.setMinimumHeight(34); run.clicked.connect(self.run_input_data_qc)
        run_row.addWidget(run)
        run_row.addStretch(1)
        qc_root.addLayout(run_row)

        results = CollapsibleBox('QC Summary / Flags', checked=True)
        rl = QVBoxLayout()
        self.qc_summary = QTextEdit(); self.qc_summary.setReadOnly(True); self.qc_summary.setMinimumHeight(260)
        rl.addWidget(self.qc_summary)
        results.setContentLayout(rl)
        qc_root.addWidget(results)
        qc_root.addStretch(1)
        analysis_tabs.addTab(qc_tab, 'QC')

        # ------------------------------------------------------------------
        # Sub-tab 2: Histograms. This is independent of the Data QC run.
        # The user selects one raster and explicitly generates a histogram.
        # ------------------------------------------------------------------
        hist_tab = QWidget(); hist_root = QVBoxLayout(hist_tab)
        hist_controls = CollapsibleBox('Histogram Input', checked=True)
        hcl = QGridLayout()
        self.hist_raster_combo = QComboBox()
        self.hist_raster_combo.setMinimumWidth(420)
        self.hist_bins = QSpinBox(); self.hist_bins.setRange(20, 300); self.hist_bins.setValue(80)
        self.hist_sampling_mode = QComboBox(); self.hist_sampling_mode.addItems(['Full raster', 'Fast preview', 'Custom sample count'])
        self.hist_sample_max = QSpinBox(); self.hist_sample_max.setRange(10000, 5000000); self.hist_sample_max.setSingleStep(50000); self.hist_sample_max.setValue(500000)
        self.hist_sampling_mode.currentIndexChanged.connect(self._update_histogram_sampling_controls)
        refresh_hist = QPushButton('Refresh Raster List')
        refresh_hist.clicked.connect(self._refresh_histogram_raster_combo)
        run_hist = QPushButton('Generate Histogram')
        run_hist.setObjectName('PrimaryButton')
        run_hist.clicked.connect(self.run_single_raster_histogram)
        export_hist_img = QPushButton('Export Histogram Image')
        export_hist_img.clicked.connect(self.export_histogram_image)
        export_hist_data = QPushButton('Export Histogram Data')
        export_hist_data.clicked.connect(self.export_histogram_data)
        hcl.addWidget(QLabel('Raster:'), 0, 0); hcl.addWidget(self.hist_raster_combo, 0, 1, 1, 3)
        hcl.addWidget(QLabel('Histogram bins:'), 1, 0); hcl.addWidget(self.hist_bins, 1, 1)
        hcl.addWidget(QLabel('Sampling mode:'), 2, 0); hcl.addWidget(self.hist_sampling_mode, 2, 1)
        hcl.addWidget(QLabel('Maximum sampled cells:'), 3, 0); hcl.addWidget(self.hist_sample_max, 3, 1)
        hcl.addWidget(refresh_hist, 4, 1); hcl.addWidget(run_hist, 4, 2)
        hcl.addWidget(export_hist_img, 5, 1); hcl.addWidget(export_hist_data, 5, 2)
        hist_note = QLabel('Histogram sampling is exploratory only. The QC tab uses full raster statistics for deterministic validation.')
        hist_note.setWordWrap(True)
        hcl.addWidget(hist_note, 6, 0, 1, 4)
        self._update_histogram_sampling_controls()
        hist_controls.setContentLayout(hcl)
        hist_root.addWidget(hist_controls)

        hist_box = CollapsibleBox('Raster Histogram Display', checked=True)
        hl = QVBoxLayout()
        if FigureCanvas is not None:
            self.qc_fig = Figure(figsize=(8, 4), tight_layout=True)
            self.qc_canvas = FigureCanvas(self.qc_fig)
            self.qc_canvas.setMinimumHeight(420)
            hl.addWidget(self.qc_canvas)
        else:
            self.qc_canvas = None
            hl.addWidget(QLabel('Matplotlib is not available in this QGIS Python environment. QC statistics will still be shown.'))
        hist_box.setContentLayout(hl)
        hist_root.addWidget(hist_box)

        hist_stats_box = CollapsibleBox('Statistics', checked=True)
        hsl = QVBoxLayout()
        self.hist_stats_text = QTextEdit()
        self.hist_stats_text.setReadOnly(True)
        self.hist_stats_text.setMinimumHeight(150)
        self.hist_stats_text.setPlainText('Generate a histogram to view statistics for the selected raster.')
        hsl.addWidget(self.hist_stats_text)
        hist_stats_box.setContentLayout(hsl)
        hist_root.addWidget(hist_stats_box)
        hist_root.addStretch(1)
        analysis_tabs.addTab(hist_tab, 'Histograms')

        # ------------------------------------------------------------------
        # Sub-tab 3: Crossplots. Existing Stage 3 crossplot tool embedded here.
        # ------------------------------------------------------------------
        crossplot_tab = QWidget(); crossplot_root = QVBoxLayout(crossplot_tab)
        self.crossplot_widget = CombinedAnalysisDialog(crossplot_tab, processing_folder=self._active_processing_folder(), crossplot_only=True)
        self.crossplot_widget.setWindowFlags(Qt.Widget)
        self.crossplot_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        crossplot_root.addWidget(self.crossplot_widget, 1)
        analysis_tabs.addTab(crossplot_tab, 'Crossplots')

        try:
            analysis_tabs.setTabIcon(0, self._icon('qc'))
            analysis_tabs.setTabIcon(1, self._icon('histogram'))
            analysis_tabs.setTabIcon(2, self._icon('crossplot'))
        except Exception:
            pass

        self._update_qc_data_inputs()
        self._refresh_histogram_raster_combo()
        return w

    def _find_latest_raster(self, folder, tokens, exclude_tokens=None):
        """Find the newest GeoTIFF in folder matching all token groups.

        tokens can be strings or tuples/lists. A string must be present in the
        filename. A tuple/list means any of the alternatives can be present.
        """
        if not folder or not os.path.isdir(folder):
            return ''
        exclude_tokens = [t.lower() for t in (exclude_tokens or [])]
        matches = []
        for root, _, files in os.walk(folder):
            for fname in files:
                low = fname.lower().replace('-', '_').replace(' ', '_')
                if not low.endswith(('.tif', '.tiff')):
                    continue
                if any(t in low for t in exclude_tokens):
                    continue
                ok = True
                for token in tokens:
                    if isinstance(token, (tuple, list)):
                        if not any(str(t).lower() in low for t in token):
                            ok = False; break
                    else:
                        if str(token).lower() not in low:
                            ok = False; break
                if ok:
                    path = os.path.join(root, fname)
                    matches.append((os.path.getmtime(path), path))
        if not matches:
            return ''
        matches.sort(reverse=True)
        return matches[0][1]

    def _qc_expected_entries(self):
        mode = self.qc_mode_combo.currentText() if hasattr(self, 'qc_mode_combo') else 'Input Data'
        folder = self.state.processing_folder or ''
        if mode == 'Input Data':
            root = os.path.join(folder, '01_Input_Data')
            return [
                ('Uranium (U)', self._find_latest_raster(root, [('uranium', 'u_')], exclude_tokens=['ratio','norm','rrai','combined','k_th','u_th','u_k'])),
                ('Potassium (K)', self._find_latest_raster(root, [('potassium', 'k_')], exclude_tokens=['ratio','norm','rrai','combined','k_th','u_th','u_k'])),
                ('Thorium (Th)', self._find_latest_raster(root, [('thorium', 'th_')], exclude_tokens=['ratio','norm','rrai','combined','k_th','u_th','u_k'])),
            ]
        if mode == 'Pre-processed Data':
            # Prefer final filtered rasters; fall back to conditioned rasters if required.
            root = os.path.join(folder, '02_Preprocessed')
            return [
                ('Uranium (U)', self._find_latest_raster(root, [('uranium', 'u_')], exclude_tokens=['ratio','norm','rrai','combined','k_th','u_th','u_k'])),
                ('Potassium (K)', self._find_latest_raster(root, [('potassium', 'k_')], exclude_tokens=['ratio','norm','rrai','combined','k_th','u_th','u_k'])),
                ('Thorium (Th)', self._find_latest_raster(root, [('thorium', 'th_')], exclude_tokens=['ratio','norm','rrai','combined','k_th','u_th','u_k'])),
            ]
        if mode == 'Ratio Maps':
            root = os.path.join(folder, '03_Ratio_Maps')
            return [
                ('U/Th ratio', self._find_latest_raster(root, ['u_th', 'ratio'])),
                ('U/K ratio', self._find_latest_raster(root, ['u_k', 'ratio'])),
                ('K/U ratio', self._find_latest_raster(root, ['k_u', 'ratio'])),
                ('K/Th ratio', self._find_latest_raster(root, ['k_th', 'ratio'])),
            ]
        if mode == 'Normalised Ratio Maps':
            root = os.path.join(folder, '04_Normalised_Ratio_Maps')
            return [
                ('Normalised U/Th', self._find_latest_raster(root, ['u_th', 'norm'])),
                ('Normalised U/K', self._find_latest_raster(root, ['u_k', 'norm'])),
                ('Normalised K/Th', self._find_latest_raster(root, ['k_th', 'norm'], exclude_tokens=['inverse'])),
            ]
        # RRAI / combined map: one raster only.
        roots = [os.path.join(folder, '07_Combined_Maps'), os.path.join(folder, '08_Combined_Threshold_Maps')]
        found = ''
        for root in roots:
            found = self._find_latest_raster(root, [('combined_radiometric_index', 'rrai', 'combined')])
            if found:
                break
        return [('Combined / RRAI map', found)]

    def _update_qc_data_inputs(self):
        if not hasattr(self, 'qc_path_labels'):
            return
        entries = self._qc_expected_entries()
        for i, (label, path) in enumerate(entries):
            self.qc_path_checks[i].setVisible(True)
            self.qc_path_checks[i].setEnabled(bool(path and os.path.exists(path)))
            if not path or not os.path.exists(path):
                self.qc_path_checks[i].setChecked(False)
            elif not self.qc_path_checks[i].isChecked():
                # Preserve a deliberate user-off state if the raster remains available.
                pass
            else:
                self.qc_path_checks[i].setChecked(True)
            self.qc_path_labels[i].setText(label + ':')
            self.qc_path_edits[i].setText(path if path and os.path.exists(path) else 'No data available')
            self.qc_path_labels[i].setVisible(True)
            self.qc_path_edits[i].setVisible(True)
        for i in range(len(entries), len(self.qc_path_labels)):
            self.qc_path_checks[i].setVisible(False)
            self.qc_path_labels[i].setVisible(False)
            self.qc_path_edits[i].setVisible(False)
        self._refresh_histogram_raster_combo()

    def _selected_qc_entries(self):
        entries = self._qc_expected_entries()
        selected = []
        missing = []
        for i, (label, path) in enumerate(entries):
            checked = True
            if hasattr(self, 'qc_path_checks') and i < len(self.qc_path_checks):
                checked = self.qc_path_checks[i].isChecked()
            if checked and path and os.path.exists(path):
                selected.append((label, path))
            elif checked:
                missing.append(label)
        return selected, missing

    def _refresh_histogram_raster_combo(self):
        """Populate the histogram raster selector from the processing folder only.

        This deliberately avoids listing every raster currently loaded in QGIS so
        the histogram workflow remains tied to the active radiometric processing
        folder selected on the main page.
        """
        if not hasattr(self, 'hist_raster_combo'):
            return
        cur = self.hist_raster_combo.currentData()
        self.hist_raster_combo.blockSignals(True)
        self.hist_raster_combo.clear()
        self.hist_raster_combo.addItem('(Select raster from processing folder)', '')

        folder = self.state.processing_folder or ''
        seen = set()

        # Put the detected QC rasters first for convenience.
        for label, path in self._qc_expected_entries():
            if path and os.path.exists(path):
                key = os.path.normcase(os.path.abspath(path))
                if key not in seen:
                    self.hist_raster_combo.addItem(label + ' - detected', path)
                    seen.add(key)

        # Then list all GeoTIFF rasters beneath the selected processing folder.
        if folder and os.path.isdir(folder):
            for root, _, files in os.walk(folder):
                for fname in sorted(files):
                    if not fname.lower().endswith(('.tif', '.tiff')):
                        continue
                    path = os.path.join(root, fname)
                    key = os.path.normcase(os.path.abspath(path))
                    if key in seen:
                        continue
                    rel = os.path.relpath(path, folder)
                    self.hist_raster_combo.addItem(rel, path)
                    seen.add(key)

        ix = self.hist_raster_combo.findData(cur)
        if ix >= 0:
            self.hist_raster_combo.setCurrentIndex(ix)
        self.hist_raster_combo.blockSignals(False)

    def _update_histogram_sampling_controls(self):
        try:
            mode = self.hist_sampling_mode.currentText().lower()
            self.hist_sample_max.setEnabled(not mode.startswith('full'))
            if mode.startswith('fast'):
                self.hist_sample_max.setValue(500000)
        except Exception:
            pass

    def _histogram_sample_limit(self):
        try:
            mode = self.hist_sampling_mode.currentText().lower()
            if mode.startswith('full'):
                return None
            return int(self.hist_sample_max.value())
        except Exception:
            return None

    def run_single_raster_histogram(self):
        if self.qc_canvas is None:
            QMessageBox.warning(self, 'Histogram unavailable', 'Matplotlib is not available in this QGIS Python environment.')
            return
        path = self.hist_raster_combo.currentData() if hasattr(self, 'hist_raster_combo') else ''
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, 'No raster selected', 'Select a valid raster before generating a histogram.')
            return
        label = self.hist_raster_combo.currentText().replace(' - detected', '').replace(' - QGIS layer', '')
        try:
            report = self._qc_one_raster(label, path, sample_max=self._histogram_sample_limit())
            report['path'] = path
            self._last_histogram_report = report
            self._last_histogram_label = label
            self._plot_single_histogram(label, report)
            if hasattr(self, 'hist_stats_text'):
                self.hist_stats_text.setPlainText(self._format_histogram_stats(label, report))
        except Exception as e:
            QMessageBox.critical(self, 'Histogram failed', str(e))

    def _plot_single_histogram(self, label, r):
        if self.qc_canvas is None:
            return
        self.qc_fig.clear()
        ax = self.qc_fig.add_subplot(111)
        vals = r.get('values')
        ax.set_title(label)
        ax.set_xlabel(r.get('unit', 'value'))
        ax.set_ylabel('Frequency')
        if vals is not None and vals.size:
            lo, hi = np.nanpercentile(vals, [1, 99])
            plot_vals = vals[np.isfinite(vals)]
            if hi > lo:
                plot_vals = plot_vals[(plot_vals >= lo) & (plot_vals <= hi)]
            ax.hist(plot_vals, bins=int(self.hist_bins.value()))
            stats = r.get('stats', {})
            mean = stats.get('mean')
            med = stats.get('median')
            if mean is not None: ax.axvline(mean, linestyle='-', linewidth=1, label='mean')
            if med is not None: ax.axvline(med, linestyle='--', linewidth=1, label='median')
            ax.legend(fontsize=8)
        else:
            ax.text(0.5, 0.5, 'No valid cells', ha='center', va='center')
        self.qc_fig.tight_layout()
        self.qc_canvas.draw()

    def _histogram_export_dir(self):
        folder = self.state.processing_folder or ''
        if not folder:
            return ''
        return ensure_stats_log_structure(folder)['histogram']

    def _safe_export_stem(self, label):
        stem = ''.join(ch if ch.isalnum() or ch in ('_', '-') else '_' for ch in str(label or 'histogram'))
        while '__' in stem:
            stem = stem.replace('__', '_')
        return stem.strip('_') or 'histogram'

    def export_histogram_image(self):
        if self.qc_canvas is None or not hasattr(self, 'qc_fig'):
            QMessageBox.warning(self, 'No histogram', 'Generate a histogram before exporting an image.')
            return
        outdir = self._histogram_export_dir()
        if not outdir:
            QMessageBox.warning(self, 'Processing folder required', 'Set the Processing folder before exporting histogram outputs.')
            return
        label = getattr(self, '_last_histogram_label', 'histogram')
        path = make_available_path(os.path.join(outdir, self._safe_export_stem(label) + '_histogram.png'))
        try:
            self.qc_fig.savefig(path, dpi=180)
            append_session_log(self.state.processing_folder, 'Histogram Export Image', outputs={'image': path}, parameters={'Raster': label})
            QMessageBox.information(self, 'Histogram image exported', path)
        except Exception as e:
            QMessageBox.critical(self, 'Histogram image export failed', str(e))

    def export_histogram_data(self):
        r = getattr(self, '_last_histogram_report', None)
        label = getattr(self, '_last_histogram_label', 'histogram')
        if not isinstance(r, dict) or r.get('values') is None:
            QMessageBox.warning(self, 'No histogram', 'Generate a histogram before exporting histogram data.')
            return
        outdir = self._histogram_export_dir()
        if not outdir:
            QMessageBox.warning(self, 'Processing folder required', 'Set the Processing folder before exporting histogram outputs.')
            return
        path = make_available_path(os.path.join(outdir, self._safe_export_stem(label) + '_histogram_data.csv'))
        try:
            vals = r.get('values')
            vals = vals[np.isfinite(vals)] if vals is not None else np.array([])
            counts, edges = np.histogram(vals, bins=int(self.hist_bins.value())) if vals.size else (np.array([]), np.array([]))
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['SECTION', 'NAME', 'PARAMETER', 'VALUE'])
                w.writerow(['Histogram metadata', label, 'Raster_path', r.get('path', '')])
                w.writerow(['Histogram metadata', label, 'Sampling_mode', self.hist_sampling_mode.currentText() if hasattr(self, 'hist_sampling_mode') else ''])
                w.writerow(['Histogram metadata', label, 'Histogram_bins', int(self.hist_bins.value()) if hasattr(self, 'hist_bins') else ''])
                for k, v in (r.get('stats') or {}).items():
                    w.writerow(['Raster statistics', label, k, v])
                w.writerow([])
                w.writerow(['bin_index', 'bin_min', 'bin_max', 'count'])
                for i, c in enumerate(counts):
                    w.writerow([i + 1, edges[i], edges[i + 1], int(c)])
            append_session_log(self.state.processing_folder, 'Histogram Export Data', outputs={'csv': path}, parameters={'Raster': label})
            QMessageBox.information(self, 'Histogram data exported', path)
        except Exception as e:
            QMessageBox.critical(self, 'Histogram data export failed', str(e))

    def _format_histogram_stats(self, label, r):
        stats = r.get('stats', {}) if isinstance(r, dict) else {}
        vals = r.get('values') if isinstance(r, dict) else None
        lines = [f'Raster: {label}']
        if r.get('path'):
            lines.append(f'Path: {r.get("path")}')
        def fmt(v):
            try:
                if v is None or not np.isfinite(float(v)):
                    return 'n/a'
                return f'{float(v):.6g}'
            except Exception:
                return 'n/a'
        ordered = [
            ('Total cells', r.get('total')),
            ('Valid cells', r.get('valid')),
            ('NoData / invalid cells', r.get('nodata')),
            ('Cell size X', r.get('cell_x')),
            ('Cell size Y', r.get('cell_y')),
            ('Minimum', stats.get('min')),
            ('Maximum', stats.get('max')),
            ('Mean', stats.get('mean')),
            ('Median', stats.get('median')),
            ('Standard deviation', stats.get('std')),
            ('P01', stats.get('P1') or stats.get('p01')),
            ('P05', stats.get('P5') or stats.get('p05')),
            ('P50', stats.get('P50') or stats.get('p50')),
            ('P95', stats.get('P95') or stats.get('p95')),
            ('P99', stats.get('P99') or stats.get('p99')),
        ]
        for k, v in ordered:
            if v is not None:
                lines.append(f'{k}: {fmt(v)}')
        if vals is not None and hasattr(vals, 'size'):
            lines.append(f'Sampled values used: {int(vals.size):,}')
        if r.get('warnings'):
            lines.append('')
            lines.append('Flags:')
            for w in r.get('warnings'):
                lines.append(f'- {w}')
        return '\n'.join(lines)

    def _refresh_qc_raster_combos(self):
        for attr in ('qc_u_combo', 'qc_k_combo', 'qc_th_combo'):
            old = getattr(self, attr, None)
            if old is None:
                continue
            cur = old.currentData()
            old.blockSignals(True)
            old.clear(); old.addItem('(Select loaded raster layer)', '')
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsRasterLayer) and lyr.isValid():
                    old.addItem(lyr.name(), lyr.source())
            ix = old.findData(cur)
            if ix >= 0: old.setCurrentIndex(ix)
            old.blockSignals(False)

    def _qc_use_stage1_inputs(self):
        # Copy the Stage 1 selected rasters into the QC selectors where possible.
        mapping = [
            ('u_combo', 'u_file', 'qc_u_combo', 'qc_u_file'),
            ('k_combo', 'k_file', 'qc_k_combo', 'qc_k_file'),
            ('th_combo', 'th_file', 'qc_th_combo', 'qc_th_file'),
        ]
        for src_combo_name, src_file_name, dst_combo_name, dst_file_name in mapping:
            src_combo = getattr(self, src_combo_name, None)
            src_file = getattr(self, src_file_name, None)
            dst_combo = getattr(self, dst_combo_name, None)
            dst_file = getattr(self, dst_file_name, None)
            if not dst_combo or not dst_file:
                continue
            src_path = ''
            if src_combo is not None:
                src_path = src_combo.currentData() or ''
            if not src_path and src_file is not None:
                src_path = src_file.text().strip()
            ix = dst_combo.findData(src_path)
            if ix >= 0:
                dst_combo.setCurrentIndex(ix)
                dst_file.clear()
            else:
                dst_file.setText(src_path)

    def _sample_valid_values(self, arr, max_cells):
        vals = arr[np.isfinite(arr)]
        if vals.size > max_cells:
            idx = np.linspace(0, vals.size - 1, int(max_cells)).astype(np.int64)
            vals = vals[idx]
        return vals

    def _infer_units_and_flags(self, label, ds, vals, nodata_count, total_count):
        flags = []
        unit = 'unknown'
        if ds.RasterCount >= 3:
            # RGB-style grids usually have three byte bands with 0-255 display values.
            b1 = ds.GetRasterBand(1)
            dtype = gdal.GetDataTypeName(b1.DataType).lower()
            if 'byte' in dtype:
                unit = 'RGB / display raster, not radiometric values'
                flags.append('Raster has 3+ byte bands; this looks like RGB/display data rather than a single-band radiometric grid.')
        if vals.size == 0:
            flags.append('No valid numeric cells found.')
            return unit, flags
        vmin, vmax, mean = float(np.nanmin(vals)), float(np.nanmax(vals)), float(np.nanmean(vals))
        low_label = label.lower()
        if unit == 'unknown':
            if 'potassium' in low_label or low_label.startswith('k'):
                unit = 'likely % K'
                if vmin < -0.1: flags.append('Potassium has negative values; check NoData/null handling or calibration.')
                if vmax > 20: flags.append('Potassium maximum is unusually high for %K; check units or outliers.')
                elif vmax > 10: flags.append('Potassium maximum is high; may be valid locally but worth checking.')
            elif 'uranium' in low_label or low_label.startswith('u'):
                unit = 'likely ppm eU'
                if vmin < -0.1: flags.append('Uranium has negative values; check NoData/null handling or calibration.')
                if vmax > 100: flags.append('Uranium maximum is very high; check spikes/outliers or unit scaling.')
                elif vmax > 50: flags.append('Uranium maximum is high; inspect for isolated spikes.')
            elif 'thorium' in low_label or 'th' in low_label:
                unit = 'likely ppm eTh'
                if vmin < -0.1: flags.append('Thorium has negative values; check NoData/null handling or calibration.')
                if vmax > 300: flags.append('Thorium maximum is very high; check spikes/outliers or unit scaling.')
                elif vmax > 150: flags.append('Thorium maximum is high; inspect for isolated spikes.')
            else:
                if 0 <= vmin and vmax <= 1.5:
                    unit = 'normalised/index-like values'
                elif 0 <= vmin and vmax <= 255 and np.allclose(vals[:min(vals.size,1000)], np.round(vals[:min(vals.size,1000)])):
                    unit = 'possibly byte/display values'
                    flags.append('Values are integer-like and within 0-255; confirm this is not RGB/display data.')
                elif vmax <= 30:
                    unit = 'possibly % or low-range ppm'
                else:
                    unit = 'possibly ppm'
        if nodata_count / max(total_count, 1) > 0.5:
            flags.append('More than 50% of cells are NoData/null.')
        if mean == 0 and vmax == 0:
            flags.append('All valid values are zero.')
        if vmax > 0 and (float(np.nanpercentile(vals, 99)) / max(float(np.nanmedian(vals)), 1e-12)) > 50:
            flags.append('Extreme upper tail detected; histogram/threshold review should use compression/winsorising.')
        return unit, flags

    def _qc_one_raster(self, label, path, sample_max=None):
        ds = gdal.Open(path)
        if ds is None:
            raise ValueError(f'Could not open {label} raster: {path}')
        band = ds.GetRasterBand(1)
        arr = band.ReadAsArray().astype(np.float64)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            arr[arr == nodata] = np.nan
        arr[arr <= -9999] = np.nan
        total = arr.size
        valid_count = int(np.isfinite(arr).sum())
        nodata_count = int(total - valid_count)
        vals = self._sample_valid_values(arr, sample_max) if sample_max else arr[np.isfinite(arr)]
        gt = ds.GetGeoTransform()
        cell_x = abs(float(gt[1])) if gt else None
        cell_y = abs(float(gt[5])) if gt else None
        stats = {}
        if vals.size:
            for name, func in [('min', np.nanmin), ('max', np.nanmax), ('mean', np.nanmean), ('median', np.nanmedian), ('std', np.nanstd)]:
                stats[name] = float(func(vals))
            for p in (1, 5, 50, 95, 99):
                stats[f'P{p}'] = float(np.nanpercentile(vals, p))
        unit, flags = self._infer_units_and_flags(label, ds, vals, nodata_count, total)
        meta = {
            'path': path, 'xsize': ds.RasterXSize, 'ysize': ds.RasterYSize, 'bands': ds.RasterCount,
            'cell_x': cell_x, 'cell_y': cell_y, 'total': total, 'valid': valid_count,
            'nodata': nodata_count, 'nodata_value': nodata, 'unit': unit, 'flags': flags,
            'stats': stats, 'values': vals
        }
        ds = None
        return meta

    def run_input_data_qc(self):
        self._update_qc_data_inputs()
        entries = self._qc_expected_entries()
        selected_entries, missing = self._selected_qc_entries()
        paths = {label: path for label, path in selected_entries}
        if not paths:
            QMessageBox.warning(self, 'No QC rasters selected', 'No valid rasters are selected for the current Data to QC option.')
            self.qc_summary.setPlainText('No valid rasters selected for: ' + (self.qc_mode_combo.currentText() if hasattr(self, 'qc_mode_combo') else 'selected QC mode'))
            return
        self.qc_summary.clear()
        reports = []
        try:
            for label, path in paths.items():
                reports.append((label, self._qc_one_raster(label, path)))
            lines = []
            lines.append('Data QC mode: ' + self.qc_mode_combo.currentText())
            lines.append('Processing folder: ' + (self.state.processing_folder or '(not set)'))
            lines.append('')
            if missing:
                lines.append('Missing / not found:')
                for m in missing:
                    lines.append('  - ' + m + ': No data available')
                lines.append('')
            for label, r in reports:
                lines.append(f'{label}')
                lines.append('-' * len(label))
                lines.append(f"Path: {r['path']}")
                lines.append(f"Raster size: {r['xsize']} x {r['ysize']} pixels ({r['total']:,} cells)")
                lines.append(f"Bands: {r['bands']}")
                if r['cell_x'] is not None:
                    lines.append(f"Cell size: {r['cell_x']:.4g} x {r['cell_y']:.4g}")
                lines.append(f"Valid cells: {r['valid']:,}  |  NoData/null cells: {r['nodata']:,} ({100.0*r['nodata']/max(r['total'],1):.2f}%)")
                lines.append(f"NoData value from raster: {r['nodata_value']}")
                lines.append(f"Inferred unit/type: {r['unit']}")
                if r['stats']:
                    st = r['stats']
                    lines.append('Stats: min={min:.4g}, P1={P1:.4g}, P5={P5:.4g}, median={median:.4g}, mean={mean:.4g}, std={std:.4g}, P95={P95:.4g}, P99={P99:.4g}, max={max:.4g}'.format(**st))
                if r['flags']:
                    lines.append('Flags:')
                    for f in r['flags']:
                        lines.append('  - ' + f)
                else:
                    lines.append('Flags: none obvious from basic range/statistical checks.')
                lines.append('')
            self.qc_summary.setPlainText('\n'.join(lines))
        except Exception as e:
            QMessageBox.critical(self, 'Data QC failed', str(e))

    def _plot_qc_histograms(self, reports):
        if self.qc_canvas is None:
            return
        self.qc_fig.clear()
        axes = self.qc_fig.subplots(1, 3)
        if not isinstance(axes, (list, np.ndarray)):
            axes = [axes]
        for ax, (label, r) in zip(axes, reports):
            vals = r['values']
            ax.set_title(label)
            ax.set_xlabel(r['unit'])
            ax.set_ylabel('Frequency')
            if vals.size:
                lo, hi = np.nanpercentile(vals, [1, 99])
                plot_vals = vals[np.isfinite(vals)]
                if hi > lo:
                    plot_vals = plot_vals[(plot_vals >= lo) & (plot_vals <= hi)]
                ax.hist(plot_vals, bins=int(self.hist_bins.value()) if hasattr(self, 'hist_bins') else 80)
                mean = r['stats'].get('mean')
                med = r['stats'].get('median')
                if mean is not None: ax.axvline(mean, linestyle='-', linewidth=1, label='mean')
                if med is not None: ax.axvline(med, linestyle='--', linewidth=1, label='median')
                ax.legend(fontsize=7)
            else:
                ax.text(0.5, 0.5, 'No valid cells', ha='center', va='center')
        self.qc_fig.tight_layout()
        self.qc_canvas.draw()

    def _refresh_ratio_raster_combos(self):
        for attr in ('ratio_u_combo', 'ratio_k_combo', 'ratio_th_combo'):
            combo = getattr(self, attr, None)
            if combo is None:
                continue
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear(); combo.addItem('(Auto / select loaded filtered raster)', '')
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsRasterLayer) and lyr.isValid():
                    name_low = (lyr.name() + ' ' + os.path.basename(lyr.source())).lower()
                    if any(bad in name_low for bad in ('ratio', 'rrai', 'combined', 'normalised', 'normalized', 'class', 'mask')):
                        continue
                    combo.addItem(lyr.name(), lyr.source())
            ix = combo.findData(cur)
            if ix >= 0:
                combo.setCurrentIndex(ix)
            combo.blockSignals(False)

    def _selected_ratio_source(self, role):
        combo = getattr(self, {'U': 'ratio_u_combo', 'K': 'ratio_k_combo', 'TH': 'ratio_th_combo'}[role], None)
        val = (combo.currentData() or '').strip() if combo is not None else ''
        if val:
            return val
        return self.state.filtered_rasters.get(role, '')

    def _make_ratio_generation_stage_tab(self):
        w = QWidget(); root = QVBoxLayout(w)
        h = QHBoxLayout()
        heading = QLabel('Stage 1 - Ratio Maps')
        heading.setObjectName('SectionHeading')
        heading.setStyleSheet('font-weight:bold; font-size:14px;')
        heading.setWordWrap(True)
        h.addWidget(heading, 1)
        stage1_sections = [
            ('Stage 1 overview', 'Stage 1 prepares the Uranium, Potassium and Thorium rasters, then generates ratio maps. Use Pre-Processing / Data Conditioning first, then Ratio Map Generation.'),
            ('Pre-processing / Data Conditioning', PRE_GUIDE if isinstance(PRE_GUIDE, str) else 'Pre-processing guide.'),
            ('Ratio Map Generation', RATIO_GUIDE if isinstance(RATIO_GUIDE, str) else 'Ratio map guide.'),
        ]
        h.addWidget(self._guide_button('Stage 1 - User Guide and Theory', stage1_sections))
        root.addLayout(h)
        self.stage1_subtabs = QTabWidget()
        self.stage1_subtabs.addTab(self._make_preprocessing_tab(), self._icon('filter'), 'Pre-Processing / Data Conditioning')
        self.stage1_subtabs.addTab(self._make_ratio_tab(), self._icon('ratio'), 'Ratio Map Generation')
        root.addWidget(self.stage1_subtabs, 1)
        return w

    def _make_preprocessing_tab(self):
        w = QWidget(); root = QVBoxLayout(w)

        inputs = CollapsibleBox('Input Reference Radiometric Rasters', checked=True)
        il = QVBoxLayout()
        il.addLayout(self._raster_selector_row('Uranium raster / reference grid:', 'u_combo', 'u_file'))
        il.addLayout(self._raster_selector_row('Potassium raster:', 'k_combo', 'k_file'))
        il.addLayout(self._raster_selector_row('Thorium raster:', 'th_combo', 'th_file'))
        rr = QHBoxLayout()
        refresh = QPushButton('Refresh loaded QGIS layers'); refresh.clicked.connect(self._refresh_raster_combos)
        auto_project = QPushButton('Auto-load from project folder / QGIS project'); auto_project.clicked.connect(self._auto_stage1_inputs_from_project)
        rr.addWidget(refresh); rr.addWidget(auto_project); rr.addStretch(1); il.addLayout(rr)
        inputs.setContentLayout(il)
        root.addWidget(inputs)

        mask_box = CollapsibleBox('Optional Study Area Mask', checked=False)
        ml = QGridLayout()
        ml.addWidget(QLabel('Mask polygon from project:'), 0, 0)
        self.mask_combo = self._project_polygon_combo(include_none=True)
        ml.addWidget(self.mask_combo, 0, 1)
        note = QLabel('No file-path override is used here. Load the polygon into QGIS first, then select it from this list. If no mask is selected, the Uranium raster extent is used.')
        note.setWordWrap(True); ml.addWidget(note, 1, 0, 1, 2)
        mask_box.setContentLayout(ml)
        root.addWidget(mask_box)

        nodata = CollapsibleBox('NoData / NULL Handling', checked=True)
        nl = QGridLayout()
        self.use_extra_nodata = QCheckBox('Treat an additional NoData / NULL value as null'); self.use_extra_nodata.setChecked(True)
        self.extra_nodata = QDoubleSpinBox(); self.extra_nodata.setDecimals(3); self.extra_nodata.setRange(-1e12, 1e12); self.extra_nodata.setValue(-9999.0)
        self.invalid_le_zero = QCheckBox('Treat radiometric values <= 0 as invalid / NoData'); self.invalid_le_zero.setChecked(True)
        nl.addWidget(self.use_extra_nodata, 0, 0, 1, 2); nl.addWidget(QLabel('Additional NoData value:'), 1, 0); nl.addWidget(self.extra_nodata, 1, 1); nl.addWidget(self.invalid_le_zero, 2, 0, 1, 2)
        nodata.setContentLayout(nl)
        root.addWidget(nodata)

        filt = CollapsibleBox('Filtering / Data Conditioning', checked=True)
        fl = QGridLayout()
        self.filter_combo = QComboBox(); self.filter_combo.addItems(['Mean focal filter', 'Median focal filter', 'Gaussian filter'])
        self.window_combo = QComboBox(); self.window_combo.addItems(['3 x 3', '5 x 5', '7 x 7', '9 x 9', '11 x 11', '13 x 13', '15 x 15']); self.window_combo.setCurrentIndex(1)
        self.gauss_sigma = QDoubleSpinBox(); self.gauss_sigma.setDecimals(2); self.gauss_sigma.setRange(0.1, 20.0); self.gauss_sigma.setValue(1.0); self.gauss_sigma.setSingleStep(0.1)
        self.load_filtered = QCheckBox('Load filtered outputs into QGIS'); self.load_filtered.setChecked(True)
        fl.addWidget(QLabel('Filter method:'), 0, 0); fl.addWidget(self.filter_combo, 0, 1)
        fl.addWidget(QLabel('Window size:'), 1, 0); fl.addWidget(self.window_combo, 1, 1)
        fl.addWidget(QLabel('Gaussian sigma:'), 2, 0); fl.addWidget(self.gauss_sigma, 2, 1)
        fl.addWidget(self.load_filtered, 3, 0, 1, 2)
        filt.setContentLayout(fl)
        root.addWidget(filt)

        out_box = CollapsibleBox('Outputs', checked=True)
        ol = QVBoxLayout()
        self.pre_log = QTextEdit(); self.pre_log.setReadOnly(True); self.pre_log.setMaximumHeight(120)
        self.pre_progress = QProgressBar(); self.pre_progress.setRange(0, 100); self.pre_progress.setValue(0); self.pre_progress.setFormat('Idle')
        run = QPushButton('Run Pre-Processing / Data Conditioning'); run.setMinimumHeight(34); run.clicked.connect(self.run_preprocessing)
        ol.addWidget(QLabel('Outputs are written to 02_Preprocessed under the processing folder.'))
        ol.addWidget(run); ol.addWidget(self.pre_progress); ol.addWidget(self.pre_log)
        out_box.setContentLayout(ol)
        root.addWidget(out_box)

        root.addWidget(self._guide_button('Pre-Processing / Data Conditioning - User Guide and Theory', PRE_GUIDE_SECTIONS))
        root.addStretch(1)
        self._auto_load_input_rasters_from_folder(set_combos=True, silent=True)
        return w

    def _make_ratio_tab(self):
        w = QWidget(); root = QVBoxLayout(w)

        inp = CollapsibleBox('Filtered Raster Inputs from Pre-Processing', checked=True)
        il = QGridLayout()
        self.ratio_u_combo = self._project_raster_combo()
        self.ratio_k_combo = self._project_raster_combo()
        self.ratio_th_combo = self._project_raster_combo()
        il.addWidget(QLabel('Filtered Uranium:'), 0, 0); il.addWidget(self.ratio_u_combo, 0, 1)
        il.addWidget(QLabel('Filtered Potassium:'), 1, 0); il.addWidget(self.ratio_k_combo, 1, 1)
        il.addWidget(QLabel('Filtered Thorium:'), 2, 0); il.addWidget(self.ratio_th_combo, 2, 1)
        refresh = QPushButton('Auto-load latest filtered rasters from project folder')
        refresh.clicked.connect(lambda: self._auto_load_filtered_rasters_from_folder(set_combos=True, silent=False))
        refresh_layers = QPushButton('Refresh loaded QGIS raster list')
        refresh_layers.clicked.connect(self._refresh_ratio_raster_combos)
        il.addWidget(refresh, 3, 0, 1, 2)
        il.addWidget(refresh_layers, 4, 0, 1, 2)
        note = QLabel('The tab auto-selects the latest U/K/Th filtered rasters from 02_Preprocessed. It generates four ratio maps: U/Th, U/K, K/U and K/Th. U/K and K/U answer different geological questions, so both are retained and clearly named.')
        note.setWordWrap(True); il.addWidget(note, 5, 0, 1, 2)
        inp.setContentLayout(il)
        root.addWidget(inp)

        guard_box = CollapsibleBox('Ratio Calculation - Denominator Guard', checked=True)
        gl = QGridLayout()
        self.denominator_guard_mode = QComboBox()
        self.denominator_guard_mode.addItems(['Absolute value', 'Percentile of positive denominator values'])
        self.min_denominator = QDoubleSpinBox(); self.min_denominator.setRange(0.0, 999999.0); self.min_denominator.setDecimals(6); self.min_denominator.setValue(0.0)
        self.min_denominator.setToolTip('Absolute guard: denominator cells <= this value are set to NoData before division. Use 0 to reject zero and negative denominator cells.')
        self.denominator_percentile = QDoubleSpinBox(); self.denominator_percentile.setRange(0.0, 25.0); self.denominator_percentile.setDecimals(2); self.denominator_percentile.setValue(1.0)
        self.denominator_percentile.setToolTip('Percentile guard: compute this percentile from each denominator raster positive values, then mask denominator cells <= that value. Useful when near-zero positive values create spikes.')
        gl.addWidget(QLabel('Guard mode:'), 0, 0); gl.addWidget(self.denominator_guard_mode, 0, 1)
        gl.addWidget(QLabel('Absolute minimum denominator:'), 1, 0); gl.addWidget(self.min_denominator, 1, 1)
        gl.addWidget(QLabel('Denominator percentile:'), 2, 0); gl.addWidget(self.denominator_percentile, 2, 1)
        guard_note = QLabel('Applies separately to denominators in U/Th, U/K, K/U and K/Th. Absolute mode uses one fixed value. Percentile mode calculates a small positive denominator floor from each denominator raster, avoiding ratio spikes caused by near-zero K, U or Th values.')
        guard_note.setWordWrap(True); gl.addWidget(guard_note, 3, 0, 1, 2)
        def _toggle_den_guard_controls():
            is_pct = self.denominator_guard_mode.currentText().startswith('Percentile')
            self.min_denominator.setEnabled(not is_pct)
            self.denominator_percentile.setEnabled(is_pct)
        self.denominator_guard_mode.currentIndexChanged.connect(_toggle_den_guard_controls)
        _toggle_den_guard_controls()
        guard_box.setContentLayout(gl)
        root.addWidget(guard_box)

        clip_box = CollapsibleBox('Extreme Value Clipping to Output Ratio Maps (optional)', checked=False)
        cl = QGridLayout()
        self.clip_ratio_extremes = QCheckBox('Clip / winsorise extreme ratio values before writing outputs')
        self.clip_ratio_extremes.setChecked(False)
        self.clip_ratio_low = QDoubleSpinBox(); self.clip_ratio_low.setRange(0, 49); self.clip_ratio_low.setDecimals(2); self.clip_ratio_low.setValue(1.0)
        self.clip_ratio_high = QDoubleSpinBox(); self.clip_ratio_high.setRange(51, 100); self.clip_ratio_high.setDecimals(2); self.clip_ratio_high.setValue(99.0)
        cl.addWidget(self.clip_ratio_extremes, 0, 0, 1, 2)
        cl.addWidget(QLabel('Low percentile cap:'), 1, 0); cl.addWidget(self.clip_ratio_low, 1, 1)
        cl.addWidget(QLabel('High percentile cap:'), 2, 0); cl.addWidget(self.clip_ratio_high, 2, 1)
        clip_note = QLabel('This is winsorising: cells below/above the selected percentiles are capped to those percentile values, not removed. Leave off for raw ratio outputs and use Stage 2 for interpretive clipping/threshold review.')
        clip_note.setWordWrap(True); cl.addWidget(clip_note, 3, 0, 1, 2)
        clip_box.setContentLayout(cl)
        root.addWidget(clip_box)

        opts = CollapsibleBox('Display / Loading Options', checked=True)
        ol = QGridLayout()
        self.style_low = QDoubleSpinBox(); self.style_low.setRange(0, 49); self.style_low.setDecimals(1); self.style_low.setValue(1.0)
        self.style_high = QDoubleSpinBox(); self.style_high.setRange(51, 100); self.style_high.setDecimals(1); self.style_high.setValue(99.0)
        self.load_ratios = QCheckBox('Load and style ratio outputs in QGIS using blue-white-red quantile pseudocolour'); self.load_ratios.setChecked(True)
        ol.addWidget(QLabel('Display lower quantile %:'), 0, 0); ol.addWidget(self.style_low, 0, 1)
        ol.addWidget(QLabel('Display upper quantile %:'), 1, 0); ol.addWidget(self.style_high, 1, 1)
        ol.addWidget(self.load_ratios, 2, 0, 1, 2)
        style_note = QLabel('This affects display only. Output raster values are unchanged unless Optional Extreme Value Clipping is enabled above.')
        style_note.setWordWrap(True); ol.addWidget(style_note, 3, 0, 1, 2)
        opts.setContentLayout(ol)
        root.addWidget(opts)

        out = CollapsibleBox('Run Ratio Map Generation', checked=True)
        rl = QVBoxLayout()

        perf_box = CollapsibleBox('Performance / Large Raster Processing', checked=False)
        pl = QGridLayout()
        self.use_chunked_ratio = QCheckBox('Use chunked division for large rasters')
        self.use_chunked_ratio.setChecked(False)
        self.use_chunked_ratio.setToolTip('Writes ratio maps block-by-block to reduce RAM usage. Best when optional ratio clipping is OFF.')
        self.chunked_block_size = QSpinBox(); self.chunked_block_size.setRange(128, 4096); self.chunked_block_size.setSingleStep(128); self.chunked_block_size.setValue(512)
        pl.addWidget(self.use_chunked_ratio, 0, 0, 1, 2)
        pl.addWidget(QLabel('Block size, pixels:'), 1, 0); pl.addWidget(self.chunked_block_size, 1, 1)
        perf_note = QLabel('Chunked mode uses the same safe denominator logic but writes each ratio in blocks. If clipping/winsorising is enabled, the plugin will use the standard in-memory path because clipping requires full raster percentiles.')
        perf_note.setWordWrap(True); pl.addWidget(perf_note, 2, 0, 1, 2)
        perf_box.setContentLayout(pl)
        rl.addWidget(perf_box)

        self.ratio_log = QTextEdit(); self.ratio_log.setReadOnly(True); self.ratio_log.setMaximumHeight(140)
        self.ratio_progress = QProgressBar(); self.ratio_progress.setRange(0, 100); self.ratio_progress.setValue(0); self.ratio_progress.setFormat('Idle')
        run = QPushButton('Generate Ratio Maps: U/Th, U/K, K/U and K/Th'); run.setMinimumHeight(34); run.clicked.connect(self.run_ratio_generation)
        rl.addWidget(run); rl.addWidget(self.ratio_progress); rl.addWidget(self.ratio_log)
        out.setContentLayout(rl)
        root.addWidget(out)
        root.addWidget(self._guide_button('Ratio Map Generation - User Guide and Theory', RATIO_GUIDE_SECTIONS))
        root.addStretch(1)
        self._auto_load_filtered_rasters_from_folder(set_combos=True, silent=True)
        return w

    def _apply_icons_to_child_tabs(self, parent, mapping):
        """Best-effort icon assignment for embedded Stage 2/3 dialogs."""
        try:
            tabs = parent.findChildren(QTabWidget)
        except Exception:
            return
        for tw in tabs:
            for i in range(tw.count()):
                label = tw.tabText(i) or ''
                icon_name = None
                for key, candidate in mapping.items():
                    if key.lower() in label.lower():
                        icon_name = candidate
                        break
                if icon_name:
                    tw.setTabIcon(i, self._icon(icon_name))

    def _normalise_grid_label_colons(self, layout):
        """Add colons to labels that sit immediately left of input widgets."""
        try:
            for i in range(layout.count()):
                item = layout.itemAt(i)
                child_layout = item.layout()
                if child_layout is not None:
                    self._normalise_grid_label_colons(child_layout)
                    continue
                w = item.widget()
                if isinstance(w, QGridLayout):
                    self._normalise_grid_label_colons(w)
        except Exception:
            pass

    def _make_stage2_analysis_tab(self):
        w = QWidget(); root = QVBoxLayout(w)
        h = QHBoxLayout()
        heading = QLabel('Stage 2')
        heading.setStyleSheet('font-weight:bold; font-size:14px;')
        heading.setWordWrap(True)
        h.addWidget(heading, 1)
        h.addWidget(self._guide_button('Stage 2 - User Guide and Theory', STAGE2_GUIDE_SECTIONS))
        root.addLayout(h)
        self.stage3_widget = ThresholdReviewDialog(w, processing_folder=self._active_processing_folder())
        self.stage3_widget.setWindowFlags(Qt.Widget)
        self.stage3_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.stage3_widget, 1)
        try:
            self._apply_icons_to_child_tabs(self.stage3_widget, {'Threshold': 'threshold', 'Anomaly': 'polygon', 'Extract': 'polygon', 'Review': 'qc', 'Table': 'table'})
        except Exception:
            pass
        return w

    def _make_stage3_combined_tab(self):
        w = QWidget(); root = QVBoxLayout(w)
        h = QHBoxLayout()
        heading = QLabel('Stage 3')
        heading.setStyleSheet('font-weight:bold; font-size:14px;')
        heading.setWordWrap(True)
        h.addWidget(heading, 1)
        h.addWidget(self._guide_button('Stage 3 - User Guide and Theory', STAGE3_GUIDE_SECTIONS))
        root.addLayout(h)
        self.stage4_widget = CombinedAnalysisDialog(w, processing_folder=self._active_processing_folder(), include_crossplot=False)
        self.stage4_widget.setWindowFlags(Qt.Widget)
        self.stage4_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.stage4_widget, 1)
        try:
            self._apply_icons_to_child_tabs(self.stage4_widget, {'Normal': 'normalise', 'Cross': 'crossplot', 'Combination': 'combine', 'Combined': 'combine', 'Threshold': 'threshold', 'Anomaly': 'polygon', 'Extract': 'polygon'})
        except Exception:
            pass
        return w

    # ------------------------------------------------------------------ embedded stage 1A
    def _append_pre_log(self, text):
        self.pre_log.append(text)

    def run_preprocessing(self):
        self._ensure_work_session()
        folder = self._active_processing_folder()
        if not folder:
            QMessageBox.warning(self, 'Processing folder required', 'Set the Processing folder on the Radiometric Analysis tab first.')
            return
        u_path = self._selected_raster_source(self.u_combo, self.u_file)
        k_path = self._selected_raster_source(self.k_combo, self.k_file)
        th_path = self._selected_raster_source(self.th_combo, self.th_file)
        if not u_path or not k_path or not th_path:
            QMessageBox.warning(self, 'Missing rasters', 'Select Uranium, Potassium and Thorium rasters from loaded QGIS layers. Use Auto-load to load rasters from the project input folder.')
            return
        try:
            u_path = self._copy_input_raster_to_project(u_path, 'U')
            k_path = self._copy_input_raster_to_project(k_path, 'K')
            th_path = self._copy_input_raster_to_project(th_path, 'TH')
            # As soon as pre-processing starts, make the copied source rasters
            # visible under Radiometric Processing > 01_Input_Data. Previously
            # these were only appearing later when Ratio Map Generation refreshed
            # project inputs, which made the workflow look out of sequence.
            self._add_raster_to_project_if_needed(u_path, 'Input_Uranium')
            self._add_raster_to_project_if_needed(k_path, 'Input_Potassium')
            self._add_raster_to_project_if_needed(th_path, 'Input_Thorium')
            self._refresh_raster_combos()
        except Exception as e:
            QMessageBox.warning(self, 'Input raster copy warning', 'Could not copy/load one or more input rasters into 01_Input_Data. Processing will continue from the original paths.\n\n' + str(e))
        mask_path = self.mask_combo.currentData() or ''
        size = [3, 5, 7, 9, 11, 13, 15][self.window_combo.currentIndex()]
        filt_idx = self.filter_combo.currentIndex()
        method_name = ['MeanF', 'MedianF', 'GaussianF'][filt_idx]
        suffix = f'{method_name}_{size}x{size}'
        pre = ensure_dir(os.path.join(folder, '02_Preprocessed'))
        filtered_dir = pre
        use_extra = self.use_extra_nodata.isChecked()
        extra = float(self.extra_nodata.value())
        # Only exact additional NoData value is handled here. The separate <=0
        # radiometric invalid checkbox below is the only <=0 rule in the UI.
        nodata_leq = False
        self.pre_log.clear()
        self._set_progress('pre_progress', 0, 'Starting...')
        self._append_pre_log('Starting pre-processing / data conditioning...')
        self._append_pre_log('Input rasters are copied to 01_Input_Data where possible.')
        if mask_path:
            self._append_pre_log('Mask polygon selected: cells outside polygon will be NoData.')
        else:
            self._append_pre_log('No mask polygon selected: Uranium raster extent/reference grid is used.')
        outputs = {}
        feedback = QgsProcessingFeedback()
        try:
            stage_items = [('U', u_path), ('K', k_path), ('TH', th_path)]
            for idx, (key, src) in enumerate(stage_items):
                base = prealg._base_name(src)
                aligned = os.path.join(pre, f'{base}_aligned.tif')
                conditioned = os.path.join(pre, f'{base}_conditioned.tif')
                filtered = os.path.join(filtered_dir, f'{base}_{suffix}.tif')
                self._set_progress('pre_progress', 5 + idx * 30, f'Aligning {key}...')
                self._append_pre_log(f'Aligning {base}...')
                prealg._warp_to_reference(src, aligned, u_path, mask_path or None)
                arr, profile = prealg._read_raster(aligned)
                arr = prealg._clean_array(arr, use_extra, extra, nodata_leq)
                # Radiometric K, U and Th grids should not contain negative or zero physical values.
                # If enabled, mask these before filtering so the smoother does not smear invalid
                # negative/zero cells into surrounding valid data.
                if hasattr(self, 'invalid_le_zero') and self.invalid_le_zero.isChecked():
                    invalid0 = np.isfinite(arr) & (arr <= 0.0)
                    if np.count_nonzero(invalid0):
                        self._append_pre_log(f'{base}: set {np.count_nonzero(invalid0):,} cells <= 0 to NoData before filtering.')
                    arr[invalid0] = np.nan
                prealg._write_raster(conditioned, arr, profile, gdal.GDT_Float32)
                self._set_progress('pre_progress', 15 + idx * 30, f'Filtering {key}...')
                self._append_pre_log(f'Filtering {base} with {method_name} {size}x{size}...')
                if filt_idx == 0:
                    out_arr = prealg._focal_mean(arr, size)
                elif filt_idx == 1:
                    out_arr = prealg._focal_median_chunked(arr, size, feedback=feedback)
                else:
                    out_arr = self._gaussian_filter_nan(arr, size, float(self.gauss_sigma.value()))
                prealg._write_raster(filtered, out_arr, profile, gdal.GDT_Float32)
                outputs[key] = filtered
                if self.load_filtered.isChecked():
                    self._load_raster_grouped(filtered, f'{base}_{suffix}')
            self.state.filtered_rasters = outputs
            self._auto_load_filtered_rasters_from_folder(set_combos=True, silent=True)
            self._update_ratio_input_labels()
            self._set_progress('pre_progress', 100, 'Pre-processing complete')
            self._append_pre_log('Pre-processing complete.')
            append_session_log(folder, 'Stage 1 - Pre-processing / Data Conditioning', inputs={'U': u_path, 'K': k_path, 'Th': th_path, 'Mask': mask_path or ''}, outputs=outputs, parameters={'Filter': method_name, 'Window': f'{size}x{size}', 'Invalid_LE_Zero': bool(getattr(self, 'invalid_le_zero', None) and self.invalid_le_zero.isChecked())})
            QMessageBox.information(self, 'Pre-processing complete', 'Filtered rasters were created and passed to the Ratio Map Generation tab.')
        except Exception as e:
            self._set_progress('pre_progress', 0, 'Pre-processing failed')
            QMessageBox.critical(self, 'Pre-processing failed', str(e))
            self._append_pre_log('ERROR: ' + str(e))

    def _gaussian_filter_nan(self, arr, size, sigma):
        pad = size // 2
        ax = np.arange(-pad, pad + 1, dtype=np.float64)
        xx, yy = np.meshgrid(ax, ax)
        kernel = np.exp(-(xx * xx + yy * yy) / (2.0 * sigma * sigma))
        kernel = kernel / np.nansum(kernel)
        padded = np.pad(arr, pad, mode='constant', constant_values=np.nan)
        total = np.zeros(arr.shape, dtype=np.float64)
        weight = np.zeros(arr.shape, dtype=np.float64)
        for i in range(size):
            for j in range(size):
                w = padded[i:i + arr.shape[0], j:j + arr.shape[1]]
                valid = np.isfinite(w)
                kw = kernel[i, j]
                total[valid] += w[valid] * kw
                weight[valid] += kw
        out = total / weight
        out[weight == 0] = np.nan
        return out

    # ------------------------------------------------------------------ embedded stage 1B
    def _append_ratio_log(self, text):
        self.ratio_log.append(text)

    def _detect_filtered_inputs(self, update_labels=False, show_message=False, force_folder=False):
        folder = self._active_processing_folder()
        if not folder:
            if update_labels:
                self._update_ratio_input_labels()
            return self.state.filtered_rasters

        # First honour explicit outputs from the current pre-processing run unless
        # a folder refresh is requested.
        existing = self.state.filtered_rasters or {}
        if (not force_folder) and all(existing.get(k) and os.path.exists(existing.get(k)) for k in ('U', 'K', 'TH')):
            if update_labels:
                self._update_ratio_input_labels()
            return self.state.filtered_rasters

        # Fallback auto-detection is deliberately restricted to preprocessed
        # folder. Older versions searched the whole processing folder, which could
        # accidentally pick ratio rasters such as K_Th_ratio.tif as a Thorium input.
        found = {'U': '', 'K': '', 'TH': ''}
        search_roots = []
        for sub in ('02_Preprocessed',):
            p = os.path.join(folder, sub)
            if os.path.isdir(p):
                search_roots.append(p)
        candidates = []
        for root0 in search_roots:
            for root, _, files in os.walk(root0):
                for f in files:
                    low = f.lower()
                    if not low.endswith(('.tif', '.tiff')):
                        continue
                    if any(bad in low for bad in ('ratio', 'rrai', 'combined', 'norm', 'class', 'mask')):
                        continue
                    p = os.path.join(root, f)
                    score = os.path.getmtime(p)
                    candidates.append((score, low, p))

        def pick(role):
            if role == 'U':
                tests = [lambda l: 'uranium' in l, lambda l: l.startswith('u_') or '_u_' in l, lambda l: 'u_meanf' in l or 'u_medianf' in l or 'u_gaussianf' in l or 'u_smooth' in l]
            elif role == 'K':
                tests = [lambda l: 'potassium' in l, lambda l: l.startswith('k_') or '_k_' in l, lambda l: 'k_meanf' in l or 'k_medianf' in l or 'k_gaussianf' in l or 'k_smooth' in l]
            else:
                tests = [lambda l: 'thorium' in l, lambda l: l.startswith('th_') or '_th_' in l, lambda l: 'th_meanf' in l or 'th_medianf' in l or 'th_gaussianf' in l or 'th_smooth' in l]
            matches = []
            for mtime, low, path in candidates:
                if any(t(low) for t in tests):
                    matches.append((mtime, path))
            if not matches:
                return ''
            matches.sort(reverse=True)
            return matches[0][1]

        found['U'] = pick('U')
        found['K'] = pick('K')
        found['TH'] = pick('TH')
        for k, v in found.items():
            if v:
                self.state.filtered_rasters[k] = v
        if update_labels:
            self._update_ratio_input_labels()
        if show_message:
            msg = 'Detection complete. Check the listed U/K/Th filtered raster paths before generating ratios.'
            if not all(found.values()):
                msg += '\n\nSome inputs were not detected. Run pre-processing first.'
            QMessageBox.information(self, 'Filtered raster detection', msg)
        return self.state.filtered_rasters

    def _update_ratio_input_labels(self):
        # v3.15: ratio inputs are dropdowns, not path labels. Select detected
        # project-folder rasters in the dropdowns when available.
        if hasattr(self, 'ratio_u_combo'):
            self._refresh_ratio_raster_combos()
            self._set_combo_or_file(self.ratio_u_combo, None, self.state.filtered_rasters.get('U') or '')
            self._set_combo_or_file(self.ratio_k_combo, None, self.state.filtered_rasters.get('K') or '')
            self._set_combo_or_file(self.ratio_th_combo, None, self.state.filtered_rasters.get('TH') or '')

    def run_ratio_generation(self):
        self._ensure_work_session()
        folder = self._active_processing_folder()
        if not folder:
            QMessageBox.warning(self, 'Processing folder required', 'Set the Processing folder on the Radiometric Analysis tab first.')
            return
        self._detect_filtered_inputs(update_labels=True)
        u_path = self._selected_ratio_source('U')
        k_path = self._selected_ratio_source('K')
        th_path = self._selected_ratio_source('TH')
        if not u_path or not k_path or not th_path:
            QMessageBox.warning(self, 'Filtered rasters not found', 'Run Pre-Processing first or click Load / Refresh Filtered Rasters from Stage 1.')
            return
        if self.style_low.value() >= self.style_high.value():
            QMessageBox.warning(self, 'Display quantiles', 'Display lower quantile must be less than display upper quantile.')
            return
        self.ratio_log.clear()
        self._set_progress('ratio_progress', 0, 'Starting...')
        self._append_ratio_log('Reading filtered U/K/Th rasters...')
        try:
            self._set_progress('ratio_progress', 10, 'Reading rasters...')
            U, profile = read_raster(u_path)
            K, _ = read_raster(k_path)
            Th, _ = read_raster(th_path)
            if U.shape != K.shape or U.shape != Th.shape:
                raise ValueError('Input rasters must have the same dimensions.')
            ratios_dir = ensure_dir(os.path.join(folder, '03_Ratio_Maps'))
            logs = ensure_dir(os.path.join(folder, '00_Stats_Logs'))
            self._append_ratio_log('Using the same GDAL/NumPy ratio calculation as the standalone Processing script.')
            self._append_ratio_log('U input: ' + str(u_path))
            self._append_ratio_log('K input: ' + str(k_path))
            self._append_ratio_log('Th input: ' + str(th_path))
            self._set_progress('ratio_progress', 30, 'Calculating ratios...')
            guard_mode = self.denominator_guard_mode.currentText() if hasattr(self, 'denominator_guard_mode') else 'Absolute value'
            guard_is_percentile = str(guard_mode).startswith('Percentile')
            abs_min_den = float(self.min_denominator.value()) if hasattr(self, 'min_denominator') else 0.0
            den_pct = float(self.denominator_percentile.value()) if hasattr(self, 'denominator_percentile') else 1.0
            def _positive_den_percentile(arr, pct, label):
                vals = arr[np.isfinite(arr) & (arr > 0)]
                if vals.size == 0:
                    self._append_ratio_log(f'WARNING: no positive denominator values found for {label}; using infinite guard so output is NoData.')
                    return float('inf')
                return max(0.0, float(np.nanpercentile(vals, pct)))
            if guard_is_percentile:
                guard_U = _positive_den_percentile(U, den_pct, 'U')
                guard_K = _positive_den_percentile(K, den_pct, 'K')
                guard_Th = _positive_den_percentile(Th, den_pct, 'Th')
                denominator_guards = {
                    'U_Th_ratio': guard_Th,
                    'U_K_ratio': guard_K,
                    'K_U_ratio': guard_U,
                    'K_Th_ratio': guard_Th,
                }
                self._append_ratio_log(f'Denominator guard mode: percentile P{den_pct:g} of positive denominator values.')
                self._append_ratio_log(f'Computed denominator floors: U={guard_U:g}, K={guard_K:g}, Th={guard_Th:g}')
            else:
                denominator_guards = {
                    'U_Th_ratio': abs_min_den,
                    'U_K_ratio': abs_min_den,
                    'K_U_ratio': abs_min_den,
                    'K_Th_ratio': abs_min_den,
                }
                self._append_ratio_log(f'Denominator guard mode: absolute value <= {abs_min_den:g} becomes NoData.')
            use_chunked = bool(getattr(self, 'use_chunked_ratio', None) and self.use_chunked_ratio.isChecked())
            clipping_on = bool(getattr(self, 'clip_ratio_extremes', None) and self.clip_ratio_extremes.isChecked())
            if use_chunked and not clipping_on:
                self._append_ratio_log('Using chunked file-based safe division for memory-safe ratio generation.')
                ratio_inputs = {
                    'U_Th_ratio': (u_path, th_path),
                    'U_K_ratio': (u_path, k_path),
                    'K_U_ratio': (k_path, u_path),
                    'K_Th_ratio': (k_path, th_path),
                }
                stats = {}; paths = {}
                block_size = int(self.chunked_block_size.value()) if hasattr(self, 'chunked_block_size') else 512
                for out_idx, (name, (num_path, den_path)) in enumerate(ratio_inputs.items()):
                    den_guard = float(denominator_guards.get(name, abs_min_den))
                    self._set_progress('ratio_progress', 30 + int((out_idx / max(1, len(ratio_inputs))) * 55), f'Writing {name} in chunks...')
                    self._append_ratio_log(f'Writing {name} in chunks; denominator guard > {den_guard:g}; block={block_size}px')
                    p = make_available_path(os.path.join(ratios_dir, f'{name}.tif'))
                    safe_divide_chunked(num_path, den_path, p, min_denominator=den_guard, block_size=block_size)
                    arr, _ = read_raster(p)
                    stats[name] = array_stats(arr, [1, 5, 10, 15, 25, 50, 75, 85, 90, 95, 99])
                    paths[name] = p
                    if self.load_ratios.isChecked():
                        load_raster_pseudocolor_quantile(p, name, low_q=float(self.style_low.value()), high_q=float(self.style_high.value()))
                settings = {'Input_U': u_path, 'Input_K': k_path, 'Input_Th': th_path, 'Display_Low_Q': self.style_low.value(), 'Display_High_Q': self.style_high.value(), 'Denominator_guard_mode': guard_mode, 'Minimum_denominator_guard': abs_min_den, 'Denominator_guard_percentile': den_pct if guard_is_percentile else '', 'Computed_U_guard': denominator_guards.get('K_U_ratio'), 'Computed_K_guard': denominator_guards.get('U_K_ratio'), 'Computed_Th_guard': denominator_guards.get('U_Th_ratio'), 'Ratio_Extreme_Clipping': False, 'Chunked_processing': True, 'Chunk_block_size': block_size}
                write_stats_csv(os.path.join(logs, 'ratio_map_statistics.csv'), stats, settings)
                self.state.ratio_rasters = paths
                self._sync_folder_state()
                self._set_progress('ratio_progress', 100, 'Chunked ratio map generation complete')
                self._append_ratio_log('Chunked ratio map generation complete.')
                append_session_log(folder, 'Stage 1 - Ratio Map Generation', inputs={'U': u_path, 'K': k_path, 'Th': th_path}, outputs=paths, parameters=settings)
                QMessageBox.information(self, 'Ratio maps complete', 'U/Th, U/K, K/U and K/Th ratio maps were generated using chunked safe division.')
                return
            elif use_chunked and clipping_on:
                self._append_ratio_log('Chunked mode requested, but ratio clipping is enabled; using standard in-memory path so percentiles can be calculated.')
            raw_maps = {}
            denominator_masks = {}
            raw_maps['U_Th_ratio'], denominator_masks['U_Th_ratio'] = safe_divide(U, Th, min_denominator=denominator_guards['U_Th_ratio'], return_mask=True)
            raw_maps['U_K_ratio'], denominator_masks['U_K_ratio'] = safe_divide(U, K, min_denominator=denominator_guards['U_K_ratio'], return_mask=True)
            raw_maps['K_U_ratio'], denominator_masks['K_U_ratio'] = safe_divide(K, U, min_denominator=denominator_guards['K_U_ratio'], return_mask=True)
            raw_maps['K_Th_ratio'], denominator_masks['K_Th_ratio'] = safe_divide(K, Th, min_denominator=denominator_guards['K_Th_ratio'], return_mask=True)
            total_cells = float(U.size)
            for rname, mask in denominator_masks.items():
                pct = 100.0 * float(np.count_nonzero(mask)) / total_cells if total_cells else 0.0
                self._append_ratio_log(f'{rname}: masked {pct:.3f}% cells with denominator <= {float(denominator_guards.get(rname, 0.0)):g}')
            if hasattr(self, 'clip_ratio_extremes') and self.clip_ratio_extremes.isChecked():
                low_q = float(self.clip_ratio_low.value())
                high_q = float(self.clip_ratio_high.value())
                if low_q >= high_q:
                    raise ValueError('Ratio clipping low percentile must be less than high percentile.')
                self._append_ratio_log(f'Clipping/winsorising ratio outputs to P{low_q:g}–P{high_q:g}.')
                for rname, arr in list(raw_maps.items()):
                    valid = arr[np.isfinite(arr)]
                    if valid.size:
                        lo = float(np.nanpercentile(valid, low_q))
                        hi = float(np.nanpercentile(valid, high_q))
                        clipped = np.array(arr, copy=True)
                        low_count = int(np.count_nonzero(np.isfinite(clipped) & (clipped < lo)))
                        high_count = int(np.count_nonzero(np.isfinite(clipped) & (clipped > hi)))
                        clipped[np.isfinite(clipped) & (clipped < lo)] = lo
                        clipped[np.isfinite(clipped) & (clipped > hi)] = hi
                        raw_maps[rname] = clipped
                        self._append_ratio_log(f'{rname}: capped {low_count:,} low cells and {high_count:,} high cells.')
            stats = {}; paths = {}
            ratio_items = list(raw_maps.items())
            for out_idx, (name, arr) in enumerate(ratio_items):
                self._set_progress('ratio_progress', 55 + int((out_idx / max(1, len(ratio_items))) * 35), f'Writing {name}...')
                self._append_ratio_log(f'Writing {name}...')
                p = make_available_path(os.path.join(ratios_dir, f'{name}.tif'))
                write_raster(p, arr, profile, gdal.GDT_Float32)
                stats[name] = array_stats(arr, [1, 5, 10, 15, 25, 50, 75, 85, 90, 95, 99])
                paths[name] = p
                if self.load_ratios.isChecked():
                    lyr = load_raster_pseudocolor_quantile(p, name, low_q=float(self.style_low.value()), high_q=float(self.style_high.value()))
                    # load_raster_pseudocolor_quantile handles grouped insertion after v3.6 utils patch
            settings = {'Input_U': u_path, 'Input_K': k_path, 'Input_Th': th_path, 'Display_Low_Q': self.style_low.value(), 'Display_High_Q': self.style_high.value(), 'Denominator_guard_mode': guard_mode, 'Minimum_denominator_guard': abs_min_den, 'Denominator_guard_percentile': den_pct if guard_is_percentile else '', 'Computed_U_guard': denominator_guards.get('K_U_ratio'), 'Computed_K_guard': denominator_guards.get('U_K_ratio'), 'Computed_Th_guard': denominator_guards.get('U_Th_ratio'), 'Ratio_Extreme_Clipping': bool(getattr(self, 'clip_ratio_extremes', None) and self.clip_ratio_extremes.isChecked()), 'Ratio_Clip_Low_Q': float(self.clip_ratio_low.value()) if hasattr(self, 'clip_ratio_low') else '', 'Ratio_Clip_High_Q': float(self.clip_ratio_high.value()) if hasattr(self, 'clip_ratio_high') else '', 'Chunked_processing': False}
            write_stats_csv(os.path.join(logs, 'ratio_map_statistics.csv'), stats, settings)
            self.state.ratio_rasters = paths
            self._sync_folder_state()
            self._set_progress('ratio_progress', 100, 'Ratio map generation complete')
            self._append_ratio_log('Ratio map generation complete.')
            append_session_log(folder, 'Stage 1 - Ratio Map Generation', inputs={'U': u_path, 'K': k_path, 'Th': th_path}, outputs=paths, parameters=settings)
            QMessageBox.information(self, 'Ratio maps complete', 'U/Th, U/K, K/U and K/Th ratio maps were generated. U/K supports the uranium-enrichment index; K/U supports the hybrid alteration/source-side index.')
        except Exception as e:
            self._set_progress('ratio_progress', 0, 'Ratio map generation failed')
            QMessageBox.critical(self, 'Ratio map generation failed', str(e))
            self._append_ratio_log('ERROR: ' + str(e))

    def _detect_ratio_outputs(self, update_state=True):
        folder = self._active_processing_folder()
        if not folder: return {}
        rdir = os.path.join(folder, '03_Ratio_Maps')
        found = {}
        for name in ('U_Th_ratio', 'U_K_ratio', 'K_U_ratio', 'K_Th_ratio'):
            p = os.path.join(rdir, f'{name}.tif')
            if os.path.exists(p): found[name] = p
        if update_state and found:
            self.state.ratio_rasters.update(found)
        return found

    # ------------------------------------------------------------------ grouped loading
    def _layer_group_names(self, path):
        parent = os.path.basename(os.path.dirname(path))
        grand = os.path.basename(os.path.dirname(os.path.dirname(path)))
        if parent and (parent[:2].isdigit() or parent.startswith('0')):
            return grand or 'Radiometric Analysis', parent
        return parent or 'Radiometric Analysis', ''

    def _load_raster_grouped(self, path, name):
        # Legacy wrapper retained for older call sites. Use the canonical
        # project-folder mirroring loader so every layer is placed under:
        # Radiometric Processing > <project subfolder> > <optional subgroup>.
        if not os.path.exists(path): return None
        layer = QgsRasterLayer(path, name)
        if not layer.isValid(): return None
        return self._add_layer_to_group(layer, path)

    # ------------------------------------------------------------------ processing launching retained for toolbox fallback
    def _params_for_step(self, step_id):
        folder = self._active_processing_folder()
        if not folder: return {}
        if step_id == 'step_01_preprocessing': return {'OUTPUT_FOLDER': folder}
        return {'PROCESSING_FOLDER': folder, 'OUTPUT_FOLDER': folder}

    def open_processing_step(self, step_id):
        alg_id = resolve_algorithm_id_by_step(step_id)
        if not alg_id:
            QMessageBox.warning(self, 'Algorithm not found', 'Could not find algorithm for ' + step_id)
            return
        try:
            processing.execAlgorithmDialog(alg_id, self._params_for_step(step_id))
        except TypeError:
            processing.execAlgorithmDialog(alg_id)
        except Exception as e:
            QMessageBox.critical(self, 'Could not open algorithm', str(e))
