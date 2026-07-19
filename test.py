from orchestrator.job_orchestrator import JobOrchestrator

orchestrator = JobOrchestrator(
    steps_dir="orchestrator/steps", jobs_dir="orchestrator/jobs"
)
orchestrator.execute_job(
    job_id="workspace-import-job",
    inputs={"workspace_id": "workspace_123", "tdei_dataset_id": "dataset_456"},
)
