from orchestrator.impl.create_changeset import CreateChangesetStep
from orchestrator.job_orchestrator import JobOrchestrator
from orchestrator.services.tdei_service import TDEIService

tdei_service = TDEIService()

access_token = tdei_service.authenticate("nareshd@gaussiansolutions.com", "Testing01*")

orchestrator = JobOrchestrator(
    steps_dir="orchestrator/steps", jobs_dir="orchestrator/jobs"
)

result1 = orchestrator.execute_job(
    job_id="workspace-import-job",
    inputs={
        "workspace_id": "workspace_123",
        "tdei_dataset_id": "7b320840-f131-484e-a80b-a1048e661896",
        "tdei_token": access_token,
    },
)

# xml_file_path = "jobs/01b37727-f201-49b3-920b-5fe9227382a5/tdei-extracted/osm.7b320840-f131-484e-a80b-a1048e661896.xml"

# changeset_create_step = CreateChangesetStep(
#     "", "jobs/01b37727-f201-49b3-920b-5fe9227382a5/"
# )
# changeset_create_step.execute(
#     {"xml_file_path": xml_file_path, "workspace_id": "", "tdei_token": "token"}
# )
