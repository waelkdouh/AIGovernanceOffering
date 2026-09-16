import unittest
from unittest.mock import patch

import requests

from shared import apim


class _Response:
    status_code = 200

    def __init__(self, body: str):
        self.content = body.encode()
        self.text = body


class AppInsightsConnectionStringTests(unittest.TestCase):
    def test_get_connection_string(self):
        resource_id = "/subscriptions/s/resourceGroups/g/providers/Microsoft.Insights/components/a"
        with patch.object(
            apim,
            "_request",
            return_value=_Response(
                '{"properties":{"ConnectionString":"InstrumentationKey=example"}}'
            ),
        ) as request:
            result = apim.get_app_insights_connection_string(resource_id)

        self.assertEqual(result, "InstrumentationKey=example")
        request.assert_called_once_with(
            "GET",
            f"{apim.ARM_BASE}{resource_id}",
            params={"api-version": "2020-02-02"},
        )

    def test_falls_back_to_instrumentation_key(self):
        with patch.object(
            apim,
            "_request",
            return_value=_Response('{"properties":{"InstrumentationKey":"example"}}'),
        ):
            result = apim.get_app_insights_connection_string("/resource")

        self.assertEqual(result, "InstrumentationKey=example")

    def test_returns_none_without_credentials(self):
        with patch.object(apim, "_request", return_value=_Response('{"properties":{}}')):
            result = apim.get_app_insights_connection_string("/resource")

        self.assertIsNone(result)

    def test_lookup_error_is_chained_to_configuration_guidance(self):
        lookup_error = requests.RequestException("unavailable")
        with patch.object(
            apim, "get_app_insights_connection_string", side_effect=lookup_error
        ):
            with self.assertRaises(ValueError) as raised:
                apim.ensure_logger(
                    "subscription", "group", "service", "logger",
                    app_insights_resource_id="/resource",
                )

        self.assertIs(raised.exception.__cause__, lookup_error)
        self.assertIn("APP_INSIGHTS_CONNECTION_STRING", str(raised.exception))

    def test_uses_resolved_connection_string_for_logger_credentials(self):
        with patch.object(
            apim,
            "get_app_insights_connection_string",
            return_value="InstrumentationKey=example",
        ), patch.object(apim, "_request", return_value=_Response("{}")) as request:
            apim.ensure_logger(
                "subscription", "group", "service", "logger",
                app_insights_resource_id="/resource",
            )

        self.assertEqual(
            request.call_args.kwargs["json_body"]["properties"]["credentials"],
            {"connectionString": "InstrumentationKey=example"},
        )


if __name__ == "__main__":
    unittest.main()
