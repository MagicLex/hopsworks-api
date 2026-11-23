#
#   Copyright 2026 Hopsworks AB
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

import time
from datetime import datetime, timedelta

import pytest
from hopsworks_common import job
from hopsworks_common.client import exceptions
from hopsworks_common.core import dataset_api
from hopsworks_common.engine import execution_engine


class TestWaitUntilFinished:
    """_wait_until_finished skips the log wait only for job types that write their logs first."""

    @pytest.fixture
    def clock(self, mocker):
        # The engine's own references to time and datetime, so other threads keep the real clock.
        now = [0.0]
        start = datetime(2026, 1, 1)
        fake_time = mocker.Mock(wraps=time)
        fake_time.sleep.side_effect = lambda seconds: now.__setitem__(
            0, now[0] + seconds
        )
        fake_datetime = mocker.Mock(wraps=datetime)
        fake_datetime.now.side_effect = lambda: start + timedelta(seconds=now[0])
        mocker.patch.object(execution_engine, "time", fake_time)
        mocker.patch.object(execution_engine, "datetime", fake_datetime)
        return now

    def _run(self, mocker, job_type, body, existing, timeout=None):
        """Wait on the execution the backend answers with `body`; the Logs dataset holds `existing`."""
        client = mocker.patch("hopsworks_common.client._get_instance").return_value
        client._project_id = 2167
        client._send_request.return_value = body
        mocker.patch.object(
            dataset_api.DatasetApi, "exists", lambda self, path: path in existing
        )
        j = job.Job(
            id=1,
            name="j",
            creation_time=None,
            config={},
            job_type=job_type,
            creator=None,
        )
        execution = mocker.Mock(id=body["id"])
        return execution_engine.ExecutionEngine()._wait_until_finished(
            j, execution, timeout
        )

    @pytest.mark.parametrize("job_type", ["PYTHON", "DOCKER"])
    def test_a_failed_job_without_stdout_log_returns_at_once(
        self, mocker, backend_fixtures, clock, job_type
    ):
        failed = backend_fixtures["execution"]["get_python_failed"]["response"]
        with pytest.raises(exceptions.JobExecutionException, match="failed"):
            self._run(mocker, job_type, failed, existing={failed["stderrPath"]})

        assert clock[0] == 0

    @pytest.mark.parametrize("job_type", ["SPARK", "PYSPARK", "RAY", "INGESTION"])
    def test_other_jobs_without_their_logs_still_wait_for_them(
        self, mocker, backend_fixtures, clock, job_type
    ):
        # Spark and Ray set the final status before they aggregate the logs, and
        # multi-table ingestion merges its logs after the final state.
        result = self._run(
            mocker,
            job_type,
            backend_fixtures["execution"]["get_python_finished"]["response"],
            existing=set(),
        )

        assert result.final_status == "SUCCEEDED"
        assert clock[0] >= execution_engine._LOG_AGGREGATION_SECONDS

    def test_a_finished_python_job_with_its_logs_returns_at_once(
        self, mocker, backend_fixtures, clock
    ):
        finished = backend_fixtures["execution"]["get_python_finished"]["response"]
        result = self._run(
            mocker,
            "PYTHON",
            finished,
            existing={finished["stdoutPath"], finished["stderrPath"]},
        )

        assert result.state == "FINISHED"
        assert clock[0] == 0

    def test_a_queue_timeout_fails_at_once(self, mocker, backend_fixtures, clock):
        # KubeJobsMonitor sets QUEUE_TIMEOUT with final status FAILED, and no logs.
        failed = backend_fixtures["execution"]["get_python_failed"]["response"]
        with pytest.raises(exceptions.JobExecutionException, match="QUEUE_TIMEOUT"):
            self._run(
                mocker, "PYTHON", {**failed, "state": "QUEUE_TIMEOUT"}, existing=set()
            )

        assert clock[0] == 0

    @pytest.mark.parametrize(
        "state, logs",
        # YarnJob writes no log on INITIALIZATION_FAILED, only stderr on APP_MASTER_START_FAILED.
        [("INITIALIZATION_FAILED", ()), ("APP_MASTER_START_FAILED", ("stderrPath",))],
    )
    def test_a_spark_job_that_failed_to_start_ends_the_wait(
        self, mocker, backend_fixtures, clock, state, logs
    ):
        # YarnJob sets the state alone: the final status stays UNDEFINED.
        spark = backend_fixtures["execution"]["get_spark_finished"]["response"]
        body = {**spark, "state": state, "finalStatus": "UNDEFINED"}
        # Bounded, so a success that stays None fails instead of waiting forever.
        with pytest.raises(exceptions.JobExecutionException, match=state):
            self._run(
                mocker,
                "SPARK",
                body,
                existing={spark[log] for log in logs},
                timeout=600,
            )

        assert clock[0] == 0
