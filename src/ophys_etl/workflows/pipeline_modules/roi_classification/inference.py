from pathlib import Path
from types import ModuleType
from typing import List, Dict

import json

import pandas as pd
from deepcell.cli.modules import inference
from deepcell.datasets.model_input import ModelInput

from ophys_etl.workflows.workflow_names import WorkflowName
from ophys_etl.workflows.workflow_step_runs import get_latest_run
from ophys_etl.workflows.workflow_steps import WorkflowStep

from ophys_etl.workflows.well_known_file_types import WellKnownFileType

from ophys_etl.workflows.db.db_utils import get_well_known_file_type

from ophys_etl.workflows.db.schemas import ROIClassifierEnsemble, \
    WellKnownFile, ROIClassifierInferenceResults, OphysROI
from sqlalchemy import select
from sqlmodel import Session

from ophys_etl.workflows.app_config.app_config import app_config
from ophys_etl.workflows.db import engine

from ophys_etl.workflows.ophys_experiment import OphysExperiment

from ophys_etl.workflows.pipeline_module import PipelineModule, OutputFile
from ophys_etl.workflows.pipeline_modules.roi_classification.utils\
    .mlflow_utils \
    import \
    MLFlowRun


class InferenceModule(PipelineModule):
    """Uses trained ROI classifier to classify ROIs"""
    def __init__(
        self,
        ophys_experiment: OphysExperiment,
        prevent_file_overwrites: bool = True,
        **kwargs
    ):
        super().__init__(
            ophys_experiment=ophys_experiment,
            prevent_file_overwrites=prevent_file_overwrites,
            **kwargs
        )

        thumbnails_dir: OutputFile = kwargs['thumbnails_dir']

        self._ensemble = self._get_model_ensemble(
            ensemble_id=kwargs['ensemble_id'])

        self._model_inputs_path = self._write_model_inputs_to_disk(
            thumbnails_dir=thumbnails_dir.path
        )

    @property
    def queue_name(self) -> WorkflowStep:
        return WorkflowStep.ROI_CLASSIFICATION_INFERENCE

    @property
    def inputs(self) -> Dict:
        model_params = self._get_mlflow_model_params()
        return {
            'model_inputs_path': self._model_inputs_path,
            'model_params': {
                'use_pretrained_model': model_params['use_pretrained_model'],
                'model_architecture': model_params['model_architecture'],
                'truncate_to_layer': model_params['truncate_to_layer']
            },
            'model_load_path': self._ensemble.path,
            'save_path': self.output_path,
            'mode': 'production',
            'experiment_id': self.ophys_experiment.id
        }

    @property
    def outputs(self) -> List[OutputFile]:
        return [
            OutputFile(
                well_known_file_type=(
                    WellKnownFileType.
                    ROI_CLASSIFICATION_EXPERIMENT_PREDICTIONS),
                path=(self.output_path /
                      f'{self.ophys_experiment.id}_inference.csv')
            )
        ]

    @property
    def _executable(self) -> ModuleType:
        return inference

    def _write_model_inputs_to_disk(
        self,
        thumbnails_dir: Path
    ) -> Path:
        """Creates and writes model inputs to disk

        Parameters
        ----------
        thumbnails_dir
            Path to classifier thumbnail images directory

        Returns
        -------
        Path
            Path where model inputs file is saved
        """
        rois = self._get_rois()
        model_inputs = [
            ModelInput.from_data_dir(
                data_dir=thumbnails_dir,
                experiment_id=self.ophys_experiment.id,
                roi_id=str(roi.id),
                channels=(
                    app_config.pipeline_steps.roi_classification.
                    input_channels)
            )
            for roi in rois]

        model_inputs = [x.to_dict() for x in model_inputs]

        out_path = \
            self.output_path / f'{self.ophys_experiment.id}_model_inputs.json'
        with open(out_path, 'w') as f:
            f.write(json.dumps(model_inputs, indent=2))

        return out_path

    @staticmethod
    def save_predictions_to_db(
        output_files: Dict[str, OutputFile],
        session: Session,
        run_id: int,
        ensemble_id: int
    ):
        preds_file = output_files[
            WellKnownFileType.ROI_CLASSIFICATION_EXPERIMENT_PREDICTIONS.value]
        preds = pd.read_csv(preds_file.path)

        # renaming so that hyphen doesn't cause problems
        preds.rename(columns={'roi-id': 'roi_id'}, inplace=True)

        for pred in preds.itertuples(index=False):
            inference_res = ROIClassifierInferenceResults(
                roi_id=pred.roi_id,
                ensemble_id=ensemble_id,
                score=pred.y_score
            )
            session.add(inference_res)

    @staticmethod
    def _get_model_ensemble(
            ensemble_id: int
    ):
        with Session(engine) as session:
            model_file = get_well_known_file_type(
                session=session,
                name=WellKnownFileType.ROI_CLASSIFICATION_TRAINED_MODEL,
                workflow=WorkflowName.ROI_CLASSIFIER_TRAINING,
                workflow_step_name=WorkflowStep.ROI_CLASSIFICATION_TRAINING
            )
            statement = (
                select(ROIClassifierEnsemble, WellKnownFile.path)
                .where(
                    ROIClassifierEnsemble.id == ensemble_id,
                    WellKnownFile.workflow_step_run_id ==
                    ROIClassifierEnsemble.workflow_step_run_id,
                    WellKnownFile.well_known_file_type_id == model_file.id
                )
            )
            res = session.exec(statement=statement).one()
        return res

    def _get_mlflow_model_params(self) -> Dict:
        """Pulls the mlflow run for `run_id` and fetches the params used
        for that run

        Returns
        -------
        Dict
            The params used to train the model
        """
        run = MLFlowRun(
            mlflow_experiment_name=(
                app_config.pipeline_steps.roi_classification.training.tracking.
                mlflow_experiment_name
            ),
            run_id=self._ensemble.mlflow_run_id
        )
        params = run.run.data.params

        model_params = {
            param['key'].replace('model_params_', ''): param['value']
            for param in params
            if param['key'].startswith('model_params')
        }
        return model_params

    def _get_rois(self) -> List[OphysROI]:
        """
        Returns
        -------
        ROIs from most recent segmentation run for `self.ophys_experiment.id`
        """
        with Session(engine) as session:
            segmentation_run_id = get_latest_run(
                session=session,
                workflow_name=WorkflowName.OPHYS_PROCESSING,
                workflow_step=WorkflowStep.SEGMENTATION,
                ophys_experiment_id=self.ophys_experiment.id
            )

            rois = session.exec(
                select(OphysROI)
                .where(OphysROI.workflow_step_run_id == segmentation_run_id)
            ).all()
            return rois