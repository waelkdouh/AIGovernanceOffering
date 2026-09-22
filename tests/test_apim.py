import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shared import apim


class ApimPolicyTests(unittest.TestCase):
    def test_default_request_uses_llm_capable_api_version(self):
        response = SimpleNamespace(status_code=200)
        with (
            patch.object(apim, "_headers", return_value={}),
            patch.object(apim.requests, "request", return_value=response) as request,
        ):
            apim._request("GET", "https://example.test")

        self.assertEqual(
            request.call_args.kwargs["params"]["api-version"],
            "2024-06-01-preview",
        )

    def test_demo2_token_metric_is_inbound(self):
        policy_path = (
            Path(__file__).resolve().parents[1]
            / "policies"
            / "demo2-emit-token-metric.xml"
        )
        policy = ET.parse(policy_path).getroot()

        metric = policy.find("./inbound/llm-emit-token-metric")
        self.assertIsNotNone(metric)
        self.assertEqual(metric.attrib["namespace"], "module8")
        self.assertEqual(
            [dimension.attrib["name"] for dimension in metric.findall("dimension")],
            ["API ID", "Subscription ID", "ClientApp"],
        )
        self.assertIsNone(policy.find("./outbound/llm-emit-token-metric"))
        self.assertEqual(
            [child.tag for child in policy.find("outbound")],
            ["base"],
        )


class EnsureLoggerTests(unittest.TestCase):
    def test_requires_connection_string_even_with_resource_id(self):
        for connection_string in (None, "   "):
            with self.subTest(connection_string=connection_string):
                with patch.object(apim, "_request") as request:
                    with self.assertRaisesRegex(
                        ValueError,
                        "APIM requires credentials.*APP_INSIGHTS_CONNECTION_STRING",
                    ):
                        apim.ensure_logger(
                            "sub",
                            "rg",
                            "apim",
                            "logger",
                            app_insights_resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Insights/components/appi",
                            app_insights_connection_string=connection_string,
                        )

                request.assert_not_called()

    def test_put_body_includes_resource_id_and_connection_string(self):
        response = SimpleNamespace(status_code=200, content=b"{}", text="{}")
        resource_id = (
            "/subscriptions/sub/resourceGroups/rg/"
            "providers/Microsoft.Insights/components/appi"
        )

        with patch.object(apim, "_request", return_value=response) as request:
            apim.ensure_logger(
                "sub",
                "rg",
                "apim",
                "logger",
                app_insights_resource_id=resource_id,
                app_insights_connection_string="InstrumentationKey=key",
                description="Demo logger",
            )

        self.assertEqual(request.call_args.args[0], "PUT")
        self.assertTrue(request.call_args.args[1].endswith("/loggers/logger"))
        properties = request.call_args.kwargs["json_body"]["properties"]
        self.assertEqual(properties["resourceId"], resource_id)
        self.assertEqual(
            properties["credentials"]["connectionString"],
            "InstrumentationKey=key",
        )

    def test_put_body_omits_blank_resource_id(self):
        response = SimpleNamespace(status_code=200, content=b"{}", text="{}")

        with patch.object(apim, "_request", return_value=response) as request:
            apim.ensure_logger(
                "sub",
                "rg",
                "apim",
                "logger",
                app_insights_resource_id="   ",
                app_insights_connection_string="InstrumentationKey=key",
            )

        properties = request.call_args.kwargs["json_body"]["properties"]
        self.assertNotIn("resourceId", properties)
        self.assertEqual(
            properties["credentials"]["connectionString"],
            "InstrumentationKey=key",
        )


class EnsureApiDiagnosticTests(unittest.TestCase):
    def test_llm_request_has_no_logs_property(self):
        response = SimpleNamespace(status_code=200, content=b"{}", text="{}")
        with patch.object(apim, "_request", return_value=response) as request:
            apim.ensure_api_diagnostic("sub", "rg", "apim", "api", "logger")

        properties = request.call_args.kwargs["json_body"]["properties"]
        self.assertNotIn("logs", properties["largeLanguageModel"])
        self.assertEqual(properties["alwaysLog"], "allErrors")
        self.assertIs(properties["metrics"], True)
        self.assertIn("loggerId", properties)
        self.assertIn("sampling", properties)
        self.assertIn("frontend", properties)
        self.assertIn("backend", properties)

    def test_retries_without_llm_block_after_matching_validation_error(self):
        failed_response = SimpleNamespace(
            status_code=400, text="Invalid field 'LARGELANGUAGEMODEL' specified"
        )
        error = apim.ApimError("PUT", "https://example.test", failed_response)
        response = SimpleNamespace(status_code=200, content=b"{}", text="{}")
        with patch.object(apim, "_request", side_effect=[error, response]) as request:
            apim.ensure_api_diagnostic("sub", "rg", "apim", "api", "logger")

        self.assertEqual(request.call_count, 2)
        fallback_properties = request.call_args.kwargs["json_body"]["properties"]
        self.assertNotIn("largeLanguageModel", fallback_properties)
        self.assertIs(fallback_properties["metrics"], True)

    def test_reraises_unrelated_error_without_retrying(self):
        failed_response = SimpleNamespace(status_code=400, text="Invalid field 'loggerId'")
        error = apim.ApimError("PUT", "https://example.test", failed_response)
        with patch.object(apim, "_request", side_effect=error) as request:
            with self.assertRaisesRegex(apim.ApimError, "loggerId"):
                apim.ensure_api_diagnostic("sub", "rg", "apim", "api", "logger")

        self.assertEqual(request.call_count, 1)


class GetApiPolicyTests(unittest.TestCase):
    POLICY_XML = '<policies><inbound><llm-emit-token-metric namespace="module8" /></inbound></policies>'

    def test_raw_xml_response_returns_policy_document(self):
        response = SimpleNamespace(
            status_code=200,
            content=self.POLICY_XML.encode("utf-8"),
            text=self.POLICY_XML,
        )
        with patch.object(apim, "_request", return_value=response) as request:
            policy = apim.get_api_policy("sub", "rg", "apim", "api")

        self.assertEqual(request.call_args.kwargs["params"]["format"], "rawxml")
        self.assertEqual(policy["properties"]["value"], self.POLICY_XML)
        self.assertEqual(policy["properties"]["format"], "rawxml")

    def test_json_response_is_parsed(self):
        body = '{"properties": {"format": "xaml", "value": "<policies />"}}'
        response = SimpleNamespace(status_code=200, content=body.encode("utf-8"), text=body)
        with patch.object(apim, "_request", return_value=response):
            policy = apim.get_api_policy(
                "sub", "rg", "apim", "api", policy_format="xaml"
            )

        self.assertEqual(policy["properties"]["value"], "<policies />")


class TokenMetricsTests(unittest.TestCase):
    RESOURCE_ID = (
        "/subscriptions/sub/resourceGroups/rg"
        "/providers/Microsoft.ApiManagement/service/apim"
    )

    APP_INSIGHTS_ID = (
        "/subscriptions/sub/resourceGroups/rg"
        "/providers/Microsoft.Insights/components/appi"
    )

    def _fake_logs_client(self, tables, calls):
        def fake_client(credential):
            def query_resource(resource_id, query, timespan=None, **kwargs):
                calls.append({"resource_id": resource_id, "query": query})
                return SimpleNamespace(tables=tables)

            return SimpleNamespace(query_resource=query_resource)

        return SimpleNamespace(LogsQueryClient=fake_client)

    def _query_app_insights_token_metrics(self, columns, calls=None, **kwargs):
        timestamp = object()
        table = SimpleNamespace(
            columns=columns,
            rows=[[timestamp, "prompt_tokens", "claims-portal", 12.0]],
        )
        monitor_query = self._fake_logs_client([table], calls if calls is not None else [])
        with (
            patch.dict("sys.modules", {"azure.monitor.query": monitor_query}),
            patch("shared.auth.get_credential", return_value=object()),
        ):
            return timestamp, apim.query_app_insights_token_metrics(
                app_insights_resource_id=self.APP_INSIGHTS_ID,
                metric_names=["prompt_tokens"],
                **kwargs,
            )

    def test_query_token_metrics_reads_application_insights(self):
        timestamp = object()
        table = SimpleNamespace(
            columns=["timestamp", "metric_name", "dimension_value", "total"],
            rows=[[timestamp, "prompt_tokens", "claims-portal", 12.0]],
        )
        calls = []
        monitor_query = self._fake_logs_client([table], calls)
        with (
            patch.dict("sys.modules", {"azure.monitor.query": monitor_query}),
            patch("shared.auth.get_credential", return_value=object()),
            patch.object(
                apim,
                "get_app_insights_for_apim",
                return_value={"app_insights_resource_id": self.APP_INSIGHTS_ID},
            ) as resolve,
        ):
            rows = apim.query_token_metrics(
                resource_id=self.RESOURCE_ID,
                metric_names=["prompt_tokens"],
                subscription_filter="demo-sub",
            )

        resolve.assert_called_once_with("sub", "rg", "apim")
        self.assertEqual(calls[0]["resource_id"], self.APP_INSIGHTS_ID)
        self.assertIn("customMetrics", calls[0]["query"])
        self.assertIn("'demo-sub'", calls[0]["query"])
        self.assertNotIn("Microsoft.ResourceId", calls[0]["query"])
        self.assertEqual(
            rows,
            [
                {
                    "timestamp": timestamp,
                    "metric_name": "prompt_tokens",
                    "dimension_name": "ClientApp",
                    "dimension_value": "claims-portal",
                    "total": 12.0,
                }
            ],
        )

    def test_query_token_metrics_uses_explicit_app_insights_resource_id(self):
        calls = []
        monitor_query = self._fake_logs_client([], calls)
        with (
            patch.dict("sys.modules", {"azure.monitor.query": monitor_query}),
            patch("shared.auth.get_credential", return_value=object()),
            patch.object(
                apim, "get_app_insights_for_apim", side_effect=AssertionError("no ARM call")
            ),
        ):
            rows = apim.query_token_metrics(
                resource_id=self.RESOURCE_ID,
                metric_names=["prompt_tokens"],
                app_insights_resource_id=self.APP_INSIGHTS_ID,
            )

        self.assertEqual(rows, [])
        self.assertEqual(calls[0]["resource_id"], self.APP_INSIGHTS_ID)

    def test_query_token_metrics_raises_when_app_insights_cannot_be_resolved(self):
        with patch.object(apim, "get_app_insights_for_apim", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Application Insights"):
                apim.query_token_metrics(
                    resource_id=self.RESOURCE_ID, metric_names=["prompt_tokens"]
                )

    def test_app_insights_resolution_ignores_malformed_resource_ids(self):
        with patch.object(
            apim, "get_app_insights_for_apim", side_effect=AssertionError("no ARM call")
        ):
            for malformed in (
                "",
                "/subscriptions/sub/resourceGroups/service",
                "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Insights/components/appi",
            ):
                self.assertIsNone(
                    apim._app_insights_for_apim_resource_id(malformed), malformed
                )

    def test_query_app_insights_token_metrics_returns_empty_list_without_rows(self):
        empty_table = SimpleNamespace(
            columns=["timestamp", "metric_name", "dimension_value", "total"], rows=[]
        )
        monitor_query = self._fake_logs_client([empty_table], [])
        with (
            patch.dict("sys.modules", {"azure.monitor.query": monitor_query}),
            patch("shared.auth.get_credential", return_value=object()),
        ):
            self.assertEqual(
                apim.query_app_insights_token_metrics(
                    app_insights_resource_id=self.APP_INSIGHTS_ID,
                    metric_names=["prompt_tokens"],
                ),
                [],
            )

    def test_query_app_insights_token_metrics_accepts_string_columns(self):
        timestamp, rows = self._query_app_insights_token_metrics(
            ["timestamp", "metric_name", "dimension_value", "total"]
        )

        self.assertEqual(
            rows,
            [
                {
                    "timestamp": timestamp,
                    "metric_name": "prompt_tokens",
                    "dimension_name": "ClientApp",
                    "dimension_value": "claims-portal",
                    "total": 12.0,
                }
            ],
        )

    def test_query_app_insights_token_metrics_accepts_named_columns(self):
        timestamp, rows = self._query_app_insights_token_metrics(
            [
                SimpleNamespace(name="timestamp"),
                SimpleNamespace(name="metric_name"),
                SimpleNamespace(name="dimension_value"),
                SimpleNamespace(name="total"),
            ]
        )

        self.assertEqual(
            rows,
            [
                {
                    "timestamp": timestamp,
                    "metric_name": "prompt_tokens",
                    "dimension_name": "ClientApp",
                    "dimension_value": "claims-portal",
                    "total": 12.0,
                }
            ],
        )
