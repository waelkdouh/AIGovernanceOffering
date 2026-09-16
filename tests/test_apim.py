import unittest
from types import SimpleNamespace
from unittest.mock import patch

from shared import apim


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
