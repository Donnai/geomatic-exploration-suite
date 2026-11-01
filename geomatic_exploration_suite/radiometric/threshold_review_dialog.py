# -*- coding: utf-8 -*-
"""Stage 2 interactive ratio-map threshold + trending review for radiometric ratio rasters.

This dialog intentionally lives in the main plugin rather than as a pure
Processing algorithm because the workflow is interpretive: inspect histograms,
preview spatial threshold masks, tune continuity/trending, then extract final
polygons.
"""
import logging
import os, csv, math
import numpy as np
import processing
from osgeo import gdal

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QDoubleSpinBox, QFileDialog, QCheckBox, QGroupBox,
    QMessageBox, QPlainTextEdit, QTabWidget, QWidget, QSpinBox, QSlider,
    QLineEdit, QSizePolicy, QProgressBar, QApplication
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QRectF
from qgis.PyQt.QtGui import QPainter, QPen, QBrush, QColor
from qgis.core import QgsProject, QgsRasterLayer

from .algs.utils import (
    read_raster, write_raster, ensure_dir, make_available_path,
    load_raster_pseudocolor_quantile, load_layer, polygonize_class_raster, polygonize_binary_mask_raster,
    write_stats_csv, array_stats, append_session_log
)

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    HAS_MPL = True
except Exception:
    HAS_MPL = False
    FigureCanvas = None
    Figure = None


def _valid_values(arr, max_samples=750000):
    vals = arr[np.isfinite(arr)]
    if vals.size == 0:
        raise RuntimeError('No valid raster cells found.')
    if vals.size > max_samples:
        step = max(1, int(math.ceil(vals.size / float(max_samples))))
        vals = vals[::step]
    return vals


def _winsorise_array(arr, low_q, high_q):
    out = np.array(arr, copy=True, dtype=np.float64)
    vals = out[np.isfinite(out)]
    if vals.size == 0:
        return out, np.nan, np.nan, 0.0, 0.0
    lo = float(np.nanpercentile(vals, low_q))
    hi = float(np.nanpercentile(vals, high_q))
    low_mask = np.isfinite(out) & (out < lo)
    high_mask = np.isfinite(out) & (out > hi)
    n = float(vals.size)
    low_pct = 100.0 * float(np.count_nonzero(low_mask)) / n
    high_pct = 100.0 * float(np.count_nonzero(high_mask)) / n
    out[low_mask] = lo
    out[high_mask] = hi
    return out, lo, hi, low_pct, high_pct




def _square_dilate(mask, size, iterations=1):
    out = mask.astype(bool)
    size = max(1, int(size)); pad = size // 2
    for _ in range(max(1, int(iterations))):
        p = np.pad(out, pad, mode='constant', constant_values=False)
        res = np.zeros_like(out, dtype=bool)
        for i in range(size):
            for j in range(size):
                res |= p[i:i+out.shape[0], j:j+out.shape[1]]
        out = res
    return out


def _square_erode(mask, size, iterations=1):
    out = mask.astype(bool)
    size = max(1, int(size)); pad = size // 2
    for _ in range(max(1, int(iterations))):
        p = np.pad(out, pad, mode='constant', constant_values=False)
        res = np.ones_like(out, dtype=bool)
        for i in range(size):
            for j in range(size):
                res &= p[i:i+out.shape[0], j:j+out.shape[1]]
        out = res
    return out


def _focal_fraction(mask, size):
    size = max(1, int(size)); pad = size // 2
    p = np.pad(mask.astype(float), pad, mode='constant', constant_values=0.0)
    total = np.zeros(mask.shape, dtype=np.float64)
    for i in range(size):
        for j in range(size):
            total += p[i:i+mask.shape[0], j:j+mask.shape[1]]
    return total / float(size * size)



class RangeSlider(QWidget):
    """Compact two-handle horizontal range slider, values 0..1000."""
    valuesChanged = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._low = 250
        self._high = 750
        self._drag = None
        self.setMinimumHeight(34)
        self.setMaximumHeight(40)
        self.setMouseTracking(True)

    def values(self):
        return self._low, self._high

    def setValues(self, low, high, emit=False):
        low = max(0, min(1000, int(low)))
        high = max(0, min(1000, int(high)))
        if low > high:
            low, high = high, low
        changed = (low != self._low or high != self._high)
        self._low, self._high = low, high
        self.update()
        if emit and changed:
            self.valuesChanged.emit(self._low, self._high)

    def _track_rect(self):
        return QRectF(12, self.height()/2 - 3, max(1, self.width()-24), 6)

    def _x_for_value(self, v):
        r = self._track_rect()
        return r.left() + (float(v) / 1000.0) * r.width()

    def _value_for_x(self, x):
        r = self._track_rect()
        return int(round(1000.0 * max(0.0, min(1.0, (x - r.left()) / r.width()))))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        r = self._track_rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(210, 210, 210)))
        painter.drawRoundedRect(r, 3, 3)
        x1 = self._x_for_value(self._low)
        x2 = self._x_for_value(self._high)
        painter.setBrush(QBrush(QColor(90, 130, 190)))
        painter.drawRoundedRect(QRectF(x1, r.top(), max(1, x2-x1), r.height()), 3, 3)
        painter.setPen(QPen(QColor(40, 40, 40), 1))
        painter.setBrush(QBrush(QColor(245, 245, 245)))
        for x in (x1, x2):
            painter.drawEllipse(QRectF(x-6, self.height()/2-9, 12, 18))

    def mousePressEvent(self, event):
        x = event.position().x() if hasattr(event, 'position') else event.x()
        x1 = self._x_for_value(self._low)
        x2 = self._x_for_value(self._high)
        self._drag = 'low' if abs(x-x1) <= abs(x-x2) else 'high'
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if not self._drag:
            return
        x = event.position().x() if hasattr(event, 'position') else event.x()
        v = self._value_for_x(x)
        if self._drag == 'low':
            self.setValues(min(v, self._high), self._high, emit=True)
        else:
            self.setValues(self._low, max(v, self._low), emit=True)

    def mouseReleaseEvent(self, event):
        self._drag = None

class ThresholdReviewDialog(QDialog):
    """Stage 2: ratio map analysis, spatial preview, trending, and anomaly extraction."""
    def __init__(self, parent=None, processing_folder=''):
        super().__init__(parent)
        self.setWindowTitle('Stage 2 - Ratio Map Analysis and Anomaly Extraction')
        self.resize(900, 700)
        self.processing_folder = processing_folder or ''
        self._raw = {}
        self._clipped = {}
        self._profile = None
        self._paths = {}
        self._clip_report = {}
        self._build_ui()
        self._refresh_layers()
        self._auto_from_processing_folder()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        intro = QLabel('Analyse high U/Th, high U/K, high K/U and low K/Th ratio anomalies. Use the first tab to select thresholds and generate threshold maps; use the second tab to extract anomaly polygons with or without trending.')
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.folder_label = QLabel(self.processing_folder or '(not set)')
        self.folder_label.setVisible(False)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._make_threshold_tab(), 'Threshold Analysis')
        self.tabs.addTab(self._make_trending_tab(), 'Anomaly Extraction')
        root.addWidget(self.tabs, 1)

        # Stage 2 settings are saved automatically when threshold maps or polygons are generated.

    def _make_threshold_tab(self):
        w = QWidget(); root = QVBoxLayout(w)

        gb = self._collapsible_group('Input Radiometric Ratio Rasters')
        grid = QGridLayout(gb)
        self.cmb_uth = QComboBox(); self.cmb_uk = QComboBox(); self.cmb_ku = QComboBox(); self.cmb_kth = QComboBox()
        grid.addWidget(QLabel('U/Th ratio:'), 0, 0); grid.addWidget(self.cmb_uth, 0, 1)
        grid.addWidget(QLabel('U/K ratio - RRAI-U enrichment:'), 1, 0); grid.addWidget(self.cmb_uk, 1, 1)
        grid.addWidget(QLabel('K/U ratio - HRAI-KU hybrid/source-side:'), 2, 0); grid.addWidget(self.cmb_ku, 2, 1)
        grid.addWidget(QLabel('K/Th ratio:'), 3, 0); grid.addWidget(self.cmb_kth, 3, 1)
        refresh = QPushButton('Refresh raster list'); refresh.clicked.connect(self._refresh_layers); grid.addWidget(refresh, 0, 2)
        auto = QPushButton('Auto-detect from folder'); auto.clicked.connect(self._auto_from_processing_folder); grid.addWidget(auto, 1, 2)
        load = QPushButton('Generate / Reset Histograms'); load.clicked.connect(self._generate_reset_histograms); grid.addWidget(load, 2, 2)
        note = QLabel('Select the ratio maps used for threshold review and anomaly extraction.')
        note.setWordWrap(True); grid.addWidget(note, 4, 0, 1, 3)
        root.addWidget(gb)

        clip = self._collapsible_group('Histogram compression / extreme-value clipping preview')
        cgrid = QGridLayout(clip)
        self.enable_clip = QCheckBox('Winsorise for histogram and thresholding')
        self.enable_clip.setChecked(False)
        cgrid.addWidget(self.enable_clip, 0, 0, 1, 2)
        self.clip_low = self._percent_spin(1.0); self.clip_high = self._percent_spin(99.0)
        cgrid.addWidget(QLabel('Low clip percentile:'), 1, 0); cgrid.addWidget(self.clip_low, 1, 1)
        cgrid.addWidget(QLabel('High clip percentile:'), 1, 2); cgrid.addWidget(self.clip_high, 1, 3)
        cgrid.addWidget(QLabel('Caps extreme display values for analysis without deleting raster cells.'), 2, 0, 1, 4)
        c_refresh = QPushButton('Refresh Histogram')
        c_refresh.clicked.connect(self._load_and_plot)
        cgrid.addWidget(c_refresh, 3, 0, 1, 4)
        root.addWidget(clip)

        threshold_params = self._collapsible_group('Threshold Parameters')
        threshold_params_layout = QVBoxLayout(threshold_params)

        calc = self._collapsible_group('Calculate thresholds')
        calcgrid = QGridLayout(calc)
        self.calc_mode = QComboBox(); self.calc_mode.addItems(['Percentile', 'Mean ± standard deviation'])
        self.calc_high_pct = self._percent_spin(85.0); self.calc_vhigh_pct = self._percent_spin(95.0)
        self.calc_low_pct = self._percent_spin(15.0); self.calc_vlow_pct = self._percent_spin(5.0)
        self.calc_high_sigma = QDoubleSpinBox(); self.calc_high_sigma.setRange(0.0, 10.0); self.calc_high_sigma.setDecimals(2); self.calc_high_sigma.setSingleStep(0.25); self.calc_high_sigma.setValue(1.0)
        self.calc_vhigh_sigma = QDoubleSpinBox(); self.calc_vhigh_sigma.setRange(0.0, 10.0); self.calc_vhigh_sigma.setDecimals(2); self.calc_vhigh_sigma.setSingleStep(0.25); self.calc_vhigh_sigma.setValue(2.0)
        calcgrid.addWidget(QLabel('Method:'),0,0); calcgrid.addWidget(self.calc_mode,0,1)
        calcgrid.addWidget(QLabel('High percentile:'),1,0); calcgrid.addWidget(self.calc_high_pct,1,1)
        calcgrid.addWidget(QLabel('Very high percentile:'),1,2); calcgrid.addWidget(self.calc_vhigh_pct,1,3)
        calcgrid.addWidget(QLabel('Low percentile:'),2,0); calcgrid.addWidget(self.calc_low_pct,2,1)
        calcgrid.addWidget(QLabel('Very low percentile:'),2,2); calcgrid.addWidget(self.calc_vlow_pct,2,3)
        calcgrid.addWidget(QLabel('High σ:'),3,0); calcgrid.addWidget(self.calc_high_sigma,3,1)
        calcgrid.addWidget(QLabel('Very high σ:'),3,2); calcgrid.addWidget(self.calc_vhigh_sigma,3,3)
        calcbtn = QPushButton('Calculate / Fill Threshold Values'); calcbtn.clicked.connect(self._calculate_threshold_values)
        calcgrid.addWidget(calcbtn,4,0,1,4)
        threshold_params_layout.addWidget(calc)

        pgb = self._collapsible_group('Threshold settings')
        pgrid = QGridLayout(pgb)
        self.uth_high = self._value_spin(); self.uth_vhigh = self._value_spin()
        self.uk_high = self._value_spin(); self.uk_vhigh = self._value_spin()
        self.ku_high = self._value_spin(); self.ku_vhigh = self._value_spin()
        self.kth_low = self._value_spin(); self.kth_vlow = self._value_spin()
        pgrid.addWidget(QLabel('U/Th High value:'),0,0); pgrid.addWidget(self.uth_high,0,1)
        pgrid.addWidget(QLabel('U/Th Very High value:'),0,2); pgrid.addWidget(self.uth_vhigh,0,3)
        pgrid.addWidget(QLabel('U/K High value:'),1,0); pgrid.addWidget(self.uk_high,1,1)
        pgrid.addWidget(QLabel('U/K Very High value:'),1,2); pgrid.addWidget(self.uk_vhigh,1,3)
        pgrid.addWidget(QLabel('K/U High value:'),2,0); pgrid.addWidget(self.ku_high,2,1)
        pgrid.addWidget(QLabel('K/U Very High value:'),2,2); pgrid.addWidget(self.ku_vhigh,2,3)
        pgrid.addWidget(QLabel('K/Th Low value:'),3,0); pgrid.addWidget(self.kth_low,3,1)
        pgrid.addWidget(QLabel('K/Th Very Low value:'),3,2); pgrid.addWidget(self.kth_vlow,3,3)
        threshold_params_layout.addWidget(pgb)
        root.addWidget(threshold_params)

        hgb = self._collapsible_group('Histogram display and manual threshold range controls')
        hroot = QGridLayout(hgb)
        self._hist_figs = {}; self._hist_canvases = {}; self._sliders = {}; self._hist_visible_checks = {}; self._clip_labels = {}
        hist_visible_row = QHBoxLayout()
        hist_visible_row.addWidget(QLabel('Show histograms:'))
        for label, key in [('U/Th', 'U_Th_ratio'), ('U/K', 'U_K_ratio'), ('K/U', 'K_U_ratio'), ('K/Th', 'K_Th_ratio')]:
            chk = QCheckBox(label)
            chk.setChecked(True)
            chk.stateChanged.connect(self._plot_histograms)
            self._hist_visible_checks[key] = chk
            hist_visible_row.addWidget(chk)
        hist_visible_row.addStretch(1)
        hroot.addLayout(hist_visible_row, 0, 0, 1, 3)

        display_row = QHBoxLayout()
        display_row.addWidget(QLabel('Histogram datasets:'))
        self.chk_show_raw = QCheckBox('Raw')
        self.chk_show_analysis = QCheckBox('Analysis / clipped')
        self.chk_show_raw.setChecked(True); self.chk_show_analysis.setChecked(True)
        for chk in [self.chk_show_raw, self.chk_show_analysis]:
            chk.stateChanged.connect(self._plot_histograms)
            display_row.addWidget(chk)
        display_row.addSpacing(18)
        display_row.addWidget(QLabel('Manual axis range when raw + analysis displayed:'))
        self.chk_manual_axis = QCheckBox('Use manual X range')
        self.axis_min = QLineEdit(); self.axis_min.setPlaceholderText('min'); self.axis_min.setMaximumWidth(95)
        self.axis_max = QLineEdit(); self.axis_max.setPlaceholderText('max'); self.axis_max.setMaximumWidth(95)
        self.chk_manual_axis.stateChanged.connect(self._plot_histograms)
        self.axis_min.editingFinished.connect(self._plot_histograms)
        self.axis_max.editingFinished.connect(self._plot_histograms)
        display_row.addWidget(self.chk_manual_axis); display_row.addWidget(self.axis_min); display_row.addWidget(self.axis_max)
        display_row.addStretch(1)
        hroot.addLayout(display_row, 1, 0, 1, 3)

        for col, key in enumerate(['U_Th_ratio','U_K_ratio','K_U_ratio','K_Th_ratio']):
            if HAS_MPL:
                fig = Figure(figsize=(4.6, 3.4), tight_layout=True)
                canv = FigureCanvas(fig)
                canv.setMinimumHeight(260)
                canv.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                self._hist_figs[key] = fig; self._hist_canvases[key] = canv
                hroot.addWidget(canv, 2, col)
            rng = RangeSlider()
            rng.valuesChanged.connect(lambda low, high, k=key: self._range_slider_changed(k, low, high))
            self._sliders[key] = rng
            hroot.addWidget(rng, 3, col)
            lbl = QLabel('Clipped: low 0.00%, high 0.00%')
            lbl.setStyleSheet('color:#555; font-size:8pt;')
            self._clip_labels[key] = lbl
            hroot.addWidget(lbl, 4, col)
        overlay_row = QHBoxLayout()
        overlay_row.addWidget(QLabel('Display overlays:'))
        self.chk_overlay_mean = QCheckBox('Mean')
        self.chk_overlay_median = QCheckBox('Median')
        self.chk_overlay_std = QCheckBox('Std dev')
        self.chk_overlay_percentiles = QCheckBox('Percentile lines')
        self.chk_overlay_removed = QCheckBox('Removed')
        self.chk_overlay_highlow = QCheckBox('High/Low pass')
        self.chk_overlay_very = QCheckBox('Very High/Very Low pass')
        for chk, state in [
            (self.chk_overlay_mean, True),
            (self.chk_overlay_median, True),
            (self.chk_overlay_std, True),
            (self.chk_overlay_percentiles, False),
            (self.chk_overlay_removed, True),
            (self.chk_overlay_highlow, True),
            (self.chk_overlay_very, True),
        ]:
            chk.setChecked(state)
            chk.stateChanged.connect(self._plot_histograms)
            overlay_row.addWidget(chk)
        overlay_row.addStretch(1)
        hroot.addLayout(overlay_row, 5, 0, 1, 3)
        if not HAS_MPL:
            warn = QLabel('Matplotlib is not available. Statistics, previews and extraction still work, but histograms cannot be displayed.')
            warn.setWordWrap(True); warn.setStyleSheet('color:#a33;')
            hroot.addWidget(warn, 2, 0, 1, 3)
        root.addWidget(hgb, 1)

        log_row = QHBoxLayout()
        self.log_button = QPushButton('Show Summary / Log')
        self.log_button.clicked.connect(self._toggle_summary_log)
        log_row.addWidget(self.log_button)
        log_row.addStretch(1)
        root.addLayout(log_row)
        self.summary = QPlainTextEdit(); self.summary.setReadOnly(True); self.summary.setMaximumHeight(95); self.summary.setVisible(False)
        root.addWidget(self.summary)

        preview_gb = self._collapsible_group('Preview Options')
        preview_grid = QGridLayout(preview_gb)
        self.preview_ratio = QComboBox(); self.preview_ratio.addItems(['All', 'U/Th', 'U/K', 'K/U', 'K/Th'])
        self.preview_level = QComboBox(); self.preview_level.addItems(['High / Low', 'Very High / Very Low'])
        self.preview_mask_mode = QComboBox(); self.preview_mask_mode.addItems(['Show removed mask', 'Show kept mask'])
        self.preview_mask_colour = QComboBox(); self.preview_mask_colour.addItems(['White', 'Black'])
        self.preview_opacity = QSlider(Qt.Horizontal); self.preview_opacity.setRange(0, 100); self.preview_opacity.setValue(45)
        self.preview_opacity_label = QLabel('45%')
        self.preview_opacity.valueChanged.connect(lambda v: self.preview_opacity_label.setText(f'{v}%'))
        preview_grid.addWidget(QLabel('Ratio:'), 0, 0); preview_grid.addWidget(self.preview_ratio, 0, 1)
        preview_grid.addWidget(QLabel('Threshold level:'), 0, 2); preview_grid.addWidget(self.preview_level, 0, 3)
        preview_grid.addWidget(QLabel('Preview mode:'), 1, 0); preview_grid.addWidget(self.preview_mask_mode, 1, 1)
        preview_grid.addWidget(QLabel('Mask colour:'), 1, 2); preview_grid.addWidget(self.preview_mask_colour, 1, 3)
        preview_grid.addWidget(QLabel('Opacity:'), 2, 0); preview_grid.addWidget(self.preview_opacity, 2, 1, 1, 2); preview_grid.addWidget(self.preview_opacity_label, 2, 3)
        refresh_preview = QPushButton('Refresh Preview'); refresh_preview.clicked.connect(self._create_threshold_previews)
        clear_preview = QPushButton('Clear Preview'); clear_preview.clicked.connect(self._clear_threshold_previews)
        preview_grid.addWidget(refresh_preview, 3, 0, 1, 2)
        preview_grid.addWidget(clear_preview, 3, 2, 1, 2)
        root.addWidget(preview_gb)

        threshold_btn_row = QHBoxLayout()
        threshold_btn_row.addStretch(1)
        self.gen_threshold_maps_btn = QPushButton('Generate Threshold Maps')
        self.gen_threshold_maps_btn.setMinimumHeight(32)
        self.gen_threshold_maps_btn.clicked.connect(self._generate_threshold_maps)
        threshold_btn_row.addWidget(self.gen_threshold_maps_btn)
        root.addLayout(threshold_btn_row)
        self.threshold_progress = QProgressBar()
        self.threshold_progress.setRange(0, 100)
        self.threshold_progress.setValue(0)
        self.threshold_progress.setFormat('Threshold map generation: %p%')
        self.threshold_progress.setVisible(False)
        root.addWidget(self.threshold_progress)
        return w

    def _make_trending_tab(self):
        w = QWidget(); root = QVBoxLayout(w)
        mode_gb = self._collapsible_group('Anomaly polygon extraction mode')
        mode_grid = QGridLayout(mode_gb)
        self.extraction_mode = QComboBox()
        self.extraction_mode.addItems([
            'Extract features based on threshold maps',
            'Extract features from threshold maps with trending'
        ])
        self.extraction_mode.currentIndexChanged.connect(lambda idx: self.enable_trend.setChecked(idx == 1) if hasattr(self, 'enable_trend') else None)
        mode_grid.addWidget(QLabel('Mode:'), 0, 0)
        mode_grid.addWidget(self.extraction_mode, 0, 1, 1, 3)
        root.addWidget(mode_gb)

        method_gb = self._collapsible_group('Polygon extraction method')
        method_grid = QGridLayout(method_gb)
        self.extract_level = QComboBox(); self.extract_level.addItems(['High / Low', 'Very High / Very Low', 'Both'])
        self.polygon_method = QComboBox()
        self.polygon_method.addItems([
            'Fast Mask Polygonise (Recommended)',
            'GDAL Polygonize Full Raster (Fallback)'
        ])
        self.use_min_pixels = QCheckBox('Remove small connected regions before polygonising')
        self.use_min_pixels.setChecked(True)
        self.min_pixels = QSpinBox(); self.min_pixels.setRange(0, 1000000); self.min_pixels.setValue(5)
        method_grid.addWidget(QLabel('Extraction level:'), 0, 0); method_grid.addWidget(self.extract_level, 0, 1, 1, 2)
        method_grid.addWidget(QLabel('Extraction method:'), 1, 0); method_grid.addWidget(self.polygon_method, 1, 1, 1, 3)
        method_grid.addWidget(self.use_min_pixels, 2, 0, 1, 3)
        method_grid.addWidget(QLabel('Minimum connected pixels:'), 2, 3); method_grid.addWidget(self.min_pixels, 2, 4)
        root.addWidget(method_gb)

        gb = self._collapsible_group('Continuity options applied after thresholding and before polygon extraction')
        grid = QGridLayout(gb)
        self.enable_trend = QCheckBox('Enable continuity / trending enhancement')
        self.enable_trend.setChecked(False)
        self.enable_trend.stateChanged.connect(lambda state: self.extraction_mode.setCurrentIndex(1 if state else 0) if hasattr(self, 'extraction_mode') else None)
        grid.addWidget(self.enable_trend,0,0,1,4)
        self.trend_method = QComboBox()
        self.trend_method.addItems(['Morphological closing (dilate then erode)', 'Dilation only', 'Focal majority / smooth mask'])
        grid.addWidget(QLabel('Method:'),1,0); grid.addWidget(self.trend_method,1,1,1,3)
        self.trend_kernel = QComboBox(); self.trend_kernel.addItems(['3 x 3','5 x 5','7 x 7','9 x 9','11 x 11','13 x 13','15 x 15'])
        self.trend_iterations = QSpinBox(); self.trend_iterations.setRange(1,10); self.trend_iterations.setValue(1)
        self.majority_fraction = QDoubleSpinBox(); self.majority_fraction.setRange(0.05,1.0); self.majority_fraction.setDecimals(2); self.majority_fraction.setSingleStep(0.05); self.majority_fraction.setValue(0.35)
        grid.addWidget(QLabel('Kernel:'),2,0); grid.addWidget(self.trend_kernel,2,1)
        grid.addWidget(QLabel('Iterations:'),2,2); grid.addWidget(self.trend_iterations,2,3)
        grid.addWidget(QLabel('Focal majority fraction:'),3,0); grid.addWidget(self.majority_fraction,3,1)
        self.min_area = QDoubleSpinBox(); self.min_area.setRange(0.0, 999999.0); self.min_area.setDecimals(4); self.min_area.setValue(0.0)
        grid.addWidget(QLabel('Minimum polygon area km²:'),4,0); grid.addWidget(self.min_area,4,1)
        root.addWidget(gb)

        smooth_gb = self._collapsible_group('Polygon smoothing / geometry refinement')
        smooth_grid = QGridLayout(smooth_gb)
        self.smooth_polygons = QCheckBox('Smooth polygons after extraction')
        self.smooth_polygons.setChecked(False)
        self.smooth_iterations = QSpinBox(); self.smooth_iterations.setRange(1, 20); self.smooth_iterations.setValue(2)
        self.smooth_offset = QDoubleSpinBox(); self.smooth_offset.setRange(0.01, 0.99); self.smooth_offset.setDecimals(2); self.smooth_offset.setSingleStep(0.05); self.smooth_offset.setValue(0.25)
        self.smooth_max_angle = QDoubleSpinBox(); self.smooth_max_angle.setRange(1.0, 180.0); self.smooth_max_angle.setDecimals(1); self.smooth_max_angle.setValue(180.0)
        smooth_grid.addWidget(self.smooth_polygons, 0, 0, 1, 4)
        smooth_grid.addWidget(QLabel('Iterations:'), 1, 0); smooth_grid.addWidget(self.smooth_iterations, 1, 1)
        smooth_grid.addWidget(QLabel('Offset:'), 1, 2); smooth_grid.addWidget(self.smooth_offset, 1, 3)
        smooth_grid.addWidget(QLabel('Maximum angle:'), 2, 0); smooth_grid.addWidget(self.smooth_max_angle, 2, 1)
        smooth_note = QLabel('Optional smoothing improves presentation but can slightly shift extracted boundaries.')
        smooth_note.setWordWrap(True)
        smooth_grid.addWidget(smooth_note, 3, 0, 1, 4)
        root.addWidget(smooth_gb)

        help_txt = QLabel(
            'Continuity enhancement connects or smooths threshold masks so polygons better follow continuous geological footprints. '
            'Use it cautiously: it can create continuity that is not present in the raw data. Preview first, then extract.'
        )
        help_txt.setWordWrap(True)
        root.addWidget(help_txt)

        self.extract_polygons_btn = QPushButton('Extract Anomaly Polygons')
        self.extract_polygons_btn.setMinimumHeight(36)
        self.extract_polygons_btn.clicked.connect(self._extract_polygons)
        root.addWidget(self.extract_polygons_btn)
        self.polygon_progress = QProgressBar(); self.polygon_progress.setRange(0, 100); self.polygon_progress.setValue(0); self.polygon_progress.setFormat('Polygon extraction: %p%'); self.polygon_progress.setVisible(False)
        root.addWidget(self.polygon_progress)

        self.trend_summary = QPlainTextEdit(); self.trend_summary.setReadOnly(True)
        root.addWidget(self.trend_summary, 1)
        return w

    def _collapsible_group(self, title):
        gb = QGroupBox(title)
        gb.setCheckable(True)
        # Keep the key sections open, collapse secondary sections by default to fit smaller docks.
        default_open = title in (
            'Input Radiometric Ratio Rasters',
            'Threshold Parameters',
            'Histogram display and manual threshold range controls',
            'Preview Options',
            'Anomaly polygon extraction mode',
            'Polygon extraction method',
            'Continuity options applied after thresholding and before polygon extraction'
        )
        gb.setChecked(default_open)
        def on_toggle(checked):
            for child in gb.findChildren(QWidget):
                if child is not gb:
                    child.setVisible(checked)
        gb.toggled.connect(on_toggle)
        on_toggle(default_open)
        return gb

    def _percent_spin(self, val):
        s = QDoubleSpinBox(); s.setRange(0.0, 100.0); s.setDecimals(2); s.setSingleStep(1.0); s.setValue(val); return s

    def _value_spin(self):
        s = QDoubleSpinBox(); s.setRange(0.0, 1.0e12); s.setDecimals(8); s.setSingleStep(0.1); s.setValue(0.0); return s

    # --------------------------------------------------------------- loading
    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select radiometric processing folder', self.processing_folder)
        if folder:
            self.processing_folder = folder
            self.folder_label.setText(folder)
            self._auto_from_processing_folder()

    def _refresh_layers(self):
        layers = [l for l in QgsProject.instance().mapLayers().values() if isinstance(l, QgsRasterLayer)]
        items = [(l.name(), l.source()) for l in layers]
        for cmb in (self.cmb_uth, self.cmb_uk, self.cmb_ku, self.cmb_kth):
            cmb.clear()
            for name, src in items:
                cmb.addItem(name, src)
        self._auto_select(self.cmb_uth, ['u_th', 'uth', 'u/th'])
        self._auto_select(self.cmb_uk, ['u_k', 'uk', 'u/k'])
        self._auto_select(self.cmb_ku, ['k_u', 'ku', 'k/u'])
        self._auto_select(self.cmb_kth, ['k_th', 'kth', 'k/th'])

    def _auto_select(self, combo, needles):
        for i in range(combo.count()):
            txt = (combo.itemText(i) + ' ' + str(combo.itemData(i))).lower().replace('\\','/').replace('-', '_')
            if any(n in txt for n in needles):
                combo.setCurrentIndex(i); return

    def _auto_from_processing_folder(self):
        folder = self.processing_folder
        if not folder:
            return
        ratio_dir = os.path.join(folder, '03_Ratio_Maps')
        candidates = {
            self.cmb_uth: ['U_Th_ratio.tif','UTh_ratio.tif','UTH_ratio.tif'],
            self.cmb_uk: ['U_K_ratio.tif','UK_ratio.tif'],
            self.cmb_ku: ['K_U_ratio.tif','KU_ratio.tif'],
            self.cmb_kth: ['K_Th_ratio.tif','KTH_ratio.tif'],
        }
        for combo, names in candidates.items():
            for name in names:
                p = os.path.join(ratio_dir, name)
                if os.path.exists(p):
                    # Ensure item exists in combo.
                    found = False
                    for i in range(combo.count()):
                        if os.path.normcase(combo.itemData(i)) == os.path.normcase(p):
                            combo.setCurrentIndex(i); found = True; break
                    if not found:
                        combo.addItem(os.path.basename(p), p); combo.setCurrentIndex(combo.count()-1)
                    break

    def _selected(self):
        return {'U_Th_ratio': self.cmb_uth.currentData(), 'U_K_ratio': self.cmb_uk.currentData(), 'K_U_ratio': self.cmb_ku.currentData(), 'K_Th_ratio': self.cmb_kth.currentData()}

    def _load_arrays(self):
        self._paths = self._selected(); self._raw = {}; self._profile = None
        for name, path in self._paths.items():
            if not path:
                raise RuntimeError('Missing raster selection for ' + name)
            arr, profile = read_raster(path)
            self._raw[name] = arr
            if self._profile is None:
                self._profile = profile
        shapes = {arr.shape for arr in self._raw.values()}
        if len(shapes) != 1:
            raise RuntimeError('Input ratio rasters must have the same dimensions.')
        self._apply_clipping()

    def _apply_clipping(self):
        self._clipped = {}; self._clip_report = {}
        for name, arr in self._raw.items():
            if self.enable_clip.isChecked():
                out, lo, hi, lp, hp = _winsorise_array(arr, self.clip_low.value(), self.clip_high.value())
            else:
                out = np.array(arr, copy=True); lo = hi = np.nan; lp = hp = 0.0
            self._clipped[name] = out
            self._clip_report[name] = (lo, hi, lp, hp)

    def _generate_reset_histograms(self):
        # Reset to raw-raster histogram view. Thresholds are recalculated after loading.
        self.enable_clip.setChecked(False)
        self._load_and_plot(recalculate=True)

    def _load_and_plot(self, recalculate=False):
        try:
            self._load_arrays()
            if recalculate or all(abs(x.value()) < 1.0e-12 for x in [self.uth_high, self.uth_vhigh, self.uk_high, self.uk_vhigh, self.kth_low, self.kth_vlow]):
                self._calculate_threshold_values(show_message=False)
            self._plot_histograms()
            self._update_summary()
        except Exception as e:
            QMessageBox.critical(self, 'Stage 2 ratio-map analysis failed', str(e))

    # ------------------------------------------------------------- thresholds
    def _nonnegative_threshold(self, value):
        """Clamp threshold values to zero or greater.

        Radiometric ratios should not use negative threshold cut-offs. Mean-minus-standard
        deviation can sometimes calculate a negative K/Th low threshold on noisy datasets;
        that value is not geologically useful for this workflow, so it is clamped to 0.
        """
        try:
            v = float(value)
        except Exception:
            return 0.0
        if not np.isfinite(v):
            return 0.0
        return max(0.0, v)

    def _clamp_threshold_boxes_nonnegative(self):
        for box in [self.uth_high, self.uth_vhigh, self.uk_high, self.uk_vhigh, self.ku_high, self.ku_vhigh, self.kth_low, self.kth_vlow]:
            if box.value() < 0:
                box.setValue(0.0)

    def _threshold_values(self):
        return {
            'U_Th_High': self._nonnegative_threshold(self.uth_high.value()), 'U_Th_VeryHigh': self._nonnegative_threshold(self.uth_vhigh.value()),
            'U_K_High': self._nonnegative_threshold(self.uk_high.value()), 'U_K_VeryHigh': self._nonnegative_threshold(self.uk_vhigh.value()),
            'K_U_High': self._nonnegative_threshold(self.ku_high.value()), 'K_U_VeryHigh': self._nonnegative_threshold(self.ku_vhigh.value()),
            'K_Th_Low': self._nonnegative_threshold(self.kth_low.value()), 'K_Th_VeryLow': self._nonnegative_threshold(self.kth_vlow.value()),
        }

    def _computed_thresholds(self):
        # Threshold boxes now store actual raster values, not percentiles.
        return self._threshold_values()

    def _calculate_threshold_values(self, show_message=True):
        if not self._clipped:
            self._load_arrays()
        mode = self.calc_mode.currentText().lower()
        vals = {k: self._clipped[k][np.isfinite(self._clipped[k])] for k in self._clipped}
        if 'percentile' in mode:
            uth_h = float(np.nanpercentile(vals['U_Th_ratio'], self.calc_high_pct.value()))
            uth_v = float(np.nanpercentile(vals['U_Th_ratio'], self.calc_vhigh_pct.value()))
            uk_h = float(np.nanpercentile(vals['U_K_ratio'], self.calc_high_pct.value()))
            uk_v = float(np.nanpercentile(vals['U_K_ratio'], self.calc_vhigh_pct.value()))
            ku_h = float(np.nanpercentile(vals['K_U_ratio'], self.calc_high_pct.value()))
            ku_v = float(np.nanpercentile(vals['K_U_ratio'], self.calc_vhigh_pct.value()))
            kth_l = float(np.nanpercentile(vals['K_Th_ratio'], self.calc_low_pct.value()))
            kth_v = float(np.nanpercentile(vals['K_Th_ratio'], self.calc_vlow_pct.value()))
        else:
            hs = float(self.calc_high_sigma.value()); vs = float(self.calc_vhigh_sigma.value())
            def mstd(a):
                return float(np.nanmean(a)), float(np.nanstd(a))
            m,s = mstd(vals['U_Th_ratio']); uth_h, uth_v = m + hs*s, m + vs*s
            m,s = mstd(vals['U_K_ratio']); uk_h, uk_v = m + hs*s, m + vs*s
            m,s = mstd(vals['K_U_ratio']); ku_h, ku_v = m + hs*s, m + vs*s
            m,s = mstd(vals['K_Th_ratio']); kth_l, kth_v = m - hs*s, m - vs*s
        uth_h = self._nonnegative_threshold(uth_h); uth_v = self._nonnegative_threshold(uth_v)
        uk_h = self._nonnegative_threshold(uk_h); uk_v = self._nonnegative_threshold(uk_v)
        ku_h = self._nonnegative_threshold(ku_h); ku_v = self._nonnegative_threshold(ku_v)
        kth_l = self._nonnegative_threshold(kth_l); kth_v = self._nonnegative_threshold(kth_v)
        self.uth_high.setValue(uth_h); self.uth_vhigh.setValue(uth_v)
        self.uk_high.setValue(uk_h); self.uk_vhigh.setValue(uk_v)
        self.ku_high.setValue(ku_h); self.ku_vhigh.setValue(ku_v)
        self.kth_low.setValue(kth_l); self.kth_vlow.setValue(kth_v)
        self._update_sliders_from_thresholds()
        self._plot_histograms()
        self._update_summary()
        if show_message:
            QMessageBox.information(self, 'Thresholds calculated', 'Threshold value boxes have been filled from the selected calculation method.')

    def _mask_for(self, key, level='high'):
        th = self._computed_thresholds()
        arr = self._clipped[key]
        if key == 'U_Th_ratio':
            t = th['U_Th_VeryHigh'] if level == 'very' else th['U_Th_High']
            return np.isfinite(arr) & (arr >= t)
        if key == 'U_K_ratio':
            t = th['U_K_VeryHigh'] if level == 'very' else th['U_K_High']
            return np.isfinite(arr) & (arr >= t)
        if key == 'K_U_ratio':
            t = th['K_U_VeryHigh'] if level == 'very' else th['K_U_High']
            return np.isfinite(arr) & (arr >= t)
        t = th['K_Th_VeryLow'] if level == 'very' else th['K_Th_Low']
        return np.isfinite(arr) & (arr <= t)

    def _trend_mask(self, mask):
        if not self.enable_trend.isChecked():
            return mask.astype(bool)
        size = int(self.trend_kernel.currentText().split()[0])
        it = int(self.trend_iterations.value())
        method = self.trend_method.currentText().lower()
        if 'closing' in method:
            return _square_erode(_square_dilate(mask, size, it), size, it)
        if 'dilation' in method:
            return _square_dilate(mask, size, it)
        frac = _focal_fraction(mask, size)
        return frac >= float(self.majority_fraction.value())

    # --------------------------------------------------------------- plotting
    def _slider_range_for(self, key):
        arr = self._clipped.get(key)
        if arr is None:
            return (0.0, 1.0)
        vals = arr[np.isfinite(arr)]
        if vals.size == 0:
            return (0.0, 1.0)
        lo = float(np.nanmin(vals)); hi = float(np.nanmax(vals))
        if math.isclose(lo, hi):
            hi = lo + 1.0
        return lo, hi

    def _value_to_slider(self, key, value):
        lo, hi = self._slider_range_for(key)
        return int(max(0, min(1000, round(1000.0 * (float(value) - lo) / (hi - lo)))))

    def _slider_to_value(self, key, pos):
        lo, hi = self._slider_range_for(key)
        return lo + (hi - lo) * (float(pos) / 1000.0)

    def _update_sliders_from_thresholds(self):
        if not getattr(self, '_sliders', None):
            return
        mapping = {
            'U_Th_ratio': (self.uth_high.value(), self.uth_vhigh.value()),
            'U_K_ratio': (self.uk_high.value(), self.uk_vhigh.value()),
            'K_U_ratio': (self.ku_high.value(), self.ku_vhigh.value()),
            # RangeSlider is always low-to-high left-to-right; for K/Th the lower handle is Very Low, upper handle is Low.
            'K_Th_ratio': (self.kth_vlow.value(), self.kth_low.value()),
        }
        for key, (v1, v2) in mapping.items():
            if key in self._sliders:
                slider = self._sliders[key]
                slider.blockSignals(True)
                slider.setValues(self._value_to_slider(key, v1), self._value_to_slider(key, v2))
                slider.blockSignals(False)

    def _range_slider_changed(self, key, low_pos, high_pos):
        if not self._clipped:
            return
        v_low = self._slider_to_value(key, low_pos)
        v_high = self._slider_to_value(key, high_pos)
        if key == 'U_Th_ratio':
            self.uth_high.setValue(v_low)
            self.uth_vhigh.setValue(v_high)
        elif key == 'U_K_ratio':
            self.uk_high.setValue(v_low)
            self.uk_vhigh.setValue(v_high)
        elif key == 'K_U_ratio':
            self.ku_high.setValue(v_low)
            self.ku_vhigh.setValue(v_high)
        else:
            self.kth_vlow.setValue(v_low)
            self.kth_low.setValue(v_high)
        self._plot_histograms()
        self._update_summary()

    def _toggle_summary_log(self):
        visible = not self.summary.isVisible()
        self.summary.setVisible(visible)
        self.log_button.setText('Hide Summary / Log' if visible else 'Show Summary / Log')

    def _manual_axis_range(self):
        """Return manual x-axis range if enabled and valid; otherwise None."""
        if not getattr(self, 'chk_manual_axis', None) or not self.chk_manual_axis.isChecked():
            return None
        try:
            xmin = float(self.axis_min.text())
            xmax = float(self.axis_max.text())
            if math.isfinite(xmin) and math.isfinite(xmax) and xmax > xmin:
                return xmin, xmax
        except Exception:
            logging.getLogger(__name__).debug("Optional operation failed in ThresholdReviewDialog._manual_axis_range; existing fallback retained.", exc_info=True)
        return None

    def _hist_bins_for(self, key, n_bins=80):
        """Use one shared bin set for raw and analysis.

        The default binning is based on the analysis/clipped distribution so the raw
        and analysis histograms are directly comparable after winsorising. If the
        user enables manual X range, that range is used for both histograms.
        """
        manual = self._manual_axis_range()
        if manual is not None and self.chk_show_raw.isChecked() and self.chk_show_analysis.isChecked():
            xmin, xmax = manual
        else:
            xmin, xmax = self._slider_range_for(key)
        if not math.isfinite(xmin) or not math.isfinite(xmax) or math.isclose(xmin, xmax):
            xmin, xmax = 0.0, 1.0
        return np.linspace(xmin, xmax, int(n_bins) + 1), xmin, xmax

    def _plot_histograms(self, *args):
        if not HAS_MPL:
            return
        if not self._raw or not self._clipped:
            return
        th = self._computed_thresholds()
        self._update_sliders_from_thresholds()
        labels = {
            'U_Th_ratio': ('U/Th ratio - HIGH threshold logic', 'U/Th ratio value'),
            'U_K_ratio': ('U/K ratio - HIGH threshold logic for RRAI-U', 'U/K ratio value'),
            'K_U_ratio': ('K/U ratio - HIGH threshold logic for HRAI-KU', 'K/U ratio value'),
            'K_Th_ratio': ('K/Th ratio - LOW threshold logic', 'K/Th ratio value'),
        }
        visible_keys = []
        for key in ['U_Th_ratio','U_K_ratio','K_U_ratio','K_Th_ratio']:
            visible = True
            if hasattr(self, '_hist_visible_checks') and key in self._hist_visible_checks:
                visible = self._hist_visible_checks[key].isChecked()
            if visible:
                visible_keys.append(key)
        # Hidden canvases/sliders collapse, so one visible histogram occupies the full row,
        # two occupy half each, and three occupy thirds.
        for key in ['U_Th_ratio','U_K_ratio','K_U_ratio','K_Th_ratio']:
            visible = key in visible_keys
            if key in getattr(self, '_hist_canvases', {}):
                self._hist_canvases[key].setVisible(visible)
            if key in getattr(self, '_sliders', {}):
                self._sliders[key].setVisible(visible)
            if key in getattr(self, '_clip_labels', {}):
                self._clip_labels[key].setVisible(visible)
        for key in visible_keys:
            fig = self._hist_figs.get(key)
            canv = self._hist_canvases.get(key)
            if fig is None or canv is None:
                continue
            fig.clear()
            ax = fig.add_subplot(1, 1, 1)
            raw = _valid_values(self._raw[key])
            clipped = _valid_values(self._clipped[key])
            bins, xmin, xmax = self._hist_bins_for(key)

            show_raw = getattr(self, 'chk_show_raw', None) is None or self.chk_show_raw.isChecked()
            show_analysis = getattr(self, 'chk_show_analysis', None) is None or self.chk_show_analysis.isChecked()
            if not show_raw and not show_analysis:
                show_analysis = True
                if getattr(self, 'chk_show_analysis', None):
                    self.chk_show_analysis.blockSignals(True); self.chk_show_analysis.setChecked(True); self.chk_show_analysis.blockSignals(False)

            if show_raw:
                ax.hist(raw, bins=bins, histtype='step', linewidth=0.95, color='#2c6db2', label='raw')
            if show_analysis:
                ax.hist(clipped, bins=bins, alpha=0.36, color='#f0c7ad', label='analysis')

            lo, hi, lp, hp = self._clip_report.get(key, (np.nan, np.nan, 0.0, 0.0))
            if key in getattr(self, '_clip_labels', {}):
                if self.enable_clip.isChecked():
                    self._clip_labels[key].setText(f'Clipped low {lp:.2f}% at {lo:.6g}; high {hp:.2f}% at {hi:.6g}')
                else:
                    self._clip_labels[key].setText('Clipped low 0.00%; high 0.00%')

            mean = float(np.nanmean(clipped)); med = float(np.nanmedian(clipped)); std = float(np.nanstd(clipped))
            if getattr(self, 'chk_overlay_mean', None) is None or self.chk_overlay_mean.isChecked():
                ax.axvline(mean, color='0.25', linestyle='-', linewidth=1.0, label='mean')
            if getattr(self, 'chk_overlay_median', None) is None or self.chk_overlay_median.isChecked():
                ax.axvline(med, color='0.25', linestyle='--', linewidth=1.0, label='median')
            if getattr(self, 'chk_overlay_std', None) is None or self.chk_overlay_std.isChecked():
                for n in [1,2]:
                    ax.axvline(mean+n*std, color='0.45', linestyle=':', linewidth=0.8, label='+/- std' if n == 1 else None)
                    ax.axvline(mean-n*std, color='0.45', linestyle=':', linewidth=0.8)
            if getattr(self, 'chk_overlay_percentiles', None) is not None and self.chk_overlay_percentiles.isChecked():
                for pct in [5, 10, 15, 50, 85, 90, 95]:
                    x = float(np.nanpercentile(clipped, pct))
                    ax.axvline(x, color='0.55', linestyle='-.', linewidth=0.75)
                    ymax = ax.get_ylim()[1]
                    ax.text(x, ymax * 0.96, f'P{pct}', rotation=90, va='top', ha='right', fontsize=6, color='0.35')

            if key == 'U_Th_ratio':
                primary = th['U_Th_High']; secondary = th['U_Th_VeryHigh']
                primary_label = 'High'; secondary_label = 'Very high'
                removed_span = (xmin, primary); high_span = (primary, xmax); very_span = (secondary, xmax)
            elif key == 'U_K_ratio':
                primary = th['U_K_High']; secondary = th['U_K_VeryHigh']
                primary_label = 'High'; secondary_label = 'Very high'
                removed_span = (xmin, primary); high_span = (primary, xmax); very_span = (secondary, xmax)
            elif key == 'K_U_ratio':
                primary = th['K_U_High']; secondary = th['K_U_VeryHigh']
                primary_label = 'High'; secondary_label = 'Very high'
                removed_span = (xmin, primary); high_span = (primary, xmax); very_span = (secondary, xmax)
            else:
                primary = th['K_Th_Low']; secondary = th['K_Th_VeryLow']
                primary_label = 'Low'; secondary_label = 'Very low'
                removed_span = (primary, xmax); high_span = (xmin, primary); very_span = (xmin, secondary)

            def _span(span, color, alpha, label):
                a, b = span
                a = max(xmin, min(xmax, a)); b = max(xmin, min(xmax, b))
                if b > a:
                    ax.axvspan(a, b, alpha=alpha, color=color, label=label)

            if getattr(self, 'chk_overlay_removed', None) is not None and self.chk_overlay_removed.isChecked():
                _span(removed_span, '0.65', 0.10, 'removed')
            if getattr(self, 'chk_overlay_highlow', None) is not None and self.chk_overlay_highlow.isChecked():
                _span(high_span, '#90ee90', 0.20, 'high/low pass')
            if getattr(self, 'chk_overlay_very', None) is not None and self.chk_overlay_very.isChecked():
                _span(very_span, '#008000', 0.16, 'very high/low pass')

            ax.axvline(primary, color='black', linestyle='-', linewidth=1.8, label=primary_label)
            ax.axvline(secondary, color='black', linestyle='--', linewidth=1.8, label=secondary_label)
            ymax = ax.get_ylim()[1]
            ax.text(primary, ymax * 0.92, primary_label, rotation=90, va='top', ha='right', fontsize=7, color='black')
            ax.text(secondary, ymax * 0.92, secondary_label, rotation=90, va='top', ha='left', fontsize=7, color='black')

            title, xlabel = labels[key]
            ax.set_title(title, fontsize=9)
            ax.set_xlabel(xlabel)
            ax.set_ylabel('Frequency')
            ax.set_xlim(xmin, xmax)
            ax.legend(loc='best', fontsize=6, framealpha=0.75)
            fig.tight_layout(pad=0.6)
            canv.draw()

    def _update_summary(self):
        if not self._clipped: return
        th = self._computed_thresholds(); lines = []
        for key in ['U_Th_ratio','U_K_ratio','K_U_ratio','K_Th_ratio']:
            vals = _valid_values(self._clipped[key]); lo, hi, lp, hp = self._clip_report[key]
            lines.append(f'{key}: valid={vals.size:,} min={np.nanmin(vals):.6g} max={np.nanmax(vals):.6g} mean={np.nanmean(vals):.6g} median={np.nanmedian(vals):.6g}')
            if self.enable_clip.isChecked():
                lines.append(f'  Winsorised at P{self.clip_low.value():g}/P{self.clip_high.value():g}: low capped={lp:.2f}% high capped={hp:.2f}% values=({lo:.6g}, {hi:.6g})')
            if key == 'U_Th_ratio':
                lines.append('  retained >= High %.2f%%; >= VeryHigh %.2f%%' % (100*np.mean(vals>=th['U_Th_High']), 100*np.mean(vals>=th['U_Th_VeryHigh'])))
            elif key == 'U_K_ratio':
                lines.append('  retained >= High %.2f%%; >= VeryHigh %.2f%%' % (100*np.mean(vals>=th['U_K_High']), 100*np.mean(vals>=th['U_K_VeryHigh'])))
            elif key == 'K_U_ratio':
                lines.append('  retained >= High %.2f%%; >= VeryHigh %.2f%%' % (100*np.mean(vals>=th['K_U_High']), 100*np.mean(vals>=th['K_U_VeryHigh'])))
            else:
                lines.append('  retained <= Low %.2f%%; <= VeryLow %.2f%%' % (100*np.mean(vals<=th['K_Th_Low']), 100*np.mean(vals<=th['K_Th_VeryLow'])))
        lines.append('')
        for k,v in th.items(): lines.append(f'{k} = {v:.10g}')
        self.summary.setPlainText('\n'.join(lines))

    # --------------------------------------------------------------- previews
    def _stage3_dir(self):
        folder = self.processing_folder or QFileDialog.getExistingDirectory(self, 'Select output folder for Stage 2 previews/extraction')
        if folder:
            self.processing_folder = folder; self.folder_label.setText(folder)
        return ensure_dir(os.path.join(folder, '05_Ratio_Threshold_Class_Maps')) if folder else ''

    def _ratio_folder_name(self, key):
        return {
            'U_Th_ratio': 'U_Th',
            'U_K_ratio': 'U_K',
            'K_U_ratio': 'K_U',
            'K_Th_ratio': 'K_Th',
        }.get(key, key)

    def _level_label(self, key, level):
        # K/Th uses low-tail logic; U/Th, U/K and K/U use high-tail logic.
        if key == 'K_Th_ratio':
            return 'very_low' if str(level).startswith('very') else 'low'
        return 'very_high' if str(level).startswith('very') else 'high'

    def _ratio_threshold_dir(self, key):
        return ensure_dir(os.path.join(self.processing_folder, '05_Ratio_Threshold_Class_Maps', self._ratio_folder_name(key)))

    def _ratio_polygon_dir(self, key):
        return ensure_dir(os.path.join(self.processing_folder, '06_Ratio_Map_Anomaly_Extraction', self._ratio_folder_name(key)))

    def _preview_keys(self):
        choice = self.preview_ratio.currentText() if hasattr(self, 'preview_ratio') else 'All'
        if choice == 'U/Th':
            return ['U_Th_ratio']
        if choice == 'U/K':
            return ['U_K_ratio']
        if choice == 'K/U':
            return ['K_U_ratio']
        if choice == 'K/Th':
            return ['K_Th_ratio']
        return ['U_Th_ratio', 'U_K_ratio', 'K_U_ratio', 'K_Th_ratio']

    def _style_preview_mask_layer(self, layer, colour_name='White', opacity=0.45):
        try:
            from qgis.PyQt.QtGui import QColor
            from qgis.core import QgsRasterShader, QgsColorRampShader, QgsSingleBandPseudoColorRenderer
            col = QColor(255, 255, 255) if colour_name.lower() == 'white' else QColor(0, 0, 0)
            shader = QgsRasterShader()
            ramp = QgsColorRampShader()
            ramp.setColorRampType(QgsColorRampShader.Discrete)
            ramp.setColorRampItemList([QgsColorRampShader.ColorRampItem(1, col, 'Mask')])
            shader.setRasterShaderFunction(ramp)
            renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
            layer.setRenderer(renderer)
            try:
                layer.setOpacity(float(opacity))
            except Exception:
                logging.getLogger(__name__).debug("Optional operation failed in ThresholdReviewDialog._style_preview_mask_layer; existing fallback retained.", exc_info=True)
            layer.setCustomProperty('radiometric_stage3_preview_mask', True)
            layer.triggerRepaint()
        except Exception:
            try:
                layer.setOpacity(float(opacity))
            except Exception:
                logging.getLogger(__name__).debug("Optional operation failed in ThresholdReviewDialog._style_preview_mask_layer; existing fallback retained.", exc_info=True)

    def _clear_threshold_previews(self):
        project = QgsProject.instance()
        for layer_id, layer in list(project.mapLayers().items()):
            try:
                is_preview = bool(layer.customProperty('radiometric_stage3_preview_mask', False)) or layer.name().startswith('Stage3_preview_')
            except Exception:
                is_preview = layer.name().startswith('Stage3_preview_')
            if is_preview:
                project.removeMapLayer(layer_id)

    def _create_threshold_previews(self):
        try:
            if not self._clipped:
                self._load_arrays()
            outdir = ensure_dir(os.path.join(self._stage3_dir(), 'preview_threshold'))
            if not outdir:
                return
            self._clear_threshold_previews()
            level = 'very' if self.preview_level.currentIndex() == 1 else 'high'
            show_kept = self.preview_mask_mode.currentText().lower().startswith('show kept')
            colour = self.preview_mask_colour.currentText()
            opacity = float(self.preview_opacity.value()) / 100.0
            written = []
            for key in self._preview_keys():
                accepted = self._mask_for(key, level)
                finite = np.isfinite(self._clipped[key])
                visible = accepted if show_kept else (finite & ~accepted)
                arr = np.zeros(accepted.shape, dtype=np.uint8)
                arr[visible] = 1
                label = {'U_Th_ratio': 'UTh', 'U_K_ratio': 'UK', 'K_U_ratio': 'KU', 'K_Th_ratio': 'KTh'}[key]
                mode = 'kept' if show_kept else 'removed'
                path = os.path.join(outdir, f'Stage3_preview_{label}_{level}_{mode}_mask.tif')
                try:
                    # Stable filename so Refresh Preview behaves like an update.
                    write_raster(path, arr, self._profile, gdal.GDT_Byte, nodata=0)
                except Exception:
                    # If Windows/QGIS has locked the previous temporary raster, write a versioned copy.
                    path = make_available_path(path)
                    write_raster(path, arr, self._profile, gdal.GDT_Byte, nodata=0)
                lyr = QgsRasterLayer(path, f'Stage3_preview_{label}_{level}_{mode}_mask')
                if lyr.isValid():
                    self._style_preview_mask_layer(lyr, colour, opacity)
                    QgsProject.instance().addMapLayer(lyr)
                written.append(path)
            txt = self.summary.toPlainText().strip()
            txt = (txt + '\n\n' if txt else '') + 'Preview refreshed:\n' + '\n'.join(written)
            self.summary.setPlainText(txt)
        except Exception as e:
            QMessageBox.critical(self, 'Could not refresh preview', str(e))

    def _create_trending_previews(self):
        try:
            if not self._clipped: self._load_arrays()
            outdir = ensure_dir(os.path.join(self._stage3_dir(), 'preview_trending'))
            if not outdir: return
            level_idx = self.extract_level.currentIndex()
            levels = ['high', 'very'] if level_idx == 2 else (['very'] if level_idx == 1 else ['high'])
            lines = []
            for level in levels:
                for key in ['U_Th_ratio','U_K_ratio','K_U_ratio','K_Th_ratio']:
                    raw_mask = self._mask_for(key, level)
                    tr_mask = self._trend_mask(raw_mask)
                    added = tr_mask & ~raw_mask
                    removed = raw_mask & ~tr_mask
                    tr_arr = np.where(tr_mask, 1, 0).astype(np.uint8)
                    diff_arr = np.zeros(raw_mask.shape, dtype=np.uint8)
                    diff_arr[raw_mask] = 1
                    diff_arr[added] = 2
                    diff_arr[removed] = 3
                    p1 = make_available_path(os.path.join(outdir, f'trended_mask_{key}_{level}.tif'))
                    p2 = make_available_path(os.path.join(outdir, f'trend_difference_{key}_{level}.tif'))
                    write_raster(p1, tr_arr, self._profile, gdal.GDT_Byte, nodata=0)
                    write_raster(p2, diff_arr, self._profile, gdal.GDT_Byte, nodata=0)
                    load_layer(p1, os.path.splitext(os.path.basename(p1))[0])
                    load_layer(p2, os.path.splitext(os.path.basename(p2))[0])
                    total = np.isfinite(self._clipped[key]).sum()
                    lines.append(f'{key} {level}: raw={100*np.count_nonzero(raw_mask)/total:.2f}% trended={100*np.count_nonzero(tr_mask)/total:.2f}% added={100*np.count_nonzero(added)/total:.2f}% removed={100*np.count_nonzero(removed)/total:.2f}%')
            self.trend_summary.setPlainText('\n'.join(lines))
            QMessageBox.information(self, 'Trending preview created', 'Trending preview rasters created and loaded.')
        except Exception as e:
            QMessageBox.critical(self, 'Could not create trending preview', str(e))

    # --------------------------------------------------------------- outputs
    def _save_csv(self, quiet=True):
        try:
            if not self._clipped: self._load_arrays()
            outdir = self._stage3_dir()
            if not outdir: return
            path = os.path.join(outdir, 'stage3_threshold_trending_settings.csv')
            th = self._computed_thresholds()
            stats = {k: array_stats(v, [5,10,15,50,85,90,95]) for k,v in self._clipped.items()}
            settings = dict(th)
            settings.update({'Threshold_calculation_mode': self.calc_mode.currentText()})
            settings.update({
                'Winsorise_enabled': self.enable_clip.isChecked(),
                'Clip_low_percentile': self.clip_low.value(),
                'Clip_high_percentile': self.clip_high.value(),
                'Trending_enabled': self.enable_trend.isChecked(),
                'Trending_method': self.trend_method.currentText(),
                'Trending_kernel': self.trend_kernel.currentText(),
                'Trending_iterations': self.trend_iterations.value(),
                'Focal_majority_fraction': self.majority_fraction.value(),
                'Minimum_area_km2': self.min_area.value(),
            })
            for k,(lo,hi,lp,hp) in self._clip_report.items():
                settings[f'{k}_clip_low_value'] = lo; settings[f'{k}_clip_high_value'] = hi
                settings[f'{k}_clip_low_pct'] = lp; settings[f'{k}_clip_high_pct'] = hp
            for name,path0 in self._paths.items(): settings[f'{name}_path'] = path0
            write_stats_csv(path, stats, settings)
            
            if not quiet:
                QMessageBox.information(self, 'Saved', 'Stage 2 settings CSV saved:\n\n' + path)
        except Exception as e:
            QMessageBox.critical(self, 'Could not save CSV', str(e))

    def _generate_threshold_maps(self):
        """Create class rasters from the current threshold settings without polygonising.

        Classes: 0 = background/removed, 1 = High or Low, 2 = Very High or Very Low.
        U/Th, U/K and K/U use high-tail logic. K/Th uses low-tail logic.
        """
        try:
            if not self._clipped:
                self._load_arrays()
            base = self._stage3_dir()
            if not base:
                return
            th = self._computed_thresholds()
            outputs = []
            specs = [
                ('U_Th_ratio', th['U_Th_High'], th['U_Th_VeryHigh'], 'high'),
                ('U_K_ratio', th['U_K_High'], th['U_K_VeryHigh'], 'high'),
                ('K_U_ratio', th['K_U_High'], th['K_U_VeryHigh'], 'high'),
                ('K_Th_ratio', th['K_Th_Low'], th['K_Th_VeryLow'], 'low'),
            ]
            if hasattr(self, 'threshold_progress'):
                self.threshold_progress.setVisible(True)
                self.threshold_progress.setValue(0)
            if hasattr(self, 'gen_threshold_maps_btn'):
                self.gen_threshold_maps_btn.setEnabled(False)
            QApplication.processEvents()
            total_specs = max(1, len(specs))
            for idx, (key, t1, t2, logic) in enumerate(specs, start=1):
                arr0 = self._clipped[key]
                out = np.zeros(arr0.shape, dtype=np.uint8)
                finite = np.isfinite(arr0)
                if logic == 'high':
                    out[finite & (arr0 >= t1)] = 1
                    out[finite & (arr0 >= t2)] = 2
                else:
                    out[finite & (arr0 <= t1)] = 1
                    out[finite & (arr0 <= t2)] = 2
                path = make_available_path(os.path.join(self._ratio_threshold_dir(key), f'{key}_threshold_class.tif'))
                write_raster(path, out, self._profile, gdal.GDT_Byte, nodata=0)
                load_layer(path, os.path.splitext(os.path.basename(path))[0])
                outputs.append(path)
                if hasattr(self, 'threshold_progress'):
                    self.threshold_progress.setValue(int(idx / total_specs * 100))
                QApplication.processEvents()
            self._save_csv(quiet=True)
            append_session_log(self.processing_folder, 'Stage 2 - Generate Ratio Threshold Class Maps', outputs=outputs, parameters={'Thresholds': th})
            if hasattr(self, 'threshold_progress'):
                self.threshold_progress.setValue(100)
            if hasattr(self, 'gen_threshold_maps_btn'):
                self.gen_threshold_maps_btn.setEnabled(True)
            QMessageBox.information(self, 'Threshold maps generated', 'Threshold class maps generated and loaded.\n\n' + '\n'.join(outputs))
        except Exception as e:
            if hasattr(self, 'gen_threshold_maps_btn'):
                self.gen_threshold_maps_btn.setEnabled(True)
            QMessageBox.critical(self, 'Threshold map generation failed', str(e))

    def _smooth_polygon_output(self, gpkg_path, layer_name):
        """Optionally smooth a polygon layer using QGIS native smoothgeometry."""
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
                self.trend_summary.appendPlainText('Polygon smoothing failed; using unsmoothed output. ' + str(e))
            except Exception:
                logging.getLogger(__name__).debug("Optional operation failed in ThresholdReviewDialog._smooth_polygon_output; existing fallback retained.", exc_info=True)
            return gpkg_path

    def _extract_polygons(self):
        try:
            if not self._clipped: self._load_arrays()
            base = self._stage3_dir()
            if not base: return
            level_idx = self.extract_level.currentIndex()
            levels = ['high', 'very'] if level_idx == 2 else (['very'] if level_idx == 1 else ['high'])
            area = float(self.min_area.value())
            outputs = []
            total_outputs = max(1, len(levels) * 4)
            done_outputs = 0
            if hasattr(self, 'polygon_progress'):
                self.polygon_progress.setVisible(True); self.polygon_progress.setValue(0)
            if hasattr(self, 'extract_polygons_btn'):
                self.extract_polygons_btn.setEnabled(False)
            QApplication.processEvents()
            for level in levels:
                for key in ['U_Th_ratio','U_K_ratio','K_U_ratio','K_Th_ratio']:
                    raw_mask = self._mask_for(key, level)
                    final_mask = self._trend_mask(raw_mask)
                    arr = np.zeros(raw_mask.shape, dtype=np.uint8)
                    arr[final_mask] = 1
                    level_name = self._level_label(key, level)
                    cls_name = f'{key}_{level_name}_trended_class' if self.enable_trend.isChecked() else f'{key}_{level_name}_class'
                    rp = make_available_path(os.path.join(self._ratio_threshold_dir(key), cls_name + '.tif'))
                    write_raster(rp, arr, self._profile, gdal.GDT_Byte, nodata=0)
                    gp = make_available_path(os.path.join(self._ratio_polygon_dir(key), cls_name + '_polygons.gpkg'))
                    min_pix = int(self.min_pixels.value()) if getattr(self, 'use_min_pixels', None) and self.use_min_pixels.isChecked() else 0
                    if getattr(self, 'polygon_method', None) and self.polygon_method.currentIndex() == 0:
                        polygonize_binary_mask_raster(rp, gp, cls_name, area, min_pix)
                    else:
                        polygonize_class_raster(rp, gp, cls_name, [1], area)
                    final_gp = self._smooth_polygon_output(gp, cls_name)
                    load_layer(rp, os.path.splitext(os.path.basename(rp))[0])
                    load_layer(final_gp, os.path.splitext(os.path.basename(final_gp))[0])
                    outputs.append(final_gp)
                    done_outputs += 1
                    if hasattr(self, 'polygon_progress'):
                        self.polygon_progress.setValue(int(done_outputs / total_outputs * 100))
                    QApplication.processEvents()
                    if getattr(self, '_stop_requested', False):
                        self.trend_summary.appendPlainText('Stop requested. Stopping after current output GPKG.')
                        self._save_csv(quiet=True)
                        append_session_log(self.processing_folder, 'Stage 2 - Extract Ratio Anomaly Polygons', status='stopped', outputs=outputs, parameters={'Extraction_level': self.extract_level.currentText(), 'Minimum_area_km2': area, 'Trending_enabled': self.enable_trend.isChecked()})
                        if hasattr(self, 'extract_polygons_btn'):
                            self.extract_polygons_btn.setEnabled(True)
                        QMessageBox.information(self, 'Extraction stopped', 'Stopped after current output GPKG:\n' + final_gp)
                        return
            self._save_csv(quiet=True)
            if hasattr(self, 'polygon_progress'):
                self.polygon_progress.setValue(100)
            if hasattr(self, 'extract_polygons_btn'):
                self.extract_polygons_btn.setEnabled(True)
            append_session_log(self.processing_folder, 'Stage 2 - Extract Ratio Anomaly Polygons', outputs=outputs, parameters={'Extraction_level': self.extract_level.currentText(), 'Minimum_area_km2': area, 'Trending_enabled': self.enable_trend.isChecked()})
            QMessageBox.information(self, 'Extraction complete', 'Anomaly polygons extracted and loaded.\n\n' + '\n'.join(outputs))
        except Exception as e:
            if hasattr(self, 'extract_polygons_btn'):
                self.extract_polygons_btn.setEnabled(True)
            QMessageBox.critical(self, 'Extraction failed', str(e))
