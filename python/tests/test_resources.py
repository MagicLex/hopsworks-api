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

import copy

import pytest
from hsml import resources


class TestResources:
    # Resources

    def test_from_response_json_cpus(self, backend_fixtures):
        # Arrange
        json = backend_fixtures["resources"]["get_only_cores"]["response"]

        # Act
        r = resources.Resources.from_response_json(json)

        # Assert
        assert r.cores == json["cores"]
        assert r.memory is None
        assert r.gpus is None

    def test_from_response_json_memory(self, backend_fixtures):
        # Arrange
        json = backend_fixtures["resources"]["get_only_memory"]["response"]

        # Act
        r = resources.Resources.from_response_json(json)

        # Assert
        assert r.cores is None
        assert r.memory is json["memory"]
        assert r.gpus is None

    def test_from_response_json_gpus(self, backend_fixtures):
        # Arrange
        json = backend_fixtures["resources"]["get_only_gpus"]["response"]

        # Act
        r = resources.Resources.from_response_json(json)

        # Assert
        assert r.cores is None
        assert r.memory is None
        assert r.gpus == json["gpus"]

    def test_from_response_json_cores_and_memory(self, backend_fixtures):
        # Arrange
        json = backend_fixtures["resources"]["get_cores_and_memory"]["response"]

        # Act
        r = resources.Resources.from_response_json(json)

        # Assert
        assert r.cores == json["cores"]
        assert r.memory == json["memory"]
        assert r.gpus is None

    def test_from_response_json_cores_and_gpus(self, backend_fixtures):
        # Arrange
        json = backend_fixtures["resources"]["get_cores_and_gpus"]["response"]

        # Act
        r = resources.Resources.from_response_json(json)

        # Assert
        assert r.cores == json["cores"]
        assert r.memory is None
        assert r.gpus == json["gpus"]

    def test_from_response_json_memory_and_gpus(self, backend_fixtures):
        # Arrange
        json = backend_fixtures["resources"]["get_memory_and_gpus"]["response"]

        # Act
        r = resources.Resources.from_response_json(json)

        # Assert
        assert r.cores is None
        assert r.memory == json["memory"]
        assert r.gpus == json["gpus"]

    def test_from_response_json_cores_memory_and_gpus(self, backend_fixtures):
        # Arrange
        json = backend_fixtures["resources"]["get_cores_memory_and_gpus"]["response"]

        # Act
        r = resources.Resources.from_response_json(json)

        # Assert
        assert r.cores == json["cores"]
        assert r.memory == json["memory"]
        assert r.gpus == json["gpus"]

    # ComponentResources

    # - from response json

    def test_from_response_json_component_resources(self, mocker):
        # Arrange
        res = {"something": "here"}
        json_decamelized = {"key": "value"}
        mock_humps_decamelize = mocker.patch(
            "humps.decamelize", return_value=json_decamelized
        )
        mock_from_json = mocker.patch(
            "hsml.resources.ComponentResources.from_json",
            return_value="from_json_result",
        )

        # Act
        result = resources.ComponentResources.from_response_json(res)

        # Assert
        assert result == "from_json_result"
        mock_humps_decamelize.assert_called_once_with(res)
        mock_from_json.assert_called_once_with(json_decamelized)

    # - constructor

    def test_constructor_component_resources_default(self):
        # Act
        pr = resources.PredictorResources(num_instances=1)

        # Assert
        assert pr.requests is None
        assert pr.limits is None

    def test_constructor_component_resources(self, backend_fixtures):
        # Arrange
        json = backend_fixtures["resources"][
            "get_component_resources_num_instances_requests_and_limits"
        ]["response"]

        # Act
        expected = copy.deepcopy(json)
        pr = resources.PredictorResources(
            num_instances=json["num_instances"],
            requests=json["requests"],
            limits=json["limits"],
        )

        # Assert
        assert pr.num_instances == expected["num_instances"]
        assert pr.requests.to_dict() == expected["requests"]
        assert pr.limits.to_dict() == expected["limits"]

    # - to dict

    @pytest.mark.parametrize(
        "cls,key",
        [
            (resources.PredictorResources, "predictorResources"),
            (resources.TransformerResources, "transformerResources"),
        ],
    )
    def test_to_dict_unset_is_omitted(self, cls, key):
        assert cls(num_instances=1).to_dict()[key] == {}

    @pytest.mark.parametrize(
        "cls,key",
        [
            (resources.PredictorResources, "predictorResources"),
            (resources.TransformerResources, "transformerResources"),
        ],
    )
    def test_to_dict_sends_each_half_as_given(self, cls, key):
        requests = {"cores": 0.5, "memory": 64, "gpus": 0}
        limits = {"cores": 1, "memory": 128}

        both = cls(requests=dict(requests), limits=dict(limits)).to_dict()[key]
        only_limits = cls(limits=dict(limits)).to_dict()[key]
        only_requests = cls(requests=dict(requests)).to_dict()[key]

        assert both == {"requests": requests, "limits": limits}
        assert only_limits == {"limits": limits}
        assert only_requests == {"requests": requests}

    def test_to_dict_omits_unset_fields_of_a_half(self):
        pr = resources.PredictorResources(limits=resources.Resources(memory=3072))

        assert pr.to_dict()["predictorResources"] == {"limits": {"memory": 3072}}

    def test_read_back_to_dict_keeps_backend_values(self, backend_fixtures):
        json = backend_fixtures["resources"][
            "get_component_resources_requested_instances_and_predictor_resources"
        ]["response"]

        expected = copy.deepcopy(json["predictor_resources"])

        pr = resources.PredictorResources.from_response_json(json)

        assert pr.to_dict()["predictorResources"] == expected

    def test_num_instances_property_warns(self):
        pr = resources.PredictorResources(num_instances=1)

        with pytest.warns(DeprecationWarning, match="num_instances"):
            _ = pr.num_instances

        with pytest.warns(DeprecationWarning, match="num_instances"):
            pr.num_instances = 2

    # - extract fields from json

    def test_extract_fields_from_json_component_resources_with_key(
        self, backend_fixtures
    ):
        # Arrange
        json = backend_fixtures["resources"][
            "get_component_resources_requested_instances_and_predictor_resources"
        ]["response"]
        copy_json = copy.deepcopy(json)
        resources.ComponentResources.RESOURCES_CONFIG_KEY = "predictor_resources"
        resources.ComponentResources.NUM_INSTANCES_KEY = "requested_instances"

        # Act
        kwargs = resources.ComponentResources.extract_fields_from_json(copy_json)

        # Assert
        assert kwargs["num_instances"] == json["requested_instances"]
        assert isinstance(kwargs["requests"], resources.Resources)
        assert (
            kwargs["requests"].cores == json["predictor_resources"]["requests"]["cores"]
        )
        assert (
            kwargs["requests"].memory
            == json["predictor_resources"]["requests"]["memory"]
        )
        assert (
            kwargs["requests"].gpus == json["predictor_resources"]["requests"]["gpus"]
        )
        assert isinstance(kwargs["limits"], resources.Resources)
        assert kwargs["limits"].cores == json["predictor_resources"]["limits"]["cores"]
        assert (
            kwargs["limits"].memory == json["predictor_resources"]["limits"]["memory"]
        )
        assert kwargs["limits"].gpus == json["predictor_resources"]["limits"]["gpus"]

    def test_extract_fields_from_json_component_resources(
        self, mocker, backend_fixtures
    ):
        # Arrange
        json = backend_fixtures["resources"][
            "get_component_resources_requested_instances_and_predictor_resources_alternative"
        ]["response"]
        copy_json = copy.deepcopy(json)
        resources.ComponentResources.RESOURCES_CONFIG_KEY = "predictor_resources"
        resources.ComponentResources.NUM_INSTANCES_KEY = "requested_instances"

        # Act
        kwargs = resources.ComponentResources.extract_fields_from_json(copy_json)

        # Assert
        assert kwargs["num_instances"] == json["num_instances"]
        assert isinstance(kwargs["requests"], resources.Resources)
        assert kwargs["requests"].cores == json["resources"]["requests"]["cores"]
        assert kwargs["requests"].memory == json["resources"]["requests"]["memory"]
        assert kwargs["requests"].gpus == json["resources"]["requests"]["gpus"]
        assert isinstance(kwargs["limits"], resources.Resources)
        assert kwargs["limits"].cores == json["resources"]["limits"]["cores"]
        assert kwargs["limits"].memory == json["resources"]["limits"]["memory"]
        assert kwargs["limits"].gpus == json["resources"]["limits"]["gpus"]

    def test_extract_fields_from_json_component_resources_flatten(
        self, backend_fixtures
    ):
        # Arrange
        json = backend_fixtures["resources"][
            "get_component_resources_num_instances_requests_and_limits"
        ]["response"]
        copy_json = copy.deepcopy(json)
        resources.ComponentResources.RESOURCES_CONFIG_KEY = "predictor_resources"
        resources.ComponentResources.NUM_INSTANCES_KEY = "requested_instances"

        # Act
        kwargs = resources.ComponentResources.extract_fields_from_json(copy_json)

        # Assert
        assert kwargs["num_instances"] == json["num_instances"]
        assert isinstance(kwargs["requests"], resources.Resources)
        assert kwargs["requests"].cores == json["requests"]["cores"]
        assert kwargs["requests"].memory == json["requests"]["memory"]
        assert kwargs["requests"].gpus == json["requests"]["gpus"]
        assert isinstance(kwargs["limits"], resources.Resources)
        assert kwargs["limits"].cores == json["limits"]["cores"]
        assert kwargs["limits"].memory == json["limits"]["memory"]
        assert kwargs["limits"].gpus == json["limits"]["gpus"]

    # PredictorResources

    def test_from_response_json_predictor_resources(self, backend_fixtures):
        json = backend_fixtures["resources"][
            "get_component_resources_num_instances_requests_and_limits"
        ]["response"]

        # Act
        r = resources.PredictorResources.from_response_json(json)

        # Assert
        assert r.num_instances == json["num_instances"]
        assert r.requests.cores == json["requests"]["cores"]
        assert r.requests.memory == json["requests"]["memory"]
        assert r.requests.gpus == json["requests"]["gpus"]
        assert r.limits.cores == json["limits"]["cores"]
        assert r.limits.memory == json["limits"]["memory"]
        assert r.limits.gpus == json["limits"]["gpus"]

    def test_from_response_json_predictor_resources_specific_keys(
        self, backend_fixtures
    ):
        json = backend_fixtures["resources"][
            "get_component_resources_requested_instances_and_predictor_resources"
        ]["response"]

        # Act
        r = resources.PredictorResources.from_response_json(json)

        # Assert
        assert r.num_instances == json["requested_instances"]
        assert r.requests.cores == json["predictor_resources"]["requests"]["cores"]
        assert r.requests.memory == json["predictor_resources"]["requests"]["memory"]
        assert r.requests.gpus == json["predictor_resources"]["requests"]["gpus"]
        assert r.limits.cores == json["predictor_resources"]["limits"]["cores"]
        assert r.limits.memory == json["predictor_resources"]["limits"]["memory"]
        assert r.limits.gpus == json["predictor_resources"]["limits"]["gpus"]

    # TransformerResources

    def test_from_response_json_transformer_resources(self, backend_fixtures):
        json = backend_fixtures["resources"][
            "get_component_resources_num_instances_requests_and_limits"
        ]["response"]

        # Act
        r = resources.TransformerResources.from_response_json(json)

        # Assert
        assert r.num_instances == json["num_instances"]
        assert r.requests.cores == json["requests"]["cores"]
        assert r.requests.memory == json["requests"]["memory"]
        assert r.requests.gpus == json["requests"]["gpus"]
        assert r.limits.cores == json["limits"]["cores"]
        assert r.limits.memory == json["limits"]["memory"]
        assert r.limits.gpus == json["limits"]["gpus"]

    def test_from_response_json_transformer_resources_specific_keys(
        self, backend_fixtures
    ):
        json = backend_fixtures["resources"][
            "get_component_resources_requested_instances_and_transformer_resources"
        ]["response"]

        # Act
        r = resources.TransformerResources.from_response_json(json)

        # Assert
        assert r.num_instances == json["requested_transformer_instances"]
        assert r.requests.cores == json["transformer_resources"]["requests"]["cores"]
        assert r.requests.memory == json["transformer_resources"]["requests"]["memory"]
        assert r.requests.gpus == json["transformer_resources"]["requests"]["gpus"]
        assert r.limits.cores == json["transformer_resources"]["limits"]["cores"]
        assert r.limits.memory == json["transformer_resources"]["limits"]["memory"]
        assert r.limits.gpus == json["transformer_resources"]["limits"]["gpus"]

    def test_from_response_json_transformer_resources_without_limits(
        self, backend_fixtures
    ):
        json = backend_fixtures["resources"][
            "get_component_resources_num_instances_and_requests"
        ]["response"]

        # Act
        r = resources.TransformerResources.from_response_json(json)

        # Assert
        assert r.num_instances == json["num_instances"]
        assert r.requests.cores == json["requests"]["cores"]
        assert r.requests.memory == json["requests"]["memory"]
        assert r.requests.gpus == json["requests"]["gpus"]
        assert r.limits is None
