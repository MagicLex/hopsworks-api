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

import os
import shutil
import tempfile
from typing import Any

from hsml.constants import MODEL
from hsml.model import Model


# Triton loads models from a repository laid out as <model_name>/<version>/model.onnx.
# Each Hopsworks model version is its own artifact, so a single numeric version dir per
# repository is enough.
_TRITON_MODEL_VERSION = "1"
_TRITON_MODEL_FILE = "model.onnx"


class Model(Model):
    """Metadata object representing an ONNX model in the Model Registry."""

    def __init__(
        self,
        id,
        name,
        version=None,
        created=None,
        creator=None,
        environment=None,
        description=None,
        project_name=None,
        metrics=None,
        program=None,
        user_full_name=None,
        model_schema=None,
        training_dataset=None,
        input_example=None,
        model_registry_id=None,
        tags=None,
        href=None,
        feature_view=None,
        training_dataset_version=None,
        missing_mandatory_tags=None,
        **kwargs,
    ):
        super().__init__(
            id,
            name,
            version=version,
            created=created,
            creator=creator,
            environment=environment,
            description=description,
            project_name=project_name,
            metrics=metrics,
            program=program,
            user_full_name=user_full_name,
            model_schema=model_schema,
            training_dataset=training_dataset,
            input_example=input_example,
            framework=MODEL.FRAMEWORK_ONNX,
            model_registry_id=model_registry_id,
            feature_view=feature_view,
            training_dataset_version=training_dataset_version,
            missing_mandatory_tags=missing_mandatory_tags,
            tags=tags,
        )

    def save(
        self,
        model_path: str,
        await_registration: int = 480,
        keep_original_files: bool = False,
        upload_configuration: dict[str, Any] | None = None,
    ) -> Model:
        """Persist the ONNX model and its metadata to the model registry.

        Triton serves from a model repository laid out as `<model_name>/<version>/model.onnx`.
        When `model_path` is a single `.onnx` file, it is wrapped into that layout before upload.
        When `model_path` is a directory, it is assumed to already be a valid Triton model
        repository and uploaded as-is. No `config.pbtxt` is generated: the `kserve-tritonserver`
        runtime runs with auto-complete-config enabled, so the onnxruntime backend derives the
        model configuration (inputs, outputs, dims) from the ONNX graph.

        Parameters:
            model_path: Local or remote (Hopsworks file system) path to the `.onnx` file, or to a
                directory that is already a Triton model repository.
            await_registration: Awaiting time for the model to be registered in Hopsworks.
            keep_original_files: If the model files are in hopsfs, whether to copy rather than move them.
            upload_configuration: Upload chunking/parallelism/retry options, see `Model.save`.

        Returns:
            The model metadata object.
        """
        if os.path.isfile(model_path) and model_path.endswith(".onnx"):
            with tempfile.TemporaryDirectory() as repo_dir:
                version_dir = os.path.join(repo_dir, self._name, _TRITON_MODEL_VERSION)
                os.makedirs(version_dir)
                shutil.copy2(model_path, os.path.join(version_dir, _TRITON_MODEL_FILE))
                return super().save(
                    repo_dir,
                    await_registration=await_registration,
                    keep_original_files=keep_original_files,
                    upload_configuration=upload_configuration,
                )
        return super().save(
            model_path,
            await_registration=await_registration,
            keep_original_files=keep_original_files,
            upload_configuration=upload_configuration,
        )

    def update_from_response_json(self, json_dict):
        self.__init__(**self._response_kwargs(json_dict, "framework"))
        return self
