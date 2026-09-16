"""Planner endpoint: base_url validation against the same policy as vision."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.config import Config
from omarchy_voice import planner
from omarchy_voice.planner import PlannerUnavailable, _chat_base


class HistoryTests(unittest.TestCase):
    """Conversational memory: load/append/cap sobre STATE_DIR parcheado."""

    def setUp(self):
        import tempfile
        import types
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_config = planner._config
        planner._config = types.SimpleNamespace(STATE_DIR=Path(self.tmp.name))
        self.addCleanup(setattr, planner, "_config", self._orig_config)

    def test_empty_history_when_file_missing(self):
        self.assertEqual(planner._history_load(6), [])

    def test_append_and_load_roundtrip(self):
        planner._history_append("abre el navegador", "Hecho, Socio.")
        turns = planner._history_load(6)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user"], "abre el navegador")
        self.assertEqual(turns[0]["reply"], "Hecho, Socio.")

    def test_load_respects_limit(self):
        for i in range(10):
            planner._history_append(f"frase {i}", f"respuesta {i}")
        self.assertEqual(len(planner._history_load(6)), 6)
        self.assertEqual(planner._history_load(6)[0]["user"], "frase 4")

    def test_load_limit_zero_returns_empty(self):
        planner._history_append("algo", "algo más")
        self.assertEqual(planner._history_load(0), [])

    def test_corrupt_history_file_is_tolerated(self):
        planner._history_path().write_text("{no soy json")
        planner._history_append("a", "b")   # no debe lanzar
        self.assertEqual(len(planner._history_load(6)), 1)


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
