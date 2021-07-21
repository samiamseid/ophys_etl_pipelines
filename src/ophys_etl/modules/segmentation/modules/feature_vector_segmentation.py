import argschema
import pathlib

from ophys_etl.modules.segmentation.modules.schemas import \
    FeatureVectorSegmentationInputSchema

from ophys_etl.modules.segmentation.detect.feature_vector_segmentation import (
        FeatureVectorSegmenter)

from ophys_etl.modules.segmentation.detect.feature_vector_rois import (
    PearsonFeatureROI, PCAFeatureROI)

ROI_CLASS_MAP = {"PearsonFeatureROI": PearsonFeatureROI,
                 "PCAFeatureROI": PCAFeatureROI}


class FeatureVectorSegmentationRunner(argschema.ArgSchemaParser):

    default_schema = FeatureVectorSegmentationInputSchema

    def run(self):
        graph_input = pathlib.Path(self.args['graph_input'])
        video_input = pathlib.Path(self.args['video_input'])
        n_processors = self.args['n_parallel_workers']
        attr = self.args['attribute']
        roi_class = ROI_CLASS_MAP[self.args["roi_class"]]
        segmenter = FeatureVectorSegmenter(
                graph_input,
                video_input,
                attribute=attr,
                n_processors=n_processors,
                roi_class=roi_class,
                filter_fraction=self.args['filter_fraction'],
                seeder_args=self.args['seeder_args'],
                min_window_size=self.args['min_window_size'],
                max_window_size=self.args['max_window_size'])

        if self.args['plot_output'] is not None:
            plot_output = pathlib.Path(self.args['plot_output'])
        else:
            plot_output = None
        segmenter.run(roi_output=self.args['roi_output'],
                      qc_output=pathlib.Path(self.args['qc_output']),
                      plot_output=plot_output,
                      seed_plot_output=self.args['seed_plot_output'])


if __name__ == "__main__":
    seg = FeatureVectorSegmentationRunner()
    seg.run()
