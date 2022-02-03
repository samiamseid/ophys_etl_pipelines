import copy
import json
import os
from pathlib import Path

import argschema
import h5py
import numpy as np
import pandas as pd
import tifffile
from PIL import Image

from ophys_etl.qc.registration_qc import RegistrationQC
from ophys_etl.modules.suite2p_wrapper.__main__ import Suite2PWrapper
from ophys_etl.modules.suite2p_registration import utils
from ophys_etl.modules.suite2p_registration.schemas import (
    Suite2PRegistrationInputSchema, Suite2PRegistrationOutputSchema)
from ophys_etl.modules.suite2p_registration.suite2p_utils import (
    load_initial_frames, compute_reference, optimize_motion_parameters)
from suite2p.registration.rigid import shift_frame


class Suite2PRegistration(argschema.ArgSchemaParser):
    default_schema = Suite2PRegistrationInputSchema
    default_output_schema = Suite2PRegistrationOutputSchema

    def run(self):
        self.logger.name = type(self).__name__
        self.logger.setLevel(self.args['log_level'])
        ophys_etl_commit_sha = os.environ.get("OPHYS_ETL_COMMIT_SHA",
                                              "local build")
        self.logger.info(f"OPHYS_ETL_COMMIT_SHA: {ophys_etl_commit_sha}")

        # Get suite2p args.
        suite2p_args = self.args['suite2p_args']

        if suite2p_args['force_refImg'] and len(suite2p_args['refImg']) == 0:
            # Use our own version of compute_reference to create the initial
            # reference image used by suite2p.
            self.logger.info(f'Loading {suite2p_args["nimg_init"]} frames '
                             'for reference image creation.')
            intial_frames = load_initial_frames(
                file_path=suite2p_args['h5py'],
                h5py_key=suite2p_args['h5py_key'],
                n_frames=suite2p_args['nimg_init'],)
            # Optimizing motion parameters is only available for nonrigid
            # settings.
            if self.args['do_optimize_motion_params'] and \
               not suite2p_args['nonrigid']:
                self.logger.info("Attempting to optimize registration "
                                 "parameters Using:")
                self.logger.info(
                    '\tsmooth_sigma range: '
                    f'{self.args["smooth_sigma_min"]} - '
                    f'{self.args["smooth_sigma_max"]}, '
                    f'steps: {self.args["smooth_sigma_steps"]}')
                self.logger.info(
                    '\tsmooth_sigma_time range: '
                    f'{self.args["smooth_sigma_time_min"]} - '
                    f'{self.args["smooth_sigma_time_max"]}, '
                    f'steps: {self.args["smooth_sigma_time_steps"]}')

                # Create linear spaced arrays for the range of smooth
                # parameters to try.
                smooth_sigmas = np.linspace(self.args['smooth_sigma_min'],
                                            self.args['smooth_sigma_max'],
                                            self.args['smooth_sigma_steps'])
                smooth_sigma_times = np.linspace(
                    self.args['smooth_sigma_time_min'],
                    self.args['smooth_sigma_time_max'],
                    self.args['smooth_sigma_time_steps'])

                optimize_result = optimize_motion_parameters(
                    intial_frames,
                    smooth_sigmas,
                    smooth_sigma_times,
                    suite2p_args,
                    self.logger.info)
                if self.args['use_ave_image_as_reference']:
                    suite2p_args['refImg'] = optimize_result['ave_image']
                else:
                    suite2p_args['refImg'] = optimize_result['ref_image']
                suite2p_args['smooth_sigma'] = optimize_result['smooth_sigma']
                suite2p_args['smooth_sigma_time'] = \
                    optimize_result['smooth_sigma_time']
            else:
                # Create the initial reference image and store it in the
                # suite2p_args dictionary. 8 iterations is the current default
                # in suite2p.
                self.logger.info('Creating custom reference image...')
                suite2p_args['refImg'] = compute_reference(
                    frames=intial_frames,
                    niter=self.args["max_reference_iterations"],
                    maxregshift=suite2p_args['maxregshift'],
                    smooth_sigma=suite2p_args['smooth_sigma'],
                    smooth_sigma_time=suite2p_args['smooth_sigma_time'],)

        # register with Suite2P
        self.logger.info("attempting to motion correct "
                         f"{suite2p_args['h5py']}")
        register = Suite2PWrapper(input_data=suite2p_args, args=[])
        register.run()

        # why does this logger assume the Suite2PWrapper name? reset
        self.logger.name = type(self).__name__

        # get paths to Suite2P outputs
        with open(suite2p_args["output_json"], "r") as f:
            outj = json.load(f)
        tif_paths = np.sort([Path(i) for i in outj['output_files']["*.tif"]])
        ops_path = Path(outj['output_files']['ops.npy'][0])

        # Suite2P ops file contains at least the following keys:
        # ["Lx", "Ly", "nframes", "xrange", "yrange", "xoff", "yoff",
        #  "corrXY", "meanImg"]
        ops = np.load(ops_path, allow_pickle=True)

        # identify and clip offset outliers
        detrend_size = int(self.args['movie_frame_rate_hz'] *
                           self.args['outlier_detrend_window'])
        xlimit = int(ops.item()['Lx'] * self.args['outlier_maxregshift'])
        ylimit = int(ops.item()['Ly'] * self.args['outlier_maxregshift'])
        self.logger.info("checking whether to clip where median-filtered "
                         "offsets exceed (x,y) limits of "
                         f"({xlimit},{ylimit}) [pixels]")
        delta_x, x_clipped = utils.identify_and_clip_outliers(
            np.array(ops.item()["xoff"]), detrend_size, xlimit)
        delta_y, y_clipped = utils.identify_and_clip_outliers(
            np.array(ops.item()["yoff"]), detrend_size, ylimit)
        clipped_indices = list(set(x_clipped).union(set(y_clipped)))
        self.logger.info(f"{len(x_clipped)} frames clipped in x")
        self.logger.info(f"{len(y_clipped)} frames clipped in y")
        self.logger.info(f"{len(clipped_indices)} frames will be adjusted "
                         "for clipping")

        # accumulate data from Suite2P's tiffs
        data = []
        for fname in tif_paths:
            with tifffile.TiffFile(fname) as f:
                nframes = len(f.pages)
                for i, page in enumerate(f.pages):
                    arr = page.asarray()
                    if i == 0:
                        data.append(
                            np.zeros((nframes, *arr.shape), dtype='int16'))
                    data[-1][i] = arr
        data = np.concatenate(data, axis=0)

        # Motion correction in suite2p should conserve flux and ordering
        # of the frames in the movie. This check makes sure the output
        # movie is correctly ordered and has it's flux preserved.
        if suite2p_args['nonrigid']:
            self.logger.info('nonrigid motion correction is enabled in '
                             'suite2p. Skipping test of corrected movie '
                             'against raw as nonrigid does not conserve '
                             'flux.')
        else:
            self.logger.info('Testing raw frames against motion corrected '
                             'frames.')
            utils.check_movie_against_raw(data,
                                          suite2p_args['h5py'],
                                          suite2p_args['h5py_key'])
            self.logger.info('\tSuccessfully finished comparing motion '
                             'corrected to raw frames.')

        if self.args['clip_negative']:
            data[data < 0] = 0
            data = np.uint16(data)

        # anywhere we've clipped the offset, translate the frame
        # using Suite2P's shift_frame by the difference resulting
        # from clipping, for example, if Suite2P moved a frame
        # by 100 pixels, and we have clipped that to 30, this will
        # move it -70 pixels
        for frame_index in clipped_indices:
            dx = delta_x[frame_index] - ops.item()['xoff'][frame_index]
            dy = delta_y[frame_index] - ops.item()['yoff'][frame_index]
            data[frame_index] = shift_frame(data[frame_index], dy, dx)

        # write the hdf5
        with h5py.File(self.args['motion_corrected_output'], "w") as f:
            f.create_dataset('data', data=data, chunks=(1, *data.shape[1:]))
            # Sort the reference image used to register. If we do not used
            # our custom reference image creation code, this dataset will
            # be empty.
            f.create_dataset('ref_image', data=suite2p_args['refImg'])
            # Write a copy of the configuration output of this dataset into the
            # HDF5 file.
            args_copy = copy.deepcopy(self.args)
            suite_args_copy = copy.deepcopy(suite2p_args)
            # We have to pop the ref image out as numpy arrays can't be
            # serialized into json. The reference image is instead stored in
            # the 'ref_image' dataset.
            suite_args_copy.pop('refImg')
            args_copy['suite2p_args'] = suite_args_copy
            f.create_dataset(name='metadata',
                             data=json.dumps(args_copy).encode('utf-8'))
        self.logger.info("concatenated Suite2P tiff output to "
                         f"{self.args['motion_corrected_output']}")

        # make projections
        mx_proj = utils.projection_process(data, projection="max")
        av_proj = utils.projection_process(data, projection="avg")
        # TODO: normalize here, if desired
        # save projections
        for im, dst_path in zip(
                [mx_proj, av_proj],
                [self.args['max_projection_output'],
                    self.args['avg_projection_output']]):
            with Image.fromarray(im) as pilim:
                pilim.save(dst_path)
            self.logger.info(f"wrote {dst_path}")

        # Save motion offset data to a csv file
        # TODO: This *.csv file is being created to maintain compatability
        # with current ophys processing pipeline. In the future this output
        # should be removed and a better data storage format used.
        # 01/25/2021 - NJM
        motion_offset_df = pd.DataFrame({
            "framenumber": list(range(ops.item()["nframes"])),
            "x": delta_x,
            "y": delta_y,
            "x_pre_clip": ops.item()['xoff'],
            "y_pre_clip": ops.item()['yoff'],
            "correlation": ops.item()["corrXY"]
        })
        motion_offset_df.to_csv(
            path_or_buf=self.args['motion_diagnostics_output'],
            index=False)
        self.logger.info(
            f"Writing the LIMS expected 'OphysMotionXyOffsetData' "
            f"csv file to: {self.args['motion_diagnostics_output']}")
        if len(clipped_indices) != 0:
            self.logger.warning(
                "some offsets have been clipped and the values "
                "for 'correlation' in "
                "{self.args['motion_diagnostics_output']} "
                "where (x_clipped OR y_clipped) = True are not valid")

        qc_args = {k: self.args[k]
                   for k in ['movie_frame_rate_hz',
                             'max_projection_output',
                             'avg_projection_output',
                             'motion_diagnostics_output',
                             'motion_corrected_output',
                             'motion_correction_preview_output',
                             'registration_summary_output',
                             'log_level']}
        qc_args.update({
            'uncorrected_path': self.args['suite2p_args']['h5py']})
        rqc = RegistrationQC(input_data=qc_args, args=[])
        rqc.run()

        # Clean up temporary directories and/or files created during
        # Schema invocation
        if self.schema.tmpdir is not None:
            self.schema.tmpdir.cleanup()

        outj = {k: self.args[k]
                for k in ['motion_corrected_output',
                          'motion_diagnostics_output',
                          'max_projection_output',
                          'avg_projection_output',
                          'registration_summary_output',
                          'motion_correction_preview_output'
                          ]}
        self.output(outj, indent=2)


if __name__ == "__main__":  # pragma: nocover
    s2preg = Suite2PRegistration()
    s2preg.run()
