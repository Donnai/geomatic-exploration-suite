# Geomatic Exploration Suite — User Guide

Version 1.0.1 · Author and maintainer: **Donald Annor Appiah**

This plugin combines the supplied Radiometric Analysis Suite and Seismic CDP
Interval Annotator into one installable package, shared menu and dock. The
radiometric tools and seismic tools remain distinct workflows: neither changes
the other dataset automatically.

## 1. Install and open

1. Install `geomatic_exploration_suite.zip` using **Plugins > Manage and Install Plugins > Install from ZIP**.
2. Enable **Geomatic Exploration Suite**. Open its menu under **Plugins** or use the toolbar icons.
3. Choose **Radiometric analysis**, **Seismic CDP and SEG-Y tools**, or **User guide and methods**. They open tabs in the same dock.
4. Resize, move or float the dock using QGIS's normal dock controls. Scrollbars keep the existing forms usable in a narrow dock.

The package declares QGIS 3.22 or later within QGIS 3; runtime testing is reported
separately. QGIS 4 / Qt 6 compatibility is not claimed. Keep the original plugins
disabled if you prefer one set of menus. The integrated radiometric provider has
its own identifier, `geomatic_exploration_suite`, so it can be distinguished from
the original provider.

### Dependencies

The QGIS Python environment must include NumPy and GDAL/OGR/OSR. Matplotlib
enables the radiometric plots. **SEG-Y conversion additionally needs `segyio`**;
the rest of the suite can open without it. Install additional packages into the
Python environment used by QGIS. For an OSGeo4W installation, use its shell:

```text
python -m pip install segyio
```

Installing into a separate system Python does not make a package available to
QGIS. Restart QGIS after adding a dependency.

## 2. Prepare a study

Use a new processing folder for each study or processing variant. Keep original
rasters and navigation files separately from generated results. Check the CRS,
extent, pixel size, units and NoData values before processing. For work in the
appropriate Ghana UTM area, WGS 84 / UTM zone 30N is `EPSG:32630`; select it only
when it describes the actual coordinates.

Radiometric inputs are the uranium (U), potassium (K) and thorium (Th) rasters.
Their units, calibration and processing history must be compatible with the
interpretation being made. An optional polygon can limit the study area.
Seismic inputs are an existing CDP point layer or SEG-Y files containing usable
trace-navigation headers.

## 3. Radiometric workflow

The **Radiometric analysis** tab retains the original panel's six sections:
Radiometric Analysis, Work Manager, Data Analysis and QC, Stage 1 – Ratio Maps,
Stage 2 – Ratio Analysis, and Stage 3 – Combined. Start with its processing
folder and inputs; use Work Manager to inspect and organize generated outputs.

### 3.1 Input QC

1. Select the U, K and Th rasters and the processing folder.
2. Review raster statistics, histograms, CRS, resolution and valid/NoData coverage.
3. Confirm that channels align spatially. Inspect unexpected tails, spikes, zeros and negative values before treating them as geological anomalies.
4. Choose the masking and conditioning settings to match the data. Save the settings and QC outputs with the run.

### 3.2 Conditioning and alignment

The preprocessing algorithm uses the U raster as the reference grid and aligns
K and Th to it. A mask retains that grid and writes NoData outside the polygon.
Controls include an additional NoData value, exclusion of values at or below
zero, optional percentile clipping, and mean or median focal filtering.
Available windows range from 3 × 3 to 15 × 15 cells.

Choose the settings explicitly; smoothing and clipping can alter both anomaly
shape and local amplitudes. Compare the conditioned rasters with their inputs.
The Processing algorithm writes alignment, conditioning and filtered products
under `02_Preprocessed`.

### 3.3 Generate four ratio maps

Select the conditioned/filtered rasters or let the tool discover them from the
processing folder. Generate:

| Ratio | Calculation |
| --- | --- |
| U/Th | U divided by Th |
| U/K | U divided by K |
| K/U | K divided by U |
| K/Th | K divided by Th |

Set the minimum denominator guard to exclude unstable division near zero.
Chunked processing and the block-size control are available for large rasters.
Inspect the ratio statistics and spatial patterns before continuing. QGIS display
quantiles affect styling; they are not a second copy of the ratio measurements.
The ratio rasters are written under `03_Ratio_Maps`.

### 3.4 Review thresholds and extract anomalies

Open the Stage 2 review panel. Inspect each histogram and the threshold preview.
U/Th, U/K and K/U use high-tail selection; K/Th uses low-tail selection. A High
percentile of 85 means an upper-tail threshold at P85, while a Very Low threshold
at P5 selects the lower end of the distribution.

Choose and save the threshold values, then review any trending/continuity
settings and extract the anomaly rasters and polygons. Use a minimum area only
when its interpretation and units are appropriate for the study. Record the
threshold method and selected values with the outputs rather than assuming a
particular P90/P95 convention.

### 3.5 Combined indices

For weighted-mean combination, the supplied tools implement:

```text
RRAI-U  = w1*N(U/Th) + w2*N(U/K) + w3*[1 - N(K/Th)]
HRAI-KU = w1*N(U/Th) + w2*N(K/U) + w3*[1 - N(K/Th)]
HRI     = s1*z(ln(U/Th)) + s2*z(ln(U/K)) - s3*z(ln(Th/K))
```

`N` is the selected normalization. `z` standardizes a log ratio using the valid
cells' mean and population standard deviation. Log ratios require positive
values. RRAI-U and HRAI-KU also support the supplied alternative combination
modes, including geometric, minimum and maximum combinations.

**The supplied GUI and Processing tool have different presets.** These are
preserved rather than silently changing the imported scientific settings:

| Setting | Combined-map GUI preset | Processing algorithm default |
| --- | --- | --- |
| RRAI-U triplet | 0.4, 0.4, 0.2 | 0.4, 0.4, 0.2 |
| HRAI-KU triplet | 0.25, 0.60, 0.15 | 0.4, 0.4, 0.2 |
| HRI coefficients | 1, 1, 1; raw scalars | 0.4, 0.4, 0.2; normalized triplet |

For comparable runs, explicitly set the same normalization, coefficients,
combination method, validity mask and thresholds. Save the parameter log. HRI is
a signed standardized score, so its values need not be between zero and one.
These are screening indices; a radiometric anomaly alone does not establish a
hydrocarbon occurrence.

### 3.6 Diagnostics, PCA and validation

Use the supplied ratio crossplots, source/trap diagnostics and histograms to
inspect how the channels contribute to an index. The combined panel also
contains PCA, loading/variance outputs, PC-score maps, quadrant products and
PC1–HRI comparison controls. Interpret the component loadings before assigning
geological meaning to a quadrant.

The synthetic validation tool creates a deterministic 50 × 50-cell dataset and
expected outputs. Run the normal workflow using those rasters and compatible
parameters, then run the processing-folder validator. Read its PASS/FAIL,
missing-file and parameter-discovery results. A different preset or smoothing
choice can legitimately change the comparison; a synthetic check does not
validate the geological interpretation of field data.

## 4. Seismic CDP interval annotation

1. Load a CDP **point** layer with a line-name field and numeric CDP field.
2. Open **Seismic / SEG-Y > CDP interval annotation** and select the layer and fields.
3. Use a projected CRS in metres for kilometre spacing. Set the interval in km.
4. Choose whether to include the first point and the line endpoint.
5. Set the label template, output folder and output basename, then run.

Template tokens are `{Line}`, `{CDP}` and `{Dist_km}`. The tool sorts points by
CDP within each line, constructs a line through those points, and places
annotations at chainage intervals. It labels each interpolated position using
the nearest original CDP by chainage. An annotation coordinate can therefore
lie between original trace coordinates; it is not a newly measured trace point.

Check line grouping, CDP order and large navigation gaps before using the
result. Crossed, repeated or incorrectly ordered navigation can produce an
incorrect reconstructed line.

| Output | Content |
| --- | --- |
| `<base>.shp` and sidecars | Annotation points with Line, CDP, Dist_m, Lbl, X and Y |
| `<base>_Kingdom_CDP_Postings.csv` | Line, CDP, Dist_m, X, Y and Label columns |
| `<base>_Kingdom_CDP_Postings.txt` | Space-delimited postings with quoted label |

Keep Shapefile sidecar files together. Validate the field mapping, delimiter and
CRS in the receiving Kingdom project before bulk import.

## 5. SEG-Y navigation conversion

1. Install `segyio` in QGIS's Python environment if it is not already present.
2. Open **SEG-Y to points/lines**, add the `.sgy`/`.segy` files and check the naming table.
3. Edit each output line name or use **Auto-clean names**. Renaming the output does not rename the source file.
4. Select the navigation pair: `CDP_X/CDP_Y`, `SourceX/SourceY` or `GroupX/GroupY`.
5. Select the CDP identifier or trace-sequence header and the trace sampling step. A step of 1 exports every trace.
6. Review coordinate-scalar handling and the option to skip missing/zero coordinates.
7. Set the CRS that actually describes the header coordinates, the label template, output folder and basename. Run and inspect the log.

Coordinate-scalar handling follows the implemented rule: positive scalar means
multiply, negative scalar means divide by its absolute value, and zero means no
scale change. The CRS field **assigns** the CRS to the coordinates; it does not
transform them into another coordinate system. Do not label degree coordinates
as EPSG:32630 merely to obtain a UTM layer.

Use projected metre coordinates for the supplied length and interval outputs.
Review a few scaled header positions against known navigation control before
processing many lines. The trace-navigation line is a reconstruction from the
selected headers and sampling step, not a recovered acquisition survey.

SEG-Y label tokens are `{Line}`, `{CDP}`, `{TraceNo}` and `{FileName}`. The default
label is `{Line}_CDP_{CDP}`. Exports include point and line Shapefiles,
`<base>_Kingdom_CDP_Points.csv` and `<base>_Kingdom_CDP_Points.txt`. Point fields
include Line, CDP, CDP_Name, TraceNo, FileName, OrigName, X, Y, Scalar and Lbl.

## 6. Troubleshooting

| Symptom | Check |
| --- | --- |
| ZIP refused because of caches | Rebuild from the plugin folder without `__pycache__`, `.pyc`, `.pyo` or nested ZIP files. |
| Radiometric button cannot find an algorithm | Confirm the integrated provider is enabled and restart QGIS after replacing a plugin version. |
| Empty ratio or anomaly outputs | Inspect valid-cell overlap, NoData, positivity, denominator guard and selected thresholds. |
| GUI and Processing combined maps differ | Compare the presets, normalization and coefficient handling in section 3.5. |
| `segyio` not available | Install it in QGIS's own Python environment, then restart QGIS. |
| SEG-Y points plot incorrectly | Inspect the selected X/Y headers, coordinate scalar, CRS and skipped-zero settings. |
| Interval distances are wrong | Use valid, correctly ordered CDPs in a projected metre CRS; inspect line gaps. |

## 7. Sources, attribution and license

Implementation details in this guide are grounded in the supplied radiometric
and seismic source code. The combined integration does not establish external
scientific validation of their methods. See `AUTHORS.md` for component credits.
Geomatic Exploration Suite is distributed under GPL-3.0-or-later; see `LICENSE`.

Official technical references:

- QGIS Python plugins: https://docs.qgis.org/latest/en/docs/pyqgis_developer_cookbook/plugins/plugins.html
- SEG-Y I/O library: https://github.com/equinor/segyio
- QGIS plugin publication: https://plugins.qgis.org/docs/publish
