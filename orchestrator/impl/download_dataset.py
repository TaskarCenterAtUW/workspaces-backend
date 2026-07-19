"""
Class that executes the import dataset step.

"""

from orchestrator.impl.step_base import StepBase


class DownloadDatasetStep(StepBase):
    def execute(self, inputs: dict) -> dict:
        source_url = inputs.get(
            "workspace_id"
        )  # Assuming the input is named 'workspace_id' for demonstration
        if not source_url:
            raise ValueError("Missing required input: source_url")

        # Here you would implement the logic to download the dataset from the source URL.
        # For demonstration purposes, we will just print the URL and return a mock output.
        print(f"Downloading dataset from: {source_url}")

        # Return a mock output for demonstration
        return {
            "status": "success",
            "downloaded_file_path": "/path/to/downloaded/file.csv",
        }
