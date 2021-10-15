"""
This module contains utility functions that roi_merging.py uses
to calculate correlations between pixels in ROIs
"""
from typing import Union, List, Tuple
import numpy as np
import multiprocessing
import multiprocessing.managers
from ophys_etl.modules.segmentation.utils.multiprocessing_utils import (
    _winnow_process_list)


def _weights_to_series(sub_video: np.ndarray,
                       weights: np.ndarray) -> np.ndarray:
    """
    Given a sub_video and an array of weights,
    compute the weighted sum of pixel time series to get
    a time series characterizing the sub_video.

    Parameters
    ----------
    sub_video: np.ndarray
        Flattened in space so that the shape is (ntime, npixels)

    weights: np.ndarray
        An array of weights that will be used to
        sum the time series from sub_video to create the
        characteristic timeseries. Shape is (npixels, )

    Returns
    -------
    timeseries: np.ndarray
       A single time series characterizing the whole sub_video

    Notes
    -----
    This algorithm will renormalize weights by subtracting off
    the median, setting any weights that are < 0 to 0 (thus
    discarding anything below the median), subtracting the
    resulting minimum, and dividing by the resulting maximum.
    If, for some reason, this results in an array of zeros
    (because, for instance, all weights were the same), the
    algorithm will reset weights to an array of ones so that all
    pixels are weighted equally.
    """
    if len(weights) == 1:
        return sub_video[:, 0]

    # only accept pixels brigter than the median
    th = np.median(weights)
    weights -= th
    mask = (weights < 0)
    weights[mask] = 0.0

    weights -= weights.min()
    norm = weights.max()
    if norm < 1.0e-10:
        weights = np.ones(weights.shape, dtype=float)
        # re-apply mask on weights that were
        # originally negative
        weights[mask] = 0.0
    else:
        weights = weights/norm

    if weights.sum() < 1.0e-10:
        # probably because all weights had
        # the same value, so filtering on the median
        # gave an array of zeros
        weights = np.ones(weights.shape, dtype=float)

    timeseries = np.dot(sub_video, weights)
    return timeseries/weights.sum()


def correlate_sub_video(sub_video: np.ndarray,
                        timeseries: np.ndarray,
                        filter_fraction: float = 0.2) -> np.ndarray:
    """
    Correlated all of the pixels in a sub_video against
    a provided time series using only the brightest N
    timesteps

    Parameters
    ----------
    sub_video: np.ndarray
        Shape is (ntime, npixels)

    timeseries: np.ndarray
        Shape is (ntime,)
        This is the time series against which to correlate
        the pixels in sub_video

    filter_fraction: float
        Keep the brightest filter_fraction timesteps when doing
        the correlation (this is reckoned by the flux values in
        timeseries)

    Returns
    -------
    corr: np.ndarray
        Shape is (npix,)
        These are the correlation values of the pixels in
        sub_video against timeseries
    """
    discard = 1.0-filter_fraction
    th = np.quantile(timeseries, discard)
    mask = (timeseries >= th)
    sub_video = sub_video[mask, :]
    timeseries = timeseries[mask]
    ntime = len(timeseries)

    t_mu = np.mean(timeseries)
    vid_mu = np.mean(sub_video, axis=0)
    t_var = np.mean((timeseries-t_mu)**2)
    sub_vid_minus_mu = sub_video-vid_mu
    sub_var = np.mean(sub_vid_minus_mu**2, axis=0)

    numerator = np.dot((timeseries-t_mu), sub_vid_minus_mu)/ntime

    corr = numerator/np.sqrt(sub_var*t_var)
    return corr


def _self_correlate(sub_video: np.ndarray,
                    i_pixel: int,
                    filter_fraction: float = 0.2) -> float:
    """
    Correlate one pixel in a sub video against all other pixels in
    that sub video. Return the sum of the correlation coefficients

    Parameters
    ----------
    sub_video: np.ndarray
        Flattened in space so that the shape is (ntime, npixels)

    i_pixel: int
        The index of the pixel being correlated

    filter_fraction: float
        The fraction of timesteps (chosen to be the brightest) to
        keep when doing the correlation (default=0.2)

    Returns
    -------
    corr: float
        The sum of the Pearson correlation coefficients relating each
        other pixel to i_pixel. When comparing each pair of pixels,
        use the union of the (1-filter_fraction) brightest
        timesteps for i_pixel and the (1-filter_fraction) brightest
        timesteps for the other pixel
    """
    discard = 1.0-filter_fraction
    th = np.quantile(sub_video[:, i_pixel], discard)
    this_mask = (sub_video[:, i_pixel] >= th)
    this_pixel = sub_video[:, i_pixel]
    corr = np.zeros(sub_video.shape[1], dtype=float)
    for j_pixel in range(len(corr)):
        other = sub_video[:, j_pixel]
        th = np.quantile(other, discard)
        other_mask = (other >= th)
        mask = np.logical_or(other_mask, this_mask)
        masked_this = this_pixel[mask]
        masked_other = other[mask]
        this_mu = np.mean(masked_this)
        other_mu = np.mean(masked_other)
        this_var = np.var(masked_this, ddof=1)
        other_var = np.var(masked_other, ddof=1)
        num = np.mean((masked_this-this_mu)*(masked_other-other_mu))
        corr[j_pixel] = num/np.sqrt(this_var*other_var)

    return np.sum(corr)


def _correlate_batch(
        pixel_list: Union[List[int], np.ndarray],
        sub_video: np.ndarray,
        output_dict: Union[dict, multiprocessing.managers.DictProxy],
        filter_fraction: float = 0.2) -> None:
    """
    Run _self_correlate on a batch of pixels (for use when processing
    a sub_video with multiprocessing)

    Parameters
    ----------
    pixel_list: Union[List[int], np.ndarray]
        List or array of pixel indices to be passed to _self_correlate
        as i_pixel

    sub_video: np.ndarray
        Flattened in space so that the shape is (ntime, npixels)

    output_dict: Union[dict, multiprocessing.managers.DictProxy]
        Dict where output will be kept. Keys are the values in
        pixel_list; values are the results of calling _self_correlate.

    filter_fraction: float
        The fraction of timesteps (chosen to be the brightest) to
        keep when doing the correlation (default=0.2)

    Returns
    -------
    None
    """
    for ipix in pixel_list:
        value = _self_correlate(sub_video,
                                ipix,
                                filter_fraction=filter_fraction)
        output_dict[ipix] = value
    return None


def get_characteristic_timeseries_parallel(
        sub_video: np.ndarray,
        n_processors: int = 8,
        filter_fraction: float = 0.2) -> np.ndarray:
    """
    A variant of get_characteristic_timeseries to be called when the
    spatial extent of the sub video is so large that it makes
    more sense to parallelize on a per-pixel level than a
    per-ROI level

    Parameters
    ----------
    sub_video: np.ndarray
        A sub_video characterizing the ROI. It has been flattened
        in space such that the shape is (ntime, npixels)

    n_processors: int
        Number of processors to use (default=8)

    filter_fraction: float
        The fraction of timesteps (chosen to be the brightest) to
        keep when doing the correlation (default=0.2)

    Returns
    -------
    characteristic_timeseries: np.ndarray
        Time series characterizing the full sub_video

    Notes
    -----
    This function returns a weighted average of all of the
    time series in the ROI. The weights are computed by
    calling _self_correlate on every pixel in the sub_video
    and then using these weights to compute a single time
    series by calling _weights_to_series.
    """
    npix = sub_video.shape[1]
    chunksize = max(1, npix//(n_processors-1))
    mgr = multiprocessing.Manager()
    output_dict = mgr.dict()
    pix_list = list(range(npix))
    process_list = []
    for i0 in range(0, npix, chunksize):
        chunk = pix_list[i0:i0+chunksize]
        args = (chunk, sub_video, output_dict)
        kwargs = {'filter_fraction': filter_fraction}
        p = multiprocessing.Process(target=_correlate_batch,
                                    args=args,
                                    kwargs=kwargs)
        p.start()
        process_list.append(p)
        while len(process_list) > 0 and len(process_list) >= (n_processors-1):
            process_list = _winnow_process_list(process_list)
    for p in process_list:
        p.join()

    weights = np.zeros(npix, dtype=float)
    for ipix in range(npix):
        weights[ipix] = output_dict[ipix]

    return _weights_to_series(sub_video, weights)


def get_characteristic_timeseries(
        sub_video: np.ndarray,
        filter_fraction: float = 0.2) -> np.ndarray:
    """
    Return a single time series that can characterize an
    entire ROI

    Parameters
    ----------
    sub_video: np.ndarray
        A sub_video characterizing the ROI. It has been flattened
        in space such that the shape is (ntime, npixels)

    filter_fraction: float
        The fraction of timesteps (chosen to be the brightest) to
        keep when doing the correlation (default=0.2)

    Returns
    -------
    characteristic_pixel: np.ndarray
        Time series of taken from the video at the
        brightest pixel in the ROI

    Notes
    -----
    This function returns a weighted average of all of the
    time series in the ROI. The weights are computed by
    calling _self_correlate on every pixel in the sub_video
    and then using these weights to compute a single time
    series by calling _weights_to_series.
    """
    npix = sub_video.shape[1]
    weights = np.zeros(npix, dtype=float)
    for ipix in range(npix):
        weights[ipix] = _self_correlate(
                                     sub_video,
                                     ipix,
                                     filter_fraction=filter_fraction)

    return _weights_to_series(sub_video, weights)


def calculate_merger_metric(distribution_params: Tuple[float, float],
                            distribution_centroid: np.ndarray,
                            roi_video: np.ndarray,
                            filter_fraction: float = 0.2) -> float:
    """
    Calculate the merger metric between two ROIs by correlating
    the pixels in one ROI to the characteristic timescale of the other
    ROI and comparing the distribution of values to a fiducial Gaussian
    pre-computed by correlating the pixels in the reference ROI to
    its own characteristic time series.

    Parameters
    ----------
    distribution_params: Tuple[float, float]
        (mu, std) -- the mean and standard deviation of the fiducial
        Gaussian distribution

    distribution_centroid: np.ndarray
        The characteristic timeseries (calculated with
        get_characteristic_timeseries) of the ROI used to
        create the fiducial Gaussian distribution

    roi_video: np.ndarray
        The sub-video corresponding to the other (non-fiducial) ROI,
        flattened in space so that its shape is (ntime, npixels)

    filter_fraction: float
        The fraction of time steps to keep when doing
        time correlation (default = 0.2)

    Returns
    -------
    metric: float
        The median z-score of the correlation of the non-fiducial
        ROI's pixels to distribution_centroid relative to the Gaussian
        distribution specified by distribution_params.
    """

    mu = distribution_params[0]
    std = distribution_params[1]

    roi1_to_roi0 = correlate_sub_video(roi_video,
                                       distribution_centroid,
                                       filter_fraction=filter_fraction)

    z_score = (roi1_to_roi0-mu)/std
    metric = np.quantile(z_score, 0.5)
    return metric


def get_self_correlation(sub_video: np.ndarray,
                         characteristic_timeseries: np.ndarray,
                         filter_fraction: float) -> Tuple[float, float]:
    """
    Correlate all of the pixels in a sub_video with a specified time series.
    Return a tuple containing the mean and standard deviation of the
    resulting correlation values.

    Parameters
    ----------
    sub_video: np.ndarray
        A sub-video flattened in space so that it's shape is
        (ntime, npixels)

    characteristic_timeseries: np.ndarray
        The time series against which to correlate all of the pixels
        in the sub-video

    filter_fraction: float
        Fraction of timesteps to use when doing time correlation

    Returns
    -------
    distribution_parameters: Tuple[float, float]
        (mu, std) -- the mean and standard deviation of correlation
        coefficients between pixels in the sub_video and
        characteristic_timeseries

    Notes
    -----
    If there are fewer than two pixels in the sub-video, the
    Gaussian distribution implied by distribution_parameters will
    be meaningless. This function returns (0.0, 1.0) in that case.
    """
    if sub_video.shape[1] < 2:
        return (0.0, 1.0)

    corr = correlate_sub_video(sub_video,
                               characteristic_timeseries,
                               filter_fraction=filter_fraction)

    return (np.mean(corr), np.std(corr, ddof=1))