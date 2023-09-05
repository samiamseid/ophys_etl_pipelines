import datetime
import tempfile
from pathlib import Path
from unittest.mock import patch, PropertyMock

from ophys_etl.workflows.db.schemas import OphysROI, OphysROIMaskValue, \
    MotionCorrectionRun

from ophys_etl.workflows.pipeline_modules.segmentation import \
    SegmentationModule

from ophys_etl.workflows.output_file import OutputFile

from ophys_etl.workflows.db.db_utils import save_job_run_to_db
from sqlmodel import Session, select

from ophys_etl.workflows.ophys_experiment import OphysSession, Specimen, \
    OphysExperiment, ImagingPlaneGroup, OphysContainer

from ophys_etl.workflows.pipeline_modules.decrosstalk import \
    DecrosstalkModule, DECROSSTALK_FLAGS
from ophys_etl.workflows.well_known_file_types import WellKnownFileTypeEnum
from ophys_etl.workflows.workflow_names import WorkflowNameEnum
from ophys_etl.workflows.workflow_steps import WorkflowStepEnum
from tests.workflows.conftest import MockSQLiteDB


class TestDecrosstalkModule(MockSQLiteDB):
    @classmethod
    def setup_class(cls):
        cls._experiment_ids = [1, 2]

    def setup(self):
        super().setup()

    @patch.object(OphysExperiment, 'from_id')
    @patch.object(OphysSession, 'get_ophys_experiment_ids')
    @patch.object(OphysExperiment, 'motion_border',
                  new_callable=PropertyMock)
    @patch.object(OphysExperiment, 'rois',
                  new_callable=PropertyMock)
    @patch.object(OphysSession, 'output_dir',
                  new_callable=PropertyMock)
    def test_inputs(self,
                    mock_output_dir,
                    mock_oe_rois,
                    mock_motion_border,
                    mock_ophys_session_oe_ids,
                    mock_ophys_experiment_from_id,
                    temp_dir,
                    mock_motion_border_run,
                    mock_rois,
                    mock_ophys_session
                    ):

        mock_oe_rois.return_value = mock_rois
        mock_motion_border.return_value = mock_motion_border_run
        mock_ophys_session_oe_ids.return_value = self._experiment_ids
        mock_ophys_experiment_from_id.side_effect = \
            lambda id: OphysExperiment(
                id=id,
                movie_frame_rate_hz=1,
                raw_movie_filename=Path('foo'),
                session=mock_ophys_session,
                container=OphysContainer(id=1, specimen=Specimen(id='1')),
                specimen=mock_ophys_session.specimen,
                storage_directory=Path('foo'),
                imaging_plane_group=ImagingPlaneGroup(
                    id=0 if id == 1 else 1,
                    group_order=0 if id == 1 else 1
                ),
                full_genotype="Vip-IRES-Cre/wt;Ai148(TIT2L-GC6f-ICL-tTA2)/wt",
                equipment_name='MESO.1'
            )
        mock_output_dir.return_value = temp_dir

        with patch('ophys_etl.workflows.pipeline_modules.decrosstalk.engine',
                   new=self._engine):
            mod = DecrosstalkModule(
                docker_tag='main',
                ophys_session=mock_ophys_session
            )
            obtained_inputs = mod.inputs

        expected_inputs = {
            'log_level': 'INFO',
            'ophys_session_id': mock_ophys_session.id,
            'qc_output_dir': str(
                    mock_ophys_session.output_dir / 'DECROSSTALK' / mod.now_str),
            'coupled_planes': [
                {
                    'ophys_imaging_plane_group_id': (
                        0 if self._experiment_ids[i] == 1 else 1),
                    'group_order': (
                        0 if self._experiment_ids[i] == 1 else 1
                    ),
                    'planes': [
                        {
                            'ophys_experiment_id': self._experiment_ids[i],
                            'motion_corrected_stack': str(
                                self._tmp_dir /
                                f'{self._experiment_ids[i]}_'
                                f'motion_correction.h5'),
                            'maximum_projection_image_file': str(
                                self._tmp_dir /
                                f'{self._experiment_ids[i]}_max_proj.png'
                            ),
                            'output_roi_trace_file': str(
                                mod.output_path /
                                f'ophys_experiment_{self._experiment_ids[i]}_'
                                f'roi_traces.h5'
                            ),
                            'output_neuropil_trace_file': str(
                                mod.output_path /
                                f'ophys_experiment_{self._experiment_ids[i]}_'
                                'neuropil_traces.h5'
                            ),
                            'motion_border': (
                                mock_motion_border.return_value.to_dict()),
                            'rois': [
                                {'mask_matrix' if k == 'mask' else k: v
                                 for k, v in x.to_dict().items()
                                 }
                                for x in mock_rois.return_value]
                        }
                    ]
                }
                for i in range(len(self._experiment_ids))]
        }
        assert obtained_inputs == expected_inputs

    @patch.object(OphysExperiment, 'from_id')
    def test_save_decrosstalk_flags_to_db(
            self,
            mock_ophys_experiment_from_id
    ):
        ophys_session = OphysSession(
            id=1,
            specimen=Specimen(id='specimen_1')
        )

        mock_ophys_experiment_from_id.side_effect = \
            lambda id: OphysExperiment(
                id=id,
                movie_frame_rate_hz=1,
                raw_movie_filename=Path('foo'),
                session=ophys_session,
                container=OphysContainer(
                    id=1, specimen=ophys_session.specimen),
                specimen=ophys_session.specimen,
                storage_directory=Path('foo'),
                full_genotype="Vip-IRES-Cre/wt;Ai148(TIT2L-GC6f-ICL-tTA2)/wt",
                equipment_name='MESO.1'
            )

        # 1. Save segmentation run
        _rois_path = Path(__file__).parent / "resources" / "rois.json"

        for oe_id in self._experiment_ids:
            with Session(self._engine) as session:
                with patch(
                        'ophys_etl.workflows.ophys_experiment.engine',
                        new=self._engine):
                    save_job_run_to_db(
                        workflow_step_name=WorkflowStepEnum.SEGMENTATION,
                        start=datetime.datetime.now(),
                        end=datetime.datetime.now(),
                        module_outputs=[
                            OutputFile(
                                well_known_file_type=(
                                    WellKnownFileTypeEnum.OPHYS_ROIS
                                ),
                                path=_rois_path
                            )
                        ],
                        ophys_experiment_id=oe_id,
                        sqlalchemy_session=session,
                        storage_directory="/foo",
                        log_path="/foo",
                        additional_steps=SegmentationModule.save_rois_to_db,
                        workflow_name=WorkflowNameEnum.OPHYS_PROCESSING
                    )

        # 2. Save decrosstalk run
        with patch('ophys_etl.workflows.ophys_experiment.engine',
                   new=self._engine):
            save_job_run_to_db(
                workflow_step_name=WorkflowStepEnum.DECROSSTALK,
                start=datetime.datetime.now(),
                end=datetime.datetime.now(),
                module_outputs=[
                    OutputFile(
                        well_known_file_type=WellKnownFileTypeEnum
                        .DECROSSTALK_FLAGS,
                        path=(Path(__file__).parent / "resources" /
                              "decrosstalk_output.json")
                    )
                ],
                ophys_session_id=ophys_session.id,
                sqlalchemy_session=session,
                storage_directory="/foo",
                log_path="/foo",
                additional_steps=(
                    DecrosstalkModule.save_decrosstalk_flags_to_db),
                workflow_name=WorkflowNameEnum.OPHYS_PROCESSING
            )

        # 3. Try fetch decrosstalk flags
        with Session(self._engine) as session:
            rois = session.exec(select(OphysROI)).all()

        expected_flags = {
            3: ['decrosstalk_invalid_raw',
                'decrosstalk_invalid_unmixed',
                'decrosstalk_ghost'],
            4: ['decrosstalk_invalid_raw',
                'decrosstalk_invalid_unmixed']
        }

        for roi in rois:
            flags = expected_flags.get(roi.id, [])
            for flag in DECROSSTALK_FLAGS:
                if flag in flags:
                    assert getattr(roi, f'is_{flag}')
                else:
                    assert getattr(roi, f'is_{flag}') is False

    def teardown(self):
        self.temp_dir_obj.cleanup()
