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

"""MongoDB init script"""
from kubernetes import client, config
import sys
import os

mongodb_crd_group = 'mongodbcommunity.mongodb.com'
mongodb_crd_version = 'v1'
mongodb_crd_plural = 'mongodbcommunity'
mongodb_crd_name = 'mongodb'
mongodb_desired_replica_count = int(os.getenv('MONGODESIREDREPLICAS', '3'))
mongodb_storage_class = os.getenv('MONGOSTORAGECLASS', 'nfs-client')
mongodb_storage_size = os.getenv('MONGOSTORAGESIZE', '100Gi')
mongodb_secret = os.getenv('IMAGEPULLSECRET', 'imagepullsecret')
mongodb_namespace = 'default'


def create_mongodb_replicaset():
    """Creates a MongoDB replicaset"""
    config.load_incluster_config()

    # Initialize the custom objects API
    api_instance = client.CustomObjectsApi()

    mongodb_body = {
        "apiVersion": "mongodbcommunity.mongodb.com/v1",
        "kind": "MongoDBCommunity",
        "metadata": {
            "name": mongodb_crd_name,
        },
        "spec": {
            "members": mongodb_desired_replica_count,
            "type": "ReplicaSet",
            "version": "6.0.5",
            "security": {
                "authentication": {
                    "modes": ["SCRAM"]
                }
            },
            "users": [
                {
                    "name": "default-user",
                    "db": "admin",
                    "passwordSecretRef": {
                        "name": mongodb_secret,
                        "key": ".dockerconfigjson"
                    },
                    "roles": [
                        {
                            "name": "root",
                            "db": "admin"
                        }
                    ],
                    "scramCredentialsSecretName": "scram-secret",
                }
            ],
            "additionalMongodConfig": {
                "storage.wiredTiger.engineConfig.journalCompressor": "zlib"
            },
            "statefulSet": {
                "spec": {
                    "volumeClaimTemplates": [
                        {
                            "metadata": {
                                "name": "data-volume"
                            },
                            "spec": {
                                "storageClassName": mongodb_storage_class,
                                "accessModes": ["ReadWriteMany"],
                                "resources": {
                                    "requests": {
                                        "storage": mongodb_storage_size
                                    }
                                }
                            }
                        },
                        {
                            "metadata": {
                                "name": "logs-volume"
                            },
                            "spec": {
                                "storageClassName": mongodb_storage_class,
                                "accessModes": ["ReadWriteMany"],
                                "resources": {
                                    "requests": {
                                        "storage": mongodb_storage_size
                                    }
                                }
                            }
                        }
                    ]
                }
            }
        },
    }

    # Create the custom resource in the specified namespace
    try:
        api_instance.create_namespaced_custom_object(mongodb_crd_group, mongodb_crd_version, mongodb_namespace, mongodb_crd_plural, mongodb_body)
        print("MongoDB replicaset created successfully.", file=sys.stderr)
    except Exception as e:
        print(f"Failed to create MongoDB replicaset': {e}", file=sys.stderr)
        raise e


def patch_mongodb_replicaset():
    """Patch MongoDB Replicaset"""
    config.load_incluster_config()

    # Initialize the custom objects API
    api_instance = client.CustomObjectsApi()

    updated_spec = {
        "spec": {
            "members": mongodb_desired_replica_count
        }
    }

    try:
        api_instance.patch_namespaced_custom_object(mongodb_crd_group, mongodb_crd_version, mongodb_namespace, mongodb_crd_plural, name=mongodb_crd_name, body=updated_spec)
        print("MongoDB replicaset patched successfully.", file=sys.stderr)
    except Exception as e:
        print(f"Failed to patch MongoDB replicaset': {e}", file=sys.stderr)
        raise e


if __name__ == "__main__":
    config.load_incluster_config()

    api_instance = client.CustomObjectsApi()

    try:
        api_response = api_instance.list_namespaced_custom_object(group=mongodb_crd_group, version=mongodb_crd_version, namespace=mongodb_namespace, plural=mongodb_crd_plural)
        print("MongoDB response: ", api_response, file=sys.stderr)
        items = api_response['items']
        if len(items) == 0:
            create_mongodb_replicaset()
        else:
            status = items[0].get('status', {})
            phase = status.get('phase', '')
            if phase == 'Running' and mongodb_desired_replica_count != status.get('currentStatefulSetReplicas', 0):
                patch_mongodb_replicaset()
            elif phase != 'Running':
                print("Unknown MongoDB Replicaset status: ", status, file=sys.stderr)
                raise Exception("Unknown MongoDB Replicaset status")
    except Exception as e:
        print("Exception when calling CustomObjectsApi: ", str(e), file=sys.stderr)
        raise e
