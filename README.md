# Geomatic Exploration Suite

One QGIS plugin combining radiometric analysis, seismic CDP interval annotation
and SEG-Y navigation conversion. Author and maintainer: **Donald Annor Appiah**.

Source: https://github.com/Donnai/geomatic-exploration-suite

Issues: https://github.com/Donnai/geomatic-exploration-suite/issues

## Install

Install `geomatic_exploration_suite.zip` from **Plugins > Manage and Install
Plugins > Install from ZIP**, then enable **Geomatic Exploration Suite**.
Use its Plugins menu or toolbar to select Radiometric analysis, Seismic CDP and
SEG-Y tools, or the user guide. All open in one dock with three tabs.

The archive must contain the single `geomatic_exploration_suite/` folder.
Python caches, compiled bytecode and nested plugin ZIP files are excluded.

## Tools

- Radiometric input QC, alignment, conditioning and filtering.
- U/Th, U/K, K/U and K/Th ratios, threshold review, previews and anomaly polygons.
- RRAI-U, HRAI-KU and HRI, normalization, diagnostics, PCA and validation tools.
- Sparse seismic CDP labels at selected chainage intervals.
- SEG-Y navigation-header conversion to point/line Shapefiles and Kingdom CSV/TXT postings.

Read [the complete user guide](geomatic_exploration_suite/USER_GUIDE.md) for the
workflow, inputs, outputs, presets, coordinate handling and troubleshooting.
It is also available in the plugin's Guide tab. The supplied GUI and Processing
coefficient presets differ; the guide documents the differences so comparative
runs can use matching settings.

## Dependencies and compatibility

QGIS/PyQGIS, NumPy and GDAL are required. Matplotlib enables radiometric plots.
SEG-Y conversion additionally needs `segyio` installed in QGIS's Python
environment. In an OSGeo4W shell: `python -m pip install segyio`.

The original QGIS 3.22 minimum declaration is retained. The integrated package
was tested using real QGIS 3.34.4 APIs with synthetic data. QGIS 4 / Qt 6 and
every processing option are not claimed as validated. No operational study,
input datasets or private test logs are included in this public repository.

## License and attribution

GPL-3.0-or-later. See [LICENSE](LICENSE) and the retained component credits in
[AUTHORS.md](geomatic_exploration_suite/AUTHORS.md).

## Version 1.0.1

Optional-operation fallback failures are now logged for diagnosis. Scientific
settings and numerical formulas remain unchanged.
