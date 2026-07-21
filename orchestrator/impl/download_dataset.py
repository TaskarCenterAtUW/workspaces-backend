"""
Class that executes the import dataset step.

"""

import os
import time
import uuid

from orchestrator.impl.step_base import StepBase
from orchestrator.services.tdei_service import TDEIService


class DownloadDatasetStep(StepBase):
    def execute(self, inputs: dict) -> dict:
        workspace_id = inputs.get(
            "workspace_id"
        )  # Assuming the input is named 'workspace_id' for demonstration
        if not workspace_id:
            raise ValueError("Missing required input: workspace_id")
        self.print_inputs(inputs)
        dataset_id = inputs.get("tdei_dataset_id")
        tdei_token = inputs.get("tdei_token")
        print(f"Downloading dataset  {dataset_id} into {self.working_dir}")
        output_file_path = os.path.join(self.working_dir, "dataset.zip")
        tdei_service = TDEIService()
        output_path = tdei_service.download_dataset(
            tdei_token, dataset_id, output_file_path
        )
        extracted_path = os.path.join(self.working_dir, "tdei-extracted")
        extracted_files_path = tdei_service.extract_downloaded_dataset(
            output_file_path, extracted_path
        )
        osm_xml_file_path = self.discover_osm_xml_file(extracted_files_path)
        if osm_xml_file_path:
            return {
                "status": "success",
                "downloaded_file_path": osm_xml_file_path,
            }
        else:
            return {"status": "failed", "downloaded_file_path": ""}

    def discover_osm_xml_file(self, extracted_directory: str) -> str:
        xml_files = [f for f in os.listdir(extracted_directory) if f.endswith(".xml")]
        if len(xml_files) > 0:
            return os.path.join(
                extracted_directory, xml_files[0]
            )  # Full path of the xml file
        return None
