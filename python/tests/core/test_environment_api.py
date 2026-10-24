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

import json
from types import SimpleNamespace

import pytest
from hopsworks_common import client
from hopsworks_common.client.exceptions import EnvironmentException
from hopsworks_common.command import Command
from hopsworks_common.core import environment_api


class TestEnvironmentApi:
    def _api(self, mocker):
        api = environment_api.EnvironmentApi()
        mock_client = mocker.MagicMock()
        mock_client._project_id = 1
        mock_client._send_request.return_value = {"name": "myenv"}
        mocker.patch("hopsworks_common.client._get_instance", return_value=mock_client)
        return api

    def test_create_environment_forwards_an_explicit_timeout(self, mocker):
        """The parameter is only worth exposing if it reaches the wait it is meant to bound."""
        # Arrange
        api = self._api(mocker)
        mock_await = mocker.patch.object(
            api._environment_engine, "_await_environment_command"
        )

        # Act
        api.create_environment("myenv", timeout=42)

        # Assert
        mock_await.assert_called_once_with("myenv", 42)

    def test_create_environment_defaults_the_timeout_to_none(self, mocker):
        """Unset has to arrive as `None`, which is what tells the engine to use its own default."""
        # Arrange
        api = self._api(mocker)
        mock_await = mocker.patch.object(
            api._environment_engine, "_await_environment_command"
        )

        # Act
        api.create_environment("myenv")

        # Assert
        mock_await.assert_called_once_with("myenv", None)

    def test_create_environment_does_not_wait_when_not_awaiting(self, mocker):
        # Arrange
        api = self._api(mocker)
        mock_await = mocker.patch.object(
            api._environment_engine, "_await_environment_command"
        )

        # Act
        api.create_environment("myenv", await_creation=False, timeout=42)

        # Assert
        mock_await.assert_not_called()

    def test_create_environment_clones_a_project_environment(self, mocker):
        """A clone names its source and no base image: Hopsworks 300029 points a Data scientist here."""
        # Arrange
        api = self._api(mocker)
        mocker.patch.object(api._environment_engine, "_await_environment_command")
        sent = client._get_instance()._send_request

        # Act
        api.create_environment(
            "shop-app-env-scott", source_environment_name="shop-app-env"
        )

        # Assert
        assert json.loads(sent.call_args.kwargs["data"]) == {
            "name": "shop-app-env-scott",
            "sourceEnvironment": "shop-app-env",
        }

    def test_create_environment_from_a_base_image_is_unchanged(self, mocker):
        # Arrange
        api = self._api(mocker)
        mocker.patch.object(api._environment_engine, "_await_environment_command")
        sent = client._get_instance()._send_request

        # Act
        api.create_environment("myenv", base_environment_name="python-agent-pipeline")

        # Assert
        assert json.loads(sent.call_args.kwargs["data"]) == {
            "name": "myenv",
            "baseImage": {"name": "python-agent-pipeline", "description": None},
        }

    def _build(self, mocker, api, *statuses):
        """The environment's CREATE command as each poll sees it."""
        mocker.patch.object(api._environment_engine, "POLL_INTERVAL", 0)
        return mocker.patch.object(
            api._environment_engine,
            "_poll_commands_environment",
            side_effect=[
                SimpleNamespace(_commands=[Command(status=s, op="CREATE")])
                for s in statuses
            ],
        )

    def test_await_creation_returns_once_the_build_succeeded(self, mocker):
        # Arrange
        api = self._api(mocker)
        poll = self._build(mocker, api, "ONGOING", "SUCCESS")

        # Act
        api.await_creation("shop-app-env-meb10000")

        # Assert
        assert poll.call_count == 2
        assert poll.call_args.args[0] == "shop-app-env-meb10000"

    def test_await_creation_raises_when_the_build_failed(self, mocker):
        # Arrange
        api = self._api(mocker)
        self._build(mocker, api, "FAILED")

        # Act, Assert
        with pytest.raises(EnvironmentException, match="Command failed"):
            api.await_creation("shop-app-env-meb10000")


class TestCreatedForApp:
    """Against a real HTTP server: the request that leaves the client."""

    @staticmethod
    def _create(**kwargs):
        from tests.real_hopsworks import API, hopsworks_server

        def answer(method, path, body):
            return 200, {"name": body["name"]}

        with hopsworks_server(answer) as seen:
            environment_api.EnvironmentApi().create_environment(
                "shop-app-env-scott", await_creation=False, **kwargs
            )
        assert [(m, p) for m, p, _ in seen] == [
            ("POST", f"{API}/python/environments/shop-app-env-scott")
        ]
        return seen[0][2]

    def test_a_clone_carries_the_app_it_was_made_for(self):
        body = self._create(
            source_environment_name="shop-app-env", created_for_app="shop"
        )

        assert body == {
            "name": "shop-app-env-scott",
            "sourceEnvironment": "shop-app-env",
            "createdForApp": "shop",
        }

    def test_a_create_from_a_base_image_carries_the_app_it_was_made_for(self):
        body = self._create(created_for_app="shop")

        assert body["createdForApp"] == "shop"
        assert body["baseImage"]["name"] == "python-feature-pipeline"

    def test_without_an_app_the_request_has_no_created_for_app(self):
        body = self._create(source_environment_name="shop-app-env")

        assert "createdForApp" not in body
