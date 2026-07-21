import os
import shutil
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

from orchestrator.job_executor import JobExecutor
from orchestrator.registry import OrchestratorRegistry


class JobOrchestrator:
    def __init__(self, steps_dir: str, jobs_dir: str):
        self.registry = OrchestratorRegistry(steps_dir, jobs_dir)
        self.thread_pool = ThreadPoolExecutor(
            max_workers=5
        )  # Adjust the number of workers as needed

    def execute_job(self, job_id: str, inputs: dict):
        job_definition = self.registry.get_job(job_id)
        if not job_definition:
            raise ValueError(f"Job with ID {job_id} not found.")

        # Here you would implement the logic to execute the job based on the job_definition
        # and the provided inputs. This is a placeholder for demonstration purposes.
        print(f"Executing job: {job_definition.name}")
        # Return a mock result for demonstration
        job_executor = JobExecutor(self.registry)
        job_working_folder, job_unique_id = self.create_job_working_directory()
        # job_status = job_executor.execute_job(job_id, inputs, job_working_folder)

        job_execution_thread = self.thread_pool.submit(
            job_executor.execute_job, job_id, inputs, job_working_folder
        )
        job_execution_thread.add_done_callback(
            lambda x: self.on_job_completion(job_unique_id, x)
        )

        return {"job_id": job_unique_id}

    def get_step(self, step_id: str):
        return self.registry.get_step(step_id)

    def get_job(self, job_id: str):
        return self.registry.get_job(job_id)

    def create_job_working_directory(self) -> str:
        job_unique_id = str(uuid.uuid4())
        job_folder = os.path.join("jobs", job_unique_id)
        os.makedirs(job_folder)
        return job_folder, job_unique_id

    def remove_job_working_directory(self, job_folder: str):
        if os.path.exists(job_folder):
            shutil.rmtree(job_folder)

    def on_job_completion(self, job_id: str, status: Future):
        # Implement any post-job completion logic here, such as logging or notifications.
        job_folder = os.path.join("jobs", job_id)
        # self.remove_job_working_directory(job_folder=job_folder)
        result = status.result()
        print(f"Job {job_id} completed with status: {result.get('job_id')}")
