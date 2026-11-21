# omarchy-voice

> **BytIA fork** (`asturwebs/omarchy-bytia-voice`) — a Spanish-language, BytIA-branded
> build for our own desktop, and since 2026-11-21 the **single source of truth for the
> whole voice stack** of that machine (see [BytIA voice stack](#bytia-voice-stack-fork)
> and the [stack design](docs/DESIGN-STACK.md), in Spanish).
> Changes versus upstream: a configurable OpenAI-compatible planner (`[openai]` —
> Z.ai GLM, Unsloth/llama.cpp, Ollama), the Spanish **BytIA** persona, persistent
> planner memory, the `bytia-llm` brain switcher, the local F9/F10 pipeline
> (`bin/bytia-*`: voxtype + Parakeet STT + local planner + Piper TTS), the folio
> file-manager fix, the JEVAL router, phrase streaming and the BytIA bar widgets.
> Everything else — architecture, tools, policy gate, docs — is upstream work,
> credited here in full. Same MIT license; all upstream credit belongs to
> [wombatoperator/omarchy-voice](https://github.com/wombatoperator/omarchy-voice).

Voice control for the Omarchy desktop. Ask OMA to move windows, open applications,
read a page, or manage a background task. OpenAI handles speech and planning;
local tools carry out desktop actions through a policy gate.

**Experimental · Omarchy with Lua-based Hyprland · Python 3.11+ · MIT**

## What it does

- Controls applications, windows, workspaces, and desktop settings.
- Reads browser pages and managed terminals, with OCR when text is unavailable.
- Discovers commands, shortcuts, and applications from your installed system.
- Supports Realtime voice and an optional Live backend with delegated tasks.
- Runs durable coding and analysis tasks with saved artifacts and verification.
- Inspects physical objects through an on-demand camera preview with configurable
  cloud or local vision models, cropping, and gentle sharpening.

Example requests:

- “Move this window to workspace three.”
- “Open a terminal beside the browser.”
- “Read the current page.”
- “Look at this connector.”
- “Tell me when the build finishes.”

OMA starts muted. While listening is enabled, microphone audio goes to OpenAI.
Page text, screenshots, and tool results may also be sent when a task uses them.
Camera inspections send a selected frame to the configured vision endpoint.
Cloud API usage is billed to your account. Read the [security policy](SECURITY.md) before
using an agent with your desktop and signed-in applications.

## BytIA voice stack (fork)

On this machine the fork also ships the local, **OpenAI-free** voice stack that drives
the desktop from the keyboard — the main flows need no cloud key:

- **F9 — dictation**: `bin/bytia-dict` → voxtype/Parakeet (local STT), typed into the
  focused window.
- **F10 — push-to-talk**: `bin/bytia-voice` records with PipeWire, transcribes with
  voxtype (CPU AVX-512), plans with the local planner (`omarchy-voice say`) and answers
  with Piper TTS (`bin/bytia-tts`).
- **Brain switcher**: `bin/bytia-llm local|ollama|cloud [model]` rewrites `[openai]` in
  `config.toml` and restarts the daemon (`low` reasoning effort for local Unsloth,
  `none` for Ollama — see the design doc). A measured VRAM gate refuses models that
  cannot fit next to Parakeet; override with `--force`.
- **Bar/OSD plugins**: `plugin/bytia.voice` (state + menu), `plugin/voice.indicator`
  (upstream toggle) and the upstream orb OSD.
- **systemd user units** (7): TTS daemon, voice heal, audio reaper, bar border and
  notification reader — versioned in `share/units/` with `%h` paths.

`install.sh` links every `bin/bytia-*` **to the clone** (edit the repo → live change),
copies the units and plugins, and enforces a hard post-install gate (`diff -rq src`)
so an install can never silently drop local work. `uninstall.sh` mirrors it.
Environment variable names (no values) are listed in [`share/env.example`](share/env.example).

Design, invariants and open decisions: [`docs/DESIGN-STACK.md`](docs/DESIGN-STACK.md)
(in Spanish; it is the source of truth for this stack).

## Install

You need an Omarchy desktop, Python 3.11 or later, PipeWire with a working
microphone/output device, and an OpenAI API key with access to the configured
models. The installer can install Arch's `python-websockets` package. Individual
tools may also need `tmux`, `wtype`, `ydotool`, `grim`, or `tesseract`; `doctor`
reports available desktop capabilities. Camera vision also needs FFmpeg
(`ffmpeg` and `ffplay`) and a supported V4L2 camera; see [OMA Vision](docs/vision.md).

```sh
git clone https://github.com/wombatoperator/omarchy-voice.git
cd omarchy-voice
./install.sh
```

Run the installer as your desktop user. It copies the application and asks before
adding the bar widget, systemd user service, keybinding, or optional
`omarchy voice` command aliases. It preserves existing configuration and backs up
keybindings before editing them.

Add your API key to `~/.config/omarchy-voice/env` using your editor:

```sh
OPENAI_API_KEY=your-api-key
```

Keep that file private (`chmod 600 ~/.config/omarchy-voice/env`). A key exported
only in a terminal is not automatically available to the systemd service.

```sh
omarchy-voice doctor
systemctl --user start omarchy-voice  # if you installed the user service
```

Without the service, run `omarchy-voice run` in a terminal. Press
**Super + Shift + V** if you installed the binding, or click the bar widget, to
toggle listening. The indicator shows listening, working, and confirmation states.
Clicking it while a confirmation is pending confirms the held action.

## Everyday commands

| Command | Purpose |
| --- | --- |
| `omarchy-voice listen toggle` | Start or stop listening in the running daemon |
| `omarchy-voice listen confirm` | Confirm a held action locally |
| `omarchy-voice listen cancel` | Cancel a held confirmation; Live also cancels unstarted tool calls |
| `omarchy-voice say "open a terminal"` | Send a typed request to the one-shot planner |
| `omarchy-voice --dry-run say "open a terminal"` | Preview changing actions; still permits read-only queries and API use |
| `omarchy-vision start` | Open the local camera preview without an API call |
| `omarchy-vision stop` | Close the camera preview and release capture |
| `omarchy-voice status --json` | Inspect daemon state |
| `omarchy-voice map` | Explore local capabilities without an API request |
| `omarchy-voice log -f` | Follow private diagnostic logs |

Muting stops the recorder. It does not undo an action already started or cancel
independent background workers. Manage those with `omarchy-voice task`.

## Configuration

Edit `~/.config/omarchy-voice/config.toml`. The commented
[configuration example](share/config.example.toml) lists defaults and optional
settings. Restart the idle daemon after changes.

The Python application honors `XDG_CONFIG_HOME`, `XDG_STATE_HOME`,
`XDG_CACHE_HOME`, and `XDG_RUNTIME_DIR`. The installer, uninstaller, and supplied
service use the standard home-directory paths shown here. Custom XDG layouts or
an alternate install `PREFIX` need corresponding service/configuration changes.

Realtime is the default voice engine. To select Live:

```toml
[openai]
engine = "live"
```

See [Live setup](docs/live.md) for model access, session limits, audio behavior,
and switching engines. Shell execution is disabled by default. Confirmation
rules reduce mistakes but do not make desktop automation a sandbox.

For speakers, leave `barge_in = false` under `[ears]` to reduce echo-triggered
commands. Use headphones or configure PipeWire echo cancellation before enabling
interruptions; an [example configuration](share/echo-cancel.conf) is included.

## Documentation and support

| Guide | Contents |
| --- | --- |
| [Live backend](docs/live.md) | Setup, usage controls, browser delegation, recovery |
| [Task workers](docs/task-workers.md) | Submit, inspect, cancel, and resume durable work |
| [OMA Vision](docs/vision.md) | Camera setup, model switching, crop, privacy, and latency |
| [Diagnostics](docs/diagnostics.md) | Troubleshooting, latency, and private logs |
| [System discovery](docs/omarchy-architecture.md) | How OMA reads the installed desktop's capabilities |
| [BytIA stack design](docs/DESIGN-STACK.md) | Fork: local F9/F10 stack, invariants and distribution (Spanish) |
| [Security](SECURITY.md) | Data sharing, execution boundaries, private disclosure |
| [Contributing](CONTRIBUTING.md) | Development setup, tests, and pull requests |

For bugs, [open an issue](https://github.com/wombatoperator/omarchy-voice/issues/new/choose)
with your versions, reproduction steps, and expected behavior. Review any excerpt
before attaching it. Report security vulnerabilities through the private process
in [SECURITY.md](SECURITY.md).

## Uninstall

From your clone, run `./uninstall.sh`. It removes the installed application and
integration, preserving configuration, logs, and task artifacts. Cancel active
background workers first. `./uninstall.sh --purge` also deletes
`~/.config/omarchy-voice` and `~/.local/state/omarchy-voice`. Custom XDG paths and
task roots outside those directories remain. The default cache is removed in
both modes.

## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m unittest discover -s tests
```

The Python package provides `omarchy-voice` and `omarchy-vision`; the installer
handles desktop integration.
Unit tests use synthetic inputs and mocked providers, plus local test sockets.
Paid API and real-desktop checks are separate, opt-in commands described in
[CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the [MIT License](LICENSE).
