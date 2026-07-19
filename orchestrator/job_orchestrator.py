from orchestrator.job_executor import JobExecutor
from orchestrator.registry import OrchestratorRegistry


class JobOrchestrator:
    def __init__(self, steps_dir: str, jobs_dir: str):
        self.registry = OrchestratorRegistry(steps_dir, jobs_dir)

    def execute_job(self, job_id: str, inputs: dict):
        job_definition = self.registry.get_job(job_id)
        if not job_definition:
            raise ValueError(f"Job with ID {job_id} not found.")

        # Here you would implement the logic to execute the job based on the job_definition
        # and the provided inputs. This is a placeholder for demonstration purposes.
        print(f"Executing job: {job_definition.name} with inputs: {inputs}")
        # Return a mock result for demonstration
        job_executor = JobExecutor(self.registry)
        return job_executor.execute_job(job_id, inputs)

    def get_step(self, step_id: str):
        return self.registry.get_step(step_id)

    def get_job(self, job_id: str):
        return self.registry.get_job(job_id)
