from orchestrator.impl.step_base import StepBase


class CreateChangesetStep(StepBase):
    def execute(self, inputs):
        xml_file_path = inputs["xml_file_path"]
        workspace_id = inputs["workspace_id"]
        tdei_token = inputs["tdei_token"]
        # Implement the logic to create a changeset here
        print(f" XML Path at CreateChangesetStep: {xml_file_path}")
        changeset_id = "generated_changeset_id"
        return {"changeset_id": changeset_id}
