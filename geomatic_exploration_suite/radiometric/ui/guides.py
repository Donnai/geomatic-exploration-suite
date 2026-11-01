# -*- coding: utf-8 -*-
"""Guide and theory text used by the dock UI.

The *_GUIDE_SECTIONS objects are used by the dock guide dialog to build
collapsible/expandable guide sections. The *_GUIDE strings are retained as
plain-text fallbacks for older plugin code paths.
"""

MAIN_GUIDE_SECTIONS = [
    ("Objective", """
The Radiometric Analysis Suite is a project-based workflow for conditioning radiometric rasters, generating ratio maps, analysing thresholds, extracting anomaly polygons, and producing complementary combined radiometric indices: RRAI-U, HRAI-KU and HRI.

The design principle is:

    Input rasters → conditioned rasters → ratio maps → normalised ratios + ZLog ratios → RRAI/HRAI/HRI combined maps → threshold maps → anomaly polygons

The project processing folder is the source of truth. The plugin writes numbered subfolders and reloads the expected outputs from those folders so the workflow remains repeatable.
"""),
    ("Workflow diagram", """
Simple workflow:

    [Data QC]
        ↓
    [Stage 1: Ratio Map Generation]
        ├─ Pre-processing / Data Conditioning
        └─ Ratio Map Generation
        ↓
    [Stage 2: Ratio Map Analysis and Anomaly Extraction]
        ├─ Threshold Analysis
        └─ Anomaly Extraction
        ↓
    [Stage 3: Combined Analysis and Anomaly Extraction]
        ├─ Normalisation
        ├─ Crossplot Analysis
        ├─ Combinations
        ├─ Threshold Analysis
        └─ Anomaly Extraction

Recommended use:
1. QC the original U/K/Th input rasters.
2. Pre-process and filter the input rasters.
3. Generate ratio maps.
4. Review ratio histograms and threshold maps.
5. Extract polygons from ratio threshold maps.
6. Generate RRAI-U, HRAI-KU and/or HRI combined rasters.
7. Review crossplots, threshold combined maps and extract combined anomalies.
"""),
    ("Project-folder principle", """
The plugin expects a project processing folder with standard subfolders. Stages auto-load from these folders where possible.

Typical folders:

    00_Data_QC
    01_Input_Data
    02_Preprocessed
    03_Ratio_Maps
    04_Normalised_Ratio_Maps
    05_Ratio_Threshold_Class_Maps
    06_Ratio_Map_Anomaly_Extraction
    07_Combined_Maps
    08_Combined_Threshold_Maps
    09_Combined_Map_Anomaly_Extraction
    10_Project_Settings

Using consistent folders prevents the wrong rasters from being selected accidentally and makes the workflow auditable.
"""),
]

DATA_QC_GUIDE_SECTIONS = [
    ("Objective", """
Data Analysis and QC now has three separate subtabs:

1. QC and Statistics
   - Checks selected rasters and writes a text-based QC/statistics report.
   - It does not generate or overwrite histograms.

2. Histograms
   - Lets the user select a single raster and generate a histogram on demand.
   - Histogram bins are controlled in this tab.

3. Crossplots
   - Embeds the crossplot analysis tools that were previously located under Stage 3.

This separation keeps QC reporting, histogram inspection and crossplot interpretation as related but independent analysis workflows.
"""),
    ("QC and Statistics", """
The QC and Statistics subtab checks whether radiometric rasters are suitable for processing and interpretation. It can be used on raw inputs, pre-processed rasters, ratio maps, normalised maps and combined/RRAI maps.

The Data to QC section chooses the product group. The nested Input Data to QC - Radiometric Rasters subsection lists the detected rasters. Use the checkbox beside each raster to include or exclude it from the QC run.

The QC output reports:

- raster size and pixel count
- band count
- cell size
- valid and NoData/null cell counts
- inferred unit/type
- minimum, maximum, mean, median, standard deviation and percentiles
- warning flags for possible data issues

Running Data QC only updates the QC Summary / Flags text box. It does not update the Histograms subtab.
"""),
    ("Histograms", """
The Histograms subtab is a separate raster-inspection tool.

Workflow:

1. Choose a raster from the active processing folder using the Raster dropdown.
2. Set Histogram bins.
3. Click Generate Histogram.

The dropdown is limited to GeoTIFF rasters inside the active radiometric processing folder; it does not list every raster loaded in QGIS. The histogram clips the display range to approximately the 1st-99th percentile so isolated spikes do not dominate the visual plot.

Use histograms to inspect:

- skewed distributions
- long tails
- spikes/outliers
- clipping or quantisation
- gaps in values
- whether percentile thresholds are sensible
"""),
    ("Crossplots", """
The Crossplots subtab is for comparing relationships between rasters/attributes.

Use crossplots to inspect whether two radiometric products are correlated, separated into domains, or showing anomalous populations. This is especially useful for ratio maps, normalised ratio maps and combined products because crossplots can reveal relationships that are not obvious in a single map or histogram.
"""),
    ("Expected radiometric ranges", """
Expected ranges vary by survey, geology and processing method, but useful rules of thumb are:

Potassium (K)
- Often reported as %K.
- Values above ~10% are high.
- Values above ~20% should be checked for scaling or artefacts.

Uranium (U)
- Often reported as ppm/eU.
- Values above ~50 ppm are high.
- Values above ~100 ppm may be real but should be checked for spikes/scaling.

Thorium (Th)
- Often reported as ppm/eTh.
- Values above ~150 ppm are high.
- Values above ~300 ppm should be checked.

Negative values are usually not physically meaningful for concentration grids and should normally be treated as invalid/NoData before filtering and ratio calculation.
"""),
]


PRE_GUIDE_SECTIONS = [
    ("Objective", """
Pre-processing / Data Conditioning prepares the original U, K and Th rasters so ratio calculations are stable and geologically meaningful.

The key objectives are:

- align U, K and Th to a common grid
- handle NoData/null values correctly
- remove invalid radiometric values
- optionally clip or smooth noise/spikes
- produce filtered rasters for ratio generation

This is a conditioning step, not the final interpretation step.
"""),
    ("Input rasters", """
The tab auto-fills U, K and Th from the project input folder when possible:

    01_Input_Data

Users can still override by selecting loaded QGIS raster layers.

The Uranium raster is normally used as the reference grid. Potassium and Thorium are aligned/warped to match the reference grid so all rasters share:

- extent
- resolution
- row/column count
- geotransform
- projection

This prevents pixel offsets during ratio calculation.
"""),
    ("Mask polygon", """
The mask polygon is optional. If supplied, the rasters are clipped/masked to the study area.

If no mask is supplied, the workflow defaults to the reference raster extent.

Use a mask when:
- the survey includes unwanted edge areas
- you only want to process a basin/block/permit area
- null/noisy edges should be excluded

Avoid over-tight masks if anomalies may continue outside the current interpretation polygon.
"""),
    ("NoData / NULL handling", """
NoData handling is critical because ratio calculations amplify bad values.

The plugin handles:

- raster NoData values from the raster band
- user-specified additional NoData, commonly -9999
- NaN/Inf values
- optional invalid radiometric values <= 0

Recommended option:

    Treat radiometric values <= 0 as invalid / NoData

Why:
Radiometric concentrations should not be negative. Zero or negative K/Th denominators can create unstable, huge or inverted ratios. Invalid values are removed before filtering so filters do not smear them into neighbouring valid cells.
"""),
    ("Filtering options", """
Filters reduce local noise and stabilise downstream ratios.

Mean filter
- Math: replaces each cell by the average of cells inside a moving window.
- Strength: good at reducing random noise.
- Weakness: blurs edges and can smear anomalies.
- Use when: data is noisy but anomaly continuity matters more than sharp edges.

Median filter
- Math: replaces each cell by the median of the moving window.
- Strength: robust against spikes and salt-and-pepper noise.
- Weakness: can flatten subtle gradients.
- Use when: the data contains isolated high/low spikes.

Gaussian filter
- Math: weighted average where nearby cells have stronger influence than distant cells.
- Strength: smoother and more natural than a simple mean.
- Weakness: can still blur narrow anomalies if window/sigma is too large.
- Use when: you want scale-dependent smoothing without a hard window-edge effect.
"""),
    ("Window size", """
Window size controls the spatial scale of smoothing.

Examples:

    3 x 3   → light smoothing, preserves detail
    5 x 5   → moderate smoothing
    7 x 7+  → stronger smoothing, more regionalised anomalies
    15 x 15 → broad regional smoothing; may suppress small targets

Larger windows improve continuity but reduce spatial resolution. For exploration screening, compare at least one conservative filter (median 3x3 or 5x5) with one smoother regional option.
"""),
    ("Output naming and theory diagram", """
The output name is based on the input raster name and filter setting, for example:

    Uranium_MedianF_5x5.tif
    Potassium_GaussianF_7x7.tif

Conceptual diagram:

    Raw U/K/Th rasters
        ↓ clean NoData / <=0
    Valid concentration fields
        ↓ align to reference grid
    Common U/K/Th grid
        ↓ filter / smooth
    Filtered U/K/Th rasters
        ↓
    Ratio Map Generation

The purpose is to reduce invalid numerical behaviour before creating ratios.
"""),
]

RATIO_GUIDE_SECTIONS = [
    ("Objective", """
Ratio Map Generation creates exploration ratio maps from filtered U, K and Th rasters.

The objective is to convert absolute radiometric concentration maps into relative geochemical indicators that are more sensitive to uranium enrichment, potassium depletion, alteration patterns and source-side potassium behaviour.
"""),
    ("Auto-loaded filtered rasters", """
The ratio tab auto-loads the latest filtered U/K/Th rasters from the processing folder.

If multiple filtered versions exist, for example mean, median or Gaussian outputs, the most recently modified matching raster is selected.

Users can still override by selecting loaded QGIS raster layers.
"""),
    ("Ratio map theory", """
Absolute radiometric values can be strongly controlled by lithology, soil cover, weathering, acquisition effects and background concentration.

Ratios help suppress some background effects and highlight relative enrichment or depletion.

Target logic:

    U/Th → HIGH values are favourable
    U/K  → HIGH values are favourable
    K/U  → HIGH values can support HRAI-KU / potassium-context screening
    K/Th → LOW values are favourable

Important: the target is not low uranium. The target is uranium enrichment relative to thorium/potassium and potassium depletion relative to thorium.
"""),
    ("What each ratio shows", """
U/Th
- High U/Th suggests uranium enrichment relative to thorium.
- Thorium is commonly treated as more immobile than uranium.
- Useful for highlighting uranium mobility/redox effects.

U/K
- High U/K suggests uranium enrichment relative to potassium.
- Used by the RRAI-U index as the second uranium-enrichment indicator.

K/U
- High K/U means potassium is high relative to uranium.
- This is not the same anomaly logic as U/K.
- It is retained for the HRAI-KU branch, where it can help examine potassium/source-side or alteration-context behaviour.

K/Th
- Low K/Th suggests potassium depletion relative to thorium.
- In the combined indices, K/Th is directionally inverted after normalisation using 1 - norm(K/Th), not 1/(K/Th).

Together these ratios support individual ratio interpretation and combined RRAI-U, HRAI-KU and HRI screening.
"""),
    ("Ratio equations", """
Equations:

    U_Th_ratio = U / Th
    U_K_ratio  = U / K
    K_U_ratio  = K / U
    K_Th_ratio = K / Th

Validity condition:

    denominator must be positive and above the selected near-zero guard.

If the denominator is zero, negative or too close to zero, the ratio cell is set to NoData.
"""),
    ("Ratio Calculation - Denominator Guard", """
Ratios can produce huge values when the denominator is very small.

Example:

    U = 4
    Th = 0.001
    U/Th = 4000

That value may be mathematically valid but geologically meaningless if Th is near the noise floor.

Guard modes:

Absolute value
- Uses a fixed denominator floor.
- Good when you know the minimum meaningful K, U or Th value.

Percentile of positive denominator values
- Calculates a small positive floor from the denominator raster distribution.
- Useful when different surveys have different scaling/ranges.
- Applied separately to denominators used in U/Th, U/K, K/U and K/Th.
"""),
    ("Extreme Value Clipping to Output Ratio Maps", """
Optional clipping in ratio generation is winsorisation:

    values below low percentile  → set to low percentile value
    values above high percentile → set to high percentile value

This does not remove cells. It compresses extreme values.

Strength:
- improves display and reduces spike dominance

Weakness:
- may suppress real extreme anomalies

For interpretation decisions, Stage 2 clipping/histogram review is usually preferred because it shows raw vs analysis distributions and spatial previews.
"""),
    ("Display loading", """
Quantile display styling affects only how rasters are shown in QGIS.

A blue-white-red pseudocolour ramp is used for visual review:

    low values  → blue
    mid values  → white
    high values → red

This display style does not change raster values.
"""),
]

STAGE2_GUIDE_SECTIONS = [
    ("Objective", """
Stage 2 analyses the individual ratio maps and extracts anomaly polygons from thresholded ratio maps.

The objective is to identify geochemically meaningful ratio anomalies for exploration screening, while allowing the interpreter to inspect how statistical thresholds affect both the histogram and the spatial footprint.
"""),
    ("Conceptual background", """
Stage 1 generated ratio maps:

    U/Th, U/K, K/U, K/Th

Stage 2 asks:

    Which parts of each ratio distribution are anomalous?
    Where are those anomalous cells located spatially?
    Do they form coherent geological trends or only isolated noise?

This is an interpretation loop, not just a batch extraction step.
"""),
    ("Ratio interpretation", """
U/Th
- High values suggest uranium enrichment relative to thorium.
- High/Very High thresholds are used.

U/K
- High values suggest uranium enrichment relative to potassium.
- High/Very High thresholds are used.

K/U
- High values show potassium dominance relative to uranium.
- High/Very High thresholds can be useful for HRAI-KU support, but interpretation is different from U/K.

K/Th
- Low values suggest potassium depletion relative to thorium.
- Low/Very Low thresholds are used.

Interpretation logic:

    U/Th: keep upper tail
    U/K : keep upper tail
    K/U : keep upper tail when used for HRAI-KU support
    K/Th: keep lower tail
"""),
    ("Threshold Analysis workflow diagram", """
Stage 2 threshold workflow:

    Ratio maps
        ↓
    Histogram review
        ↓
    Optional histogram compression / winsorisation
        ↓
    Select High/Low and Very High/Very Low thresholds
        ↓
    Preview accepted/removed cells on map
        ↓
    Generate threshold/class maps
        ↓
    Extract anomaly polygons
"""),
    ("Raw vs Analysis histograms", """
Raw histogram
- Displays original ratio values.
- Shows the true distribution including extreme tails.

Analysis histogram
- Displays the working distribution after optional compression/clipping.
- Used to make threshold decisions.

The plugin uses common bins when comparing raw and analysis distributions so the visual comparison is meaningful.
"""),
    ("Histogram compression / winsorisation", """
Winsorisation caps extreme values:

    x < P_low  → P_low
    x > P_high → P_high

It does not delete cells. It compresses extreme tails so a few values do not dominate the histogram axis.

Strengths:
- improves histogram readability
- reduces the influence of spikes
- makes thresholds easier to select

Weaknesses:
- can hide true extreme anomalies if used too aggressively
- changes the analysis distribution shape

Use the clipped percentage summary to understand how much of the data is affected.
"""),
    ("Threshold calculation methods", """
Percentile method
- Math: threshold = value at selected percentile.
- Strength: robust for skewed radiometric distributions.
- Weakness: always selects a fixed proportion of data even if no real anomaly exists.
- Best use: first-pass regional screening.

Mean ± standard deviation
- Math: threshold = mean ± n × standard deviation.
- Strength: simple and interpretable for near-normal data.
- Weakness: radiometric ratio distributions are often skewed, so thresholds can be misleading.
- Negative thresholds are clamped to zero.

Manual thresholds
- User directly edits threshold values or moves sliders.
- Strength: best for expert interpretation.
- Weakness: more subjective and should be documented.
"""),
    ("Threshold levels", """
High / Low threshold
- Broad anomaly envelope.
- Useful for seeing the wider altered or enriched zone.

Very High / Very Low threshold
- Core anomaly zone.
- Useful for ranking and focusing leads.

For U/Th, U/K and K/U:

    High pass = values >= High threshold
    Very High pass = values >= Very High threshold

For K/Th:

    Low pass = values <= Low threshold
    Very Low pass = values <= Very Low threshold
"""),
    ("Histogram overlays", """
Available overlays:

Mean
- Arithmetic centre of the distribution.
- Sensitive to outliers.

Median
- Robust centre.
- Less sensitive to extreme values.

Standard deviation
- Spread around the mean.
- Useful if distribution is approximately normal.

Percentile lines
- Show distribution structure.
- Very useful for skewed data.

Removed / High-Low / Very High-Low overlays
- Show which portion of the histogram is accepted or rejected by the current thresholds.
"""),
    ("Preview options", """
Preview masks show threshold decisions spatially before extracting polygons.

Options usually include:
- show removed cells
- show kept cells
- white or black mask colour
- opacity
- ratio selector: U/Th, U/K, K/U, K/Th or all

This is the key geological QA step. A threshold may look reasonable in a histogram but produce spatially meaningless speckle, or it may reveal coherent structural trends.
"""),
    ("Anomaly Extraction workflow diagram", """
Anomaly extraction workflow:

    Threshold/class maps
        ↓
    Select extraction level:
        - High/Low
        - Very High/Very Low
        - Both
        ↓
    Select polygon extraction method
        ↓
    Optional raster cleanup: minimum connected pixels
        ↓
    Polygonise
        ↓
    Optional vector cleanup: minimum polygon area
        ↓
    Optional vector smoothing
        ↓
    Final anomaly polygons
"""),
    ("Extraction level", """
Extraction level controls which threshold class is converted to polygons.

High/Low
- Broader anomaly envelope.
- Better for seeing trends and alteration halos.

Very High/Very Low
- Smaller, stronger anomaly core.
- Better for ranking and follow-up.

Both
- Extracts both classes where useful for combined interpretation.
"""),
    ("Extraction method", """
Fast Mask Polygonise (recommended)
- Converts selected anomaly cells to a binary mask.
- Background becomes NoData.
- Polygonises only anomaly cells.
- Much faster for large rasters.
- Best default method.

GDAL Polygonize Full Raster (fallback)
- Polygonises the full threshold/class raster.
- More complete for debugging or edge cases.
- Slower because it processes background and all classes.
"""),
    ("Minimum connected pixels, polygon area and smoothing", """
Minimum connected pixels
- Raster-stage cleanup before polygonising.
- Removes small isolated pixel clusters.
- Faster because it reduces noise before vector creation.

Minimum polygon area
- Vector-stage cleanup after polygonising.
- Removes small polygons based on map units / area.
- Useful for final cartographic/geological cleanup.

Polygon smoothing
- Optional vector geometry smoothing after polygon creation.
- Useful for reducing pixelated raster edges in presentation layers.
- Should not be over-used if exact cell boundaries are important.
"""),
]

STAGE3_GUIDE_SECTIONS = [
    ("Objective", """
Stage 3 combines normalised ratio indicators into one or more combined anomaly maps, then thresholds/extracts polygons from those combined maps.

The current workflow supports three complementary combined indices:

    RRAI-U  → uranium-enrichment oriented index
    HRAI-KU → hybrid potassium/uranium alteration-context index
    HRI     → log-standardised hydrocarbon radiometric index

The objective is to move from individual ratio responses to higher-confidence integrated anomaly layers and to compare different geological assumptions.
"""),
    ("Conceptual background", """
Stage 2 asks: where is each ratio anomalous?

Stage 3 asks: where do selected ratio indicators combine into a strong integrated response?

This helps reduce ambiguity. A single ratio can be noisy or lithology-driven. A combined index can highlight areas where enrichment/depletion indicators are spatially consistent.
"""),
    ("Combined analysis diagram", """
RRAI-U workflow:

    U/Th ratio        → norm(U/Th) ┐
    U/K ratio         → norm(U/K)  ├→ combine / weight → RRAI-U
    K/Th ratio        → 1 - norm(K/Th) ┘

HRAI-KU workflow:

    U/Th ratio        → norm(U/Th) ┐
    K/U ratio         → norm(K/U)  ├→ combine / weight → HRAI-KU
    K/Th ratio        → 1 - norm(K/Th) ┘

HRI workflow:

    U/Th ratio        → z(ln(U/Th)) ┐
    U/K ratio         → z(ln(U/K))  ├→ HRI = w1·z(ln(U/Th)) + w2·z(ln(U/K)) - w3·z(ln(Th/K))
    Th/K ratio        → z(ln(Th/K)) ┘

Then:

    combined map → threshold combined map → extract combined anomaly polygons
"""),
    ("Why normalisation is required", """
The ratio maps have different numerical ranges and distributions.

For example:
- U/Th may have a broad long-tailed range.
- U/K may be sensitive to small K values.
- K/U has different meaning and scale from U/K.
- K/Th has inverse favourability logic where low values are favourable.
- HRI uses log-standardised terms, so positive valid ratios are required before ln() is applied. The ZLog component rasters are generated in the Normalisation tab and saved to 04_ZLog_Ratio_Maps for inspection.

Normalisation converts each component to a comparable scale before combination. Without normalisation, the ratio with the largest numerical range can dominate the combined map.
"""),
    ("Normalisation methods", """
Min-Max
- Math: (x - min) / (max - min)
- Strength: simple 0-1 scaling.
- Weakness: very sensitive to outliers.

Min-Max extremes clipped
- Clips extreme tails first, then rescales.
- Strength: more robust for noisy geophysical data.
- Weakness: clipping values must be chosen carefully.

Percentile / quantile rank
- Converts values to their relative distribution rank.
- Strength: very good for skewed distributions.
- Weakness: changes the meaning from absolute value to rank.

Z-score
- Math: (x - mean) / standard deviation
- Strength: useful for approximately normal distributions.
- Weakness: sensitive to outliers and skew.

Robust Z-score
- Math: based on median and MAD.
- Strength: better for skewed/noisy data.
- Weakness: less intuitive than min-max scaling.
"""),
    ("K/Th directional inversion: 1 - norm(K/Th)", """
For U/Th and U/K, high values are favourable.

For K/Th, low values are favourable.

Before combination, K/Th is directionally inverted so all components point in the same anomaly-positive direction:

    KTh_component = 1 - norm(K/Th)

This is NOT the same as:

    1 / (K/Th)

The plugin uses 1 - norm(K/Th), not a reciprocal transform. This reverses anomaly polarity without creating unstable huge values near zero.
"""),
    ("RRAI-U", """
RRAI-U uses:

    norm(U/Th)
    norm(U/K)
    1 - norm(K/Th)

Conceptual focus:
- uranium enrichment relative to thorium
- uranium enrichment relative to potassium
- potassium depletion / alteration support

Example weighted form:

    RRAI-U = 0.4 × norm(U/Th) + 0.4 × norm(U/K) + 0.2 × [1 - norm(K/Th)]
"""),
    ("HRAI-KU", """
HRAI-KU uses:

    norm(U/Th)
    norm(K/U)
    1 - norm(K/Th)

Conceptual focus:
- uranium enrichment relative to thorium
- potassium/uranium relationship as an alteration or source-context indicator
- potassium depletion relative to thorium

Example weighted form:

    HRAI-KU = 0.4 × norm(U/Th) + 0.4 × norm(K/U) + 0.2 × [1 - norm(K/Th)]
"""),
    ("HRI - Hydrocarbon Radiometric Index", """
HRI uses a formula-based log-standardised composite:

    HRI = w1·z(ln(U/Th)) + w2·z(ln(U/K)) - w3·z(ln(Th/K))

Conceptual focus:
- high U/Th supports uranium enrichment relative to immobile thorium
- high U/K supports uranium enrichment relative to potassium
- high Th/K is subtracted as a penalty, helping reduce false positives driven mainly by extreme potassium depletion or weathering

HRI differs from RRAI-U because it uses logarithms and z-scores rather than the selected min-max/percentile normalised rasters. It still has w1, w2 and w3 weights, but the components are ZLog rasters rather than 0-1 normalised ratio rasters.

Important validity rule:
All log inputs must be positive. Cells with invalid or non-positive ratios are treated as NoData before HRI is calculated.
"""),
    ("Weighting presets", """
Weights express how much confidence or importance is assigned to each component.

Balanced / default example:

    first ratio = 0.4
    second ratio = 0.4
    1 - norm(K/Th) = 0.2

Interpretation:
- RRAI-U second-ratio = U/K.
- HRAI-KU second-ratio = K/U.
- 1 - norm(K/Th) contributes alteration/potassium depletion context.

Equal weights are useful when you do not want to preference any ratio.
Custom weights are useful when local geology suggests one ratio is more reliable.
"""),
    ("Combination methods", """
Weighted mean
- Default and most interpretable.
- Smoothly integrates multiple indicators.

Arithmetic mean
- Equal-weight average.
- Good neutral baseline.

Geometric mean
- Penalises weak components.
- Highlights areas where all indicators are jointly favourable.

Minimum consensus
- Conservative method.
- Combined score is controlled by the weakest component.
- Useful for high-confidence targets.

Maximum response
- Permissive method.
- Highlights areas where any one component is strong.
- Useful for reconnaissance but may increase false positives.
"""),
    ("Threshold analysis on the combined map", """
Stage 3 thresholding is similar to Stage 2, but it analyses one combined map at a time rather than individual ratio maps.

Important:
Do not directly reuse Stage 2 ratio thresholds. RRAI-U, HRAI-KU and HRI have their own distributions and require their own thresholds.

Typical defaults:
- High = P85
- Very High = P95

Thresholds must be non-negative. Calculated negative thresholds are clamped to zero.
"""),
    ("Stage 3 histogram overlays", """
The combined-map histogram supports the same display logic as Stage 2:

- Raw display
- Analysis display
- Mean
- Median
- Standard deviation
- Percentiles
- Removed overlay
- High pass overlay
- Very High pass overlay

High pass is shown in light green.
Very High pass is shown in green.

The overlay helps connect the statistical decision to the amount of the combined distribution being kept or removed.
"""),
    ("Preview and extraction", """
Preview creates a temporary spatial mask from the current combined-map threshold settings.

Use preview before extracting polygons to check:
- whether the threshold produces coherent anomaly footprints
- whether anomalies align with geology/structure
- whether the result is too speckled or too broad

Extraction then converts selected threshold classes to polygons.
The same Fast Mask Polygonise and Full Raster Polygonise logic used in Stage 2 can be used here.
"""),
    ("Interpretation note", """
Stage 2 is more sensitive because it preserves individual ratio behaviour.

Stage 3 is more selective because it integrates multiple indicators. HRI adds a log-standardised alternative that emphasises uranium enrichment while penalising high Th/K behaviour.

Practical interpretation:

    Stage 2 = find candidate anomaly patterns
    Stage 3 = identify higher-confidence combined anomalies

Strong targets are often those where Stage 2 ratio anomalies and Stage 3 combined anomalies spatially agree.
"""),
]

# Plain-text fallback strings retained for existing code paths.
def _sections_to_text(sections):
    parts = []
    for title, body in sections:
        parts.append(title.upper())
        parts.append(body.strip())
        parts.append('')
    return '\n'.join(parts).strip()

MAIN_GUIDE = _sections_to_text(MAIN_GUIDE_SECTIONS)
DATA_QC_GUIDE = _sections_to_text(DATA_QC_GUIDE_SECTIONS)
PRE_GUIDE = _sections_to_text(PRE_GUIDE_SECTIONS)
RATIO_GUIDE = _sections_to_text(RATIO_GUIDE_SECTIONS)
STAGE2_GUIDE = _sections_to_text(STAGE2_GUIDE_SECTIONS)
STAGE3_GUIDE = _sections_to_text(STAGE3_GUIDE_SECTIONS)
