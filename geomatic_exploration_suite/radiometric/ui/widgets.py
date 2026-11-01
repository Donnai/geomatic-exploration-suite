# -*- coding: utf-8 -*-
"""Small reusable Qt widgets for the plugin UI."""

from qgis.PyQt.QtWidgets import QGroupBox

class CollapsibleBox(QGroupBox):
    """Small checkable group box used as a collapsible/expandable section."""
    def __init__(self, title='', checked=True):
        super().__init__(title)
        self.setCheckable(True)
        self.setChecked(checked)
        self.toggled.connect(self._on_toggled)

    def setContentLayout(self, layout):
        self.setLayout(layout)
        self._on_toggled(self.isChecked())

    def _on_toggled(self, checked):
        lay = self.layout()
        if not lay:
            return
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item.widget():
                item.widget().setVisible(checked)
            elif item.layout():
                self._set_layout_visible(item.layout(), checked)

    def _set_layout_visible(self, layout, visible):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget():
                item.widget().setVisible(visible)
            elif item.layout():
                self._set_layout_visible(item.layout(), visible)

