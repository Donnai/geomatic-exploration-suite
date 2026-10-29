# -*- coding: utf-8 -*-
"""Raster I/O facade used by dock tabs and Processing algorithms.

The implementation currently delegates to algs.utils so the dock UI and the
Processing Toolbox algorithms use the same raster behaviour.
"""
from ..algs.utils import read_raster, write_raster, safe_divide, safe_divide_chunked

__all__ = ["read_raster", "write_raster", "safe_divide", "safe_divide_chunked"]
