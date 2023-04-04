from types import ModuleType
from typing import List, Dict

from deepcell.cli.modules.cloud import train
from sqlmodel import Session

from ophys_etl.workflows.db.schemas import ROIClassifierEnsemble, \
    ROIClassifierTrainingRun
from ophys_etl.workflows.pipeline_modules.roi_classification.utils\
    .mlflow_utils \
    import \
    MLFlowRun
from ophys_etl.workflows.well_known_file_types import WellKnownFileType

from ophys_etl.workflows.app_config.app_config import app_config

from ophys_etl.workflows.pipeline_module import PipelineModule, OutputFile
from ophys_etl.workflows.workflow_steps import WorkflowStep as WorkflowStepEnum


class TrainingModule(PipelineModule):
    def __init__(
        self,
        prevent_file_overwrites: bool = True,
        **kwargs
    ):
        super().__init__(
            ophys_experiment=None,
            prevent_file_overwrites=prevent_file_overwrites,
            **kwargs
        )

        self._model_inputs_path: OutputFile = kwargs['train_set_path']
        self._mlflow_run_name = kwargs['mlflow_run_name']

    @property
    def queue_name(self) -> WorkflowStepEnum:
        return WorkflowStepEnum.ROI_CLASSIFICATION_TRAINING

    @property
    def inputs(self) -> Dict:
        return {
            'train_params': {
                'model_inputs_path': self._model_inputs_path,
                'model_params': {
                    'freeze_to_layer': (
                        app_config.pipeline_steps.roi_classification.training.
                        model.freeze_to_layer),
                    'truncate_to_layer': (
                        app_config.pipeline_steps.roi_classification.training.
                        model.truncate_to_layer
                    )
                },
                'tracking_params': {
                    'mlflow_server_uri': (
                        app_config.pipeline_steps.roi_classification.training.
                        tracking.mlflow_server_uri
                    ),
                    'mlflow_run_name': self._mlflow_run_name
                }
            },
            'docker_params': {
                'image_uri': (
                    app_config.pipeline_steps.roi_classification.training.
                    docker.image_uri
                )
            },
            's3_params': {
                'bucket_name': (
                    app_config.pipeline_steps.roi_classification.training.
                    s3.bucket_name
                ),
                'data_key': (
                    app_config.pipeline_steps.roi_classification.training.
                    s3.data_key
                )
            }
        }

    @property
    def outputs(self) -> List[OutputFile]:
        return [
            OutputFile(
                well_known_file_type=(
                    WellKnownFileType.ROI_CLASSIFICATION_TRAINED_MODEL),
                path=self.output_path / 'model'
            )
        ]

    @property
    def _executable(self) -> ModuleType:
        return train

    @staticmethod
    def save_trained_model_to_db(
        output_files: Dict[str, OutputFile],
        session: Session,
        run_id: int,
        mlflow_parent_run_name: str
    ):
        mlflow_run = MLFlowRun(
            mlflow_experiment_name=(
                app_config.pipeline_steps.roi_classification.training.
                tracking.mlflow_experiment_name),
            run_name=mlflow_parent_run_name
        )

        ensemble = ROIClassifierEnsemble(
            workflow_step_run_id=run_id,
            mlflow_run_id=mlflow_run.run.info.run_id
        )
        session.add(ensemble)

        # flush to get ensemble id of just added ensemble
        session.flush()

        for child_run in mlflow_run.child_runs:
            training_run = ROIClassifierTrainingRun(
                ensemble_id=ensemble.id,
                mlflow_run_id=child_run.run.info.run_id,
                sagemaker_job_id=child_run.sagemaker_job_id
            )
            session.add(training_run)