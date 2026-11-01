"""Geomatic Exploration Suite: radiometric and seismic tools for QGIS."""


def classFactory(iface):
    from .plugin import GeomaticExplorationSuite
    return GeomaticExplorationSuite(iface)
