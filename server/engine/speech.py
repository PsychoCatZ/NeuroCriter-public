"""Стадии речи по накопленной «заботе», а не по календарю."""
from .constants import STAGE_THRESHOLDS


def stage_for(care_points):
    stage = 1
    for i, threshold in enumerate(STAGE_THRESHOLDS, start=1):
        if care_points >= threshold:
            stage = i
    return stage
