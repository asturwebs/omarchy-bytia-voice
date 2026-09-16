"""Planner endpoint: base_url validation against the same policy as vision."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.config import Config
from omarchy_voice.planner import PlannerUnavailable, _chat_base


class PlannerEndpointValidationTests(unittest.TestCase):
    def test_https_custom_endpoint_is_allowed(self):
        config = Config(base_url="https://api.z.ai/api/paas/v4")
        self.assertEqual(_chat_base(config), "https://api.z.ai/api/paas/v4")

    def test_trailing_slash_is_stripped(self):
        config = Config(base_url="https://api.z.ai/api/paas/v4/")
        self.assertEqual(_chat_base(config), "https://api.z.ai/api/paas/v4")

    def test_loopback_http_is_allowed(self):
        for host in ("127.0.0.1:8080", "localhost:8892", "[::1]:8892"):
            config = Config(base_url=f"http://{host}")
            self.assertEqual(_chat_base(config), f"http://{host}")

    def test_plain_http_off_loopback_is_refused(self):
        config = Config(base_url="http://api.ejemplo.com/v1")
        with self.assertRaises(PlannerUnavailable):
            _chat_base(config)

    def test_embedded_credentials_are_refused(self):
        config = Config(base_url="https://user:pass@api.z.ai/v1")
        with self.assertRaises(PlannerUnavailable):
            _chat_base(config)

    def test_query_or_fragment_are_refused(self):
        config = Config(base_url="https://api.z.ai/v1?x=1")
        with self.assertRaises(PlannerUnavailable):
            _chat_base(config)
        config = Config(base_url="https://api.z.ai/v1#frag")
        with self.assertRaises(PlannerUnavailable):
            _chat_base(config)


if __name__ == "__main__":
    unittest.main()
