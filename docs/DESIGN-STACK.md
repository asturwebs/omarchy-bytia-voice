# Diseño — Stack de voz BytIA (F9 / F10 / daemon / OSD)

> Estado: **propuesta de consolidación v2** (21-nov-2026). Sustituye al mosaico actual
> de scripts sueltos en `~/.local/bin` por este repo como fuente única.
> Autor: BytIA + Socio.
>
> **Revisión adversarial ejecutada** (21-nov, 2 motores independientes: MiMo-V2.6-Pro
> y GLM-5.3-flash, panes herdr — ambos NO-GO v1). Esta v2 incorpora la reparación:
> hallazgos re-verificados en disco por el autor. Las decisiones del Socio que aún no
> tienen respuesta van marcadas **PROPUESTO** (no se implementan sin ratificación).
>
> **Fase 0 ejecutada** (21-nov): `main` = estado vivo del daemon (commits `37b956f`
> trabajo vivo + `4aa2e6e` reconciliación; gate `diff -rq` repo↔instalado vacío;
> 20/20 tests del planner). **Push pendiente**: el hook de publicación del repo
> público bloquea los 21 commits nunca publicados por email no privado en la
> historia antigua (decisión del Socio: sanear historia o dejarlo local).

## 1. Alcance

Este repo (`asturwebs/omarchy-bytia-voice`, fork de `wombatoperator/omarchy-voice`)
pasa a ser la **fuente de verdad** de todo el stack de voz de la máquina:

- `bin/bytia-dict` — pre-flight de micrófono (antes suelto).
- `bin/bytia-voice` — F10: grabación, transcripción, planner, TTS (antes suelto).
- `bin/bytia-tts`, `bin/bytia-ttsd.py` — boca TTS piper con daemon (antes sueltos).
- `bin/bytia-mic-reset`, `bin/bytia-speak-notifications`, `bin/bytia-audio-reaper` — utilidades (antes sueltas).
- `bin/bytia-voice-orb-level` — publicador de nivel para el orbe (nuevo, 21-nov).
- `bin/bytia-llm` — conmutador de cerebro (ya versionado).
- `plugin/bytia.voice` — widget de barra propio (estado + menú).
- `plugin/voice.orb`, `plugin/voice.indicator` — OSD e indicador (del upstream).
- `src/`, `bin/omarchy-voice`, `bin/omarchy-vision` — daemon/planner (upstream + parches).

`install.sh` distribuye: binarios a `~/.local/bin` (**symlink al repo** — es el
patrón ya vivo de `bytia-llm`, verificado con `readlink`; `install.sh:60-61` aún
enlaza a la copia y se corrige en Fase 2), plugins a `~/.config/omarchy/plugins/`,
y **los servicios systemd user** (hoy 8 units propios viven solo en
`~/.config/systemd/user/`, fuera del repo — se versionan en `share/units/` en
Fase 2). Las configs viven en `~/.config/{omarchy-voice,voxtype}/`; plantilla
real: `share/config.example.toml` (aún perfil upstream — se actualiza al perfil
del fork en Fase 2) y `share/env.example` **por crear** (solo nombres:
`UNSLOTH_LOCAL_API_KEY`, `ZAI_API_KEY`, `OLLAMA_LOCAL_API_KEY`, `OPENAI_API_KEY`
opcional). Nunca secretos.

## 2. Topología (quién manda en qué)

```
F9  ──▶ bytia-dict start ──▶ voxtype record start/stop (parakeet, OSD propio)
F10 ──▶ bytia-dict mic ──▶ bytia-voice start ── pw-record --target <elegido> ──▶ /tmp/bytia-voice-rec.wav
        (suelta F10)      bytia-voice stop ── clean_rec ── voxtype-onnx-avx512 (CPU) ──▶ texto
                                                                      ├──▶ omarchy-voice say (planner local) ──▶ tool_calls
                                                                      └──▶ reply-chunk ──▶ bytia-tts (piper) ──▶ respuesta hablada
Super+Shift+V ──▶ walkie LOCAL: bytia-dict mic && bytia-voice toggle (pulsa para empezar, otra para procesar)
Modo Live/OpenAI ──▶ SIN TECLA: se lanza desde el widget voice.indicator (omarchy-voice listen
                     toggle/confirm) o por CLI `omarchy-voice listen toggle`; requiere OPENAI_API_KEY
                     (sin key → 401, no afecta a F9/F10). PROPUESTO: mantenerlo así (modo secundario,
                     sin tecla propia) — decisión §6(c).

Estado:        bytia-voice → ~/.cache/bytia-voice/state.json (plugin bytia.voice)
OSD:           ventana: $XDG_RUNTIME_DIR/voxtype/state (escrito por bytia-voice: recording/idle)
               curvas:  daemon voxtype → audio.sock (solo cuando ÉL graba)
               ORBE PROPIO (PENDIENTE, post-GO): $XDG_RUNTIME_DIR/omarchy-voice/state.json YA
               TIENE DUEÑO (el daemon: {status,icon,class,updated}; el orb exige status
               listening/thinking y fresh = updated < 900 s). PROPUESTO: fichero propio
               (bytia-state.json con campo updated) + productor único (bytia-voice), y el nivel
               por bytia-voice-orb-level — que hoy lee PCM por stdin, no «el WAV». Decisión §6(d).
```

## 3. Invariantes (lecciones medidas el 21-nov-2026 — no re-litigar)

| # | Invariante | Porqué (evidencia) |
|---|---|---|
| I1 | **El micro lo decide el Socio**: `bytia-dict` NO escribe el default ni `pw-metadata default.audio.source`, NO conmuta por señal, NO restaura el source. Solo: si el default es BT → HFP (obligatorio) y desmuteo. | El metadata de PipeWire gana sobre pactl/WirePlumber; bytia-dict lo pisaba en cada dictado (soundcore→webcam). El restore devolvía un PREV_SRC obsoleto. |
| I2 | **Planner: `reasoning_effort` es una matriz por destino, no un valor universal**: `local`/Unsloth → **`low`**; `ollama`/mimo → `none` (su docstring lo mide así desde 6-nov); `cloud` → según medición. ⚠️ `bin/bytia-llm` **aún escribe `none` en el preset `local`** (líneas 43/53) — fix obligatorio en Fase 2. | Con `none` en Unsloth el modelo responde «Hecho» **sin tool_calls** (medido: 4–8 tok out, sin `action`). Con `low`: `action` presente. El conmutador versionado reintroduciría el bug al primer `bytia-llm local`. |
| I3 | **`bytia-voice forget` ante «Hecho y no hace nada»**. | `planner-history.json` acumulado hace que el modelo crea que ya lo hizo. Verificado: forget → tool emitida. |
| I4 | **Gestor de archivos = folio**: `omarchy launch files` (parche en `src/omarchy_voice/{capabilities,tools}.py`). | Nautilus no existe desde el 11-nov (folio, r7697). |
| I5 | **La transcripción no se ancla a mensajes de log**: el wrapper filtra por formato (prefijos `Loading audio file`/`Audio format`/`Processing` + líneas con ANSI), no por la frase `transcription completed`. En bytia-voice usar la variante **CPU AVX-512** (la GPU abortaba al salir: observado en voxtype 1.0.1, coredump ~360 MB/frase; instalado hoy 1.3.0, **sin re-verificar** — re-testear GPU en cada update mayor antes de retirar el workaround). ⚠️ La **rama fallback** de `transcribe()` (sin el binario AVX-512) NO aplica ese filtro — fix en Fase 2. | El update de voxtype del 19-nov cambió la salida → `sed` viejo devolvía vacío → «No entendí nada». |
| I6 | **OSD**: ventana por el fichero `voxtype/state` (ya integrado); curvas por `audio.sock` del daemon (solo si graba él). El orbe propio (VoiceOrb) leerá `omarchy-voice/{state.json,level}`. | El OSD de Quickshell lee state (fichero) + audio.sock (daemon). |
| I7 | **OpenAI Live es un modo aparte** (toggle Super+Shift+V). Sin `OPENAI_API_KEY` da 401 y **no afecta a F9/F10** (100% locales). | F10 = voxtype+planner local; los 401 no tocan ese flujo. |
| I8 | **El planner debe caber con parakeet**: ≤ ~7 GiB en VRAM (TwIL 5 GB o gemma-12b-qat 6,7 GB). Modelos grandes (Coder 16,5 GB) rompen la transcripción GPU de F9 por `OutOfDeviceMemory`. | Medido: parakeet 740 MB no cabía con el Coder cargado. F10 transcribe por CPU igualmente. |
| I9 | **Parches al fork — inventario completo con estado**: identidad/persona (`6d1d431`), memoria del planner (`53d4db4`), endpoints OpenAI-compatibles (`6d1d431`), `bytia-llm` (`ddde6b9`), folio (`37b956f`), y **jeval + streaming + modelo dinámico + ventana de memoria** portados en `4aa2e6e` desde `feat/jeval-router` (= estado vivo verificado md5). Tras este diseño, el repo ES la fuente; hasta que `install.sh` esté extendido (Fase 2), reinstalar con el install.sh actual **borra jeval/streaming** (hace `rm -rf` del `src/` instalado) → gate `diff -rq` obligatorio. Proponer el fix folio a upstream. | El fork es upstream + commits propios; la copia instalada iba por delante de `main` hasta la reconciliación del 21-nov. |

## 4. Distribución (qué hace y qué hará install.sh)

**Hecho — Fase 0, 21-nov**: `main` contiene el estado vivo (`37b956f` + `4aa2e6e`):
8 `bin/bytia-*`, `plugin/bytia.voice`, parche folio, jeval/streaming/persona/config,
`share/config.example.toml` con `router_mode`/`router_threshold`, `.gitignore`
(`*.bak`, `*.bak-*`, `*.bak.*`). Gate verificado: `diff -rq repo/src ↔
~/.local/share/omarchy-voice/src` = vacío (md5 a md5 en planner/config/live/cli/
persona/jeval) y 20/20 tests del planner. `feat/streaming` queda como rama histórica
(su contenido está en `main`); `feat/jeval-router` también (no comparte historia
con `main`: era una rama reescrita — se reconcilió por contenido).

**Por hacer — Fase 2 (install.sh)**:
- Symlinkear **todos** los `bin/bytia-*` a `~/.local/bin` **apuntando al repo**
  (`$SOURCE/bin`), corrigiendo `install.sh:60-61` (hoy apunta a la copia instalada
  y repuntaría el symlink vivo de `bytia-llm` sin aviso).
- Instalar `plugin/*` y versionar los **8 units systemd propios** en `share/units/`
  (bytia-ttsd, audio-reaper svc+timer, voice-heal svc+timer, voice-border,
  notify-voice) con sus dependencias (venv piper por ruta absoluta, border.qml).
- Extender `uninstall.sh` al nuevo alcance (hoy solo conoce omarchy-voice/vision).
- `share/env.example` (solo nombres de vars) y actualizar `share/config.example.toml`
  al perfil del fork (TwIL + engine live; hoy es el perfil upstream y no arranca).
- Gate post-install: `diff -rq "$SOURCE/src" "$PREFIX/src"` debe dar vacío.
- Plantilla voxtype: la gestiona el paquete del sistema — no se versiona (decisión).

## 5. Pendientes (post-GO)

1. **Orbe con curvas** (con H3/H9 de la revisión cerrados): el `state.json` del runtime
   **ya tiene dueño** (el daemon) y el orb exige `status ∈ {listening,thinking}` +
   `updated` (fresh < 900 s). Productor único y fichero propio (PROPUESTO:
   `bytia-state.json` con `updated`) o contrato con el del daemon — decisión §6(d).
   `bytia-voice-orb-level` **lee PCM por stdin** (no «el WAV»: hay que cablear
   `pw-record | tee`), el factor `rms/3000` es mágico (documentarlo o parametrizarlo),
   y la mitad «oma» del anillo no tiene fuente (recortar o alimentar desde bytia-tts —
   trabajo nuevo no previsto). `chmod +x` ya hecho (Fase 0).
2. **Modo Live/OpenAI**: la decisión real es tecla nueva / solo widget-CLI / retirar
   (no «mantener o retirar el toggle»: el toggle como tecla no existe hoy) — §6(c).
3. **Coexistencia de indicadores en barra**: `bytia.voice` (fork) y `voice.indicator`
   (upstream) están ambos desplegados — decisión §6(c2).
4. **Smoke automatizado**: WAV de fixtures → transcribe → planner → tool esperada
   (el bench `~/Projects/bytia-voice-bench` cubre el planner; falta el wrapper).
5. **Upstream**: PR con el fix folio (`nautilus`→`files`).
6. **Fixes de código de la revisión adversarial** (Fase 2, con test):
   `bytia-llm` preset `local` `none`→`low` (I2); filtro de prefijos en la rama
   fallback de `transcribe()` (I5); borrar `rms()` muerta y su comentario en
   `bytia-dict`; añadir `forget` al usage de `bytia-voice`; gate de VRAM en
   `bytia-llm` para I8 (tabla modelo→GiB medida); `uninstall.sh` extendido.

## 6. Decisiones (Socio)

- **(a) Binarios: symlink al repo vs copia** — **PROPUESTO: symlink al repo** para los
  `bin/bytia-*` (es el patrón ya vivo de `bytia-llm`, verificado con `readlink`) y
  copia instalada para `src/` con el gate `diff -rq` vacío. Ya no bloquea: Fase 0
  reconcilió `main` con el estado vivo.
- **(b) Orbe vs OSD de voxtype** — **PROPUESTO: conviven**, pero el orbe SOLO tras
  cerrar productor único + formato `updated` + mapping de estados (§5.1); el OSD de
  voxtype ya cubre F10 hoy (`voxtype/state`, verificado). Sustitución total solo si
  el orbe cubre también la ventana.
- **(c) Modo Live/OpenAI** — opciones: (1) **PROPUESTO: mantenerlo como modo
  secundario sin tecla propia** (widget `voice.indicator` + CLI); (2) tecla nueva;
  (3) retirar el modo y documentar `[live]` como legacy. Sin `OPENAI_API_KEY` da 401
  y no afecta a F9/F10.
- **(c2) Indicadores en barra** — hoy conviven `bytia.voice` y `voice.indicator`:
  PROPUESTO dejar ambos (uno muestra estado/menú propio, el otro expone el toggle del
  daemon) o fusionar en Fase 2 — decidir antes de tocar plugins.
- **(d) Cerebro canónico** — manda `~/.config/omarchy-voice/config.toml`
  (`planner_model`: hoy TwIL-8B; la cabecera del HANDOFF citaba Coder — obsoleto).
  El diseño no declara un modelo fijo: declara que **config manda** y que I8 limita
  el tamaño (≤ ~7 GiB en VRAM con parakeet).
