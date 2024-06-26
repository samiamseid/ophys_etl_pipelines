import numpy as np
import ophys_etl.modules.decrosstalk.decrosstalk_types as dc_types
import ophys_etl.modules.decrosstalk.decrosstalk as decrosstalk


def test_rolling_mean_and_std():

    rng = np.random.RandomState(11123)
    data = rng.random_sample(100)
    mask = np.ones(100, dtype=bool)
    mean, std = decrosstalk._centered_rolling_mean(data, mask, window=10)
    for ii in range(100):
        i0 = ii-5
        if i0 < 0:
            i0 = 0
        i1 = i0+10
        if i1 > 100:
            i1 = 100
            i0 = 90
        m = np.mean(data[i0:i1])
        s = np.std(data[i0:i1], ddof=1)
        assert abs(m-mean[ii]) < 1.0e-10
        assert abs(s-std[ii]) < 1.0e-10

    mask[0:100:2] = False
    mean, std = decrosstalk._centered_rolling_mean(data, mask, window=10)
    for ii in range(100):
        i0 = ii-5
        if i0 < 0:
            i0 = 0
        i1 = i0+10
        if i1 > 100:
            i1 = 100
            i0 = 90
        if i0 % 2 == 0:
            m = np.mean(data[i0+1:i1:2])
            s = np.std(data[i0+1:i1:2], ddof=1)
        else:
            m = np.mean(data[i0:i1:2])
            s = np.std(data[i0:i1:2], ddof=1)

        assert abs(m-mean[ii]) < 1.0e-10
        assert abs(s-std[ii]) < 1.0e-10

    mask = np.ones(100, dtype=bool)
    idx = np.unique(rng.randint(0, 100, 20))
    mask[idx] = False

    mean, std = decrosstalk._centered_rolling_mean(data, mask, window=10)
    for ii in range(100):
        i0 = ii-5
        if i0 < 0:
            i0 = 0
        i1 = i0+10
        if i1 > 100:
            i1 = 100
            i0 = 90
        d = data[i0:i1][mask[i0:i1]]
        m = np.mean(d)
        s = np.std(d, ddof=1)

        assert abs(m-mean[ii]) < 1.0e-10
        assert abs(s-std[ii]) < 1.0e-10

    # test case where stretch of `False` in mask is longer than window
    edge_case_exercised = False
    mask[50:75] = False
    mean, std = decrosstalk._centered_rolling_mean(data, mask, window=10)
    for ii in range(100):
        i0 = ii-5
        if i0 < 0:
            i0 = 0
        i1 = i0+10
        if i1 > 100:
            i1 = 100
            i0 = 90
        sub_mask = mask[i0:i1]
        d = data[i0:i1][sub_mask]

        if sub_mask.sum() < 2:
            edge_case_exercised = True
            if sub_mask.sum() == 0:
                assert np.isnan(mean[ii])
            else:
                assert abs(mean[ii] - np.mean(d)) < 1.0e-10
            assert np.isnan(std[ii])
        else:
            m = np.mean(d)
            s = np.std(d, ddof=1)
            assert abs(m-mean[ii]) < 1.0e-10
            assert abs(s-std[ii]) < 1.0e-10

    assert edge_case_exercised


def test_clean_negative_traces():

    rng = np.random.RandomState(88123)
    input_data = dc_types.ROISetDict()
    trace = rng.normal(7.0, 0.2, size=1000)
    trace[77] = 15.0
    trace[99] = 1.0
    roi = dc_types.ROIChannels()
    roi['signal'] = trace
    roi['crosstalk'] = rng.random_sample(1000)
    input_data['roi'][0] = roi

    trace = rng.normal(11.0, 0.2, size=1000)
    trace[44] = 22.0
    trace[88] = 1.0
    roi = dc_types.ROIChannels()
    roi['signal'] = trace
    roi['crosstalk'] = rng.random_sample(1000)
    input_data['neuropil'][0] = roi

    # make sure traces with NaNs are left untouched
    nan_trace = rng.normal(7.0, 0.2, size=1000)
    nan_trace[11] = 17.0
    nan_trace[33] = -1.0
    nan_trace[44] = np.NaN
    roi = dc_types.ROIChannels()
    roi['signal'] = nan_trace
    roi['crosstalk'] = rng.random_sample(1000)
    input_data['roi'][1] = roi

    cleaned = decrosstalk.clean_negative_traces(input_data)
    assert cleaned['roi'][0]['signal'].min() > 6.0
    assert cleaned['roi'][0]['signal'].min() < 7.0
    assert abs(cleaned['roi'][0]['signal'][77] - 15.0) < 0.001
    assert not np.isnan(cleaned['roi'][0]['signal']).any()

    np.testing.assert_array_equal(cleaned['roi'][1]['signal'], nan_trace)

    assert cleaned['neuropil'][0]['signal'].min() > 10.0
    assert cleaned['neuropil'][0]['signal'].min() < 11.0
    assert abs(cleaned['neuropil'][0]['signal'][44] - 22.0) < 0.001
