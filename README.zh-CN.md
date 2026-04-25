[English](README.md) | **[简体中文](README.zh-CN.md)** | [日本語](README.ja.md) | [Français](README.fr.md) | [한국어](README.ko.md)

# Smart Cut

AI 驱动的智能视频剪辑工具，专为教学录制设计。自动检测并去除静音、口吃、口误、语气词和重复句子。

## 功能特性

- **多层检测**：静音片段、重复句子、语气词、口吃、错误开头、孤立片段
- **词级精度**：使用 faster-whisper 的词级时间戳，实现精准边界检测
- **段落内重说检测**：捕获单个 Whisper 段落中的短语重说（例如"让我介绍一下，让我介绍一下整个……"）
- **可选 LLM 审核**：增加语义层面的审核，处理自我重复和孤立片段
- **帧级精准剪辑**：两遍 FFmpeg 剪辑，对齐关键帧
- **EDL 导出**：兼容 Premiere Pro、DaVinci Resolve、Final Cut
- **PRPROJ 导出**：通过 `export_prproj.py` 生成 Premiere Pro 项目文件

## 系统要求

- Python 3.8+
- FFmpeg（必须在 PATH 中）

## 安装

```bash
# 核心依赖
pip install faster-whisper torch

# 可选：用于 LLM 审核
pip install anthropic    # Claude
pip install openai       # OpenAI 兼容 API
```

## 使用方法

### 一键模式（分析 + 剪辑）

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4
```

### 分步操作

```bash
# 第一步：分析
python smart_cut.py analyze input.mp4 --output-dir ./output

# 第二步：检查 analysis.json，然后执行剪辑
python smart_cut.py cut input.mp4 --cut-list ./output/analysis.json --output cleaned.mp4
```

### 配合 LLM 审核

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --llm-review \
  --llm-model gpt-4o-mini \
  --api-provider openai_compatible \
  --api-key YOUR_KEY \
  --base-url https://api.openai.com/v1
```

### 使用 Qwen3-ASR 作为参考转录（可选）

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --use-qwen3 --qwen3-model-path /path/to/Qwen3-ASR-1.7B
```

## 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--whisper-model` | small | faster-whisper 模型大小（base/small/medium/large） |
| `--silence-threshold` | -35dB | 静音检测的噪声阈值 |
| `--min-silence` | 0.6s | 检测静音的最小时长 |
| `--padding` | 0.08s | 保留片段前后的缓冲时长 |
| `--repeat-threshold` | 0.6 | 重复检测的文本相似度阈值 |
| `--llm-review` | false | 启用 LLM 语义审核 |
| `--llm-model` | gpt-4o-mini | 用于审核的 LLM 模型 |
| `--api-provider` | openai_compatible | API 提供商（anthropic/openai_compatible/auto） |
| `--language` | zh | 转录语言代码 |

## 工作流程

```
输入视频
  -> FFmpeg：提取音频（16kHz WAV）
  -> FFmpeg silencedetect：检测静音片段
  -> faster-whisper：带词级时间戳的语音转文字
  -> trim_fillers_from_segments：去除边界处的语气词
  -> split_segments_by_clauses：在从句边界拆分长段落
  -> detect_intra_restarts：词级重说检测
  -> build_utterances：合并静音与 ASR 到统一时间线
  -> detect_repeats：句子级重复检测
  -> detect_orphan_fragments：子串 + 模糊匹配
  -> detect_fillers：纯语气词 / 高语气词比例段落
  -> detect_stutters：字符级口吃检测（4次以上重复）
  -> detect_false_starts：延续前的短错误开头
  -> [可选] review_cuts_with_llm：语义级审核
  -> generate_cut_list -> FFmpeg：执行剪辑 -> 拼接
  -> 输出干净视频（H.264/AAC MP4）
```

## 检测算法

### 重复检测
基于 SequenceMatcher 相似度比较。短段落（<=25字符）使用降低的阈值（0.52）。文本与时长比例异常偏低的段落（可能是 Whisper 转录错误）将被跳过。

### 孤立片段检测
两阶段匹配：
1. **精确子串匹配**（任意长度）：段落文本出现在附近保留的段落中
2. **模糊子串匹配**（仅<=4字符）：滑动窗口 SequenceMatcher >= 0.6

### 段落内重说检测
词级检测，包含三种策略：
1. **基于间隔**：发现大于 0.6 秒的词间隔，且前后内容相似
2. **N词重复**：检测段落内重复的词序列
3. **长词吸收**：Whisper 有时将重复的短语合并为一个长词（<=3字符但时长>5秒）

## 输入/输出

| 输入格式 | 说明 |
|----------|------|
| .webm | OpenScreen 录屏（AV1+Opus） |
| .mp4 | 原生支持 |
| .mkv/.mov | 原生支持 |

输出：始终为 H.264/AAC MP4 格式，附带 EDL 文件。

## 许可证

MIT
