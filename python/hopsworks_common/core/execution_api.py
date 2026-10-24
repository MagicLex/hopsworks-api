#
#   Copyright 2022 Logical Clocks AB
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#

import json
from datetime import datetime, timedelta, timezone

from hopsworks_apigen import also_available_as
from hopsworks_common import client, execution
from hopsworks_common.client.exceptions import BackendUnavailableError
from hopsworks_common.core import execution_pod_log


@also_available_as("hopsworks.core.execution_api.ExecutionApi")
class ExecutionApi:
    def _start(
        self,
        job,
        args: str = None,
        *,
        start_time: datetime = None,
        end_time: datetime = None,
        logical_date: datetime = None,
        env_vars: dict = None,
    ):
        """Start an execution.

        If any of start_time/end_time/logical_date/env_vars is provided, POSTs a JSON body
        (endpoint accepts `application/json`); otherwise falls back to the original plain-text
        args body for backwards compatibility.
        """
        _client = client._get_instance()
        path_params = ["project", _client._project_id, "jobs", job.name, "executions"]

        use_json = (
            start_time is not None
            or end_time is not None
            or logical_date is not None
            or env_vars is not None
        )
        if not use_json:
            # The backend has two @POST handlers on this path (text/plain for legacy
            # args and application/json for logical-time params); without an explicit
            # Content-Type Jersey can't dispatch and returns 415.
            headers = {"content-type": "text/plain"}
            return execution.Execution.from_response_json(
                self._post_start(job.name, path_params, headers, args), job
            )

        # Merge start/end_time into envVars if supplied separately; the backend treats the
        # run envVars as the source of truth when set (overrides scheduler-provided HOPS_*).
        merged_env = dict(env_vars) if env_vars else {}
        if start_time is not None:
            merged_env.setdefault("HOPS_START_TIME", _to_env_iso(start_time))
        if end_time is not None:
            merged_env.setdefault("HOPS_END_TIME", _to_env_iso(end_time))

        body = {}
        if args is not None:
            body["args"] = args
        if merged_env:
            body["envVars"] = merged_env
        if logical_date is not None:
            body["logicalDate"] = _to_iso(logical_date)
        if end_time is not None:
            # data interval end mirrors HOPS_END_TIME for a one-shot backfill run.
            body["dataIntervalEnd"] = _to_iso(end_time)

        headers = {"content-type": "application/json"}
        return execution.Execution.from_response_json(
            self._post_start(job.name, path_params, headers, json.dumps(body)), job
        )

    def _post_start(self, job_name, path_params, headers, data):
        """POST an execution start. If the backend was unavailable, find the execution the start may have created."""
        _client = client._get_instance()
        sent_at = datetime.now(timezone.utc)
        try:
            return _client._send_request(
                "POST", path_params, headers=headers, data=data
            )
        except BackendUnavailableError:
            started = self._started_since(job_name, sent_at)
            if len(started) != 1:
                raise
            return started[0]

    def _started_since(self, job_name: str, since: datetime) -> list:
        """The executions of the job submitted after `since`, as the backend returns them."""
        _client = client._get_instance()
        path_params = ["project", _client._project_id, "jobs", job_name, "executions"]
        # The backend stamps submission time with its own clock; the margin covers a small skew.
        since = since - timedelta(seconds=10)
        query_params = {
            "sort_by": "submissiontime:desc",
            "filter_by": "submissiontime_gt:"
            + since.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z",
        }
        response = _client._send_request("GET", path_params, query_params=query_params)
        return (response or {}).get("items") or []

    def _get(self, job, id):
        _client = client._get_instance()
        path_params = [
            "project",
            _client._project_id,
            "jobs",
            job.name,
            "executions",
            id,
        ]

        headers = {"content-type": "application/json"}
        return execution.Execution.from_response_json(
            _client._send_request("GET", path_params, headers=headers), job
        )

    def _get_all(self, job):
        _client = client._get_instance()
        path_params = ["project", _client._project_id, "jobs", job.name, "executions"]

        query_params = {"sort_by": "submissiontime:desc"}

        headers = {"content-type": "application/json"}
        return execution.Execution.from_response_json(
            _client._send_request(
                "GET", path_params, headers=headers, query_params=query_params
            ),
            job,
        )

    def _delete(self, job_name, id):
        _client = client._get_instance()
        path_params = [
            "project",
            _client._project_id,
            "jobs",
            job_name,
            "executions",
            id,
        ]
        _client._send_request("DELETE", path_params)

    def _rerun(self, job_name: str, id: int) -> dict:
        _client = client._get_instance()
        path_params = [
            "project",
            _client._project_id,
            "jobs",
            job_name,
            "executions",
            id,
            "rerun",
        ]
        return _client._send_request("POST", path_params)

    def _stop(self, job_name: str, id: int) -> None:
        _client = client._get_instance()
        path_params = [
            "project",
            _client._project_id,
            "jobs",
            job_name,
            "executions",
            id,
            "status",
        ]
        _client._send_request(
            "PUT",
            path_params=path_params,
            data={"state": "stopped"},
            headers={"Content-Type": "application/json"},
        )

    def _stop_table(self, job_name: str, id: int, table_index: int) -> None:
        _client = client._get_instance()
        path_params = [
            "project",
            _client._project_id,
            "jobs",
            job_name,
            "executions",
            id,
            "tables",
            table_index,
            "status",
        ]
        _client._send_request(
            "PUT",
            path_params=path_params,
            headers={"Content-Type": "application/json"},
        )

    def _get_pod_logs(
        self,
        job_name: str,
        id: int,
        table_index: int | None = None,
        lines: int | None = None,
        limit_bytes: int | None = None,
    ) -> execution_pod_log.ExecutionPodLog:
        _client = client._get_instance()
        path_params = [
            "project",
            _client._project_id,
            "jobs",
            job_name,
            "executions",
            id,
            "pod-logs",
        ]
        query_params = {}
        if table_index is not None:
            query_params["tableIndex"] = table_index
        if lines is not None:
            query_params["lines"] = lines
        if limit_bytes is not None:
            query_params["limitBytes"] = limit_bytes

        return execution_pod_log.ExecutionPodLog.from_response_json(
            _client._send_request(
                "GET",
                path_params=path_params,
                query_params=query_params,
                headers={"content-type": "application/json"},
            )
        )


def _to_env_iso(dt: datetime) -> str:
    """The scheduler's own form of HOPS_START_TIME and HOPS_END_TIME, `...Z` in UTC.

    The job's own SDK parses these, and an older one rejects an offset like `+00:00`.
    """
    from datetime import timezone as _tz

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")


def _to_iso(dt: datetime) -> str:
    """ISO-8601 UTC string, matching what the backend parses into Instant."""
    if dt.tzinfo is None:
        # Treat naive as UTC rather than silently shift by the local offset.
        from datetime import timezone as _tz

        dt = dt.replace(tzinfo=_tz.utc)
    return dt.isoformat()
