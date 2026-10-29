# -*- coding: utf-8 -*-
"""
Dynamic algorithm discovery for the Radiometric Analysis plugin.

Each Processing algorithm may expose:
    WORKFLOW_STEP_ID = 'step_01_preprocessing'

The plugin buttons use these stable step identifiers instead of depending on
Python filenames or display names. This lets you replace/rename script files as
long as the algorithm class keeps the same WORKFLOW_STEP_ID.
"""
import logging
import importlib
import inspect
import os
import pkgutil

from qgis.core import QgsProcessingAlgorithm

PLUGIN_PROVIDER_ID = 'geomatic_exploration_suite'

STEP_ORDER = [
    'step_01_preprocessing',
    'step_02_ratio_generation',
    'step_03_ratio_anomaly_extraction',
    'step_04_combined_analysis',
]

STEP_LABELS = {
    'step_01_preprocessing': '1A - Pre-Processing / Data Conditioning',
    'step_02_ratio_generation': '1B - Ratio Map Generation',
    'step_03_ratio_anomaly_extraction': '2 - Ratio Map Analysis and Anomaly Extraction',
    'step_04_combined_analysis': '3 - Combined Analysis and Anomaly Extraction',
}

# Backward-compatible names. These are fallbacks only.
STEP_NAME_CANDIDATES = {
    'step_01_preprocessing': ['01_preprocessing_data_conditioning'],
    'step_02_ratio_generation': ['02_ratio_map_generation'],
    'step_03_ratio_anomaly_extraction': ['03_ratio_map_anomaly_extraction'],
    'step_04_combined_analysis': ['04_combined_analysis_anomaly_extraction'],
}


def discover_algorithm_classes():
    """Return Processing algorithm classes found in the algs package."""
    classes = []
    package_name = __package__
    package_dir = os.path.dirname(__file__)

    for mod_info in pkgutil.iter_modules([package_dir]):
        name = mod_info.name
        if name.startswith('_') or name in ('utils', 'registry'):
            continue
        module = importlib.import_module(f'{package_name}.{name}')
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is QgsProcessingAlgorithm:
                continue
            if issubclass(obj, QgsProcessingAlgorithm) and obj.__module__ == module.__name__:
                classes.append(obj)
    return classes


def instantiate_algorithms():
    """Create algorithm instances, sorted by stable workflow step where available."""
    items = []
    for cls in discover_algorithm_classes():
        try:
            alg = cls()
            step = getattr(alg, 'WORKFLOW_STEP_ID', None)
            idx = STEP_ORDER.index(step) if step in STEP_ORDER else 999
            items.append((idx, alg.name(), alg))
        except Exception:
            # Do not let one broken experimental script block the whole provider.
            logging.getLogger(__name__).debug("Optional operation failed in instantiate_algorithms; existing fallback retained.", exc_info=True); continue
    return [alg for _, _, alg in sorted(items, key=lambda x: (x[0], x[1]))]


def resolve_algorithm_id_by_step(step_id):
    """Resolve a stable step id to the currently registered QGIS algorithm id."""
    from qgis.core import QgsApplication

    registry = QgsApplication.processingRegistry()
    prefix = PLUGIN_PROVIDER_ID + ':'

    # Preferred: inspect loaded algorithm objects for the common identifier.
    for alg in registry.algorithms():
        try:
            if alg.id().startswith(prefix) and getattr(alg, 'WORKFLOW_STEP_ID', None) == step_id:
                return alg.id()
        except Exception:
            logging.getLogger(__name__).debug("Optional operation failed in resolve_algorithm_id_by_step; existing fallback retained.", exc_info=True)

    # Fallback: old fixed names.
    for name in STEP_NAME_CANDIDATES.get(step_id, []):
        alg_id = prefix + name
        if registry.algorithmById(alg_id) is not None:
            return alg_id

    return None
