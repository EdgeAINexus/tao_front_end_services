# Copyright (c) 2024, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""DGX job's kubernetes controller"""
import sys
import json
import time
import asyncio
import traceback
from datetime import datetime
from kubernetes import client, config

from handlers.ngc_handler import send_ngc_api_request

config.load_incluster_config()
api_instance = client.CustomObjectsApi()
job_tracker = {}
logs_tracker = set([])

BCP_STARTING_STATUS = ("CREATED", "QUEUED", "STARTING", "PENDING_TERMINATION", "PREEMPTED", "PREEMPTED_BY_ADMIN", "PENDING_STORAGE_CREATION", "RESOURCE_CONSUMPTION_REQUEST_IN_PROGRESS", "RESOURCE_GRANTED", "REQUESTING_RESOURCE")
BCP_SUCCESS_TERMINAL_STATUS = ("FINISHED_SUCCESS", "KILLED_BY_USER")
BCP_FAILURE_TERMINAL_STATUS = ("UNKNOWN", "FAILED_RUN_LIMIT_EXCEEDED", "FAILED", "CANCELED", "TASK_LOST", "KILLED_BY_SYSTEM", "KILLED_BY_ADMIN", "INFINITY_POOL_MISSING", "RESOURCE_RELEASED", "IM_INTERNAL_ERROR", "RESOURCE_GRANT_DENIED", "RESOURCE_LIMIT_EXCEEDED")


def create_dgx_job(dgx_cr):
    """Construct requests call for triggering Job on DGX cloud"""
    config.load_incluster_config()

    assert dgx_cr.get("metadata", "")
    assert dgx_cr.get("spec", "")

    namespace = dgx_cr['metadata']['namespace']
    custom_resource_name = dgx_cr['metadata']['name']

    user_id = dgx_cr["spec"].get("user_id")
    name = dgx_cr["spec"].get("name")
    command = dgx_cr["spec"].get("command")
    dockerImageName = dgx_cr["spec"].get("dockerImageName")
    orgName = dgx_cr["spec"].get("orgName")
    teamName = dgx_cr["spec"].get("teamName")
    aceName = dgx_cr["spec"].get("aceName")
    aceInstance = dgx_cr["spec"].get("aceInstance")
    runPolicy = dgx_cr["spec"].get("runPolicy")
    envs = dgx_cr["spec"].get("envs")
    runPolicy["totalRuntimeSeconds"] = 4 * 7 * 24 * 60 * 60
    resultContainerMountPoint = dgx_cr["spec"].get("resultContainerMountPoint")
    workspaceMounts = dgx_cr["spec"].get("workspaceMounts")

    request_body = {"name": name,
                    "aceInstance": aceInstance,
                    "dockerImageName": dockerImageName.replace("nvcr.io/", ""),
                    "aceName": aceName,
                    "jobPriority": "HIGH",
                    "workspaceMounts": workspaceMounts,
                    "command": command,
                    "runPolicy": runPolicy,
                    "envs": envs,
                    "userLabels": ["api___tao"],
                    "reservedLabels": ["_wl___computer_vision"],
                    "resultContainerMountPoint": resultContainerMountPoint}
    endpoint = f"https://api.ngc.nvidia.com/v2/org/{orgName}/team/{teamName}/jobs"
    job_create_response = send_ngc_api_request(user_id=user_id, endpoint=endpoint, requests_method="POST", request_body=json.dumps(request_body), refresh=False, json=True)
    if job_create_response.status_code not in [200, 415]:
        print("Error while creating DGX job", job_create_response.json(), file=sys.stderr)
        print("Custom resource values", request_body, file=sys.stderr)
        updated_cr_error = api_instance.patch_namespaced_custom_object(
            group="dgx-job-manager.nvidia.io",
            version="v1alpha1",
            namespace=namespace,
            plural="dgxjobs",
            name=custom_resource_name,
            body={"status": {"phase": "Error"}}
        )
        return updated_cr_error
    if job_create_response.status_code in [415]:
        print("Retrying NGC job submit request", file=sys.stderr)
        time.sleep(5)
        return create_dgx_job(dgx_cr)
    if job_create_response.status_code == 200:
        job_create_response_json = job_create_response.json()
        job_id = job_create_response_json.get("job", {}).get("resultset", {}).get("id", {})
        updated_spec = {"job_id": str(job_id)}
        # Patch the custom resource with the updated job_id
        updated_cr = api_instance.patch_namespaced_custom_object(
            group="dgx-job-manager.nvidia.io",
            version="v1alpha1",
            namespace=namespace,
            plural="dgxjobs",
            name=custom_resource_name,
            body={"spec": updated_spec},
        )
        return updated_cr
    return dgx_cr


def delete_dgx_job(dgx_cr):
    """Construct requests call for deleting Job on DGX cloud"""
    user_id = dgx_cr["spec"].get("user_id")
    orgName = dgx_cr["spec"].get("orgName")
    job_id = dgx_cr["spec"].get("job_id")
    endpoint = f"https://api.ngc.nvidia.com/v2/org/{orgName}/jobs/{job_id}"
    job_info_response = send_ngc_api_request(user_id=user_id, endpoint=endpoint, requests_method="GET", request_body={}, refresh=False)
    if job_info_response.status_code == 200:
        job_delete_response = send_ngc_api_request(user_id=user_id, endpoint=endpoint, requests_method="DELETE", request_body={}, refresh=False)
        if job_delete_response.status_code not in (200, 422):
            print("job_delete_response", job_delete_response, job_delete_response.json(), file=sys.stderr)


def print_job_logs(user_id, job_id, orgName, custom_resource_name):
    """Print logs of DGX job on controller pod"""
    job_logs_endpoint = f"https://api.ngc.nvidia.com/v2/org/{orgName}/resultsets/{job_id}/file/joblog.log"
    job_logs_response = send_ngc_api_request(user_id=user_id, endpoint=job_logs_endpoint, requests_method="GET", request_body={}, refresh=False)
    job_logs = job_logs_response.text
    current_time = datetime.utcnow()
    formatted_time = current_time.strftime('%d/%b/%Y:%H:%M:%S')
    for line in job_logs.split("\n"):
        if line:
            print(f"{formatted_time},{job_id},{custom_resource_name}: {line}", file=sys.stderr)


def update_status(job_tracker, logs_tracker):
    """Update the status of the custom resources based on status of NGC job"""
    for _, dgx_cr in job_tracker.items():
        custom_resource_name = dgx_cr["metadata"].get('name')
        user_id = dgx_cr["spec"].get("user_id")
        orgName = dgx_cr["spec"].get("orgName")
        job_id = dgx_cr["spec"].get("job_id")
        endpoint = f"https://api.ngc.nvidia.com/v2/org/{orgName}/jobs/{job_id}"
        job_monitor_response = send_ngc_api_request(user_id=user_id, endpoint=endpoint, requests_method="GET", request_body={}, refresh=False)
        job_monitor_response_json = job_monitor_response.json()
        status = "Pending"
        if job_monitor_response_json.get("jobStatusHistory", []):
            status = job_monitor_response_json["jobStatusHistory"][0].get("status")
            if status in BCP_STARTING_STATUS:
                status = "Pending"
            elif status == "RUNNING":
                status = "Running"
            elif status in BCP_SUCCESS_TERMINAL_STATUS:
                if custom_resource_name not in logs_tracker:
                    print_job_logs(user_id, job_id, orgName, custom_resource_name)
                logs_tracker.add(custom_resource_name)
                status = "Done"
            elif status in BCP_FAILURE_TERMINAL_STATUS:
                if custom_resource_name not in logs_tracker:
                    print_job_logs(user_id, job_id, orgName, custom_resource_name)
                logs_tracker.add(custom_resource_name)
                status = "Error"
        try:
            namespace = dgx_cr['metadata']['namespace']
            updated_spec = {"phase": status}

            # Patch the custom resource with the updated status
            api_instance.patch_namespaced_custom_object(
                group="dgx-job-manager.nvidia.io",
                version="v1alpha1",
                namespace=namespace,
                plural="dgxjobs",
                name=custom_resource_name,
                body={"status": updated_spec}
            )
        except Exception:
            pass


async def process_events():
    """Process DGX JOB events"""
    global job_tracker
    job_tracker = job_tracker if 'job_tracker' in globals() else {}
    global logs_tracker
    logs_tracker = logs_tracker if 'logs_tracker' in globals() else {}

    while True:
        try:
            # Fetch the list of DGX job custom resources
            dgx_jobs = api_instance.list_cluster_custom_object(
                group="dgx-job-manager.nvidia.io",
                version="v1alpha1",
                plural="dgxjobs",
            )

            for item in dgx_jobs['items']:
                custom_resource_name = item['metadata']['name']

                if custom_resource_name not in job_tracker:
                    # Handle added event
                    print(f"DGX CR added: {custom_resource_name}", file=sys.stderr)
                    updated_item = create_dgx_job(item)
                    job_tracker[custom_resource_name] = updated_item

            # Check for deleted jobs
            existing_jobs = set(job_tracker.keys())
            current_jobs = set(item['metadata']['name'] for item in dgx_jobs['items'])
            deleted_jobs = existing_jobs - current_jobs

            for deleted_job in deleted_jobs:
                print(f"DGX CR deleted: {deleted_job}")
                delete_dgx_job(job_tracker[deleted_job])
                update_status(job_tracker, logs_tracker)
                del job_tracker[deleted_job]
            await asyncio.sleep(10)

        except Exception as e:
            print(traceback.format_exc(), file=sys.stderr)
            print(f"Error in the event processing loop: {str(e)}", file=sys.stderr)


async def main():
    """Controller Main function"""
    global job_tracker
    job_tracker = job_tracker if 'job_tracker' in globals() else {}
    global logs_tracker
    logs_tracker = logs_tracker if 'logs_tracker' in globals() else {}

    asyncio.create_task(process_events())
    while True:
        update_status(job_tracker, logs_tracker)
        await asyncio.sleep(10)

if __name__ == "__main__":
    # Run the main function asynchronously
    asyncio.run(main())
