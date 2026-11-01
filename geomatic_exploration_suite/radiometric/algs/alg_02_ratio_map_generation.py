# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingOutputFolder,
    QgsProcessingException,
)
from osgeo import gdal
import os
import csv
import numpy as np
from .utils import (
    read_raster, write_raster, safe_divide, safe_divide_chunked, array_stats, write_stats_csv,
    ensure_dir, make_available_path, load_raster_pseudocolor_quantile
)


class RatioMapGeneration(QgsProcessingAlgorithm):
    """Step 2: Generate U/Th, U/K and K/Th ratio rasters and stats only.

    This version writes one ratio raster per ratio. It does not create separate
    quantile-display rasters. Instead, loaded outputs are styled as Singleband
    pseudocolor using a blue-to-red quantile stretch.
    """

    WORKFLOW_STEP_ID = 'step_02_ratio_generation'

    PROCESSING_FOLDER = 'PROCESSING_FOLDER'
    U = 'URANIUM_SMOOTH'
    K = 'POTASSIUM_SMOOTH'
    TH = 'THORIUM_SMOOTH'
    OUT = 'OUTPUT_FOLDER'

    STYLE_LOW_Q = 'STYLE_LOW_Q'
    STYLE_HIGH_Q = 'STYLE_HIGH_Q'
    LOAD = 'LOAD_OUTPUTS'
    MIN_DENOMINATOR = 'MIN_DENOMINATOR'
    USE_CHUNKED = 'USE_CHUNKED'
    BLOCK_SIZE = 'BLOCK_SIZE'
    OUTPUT_FOLDER = 'OUTPUT_FOLDER_OUT'

    def tr(self, s):
        return QCoreApplication.translate('RatioMapGeneration', s)

    def createInstance(self):
        return RatioMapGeneration()

    def name(self):
        return '02_ratio_map_generation'

    def displayName(self):
        return self.tr('01B - Ratio Map Generation')

    def group(self):
        return self.tr('Radiometric Analysis')

    def groupId(self):
        return 'radiometric_analysis'

    def shortHelpString(self):
        return self.tr(
            'Creates U/Th, U/K, K/U and K/Th ratio maps and ratio statistics. '\
            'Step 2 now writes only one raster per ratio. Quantile display is applied '\
            'as QGIS Singleband pseudocolor styling when outputs are loaded, not as duplicate rasters. '\
            'Extreme value compression/clipping is handled in Stage 2 during threshold analysis.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.PROCESSING_FOLDER,
            self.tr('Radiometric processing folder from Step 1'),
            behavior=QgsProcessingParameterFile.Folder,
            optional=True,
        ))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.U, self.tr('Smoothed/filtered Uranium raster'), optional=True
        ))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.K, self.tr('Smoothed/filtered Potassium raster'), optional=True
        ))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.TH, self.tr('Smoothed/filtered Thorium raster'), optional=True
        ))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUT, self.tr('Output / processing folder')
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.STYLE_LOW_Q,
            self.tr('Loaded layer display lower quantile, %'),
            QgsProcessingParameterNumber.Double,
            defaultValue=1.0,
            minValue=0.0,
            maxValue=49.0,
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.STYLE_HIGH_Q,
            self.tr('Loaded layer display upper quantile, %'),
            QgsProcessingParameterNumber.Double,
            defaultValue=99.0,
            minValue=51.0,
            maxValue=100.0,
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_DENOMINATOR,
            self.tr('Minimum denominator value / near-zero guard'),
            QgsProcessingParameterNumber.Double,
            defaultValue=0.0,
            minValue=0.0,
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_CHUNKED, self.tr('Use chunked processing for very large rasters'), defaultValue=False
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.BLOCK_SIZE,
            self.tr('Chunk/block size in pixels'),
            QgsProcessingParameterNumber.Integer,
            defaultValue=512,
            minValue=128,
            maxValue=4096,
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.LOAD, self.tr('Load and style ratio outputs in QGIS'), defaultValue=True
        ))
        self.addOutput(QgsProcessingOutputFolder(self.OUTPUT_FOLDER, self.tr('Output folder')))

    def processAlgorithm(self, parameters, context, feedback):
        out = self.parameterAsString(parameters, self.OUT, context)
        processing_folder = self.parameterAsString(parameters, self.PROCESSING_FOLDER, context)
        if processing_folder and not out:
            out = processing_folder
        if not out:
            raise QgsProcessingException('Output / processing folder is required.')
        if not processing_folder:
            processing_folder = out

        u_layer = self.parameterAsRasterLayer(parameters, self.U, context)
        k_layer = self.parameterAsRasterLayer(parameters, self.K, context)
        th_layer = self.parameterAsRasterLayer(parameters, self.TH, context)

        u_path = u_layer.source() if u_layer else None
        k_path = k_layer.source() if k_layer else None
        th_path = th_layer.source() if th_layer else None

        if processing_folder and (not u_path or not k_path or not th_path):
            feedback.pushInfo('Trying to auto-detect Step 1 filtered rasters from processing folder: ' + processing_folder)
            found = self._detect_smoothed_inputs(processing_folder)
            u_path = u_path or found.get('U')
            k_path = k_path or found.get('K')
            th_path = th_path or found.get('TH')

        if not u_path or not k_path or not th_path:
            raise QgsProcessingException(
                'Smoothed/filtered U, K and Th rasters are required. Either select the three rasters, '
                'or provide the Step 1 processing folder so the tool can auto-detect them.'
            )

        ratios_dir = ensure_dir(os.path.join(out, '03_Ratio_Maps'))
        logs = ensure_dir(os.path.join(out, '00_Stats_Logs'))

        min_den = self.parameterAsDouble(parameters, self.MIN_DENOMINATOR, context)
        use_chunked = self.parameterAsBoolean(parameters, self.USE_CHUNKED, context)
        block_size = int(self.parameterAsDouble(parameters, self.BLOCK_SIZE, context))

        style_low = self.parameterAsDouble(parameters, self.STYLE_LOW_Q, context)
        style_high = self.parameterAsDouble(parameters, self.STYLE_HIGH_Q, context)
        load_outputs = self.parameterAsBoolean(parameters, self.LOAD, context)

        if style_low >= style_high:
            raise QgsProcessingException('Display lower quantile must be less than display upper quantile.')

        settings = {
            'Processing_Folder': processing_folder,
            'Input_U': u_path,
            'Input_K': k_path,
            'Input_Th': th_path,
            'QGIS_Display_Mode': 'Singleband pseudocolor; blue to red; quantile stretch on loaded layer only',
            'Display_Low_Q': style_low,
            'Display_High_Q': style_high,
            'Minimum_denominator_guard': min_den,
            'Chunked_processing': use_chunked,
            'Chunk_block_size': block_size if use_chunked else '',
        }

        stats = {}
        paths = {}

        if use_chunked:
            feedback.pushInfo('Calculating ratio rasters using chunked file-based division for memory safety.')
            ratio_inputs = {
                'U_Th_ratio': (u_path, th_path),
                'U_K_ratio': (u_path, k_path),
                'K_U_ratio': (k_path, u_path),
                'K_Th_ratio': (k_path, th_path),
            }
            for idx, (name, (num_path, den_path)) in enumerate(ratio_inputs.items()):
                p = make_available_path(os.path.join(ratios_dir, f'{name}.tif'))
                feedback.pushInfo(f'Writing {name} in chunks...')
                safe_divide_chunked(num_path, den_path, p, min_denominator=min_den, block_size=block_size, feedback=feedback)
                arr, _profile = read_raster(p)
                stats[name] = array_stats(arr, [1, 5, 10, 15, 25, 50, 75, 85, 90, 95, 99])
                paths[name] = p
                feedback.setProgress(25 + int((idx + 1) * 55 / 4))
        else:
            feedback.pushInfo('Reading smoothed/filtered U/K/Th rasters.')
            U, profile = read_raster(u_path)
            K, _ = read_raster(k_path)
            Th, _ = read_raster(th_path)
            if U.shape != K.shape or U.shape != Th.shape:
                raise QgsProcessingException('Input rasters must have the same dimensions.')

            feedback.pushInfo('Calculating U/Th, U/K, K/U and K/Th ratio rasters.')
            raw_maps = {}
            denominator_masks = {}
            raw_maps['U_Th_ratio'], denominator_masks['U_Th_ratio'] = safe_divide(U, Th, min_denominator=min_den, return_mask=True)
            raw_maps['U_K_ratio'], denominator_masks['U_K_ratio'] = safe_divide(U, K, min_denominator=min_den, return_mask=True)
            raw_maps['K_U_ratio'], denominator_masks['K_U_ratio'] = safe_divide(K, U, min_denominator=min_den, return_mask=True)
            raw_maps['K_Th_ratio'], denominator_masks['K_Th_ratio'] = safe_divide(K, Th, min_denominator=min_den, return_mask=True)
            if min_den > 0:
                total_cells = float(U.size)
                for rname, mask in denominator_masks.items():
                    pct = 100.0 * float(np.count_nonzero(mask)) / total_cells if total_cells else 0.0
                    feedback.pushInfo(f'{rname}: masked {pct:.3f}% cells with denominator <= {min_den:g}')
            for name, arr in raw_maps.items():
                raw_stats = array_stats(arr, [1, 5, 10, 15, 25, 50, 75, 85, 90, 95, 99])
                p = make_available_path(os.path.join(ratios_dir, f'{name}.tif'))
                write_raster(p, arr, profile, gdal.GDT_Float32)
                paths[name] = p
                stats[name] = raw_stats

        write_stats_csv(os.path.join(logs, 'ratio_map_statistics.csv'), stats, settings)

        if load_outputs:
            for n, p in paths.items():
                load_raster_pseudocolor_quantile(p, n, low_q=style_low, high_q=style_high)

        feedback.setProgress(100)
        feedback.pushInfo('Ratio maps and statistics complete. Four rasters were written: U/Th, U/K, K/U and K/Th. One raster per ratio was written; display is handled by QGIS quantile pseudocolor styling.')
        return {self.OUTPUT_FOLDER: out}

    def _detect_smoothed_inputs(self, folder):
        search_roots = [folder]
        for sub in ('02_Preprocessed',):
            p = os.path.join(folder, sub)
            if os.path.isdir(p):
                search_roots.insert(0, p)

        candidates = []
        for root0 in search_roots:
            for root, _, files in os.walk(root0):
                for f in files:
                    low = f.lower()
                    if low.endswith(('.tif', '.tiff')):
                        p = os.path.join(root, f)
                        score = 0
                        if 'meanf' in low or 'medianf' in low or 'smooth' in low or 'filter' in low:
                            score += 10
                        if os.path.basename(root).lower() in ('02_filtered', '02_smoothed'):
                            score += 5
                        if 'conditioned' in low or 'aligned' in low or 'clean' in low:
                            score -= 5
                        candidates.append((score, low, p))

        def pick(tokens):
            matches = []
            for score, low, p in candidates:
                if any(tok in low for tok in tokens):
                    matches.append((score, low, p))
            if not matches:
                return None
            matches.sort(key=lambda x: (x[0], os.path.getmtime(x[2])), reverse=True)
            return matches[0][2]

        return {
            'U': pick(['uranium', '_u_', 'u_smooth', 'u_meanf', 'u_medianf', 'u_filter']),
            'K': pick(['potassium', '_k_', 'k_smooth', 'k_meanf', 'k_medianf', 'k_filter']),
            'TH': pick(['thorium', '_th_', 'th_smooth', 'th_meanf', 'th_medianf', 'th_filter']),
        }

    def _clip_by_quantile(self, arr, low_q, high_q):
        valid = arr[np.isfinite(arr)]
        if valid.size == 0:
            return arr, np.nan, np.nan, 0.0, 0.0
        lo = float(np.nanpercentile(valid, low_q))
        hi = float(np.nanpercentile(valid, high_q))
        out = np.array(arr, copy=True)
        low_mask = np.isfinite(out) & (out < lo)
        high_mask = np.isfinite(out) & (out > hi)
        total = float(valid.size)
        low_pct = 100.0 * float(np.count_nonzero(low_mask)) / total
        high_pct = 100.0 * float(np.count_nonzero(high_mask)) / total
        out[low_mask] = lo
        out[high_mask] = hi
        return out, lo, hi, low_pct, high_pct

    def _write_clip_csv(self, path, rows):
        ensure_dir(os.path.dirname(path))
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['ratio', 'low_quantile_pct', 'low_value', 'high_quantile_pct', 'high_value', 'cells_below_low_pct', 'cells_above_high_pct'])
            for r in rows:
                w.writerow(r)
