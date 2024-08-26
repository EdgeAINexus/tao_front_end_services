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

"""Authentication utils session modules"""
import functools
import threading
from datetime import datetime, timezone
from handlers.mongo_handler import MongoHandler
import sys

__SESSION_EXPIRY_SECONDS__ = 86400  # Equal to 24 hours


def synchronized(wrapped):
    """Decorator function for thread synchronization"""
    lock = threading.Lock()

    @functools.wraps(wrapped)
    def _wrap(*args, **kwargs):
        with lock:
            return wrapped(*args, **kwargs)
    return _wrap


@synchronized
def set(user_id, org_name, token, extra_user_metadata):
    """Save session in DB"""
    mongo = MongoHandler("tao", "users")
    mongo.create_text_index("org_name")
    session = {'id': user_id}
    if extra_user_metadata:
        session.update(extra_user_metadata)

    tmp_session = mongo.find_one(session)
    if tmp_session:
        tmp_session.update(session)
        session = tmp_session

    token_present = False
    for idx, token_info in enumerate(session.get("token_info", [])):
        if token_info.get("token") == token:
            token_present = True
            session["token_info"][idx]["last_modified"] = datetime.now(tz=timezone.utc)
    if not token_present:
        if not session.get("token_info"):
            session["token_info"] = []
        session["token_info"].append({"token": token,
                                      "created_on": datetime.now(tz=timezone.utc),
                                      "last_modified": datetime.now(tz=timezone.utc)})
    session['org_name'] = org_name
    session['last_modified'] = datetime.now(tz=timezone.utc)
    mongo.upsert({'id': user_id}, session)
    return session


@synchronized
def get(token, org_name):
    """Load session from DB"""
    session = {}
    mongo = MongoHandler("tao", "users")
    users = mongo.find({"org_name": org_name})
    for user in users:
        try:
            for token_info in user.get('token_info', []):
                if token_info.get('token', 'invalid') == token and isinstance(token_info.get('last_modified', False), datetime):
                    dt_delta = datetime.now(tz=timezone.utc) - token_info['last_modified']
                    if dt_delta.total_seconds() < __SESSION_EXPIRY_SECONDS__:
                        session = user
                        break
        except Exception as e:
            print("Warning, error while retrieving user token: ", str(e), file=sys.stderr)

    return session
