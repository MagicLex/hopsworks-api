"""Tests for the ``hops trino`` / ``hops sql`` connection defaults."""

from __future__ import annotations

from unittest import mock

import click
import pytest
from click.testing import CliRunner
from hopsworks.cli import config, session
from hopsworks.cli.commands import trino
from hopsworks.cli.main import cli
from hopsworks_common.client.exceptions import TrinoAccountPendingError
from trino.exceptions import TrinoUserError


def _connect_kwargs(catalog=None, schema=None):
    """Return the kwargs ``trino._connect`` passes to ``TrinoApi.connect``."""
    ctx = click.Context(cli)
    ctx.obj = {
        "config": config.HopsConfig(host="https://h", api_key="k", project="Sales")
    }
    fake_api = mock.MagicMock(name="TrinoApi")
    fake_project = mock.MagicMock(name="Project")
    fake_project.name = "Sales"
    fake_project.get_trino_api.return_value = fake_api
    fake_fs = mock.MagicMock(name="FeatureStore")
    fake_fs.name = "sales_featurestore"
    with (
        mock.patch.object(session, "get_project", return_value=fake_project),
        mock.patch.object(session, "get_feature_store", return_value=fake_fs),
    ):
        trino._connect(ctx, catalog=catalog, schema=schema)
    return fake_api.connect.call_args.kwargs


def test_defaults_to_the_feature_stores_delta_tables():
    # Feature groups are created as DELTA tables in <backing name>_featurestore, so a
    # bare ``hops sql "SELECT * FROM <fg>_1"`` must land there.
    kwargs = _connect_kwargs()
    assert kwargs["catalog"] == "delta"
    assert kwargs["schema"] == "sales_featurestore"


def test_explicit_catalog_and_schema_win():
    kwargs = _connect_kwargs(catalog="iceberg", schema="other")
    assert kwargs["catalog"] == "iceberg"
    assert kwargs["schema"] == "other"


def test_a_rejected_query_is_one_error_line(mock_project):
    cursor = mock.MagicMock()
    cursor.execute.side_effect = RuntimeError(
        "error 401: b'Access Denied: Invalid credentials'"
    )
    connection = mock.MagicMock()
    connection.cursor.return_value = cursor
    with mock.patch.object(trino, "_connect", return_value=connection):
        result = CliRunner().invoke(cli, ["sql", "SELECT 1"])
    assert result.exit_code == 1
    assert result.output.strip() == (
        "Error: Could not run the query: error 401: "
        "b'Access Denied: Invalid credentials'"
    )
    connection.close.assert_called_once()


def _rejected(exc):
    cursor = mock.MagicMock()
    cursor.execute.side_effect = exc
    connection = mock.MagicMock()
    connection.cursor.return_value = cursor
    with mock.patch.object(trino, "_connect", return_value=connection):
        return CliRunner().invoke(cli, ["sql", "SELECT count(*) FROM orders"])


def _not_found(table):
    return TrinoUserError(
        {
            "errorName": "TABLE_NOT_FOUND",
            "errorType": "USER_ERROR",
            "message": f"line 1:22: Table 'delta.p_featurestore.{table}' does not exist",
        },
        "q1",
    )


def test_a_feature_group_name_without_version_gets_the_versioned_table(mock_project):
    result = _rejected(_not_found("orders"))
    assert result.exit_code == 1
    assert result.output.strip().endswith(
        "A feature group is the table <name>_<version>, such as orders_1."
    )


def test_a_missing_versioned_table_gets_no_hint(mock_project):
    result = _rejected(_not_found("orders_2"))
    assert result.exit_code == 1
    assert "<name>_<version>" not in result.output


def _store(name, group=None):
    fs = mock.MagicMock()
    fs.name = name
    fs.get_feature_group.return_value = group
    return fs


def test_a_table_in_a_shared_store_names_that_store(mock_project):
    stores = [_store("p_featurestore"), _store("9abc_featurestore", mock.MagicMock())]
    with (
        mock.patch.object(
            session, "get_accessible_feature_stores", return_value=stores
        ),
        mock.patch.object(trino, "_featurestore_schema", return_value="p_featurestore"),
    ):
        result = _rejected(_not_found("orders_1"))
    assert result.exit_code == 1
    assert result.output.strip().endswith(
        "orders_1 is in the shared feature store 9abc_featurestore: write "
        '"9abc_featurestore".orders_1, or pass --schema 9abc_featurestore.'
    )
    stores[0].get_feature_group.assert_not_called()
    stores[1].get_feature_group.assert_called_once_with("orders", version=1)


def test_a_store_name_starting_with_a_digit_gets_the_quoting_hint(mock_project):
    result = _rejected(
        TrinoUserError(
            {
                "errorName": "SYNTAX_ERROR",
                "errorType": "USER_ERROR",
                "message": "line 1:22: identifiers must not start with a digit; "
                "surround the identifier with double quotes",
            },
            "q1",
        )
    )
    assert result.exit_code == 1
    assert result.output.strip().endswith('"<store>".<name>_<version>.')


def test_pending_account_is_one_clear_error_line():
    ctx = click.Context(cli)
    ctx.obj = {
        "config": config.HopsConfig(host="https://h", api_key="k", project="Sales")
    }
    fake_project = mock.MagicMock(name="Project")
    fake_project.name = "Sales"
    fake_project.get_trino_api.return_value.connect.side_effect = (
        TrinoAccountPendingError(5)
    )
    fake_fs = mock.MagicMock(name="FeatureStore")
    fake_fs.name = "sales_featurestore"
    with (
        mock.patch.object(session, "get_project", return_value=fake_project),
        mock.patch.object(session, "get_feature_store", return_value=fake_fs),
        pytest.raises(click.ClickException) as exc_info,
    ):
        trino._connect(ctx, catalog=None, schema=None)
    assert exc_info.value.message == (
        "Trino access for this project is still being set up; retry in 5 s"
    )


def _pending_from_server():
    import requests
    from hopsworks_common.client.exceptions import RestAPIError

    response = requests.Response()
    response.status_code = 409
    response._content = b'{"errorCode": 540025, "errorMsg": "x", "usrMsg": "The Trino login is being set up"}'
    try:
        raise RestAPIError("http://x", response)
    except RestAPIError as cause:
        try:
            raise TrinoAccountPendingError(2) from cause
        except TrinoAccountPendingError as pending:
            return pending


def test_trino_info_through_the_cli_prints_the_pending_message(mock_project):
    api = mock.MagicMock()
    api.get_basic_auth.side_effect = _pending_from_server()
    with mock.patch.object(trino, "_api", return_value=api):
        result = CliRunner().invoke(cli, ["trino", "info"])
    assert result.exit_code == 1
    assert result.output.strip() == (
        "Error: Trino access for this project is still being set up; retry in 2 s"
    )


def test_sql_through_the_cli_prints_the_pending_message(mock_project):
    api = mock.MagicMock()
    api.connect.side_effect = _pending_from_server()
    with (
        mock.patch.object(trino, "_api", return_value=api),
        mock.patch.object(trino, "_featurestore_schema", return_value="p_featurestore"),
    ):
        result = CliRunner().invoke(cli, ["sql", "SELECT 1"])
    assert result.exit_code == 1
    assert result.output.strip() == (
        "Error: Trino access for this project is still being set up; retry in 2 s"
    )


@pytest.mark.parametrize(
    ("args", "sql"),
    [
        (["schemas", "2yck__pg"], 'SHOW SCHEMAS FROM "2yck__pg"'),
        (["tables", "2yck__pg.public"], 'SHOW TABLES FROM "2yck__pg"."public"'),
        (["tables", "public"], 'SHOW TABLES FROM "public"'),
        (["columns", "2yck__pg.public.t"], 'DESCRIBE "2yck__pg"."public"."t"'),
        (["schemas", '"2yck__pg"'], 'SHOW SCHEMAS FROM "2yck__pg"'),
    ],
)
def test_catalog_and_schema_names_are_quoted(args, sql):
    with mock.patch.object(trino, "_execute") as execute:
        result = CliRunner().invoke(cli, ["trino", *args])
    assert result.exit_code == 0, result.output
    assert execute.call_args.args[1] == sql
