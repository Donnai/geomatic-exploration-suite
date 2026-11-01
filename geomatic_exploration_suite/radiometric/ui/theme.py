# -*- coding: utf-8 -*-
"""Shared light/dark red UI theme for the Radiometric Analysis Suite.

v3.55.4 UI refinement:
- labels/plain text are unboxed
- only editable inputs, selectors, buttons, tables and section containers are framed
- panels are only very slightly transparent; outer edges remain softer
- buttons use a Qt-like raised/bezel treatment
"""
import logging

from qgis.PyQt.QtGui import QFont

LIGHT = "light"
DARK = "dark"


def _common_qss():
    return r"""
    * {
        font-family: "Segoe UI", "Inter", "Noto Sans", Arial, sans-serif;
        font-size: 10pt;
    }

    QLabel {
        background-color: transparent;
        background: transparent;
        border: none;
        padding: 0px;
    }

    QLabel#SectionHeading, QLabel#RadiometricTitle, QLabel#ThemeLabel {
        background-color: transparent;
        border: none;
    }

    QWidget > QLabel, QGroupBox QLabel, QFrame QLabel {
        background-color: transparent;
        border: none;
    }

    QToolTip {
        padding: 6px 8px;
        border-radius: 4px;
    }

    QFrame {
        background: transparent;
        border: none;
    }

    QScrollArea {
        border: none;
        background: transparent;
    }

    QTabWidget::pane {
        border-radius: 7px;
        top: -1px;
    }

    QTabBar::tab {
        padding: 9px 18px 9px 14px;
        margin: 2px 3px 0px 0px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        min-width: 138px;
        min-height: 32px;
    }

    QTabBar::tab:selected {
        font-weight: 600;
        padding-top: 9px;
        padding-bottom: 9px;
    }

    QTabBar::tab:!selected {
        margin-top: 4px;
    }

    QGroupBox {
        margin-top: 16px;
        padding: 13px 9px 9px 9px;
        font-weight: 600;
        border-radius: 7px;
    }

    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0px 5px;
        background: transparent;
        border: none;
    }

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
        min-height: 24px;
        padding: 3px 6px;
        border-radius: 4px;
        font-weight: 400;
    }

    QComboBox {
        padding-right: 22px;
    }


    QSpinBox::up-button, QDoubleSpinBox::up-button {
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 18px;
        border-left: 1px solid rgba(120, 120, 120, 115);
        border-bottom: 1px solid rgba(120, 120, 120, 80);
        background-color: rgba(180, 180, 180, 180);
    }

    QSpinBox::down-button, QDoubleSpinBox::down-button {
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 18px;
        border-left: 1px solid rgba(120, 120, 120, 115);
        background-color: rgba(165, 165, 165, 180);
    }

    /* Spinbox arrows are left to the native Qt style so the up/down triangles remain visible. */

    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
        background-color: rgba(205, 205, 205, 220);
    }

    QPushButton, QToolButton {
        min-height: 24px;
        padding: 4px 10px;
        border-radius: 5px;
        font-weight: 500;
    }

    QPushButton:pressed, QToolButton:pressed {
        padding-top: 5px;
        padding-left: 11px;
    }

    QPushButton:disabled, QToolButton:disabled, QLineEdit:disabled, QComboBox:disabled,
    QSpinBox:disabled, QDoubleSpinBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
        opacity: 0.65;
    }

    QCheckBox {
        background: transparent;
        border: none;
    }

    QCheckBox::indicator {
        width: 14px;
        height: 14px;
    }

    QProgressBar {
        border-radius: 5px;
        text-align: center;
        min-height: 18px;
    }

    QProgressBar::chunk {
        border-radius: 4px;
        background-color: #D84A43;
    }

    QTableView, QTableWidget, QTreeView {
        alternate-background-color: rgba(220, 55, 48, 18);
        selection-background-color: rgba(210, 55, 48, 115);
        selection-color: white;
    }

    QHeaderView::section {
        padding: 5px 6px;
        font-weight: 600;
    }

    QScrollBar:vertical, QScrollBar:horizontal {
        border: none;
        margin: 0px;
    }

    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        border-radius: 4px;
        min-height: 28px;
        min-width: 28px;
    }

    QLabel#RadiometricTitle {
        font-size: 12pt;
        font-weight: 700;
    }

    QFrame#RadiometricHeader, QWidget#RadiometricHeader {
        border-radius: 9px;
    }

    QLabel#ThemeLabel {
        font-size: 9pt;
        font-weight: 600;
    }

    QToolButton#ThemeButton {
        min-width: 74px;
        font-weight: 600;
    }

    QDockWidget#RadiometricAnalysisSuiteDock::title {
        padding: 4px 8px;
        font-weight: 600;
    }

    /* Plain text should sit directly on the panel background. */
    QLabel, QLabel:disabled {
        background-color: transparent;
        border: none;
    }
    """


def light_qss():
    return _common_qss() + r"""
    QWidget {
        color: #202427;
        background-color: transparent;
    }

    QDialog, QMenu, QAbstractScrollArea, QScrollArea QWidget#qt_scrollarea_viewport {
        background-color: rgba(250, 250, 250, 252);
    }

    QWidget#RadiometricAnalysisPanel {
        background-color: rgba(248, 248, 248, 252);
    }

    QFrame#RadiometricHeader, QWidget#RadiometricHeader {
        background-color: rgba(255, 255, 255, 248);
        border: 1px solid rgba(150, 155, 160, 85);
    }

    QDockWidget#RadiometricAnalysisSuiteDock {
        background-color: rgba(248, 248, 248, 252);
    }

    QDockWidget#RadiometricAnalysisSuiteDock::title {
        background-color: rgba(255, 255, 255, 246);
        color: #202427;
        border-bottom: 1px solid rgba(0, 0, 0, 22);
    }

    QTabWidget::pane, QGroupBox {
        background-color: rgba(255, 255, 255, 250);
        border: 1px solid rgba(120, 125, 130, 88);
    }

    QGroupBox::title {
        color: #24292C;
    }

    QTabBar::tab {
        background-color: rgba(240, 241, 242, 248);
        color: #2B3033;
        border: 1px solid rgba(135, 140, 145, 72);
        border-bottom-color: rgba(135, 140, 145, 105);
    }

    QTabBar::tab:selected {
        color: #B93631;
        background-color: rgba(255, 255, 255, 252);
        border: 1px solid rgba(135, 140, 145, 105);
        border-bottom: 3px solid #D84A43;
    }

    QTabBar::tab:hover:!selected {
        background-color: rgba(250, 244, 243, 250);
        color: #B93631;
    }

    QPushButton, QToolButton {
        color: #202427;
        border: 1px solid rgba(85, 90, 95, 105);
        border-top-color: rgba(255, 255, 255, 210);
        border-left-color: rgba(255, 255, 255, 190);
        border-right-color: rgba(80, 85, 90, 125);
        border-bottom-color: rgba(70, 75, 80, 145);
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 rgba(255,255,255,252),
                                          stop:1 rgba(226,228,230,250));
    }

    QPushButton:hover, QToolButton:hover {
        border: 1px solid rgba(210, 74, 67, 145);
        border-top-color: rgba(255, 255, 255, 220);
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 rgba(255,250,249,252),
                                          stop:1 rgba(239,226,225,250));
        color: #A9332E;
    }

    QPushButton:pressed, QToolButton:pressed {
        border-top-color: rgba(70, 75, 80, 145);
        border-left-color: rgba(70, 75, 80, 125);
        border-right-color: rgba(255, 255, 255, 175);
        border-bottom-color: rgba(255, 255, 255, 190);
        background-color: rgba(225, 226, 228, 250);
    }

    QPushButton:default, QPushButton#PrimaryButton {
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #E9645D,
                                          stop:1 #C83B35);
        color: white;
        border-top-color: rgba(255, 255, 255, 130);
        border-left-color: rgba(255, 255, 255, 95);
        border-right-color: #9F2B27;
        border-bottom-color: #8D2622;
    }

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
        background-color: rgba(255, 255, 255, 252);
        color: #202427;
        border: 1px solid rgba(110, 115, 120, 135);
        selection-background-color: rgba(216, 74, 67, 115);
    }

    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QTextEdit:focus, QPlainTextEdit:focus {
        border: 1px solid rgba(210, 74, 67, 170);
    }

    QCheckBox::indicator {
        background-color: rgba(255,255,255,252);
        border: 1px solid rgba(110, 115, 120, 135);
        border-radius: 2px;
    }

    QCheckBox::indicator:checked {
        background-color: #D84A43;
        border: 1px solid #B93631;
    }

    QProgressBar {
        background-color: rgba(0, 0, 0, 18);
        border: 1px solid rgba(0, 0, 0, 34);
    }

    QTableView, QTableWidget, QTreeView {
        background-color: rgba(255,255,255,252);
        color: #202427;
        border: 1px solid rgba(110, 115, 120, 90);
        gridline-color: rgba(140, 145, 150, 65);
    }

    QHeaderView::section {
        background-color: rgba(240, 241, 242, 250);
        color: #303437;
        border: 1px solid rgba(0, 0, 0, 28);
    }

    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        background-color: rgba(190, 196, 200, 185);
    }

    QToolTip {
        color: #202427;
        background-color: rgba(255, 255, 255, 250);
        border: 1px solid rgba(210, 74, 67, 115);
    }
    """


def dark_qss():
    return _common_qss() + r"""
    QWidget {
        color: #EDEDED;
        background-color: transparent;
    }

    QDialog, QMenu, QAbstractScrollArea, QScrollArea QWidget#qt_scrollarea_viewport {
        background-color: rgba(31, 32, 33, 252);
    }

    QWidget#RadiometricAnalysisPanel {
        background-color: rgba(31, 32, 33, 252);
    }

    QFrame#RadiometricHeader, QWidget#RadiometricHeader {
        background-color: rgba(41, 42, 43, 248);
        border: 1px solid rgba(255, 255, 255, 58);
    }

    QDockWidget#RadiometricAnalysisSuiteDock {
        background-color: rgba(28, 29, 30, 252);
    }

    QDockWidget#RadiometricAnalysisSuiteDock::title {
        background-color: rgba(34, 35, 36, 246);
        color: #F2F2F2;
        border-bottom: 1px solid rgba(255, 255, 255, 26);
    }

    QTabWidget::pane, QGroupBox {
        background-color: rgba(43, 44, 45, 250);
        border: 1px solid rgba(255, 255, 255, 64);
    }

    QGroupBox::title {
        color: #F0F0F0;
    }

    QTabBar::tab {
        background-color: rgba(48, 49, 50, 248);
        color: #DADADA;
        border: 1px solid rgba(255, 255, 255, 34);
        border-bottom-color: rgba(255, 255, 255, 55);
    }

    QTabBar::tab:selected {
        color: #FFFFFF;
        background-color: rgba(85, 47, 45, 252);
        border: 1px solid rgba(255, 255, 255, 72);
        border-bottom: 3px solid #D85A53;
    }

    QTabBar::tab:hover:!selected {
        background-color: rgba(66, 53, 52, 250);
        color: #FFFFFF;
    }

    QPushButton, QToolButton {
        color: #EDEDED;
        border: 1px solid rgba(0, 0, 0, 160);
        border-top-color: rgba(255, 255, 255, 70);
        border-left-color: rgba(255, 255, 255, 52);
        border-right-color: rgba(0, 0, 0, 190);
        border-bottom-color: rgba(0, 0, 0, 215);
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 rgba(67,68,69,252),
                                          stop:1 rgba(45,46,47,250));
    }

    QPushButton:hover, QToolButton:hover {
        border: 1px solid rgba(216, 90, 83, 140);
        border-top-color: rgba(255, 255, 255, 85);
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 rgba(78,65,64,252),
                                          stop:1 rgba(55,45,44,250));
        color: #FFFFFF;
    }

    QPushButton:pressed, QToolButton:pressed {
        border-top-color: rgba(0, 0, 0, 210);
        border-left-color: rgba(0, 0, 0, 185);
        border-right-color: rgba(255, 255, 255, 52);
        border-bottom-color: rgba(255, 255, 255, 60);
        background-color: rgba(45, 46, 47, 250);
    }

    QPushButton:default, QPushButton#PrimaryButton {
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #C95A54,
                                          stop:1 #963531);
        color: white;
        border-top-color: rgba(255, 255, 255, 85);
        border-left-color: rgba(255, 255, 255, 60);
        border-right-color: rgba(0, 0, 0, 210);
        border-bottom-color: rgba(0, 0, 0, 230);
    }

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
        background-color: rgba(24, 25, 26, 252);
        color: #EDEDED;
        border: 1px solid rgba(255, 255, 255, 62);
        selection-background-color: rgba(216, 90, 83, 130);
    }

    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QTextEdit:focus, QPlainTextEdit:focus {
        border: 1px solid rgba(216, 90, 83, 170);
    }

    QCheckBox::indicator {
        background-color: rgba(24,25,26,252);
        border: 1px solid rgba(255, 255, 255, 62);
        border-radius: 2px;
    }

    QCheckBox::indicator:checked {
        background-color: #D85A53;
        border: 1px solid #F07A72;
    }

    QProgressBar {
        background-color: rgba(0, 0, 0, 80);
        border: 1px solid rgba(255, 255, 255, 38);
    }

    QTableView, QTableWidget, QTreeView {
        background-color: rgba(32,33,34,252);
        color: #EDEDED;
        border: 1px solid rgba(255, 255, 255, 56);
        gridline-color: rgba(255, 255, 255, 34);
    }

    QHeaderView::section {
        background-color: rgba(44, 45, 46, 250);
        color: #EDEDED;
        border: 1px solid rgba(255, 255, 255, 32);
    }

    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        background-color: rgba(95, 98, 100, 200);
    }

    QToolTip {
        color: #F2F2F2;
        background-color: rgba(40, 40, 40, 250);
        border: 1px solid rgba(216, 90, 83, 130);
    }
    """


def stylesheet(theme):
    return dark_qss() if str(theme).lower() == DARK else light_qss()


def apply_radiometric_theme(widget, theme=LIGHT):
    """Apply the radiometric theme to a widget and, if present, its dock parent."""
    app_font = QFont("Segoe UI", 10)
    try:
        widget.setFont(app_font)
    except Exception:
        logging.getLogger(__name__).debug("Optional operation failed in apply_radiometric_theme; existing fallback retained.", exc_info=True)
    try:
        widget.setStyleSheet(stylesheet(theme))
    except Exception:
        logging.getLogger(__name__).debug("Optional operation failed in apply_radiometric_theme; existing fallback retained.", exc_info=True)

    parent = widget.parentWidget()
    while parent is not None:
        try:
            if parent.objectName() == "RadiometricAnalysisSuiteDock":
                parent.setStyleSheet(stylesheet(theme))
                # Keep the dock visually solid. The QSS uses very high alpha;
                # opacity remains just below 1.0 for a subtle non-flat edge.
                parent.setWindowOpacity(0.995)
                break
        except Exception:
            logging.getLogger(__name__).debug("Optional operation failed in apply_radiometric_theme; existing fallback retained.", exc_info=True)
        parent = parent.parentWidget()
