import time

from orchestrator.impl.step_base import StepBase
from orchestrator.services.osm_service import OSMService
import xml.etree.cElementTree as ET

class UploadChangesetStep(StepBase):
    def execute(self, inputs):
        self.print_inputs(inputs)
        changeset_file = inputs["changeset_file"]
        changeset_id = inputs["changeset_id"]
        posm_auth_token = inputs["posm_auth_token"]
        workspace_id = inputs["workspace_id"]
        # Implement the logic to upload the changeset to POSM here
        self.logger.info(f'Upload changeset file')
        osm_base_url = "http://osm-proxy:80"
        osm_service = OSMService(posm_auth_token, osm_base_url, workspace_id)
        osm_service.changeset = int(changeset_id)
        changesetElement = ET.ElementTree(file=changeset_file)
        osm_service.upload(changesetElement.getroot())
        
        # osm_service.upload()
        # osm_service.upload_changeset(
        #     changeset_file=changeset_file,
        #     changeset_id=changeset_id,
        #     created_by="Workspaces Orchestrator",
        #     comment=f"Uploading changeset {changeset_id} for workspace {workspace_id}",
        #     url="http://example.com"
        # )
        time.sleep(3)
        return {"changeset_result": "success"}
