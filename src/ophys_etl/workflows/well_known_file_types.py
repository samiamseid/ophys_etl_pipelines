"""Well known file types"""
from enum import Enum


class WellKnownFileTypeEnum(Enum):
    ########
    # motion correction
    ########
    MAX_INTENSITY_PROJECTION_IMAGE = "MAX_INTENSITY_PROJECTION_IMAGE"
    AVG_INTENSITY_PROJECTION_IMAGE = "AVG_INTENSITY_PROJECTION_IMAGE"
    REGISTRATION_SUMMARY_IMAGE = "REGISTRATION_SUMMARY_IMAGE"
    MOTION_CORRECTED_IMAGE_STACK = "MOTION_CORRECTED_IMAGE_STACK"
    MOTION_X_Y_OFFSET_DATA = "MOTION_X_Y_OFFSET_DATA"
    MOTION_PREVIEW = "MOTION_PREVIEW"

    ########
    # denoising
    ########
    DEEPINTERPOLATION_FINETUNED_MODEL = "DEEPINTERPOLATION_FINETUNED_MODEL"
    DEEPINTERPOLATION_DENOISED_MOVIE = "DEEPINTERPOLATION_DENOISED_MOVIE"

    ########
    # segmentation
    ########
    OPHYS_ROIS = "OPHYS_ROIS"

    ########
    # trace extraction
    ########
    NEUROPIL_TRACE = "NEUROPIL_TRACE"
    ROI_TRACE = "ROI_TRACE"
    NEUROPIL_MASK = "NEUROPIL_MASK"
    TRACE_EXTRACTION_EXCLUSION_LABELS = "TRACE_EXTRACTION_EXCLUSION_LABELS"

    ########
    # Demixed Traces
    ########
    DEMIXED_TRACES = "DEMIXED_TRACES"

    ########
    # ROI classification
    ########
    ROI_CLASSIFICATION_CORRELATION_PROJECTION_GRAPH = (
        "ROI_CLASSIFICATION_CORRELATION_PROJECTION_GRAPH"
    )
    ROI_CLASSIFICATION_THUMBNAIL_IMAGES = "ROI_CLASSIFICATION_THUMBNAIL_IMAGES"

    # training-specific
    ROI_CLASSIFICATION_TRAIN_SET = "ROI_CLASSIFICATION_TRAIN_SET"
    ROI_CLASSIFICATION_TEST_SET = "ROI_CLASSIFICATION_TEST_SET"
    ROI_CLASSIFICATION_TRAINED_MODEL = "ROI_CLASSIFICATION_TRAINED_MODEL"

    # inference-specific
    ROI_CLASSIFICATION_EXPERIMENT_PREDICTIONS = (
        "ROI_CLASSIFICATION_EXPERIMENT_PREDICTIONS"
    )

    ########
    # decrosstalk
    ########
    DECROSSTALK_FLAGS = "DECROSSTALK_FLAGS"

    ########
    # Neuropil correction
    ########
    NEUROPIL_CORRECTED_TRACES = "NEUROPIL_CORRECTED_TRACES"

    ########
    # DF/F
    ########
    DFF_TRACES = "DFF_TRACES"

    ########
    # nway cell matching
    ########
    NWAY_CELL_MATCHING_METADATA = "NWAY_CELL_MATCHING_METADATA"
