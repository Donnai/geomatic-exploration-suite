# -*- coding: utf-8 -*-
import os, csv, math
from datetime import datetime
import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import QgsProcessingException, QgsRasterLayer, QgsVectorLayer, QgsProject

gdal.UseExceptions()

def ensure_dir(path):
    os.makedirs(path, exist_ok=True); return path

def read_raster(path):
    ds = gdal.Open(path)
    if ds is None: raise QgsProcessingException(f"Could not open raster: {path}")
    b = ds.GetRasterBand(1); arr = b.ReadAsArray().astype(np.float64)
    nd = b.GetNoDataValue()
    if nd is not None: arr[arr == nd] = np.nan
    arr[arr <= -9999] = np.nan
    profile = {"geotransform": ds.GetGeoTransform(), "projection": ds.GetProjection(), "xsize": ds.RasterXSize, "ysize": ds.RasterYSize}
    ds = None
    return arr, profile

def write_raster(path, arr, profile, gdal_type=gdal.GDT_Float32, nodata=-9999):
    ensure_dir(os.path.dirname(path))
    ds = gdal.GetDriverByName('GTiff').Create(path, profile['xsize'], profile['ysize'], 1, gdal_type, options=['COMPRESS=LZW','TILED=YES','BIGTIFF=IF_SAFER'])
    ds.SetGeoTransform(profile['geotransform']); ds.SetProjection(profile['projection'])
    b = ds.GetRasterBand(1); b.SetNoDataValue(nodata)
    out = np.array(arr, copy=True)
    if gdal_type == gdal.GDT_Byte:
        out = np.where(np.isfinite(out), out, 0).astype(np.uint8)
    else:
        out = out.astype(np.float32); out[~np.isfinite(out)] = nodata
    b.WriteArray(out); b.FlushCache()
    try: b.ComputeStatistics(False)
    except Exception: pass
    ds = None

def warp_to_reference(input_path, output_path, ref_path, mask_path=None):
    ref = gdal.Open(ref_path)
    if ref is None: raise QgsProcessingException(f"Could not open reference raster: {ref_path}")
    gt = ref.GetGeoTransform(); xsize = ref.RasterXSize; ysize = ref.RasterYSize
    xmin = gt[0]; ymax = gt[3]; xmax = xmin + gt[1] * xsize; ymin = ymax + gt[5] * ysize
    opts = dict(format='GTiff', outputBounds=[xmin, ymin, xmax, ymax], width=xsize, height=ysize, dstSRS=ref.GetProjection(), resampleAlg=gdal.GRA_Bilinear, dstNodata=-9999, multithread=True, creationOptions=['COMPRESS=LZW','TILED=YES','BIGTIFF=IF_SAFER'])
    if mask_path:
        opts['cutlineDSName'] = mask_path; opts['cropToCutline'] = False
    gdal.Warp(output_path, input_path, **opts); ref = None

def focal_mean(arr, size):
    pad = size // 2; p = np.pad(arr, pad, mode='constant', constant_values=np.nan)
    total = np.zeros(arr.shape, dtype=np.float64); count = np.zeros(arr.shape, dtype=np.float64)
    for i in range(size):
        for j in range(size):
            w = p[i:i+arr.shape[0], j:j+arr.shape[1]]; v = np.isfinite(w)
            total[v] += w[v]; count[v] += 1
    out = total / count; out[count == 0] = np.nan; return out

def focal_median(arr, size):
    pad = size // 2; p = np.pad(arr, pad, mode='constant', constant_values=np.nan)
    stack = [p[i:i+arr.shape[0], j:j+arr.shape[1]] for i in range(size) for j in range(size)]
    with np.errstate(all='ignore'):
        return np.nanmedian(np.stack(stack, axis=0), axis=0)

def safe_divide(a, b, min_denominator=0.0, return_mask=False):
    """Safely divide two arrays and mask invalid/small denominators.

    min_denominator is a positive denominator floor. Cells with denominator <=
    min_denominator are written as NoData/NaN. This avoids huge ratio spikes
    caused by near-zero K or Th values. Default 0.0 preserves the original
    behaviour except exact zero denominators are masked.
    """
    out = np.full(a.shape, np.nan, dtype=np.float64)
    den_ok = np.isfinite(b) & (b > float(min_denominator))
    v = np.isfinite(a) & den_ok
    out[v] = a[v] / b[v]
    out[~np.isfinite(out)] = np.nan
    if return_mask:
        small_den = np.isfinite(b) & ~den_ok
        return out, small_den
    return out



def _raster_grid_signature(ds):
    """Return a lightweight grid signature used to check raster compatibility."""
    return (ds.RasterXSize, ds.RasterYSize, ds.GetGeoTransform(), ds.GetProjection())


def safe_divide_chunked(a_path, b_path, out_path, min_denominator=0.0, block_size=512,
                        nodata=-9999.0, feedback=None):
    """Safely divide two raster files in blocks and write a Float32 GeoTIFF.

    This is the memory-safe file-based equivalent of :func:`safe_divide`.
    It is designed for large U/K/Th ratio rasters where reading all arrays at
    once may use too much RAM.

    Rules applied per pixel:
    - input NoData values and non-finite values are treated as invalid;
    - legacy values <= -9999 are treated as invalid;
    - denominator cells <= min_denominator are treated as invalid;
    - invalid output cells are written as the declared output NoData value.

    Parameters
    ----------
    a_path, b_path : str
        Numerator and denominator raster paths.
    out_path : str
        Output GeoTIFF path.
    min_denominator : float
        Denominator guard. Cells with denominator <= this value become NoData.
    block_size : int
        Processing block size in pixels. 512 is a safe default.
    nodata : float
        Output NoData value. Default is -9999.
    feedback : optional
        QGIS Processing feedback object or similar object with ``pushInfo``.

    Returns
    -------
    str
        The output path.
    """
    ensure_dir(os.path.dirname(out_path))
    ds_a = gdal.Open(a_path)
    ds_b = gdal.Open(b_path)
    if ds_a is None or ds_b is None:
        raise QgsProcessingException('Could not open input rasters for chunked division.')

    if ds_a.RasterXSize != ds_b.RasterXSize or ds_a.RasterYSize != ds_b.RasterYSize:
        raise QgsProcessingException('Chunked division requires rasters with the same row/column dimensions.')

    gt_a, gt_b = ds_a.GetGeoTransform(), ds_b.GetGeoTransform()
    if gt_a != gt_b:
        raise QgsProcessingException('Chunked division requires rasters with the same geotransform/alignment.')

    proj_a, proj_b = ds_a.GetProjection(), ds_b.GetProjection()
    if proj_a and proj_b and proj_a != proj_b:
        raise QgsProcessingException('Chunked division requires rasters with the same projection.')

    driver = gdal.GetDriverByName('GTiff')
    out_ds = driver.Create(
        out_path,
        ds_a.RasterXSize,
        ds_a.RasterYSize,
        1,
        gdal.GDT_Float32,
        options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER']
    )
    if out_ds is None:
        raise QgsProcessingException(f'Could not create chunked ratio output: {out_path}')
    out_ds.SetGeoTransform(ds_a.GetGeoTransform())
    out_ds.SetProjection(ds_a.GetProjection())
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(float(nodata))

    band_a = ds_a.GetRasterBand(1)
    band_b = ds_b.GetRasterBand(1)
    nd_a = band_a.GetNoDataValue()
    nd_b = band_b.GetNoDataValue()

    block_size = int(max(128, min(4096, block_size)))
    total_blocks = ((ds_a.RasterYSize + block_size - 1) // block_size) * ((ds_a.RasterXSize + block_size - 1) // block_size)
    done_blocks = 0
    masked_den = 0
    valid_out = 0

    for y in range(0, ds_a.RasterYSize, block_size):
        rows = min(block_size, ds_a.RasterYSize - y)
        for x in range(0, ds_a.RasterXSize, block_size):
            cols = min(block_size, ds_a.RasterXSize - x)
            a = band_a.ReadAsArray(x, y, cols, rows).astype(np.float64)
            b = band_b.ReadAsArray(x, y, cols, rows).astype(np.float64)

            if nd_a is not None:
                a[a == nd_a] = np.nan
            if nd_b is not None:
                b[b == nd_b] = np.nan
            a[a <= -9999] = np.nan
            b[b <= -9999] = np.nan

            den_ok = np.isfinite(b) & (b > float(min_denominator))
            valid = np.isfinite(a) & den_ok
            out_block = np.full((rows, cols), float(nodata), dtype=np.float32)
            out_block[valid] = (a[valid] / b[valid]).astype(np.float32)
            out_band.WriteArray(out_block, x, y)

            masked_den += int(np.count_nonzero(np.isfinite(b) & ~den_ok))
            valid_out += int(np.count_nonzero(valid))
            done_blocks += 1
            if feedback is not None and done_blocks % 25 == 0:
                try:
                    feedback.pushInfo(f'Chunked divide progress: {done_blocks}/{total_blocks} blocks')
                except Exception:
                    pass

    out_band.FlushCache()
    try:
        out_band.ComputeStatistics(False)
    except Exception:
        pass
    out_ds = None
    ds_a = None
    ds_b = None

    if feedback is not None:
        try:
            feedback.pushInfo(f'Chunked divide complete: valid output cells={valid_out:,}; denominator-guarded cells={masked_den:,}')
        except Exception:
            pass
    return out_path

def normalise(arr):
    valid = arr[np.isfinite(arr)]; out = np.full(arr.shape, np.nan, dtype=np.float64)
    if valid.size == 0: return out
    mn = float(np.nanmin(valid)); mx = float(np.nanmax(valid))
    if math.isclose(mx, mn): out[np.isfinite(arr)] = 0.0; return out
    out[np.isfinite(arr)] = (arr[np.isfinite(arr)] - mn) / (mx - mn); out[out < 0] = 0; out[out > 1] = 1
    return out

def p_label(p): return str(int(p)) if float(p).is_integer() else str(p).replace('.', '_')

def array_stats(arr, percentiles):
    valid = arr[np.isfinite(arr)]
    if valid.size == 0: raise QgsProcessingException('No valid raster cells found for statistics.')
    stats = {'count': int(valid.size), 'min': float(np.nanmin(valid)), 'max': float(np.nanmax(valid)), 'mean': float(np.nanmean(valid)), 'median': float(np.nanmedian(valid)), 'std': float(np.nanstd(valid))}
    for p in sorted(set(float(x) for x in percentiles)): stats[f'P{p_label(p)}'] = float(np.nanpercentile(valid, p))
    return stats

def _split_csv_paths(path):
    """Return separate stats and processing-parameter CSV paths.

    A requested path like ratio_map_statistics.csv becomes:
      ratio_map_stats.csv
      ratio_map_proc_parameters.csv

    This keeps raster statistics separate from processing/audit parameters.
    """
    folder = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    for suffix in ('_statistics', '_stats', '_thresholds', '_settings'):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return (
        os.path.join(folder, f'{stem}_stats.csv'),
        os.path.join(folder, f'{stem}_proc_parameters.csv')
    )



def ensure_stats_log_structure(processing_folder):
    """Create and return the canonical 00_Stats_Logs structure."""
    root = ensure_dir(os.path.join(processing_folder, '00_Stats_Logs'))
    return {
        'root': root,
        'settings': ensure_dir(os.path.join(root, 'Settings')),
        'validation': ensure_dir(os.path.join(root, 'Validation')),
        'processing_logs': ensure_dir(os.path.join(root, 'Processing_Logs')),
        'sessions': ensure_dir(os.path.join(root, 'Sessions')),
        'statistics': ensure_dir(os.path.join(root, 'Statistics')),
        'histogram': ensure_dir(os.path.join(root, 'Histogram')),
    }


def _find_processing_folder_for_log_path(path):
    """Infer the project processing folder for an output/log path.

    Many callers historically passed paths inside output folders such as
    03_Ratio_Maps or 07_Combined_Maps.  Audit files should now always be
    routed to the canonical processing-folder audit tree:

        00_Stats_Logs/Statistics
        00_Stats_Logs/Settings
        00_Stats_Logs/Processing_Logs
        00_Stats_Logs/Validation
        00_Stats_Logs/Histogram
        00_Stats_Logs/Sessions
    """
    folder = os.path.abspath(os.path.dirname(path))
    known_output_dirs = {
        '01_Input_Data', '02_Preprocessed', '02_Filtered_Rasters', '03_Ratio_Maps',
        '04_Normalised_Ratio_Maps', '04_Normalized_Ratio_Maps', '04_ZLog_Ratio_Maps',
        '05_Ratio_Threshold_Class_Maps', '06_Ratio_Map_Anomaly_Extraction',
        '07_Combined_Maps', '08_Combined_Threshold_Maps', '09_Combined_Anomaly_Extraction',
        'Validation_Test_Outputs'
    }
    audit_dirs = {'00_Stats_Logs', 'Settings', 'Validation', 'Processing_Logs', 'Sessions', 'Statistics', 'Histogram'}

    cur = folder
    while cur and os.path.dirname(cur) != cur:
        base = os.path.basename(cur)
        if base == '00_Stats_Logs':
            return os.path.dirname(cur)
        if base in known_output_dirs:
            return os.path.dirname(cur)
        cur = os.path.dirname(cur)

    # If the path was already inside a canonical audit subfolder, walk upward.
    cur = folder
    while cur and os.path.dirname(cur) != cur:
        if os.path.basename(cur) == '00_Stats_Logs':
            return os.path.dirname(cur)
        cur = os.path.dirname(cur)

    # Last resort: use the immediate folder so old behaviour still works.
    return folder


def _canonical_log_paths_for(path):
    """Route stats and parameter CSVs into canonical subfolders."""
    folder = os.path.dirname(path)
    base_folder = os.path.basename(folder).lower()
    stem = os.path.splitext(os.path.basename(path))[0]
    for suffix in ('_statistics', '_stats', '_thresholds', '_settings'):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break

    # If explicitly writing inside Statistics/Settings, respect the requested
    # audit root but still split stats/settings correctly.
    cur = os.path.abspath(folder)
    audit_root = None
    while cur and os.path.dirname(cur) != cur:
        if os.path.basename(cur) == '00_Stats_Logs':
            audit_root = cur
            break
        cur = os.path.dirname(cur)

    if audit_root is None:
        processing_folder = _find_processing_folder_for_log_path(path)
        audit_root = ensure_stats_log_structure(processing_folder)['root']
    else:
        ensure_stats_log_structure(os.path.dirname(audit_root))

    stats_dir = ensure_dir(os.path.join(audit_root, 'Statistics'))
    settings_dir = ensure_dir(os.path.join(audit_root, 'Settings'))
    return (os.path.join(stats_dir, f'{stem}_stats.csv'), os.path.join(settings_dir, f'{stem}_proc_parameters.csv'))

def append_session_log(processing_folder, process_name, status='completed', inputs=None, outputs=None, parameters=None, message=''):
    """Append one processing-session record to 00_Stats_Logs/session_log.csv.

    The log is intentionally simple CSV so it can be opened in Excel/QGIS and
    audited later. It records what was run, when, input/output paths and key
    settings. This is a lightweight audit trail, not a replacement for the
    detailed per-process stats/parameter CSVs.
    """
    if not processing_folder:
        return None
    log_dirs = ensure_stats_log_structure(processing_folder)
    path = os.path.join(log_dirs['sessions'], 'radiometric_session_log.csv')
    new_file = not os.path.exists(path)
    def _join(v):
        if v is None:
            return ''
        if isinstance(v, dict):
            return '; '.join(f'{k}={val}' for k, val in v.items())
        if isinstance(v, (list, tuple, set)):
            return '; '.join(str(x) for x in v)
        return str(v)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(['timestamp', 'process', 'status', 'inputs', 'outputs', 'parameters', 'message'])
        w.writerow([
            datetime.now().isoformat(timespec='seconds'),
            process_name,
            status,
            _join(inputs),
            _join(outputs),
            _join(parameters),
            str(message or '')
        ])
    return path

def write_stats_csv(path, stats, settings=None):
    """Write raster statistics and processing parameters to separate CSVs.

    Older plugin versions wrote both into one CSV. The current project structure
    writes two audit files with matching base names:
      *_stats.csv
      *_proc_parameters.csv
    """
    ensure_dir(os.path.dirname(path))
    stats_path, params_path = _canonical_log_paths_for(path)
    ensure_dir(os.path.dirname(stats_path)); ensure_dir(os.path.dirname(params_path))

    with open(stats_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['SECTION','NAME','PARAMETER','VALUE'])
        for name, st in (stats or {}).items():
            for k, v in st.items():
                w.writerow(['Raster statistics', name, k, v])

    if settings is not None:
        with open(params_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['SECTION','NAME','PARAMETER','VALUE'])
            for k, v in settings.items():
                w.writerow(['Processing parameters', 'Workflow', k, v])

    # Return paths for callers that want to log them, while preserving backward
    # compatibility with callers that ignore the return value.
    return {'stats_csv': stats_path, 'proc_parameters_csv': params_path if settings is not None else None}

def polygonize_class_raster(raster_path, output_gpkg, layer_name, keep_values, min_area_km2=0.0):
    if os.path.exists(output_gpkg):
        try: os.remove(output_gpkg)
        except Exception: pass
    src = gdal.Open(raster_path)
    if src is None: raise QgsProcessingException(f'Could not open class raster: {raster_path}')
    band = src.GetRasterBand(1); proj = src.GetProjection(); dst = ogr.GetDriverByName('GPKG').CreateDataSource(output_gpkg)
    if dst is None: raise QgsProcessingException(f'Could not create output: {output_gpkg}')
    srs = osr.SpatialReference()
    if proj: srs.ImportFromWkt(proj)
    lyr = dst.CreateLayer(layer_name, srs=srs, geom_type=ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn('class', ogr.OFTInteger)); gdal.Polygonize(band, None, lyr, 0, [], callback=None)
    lyr.CreateField(ogr.FieldDefn('class_name', ogr.OFTString)); lyr.CreateField(ogr.FieldDefn('area_km2', ogr.OFTReal)); lyr.CreateField(ogr.FieldDefn('anomaly_id', ogr.OFTInteger))
    keep = set(int(v) for v in keep_values); delete = []; nid = 1; lyr.ResetReading()
    for feat in lyr:
        cv = feat.GetField('class'); geom = feat.GetGeometryRef()
        if cv is None or int(cv) not in keep or geom is None: delete.append(feat.GetFID()); continue
        area = geom.GetArea() / 1000000.0
        if min_area_km2 > 0 and area < min_area_km2: delete.append(feat.GetFID()); continue
        cname = {1:'High_or_Low', 2:'Very_High_or_Very_Low', 3:'Very_High'}.get(int(cv), 'Major_Anomaly')
        feat.SetField('class_name', cname); feat.SetField('area_km2', float(area)); feat.SetField('anomaly_id', nid); lyr.SetFeature(feat); nid += 1
    for fid in delete: lyr.DeleteFeature(fid)
    try: dst.ExecuteSQL(f'CREATE SPATIAL INDEX ON {layer_name} (geom)')
    except Exception: pass
    dst = None; src = None


def polygonize_binary_mask_raster(raster_path, output_gpkg, layer_name, min_area_km2=0.0, min_pixels=0):
    """Fast polygon extraction from a binary class raster.

    This method treats class value 1 as anomaly and class value 0/NoData as
    background. If min_pixels > 0, GDAL SieveFilter is applied before
    polygonising so tiny connected regions are removed cheaply in raster space.
    GDAL Polygonize is then called with the anomaly band as its own mask so the
    background is ignored rather than polygonised and deleted later.
    """
    if os.path.exists(output_gpkg):
        try: os.remove(output_gpkg)
        except Exception: pass
    src = gdal.Open(raster_path)
    if src is None:
        raise QgsProcessingException(f'Could not open binary mask raster: {raster_path}')
    proj = src.GetProjection(); gt = src.GetGeoTransform(); xsize = src.RasterXSize; ysize = src.RasterYSize
    src_band = src.GetRasterBand(1)

    # Build an in-memory Byte mask: anomaly=1, background=0.
    mem_drv = gdal.GetDriverByName('MEM')
    work = mem_drv.Create('', xsize, ysize, 1, gdal.GDT_Byte)
    work.SetGeoTransform(gt); work.SetProjection(proj)
    arr = src_band.ReadAsArray()
    mask_arr = ((arr == 1) | (arr == 2) | (arr == 3)).astype('uint8')
    work.GetRasterBand(1).SetNoDataValue(0)
    work.GetRasterBand(1).WriteArray(mask_arr)
    work.GetRasterBand(1).FlushCache()

    if min_pixels and int(min_pixels) > 0:
        sieved = mem_drv.Create('', xsize, ysize, 1, gdal.GDT_Byte)
        sieved.SetGeoTransform(gt); sieved.SetProjection(proj)
        sieved.GetRasterBand(1).SetNoDataValue(0)
        # Connectedness=8 is generally better for geological anomaly footprints.
        gdal.SieveFilter(work.GetRasterBand(1), None, sieved.GetRasterBand(1), int(min_pixels), 8)
        work = sieved

    band = work.GetRasterBand(1)
    drv = ogr.GetDriverByName('GPKG')
    dst = drv.CreateDataSource(output_gpkg)
    if dst is None:
        raise QgsProcessingException(f'Could not create output: {output_gpkg}')
    srs = osr.SpatialReference()
    if proj: srs.ImportFromWkt(proj)
    lyr = dst.CreateLayer(layer_name, srs=srs, geom_type=ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn('class', ogr.OFTInteger))
    # Mask band = binary band, so only anomaly cells are polygonised.
    gdal.Polygonize(band, band, lyr, 0, [], callback=None)
    lyr.CreateField(ogr.FieldDefn('class_name', ogr.OFTString))
    lyr.CreateField(ogr.FieldDefn('area_km2', ogr.OFTReal))
    lyr.CreateField(ogr.FieldDefn('anomaly_id', ogr.OFTInteger))
    delete=[]; nid=1; lyr.ResetReading()
    for feat in lyr:
        cv = feat.GetField('class'); geom = feat.GetGeometryRef()
        if cv is None or int(cv) != 1 or geom is None:
            delete.append(feat.GetFID()); continue
        area = geom.GetArea() / 1000000.0
        if min_area_km2 > 0 and area < min_area_km2:
            delete.append(feat.GetFID()); continue
        feat.SetField('class_name', 'Anomaly')
        feat.SetField('area_km2', float(area))
        feat.SetField('anomaly_id', nid)
        lyr.SetFeature(feat); nid += 1
    for fid in delete:
        lyr.DeleteFeature(fid)
    try: dst.ExecuteSQL(f'CREATE SPATIAL INDEX ON {layer_name} (geom)')
    except Exception: pass
    dst = None; src = None; work = None

def load_layer(path, name):
    if not os.path.exists(path): return
    lyr = QgsVectorLayer(path, name, 'ogr') if path.lower().endswith('.gpkg') else QgsRasterLayer(path, name)
    if lyr.isValid(): QgsProject.instance().addMapLayer(lyr)

# -------------------------------------------------------------------------
# Output/path and raster styling helpers added for v2.5
# -------------------------------------------------------------------------
def make_available_path(path):
    """Return path, or path with _vNN suffix if the file already exists.

    This avoids Windows/QGIS GeoTIFF locking errors when a previous output is
    still loaded in the Layers panel. The old layer remains untouched and the
    new run writes a new filename instead of failing with Permission denied.
    """
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for i in range(2, 1000):
        candidate = f'{base}_v{i}{ext}'
        if not os.path.exists(candidate):
            return candidate
    return f'{base}_new{ext}'


def raster_quantile_values(path, low_q=1.0, high_q=99.0):
    arr, _ = read_raster(path)
    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        return None, None, None
    lo = float(np.nanpercentile(valid, low_q))
    hi = float(np.nanpercentile(valid, high_q))
    mid = float(np.nanpercentile(valid, 50.0))
    if hi == lo:
        hi = float(np.nanmax(valid))
        lo = float(np.nanmin(valid))
        mid = (lo + hi) / 2.0
    return lo, mid, hi


def apply_blue_red_pseudocolor(layer, low_q=1.0, high_q=99.0):
    """Apply Singleband pseudocolor renderer with a blue-white-red quantile stretch.

    This deliberately uses the raster's quantile values as the shader minimum,
    median and maximum. Values outside the display quantile range are clipped to
    the end colours so the raster always displays as blue-white-red rather than
    falling back to default greyscale/black rendering.
    """
    try:
        from qgis.PyQt.QtGui import QColor
        from qgis.core import QgsRasterShader, QgsColorRampShader, QgsSingleBandPseudoColorRenderer
        lo, mid, hi = raster_quantile_values(layer.source(), low_q, high_q)
        if lo is None:
            return False
        # Guard against degenerate quantiles.
        if hi <= lo:
            return False
        shader = QgsRasterShader()
        ramp = QgsColorRampShader()
        ramp.setColorRampType(QgsColorRampShader.Interpolated)
        # These methods exist in QGIS 3.x and make the quantile stretch explicit.
        try:
            ramp.setMinimumValue(float(lo))
            ramp.setMaximumValue(float(hi))
        except Exception:
            pass
        try:
            ramp.setClip(True)
        except Exception:
            pass
        ramp.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(float(lo), QColor(0, 0, 255), f'P{low_q:g}'),
            QgsColorRampShader.ColorRampItem(float(mid), QColor(255, 255, 255), 'Median'),
            QgsColorRampShader.ColorRampItem(float(hi), QColor(255, 0, 0), f'P{high_q:g}'),
        ])
        shader.setRasterShaderFunction(ramp)
        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        layer.setRenderer(renderer)
        layer.setContrastEnhancement(None)
        layer.triggerRepaint()
        try:
            layer.reload()
        except Exception:
            pass
        return True
    except Exception as e:
        try:
            print('RadiometricAnalysisPlugin styling failed:', e)
        except Exception:
            pass
        return False

def load_raster_pseudocolor_quantile(path, name, low_q=1.0, high_q=99.0):
    if not os.path.exists(path):
        return None
    lyr = QgsRasterLayer(path, name)
    if lyr.isValid():
        apply_blue_red_pseudocolor(lyr, low_q, high_q)
        QgsProject.instance().addMapLayer(lyr)
        return lyr
    return None

# -------------------------------------------------------------------------
# QGIS grouped layer loading helpers added for dock workflow
# -------------------------------------------------------------------------
KNOWN_RAD_FOLDERS = [
    '00_Data_QC',
    '01_Input_Data',
    '02_Preprocessed',
    '03_Ratio_Maps',
    '04_Normalised_Ratio_Maps',
    '05_Ratio_Threshold_Class_Maps',
    '06_Ratio_Map_Anomaly_Extraction',
    '07_Combined_Maps',
    '08_Combined_Threshold_Maps',
    '09_Combined_Map_Anomaly_Extraction',
    '10_Project_Settings',
]

def _radiometric_group_parts_from_path(path):
    """Return layer-tree parts that mirror the radiometric project folder path.

    Example:
        <project>/05_Ratio_Threshold_Class_Maps/U_Th/x.tif
        -> ['05_Ratio_Threshold_Class_Maps', 'U_Th']
    """
    norm = os.path.normpath(str(path))
    parts = norm.split(os.sep)
    for i, part in enumerate(parts):
        if part in KNOWN_RAD_FOLDERS:
            # everything from the known output folder down to the file's parent dir
            return parts[i:-1]
    # Fallback: use immediate parent only, but still under Radiometric Processing.
    parent = os.path.basename(os.path.dirname(norm))
    return [parent] if parent else []

def add_layer_to_radiometric_group(layer, path):
    """Add a layer under Radiometric Processing, mirroring project folders."""
    try:
        if layer is None or not layer.isValid():
            return False
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
        for part in _radiometric_group_parts_from_path(path):
            if not part:
                continue
            child = target.findGroup(part)
            if child is None:
                child = target.addGroup(part)
            target = child
        if layer.id() not in QgsProject.instance().mapLayers():
            QgsProject.instance().addMapLayer(layer, False)
        # avoid duplicate layer-tree nodes in the target group
        for child in target.children():
            try:
                if hasattr(child, 'layer') and child.layer() and child.layer().id() == layer.id():
                    return True
            except Exception:
                pass
        target.addLayer(layer)
        return True
    except Exception:
        return False

# Override original load helpers with grouped versions. Existing algorithms that
# import these functions will now load outputs into the processing-folder group.

def polygonize_binary_mask_raster(raster_path, output_gpkg, layer_name, min_area_km2=0.0, min_pixels=0):
    """Fast polygon extraction from a binary class raster.

    This method treats class value 1 as anomaly and class value 0/NoData as
    background. If min_pixels > 0, GDAL SieveFilter is applied before
    polygonising so tiny connected regions are removed cheaply in raster space.
    GDAL Polygonize is then called with the anomaly band as its own mask so the
    background is ignored rather than polygonised and deleted later.
    """
    if os.path.exists(output_gpkg):
        try: os.remove(output_gpkg)
        except Exception: pass
    src = gdal.Open(raster_path)
    if src is None:
        raise QgsProcessingException(f'Could not open binary mask raster: {raster_path}')
    proj = src.GetProjection(); gt = src.GetGeoTransform(); xsize = src.RasterXSize; ysize = src.RasterYSize
    src_band = src.GetRasterBand(1)

    # Build an in-memory Byte mask: anomaly=1, background=0.
    mem_drv = gdal.GetDriverByName('MEM')
    work = mem_drv.Create('', xsize, ysize, 1, gdal.GDT_Byte)
    work.SetGeoTransform(gt); work.SetProjection(proj)
    arr = src_band.ReadAsArray()
    mask_arr = ((arr == 1) | (arr == 2) | (arr == 3)).astype('uint8')
    work.GetRasterBand(1).SetNoDataValue(0)
    work.GetRasterBand(1).WriteArray(mask_arr)
    work.GetRasterBand(1).FlushCache()

    if min_pixels and int(min_pixels) > 0:
        sieved = mem_drv.Create('', xsize, ysize, 1, gdal.GDT_Byte)
        sieved.SetGeoTransform(gt); sieved.SetProjection(proj)
        sieved.GetRasterBand(1).SetNoDataValue(0)
        # Connectedness=8 is generally better for geological anomaly footprints.
        gdal.SieveFilter(work.GetRasterBand(1), None, sieved.GetRasterBand(1), int(min_pixels), 8)
        work = sieved

    band = work.GetRasterBand(1)
    drv = ogr.GetDriverByName('GPKG')
    dst = drv.CreateDataSource(output_gpkg)
    if dst is None:
        raise QgsProcessingException(f'Could not create output: {output_gpkg}')
    srs = osr.SpatialReference()
    if proj: srs.ImportFromWkt(proj)
    lyr = dst.CreateLayer(layer_name, srs=srs, geom_type=ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn('class', ogr.OFTInteger))
    # Mask band = binary band, so only anomaly cells are polygonised.
    gdal.Polygonize(band, band, lyr, 0, [], callback=None)
    lyr.CreateField(ogr.FieldDefn('class_name', ogr.OFTString))
    lyr.CreateField(ogr.FieldDefn('area_km2', ogr.OFTReal))
    lyr.CreateField(ogr.FieldDefn('anomaly_id', ogr.OFTInteger))
    delete=[]; nid=1; lyr.ResetReading()
    for feat in lyr:
        cv = feat.GetField('class'); geom = feat.GetGeometryRef()
        if cv is None or int(cv) != 1 or geom is None:
            delete.append(feat.GetFID()); continue
        area = geom.GetArea() / 1000000.0
        if min_area_km2 > 0 and area < min_area_km2:
            delete.append(feat.GetFID()); continue
        feat.SetField('class_name', 'Anomaly')
        feat.SetField('area_km2', float(area))
        feat.SetField('anomaly_id', nid)
        lyr.SetFeature(feat); nid += 1
    for fid in delete:
        lyr.DeleteFeature(fid)
    try: dst.ExecuteSQL(f'CREATE SPATIAL INDEX ON {layer_name} (geom)')
    except Exception: pass
    dst = None; src = None; work = None

def load_layer(path, name):
    if not os.path.exists(path):
        return None
    lyr = QgsVectorLayer(path, name, 'ogr') if path.lower().endswith('.gpkg') else QgsRasterLayer(path, name)
    if lyr.isValid():
        if not add_layer_to_radiometric_group(lyr, path):
            QgsProject.instance().addMapLayer(lyr)
        return lyr
    return None


def load_raster_pseudocolor_quantile(path, name, low_q=1.0, high_q=99.0):
    if not os.path.exists(path):
        return None
    lyr = QgsRasterLayer(path, name)
    if lyr.isValid():
        apply_blue_red_pseudocolor(lyr, low_q, high_q)
        if not add_layer_to_radiometric_group(lyr, path):
            QgsProject.instance().addMapLayer(lyr)
        return lyr
    return None
