import os
import tempfile
import h5py
import numpy as np
import ophys_etl.decrosstalk.ophys_plane as ophys_plane


def _create_ophys_test_data(tmp_filename):
    """
    Write synthetic movie data to tmp_filename
    """

    movie_data = np.zeros((200, 100, 100), dtype=float)


    movie_data[33:58, 10:15,25:30] = 2.0
    movie_data[22:73, 80:90, 54:64] = 3.0

    with h5py.File(tmp_filename, mode='w') as out_file:
        out_file.create_dataset('data', data=movie_data)


def _run_ophys_movie_test(tmp_filename):
    """
    Write synthetic movie data to tmp_filename.
    Use the synthetic movie to crate an OphysMovie.
    Attempt to extract traces from the known ROIs in that movie.
    """
    motion_border = {'x0': 5.0, 'x1':5.0,
                     'y0':5.0, 'y1':5.0}

    _create_ophys_test_data(tmp_filename)

    ophys_movie = ophys_plane.OphysMovie(tmp_filename, motion_border)

    roi_list = []
    roi = ophys_plane.OphysROI(roi_id=0,
                               x0=25, y0=10,
                               width=5, height=5,
                               valid_roi=True,
                               mask_matrix = list([[True]*5 for ii in range(5)]))

    roi_list.append(roi)

    roi = ophys_plane.OphysROI(roi_id=1,
                               x0=54, y0=80,
                               width=10, height=5,
                               valid_roi=True,
                               mask_matrix = list([[True]*5 for ii in range(10)]))

    roi_list.append(roi)

    roi = ophys_plane.OphysROI(roi_id=2,
                               x0=45, y0=60,
                               width=10, height=5,
                               valid_roi=True,
                               mask_matrix = list([[True]*5 for ii in range(10)]))

    roi_list.append(roi)

    trace_output = ophys_movie.get_trace(roi_list)

    assert len(trace_output['roi']) == 3
    assert trace_output['roi'][0].shape == (200,)
    assert trace_output['roi'][1].shape == (200,)
    assert trace_output['roi'][2].shape == (200,)
    np.testing.assert_array_equal(trace_output['roi'][0][33:58], 2.0*np.ones(25, dtype=float))
    np.testing.assert_array_equal(trace_output['roi'][0][:33], np.zeros(33, dtype=float))
    np.testing.assert_array_equal(trace_output['roi'][0][58:], np.zeros(142, dtype=float))

    np.testing.assert_array_equal(trace_output['roi'][1][22:73], 3.0*np.ones(51, dtype=float))
    np.testing.assert_array_equal(trace_output['roi'][1][:22], np.zeros(22, dtype=float))
    np.testing.assert_array_equal(trace_output['roi'][1][73:], np.zeros(127, dtype=float))

    np.testing.assert_array_equal(trace_output['roi'][2], np.zeros(200, dtype=float))

def test_ophys_movie():
    this_dir = os.path.dirname(os.path.abspath(__file__))
    tmp_dir = os.path.join(this_dir, 'tmp')
    assert os.path.isdir(tmp_dir)
    tmp_filename = tempfile.mkstemp(prefix='ophys_movie_filename',
                                    suffix='.h5',
                                    dir=tmp_dir)[1]

    try:
        _run_ophys_movie_test(tmp_filename)
    except:
        raise
    finally:
        if os.path.exists(tmp_filename):
            os.unlink(tmp_filename)
