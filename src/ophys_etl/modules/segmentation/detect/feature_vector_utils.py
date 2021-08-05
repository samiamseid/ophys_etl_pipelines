from typing import Set, Tuple, Optional
from itertools import product
import numpy as np


def flux_partition(
        image_data: np.ndarray,
        seed_pt: Tuple[int, int],
        threshold: float=0.8) -> np.ndarray:
    """
    Select all of the pixels in an image that are connected
    to seed_pt with fluxes greater than or equal to
    threshold*image_data[seed_pt]

    resulting mask is True at valid pixels
    """
    checked_pixels = set()
    pixels_to_check = set()
    for dr, dc in product(range(-1, 2), range(-1, 2)):
        if dr == 0 and dc == 0:
            continue
        r = seed_pt[0]+dr
        c = seed_pt[1]+dc
        if r < 0:
            continue
        elif r >= image_data.shape[0]:
            continue
        elif c < 0:
            continue
        elif c >= image_data.shape[1]:
            continue
        pixels_to_check.add((r, c))
    seed_flux = image_data[seed_pt[0], seed_pt[1]]

    valid_pixels = np.zeros(image_data.shape, dtype=bool)
    n_total_pixels = image_data.shape[0]*image_data.shape[1]
    while len(pixels_to_check) > 0:
        if len(checked_pixels) >= n_total_pixels:
            break

        pixel = pixels_to_check.pop()
        checked_pixels.add(pixel)
        if image_data[pixel[0], pixel[1]] >= threshold*seed_flux:
            valid_pixels[pixel[0], pixel[1]] = True
        for dr, dc in product(range(-1, 2), range(-1, 2)):
            r = pixel[0] + dr
            c = pixel[1] + dc
            if dr == 0 and dc == 0:
                continue
            elif r < 0:
                continue
            elif r >= image_data.shape[0]:
                continue
            elif c < 0:
                continue
            elif c >= image_data.shape[1]:
                continue

            p2 = (r, c)
            if p2 in pixels_to_check:
                continue
            elif p2 in checked_pixels:
                continue
            pixels_to_check.add(p2)

    return valid_pixels


def choose_timesteps(
            sub_video: np.ndarray,
            seed_pt: Tuple[int, int],
            filter_fraction: float,
            image_data: np.ndarray,
            pixel_ignore: Optional[np.ndarray] = None) -> np.ndarray:
    """
    Choose the timesteps to use for a given seed when calculating Pearson
    correlation coefficients.

    Parameters
    ----------
    sub_video: np.ndarray
        A subset of a video to be correlated.
        Shape is (n_time, n_row, n_cols)

    seed_pt: Tuple[int, int]
        The seed point around which to build the ROI

    filter_fraction: float
        The fraction of brightest timesteps to be used in calculating
        the Pearson correlation between pixels

    image_data: np.ndarray
        The metric image being used for seeding

    pixel_ignore: Optional[np.ndarray]:
        An 1-D array of booleans marked True at any pixels
        that should be ignored, presumably because they have already been
        selected as ROI pixels. (default: None)

    Returns
    -------
    global_mask: np.ndarray
        Array of timesteps (ints) to be used for calculating correlations
    """
    if pixel_ignore is not None:
        if pixel_ignore.shape != image_data.shape:
            msg = f"pixel_ignore.shape {pixel_ignore.shape}\n"
            msg += f"image_data.shape {image_data.shape}\n"
            msg += "These should be the same"
            raise RuntimeError(msg)

    # fraction of timesteps to discard
    discard = 1.0-filter_fraction

    # start assembling mask in timesteps
    trace = sub_video[:, seed_pt[0], seed_pt[1]]
    thresh = np.quantile(trace, discard)
    global_mask = []
    mask = np.where(trace >= thresh)[0]
    global_mask.append(mask)

    # now select the pixels that are closest to
    # image_data.max() - sigma and
    # image_data.max() - 2*sigma

    image_flat = image_data.flatten()
    if pixel_ignore is not None:
        pixel_ignore_flat = pixel_ignore.flatten()
    else:
        pixel_ignore_flat = np.zeros(len(image_flat), dtype=bool)


    """
    flux_based_pixels = flux_partition(
                           image_data,
                           seed_pt,
                           threshold=0.5)

    print('flux based ',flux_based_pixels.sum(),
    image_data.shape[0]*image_data.shape[1])
    if flux_based_pixels.sum() > 0:
        flux_based_pixels = flux_based_pixels.flatten()
        pixel_ignore_flat = np.logical_or(pixel_ignore_flat,
                                          np.logical_not(flux_based_pixels))
    """

    pixel_indices = np.arange(len(image_flat), dtype=int)
    valid_pixels = np.logical_not(pixel_ignore_flat)
    image_flat = image_flat[valid_pixels]
    pixel_indices = pixel_indices[valid_pixels]

    image_max = image_flat.max()
    t25, t75 = np.quantile(image_flat, (0.25, 0.75))
    std = (t75-t25)/1.34896

    for ds in (-1, -2):
        val = image_max + ds*std
        ii = pixel_indices[np.argmin(np.abs(val-image_flat))]
        pt = np.unravel_index(ii, sub_video.shape[1:])
        trace = sub_video[:, pt[0], pt[1]]
        thresh = np.quantile(trace, discard)
        mask = np.where(trace >= thresh)[0]
        global_mask.append(mask)

    return np.unique(np.concatenate(global_mask))


def select_window_size(
        seed_pt: Tuple[int, int],
        image_data: np.ndarray,
        target_z_score: float = 2.0,
        window_min: int = 20,
        window_max: int = 300,
        pixel_ignore: Optional[np.ndarray] = None) -> int:
    """
    For an image, find the window half side length
    centered on a pixel such that the specified pixel
    is at least N-sigma brighter than the distribution
    of pixels the image.

    Parameters
    ----------
    seed_pt: Tuple[int, int]
        (row, col) of the specified point

    image_data: np.ndarray

    target_z_score: float
        The target z-score of seed_pt within image
        (default=2.0)

    window_min: int
        Minimum window size to return (default=20)

    window_max: int
        Maximum window size to return. If the window size
        exceeds this limit without matching or exceeding
        target_z_score, return the last window size tested.
        (default=300)

    pixel_ignore: Optional[np.ndarray]
        A mask marked as True for pixels that are to be
        ignored when calculating the z-score of seed_pt

    Returns
    -------
    window: int
        The half side length (in pixels) of a window
        centered on seed_pt such that seed_pt is at
        least target_z_score standard deviations
        brighter than all of the pixels in the window

    Notes
    -----
    This method starts with a window size of window_min.
    It grows the window by a factor for 3/2 at each step.
    It returns the first value of window that either
    exceeds window_max or results in a z-score exceeding
    target_z_score.
    """
    if pixel_ignore is not None:
        if pixel_ignore.shape != image_data.shape:
            msg = f"pixel_ignore.shape {pixel_ignore.shape}\n"
            msg += f"image_data.shape {image_data.shape}\n"
            msg += "These should be the same"
            raise RuntimeError(msg)
        pixel_use = np.logical_not(pixel_ignore)
    else:
        pixel_use = np.ones(image_data.shape, dtype=bool)

    window = None
    seed_flux = image_data[seed_pt[0], seed_pt[1]]
    z_score = 0.0
    while z_score < target_z_score:
        if window is None:
            window = window_min
        else:
            window = max(3*window//2, window+1)
        r0 = max(0, seed_pt[0]-window)
        r1 = min(image_data.shape[0], seed_pt[0]+window+1)
        c0 = max(0, seed_pt[1]-window)
        c1 = min(image_data.shape[1], seed_pt[1]+window+1)
        local_mask = pixel_use[r0:r1, c0:c1]
        local_image = image_data[r0:r1, c0:c1]
        background = local_image[local_mask].flatten()
        mu = np.mean(background)
        q25, q75 = np.quantile(background, (0.25, 0.75))
        std = max(1.0e-10, (q75-q25)/1.34896)
        z_score = (seed_flux-mu)/std
        if window >= window_max:
            break
        if r0 == 0 and r1 == image_data.shape[0]:
            if c0 == 0 and c1 == image_data.shape[1]:
                break
    return window
