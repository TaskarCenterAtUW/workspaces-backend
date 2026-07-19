# Contains reference to all the existing registry steps
import os
import re
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel, Field


class StepDefinition(BaseModel):
    name: str
    description: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    id: str
    class_name: str


class WorkflowStepReference(BaseModel):
    name: str  # The execution instance alias (e.g., "step_1")
    step_id: str  # References StepDefinition.id
    arguments: Dict[str, Any] = Field(default_factory=dict, alias="with")


class JobDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    workflow: List[WorkflowStepReference]


class OrchestratorRegistry:
    def __init__(self, steps_dir: str, jobs_dir: str):
        self.steps_dir = steps_dir
        self.jobs_dir = jobs_dir
        self.steps: Dict[str, StepDefinition] = {}
        self.jobs: Dict[str, JobDefinition] = {}
        self.load_registry()

    def load_registry(self):
        # Load steps
        for filename in os.listdir(self.steps_dir):
            if filename.endswith(".yaml") or filename.endswith(".yml"):
                with open(os.path.join(self.steps_dir, filename), "r") as f:
                    step_data = yaml.safe_load(f)
                    step = StepDefinition(**step_data)
                    self.steps[step.id] = step

        # Load jobs
        for filename in os.listdir(self.jobs_dir):
            if filename.endswith(".yaml") or filename.endswith(".yml"):
                with open(os.path.join(self.jobs_dir, filename), "r") as f:
                    job_data = yaml.safe_load(f)
                    job = JobDefinition(**job_data)
                    self.jobs[job.id] = job

    def get_step(self, step_id: str) -> StepDefinition:
        return self.steps.get(step_id)

    def get_job(self, job_id: str) -> JobDefinition:
        return self.jobs.get(job_id)
