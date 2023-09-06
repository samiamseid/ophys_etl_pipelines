"""Slurm interface"""
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import logging

import json

import pytz
import requests
from paramiko import SSHClient

from ophys_etl.workflows.app_config.app_config import app_config
from ophys_etl.workflows.app_config.slurm import SlurmSettings
from ophys_etl.workflows.pipeline_module import PipelineModule

logger = logging.getLogger('airflow.task')


class SlurmRestApiException(RuntimeError):
    """Catch Slurm rest api failures.
    """
    pass


def _exec_slurm_command(command: str) -> str:
    """Execute command over ssh on hpc-login"""
    with SSHClient() as client:
        client.load_system_host_keys()
        client.connect('hpc-login')
        _, stdout, stderr = client.exec_command(command=command)
        stderr = stderr.read()
        if stderr:
            raise RuntimeError(stderr)
        stdout = bytes.decode(stdout.read())
    return stdout


class SlurmState(Enum):
    """States that a slurm job can be in

    Source: https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES
    """
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    RUNNING = 'RUNNING'
    PENDING = 'PENDING'
    BOOT_FAIL = 'BOOT_FAIL'
    CANCELLED = 'CANCELLED'
    CONFIGURING = 'CONFIGURING'
    COMPLETING = 'COMPLETING'
    DEADLINE = 'DEADLINE'
    NODE_FAIL = 'NODE_FAIL'
    OUT_OF_MEMORY = 'OUT_OF_MEMORY'
    PREEMPTED = 'PREEMPTED'
    RESV_DEL_HOLD = 'RESV_DEL_HOLD'
    REQUEUE_FED = 'REQUEUE_FED'
    REQUEUE_HOLD = 'REQUEUE_HOLD'
    REQUEUED = 'REQUEUED'
    RESIZING = 'RESIZING'
    REVOKED = 'REVOKED'
    SIGNALING = 'SIGNALING'
    SPECIAL_EXIT = 'SPECIAL_EXIT'
    STAGE_OUT = 'STAGE_OUT'
    STOPPED = 'STOPPED'
    SUSPENDED = 'SUSPENDED'
    TIMEOUT = 'TIMEOUT'
    CANCELLED_plus = 'CANCELLED+'


class SlurmJobFailedException(Exception):
    """The slurm job failed"""
    pass


@dataclass
class SlurmJob:
    """A job that has been submitted to slurm

    Attributes:

    - :class:`id`: Slurm job id
    - :class:`state`: slurm job state. None if not started yet
    - :class:`start`: start time of job
    - :class:`end`: end time of job
    """
    id: str
    state: Optional[SlurmState] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None

    @classmethod
    def from_job_id(
            cls,
            job_id: str,
    ) -> "SlurmJob":
        """Fetches job status and instantiates `SlurmJob`

        Returns
        -------
        If job id `job_id` found, returns `SlurmJob` instance

        """
        r = requests.get(
            url=f'http://slurm.corp.alleninstitute.org/api/slurmdb/v0.0.36/'
                f'job/{job_id}',
            headers={
                'X-SLURM-USER-NAME': app_config.slurm.username,
                'X-SLURM-USER-TOKEN': (
                    app_config.slurm.api_token.get_secret_value()),
            }
        )
        if r.status_code != 200:
            raise SlurmRestApiException(r.text)
        response = r.json()

        if len(response['jobs']) == 0:
            # If we got here it means the job has not started yet
            return cls(
                id=job_id
            )
        job = response['jobs'][-1]

        # slurm records datetimes in local time (Pacific). Convert to UTC
        start = datetime.fromtimestamp(job['time']['start'],
                                       tz=pytz.timezone('US/Pacific'))
        start = start.astimezone(tz=pytz.UTC)
        end = datetime.fromtimestamp(job['time']['end'],
                                     tz=pytz.timezone('US/Pacific'))
        end = end.astimezone(tz=pytz.UTC)

        return cls(
            id=job_id,
            state=SlurmState(job['state']['current']),
            start=start,
            end=end
        )

    def is_done(self) -> bool:
        """Whether the job is done"""
        return self.state == SlurmState.COMPLETED

    def is_failed(self) -> bool:
        """Whether the job failed"""
        failed_states = [
            SlurmState.FAILED,
            SlurmState.CANCELLED_plus,
            SlurmState.TIMEOUT,
            SlurmState.CANCELLED,
            SlurmState.OUT_OF_MEMORY,
        ]
        return self.state in failed_states


class Slurm:
    """Wrapper around slurm"""
    def __init__(
        self,
        pipeline_module: PipelineModule,
        config: SlurmSettings,
        log_path: Path
    ):
        """
        Parameters
        ----------
        pipeline_module
            `PipelineModule` instance
        config
            Slurm settings
        log_path
            Where to write slurm job logs to
        """
        self._pipeline_module = pipeline_module
        self._job: Optional[SlurmJob] = None

        if app_config.is_debug:
            logger.info(f'is debug: {app_config.is_debug}. '
                        f'Overriding slurm settings')
            config = SlurmSettings(
                cpus_per_task=4,
                mem=16,
                time=120,
                gpus=0
            )
        self._slurm_settings = config
        self._log_path = log_path

        os.makedirs(log_path.parent, exist_ok=True)

    @property
    def job(self) -> SlurmJob:
        return self._job

    def _write_job_to_disk(
            self,
            tmp_storage_adjustment_factor: int = 3,
            *args,
            **kwargs
    ) -> Path:
        """
        Construct slurm job request and write to disk

        Parameters
        ----------
        tmp_storage_adjustment_factor
            Multiplies the ophys experiment file size by this amount to give
            some breathing room to the amount of tmp storage to reserve.
            Not used if request_additional_tmp_storage is False
        args
            positional args to pass to command
        kwargs
            keyword args to pass to command


        Returns
        -------
        Path to slurm script
        """
        args = ' '.join([f'{x}' for x in args])
        kwargs = ' '.join([f'--{k} {v}' for k, v in kwargs.items()])

        docker_tag = self._pipeline_module.docker_tag
        singularity_username = \
            app_config.singularity.username.get_secret_value()
        singularity_password = \
            app_config.singularity.password.get_secret_value()

        request_gpu = self._slurm_settings.gpus > 0

        # Note: downloading image to tmprdir and disabling caching due to
        # cryptic errors, i.e. stale NFS file mount, bus error, etc.
        script = f'''#! /bin/bash
# Adds mksquashfs (needed for singularity) to $PATH
source /etc/profile

export SINGULARITY_DOCKER_USERNAME={singularity_username}
export SINGULARITY_DOCKER_PASSWORD={singularity_password}

SINGULARITY_TMPDIR=/scratch/fast/${{SLURM_JOB_ID}} \
SINGULARITY_PULLFOLDER=/scratch/fast/${{SLURM_JOB_ID}} \
SINGULARITY_DISABLE_CACHE=True \
singularity run \
    --bind /allen:/allen,/scratch/fast/${{SLURM_JOB_ID}}:/tmp \
    {"--nv" if request_gpu else ""} \
    docker://alleninstitutepika/\
{self._pipeline_module.dockerhub_repository_name}:{docker_tag} \
    {self._pipeline_module.python_interpreter_path} -m \
{self._pipeline_module.executable.__name__} {args} {kwargs}'''

        if not self._slurm_settings.request_additional_tmp_storage:
            tmp = 0
        else:
            tmp = self._get_tmp_storage(
                adjustment_factor=tmp_storage_adjustment_factor)
        standard_error = self._log_path.parent / f'{self._log_path.stem}.err'
        job = {
            'job': {
                'qos': 'production',
                'partition': app_config.slurm.partition,
                'nodes': 1,
                'cpus_per_task': self._slurm_settings.cpus_per_task,
                'gpus': self._slurm_settings.gpus,
                'memory_per_node': self._slurm_settings.mem * 1024,  # MB
                'time_limit': self._slurm_settings.time,
                'name': self._pipeline_module.queue_name.value,
                'temporary_disk_per_node': f'{tmp}G',
                'standard_output': str(self._log_path),
                'standard_error': str(standard_error),
                "environment": {
                    "PATH": "/bin:/usr/bin/:/usr/local/bin/",
                    "LD_LIBRARY_PATH": "/lib/:/lib64/:/usr/local/lib"
                }
            },
            'script': script
        }

        out = self._pipeline_module.output_path / 'slurm_job.json'

        with open(out, 'w') as f:
            f.write(json.dumps(job, indent=2))
        logger.info(f'Wrote slurm job payload to {out}')
        return Path(out)

    def submit_job(
        self,
        *args,
        **kwargs
    ) -> str:
        """
        Submits batch job to slurm

        Parameters
        ----------
        args
            positional args to pass to command
        kwargs
            keyword args to pass to command

        Returns
        -------
        slurm job id
        """
        request_path = self._write_job_to_disk(*args, **kwargs)
        with open(request_path) as f:
            data = json.load(f)

        r = requests.post(
            url='http://slurm.corp.alleninstitute.org/api/slurm/v0.0.36/'
                'job/submit',
            headers={
                'X-SLURM-USER-NAME': app_config.slurm.username,
                'X-SLURM-USER-TOKEN': (
                    app_config.slurm.api_token.get_secret_value()),
                'Content-type': 'application/json'
            },
            data=json.dumps(data)
        )
        if r.status_code != 200:
            raise SlurmRestApiException(r.text)
        response = r.json()
        job_id = response.get('job_id')
        job = SlurmJob.from_job_id(job_id=job_id)
        logger.info(response)
        self._job = job
        return job_id

    def _get_tmp_storage(
        self,
        adjustment_factor: int = 3
    ) -> int:
        """
        Gets the amount of temporary storage to reserve

        Parameters
        ----------
        adjustment_factor
            Multiplies the ophys experiment file size by this amount to give
            some breathing room

        Returns
        -------
        Amount of file size to reserve in GB
        """
        storage_directory = \
            self._pipeline_module.ophys_experiment.storage_directory
        raw_movie_filename =\
            self._pipeline_module.ophys_experiment.raw_movie_filename
        file_size = (storage_directory / raw_movie_filename).stat().st_size
        return int(file_size / (1024 ** 3) * adjustment_factor)
