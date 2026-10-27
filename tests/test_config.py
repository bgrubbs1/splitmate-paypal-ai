"""Unit tests for configuration handling (modes, .env parsing, warnings)."""

import os
import tempfile
import unittest

from splitmate.config import Config, ConfigError, load_env_file


class EnvFileTests(unittest.TestCase):
    def test_parses_simple_pairs(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write(
                "# comment\n"
                "PAYPAL_CLIENT_ID=abc123\n"
                "export PAYPAL_CLIENT_SECRET='shh'\n"
                "LLM_MODEL=\"gpt-test\"\n"
                "broken line without equals\n"
            )
            path = handle.name
        try:
            values = load_env_file(path)
        finally:
            os.unlink(path)
        self.assertEqual(values["PAYPAL_CLIENT_ID"], "abc123")
        self.assertEqual(values["PAYPAL_CLIENT_SECRET"], "shh")
        self.assertEqual(values["LLM_MODEL"], "gpt-test")
        self.assertNotIn("broken line without equals", values)

    def test_missing_file_is_empty(self):
        self.assertEqual(load_env_file("/nonexistent/splitmate.env"), {})


class ModeTests(unittest.TestCase):
    def test_defaults_to_mock_without_credentials(self):
        config = Config.from_env(env={})
        self.assertEqual(config.mode, "auto")
        self.assertEqual(config.paypal_mode, "mock")
        self.assertEqual(config.ai_mode, "mock")

    def test_auto_uses_sandbox_when_credentials_present(self):
        config = Config.from_env(
            env={"PAYPAL_CLIENT_ID": "id", "PAYPAL_CLIENT_SECRET": "secret"}
        )
        self.assertEqual(config.paypal_mode, "sandbox")
        self.assertEqual(config.base_url, "https://api-m.sandbox.paypal.com")

    def test_mock_mode_overrides_credentials(self):
        config = Config.from_env(
            env={
                "SPLITMATE_MODE": "mock",
                "PAYPAL_CLIENT_ID": "id",
                "PAYPAL_CLIENT_SECRET": "secret",
                "LLM_API_KEY": "key",
            }
        )
        self.assertEqual(config.paypal_mode, "mock")
        self.assertEqual(config.ai_mode, "mock")

    def test_real_mode_requires_credentials(self):
        with self.assertRaises(ConfigError) as context:
            Config.from_env(env={"SPLITMATE_MODE": "real"})
        self.assertIn("PAYPAL_CLIENT_ID", str(context.exception))

    def test_real_mode_accepts_credentials(self):
        config = Config.from_env(
            env={
                "SPLITMATE_MODE": "real",
                "PAYPAL_CLIENT_ID": "id",
                "PAYPAL_CLIENT_SECRET": "secret",
            }
        )
        self.assertEqual(config.paypal_mode, "sandbox")
        self.assertEqual(config.ai_mode, "mock")

    def test_partial_credentials_warns_and_falls_back(self):
        config = Config.from_env(env={"PAYPAL_CLIENT_ID": "id"})
        self.assertEqual(config.paypal_mode, "mock")
        self.assertTrue(any("Only one PayPal credential" in warning for warning in config.warnings))

    def test_placeholder_credentials_ignored(self):
        config = Config.from_env(
            env={
                "PAYPAL_CLIENT_ID": "your-paypal-client-id",
                "PAYPAL_CLIENT_SECRET": "your-paypal-client-secret",
            }
        )
        self.assertEqual(config.paypal_mode, "mock")

    def test_invalid_mode_raises(self):
        with self.assertRaises(ConfigError):
            Config.from_env(env={"SPLITMATE_MODE": "turbo"})

    def test_invalid_port_raises(self):
        with self.assertRaises(ConfigError):
            Config.from_env(env={"SPLITMATE_PORT": "not-a-port"})
        with self.assertRaises(ConfigError):
            Config.from_env(env={"SPLITMATE_PORT": "70000"})

    def test_public_base_url_tracks_port(self):
        config = Config.from_env(env={"SPLITMATE_PORT": "9123"})
        self.assertEqual(config.public_base, "http://127.0.0.1:9123")
        self.assertEqual(config.return_url, "http://127.0.0.1:9123/return")
        self.assertEqual(config.cancel_url, "http://127.0.0.1:9123/cancel")

    def test_unknown_currency_falls_back_with_warning(self):
        config = Config.from_env(env={"PAYPAL_CURRENCY": "XYZ"})
        self.assertEqual(config.currency, "USD")
        self.assertTrue(any("PAYPAL_CURRENCY" in warning for warning in config.warnings))

    def test_public_description_never_contains_secrets(self):
        config = Config.from_env(
            env={
                "PAYPAL_CLIENT_ID": "super-secret-id",
                "PAYPAL_CLIENT_SECRET": "super-secret-secret",
                "LLM_API_KEY": "super-secret-key",
            }
        )
        description = str(config.public_description())
        self.assertNotIn("super-secret", description)
        self.assertEqual(description.count("secret"), 0)

    def test_experience_context_flag(self):
        config = Config.from_env(env={"PAYPAL_EXPERIENCE_CONTEXT": "1"})
        self.assertTrue(config.paypal_experience_context)
        self.assertFalse(Config.from_env(env={}).paypal_experience_context)


if __name__ == "__main__":
    unittest.main()
