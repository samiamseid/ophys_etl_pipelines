import json
import math
import os.path
import tempfile
import warnings
from typing import Any
from urllib.parse import urlparse

import boto3
import h5py
import joblib
import marshmallow.fields as mm_fields
import numpy as np
from argschema import ArgSchema, ArgSchemaParser, fields
from botocore.errorfactory import ClientError
from marshmallow import Schema, ValidationError, post_load, pre_load, validates
from marshmallow.validate import OneOf
from scipy.signal import resample_poly
from scipy.sparse import coo_matrix

from croissant.features import FeatureExtractor
from ophys_etl.schemas import DenseROISchema
from ophys_etl.schemas.fields import H5InputFile
from ophys_etl.transforms.registry import RegistryConnection

NOT_CELL_EXCLUSION_LABEL = "classified_as_not_cell"


class SparseAndDenseROISchema(DenseROISchema):
    """Version of DenseROISchema which also includes ROIs in sparse format."""
    coo_roi = mm_fields.Field(required=False, load_only=True)

    @post_load
    def add_coo_data(self, data, **kwargs):
        """Convert ROIs to coo format, which is used by the croissant
        FeatureExtractor. Input includes 'x' and 'y' fields
        which designate the cartesian coordinates of the top right corner,
        the width and height of the bounding box, and boolean values for
        whether the mask pixel is contained. The returned coo_matrix
        will contain all the data in the mask in the proper shape,
        but essentially discards the 'x' and 'y' information (the
        cartesian position of the masks is not important for the
        below methods). Represented as a dense array, the mask data
        would be "cropped" to the bounding box.

        Note: If the methods were updated such that the position of
        the mask relative to the input data *were*
        important (say, if necessary to align the masks to the movie
        from which they were created), then this function would require
        the dimensions of the source movie.
        """
        shape = (data["height"], data["width"])
        arr = np.array(data["mask_matrix"]).astype("int")
        if data["height"] + data["width"] == 0:
            warnings.warn("Input data contains empty ROI. "
                          "This may cause problems.")
        elif arr.shape != shape:
            raise ValidationError("Data in mask matrix did not correspond to "
                                  "the (height, width) dimensions. Please "
                                  "check the input data.")
        mat = coo_matrix(arr)
        data.update({"coo_roi": mat})
        return data


class InferenceInputSchema(ArgSchema):
    """ Argschema parser for module as a script """
    neuropil_traces_path = H5InputFile(
        required=True,
        description=(
            "Path to neuropil traces from an experiment (h5 format). "
            "The order of the traces in the dataset should correspond to "
            "the order of masks in `roi_masks_path`.")
    )
    neuropil_traces_data_key = fields.Str(
        required=False,
        missing="data",
        description=("Key in `neuropil_traces_path` h5 file where data array "
                     "is stored.")
    )
    traces_path = H5InputFile(
        required=True,
        description=(
            "Path to traces extracted from an experiment (h5 format). "
            "The order of the traces in the dataset should correspond to "
            "the order of masks in `roi_masks_path`.")
    )
    traces_data_key = fields.Str(
        required=False,
        missing="data",
        description=("Key in `traces_path` h5 file where data array is "
                     "stored.")
    )
    roi_masks_path = fields.InputFile(
        required=True,
        description=("Path to json file of segmented ROI masks. The file "
                     "records must conform to the schema "
                     "`DenseROISchema`")
    )
    rig = fields.Str(
        required=True,
        description=("Name of the ophys rig used for the experiment.")
    )
    depth = fields.Int(
        required=True,
        description=("Imaging depth for the experiment.")
    )
    full_genotype = fields.Str(
        required=True,
        description=("Genotype of the experimental subject.")
    )
    targeted_structure = fields.Str(
        required=True,
        description=("Name of the brain structure targeted by imaging.")
    )
    classifier_model_path = fields.Str(
        required=True,
        description=("Path to model. Can either be an s3 location or a "
                     "path on the local file system. The output of the model "
                     "should be 0 if the ROI is classified as not a cell, "
                     "and 1 if the ROI is classified as a cell. If this "
                     "field is not provided, the classifier model registry "
                     "DynamoDB will be queried.")
    )
    trace_sampling_fps = fields.Int(
        required=False,
        missing=31,
        description=("Sampling rate of trace (frames per second). By default "
                     "trace sampling rates are assumed to be 31 Hz (inherited "
                     "from the source motion_corrected.h5 movie).")
    )
    downsample_to = fields.Int(
        required=False,
        missing=4,
        validate=lambda x: x > 0,
        description=("Target rate to downsample trace data (frames per "
                     "second). Will use average bin values for downsampling.")
    )
    output_json = fields.OutputFile(
        required=True,
        description="Filepath to dump json output."
    )
    model_registry_table_name = fields.Str(
        required=False,
        missing="ROIClassifierRegistry",
        description=("The name of the DynamoDB table containing "
                     "the ROI classifier model registry.")
    )
    model_registry_env = fields.Str(
        required=False,
        validate=OneOf({'dev', 'stage', 'prod'},
                       error=("'{input}' is not a valid value for the "
                              "'model_registry_env' field. Possible "
                              "valid options are: {choices}")),
        missing="prod",
        description=("Which environment to query when searching for a "
                     "classifier model path from the classifier model "
                     "registry. Possible options are: ['dev', 'stage', 'prod]")
    )

    @pre_load
    def determine_classifier_model_path(self, data: dict, **kwargs) -> dict:
        if "classifier_model_path" not in data:
            # Can't rely on field `missing` param as it doesn't get filled in
            # until deserialization/validation. The get defaults should match
            # the 'missing' param for the model_registry_table_name and
            # model_registry_env fields.
            table_name = data.get("model_registry_table_name",
                                  "ROIClassifierRegistry")
            model_env = data.get("model_registry_env", "prod")
            model_registry = RegistryConnection(table_name=table_name)
            model_path = model_registry.get_active_model(env=model_env)
            data["classifier_model_path"] = model_path
        return data

    @validates("classifier_model_path")
    def validate_classifier_model_path(self, uri: str, **kwargs):
        """ Check to see if file exists (either s3 or local file) """
        if uri.startswith("s3://"):
            s3 = boto3.client("s3")
            parsed = urlparse(uri, allow_fragments=False)
            try:
                s3.head_object(
                    Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))
            except ClientError as e:
                if e.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
                    raise ValidationError(
                        f"Object at URI {uri} does not exist.")
                else:
                    raise e from None
        else:
            if not os.path.exists(uri):
                raise ValidationError(f"File at '{uri}' does not exist.")

    @post_load
    def check_keys_exist(self, data: dict, **kwargs) -> dict:
        """ For h5 files, check that the passed key exists in the data. """
        pairs = [("neuropil_traces_path", "neuropil_traces_data_key"),
                 ("traces_path", "traces_data_key")]
        for h5file, key in pairs:
            with h5py.File(data[h5file], "r") as f:
                if not data[key] in f.keys():
                    raise ValidationError(
                        f"Key '{data[key]}' ({key}) was missing in h5 file "
                        f"{data[h5file]} ({h5file}.")
        return data


class InferenceOutputSchema(Schema):
    """ Schema for output json (result of main module script) """
    classified_rois = fields.Nested(
        SparseAndDenseROISchema,
        many=True,
        required=True
    )
    classifier_model_path = fields.Str(
        required=True,
        description=("Path to model. Can either be an s3 location or a "
                     "path on the local file system.")
    )


class InferenceParser(ArgSchemaParser):
    """ Argschema entry point """
    default_schema = InferenceInputSchema
    default_output_schema = InferenceOutputSchema


def _munge_data(parser: InferenceParser, roi_data: list):
    """
    Format the input data for downstream processing.
    Params
    ------
    parser: InferenceParser
        An instance of InferenceParser
    roi_data: list
        List of objects conforming to SparseAndDenseROISchema
    Returns
    -------
    tuple
        rois (list of coo_matrices), metadata dictionary,
        traces data (np.1darray) and neuropil traces data (np.1darray)
    """
    # Format metadata and multiply for input (all same)
    metadata = [{
        "depth": parser.args["depth"],
        "rig": parser.args["rig"],
        "targeted_structure": parser.args["targeted_structure"],
        "full_genotype": parser.args["full_genotype"]
        }] * len(roi_data)
    rois = [r["coo_roi"] for r in roi_data]
    traces = []
    np_traces = []

    traces_file = h5py.File(parser.args["traces_path"], "r")
    np_traces_file = h5py.File(parser.args["neuropil_traces_path"], "r")
    traces_data = traces_file[parser.args["traces_data_key"]]
    np_traces_data = np_traces_file[parser.args["neuropil_traces_data_key"]]
    for n in range(len(roi_data)):
        # Downsample traces by accessing on axis that the h5 file should be
        # more performant on
        trace = downsample(
            traces_data[n, :], parser.args["trace_sampling_fps"],
            parser.args["downsample_to"])
        np_trace = downsample(
            np_traces_data[n, :], parser.args["trace_sampling_fps"],
            parser.args["downsample_to"])
        traces.append(trace)
        np_traces.append(np_trace)
    traces_file.close()
    np_traces_file.close()
    return rois, metadata, traces, np_traces


def downsample(trace: np.ndarray, input_fps: int, output_fps: int):
    """Downsample 1d array using scipy resample_poly.
    See https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html#scipy.signal.resample_poly    # noqa
    for more information.

    Parameters
    ----------
    trace: np.ndarray
        1d array of values to downsample
    input_fps: int
        The FPS that the trace data was captured at
    output_fps: int
        The desired FPS of the trace
    Returns
    -------
    np.ndarray
        1d array of values, downsampled to output_fps
    """
    if input_fps == output_fps:
        return trace
    elif output_fps > input_fps:
        raise ValueError("Output FPS can't be greater than input FPS.")
    gcd = math.gcd(input_fps, output_fps)
    up = output_fps / gcd
    down = input_fps / gcd
    downsample = resample_poly(trace, up, down, axis=0, padtype="median")
    return downsample


def load_model(classifier_model_uri: str) -> Any:
    """Load a classifier model given a valid URI.

    Parameters
    ----------
    classifier_model_uri : str
        A valid URI that points to either an AWS S3 resource or a
        local filepath. URI validity is only guaranteed by the
        'InferenceInputSchema'.

    Returns
    -------
    Any
        A loaded ROI classifier model.
    """
    if classifier_model_uri.startswith("s3://"):
        s3 = boto3.client("s3")
        parsed = urlparse(classifier_model_uri, allow_fragments=False)

        with tempfile.TemporaryFile() as fp:
            s3.download_fileobj(Bucket=parsed.netloc,
                                Key=parsed.path.lstrip("/"),
                                Fileobj=fp)
            fp.seek(0)
            model = joblib.load(fp)
    else:
        model = joblib.load(classifier_model_uri)
    return model


def filtered_roi_load(roi_masks_path):
    with open(roi_masks_path, "r") as f:
        raw_roi_data = json.load(f)
    roi_input_schema = SparseAndDenseROISchema(many=True)
    roi_data = roi_input_schema.load(raw_roi_data)
    excluded = [r for r in roi_data if r["exclusion_labels"]]
    included = [r for r in roi_data if not r["exclusion_labels"]]
    return included, excluded


def main(parser):
    roi_data, excluded_rois = filtered_roi_load(parser.args["roi_masks_path"])

    rois, metadata, traces, _ = _munge_data(parser, roi_data)
    # TODO: add neuropil traces later
    features = FeatureExtractor(rois, traces, metadata).run()
    model = load_model(parser.args["classifier_model_path"])
    predictions = model.predict(features)
    if len(predictions) != len(roi_data):
        raise ValueError(
            f"Expected the number of predictions ({len(predictions)}) to  "
            f"equal the number of input ROIs ({len(roi_data)}), but they "
            "are not the same.")
    for obj, prediction in zip(roi_data, predictions):
        if prediction == 0:
            obj["exclusion_labels"].append(NOT_CELL_EXCLUSION_LABEL)
            obj["valid_roi"] = False

    roi_data.extend(excluded_rois)

    output_data = {
        "classified_rois": roi_data,
        "classifier_model_path": parser.args["classifier_model_path"]
    }
    parser.output(output_data)


if __name__ == "__main__":
    parser = InferenceParser()
    main(parser)
