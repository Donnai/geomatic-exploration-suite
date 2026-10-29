# -*- coding: utf-8 -*-
"""Stage 3 combined radiometric index analysis dock widget.

This mirrors the Stage 2 ratio-map analysis structure, but operates on a single
combined radiometric index after generating normalised ratio maps.
"""
import os, csv, math, glob
from datetime import datetime
import numpy as np
import processing
from osgeo import gdal, ogr

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QDoubleSpinBox, QFileDialog, QCheckBox, QGroupBox,
    QMessageBox, QPlainTextEdit, QTabWidget, QWidget, QSpinBox, QLineEdit, QSlider, QSizePolicy, QProgressBar, QApplication, QTableWidget, QTableWidgetItem, QHeaderView
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsRasterLayer

from .algs.utils import (
    read_raster, write_raster, ensure_dir, make_available_path, normalise,
    load_raster_pseudocolor_quantile, load_layer, polygonize_class_raster, polygonize_binary_mask_raster,
    write_stats_csv, array_stats, append_session_log
)
from .threshold_review_dialog import _square_dilate, _square_erode, _focal_fraction, _valid_values
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
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    HAS_MPL = True
except Exception:
    HAS_MPL = False
    FigureCanvas = None
    Figure = None


class CombinedAnalysisDialog(QDialog):
    """Stage 3: normalise ratios, build combined index, threshold one raster, extract polygons."""
    def __init__(self, parent=None, processing_folder='', crossplot_only=False, include_crossplot=True):
        super().__init__(parent)
        self.setWindowTitle('Stage 3 - Combined Analysis and Anomaly Extraction')
        self.processing_folder = processing_folder or ''
        self.crossplot_only = bool(crossplot_only)
        self.include_crossplot = bool(include_crossplot)
        self._combined = None
        self._profile = None
        self._combined_path = ''
        self._analysis = None
        self._clip_report = (0.0, 0.0)
        self._build_ui()
        self._refresh_layers()
        self._auto_from_processing_folder()


    def _default_run_id(self):
        """Default QC run identifier used in versioned output file names."""
        return 'QC_' + datetime.now().strftime('%Y%m%d_%H%M')

    def _safe_run_id(self):
        """Return a filesystem-safe run id, using run_id_edit if present."""
        txt = ''
        try:
            txt = self.run_id_edit.text().strip()
        except Exception:
            txt = getattr(self, '_active_run_id', '') or self._default_run_id()
        if not txt:
            txt = self._default_run_id()
        safe = ''.join(ch if ch.isalnum() or ch in ('_', '-') else '_' for ch in txt)
        self._active_run_id = safe
        try:
            self.run_id_edit.setText(safe)
        except Exception:
            pass
        return safe

    def _build_ui(self):
        root = QVBoxLayout(self)
        if getattr(self, 'crossplot_only', False):
            title = QLabel('Crossplot Analysis')
            title.setStyleSheet('font-weight:bold; font-size:15px;')
            root.addWidget(title)
        else:
            intro = QLabel('Generate complementary combined radiometric indices: RRAI-U and HRAI-KU from normalised ratio rasters, and HRI from z-log ratio rasters using scalar coefficients.')
            intro.setWordWrap(True); root.addWidget(intro)

        self.folder_label = QLabel(self.processing_folder or '(not set)')
        self.folder_label.setVisible(False)

        if getattr(self, 'crossplot_only', False):
            root.addWidget(self._make_crossplot_tab(), 1)
            return

        self.tabs = QTabWidget()
        self.tabs.addTab(self._make_normalisation_tab(), 'Normalisation')
        if getattr(self, 'include_crossplot', True):
            self.tabs.addTab(self._make_crossplot_tab(), 'Crossplot Analysis')
        self.tabs.addTab(self._make_pca_tab(), 'PCA / Multivariate Analysis')
        self.tabs.addTab(self._make_combinations_tab(), 'Combinations')
        self.tabs.addTab(self._make_threshold_tab(), 'Threshold Analysis')
        self.tabs.addTab(self._make_extraction_tab(), 'Anomaly Extraction')
        root.addWidget(self.tabs, 1)

    # ------------------------- tabs
    def _make_normalisation_tab(self):
        """Stage 3 tab 1: generate derived ratio products used by Stage 3."""
        w=QWidget(); root=QVBoxLayout(w)
        gb=CollapsibleBox('Input Radiometric Ratio Rasters', checked=True)
        grid=QGridLayout()
        self.cmb_uth=QComboBox(); self.cmb_uk=QComboBox(); self.cmb_ku=QComboBox(); self.cmb_kth=QComboBox()
        grid.addWidget(QLabel('U/Th ratio:'),0,0); grid.addWidget(self.cmb_uth,0,1)
        grid.addWidget(QLabel('U/K ratio - uranium enrichment:'),1,0); grid.addWidget(self.cmb_uk,1,1)
        grid.addWidget(QLabel('K/U ratio - hybrid alteration/source-side:'),2,0); grid.addWidget(self.cmb_ku,2,1)
        grid.addWidget(QLabel('K/Th ratio:'),3,0); grid.addWidget(self.cmb_kth,3,1)
        refresh=QPushButton('Refresh raster list'); refresh.clicked.connect(self._refresh_layers); grid.addWidget(refresh,0,2)
        auto=QPushButton('Auto-detect from folder'); auto.clicked.connect(self._auto_from_processing_folder); grid.addWidget(auto,1,2)
        note_dual=QLabel('Select the four ratio rasters used by the Stage 3 derived products.')
        note_dual.setWordWrap(True); grid.addWidget(note_dual,4,0,1,3)
        gb.setContentLayout(grid)
        root.addWidget(gb)

        prodgb=CollapsibleBox('Generate Derived Ratio Products', checked=True)
        pgrid=QGridLayout()
        self.gen_norm_maps=QCheckBox('Generate Normalised Ratio Maps')
        self.gen_log_maps=QCheckBox('Generate Log Ratio Maps')
        self.gen_zlog_maps=QCheckBox('Generate ZLog Ratio Maps')
        self.gen_norm_maps.setChecked(True)
        self.gen_log_maps.setChecked(False)
        self.gen_zlog_maps.setChecked(True)
        pgrid.addWidget(self.gen_norm_maps,0,0,1,2)
        pgrid.addWidget(self.gen_log_maps,1,0,1,2)
        pgrid.addWidget(self.gen_zlog_maps,2,0,1,2)
        pnote=QLabel('Choose which derived ratio products to create for the combined-index workflow.')
        pnote.setWordWrap(True); pgrid.addWidget(pnote,3,0,1,2)
        prodgb.setContentLayout(pgrid)
        root.addWidget(prodgb)

        normgb=CollapsibleBox('Normalisation Method for Normalised Ratio Maps', checked=True)
        ngrid=QGridLayout()
        self.norm_method=QComboBox(); self.norm_method.addItems(['Min-Max','Min-Max extremes clipped','Percentile / quantile rank','Z-score','Robust Z-score'])
        self.norm_low_pct=self._percent_spin(1.0); self.norm_high_pct=self._percent_spin(99.0)
        ngrid.addWidget(QLabel('Normalisation method:'),0,0); ngrid.addWidget(self.norm_method,0,1,1,3)
        ngrid.addWidget(QLabel('Low clip percentile:'),1,0); ngrid.addWidget(self.norm_low_pct,1,1)
        ngrid.addWidget(QLabel('High clip percentile:'),1,2); ngrid.addWidget(self.norm_high_pct,1,3)
        note=QLabel('Controls percentile clipping and scaling for normalised ratio-map outputs only.')
        note.setWordWrap(True); ngrid.addWidget(note,2,0,1,4)
        normgb.setContentLayout(ngrid)
        root.addWidget(normgb)
        self.normalisation_method_box = normgb
        self.gen_norm_maps.toggled.connect(self._update_normalisation_controls)

        run=QPushButton('Generate Derived Ratio Maps')
        run.setMinimumHeight(38); run.clicked.connect(self._generate_derived_ratio_maps)
        root.addWidget(run)

        self.norm_summary=QPlainTextEdit(); self.norm_summary.setReadOnly(True)
        root.addWidget(self.norm_summary,1)
        self._update_normalisation_controls()
        return w

    def _make_crossplot_tab(self):
        """Stage 3 tab 2: diagnostic scatter/crossplot analysis."""
        w=QWidget(); root=QVBoxLayout(w)
        srcbox=CollapsibleBox('Crossplot Data Source', checked=True)
        grid=QGridLayout()
        self.crossplot_source=QComboBox(); self.crossplot_source.addItems(['Raw Ratio Maps','Normalised Ratio Maps','Log Ratio Maps','ZLog Ratio Maps','Combined Maps'])
        self.crossplot_x=QComboBox(); self.crossplot_y=QComboBox(); self.crossplot_colour=QComboBox()
        self.crossplot_colour.addItem('(none)', None)
        grid.addWidget(QLabel('Data source:'),0,0); grid.addWidget(self.crossplot_source,0,1)
        grid.addWidget(QLabel('X raster:'),1,0); grid.addWidget(self.crossplot_x,1,1)
        grid.addWidget(QLabel('Y raster:'),2,0); grid.addWidget(self.crossplot_y,2,1)
        grid.addWidget(QLabel('Colour by:'),3,0); grid.addWidget(self.crossplot_colour,3,1)
        self.crossplot_log_x=QCheckBox('log X'); self.crossplot_log_y=QCheckBox('log Y')
        grid.addWidget(self.crossplot_log_x,4,0); grid.addWidget(self.crossplot_log_y,4,1)
        self.crossplot_sample=QSpinBox(); self.crossplot_sample.setRange(1000,200000); self.crossplot_sample.setValue(8000); self.crossplot_sample.setSingleStep(1000)
        grid.addWidget(QLabel('Maximum sample points:'),5,0); grid.addWidget(self.crossplot_sample,5,1)
        refresh=QPushButton('Refresh Crossplot Raster Lists'); refresh.clicked.connect(self._refresh_crossplot_lists)
        plot=QPushButton('Generate Crossplot'); plot.clicked.connect(self._create_selected_crossplot)
        grid.addWidget(refresh,6,0); grid.addWidget(plot,6,1)
        note=QLabel('Compare two selected rasters using sampled cell values from the active processing folder.')
        note.setWordWrap(True); grid.addWidget(note,7,0,1,2)
        srcbox.setContentLayout(grid)
        root.addWidget(srcbox)
        self.crossplot_source.currentIndexChanged.connect(self._refresh_crossplot_lists)
        if HAS_MPL:
            self.crossplot_fig=Figure(figsize=(7,5), tight_layout=True)
            self.crossplot_canvas=FigureCanvas(self.crossplot_fig)
            root.addWidget(self.crossplot_canvas,1)
        else:
            root.addWidget(QLabel('Matplotlib is required for crossplot analysis.'),1)
        return w

    def _make_pca_tab(self):
        """Build PCA / multivariate analysis tab.

        PCA is treated as a diagnostic, data-driven complement to RRAI-U and
        HRAI-KU. It does not replace the fixed geological indices; it helps QC
        the multivariate structure in the ratio/log-ratio maps.
        """
        w = QWidget(); root = QVBoxLayout(w)
        info = QLabel(
            'PCA uses several ratio or log-ratio rasters together to find the strongest multivariate patterns. '
            'Use Z-Log ratio maps where available; otherwise normalised ratios are acceptable for a QC diagnostic. '
            'Interpret components from their loadings and map patterns, not from the component number alone.'
        )
        info.setWordWrap(True); root.addWidget(info)

        inputs = CollapsibleBox('PCA Inputs', checked=True)
        grid = QGridLayout()
        self.pca_source = QComboBox()
        self.pca_source.addItems(['Z-Log ratio maps (recommended)', 'Log ratio maps', 'Normalised ratio maps', 'Current selected ratio rasters'])
        self.pca_source.currentIndexChanged.connect(self._pca_refresh_input_preview)
        self.pca_vars = {}
        for name in ['U/Th', 'U/K', 'K/U', 'K/Th']:
            chk = QCheckBox(name); chk.setChecked(True)
            self.pca_vars[name] = chk
        self.pca_scale = QCheckBox('Centre and scale variables before PCA')
        self.pca_scale.setChecked(True)
        self.pca_ncomp = QSpinBox(); self.pca_ncomp.setRange(2, 4); self.pca_ncomp.setValue(3)
        self.pca_sample_max = QSpinBox(); self.pca_sample_max.setRange(1000, 500000); self.pca_sample_max.setSingleStep(10000); self.pca_sample_max.setValue(100000)
        self.pca_write_scores = QCheckBox('Write PC score rasters and load into QGIS'); self.pca_write_scores.setChecked(True)
        self.pca_input_preview = QPlainTextEdit(); self.pca_input_preview.setReadOnly(True); self.pca_input_preview.setMaximumHeight(90)
        grid.addWidget(QLabel('Input source'),0,0); grid.addWidget(self.pca_source,0,1,1,3)
        grid.addWidget(QLabel('Variables'),1,0)
        c = 1
        for chk in self.pca_vars.values(): grid.addWidget(chk,1,c); c += 1
        grid.addWidget(self.pca_scale,2,0,1,2)
        grid.addWidget(QLabel('Components'),2,2); grid.addWidget(self.pca_ncomp,2,3)
        grid.addWidget(QLabel('Fit sample max cells'),3,0); grid.addWidget(self.pca_sample_max,3,1)
        grid.addWidget(self.pca_write_scores,3,2,1,2)
        refresh = QPushButton('Refresh PCA Input Preview'); refresh.clicked.connect(self._pca_refresh_input_preview)
        grid.addWidget(refresh,4,0,1,2)
        help_btn = QPushButton('PCA Help and Interpretation'); help_btn.clicked.connect(self._show_pca_explanation)
        grid.addWidget(help_btn,4,2,1,2)
        grid.addWidget(self.pca_input_preview,5,0,1,4)
        inputs.setContentLayout(grid); root.addWidget(inputs)

        runbox = CollapsibleBox('Run PCA and Review Loadings', checked=True)
        rlay = QVBoxLayout()
        run_btn = QPushButton('Run PCA'); run_btn.setMinimumHeight(34); run_btn.clicked.connect(self._run_pca_analysis)
        rlay.addWidget(run_btn)
        self.pca_table = QTableWidget(); self.pca_table.setMinimumHeight(130)
        self.pca_table.setColumnCount(1); self.pca_table.setHorizontalHeaderLabels(['Run PCA to fill loadings'])
        self.pca_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        rlay.addWidget(self.pca_table)
        if HAS_MPL:
            self.pca_fig = Figure(figsize=(8,4.5), tight_layout=True)
            self.pca_canvas = FigureCanvas(self.pca_fig)
            self.pca_canvas.setMinimumHeight(320)
            rlay.addWidget(self.pca_canvas)
            exp_row = QHBoxLayout()
            export_img = QPushButton('Export PCA Plot Image')
            export_img.clicked.connect(self._export_pca_plot_image)
            export_csv = QPushButton('Export PCA Scores Sample CSV')
            export_csv.clicked.connect(self._export_pca_scores_sample_csv)
            exp_row.addWidget(export_img)
            exp_row.addWidget(export_csv)
            rlay.addLayout(exp_row)
        else:
            rlay.addWidget(QLabel('Matplotlib not available: PCA still writes rasters and CSV outputs.'))
        runbox.setContentLayout(rlay); root.addWidget(runbox, 1)

        ext = CollapsibleBox('PCA Score Threshold and Polygon Extraction', checked=False)
        egrid = QGridLayout()
        self.pca_extract_pc = QComboBox(); self.pca_extract_pc.addItems(['PC1','PC2','PC3','PC4'])
        self.pca_condition = QComboBox(); self.pca_condition.addItems(['Z score >= threshold', 'Z score <= -threshold', 'Absolute Z score >= threshold'])
        self.pca_z_threshold = QDoubleSpinBox(); self.pca_z_threshold.setRange(0.1, 10.0); self.pca_z_threshold.setDecimals(2); self.pca_z_threshold.setSingleStep(0.25); self.pca_z_threshold.setValue(2.0)
        self.pca_min_pixels = QSpinBox(); self.pca_min_pixels.setRange(0,1000000); self.pca_min_pixels.setValue(5)
        self.pca_min_area = QDoubleSpinBox(); self.pca_min_area.setRange(0.0,999999.0); self.pca_min_area.setDecimals(3); self.pca_min_area.setValue(0.0)
        egrid.addWidget(QLabel('Component'),0,0); egrid.addWidget(self.pca_extract_pc,0,1)
        egrid.addWidget(QLabel('Condition'),0,2); egrid.addWidget(self.pca_condition,0,3)
        egrid.addWidget(QLabel('Threshold'),1,0); egrid.addWidget(self.pca_z_threshold,1,1)
        egrid.addWidget(QLabel('Min pixels'),1,2); egrid.addWidget(self.pca_min_pixels,1,3)
        egrid.addWidget(QLabel('Min area km²'),2,0); egrid.addWidget(self.pca_min_area,2,1)
        extract_btn = QPushButton('Extract PCA Anomaly Polygons'); extract_btn.clicked.connect(self._extract_pca_polygons)
        egrid.addWidget(extract_btn,3,0,1,4)
        enote = QLabel('PCA anomalies are data-driven statistical outliers. Use them as a QC/exploration screening layer and compare with RRAI-U, HRAI-KU, ATP, geochemistry, structure and seismic evidence.')
        enote.setWordWrap(True); egrid.addWidget(enote,4,0,1,4)
        ext.setContentLayout(egrid); root.addWidget(ext)

        qbox = CollapsibleBox('PCA Quadrants and PC1–HRI Comparison', checked=False)
        qgrid = QGridLayout()
        qnote = QLabel('After running PCA, create a PC1/PC2 quadrant raster (classes 1–4), extract polygons by quadrant, or compare PC1 against HRI. Quadrant meanings must be interpreted from the loading table; the labels are diagnostic guides, not fixed geology.')
        qnote.setWordWrap(True)
        qgrid.addWidget(qnote, 0, 0, 1, 4)
        self.pca_filter_tiny_quadrants = QCheckBox('Optional: remove tiny quadrant polygons before summary/export')
        self.pca_filter_tiny_quadrants.setChecked(False)
        self.pca_quadrant_min_area = QDoubleSpinBox()
        self.pca_quadrant_min_area.setRange(0.0, 999999.0)
        self.pca_quadrant_min_area.setDecimals(4)
        self.pca_quadrant_min_area.setSingleStep(0.05)
        self.pca_quadrant_min_area.setValue(0.25)
        qgrid.addWidget(self.pca_filter_tiny_quadrants, 1, 0, 1, 3)
        qgrid.addWidget(QLabel('Min area km²'), 1, 3)
        qgrid.addWidget(self.pca_quadrant_min_area, 1, 4)
        quad_btn = QPushButton('Export PC1/PC2 Quadrant Raster')
        quad_btn.clicked.connect(self._export_pca_quadrant_raster)
        quad_poly_btn = QPushButton('Extract PC1/PC2 Quadrant Polygons')
        quad_poly_btn.clicked.connect(self._extract_pca_quadrant_polygons)
        pc1_hri_btn = QPushButton('Plot PC1 vs HRI')
        pc1_hri_btn.clicked.connect(self._plot_pc1_vs_hri)
        pc1_hri_csv = QPushButton('Export PC1 vs HRI CSV')
        pc1_hri_csv.clicked.connect(self._export_pc1_hri_csv)
        qgrid.addWidget(quad_btn, 2, 0)
        qgrid.addWidget(quad_poly_btn, 2, 1)
        qgrid.addWidget(pc1_hri_btn, 2, 2)
        qgrid.addWidget(pc1_hri_csv, 2, 3, 1, 2)
        qbox.setContentLayout(qgrid); root.addWidget(qbox)

        self.pca_summary = QPlainTextEdit(); self.pca_summary.setReadOnly(True); self.pca_summary.setMaximumHeight(120)
        root.addWidget(self.pca_summary)
        try: self._pca_refresh_input_preview()
        except Exception: pass
        return w


    # ------------------------- PCA / multivariate analysis

    def _pca_base_run_dir(self):
        """Base PCA folder for the active QC run/session."""
        return ensure_dir(os.path.join(self._pca_dir(), self._safe_run_id()))

    def _next_pca_version_tag(self):
        """Create a monotonically numbered PCA version tag with date/time.

        This prevents repeated PCA runs from overwriting or mixing outputs.
        Example: PCA_v001_20260511_063012
        """
        base = self._pca_base_run_dir()
        existing = []
        try:
            for name in os.listdir(base):
                if name.startswith('PCA_v') and os.path.isdir(os.path.join(base, name)):
                    # expected pattern PCA_v001_YYYYMMDD_HHMMSS
                    try:
                        existing.append(int(name.split('_')[1].replace('v','')))
                    except Exception:
                        pass
        except Exception:
            pass
        next_no = (max(existing) + 1) if existing else 1
        return f"PCA_v{next_no:03d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _new_pca_run_dir(self):
        """Create and remember a fresh PCA version folder for a new PCA run."""
        tag = self._next_pca_version_tag()
        out_dir = ensure_dir(os.path.join(self._pca_base_run_dir(), tag))
        self._pca_version_tag = tag
        self._pca_output_token = f"{self._safe_run_id()}_{tag}"
        self._pca_out_dir = out_dir
        return out_dir

    def _current_pca_run_dir(self):
        """Return the current PCA version folder, creating one only if PCA has not been run."""
        out_dir = getattr(self, '_pca_out_dir', '')
        if out_dir and os.path.isdir(out_dir):
            return out_dir
        return self._new_pca_run_dir()

    def _pca_token(self):
        """Filesystem-safe token used in PCA output filenames."""
        return getattr(self, '_pca_output_token', f"{self._safe_run_id()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    def _pca_source_paths(self):
        """Return ordered PCA variable names and raster paths from selected source."""
        source = self.pca_source.currentText() if hasattr(self, 'pca_source') else 'Normalised ratio maps'
        selected = [name for name, chk in getattr(self, 'pca_vars', {}).items() if chk.isChecked()]
        if not selected:
            raise RuntimeError('Select at least two PCA variables.')
        candidates = {}
        if source.startswith('Current'):
            combo_map = {
                'U/Th': getattr(self, 'cmb_uth', None),
                'U/K': getattr(self, 'cmb_uk', None),
                'K/U': getattr(self, 'cmb_ku', None),
                'K/Th': getattr(self, 'cmb_kth', None),
            }
            for name in selected:
                cmb = combo_map.get(name)
                if cmb and cmb.currentData(): candidates[name] = cmb.currentData()
        else:
            if source.startswith('Z-Log'):
                dirs = [self._zlog_dir(), os.path.join(self.processing_folder, '04_ZLog_Ratio_Maps')]
                pattern_map = {'U/Th':['U_Th_zlog.tif','ZLog_U_Th.tif','z_ln_U_Th.tif'], 'U/K':['U_K_zlog.tif','ZLog_U_K.tif','z_ln_U_K.tif'], 'K/U':['K_U_zlog.tif','ZLog_K_U.tif','z_ln_K_U.tif'], 'K/Th':['K_Th_zlog.tif','ZLog_K_Th.tif','z_ln_K_Th.tif']}
            elif source.startswith('Log'):
                dirs = [self._log_dir(), os.path.join(self.processing_folder, '04_Log_Ratio_Maps')]
                pattern_map = {'U/Th':['U_Th_log.tif','Log_U_Th.tif','ln_U_Th.tif'], 'U/K':['U_K_log.tif','Log_U_K.tif','ln_U_K.tif'], 'K/U':['K_U_log.tif','Log_K_U.tif','ln_K_U.tif'], 'K/Th':['K_Th_log.tif','Log_K_Th.tif','ln_K_Th.tif']}
            else:
                dirs = [self._norm_dir(), os.path.join(self.processing_folder, '04_Normalised_Ratio_Maps')]
                pattern_map = {'U/Th':['U_Th_norm.tif'], 'U/K':['U_K_norm.tif'], 'K/U':['K_U_norm.tif'], 'K/Th':['K_Th_norm.tif']}
            for name in selected:
                for d in dirs:
                    for fn in pattern_map.get(name, []):
                        p = os.path.join(d, fn)
                        if os.path.exists(p):
                            candidates[name] = p; break
                    if name in candidates: break
        missing = [n for n in selected if n not in candidates or not os.path.exists(str(candidates[n]))]
        if missing:
            raise RuntimeError('Missing PCA input rasters for: ' + ', '.join(missing) + '. Generate the selected map type first or use Current selected ratio rasters.')
        names = [n for n in selected]
        paths = [candidates[n] for n in names]
        return names, paths

    def _pca_refresh_input_preview(self):
        if not hasattr(self, 'pca_input_preview'):
            return
        try:
            names, paths = self._pca_source_paths()
            msg = ['Selected PCA inputs:'] + [f'  {n}: {p}' for n,p in zip(names, paths)]
            msg.append('\nRecommended: Z-Log ratio maps for log-ratio PCA; Normalised ratio maps for quick QC if Z-Log maps are not available.')
            self.pca_input_preview.setPlainText('\n'.join(msg))
        except Exception as e:
            self.pca_input_preview.setPlainText('PCA input preview warning: ' + str(e))

    def _run_pca_analysis(self):
        try:
            names, paths = self._pca_source_paths()
            ncomp = min(int(self.pca_ncomp.value()), len(names))
            arrays = []
            profile = None
            for p in paths:
                arr, prof = read_raster(p)
                if profile is None: profile = prof
                arrays.append(arr.astype(np.float64))
            shape = arrays[0].shape
            if any(a.shape != shape for a in arrays):
                raise RuntimeError('All PCA input rasters must have identical dimensions.')
            valid = np.ones(shape, dtype=bool)
            for a in arrays: valid &= np.isfinite(a)
            idx_all = np.flatnonzero(valid.ravel())
            if idx_all.size < 10:
                raise RuntimeError('Too few valid cells for PCA.')
            sample_max = int(self.pca_sample_max.value())
            if idx_all.size > sample_max:
                rng = np.random.default_rng(42)
                fit_idx = rng.choice(idx_all, sample_max, replace=False)
            else:
                fit_idx = idx_all
            X_fit = np.column_stack([a.ravel()[fit_idx] for a in arrays]).astype(np.float64)
            mean = np.nanmean(X_fit, axis=0)
            scale = np.nanstd(X_fit, axis=0) if self.pca_scale.isChecked() else np.ones(len(names), dtype=np.float64)
            scale[~np.isfinite(scale) | (scale == 0)] = 1.0
            Xc = (X_fit - mean) / scale
            # SVD-based PCA; no external dependency required.
            U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
            components = Vt[:ncomp].T  # variables x components
            eig = (S[:ncomp] ** 2) / max(1, (Xc.shape[0] - 1))
            all_eig = (S ** 2) / max(1, (Xc.shape[0] - 1))
            explained = eig / np.sum(all_eig) if np.sum(all_eig) > 0 else np.zeros_like(eig)

            # Full raster projection in memory. This is acceptable for the typical grids used here;
            # session output isolation prevents accidental overwrite during QC reruns.
            X_full = np.column_stack([a.ravel()[idx_all] for a in arrays]).astype(np.float64)
            X_full = (X_full - mean) / scale
            score_vals = X_full @ components
            score_arrays = []
            for k in range(ncomp):
                out = np.full(shape, np.nan, dtype=np.float64)
                out.ravel()[idx_all] = score_vals[:, k]
                score_arrays.append(out)

            out_dir = self._new_pca_run_dir()
            pca_token = self._pca_token()
            score_paths = []
            if self.pca_write_scores.isChecked():
                for k, arr in enumerate(score_arrays, start=1):
                    p = make_available_path(os.path.join(out_dir, f'PCA_PC{k}_score_{pca_token}.tif'))
                    write_raster(p, arr, profile, gdal.GDT_Float32)
                    load_raster_pseudocolor_quantile(p, f'PCA_PC{k}_{pca_token}')
                    score_paths.append(p)
            # CSV report
            csv_path = os.path.join(out_dir, f'PCA_loadings_explained_variance_{pca_token}.csv')
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['variable'] + [f'PC{k}' for k in range(1, ncomp+1)])
                for i, name in enumerate(names):
                    w.writerow([name] + [float(components[i, k]) for k in range(ncomp)])
                w.writerow([])
                w.writerow(['component','explained_variance_ratio'])
                for k in range(ncomp): w.writerow([f'PC{k+1}', float(explained[k])])
                w.writerow([])
                w.writerow(['centering_mean'] + [float(x) for x in mean])
                w.writerow(['scaling_std'] + [float(x) for x in scale])
                w.writerow(['input_source', self.pca_source.currentText()])
                w.writerow(['pca_version', getattr(self, '_pca_version_tag', '')])
                w.writerow(['pca_run_folder', out_dir])
                for name, path in zip(names, paths): w.writerow([name, path])
            # Populate UI
            self._pca_names = names; self._pca_components = components; self._pca_explained = explained
            self._pca_scores = score_arrays; self._pca_profile = profile; self._pca_score_paths = score_paths; self._pca_out_dir = out_dir
            self._fill_pca_table(names, components, explained)
            self._plot_pca_results(score_vals, components, explained, names)
            msg = [f'PCA version: {getattr(self, '_pca_version_tag', '')}', f'PCA folder: {out_dir}', f'PCA complete for {len(names)} variables and {idx_all.size:,} valid cells.', f'Fit sample cells: {fit_idx.size:,}', f'Loadings CSV: {csv_path}']
            if score_paths: msg.append('Score rasters:\n' + '\n'.join(score_paths))
            self.pca_summary.setPlainText('\n'.join(msg))
            QMessageBox.information(self, 'PCA complete', 'PCA score rasters and loadings were generated. Interpret the components from the loadings and biplot before extracting anomalies.')
        except Exception as e:
            QMessageBox.critical(self, 'PCA failed', str(e))

    def _fill_pca_table(self, names, components, explained):
        ncomp = components.shape[1]
        self.pca_table.setColumnCount(ncomp + 1)
        self.pca_table.setRowCount(len(names) + 1)
        self.pca_table.setHorizontalHeaderLabels(['Variable'] + [f'PC{k}' for k in range(1, ncomp+1)])
        for i, name in enumerate(names):
            self.pca_table.setItem(i, 0, QTableWidgetItem(name))
            for k in range(ncomp):
                self.pca_table.setItem(i, k+1, QTableWidgetItem(f'{components[i,k]:.4f}'))
        self.pca_table.setItem(len(names), 0, QTableWidgetItem('Explained variance'))
        for k in range(ncomp):
            self.pca_table.setItem(len(names), k+1, QTableWidgetItem(f'{100*explained[k]:.2f}%'))
        self.pca_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    def _plot_pca_results(self, score_vals, components, explained, names):
        if not HAS_MPL or not hasattr(self, 'pca_fig'):
            return
        self.pca_fig.clear()
        ax1 = self.pca_fig.add_subplot(1,2,1)
        xs = np.arange(1, len(explained)+1)
        ax1.bar(xs, 100*explained)
        ax1.set_xlabel('Principal component')
        ax1.set_ylabel('Explained variance (%)')
        ax1.set_title('PCA scree plot')
        ax1.set_xticks(xs)
        ax2 = self.pca_fig.add_subplot(1,2,2)
        if score_vals.shape[1] >= 2:
            sample = min(6000, score_vals.shape[0])
            rng = np.random.default_rng(42)
            use = rng.choice(score_vals.shape[0], sample, replace=False) if score_vals.shape[0] > sample else np.arange(score_vals.shape[0])
            ax2.scatter(score_vals[use,0], score_vals[use,1], s=6, alpha=0.35, edgecolors='none')
            # Loading arrows scaled by score std for visibility.
            sx = np.nanstd(score_vals[:,0]); sy = np.nanstd(score_vals[:,1])
            arrow_scale = 2.2
            for i, name in enumerate(names):
                x = components[i,0] * sx * arrow_scale
                y = components[i,1] * sy * arrow_scale
                ax2.arrow(0,0,x,y, head_width=0.05*max(sx,1e-9), length_includes_head=True, alpha=0.85)
                ax2.text(x*1.08, y*1.08, name, fontsize=8)
            ax2.axhline(0, color='gray', lw=0.8, ls='--')
            ax2.axvline(0, color='gray', lw=0.8, ls='--')
            ax2.set_xlabel(f'PC1 ({100*explained[0]:.1f}%)')
            ax2.set_ylabel(f'PC2 ({100*explained[1]:.1f}%)')
            ax2.set_title('PC1–PC2 biplot')
        else:
            ax2.text(0.5,0.5,'Need at least 2 components for biplot', ha='center')
        self.pca_canvas.draw()

    def _extract_pca_polygons(self):
        try:
            if not hasattr(self, '_pca_scores') or not self._pca_scores:
                raise RuntimeError('Run PCA first.')
            pc_idx = self.pca_extract_pc.currentIndex()
            if pc_idx >= len(self._pca_scores):
                raise RuntimeError('Selected PC was not generated.')
            arr = self._pca_scores[pc_idx]
            vals = arr[np.isfinite(arr)]
            if vals.size == 0: raise RuntimeError('Selected PC has no valid cells.')
            mu = float(np.nanmean(vals)); sd = float(np.nanstd(vals))
            if sd <= 0: raise RuntimeError('Selected PC has zero standard deviation.')
            z = (arr - mu) / sd
            thr = float(self.pca_z_threshold.value())
            cond = self.pca_condition.currentText()
            if cond.startswith('Z score >='):
                mask = z >= thr
                label = f'PC{pc_idx+1}_z_ge_{thr:g}'
            elif cond.startswith('Z score <='):
                mask = z <= -thr
                label = f'PC{pc_idx+1}_z_le_minus_{thr:g}'
            else:
                mask = np.abs(z) >= thr
                label = f'PC{pc_idx+1}_abs_z_ge_{thr:g}'
            out = np.zeros(arr.shape, dtype=np.uint8); out[np.isfinite(z) & mask] = 1
            out_dir = ensure_dir(os.path.join(self._current_pca_run_dir(), 'extraction'))
            pca_token = self._pca_token()
            mask_path = make_available_path(os.path.join(out_dir, f'PCA_{label}_mask_{pca_token}.tif'))
            write_raster(mask_path, out, self._pca_profile, gdal.GDT_Byte)
            gpkg = make_available_path(os.path.join(out_dir, f'PCA_{label}_polygons_{pca_token}.gpkg'))
            polygonize_binary_mask_raster(mask_path, gpkg, 'pca_anomaly', float(self.pca_min_area.value()), int(self.pca_min_pixels.value()))
            load_layer(mask_path, os.path.basename(mask_path))
            load_layer(gpkg, os.path.basename(gpkg).replace('.gpkg',''))
            self.pca_summary.setPlainText((self.pca_summary.toPlainText() + '\n\nPCA extraction:\n' + mask_path + '\n' + gpkg).strip())
            QMessageBox.information(self, 'PCA extraction complete', f'Created:\n{gpkg}')
        except Exception as e:
            QMessageBox.critical(self, 'PCA extraction failed', str(e))

    def _pca_quadrant_array(self):
        """Classify PC1/PC2 scores into four diagnostic quadrants.

        Values:
            1 = PC1+ / PC2+
            2 = PC1- / PC2+
            3 = PC1- / PC2-
            4 = PC1+ / PC2-

        The geological meaning of each class must be interpreted from the PCA
        loading table and biplot for the current dataset. The classes are not
        fixed lithological or petroleum-system categories by themselves.
        """
        if not hasattr(self, '_pca_scores') or len(self._pca_scores) < 2:
            raise RuntimeError('Run PCA with at least two components first.')
        pc1 = self._pca_scores[0]
        pc2 = self._pca_scores[1]
        out = np.zeros(pc1.shape, dtype=np.uint8)
        valid = np.isfinite(pc1) & np.isfinite(pc2)
        out[valid & (pc1 >= 0) & (pc2 >= 0)] = 1
        out[valid & (pc1 < 0) & (pc2 >= 0)] = 2
        out[valid & (pc1 < 0) & (pc2 < 0)] = 3
        out[valid & (pc1 >= 0) & (pc2 < 0)] = 4
        return out

    def _pca_quadrant_legend(self):
        # Class labels requested for the PCA quadrant legend. These are
        # interpretive guides that should still be checked against the PCA
        # loading table and biplot for the current run.
        return [
            (1, 'Uranium_Enrichment_U_Th_Decoupled', 'High PC1 (U/K enriched) + High PC2 (U/Th decoupled) -> Redox fronts, alteration. HIGHEST PRIORITY TARGETS.'),
            (2, 'Potassium_Dominant_Alteration', 'Low PC1 (K/U dominant) + High PC2 (U/Th decoupled) -> Alteration halos, source rock context. LOW PRIORITY.'),
            (3, 'Background_Thorium_Potassium', 'Low PC1 + Low PC2 -> K/Th and K/U association. BACKGROUND. NO URANIUM TARGET.'),
            (4, 'Uranium_Enrichment_Only', 'High PC1 + Low PC2 -> U enrichment without U/Th decoupling. MODERATE PRIORITY TARGETS.'),
        ]

    def _write_pca_quadrant_legend_csv(self, out_dir):
        path = make_available_path(os.path.join(out_dir, f'PCA_PC1_PC2_quadrant_legend_{self._pca_token()}.csv'))
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['class', 'class_name', 'interpretation_note'])
            for row in self._pca_quadrant_legend():
                w.writerow(row)
        return path

    def _pca_quadrant_min_area_value(self):
        try:
            if hasattr(self, 'pca_filter_tiny_quadrants') and self.pca_filter_tiny_quadrants.isChecked():
                return float(self.pca_quadrant_min_area.value())
        except Exception:
            pass
        return 0.0

    def _apply_pca_quadrant_class_names(self, gpkg_path):
        """Update quadrant polygon class_name values to match the quadrant legend."""
        legend = {int(c): str(name) for c, name, note in self._pca_quadrant_legend()}
        try:
            ds = ogr.Open(gpkg_path, 1)
            if ds is None:
                return
            lyr = ds.GetLayer(0)
            lyr.ResetReading()
            for feat in lyr:
                cv = feat.GetField('class')
                try:
                    cv = int(cv)
                except Exception:
                    continue
                if cv in legend:
                    feat.SetField('class_name', legend[cv])
                    lyr.SetFeature(feat)
            ds = None
        except Exception:
            pass

    def _write_pca_quadrant_area_summary_csv(self, gpkg_path, out_dir):
        """Write per-quadrant polygon counts and area totals after optional filtering."""
        path = make_available_path(os.path.join(out_dir, f'PCA_PC1_PC2_quadrant_area_summary_{self._pca_token()}.csv'))
        legend = {int(c): (str(name), str(note)) for c, name, note in self._pca_quadrant_legend()}
        min_area = self._pca_quadrant_min_area_value()
        totals = {c: {'count': 0, 'area': 0.0} for c in legend}
        try:
            ds = ogr.Open(gpkg_path, 0)
            if ds is not None:
                lyr = ds.GetLayer(0)
                for feat in lyr:
                    cv = feat.GetField('class')
                    area = feat.GetField('area_km2')
                    try:
                        cv = int(cv); area = float(area)
                    except Exception:
                        continue
                    if cv in totals:
                        totals[cv]['count'] += 1
                        totals[cv]['area'] += area
                ds = None
        except Exception:
            pass
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['class','class_name','polygon_count','total_area_km2','min_area_filter_km2','interpretation_note'])
            for c in sorted(legend):
                name, note = legend[c]
                w.writerow([c, name, totals[c]['count'], totals[c]['area'], min_area, note])
        return path

    def _export_pca_quadrant_raster(self):
        """Write a PC1/PC2 quadrant class raster for map-based review."""
        try:
            quad = self._pca_quadrant_array()
            out_dir = ensure_dir(os.path.join(self._current_pca_run_dir(), 'quadrants'))
            pca_token = self._pca_token()
            path = make_available_path(os.path.join(out_dir, f'PCA_PC1_PC2_quadrants_{pca_token}.tif'))
            write_raster(path, quad, self._pca_profile, gdal.GDT_Byte)
            legend = self._write_pca_quadrant_legend_csv(out_dir)
            load_layer(path, os.path.basename(path))
            self.pca_summary.setPlainText((self.pca_summary.toPlainText() + f'\n\nPCA quadrant raster:\n{path}\nLegend:\n{legend}').strip())
            QMessageBox.information(self, 'PCA quadrant raster exported', f'Created:\n{path}\n\nLegend:\n{legend}')
        except Exception as e:
            QMessageBox.critical(self, 'PCA quadrant raster failed', str(e))

    def _extract_pca_quadrant_polygons(self):
        """Polygonise PC1/PC2 quadrant classes 1–4."""
        try:
            quad = self._pca_quadrant_array()
            out_dir = ensure_dir(os.path.join(self._current_pca_run_dir(), 'quadrants'))
            pca_token = self._pca_token()
            raster_path = make_available_path(os.path.join(out_dir, f'PCA_PC1_PC2_quadrants_{pca_token}.tif'))
            write_raster(raster_path, quad, self._pca_profile, gdal.GDT_Byte)
            gpkg = make_available_path(os.path.join(out_dir, f'PCA_PC1_PC2_quadrant_polygons_{pca_token}.gpkg'))
            min_area = self._pca_quadrant_min_area_value()
            polygonize_class_raster(raster_path, gpkg, 'pca_pc1_pc2_quadrants', [1,2,3,4], min_area)
            self._apply_pca_quadrant_class_names(gpkg)
            legend = self._write_pca_quadrant_legend_csv(out_dir)
            area_summary = self._write_pca_quadrant_area_summary_csv(gpkg, out_dir)
            load_layer(raster_path, os.path.basename(raster_path))
            load_layer(gpkg, os.path.basename(gpkg).replace('.gpkg',''))
            filter_note = f'\nMin area filter: {min_area} km²' if min_area > 0 else '\nMin area filter: off'
            self.pca_summary.setPlainText((self.pca_summary.toPlainText() + f'\n\nPCA quadrant polygons:\n{gpkg}\nLegend:\n{legend}\nArea summary:\n{area_summary}' + filter_note).strip())
            QMessageBox.information(self, 'PCA quadrant polygons exported', f'Created:\n{gpkg}\n\nLegend:\n{legend}\nArea summary:\n{area_summary}{filter_note}')
        except Exception as e:
            QMessageBox.critical(self, 'PCA quadrant extraction failed', str(e))

    def _find_latest_hri_path(self):
        """Find the newest HRI raster from the combined outputs or loaded QGIS layers."""
        candidates = []
        # Prefer loaded layers first if user is actively working with one.
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if isinstance(lyr, QgsRasterLayer) and 'hri' in (lyr.name() + ' ' + lyr.source()).lower():
                    candidates.append(lyr.source())
            except Exception:
                pass
        folders = [self._combined_dir(), os.path.join(self.processing_folder, '07_Combined_Maps')]
        for folder in folders:
            if folder and os.path.isdir(folder):
                for pat in ('HRI_Hydrocarbon_Radiometric_Index*.tif', '*HRI*.tif'):
                    candidates.extend(glob.glob(os.path.join(folder, pat)))
        candidates = [p for p in candidates if p and os.path.exists(p)]
        if not candidates:
            return ''
        candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return candidates[0]

    def _plot_pc1_vs_hri(self):
        """Scatterplot PC1 against HRI to check agreement/disagreement."""
        try:
            if not hasattr(self, '_pca_scores') or not self._pca_scores:
                raise RuntimeError('Run PCA first.')
            if not HAS_MPL or not hasattr(self, 'pca_fig'):
                raise RuntimeError('Matplotlib is not available.')
            hri_path = self._find_latest_hri_path()
            if not hri_path:
                raise RuntimeError('Could not find an HRI raster. Generate HRI first in Stage 3 Combined Maps.')
            hri, _ = read_raster(hri_path)
            pc1 = self._pca_scores[0]
            if pc1.shape != hri.shape:
                raise RuntimeError('PC1 and HRI rasters have different dimensions. Use matching session outputs.')
            valid = np.isfinite(pc1) & np.isfinite(hri)
            idx = np.flatnonzero(valid.ravel())
            if idx.size == 0:
                raise RuntimeError('No overlapping valid PC1 and HRI cells.')
            sample = min(10000, idx.size)
            rng = np.random.default_rng(42)
            use = rng.choice(idx, sample, replace=False) if idx.size > sample else idx
            x = pc1.ravel()[use]
            y = hri.ravel()[use]
            self._pc1_hri_sample = (use, x, y, hri_path)
            self.pca_fig.clear()
            ax = self.pca_fig.add_subplot(1,1,1)
            ax.scatter(x, y, s=7, alpha=0.35, edgecolors='none')
            # Correlation is a diagnostic only; it does not prove geological equivalence.
            corr = np.corrcoef(x, y)[0,1] if x.size > 2 else np.nan
            ax.axhline(np.nanmean(y), color='gray', lw=0.8, ls='--')
            ax.axvline(np.nanmean(x), color='gray', lw=0.8, ls='--')
            ax.set_xlabel('PC1 score')
            ax.set_ylabel('HRI value')
            ax.set_title(f'PC1 vs HRI diagnostic scatterplot (r = {corr:.3f})')
            ax.grid(True, alpha=0.25)
            ax.text(0.02, 0.98, 'High agreement: points trend diagonally.\nStrong disagreement/outliers: investigate spatially.', transform=ax.transAxes, va='top', fontsize=8)
            self.pca_canvas.draw()
            self.pca_summary.setPlainText((self.pca_summary.toPlainText() + f'\n\nPC1 vs HRI scatter plotted. HRI source: {hri_path}\nCorrelation r = {corr:.4f}').strip())
        except Exception as e:
            QMessageBox.critical(self, 'PC1 vs HRI plot failed', str(e))

    def _export_pc1_hri_csv(self):
        """Export the current PC1-vs-HRI sampled scatter values."""
        try:
            if not hasattr(self, '_pc1_hri_sample'):
                raise RuntimeError('Run Plot PC1 vs HRI first.')
            idx, x, y, hri_path = self._pc1_hri_sample
            out_dir = ensure_dir(os.path.join(self._current_pca_run_dir(), 'exports'))
            path, _ = QFileDialog.getSaveFileName(self, 'Export PC1 vs HRI CSV', os.path.join(out_dir, f'PC1_vs_HRI_sample_{self._pca_token()}.csv'), 'CSV (*.csv)')
            if not path:
                return
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['sample_index', 'PC1_score', 'HRI_value', 'HRI_raster'])
                for i, pc1v, hriv in zip(idx, x, y):
                    w.writerow([int(i), float(pc1v), float(hriv), hri_path])
            QMessageBox.information(self, 'PC1 vs HRI CSV exported', f'Exported:\n{path}')
        except Exception as e:
            QMessageBox.critical(self, 'PC1 vs HRI export failed', str(e))

    def _show_pca_explanation(self):
        QMessageBox.information(self, 'PCA for radiometric data',
            'Principal Component Analysis (PCA) is a data-driven QC and anomaly-screening method.\n\n'
            'It transforms correlated ratio or log-ratio rasters into uncorrelated component score maps. '
            'PC1 captures the largest multivariate pattern; PC2 captures the next strongest independent pattern, and so on.\n\n'
            'Recommended input: Z-Log ratio maps where available, because ln(ratio) reduces ratio skew and z(ln ratio) puts all ratios on a comparable scale. '
            'Normalised ratio maps can also be used for quick QC.\n\n'
            'How to interpret:\n'
            '• Loadings show which variables drive each component.\n'
            '• A high positive or negative PC score is a statistical contrast, not automatically a hydrocarbon anomaly.\n'
            '• Use PCA score rasters as screening layers and compare them with RRAI-U, HRAI-KU, ATP, geochemistry, structural lineaments and seismic leads.\n\n'
            'Important: Component meanings are not fixed. Always interpret PC1/PC2 from the loading table and biplot for the current dataset. The PCA quadrant raster is a diagnostic PC1/PC2 classification; its geology depends on the signs and magnitudes of the loadings.'
        )



    def _export_pca_plot_image(self):
        """Export the current PCA scree/biplot figure as PNG."""
        try:
            if not HAS_MPL or not hasattr(self, 'pca_fig'):
                raise RuntimeError('Matplotlib/PCA figure is not available.')
            out_dir = self._current_pca_run_dir()
            path, _ = QFileDialog.getSaveFileName(self, 'Export PCA plot image', os.path.join(out_dir, f'PCA_plot_{self._pca_token()}.png'), 'PNG Image (*.png);;JPEG Image (*.jpg);;PDF (*.pdf)')
            if not path:
                return
            self.pca_fig.savefig(path, dpi=200, bbox_inches='tight')
            QMessageBox.information(self, 'PCA plot exported', f'Exported:\n{path}')
        except Exception as e:
            QMessageBox.critical(self, 'PCA plot export failed', str(e))

    def _export_pca_scores_sample_csv(self):
        """Export a sampled CSV of PCA score values for QC."""
        try:
            if not hasattr(self, '_pca_scores') or not self._pca_scores:
                raise RuntimeError('Run PCA first.')
            valid = np.ones(self._pca_scores[0].shape, dtype=bool)
            for arr in self._pca_scores:
                valid &= np.isfinite(arr)
            idx = np.flatnonzero(valid.ravel())
            if idx.size == 0:
                raise RuntimeError('No valid PCA scores to export.')
            sample = min(20000, idx.size)
            if idx.size > sample:
                rng = np.random.default_rng(42)
                idx = rng.choice(idx, sample, replace=False)
            out_dir = self._current_pca_run_dir()
            path, _ = QFileDialog.getSaveFileName(self, 'Export PCA score sample CSV', os.path.join(out_dir, f'PCA_scores_sample_{self._pca_token()}.csv'), 'CSV (*.csv)')
            if not path:
                return
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                headers = ['sample_index'] + [f'PC{k+1}' for k in range(len(self._pca_scores))]
                w.writerow(headers)
                for j, flat_idx in enumerate(idx):
                    row = [int(flat_idx)] + [float(arr.ravel()[flat_idx]) for arr in self._pca_scores]
                    w.writerow(row)
            QMessageBox.information(self, 'PCA sample exported', f'Exported:\n{path}')
        except Exception as e:
            QMessageBox.critical(self, 'PCA CSV export failed', str(e))

    def _make_combinations_tab(self):
        """Stage 3 tab 3: choose which combined indices to build and set their combination parameters."""
        w = QWidget(); root = QVBoxLayout(w)

        table_box = CollapsibleBox('Combined Index Generation', checked=True)
        outer = QVBoxLayout()

        method_items = [
            'Weighted mean',
            'Arithmetic mean',
            'Geometric mean',
            'Minimum consensus',
            'Maximum response',
            'Weighted geometric/product consensus'
        ]
        weight_preset_items = [
            '(0.4, 0.4, 0.2)',
            '(0.333, 0.333, 0.333)',
            '(0.6, 0.25, 0.15)',
            '(0.25, 0.6, 0.15)',
            '(0.3, 0.3, 0.4)',
            'Custom'
        ]
        hrai_weight_preset_items = [
            '(0.25, 0.6, 0.15)',
            '(0.333, 0.333, 0.333)',
            '(0.6, 0.25, 0.15)',
            '(0.4, 0.4, 0.2)',
            '(0.3, 0.3, 0.4)',
            'Custom'
        ]
        scalar_preset_items = [
            '(1, 1, 1)',
            '(2, 1, 1)',
            '(1, 2, 1)',
            '(1, 1, 2)',
            'Custom'
        ]

        # RRAI/HRAI subsection: normalised ratio rasters and normalised weights.
        rbox = QGroupBox('RRAI / HRAI - Normalised Ratio Index Inputs')
        rgrid = QGridLayout(rbox)
        rgrid.setColumnStretch(2, 3)
        for col, text in enumerate(['Generate', 'Index', 'Formula', 'Combination method', 'Weight preset', 'w1', 'w2', 'w3']):
            lab = QLabel(text); lab.setStyleSheet('font-weight:bold;')
            rgrid.addWidget(lab, 0, col)

        self.chk_generate_rrai = QCheckBox(); self.chk_generate_rrai.setChecked(True)
        self.rrai_combination_method = QComboBox(); self.rrai_combination_method.addItems(method_items)
        self.rrai_weight_preset = QComboBox(); self.rrai_weight_preset.addItems(weight_preset_items)
        self.rrai_weight_preset.currentIndexChanged.connect(lambda idx: self._apply_weight_preset('rrai'))
        self.rrai_w_uth = QDoubleSpinBox(); self.rrai_w_second = QDoubleSpinBox(); self.rrai_w_kth = QDoubleSpinBox()
        for sp, val in [(self.rrai_w_uth, 0.4), (self.rrai_w_second, 0.4), (self.rrai_w_kth, 0.2)]:
            sp.setRange(0, 1); sp.setDecimals(3); sp.setSingleStep(0.05); sp.setValue(val)
        rgrid.addWidget(self.chk_generate_rrai, 1, 0)
        rgrid.addWidget(QLabel('RRAI-U'), 1, 1)
        rgrid.addWidget(QLabel('w1·norm(U/Th) + w2·norm(U/K) + w3·[1 - norm(K/Th)]'), 1, 2)
        rgrid.addWidget(self.rrai_combination_method, 1, 3)
        rgrid.addWidget(self.rrai_weight_preset, 1, 4)
        rgrid.addWidget(self.rrai_w_uth, 1, 5); rgrid.addWidget(self.rrai_w_second, 1, 6); rgrid.addWidget(self.rrai_w_kth, 1, 7)

        self.chk_generate_hrai = QCheckBox(); self.chk_generate_hrai.setChecked(True)
        self.hrai_combination_method = QComboBox(); self.hrai_combination_method.addItems(method_items)
        self.hrai_weight_preset = QComboBox(); self.hrai_weight_preset.addItems(hrai_weight_preset_items)
        self.hrai_weight_preset.currentIndexChanged.connect(lambda idx: self._apply_weight_preset('hrai'))
        self.hrai_w_uth = QDoubleSpinBox(); self.hrai_w_second = QDoubleSpinBox(); self.hrai_w_kth = QDoubleSpinBox()
        for sp, val in [(self.hrai_w_uth, 0.25), (self.hrai_w_second, 0.6), (self.hrai_w_kth, 0.15)]:
            sp.setRange(0, 1); sp.setDecimals(3); sp.setSingleStep(0.05); sp.setValue(val)
        rgrid.addWidget(self.chk_generate_hrai, 2, 0)
        rgrid.addWidget(QLabel('HRAI-KU'), 2, 1)
        rgrid.addWidget(QLabel('w1·norm(U/Th) + w2·norm(K/U) + w3·[1 - norm(K/Th)]'), 2, 2)
        rgrid.addWidget(self.hrai_combination_method, 2, 3)
        rgrid.addWidget(self.hrai_weight_preset, 2, 4)
        rgrid.addWidget(self.hrai_w_uth, 2, 5); rgrid.addWidget(self.hrai_w_second, 2, 6); rgrid.addWidget(self.hrai_w_kth, 2, 7)

        self.rrai_hrai_inputs_label = QLabel('Inputs: normalised U/Th, U/K, K/U and K/Th rasters from 04_Normalised_Ratio_Maps.')
        self.rrai_hrai_inputs_label.setWordWrap(True)
        rgrid.addWidget(self.rrai_hrai_inputs_label, 3, 0, 1, 8)
        outer.addWidget(rbox)

        # HRI subsection: z-log ratio rasters and raw scalar coefficients.
        hbox = QGroupBox('HRI - ZLog Ratio Index Inputs')
        hgrid = QGridLayout(hbox)
        hgrid.setColumnStretch(2, 3)
        for col, text in enumerate(['Generate', 'Index', 'Formula', 'Combination method', 'Scalar preset', 's1', 's2', 's3']):
            lab = QLabel(text); lab.setStyleSheet('font-weight:bold;')
            hgrid.addWidget(lab, 0, col)

        self.chk_generate_hri = QCheckBox(); self.chk_generate_hri.setChecked(True)
        self.hri_combination_method = QComboBox(); self.hri_combination_method.addItems(['z-log sum'])
        self.hri_weight_preset = QComboBox(); self.hri_weight_preset.addItems(scalar_preset_items)
        self.hri_weight_preset.currentIndexChanged.connect(lambda idx: self._apply_weight_preset('hri'))
        self.hri_w_uth = QDoubleSpinBox(); self.hri_w_second = QDoubleSpinBox(); self.hri_w_kth = QDoubleSpinBox()
        for sp, val in [(self.hri_w_uth, 1.0), (self.hri_w_second, 1.0), (self.hri_w_kth, 1.0)]:
            sp.setRange(0, 10); sp.setDecimals(3); sp.setSingleStep(0.25); sp.setValue(val)
        hgrid.addWidget(self.chk_generate_hri, 1, 0)
        hgrid.addWidget(QLabel('HRI'), 1, 1)
        hgrid.addWidget(QLabel('s1·z(ln(U/Th)) + s2·z(ln(U/K)) - s3·z(ln(Th/K))'), 1, 2)
        hgrid.addWidget(self.hri_combination_method, 1, 3)
        hgrid.addWidget(self.hri_weight_preset, 1, 4)
        hgrid.addWidget(self.hri_w_uth, 1, 5); hgrid.addWidget(self.hri_w_second, 1, 6); hgrid.addWidget(self.hri_w_kth, 1, 7)

        self.hri_inputs_label = QLabel('Inputs: U_Th_zlog, U_K_zlog and Th_K_zlog rasters from 04_ZLog_Ratio_Maps.')
        self.hri_inputs_label.setWordWrap(True)
        hgrid.addWidget(self.hri_inputs_label, 2, 0, 1, 8)
        outer.addWidget(hbox)

        note = QLabel(
            'RRAI-U and HRAI-KU use normalised ratio rasters with weight presets that are normalised to sum to 1. '
            'HRI uses raw scalar coefficients in a z-log sum, with default scalars 1, 1, 1.'
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        table_box.setContentLayout(outer)
        root.addWidget(table_box)

        run_box = CollapsibleBox('Generate Combined Maps', checked=True)
        run_lay = QVBoxLayout()
        self.generate_combined_btn = QPushButton('Generate Selected Combined Maps')
        self.generate_combined_btn.setMinimumHeight(38)
        self.generate_combined_btn.clicked.connect(self._generate_combined)
        run_lay.addWidget(self.generate_combined_btn)
        run_box.setContentLayout(run_lay)
        root.addWidget(run_box)

        self.combination_summary = QPlainTextEdit(); self.combination_summary.setReadOnly(True)
        root.addWidget(self.combination_summary, 1)
        try:
            self._update_combination_input_labels()
        except Exception:
            pass
        return w


    def _make_threshold_tab(self):
        """Stage 3 tab 4: calculate thresholds and generate combined-index threshold maps."""
        w = QWidget(); root = QVBoxLayout(w)

        srcbox = CollapsibleBox('Combined Index Raster', checked=True)
        grid = QGridLayout()
        self.cmb_combined = QComboBox()
        grid.addWidget(QLabel('Combined index raster:'), 0, 0)
        grid.addWidget(self.cmb_combined, 0, 1)
        refresh = QPushButton('Refresh raster list')
        refresh.clicked.connect(self._refresh_layers)
        load = QPushButton('Load / Update Histogram')
        load.clicked.connect(self._load_and_plot)
        grid.addWidget(refresh, 0, 2)
        grid.addWidget(load, 0, 3)
        note = QLabel('Select a generated RRAI, HRAI or HRI raster from the combined-map outputs.')
        note.setWordWrap(True); grid.addWidget(note, 1, 0, 1, 4)
        srcbox.setContentLayout(grid)
        root.addWidget(srcbox)

        thbox = CollapsibleBox('Threshold Parameters', checked=True)
        tgrid = QGridLayout()
        self.calc_mode = QComboBox(); self.calc_mode.addItems(['Percentile', 'Mean + standard deviation'])
        self.high_pct = self._percent_spin(85.0); self.vhigh_pct = self._percent_spin(95.0)
        self.high_sigma = QDoubleSpinBox(); self.high_sigma.setRange(0, 10); self.high_sigma.setDecimals(2); self.high_sigma.setSingleStep(0.25); self.high_sigma.setValue(1.0)
        self.vhigh_sigma = QDoubleSpinBox(); self.vhigh_sigma.setRange(0, 10); self.vhigh_sigma.setDecimals(2); self.vhigh_sigma.setSingleStep(0.25); self.vhigh_sigma.setValue(2.0)
        self.high_val = self._value_spin(); self.vhigh_val = self._value_spin()
        tgrid.addWidget(QLabel('Calculation mode:'), 0, 0); tgrid.addWidget(self.calc_mode, 0, 1, 1, 3)
        tgrid.addWidget(QLabel('High percentile:'), 1, 0); tgrid.addWidget(self.high_pct, 1, 1)
        tgrid.addWidget(QLabel('Very High percentile:'), 1, 2); tgrid.addWidget(self.vhigh_pct, 1, 3)
        tgrid.addWidget(QLabel('High sigma:'), 2, 0); tgrid.addWidget(self.high_sigma, 2, 1)
        tgrid.addWidget(QLabel('Very High sigma:'), 2, 2); tgrid.addWidget(self.vhigh_sigma, 2, 3)
        calc = QPushButton('Calculate Threshold Values')
        calc.clicked.connect(self._calculate_thresholds)
        tgrid.addWidget(calc, 3, 0, 1, 4)
        tgrid.addWidget(QLabel('High threshold:'), 4, 0); tgrid.addWidget(self.high_val, 4, 1)
        tgrid.addWidget(QLabel('Very High threshold:'), 4, 2); tgrid.addWidget(self.vhigh_val, 4, 3)
        note = QLabel('Use percentile or standard-deviation logic to define High and Very High classes for the selected combined index.')
        note.setWordWrap(True); tgrid.addWidget(note, 5, 0, 1, 4)
        thbox.setContentLayout(tgrid)
        root.addWidget(thbox)

        histbox = CollapsibleBox('Histogram Review', checked=True)
        hroot = QVBoxLayout()
        opts = QHBoxLayout()
        self.chk_s3_show_raw = QCheckBox('Raw values'); self.chk_s3_show_raw.setChecked(True)
        self.chk_s3_show_analysis = QCheckBox('Analysis values'); self.chk_s3_show_analysis.setChecked(True)
        self.chk_s3_removed = QCheckBox('Removed below High')
        self.chk_s3_high = QCheckBox('High pass')
        self.chk_s3_very = QCheckBox('Very High pass')
        self.chk_s3_mean = QCheckBox('Mean'); self.chk_s3_mean.setChecked(True)
        self.chk_s3_median = QCheckBox('Median'); self.chk_s3_median.setChecked(True)
        self.chk_s3_std = QCheckBox('±1 std')
        self.chk_s3_percentiles = QCheckBox('Percentiles')
        for cb in [self.chk_s3_show_raw, self.chk_s3_show_analysis, self.chk_s3_removed, self.chk_s3_high, self.chk_s3_very, self.chk_s3_mean, self.chk_s3_median, self.chk_s3_std, self.chk_s3_percentiles]:
            cb.toggled.connect(self._plot_histogram)
            opts.addWidget(cb)
        opts.addStretch(1)
        hroot.addLayout(opts)
        axis = QHBoxLayout()
        self.chk_s3_manual_axis = QCheckBox('Manual X range')
        self.s3_axis_min = QLineEdit(); self.s3_axis_min.setPlaceholderText('min')
        self.s3_axis_max = QLineEdit(); self.s3_axis_max.setPlaceholderText('max')
        redraw = QPushButton('Redraw')
        redraw.clicked.connect(self._plot_histogram)
        axis.addWidget(self.chk_s3_manual_axis); axis.addWidget(self.s3_axis_min); axis.addWidget(self.s3_axis_max); axis.addWidget(redraw); axis.addStretch(1)
        hroot.addLayout(axis)
        if HAS_MPL:
            self.fig = Figure(figsize=(7, 4), tight_layout=True)
            self.canvas = FigureCanvas(self.fig)
            hroot.addWidget(self.canvas, 1)
        else:
            hroot.addWidget(QLabel('Matplotlib is required for histogram review.'), 1)
        self.show_summary_btn = QPushButton('Show / Hide Summary Logs')
        self.show_summary_btn.setCheckable(True)
        self.show_summary_btn.setChecked(False)
        self.threshold_summary = QPlainTextEdit(); self.threshold_summary.setReadOnly(True); self.threshold_summary.setVisible(False)
        self.show_summary_btn.toggled.connect(self.threshold_summary.setVisible)
        hroot.addWidget(self.show_summary_btn)
        hroot.addWidget(self.threshold_summary)
        histbox.setContentLayout(hroot)
        root.addWidget(histbox, 1)

        self.threshold_progress = QProgressBar(); self.threshold_progress.setVisible(False)
        root.addWidget(self.threshold_progress)
        self.gen_threshold_map_btn = QPushButton('Generate Threshold Maps')
        self.gen_threshold_map_btn.setMinimumHeight(38)
        self.gen_threshold_map_btn.clicked.connect(self._generate_threshold_map)
        root.addWidget(self.gen_threshold_map_btn)
        return w

    def _make_extraction_tab(self):
        """Stage 3 tab 5: extract anomaly polygons from selected combined indices."""
        w = QWidget(); root = QVBoxLayout(w)

        ibox = CollapsibleBox('Extraction Inputs', checked=True)
        igrid = QGridLayout()
        self.extraction_index = QComboBox()
        self.extraction_index.addItems(['RRAI-U', 'HRAI-KU', 'HRI', 'RRAI-U + HRAI-KU', 'RRAI-U + HRAI-KU + HRI'])
        self.extract_level = QComboBox(); self.extract_level.addItems(['High', 'Very High', 'High and Very High'])
        igrid.addWidget(QLabel('Index to extract:'), 0, 0); igrid.addWidget(self.extraction_index, 0, 1)
        igrid.addWidget(QLabel('Threshold class:'), 1, 0); igrid.addWidget(self.extract_level, 1, 1)
        note = QLabel('Choose which generated combined index outputs should be converted into anomaly polygons.')
        note.setWordWrap(True); igrid.addWidget(note, 2, 0, 1, 2)
        ibox.setContentLayout(igrid)
        root.addWidget(ibox)

        fbox = CollapsibleBox('Polygon Filtering', checked=True)
        fgrid = QGridLayout()
        self.polygon_method = QComboBox(); self.polygon_method.addItems(['Binary mask polygonise', 'Class raster polygonise'])
        self.min_area = QDoubleSpinBox(); self.min_area.setRange(0, 1e9); self.min_area.setDecimals(4); self.min_area.setSingleStep(0.1); self.min_area.setValue(0.0)
        self.use_min_pixels = QCheckBox('Apply minimum pixel count')
        self.min_pixels = QSpinBox(); self.min_pixels.setRange(0, 100000000); self.min_pixels.setValue(0)
        fgrid.addWidget(QLabel('Polygon method:'), 0, 0); fgrid.addWidget(self.polygon_method, 0, 1)
        fgrid.addWidget(QLabel('Minimum area km²:'), 1, 0); fgrid.addWidget(self.min_area, 1, 1)
        fgrid.addWidget(self.use_min_pixels, 2, 0); fgrid.addWidget(self.min_pixels, 2, 1)
        note = QLabel('Filter small polygons after thresholding to reduce isolated-cell artefacts.')
        note.setWordWrap(True); fgrid.addWidget(note, 3, 0, 1, 2)
        fbox.setContentLayout(fgrid)
        root.addWidget(fbox)

        tbox = CollapsibleBox('Trending / Connectivity', checked=False)
        tgrid = QGridLayout()
        self.enable_trend = QCheckBox('Apply trend/connectivity filter')
        self.trend_method = QComboBox(); self.trend_method.addItems(['Majority filter', 'Closing', 'Dilation'])
        self.trend_kernel = QComboBox(); self.trend_kernel.addItems(['3 x 3', '5 x 5', '7 x 7'])
        self.trend_iterations = QSpinBox(); self.trend_iterations.setRange(1, 10); self.trend_iterations.setValue(1)
        self.majority_fraction = QDoubleSpinBox(); self.majority_fraction.setRange(0.0, 1.0); self.majority_fraction.setDecimals(2); self.majority_fraction.setSingleStep(0.05); self.majority_fraction.setValue(0.50)
        tgrid.addWidget(self.enable_trend, 0, 0, 1, 2)
        tgrid.addWidget(QLabel('Method:'), 1, 0); tgrid.addWidget(self.trend_method, 1, 1)
        tgrid.addWidget(QLabel('Kernel:'), 2, 0); tgrid.addWidget(self.trend_kernel, 2, 1)
        tgrid.addWidget(QLabel('Iterations:'), 3, 0); tgrid.addWidget(self.trend_iterations, 3, 1)
        tgrid.addWidget(QLabel('Majority fraction:'), 4, 0); tgrid.addWidget(self.majority_fraction, 4, 1)
        note = QLabel('Optional spatial connectivity filtering can merge or remove small isolated threshold patches before polygon extraction.')
        note.setWordWrap(True); tgrid.addWidget(note, 5, 0, 1, 2)
        tbox.setContentLayout(tgrid)
        root.addWidget(tbox)

        sbox = CollapsibleBox('Polygon Smoothing', checked=False)
        sgrid = QGridLayout()
        self.smooth_polygons = QCheckBox('Smooth output polygons')
        self.smooth_iterations = QSpinBox(); self.smooth_iterations.setRange(1, 20); self.smooth_iterations.setValue(2)
        self.smooth_offset = QDoubleSpinBox(); self.smooth_offset.setRange(0.0, 1.0); self.smooth_offset.setDecimals(2); self.smooth_offset.setSingleStep(0.05); self.smooth_offset.setValue(0.25)
        self.smooth_max_angle = QDoubleSpinBox(); self.smooth_max_angle.setRange(1.0, 180.0); self.smooth_max_angle.setDecimals(1); self.smooth_max_angle.setValue(180.0)
        sgrid.addWidget(self.smooth_polygons, 0, 0, 1, 2)
        sgrid.addWidget(QLabel('Iterations:'), 1, 0); sgrid.addWidget(self.smooth_iterations, 1, 1)
        sgrid.addWidget(QLabel('Offset:'), 2, 0); sgrid.addWidget(self.smooth_offset, 2, 1)
        sgrid.addWidget(QLabel('Max angle:'), 3, 0); sgrid.addWidget(self.smooth_max_angle, 3, 1)
        sbox.setContentLayout(sgrid)
        root.addWidget(sbox)

        self.polygon_progress = QProgressBar(); self.polygon_progress.setVisible(False)
        root.addWidget(self.polygon_progress)
        self.extract_polygons_btn = QPushButton('Extract Anomaly Polygons')
        self.extract_polygons_btn.setMinimumHeight(38)
        self.extract_polygons_btn.clicked.connect(self._extract_polygons)
        root.addWidget(self.extract_polygons_btn)
        self.extraction_summary = QPlainTextEdit(); self.extraction_summary.setReadOnly(True)
        root.addWidget(self.extraction_summary, 1)
        return w

    def _update_combination_input_labels(self):
        """Update combination input descriptions without exposing long folder paths.

        The processing folder is already controlled on the main page. These labels
        should only remind the user which raster products feed each combined map.
        """
        if hasattr(self, 'rrai_hrai_inputs_label'):
            self.rrai_hrai_inputs_label.setText(
                'Input rasters: U_Th_norm, U_K_norm, K_U_norm, K_Th_norm and K_Th_inverse_norm.'
            )
        if hasattr(self, 'hri_inputs_label'):
            self.hri_inputs_label.setText(
                'Input rasters: U_Th_zlog, U_K_zlog and Th_K_zlog. HRI uses scalar coefficients, not normalised weights.'
            )

    def _percent_spin(self, val):
        s = QDoubleSpinBox()
        s.setRange(0, 100)
        s.setDecimals(2)
        s.setSingleStep(1)
        s.setValue(val)
        return s

    def _value_spin(self):
        s = QDoubleSpinBox()
        s.setRange(0.0, 1e12)
        s.setDecimals(8)
        s.setSingleStep(0.1)
        return s

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select radiometric processing folder', self.processing_folder)
        if folder:
            self.processing_folder = folder
            if hasattr(self, 'folder_label'):
                self.folder_label.setText(folder)
            self._refresh_layers()
            self._auto_from_processing_folder()
            self._update_combination_input_labels()

    def _combined_dir(self):
        return ensure_dir(os.path.join(self.processing_folder, '07_Combined_Maps')) if self.processing_folder else ''

    def _norm_dir(self):
        return ensure_dir(os.path.join(self.processing_folder, '04_Normalised_Ratio_Maps')) if self.processing_folder else ''

    def _log_dir(self):
        return ensure_dir(os.path.join(self.processing_folder, '04_Log_Ratio_Maps')) if self.processing_folder else ''

    def _zlog_dir(self):
        return ensure_dir(os.path.join(self.processing_folder, '04_ZLog_Ratio_Maps')) if self.processing_folder else ''


    def _pca_dir(self):
        return ensure_dir(os.path.join(self.processing_folder, '10_PCA_Multivariate_Analysis')) if self.processing_folder else ''

    def _threshold_base_dir(self):
        return ensure_dir(os.path.join(self.processing_folder, '08_Combined_Threshold_Maps')) if self.processing_folder else ''

    def _final_base_dir(self):
        return ensure_dir(os.path.join(self.processing_folder, '09_Combined_Map_Anomaly_Extraction')) if self.processing_folder else ''

    def _combined_subdir_name(self, index_key_or_path=None):
        txt = str(index_key_or_path or getattr(self, '_combined_path', '') or '').lower()
        if 'hri' in txt:
            return 'HRI'
        if 'hrai' in txt:
            return 'HRAI'
        return 'RRAI'

    def _threshold_dir_for(self, index_key_or_path=None):
        return ensure_dir(os.path.join(self._threshold_base_dir(), self._combined_subdir_name(index_key_or_path)))

    def _final_dir_for(self, index_key_or_path=None):
        return ensure_dir(os.path.join(self._final_base_dir(), self._combined_subdir_name(index_key_or_path)))

    def _final_dir(self):
        return self._final_base_dir()

    def _latest_matching_path(self, folder, stem, ext='.tif'):
        """Return newest matching output path for stem, including _vNN variants."""
        if not folder:
            return ''
        patterns = [os.path.join(folder, stem + ext), os.path.join(folder, stem + '_v*' + ext)]
        matches = []
        for pat in patterns:
            matches.extend(glob.glob(pat))
        matches = [m for m in matches if os.path.exists(m)]
        if not matches:
            return os.path.join(folder, stem + ext)
        return max(matches, key=lambda x: os.path.getmtime(x))

    def _write_raster_versioned(self, path, arr, profile, gdal_type=gdal.GDT_Float32):
        """Write a raster, creating a versioned filename if the target is locked/existing."""
        out_path = make_available_path(path)
        write_raster(out_path, arr, profile, gdal_type)
        return out_path

    def _refresh_layers(self):
        """Refresh raster combo boxes used by Stage 3 and embedded crossplots."""
        layers = [l for l in QgsProject.instance().mapLayers().values() if isinstance(l, QgsRasterLayer)]
        items = [(l.name(), l.source()) for l in layers]

        ratio_combos = [getattr(self, 'cmb_uth', None), getattr(self, 'cmb_uk', None), getattr(self, 'cmb_ku', None), getattr(self, 'cmb_kth', None)]
        for cmb in ratio_combos:
            if cmb is None:
                continue
            current = cmb.currentData()
            cmb.blockSignals(True)
            cmb.clear()
            for name, src in items:
                cmb.addItem(name, src)
            cmb.blockSignals(False)
            if current:
                for i in range(cmb.count()):
                    if os.path.normcase(str(cmb.itemData(i))) == os.path.normcase(str(current)):
                        cmb.setCurrentIndex(i)
                        break

        cmb = getattr(self, 'cmb_combined', None)
        if cmb is not None:
            current = cmb.currentData()
            cmb.blockSignals(True)
            cmb.clear()
            if self.processing_folder:
                comb_root = os.path.normcase(os.path.join(self.processing_folder, '07_Combined_Maps'))
                for name, src in items:
                    nsrc = os.path.normcase(str(src))
                    lname = name.lower()
                    if nsrc.startswith(comb_root) and any(k in lname for k in ('rrai', 'hrai', 'hri', 'combined')):
                        cmb.addItem(name, src)
                for stem in ('RRAI_U_Uranium_Enrichment_Index', 'HRAI_KU_Hybrid_Alteration_Source_Index', 'HRI_Hydrocarbon_Radiometric_Index', 'Combined_Radiometric_Index'):
                    p = self._latest_matching_path(os.path.join(self.processing_folder, '07_Combined_Maps'), stem)
                    if os.path.exists(p):
                        cmb.addItem(os.path.basename(p), p)
            cmb.blockSignals(False)
            seen = set()
            # remove duplicate paths from the combo
            for i in range(cmb.count() - 1, -1, -1):
                key = os.path.normcase(str(cmb.itemData(i)))
                if key in seen:
                    cmb.removeItem(i)
                else:
                    seen.add(key)
            if current:
                for i in range(cmb.count()):
                    if os.path.normcase(str(cmb.itemData(i))) == os.path.normcase(str(current)):
                        cmb.setCurrentIndex(i)
                        break

        self._auto_select(getattr(self, 'cmb_uth', None), ['u_th', 'uth', 'u/th'])
        self._auto_select(getattr(self, 'cmb_uk', None), ['u_k', 'uk', 'u/k'])
        self._auto_select(getattr(self, 'cmb_ku', None), ['k_u', 'ku', 'k/u'])
        self._auto_select(getattr(self, 'cmb_kth', None), ['k_th', 'kth', 'k/th'])
        if hasattr(self, '_refresh_crossplot_lists'):
            try:
                self._refresh_crossplot_lists()
            except Exception:
                pass
        try:
            self._update_combination_input_labels()
        except Exception:
            pass

    def _auto_select(self, combo, needles):
        if combo is None:
            return
        for i in range(combo.count()):
            txt = (combo.itemText(i) + ' ' + str(combo.itemData(i))).lower().replace('\\', '/').replace('-', '_')
            if any(n in txt for n in needles):
                combo.setCurrentIndex(i)
                return

    def _auto_from_processing_folder(self):
        """Add expected plugin output rasters from the active processing folder."""
        if not self.processing_folder:
            return
        ratio_dir = os.path.join(self.processing_folder, '03_Ratio_Maps')
        pairs = [
            (getattr(self, 'cmb_uth', None), ['U_Th_ratio.tif', 'UTh_ratio.tif']),
            (getattr(self, 'cmb_uk', None), ['U_K_ratio.tif', 'UK_ratio.tif']),
            (getattr(self, 'cmb_ku', None), ['K_U_ratio.tif', 'KU_ratio.tif']),
            (getattr(self, 'cmb_kth', None), ['K_Th_ratio.tif', 'KTH_ratio.tif']),
        ]
        for cmb, names in pairs:
            if cmb is None:
                continue
            for name in names:
                p = os.path.join(ratio_dir, name)
                if os.path.exists(p):
                    self._ensure_combo_path(cmb, p)
                    break
        if hasattr(self, 'cmb_combined'):
            for stem in ('RRAI_U_Uranium_Enrichment_Index', 'HRAI_KU_Hybrid_Alteration_Source_Index', 'HRI_Hydrocarbon_Radiometric_Index', 'Combined_Radiometric_Index'):
                cp = self._latest_matching_path(os.path.join(self.processing_folder, '07_Combined_Maps'), stem)
                if os.path.exists(cp):
                    self._ensure_combo_path(self.cmb_combined, cp)
        if hasattr(self, '_refresh_crossplot_lists'):
            self._refresh_crossplot_lists()
        self._update_combination_input_labels()

    def _ensure_combo_path(self,cmb,path):
        for i in range(cmb.count()):
            if os.path.normcase(str(cmb.itemData(i)))==os.path.normcase(path): cmb.setCurrentIndex(i); return
        cmb.addItem(os.path.basename(path),path); cmb.setCurrentIndex(cmb.count()-1)

    # ------------------------- normalisation

    def _apply_weight_preset(self, index_name='rrai'):
        """Apply weight presets to RRAI/HRAI or scalar presets to HRI."""
        if index_name == 'hrai':
            combo = self.hrai_weight_preset
            spins = (self.hrai_w_uth, self.hrai_w_second, self.hrai_w_kth)
            presets = {0: (0.25, 0.6, 0.15), 1: (1/3, 1/3, 1/3), 2: (0.6, 0.25, 0.15), 3: (0.4, 0.4, 0.2), 4: (0.3, 0.3, 0.4)}
        elif index_name == 'hri':
            combo = self.hri_weight_preset
            spins = (self.hri_w_uth, self.hri_w_second, self.hri_w_kth)
            presets = {0: (1.0, 1.0, 1.0), 1: (2.0, 1.0, 1.0), 2: (1.0, 2.0, 1.0), 3: (1.0, 1.0, 2.0)}
        else:
            combo = self.rrai_weight_preset
            spins = (self.rrai_w_uth, self.rrai_w_second, self.rrai_w_kth)
            presets = {0: (0.4, 0.4, 0.2), 1: (1/3, 1/3, 1/3), 2: (0.6, 0.25, 0.15), 3: (0.25, 0.6, 0.15), 4: (0.3, 0.3, 0.4)}
        idx = combo.currentIndex()
        if idx not in presets:
            return
        for spin, val in zip(spins, presets[idx]):
            spin.blockSignals(True)
            spin.setValue(float(val))
            spin.blockSignals(False)

    def _update_normalisation_controls(self):
        """Enable normalisation method controls only when normalised maps are selected."""
        enabled = bool(getattr(self, 'gen_norm_maps', None) and self.gen_norm_maps.isChecked())
        for widget_name in ('norm_method', 'norm_low_pct', 'norm_high_pct'):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _normalise_ratio(self, arr):
        """Return a favourable 0-1 normalised raster using the selected method."""
        a = np.array(arr, copy=True).astype(np.float64)
        valid = np.isfinite(a)
        vals = a[valid]
        if vals.size == 0:
            return np.full(a.shape, np.nan, dtype=np.float64)

        method = self.norm_method.currentText().lower()
        lo_p = float(self.norm_low_pct.value())
        hi_p = float(self.norm_high_pct.value())
        if hi_p <= lo_p:
            hi_p = min(100.0, lo_p + 1.0)

        out = np.full(a.shape, np.nan, dtype=np.float64)

        if method.startswith('min-max extremes'):
            lo = float(np.nanpercentile(vals, lo_p)); hi = float(np.nanpercentile(vals, hi_p))
            clipped = np.clip(a, lo, hi)
            if math.isclose(hi, lo):
                out[valid] = 0.0
            else:
                out[valid] = (clipped[valid] - lo) / (hi - lo)
            return np.clip(out, 0.0, 1.0)

        if method.startswith('percentile'):
            # Empirical CDF / quantile rank. This is robust for skewed ratio distributions.
            sorted_vals = np.sort(vals)
            ranks = np.searchsorted(sorted_vals, a[valid], side='right').astype(np.float64)
            out[valid] = ranks / float(sorted_vals.size)
            return np.clip(out, 0.0, 1.0)

        if method.startswith('z-score'):
            mu = float(np.nanmean(vals)); sd = float(np.nanstd(vals))
            if sd <= 0 or not np.isfinite(sd):
                out[valid] = 0.5
            else:
                z = (a[valid] - mu) / sd
                out[valid] = (np.clip(z, -3.0, 3.0) + 3.0) / 6.0
            return np.clip(out, 0.0, 1.0)

        if method.startswith('robust'):
            med = float(np.nanmedian(vals))
            mad = float(np.nanmedian(np.abs(vals - med)))
            scale = 1.4826 * mad
            if scale <= 0 or not np.isfinite(scale):
                q1 = float(np.nanpercentile(vals, 25)); q3 = float(np.nanpercentile(vals, 75))
                scale = (q3 - q1) / 1.349 if q3 > q1 else 0.0
            if scale <= 0 or not np.isfinite(scale):
                out[valid] = 0.5
            else:
                rz = (a[valid] - med) / scale
                out[valid] = (np.clip(rz, -3.0, 3.0) + 3.0) / 6.0
            return np.clip(out, 0.0, 1.0)

        # Default Min-Max.
        mn = float(np.nanmin(vals)); mx = float(np.nanmax(vals))
        if math.isclose(mx, mn):
            out[valid] = 0.0
        else:
            out[valid] = (a[valid] - mn) / (mx - mn)
        return np.clip(out, 0.0, 1.0)

    def _combine_normalised(self, n1, n2, inv, w1, w2, w3, method_text='Weighted mean'):
        """Combine three favourable 0-1 rasters using the selected index-specific method."""
        method = (method_text or 'Weighted mean').lower()
        stack = np.stack([n1, n2, inv], axis=0)
        valid = np.isfinite(n1) & np.isfinite(n2) & np.isfinite(inv)
        out = np.full(n1.shape, np.nan, dtype=np.float64)

        if method.startswith('arithmetic'):
            out[valid] = np.nanmean(stack[:, valid], axis=0)
        elif method.startswith('geometric'):
            eps = 1e-9
            out[valid] = np.exp((np.log(np.clip(n1[valid], eps, 1.0)) + np.log(np.clip(n2[valid], eps, 1.0)) + np.log(np.clip(inv[valid], eps, 1.0))) / 3.0)
        elif method.startswith('weighted geometric') or method.startswith('product'):
            eps = 1e-9
            out[valid] = np.exp((w1*np.log(np.clip(n1[valid], eps, 1.0))) + (w2*np.log(np.clip(n2[valid], eps, 1.0))) + (w3*np.log(np.clip(inv[valid], eps, 1.0))))
        elif method.startswith('minimum'):
            out[valid] = np.nanmin(stack[:, valid], axis=0)
        elif method.startswith('maximum'):
            out[valid] = np.nanmax(stack[:, valid], axis=0)
        else:
            out[valid] = (w1 * n1[valid]) + (w2 * n2[valid]) + (w3 * inv[valid])
        return np.clip(out, 0.0, 1.0)

    def _normalised_weights(self, a, b, c, label):
        s = float(a) + float(b) + float(c)
        if s <= 0:
            raise RuntimeError(f'{label} weights must sum to greater than zero.')
        return float(a)/s, float(b)/s, float(c)/s

    def _normalise_inputs(self):
        """Read selected ratio rasters and return normalised components plus profile/paths."""
        if not self.processing_folder:
            raise RuntimeError('Set the processing folder first.')
        paths=[self.cmb_uth.currentData(),self.cmb_uk.currentData(),self.cmb_ku.currentData(),self.cmb_kth.currentData()]
        if not all(paths):
            raise RuntimeError('Select U/Th, U/K, K/U and K/Th ratio rasters.')
        UTh,profile=read_raster(paths[0]); UK,_=read_raster(paths[1]); KU,_=read_raster(paths[2]); KTh,_=read_raster(paths[3])
        if UTh.shape!=UK.shape or UTh.shape!=KU.shape or UTh.shape!=KTh.shape:
            raise RuntimeError('Input ratio rasters must have the same dimensions.')
        n_uth=self._normalise_ratio(UTh)
        n_uk=self._normalise_ratio(UK)
        n_ku=self._normalise_ratio(KU)
        n_kth=self._normalise_ratio(KTh)
        inv_kth=1.0-n_kth; inv_kth[~np.isfinite(n_kth)]=np.nan
        return paths, profile, n_uth, n_uk, n_ku, n_kth, inv_kth

    def _write_normalised_outputs(self, profile, n_uth, n_uk, n_ku, n_kth, inv_kth, load=True):
        """Write normalised ratio rasters to 04_Normalised_Ratio_Maps."""
        norm_dir=self._norm_dir()
        outputs=[]
        for name,arr in [('U_Th_norm',n_uth),('U_K_norm',n_uk),('K_U_norm',n_ku),('K_Th_norm',n_kth),('K_Th_inverse_norm',inv_kth)]:
            p=os.path.join(norm_dir,name+'.tif')
            p=self._write_raster_versioned(p,arr,profile,gdal.GDT_Float32)
            if load:
                load_raster_pseudocolor_quantile(p,name)
            outputs.append(p)
        return outputs

    def _generate_normalised_maps(self):
        """Generate only the normalised ratio maps, without generating combined indices."""
        try:
            paths, profile, n_uth, n_uk, n_ku, n_kth, inv_kth = self._normalise_inputs()
            outputs = self._write_normalised_outputs(profile, n_uth, n_uk, n_ku, n_kth, inv_kth, load=True)
            msg = ('Generated normalised ratio maps:\n' + '\n'.join(outputs) +
                   '\n\nK/Th inverse component is 1 - norm(K/Th), not 1/(K/Th).')
            if hasattr(self, 'norm_summary'):
                self.norm_summary.setPlainText(msg)
            append_session_log(self.processing_folder, 'Stage 3 - Generate Normalised Ratio Maps', inputs={'U/Th': paths[0], 'U/K': paths[1], 'K/U': paths[2], 'K/Th': paths[3]}, outputs=outputs, parameters={'Normalisation': self.norm_method.currentText()})
            self._refresh_crossplot_lists()
            QMessageBox.information(self,'Normalised maps generated','Normalised ratio maps were generated in 04_Normalised_Ratio_Maps.')
        except Exception as e:
            QMessageBox.critical(self,'Normalised map generation failed',str(e))

    def _generate_derived_ratio_maps(self):
        """Generate selected derived ratio products: normalised, log, and/or ZLog maps."""
        try:
            if not (self.gen_norm_maps.isChecked() or self.gen_log_maps.isChecked() or self.gen_zlog_maps.isChecked()):
                raise RuntimeError('Select at least one derived product to generate.')
            messages = []

            if self.gen_norm_maps.isChecked():
                paths, profile, n_uth, n_uk, n_ku, n_kth, inv_kth = self._normalise_inputs()
                outputs = self._write_normalised_outputs(profile, n_uth, n_uk, n_ku, n_kth, inv_kth, load=True)
                messages.append('Normalised ratio maps saved to 04_Normalised_Ratio_Maps:\n' + '\n'.join(outputs))
                append_session_log(self.processing_folder, 'Stage 3 - Generate Normalised Ratio Maps', inputs={'U/Th': paths[0], 'U/K': paths[1], 'K/U': paths[2], 'K/Th': paths[3]}, outputs=outputs, parameters={'Normalisation': self.norm_method.currentText()})

            if self.gen_log_maps.isChecked():
                paths, profile, l_uth, l_uk, l_ku, l_kth, l_thk = self._log_inputs()
                outputs = self._write_log_outputs(profile, l_uth, l_uk, l_ku, l_kth, l_thk, load=True)
                messages.append('Log ratio maps saved to 04_Log_Ratio_Maps:\n' + '\n'.join(outputs))
                append_session_log(self.processing_folder, 'Stage 3 - Generate Log Ratio Maps', inputs={'U/Th': paths[0], 'U/K': paths[1], 'K/U': paths[2], 'K/Th': paths[3]}, outputs=outputs, parameters={'Transform': 'natural log'})

            if self.gen_zlog_maps.isChecked():
                paths, profile, z_uth, z_uk, z_ku, z_kth, z_thk = self._zlog_inputs()
                outputs = self._write_zlog_outputs(profile, z_uth, z_uk, z_ku, z_kth, z_thk, load=True)
                messages.append('ZLog ratio maps saved to 04_ZLog_Ratio_Maps:\n' + '\n'.join(outputs))
                append_session_log(self.processing_folder, 'Stage 3 - Generate ZLog Ratio Maps', inputs={'U/Th': paths[0], 'U/K': paths[1], 'K/U': paths[2], 'K/Th': paths[3]}, outputs=outputs, parameters={'Transform': 'log + z-score'})

            msg = '\n\n'.join(messages)
            msg += '\n\nHRI uses s1·z(ln(U/Th)) + s2·z(ln(U/K)) - s3·z(ln(Th/K)). RRAI/HRAI use normalised ratio maps and 1 - norm(K/Th).'
            if hasattr(self, 'norm_summary'):
                self.norm_summary.setPlainText(msg)
            self._refresh_crossplot_lists()
            QMessageBox.information(self, 'Derived ratio maps generated', 'Selected derived ratio maps were generated.')
        except Exception as e:
            QMessageBox.critical(self, 'Derived ratio map generation failed', str(e))

    def _crossplot_folder(self):
        if not self.processing_folder:
            return ''
        src = self.crossplot_source.currentText() if hasattr(self, 'crossplot_source') else 'Normalised Ratio Maps'
        if src.startswith('Raw'):
            return os.path.join(self.processing_folder, '03_Ratio_Maps')
        if src.startswith('Combined'):
            return os.path.join(self.processing_folder, '07_Combined_Maps')
        if src.startswith('ZLog'):
            return os.path.join(self.processing_folder, '04_ZLog_Ratio_Maps')
        if src.startswith('Log'):
            return os.path.join(self.processing_folder, '04_Log_Ratio_Maps')
        return os.path.join(self.processing_folder, '04_Normalised_Ratio_Maps')

    def _refresh_crossplot_lists(self):
        """Populate crossplot raster selectors from the selected project output folder."""
        if not hasattr(self, 'crossplot_x'):
            return
        current_x=self.crossplot_x.currentData(); current_y=self.crossplot_y.currentData(); current_c=self.crossplot_colour.currentData()
        for cmb in (self.crossplot_x, self.crossplot_y, self.crossplot_colour):
            cmb.blockSignals(True); cmb.clear()
        self.crossplot_colour.addItem('(none)', None)
        folder=self._crossplot_folder()
        rasters=[]
        if folder and os.path.isdir(folder):
            for root, dirs, files in os.walk(folder):
                for fn in sorted(files):
                    if fn.lower().endswith(('.tif','.tiff')):
                        p=os.path.join(root,fn)
                        label=os.path.relpath(p,folder).replace('\\','/')
                        rasters.append((label,p))
        for label,p in rasters:
            self.crossplot_x.addItem(label,p); self.crossplot_y.addItem(label,p); self.crossplot_colour.addItem(label,p)
        def restore(cmb, val):
            if val:
                for i in range(cmb.count()):
                    if os.path.normcase(str(cmb.itemData(i)))==os.path.normcase(str(val)):
                        cmb.setCurrentIndex(i); return
        restore(self.crossplot_x,current_x); restore(self.crossplot_y,current_y); restore(self.crossplot_colour,current_c)
        for cmb in (self.crossplot_x, self.crossplot_y, self.crossplot_colour):
            cmb.blockSignals(False)

    def _create_selected_crossplot(self):
        """Create a generic raster-vs-raster crossplot from the selected source folder."""
        if not HAS_MPL:
            QMessageBox.warning(self, 'Crossplot', 'Matplotlib is required for crossplot analysis.')
            return
        try:
            x_path=self.crossplot_x.currentData(); y_path=self.crossplot_y.currentData(); c_path=self.crossplot_colour.currentData()
            if not x_path or not y_path:
                raise RuntimeError('Select X and Y rasters for the crossplot.')
            x_arr,_=read_raster(x_path); y_arr,_=read_raster(y_path)
            if x_arr.shape!=y_arr.shape:
                raise RuntimeError('X and Y rasters must have the same dimensions.')
            valid=np.isfinite(x_arr)&np.isfinite(y_arr)
            x=x_arr; y=y_arr
            if self.crossplot_log_x.isChecked():
                valid &= x_arr>0
                x=np.full_like(x_arr,np.nan,dtype=np.float64); x[valid]=np.log(x_arr[valid])
            if self.crossplot_log_y.isChecked():
                valid &= y_arr>0
                y=np.full_like(y_arr,np.nan,dtype=np.float64); y[valid]=np.log(y_arr[valid])
            colour=None; colour_label=None
            if c_path:
                c_arr,_=read_raster(c_path)
                if c_arr.shape!=x_arr.shape:
                    raise RuntimeError('Colour raster must have the same dimensions as X and Y.')
                valid &= np.isfinite(c_arr)
                colour=c_arr
                colour_label=os.path.basename(c_path)
            flat_idx=np.flatnonzero(valid.ravel())
            if flat_idx.size==0:
                raise RuntimeError('No valid overlapping cells for crossplot.')
            sample_size=min(int(self.crossplot_sample.value()), flat_idx.size)
            if flat_idx.size>sample_size:
                rng=np.random.default_rng(42); flat_idx=rng.choice(flat_idx, sample_size, replace=False)
            xv=x.ravel()[flat_idx]; yv=y.ravel()[flat_idx]
            self.crossplot_fig.clear(); ax=self.crossplot_fig.add_subplot(111)
            if colour is not None:
                cv=colour.ravel()[flat_idx]
                sc=ax.scatter(xv,yv,c=cv,cmap='viridis',s=8,alpha=0.65,edgecolors='none')
                cb=self.crossplot_fig.colorbar(sc, ax=ax); cb.set_label(colour_label)
            else:
                ax.scatter(xv,yv,s=8,alpha=0.55,edgecolors='none')
            ax.set_xlabel(os.path.basename(x_path) + (' (ln)' if self.crossplot_log_x.isChecked() else ''))
            ax.set_ylabel(os.path.basename(y_path) + (' (ln)' if self.crossplot_log_y.isChecked() else ''))
            ax.set_title(self.crossplot_source.currentText() + ' Crossplot')
            ax.grid(True, alpha=0.25)
            self.crossplot_canvas.draw_idle()
        except Exception as e:
            QMessageBox.critical(self,'Crossplot failed',str(e))


    def _log_ratio(self, arr, invert_for_th_over_k=False):
        """Return ln(arr) for positive ratio values.

        If invert_for_th_over_k=True, returns ln(Th/K) from K/Th without explicitly
        calculating a reciprocal raster, using ln(Th/K) = -ln(K/Th).
        """
        a = np.asarray(arr, dtype=np.float64)
        valid = np.isfinite(a) & (a > 0)
        out = np.full(a.shape, np.nan, dtype=np.float64)
        if not np.any(valid):
            return out
        logv = np.log(a[valid])
        if invert_for_th_over_k:
            logv = -logv
        out[valid] = logv
        return out

    def _log_inputs(self):
        """Read selected ratio rasters and return log-transformed components."""
        if not self.processing_folder:
            raise RuntimeError('Set the processing folder first.')
        paths=[self.cmb_uth.currentData(), self.cmb_uk.currentData(), self.cmb_ku.currentData(), self.cmb_kth.currentData()]
        if not all(paths):
            raise RuntimeError('Select U/Th, U/K, K/U and K/Th ratio rasters.')
        UTh, profile = read_raster(paths[0]); UK, _ = read_raster(paths[1]); KU, _ = read_raster(paths[2]); KTh, _ = read_raster(paths[3])
        if UTh.shape != UK.shape or UTh.shape != KU.shape or UTh.shape != KTh.shape:
            raise RuntimeError('Input ratio rasters must have the same dimensions.')
        l_uth = self._log_ratio(UTh)
        l_uk = self._log_ratio(UK)
        l_ku = self._log_ratio(KU)
        l_kth = self._log_ratio(KTh)
        l_thk = self._log_ratio(KTh, invert_for_th_over_k=True)
        return paths, profile, l_uth, l_uk, l_ku, l_kth, l_thk

    def _write_log_outputs(self, profile, l_uth, l_uk, l_ku, l_kth, l_thk, load=True):
        """Write ln(ratio) rasters to 04_Log_Ratio_Maps."""
        ldir = self._log_dir()
        outputs=[]
        for name, arr in [('U_Th_log', l_uth), ('U_K_log', l_uk), ('K_U_log', l_ku), ('K_Th_log', l_kth), ('Th_K_log', l_thk)]:
            p=os.path.join(ldir, name+'.tif')
            p=self._write_raster_versioned(p, arr, profile, gdal.GDT_Float32)
            if load:
                load_raster_pseudocolor_quantile(p, name)
            outputs.append(p)
        return outputs

    def _generate_log_maps(self):
        """Generate ln(ratio) rasters for distribution inspection and crossplotting."""
        try:
            paths, profile, l_uth, l_uk, l_ku, l_kth, l_thk = self._log_inputs()
            outputs = self._write_log_outputs(profile, l_uth, l_uk, l_ku, l_kth, l_thk, load=True)
            msg = 'Generated log ratio maps:\n' + '\n'.join(outputs)
            if hasattr(self, 'norm_summary'):
                self.norm_summary.setPlainText(msg)
            append_session_log(self.processing_folder, 'Stage 3 - Generate Log Ratio Maps', inputs={'U/Th': paths[0], 'U/K': paths[1], 'K/U': paths[2], 'K/Th': paths[3]}, outputs=outputs, parameters={'Transform': 'natural log'})
            self._refresh_crossplot_lists()
            QMessageBox.information(self, 'Log maps generated', 'Log ratio maps were generated in 04_Log_Ratio_Maps.')
        except Exception as e:
            QMessageBox.critical(self, 'Log map generation failed', str(e))

    def _zscore_log_ratio(self, arr, invert_for_th_over_k=False):
        """Return z(ln(arr)) for positive ratio values.

        If invert_for_th_over_k=True, this returns z(ln(Th/K)) from K/Th
        without explicitly creating a reciprocal raster, using ln(Th/K) = -ln(K/Th).
        """
        a = np.asarray(arr, dtype=np.float64)
        valid = np.isfinite(a) & (a > 0)
        out = np.full(a.shape, np.nan, dtype=np.float64)
        if not np.any(valid):
            return out
        logv = np.log(a[valid])
        if invert_for_th_over_k:
            logv = -logv
        mu = float(np.nanmean(logv))
        sd = float(np.nanstd(logv))
        if sd <= 0 or not np.isfinite(sd):
            out[valid] = 0.0
        else:
            out[valid] = (logv - mu) / sd
        return out

    def _zlog_inputs(self):
        """Read selected ratio rasters and return z-log components used by HRI."""
        if not self.processing_folder:
            raise RuntimeError('Set the processing folder first.')
        paths=[self.cmb_uth.currentData(), self.cmb_uk.currentData(), self.cmb_ku.currentData(), self.cmb_kth.currentData()]
        if not all(paths):
            raise RuntimeError('Select U/Th, U/K, K/U and K/Th ratio rasters.')
        UTh, profile = read_raster(paths[0]); UK, _ = read_raster(paths[1]); KU, _ = read_raster(paths[2]); KTh, _ = read_raster(paths[3])
        if UTh.shape != UK.shape or UTh.shape != KU.shape or UTh.shape != KTh.shape:
            raise RuntimeError('Input ratio rasters must have the same dimensions.')
        z_uth = self._zscore_log_ratio(UTh)
        z_uk = self._zscore_log_ratio(UK)
        z_ku = self._zscore_log_ratio(KU)
        z_kth = self._zscore_log_ratio(KTh)
        z_thk = self._zscore_log_ratio(KTh, invert_for_th_over_k=True)  # z(ln(Th/K)) from K/Th
        return paths, profile, z_uth, z_uk, z_ku, z_kth, z_thk

    def _write_zlog_outputs(self, profile, z_uth, z_uk, z_ku, z_kth, z_thk, load=True):
        """Write log + z-score transformed ratio rasters to 04_ZLog_Ratio_Maps."""
        zdir = self._zlog_dir()
        outputs=[]
        for name, arr in [('U_Th_zlog', z_uth), ('U_K_zlog', z_uk), ('K_U_zlog', z_ku), ('K_Th_zlog', z_kth), ('Th_K_zlog', z_thk)]:
            p=os.path.join(zdir, name+'.tif')
            p=self._write_raster_versioned(p, arr, profile, gdal.GDT_Float32)
            if load:
                load_raster_pseudocolor_quantile(p, name)
            outputs.append(p)
        return outputs

    def _generate_zlog_maps(self):
        """Generate z(ln(ratio)) rasters for HRI review and crossplotting."""
        try:
            paths, profile, z_uth, z_uk, z_ku, z_kth, z_thk = self._zlog_inputs()
            outputs = self._write_zlog_outputs(profile, z_uth, z_uk, z_ku, z_kth, z_thk, load=True)
            msg = ('Generated ZLog ratio maps for HRI:\n' + '\n'.join(outputs) +
                   '\n\nHRI uses s1·z(ln(U/Th)) + s2·z(ln(U/K)) - s3·z(ln(Th/K)).')
            if hasattr(self, 'norm_summary'):
                self.norm_summary.setPlainText(msg)
            append_session_log(self.processing_folder, 'Stage 3 - Generate ZLog Ratio Maps', inputs={'U/Th': paths[0], 'U/K': paths[1], 'K/U': paths[2], 'K/Th': paths[3]}, outputs=outputs, parameters={'Transform': 'log + z-score'})
            self._refresh_crossplot_lists()
            QMessageBox.information(self, 'ZLog maps generated', 'ZLog ratio maps were generated in 04_ZLog_Ratio_Maps.')
        except Exception as e:
            QMessageBox.critical(self, 'ZLog map generation failed', str(e))

    def _generate_hri_from_zlog_components(self, z_uth, z_uk, z_thk, profile, w1, w2, w3):
        """Generate weighted HRI = w1·z(ln(U/Th)) + w2·z(ln(U/K)) - w3·z(ln(Th/K))."""
        valid = np.isfinite(z_uth) & np.isfinite(z_uk) & np.isfinite(z_thk)
        hri = np.full(z_uth.shape, np.nan, dtype=np.float64)
        hri[valid] = (w1 * z_uth[valid]) + (w2 * z_uk[valid]) - (w3 * z_thk[valid])
        return hri, profile

    def _generate_combined(self):
        try:
            do_rrai = getattr(self, 'chk_generate_rrai', None).isChecked() if hasattr(self, 'chk_generate_rrai') else True
            do_hrai = getattr(self, 'chk_generate_hrai', None).isChecked() if hasattr(self, 'chk_generate_hrai') else True
            do_hri = getattr(self, 'chk_generate_hri', None).isChecked() if hasattr(self, 'chk_generate_hri') else True
            if not (do_rrai or do_hrai or do_hri):
                raise RuntimeError('Select at least one combined map to generate: RRAI-U, HRAI-KU or HRI.')

            paths, profile, n_uth, n_uk, n_ku, n_kth, inv_kth = self._normalise_inputs()
            r_w1, r_w2, r_w3 = self._normalised_weights(self.rrai_w_uth.value(), self.rrai_w_second.value(), self.rrai_w_kth.value(), 'RRAI-U')
            h_w1, h_w2, h_w3 = self._normalised_weights(self.hrai_w_uth.value(), self.hrai_w_second.value(), self.hrai_w_kth.value(), 'HRAI-KU')
            hri_w1, hri_w2, hri_w3 = float(self.hri_w_uth.value()), float(self.hri_w_second.value()), float(self.hri_w_kth.value())
            rrai_method = self.rrai_combination_method.currentText()
            hrai_method = self.hrai_combination_method.currentText()
            hri_method = self.hri_combination_method.currentText()

            outputs = self._write_normalised_outputs(profile, n_uth, n_uk, n_ku, n_kth, inv_kth, load=True)
            comb_dir = self._combined_dir()
            generated = []
            generated_outputs = {'Normalised': outputs}
            summary_parts = ['Generated normalised maps:'] + outputs + ['']

            if do_rrai:
                rrai_u = self._combine_normalised(n_uth, n_uk, inv_kth, r_w1, r_w2, r_w3, rrai_method)
                rrai_u[~np.isfinite(n_uth) | ~np.isfinite(n_uk) | ~np.isfinite(inv_kth)] = np.nan
                rrai_path = os.path.join(comb_dir, 'RRAI_U_Uranium_Enrichment_Index.tif')
                rrai_path = self._write_raster_versioned(rrai_path, rrai_u, profile, gdal.GDT_Float32)
                load_raster_pseudocolor_quantile(rrai_path, 'RRAI-U_Uranium_Enrichment_Index')
                generated.append(('RRAI-U', rrai_path, rrai_u, profile))
                generated_outputs['RRAI'] = rrai_path
                summary_parts.append(f'RRAI-U: {rrai_path}')
                summary_parts.append(f'  mode={rrai_method}; weights U/Th={r_w1:.3f}, U/K={r_w2:.3f}, 1 - norm(K/Th)={r_w3:.3f}')

            if do_hrai:
                hrai_ku = self._combine_normalised(n_uth, n_ku, inv_kth, h_w1, h_w2, h_w3, hrai_method)
                hrai_ku[~np.isfinite(n_uth) | ~np.isfinite(n_ku) | ~np.isfinite(inv_kth)] = np.nan
                hrai_path = os.path.join(comb_dir, 'HRAI_KU_Hybrid_Alteration_Source_Index.tif')
                hrai_path = self._write_raster_versioned(hrai_path, hrai_ku, profile, gdal.GDT_Float32)
                load_raster_pseudocolor_quantile(hrai_path, 'HRAI-KU_Hybrid_Alteration_Source_Index')
                generated.append(('HRAI-KU', hrai_path, hrai_ku, profile))
                generated_outputs['HRAI'] = hrai_path
                summary_parts.append(f'HRAI-KU: {hrai_path}')
                summary_parts.append(f'  mode={hrai_method}; weights U/Th={h_w1:.3f}, K/U={h_w2:.3f}, 1 - norm(K/Th)={h_w3:.3f}')

            if do_hri:
                _, hri_profile, z_uth, z_uk, _z_ku, _z_kth, z_thk = self._zlog_inputs()
                zlog_outputs = self._write_zlog_outputs(hri_profile, z_uth, z_uk, _z_ku, _z_kth, z_thk, load=True)
                generated_outputs['ZLog'] = zlog_outputs
                hri, hri_profile = self._generate_hri_from_zlog_components(z_uth, z_uk, z_thk, hri_profile, hri_w1, hri_w2, hri_w3)
                hri_path = os.path.join(comb_dir, 'HRI_Hydrocarbon_Radiometric_Index.tif')
                hri_path = self._write_raster_versioned(hri_path, hri, hri_profile, gdal.GDT_Float32)
                load_raster_pseudocolor_quantile(hri_path, 'HRI_Hydrocarbon_Radiometric_Index')
                generated.append(('HRI', hri_path, hri, hri_profile))
                generated_outputs['HRI'] = hri_path
                summary_parts.append(f'HRI: {hri_path}')
                summary_parts.append(f'  mode={hri_method}; scalars U/Th={hri_w1:.3f}, U/K={hri_w2:.3f}, -Th/K={hri_w3:.3f}')

            # Set the active combined map to the first map generated in the selected list.
            active_name, active_path, active_arr, active_profile = generated[0]
            self._combined = active_arr
            self._profile = active_profile
            self._combined_path = active_path
            for _, path, _, _ in generated:
                self._ensure_combo_path(self.cmb_combined, path)
            self._ensure_combo_path(self.cmb_combined, active_path)

            summary_parts.extend([
                '',
                'RRAI-U = combination of norm(U/Th), norm(U/K) and [1 - norm(K/Th)].',
                'HRAI-KU = combination of norm(U/Th), norm(K/U) and [1 - norm(K/Th)].',
                'HRI = s1·z(ln(U/Th)) + s2·z(ln(U/K)) - s3·z(ln(Th/K)).',
                'Note: 1 - norm(K/Th) is a polarity alignment, not reciprocal 1/(K/Th).'
            ])
            summary_text = '\n'.join(summary_parts)
            if hasattr(self, 'norm_summary'):
                self.norm_summary.setPlainText(summary_text)
            if hasattr(self, 'combination_summary'):
                self.combination_summary.setPlainText(summary_text)
            append_session_log(
                self.processing_folder,
                'Stage 3 - Generate Combined Maps',
                inputs={'U/Th': paths[0], 'U/K': paths[1], 'K/U': paths[2], 'K/Th': paths[3]},
                outputs=generated_outputs,
                parameters={
                    'Normalisation': self.norm_method.currentText(),
                    'Normalisation_low_pct': float(self.norm_low_pct.value()) if hasattr(self, 'norm_low_pct') else 1.0,
                    'Normalisation_high_pct': float(self.norm_high_pct.value()) if hasattr(self, 'norm_high_pct') else 99.0,
                    'Generate_RRAI': do_rrai,
                    'Generate_HRAI': do_hrai,
                    'Generate_HRI': do_hri,
                    'RRAI_mode': rrai_method,
                    'HRAI_mode': hrai_method,
                    'RRAI_weights': (r_w1, r_w2, r_w3),
                    'HRAI_weights': (h_w1, h_w2, h_w3),
                    'HRI_mode': hri_method,
                    'HRI_scalars': (hri_w1, hri_w2, hri_w3),
                    'HRI_formula': 's1*z(ln(U/Th)) + s2*z(ln(U/K)) - s3*z(ln(Th/K))'
                }
            )
            self._refresh_layers()
            QMessageBox.information(self, 'Combined maps generated', 'Generated: ' + ', '.join([name for name, _, _, _ in generated]) + '. Use the Threshold Analysis tab to select an index and extract anomalies.')
        except Exception as e:
            QMessageBox.critical(self, 'Combined map generation failed', str(e))


    def _norm_path(self, filename):
        stem, ext = os.path.splitext(filename)
        return self._latest_matching_path(self._norm_dir(), stem, ext or '.tif')

    def _create_scatterplot(self):
        """Show a K/U-vs-U/Th scatterplot coloured by HRAI-KU.

        This is diagnostic only. It helps check whether the hybrid K/U index is
        separating a source/alteration trend from the direct U-enrichment trend.
        """
        if not HAS_MPL:
            QMessageBox.warning(self, 'Scatterplot', 'Matplotlib is required for scatterplots.')
            return
        try:
            required = {
                'U/Th norm': self._norm_path('U_Th_norm.tif'),
                'K/U norm': self._norm_path('K_U_norm.tif'),
                'K/Th component [1 - norm(K/Th)]': self._norm_path('K_Th_inverse_norm.tif'),
            }
            missing = [label for label, path in required.items() if not os.path.exists(path)]
            if missing:
                raise RuntimeError('Generate the normalised ratio maps first. Missing: ' + ', '.join(missing))

            uth, _ = read_raster(required['U/Th norm'])
            ku, _ = read_raster(required['K/U norm'])
            inv, _ = read_raster(required['K/Th component [1 - norm(K/Th)]'])
            hrai_path = os.path.join(self._combined_dir(), 'HRAI_KU_Hybrid_Alteration_Source_Index.tif')
            if os.path.exists(hrai_path):
                colour, _ = read_raster(hrai_path)
                colour_label = 'HRAI-KU'
            else:
                # Fallback diagnostic using balanced HRAI-style weighting.
                colour = 0.4 * uth + 0.4 * ku + 0.2 * inv
                colour_label = 'HRAI-KU proxy'

            valid = np.isfinite(uth) & np.isfinite(ku) & np.isfinite(colour)
            flat_idx = np.flatnonzero(valid.ravel())
            if flat_idx.size == 0:
                raise RuntimeError('No valid overlapping cells for scatterplot.')
            sample_size = min(8000, flat_idx.size)
            if flat_idx.size > sample_size:
                rng = np.random.default_rng(42)
                flat_idx = rng.choice(flat_idx, sample_size, replace=False)
            x = ku.ravel()[flat_idx]
            y = uth.ravel()[flat_idx]
            c = colour.ravel()[flat_idx]

            fig = Figure(figsize=(8.5, 6.5), tight_layout=True)
            canvas = FigureCanvas(fig)
            ax = fig.add_subplot(111)
            sc = ax.scatter(x, y, c=c, cmap='viridis', s=8, alpha=0.65, edgecolors='none')
            ax.set_xlabel('K/U normalised: potassium dominance / uranium depletion signal')
            ax.set_ylabel('U/Th normalised: uranium mobility / enrichment signal')
            ax.set_title('Source–Trap Diagnostic Scatterplot')
            ax.axhline(0.5, color='0.5', linestyle='--', linewidth=1.0, alpha=0.6)
            ax.axvline(0.5, color='0.5', linestyle='--', linewidth=1.0, alpha=0.6)
            ax.text(0.73, 0.88, 'High K/U + high U/Th\ntransition / alteration trend', transform=ax.transAxes, ha='center', fontsize=8)
            ax.text(0.73, 0.12, 'High K/U + low U/Th\nsource-side depletion / K-dominance', transform=ax.transAxes, ha='center', fontsize=8)
            ax.text(0.23, 0.88, 'Low K/U + high U/Th\nuranium enrichment side', transform=ax.transAxes, ha='center', fontsize=8)
            cb = fig.colorbar(sc, ax=ax)
            cb.set_label(colour_label)

            dlg = QDialog(self)
            dlg.setWindowTitle('Source–Trap Scatterplot')
            layout = QVBoxLayout(dlg)
            note = QLabel('Diagnostic plot: K/U and U/Th are sampled from the normalised rasters. Colour shows HRAI-KU or a balanced proxy if the HRAI raster has not been generated.')
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addWidget(canvas)
            close_btn = QPushButton('Close')
            close_btn.clicked.connect(dlg.accept)
            layout.addWidget(close_btn)
            dlg.resize(930, 760)
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, 'Scatterplot error', str(e))

    # ------------------------- thresholding
    def _load_combined(self):
        path=self.cmb_combined.currentData() or self._combined_path
        if not path: raise RuntimeError('Select or generate the combined radiometric map first.')
        self._combined,self._profile=read_raster(path); self._combined_path=path
        self._analysis=np.array(self._combined, copy=True)

    def _load_and_plot(self):
        try:
            self._load_combined(); self._calculate_thresholds(show_message=False); self._plot_histogram(); self._update_threshold_summary()
        except Exception as e:
            QMessageBox.critical(self,'Could not load combined map',str(e))

    def _nonnegative_threshold(self, value):
        try:
            v=float(value)
        except Exception:
            return 0.0
        if not np.isfinite(v):
            return 0.0
        return max(0.0, v)

    def _threshold_values(self):
        h=self._nonnegative_threshold(self.high_val.value())
        v=self._nonnegative_threshold(self.vhigh_val.value())
        if self.high_val.value()!=h: self.high_val.setValue(h)
        if self.vhigh_val.value()!=v: self.vhigh_val.setValue(v)
        return h, v

    def _calculate_thresholds(self, show_message=True):
        if self._combined is None: self._load_combined()
        arr=self._analysis if self._analysis is not None else self._combined
        vals=arr[np.isfinite(arr)]
        if vals.size==0: raise RuntimeError('No valid combined raster cells found.')
        if self.calc_mode.currentText().lower().startswith('percentile'):
            h=float(np.nanpercentile(vals,self.high_pct.value())); v=float(np.nanpercentile(vals,self.vhigh_pct.value()))
        else:
            m=float(np.nanmean(vals)); s=float(np.nanstd(vals)); h=m+self.high_sigma.value()*s; v=m+self.vhigh_sigma.value()*s
        h=self._nonnegative_threshold(h); v=self._nonnegative_threshold(v)
        self.high_val.setValue(h); self.vhigh_val.setValue(v); self._plot_histogram(); self._update_threshold_summary()
        if show_message: QMessageBox.information(self,'Thresholds calculated','Combined threshold values have been filled.')

    def _manual_axis_range(self):
        if not getattr(self,'chk_s3_manual_axis',None) or not self.chk_s3_manual_axis.isChecked(): return None
        try:
            xmin=float(self.s3_axis_min.text()); xmax=float(self.s3_axis_max.text())
            if np.isfinite(xmin) and np.isfinite(xmax) and xmax>xmin: return xmin,xmax
        except Exception:
            pass
        return None

    def _plot_histogram(self):
        if not HAS_MPL or self._combined is None: return
        self.fig.clear(); ax=self.fig.add_subplot(1,1,1)
        raw=_valid_values(self._combined)
        analysis=_valid_values(self._analysis if self._analysis is not None else self._combined)
        if raw.size==0 or analysis.size==0: return
        show_raw=getattr(self,'chk_s3_show_raw',None) is None or self.chk_s3_show_raw.isChecked()
        show_analysis=getattr(self,'chk_s3_show_analysis',None) is None or self.chk_s3_show_analysis.isChecked()
        if not show_raw and not show_analysis:
            ax.text(0.5,0.5,'No histogram dataset selected',ha='center',va='center',transform=ax.transAxes); self.canvas.draw_idle(); return
        # use shared bins for raw and analysis so winsor/compression views are comparable
        combined_vals=np.concatenate([raw,analysis]) if (show_raw and show_analysis) else (raw if show_raw else analysis)
        manual=self._manual_axis_range()
        if manual is not None and show_raw and show_analysis:
            xmin,xmax=manual
        else:
            xmin=float(np.nanmin(combined_vals)); xmax=float(np.nanmax(combined_vals))
        if not np.isfinite(xmin) or not np.isfinite(xmax) or math.isclose(xmin,xmax):
            xmin,xmax=xmin-0.5,xmax+0.5
        bins=np.linspace(xmin,xmax,101)
        if show_raw: ax.hist(raw,bins=bins,alpha=0.40,label='Raw')
        if show_analysis: ax.hist(analysis,bins=bins,alpha=0.55,label='Analysis')
        h,v=self._threshold_values()
        finite=np.isfinite(self._combined); total=float(np.count_nonzero(finite)) or 1.0
        if getattr(self,'chk_s3_removed',None) is not None and self.chk_s3_removed.isChecked():
            rem=analysis[analysis < h]
            if rem.size: ax.hist(rem,bins=bins,alpha=0.18,label='Removed')
        if getattr(self,'chk_s3_high',None) is not None and self.chk_s3_high.isChecked():
            hp=analysis[analysis >= h]
            if hp.size: ax.hist(hp,bins=bins,alpha=0.25,color='lightgreen',label='High pass')
        if getattr(self,'chk_s3_very',None) is not None and self.chk_s3_very.isChecked():
            vp=analysis[analysis >= v]
            if vp.size: ax.hist(vp,bins=bins,alpha=0.35,color='green',label='Very High pass')
        if getattr(self,'chk_s3_mean',None) is None or self.chk_s3_mean.isChecked(): ax.axvline(float(np.nanmean(analysis)),color='0.35',linestyle='-',linewidth=1,label='Mean')
        if getattr(self,'chk_s3_median',None) is None or self.chk_s3_median.isChecked(): ax.axvline(float(np.nanmedian(analysis)),color='0.35',linestyle='--',linewidth=1,label='Median')
        if getattr(self,'chk_s3_std',None) is not None and self.chk_s3_std.isChecked():
            m=float(np.nanmean(analysis)); sd=float(np.nanstd(analysis))
            ax.axvline(max(0.0,m-sd),color='0.55',linestyle=':',linewidth=1,label='±1 std')
            ax.axvline(m+sd,color='0.55',linestyle=':',linewidth=1)
        if getattr(self,'chk_s3_percentiles',None) is not None and self.chk_s3_percentiles.isChecked():
            for pctl in [5,10,15,50,85,90,95]:
                ax.axvline(float(np.nanpercentile(analysis,pctl)),color='0.75',linestyle=':',linewidth=0.8)
        ax.axvline(h,color='black',linestyle='-',linewidth=1.5,label='High')
        ax.axvline(v,color='black',linestyle='--',linewidth=1.5,label='Very High')
        ax.set_title('Combined radiometric index - HIGH threshold logic')
        ax.set_xlabel('Combined radiometric index value'); ax.set_ylabel('Frequency')
        ax.legend(fontsize=7); self.canvas.draw_idle()

    def _update_threshold_summary(self):
        if self._combined is None: return
        h,v=self._threshold_values()
        finite=np.isfinite(self._combined); n=float(np.count_nonzero(finite)) or 1.0
        hmask=finite&(self._combined>=h); vmask=finite&(self._combined>=v)
        removed=finite&(self._combined<h)
        self.threshold_summary.setPlainText(
            f'Combined threshold summary\n'
            f'High threshold: {h:.8g} | retained: {100*np.count_nonzero(hmask)/n:.2f}% | removed below high: {100*np.count_nonzero(removed)/n:.2f}%\n'
            f'Very High threshold: {v:.8g} | retained: {100*np.count_nonzero(vmask)/n:.2f}%\n'
            f'Valid cells: {int(n):,}\n'
            f'Combined map: {self._combined_path}'
        )

    def _generate_threshold_map(self):
        try:
            if self._combined is None: self._load_combined()
            if hasattr(self, 'threshold_progress'):
                self.threshold_progress.setVisible(True); self.threshold_progress.setValue(0)
            if hasattr(self, 'gen_threshold_map_btn'):
                self.gen_threshold_map_btn.setEnabled(False)
            QApplication.processEvents()
            h,v=self._threshold_values()
            if hasattr(self, 'threshold_progress'): self.threshold_progress.setValue(25)
            out=np.zeros(self._combined.shape,dtype=np.uint8); finite=np.isfinite(self._combined)
            out[finite&(self._combined>=h)]=1
            out[finite&(self._combined>=v)]=2
            if hasattr(self, 'threshold_progress'): self.threshold_progress.setValue(55)
            outdir=self._threshold_dir_for(self._combined_path or self.cmb_combined.currentData())
            prefix=self._combined_subdir_name(self._combined_path or self.cmb_combined.currentData())
            p=make_available_path(os.path.join(outdir,prefix+'_threshold_class.tif'))
            write_raster(p,out,self._profile,gdal.GDT_Byte,nodata=0); load_layer(p,os.path.splitext(os.path.basename(p))[0])
            if hasattr(self, 'threshold_progress'): self.threshold_progress.setValue(85)
            self._write_settings_csv(); self._update_threshold_summary()
            append_session_log(self.processing_folder, 'Stage 3 - Generate Combined Threshold Class Map', inputs={'Combined_map': self._combined_path or self.cmb_combined.currentData()}, outputs=[p], parameters={'High_threshold': h, 'VeryHigh_threshold': v})
            if hasattr(self, 'threshold_progress'): self.threshold_progress.setValue(100)
            if hasattr(self, 'gen_threshold_map_btn'): self.gen_threshold_map_btn.setEnabled(True)
            QMessageBox.information(self,'Threshold map generated','Combined threshold map generated and loaded.\n\n'+p)
        except Exception as e:
            if hasattr(self, 'gen_threshold_map_btn'): self.gen_threshold_map_btn.setEnabled(True)
            QMessageBox.critical(self,'Threshold map generation failed',str(e))

    def _create_threshold_preview(self):
        try:
            if self._combined is None: self._load_combined()
            h,v=self._threshold_values()
            threshold=v if self.preview_level.currentText().lower().startswith('very') else h
            finite=np.isfinite(self._combined)
            kept=finite & (self._combined>=threshold)
            show_removed=self.preview_mode.currentText().lower().startswith('show removed')
            mask=(finite & ~kept) if show_removed else kept
            arr=np.zeros(self._combined.shape,dtype=np.uint8); arr[mask]=1
            outdir=ensure_dir(os.path.join(self._threshold_dir_for(self._combined_path or self.cmb_combined.currentData()),'threshold_preview'))
            name='Combined_preview_' + ('removed' if show_removed else 'kept') + '_' + self.preview_level.currentText().replace(' ','_') + '.tif'
            p=make_available_path(os.path.join(outdir,name))
            write_raster(p,arr,self._profile,gdal.GDT_Byte,nodata=0); load_layer(p,os.path.splitext(os.path.basename(p))[0])
            n=float(np.count_nonzero(finite)) or 1.0
            self.threshold_summary.append(f'\nPreview created: {p}\nPreview cells: {100*np.count_nonzero(mask)/n:.2f}% | mode: {self.preview_mode.currentText()} | colour: {self.preview_colour.currentText()} | opacity: {self.preview_opacity.value()}%')
        except Exception as e:
            QMessageBox.critical(self,'Threshold preview failed',str(e))

    def _clear_threshold_preview(self):
        # Non-destructive: remove loaded preview layers from the current QGIS project only.
        removed=0
        for lid,layer in list(QgsProject.instance().mapLayers().items()):
            if 'combined_preview_' in layer.name().lower():
                QgsProject.instance().removeMapLayer(lid); removed+=1
        if hasattr(self,'threshold_summary'):
            self.threshold_summary.append(f'\nCleared {removed} loaded Stage 3 preview layer(s).')

    # ------------------------- extraction
    def _index_file_path(self, index_key):
        """Return the newest Stage 3 combined-index raster path for an index key."""
        comb_dir = self._combined_dir()
        if index_key == 'hri':
            return self._latest_matching_path(comb_dir, 'HRI_Hydrocarbon_Radiometric_Index')
        if index_key == 'hrai':
            return self._latest_matching_path(comb_dir, 'HRAI_KU_Hybrid_Alteration_Source_Index')
        return self._latest_matching_path(comb_dir, 'RRAI_U_Uranium_Enrichment_Index')

    def _selected_extraction_indices(self):
        txt = self.extraction_index.currentText().lower() if hasattr(self, 'extraction_index') else 'rrai'
        if 'all' in txt:
            return [('rrai', 'RRAI_U_Uranium_Enrichment_Index', 'RRAI-U'), ('hrai', 'HRAI_KU_Hybrid_Alteration_Source_Index', 'HRAI-KU'), ('hri', 'HRI_Hydrocarbon_Radiometric_Index', 'HRI')]
        if 'both' in txt:
            return [('rrai', 'RRAI_U_Uranium_Enrichment_Index', 'RRAI-U'), ('hrai', 'HRAI_KU_Hybrid_Alteration_Source_Index', 'HRAI-KU')]
        if 'hri' in txt:
            return [('hri', 'HRI_Hydrocarbon_Radiometric_Index', 'HRI')]
        if 'hrai' in txt:
            return [('hrai', 'HRAI_KU_Hybrid_Alteration_Source_Index', 'HRAI-KU')]
        return [('rrai', 'RRAI_U_Uranium_Enrichment_Index', 'RRAI-U')]

    def _load_index_array(self, index_key):
        """Load RRAI-U or HRAI-KU from the combined-analysis folder."""
        p = self._index_file_path(index_key)
        if not os.path.exists(p):
            raise RuntimeError(f'{os.path.basename(p)} was not found. Generate the combined indices first in the Normalisation / Dual Indices tab.')
        arr, profile = read_raster(p)
        return arr, profile, p

    def _thresholds_for_array(self, arr):
        """Calculate High and Very High thresholds for the supplied combined index array using the current threshold settings."""
        vals = arr[np.isfinite(arr)]
        if vals.size == 0:
            raise RuntimeError('No valid combined raster cells found for threshold calculation.')
        if self.calc_mode.currentText().lower().startswith('percentile'):
            h = float(np.nanpercentile(vals, self.high_pct.value()))
            v = float(np.nanpercentile(vals, self.vhigh_pct.value()))
        else:
            m = float(np.nanmean(vals)); sd = float(np.nanstd(vals))
            h = m + self.high_sigma.value() * sd
            v = m + self.vhigh_sigma.value() * sd
        return self._nonnegative_threshold(h), self._nonnegative_threshold(v)

    def _mask_for_array(self, arr, level):
        h, v = self._thresholds_for_array(arr)
        threshold = v if level == 'very_high' else h
        return np.isfinite(arr) & (arr >= threshold), h, v

    def _mask(self, level=None):
        if self._combined is None: self._load_combined()
        idx = self.extract_level.currentIndex() if level is None else (1 if level == 'very_high' else 0)
        threshold=self._threshold_values()[1] if idx==1 else self._threshold_values()[0]
        return np.isfinite(self._combined)&(self._combined>=threshold)
    def _trend_mask(self,mask):
        if not self.enable_trend.isChecked(): return mask.astype(bool)
        size=int(self.trend_kernel.currentText().split()[0]); it=int(self.trend_iterations.value()); method=self.trend_method.currentText().lower()
        if 'closing' in method: return _square_erode(_square_dilate(mask,size,it),size,it)
        if 'dilation' in method: return _square_dilate(mask,size,it)
        return _focal_fraction(mask,size) >= float(self.majority_fraction.value())
    def _create_trending_preview(self):
        try:
            levels = ['high','very_high'] if self.extract_level.currentIndex() == 2 else (['very_high'] if self.extract_level.currentIndex() == 1 else ['high'])
            created=[]
            summary=[]
            for index_key, index_name, index_label in self._selected_extraction_indices():
                arr, profile, path = self._load_index_array(index_key)
                for level in levels:
                    raw, h, v = self._mask_for_array(arr, level)
                    tr = self._trend_mask(raw)
                    out=np.zeros(raw.shape,dtype=np.uint8); out[raw]=1; out[tr & ~raw]=2
                    p=make_available_path(os.path.join(self._final_dir(),f'{index_name}_trending_preview_{level}.tif'))
                    write_raster(p,out,profile,gdal.GDT_Byte,nodata=0); load_layer(p,os.path.splitext(os.path.basename(p))[0])
                    n=float(np.count_nonzero(np.isfinite(arr))) or 1.0
                    created.append(p)
                    summary.append(f'{index_label} {level}: raw {100*np.count_nonzero(raw)/n:.2f}% | trended {100*np.count_nonzero(tr)/n:.2f}% | High={h:.8g} | VeryHigh={v:.8g}')
            self.extraction_summary.setPlainText('Trending preview created:\n'+'\n'.join(created)+'\n\n'+'\n'.join(summary))
        except Exception as e:
            QMessageBox.critical(self,'Trending preview failed',str(e))
    def _smooth_polygon_output(self, gpkg_path, layer_name):
        if not getattr(self, 'smooth_polygons', None) or not self.smooth_polygons.isChecked():
            return gpkg_path
        try:
            out_path = make_available_path(gpkg_path[:-5] + '_smooth.gpkg' if gpkg_path.lower().endswith('.gpkg') else gpkg_path + '_smooth.gpkg')
            processing.run('native:smoothgeometry', {
                'INPUT': gpkg_path,
                'ITERATIONS': int(self.smooth_iterations.value()),
                'OFFSET': float(self.smooth_offset.value()),
                'MAX_ANGLE': float(self.smooth_max_angle.value()),
                'OUTPUT': out_path
            })
            return out_path
        except Exception as e:
            try:
                self.extraction_summary.appendPlainText('Polygon smoothing failed; using unsmoothed output. ' + str(e))
            except Exception:
                pass
            return gpkg_path

    def _extract_polygons(self):
        try:
            idx = self.extract_level.currentIndex()
            levels = ['high','very_high'] if idx == 2 else (['very_high'] if idx == 1 else ['high'])
            outputs=[]
            log_lines=[]
            indices = self._selected_extraction_indices()
            total_outputs = max(1, len(indices) * len(levels))
            done_outputs = 0
            if hasattr(self, 'polygon_progress'):
                self.polygon_progress.setVisible(True); self.polygon_progress.setValue(0)
            if hasattr(self, 'extract_polygons_btn'):
                self.extract_polygons_btn.setEnabled(False)
            QApplication.processEvents()
            for index_key, index_name, index_label in indices:
                final_dir=self._final_dir_for(index_key)
                arr, profile, path = self._load_index_array(index_key)
                for level in levels:
                    raw, h, v = self._mask_for_array(arr, level)
                    final=self._trend_mask(raw)
                    out=np.zeros(raw.shape,dtype=np.uint8); out[final]=1
                    name=index_name+'_'+level+('_trended' if self.enable_trend.isChecked() else '')
                    rp=make_available_path(os.path.join(final_dir,name+'_class.tif'))
                    write_raster(rp,out,profile,gdal.GDT_Byte,nodata=0)
                    gp=make_available_path(os.path.join(final_dir,name+'_polygons.gpkg'))
                    min_pix = int(self.min_pixels.value()) if getattr(self, 'use_min_pixels', None) and self.use_min_pixels.isChecked() else 0
                    if getattr(self, 'polygon_method', None) and self.polygon_method.currentIndex() == 0:
                        polygonize_binary_mask_raster(rp,gp,name,float(self.min_area.value()),min_pix)
                    else:
                        polygonize_class_raster(rp,gp,name,[1],float(self.min_area.value()))
                    final_gp = self._smooth_polygon_output(gp, name)
                    load_layer(rp,os.path.splitext(os.path.basename(rp))[0])
                    load_layer(final_gp,os.path.splitext(os.path.basename(final_gp))[0])
                    outputs.append(final_gp)
                    n=float(np.count_nonzero(np.isfinite(arr))) or 1.0
                    retained=100*np.count_nonzero(final)/n
                    log_lines.append(f'{index_label} {level}: {retained:.2f}% retained | High={h:.8g} | VeryHigh={v:.8g} | {final_gp}')
                    done_outputs += 1
                    if hasattr(self, 'polygon_progress'):
                        self.polygon_progress.setValue(int(done_outputs / total_outputs * 100))
                    QApplication.processEvents()
                    if getattr(self, '_stop_requested', False):
                        self.extraction_summary.appendPlainText('Stop requested. Stopping after current output GPKG.')
                        self._write_settings_csv()
                        append_session_log(self.processing_folder, 'Stage 3 - Extract Combined Anomaly Polygons', status='stopped', outputs=outputs, parameters={'Extraction_level': self.extract_level.currentText(), 'Minimum_area_km2': self.min_area.value(), 'Trending_enabled': self.enable_trend.isChecked()})
                        if hasattr(self, 'extract_polygons_btn'):
                            self.extract_polygons_btn.setEnabled(True)
                        QMessageBox.information(self, 'Extraction stopped', 'Stopped after current output GPKG:\n' + final_gp)
                        return
            self._write_settings_csv()
            if hasattr(self, 'polygon_progress'):
                self.polygon_progress.setValue(100)
            if hasattr(self, 'extract_polygons_btn'):
                self.extract_polygons_btn.setEnabled(True)
            append_session_log(self.processing_folder, 'Stage 3 - Extract Combined Anomaly Polygons', outputs=outputs, parameters={'Extraction_level': self.extract_level.currentText(), 'Minimum_area_km2': self.min_area.value(), 'Trending_enabled': self.enable_trend.isChecked()})
            self.extraction_summary.setPlainText('Extracted anomaly polygons:\n'+'\n'.join(log_lines))
            QMessageBox.information(self,'Extraction complete','Anomaly polygons extracted and loaded.\n\n'+'\n'.join(outputs))
        except Exception as e:
            if hasattr(self, 'extract_polygons_btn'):
                self.extract_polygons_btn.setEnabled(True)
            QMessageBox.critical(self,'Extraction failed',str(e))

    def _write_settings_csv(self):
        if not self.processing_folder or self._combined is None: return
        stats={'Combined_Radiometric_Index':array_stats(self._combined,[50,75,80,85,90,95])}
        h,v=self._threshold_values()
        selected = self.extraction_index.currentText() if hasattr(self, 'extraction_index') else 'Current combined map'
        settings={'High_threshold':h,'VeryHigh_threshold':v,'Combined_map':self._combined_path,'Selected_extraction_index':selected,'Minimum_area_km2':self.min_area.value(),'Trending_enabled':self.enable_trend.isChecked(),'Trending_method':self.trend_method.currentText()}
        write_stats_csv(os.path.join(self._combined_dir(),'combined_threshold_settings.csv'),stats,settings)
