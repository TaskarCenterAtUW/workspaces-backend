"""
Class to handle execution of single job.
This class is responsible for orchestrating the execution of a job by utilizing the OrchestratorRegistry to retrieve job and step definitions.
It provides methods to execute a job, retrieve step definitions, and retrieve job definitions, retrieve the current step
being executed, and retrieve the current job being executed.
"""

import importlib
import logging
import re
from functools import reduce

from orchestrator.impl.step_base import StepBase

VARIABLE_PATTERN = re.compile(r"\$\{([^}]+)\}")

class JobExecutor:
    def __init__(self, orchestrator_registry):
        self.registry = orchestrator_registry
        self.current_step = None
        self.current_job = None
        self.job_inputs = None
        self.working_directory = None
        self.context_dictionary = (
            {}
        )  # Dictionary to hold context data for the job execution
        self.logger = logging.getLogger(self.__class__.__name__)

    def execute_job(self, job_id: str, inputs: dict, working_dir: str):
        job_definition = self.registry.get_job(job_id)
        if not job_definition:
            raise ValueError(f"Job with ID {job_id} not found.")

        self.current_job = job_definition
        # Reset per-run context so no values leak across sequential executions.
        self.context_dictionary = {
            "job": {"inputs": inputs},
            "steps": {},
        }
        self.working_directory = working_dir
        # Here you would implement the logic to execute the job based on the job_definition
        # and the provided inputs. This is a placeholder for demonstration purposes.
        self.logger.info(f"Executing job: {job_definition.name}")
        self.job_inputs = inputs
        # Start executing each step in the job's workflow
        for step_ref in job_definition.workflow:
            step_definition = self.registry.get_step(step_ref.step_id)
            if not step_definition:
                raise ValueError(f"Step with ID {step_ref.step_id} not found.")

            self.current_step = step_definition
            self.logger.info(
                f"Executing step: {step_definition.name}"
            )
            # Here you would call the actual step execution logic, passing in the arguments.
            # For demonstration, we just print the step execution.
            # Figure out the class name from the step definition and dynamically import and execute it.
            class_name = step_definition.class_name
            step_class = self._import_class(class_name)
            if not step_class:
                raise ValueError(f"Step class {class_name} not found.")
            step_instance = step_class(step_definition, self.working_directory)
            step_arguments = self.apply_context_values(step_ref.arguments or {})
            step_outputs = step_instance.execute(step_arguments)
            self.context_dictionary["steps"][step_ref.step_id] = {
                "outputs": step_outputs
            }
            # After executing the step, you might want to collect outputs and pass them to the next step.

        # Return a mock result for demonstration
        return {"status": "success", "job_id": job_id, "outputs": {}}

    def get_step(self, step_id: str):
        return self.registry.get_step(step_id)

    def get_job(self, job_id: str):
        return self.registry.get_job(job_id)

    def get_current_step(self):
        return self.current_step

    def get_current_job(self):
        return self.current_job

    def apply_context_values(self, inputs: dict) -> dict:
        """Return a copy of inputs with ${...} placeholders resolved from context."""
        resolved_inputs = {}
        for key, value in inputs.items():
            resolved_inputs[key] = self.resolve_context_value(value)
        return resolved_inputs

    def resolve_context_value(self, value):
        """Resolve a context value from the context dictionary."""
        if isinstance(value, dict):
            return self.apply_context_values(value)

        if isinstance(value, list):
            return [self.resolve_context_value(item) for item in value]

        if not isinstance(value, str):
            return value

        match = VARIABLE_PATTERN.fullmatch(value)
        if match:
            context_key = match.group(1)
            resolved = self.get_nested_value(context_key)
            # Keep original placeholder when no context value exists.
            return value if resolved is None else resolved

        return value

    def get_nested_value(self, key_path: str):
        try:
            return reduce(
                lambda d, k: d[k], key_path.split("."), self.context_dictionary
            )
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _import_class(class_path: str) -> type:
        """Dynamically import ``module.path.ClassName`` and return the class."""
        module_path, _, class_name = class_path.rpartition(".")
        if not module_path:
            raise ImportError(
                f"Invalid class path {class_path!r}. "
                "Must be 'module.path.ClassName'."
            )
        module = importlib.import_module(module_path)
        klass = getattr(module, class_name)
        if not (isinstance(klass, type) and issubclass(klass, StepBase)):
            raise TypeError(
                f"{class_path!r} must be a subclass of StepBase, got {klass!r}."
            )
        return klass
