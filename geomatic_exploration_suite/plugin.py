"""One QGIS plugin, menu and dock for the two original toolsets."""
from pathlib import Path
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QTabWidget, QScrollArea, QWidget,
    QVBoxLayout, QLabel, QTextBrowser, QDialog,
)
from .provider import ExplorationProvider

HERE = Path(__file__).parent
MENU = "&Geomatic Exploration Suite"


class GeomaticExplorationSuite:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.dock = None
        self.tabs = None
        self.radiometric = None
        self.seismic = None
        self.guide_dialog = None

    def initProcessing(self):
        if self.provider is None:
            provider = ExplorationProvider()
            if QgsApplication.processingRegistry().addProvider(provider):
                self.provider = provider
            else:
                raise RuntimeError("Geomatic Exploration Suite Processing provider could not be registered.")

    def initGui(self):
        self.initProcessing()
        specs = [
            ("Radiometric analysis", "radiometric/ui/icons/radiometric.svg", lambda: self.open_dock(0)),
            ("Seismic CDP and SEG-Y tools", "seismic/icon.png", lambda: self.open_dock(1)),
            ("User guide and methods", "radiometric/ui/icons/guide.svg", self.open_guide),
        ]
        for title, icon, callback in specs:
            action = QAction(QIcon(str(HERE / icon)), title, self.iface.mainWindow())
            action.setToolTip(title + " — Geomatic Exploration Suite")
            action.triggered.connect(callback)
            self.iface.addPluginToMenu(MENU, action)
            self.iface.addToolBarIcon(action)
            self.actions.append(action)

    def _scroll(self, widget):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    def open_dock(self, tab=0):
        if self.dock is None:
            from .radiometric.dialog import RadiometricAnalysisPanel
            from .seismic.dialog import CDPIntervalDialog

            self.dock = QDockWidget("Geomatic Exploration Suite", self.iface.mainWindow())
            self.dock.setObjectName("GeomaticExplorationSuiteDock")
            self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(4, 4, 4, 4)
            author = QLabel("<b>Geomatic Exploration Suite</b> · Donald Annor Appiah")
            author.setWordWrap(True)
            layout.addWidget(author)
            self.tabs = QTabWidget()
            self.radiometric = RadiometricAnalysisPanel(self.iface)
            suite_dock = self.dock

            class SeismicPanel(CDPIntervalDialog):
                def close(self):
                    suite_dock.hide()
                    return True

            self.seismic = SeismicPanel(self.iface)
            self.seismic.setWindowFlags(Qt.Widget)
            self.seismic.setMinimumSize(0, 0)
            self.tabs.addTab(self._scroll(self.radiometric), "Radiometric analysis")
            self.tabs.addTab(self._scroll(self.seismic), "Seismic / SEG-Y")
            self.help_browser = QTextBrowser()
            self.help_browser.setOpenExternalLinks(True)
            self.help_browser.setSource(QUrl.fromLocalFile(str(HERE / "help/index.html")))
            self.tabs.addTab(self.help_browser, "Guide")
            layout.addWidget(self.tabs)
            self.dock.setWidget(container)
            self.dock.setMinimumWidth(560)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        if tab == 1:
            self.seismic.refresh_layers()
            self.seismic.show()
        self.tabs.setCurrentIndex(tab)
        self.dock.show()
        self.dock.raise_()

    def open_guide(self):
        self.open_dock(2)

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.close()
            self.dock.deleteLater()
            self.dock = None
            self.radiometric = None
            self.seismic = None
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
            self.iface.removeToolBarIcon(action)
            action.deleteLater()
        self.actions = []
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
