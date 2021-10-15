import pytest
import numpy as np
import imageio
import copy
import h5py
import pathlib
import tempfile
import gc
from itertools import product

from ophys_etl.types import ExtractROI

from ophys_etl.modules.segmentation.qc_utils.video_utils import (
    upscale_video_frame,
    trim_video,
    thumbnail_video_from_array,
    thumbnail_video_from_path,
    _thumbnail_video_from_ROI_array,
    _thumbnail_video_from_ROI_path,
    thumbnail_video_from_ROI,
    scale_video_to_uint8,
    video_bounds_from_ROI,
    ThumbnailVideo,
    _read_and_scale_all_at_once,
    _read_and_scale_by_chunks,
    read_and_scale)


@pytest.fixture(scope='session')
def example_video():
    rng = np.random.RandomState(16412)
    data = rng.randint(0, 100, (100, 60, 60)).astype(np.uint8)
    for ii in range(100):
        data[ii, ::, :] = ii
    return data


@pytest.fixture(scope='session')
def example_video_path(tmpdir_factory, example_video):
    tmpdir = pathlib.Path(tmpdir_factory.mktemp('example_video'))
    base_fname = tempfile.mkstemp(dir=tmpdir,
                                  prefix='example_video_',
                                  suffix='.h5')[1]
    with h5py.File(base_fname, 'w') as out_file:
        out_file.create_dataset('data',
                                data=example_video)
    base_fname = pathlib.Path(base_fname)
    yield base_fname
    base_fname.unlink()
    tmpdir.rmdir()


@pytest.fixture
def example_rgb_video():
    rng = np.random.RandomState(16412)
    data = rng.randint(0, 100, (100, 60, 60, 3)).astype(np.uint8)
    for ii in range(100):
        data[ii, ::, :] = ii
    return data


@pytest.fixture(scope='session')
def example_unnormalized_rgb_video():
    rng = np.random.RandomState(6125321)
    data = rng.randint(0, 700, (100, 60, 60, 3))
    return data


@pytest.fixture(scope='session')
def example_unnormalized_rgb_video_path(
        tmpdir_factory,
        example_unnormalized_rgb_video):
    tmpdir = pathlib.Path(tmpdir_factory.mktemp('eg_unnorm_rgb_video'))
    # write video to a tempfile
    h5_fname = tempfile.mkstemp(dir=tmpdir,
                                prefix='example_unnormalized_rgb_video_',
                                suffix='.h5')[1]
    with h5py.File(h5_fname, 'w') as out_file:
        out_file.create_dataset('data', data=example_unnormalized_rgb_video)

    h5_fname = pathlib.Path(h5_fname)
    yield h5_fname
    h5_fname.unlink()
    tmpdir.rmdir()


@pytest.fixture(scope='session')
def chunked_video_path(tmpdir_factory):
    tmpdir = pathlib.Path(tmpdir_factory.mktemp('chunked_video'))
    fname = tempfile.mkstemp(dir=tmpdir,
                             prefix='example_large_video_chunked_',
                             suffix='.h5')[1]
    rng = np.random.RandomState(22312)
    with h5py.File(fname, 'w') as out_file:
        dataset = out_file.create_dataset('data',
                                          (214, 10, 10),
                                          chunks=(100, 5, 5),
                                          dtype=np.uint16)
        for chunk in dataset.iter_chunks():
            arr = rng.randint(0, 65536,
                              (chunk[0].stop-chunk[0].start,
                               chunk[1].stop-chunk[1].start,
                               chunk[2].stop-chunk[2].start))
            dataset[chunk] = arr

    fname = pathlib.Path(fname)
    yield fname
    fname.unlink()
    tmpdir.rmdir()


@pytest.fixture(scope='session')
def unchunked_video_path(tmpdir_factory):
    tmpdir = pathlib.Path(tmpdir_factory.mktemp('unchunked_video'))
    fname = tempfile.mkstemp(dir=tmpdir,
                             prefix='example_large_video_unchunked_',
                             suffix='.h5')[1]
    rng = np.random.RandomState(714432)
    with h5py.File(fname, 'w') as out_file:
        data = rng.randint(0, 65536, size=(214, 10, 10)).astype(np.uint16)
        out_file.create_dataset('data',
                                data=data,
                                chunks=None,
                                dtype=np.uint16)

    fname = pathlib.Path(fname)
    yield fname
    fname.unlink()
    tmpdir.rmdir()


@pytest.mark.parametrize("data_fixture", ["example_video",
                                          "example_rgb_video"])
def test_thumbnail_video(data_fixture, tmpdir, request):
    """
    Just test that ThumbnailVideo can write the video
    and set its properties correctly
    """
    video_data = request.getfixturevalue(data_fixture)
    n_t = video_data.shape[0]
    video_path = tempfile.mkstemp(dir=tmpdir, suffix='.mp4')[1]
    thumbnail = ThumbnailVideo(video_data,
                               pathlib.Path(video_path),
                               (111, 222),
                               quality=6,
                               fps=22)

    assert thumbnail.video_path.is_file()
    read_data = imageio.mimread(thumbnail.video_path)
    assert len(read_data) == n_t
    assert thumbnail.frame_shape == (video_data.shape[1],
                                     video_data.shape[2])

    assert thumbnail.origin == (111, 222)
    assert thumbnail.timesteps is None

    # check that the video is deleted when the thumbnail is deleted
    test_path = copy.deepcopy(thumbnail.video_path)
    del thumbnail
    gc.collect()
    assert not test_path.exists()

    # test non-None timesteps
    video_path = tempfile.mkstemp(dir=tmpdir, suffix='.mp4')[1]
    thumbnail = ThumbnailVideo(video_data,
                               pathlib.Path(video_path),
                               (111, 222),
                               quality=6,
                               fps=22,
                               timesteps=np.arange(450, 450+n_t))

    assert thumbnail.video_path.is_file()
    read_data = imageio.mimread(thumbnail.video_path)
    assert len(read_data) == n_t
    assert thumbnail.frame_shape == (video_data.shape[1],
                                     video_data.shape[2])

    assert thumbnail.origin == (111, 222)
    np.testing.assert_array_equal(thumbnail.timesteps,
                                  np.arange(450, 550))

    # check that the video is deleted when the thumbnail is deleted
    test_path = copy.deepcopy(thumbnail.video_path)
    del thumbnail
    gc.collect()
    assert not test_path.exists()


@pytest.mark.parametrize("video_data_fixture",
                         ["example_video",
                          "example_rgb_video"])
def test_trim_video(video_data_fixture, request):
    video_data = request.getfixturevalue(video_data_fixture)

    origin = (3, 9)
    frame_shape = (10, 14)

    # no timesteps specified
    expected = video_data[:,
                          origin[0]:origin[0]+frame_shape[0],
                          origin[1]:origin[1]+frame_shape[1]]

    trimmed_video = trim_video(video_data, origin, frame_shape)
    np.testing.assert_array_equal(trimmed_video, expected)
    assert len(trimmed_video.shape) == len(video_data.shape)

    # specify timesteps
    timesteps = np.concatenate([np.arange(15, 45),
                                np.arange(76, 83)])

    expected = expected[timesteps]
    trimmed_video = trim_video(video_data,
                               origin,
                               frame_shape,
                               timesteps=timesteps)
    np.testing.assert_array_equal(trimmed_video, expected)
    assert len(trimmed_video.shape) == len(video_data.shape)


def test_scale_video():

    data = np.array([[1.0, 2.0, 3.0, 4.0],
                     [5.0, 6.0, 7.0, 8.0]])

    scaled = scale_video_to_uint8(data,
                                  0.0,
                                  8.0)
    assert scaled.dtype == np.uint8

    expected = np.array([[32, 64, 96, 128],
                         [159, 191, 223, 255]]).astype(np.uint8)

    np.testing.assert_array_equal(expected, scaled)

    scaled = scale_video_to_uint8(data,
                                  0.0,
                                  15.0)
    expected = np.array([[17, 34, 51, 68],
                         [85, 102, 119, 136]]).astype(np.uint8)

    np.testing.assert_array_equal(expected, scaled)

    scaled = scale_video_to_uint8(data,
                                  2.0,
                                  7.0)

    expected = np.array([[0, 0, 51, 102],
                         [153, 204, 255, 255]]).astype(np.uint8)

    np.testing.assert_array_equal(expected, scaled)

    with pytest.raises(RuntimeError, match="in scale_video_to_uint8"):
        _ = scale_video_to_uint8(data, 1.0, 0.0)


@pytest.mark.parametrize("timesteps",
                         [None, np.arange(22, 56)])
def test_thumbnail_from_array(tmpdir, example_video, timesteps):

    th_video = thumbnail_video_from_array(example_video,
                                          (11, 3),
                                          (16, 32),
                                          timesteps=timesteps)

    assert type(th_video) == ThumbnailVideo
    assert th_video.video_path.is_file()
    assert th_video.origin == (11, 3)
    assert th_video.frame_shape == (16, 32)
    if timesteps is None:
        assert th_video.timesteps is None
        n_t = example_video.shape[0]
    else:
        np.testing.assert_array_equal(timesteps,
                                      th_video.timesteps)
        n_t = len(timesteps)

    read_data = imageio.mimread(th_video.video_path)

    assert len(read_data) == n_t

    # shape gets automatically upscaled when
    # written to temporary video file
    assert read_data[0].shape == (32, 64, 3)

    # cannot to bitwise comparison of input to read data;
    # mp4 compression leads to slight differences

    file_path = str(th_video.video_path)
    file_path = pathlib.Path(file_path)

    del th_video
    gc.collect()

    assert not file_path.exists()


@pytest.mark.parametrize("timesteps",
                         [None, np.arange(22, 56)])
def test_thumbnail_from_rgb_array(tmpdir, example_rgb_video, timesteps):

    th_video = thumbnail_video_from_array(example_rgb_video,
                                          (11, 3),
                                          (16, 32),
                                          timesteps=timesteps)

    assert type(th_video) == ThumbnailVideo
    assert th_video.video_path.is_file()
    assert th_video.origin == (11, 3)
    assert th_video.frame_shape == (16, 32)
    if timesteps is None:
        n_t = example_rgb_video.shape[0]
        assert th_video.timesteps is None
    else:
        n_t = len(timesteps)
        np.testing.assert_array_equal(timesteps,
                                      th_video.timesteps)

    read_data = imageio.mimread(th_video.video_path)

    assert len(read_data) == n_t

    # shape gets automatically upscaled by factor of 4
    # when written to temporary video file
    assert read_data[0].shape == (32, 64, 3)

    # cannot to bitwise comparison of input to read data;
    # mp4 compression leads to slight differences

    file_path = str(th_video.video_path)
    file_path = pathlib.Path(file_path)

    del th_video
    gc.collect()

    assert not file_path.exists()


@pytest.mark.parametrize("timesteps, padding, with_others, roi_color",
                         product((None, np.arange(22, 56)),
                                 (10, 0),
                                 (True, False),
                                 (None, (0, 255, 0), 'a_dict')))
def test_thumbnail_from_roi(tmpdir,
                            example_video,
                            timesteps,
                            padding,
                            with_others,
                            roi_color):

    if timesteps is None:
        n_t = example_video.shape[0]
    else:
        n_t = len(timesteps)

    mask = np.zeros((7, 8), dtype=bool)
    mask[3:5, 1:6] = True

    y0 = 20
    height = 7
    x0 = 10
    width = 8

    roi = ExtractROI(id=0,
                     y=y0,
                     height=height,
                     x=x0,
                     width=width,
                     valid=True,
                     mask=[list(row) for row in mask])

    if with_others:
        other_roi = []
        ct = 0
        for dx, dy in product((1, 2), (-1, 0, 1)):
            ct += 1
            other_roi.append(ExtractROI(
                                id=ct,
                                y=y0+dy,
                                x=x0+dx,
                                height=height,
                                width=width,
                                valid=True,
                                mask=[list(row) for row in mask]))

    else:
        other_roi = None

    if isinstance(roi_color, str):
        roi_color = dict()
        roi_color[0] = (255, 0, 0)
        if other_roi is not None:
            rng = np.random.default_rng(111)
            for roi in other_roi:
                color = tuple(rng.integers(0, 255, size=3))
                roi_color[roi['id']] = color

    thumbnail = _thumbnail_video_from_ROI_array(
                    example_video,
                    roi,
                    other_roi=other_roi,
                    roi_color=roi_color,
                    padding=padding,
                    tmp_dir=pathlib.Path(tmpdir),
                    quality=9,
                    timesteps=timesteps)

    origin, fov = video_bounds_from_ROI(roi,
                                        example_video.shape[1:3],
                                        padding)

    assert thumbnail.origin == origin
    assert thumbnail.frame_shape == fov

    rowmin = thumbnail.origin[0]
    rowmax = thumbnail.origin[0]+thumbnail.frame_shape[0]
    colmin = thumbnail.origin[1]
    colmax = thumbnail.origin[1]+thumbnail.frame_shape[1]
    assert rowmin <= y0
    assert rowmax >= y0+height
    assert colmin <= x0
    assert colmax >= x0+width

    assert thumbnail.video_path.is_file()

    read_data = imageio.mimread(thumbnail.video_path)
    assert len(read_data) == n_t

    # factor of 4 reflects the upscaling of video frame sizes
    frame_shape = thumbnail.frame_shape
    if frame_shape[0] < 128 or frame_shape[1] < 128:
        assert read_data[0].shape == (2*thumbnail.frame_shape[0],
                                      2*thumbnail.frame_shape[1],
                                      3)
    else:
        assert read_data[0].shape == (thumbnail.frame_shape[0],
                                      thumbnail.frame_shape[1],
                                      3)


@pytest.mark.parametrize("min_max, quantiles, timesteps",
                         [((50, 650), None, None),
                          ((50, 650), None, np.arange(22, 57)),
                          ((50, 650), None, None),
                          ((50, 650), None, np.arange(22, 57)),
                          ((111, 556), None, None),
                          ((111, 556), None, np.arange(22, 57)),
                          (None, (0.1, 0.75), None),
                          (None, (0.1, 0.75), np.arange(22, 57))
                          ])
def test_thumbnail_from_path(tmpdir,
                             example_unnormalized_rgb_video,
                             example_unnormalized_rgb_video_path,
                             min_max,
                             quantiles,
                             timesteps):
    """
    Test thumbnail_from_path by comparing output to result
    from thumbnail_from_array
    """

    if timesteps is None:
        n_t = example_unnormalized_rgb_video.shape[0]
    else:
        n_t = len(timesteps)

    sub_video = np.copy(example_unnormalized_rgb_video[:, 18:30, 14:29, :])

    if quantiles is not None:
        local_min_max = np.quantile(example_unnormalized_rgb_video,
                                    quantiles)
    else:
        local_min_max = min_max

    sub_video = scale_video_to_uint8(sub_video,
                                     local_min_max[0],
                                     local_min_max[1])

    control_video = thumbnail_video_from_array(
                       sub_video,
                       (0, 0),
                       (12, 15),
                       tmp_dir=pathlib.Path(tmpdir),
                       timesteps=timesteps)

    test_video = thumbnail_video_from_path(
                     example_unnormalized_rgb_video_path,
                     (18, 14),
                     (12, 15),
                     tmp_dir=pathlib.Path(tmpdir),
                     min_max=min_max,
                     quantiles=quantiles,
                     timesteps=timesteps)

    assert test_video.origin == (18, 14)
    assert test_video.frame_shape == (12, 15)
    control_data = imageio.mimread(control_video.video_path)
    test_data = imageio.mimread(test_video.video_path)
    assert len(control_data) == len(test_data)
    assert len(test_data) == n_t
    for ii in range(len(control_data)):
        np.testing.assert_array_equal(control_data[ii], test_data[ii])


@pytest.mark.parametrize("quantiles,min_max,roi_color,timesteps,padding,"
                         "with_others",
                         product(((0.1, 0.9), None),
                                 ((250, 600), None),
                                 ((255, 0, 0), None, 'a_dict'),
                                 (np.arange(22, 76), None),
                                 (0, 10),
                                 (True, False)))
def test_thumbnail_from_roi_and_path(tmpdir,
                                     example_unnormalized_rgb_video,
                                     example_unnormalized_rgb_video_path,
                                     quantiles,
                                     min_max,
                                     roi_color,
                                     timesteps,
                                     padding,
                                     with_others):
    """
    Test _thumbnail_from_ROI_path by comparing output to result
    from _thumbnail_from_ROI_array
    """

    if min_max is not None and quantiles is not None:
        return
    if min_max is None and quantiles is None:
        return

    if timesteps is None:
        n_t = example_unnormalized_rgb_video.shape[0]
    else:
        n_t = len(timesteps)

    mask = np.zeros((12, 15), dtype=bool)
    mask[2:10, 3:13] = True

    x0 = 14
    y0 = 18
    width = 15
    height = 12

    roi = ExtractROI(x=x0, width=width,
                     y=y0, height=height,
                     mask=[list(i) for i in mask],
                     id=0)

    if with_others:
        other_roi = []
        ct = 0
        for dx, dy in product((1, 2), (-1, 0, 1)):
            ct += 1
            other_roi.append(ExtractROI(
                                id=ct,
                                y=y0+dy,
                                x=x0+dx,
                                height=height,
                                width=width,
                                valid=True,
                                mask=[list(row) for row in mask]))
    else:
        other_roi = None

    if isinstance(roi_color, str):
        roi_color = dict()
        roi_color[0] = (255, 0, 0)
        if with_others:
            rng = np.random.default_rng(2823)
            for roi in other_roi:
                color = rng.integers(0, 255, size=3)
                roi_color[roi['id']] = tuple(color)

    h5_fname = example_unnormalized_rgb_video_path

    if quantiles is not None:
        local_min_max = np.quantile(example_unnormalized_rgb_video,
                                    quantiles)
    else:
        local_min_max = min_max

    normalized_video = scale_video_to_uint8(
                            np.copy(example_unnormalized_rgb_video),
                            local_min_max[0],
                            local_min_max[1])

    control_video = _thumbnail_video_from_ROI_array(
                       normalized_video,
                       roi,
                       other_roi=other_roi,
                       padding=padding,
                       roi_color=roi_color,
                       tmp_dir=pathlib.Path(tmpdir),
                       timesteps=timesteps)

    test_video = _thumbnail_video_from_ROI_path(
                     h5_fname,
                     roi,
                     other_roi=other_roi,
                     padding=padding,
                     roi_color=roi_color,
                     tmp_dir=pathlib.Path(tmpdir),
                     quantiles=quantiles,
                     min_max=min_max,
                     timesteps=timesteps)

    origin, fov = video_bounds_from_ROI(roi, normalized_video.shape[1:3],
                                        padding)

    assert test_video.origin == origin
    assert test_video.frame_shape == fov

    control_data = imageio.mimread(control_video.video_path)
    test_data = imageio.mimread(test_video.video_path)
    assert test_video.origin == control_video.origin
    assert test_video.frame_shape == control_video.frame_shape
    assert len(control_data) == len(test_data)
    assert len(test_data) == n_t
    for ii in range(len(control_data)):
        np.testing.assert_array_equal(control_data[ii], test_data[ii])


@pytest.mark.parametrize("timesteps, padding",
                         [(None, 0),
                          (None, 10),
                          (np.arange(22, 56), 0),
                          (np.arange(22, 56), 10)])
def test_generic_generation_from_ROI(tmpdir,
                                     example_video,
                                     example_video_path,
                                     timesteps,
                                     padding):
    """
    Just smoketest thumbnail_video_from_ROI
    """
    if timesteps is None:
        n_t = example_video.shape[0]
    else:
        n_t = len(timesteps)

    mask = np.zeros((12, 15), dtype=bool)
    mask[2:10, 3:13] = True

    roi = ExtractROI(x=14, width=15,
                     y=18, height=12,
                     mask=[list(i) for i in mask])

    true_origin, true_fov = video_bounds_from_ROI(roi,
                                                  example_video.shape[1:3],
                                                  padding)

    th = thumbnail_video_from_ROI(example_video.astype(np.uint8),
                                  roi,
                                  padding=padding,
                                  roi_color=(0, 255, 0),
                                  file_path=None,
                                  tmp_dir=pathlib.Path(tmpdir),
                                  quality=7,
                                  timesteps=timesteps,
                                  quantiles=(0.1, 0.99))

    assert th.origin == true_origin
    assert th.frame_shape == true_fov

    read_data = imageio.mimread(th.video_path)
    assert len(read_data) == n_t

    base_fname = example_video_path

    th = thumbnail_video_from_ROI(pathlib.Path(base_fname),
                                  roi,
                                  padding=padding,
                                  roi_color=(0, 255, 0),
                                  file_path=None,
                                  tmp_dir=pathlib.Path(tmpdir),
                                  quality=7,
                                  timesteps=timesteps,
                                  quantiles=(0.01, 0.99))

    assert th.origin == true_origin
    assert th.frame_shape == true_fov

    read_data = imageio.mimread(th.video_path)
    assert len(read_data) == n_t


@pytest.mark.parametrize('factor', [3, 4, 5])
def test_upscale_video_frame(factor):
    rng = np.random.RandomState(88123)
    raw_data = rng.randint(0, 256, (100, 14, 17), dtype=np.uint8)
    new_data = upscale_video_frame(raw_data, factor)
    assert new_data.shape == (100, factor*14, factor*17)
    assert new_data.dtype == raw_data.dtype

    # brute force check that pixels were all correctly copied
    for ii in range(14):
        for ii1 in range(factor*ii, factor*(ii+1)):
            for jj in range(17):
                expected = raw_data[:, ii, jj]
                for jj1 in range(factor*jj, factor*(jj+1)):
                    actual = new_data[:, ii1, jj1]
                    np.testing.assert_array_equal(expected, actual)

    # now try on data with a color axis
    raw_data = rng.randint(0, 256, (100, 14, 17, 5), dtype=np.uint8)
    new_data = upscale_video_frame(raw_data, factor)
    assert new_data.shape == (100, factor*14, factor*17, 5)
    assert new_data.dtype == raw_data.dtype

    # brute force check that pixels were all correctly copied
    for color in range(5):
        for ii in range(14):
            for ii1 in range(factor*ii, factor*(ii+1)):
                for jj in range(17):
                    expected = raw_data[:, ii, jj, color]
                    for jj1 in range(factor*jj, factor*(jj+1)):
                        actual = new_data[:, ii1, jj1, color]
                        np.testing.assert_array_equal(expected, actual)


@pytest.mark.parametrize('padding, y0, x0, height, width',
                         [(5, 3, 2, 10, 12),
                          (10, 3, 2, 10, 12),
                          (5, 50, 50, 11, 23),
                          (10, 50, 50, 11, 23),
                          (5, 118, 50, 10, 12),
                          (10, 118, 50, 10, 12),
                          (5, 50, 118, 12, 10),
                          (10, 50, 118, 12, 10),
                          (5, 3, 50, 12, 13),
                          (10, 50, 3, 12, 13),
                          (5, 118, 118, 10, 10),
                          (10, 118, 118, 10, 10)])
def test_video_bounds_from_ROI(padding, x0, y0, height, width):

    roi = ExtractROI(x=x0, y=y0, height=height, width=width)
    x1 = x0 + width
    y1 = y0 + height

    origin, fov = video_bounds_from_ROI(roi, (128, 128), padding)
    assert fov[0] % 16 == 0
    assert fov[1] % 16 == 0
    assert fov[0] == fov[1]  # only considering cases that can give squares
    assert fov[0] >= max(height+2*padding, width+2*padding)
    assert origin[0] <= y0
    assert origin[1] <= x0
    assert origin[0]+fov[0] >= y1
    assert origin[1]+fov[1] >= x1
    assert origin[0] >= 0
    assert origin[1] >= 0
    assert origin[0]+fov[0] <= 128
    assert origin[1]+fov[1] <= 128


@pytest.mark.parametrize(
         'to_use, normalization, geometry',
         product(('chunked', 'unchunked'),
                 ({'quantiles': None, 'min_max': (10, 5000)},
                  {'quantiles': (0.1, 0.9), 'min_max': None},
                  {'quantiles': None, 'min_max': None},
                  {'quantiles': (0.1, 0.9), 'min_max': (10, 5000)}),
                 ({'origin': (0, 0), 'frame_shape': None},
                  {'origin': (5, 5), 'frame_shape': (3, 3)})))
def test_read_and_scale_all_at_once(chunked_video_path,
                                    unchunked_video_path,
                                    to_use,
                                    normalization,
                                    geometry):
    if to_use == 'chunked':
        video_path = chunked_video_path
    elif to_use == 'unchunked':
        video_path = unchunked_video_path
    else:
        raise RuntimeError(f'bad to_use value: {to_use}')

    if normalization['quantiles'] is None and normalization['min_max'] is None:
        with pytest.raises(RuntimeError,
                           match='must specify either quantiles'):

            actual = _read_and_scale_all_at_once(
                        video_path,
                        geometry['origin'],
                        geometry['frame_shape'],
                        quantiles=normalization['quantiles'],
                        min_max=normalization['min_max'])
        return

    elif (normalization['quantiles'] is not None
          and normalization['min_max'] is not None):

        with pytest.raises(RuntimeError,
                           match='cannot specify both quantiles'):

            actual = _read_and_scale_all_at_once(
                        video_path,
                        geometry['origin'],
                        geometry['frame_shape'],
                        quantiles=normalization['quantiles'],
                        min_max=normalization['min_max'])
        return

    with h5py.File(video_path, 'r') as in_file:
        full_data = in_file['data'][()]
        if normalization['quantiles'] is not None:
            min_max = np.quantile(full_data, normalization['quantiles'])
        else:
            min_max = normalization['min_max']

    if geometry['frame_shape'] is None:
        frame_shape = full_data.shape[1:3]
    else:
        frame_shape = geometry['frame_shape']

    r0 = geometry['origin'][0]
    r1 = r0+frame_shape[0]
    c0 = geometry['origin'][1]
    c1 = c0+frame_shape[1]
    full_data = full_data[:, r0:r1, c0:c1]
    expected = scale_video_to_uint8(full_data, min_max[0], min_max[1])

    actual = _read_and_scale_all_at_once(
                    video_path,
                    geometry['origin'],
                    frame_shape,
                    quantiles=normalization['quantiles'],
                    min_max=normalization['min_max'])

    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize(
         'to_use, normalization, geometry',
         product(('chunked', 'unchunked'),
                 ({'quantiles': None, 'min_max': (10, 5000)},
                  {'quantiles': (0.1, 0.9), 'min_max': None},
                  {'quantiles': None, 'min_max': None},
                  {'quantiles': (0.1, 0.9), 'min_max': (10, 5000)}),
                 ({'origin': (0, 0), 'frame_shape': None},
                  {'origin': (5, 5), 'frame_shape': (3, 3)})))
def test_read_and_scale_by_chunks(chunked_video_path,
                                  unchunked_video_path,
                                  to_use,
                                  normalization,
                                  geometry):
    if to_use == 'chunked':
        video_path = chunked_video_path
    elif to_use == 'unchunked':
        video_path = unchunked_video_path

    if normalization['quantiles'] is None and normalization['min_max'] is None:
        with pytest.raises(RuntimeError,
                           match='must specify either quantiles'):

            actual = _read_and_scale_by_chunks(
                        video_path,
                        geometry['origin'],
                        geometry['frame_shape'],
                        quantiles=normalization['quantiles'],
                        min_max=normalization['min_max'])
        return

    elif (normalization['quantiles'] is not None
          and normalization['min_max'] is not None):

        with pytest.raises(RuntimeError,
                           match='cannot specify both quantiles'):

            actual = _read_and_scale_by_chunks(
                        video_path,
                        geometry['origin'],
                        geometry['frame_shape'],
                        quantiles=normalization['quantiles'],
                        min_max=normalization['min_max'])
        return

    with h5py.File(video_path, 'r') as in_file:
        full_data = in_file['data'][()]
        if normalization['quantiles'] is not None:
            min_max = np.quantile(full_data, normalization['quantiles'])
        else:
            min_max = normalization['min_max']

    if geometry['frame_shape'] is None:
        frame_shape = full_data.shape[1:3]
    else:
        frame_shape = geometry['frame_shape']

    r0 = geometry['origin'][0]
    r1 = r0+frame_shape[0]
    c0 = geometry['origin'][1]
    c1 = c0+frame_shape[1]
    full_data = full_data[:, r0:r1, c0:c1]
    expected = scale_video_to_uint8(full_data, min_max[0], min_max[1])

    actual = _read_and_scale_by_chunks(
                    video_path,
                    geometry['origin'],
                    frame_shape,
                    quantiles=normalization['quantiles'],
                    min_max=normalization['min_max'])

    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize(
         'to_use, normalization, geometry',
         product(('chunked', 'unchunked'),
                 ({'quantiles': None, 'min_max': (10, 5000)},
                  {'quantiles': (0.1, 0.9), 'min_max': None},
                  {'quantiles': None, 'min_max': None},
                  {'quantiles': (0.1, 0.9), 'min_max': (10, 5000)}),
                 ({'origin': (0, 0), 'frame_shape': None},
                  {'origin': (5, 5), 'frame_shape': (3, 3)})))
def test_read_and_scale(chunked_video_path,
                        unchunked_video_path,
                        to_use,
                        normalization,
                        geometry):
    if to_use == 'chunked':
        video_path = chunked_video_path
    elif to_use == 'unchunked':
        video_path = unchunked_video_path
    else:
        raise RuntimeError(f'bad to_use value: {to_use}')

    if normalization['quantiles'] is None and normalization['min_max'] is None:
        with pytest.raises(RuntimeError,
                           match='must specify either quantiles'):

            actual = read_and_scale(
                        video_path,
                        geometry['origin'],
                        geometry['frame_shape'],
                        quantiles=normalization['quantiles'],
                        min_max=normalization['min_max'])
        return

    elif (normalization['quantiles'] is not None
          and normalization['min_max'] is not None):

        with pytest.raises(RuntimeError,
                           match='cannot specify both quantiles'):

            actual = read_and_scale(
                        video_path,
                        geometry['origin'],
                        geometry['frame_shape'],
                        quantiles=normalization['quantiles'],
                        min_max=normalization['min_max'])
        return

    with h5py.File(video_path, 'r') as in_file:
        full_data = in_file['data'][()]
        if normalization['quantiles'] is not None:
            min_max = np.quantile(full_data, normalization['quantiles'])
        else:
            min_max = normalization['min_max']

    if geometry['frame_shape'] is None:
        frame_shape = full_data.shape[1:3]
    else:
        frame_shape = geometry['frame_shape']

    r0 = geometry['origin'][0]
    r1 = r0+frame_shape[0]
    c0 = geometry['origin'][1]
    c1 = c0+frame_shape[1]
    full_data = full_data[:, r0:r1, c0:c1]
    expected = scale_video_to_uint8(full_data, min_max[0], min_max[1])

    actual = read_and_scale(
                    video_path,
                    geometry['origin'],
                    frame_shape,
                    quantiles=normalization['quantiles'],
                    min_max=normalization['min_max'])

    np.testing.assert_array_equal(actual, expected)