# -*- coding: utf-8 -*-
"""Shared workflow state for the Radiometric Analysis dock."""

class RadiometricWorkflowState:
    """Shared state for the dock tabs."""
    def __init__(self):
        self.processing_folder = ''
        self.processing_session = ''
        self.session_folder = ''
        self.qc_run_label = ''
        self.filtered_rasters = {'U': '', 'K': '', 'TH': ''}
        self.ratio_rasters = {'U_Th_ratio': '', 'U_K_ratio': '', 'K_U_ratio': '', 'K_Th_ratio': ''}

