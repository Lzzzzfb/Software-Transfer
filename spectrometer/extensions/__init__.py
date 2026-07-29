"""
extensions — 光谱数据处理扩展模块.

包含基线校正 (airPLS)、去噪、寻峰等算法.
"""

from .airpls import (
    airpls,
    airpls_baseline_correct,
    penalty_matrix,
    validate_airpls_parameters,
)
