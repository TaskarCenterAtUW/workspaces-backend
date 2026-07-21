import time

from orchestrator.impl.step_base import StepBase
from orchestrator.services.osm_service import OSMService

class UploadChangesetStep(StepBase):
    def execute(self, inputs):
        changeset_file = inputs["changeset_file"]
        changeset_id = inputs["changeset_id"]
        posm_auth_token = inputs["posm_auth_token"]
        workspace_id = inputs["workspace_id"]
        # Implement the logic to upload the changeset to POSM here
        self.logger.info(f'Upload changeset file')
        osm_base_url = "http://osm-cgimap:8000"
        osm_service = OSMService(posm_auth_token, osm_base_url, workspace_id)
        time.sleep(3)
        return {"changeset_result": "success"}
