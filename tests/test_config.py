import unittest
from dataclasses import replace

from shared import config


def _base_cfg(**overrides) -> config.WorkshopConfig:
    cfg = config.WorkshopConfig(
        resource_group="rg",
        apim_name="apim",
        aoai_endpoint="https://example.openai.azure.com",
        aoai_deployment="gpt-4o-mini",
        content_safety_endpoint="https://example.cognitiveservices.azure.com",
    )
    return replace(cfg, **overrides)


class ValidateContentSafetyConfigTests(unittest.TestCase):
    def test_requires_endpoint(self):
        cfg = _base_cfg(content_safety_endpoint="")
        with self.assertRaisesRegex(ValueError, "content_safety_endpoint"):
            config.validate_content_safety_config(cfg)

    def test_rejects_endpoint_with_path(self):
        cfg = _base_cfg(
            content_safety_endpoint="https://example.cognitiveservices.azure.com/some/path"
        )
        with self.assertRaisesRegex(ValueError, "CONTENT_SAFETY_ENDPOINT"):
            config.validate_content_safety_config(cfg)

    def test_accepts_defaults(self):
        cfg = _base_cfg()
        # Should not raise.
        config.validate_content_safety_config(cfg)

    def test_rejects_non_integer_threshold(self):
        cfg = _base_cfg(content_safety_threshold_hate="high")
        with self.assertRaisesRegex(ValueError, "content_safety_threshold_hate"):
            config.validate_content_safety_config(cfg)

    def test_rejects_out_of_range_threshold(self):
        cfg = _base_cfg(content_safety_threshold_violence="8")
        with self.assertRaisesRegex(ValueError, "content_safety_threshold_violence"):
            config.validate_content_safety_config(cfg)


if __name__ == "__main__":
    unittest.main()
