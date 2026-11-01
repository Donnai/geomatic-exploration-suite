"""Register the integrated suite's radiometric Processing algorithms."""
from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
from pathlib import Path
from .radiometric.algs.registry import instantiate_algorithms


class ExplorationProvider(QgsProcessingProvider):
    def id(self):
        return "geomatic_exploration_suite"

    def name(self):
        return "Geomatic Exploration Suite"

    def longName(self):
        return self.name()

    def icon(self):
        return QIcon(str(Path(__file__).parent / "radiometric/ui/icons/radiometric.svg"))

    def loadAlgorithms(self):
        for algorithm in instantiate_algorithms():
            self.addAlgorithm(algorithm)
