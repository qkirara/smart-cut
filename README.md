# Smart Cut

AI-powered smart video editing for teaching recordings. Automatically detects and removes silence, stutters, mistakes, filler words, and repeated sentences.

## Features

- **Multi-layer detection**: silence intervals, repeated sentences, filler words, stutters, false starts, orphan fragments
- **Word-level precision**: uses faster-whisper word timestamps for accurate boundary detection
- **Intra-segment restart detection**: catches phrase restarts within a single Whisper segment (e.g., "let me introduce, let me introduce the whole...")
- **Optional LLM review**: adds semantic-level review for self-repeats and orphan fragments
- **Frame-accurate editing**: two-pass FFmpeg cutting with keyframe alignment
- **EDL export**: compatible with Premiere Pro, DaVinci Resolve, Final Cut
- **PRPROJ export**: generate Premiere Pro project files via `export_prproj.py`

## Requirements

- Python 3.8+
- FFmpeg (must be in PATH)

## Installation

```bash
# Core dependencies
pip install faster-whisper torch

# Optional: for LLM review
pip install anthropic    # for Claude
pip install openai       # for OpenAI-compatible APIs
```

## Usage

### One-shot mode (analyze + cut)

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4
```

### Step by step

```bash
# Step 1: Analyze
python smart_cut.py analyze input.mp4 --output-dir ./output

# Step 2: Review analysis.json, then execute
python smart_cut.py cut input.mp4 --cut-list ./output/analysis.json --output cleaned.mp4
```

### With LLM review

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --llm-review \
  --llm-model gpt-4o-mini \
  --api-provider openai_compatible \
  --api-key YOUR_KEY \
  --base-url https://api.openai.com/v1
```

### With Qwen3-ASR for reference transcript (optional)

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --use-qwen3 --qwen3-model-path /path/to/Qwen3-ASR-1.7B
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--whisper-model` | small | faster-whisper model size (base/small/medium/large) |
| `--silence-threshold` | -35dB | Noise threshold for silence detection |
| `--min-silence` | 0.6s | Minimum silence duration to detect |
| `--padding` | 0.08s | Padding around kept segments |
| `--repeat-threshold` | 0.6 | Text similarity threshold for repeat detection |
| `--llm-review` | false | Enable LLM narrative review |
| `--llm-model` | gpt-4o-mini | LLM model for review |
| `--api-provider` | openai_compatible | API provider (anthropic/openai_compatible/auto) |
| `--language` | zh | Language code for transcription |

## How It Works

```
Input Video
  -> FFmpeg: extract audio (16kHz WAV)
  -> FFmpeg silencedetect: find silence intervals
  -> faster-whisper: transcribe with word timestamps
  -> trim_fillers_from_segments: remove filler words at boundaries
  -> split_segments_by_clauses: split long segments at clause boundaries
  -> detect_intra_restarts: word-level restart detection
  -> build_utterances: merge silence + ASR into unified timeline
  -> detect_repeats: sentence-level repeat detection
  -> detect_orphan_fragments: substring + fuzzy matching
  -> detect_fillers: pure filler / high filler ratio segments
  -> detect_stutters: character-level stutters (4+ repeats)
  -> detect_false_starts: short false starts before continuations
  -> [optional] review_cuts_with_llm: semantic-level review
  -> generate_cut_list -> FFmpeg: execute cuts -> concat
  -> Output clean video (H.264/AAC MP4)
```

## Detection Algorithms

### Repeat Detection
SequenceMatcher similarity comparison. Short segments (<=25 chars) use a reduced threshold (0.52). Segments with suspiciously low text-to-duration ratio (likely Whisper transcription failures) are skipped.

### Orphan Fragment Detection
Two-phase matching:
1. **Exact substring** (any length): segment text appears in a nearby kept segment
2. **Fuzzy substring** (<=4 chars only): sliding window SequenceMatcher >= 0.6

### Intra-Segment Restart Detection
Word-level detection with three strategies:
1. **Gap-based**: finds word gaps > 0.6s with similar content before/after
2. **N-word repeat**: detects repeated word sequences within a segment
3. **Long word absorption**: Whisper sometimes absorbs repeated phrases into a single long word (>5s for <=3 chars)

## Input/Output

| Input | Notes |
|-------|-------|
| .webm | OpenScreen recordings (AV1+Opus) |
| .mp4 | Native support |
| .mkv/.mov | Native support |

Output: always H.264/AAC MP4 with EDL file.

## License

MIT
