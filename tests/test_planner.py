"""Planner endpoint: base_url validation against the same policy as vision."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.config import Config
from omarchy_voice import planner
from omarchy_voice.planner import PlannerUnavailable, _chat_base, _split_phrases


class PhraseSplitTests(unittest.TestCase):
    """Streaming speech: only whole sentences are emitted, decimals survive."""

    def test_decimal_point_does_not_split(self):
        self.assertEqual(_split_phrases("son 3.5 euros"), ([], "son 3.5 euros"))

    def test_complete_sentence_with_trailing_space(self):
        self.assertEqual(_split_phrases("Hola, Socio. "), (["Hola, Socio."], ""))

    def test_last_sentence_without_space_stays_pending(self):
        self.assertEqual(_split_phrases("Uno. Dos"), (["Uno."], "Dos"))

    def test_multiple_boundaries(self):
        self.assertEqual(_split_phrases("Uno. ¿Dos? ¡Tres! "),
                         (["Uno.", "¿Dos?", "¡Tres!"], ""))

    def test_closing_quote_rides_with_sentence(self):
        phrases, rest = _split_phrases(
            '"El mar no tiene secretos." / "Cada ola llega." / y se va.')
        self.assertEqual(
            phrases, ['"El mar no tiene secretos."', '/ "Cada ola llega."'])
        self.assertEqual(rest, "/ y se va.")

    def test_empty_buffer(self):
        self.assertEqual(_split_phrases(""), ([], ""))


class StreamAssemblyTests(unittest.TestCase):
    """SSE reassembly: content→phrases, tool_call deltas→one message."""

    def _run_stream(self, sse_lines):
        import io
        from unittest import mock
        phrases = []
        config = Config(base_url="https://api.z.ai/api/paas/v4")
        fake = io.BytesIO(b"\n".join(line.encode() for line in sse_lines))
        with mock.patch.object(planner.urllib.request, "urlopen", return_value=fake):
            data = planner._chat_streamed(
                [{"role": "user", "content": "x"}], [], config, "k",
                phrases.append)
        return data, phrases

    def test_content_streams_as_phrases(self):
        data, phrases = self._run_stream([
            'data: {"choices":[{"delta":{"content":"Uno. "}}]}',
            'data: {"choices":[{"delta":{"content":"Dos."}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            'data: {"usage":{"prompt_tokens":5,"completion_tokens":9}}',
            'data: [DONE]',
        ])
        self.assertEqual(phrases, ["Uno.", "Dos."])
        self.assertEqual(data["choices"][0]["message"]["content"], "Uno. Dos.")
        self.assertEqual(data["usage"]["prompt_tokens"], 5)

    def test_tool_call_deltas_are_reassembled(self):
        data, phrases = self._run_stream([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
            '"function":{"name":"omarchy","arguments":""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"{\\"a\\""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":": 1}"}}]}}]}',
            'data: [DONE]',
        ])
        self.assertEqual(phrases, [])
        calls = data["choices"][0]["message"]["tool_calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "c1")
        self.assertEqual(calls[0]["function"]["name"], "omarchy")
        self.assertEqual(calls[0]["function"]["arguments"], '{"a": 1}')

    def test_mid_stream_json_noise_is_tolerated(self):
        data, phrases = self._run_stream([
            ': keep-alive comment',
            'data: not-json',
            'data: {"choices":[{"delta":{"content":"Frase. "}}]}',
            'data: [DONE]',
        ])
        self.assertEqual(phrases, ["Frase."])


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
