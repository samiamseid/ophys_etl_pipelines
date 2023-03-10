from typing import List, Dict

from ophys_etl.workflows.well_known_file_types import WellKnownFileType

from ophys_etl.workflows.app_config.app_config import app_config

from ophys_etl.workflows.ophys_experiment import OphysExperiment

from ophys_etl.workflows.pipeline_module import PipelineModule, OutputFile


class GenerateCorrelationProjectionModule(PipelineModule):
    """Generate correlation projection graph"""
    def __init__(
        self,
        ophys_experiment: OphysExperiment,
        debug: bool = False,
        prevent_file_overwrites: bool = True,
        **kwargs
    ):
        super().__init__(
            ophys_experiment=ophys_experiment,
            debug=debug,
            prevent_file_overwrites=prevent_file_overwrites,
            **kwargs
        )

        denoised_ophys_movie_file: OutputFile = \
            kwargs['denoised_ophys_movie_file']
        self._denoised_ophys_movie_file = str(denoised_ophys_movie_file.path)

    @property
    def queue_name(self) -> str:
        return 'ROI_CLASSIFICATION_GENERATE_CORRELATION_PROJECTION'

    @property
    def inputs(self) -> Dict:
        return {
            'video_path': self._denoised_ophys_movie_file,
            'graph_output': (
                    self.output_path /
                    f'{self._ophys_experiment.id}_correlation_graph.pkl'),
            'attribute': 'filtered_hnc_Gaussian',
            'neighborhood_radius': 7,
            'n_parallel_workers': (
                app_config.pipeline_steps.roi_classification.
                generate_correlation_projection.n_workers)
        }

    @property
    def outputs(self) -> List[OutputFile]:
        return [
            OutputFile(
                well_known_file_type=(
                    WellKnownFileType.ROI_CLASSIFICATION_CORRELATION_PROJECTION_GRAPH),
                path=(self.output_path /
                      f'{self._ophys_experiment.id}_correlation_graph.pkl')
            )
        ]

    @property
    def _executable(self) -> str:
        return 'ophys_etl.modules.segmentation.modules.calculate_edges'
