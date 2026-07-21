"""
Base class for steps in the orchestrator. All steps should inherit from this class and implement the `execute` method.
The class shall also implement the `validate` method to ensure that the inputs meet the step's requirements.
"""
import logging

class StepBase:
    def __init__(self, step_definition, working_dir: str):
        self.step_definition = step_definition
        self.working_dir = working_dir
        self.logger = logging.getLogger(self.__class__.__name__)

    def execute(self, inputs: dict) -> dict:
        """
        Execute the step with the given inputs.

        Args:
            inputs (dict): A dictionary of input values for the step.

        Returns:
            dict: A dictionary of output values from the step.
        """
        raise NotImplementedError("Subclasses must implement the execute method.")

    def validate(self, inputs: dict) -> bool:
        """
        Validate the inputs for the step.

        Args:
            inputs (dict): A dictionary of input values for the step.

        Returns:
            bool: True if the inputs are valid, False otherwise.
        """
        raise NotImplementedError("Subclasses must implement the validate method.")
    
    def print_inputs(self, inputs: dict):
        """
        Print the inputs for debugging purposes.

        Args:
            inputs (dict): A dictionary of input values for the step.
        """
        self.logger.info(f"Inputs for step {self.step_definition.name}")
        for key, value in inputs.items():
            self.logger.info(f"{key}: {value}")