import pytest
from pathlib import Path

import numpy as np

from ophys_etl.modules.segmentation.graph_utils.conversion import (
    graph_to_img)
from ophys_etl.modules.segmentation.detect.feature_vector_segmentation \
    import (FeatureVectorSegmenter, _is_roi_at_edge)
from ophys_etl.modules.segmentation.processing_log import \
    SegmentationProcessingLog


@pytest.fixture
def seeder_args():
    args = {
            'exclusion_buffer': 1,
            'n_samples': 10,
            'keep_fraction': 0.05,
            'minimum_distance': 3.0,
            'seeder_grid_size': None}
    return args


def test_graph_to_img(example_graph):
    """
    smoke test graph_to_img
    """
    img = graph_to_img(example_graph,
                       attribute_name='dummy_attribute')

    # check that image has expected type and shape
    assert type(img) == np.ndarray
    assert img.shape == (40, 40)

    # check that ROI pixels are
    # brighter than non ROI pixels
    roi_mask = np.zeros((40, 40), dtype=bool)
    roi_mask[12:16, 4:7] = True
    roi_mask[25:32, 11:18] = True
    roi_mask[25:27, 15:18] = False

    roi_flux = img[roi_mask].flatten()
    complement = np.logical_not(roi_mask)
    not_roi_flux = img[complement].flatten()

    roi_mu = np.mean(roi_flux)
    roi_std = np.std(roi_flux, ddof=1)
    not_mu = np.mean(not_roi_flux)
    not_std = np.std(not_roi_flux, ddof=1)

    assert roi_mu > not_mu+roi_std+not_std


def test_segmenter(tmpdir, example_graph, example_video, seeder_args):
    """
    Smoke test for segmenter
    """

    segmenter = FeatureVectorSegmenter(graph_input=example_graph,
                                       video_input=example_video,
                                       attribute='dummy_attribute',
                                       filter_fraction=0.2,
                                       n_processors=1,
                                       seeder_args=seeder_args)

    log_path = Path(tmpdir / 'processing_log.h5')
    plot_path = Path(tmpdir / 'plot.png')
    assert not log_path.exists()
    assert not plot_path.exists()

    segmenter.run(log_path=log_path,
                  plot_output=plot_path,
                  growth_z_score=2.0)

    assert log_path.is_file()
    assert plot_path.is_file()

    # check that all ROIs are marked as valid
    processing_log = SegmentationProcessingLog(log_path, read_only=True)
    roi_data = processing_log.get_rois_from_group(
            processing_log.get_last_group())
    assert len(roi_data) > 0
    for roi in roi_data:
        assert roi['valid']

    # test that it can handle not receiving a
    # log_path or plot_path
    log_path.unlink()
    plot_path.unlink()

    assert not log_path.exists()
    assert not plot_path.exists()

    segmenter.run(log_path=log_path,
                  plot_output=None,
                  growth_z_score=2.0)

    assert log_path.exists()
    assert not plot_path.exists()

    # Not going to re-do the check on the contents
    # of roi_path, since the file is likely to be
    # empty. The segmenter carries a state that helps
    # it avoid doubling back and discovering ROIs that
    # it has already discovered. Since we have not
    # reset that state since the last run, no ROIs
    # will be found.


def test_segmenter_blank(tmpdir, blank_graph, blank_video, seeder_args):
    """
    Smoke test for segmenter on blank inputs
    """

    segmenter = FeatureVectorSegmenter(graph_input=blank_graph,
                                       video_input=blank_video,
                                       attribute='dummy_attribute',
                                       filter_fraction=0.2,
                                       n_processors=1,
                                       seeder_args=seeder_args)
    log_path = tmpdir / "processing_log.h5"
    segmenter.run(log_path=log_path,
                  growth_z_score=2.0)


def test_is_roi_at_edge():

    # in the middle of fov
    mask = np.zeros((9, 9), dtype=bool)
    mask[2, 2] = True
    assert not _is_roi_at_edge((10, 10),
                               (20, 20),
                               mask)

    # at upper edge
    mask = np.zeros((9, 9), dtype=bool)
    mask[0, 3:5] = True
    assert _is_roi_at_edge((10, 10),
                           (20, 20),
                           mask)

    # at left edge
    mask = np.zeros((9, 9), dtype=bool)
    mask[3:5, 0] = True
    assert _is_roi_at_edge((10, 10),
                           (20, 20),
                           mask)

    # at right edge
    mask = np.zeros((9, 9), dtype=bool)
    mask[3:5, 8] = True
    assert _is_roi_at_edge((10, 10),
                           (20, 20),
                           mask)

    # at bottom edge
    mask = np.zeros((9, 9), dtype=bool)
    mask[8, 3:5] = True
    assert _is_roi_at_edge((10, 10),
                           (20, 20),
                           mask)

    # at upper edge with no room to grow
    mask = np.zeros((9, 9), dtype=bool)
    mask[0, 3:5] = True
    assert not _is_roi_at_edge((0, 10),
                               (20, 20),
                               mask)

    # at left edge with no room to grow
    mask = np.zeros((9, 9), dtype=bool)
    mask[3:5, 0] = True
    assert not _is_roi_at_edge((10, 0),
                               (20, 20),
                               mask)

    # at right edge with no room to grow
    mask = np.zeros((9, 9), dtype=bool)
    mask[3:5, 8] = True
    assert not _is_roi_at_edge((10, 10),
                               (20, 19),
                               mask)

    # at bottom edge with no room to grow
    mask = np.zeros((9, 9), dtype=bool)
    mask[8, 3:5] = True
    assert not _is_roi_at_edge((10, 10),
                               (19, 20),
                               mask)