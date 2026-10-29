# -*- coding: utf-8 -*-
"""Synthetic validation tools for the Radiometric Analysis plugin.

The validation run creates a deterministic 50 x 50 K/Th/U dataset and a set of
independently computed expected outputs.  It is intended as a small, transparent
parallel trial for checking that the plugin processing logic is doing what the
user expects.
"""
import os
import csv
import math
import json
import re
from datetime import datetime
import numpy as np
from osgeo import gdal, osr

from .core.raster_io import write_raster, read_raster, safe_divide
from .core.raster_stats import normalise


def _ensure(path):
    os.makedirs(path, exist_ok=True)
    return path


def ensure_stats_log_structure(processing_folder):
    """Return canonical 00_Stats_Logs subfolders, creating them if needed."""
    root = _ensure(os.path.join(processing_folder, '00_Stats_Logs'))
    return {
        'root': root,
        'settings': _ensure(os.path.join(root, 'Settings')),
        'validation': _ensure(os.path.join(root, 'Validation')),
        'processing_logs': _ensure(os.path.join(root, 'Processing_Logs')),
        'sessions': _ensure(os.path.join(root, 'Sessions')),
        'statistics': _ensure(os.path.join(root, 'Statistics')),
        'histogram': _ensure(os.path.join(root, 'Histogram')),
    }


def _read_proc_parameter_csv(path):
    """Read plugin processing parameter CSV format into a dict."""
    params = {}
    if not path or not os.path.exists(path):
        return params
    try:
        with open(path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row.get('PARAMETER') or row.get('parameter') or '').strip()
                val = (row.get('VALUE') or row.get('value') or '').strip()
                if key:
                    params[key] = val
    except Exception:
        return {}
    return params


def _discover_recorded_input_paths(processing_folder):
    """Prefer actual recorded ratio input paths over filename guessing."""
    candidates = []
    roots = [
        os.path.join(processing_folder, '00_Stats_Logs', 'Settings'),
        os.path.join(processing_folder, '00_Stats_Logs'),
        os.path.join(processing_folder, '00_Stats_and_Logs', 'Settings'),
        os.path.join(processing_folder, '00_Stats_and_Logs'),
    ]
    names = ['ratio_map_proc_parameters.csv', 'ratio_maps_proc_parameters.csv', 'ratio_map_generation_proc_parameters.csv']
    for root in roots:
        for name in names:
            candidates.append(os.path.join(root, name))
    for root in roots[:2]:
        if os.path.isdir(root):
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    low = fn.lower()
                    if low.endswith('.csv') and 'ratio' in low and 'proc' in low and 'parameter' in low:
                        candidates.append(os.path.join(dirpath, fn))
    for csv_path in candidates:
        params = _read_proc_parameter_csv(csv_path)
        u = params.get('Input_U') or params.get('U') or params.get('Input Uranium')
        k = params.get('Input_K') or params.get('K') or params.get('Input Potassium')
        th = params.get('Input_Th') or params.get('TH') or params.get('Input Thorium')
        if u and k and th and os.path.exists(u) and os.path.exists(k) and os.path.exists(th):
            return {'U': u, 'K': k, 'TH': th, 'source_csv': csv_path}
    return {'U': '', 'K': '', 'TH': '', 'source_csv': ''}




def _as_float(value, default=0.0):
    try:
        if value is None or value == '':
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None or value == '':
        return bool(default)
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y', 'on', 'checked')


def _parse_triplet(value, default):
    """Parse '(a, b, c)', '[a,b,c]' or 'a,b,c' into a float triplet."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except Exception:
            return tuple(default)
    if value is None:
        return tuple(default)
    text = str(value).strip()
    nums = re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', text)
    if len(nums) >= 3:
        try:
            return (float(nums[0]), float(nums[1]), float(nums[2]))
        except Exception:
            pass
    return tuple(default)


def _latest_file_by_tokens(root, required_tokens, extensions=('.csv', '.json'), exclude_tokens=None):
    """Find latest file under root containing all required tokens."""
    exclude_tokens = exclude_tokens or []
    if not root or not os.path.isdir(root):
        return ''
    matches = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            low = fn.lower()
            if not low.endswith(tuple(e.lower() for e in extensions)):
                continue
            if all(tok.lower() in low for tok in required_tokens) and not any(tok.lower() in low for tok in exclude_tokens):
                path = os.path.join(dirpath, fn)
                try:
                    matches.append((os.path.getmtime(path), path))
                except Exception:
                    pass
    if not matches:
        return ''
    return sorted(matches, reverse=True)[0][1]


def _read_settings_json(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _parse_semicolon_kv(text):
    """Parse session-log strings like 'A=1; B=(0.4, 0.4, 0.2)' into a dict."""
    out = {}
    if not text:
        return out
    parts = str(text).split(';')
    for part in parts:
        if '=' not in part:
            continue
        k, v = part.split('=', 1)
        out[k.strip()] = v.strip()
    return out


def _read_latest_session_parameters(processing_folder, process_token):
    """Return latest session-log parameters matching a process-name token."""
    path = os.path.join(processing_folder, '00_Stats_Logs', 'Sessions', 'radiometric_session_log.csv')
    if not os.path.exists(path):
        return {}, ''
    latest = None
    try:
        with open(path, 'r', newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                process = row.get('process', '')
                if process_token.lower() in process.lower():
                    latest = row
    except Exception:
        return {}, path
    if not latest:
        return {}, path
    params = _parse_semicolon_kv(latest.get('parameters', ''))
    inputs = _parse_semicolon_kv(latest.get('inputs', ''))
    outputs = _parse_semicolon_kv(latest.get('outputs', ''))
    merged = dict(params)
    for k, v in inputs.items():
        merged.setdefault('input_' + k, v)
    for k, v in outputs.items():
        merged.setdefault('output_' + k, v)
    return merged, path


def _normalise_by_method(arr, method='Min-Max', low_pct=1.0, high_pct=99.0):
    """Match the Stage 3 normalisation modes used by CombinedAnalysisDialog."""
    a = np.array(arr, copy=True).astype(np.float64)
    valid = np.isfinite(a)
    vals = a[valid]
    out = np.full(a.shape, np.nan, dtype=np.float64)
    if vals.size == 0:
        return out
    method_l = (method or 'Min-Max').lower()
    lo_p = _as_float(low_pct, 1.0)
    hi_p = _as_float(high_pct, 99.0)
    if hi_p <= lo_p:
        hi_p = min(100.0, lo_p + 1.0)
    if method_l.startswith('min-max extremes'):
        lo = float(np.nanpercentile(vals, lo_p)); hi = float(np.nanpercentile(vals, hi_p))
        clipped = np.clip(a, lo, hi)
        if math.isclose(hi, lo):
            out[valid] = 0.0
        else:
            out[valid] = (clipped[valid] - lo) / (hi - lo)
        return np.clip(out, 0.0, 1.0)
    if method_l.startswith('percentile'):
        sorted_vals = np.sort(vals)
        ranks = np.searchsorted(sorted_vals, a[valid], side='right').astype(np.float64)
        out[valid] = ranks / float(sorted_vals.size)
        return np.clip(out, 0.0, 1.0)
    if method_l.startswith('z-score'):
        mu = float(np.nanmean(vals)); sd = float(np.nanstd(vals))
        if sd <= 0 or not np.isfinite(sd):
            out[valid] = 0.5
        else:
            z = (a[valid] - mu) / sd
            out[valid] = (np.clip(z, -3.0, 3.0) + 3.0) / 6.0
        return np.clip(out, 0.0, 1.0)
    if method_l.startswith('robust'):
        med = float(np.nanmedian(vals)); mad = float(np.nanmedian(np.abs(vals - med)))
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
    mn = float(np.nanmin(vals)); mx = float(np.nanmax(vals))
    if math.isclose(mx, mn):
        out[valid] = 0.0
    else:
        out[valid] = (a[valid] - mn) / (mx - mn)
    return np.clip(out, 0.0, 1.0)


def _combine_by_method(n1, n2, inv, weights, method_text='Weighted mean'):
    """Match the Stage 3 RRAI/HRAI combination modes."""
    w1, w2, w3 = weights
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


def _load_parameter_context(processing_folder, fallback_rrai, fallback_hrai, fallback_hri):
    """Load the actual run parameters used by the project, with explicit provenance.

    Priority:
      1. Canonical *_proc_parameters.csv files and latest session log.
      2. Latest saved workflow settings JSON.
      3. Validator fallback defaults, clearly reported.
    """
    log_dirs = ensure_stats_log_structure(processing_folder)
    settings_dir = log_dirs['settings']
    sessions_dir = log_dirs['sessions']
    ctx = {
        'ratio_params': {},
        'ratio_param_source': '',
        'combined_params': {},
        'combined_param_source': '',
        'workflow_json_source': '',
        'normalisation_method': 'Min-Max',
        'normalisation_low_pct': 1.0,
        'normalisation_high_pct': 99.0,
        'rrai_mode': 'Weighted mean',
        'hrai_mode': 'Weighted mean',
        'hri_mode': 'Z-log sum',
        'rrai_weights': tuple(fallback_rrai),
        'hrai_weights': tuple(fallback_hrai),
        'hri_scalars': tuple(fallback_hri),
        'provenance_notes': [],
    }

    ratio_csv = _latest_file_by_tokens(settings_dir, ['ratio', 'proc', 'parameter'], extensions=('.csv',))
    if ratio_csv:
        ctx['ratio_params'] = _read_proc_parameter_csv(ratio_csv)
        ctx['ratio_param_source'] = ratio_csv

    combined_params, session_path = _read_latest_session_parameters(processing_folder, 'Generate Combined Maps')
    if combined_params:
        ctx['combined_params'].update(combined_params)
        ctx['combined_param_source'] = session_path

    settings_json = _latest_file_by_tokens(settings_dir, ['radiometric', 'settings'], extensions=('.json',))
    if not settings_json:
        settings_json = _latest_file_by_tokens(processing_folder, ['radiometric', 'settings'], extensions=('.json',))
    workflow = _read_settings_json(settings_json)
    if workflow:
        ctx['workflow_json_source'] = settings_json
        st3 = workflow.get('stage3', {}) if isinstance(workflow, dict) else {}
        if isinstance(st3, dict):
            ctx['normalisation_method'] = st3.get('norm_method', ctx['normalisation_method'])
            ctx['normalisation_low_pct'] = _as_float(st3.get('norm_low_pct'), ctx['normalisation_low_pct'])
            ctx['normalisation_high_pct'] = _as_float(st3.get('norm_high_pct'), ctx['normalisation_high_pct'])
            ctx['rrai_mode'] = st3.get('rrai_combination_method', ctx['rrai_mode'])
            ctx['hrai_mode'] = st3.get('hrai_combination_method', ctx['hrai_mode'])
            ctx['hri_mode'] = st3.get('hri_combination_method', ctx['hri_mode'])
            ctx['rrai_weights'] = _parse_triplet((st3.get('rrai_w_uth'), st3.get('rrai_w_second'), st3.get('rrai_w_kth')), ctx['rrai_weights'])
            ctx['hrai_weights'] = _parse_triplet((st3.get('hrai_w_uth'), st3.get('hrai_w_second'), st3.get('hrai_w_kth')), ctx['hrai_weights'])
            ctx['hri_scalars'] = _parse_triplet((st3.get('hri_w_uth'), st3.get('hri_w_second'), st3.get('hri_w_kth')), ctx['hri_scalars'])

    cp = ctx['combined_params']
    if cp:
        ctx['normalisation_method'] = cp.get('Normalisation', ctx['normalisation_method'])
        ctx['normalisation_low_pct'] = _as_float(cp.get('Normalisation_low_pct') or cp.get('Normalisation_Low_Pct') or cp.get('Norm_low_pct'), ctx['normalisation_low_pct'])
        ctx['normalisation_high_pct'] = _as_float(cp.get('Normalisation_high_pct') or cp.get('Normalisation_High_Pct') or cp.get('Norm_high_pct'), ctx['normalisation_high_pct'])
        ctx['rrai_mode'] = cp.get('RRAI_mode', ctx['rrai_mode'])
        ctx['hrai_mode'] = cp.get('HRAI_mode', ctx['hrai_mode'])
        ctx['hri_mode'] = cp.get('HRI_mode', ctx['hri_mode'])
        ctx['rrai_weights'] = _parse_triplet(cp.get('RRAI_weights'), ctx['rrai_weights'])
        ctx['hrai_weights'] = _parse_triplet(cp.get('HRAI_weights'), ctx['hrai_weights'])
        ctx['hri_scalars'] = _parse_triplet(cp.get('HRI_scalars'), ctx['hri_scalars'])

    # If no explicit combined context exists, keep defaults but say so loudly.
    if not ctx['combined_param_source'] and not ctx['workflow_json_source']:
        ctx['provenance_notes'].append('No combined-map settings/session found; validator used fallback defaults for combined maps.')
    if not ctx['ratio_param_source']:
        ctx['provenance_notes'].append('No ratio processing parameter CSV found; validator used source rasters and default denominator/clipping assumptions.')
    return ctx


def _profile(width=50, height=50):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(28350)  # MGA Zone 50, useful for WA test projects.
    return {
        'xsize': width,
        'ysize': height,
        'geotransform': (500000.0, 25.0, 0.0, 7000000.0, 0.0, -25.0),
        'projection': srs.ExportToWkt(),
    }


def _stats(arr):
    v = arr[np.isfinite(arr)]
    if v.size == 0:
        return {'count': 0, 'min': '', 'max': '', 'mean': '', 'std': '', 'p05': '', 'p50': '', 'p95': ''}
    return {
        'count': int(v.size),
        'min': float(np.nanmin(v)),
        'max': float(np.nanmax(v)),
        'mean': float(np.nanmean(v)),
        'std': float(np.nanstd(v)),
        'p05': float(np.nanpercentile(v, 5)),
        'p50': float(np.nanpercentile(v, 50)),
        'p95': float(np.nanpercentile(v, 95)),
    }


def _zscore_log_ratio(ratio, invert=False):
    arr = np.asarray(ratio, dtype=np.float64)
    if invert:
        tmp = np.full(arr.shape, np.nan, dtype=np.float64)
        ok = np.isfinite(arr) & (arr > 0)
        tmp[ok] = 1.0 / arr[ok]
        arr = tmp
    out = np.full(arr.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(arr) & (arr > 0)
    if not np.any(valid):
        return out
    logv = np.log(arr[valid])
    mu = float(np.nanmean(logv))
    sd = float(np.nanstd(logv))
    if sd <= 0 or not np.isfinite(sd):
        out[valid] = 0.0
    else:
        out[valid] = (logv - mu) / sd
    return out


def _normalised_weights(a, b, c):
    total = float(a) + float(b) + float(c)
    if total == 0 or not math.isfinite(total):
        return (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    return (float(a) / total, float(b) / total, float(c) / total)


def _combine(a, b, c, w1, w2, w3):
    out = np.full(a.shape, np.nan, dtype=np.float64)
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(c)
    out[ok] = w1 * a[ok] + w2 * b[ok] + w3 * c[ok]
    return out


def synthetic_arrays(width=50, height=50):
    """Return deterministic K, Th and U arrays with trends and small anomalies."""
    y, x = np.mgrid[0:height, 0:width]
    k = 1.0 + 0.035 * x + 0.010 * y
    th = 5.0 + 0.060 * x + 0.050 * y
    u = 2.0 + 0.045 * x + 0.025 * y

    # Add a small uranium-rich anomaly and a potassium-low alteration patch.
    anomaly = ((x - 33.0) ** 2 + (y - 17.0) ** 2) <= 36.0
    u[anomaly] += 2.75
    k[anomaly] *= 0.78

    # Add a thorium high/background patch and a few nodata cells.
    th_patch = ((x - 14.0) ** 2 + (y - 35.0) ** 2) <= 49.0
    th[th_patch] += 1.8
    nodata = (x < 3) & (y < 3)
    k[nodata] = np.nan
    th[nodata] = np.nan
    u[nodata] = np.nan
    return k.astype(np.float64), th.astype(np.float64), u.astype(np.float64)


def expected_outputs(k, th, u):
    uth = safe_divide(u, th)
    uk = safe_divide(u, k)
    ku = safe_divide(k, u)
    kth = safe_divide(k, th)

    n_uth = normalise(uth)
    n_uk = normalise(uk)
    n_ku = normalise(ku)
    n_kth = normalise(kth)
    inv_kth = np.full(n_kth.shape, np.nan, dtype=np.float64)
    ok = np.isfinite(n_kth)
    inv_kth[ok] = 1.0 - n_kth[ok]

    z_uth = _zscore_log_ratio(uth)
    z_uk = _zscore_log_ratio(uk)
    z_ku = _zscore_log_ratio(ku)
    z_kth = _zscore_log_ratio(kth)
    z_thk = _zscore_log_ratio(kth, invert=True)

    rw = _normalised_weights(0.4, 0.4, 0.2)
    hw = _normalised_weights(0.25, 0.6, 0.15)
    rrai = _combine(n_uth, n_uk, inv_kth, *rw)
    hrai = _combine(n_uth, n_ku, inv_kth, *hw)
    hri = z_uth + z_uk - z_thk

    return {
        'ratios': {
            'U_Th_ratio': uth,
            'U_K_ratio': uk,
            'K_U_ratio': ku,
            'K_Th_ratio': kth,
        },
        'normalised': {
            'U_Th_norm': n_uth,
            'U_K_norm': n_uk,
            'K_U_norm': n_ku,
            'K_Th_norm': n_kth,
            'K_Th_inverse_norm': inv_kth,
        },
        'zlog': {
            'U_Th_zlog': z_uth,
            'U_K_zlog': z_uk,
            'K_U_zlog': z_ku,
            'K_Th_zlog': z_kth,
            'Th_K_zlog': z_thk,
        },
        'combined': {
            'RRAI_U_Uranium_Enrichment_Index': rrai,
            'HRAI_KU_Hybrid_Alteration_Source_Index': hrai,
            'HRI_Hydrocarbon_Radiometric_Index': hri,
        },
    }


def _write_stats_csv(path, arrays_by_group):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['group', 'name', 'count', 'min', 'max', 'mean', 'std', 'p05', 'p50', 'p95'])
        for group, arrays in arrays_by_group.items():
            for name, arr in arrays.items():
                st = _stats(arr)
                w.writerow([group, name, st['count'], st['min'], st['max'], st['mean'], st['std'], st['p05'], st['p50'], st['p95']])


def _compare_if_exists(actual_path, expected, rows, tolerance=1.0e-5):
    if not os.path.exists(actual_path):
        rows.append([actual_path, 'NOT_FOUND', '', '', 'Actual plugin output not present yet.'])
        return
    actual, _ = read_raster(actual_path)
    if actual.shape != expected.shape:
        rows.append([actual_path, 'FAIL', '', '', 'Shape mismatch: actual %s expected %s' % (actual.shape, expected.shape)])
        return
    ok = np.isfinite(actual) & np.isfinite(expected)
    same_nan = (~np.isfinite(actual)) & (~np.isfinite(expected))
    bad_nan = (~ok) & (~same_nan)
    if np.any(ok):
        diff = np.abs(actual[ok] - expected[ok])
        max_diff = float(np.nanmax(diff))
        mean_diff = float(np.nanmean(diff))
    else:
        max_diff = float('nan')
        mean_diff = float('nan')
    status = 'PASS' if (not np.any(bad_nan) and (not np.isfinite(max_diff) or max_diff <= tolerance)) else 'FAIL'
    rows.append([actual_path, status, max_diff, mean_diff, 'tolerance=%g' % tolerance])


def run_synthetic_validation(processing_folder):
    """Create synthetic rasters, expected outputs, and an optional comparison report.

    Returns
    -------
    tuple[str, str]
        (report_path, validation_project_folder)
    """
    root = _ensure(os.path.join(processing_folder, 'Validation_Synthetic_50x50'))
    inputs_dir = _ensure(os.path.join(root, '01_Input_Data'))
    ratios_dir = _ensure(os.path.join(root, '03_Ratio_Maps_EXPECTED'))
    norm_dir = _ensure(os.path.join(root, '04_Normalised_Ratio_Maps_EXPECTED'))
    zlog_dir = _ensure(os.path.join(root, '04_ZLog_Ratio_Maps_EXPECTED'))
    combined_dir = _ensure(os.path.join(root, '05_Combined_Maps_EXPECTED'))

    profile = _profile(50, 50)
    k, th, u = synthetic_arrays(50, 50)
    write_raster(os.path.join(inputs_dir, 'K_synthetic_50x50.tif'), k, profile)
    write_raster(os.path.join(inputs_dir, 'Th_synthetic_50x50.tif'), th, profile)
    write_raster(os.path.join(inputs_dir, 'U_synthetic_50x50.tif'), u, profile)

    expected = expected_outputs(k, th, u)
    for name, arr in expected['ratios'].items():
        write_raster(os.path.join(ratios_dir, name + '.tif'), arr, profile)
    for name, arr in expected['normalised'].items():
        write_raster(os.path.join(norm_dir, name + '.tif'), arr, profile)
    for name, arr in expected['zlog'].items():
        write_raster(os.path.join(zlog_dir, name + '.tif'), arr, profile)
    for name, arr in expected['combined'].items():
        write_raster(os.path.join(combined_dir, name + '.tif'), arr, profile)

    validation_dir = ensure_stats_log_structure(processing_folder)['validation']
    stats_csv = os.path.join(validation_dir, 'Validation_Expected_Statistics.csv')
    _write_stats_csv(stats_csv, {
        'input': {'K': k, 'Th': th, 'U': u},
        'ratio': expected['ratios'],
        'normalised': expected['normalised'],
        'zlog': expected['zlog'],
        'combined': expected['combined'],
    })

    # Compare against actual outputs in the selected project folder if the user has already run the plugin.
    rows = []
    actual_ratio_dir = os.path.join(processing_folder, '03_Ratio_Maps')
    actual_norm_dir = os.path.join(processing_folder, '04_Normalised_Ratio_Maps')
    actual_zlog_dir = os.path.join(processing_folder, '04_ZLog_Ratio_Maps')
    actual_combined_dir = os.path.join(processing_folder, '07_Combined_Maps')
    for name, arr in expected['ratios'].items():
        _compare_if_exists(os.path.join(actual_ratio_dir, name + '.tif'), arr, rows)
    for name, arr in expected['normalised'].items():
        _compare_if_exists(os.path.join(actual_norm_dir, name + '.tif'), arr, rows)
    for name, arr in expected['zlog'].items():
        _compare_if_exists(os.path.join(actual_zlog_dir, name + '.tif'), arr, rows)
    for name, arr in expected['combined'].items():
        _compare_if_exists(os.path.join(actual_combined_dir, name + '.tif'), arr, rows)

    comparison_csv = os.path.join(validation_dir, 'Validation_Actual_Comparison.csv')
    with open(comparison_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['actual_path', 'status', 'max_abs_difference', 'mean_abs_difference', 'note'])
        w.writerows(rows)

    report_path = os.path.join(validation_dir, 'Validation_Report.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('Radiometric Analysis Synthetic Validation Report\n')
        f.write('Generated: %s\n\n' % datetime.now().isoformat(timespec='seconds'))
        f.write('Purpose\n')
        f.write('This 50 x 50 dataset is a deterministic manual/automated QC trial. It lets you test whether normalisation, ratio maps, z-log maps and combined maps behave as expected.\n\n')
        f.write('Input rasters\n')
        f.write('K_synthetic_50x50.tif, Th_synthetic_50x50.tif and U_synthetic_50x50.tif are written to 01_Input_Data. They share the same CRS, extent, cell size and dimensions.\n\n')
        f.write('Expected formulas\n')
        f.write('Ratios: U/Th, U/K, K/U and K/Th using safe division.\n')
        f.write('Normalised maps: min-max normalisation to 0-1 over valid cells.\n')
        f.write('Z-log maps: z(ln(ratio)); Th/K is calculated as z(ln(1/(K/Th))).\n')
        f.write('RRAI-U: 0.4 norm(U/Th) + 0.4 norm(U/K) + 0.2 [1 - norm(K/Th)].\n')
        f.write('HRAI-KU: 0.25 norm(U/Th) + 0.6 norm(K/U) + 0.15 [1 - norm(K/Th)].\n')
        f.write('HRI: z(ln(U/Th)) + z(ln(U/K)) - z(ln(Th/K)).\n\n')
        f.write('Expected output folders\n')
        f.write('%s\n%s\n%s\n%s\n\n' % (ratios_dir, norm_dir, zlog_dir, combined_dir))
        f.write('Statistics CSV\n%s\n\n' % stats_csv)
        f.write('Actual comparison CSV\n%s\n\n' % comparison_csv)
        f.write('How to use\n')
        f.write('1. Create or select a clean processing folder.\n')
        f.write('2. Click Run Synthetic Raster Validation to create this sample project.\n')
        f.write('3. Use the three synthetic input rasters as Stage 1 inputs and run the normal plugin workflow.\n')
        f.write('4. Re-run Synthetic Raster Validation to compare actual plugin outputs against the expected outputs.\n')
        f.write('5. Check Validation_Actual_Comparison.csv for PASS/FAIL and max absolute differences.\n')
    return report_path, root


# -----------------------------------------------------------------------------
# Independent processing-folder validator
# -----------------------------------------------------------------------------

def _find_latest_file(folder, include_tokens, exclude_tokens=None):
    """Find latest GeoTIFF in folder matching all include tokens."""
    exclude_tokens = exclude_tokens or []
    if not folder or not os.path.isdir(folder):
        return ''
    matches = []
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            if not fn.lower().endswith(('.tif', '.tiff')):
                continue
            low = fn.lower()
            if all(tok.lower() in low for tok in include_tokens) and not any(tok.lower() in low for tok in exclude_tokens):
                path = os.path.join(root, fn)
                matches.append((os.path.getmtime(path), path))
    if not matches:
        return ''
    matches.sort(reverse=True)
    return matches[0][1]


def _grid_note(profile_a, profile_b):
    notes = []
    if profile_a.get('xsize') != profile_b.get('xsize') or profile_a.get('ysize') != profile_b.get('ysize'):
        notes.append('dimension mismatch')
    if tuple(profile_a.get('geotransform') or ()) != tuple(profile_b.get('geotransform') or ()):
        notes.append('geotransform/alignment mismatch')
    if (profile_a.get('projection') or '') != (profile_b.get('projection') or ''):
        notes.append('projection differs')
    return '; '.join(notes)


def _compare_arrays(label, actual_path, expected, expected_profile, rows, tolerance=1.0e-5):
    if not actual_path or not os.path.exists(actual_path):
        rows.append([label, 'NOT_FOUND', actual_path or '', '', '', '', 'Actual raster not found.'])
        return
    try:
        actual, actual_profile = read_raster(actual_path)
    except Exception as e:
        rows.append([label, 'ERROR', actual_path, '', '', '', str(e)])
        return
    note = _grid_note(actual_profile, expected_profile)
    if actual.shape != expected.shape:
        rows.append([label, 'FAIL', actual_path, '', '', '', 'Shape mismatch: actual %s expected %s. %s' % (actual.shape, expected.shape, note)])
        return
    ok = np.isfinite(actual) & np.isfinite(expected)
    same_nan = (~np.isfinite(actual)) & (~np.isfinite(expected))
    bad_nan = (~ok) & (~same_nan)
    if np.any(ok):
        diff = np.abs(actual[ok] - expected[ok])
        max_diff = float(np.nanmax(diff))
        mean_diff = float(np.nanmean(diff))
    else:
        max_diff = float('nan')
        mean_diff = float('nan')
    bad_nan_count = int(np.count_nonzero(bad_nan))
    status = 'PASS' if (bad_nan_count == 0 and (not np.isfinite(max_diff) or max_diff <= tolerance) and not note) else 'FAIL'
    rows.append([label, status, actual_path, max_diff, mean_diff, bad_nan_count, note or 'tolerance=%g' % tolerance])


def _z_from_ratio(ratio):
    out = np.full(ratio.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(ratio) & (ratio > 0)
    if not np.any(valid):
        return out
    vals = np.log(ratio[valid])
    mu = float(np.nanmean(vals)); sd = float(np.nanstd(vals))
    if sd <= 0 or not np.isfinite(sd):
        out[valid] = 0.0
    else:
        out[valid] = (vals - mu) / sd
    return out


def _normalised_weight_triplet(a, b, c):
    s = float(a) + float(b) + float(c)
    if s <= 0 or not np.isfinite(s):
        return (1.0/3.0, 1.0/3.0, 1.0/3.0)
    return (float(a)/s, float(b)/s, float(c)/s)


def _combine_weighted(n1, n2, n3, weights):
    out = np.full(n1.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(n1) & np.isfinite(n2) & np.isfinite(n3)
    w1, w2, w3 = weights
    out[valid] = w1*n1[valid] + w2*n2[valid] + w3*n3[valid]
    return out


def validate_processing_folder(processing_folder, tolerance=1.0e-5,
                               rrai_weights=(0.4,0.4,0.2), hrai_weights=(0.25,0.6,0.15),
                               hri_scalars=(1.0,1.0,1.0)):
    """Independently validate plugin-generated raster outputs in a processing folder.

    This validator intentionally recomputes outputs using NumPy from the raster files in
    the processing folder, rather than trusting intermediate plugin products.  It is a
    raster-only QC/audit pass and does not validate vector polygonisation.
    """
    if not processing_folder or not os.path.isdir(processing_folder):
        raise RuntimeError('Select a valid processing folder to validate.')

    input_dir = os.path.join(processing_folder, '01_Input_Data')
    filt_dir = os.path.join(processing_folder, '02_Filtered_Rasters')
    ratio_dir = os.path.join(processing_folder, '03_Ratio_Maps')
    norm_dir = os.path.join(processing_folder, '04_Normalised_Ratio_Maps')
    zlog_dir = os.path.join(processing_folder, '04_ZLog_Ratio_Maps')
    combined_dir = os.path.join(processing_folder, '07_Combined_Maps')
    log_dirs = ensure_stats_log_structure(processing_folder)
    validator_dir = log_dirs['validation']
    param_ctx = _load_parameter_context(processing_folder, rrai_weights, hrai_weights, hri_scalars)
    ratio_params = param_ctx.get('ratio_params', {})
    effective_rrai_weights = param_ctx.get('rrai_weights', rrai_weights)
    effective_hrai_weights = param_ctx.get('hrai_weights', hrai_weights)
    effective_hri_scalars = param_ctx.get('hri_scalars', hri_scalars)

    # Prefer the exact rasters recorded by the ratio generation step.
    recorded = _discover_recorded_input_paths(processing_folder)
    u_path, k_path, th_path = recorded.get('U',''), recorded.get('K',''), recorded.get('TH','')

    # Fallback to folder discovery for old projects or manually assembled folders.
    if not (u_path and k_path and th_path):
        search_folders = [
            os.path.join(processing_folder, '02_Preprocessed'),
            filt_dir,
            input_dir,
        ]
        for search_base in search_folders:
            if not os.path.isdir(search_base):
                continue
            u_path = u_path or _find_latest_file(search_base, ['u'], exclude_tokens=['ratio','norm','rrai','hrai','hri','k_u','u_k','u_th'])
            k_path = k_path or _find_latest_file(search_base, ['k'], exclude_tokens=['ratio','norm','rrai','hrai','hri','u_k','k_u','k_th'])
            th_path = th_path or _find_latest_file(search_base, ['th'], exclude_tokens=['ratio','norm','rrai','hrai','hri','u_th','k_th'])
            if u_path and k_path and th_path:
                break

    rows = []
    rows.append(['Parameter discovery - ratio settings', 'PASS' if param_ctx.get('ratio_param_source') else 'FAIL', param_ctx.get('ratio_param_source') or '', '', '', '', 'Using recorded ratio parameters.' if param_ctx.get('ratio_param_source') else 'No ratio parameter file found; using fallback assumptions.'])
    rows.append(['Parameter discovery - combined settings', 'PASS' if (param_ctx.get('combined_param_source') or param_ctx.get('workflow_json_source')) else 'FAIL', param_ctx.get('combined_param_source') or param_ctx.get('workflow_json_source') or '', '', '', '', 'Using latest combined-map session/settings parameters.' if (param_ctx.get('combined_param_source') or param_ctx.get('workflow_json_source')) else 'No combined settings found; using fallback assumptions.'])
    if not (u_path and k_path and th_path):
        rows.append(['Input discovery', 'FAIL', '', '', '', '', 'Could not identify U, K and Th source rasters. Checked recorded ratio parameters, 02_Preprocessed, 02_Filtered_Rasters and 01_Input_Data.'])
    else:
        if recorded.get('source_csv'):
            rows.append(['Input discovery', 'PASS', recorded.get('source_csv'), '', '', '', 'Using recorded Input_U/Input_K/Input_Th paths from ratio processing parameters.'])
        else:
            rows.append(['Input discovery', 'PASS', '', '', '', '', 'Using discovered source rasters from processing folder.'])
        U, profile = read_raster(u_path); K, k_profile = read_raster(k_path); Th, th_profile = read_raster(th_path)
        for lbl, prof in [('K input grid', k_profile), ('Th input grid', th_profile)]:
            note = _grid_note(profile, prof)
            rows.append([lbl, 'PASS' if not note else 'FAIL', '', '', '', '', note or 'same grid as U'])
        if U.shape == K.shape == Th.shape:
            # Recreate Stage 1 ratio maps using the recorded denominator guard and clipping parameters.
            guard_mode = str(ratio_params.get('Denominator_guard_mode', 'Absolute value'))
            guard_is_percentile = guard_mode.lower().startswith('percentile')
            abs_min_den = _as_float(ratio_params.get('Minimum_denominator_guard'), 0.0)
            if guard_is_percentile:
                denominator_guards = {
                    'U_Th_ratio': _as_float(ratio_params.get('Computed_Th_guard'), 0.0),
                    'U_K_ratio': _as_float(ratio_params.get('Computed_K_guard'), 0.0),
                    'K_U_ratio': _as_float(ratio_params.get('Computed_U_guard'), 0.0),
                    'K_Th_ratio': _as_float(ratio_params.get('Computed_Th_guard'), 0.0),
                }
            else:
                denominator_guards = {
                    'U_Th_ratio': abs_min_den,
                    'U_K_ratio': abs_min_den,
                    'K_U_ratio': abs_min_den,
                    'K_Th_ratio': abs_min_den,
                }
            ratios = {
                'U_Th_ratio': safe_divide(U, Th, min_denominator=denominator_guards['U_Th_ratio']),
                'U_K_ratio': safe_divide(U, K, min_denominator=denominator_guards['U_K_ratio']),
                'K_U_ratio': safe_divide(K, U, min_denominator=denominator_guards['K_U_ratio']),
                'K_Th_ratio': safe_divide(K, Th, min_denominator=denominator_guards['K_Th_ratio']),
            }
            if _as_bool(ratio_params.get('Ratio_Extreme_Clipping'), False):
                low_q = _as_float(ratio_params.get('Ratio_Clip_Low_Q'), 1.0)
                high_q = _as_float(ratio_params.get('Ratio_Clip_High_Q'), 99.0)
                if high_q <= low_q:
                    high_q = min(100.0, low_q + 1.0)
                for rname, arr in list(ratios.items()):
                    vals = arr[np.isfinite(arr)]
                    if vals.size:
                        lo = float(np.nanpercentile(vals, low_q))
                        hi = float(np.nanpercentile(vals, high_q))
                        clipped = np.array(arr, copy=True)
                        clipped[np.isfinite(clipped) & (clipped < lo)] = lo
                        clipped[np.isfinite(clipped) & (clipped > hi)] = hi
                        ratios[rname] = clipped
            for name, arr in ratios.items():
                actual = _find_latest_file(ratio_dir, name.lower().split('_'), exclude_tokens=['class','threshold'])
                _compare_arrays('Ratio ' + name, actual, arr, profile, rows, tolerance)

            norm_method = param_ctx.get('normalisation_method', 'Min-Max')
            norm_low = param_ctx.get('normalisation_low_pct', 1.0)
            norm_high = param_ctx.get('normalisation_high_pct', 99.0)
            n_uth = _normalise_by_method(ratios['U_Th_ratio'], norm_method, norm_low, norm_high)
            n_uk = _normalise_by_method(ratios['U_K_ratio'], norm_method, norm_low, norm_high)
            n_ku = _normalise_by_method(ratios['K_U_ratio'], norm_method, norm_low, norm_high)
            n_kth = _normalise_by_method(ratios['K_Th_ratio'], norm_method, norm_low, norm_high)
            inv_kth = np.full(n_kth.shape, np.nan, dtype=np.float64)
            ok = np.isfinite(n_kth); inv_kth[ok] = 1.0 - n_kth[ok]
            normals = {
                'U_Th_norm': n_uth,
                'U_K_norm': n_uk,
                'K_U_norm': n_ku,
                'K_Th_norm': n_kth,
                'K_Th_inverse_norm': inv_kth,
            }
            for name, arr in normals.items():
                toks = name.lower().split('_')
                actual = _find_latest_file(norm_dir, toks, exclude_tokens=['class','threshold'])
                _compare_arrays('Normalised ' + name, actual, arr, profile, rows, tolerance)

            zlogs = {
                'U_Th_zlog': _z_from_ratio(ratios['U_Th_ratio']),
                'U_K_zlog': _z_from_ratio(ratios['U_K_ratio']),
                'K_U_zlog': _z_from_ratio(ratios['K_U_ratio']),
                'K_Th_zlog': _z_from_ratio(ratios['K_Th_ratio']),
                'Th_K_zlog': _z_from_ratio(safe_divide(Th, K)),
            }
            for name, arr in zlogs.items():
                actual = _find_latest_file(zlog_dir, name.lower().split('_'), exclude_tokens=['class','threshold'])
                _compare_arrays('ZLog ' + name, actual, arr, profile, rows, tolerance)

            rw = _normalised_weight_triplet(*effective_rrai_weights)
            hw = _normalised_weight_triplet(*effective_hrai_weights)
            rrai = _combine_by_method(n_uth, n_uk, inv_kth, rw, param_ctx.get('rrai_mode', 'Weighted mean'))
            hrai = _combine_by_method(n_uth, n_ku, inv_kth, hw, param_ctx.get('hrai_mode', 'Weighted mean'))
            hri = np.full(U.shape, np.nan, dtype=np.float64)
            z_uth, z_uk, z_thk = zlogs['U_Th_zlog'], zlogs['U_K_zlog'], zlogs['Th_K_zlog']
            valid = np.isfinite(z_uth) & np.isfinite(z_uk) & np.isfinite(z_thk)
            hri[valid] = float(effective_hri_scalars[0])*z_uth[valid] + float(effective_hri_scalars[1])*z_uk[valid] - float(effective_hri_scalars[2])*z_thk[valid]
            combined = {
                'RRAI_U_Uranium_Enrichment_Index': rrai,
                'HRAI_KU_Hybrid_Alteration_Source_Index': hrai,
                'HRI_Hydrocarbon_Radiometric_Index': hri,
            }
            for name, arr in combined.items():
                actual = _find_latest_file(combined_dir, name.lower().split('_'), exclude_tokens=['class','threshold'])
                _compare_arrays('Combined ' + name, actual, arr, profile, rows, tolerance)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(validator_dir, 'processing_folder_validation_%s.csv' % ts)
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['check', 'status', 'actual_path', 'max_abs_difference', 'mean_abs_difference', 'bad_nan_count', 'note'])
        w.writerows(rows)

    pass_count = sum(1 for r in rows if len(r) > 1 and r[1] == 'PASS')
    fail_count = sum(1 for r in rows if len(r) > 1 and r[1] == 'FAIL')
    missing_count = sum(1 for r in rows if len(r) > 1 and r[1] == 'NOT_FOUND')
    error_count = sum(1 for r in rows if len(r) > 1 and r[1] == 'ERROR')
    report_path = os.path.join(validator_dir, 'processing_folder_validation_%s.txt' % ts)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('Radiometric Processing Folder Independent Raster Validation\n')
        f.write('Generated: %s\n' % datetime.now().isoformat(timespec='seconds'))
        f.write('Processing folder: %s\n\n' % processing_folder)
        f.write('This validator recomputes raster outputs independently with NumPy from U/K/Th rasters and compares them to plugin outputs.\n')
        f.write('It validates rasters only; it does not validate vector polygonisation.\n\n')
        f.write('Summary: PASS=%d, FAIL=%d, NOT_FOUND=%d, ERROR=%d\n' % (pass_count, fail_count, missing_count, error_count))
        f.write('CSV details: %s\n\n' % csv_path)
        f.write('Parameter source and effective validation settings:\n')
        f.write('Ratio parameter source: %s\n' % (param_ctx.get('ratio_param_source') or 'NOT FOUND - fallback assumptions used'))
        f.write('Combined parameter source: %s\n' % (param_ctx.get('combined_param_source') or param_ctx.get('workflow_json_source') or 'NOT FOUND - fallback assumptions used'))
        f.write('Normalisation method: %s; low_pct=%s; high_pct=%s\n' % (param_ctx.get('normalisation_method'), param_ctx.get('normalisation_low_pct'), param_ctx.get('normalisation_high_pct')))
        f.write('RRAI mode: %s; weights: %s normalised to sum=1\n' % (param_ctx.get('rrai_mode'), effective_rrai_weights))
        f.write('HRAI mode: %s; weights: %s normalised to sum=1\n' % (param_ctx.get('hrai_mode'), effective_hrai_weights))
        f.write('HRI mode: %s; scalars: %s raw z-log sum\n' % (param_ctx.get('hri_mode'), effective_hri_scalars))
        for note in param_ctx.get('provenance_notes', []):
            f.write('WARNING: %s\n' % note)
    return report_path, csv_path, {'PASS': pass_count, 'FAIL': fail_count, 'NOT_FOUND': missing_count, 'ERROR': error_count}
