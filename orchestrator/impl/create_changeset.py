import os
import time

from lxml import etree

from orchestrator.impl.step_base import StepBase
from orchestrator.services.osm_service import OSMService

class CreateChangesetStep(StepBase):
    def _local_name(self, element) -> str:
        tag = element.tag
        if isinstance(tag, str) and "}" in tag:
            return tag.rsplit("}", 1)[-1]
        return tag

    def create_changeset_file(self, xml_file_path: str, changeset_id: str) -> str:
        changeset_file_path = os.path.join(self.working_dir, "changeset.osc")

        with open(changeset_file_path, "wb") as op_file:
            with etree.xmlfile(op_file, encoding="utf-8") as xf:
                xf.write_declaration()
                with xf.element(
                    "osmChange", version="0.6", generator="Workspaces Orchestrator"
                ):
                    context = etree.iterparse(xml_file_path, events=("end",))
                    for _, elem in context:
                        local_name = self._local_name(elem)
                        if local_name in {"node", "way", "relation"}:
                            element_id = elem.get("id")
                            if element_id is not None and not element_id.startswith(
                                "-"
                            ):
                                elem.set("id", f"-{element_id}")
                                elem.set("changeset", f"{changeset_id}")
                            if local_name == "way":
                                for nd in elem.iterfind("nd"):
                                    nd_ref = nd.get("ref")
                                    if nd_ref is not None and not nd_ref.startswith(
                                        "-"
                                    ):
                                        nd.set("ref", f"-{nd_ref}")
                            xf.write(elem)
                            elem.clear()

                    del context

        return changeset_file_path

    def execute(self, inputs):
        xml_file_path = inputs["xml_file_path"]
        workspace_id = inputs["workspace_id"]
        tdei_token = inputs["tdei_token"]
        # Implement the logic to create a changeset here
        self.logger.info(f" XML Path at CreateChangesetStep: {xml_file_path}")
        # Create changeset with the osm service
        osm_base_url = "http://osm-proxy:80"
        osm_service = OSMService(tdei_token, osm_base_url, workspace_id)
        changeset_id = osm_service.create_changeset(
            created_by="Workspaces Orchestrator",
            comment=f"Changeset for workspace {workspace_id} Import",
            source="Workspaces Orchestrator",
            url="http://example.com"
        )
        self.logger.info(f"Created changeset with ID: {changeset_id}")
        self.create_changeset_file(xml_file_path, changeset_id)

        return {"changeset_id": changeset_id}
