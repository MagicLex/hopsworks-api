#
#   Copyright 2024 Hopsworks AB
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

import itertools
import logging
import os
import stat
import threading

import pytest
import requests
import urllib3
from hopsworks_common.client import auth
from hopsworks_common.client.exceptions import BackendUnavailableError
from hopsworks_common.core import factory_api
from hopsworks_common.core.users_api import UsersApi
from hsfs.client.base import Client
from hsfs.client.exceptions import RestAPIError
from hsfs.core.validation_report_api import ValidationReportApi
from hsml.core import serving_api

from tests.util import changes_environ


class TestBaseClient:
    @changes_environ
    def test_valid_token_no_retires(self, mocker):
        # Arrange
        os.environ[Client.REST_ENDPOINT] = "True"
        client = self._init_test_client()

        path_params = [
            "variables",
            "versions",
        ]

        # Mock the requests library
        mocker.patch("requests.sessions.Session.prepare_request")
        ok_response = requests.Response()
        ok_response.status_code = 200
        ok_response._content = ""
        mocker.patch("requests.sessions.Session.send", return_value=ok_response)

        spy_retry_token_expired = mocker.spy(client, "_retry_token_expired")

        # Act
        client._send_request("GET", path_params)

        # Assert
        assert spy_retry_token_expired.call_count == 0

    @changes_environ
    def test_invalid_token_retires(self, mocker):
        # Arrange
        os.environ[Client.REST_ENDPOINT] = "True"
        client = self._init_test_client()

        path_params = [
            "variables",
            "versions",
        ]

        # Mock the requests library
        mocker.patch("requests.sessions.Session.prepare_request")
        unauthorized_response = requests.Response()
        unauthorized_response.status_code = 401
        mocker.patch(
            "requests.sessions.Session.send", return_value=unauthorized_response
        )

        # Mock and spy the client
        mocker.patch("hopsworks_common.client.base.Client._read_jwt")
        spy_retry_token_expired = mocker.spy(client, "_retry_token_expired")

        # Act
        with pytest.raises(RestAPIError):
            client._send_request("GET", path_params)

        # Assert
        assert spy_retry_token_expired.call_count == 10

    @changes_environ
    def test_invalid_token_retires_backoff_break(self, mocker):
        # Arrange
        os.environ[Client.REST_ENDPOINT] = "True"
        client = self._init_test_client()

        path_params = [
            "variables",
            "versions",
        ]

        # Mock the requests library
        mocker.patch("requests.sessions.Session.prepare_request")
        # setup unauthorized response
        unauthorized_response = requests.Response()
        unauthorized_response.status_code = 401

        # setup ok response
        ok_response = requests.Response()
        ok_response.status_code = 200
        ok_response._content = ""

        mocker.patch(
            "requests.sessions.Session.send",
            side_effect=[unauthorized_response] * 5 + [ok_response],
        )

        # Mock and spy the client
        mocker.patch("hopsworks_common.client.base.Client._read_jwt")
        spy_retry_token_expired = mocker.spy(client, "_retry_token_expired")

        # Act
        client._send_request("GET", path_params)

        # Assert
        assert spy_retry_token_expired.call_count == 5

    @changes_environ
    def test_api_key_unauthorized_raises_without_token_retry(self, mocker):
        # Arrange
        os.environ[Client.REST_ENDPOINT] = "True"
        client = self._init_test_client()
        client._auth = auth.ApiKeyAuth("prefix.secret")

        mocker.patch("requests.sessions.Session.prepare_request")
        unauthorized_response = requests.Response()
        unauthorized_response.status_code = 401
        unauthorized_response._content = (
            b'{"errorCode": 320013, "errorMsg": "API key not found in the database"}'
        )
        mocker.patch(
            "requests.sessions.Session.send", return_value=unauthorized_response
        )
        read_jwt = mocker.patch("hopsworks_common.client.base.Client._read_jwt")
        spy_retry_token_expired = mocker.spy(client, "_retry_token_expired")

        # Act
        with pytest.raises(RestAPIError) as e:
            client._send_request("GET", ["project", 119, "credentials"])

        # Assert
        assert spy_retry_token_expired.call_count == 0
        read_jwt.assert_not_called()
        assert e.value.response.status_code == 401
        assert e.value.error_code == 320013
        assert "API key not found in the database" in str(e.value)

    def _init_test_client(self):
        client = Client()
        client._connected = True
        client._base_url = ""
        client._auth = auth.BearerAuth("token")
        client._verify = False
        client._session = requests.session()
        client.TOKEN_EXPIRED_RETRY_INTERVAL = 0  # Disable wait for tests

        return client

    @pytest.mark.skipif(
        os.name == "nt", reason="Windows cannot replace a file a reader has open"
    )
    def test_replace_file_is_never_seen_partial(self, tmp_path):
        path = tmp_path / "ca_chain.pem"
        # Two contents of one length, so every write replaces the file and a partial read shows as a length.
        contents = ["A" * 200_000, "B" * 200_000]
        Client._replace_file(str(path), contents[0])
        stop = threading.Event()
        seen = set()

        def write():
            turn = 0
            while not stop.is_set():
                turn += 1
                Client._replace_file(str(path), contents[turn % 2])

        writer = threading.Thread(target=write)
        writer.start()
        try:
            for _ in range(2000):
                seen.add(len(path.read_text()))
        finally:
            stop.set()
            writer.join()

        assert seen == {200_000}
        assert sorted(p.name for p in tmp_path.iterdir()) == ["ca_chain.pem"]

    @pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX file modes")
    def test_replace_file_keeps_the_file_private(self, tmp_path):
        path = tmp_path / "client_key.pem"
        path.write_text("old")
        os.chmod(path, 0o600)
        previous = os.umask(0o022)
        try:
            Client._replace_file(str(path), "new")
        finally:
            os.umask(previous)

        assert path.read_text() == "new"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_replace_file_leaves_identical_content_alone(self, tmp_path, mocker):
        path = tmp_path / "ca_chain.pem"
        path.write_text("same")
        replace = mocker.patch("os.replace")

        Client._replace_file(str(path), "same")

        replace.assert_not_called()
        assert list(tmp_path.iterdir()) == [path]

    def test_replace_file_retries_while_the_target_is_held(self, tmp_path, mocker):
        path = tmp_path / "ca_chain.pem"
        path.write_text("old")
        real_replace = os.replace
        held = [PermissionError("in use"), PermissionError("in use")]

        def replace_once_released(src, dst):
            if held:
                raise held.pop()
            real_replace(src, dst)

        replace = mocker.patch("os.replace", side_effect=replace_once_released)
        mocker.patch("time.sleep")

        Client._replace_file(str(path), "new")

        assert replace.call_count == 3
        assert path.read_text() == "new"
        assert list(tmp_path.iterdir()) == [path]

    def test_replace_file_removes_the_temporary_file_on_failure(self, tmp_path, mocker):
        path = tmp_path / "ca_chain.pem"
        mocker.patch("os.replace", side_effect=OSError("disk full"))

        with pytest.raises(OSError, match="disk full"):
            Client._replace_file(str(path), "content")

        assert list(tmp_path.iterdir()) == []


def _response(status, content=b"", content_type="application/json"):
    response = requests.Response()
    response.status_code = status
    response._content = content
    response.headers["Content-Type"] = content_type
    return response


def _refused():
    return requests.exceptions.ConnectionError(
        urllib3.exceptions.MaxRetryError(
            None,
            "/hopsworks-api/api/project",
            urllib3.exceptions.NewConnectionError(
                "HTTPSConnection(host='hopsworks.example', port=443)",
                "Failed to establish a new connection: [Errno 111] Connection refused",
            ),
        )
    )


_PAYARA_404 = (
    b"<!DOCTYPE html><html><head><title>Payara Server  6.2025.4 #badassfish - Error report</title>"
    b"</head><body><h1>HTTP Status 404 - Not Found</h1></body></html>"
)


class TestRetryWhileBackendUnavailable:
    @pytest.fixture
    def client(self, mocker):
        mocker.patch("requests.sessions.Session.prepare_request")
        mocker.patch("hopsworks_common.client.base.time.sleep")
        client = Client()
        client._connected = True
        client._base_url = "https://hopsworks.example:443"
        client._auth = auth.ApiKeyAuth("prefix.secret")
        client._verify = False
        client._session = requests.session()
        return client

    def _send(self, mocker, *outcomes):
        return mocker.patch("requests.sessions.Session.send", side_effect=outcomes)

    def test_connection_error_then_success(self, client, mocker):
        send = self._send(mocker, _refused(), _response(200, b'{"id": 1}'))

        # Refused before anything was sent, so even a non-idempotent POST is retried.
        assert client._send_request(
            "POST", ["project", 119, "jobs", "j", "executions"]
        ) == {"id": 1}
        assert send.call_count == 2

    def test_503_on_get_is_retried(self, client, mocker):
        send = self._send(mocker, _response(503), _response(503), _response(200, b"{}"))

        assert client._send_request("GET", ["project", 119]) == {}
        assert send.call_count == 3

    def test_503_on_non_idempotent_post_is_not_resent(self, client, mocker):
        send = self._send(mocker, _response(503), _response(200, b"{}"))

        with pytest.raises(BackendUnavailableError) as e:
            client._send_request(
                "POST", ["project", 119, "featurestores", 67, "featuregroups"]
            )

        assert send.call_count == 1
        assert str(e.value) == (
            "Backend unavailable during POST /hopsworks-api/api/project/119/featurestores/67/featuregroups; "
            "the request may or may not have been applied."
        )
        assert e.value.response.status_code == 503

    def test_503_on_idempotent_post_is_retried(self, client, mocker):
        send = self._send(mocker, _response(503), _response(200, b"{}"))

        client._send_request(
            "POST",
            [
                "project",
                119,
                "featurestores",
                67,
                "storageconnectors",
                "s3",
                "credentials",
                "validate",
            ],
        )

        assert send.call_count == 2

    def test_payara_html_404_on_get_is_retried(self, client, mocker):
        send = self._send(
            mocker, _response(404, _PAYARA_404, "text/html"), _response(200, b"{}")
        )

        assert client._send_request("GET", ["project", 119]) == {}
        assert send.call_count == 2

    def test_json_404_is_not_retried(self, client, mocker):
        send = self._send(
            mocker,
            _response(404, b'{"errorCode": 130009, "errorMsg": "Job not found"}'),
            _response(200, b"{}"),
        )

        with pytest.raises(RestAPIError) as e:
            client._send_request("GET", ["project", 119, "jobs", "missing"])

        assert send.call_count == 1
        assert e.value.error_code == 130009

    @changes_environ
    def test_exhausted_budget_gives_a_clear_error(self, client, mocker, caplog):
        os.environ[Client.RETRY_BUDGET_ENV] = "120"
        mocker.patch(
            "hopsworks_common.client.base.time.monotonic",
            side_effect=itertools.count(0, 50),
        )
        send = mocker.patch(
            "requests.sessions.Session.send", return_value=_response(503)
        )

        with (
            caplog.at_level(logging.WARNING, logger="hopsworks_common.client.base"),
            pytest.raises(BackendUnavailableError) as e,
        ):
            client._send_request("GET", ["project", 119])

        assert send.call_count == 3
        assert str(e.value) == (
            "Backend unavailable during GET /hopsworks-api/api/project/119; "
            "gave up after 3 tries in 120 s (HTTP 503)."
        )
        assert [r.getMessage() for r in caplog.records] == [
            "Backend unavailable during GET /hopsworks-api/api/project/119 (HTTP 503); retrying for up to 120 s.",
            "Hopsworks still unavailable, 20s left",
            "Backend unavailable during GET /hopsworks-api/api/project/119: gave up after 3 tries in 120 s (HTTP 503).",
        ]

    @changes_environ
    def test_one_still_unavailable_line_per_minute(self, client, mocker, caplog):
        os.environ[Client.RETRY_BUDGET_ENV] = "150"
        mocker.patch(
            "hopsworks_common.client.base.time.monotonic",
            side_effect=itertools.count(0, 25),
        )
        mocker.patch("requests.sessions.Session.send", return_value=_response(503))

        with (
            caplog.at_level(logging.WARNING, logger="hopsworks_common.client.base"),
            pytest.raises(BackendUnavailableError),
        ):
            client._send_request("GET", ["project", 119])

        assert [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("Hopsworks still unavailable")
        ] == [
            "Hopsworks still unavailable, 75s left",
            "Hopsworks still unavailable, 25s left",
        ]

    def test_a_long_stall_gives_one_still_unavailable_line(
        self, client, mocker, caplog
    ):
        mocker.patch(
            "hopsworks_common.client.base.time.monotonic",
            side_effect=[0, 5, 200, 201, 202, 250],
        )
        self._send(mocker, *[_response(503)] * 5, _response(200, b"{}"))

        with caplog.at_level(logging.WARNING, logger="hopsworks_common.client.base"):
            client._send_request("GET", ["project", 119])

        assert [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("Hopsworks still unavailable")
        ] == [
            "Hopsworks still unavailable, 400s left",
            "Hopsworks still unavailable, 350s left",
        ]

    def test_a_call_with_its_own_timeout_is_sent_once(self, client, mocker):
        send = self._send(mocker, _response(503), _response(200, b"{}"))

        with pytest.raises(RestAPIError) as e:
            client._send_request("GET", ["project", 119], timeout=5)

        assert send.call_count == 1
        assert e.value.response.status_code == 503

    def test_a_read_timeout_with_its_own_timeout_is_raised_as_it_is(
        self, client, mocker
    ):
        send = self._send(
            mocker, requests.exceptions.ReadTimeout(), _response(200, b"{}")
        )

        with pytest.raises(requests.exceptions.ReadTimeout):
            client._send_request("POST", ["project", 119, "metastoresync"], timeout=60)

        assert send.call_count == 1

    def test_read_timeout_on_get_is_retried(self, client, mocker):
        send = self._send(
            mocker, requests.exceptions.ReadTimeout(), _response(200, b"{}")
        )

        assert client._send_request("GET", ["project", 119]) == {}
        assert send.call_count == 2

    def test_read_timeout_on_non_idempotent_post_is_not_resent(self, client, mocker):
        send = self._send(
            mocker, requests.exceptions.ReadTimeout(), _response(200, b"{}")
        )

        with pytest.raises(BackendUnavailableError) as e:
            client._send_request(
                "POST", ["project", 119, "featurestores", 67, "featuregroups"]
            )

        assert send.call_count == 1
        assert isinstance(e.value.__cause__, requests.exceptions.ReadTimeout)

    @changes_environ
    def test_bad_budget_names_the_variable_and_the_value(self, client, mocker):
        os.environ[Client.RETRY_BUDGET_ENV] = "2m"
        send = self._send(mocker, _response(200, b"{}"))

        with pytest.raises(ValueError) as e:
            client._send_request("GET", ["project", 119])

        assert str(e.value) == (
            "HOPSWORKS_REQUEST_RETRY_SECONDS must be a whole number of seconds, got '2m'."
        )
        assert send.call_count == 0

    def test_one_log_line_when_a_series_starts_and_one_when_it_ends(
        self, client, mocker, caplog
    ):
        self._send(
            mocker,
            _refused(),
            _response(502),
            _response(504),
            _response(503),
            _response(200, b"{}"),
        )

        with caplog.at_level(logging.WARNING, logger="hopsworks_common.client.base"):
            client._send_request("GET", ["project", 119])

        assert [r.getMessage() for r in caplog.records] == [
            "Backend unavailable during GET /hopsworks-api/api/project/119 "
            "(NewConnectionError: HTTPSConnection(host='hopsworks.example', port=443): "
            "Failed to establish a new connection: [Errno 111] Connection refused); retrying for up to 600 s.",
            "Backend unavailable during GET /hopsworks-api/api/project/119: the backend answered after 5 tries.",
        ]

    @changes_environ
    def test_zero_budget_turns_retrying_off(self, client, mocker):
        os.environ[Client.RETRY_BUDGET_ENV] = "0"
        send = self._send(mocker, _response(503), _response(200, b"{}"))

        with pytest.raises(RestAPIError):
            client._send_request("GET", ["project", 119])

        assert send.call_count == 1

    def test_a_404_on_a_resent_delete_counts_as_done(self, client, mocker):
        """The try that got the 503 may have deleted it before the answer was lost."""
        send = self._send(mocker, _response(503), _response(404, b'{"errorCode": 1}'))

        assert client._send_request("DELETE", ["project", 119, "jobs", "j"]) is None
        assert send.call_count == 2

    def test_a_404_on_the_first_delete_still_raises(self, client, mocker):
        send = self._send(mocker, _response(404, b'{"errorCode": 1}'))

        with pytest.raises(RestAPIError) as e:
            client._send_request("DELETE", ["project", 119, "jobs", "j"])

        assert e.value.response.status_code == 404
        assert send.call_count == 1

    @pytest.fixture
    def project_client(self, client, mocker):
        client._project_id = 119
        client._project_name = "proj"
        mocker.patch("hopsworks_common.client._get_instance", return_value=client)
        mocker.patch("hsml.core.serving_api.client._get_instance", return_value=client)
        return client

    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(
                lambda: UsersApi().resend_confirmation_email(42), id="resend-email"
            ),
            pytest.param(lambda: UsersApi().activate_user(42), id="activate-user"),
            pytest.param(lambda: UsersApi().reject_user(42), id="reject-user"),
            pytest.param(
                lambda: factory_api._update("etl", "name: etl\n"),
                id="factory-new-version",
            ),
        ],
    )
    def test_a_503_on_a_put_with_side_effects_is_sent_once(
        self, project_client, mocker, call
    ):
        """These PUTs send an email or store a new version, so a resend would repeat it."""
        send = self._send(mocker, _response(503), _response(200, b"{}"))

        with pytest.raises(RestAPIError) as e:
            call()

        assert e.value.response.status_code == 503
        assert send.call_count == 1

    def test_a_read_timeout_on_the_resend_email_put_is_sent_once(
        self, project_client, mocker
    ):
        """The backend may have sent the email before the read timed out."""
        send = self._send(
            mocker, requests.exceptions.ReadTimeout(), _response(200, b"{}")
        )

        with pytest.raises(requests.exceptions.ReadTimeout):
            UsersApi().resend_confirmation_email(42)

        assert send.call_count == 1

    def test_a_503_on_a_validation_report_is_sent_once(self, project_client, mocker):
        """Each PUT stores a new report and triggers its alerts."""
        send = self._send(mocker, _response(503), _response(200, b"{}"))
        api = ValidationReportApi(feature_store_id=67, feature_group_id=13)
        mocker.patch.object(api._variable_api, "_get_version", return_value="4.2.0")

        with pytest.raises(RestAPIError):
            api._create(mocker.Mock(json=lambda: "{}"))

        assert send.call_count == 1

    def test_a_503_on_a_new_deployment_version_is_sent_once(
        self, project_client, mocker
    ):
        """A PUT with newVersion stores a new deployment version each time."""
        send = self._send(mocker, _response(503), _response(200, b"{}"))

        with pytest.raises(RestAPIError):
            serving_api.ServingApi()._put(
                mocker.Mock(json=lambda: "{}"), new_version=True
            )

        assert send.call_count == 1

    def test_a_503_on_a_deployment_edit_is_retried(self, project_client, mocker):
        send = self._send(mocker, _response(503), _response(200, b"{}"))

        serving_api.ServingApi()._put(mocker.Mock(json=lambda: "{}"))

        assert send.call_count == 2
