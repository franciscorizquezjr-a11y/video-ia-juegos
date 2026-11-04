# IA Video Studio: acceso rápido

**Haz doble clic en IA Video Studio, en el escritorio, o en [Abrir app.bat](<Abrir app.bat>).** Usa el entorno `.venv` existente y abre Chrome cuando el servidor está listo. Si ya está abierta, reutiliza la misma app.

- [Documentación actual](docs/INDICE.md).
- [Estructura y mantenimiento](docs/ESTRUCTURA_Y_ARRANQUE.md).
- [Control de publicaciones](CONTROL_PUBLICACIONES.md).

Las secciones siguientes documentan el pipeline original por consola, que sigue disponible. Para el estudio web actual, usa los enlaces anteriores.

---

# AI Video Pipeline

**Automatically transform gameplay/trailer videos into polished, engaging promotional shorts for TikTok, Instagram Reels, and YouTube Shorts.**

A production-quality Python automation system where the LLM (via Gemini) makes **creative decisions only** — Python orchestrates the entire technical workflow.

---

## Architecture Overview

```
INPUT: gameplay.mp4 (2-3 min)
         │
         ▼
  ┌──────────────────┐
  │  Step 1: LLM     │  Gemini API (gemini-3.5-flash)
  │  Video Analysis   │  → Storyboard + Voiceover Script
  └──────────────────┘
         │
         ▼
  ┌──────────────────┐
  │  Step 2: TTS     │  ElevenLabs API
  │  Voice Generation │  → voice.mp3
  └──────────────────┘
         │
         ▼
  ┌──────────────────┐
  │  Step 3: Measure │  FFprobe
  │  Audio Duration   │  → exact seconds
  └──────────────────┘
         │
         ▼
  ┌──────────────────┐
  │  Step 4: LLM     │  Gemini API
  │  Edit Breakdown   │  → Structured JSON with timestamps
  └──────────────────┘
         │
         ▼
  ┌──────────────────┐
  │  Steps 5-8:      │  FFmpeg (subprocess)
  │  Cut, Concat,    │  → Cut clips → Join → Merge audio
  │  Merge, Export   │  → output/final.mp4
  └──────────────────┘
         │
         ▼
OUTPUT: output/final.mp4 (25-30 sec)
```

---

## Quick Start

### 1. Prerequisites

- **Python 3.13+**
- **FFmpeg** installed and on PATH
  ```bash
  # Windows
  winget install FFmpeg
  
  # macOS
  brew install ffmpeg
  
  # Linux
  sudo apt install ffmpeg
  ```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure API Keys

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your API keys:
#   GEMINI_API_KEY=your_key_here
#   ELEVENLABS_API_KEY=your_key_here
```

### 4. Run the Pipeline

```bash
# Place your video in the input/ directory, then:
python main.py --input input/gameplay.mp4

# Dry run (validate without processing):
python main.py --input input/gameplay.mp4 --dry-run

# Custom output name:
python main.py --input input/gameplay.mp4 --output highlights.mp4

# Debug mode:
python main.py --input input/gameplay.mp4 --log-level DEBUG

# List available ElevenLabs voices:
python main.py --list-voices
```

---

## Project Structure

```
AI Video Pipeline/
├── main.py                # CLI entry point
├── pipeline.py            # Pipeline orchestrator
├── llm.py                 # Gemini LLM abstraction (ONLY Gemini-touching module)
├── voice.py               # ElevenLabs TTS (ONLY ElevenLabs-touching module)
├── ffmpeg_wrapper.py      # FFmpeg subprocess wrapper (ONLY FFmpeg-touching module)
├── config.py              # Configuration loader (.env → dataclass)
├── schemas.py             # JSON schemas & data contracts
├── utils.py               # Shared utilities & exception hierarchy
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable template
├── .env                   # Your API keys (gitignored)
├── .gitignore             # Git ignore rules
├── README.md              # This file
├── input/                 # Source video files
├── output/                # Final exported videos
├── temp/                  # Intermediate files (auto-cleaned)
├── logs/                  # Timestamped log files
└── prompts/               # LLM prompt templates
    ├── analyze_video.txt  # Step 1: creative analysis prompt
    └── edit_breakdown.txt # Step 4: editing breakdown prompt
```

---

## Module Responsibilities

| Module | Responsibility | External Dependency |
|--------|---------------|---------------------|
| `main.py` | CLI parsing, config loading, error handling | None |
| `pipeline.py` | Orchestrates the 8-step workflow | None (delegates) |
| `llm.py` | All LLM communication via Gemini | Google GenAI SDK |
| `voice.py` | All TTS generation via ElevenLabs | ElevenLabs SDK |
| `ffmpeg_wrapper.py` | All video/audio processing | FFmpeg (subprocess) |
| `config.py` | Configuration from .env | python-dotenv |
| `schemas.py` | Data contracts & JSON validation | None |
| `utils.py` | Logging, exceptions, helpers | None |

**Key design principle:** Each external dependency (Gemini, ElevenLabs, FFmpeg) is isolated in exactly ONE module. Replacing a provider requires changing only that module.

---

## JSON Data Flow

The pipeline uses JSON as the communication format between modules:

### Step 1 Output: Storyboard

```json
{
  "title": "Epic Sniper Compilation",
  "hook": "Open with the impossible triple kill",
  "narrative_flow": "mystery → action → climax → payoff",
  "emotional_progression": ["curiosity", "tension", "awe", "satisfaction"],
  "pacing": "fast-cuts with one slow-motion beat",
  "scenes": [
    {
      "order": 1,
      "description": "Triple kill with sniper from rooftop",
      "purpose": "hook",
      "visual_notes": "Slow-mo on the final headshot",
      "duration_suggestion": "short"
    }
  ],
  "voiceover_script": "You won't believe this shot..."
}
```

### Step 4 Output: Edit Breakdown

```json
{
  "total_duration": 25.5,
  "clips": [
    {
      "order": 1,
      "start": 45.2,
      "end": 49.8,
      "purpose": "hook",
      "description": "Triple kill sniper sequence",
      "transition": "cut",
      "speed": 1.0
    }
  ],
  "audio_sync_notes": "Clip 3 aligns with voiceover climax at 15s"
}
```

---

## Configuration

All settings are loaded from `.env` via `config.py`. See `.env.example` for all available options.

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Gemini API key |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Gemini model ID |
| `ELEVENLABS_API_KEY` | *(required)* | ElevenLabs API key |
| `ELEVENLABS_VOICE_ID` | `JBFqnCBsd6RMkjVDRZzb` | Voice for TTS |
| `ELEVENLABS_MODEL` | `eleven_multilingual_v2` | TTS model |
| `TARGET_DURATION` | `30.0` | Target video duration (seconds) |
| `AUDIO_TAIL_PADDING` | `0.5` | Visual-only time after voiceover |
| `MAX_RETRIES` | `3` | API retry attempts |
| `VIDEO_CRF` | `18` | Video quality (0-51, lower=better) |
| `OUTPUT_WIDTH` | `1080` | Output width (pixels) |
| `OUTPUT_HEIGHT` | `1920` | Output height (pixels) |

---

## Error Handling

The pipeline uses a custom exception hierarchy:

```
PipelineError (base)
├── LLMError          — Gemini API failures
├── VoiceGenerationError — ElevenLabs failures
├── FFmpegError       — FFmpeg/FFprobe failures
├── ValidationError   — JSON schema / timing issues
└── ConfigError       — Missing .env / API keys
```

All API calls include exponential backoff retry logic. Temp files are cleaned up even on failure.

---

## Future Extensibility

The modular architecture is designed for easy extension:

- **New LLM providers:** Change only `llm.py`
- **New TTS providers:** Change only `voice.py`
- **Subtitles/captions:** Add a new module, call from `pipeline.py`
- **Batch processing:** Loop over `Pipeline.run()` with different inputs
- **REST API:** Wrap `Pipeline` in a FastAPI endpoint
- **GUI:** Use `Pipeline` as the backend for a desktop app
- **Multiple voices:** Extend `voice.py` with voice selection logic

---

## License

Private project — all rights reserved.
