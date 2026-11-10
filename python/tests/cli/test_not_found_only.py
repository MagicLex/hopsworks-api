"""Reads that may find nothing: only a 404 means "none"; any other failure is said or raised."""

from unittest import mock

import click
import pytest
from hopsworks.cli.commands import deployment, fg, model
from hopsworks_common.client.exceptions import RestAPIError


def _rest_error(status, body=None):
    response = mock.MagicMock(status_code=status, reason="x", content=b"{}")
    response.json.return_value = body or {}
    return RestAPIError("https://host/x", response)


NOT_FOUND = _rest_error(404)
# What the dev cluster answers for a missing HopsFS path (checked live on hopsworks 5.3).
PATH_NOT_FOUND = _rest_error(400, {"errorCode": 110018, "errorMsg": "Path not found"})
BROKEN = _rest_error(503)


def _model(fv=None, fv_error=None):
    m = mock.MagicMock(version=2, version_path="/Projects/p/Models/m/2", framework="SKLEARN")
    m.name = "m"
    m.get_feature_view.side_effect = fv_error
    m.get_feature_view.return_value = fv
    return m


def _project(list_error=None, read_error=None):
    project = mock.MagicMock()
    api = project.get_dataset_api.return_value
    api.list.side_effect = list_error
    api.list.return_value = ["/x/Files/images/roc.png"]
    api.read_content.side_effect = read_error
    return project


def test_images_none_only_on_404():
    assert model._images(_project(list_error=NOT_FOUND), _model()) == []
    assert model._images(_project(list_error=PATH_NOT_FOUND), _model()) == []
    with pytest.raises(click.ClickException, match="could not be read"):
        model._images(_project(list_error=_rest_error(400, {"errorCode": 110001})), _model())
    with pytest.raises(click.ClickException, match="could not be read"):
        model._images(_project(list_error=BROKEN), _model())


def test_feature_view_label_none_only_on_404():
    assert model._feature_view_label(_model(fv_error=NOT_FOUND)) == "-"
    assert model._feature_view_label(_model(fv=None)) == "-"
    assert model._feature_view_label(_model(fv_error=BROKEN)).startswith("could not be read")


def test_run_record_empty_only_on_404():
    runs = model._runs(_project(read_error=PATH_NOT_FOUND), [_model()])
    assert runs[0]["version"] == 2
    with pytest.raises(click.ClickException, match="could not be read"):
        model._runs(_project(read_error=BROKEN), [_model()])


def test_serves_by_default_never_picks_on_a_failed_read():
    assert deployment._serves_by_default(_model(fv=mock.MagicMock()), None) is True
    assert deployment._serves_by_default(_model(fv_error=NOT_FOUND), None) is False
    with pytest.raises(click.ClickException, match="--default-predictor"):
        deployment._serves_by_default(_model(fv_error=BROKEN), None)


def test_offline_size_none_only_on_404():
    group = mock.MagicMock(location="hopsfs://nn:8020/apps/hive/warehouse/p.db/orders_1")
    with mock.patch.object(fg, "_fg_type_label", return_value="managed"):
        api = mock.MagicMock()
        api._list_dataset_path.side_effect = PATH_NOT_FOUND
        assert fg._offline_size(api, group) is None
        api._list_dataset_path.side_effect = BROKEN
        with pytest.raises(click.ClickException, match="could not be read"):
            fg._offline_size(api, group)


def _fg_info(mock_project, error, *flags):
    from click.testing import CliRunner
    from hopsworks.cli.main import cli

    group = mock.MagicMock(location="hopsfs://nn:8020/apps/hive/warehouse/p.db/orders_1", id=7, version=1, features=[], primary_key=["id"], event_time=None, description="", stream=False, online_enabled=False)
    group.name = "orders"
    mock_project.get_feature_store.return_value.get_feature_group.return_value = group
    mock_project.get_dataset_api.return_value._list_dataset_path.side_effect = error
    with mock.patch.object(fg, "_fg_type_label", return_value="managed"):
        return CliRunner().invoke(cli, [*flags, "fg", "info", "orders"])


def test_fg_info_fails_when_the_offline_size_could_not_be_read(mock_project):
    result = _fg_info(mock_project, BROKEN)
    assert result.exit_code == 1, result.output
    assert "Offline size could not be read" in result.output
    as_json = _fg_info(mock_project, BROKEN, "--json")
    assert as_json.exit_code == 1, as_json.output
    assert '"offline_size_error": "Offline size could not be read' in as_json.output


def test_fg_info_without_an_offline_table_passes(mock_project):
    result = _fg_info(mock_project, PATH_NOT_FOUND, "--json")
    assert result.exit_code == 0, result.output
    assert '"offline_size_bytes": null' in result.output
    assert "offline_size_error" not in result.output


def test_schema_label_none_only_when_missing():
    d = mock.MagicMock(schema_id="abc")
    type(d).schema = mock.PropertyMock(side_effect=PATH_NOT_FOUND)
    assert deployment._schema_label(d) == "-"
    d = mock.MagicMock(schema_id="abc")
    type(d).schema = mock.PropertyMock(side_effect=BROKEN)
    assert deployment._schema_label(d).startswith("could not be read")
