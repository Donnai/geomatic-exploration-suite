# -*- coding: utf-8 -*-
"""
QGIS Processing Algorithm
01A - Pre-Processing / Data Conditioning v2.1

Updates:
- Explicit additional NoData/null parameter, default -9999
- Optional treatment of values <= NoData as null
- Optional extreme value clipping by percentile
- Mean and median focal filters
- Window sizes: 3, 5, 7, 9, 11, 13, 15
- Output filenames use input raster basename + suffix, e.g. MyU_MeanF_5x5.tif
- Optional mask polygon keeps the reference raster extent and writes NoData outside the polygon
"""
import logging

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber,
    QgsProcessingOutputFolder,
    QgsRasterLayer,
    QgsProject,
)
from osgeo import gdal
import os
import math
import numpy as np

gdal.UseExceptions()


class PreProcessingDataConditioning(QgsProcessingAlgorithm):
    WORKFLOW_STEP_ID = 'step_01_preprocessing'
    U = 'URANIUM'
    K = 'POTASSIUM'
    TH = 'THORIUM'
    MASK = 'MASK'
    OUT = 'OUTPUT_FOLDER'
    FILTER = 'FILTER_TYPE'
    SIZE = 'SMOOTH_SIZE'
    USE_EXTRA_NODATA = 'USE_EXTRA_NODATA'
    EXTRA_NODATA = 'EXTRA_NODATA_VALUE'
    INVALID_LE_ZERO = 'INVALID_LE_ZERO'
    CLIP_EXTREMES = 'CLIP_EXTREMES'
    CLIP_LOW = 'CLIP_LOW_PERCENTILE'
    CLIP_HIGH = 'CLIP_HIGH_PERCENTILE'
    LOAD = 'LOAD_OUTPUTS'
    OUTPUT_FOLDER = 'OUTPUT_FOLDER_OUT'

    def tr(self, s):
        return QCoreApplication.translate('PreProcessingDataConditioning', s)

    def createInstance(self):
        return PreProcessingDataConditioning()

    def name(self):
        return '01_preprocessing_data_conditioning'

    def displayName(self):
        return self.tr('01A - Pre-Processing / Data Conditioning')

    def group(self):
        return self.tr('Radiometric Analysis')

    def groupId(self):
        return 'radiometric_analysis'

    def shortHelpString(self):
        return self.tr(
            'Aligns U, K and Th rasters to the Uranium reference grid, optionally masks to a polygon, '
            'converts NoData/null values to internal NaN, optionally clips extreme values, and applies '
            'mean or median smoothing. If no mask is supplied, the Uranium raster extent is used as the output outline.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.U, self.tr('Uranium raster / reference grid')))
        self.addParameter(QgsProcessingParameterRasterLayer(self.K, self.tr('Potassium raster')))
        self.addParameter(QgsProcessingParameterRasterLayer(self.TH, self.tr('Thorium raster')))
        self.addParameter(QgsProcessingParameterVectorLayer(self.MASK, self.tr('Optional study area mask polygon'), [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT, self.tr('Output folder')))

        self.addParameter(QgsProcessingParameterBoolean(self.USE_EXTRA_NODATA, self.tr('Treat an additional NoData/null value as null'), defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(self.EXTRA_NODATA, self.tr('Additional NoData/null value'), QgsProcessingParameterNumber.Double, defaultValue=-9999.0))
        self.addParameter(QgsProcessingParameterBoolean(self.INVALID_LE_ZERO, self.tr('Treat radiometric values <= 0 as invalid / NoData'), defaultValue=True))

        self.addParameter(QgsProcessingParameterBoolean(self.CLIP_EXTREMES, self.tr('Clip extreme raster values before smoothing'), defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(self.CLIP_LOW, self.tr('Lower clipping percentile'), QgsProcessingParameterNumber.Double, defaultValue=1.0, minValue=0.0, maxValue=100.0))
        self.addParameter(QgsProcessingParameterNumber(self.CLIP_HIGH, self.tr('Upper clipping percentile'), QgsProcessingParameterNumber.Double, defaultValue=99.0, minValue=0.0, maxValue=100.0))

        self.addParameter(QgsProcessingParameterEnum(self.FILTER, self.tr('Smoothing filter'), options=['Mean focal filter', 'Median focal filter'], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(self.SIZE, self.tr('Smoothing window size'), options=['3 x 3', '5 x 5', '7 x 7', '9 x 9', '11 x 11', '13 x 13', '15 x 15'], defaultValue=1))
        self.addParameter(QgsProcessingParameterBoolean(self.LOAD, self.tr('Load filtered outputs into QGIS'), defaultValue=True))
        self.addOutput(QgsProcessingOutputFolder(self.OUTPUT_FOLDER, self.tr('Output folder')))

    def processAlgorithm(self, parameters, context, feedback):
        u = self.parameterAsRasterLayer(parameters, self.U, context)
        k = self.parameterAsRasterLayer(parameters, self.K, context)
        th = self.parameterAsRasterLayer(parameters, self.TH, context)
        mask = self.parameterAsVectorLayer(parameters, self.MASK, context)
        out = self.parameterAsString(parameters, self.OUT, context)

        if not u or not k or not th:
            raise QgsProcessingException('Uranium, Potassium and Thorium rasters are required.')

        use_extra_nodata = self.parameterAsBoolean(parameters, self.USE_EXTRA_NODATA, context)
        extra_nodata = self.parameterAsDouble(parameters, self.EXTRA_NODATA, context)
        nodata_leq = False  # exact additional NoData only; <=0 is controlled by INVALID_LE_ZERO
        invalid_le_zero = self.parameterAsBoolean(parameters, self.INVALID_LE_ZERO, context)
        clip_extremes = self.parameterAsBoolean(parameters, self.CLIP_EXTREMES, context)
        clip_low = self.parameterAsDouble(parameters, self.CLIP_LOW, context)
        clip_high = self.parameterAsDouble(parameters, self.CLIP_HIGH, context)
        filt = self.parameterAsEnum(parameters, self.FILTER, context)
        size = [3, 5, 7, 9, 11, 13, 15][self.parameterAsEnum(parameters, self.SIZE, context)]
        load = self.parameterAsBoolean(parameters, self.LOAD, context)

        if clip_extremes and clip_low >= clip_high:
            raise QgsProcessingException('Lower clipping percentile must be less than upper clipping percentile.')

        pre = _ensure_dir(os.path.join(out, '02_Preprocessed'))
        sm = pre
        mask_path = mask.source() if mask else None

        feedback.pushInfo('Reference grid / default outline: Uranium raster extent.')
        if mask_path:
            feedback.pushInfo('Mask polygon supplied: outputs keep the Uranium raster grid, with cells outside the polygon set to NoData.')
        else:
            feedback.pushInfo('No mask polygon supplied: no polygon clipping is applied.')

        layers = [('U', u), ('K', k), ('Th', th)]
        outputs = []
        method_name = 'MeanF' if filt == 0 else 'MedianF'
        suffix = f'{method_name}_{size}x{size}'

        for idx, (key, layer) in enumerate(layers):
            if feedback.isCanceled():
                break

            base = _base_name(layer.source())
            aligned_path = os.path.join(pre, f'{base}_aligned.tif')
            conditioned_path = os.path.join(pre, f'{base}_conditioned.tif')
            filtered_path = os.path.join(sm, f'{base}_{suffix}.tif')

            feedback.pushInfo(f'Aligning {base} to reference grid.')
            _warp_to_reference(layer.source(), aligned_path, u.source(), mask_path)

            arr, profile = _read_raster(aligned_path)
            arr = _clean_array(arr, use_extra_nodata, extra_nodata, nodata_leq, invalid_le_zero)

            if invalid_le_zero:
                feedback.pushInfo(f'{base}: radiometric values <= 0 set to NoData before filtering.')

            if clip_extremes:
                arr, lo_val, hi_val = _clip_percentiles(arr, clip_low, clip_high)
                feedback.pushInfo(f'{base}: clipped finite values to P{clip_low:g}={lo_val:g}, P{clip_high:g}={hi_val:g}.')

            _write_raster(conditioned_path, arr, profile, gdal.GDT_Float32)

            feedback.pushInfo(f'{base}: applying {size} x {size} {method_name} filter.')
            if filt == 0:
                filtered = _focal_mean(arr, size)
            else:
                filtered = _focal_median_chunked(arr, size, feedback=feedback)

            _write_raster(filtered_path, filtered, profile, gdal.GDT_Float32)
            outputs.append((filtered_path, f'{base}_{suffix}'))
            feedback.setProgress(int(((idx + 1) / 3.0) * 95))

        if load:
            for path, name in outputs:
                _load_layer(path, name)

        feedback.setProgress(100)
        feedback.pushInfo(f'Pre-processing complete. Filtered outputs saved to: {sm}')
        return {self.OUTPUT_FOLDER: out}


# -------------------------------------------------------------------------
# Self-contained helper functions for QGIS Scripts toolbox use.
# -------------------------------------------------------------------------

def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _base_name(path):
    clean = str(path).split('|')[0]
    name = os.path.splitext(os.path.basename(clean))[0]
    return name or 'raster'


def _read_raster(path):
    ds = gdal.Open(path)
    if ds is None:
        raise QgsProcessingException(f'Could not open raster: {path}')
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray().astype(np.float64)
    nd = band.GetNoDataValue()
    if nd is not None:
        arr[arr == nd] = np.nan
    profile = {
        'geotransform': ds.GetGeoTransform(),
        'projection': ds.GetProjection(),
        'xsize': ds.RasterXSize,
        'ysize': ds.RasterYSize,
    }
    ds = None
    return arr, profile


def _clean_array(arr, use_extra_nodata=True, extra_nodata=-9999.0, nodata_leq=True, invalid_le_zero=False):
    out = np.array(arr, copy=True).astype(np.float64)
    out[~np.isfinite(out)] = np.nan
    if use_extra_nodata:
        if nodata_leq:
            out[out <= extra_nodata] = np.nan
        else:
            out[np.isclose(out, extra_nodata, rtol=0.0, atol=0.0)] = np.nan
    if invalid_le_zero:
        out[np.isfinite(out) & (out <= 0.0)] = np.nan
    return out


def _clip_percentiles(arr, low_p, high_p):
    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        raise QgsProcessingException('No valid cells available for clipping.')
    lo = float(np.nanpercentile(valid, low_p))
    hi = float(np.nanpercentile(valid, high_p))
    out = np.array(arr, copy=True)
    finite = np.isfinite(out)
    out[finite] = np.clip(out[finite], lo, hi)
    return out, lo, hi


def _write_raster(path, arr, profile, gdal_type=gdal.GDT_Float32, nodata=-9999):
    _ensure_dir(os.path.dirname(path))
    ds = gdal.GetDriverByName('GTiff').Create(
        path,
        profile['xsize'],
        profile['ysize'],
        1,
        gdal_type,
        options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'],
    )
    if ds is None:
        raise QgsProcessingException(f'Could not create raster: {path}')
    ds.SetGeoTransform(profile['geotransform'])
    ds.SetProjection(profile['projection'])
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    out = np.array(arr, copy=True).astype(np.float32)
    out[~np.isfinite(out)] = nodata
    band.WriteArray(out)
    band.FlushCache()
    try:
        band.ComputeStatistics(False)
    except Exception:
        logging.getLogger(__name__).debug("Optional operation failed in _write_raster; existing fallback retained.", exc_info=True)
    ds = None


def _warp_to_reference(input_path, output_path, ref_path, mask_path=None):
    ref = gdal.Open(str(ref_path).split('|')[0])
    if ref is None:
        raise QgsProcessingException(f'Could not open reference raster: {ref_path}')
    gt = ref.GetGeoTransform()
    xsize = ref.RasterXSize
    ysize = ref.RasterYSize
    xmin = gt[0]
    ymax = gt[3]
    xmax = xmin + gt[1] * xsize
    ymin = ymax + gt[5] * ysize
    opts = dict(
        format='GTiff',
        outputBounds=[xmin, ymin, xmax, ymax],
        width=xsize,
        height=ysize,
        dstSRS=ref.GetProjection(),
        resampleAlg=gdal.GRA_Bilinear,
        dstNodata=-9999,
        multithread=True,
        creationOptions=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'],
    )
    if mask_path:
        opts['cutlineDSName'] = str(mask_path).split('|')[0]
        opts['cropToCutline'] = False
    gdal.Warp(output_path, str(input_path).split('|')[0], **opts)
    ref = None


def _focal_mean(arr, size):
    pad = size // 2
    padded = np.pad(arr, pad, mode='constant', constant_values=np.nan)
    total = np.zeros(arr.shape, dtype=np.float64)
    count = np.zeros(arr.shape, dtype=np.float64)
    for i in range(size):
        for j in range(size):
            w = padded[i:i + arr.shape[0], j:j + arr.shape[1]]
            valid = np.isfinite(w)
            total[valid] += w[valid]
            count[valid] += 1
    out = total / count
    out[count == 0] = np.nan
    return out


def _focal_median_chunked(arr, size, chunk_rows=256, feedback=None):
    """Nan-aware median filter. Chunked to avoid huge memory spikes for 11x11 to 15x15 filters."""
    pad = size // 2
    rows, cols = arr.shape
    out = np.full(arr.shape, np.nan, dtype=np.float64)
    padded = np.pad(arr, pad, mode='constant', constant_values=np.nan)

    for r0 in range(0, rows, chunk_rows):
        if feedback and feedback.isCanceled():
            break
        r1 = min(rows, r0 + chunk_rows)
        stack = []
        for i in range(size):
            for j in range(size):
                stack.append(padded[r0 + i:r1 + i, j:j + cols])
        with np.errstate(all='ignore'):
            out[r0:r1, :] = np.nanmedian(np.stack(stack, axis=0), axis=0)
    return out


def _load_layer(path, name):
    if not os.path.exists(path):
        return
    layer = QgsRasterLayer(path, name)
    if layer.isValid():
        QgsProject.instance().addMapLayer(layer)
