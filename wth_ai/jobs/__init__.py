"""Job subsystem (Fase 4): formal jobs with queue, store, worker, manager.

Local-first, CLI-first. No external queue/broker. Handlers are pluggable and
designed to be wired to recon/executor modules in Fase 5.
"""

from wth_ai.jobs.manager import JobManager, PolicyError
from wth_ai.jobs.model import (
    JOB_PRIORITIES,
    JOB_STATUSES,
    JOB_TYPES,
    Job,
)
from wth_ai.jobs.policy import PolicyEngine
from wth_ai.jobs.queue import JobQueue
from wth_ai.jobs.store import JobStore
from wth_ai.jobs.worker import WorkerEngine

__all__ = [
    "Job",
    "JOB_TYPES",
    "JOB_STATUSES",
    "JOB_PRIORITIES",
    "JobStore",
    "JobQueue",
    "JobManager",
    "PolicyError",
    "PolicyEngine",
    "WorkerEngine",
]
