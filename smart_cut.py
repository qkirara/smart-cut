#!/usr/bin/env python3
"""
smart_cut.py - AI-powered smart video editing for teaching recordings

Pipeline (v2):
  1. Extract audio (FFmpeg, 16kHz WAV)
  2. Detect silence (FFmpeg silencedetect)
  3. Transcribe with word timestamps (faster-whisper primary, Qwen3-ASR optional)
  4. Merge silence + ASR segments into fine-grained timeline
  5. Detect: repeated sentences, filler words, stutters
  6. Output analysis JSON with cut recommendations
  7. Execute cuts (two-pass FFmpeg) and concatenate

Usage:
  python smart_cut.py analyze input.webm --output-dir ./output
  python smart_cut.py cut input.webm --cut-list ./output/analysis.json --output ./output/cleaned.mp4
  python smart_cut.py auto input.webm --output ./output/cleaned.mp4
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Silence:
    start: float
    end: float
    duration: float


@dataclass
class Utterance:
    index: int
    start: float
    end: float
    duration: float
    text: str
    flag: str = ""  # "silence" | "repeat" | "filler" | "stutter" | "" (keep)
    reason: str = ""


def run_ffmpeg(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-y"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        print(f"FFmpeg error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result


def run_ffprobe(input_file: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", input_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def get_video_info(input_file: str) -> dict:
    probe = run_ffprobe(input_file)
    video = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    return {
        "duration": float(probe["format"]["duration"]),
        "width": video["width"] if video else 0,
        "height": video["height"] if video else 0,
        "fps": eval(video["r_frame_rate"]) if video and "r_frame_rate" in video else 30.0,
        "vcodec": video["codec_name"] if video else "",
        "acodec": audio["codec_name"] if audio else "",
    }


def extract_audio(video_path: str, output_path: str, sample_rate: int = 16000) -> str:
    run_ffmpeg([
        "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1",
        output_path
    ])
    return output_path


def detect_silences(audio_path: str, threshold_db: float = -35, min_duration: float = 0.6) -> List[Silence]:
    result = run_ffmpeg([
        "-i", audio_path,
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-"
    ], check=False)

    stderr = result.stderr
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", stderr)]

    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else s + min_duration
        silences.append(Silence(start=s, end=e, duration=e - s))
    return silences


def transcribe_whisper(
    audio_path: str,
    language: str = "zh",
    model_size: str = "base",
) -> List[dict]:
    """Transcribe with faster-whisper, returns segments with word-level timestamps."""
    from faster_whisper import WhisperModel

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    print(f"Loading faster-whisper/{model_size} ({device})...")
    model = WhisperModel(
        f"guillaumekln/faster-whisper-{model_size}",
        device=device, compute_type=compute_type,
    )

    segments_iter, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )

    result = []
    for seg in segments_iter:
        words = []
        if seg.words:
            for w in seg.words:
                words.append({"word": w.word, "start": w.start, "end": w.end, "probability": w.probability})
        result.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
            "words": words,
        })
    return result


FILLER_WORDS = set("嗯啊呃嘿哈呀哦诶唔咄呸呵哼啦嘛呢哇唔额呃哼嘞呗哎啵噢噢额欸诶嘿唔嘿") \
    | {w for w in ["这个", "那个", "就是", "然后", "所以", "的话", "对对", "嗯对", "对吧", "好的", "嗯嗯",
              "其实", "怎么说呢", "这样子", "那什么", "反正", "不过", "另外"]}


def trim_fillers_from_segments(whisper_segments: List[dict], min_filler_run: float = 0.3) -> List[dict]:
    """Use word timestamps to trim leading/trailing filler words from segments."""
    trimmed = []
    for seg in whisper_segments:
        words = seg.get("words", [])
        if not words:
            trimmed.append(seg)
            continue

        # Find first non-filler word and last non-filler word
        first_content = 0
        last_content = len(words) - 1
        for i, w in enumerate(words):
            w_clean = w["word"].strip("，。！？、；：,.!?;: ")
            if w_clean not in FILLER_WORDS:
                first_content = i
                break
        for i in range(len(words) - 1, -1, -1):
            w_clean = words[i]["word"].strip("，。！？、；：,.!?;: ")
            if w_clean not in FILLER_WORDS:
                last_content = i
                break

        # Trim segment boundaries
        new_start = words[first_content]["start"] - 0.05  # small pad
        new_end = words[last_content]["end"] + 0.05
        new_start = max(seg["start"], new_start)
        new_end = min(seg["end"], new_end)

        if new_end - new_start < 0.3:  # segment is all filler
            trimmed.append({**seg, "text": "", "_skip": True})
        else:
            # Rebuild text from content words only
            content_words = [w["word"] for w in words[first_content:last_content + 1]]
            trimmed.append({
                "start": new_start,
                "end": new_end,
                "text": "".join(content_words).strip(),
                "words": words,
            })

    return [s for s in trimmed if not s.get("_skip")]


def split_segments_by_clauses(segments: List[dict], max_clause_len: float = 8.0) -> List[dict]:
    """Split long whisper segments into clauses at punctuation/pause boundaries."""
    SPLIT_CHARS = set("，。！？、；：,.!?;：")
    result = []
    for seg in segments:
        words = seg.get("words", [])
        if not words or (seg["end"] - seg["start"]) <= max_clause_len:
            result.append(seg)
            continue
        clauses = []
        current_words = []
        for w in words:
            current_words.append(w)
            w_last = w["word"][-1] if w["word"] else ""
            if w_last in SPLIT_CHARS or (current_words and current_words[-1]["end"] - current_words[0]["start"] >= max_clause_len):
                if current_words:
                    clauses.append(current_words)
                    current_words = []
        if current_words:
            clauses.append(current_words)
        for clause_words in clauses:
            result.append({
                "start": clause_words[0]["start"],
                "end": clause_words[-1]["end"],
                "text": "".join(w["word"] for w in clause_words).strip(),
                "words": clause_words,
            })
    return result


def detect_intra_restarts(whisper_segments: List[dict], gap_threshold: float = 0.6) -> List[dict]:
    """Detect phrase restarts within segments using word-level timestamps.

    Finds cases where speaker starts a phrase, pauses, then restarts with similar words.
    Keeps the LATER (more complete) version by trimming segment start.
    """
    result = []
    for seg in whisper_segments:
        words = seg.get("words", [])
        if not words or len(words) < 4:
            result.append(seg)
            continue

        # Strategy 1: Find gaps > gap_threshold between consecutive words,
        # then check if words before gap are similar to words after gap (restart)
        best_restart_idx = None
        for i in range(1, len(words)):
            gap = words[i]["start"] - words[i - 1]["end"]
            if gap > gap_threshold:
                # Compare last few words before gap with first few after gap
                n = min(4, i, len(words) - i)
                before = "".join(w["word"] for w in words[i - n:i])
                after = "".join(w["word"] for w in words[i:i + n])
                sim = text_similarity(before, after)
                if sim > 0.4:
                    # Restart detected — keep from after the gap
                    best_restart_idx = i

        # Strategy 3: Check if any N-word sequence appears again later in the segment.
        # Catches phrase restarts like "向大家介绍一下，向大家介绍一下整个..."
        # where the gap between repeats is short (< 0.6s) so Strategy 1 misses it.
        # Also checks from offset positions (not just start) to handle cases like
        # "的是向大家介绍一下整个，向大家介绍一下整个算类..."
        if best_restart_idx is None:
            _punct = "，。！？、；：,.!?;: "
            found = False
            for n in range(4, min(8, len(words) // 2 + 1)):
                for start_pos in range(min(4, len(words) - n * 2)):
                    seq = "".join(w["word"].strip(_punct) for w in words[start_pos:start_pos + n])
                    if len(seq) < 4:
                        continue
                    for j in range(start_pos + n, len(words) - n + 1):
                        candidate = "".join(w["word"].strip(_punct) for w in words[j:j + n])
                        if seq == candidate:
                            remaining_words = words[j:]
                            remaining_dur = remaining_words[-1]["end"] - remaining_words[0]["start"]
                            if remaining_dur >= 0.5:
                                best_restart_idx = j
                            found = True
                            break
                    if found:
                        break
                if found:
                    break

        # Strategy 2: Find extremely long words (whisper absorbed a phrase repeat).
        # Two modes:
        #   - First 5 words, >1.5s: midpoint split (conservative, original behavior)
        #   - Any word, >5s: the word absorbed a full repeat; truncate or trim
        split_long_word = False
        long_word_idx = None
        extreme_long_word = False
        if best_restart_idx is None:
            for i, w in enumerate(words):
                wlen = len(w["word"].strip("，。！？、；：,.!?;: "))
                wdur = w["end"] - w["start"]
                # Extreme: any word >5s for <=3 chars -> absorbed repeat
                if wlen <= 3 and wdur > 5.0:
                    long_word_idx = i
                    extreme_long_word = True
                    split_long_word = True
                    break
                # Moderate: first 5 words >1.5s -> possible restart
                if i < 5 and wlen <= 3 and wdur > 1.5:
                    long_word_idx = i
                    split_long_word = True
                    break

        if best_restart_idx is not None and best_restart_idx > 0:
            remaining_words = words[best_restart_idx:]
            remaining_dur = remaining_words[-1]["end"] - remaining_words[0]["start"]
            if remaining_dur >= 0.5:
                new_start = remaining_words[0]["start"]
                result.append({
                    "start": new_start,
                    "end": seg["end"],
                    "text": "".join(w["word"] for w in remaining_words).strip(),
                    "words": remaining_words,
                })
                continue

        if split_long_word and long_word_idx is not None:
            w = words[long_word_idx]
            if extreme_long_word:
                # Extreme case: word absorbed a full phrase repeat.
                remaining = words[long_word_idx + 1:]
                if remaining:
                    # Keep text AFTER the long word (the continuation)
                    result.append({
                        "start": remaining[0]["start"],
                        "end": seg["end"],
                        "text": "".join(ww["word"] for ww in remaining).strip(),
                        "words": remaining,
                    })
                else:
                    # Long word is last - truncate to expected duration
                    wlen = len(w["word"].strip("，。！？、；：,.!?;: "))
                    truncated_end = w["start"] + 0.4 + wlen * 0.15
                    if truncated_end > seg["start"] + 0.5:
                        result.append({
                            "start": seg["start"],
                            "end": truncated_end,
                            "text": seg["text"],
                            "words": words,
                        })
                continue
            else:
                # Moderate case: midpoint split (original behavior)
                mid = (w["start"] + w["end"]) / 2
                result.append({
                    "start": mid,
                    "end": seg["end"],
                    "text": w["word"] + "".join(ww["word"] for ww in words[long_word_idx + 1:]).strip(),
                    "words": words[long_word_idx:],
                })
                continue

        result.append(seg)
    return result


def transcribe_qwen3(model, audio_path: str, language: str = "zh", context: str = "") -> str:
    """Transcribe with Qwen3-ASR (text only, no timestamps)."""
    LANG_MAP = {"zh": "Chinese", "en": "English", "yue": "Cantonese", "ja": "Japanese", "ko": "Korean"}
    lang = LANG_MAP.get(language, language)
    results = model.transcribe(
        audio_path,
        language=lang if lang else None,
        context=context,
    )
    return "".join(r.text.strip() for r in results if r.text.strip())


def build_utterances(
    duration: float,
    silences: List[Silence],
    whisper_segments: List[dict],
) -> List[Utterance]:
    """Merge silence intervals and ASR segments into a unified timeline of utterances."""

    # Create events: silence ranges and speech ranges
    events = []

    # Silence events
    for sil in silences:
        events.append(("silence", sil.start, sil.end, sil.duration, "静音段"))

    # Whisper speech segments
    for i, seg in enumerate(whisper_segments):
        events.append(("speech", seg["start"], seg["end"], seg["end"] - seg["start"], seg["text"]))

    # Sort by start time
    events.sort(key=lambda e: e[1])

    # Resolve overlaps: silence takes priority within its range
    resolved = []
    for etype, start, end, dur, text in events:
        if etype == "silence":
            resolved.append(Utterance(
                index=len(resolved), start=start, end=end, duration=dur,
                text="", flag="silence", reason="静音段",
            ))
        else:
            # Clip speech to not overlap with silences
            actual_start = start
            actual_end = end
            for sil in silences:
                if sil.start <= start < sil.end:
                    actual_start = sil.end
                elif sil.start < end <= sil.end:
                    actual_end = sil.start
                elif start < sil.start and end > sil.end:
                    actual_end = sil.start  # truncate at silence start

            if actual_end > actual_start + 0.05:
                resolved.append(Utterance(
                    index=len(resolved), start=actual_start, end=actual_end,
                    duration=actual_end - actual_start, text=text,
                ))

    # Sort by start time and re-index
    resolved.sort(key=lambda u: u.start)
    for i, u in enumerate(resolved):
        u.index = i

    return resolved


def text_similarity(a: str, b: str) -> float:
    """Compute text similarity between two strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip(), b.strip()).ratio()


def detect_repeats(utterances: List[Utterance], window: int = 10, threshold: float = 0.45) -> List[Utterance]:
    """Detect repeated sentences. Keep the longer/more complete version."""

    speech_only = [u for u in utterances if not u.flag and u.text]

    for i in range(len(speech_only)):
        curr = speech_only[i]
        if curr.flag:
            continue
        for j in range(i):
            prev = speech_only[j]
            if prev.flag or not prev.text:
                continue
            # Skip if either segment has suspiciously little text for its duration
            # (likely a Whisper transcription failure, e.g. 16s audio -> 2 chars)
            if curr.duration > 5.0 and len(curr.text.strip()) <= 3:
                continue
            if prev.duration > 5.0 and len(prev.text.strip()) <= 3:
                continue
            sim = text_similarity(curr.text, prev.text)
            # Use lower threshold for shorter segments - short repeats
            # rarely hit high similarity due to phrasing differences
            min_len = min(len(curr.text), len(prev.text))
            effective_threshold = threshold
            if min_len <= 25:
                effective_threshold = min(threshold, 0.52)
            if sim >= effective_threshold:
                if len(curr.text) >= len(prev.text):
                    prev.flag = "repeat"
                    prev.reason = f"重复 (#{curr.index}是更完整版本, 相似度 {sim:.0%}): \"{prev.text[:30]}\""
                else:
                    curr.flag = "repeat"
                    curr.reason = f"重复 (与 #{prev.index} 相似度 {sim:.0%}): \"{curr.text[:30]}\""
                break

    return utterances


def _is_orphan(text: str, candidate: str) -> bool:
    """Check if short text is essentially a repeat of content in candidate.

    Two strategies:
    1. Exact substring match: "一季度" in "...一季度..."
    2. Fuzzy substring: "GC课" vs "GC的第一课" (high similarity with a substring)
    """
    if text in candidate:
        return True
    # Fuzzy: slide a window over candidate substrings of similar length to text
    tlen = len(text)
    if tlen < 2 or not candidate:
        return False
    for start in range(len(candidate)):
        for end in range(start + tlen, min(start + tlen + 3, len(candidate) + 1)):
            sub = candidate[start:end]
            sim = SequenceMatcher(None, text, sub).ratio()
            if sim >= 0.6:
                return True
    return False


def detect_orphan_fragments(utterances: List[Utterance], max_chars: int = 4, window: int = 5) -> List[Utterance]:
    """Flag segments whose text is contained in a nearby kept segment.

    Two modes:
    - Exact substring: any length (e.g. "欢迎来到新的一年" inside longer sentence)
    - Fuzzy substring: only for very short text (<=max_chars), via _is_orphan
    """
    kept = [(i, u) for i, u in enumerate(utterances) if not u.flag and u.text.strip()]
    for idx, (pos, u) in enumerate(kept):
        text = u.text.strip().strip("，。！？、；：,.!?;: ")
        if not text:
            continue
        for adj_idx in range(max(0, idx - window), min(len(kept), idx + window + 1)):
            if adj_idx == idx:
                continue
            adj_pos, adj_u = kept[adj_idx]
            # Exact substring match - works for any length
            if text in adj_u.text:
                u.flag = "repeat"
                u.reason = f"重复 (内容已包含在 #{adj_u.index}): \"{u.text}\""
                break
            # Fuzzy match - only for very short text
            if len(text) <= max_chars and _is_orphan(text, adj_u.text):
                u.flag = "repeat"
                u.reason = f"孤立短片段 (内容已包含在 #{adj_u.index}): \"{u.text}\""
                break
    return utterances


def detect_fillers(utterances: List[Utterance]) -> List[Utterance]:
    """Detect segments that are mostly filler words."""

    filler_pattern = re.compile(r"[嗯啊哦呃那个就是然后所以的话一个这个呃嗯啊哦]+")
    short_filler = re.compile(r"^[嗯啊哦呃嘿哈\s，。、！？,.!?]+$")

    for u in utterances:
        if u.flag or not u.text:
            continue

        # Pure filler segment (only filler characters)
        if short_filler.match(u.text) and len(u.text) <= 10:
            u.flag = "filler"
            u.reason = f"纯填充词: \"{u.text}\""
            continue

        # Heavy filler ratio in short segments
        fillers = filler_pattern.findall(u.text)
        filler_len = sum(len(f) for f in fillers)
        if u.duration < 3.0 and filler_len / max(len(u.text), 1) > 0.5:
            u.flag = "filler"
            u.reason = f"填充词过多 ({filler_len}/{len(u.text)}): \"{u.text[:30]}\""

    return utterances


def detect_stutters(utterances: List[Utterance]) -> List[Utterance]:
    """Detect stutter patterns in speech (very conservative - only clear stutters)."""

    for u in utterances:
        if u.flag or not u.text:
            continue

        # Only flag very obvious stutters: 4+ repeated single characters
        # e.g., "我我我我" but NOT "培训" (normal word)
        text_clean = re.sub(r"[a-zA-Z\s,.!?;:，。！？、；：]+", "", u.text)
        if re.search(r"(.)\1{3,}", text_clean):
            u.flag = "stutter"
            u.reason = f"结巴: \"{u.text[:30]}\""

    return utterances


def detect_false_starts(utterances: List[Utterance]) -> List[Utterance]:
    """Detect false starts: speaker begins, stops, then restarts with similar content."""

    speech_only = [u for u in utterances if not u.flag and u.text and u.duration < 8.0]

    for i in range(1, len(speech_only)):
        curr = speech_only[i]
        prev = speech_only[i - 1]
        if curr.flag or prev.flag:
            continue

        # If previous segment is short and current segment starts similarly
        if prev.duration < 4.0 and curr.duration > prev.duration * 1.5:
            sim = text_similarity(prev.text[:10], curr.text[:10])
            if sim >= 0.5:
                prev.flag = "false_start"
                prev.reason = f"假启动 (后续继续): \"{prev.text[:30]}\""

    return utterances


def _parse_line_format(text: str) -> list:
    """Parse '编号|原因' line format from LLM response (works with thinking models)."""
    cuts = []
    valid_indices = set()
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line == "无":
            continue
        # Match "6|原因" or "6 | 原因"
        m = re.match(r'^(\d+)\s*[|｜]\s*(.+)$', line)
        if m:
            try:
                idx = int(m.group(1))
                reason = m.group(2).strip()
                if idx not in valid_indices:
                    valid_indices.add(idx)
                    cuts.append({"index": idx, "reason": reason})
            except ValueError:
                continue
    return cuts


def _extract_json_from_llm_response(text: str):
    """Extract JSON object from LLM response, handling markdown fences, thinking text, and extra text."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try markdown code fence
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass
    # Search for {"cuts": ...} pattern specifically (our expected format)
    # Use findall and take the last match (most likely the final answer in thinking models)
    cuts_matches = re.findall(r'\{\s*"cuts"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL)
    for match in reversed(cuts_matches):
        try:
            result = json.loads(match)
            if "cuts" in result:
                return result
        except json.JSONDecodeError:
            continue
    # Fallback: find any top-level JSON object with "cuts" key
    for match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL):
        try:
            result = json.loads(match.group(0))
            if "cuts" in result:
                return result
        except json.JSONDecodeError:
            continue
    return None


def _call_anthropic(prompt: str, model: str, api_key: str = None) -> str:
    import anthropic
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=key)
    message = client.messages.create(
        model=model, max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in message.content:
        if hasattr(block, "text"):
            return block.text
    raise RuntimeError("No text in Anthropic response")


def _call_openai_compatible(prompt: str, model: str, api_key: str = None, base_url: str = None) -> str:
    import openai
    key = api_key or os.environ.get("OPENAI_API_KEY")
    url = base_url or os.environ.get("OPENAI_BASE_URL")
    kwargs = {"api_key": key}
    if url:
        kwargs["base_url"] = url
    client = openai.OpenAI(**kwargs)
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=16384,
    )
    msg = response.choices[0].message
    # Some models put content in reasoning field when content is empty
    text = msg.content or ""
    if not text:
        reasoning = getattr(msg, "reasoning_content", None) or (msg.model_extra or {}).get("reasoning", "")
        if reasoning:
            text = reasoning
    return text


LLM_REVIEW_PROMPT = """\
这是教学录制的分段文本（已由规则引擎处理过重复和静音）。
请仅审查以下类型的段落，标记应删除的编号：

1. 填充感叹词（如"我的天"、"哎"等无实际内容的感叹）
2. 句内自我重复（如"这个培训其实也是今天我来做这个培训其实一个"）
3. 真正的孤立碎片：≤3个字的片段，且其内容已完整包含在相邻段落中

重要限制：
- 有实际教学内容的段落必须保留，即使看起来像句子片段
- 相邻短片段拼起来能构成完整句子的，全部保留
- 不确定的一律保留
- 宁可少删不要多删

分段（上下文连续）：
{segments}

列出要删除的段落编号，每行一个。格式：编号|原因
如果没有要删除的，只输出：无
只输出结果，不要分析。\
"""


def review_cuts_with_llm(
    utterances: List[Utterance],
    llm_model: str = "gpt-4o-mini",
    api_provider: str = "auto",
    api_key: str = None,
    base_url: str = None,
) -> List[Utterance]:
    """Use LLM to review kept segments and flag additional cuts."""
    kept = [(i, u) for i, u in enumerate(utterances) if not u.flag and u.text.strip()]
    if not kept:
        print("  No kept segments to review.")
        return utterances

    lines = [f"[{u.index}] {u.text.strip()}" for _, u in kept]
    prompt = LLM_REVIEW_PROMPT.format(segments="\n".join(lines))

    try:
        if api_provider == "openai_compatible":
            result = _call_openai_compatible(prompt, llm_model, api_key, base_url)
        else:
            result = _call_anthropic(prompt, llm_model, api_key)
    except Exception as e:
        print(f"  LLM review failed: {e}")
        print("  Continuing without LLM review.")
        return utterances

    parsed = _extract_json_from_llm_response(result)
    if not parsed or "cuts" not in parsed:
        # Fallback: try to parse "编号|原因" line format (works better with thinking models)
        line_cuts = _parse_line_format(result)
        if line_cuts:
            parsed = {"cuts": line_cuts}
    # Handle "无" response (LLM found nothing to cut)
    if not parsed or "cuts" not in parsed:
        clean = result.strip()
        if clean and clean in ("无", "没有", "无\n", "没有要删除的"):
            print("  LLM found no additional cuts.")
            return utterances
        print("  LLM response could not be parsed. Skipping.")
        return utterances

    index_map = {u.index: u for _, u in kept}
    applied = 0
    for cut in parsed["cuts"]:
        idx = cut.get("index")
        reason = cut.get("reason", "LLM flagged")
        if idx in index_map and not index_map[idx].flag:
            index_map[idx].flag = "llm_review"
            index_map[idx].reason = f"[LLM] {reason}"
            applied += 1

    print(f"  LLM flagged {applied} additional segment(s).")
    return utterances


def generate_cut_list(
    utterances: List[Utterance],
    padding: float = 0.08,
) -> dict:
    """Generate cut decision list from flagged utterances."""

    cuts = []
    kept_ranges = []

    for u in utterances:
        if u.flag:
            cuts.append({
                "action": "cut",
                "type": u.flag,
                "start": u.start,
                "end": u.end,
                "duration": u.duration,
                "text": u.text[:50],
                "reason": u.reason,
            })
        else:
            kept_ranges.append({
                "start": max(0, u.start - padding),
                "end": u.end + padding,
                "text": u.text[:50] if u.text else "",
            })

    # Merge overlapping kept ranges
    merged = []
    for r in kept_ranges:
        if merged and r["start"] <= merged[-1]["end"] + 0.1:
            merged[-1]["end"] = max(merged[-1]["end"], r["end"])
            if r["text"]:
                merged[-1]["text"] += r["text"]
        else:
            merged.append(r.copy())

    total_kept = sum(r["end"] - r["start"] for r in merged)

    return {
        "cuts": cuts,
        "kept_ranges": merged,
        "total_kept_duration": total_kept,
    }


def export_edl(
    input_file: str,
    kept_ranges: List[dict],
    output_path: str,
    fps: float = 30.0,
    title: str = "Smart Cut",
):
    """Export EDL (Edit Decision List) for Premiere Pro / DaVinci Resolve / Final Cut."""

    def to_timecode(seconds: float, fps: float = 30.0) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        f = int((seconds % 1) * fps)
        return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

    lines = [f"TITLE: {title}"]

    rec_out = 0.0  # running time on timeline
    for i, r in enumerate(kept_ranges):
        src_in = r["start"]
        src_out = r["end"]
        duration = src_out - src_in
        rec_in = rec_out
        rec_out = rec_in + duration

        lines.append(
            f"{i+1:03d}  AX  V  C  "
            f"{to_timecode(src_in, fps)} {to_timecode(src_out, fps)}  "
            f"{to_timecode(rec_in, fps)} {to_timecode(rec_out, fps)}"
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return output_path


def execute_cuts(
    input_file: str,
    kept_ranges: List[dict],
    output_file: str,
    temp_dir: str,
) -> str:
    """Cut and concatenate kept segments using two-pass FFmpeg."""

    clip_files = []

    for i, r in enumerate(kept_ranges):
        clip_path = os.path.join(temp_dir, f"clip_{i:03d}.mp4")
        duration = r["end"] - r["start"]

        # Two-pass: seek before start for frame accuracy
        seek_back = min(2.0, r["start"])
        args = [
            "-ss", str(r["start"] - seek_back),
            "-i", input_file,
            "-ss", str(seek_back),
            "-t", str(duration),
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            clip_path,
        ]
        run_ffmpeg(args)
        clip_files.append(clip_path)

    if len(clip_files) == 1:
        import shutil
        shutil.copy2(clip_files[0], output_file)
        return output_file

    # Concatenate
    concat_list = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for clip in clip_files:
            f.write(f"file '{os.path.abspath(clip)}'\n")

    run_ffmpeg([
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        "-movflags", "+faststart",
        output_file,
    ])

    return output_file


def analyze(
    input_file: str,
    output_dir: str,
    whisper_model: str = "base",
    silence_threshold: float = -35,
    min_silence: float = 0.6,
    language: str = "zh",
    repeat_threshold: float = 0.6,
    padding: float = 0.08,
    use_qwen3: bool = False,
    qwen3_model_path: str = "",
    llm_review: bool = False,
    llm_model: str = "gpt-4o-mini",
    api_provider: str = "auto",
    api_key: str = None,
    base_url: str = None,
) -> str:
    """Full analysis pipeline. Returns path to analysis.json."""

    input_file = os.path.abspath(input_file)
    os.makedirs(output_dir, exist_ok=True)

    info = get_video_info(input_file)
    print(f"Video: {info['width']}x{info['height']} {info['duration']:.1f}s ({info['vcodec']})")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Step 1: Extract audio
        audio_path = os.path.join(tmpdir, "audio.wav")
        print("Extracting audio...")
        extract_audio(input_file, audio_path)

        # Step 2: Detect silences
        print("Detecting silences...")
        silences = detect_silences(audio_path, silence_threshold, min_silence)
        print(f"Found {len(silences)} silence segments (total {sum(s.duration for s in silences):.1f}s)")

        # Step 3: Transcribe with timestamps (faster-whisper)
        print("Transcribing with faster-whisper...")
        whisper_segs = transcribe_whisper(audio_path, language, whisper_model)
        print(f"Whisper: {len(whisper_segs)} segments, {sum(len(s['text']) for s in whisper_segs)} chars")

        # Step 3.5: Trim filler words from segment boundaries using word timestamps
        print("Trimming filler words from segment boundaries...")
        whisper_segs = trim_fillers_from_segments(whisper_segs)
        print(f"After filler trim: {len(whisper_segs)} segments")

        # Step 3.6: Split long segments (>8s) into clauses for finer-grained detection
        print("Splitting long segments into clauses...")
        whisper_segs = split_segments_by_clauses(whisper_segs, max_clause_len=8.0)
        print(f"After clause split: {len(whisper_segs)} segments")

        # Step 3.7: Detect intra-segment restarts (word-level: "向大家介绍一下，向大家...")
        print("Detecting intra-segment restarts...")
        before_starts = [s["start"] for s in whisper_segs]
        whisper_segs = detect_intra_restarts(whisper_segs)
        after_starts = [s["start"] for s in whisper_segs]
        changed = sum(1 for a, b in zip(before_starts, after_starts) if abs(a - b) > 0.05)
        if changed:
            print(f"Detected {changed} intra-segment restarts (trimmed segment starts)")
        else:
            print("No intra-segment restarts detected")

        full_text = " ".join(s["text"] for s in whisper_segs)
        print(f"  Preview: {full_text[:120]}...")

        # Step 4: Build unified utterance timeline
        utterances = build_utterances(info["duration"], silences, whisper_segs)
        print(f"Timeline: {len(utterances)} utterances")

        # Step 5: Detect issues
        print("Detecting repeats...")
        utterances = detect_repeats(utterances, threshold=repeat_threshold)

        print("Detecting orphan fragments...")
        utterances = detect_orphan_fragments(utterances)

        print("Detecting fillers...")
        utterances = detect_fillers(utterances)

        print("Detecting stutters...")
        utterances = detect_stutters(utterances)

        print("Detecting false starts...")
        utterances = detect_false_starts(utterances)

        # Step 5.5: LLM narrative review (optional)
        if llm_review:
            print("Running LLM narrative review...")
            utterances = review_cuts_with_llm(
                utterances, llm_model=llm_model,
                api_provider=api_provider, api_key=api_key,
                base_url=base_url,
            )

        # Step 6: Generate cut list
        cut_data = generate_cut_list(utterances, padding)

    # Optionally: run Qwen3-ASR for a better transcript (text only, for reference)
    qwen3_transcript = ""
    if use_qwen3 and qwen3_model_path and os.path.isdir(qwen3_model_path):
        print("Running Qwen3-ASR for reference transcript...")
        import torch
        from qwen_asr import Qwen3ASRModel
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        model = Qwen3ASRModel.from_pretrained(qwen3_model_path, dtype=dtype, device_map=device)
        qwen3_transcript = transcribe_qwen3(model, audio_path, language)
        print(f"Qwen3-ASR: {len(qwen3_transcript)} chars")

    # Build result
    flagged_count = sum(1 for u in utterances if u.flag)
    result = {
        "input_file": input_file,
        "duration": info["duration"],
        "silences": [asdict(s) for s in silences],
        "utterances": [asdict(u) for u in utterances],
        "cut_list": cut_data["cuts"],
        "kept_ranges": cut_data["kept_ranges"],
        "total_speech_duration": sum(u.duration for u in utterances if not u.flag),
        "estimated_output_duration": cut_data["total_kept_duration"],
        "whisper_transcript": full_text,
        "qwen3_transcript": qwen3_transcript,
        "stats": {
            "total_utterances": len(utterances),
            "flagged_utterances": flagged_count,
            "silence_count": len(silences),
            "repeat_count": sum(1 for u in utterances if u.flag == "repeat"),
            "filler_count": sum(1 for u in utterances if u.flag == "filler"),
            "stutter_count": sum(1 for u in utterances if u.flag == "stutter"),
            "false_start_count": sum(1 for u in utterances if u.flag == "false_start"),
            "llm_review_count": sum(1 for u in utterances if u.flag == "llm_review"),
        }
    }

    output_path = os.path.join(output_dir, "analysis.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Export EDL
    input_name = Path(input_file).stem
    edl_path = os.path.join(output_dir, f"{input_name}.edl")
    export_edl(input_file, cut_data["kept_ranges"], edl_path, fps=info["fps"],
               title=f"Smart Cut - {input_name}")

    print(f"\nAnalysis saved to {output_path}")
    print(f"Original: {info['duration']:.1f}s -> Estimated output: {cut_data['total_kept_duration']:.1f}s")
    print(f"  Silences: {len(silences)}, Repeats: {result['stats']['repeat_count']}, "
          f"Fillers: {result['stats']['filler_count']}, Stutters: {result['stats']['stutter_count']}, "
          f"False starts: {result['stats']['false_start_count']}"
          f"{', LLM: ' + str(result['stats']['llm_review_count']) if llm_review else ''}")
    print(f"  Total flagged: {flagged_count}/{len(utterances)}")

    return output_path


def cut_cmd(input_file: str, cut_list_path: str, output_file: str, padding: float = 0.08):
    """Execute cuts based on analysis.json."""

    with open(cut_list_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    kept_ranges = data.get("kept_ranges", [])
    if not kept_ranges:
        print("No segments to keep!")
        return

    os.makedirs(os.path.dirname(os.path.abspath(output_file)) or ".", exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"Cutting {len(kept_ranges)} segments...")
        execute_cuts(input_file, kept_ranges, output_file, tmpdir)

    info = get_video_info(output_file)
    print(f"Output: {output_file} ({info['duration']:.1f}s)")


def auto_cut(
    input_file: str,
    output_file: str,
    whisper_model: str = "base",
    silence_threshold: float = -35,
    min_silence: float = 0.6,
    language: str = "zh",
    repeat_threshold: float = 0.6,
    padding: float = 0.08,
    use_qwen3: bool = False,
    qwen3_model_path: str = "",
    llm_review: bool = False,
    llm_model: str = "gpt-4o-mini",
    api_provider: str = "auto",
    api_key: str = None,
    base_url: str = None,
):
    """One-shot: analyze + cut."""

    output_dir = os.path.dirname(os.path.abspath(output_file)) or "."
    analysis_path = analyze(
        input_file, output_dir,
        whisper_model=whisper_model,
        silence_threshold=silence_threshold,
        min_silence=min_silence,
        language=language,
        repeat_threshold=repeat_threshold,
        padding=padding,
        use_qwen3=use_qwen3,
        qwen3_model_path=qwen3_model_path,
        llm_review=llm_review,
        llm_model=llm_model,
        api_provider=api_provider,
        api_key=api_key,
        base_url=base_url,
    )
    cut_cmd(input_file, analysis_path, output_file, padding=padding)


def main():
    parser = argparse.ArgumentParser(description="Smart Cut - AI-powered video editing v2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--whisper-model", default="small", choices=["base", "small", "medium", "large"])
    common.add_argument("--silence-threshold", type=float, default=-35)
    common.add_argument("--min-silence", type=float, default=0.6)
    common.add_argument("--language", default="zh")
    common.add_argument("--repeat-threshold", type=float, default=0.6, help="Text similarity threshold for repeat detection")
    common.add_argument("--padding", type=float, default=0.08)
    common.add_argument("--use-qwen3", action="store_true", help="Also run Qwen3-ASR for reference transcript")
    common.add_argument("--qwen3-model-path", default="", help="Path to local Qwen3-ASR model")
    common.add_argument("--llm-review", action="store_true", help="Enable LLM narrative review after rule-based detection")
    common.add_argument("--llm-model", default="gpt-4o-mini", help="LLM model for review")
    common.add_argument("--api-provider", default="openai_compatible", choices=["anthropic", "openai_compatible", "auto"])
    common.add_argument("--api-key", default=None, help="API key (or set ANTHROPIC_API_KEY / OPENAI_API_KEY env var)")
    common.add_argument("--base-url", default=None, help="Base URL for OpenAI-compatible API")

    # Analyze
    p_a = subparsers.add_parser("analyze", parents=[common])
    p_a.add_argument("input")
    p_a.add_argument("--output-dir", default="./smart_cut_output")

    # Cut
    p_c = subparsers.add_parser("cut")
    p_c.add_argument("input")
    p_c.add_argument("--cut-list", required=True)
    p_c.add_argument("--output", required=True)
    p_c.add_argument("--padding", type=float, default=0.08)

    # Auto
    p_auto = subparsers.add_parser("auto", parents=[common])
    p_auto.add_argument("input")
    p_auto.add_argument("--output", required=True)

    args = parser.parse_args()

    if args.command == "analyze":
        analyze(args.input, args.output_dir,
                whisper_model=args.whisper_model,
                silence_threshold=args.silence_threshold,
                min_silence=args.min_silence,
                language=args.language,
                repeat_threshold=args.repeat_threshold,
                padding=args.padding,
                use_qwen3=args.use_qwen3,
                qwen3_model_path=args.qwen3_model_path,
                llm_review=args.llm_review,
                llm_model=args.llm_model,
                api_provider=args.api_provider,
                api_key=args.api_key,
                base_url=args.base_url)

    elif args.command == "cut":
        cut_cmd(args.input, args.cut_list, args.output, padding=args.padding)

    elif args.command == "auto":
        auto_cut(args.input, args.output,
                 whisper_model=args.whisper_model,
                 silence_threshold=args.silence_threshold,
                 min_silence=args.min_silence,
                 language=args.language,
                 repeat_threshold=args.repeat_threshold,
                 padding=args.padding,
                 use_qwen3=args.use_qwen3,
                 qwen3_model_path=args.qwen3_model_path,
                 llm_review=args.llm_review,
                 llm_model=args.llm_model,
                 api_provider=args.api_provider,
                 api_key=args.api_key,
                 base_url=args.base_url)


if __name__ == "__main__":
    main()
