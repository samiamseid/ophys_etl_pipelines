"""Ophys experiment"""
import os
from dataclasses import dataclass
from pathlib import Path
from sqlmodel import Session, select
from typing import Dict, List
from ophys_etl.workflows.app_config.app_config import app_config
from ophys_etl.workflows.utils.lims_utils import LIMSDB
from ophys_etl.workflows.workflow_step_runs import get_latest_run
from ophys_etl.workflows.workflow_steps import WorkflowStep as WorkflowStepEnum
from ophys_etl.workflows.workflow_names import WorkflowName
from ophys_etl.workflows.db.schemas import MotionCorrectionRun, OphysROI, \
    OphysROIMaskValue

@dataclass
class OphysSession:
    """Container for an ophys session"""
    id: str


@dataclass
class Specimen:
    """Container for a specimen"""
    id: str


@dataclass
class OphysExperiment:
    """Container for an ophys experiment"""
    id: str
    session: OphysSession
    specimen: Specimen
    storage_directory: Path
    raw_movie_filename: Path
    movie_frame_rate_hz: float

    @property
    def output_dir(self) -> Path:
        """Where to output files to for this experiment"""
        base_dir = app_config.output_dir

        output_dir = Path(base_dir) / f'specimen_{self.specimen.id}' / \
            f'session_{self.session.id}' / f'experiment_{self.id}'
        os.makedirs(output_dir, exist_ok=True)
        return output_dir

    @classmethod
    def from_id(
            cls,
            id: str
    ) -> "OphysExperiment":
        """Returns an `OphysExperiment` given a LIMS id for an
        ophys experiment

        Parameters
        ----------
        id
            LIMS ID for the ophys experiment

        """
        query = f'''
            SELECT
                oe.storage_directory,
                oe.ophys_session_id as session_id,
                os.specimen_id,
                oe.movie_frame_rate_hz,
                images.jp2 as raw_movie_filename
            FROM ophys_experiments oe
            JOIN images on images.id = oe.ophys_primary_image_id
            JOIN ophys_sessions os on os.id = oe.ophys_session_id
            WHERE oe.id = {id}
        '''
        lims_db = LIMSDB()
        res = lims_db.query(query=query)

        if len(res) == 0:
            raise ValueError(f'Could not fetch OphysExperiment '
                             f'for ophys experiment id '
                             f'{id}')
        res = res[0]

        session = OphysSession(id=res['session_id'])
        specimen = Specimen(id=res['specimen_id'])

        return cls(
            id=id,
            storage_directory=Path(res['storage_directory']),
            movie_frame_rate_hz=res['movie_frame_rate_hz'],
            raw_movie_filename=res['raw_movie_filename'],
            session=session,
            specimen=specimen
        )

    def get_ophys_experiment_motion_border(self,
            session: Session) -> Dict:
        """
        Get motion border for an ophys experiment

        Parameters
        ----------
        ophys_experiment_id
            The ophys experiment id
        session
            The database session

        Returns
        -------
        Dict[int]
            A dictionary containing motion border data
        """

        workflow_step_run_id = get_latest_run(session,
                                              WorkflowStepEnum.MOTION_CORRECTION,
                                              WorkflowName.OPHYS_PROCESSING,
                                              )
        query = (
            select(
                MotionCorrectionRun,
            )
            .where(MotionCorrectionRun.workflow_step_run_id == workflow_step_run_id) 
        )

        result = session.execute(query).all()
        motion_border = result[0][1]
        return {
            "x0": motion_border.max_correction_left,
            "x1": motion_border.max_correction_right,
            "y0": motion_border.max_correction_up,
            "y1": motion_border.max_correction_down
        }


    def get_ophys_experiment_roi_metadata(
            self,
            session: Session) -> List[Dict]:
        """
        Get ROI metadata for an ophys experiment
        
        Parameters
        ----------
        ophys_experiment_id
            The ophys experiment id
        session
            The database session

        Returns
        -------
        List[Dict]
            A list of dictionaries containing ROI metadata
        """
        workflow_step_run_id = get_latest_run(session,
                                              WorkflowStepEnum.MOTION_CORRECTION,
                                              WorkflowName.OPHYS_PROCESSING,
                                              self.ophys_experiment_id,
                                              )
        query = (
            select(
                OphysROI,
                OphysROIMaskValue,
            )
            .join(OphysROIMaskValue, OphysROIMaskValue.ophys_roi_id == OphysROI.id)
            .where(OphysROI.workflow_step_run_id == workflow_step_run_id)
        )

        result = session.execute(query).all()
        roi_metadata = []
        for row in result:
            ophys_roi, ophys_roi_mask_value = row
            roi_metadata.append({
                'id': ophys_roi.id,
                'x': ophys_roi.x,
                'y': ophys_roi.y,
                'width': ophys_roi.width,
                'height': ophys_roi.height,
                'mask': ophys_roi_mask_value.mask,
                'mask_matrix': ophys_roi_mask_value.mask_matrix
            })
        return roi_metadata