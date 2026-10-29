import os
import csv
import math
import re

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFileDialog, QDoubleSpinBox, QTextEdit, QCheckBox, QMessageBox,
    QGroupBox, QFormLayout, QLineEdit, QTabWidget, QWidget, QListWidget,
    QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsFeature, QgsGeometry,
    QgsPointXY, QgsFields, QgsField, QgsVectorFileWriter, QgsCoordinateTransformContext,
    QgsCoordinateReferenceSystem
)


class CDPIntervalDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle('Seismic CDP Interval Annotator')
        self.resize(980, 780)
        self.setMinimumSize(900, 680)
        self._build_ui()
        self.refresh_layers()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel('Seismic CDP Interval Annotator + SEG-Y Navigation Converter')
        title.setStyleSheet('font-size: 21px; font-weight: 800; padding: 10px 8px; color: #12324a;')
        layout.addWidget(title)

        info = QLabel(
            'Create sparse CDP annotation points at any interval and export Kingdom-friendly postings. '
            'The SEG-Y tab can convert SEG-Y trace navigation headers into QGIS point and line layers.'
        )
        info.setWordWrap(True)
        info.setStyleSheet('color: #4b5563; padding: 0 8px 8px 8px;')
        layout.addWidget(info)

        self.setStyleSheet('''
            QDialog { background: #f7f9fb; }
            QGroupBox { font-weight: 700; border: 1px solid #d7dee8; border-radius: 8px; margin-top: 12px; padding: 12px; background: #ffffff; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #12324a; }
            QPushButton { padding: 7px 12px; border-radius: 6px; background: #edf2f7; border: 1px solid #cbd5e1; }
            QPushButton:hover { background: #e2e8f0; }
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit, QListWidget, QTableWidget { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 5px; padding: 4px; }
            QTabWidget::pane { border: 1px solid #d7dee8; border-radius: 8px; background: #ffffff; }
            QTabBar::tab { padding: 9px 14px; margin-right: 2px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #12324a; color: white; font-weight: 700; }
        ''')

        self.tabs = QTabWidget()
        self.tabs.addTab(self._make_interval_tab(), 'CDP interval annotation')
        self.tabs.addTab(self._make_segy_tab(), 'SEG-Y to points/lines')
        layout.addWidget(self.tabs)

    # ------------------------------------------------------------------
    # TAB 1: CDP interval annotation from existing CDP point layer
    # ------------------------------------------------------------------
    def _make_interval_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        input_box = QGroupBox('Input CDP Point Layer')
        form = QFormLayout(input_box)
        self.layer_combo = QComboBox()
        self.line_field_combo = QComboBox()
        self.cdp_field_combo = QComboBox()
        form.addRow('CDP point layer:', self.layer_combo)
        form.addRow('Line name field:', self.line_field_combo)
        form.addRow('CDP field:', self.cdp_field_combo)
        layout.addWidget(input_box)

        self.layer_combo.currentIndexChanged.connect(self.refresh_fields)

        param_box = QGroupBox('Annotation Settings')
        pform = QFormLayout(param_box)
        self.interval_km = QDoubleSpinBox()
        self.interval_km.setDecimals(3)
        self.interval_km.setRange(0.001, 1000)
        self.interval_km.setValue(6.0)
        self.interval_km.setSuffix(' km')
        self.prefix_first = QCheckBox('Include first CDP/start point for each line')
        self.prefix_first.setChecked(True)
        self.include_end = QCheckBox('Include end point for each line')
        self.include_end.setChecked(False)
        self.label_template = QLineEdit('{Line} - CDP {CDP}')
        pform.addRow('Interval:', self.interval_km)
        pform.addRow('', self.prefix_first)
        pform.addRow('', self.include_end)
        pform.addRow('Label template:', self.label_template)
        layout.addWidget(param_box)

        output_box = QGroupBox('Outputs')
        oform = QFormLayout(output_box)
        self.output_folder = QLineEdit()
        browse_btn = QPushButton('Browse')
        browse_btn.clicked.connect(self.choose_output_folder)
        row = QHBoxLayout()
        row.addWidget(self.output_folder)
        row.addWidget(browse_btn)
        self.output_name = QLineEdit('CDP_Interval_Annotation')
        self.add_to_map = QCheckBox('Add output shapefile to QGIS map')
        self.add_to_map.setChecked(True)
        oform.addRow('Output folder:', row)
        oform.addRow('Output base name:', self.output_name)
        oform.addRow('', self.add_to_map)
        layout.addWidget(output_box)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        buttons = QHBoxLayout()
        self.run_btn = QPushButton('Run interval annotation')
        close_btn = QPushButton('Close')
        self.run_btn.clicked.connect(self.run_tool)
        close_btn.clicked.connect(self.close)
        buttons.addStretch()
        buttons.addWidget(self.run_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        return tab

    def log_msg(self, msg):
        self.log.append(msg)

    def refresh_layers(self):
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.geometryType() == QgsWkbTypes.PointGeometry:
                self.layer_combo.addItem(lyr.name(), lyr.id())
        self.layer_combo.blockSignals(False)
        self.refresh_fields()

    def current_layer(self):
        layer_id = self.layer_combo.currentData()
        return QgsProject.instance().mapLayer(layer_id) if layer_id else None

    def refresh_fields(self):
        self.line_field_combo.clear()
        self.cdp_field_combo.clear()
        lyr = self.current_layer()
        if not lyr:
            return
        for f in lyr.fields():
            self.line_field_combo.addItem(f.name())
            self.cdp_field_combo.addItem(f.name())
        for combo, candidates in [
            (self.line_field_combo, ['Line', 'LINE', 'line', 'LINENAME', 'LineName', 'Profile']),
            (self.cdp_field_combo, ['CDP', 'cdp', 'Cdp', 'CDP_NO', 'CDPNo', 'SP', 'SHOTPOINT', 'Trace'])
        ]:
            for c in candidates:
                idx = combo.findText(c)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                    break

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select output folder')
        if folder:
            self.output_folder.setText(folder)


    def _list_contains(self, list_widget, value):
        """Return True if a QListWidget already contains the exact text value.

        This prevents duplicate SEG-Y file entries when the same file is
        selected more than once.
        """
        for i in range(list_widget.count()):
            if list_widget.item(i).text() == value:
                return True
        return False

    def _safe_float(self, value):
        try:
            return float(value)
        except Exception:
            return None

    def _nearest_original_by_chainage(self, target_dist, chainages):
        return min(chainages, key=lambda t: abs(t[0] - target_dist))[1]

    def run_tool(self):
        self.log.clear()
        lyr = self.current_layer()
        if not lyr:
            QMessageBox.warning(self, 'Missing input', 'Please load/select a CDP point layer.')
            return

        line_field = self.line_field_combo.currentText()
        cdp_field = self.cdp_field_combo.currentText()
        out_folder = self.output_folder.text().strip()
        base = self.output_name.text().strip() or 'CDP_Interval_Annotation'
        interval_m = float(self.interval_km.value()) * 1000.0

        if not out_folder:
            QMessageBox.warning(self, 'Missing output folder', 'Please choose an output folder.')
            return
        os.makedirs(out_folder, exist_ok=True)

        crs = lyr.crs()
        if crs.isGeographic():
            self.log_msg('WARNING: Input CRS is geographic (degrees). Reproject to UTM/metre CRS before running for true km intervals.')

        self.log_msg(f'Input layer: {lyr.name()}')
        self.log_msg(f'Line field: {line_field}; CDP field: {cdp_field}; Interval: {interval_m:.2f} m')

        groups = {}
        skipped = 0
        for feat in lyr.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                skipped += 1
                continue
            line = str(feat[line_field])
            cdp = self._safe_float(feat[cdp_field])
            if cdp is None:
                skipped += 1
                continue
            pt = geom.asPoint() if not geom.isMultipart() else geom.asMultiPoint()[0]
            groups.setdefault(line, []).append((cdp, QgsPointXY(pt)))

        out_fields = QgsFields()
        out_fields.append(QgsField('Line', QVariant.String, len=80))
        out_fields.append(QgsField('CDP', QVariant.Int))
        out_fields.append(QgsField('Dist_m', QVariant.Double, len=20, prec=2))
        out_fields.append(QgsField('Lbl', QVariant.String, len=120))
        out_fields.append(QgsField('X', QVariant.Double, len=20, prec=3))
        out_fields.append(QgsField('Y', QVariant.Double, len=20, prec=3))

        mem = QgsVectorLayer(f'Point?crs={crs.authid()}', base, 'memory')
        pr = mem.dataProvider()
        pr.addAttributes(out_fields)
        mem.updateFields()

        total_created = 0
        label_tpl = self.label_template.text().strip() or '{Line} - CDP {CDP}'

        for line, items in groups.items():
            if len(items) < 2:
                self.log_msg(f'Skipped {line}: fewer than 2 CDP points')
                continue
            items.sort(key=lambda x: x[0])
            pts = [p for _, p in items]
            line_geom = QgsGeometry.fromPolylineXY(pts)
            length = line_geom.length()
            if length <= 0:
                self.log_msg(f'Skipped {line}: zero length')
                continue

            chainages = []
            for cdp, pt in items:
                d = line_geom.lineLocatePoint(QgsGeometry.fromPointXY(pt))
                chainages.append((d, int(round(cdp))))

            distances = []
            if self.prefix_first.isChecked():
                distances.append(0.0)
            d = interval_m
            while d < length:
                distances.append(d)
                d += interval_m
            if self.include_end.isChecked() and (not distances or abs(distances[-1] - length) > 1e-6):
                distances.append(length)

            made_for_line = 0
            for dist in distances:
                ip = line_geom.interpolate(dist)
                if ip.isEmpty():
                    continue
                pt = ip.asPoint()
                nearest_cdp = self._nearest_original_by_chainage(dist, chainages)
                lbl = label_tpl.replace('{Line}', str(line)).replace('{CDP}', str(nearest_cdp)).replace('{Dist_km}', f'{dist/1000.0:.2f}')
                f = QgsFeature(mem.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt)))
                f.setAttributes([str(line), int(nearest_cdp), round(dist, 2), lbl, round(pt.x(), 3), round(pt.y(), 3)])
                pr.addFeature(f)
                total_created += 1
                made_for_line += 1
            self.log_msg(f'{line}: length {length/1000.0:.2f} km, created {made_for_line} annotation points')

        mem.updateExtents()
        self._write_point_outputs(mem, out_folder, base, self.add_to_map.isChecked(), self.log_msg)
        self.log_msg('')
        self.log_msg(f'Done. Created {total_created} interval annotation points. Skipped raw records: {skipped}')
        QMessageBox.information(self, 'Complete', f'Created {total_created} interval annotation points. Outputs saved in:\n{out_folder}')

    # ------------------------------------------------------------------
    # TAB 2: SEG-Y to navigation points/lines
    # ------------------------------------------------------------------
    def _make_segy_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        input_box = QGroupBox('1. Input SEG-Y Files')
        form = QFormLayout(input_box)
        self.segy_files = QListWidget()
        self.segy_files.setMinimumHeight(105)
        file_buttons = QHBoxLayout()
        add_btn = QPushButton('＋ Add SEG-Y files')
        clear_btn = QPushButton('Clear list')
        add_btn.clicked.connect(self.add_segy_files)
        clear_btn.clicked.connect(self.clear_segy_files)
        file_buttons.addWidget(add_btn)
        file_buttons.addWidget(clear_btn)
        form.addRow('Selected files:', self.segy_files)
        form.addRow('', file_buttons)
        layout.addWidget(input_box)

        naming_box = QGroupBox('2. Line Naming / Renaming')
        nlayout = QVBoxLayout(naming_box)
        help_lbl = QLabel(
            'Edit the output line name before conversion. Example: source file '
            '2023VB_401_PSDM_TVFGAIN.sgy can be renamed to VB_401. The point label will become VB_401_CDP_1123.'
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet('color: #4b5563;')
        nlayout.addWidget(help_lbl)
        self.line_name_table = QTableWidget(0, 3)
        self.line_name_table.setHorizontalHeaderLabels(['SEG-Y file', 'Output line name', 'Point label preview'])
        self.line_name_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.line_name_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.line_name_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.line_name_table.setMinimumHeight(135)
        self.line_name_table.itemChanged.connect(self._line_name_item_changed)
        nlayout.addWidget(self.line_name_table)
        naming_buttons = QHBoxLayout()
        auto_btn = QPushButton('Auto-clean names')
        auto_btn.clicked.connect(self.auto_clean_line_names)
        naming_buttons.addStretch()
        naming_buttons.addWidget(auto_btn)
        nlayout.addLayout(naming_buttons)
        layout.addWidget(naming_box)

        header_box = QGroupBox('3. SEG-Y Header / Navigation Settings')
        hform = QFormLayout(header_box)
        self.xy_source_combo = QComboBox()
        self.xy_source_combo.addItems(['CDP_X / CDP_Y', 'SourceX / SourceY', 'GroupX / GroupY'])
        self.cdp_header_combo = QComboBox()
        self.cdp_header_combo.addItems(['CDP', 'TRACE_SEQUENCE_FILE', 'TRACE_SEQUENCE_LINE'])
        self.trace_step = QSpinBox()
        self.trace_step.setRange(1, 100000)
        self.trace_step.setValue(1)
        self.trace_step.setToolTip('Use 1 to export every trace. Use 10, 50, or 100 to thin very large SEG-Y files.')
        self.apply_scalar = QCheckBox('Apply SEG-Y coordinate scalar')
        self.apply_scalar.setChecked(True)
        self.drop_zero_xy = QCheckBox('Skip traces with zero or missing X/Y')
        self.drop_zero_xy.setChecked(True)
        self.segy_crs = QLineEdit('EPSG:32630')
        self.segy_crs.setToolTip('Set the CRS of the coordinates stored in the SEG-Y headers, e.g. EPSG:32630 for WGS 84 / UTM Zone 30N.')
        self.segy_label_template = QLineEdit('{Line}_CDP_{CDP}')
        self.segy_label_template.setToolTip('Use {Line}, {CDP}, {TraceNo}, {FileName}. Default gives VB_401_CDP_1123.')
        hform.addRow('Navigation headers:', self.xy_source_combo)
        hform.addRow('CDP/trace value:', self.cdp_header_combo)
        hform.addRow('Trace sampling step:', self.trace_step)
        hform.addRow('', self.apply_scalar)
        hform.addRow('', self.drop_zero_xy)
        hform.addRow('Output CRS:', self.segy_crs)
        hform.addRow('Point label template:', self.segy_label_template)
        layout.addWidget(header_box)

        output_box = QGroupBox('4. Outputs')
        oform = QFormLayout(output_box)
        self.segy_output_folder = QLineEdit()
        browse_btn = QPushButton('Browse')
        browse_btn.clicked.connect(self.choose_segy_output_folder)
        row = QHBoxLayout()
        row.addWidget(self.segy_output_folder)
        row.addWidget(browse_btn)
        self.segy_output_name = QLineEdit('SEGY_Navigation')
        self.segy_add_to_map = QCheckBox('Add point and line shapefiles to QGIS map')
        self.segy_add_to_map.setChecked(True)
        oform.addRow('Output folder:', row)
        oform.addRow('Output base name:', self.segy_output_name)
        oform.addRow('', self.segy_add_to_map)
        layout.addWidget(output_box)

        note = QLabel(
            'Requirement: install segyio in the QGIS Python environment. Windows/OSGeo4W: python -m pip install segyio'
        )
        note.setWordWrap(True)
        note.setStyleSheet('color: #6b7280; font-style: italic; padding: 4px;')
        layout.addWidget(note)

        self.segy_log = QTextEdit()
        self.segy_log.setReadOnly(True)
        self.segy_log.setMinimumHeight(90)
        layout.addWidget(self.segy_log)

        buttons = QHBoxLayout()
        run_btn = QPushButton('▶ Convert SEG-Y to points and lines')
        close_btn = QPushButton('Close')
        run_btn.clicked.connect(self.run_segy_converter)
        close_btn.clicked.connect(self.close)
        buttons.addStretch()
        buttons.addWidget(run_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        return tab

    def segy_log_msg(self, msg):
        self.segy_log.append(msg)

    def add_segy_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Select SEG-Y files', '', 'SEG-Y files (*.sgy *.segy *.SEG *.SGY);;All files (*.*)')
        for p in paths:
            if p and not self._list_contains(self.segy_files, p):
                self.segy_files.addItem(p)
                self._add_line_name_row(p)

    def clear_segy_files(self):
        self.segy_files.clear()
        self.line_name_table.setRowCount(0)

    def _guess_line_name(self, path):
        stem = os.path.splitext(os.path.basename(path))[0]
        # Prefer names like VB_401, VB401, VB-401A embedded in processing filenames.
        m = re.search(r'(VB[_-]?\d+[A-Za-z]?)', stem, re.IGNORECASE)
        if m:
            val = m.group(1).upper().replace('-', '_')
            val = re.sub(r'^VB(\d)', r'VB_\1', val)
            return val
        # Fallback: strip common processing suffixes but preserve a readable name.
        cleaned = re.sub(r'^(19|20)\d{2}', '', stem)
        cleaned = re.sub(r'(_?PSDM.*|_?PSTM.*|_?TVFGAIN.*|_?MIG.*|_?STACK.*)$', '', cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip('_- ')
        return cleaned[:80] or stem[:80]

    def _add_line_name_row(self, path):
        row = self.line_name_table.rowCount()
        self.line_name_table.insertRow(row)
        file_item = QTableWidgetItem(os.path.basename(path))
        file_item.setToolTip(path)
        file_item.setFlags(file_item.flags() & ~Qt.ItemIsEditable)
        name_item = QTableWidgetItem(self._guess_line_name(path))
        preview_item = QTableWidgetItem(f'{name_item.text()}_CDP_1123')
        preview_item.setFlags(preview_item.flags() & ~Qt.ItemIsEditable)
        self.line_name_table.setItem(row, 0, file_item)
        self.line_name_table.setItem(row, 1, name_item)
        self.line_name_table.setItem(row, 2, preview_item)

    def _line_name_item_changed(self, item):
        if item.column() != 1:
            return
        row = item.row()
        name = item.text().strip() or self._guess_line_name(self.segy_files.item(row).text())
        # Keep names safe for shapefile/Kingdom text exports.
        name = re.sub(r'[^A-Za-z0-9_\-]+', '_', name)[:80]
        if item.text() != name:
            self.line_name_table.blockSignals(True)
            item.setText(name)
            self.line_name_table.blockSignals(False)
        preview = f'{name}_CDP_1123'
        if self.line_name_table.item(row, 2):
            self.line_name_table.item(row, 2).setText(preview)

    def auto_clean_line_names(self):
        self.line_name_table.blockSignals(True)
        for row in range(self.line_name_table.rowCount()):
            path = self.segy_files.item(row).text()
            name = self._guess_line_name(path)
            self.line_name_table.item(row, 1).setText(name)
            self.line_name_table.item(row, 2).setText(f'{name}_CDP_1123')
        self.line_name_table.blockSignals(False)

    def _line_name_for_path(self, row, path):
        try:
            name = self.line_name_table.item(row, 1).text().strip()
        except Exception:
            name = ''
        return (name or self._guess_line_name(path))[:80]

    def choose_segy_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select SEG-Y output folder')
        if folder:
            self.segy_output_folder.setText(folder)

    def _scaled_coord(self, value, scalar):
        try:
            v = float(value)
            s = int(scalar)
        except Exception:
            return None
        if not self.apply_scalar.isChecked() or s in (0, 1):
            return v
        if s > 1:
            return v * s
        if s < 0:
            return v / abs(s)
        return v

    def _tf(self, segyio, name, fallback=None):
        return getattr(segyio.TraceField, name, fallback)

    def _header_value(self, header, field_constant, default=None):
        if field_constant is None:
            return default
        try:
            return header[field_constant]
        except Exception:
            return default

    def run_segy_converter(self):
        self.segy_log.clear()
        try:
            import segyio
        except Exception as exc:
            QMessageBox.critical(
                self, 'Missing segyio',
                'The Python package segyio is not available in this QGIS Python environment.\n\n'
                'Install it from OSGeo4W Shell using:\npython -m pip install segyio\n\n'
                f'Details: {exc}'
            )
            return

        files = [self.segy_files.item(i).text() for i in range(self.segy_files.count())]
        out_folder = self.segy_output_folder.text().strip()
        base = self.segy_output_name.text().strip() or 'SEGY_Navigation'
        crs_text = self.segy_crs.text().strip() or 'EPSG:32630'
        crs = QgsCoordinateReferenceSystem(crs_text)
        if not crs.isValid():
            QMessageBox.warning(self, 'Invalid CRS', f'Output CRS is invalid: {crs_text}')
            return
        if not files:
            QMessageBox.warning(self, 'Missing input', 'Please add at least one SEG-Y file.')
            return
        if not out_folder:
            QMessageBox.warning(self, 'Missing output folder', 'Please choose an output folder.')
            return
        os.makedirs(out_folder, exist_ok=True)

        p_fields = QgsFields()
        p_fields.append(QgsField('Line', QVariant.String, len=80))
        p_fields.append(QgsField('CDP', QVariant.Int))
        p_fields.append(QgsField('TraceNo', QVariant.Int))
        p_fields.append(QgsField('CDP_Name', QVariant.String, len=120))
        p_fields.append(QgsField('FileName', QVariant.String, len=120))
        p_fields.append(QgsField('OrigName', QVariant.String, len=120))
        p_fields.append(QgsField('X', QVariant.Double, len=20, prec=3))
        p_fields.append(QgsField('Y', QVariant.Double, len=20, prec=3))
        p_fields.append(QgsField('Scalar', QVariant.Int))
        p_fields.append(QgsField('Lbl', QVariant.String, len=120))

        l_fields = QgsFields()
        l_fields.append(QgsField('Line', QVariant.String, len=80))
        l_fields.append(QgsField('FileName', QVariant.String, len=120))
        l_fields.append(QgsField('OrigName', QVariant.String, len=120))
        l_fields.append(QgsField('FirstCDP', QVariant.Int))
        l_fields.append(QgsField('LastCDP', QVariant.Int))
        l_fields.append(QgsField('N_Traces', QVariant.Int))
        l_fields.append(QgsField('Length_m', QVariant.Double, len=20, prec=2))

        points_layer = QgsVectorLayer(f'Point?crs={crs.authid()}', base + '_CDP_Points', 'memory')
        lines_layer = QgsVectorLayer(f'LineString?crs={crs.authid()}', base + '_Lines', 'memory')
        ppr = points_layer.dataProvider(); ppr.addAttributes(p_fields); points_layer.updateFields()
        lpr = lines_layer.dataProvider(); lpr.addAttributes(l_fields); lines_layer.updateFields()

        nav_choice = self.xy_source_combo.currentText()
        cdp_choice = self.cdp_header_combo.currentText()
        step = int(self.trace_step.value())

        # Header constants. getattr makes the plugin tolerant of segyio version differences.
        scalar_f = self._tf(segyio, 'SourceGroupScalar', None)
        if nav_choice.startswith('CDP'):
            x_f = self._tf(segyio, 'CDP_X', None)
            y_f = self._tf(segyio, 'CDP_Y', None)
        elif nav_choice.startswith('Source'):
            x_f = self._tf(segyio, 'SourceX', None)
            y_f = self._tf(segyio, 'SourceY', None)
        else:
            x_f = self._tf(segyio, 'GroupX', None)
            y_f = self._tf(segyio, 'GroupY', None)

        if cdp_choice == 'CDP':
            cdp_f = self._tf(segyio, 'CDP', None)
        elif cdp_choice == 'TRACE_SEQUENCE_FILE':
            cdp_f = self._tf(segyio, 'TRACE_SEQUENCE_FILE', self._tf(segyio, 'TraceNumber', None))
        else:
            cdp_f = self._tf(segyio, 'TRACE_SEQUENCE_LINE', self._tf(segyio, 'TraceSequenceLine', None))

        total_points = 0
        total_lines = 0
        skipped = 0

        for row_idx, path in enumerate(files):
            fname = os.path.basename(path)
            original_line_name = os.path.splitext(fname)[0][:120]
            line_name = self._line_name_for_path(row_idx, path)
            self.segy_log_msg(f'Reading {fname} ...')
            pts_for_line = []
            cdps_for_line = []
            try:
                with segyio.open(path, 'r', ignore_geometry=True) as sf:
                    ntr = len(sf.trace)
                    for i in range(0, ntr, step):
                        h = sf.header[i]
                        raw_x = self._header_value(h, x_f)
                        raw_y = self._header_value(h, y_f)
                        scalar = self._header_value(h, scalar_f, 1)
                        x = self._scaled_coord(raw_x, scalar)
                        y = self._scaled_coord(raw_y, scalar)
                        if x is None or y is None or (self.drop_zero_xy.isChecked() and (x == 0 or y == 0)):
                            skipped += 1
                            continue
                        cdp_raw = self._header_value(h, cdp_f, i + 1)
                        try:
                            cdp = int(round(float(cdp_raw)))
                        except Exception:
                            cdp = i + 1
                        qpt = QgsPointXY(float(x), float(y))
                        pts_for_line.append(qpt)
                        cdps_for_line.append(cdp)
                        label_tpl = self.segy_label_template.text().strip() or '{Line}_CDP_{CDP}'
                        label = (label_tpl.replace('{Line}', str(line_name))
                                          .replace('{CDP}', str(cdp))
                                          .replace('{TraceNo}', str(i + 1))
                                          .replace('{FileName}', fname))
                        cdp_name = f'{line_name}_CDP_{cdp}'
                        pf = QgsFeature(points_layer.fields())
                        pf.setGeometry(QgsGeometry.fromPointXY(qpt))
                        pf.setAttributes([line_name, cdp, i + 1, cdp_name, fname, original_line_name, round(float(x), 3), round(float(y), 3), int(scalar or 1), label])
                        ppr.addFeature(pf)
                        total_points += 1
            except Exception as exc:
                self.segy_log_msg(f'ERROR reading {fname}: {exc}')
                continue

            if len(pts_for_line) >= 2:
                geom = QgsGeometry.fromPolylineXY(pts_for_line)
                lf = QgsFeature(lines_layer.fields())
                lf.setGeometry(geom)
                lf.setAttributes([line_name, fname, original_line_name, int(cdps_for_line[0]), int(cdps_for_line[-1]), len(pts_for_line), round(geom.length(), 2)])
                lpr.addFeature(lf)
                total_lines += 1
                self.segy_log_msg(f'  Created {len(pts_for_line)} points and 1 line for {line_name}')
            else:
                self.segy_log_msg(f'  No line created for {line_name}: fewer than 2 valid navigation points')

        points_layer.updateExtents(); lines_layer.updateExtents()

        point_path = os.path.join(out_folder, base + '_CDP_Points.shp')
        line_path = os.path.join(out_folder, base + '_Lines.shp')
        self._write_layer(points_layer, point_path)
        self._write_layer(lines_layer, line_path)

        csv_path = os.path.join(out_folder, base + '_Kingdom_CDP_Points.csv')
        txt_path = os.path.join(out_folder, base + '_Kingdom_CDP_Points.txt')
        with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
            writer = csv.writer(fh)
            writer.writerow(['Line', 'CDP', 'CDP_Name', 'TraceNo', 'X', 'Y', 'Label', 'FileName', 'OriginalName'])
            for f in points_layer.getFeatures():
                writer.writerow([f['Line'], f['CDP'], f['CDP_Name'], f['TraceNo'], f['X'], f['Y'], f['Lbl'], f['FileName'], f['OrigName']])
        with open(txt_path, 'w', newline='', encoding='utf-8') as fh:
            fh.write('Line CDP CDP_Name TraceNo X Y Label FileName OriginalName\n')
            for f in points_layer.getFeatures():
                fh.write(f'{f["Line"]} {f["CDP"]} {f["CDP_Name"]} {f["TraceNo"]} {f["X"]} {f["Y"]} "{f["Lbl"]}" "{f["FileName"]}" "{f["OrigName"]}"\n')

        if self.segy_add_to_map.isChecked():
            for shp, name in [(point_path, base + '_CDP_Points'), (line_path, base + '_Lines')]:
                lyr = QgsVectorLayer(shp, name, 'ogr')
                if lyr.isValid():
                    QgsProject.instance().addMapLayer(lyr)

        self.segy_log_msg('')
        self.segy_log_msg(f'Done. Points: {total_points}; Lines: {total_lines}; skipped traces: {skipped}')
        self.segy_log_msg(f'Point shapefile: {point_path}')
        self.segy_log_msg(f'Line shapefile: {line_path}')
        self.segy_log_msg(f'Kingdom CSV: {csv_path}')
        self.segy_log_msg(f'Kingdom TXT: {txt_path}')
        QMessageBox.information(self, 'Complete', f'SEG-Y conversion complete.\nPoints: {total_points}\nLines: {total_lines}\nOutputs saved in:\n{out_folder}')

    # ------------------------------------------------------------------
    # Shared output helpers
    # ------------------------------------------------------------------
    def _write_layer(self, layer, path):
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'ESRI Shapefile'
        options.fileEncoding = 'UTF-8'
        err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsCoordinateTransformContext(), options)
        if err != QgsVectorFileWriter.NoError:
            raise RuntimeError(f'Could not write {path}: {msg}')

    def _write_point_outputs(self, mem, out_folder, base, add_to_map, logger):
        shp_path = os.path.join(out_folder, base + '.shp')
        self._write_layer(mem, shp_path)

        csv_path = os.path.join(out_folder, base + '_Kingdom_CDP_Postings.csv')
        asc_path = os.path.join(out_folder, base + '_Kingdom_CDP_Postings.txt')
        rows = []
        for f in mem.getFeatures():
            rows.append([f['Line'], f['CDP'], f['Dist_m'], f['X'], f['Y'], f['Lbl']])

        with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
            writer = csv.writer(fh)
            writer.writerow(['Line', 'CDP', 'Dist_m', 'X', 'Y', 'Label'])
            writer.writerows(rows)

        with open(asc_path, 'w', newline='', encoding='utf-8') as fh:
            fh.write('Line CDP Dist_m X Y Label\n')
            for r in rows:
                fh.write(f'{r[0]} {r[1]} {r[2]} {r[3]} {r[4]} "{r[5]}"\n')

        if add_to_map:
            out_layer = QgsVectorLayer(shp_path, base, 'ogr')
            if out_layer.isValid():
                QgsProject.instance().addMapLayer(out_layer)

        logger(f'Shapefile: {shp_path}')
        logger(f'Kingdom CSV: {csv_path}')
        logger(f'Kingdom ASCII/TXT: {asc_path}')
