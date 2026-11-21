#!/usr/bin/env python3
"""bytia-ttsd — daemon de voz residente de BytIA Voice.

Carga el modelo piper UNA vez en RAM y atiende peticiones de síntesis por un
socket Unix. Así todas las superficies (BytIA Voice streaming por frases,
confirmaciones de Omarchy, lector de avisos) comparten el modelo caliente
en vez de pagar el arranque en frío (~1s) por cada frase.

Protocolo (una petición por conexión):
  cliente → una línea JSON: {"text": "...", "length_scale": 1.08}
  daemon  → bytes PCM crudos S16_LE mono (sample rate del modelo) y cierra

Uso directo:  bytia-ttsd.py            (daemon; lo arranca systemd)
              bytia-ttsd.py --client '{"text": "hola"}'   (cliente de prueba)
"""
import json
import os
import socket
import sys

MODEL = os.path.expanduser("~/.local/share/piper-voices/es_AR-daniela-high.onnx")
CONFIG = MODEL + ".json"
SOCK = os.path.expanduser("~/.local/state/bytia-ttsd.sock")
DEFAULT_LENGTH_SCALE = 1.08  # mismo valor que usaba el wrapper por binario


def synth_stream(voice, text, length_scale):
    """Genera PCM crudo (bytes) como lo hacía `piper --output-raw`."""
    from piper.config import SynthesisConfig
    syn_config = SynthesisConfig(length_scale=length_scale)
    for chunk in voice.synthesize(text, syn_config=syn_config):
        yield chunk.audio_int16_bytes


def serve():
    from piper import PiperVoice
    from piper.config import SynthesisConfig
    print(f"[bytia-ttsd] cargando modelo {MODEL} ...", flush=True)
    voice = PiperVoice.load(MODEL, config_path=CONFIG if os.path.exists(CONFIG) else None)
    sr = None
    for chunk in voice.synthesize("x", SynthesisConfig(length_scale=DEFAULT_LENGTH_SCALE)):
        sr = chunk.sample_rate
        break
    print(f"[bytia-ttsd] modelo listo (sr={sr}) — escuchando en {SOCK}", flush=True)

    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o600)
    srv.listen(4)

    while True:
        conn, _ = srv.accept()
        try:
            with conn:
                data = b""
                while b"\n" not in data:
                    part = conn.recv(65536)
                    if not part:
                        break
                    data += part
                req = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
                text = req.get("text", "")
                length_scale = float(req.get("length_scale", DEFAULT_LENGTH_SCALE))
                if not text:
                    continue
                for pcm in synth_stream(voice, text, length_scale):
                    conn.sendall(pcm)
        except Exception as e:  # el daemon no muere por una petición mala
            print(f"[bytia-ttsd] error en petición: {e}", file=sys.stderr, flush=True)


def client(payload_json):
    """Cliente de prueba: imprime el PCM a stdout."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(payload_json.encode() + b"\n")
    while True:
        part = s.recv(65536)
        if not part:
            break
        sys.stdout.buffer.write(part)
    s.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--client":
        client(sys.argv[2])
    else:
        serve()
