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


class EnsureApiDiagnosticTests(unittest.TestCase):
    def test_llm_request_has_no_logs_property(self):
        response = SimpleNamespace(status_code=200, content=b"{}", text="{}")
        with patch.object(apim, "_request", return_value=response) as request:
            apim.ensure_api_diagnostic("sub", "rg", "apim", "api", "logger")

        properties = request.call_args.kwargs["json_body"]["properties"]
        self.assertNotIn("logs", properties["largeLanguageModel"])
        self.assertEqual(properties["alwaysLog"], "allErrors")
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

    def test_reraises_unrelated_error_without_retrying(self):
        failed_response = SimpleNamespace(status_code=400, text="Invalid field 'loggerId'")
        error = apim.ApimError("PUT", "https://example.test", failed_response)
        with patch.object(apim, "_request", side_effect=error) as request:
            with self.assertRaisesRegex(apim.ApimError, "loggerId"):
                apim.ensure_api_diagnostic("sub", "rg", "apim", "api", "logger")

        self.assertEqual(request.call_count, 1)


class TokenMetricsTests(unittest.TestCase):
    RESOURCE_ID = (
        "/subscriptions/sub/resourceGroups/rg"
        "/providers/Microsoft.ApiManagement/service/apim"
    )

    def setUp(self):
        apim._METRICS_REGION_CACHE.clear()
        self.addCleanup(apim._METRICS_REGION_CACHE.clear)

    def test_region_is_derived_from_arm_location_and_cached(self):
        response = SimpleNamespace(
            status_code=200, content=b"{}", text='{"location": "East US"}'
        )
        with patch.object(apim, "_request", return_value=response) as request:
            self.assertEqual(apim.resolve_metrics_region(self.RESOURCE_ID), "eastus")
            self.assertEqual(apim.resolve_metrics_region(self.RESOURCE_ID), "eastus")

        self.assertEqual(request.call_count, 1)
        self.assertEqual(
            apim.metrics_endpoint(self.RESOURCE_ID),
            "https://eastus.metrics.monitor.azure.com",
        )

    def test_region_override_argument_and_environment_skip_arm(self):
        with patch.object(apim, "_request", side_effect=AssertionError("no ARM call")):
            self.assertEqual(
                apim.resolve_metrics_region(self.RESOURCE_ID, region="West Europe"),
                "westeurope",
            )
            with patch.dict(apim.os.environ, {"AZURE_METRICS_REGION": "North Europe"}):
                self.assertEqual(
                    apim.resolve_metrics_region(self.RESOURCE_ID), "northeurope"
                )

    def _query_token_metrics(self, metadata_values=None, **kwargs):
        timestamp = object()
        timeseries = SimpleNamespace(
            metadata_values=metadata_values or {"ClientApp": "claims-portal"},
            data=[
                SimpleNamespace(timestamp=timestamp, total=12.0),
                SimpleNamespace(timestamp=timestamp, total=None),
            ],
        )
        metric = SimpleNamespace(name="prompt_tokens", timeseries=[timeseries])
        result = SimpleNamespace(metrics=[metric])
        captured = {}

        def query_resources(**kwargs):
            captured.update(kwargs)
            return [result]

        def fake_client(endpoint, credential):
            captured["endpoint"] = endpoint
            return SimpleNamespace(query_resources=query_resources)

        querymetrics = SimpleNamespace(
            MetricsClient=fake_client,
            MetricAggregationType=SimpleNamespace(TOTAL="Total"),
        )
        with (
            patch.dict(
                "sys.modules", {"azure.monitor.querymetrics": querymetrics}
            ),
            patch.object(apim, "check_metrics_dependencies"),
            patch.object(apim, "metrics_endpoint", return_value="https://eastus.metrics.monitor.azure.com"),
            patch("shared.auth.get_credential", return_value=object()),
        ):
            rows = apim.query_token_metrics(
                resource_id=self.RESOURCE_ID,
                metric_names=["prompt_tokens"],
                **kwargs,
            )

        return timestamp, captured, rows

    def test_query_token_metrics_uses_batch_client_and_keeps_row_shape(self):
        timestamp, captured, rows = self._query_token_metrics(
            subscription_filter="demo-sub"
        )

        self.assertEqual(captured["resource_ids"], [self.RESOURCE_ID])
        self.assertEqual(
            captured["filter"],
            "ClientApp eq '*' and Microsoft.ResourceId eq '*'"
            " and Subscription ID eq 'demo-sub'",
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

    def test_query_token_metrics_filters_resource_id_without_subscription(self):
        _, captured, _ = self._query_token_metrics()

        self.assertEqual(
            captured["filter"], "ClientApp eq '*' and Microsoft.ResourceId eq '*'"
        )

    def test_query_token_metrics_does_not_duplicate_resource_id_clause(self):
        _, captured, _ = self._query_token_metrics(
            dimension_name="Microsoft.ResourceId"
        )

        self.assertEqual(captured["filter"], "Microsoft.ResourceId eq '*'")
        self.assertEqual(captured["filter"].count("Microsoft.ResourceId"), 1)

    def test_query_token_metrics_resolves_client_app_with_resource_id_metadata(self):
        _, _, rows = self._query_token_metrics(
            metadata_values={
                "Microsoft.ResourceId": self.RESOURCE_ID,
                "ClientApp": "claims-portal",
            }
        )

        self.assertEqual(rows[0]["dimension_value"], "claims-portal")

    def _query_app_insights_token_metrics(self, columns):
        timestamp = object()
        table = SimpleNamespace(
            columns=columns,
            rows=[[timestamp, "prompt_tokens", "claims-portal", 12.0]],
        )

        def fake_client(credential):
            return SimpleNamespace(
                query_resource=lambda *args, **kwargs: SimpleNamespace(tables=[table])
            )

        monitor_query = SimpleNamespace(LogsQueryClient=fake_client)
        with (
            patch.dict("sys.modules", {"azure.monitor.query": monitor_query}),
            patch("shared.auth.get_credential", return_value=object()),
        ):
            return timestamp, apim.query_app_insights_token_metrics(
                app_insights_resource_id=(
                    "/subscriptions/sub/resourceGroups/rg"
                    "/providers/Microsoft.Insights/components/appi"
                ),
                metric_names=["prompt_tokens"],
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

    def test_legacy_metadata_items_still_resolve_dimension(self):
        timeseries = SimpleNamespace(
            metadata_values=[
                SimpleNamespace(name=SimpleNamespace(value="ClientApp"), value="analyst-copilot")
            ]
        )
        self.assertEqual(
            apim._metadata_dimension_value(timeseries, "ClientApp"), "analyst-copilot"
        )
        self.assertEqual(
            apim._metadata_dimension_value(SimpleNamespace(metadata_values={}), "ClientApp"),
            "unknown",
        )
