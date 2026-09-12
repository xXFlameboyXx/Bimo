# Bimo 🤖 — Physical Desktop AI Robot Hub

Bimo is an always-on physical desktop AI robot built on a **Raspberry Pi 3 Model A+** (512MB RAM) and a **3.5-inch 480x320 SPI TFT LCD** (ILI9486).

---

## Architecture Overview

```text
       ┌─────────────────────────────────────────────────────────────┐
       │                       Robot Core                            │
       │                                                             │
       │   [Event Sources]                 [Robot State Machine]     │
       │   - Wake word                     States:                   │
       │   - Speech / Voice                - IDLE (with sub-cats)    │
       │   - AI / Tools                    - LISTENING               │
       │   - Laptop status                 - THINKING                │
       │          │                        - EXECUTING               │
       │          ▼                        - SPEAKING                │
       │     ┌─────────┐   Event Dispatch  - SUCCESS                 │
       │     │Event Bus│ ────────────────► - ERROR                   │
       │     └────┬────┘                   - SLEEPING                │
       └──────────┼──────────────────────────────────────────────────┘
                  │ State Changed Event
                  ▼
       ┌─────────────────────────────────────────────────────────────┐
       │                BaseFaceRenderer (Interface)                 │
       │   - initialize()                                            │
       │   - set_state(RobotState)                                   │
       │   - render_frame()                                          │
       │   - display_status(text)                                    │
       │   - close()                                                 │
       └──────────────┬───────────────────────────────┬──────────────┘
                      │                               │
       ┌──────────────┴──────────────┐ ┌──────────────┴──────────────┐
       │    FaceSimulator (Tkinter)  │ │ PhysicalFaceRenderer (Pi)   │
       │    - Windows / Mac / Linux  │ │ - Raspberry Pi 3.5" TFT     │
       │    - 800x480 desktop face   │ │ - Linux /dev/fb1 (ILI9486)  │
       │    - Expressive animations  │ │ - Zero-copy mmap blits      │
       │    - Keyboard shortcuts     │ │ - < 0.8% CPU, 1.13ms latency│
       └─────────────────────────────┘ └─────────────────────────────┘
```

### Key Architectural Principles
1. **Ultra-Low Memory & CPU Footprint**:
   Tailored specifically for the Raspberry Pi 3A+ (512MB RAM). Renders at **0.72% CPU** and **~48 MB RAM total footprint** by pre-scaling and pre-encoding all sprite frames into 16-bit RGB565 byte buffers at startup.
2. **Autonomous Hub & Decoupled Architecture**:
   The Raspberry Pi is the primary, always-on brain. Domain logic and state machines are completely decoupled from graphics and physical display drivers.
3. **Pluggable Display Contract (`BaseFaceRenderer`)**:
   Both `FaceSimulator` and `PhysicalFaceRenderer` adhere strictly to `BaseFaceRenderer`. Switching backends requires only a single environment variable (`DISPLAY_BACKEND="physical_lcd"` vs `DISPLAY_BACKEND="simulator"`).
4. **Resilient Off-Hardware Development**:
   `PhysicalFaceRenderer` includes a full in-memory mock fallback so the exact same hardware driver can be unit-tested on Windows, macOS, or headless Linux without hardware attached.

---

## Hardware Specification & Pinout

Phase 2 verified and mapped the physical hardware configuration:

| Component | Specification | Details |
| :--- | :--- | :--- |
| **SBC** | Raspberry Pi 3 Model A+ | Quad-Core ARM Cortex-A53, 512MB LPDDR2 RAM |
| **OS** | Debian 13.5 (Trixie) / aarch64 | Linux Kernel 6.18.34+rpt-rpi-v8 |
| **LCD Controller** | ILI9486 3.5" TFT | 480x320 Landscape, 16 bpp RGB565 |
| **Touch Controller** | ADS7846 / XPT2046 | Resistive touchscreen on `spi0.1` |
| **Display Device** | `/dev/fb1` | Linux `fbtft` (`fb_ili9486`) kernel driver |
| **SPI Clock** | 16 MHz | `dtoverlay=piscreen,speed=16000000,rotate=270` |

### GPIO Pin Mapping

| Function | Pin Number | BCM GPIO | Connection |
| :--- | :--- | :--- | :--- |
| **MOSI** | Pin 19 | GPIO 10 | SPI0 Master Out Slave In |
| **MISO** | Pin 21 | GPIO 9 | SPI0 Master In Slave Out |
| **SCLK** | Pin 23 | GPIO 11 | SPI0 Serial Clock (16 MHz) |
| **LCD CS** | Pin 24 | GPIO 8 | SPI0 Chip Enable 0 (CE0) |
| **Touch CS** | Pin 26 | GPIO 7 | SPI0 Chip Enable 1 (CE1) |
| **D/C** | Pin 18 | GPIO 24 | Data / Command Select |
| **RESET** | Pin 22 | GPIO 25 | LCD Hardware Reset |
| **Touch IRQ** | Pin 11 | GPIO 17 | Touch Pen Interrupt (PENIRQ) |

---

## Project Structure

```text
Bimo/
├── .env                               # Active configuration (.env file)
├── pyproject.toml                     # Modern package metadata and test config
├── README.md                          # Architecture guide & documentation
├── test_lcd_hardware.py               # Phase 2 Physical LCD Diagnostic Suite
├── benchmark_performance.py           # CPU, memory, and frame latency benchmark
├── faces/                             # Native face sprites (800x480 source assets)
│   ├── idle/
│   │   ├── 01_blink/                  # Sub-category 1 (blinking)
│   │   ├── 02_look/                   # Sub-category 2 (looking around)
│   │   ├── 03_sleep/                  # Sub-category 3 (dozing)
│   │   └── 04_glance/                 # Sub-category 4 (glancing)
│   ├── listening/                     # Alternate listening frames
│   ├── thinking/                      # 4-frame rotating thought cycle
│   ├── capturing/                     # Task execution face
│   ├── speaking/                      # 3-frame speaking mouth cycle
│   ├── error/                         # Error expression
│   └── warmup/                        # Sleep / rest face
├── src/
│   └── bimo/
│       ├── __init__.py
│       ├── core/
│       │   ├── config.py              # Zero-dep type-safe configuration
│       │   ├── logging.py             # Console + SD-safe rotating file logger
│       │   ├── events.py              # Typed EventBus (pub/sub)
│       │   └── state.py               # RobotState enum & RobotStateMachine
│       ├── interfaces/
│       │   ├── llm.py                 # LLMProvider, Message, ToolCall interfaces
│       │   ├── tools.py               # BaseTool & ToolRegistry interfaces
│       │   └── devices.py             # BaseDevice & DeviceRegistry
│       ├── rendering/
│       │   ├── base.py                # BaseFaceRenderer abstract interface
│       │   ├── factory.py             # create_face_renderer() factory
│       │   ├── face_simulator.py      # Tkinter desktop face simulator
│       │   ├── physical_lcd.py        # Raspberry Pi ILI9486 /dev/fb1 renderer
│       │   └── lcd_diagnostic.py      # 8-step hardware diagnostic suite
│       └── simulator_app.py           # Unified launcher (Simulator & Physical)
└── tests/
    ├── test_config.py                 # Config & env validation tests
    ├── test_logging.py                # Logging rotation tests
    ├── test_event_bus.py              # Pub/sub & event dispatch tests
    ├── test_state_machine.py          # State transitions & constraint tests
    ├── test_llm_interface.py          # LLM interface contract tests
    ├── test_tools.py                  # Tool interface & registry tests
    ├── test_devices.py                # Device registry tests
    ├── test_rendering_interface.py    # BaseFaceRenderer contract tests
    └── test_physical_lcd.py           # PhysicalFaceRenderer & mock tests
```

---

## Running & Testing

### 1. Run Physical LCD Hardware Diagnostics (Raspberry Pi)

Executes the 8-step diagnostic suite on the physical LCD screen:
1. Screen initialization
2. Screen clear (black)
3. Solid colors (Red, Green, Blue, White, Black)
4. Text rendering & calibration
5. Geometric shapes & 4 corner alignment markers
6. Pure face sprite rendering across all IDLE sub-categories
7. Full Robot State Machine transitions (driven by EventBus)
8. Frame throughput benchmark and clean shutdown

```bash
python test_lcd_hardware.py --fb /dev/fb1 --hold 1.0
```

*(You can also run with `--mock` on Windows/Mac to verify diagnostics without hardware).*

### 2. Measure CPU & Memory Performance

```bash
python benchmark_performance.py
```

**Empirical Pi 3A+ Benchmark Results:**
- **Frame Blit Latency**: 1.13 ms / frame
- **Average CPU Usage**: 0.72% (animating at 30 FPS)
- **Peak RAM Footprint**: 48.70 MB (~9.5% of 512MB RAM)

### 3. Launch Bimo Face Application

**On Raspberry Pi (Physical 3.5" LCD):**
```bash
python -m bimo.simulator_app --backend physical_lcd
```
*(Runs face animations on `/dev/fb1` while providing an interactive terminal control console over SSH).*

**On Windows / Desktop (Simulator Window):**
```bash
python -m bimo.simulator_app --backend simulator
```

### 4. Run Automated Unit Tests

Run all 44 unit tests across both platforms:

```bash
# On Windows
python -m unittest discover -s tests -t .

# On Raspberry Pi
python3 -m unittest discover -s tests -t .
```

---

## Face Sprite Mappings & Animations

- **`IDLE`**: Automatic timed rotation across sub-categories with custom durations and internal frame speeds:
  - `01_blink`: [`idle 01.png` - `03.png`] (duration=300s, frame_interval=0.80s)
  - `02_look`: [`idle 04.png` - `07.png`] (duration=300s, frame_interval=0.80s)
  - `03_sleep`: [`idle 08.png` - `09.png`] (duration=300s, frame_interval=0.80s)
  - `04_glance`: [`idle 10.png` - `12.png`] (duration=300s, frame_interval=0.80s)
- **`LISTENING`**: Alternating ear-tilt listening animation (`listen 01.png` - `02.png`)
- **`THINKING`**: Continuous 4-frame rotating eye cycle (`thinking 01.png` - `04.png`)
- **`EXECUTING`**: Focused task expression (`capturing 01.png`)
- **`SPEAKING`**: Dynamic 3-frame speaking mouth cycle (`speaking 01.png` - `03.png`)
- **`SUCCESS`**: Smiling happy expression (`speaking 01.png`)
- **`ERROR`**: Error face (`error 01.png`)
- **`SLEEPING`**: Warmup / calm rest face (`warmup 01.png`)

---

## Voice Pipeline & Speech Subsystem (Phases 3.5 & 4)

Bimo features a modular, hardware-independent voice pipeline with zero cloud dependencies:

```text
Microphone (PC / Pi)
       │
       ▼
BaseWakeWordDetector (OpenWakeWord)
  - Evaluates all configured models concurrently
  - Deterministic tie-breaking by confidence & config order
       │ [WAKE_WORD_DETECTED] -> transitions to LISTENING
       ▼
Speech Capture & EnergyVAD
  - Dynamically frames user speech audio
  - Detects utterance completion & silence
       │ [SPEECH_CAPTURE_STOPPED]
       ▼
SpeechToText (WhisperSTT / faster-whisper)
  - Transcribes audio locally on CPU (int8)
       │ [SPEECH_RECEIVED] -> transitions to THINKING
       ▼
AgentInputInterface
  - Receives user query (LLM execution strictly deferred)
       │
       ▼
TextToSpeech (PiperTTS / MockTTS / WindowsTTS)
  - Local neural synthesis using Piper voice `en_GB-semaine-medium`
  - Speaks audio through output device (PC speakers / future Pi speaker)
       │ [SPEECH_OUTPUT_STARTED] -> transitions to SPEAKING
       │ [SPEECH_OUTPUT_FINISHED] -> transitions to IDLE
```

### 1. Multi-Wake-Word Configuration
Multiple wake words can activate Bimo simultaneously. Any configured wake word transitions the robot from `IDLE` (or interrupts `SPEAKING` on barge-in) to `LISTENING`:

```env
# Comma-separated list of wake words
WAKE_WORD_MODELS=hey_jarvis,alexa,hey_mycroft
WAKE_WORD_THRESHOLD=0.5
WAKE_WORD_COOLDOWN=1.5
```

- **Adding a Wake Word**: Add another model name to `WAKE_WORD_MODELS` (e.g. `WAKE_WORD_MODELS=hey_jarvis,alexa,hey_mycroft`). Built-in openWakeWord models are automatically cached.
- **Custom Wake Word Models**: You can specify custom trained ONNX models (e.g., `models/wakeword/hey_bimo.onnx`) directly in `WAKE_WORD_MODELS`. The loader checks both filesystem paths and built-in models.
- **Deterministic Detection**: Incoming 16 kHz audio frames are fed once across all configured models. If multiple models exceed threshold, the model with the highest confidence wins; ties break by configured list order.

### 2. Local Neural Text-to-Speech (Piper TTS)
Phase 4 integrates local neural TTS using **Piper** with voice **`en_GB-semaine-medium`**:

```env
TTS_PROVIDER=piper
PIPER_MODEL_PATH=models/piper/en_GB-semaine-medium.onnx
PIPER_CONFIG_PATH=models/piper/en_GB-semaine-medium.onnx.json
TTS_VOICE=en_GB-semaine-medium
TTS_SPEECH_RATE=175
TTS_VOLUME=1.0
```

- **Hardware Independence**: High-level agent and state machine logic interact solely with `BaseTextToSpeech`. The higher-level system does not know whether audio is rendered to Windows PC speakers or a future Raspberry Pi speaker.
- **Development vs Deployment Hardware**:
  - Development currently plays through Windows PC speakers via `sounddevice`.
  - Future Raspberry Pi deployment will swap output via `TTS_PROVIDER=pi` without modifying higher-level voice logic or state machines.
- **Barge-In & Interruption**: Calling `tts.stop()` immediately cancels audio playback and worker threads, releasing audio resources and dispatching `SPEECH_OUTPUT_CANCELLED`. A wake word spoken during speech seamlessly interrupts playback and transitions the robot into `LISTENING`.

### 3. Voice Verification & Demo Commands

Run automated tests:
```bash
python -m unittest discover -s tests -t . -v
```

Run simulated pipeline demo:
```bash
python test_voice_live.py --mock
```

Run live voice pipeline with PC microphone & Piper TTS:
```bash
python test_voice_live.py --live --wake-word-models hey_jarvis,alexa
```

Run live TTS standalone demo:
```bash
python test_tts_live.py --provider piper --text "Hello, I am Bimo."
```

---

## Phase 5: LLM Agent & OmniRoute Integration

Phase 5 introduces the cognitive brain of Bimo: an LLM-driven conversational agent operating behind an abstract, provider-independent interface.

```text
User Speech Transcribed
       │ [SPEECH_RECEIVED]
       ▼
BimoAgent (AgentInputInterface)
  - Intercepts user text
  - Dispatches EventType.AI_STARTED
  - Transitions RobotStateMachine -> THINKING
       │
       ▼
ConversationContext
  - Injects System Prompt (Bimo personality & safety boundaries)
  - Maintains bounded sliding window (LLM_MAX_HISTORY turns)
  - Preserves multi-turn dialogue
       │
       ▼
LLMProvider (OmniRouteLLM / MockLLMProvider)
  - Provider-independent HTTP client (standard library urllib)
  - Connects to OmniRoute gateway (OpenAI-compatible /v1/chat/completions)
  - Configurable model (LLM_MODEL) and timeout
       │
       ▼
LLMResponse
  - Structured result: text, tool_calls, provider metadata, error state
       │
       ▼
BimoAgent
  - Records assistant response to context
  - Dispatches EventType.AI_FINISHED (or AI_ERROR -> ERROR on failure)
  - Enforces strict Phase 5 security: NO tool or command execution
       │
       ▼
TextToSpeech (BaseTextToSpeech)
  - Calls tts.speak(response_text)
  - Dispatches EventType.SPEECH_OUTPUT_STARTED -> transitions to SPEAKING
  - Dispatches EventType.SPEECH_OUTPUT_FINISHED -> transitions to IDLE
```

### 1. Key Principles & Architectural Separation
- **Strict Separation of Concerns**:
  - **Voice Input**: Microphone, openWakeWord, VAD, faster-whisper.
  - **Agent**: Conversation context, system prompt, LLM communication.
  - **Tools**: Strictly **deferred to Phase 6**. The LLM receives **no execution capabilities** in Phase 5.
  - **Speech Output**: Piper neural TTS and audio output hardware.
- **Provider Independence**: `BimoAgent` does not know whether it is communicating with OmniRoute, a direct API, or a mock test double. All interaction goes through [`BaseLLMProvider`](file:///e:/Bimo/src/bimo/interfaces/llm.py).
- **Subsystem Independence & Offline Resilience**: Local subsystems (microphones, openWakeWord wake-word detection, Piper TTS, and LCD face display) remain fully functional even if OmniRoute or the network goes offline. On LLM connection failure, Bimo gracefully speaks a friendly offline notice and transitions through `ERROR` back to `IDLE` without crashing.

### 2. Configuration (`.env`)
```env
# LLM / OmniRoute Configuration
LLM_PROVIDER=omniroute
OMNIROUTE_BASE_URL=http://localhost:20128
LLM_MODEL=gemini-3.8-flash
LLM_API_KEY=
LLM_TIMEOUT=30.0
LLM_MAX_HISTORY=20
```

### 3. Running & Verifying Phase 5

**Run automated unit tests (all 141 tests pass offline):**
```bash
python -m unittest discover -s tests -t . -v
```

**Run deterministic end-to-end mock agent demo:**
```bash
python test_agent_mock.py
```
*(Demonstrates: "Hello Bimo" -> AgentInput -> MockLLMProvider -> MockTTS -> state flow `THINKING` -> `SPEAKING` -> `IDLE` without needing hardware or live network).*

**Run live OmniRoute diagnostic utility:**
```bash
python test_llm_live.py --prompt "Hello Bimo!"
```
*(Connects to your local OmniRoute instance at `http://localhost:20128`, sends a test prompt, and reports latency and response details).*

---

## Phase 6: Secure Tool System

Phase 6 implements Bimo's secure tool infrastructure, allowing the LLM reasoning engine to request explicitly registered tools while maintaining a strict security sandbox.

```text
User Speech / Input
       │ [SPEECH_RECEIVED]
       ▼
BimoAgent (AgentInputInterface)
  - Emits EventType.AI_STARTED -> transitions to THINKING
  - Prepares OpenAI-compatible function schemas from ToolRegistry
       │
       ▼
LLMProvider (OmniRoute / Mock)
  - Evaluates conversational context + available tool schemas
       │
       ▼
Model requests Tool Calls?
  ├── NO  ──► Natural Language Response ──► TextToSpeech.speak() ──► SPEAKING ──► IDLE
  └── YES
        │ [EventType.TOOL_CALL_REQUESTED]
        ▼
   State Machine transitions -> EXECUTING
        │
        ▼
   Tool Call Parser & Validator
     - Resolves tool name via ToolRegistry (strictly rejects unknown tools)
     - Type-checks arguments against ToolParameter schemas
     - Rejects unexpected extra parameters under strict validation
        │
        ▼
   Centralized Permission Policy
     - SAFE: Autonomous execution permitted (robot.speak, robot.set_face, robot.get_status)
     - CONFIRM: Requires explicit user/system confirmation flag
     - HIGH_RISK: Prohibited by default in Phase 6
        │
        ▼
   Tool Executor (Timeout protected)
     - Executes tool in isolated worker thread
     - Catches and encapsulates exceptions into structured ToolResult
     - Emits TOOL_EXECUTION_COMPLETED / TOOL_EXECUTION_FAILED / TOOL_EXECUTION_DENIED
        │
        ▼
   ConversationContext
     - Injects assistant message with tool calls
     - Injects role="tool" result messages with tool_call_id
        │
        ▼
   State Machine transitions -> THINKING
        │
        ▼
   LLMProvider (Next reasoning turn, bounded by MAX_TOOL_ITERATIONS)
        │
        ▼
   Final Natural Language Output ──► TextToSpeech.speak() ──► SPEAKING ──► IDLE
```

### 1. What is a Tool?
A Tool is a typed, self-describing action adhering to [`BaseTool`](file:///e:/Bimo/src/bimo/tools/base.py). Each tool declares:
- **Name**: Unique identifier (e.g. `robot.speak`, `robot.set_face`, `robot.get_status`).
- **Description**: Natural language guidance explaining its purpose and behavior to the LLM.
- **Parameters**: Typed descriptors ([`ToolParameter`](file:///e:/Bimo/src/bimo/tools/base.py)) enforcing JSON data types (`string`, `integer`, `number`, `boolean`, `array`, `object`) and optional enum constraints.
- **Permission Level**: [`ToolPermission`](file:///e:/Bimo/src/bimo/tools/permissions.py) tier (`SAFE`, `CONFIRM`, `HIGH_RISK`).
- **Execution Method**: Safely encapsulated `execute(**kwargs) -> ToolResult`.

### 2. Centralized Permission System
- **`ToolPermission.SAFE`**: Actions with zero risk of harm or side-effects (e.g., querying robot status, updating the screen face, speaking text). Permitted autonomously.
- **`ToolPermission.CONFIRM`**: Actions requiring explicit user approval before execution (e.g., future system configuration edits, deletions). Blocked unless `confirmed=True` is provided in the execution context.
- **`ToolPermission.HIGH_RISK`**: Destructive or sensitive actions (e.g., future shell execution or shutdown). Prohibited by default policy.
- **Tamper-Proof**: Tools cannot escalate their own permission level. The policy is centralized in [`PermissionPolicy`](file:///e:/Bimo/src/bimo/tools/permissions.py).

### 3. Registered Phase 6 Safe Tools
Only three initial safe robot tools are registered in Phase 6:
1. **`robot.speak`** (`ToolPermission.SAFE`): Speaks natural language aloud through the existing [`BaseTextToSpeech`](file:///e:/Bimo/src/bimo/interfaces/voice.py) interface.
2. **`robot.set_face`** (`ToolPermission.SAFE`): Updates Bimo's animated face on the state machine / event bus (`idle`, `listening`, `thinking`, `executing`, `speaking`, `success`, `happy`, `error`, `sleeping`). Does not manipulate LCD hardware directly.
3. **`robot.get_status`** (`ToolPermission.SAFE`): Queries non-sensitive operational status (current state, uptime, active subsystems). Secrets and API keys are strictly omitted.

### 4. Security Restrictions & Prohibited Capabilities
Phase 6 strictly proves that the tool execution framework works safely. The following capabilities are **explicitly prohibited and deferred to future phases**:
- ❌ No Windows computer control, keyboard/mouse emulation, or screen capture (Phase 7 & 8).
- ❌ No shell or terminal command execution (`subprocess`, `os.system`).
- ❌ No arbitrary Python execution (`eval()`, `exec()`, dynamic imports).
- ❌ No filesystem alterations.
- ❌ No browser automation.
- ❌ No RGB hardware or smart-home device control (Phase 9 & 10).
- ❌ No camera capture.

Any attempt by the LLM to request an unregistered tool name (e.g. `os.system` or `computer.click`) is rejected immediately by [`ToolRegistry`](file:///e:/Bimo/src/bimo/tools/registry.py) with a structured error.

### 5. Running & Verifying Phase 6

**Run complete automated unit test suite (all 177 tests pass offline):**
```bash
python -m unittest discover -s tests -t . -v
```

**Run deterministic end-to-end mock tool demo:**
```bash
python test_tools_mock.py
```
*(Demonstrates full multi-turn tool loops: `robot.speak`, `robot.set_face`, and `robot.get_status` with `MockTTS` and `MockLLMProvider`).*

**Run live OmniRoute tool verification:**
```bash
python test_llm_live.py --with-tools --prompt "Set your face to happy and say hello Bimo"
```
*(Sends tool schemas to your live local OmniRoute instance, receives model tool calls, and executes them through the secure tool registry).*

---

## Phase 7: Secure Windows PC Agent

Phase 7 connects the central Bimo robot brain to an optional Windows PC Agent over a local trusted LAN using an authenticated, strictly bounded protocol.

### 1. Architecture

```
                    Raspberry Pi / Bimo
                  ┌─────────────────────┐
User → STT → LLM →│ Bimo Agent          │
                  │ Tool Registry       │
                  └──────────┬──────────┘
                             │ authenticated LAN (HMAC-SHA256)
                             ▼
                  ┌─────────────────────┐
                  │ Windows PC Agent    │
                  │ Command Registry    │
                  └──────────┬──────────┘
                             ▼
                  Windows OS APIs
```

- **Central Brain**: The Raspberry Pi remains the primary Bimo brain.
- **Optional Endpoint**: The Windows machine is strictly an optional execution target.
- **Offline Resilience**: When the Windows PC is shutdown, sleeping, or disconnected:
  - Bimo continues operating normally.
  - Robot-local tools (`robot.speak`, `robot.set_face`, `robot.get_status`) continue functioning.
  - PC-dependent tools report clean, structured errors (*"Your laptop is unavailable right now."*).
  - The robot never crashes or hangs on network failures.

### 2. Authentication & Replay Protection

Communication uses a pre-shared secret with cryptographic HMAC-SHA256 validation:
- **Canonical Envelope**: Every request includes `version`, `request_id`, `timestamp`, `nonce`, `command`, `arguments`, and `signature`.
- **Signature**: Computed via `HMAC-SHA256(secret, canonical_bytes)` and verified using constant-time comparison ([`hmac.compare_digest`](file:///e:/Bimo/src/bimo/pc/auth.py)).
- **Clock Skew Enforcement**: Requests with timestamps exceeding `PC_AGENT_MAX_CLOCK_SKEW` (default 30s) are rejected (`EXPIRED_TIMESTAMP`).
- **Nonce Replay Cache**: A thread-safe in-memory cache rejects replayed `(nonce, request_id)` pairs within the validity window (`REPLAY_DETECTED`).
- **Zero Secret Leakage**: The shared secret and raw signatures are never printed in logs or included in response payloads.

### 3. PC Command Registry & Allowlist Boundary

Commands are managed by [`PCCommandRegistry`](file:///e:/Bimo/windows_agent/registry.py) mirroring Phase 6 philosophy. Arbitrary execution is strictly impossible:
- ❌ **No Shell Execution**: `subprocess` with model strings, `os.system`, `cmd.exe`, and PowerShell are completely prohibited.
- ❌ **No Dynamic Code**: `eval()` and `exec()` are forbidden.
- ❌ **No Arbitrary Paths**: Executable paths (e.g. `C:\malware.exe`, `powershell.exe`) are blocked before execution.

#### Initial Safe PC Commands:
| Command | Permission | Description |
| :--- | :--- | :--- |
| **`pc.get_status`** | `SAFE` | Non-sensitive status: hostname, OS platform, agent version, uptime, registered commands. |
| **`pc.get_active_window`** | `SAFE` | Visible title and process name of the active foreground window. |
| **`pc.open_app`** | `CONFIRM` | Launches an explicitly allowlisted application (`notepad`, `calculator`, `explorer`). |
| **`pc.close_app`** | `CONFIRM` | Safely closes an allowlisted application by friendly name. |
| **`pc.type_text`** | `CONFIRM` | Types bounded text (max 1000 chars) directly into the focused window. |
| **`pc.press_key`** | `CONFIRM` | Presses an allowlisted key or shortcut (`ENTER`, `ESC`, `TAB`, `CTRL+C`, `CTRL+V`, `ALT+TAB`). |
| **`pc.click`** | `CONFIRM` | Simulates mouse click (`left` or `right`, single or double click). |

### 4. Bimo Tool Integration & Permissions

Every PC tool is registered into Bimo's Phase 6 [`ToolRegistry`](file:///e:/Bimo/src/bimo/tools/registry.py) with strict permissions:
- Read-only inspection tools (`pc.get_status`, `pc.get_active_window`) are **`SAFE`**.
- Interactive OS mutation tools (`pc.open_app`, `pc.close_app`, `pc.type_text`, `pc.press_key`, `pc.click`) are **`CONFIRM`**.
- Any execution of a `CONFIRM` tool without `confirmed=True` is blocked by the centralized [`PermissionPolicy`](file:///e:/Bimo/src/bimo/tools/permissions.py) and returns a polite explanation.

### 5. Configuration Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PC_AGENT_ENABLED` | `true` | Enable or disable PC agent client in Bimo |
| `PC_AGENT_HOST` | `0.0.0.0` | Bind address for Windows PC Agent daemon |
| `PC_AGENT_PORT` | `8088` | Port for Windows PC Agent |
| `BIMO_PC_AGENT_HOST` | `127.0.0.1` | Target Windows IP / hostname as seen by Bimo |
| `BIMO_PC_AGENT_PORT` | `8088` | Target Windows port as seen by Bimo |
| `PC_AGENT_SHARED_SECRET` | *required* | Pre-shared HMAC-SHA256 secret key |
| `PC_AGENT_CONNECT_TIMEOUT`| `3.0` | HTTP connect timeout in seconds |
| `PC_AGENT_REQUEST_TIMEOUT`| `10.0` | HTTP request timeout in seconds |
| `PC_AGENT_MAX_CLOCK_SKEW` | `30` | Maximum allowable clock skew in seconds |
| `PC_ALLOWED_APPS` | `notepad,calculator,explorer` | Comma-separated list of allowed app names |

*(Legacy `LAPTOP_*` environment variables remain fully supported for backwards compatibility).*

### 6. Running & Verifying Phase 7

**1. Start the Windows PC Agent independently:**
```bash
python -m windows_agent --host 127.0.0.1 --port 8765 --secret YOUR_SHARED_SECRET
```

**2. Run the automated live verification script:**
```bash
python test_pc_agent_live.py --host 127.0.0.1 --port 8765 --secret YOUR_SHARED_SECRET
```
*(Optionally add `--live-apps` to launch and type into a real Notepad window).*

**3. Run the complete automated test suite (all 208 tests offline):**
```bash
python -m unittest discover -s tests -t . -v
```
*(Includes 31 dedicated Phase 7 unit tests covering HMAC auth, nonce replays, clock skew, command allowlists, permission enforcement, and mock client resilience).*

---

## Phase 8: Controlled Computer Use

Phase 8 builds directly on Phase 7 to grant Bimo the controlled ability to observe and interact with the Windows desktop using typed, bounded, allowlisted primitives.

> **CRITICAL SECURITY BOUNDARY:**
> Phase 8 is **strictly controlled computer use**.
> - It is **NOT** unrestricted remote desktop control.
> - It is **NOT** a shell or terminal service.
> - It **CANNOT** execute arbitrary PowerShell, `cmd.exe`, `eval()`, `exec()`, arbitrary subprocesses, arbitrary executable paths, or unrestricted filesystem commands.

---

### 1. Architecture

```
                    Raspberry Pi / Bimo
                  ┌─────────────────────┐
User → STT → LLM →│ Bimo Agent          │
                  │ Tool Registry       │
                  └──────────┬──────────┘
                             │ authenticated LAN (HMAC-SHA256)
                             ▼
                  ┌─────────────────────┐
                  │ Windows PC Agent    │
                  │ Command Registry    │
                  └──────────┬──────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         Screenshot       Keyboard        Mouse
         / Vision         Controller      Controller
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                         Windows GUI
```

- **Separation of Concerns**: The Raspberry Pi Bimo brain coordinates conversational context, LLM reasoning, and tool selection.
- **Windows PC Agent**: Executes typed, validated commands dispatched over the single secure endpoint (`POST /api/v1/command`).
- **Autonomous & Human Approval Boundaries**: Phase 6 [`PermissionPolicy`](file:///e:/Bimo/src/bimo/tools/permissions.py) remains authoritative. Read-only observation tools execute autonomously (`SAFE`); desktop manipulation actions require explicit user confirmation (`CONFIRM`).

---

### 2. Computer-Use Command Model

Phase 8 expands the explicit [`PCCommandRegistry`](file:///e:/Bimo/windows_agent/registry.py) to 12 strictly typed commands:

| Category | Command | Permission | Arguments | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Observe** | `pc.get_status` | `SAFE` | None | Hostname, OS platform, agent version, uptime, registered commands. |
| **Observe** | `pc.get_active_window` | `SAFE` | None | Title, process name, and state of active foreground window. |
| **Observe** | `pc.get_screen_size` | `SAFE` | None | Primary monitor resolution (`width`, `height`). |
| **Observe** | `pc.screenshot` | `SAFE` | Optional: `max_width`, `max_height`, `max_bytes` | Primary desktop screenshot with bounded resolution and compression. |
| **Window** | `pc.focus_window` | `CONFIRM` | `title` (string) | Brings a visible matching window to the foreground deterministically. |
| **App** | `pc.open_app` | `CONFIRM` | `app` (string) | Launches allowlisted application (`notepad`, `calculator`, `explorer`). |
| **App** | `pc.close_app` | `CONFIRM` | `app` (string) | Closes allowlisted application cleanly. |
| **Keyboard** | `pc.type_text` | `CONFIRM` | `text` (string, max 1000 chars) | Types bounded unicode text directly into the focused window. |
| **Keyboard** | `pc.press_key` | `CONFIRM` | `key` (allowlisted name/shortcut) | Presses an allowlisted key or shortcut (e.g. `ENTER`, `ESC`, `CTRL+C`). |
| **Mouse** | `pc.move_mouse` | `CONFIRM` | `x` (int), `y` (int) | Moves cursor with strict bounds checking against screen dimensions. |
| **Mouse** | `pc.click` | `CONFIRM` | `button` ("left"/"right"), `clicks` (1/2), optional `x`, `y` | Clicks at current cursor position or moves to valid `(x, y)` and clicks. |
| **Mouse** | `pc.scroll` | `CONFIRM` | `amount` (int, [-1000..1000]) | Scrolls mouse wheel up (positive) or down (negative) within strict bounds. |

---

### 3. Screenshot Transport & Security

- **Safe In-Memory Capture**: Captured using safe Windows desktop APIs (`user32.OpenInputDesktop`, `SetThreadDesktop`, `PIL.ImageGrab`). No files are saved to disk by default.
- **Bounded Dimensions & Compression**: Image is bounded to `PC_SCREENSHOT_MAX_WIDTH` (1920) and `PC_SCREENSHOT_MAX_HEIGHT` (1080). If the captured frame exceeds limits, high-quality Lanczos downsampling is applied.
- **Strict Byte-Size Limit**: Payload is compressed into PNG (or optimized JPEG if necessary) and encoded as base64 with a hard ceiling of `PC_SCREENSHOT_MAX_BYTES` (1,500,000 bytes). Oversized captures are rejected.
- **Privacy & Transport**:
  - Image payloads are **never logged** to disk or console.
  - Screenshots are not uploaded to external third-party endpoints.
  - Decoupled from specific vision APIs via the [`DesktopCapture`](file:///e:/Bimo/src/bimo/pc/vision.py) abstraction.

---

### 4. Multi-Turn Computer-Use Loop

Bimo supports bounded multi-turn **observe → act → observe** workflows:

```
User: "Open Notepad, type Hello, and check the screen."
 ↓
Bimo Agent (THINKING)
 ↓
[Step 1] pc.open_app ("notepad") -> User Confirmed -> Executed
 ↓
Bimo Agent (THINKING)
 ↓
[Step 2] pc.focus_window ("Notepad") -> User Confirmed -> Executed
 ↓
[Step 3] pc.type_text ("Hello") -> User Confirmed -> Executed
 ↓
Bimo Agent (THINKING)
 ↓
[Step 4] pc.screenshot () -> SAFE -> Executed autonomously
 ↓
Bimo Agent (THINKING) -> Final spoken/conversational response
```

- **Iteration Bounding**: Loop is strictly capped by `MAX_COMPUTER_USE_STEPS` (default 12).
- **Graceful Termination**: When the step limit is reached, execution halts cleanly, emits `PC_COMPUTER_USE_LIMIT_REACHED`, and speaks a safe partial summary.
- **No Self-Confirmation**: The model can never approve its own `CONFIRM` tool calls. Explicit human approval is required.

---

### 5. Telemetry & Observability Events

Phase 8 introduces structured domain events into Bimo's [`EventBus`](file:///e:/Bimo/src/bimo/core/events.py):
- `PC_COMPUTER_USE_STARTED`: Emitted when the first `pc.*` tool call in a conversational turn begins.
- `PC_COMPUTER_USE_STEP`: Emitted on each computer-use action step.
- `PC_SCREENSHOT_CAPTURED`: Emitted on successful screenshot capture with dimensions and format.
- `PC_MOUSE_ACTION`: Emitted on mouse move, click, or scroll events.
- `PC_KEYBOARD_ACTION`: Emitted on text typing or key press events.
- `PC_WINDOW_FOCUSED`: Emitted when a window is focused.
- `PC_COMPUTER_USE_COMPLETED`: Emitted when the computer-use loop concludes successfully.
- `PC_COMPUTER_USE_FAILED`: Emitted when a computer-use tool fails.
- `PC_COMPUTER_USE_LIMIT_REACHED`: Emitted if the iteration limit is reached.

---

### 6. Configuration Reference

Add to your `.env` or robot configuration:

```ini
# Windows PC Agent
PC_AGENT_ENABLED=true
PC_AGENT_HOST="192.168.1.2"        # Target Windows PC IP on LAN
PC_AGENT_PORT=8088
PC_AGENT_SHARED_SECRET="your-secure-hmac-sha256-key"
PC_AGENT_CONNECT_TIMEOUT=3.0
PC_AGENT_REQUEST_TIMEOUT=10.0

# Controlled Computer Use
COMPUTER_USE_ENABLED=true
MAX_COMPUTER_USE_STEPS=12
PC_SCREENSHOT_MAX_WIDTH=1920
PC_SCREENSHOT_MAX_HEIGHT=1080
PC_SCREENSHOT_MAX_BYTES=1500000
PC_MAX_TYPE_TEXT_LENGTH=1000
PC_MAX_SCROLL=1000
```

---

### 7. Running & Verifying Phase 8

**1. Run full automated unit test suite (all 251 tests pass offline):**
```bash
python -m unittest discover -s tests -t . -v
```
*(Includes 43 dedicated Phase 8 unit tests in `tests/test_phase8_computer_use.py` covering screen size, screenshots, coordinate validation, scroll limits, keyboard allowlists, permissions, bounded multi-turn loops, and security boundaries).*

**2. Start Windows PC Agent:**
```bash
python -m windows_agent --host 0.0.0.0 --port 8088 --secret YOUR_SHARED_SECRET
```

**3. Run the live verification script:**
```bash
python test_pc_agent_live.py --host 127.0.0.1 --port 8088 --secret YOUR_SHARED_SECRET --live-apps
```
*(Performs complete live validation: health check, authenticated status, screen size, desktop screenshot capture, window focus, real Notepad launch, text typing, key press, mouse movement, scroll, clean close, and security boundary rejection).*

**4. Real LAN Test (Raspberry Pi ↔ Windows):**
```bash
# On Windows PC (IP e.g. 192.168.1.2):
python -m windows_agent --host 0.0.0.0 --port 8088 --secret YOUR_SHARED_SECRET

# On Raspberry Pi Bimo:
python test_pc_agent_live.py --host 192.168.1.2 --port 8088 --secret YOUR_SHARED_SECRET --live-apps
```

