# from requests import status_codes
import json
import os
import sys
import zipfile

import requests
from tqdm import tqdm


class TDEIService:
    """
    Service used to interact with the TDEI module.
    """

    def __init__(self):
        # print(f"TDEIService initialized")
        # self.base_urls = {
        #     "stage": "https://api-stage.tdei.us",
        #     "prod": "https://api.tdei.us",
        #     "dev": "https://api-dev.tdei.us",
        # }
        self.base_url = "https://api-dev.tdei.us"
        pass

    def upload_tdei_dataset(
        self,
        token: str,
        dataset_file: str,
        metadata_file: str,
        tdei_project_group: str,
        tdei_service_id: str,
        derived_from_dataset_id: str = None,
    ):
        """
        Uploads the TDEI dataset to the storage. and fetches the jobId from upload
        """
        try:
            self.check_access_token_validity(token)
            # Get the local meta file content json
            base_url = self.base_url
            if base_url is None:
                raise ValueError(f"Invalid base URL")
            else:
                upload_url = f"{base_url}/api/v1/osw/upload/{tdei_project_group}/{tdei_service_id}"
                if derived_from_dataset_id:
                    upload_url += f"?derived_from_dataset_id={derived_from_dataset_id}"
                print(f"Uploading to {upload_url}")
                # Upload multi-part form data
                files = {
                    "dataset": open(dataset_file, "rb"),
                    "metadata": open(metadata_file, "rb"),
                }
                headers = {"Authorization": f"Bearer {token}"}
                response = requests.post(upload_url, headers=headers, files=files)
                #    print(response.text)
                if response.status_code == 202:
                    jobId = response.text
                    print(f"Job ID: {jobId}")
                    return jobId
                else:
                    print(response.text)
                    raise ValueError(
                        f"Failed to upload dataset. Received response code {response.status_code} and response {response.text}"
                    )

        except ValueError as e:
            print(f"Error: {e}")
            raise e

    def authenticate(self, username: str, password: str):
        """
        Authenticate the user
        """
        # print(f"Authenticating the user")
        auth_path = "/api/v1/authenticate"
        base_url = self.base_url  # self.base_urls.get(environment, None)
        if base_url is None:
            raise ValueError(f"Invalid base URL")
        else:
            auth_url = f"{base_url}{auth_path}"
            payload = {"username": username, "password": password}
            headers = {"Content-Type": "application/json"}
            response = requests.post(auth_url, headers=headers, json=payload)
            if response.status_code == 200:
                response.json()
                access_token = response.json()["access_token"]
                return access_token
            else:
                print(response.text)
                raise ValueError(
                    f"Failed to authenticate user. Received response code {response.status_code} and response {response.text}"
                )

    def check_access_token_validity(self, token: str):
        """
        Check if the access token is valid.
        """
        # print(f"Checking token validity")
        base_url = self.base_url  # self.base_urls.get(environment, None)
        if base_url is None:
            raise ValueError(f"Base URL not configured")
        else:
            profile_url = f"{base_url}/api/v1/api"
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(profile_url, headers=headers)
            print(response)
            if response.status_code != 200:
                raise ValueError(
                    f"Invalid access token. Received response code {response.status_code}"
                )
            else:
                return True

    def search_tdei_dataset(self, token: str, dataset_name: str):
        path = "/api/v1/datasets"
        base_url = self.base_url  # self.base_urls.get(environment, None)
        parameters = {
            "data_type": "osw",
            "name": dataset_name,
        }
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(base_url + path, headers=headers, params=parameters)
        if response.status_code == 200:
            return response.json()
        else:
            print(response.text)
            raise ValueError(
                f"Failed to search dataset. Received response code {response.status_code} and response {response.text}"
            )

    def search_by_id(self, token: str, dataset_id: str):
        path = "/api/v1/datasets"
        base_url = self.base_url  # self.base_urls.get(environment, None)
        parameters = {
            "data_type": "osw",
            "tdei_dataset_id": dataset_id,
        }
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(base_url + path, headers=headers, params=parameters)
        if response.status_code == 200:
            return response.json()
        else:
            print(response.text)
            raise ValueError(
                f"Failed to search dataset. Received response code {response.status_code} and response {response.text}"
            )

    def get_metadata(self, token: str, dataset_id: str):

        dataset_response = self.search_by_id(token, dataset_id)
        if len(dataset_response) == 0:
            raise ValueError(f"No dataset found with id {dataset_id}")
        return dataset_response[0]

    def fetch_latest_dataset_id(
        self,
        token: str,
        dataset_name: str,
    ):
        dataset_response = self.search_tdei_dataset(token, dataset_name)
        if len(dataset_response) == 0:
            raise ValueError(f"No dataset found with name {dataset_name}")
        return dataset_response[0]["tdei_dataset_id"]

    def download_dataset(self, token: str, dataset_id: str, output_path: str):
        path = f"/api/v1/osw/{dataset_id}"
        params = {"format": "osm", "file_version": "latest"}
        base_url = self.base_url  # self.base_urls.get(environment, None)
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(base_url + path, headers=headers, params=params)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                for chunk in tqdm(
                    response.iter_content(chunk_size=2048),
                    desc=f"Downloading {dataset_id}",
                    file = sys.stdout
                ):
                    f.write(chunk)
            print(f"Downloaded dataset to {output_path}")
            return output_path
        else:
            print(response.text)
            raise ValueError(
                f"Failed to download dataset. Received response code {response.status_code} and response {response.text}"
            )

    def clone_dataset(
        self,
        token: str,
        dataset_id: str,
        tdei_project_group: str,
        tdei_service_id: str,
        metadata_file: str,
    ):
        # /api/v1/dataset/clone/{tdei_dataset_id}/{tdei_project_group_id}/{tdei_service_id}
        path = (
            f"/api/v1/dataset/clone/{dataset_id}/{tdei_project_group}/{tdei_service_id}"
        )
        base_url = self.base_url  # self.base_urls.get(environment, None)
        headers = {"Authorization": f"Bearer {token}"}
        files = {
            "file": open(metadata_file, "rb"),
        }
        response = requests.post(base_url + path, headers=headers, files=files)
        if response.status_code == 200:
            jobId = response.text
            print(f"Cloned dataset with ID: {jobId}")
            return jobId
        else:
            print(response.text)
            raise ValueError(
                f"Failed to clone dataset. Received response code {response.status_code} and response {response.text}"
            )

    def extract_downloaded_dataset(self, dataset_zip_path: str, output_path: str):
        with zipfile.ZipFile(dataset_zip_path, "r") as zip_ref:
            zip_ref.extractall(output_path)
        # Remove the original zip path
        os.remove(dataset_zip_path)
        # List the .zip files now
        zip_files = [f for f in os.listdir(output_path) if f.endswith(".zip")]
        if len(zip_files) == 0:
            return output_path
        else:
            output_files_path = os.path.join(output_path, "files")
            if not os.path.exists(output_files_path):
                os.makedirs(output_files_path)
            # Extract the zip file
            with zipfile.ZipFile(
                os.path.join(output_path, zip_files[0]), "r"
            ) as zip_ref:
                zip_ref.extractall(output_files_path)
            return output_files_path
