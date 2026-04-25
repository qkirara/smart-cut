[English](README.md) | [简体中文](README.zh-CN.md) | **[日本語](README.ja.md)** | [Français](README.fr.md) | [한국어](README.ko.md)

# Smart Cut

AI搭載のスマート動画編集ツールです。授業録画向けに設計されており、無音区間、吃音、ミス、フィラーワード、繰り返し文を自動的に検出して削除します。

## 機能

- **多層検出**：無音区間、繰り返し文、フィラーワード、吃音、言い間違いの冒頭、孤立フラグメント
- **単語レベルの精度**：faster-whisper の単語タイムスタンプを使用し、正確な境界検出を実現
- **セグメント内のやり直し検出**：単一の Whisper セグメント内でのフレーズのやり直しを検出（例：「紹介させていただきます、紹介させていただきます全体を…」）
- **オプション LLM レビュー**：自己反復や孤立フラグメントに対する意味レベルのレビューを追加
- **フレーム精度の編集**：キーフレームアラインメントによる2パス FFmpeg カット
- **EDL エクスポート**：Premiere Pro、DaVinci Resolve、Final Cut に対応
- **PRPROJ エクスポート**：`export_prproj.py` で Premiere Pro プロジェクトファイルを生成

## 動作環境

- Python 3.8+
- FFmpeg（PATH に含まれていること）

## インストール

```bash
# コア依存関係
pip install faster-whisper torch

# オプション：LLM レビューを使用する場合
pip install anthropic    # Claude 用
pip install openai       # OpenAI 互換 API 用
```

## 使い方

### ワンショットモード（分析 + カット）

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4
```

### ステップバイステップ

```bash
# ステップ 1：分析
python smart_cut.py analyze input.mp4 --output-dir ./output

# ステップ 2：analysis.json を確認してから実行
python smart_cut.py cut input.mp4 --cut-list ./output/analysis.json --output cleaned.mp4
```

### LLM レビューを使用

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --llm-review \
  --llm-model gpt-4o-mini \
  --api-provider openai_compatible \
  --api-key YOUR_KEY \
  --base-url https://api.openai.com/v1
```

### Qwen3-ASR を参照トランスクリプトとして使用（オプション）

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --use-qwen3 --qwen3-model-path /path/to/Qwen3-ASR-1.7B
```

## パラメータ

| パラメータ | デフォルト | 説明 |
|------------|-----------|------|
| `--whisper-model` | small | faster-whisper モデルサイズ（base/small/medium/large） |
| `--silence-threshold` | -35dB | 無音検出のノイズ閾値 |
| `--min-silence` | 0.6s | 検出する最小無音時間 |
| `--padding` | 0.08s | 保留セグメント前後のパディング |
| `--repeat-threshold` | 0.6 | 繰り返し検出のテキスト類似度閾値 |
| `--llm-review` | false | LLM ナラティブレビューを有効化 |
| `--llm-model` | gpt-4o-mini | レビューに使用する LLM モデル |
| `--api-provider` | openai_compatible | API プロバイダー（anthropic/openai_compatible/auto） |
| `--language` | zh | 文字起こしの言語コード |

## 動作の仕組み

```
入力動画
  -> FFmpeg：音声抽出（16kHz WAV）
  -> FFmpeg silencedetect：無音区間の検出
  -> faster-whisper：単語タイムスタンプ付き文字起こし
  -> trim_fillers_from_segments：境界のフィラーワードを除去
  -> split_segments_by_clauses：節境界で長いセグメントを分割
  -> detect_intra_restarts：単語レベルのやり直し検出
  -> build_utterances：無音 + ASR を統一タイムラインにマージ
  -> detect_repeats：文レベルの繰り返し検出
  -> detect_orphan_fragments：部分文字列 + ファジーマッチング
  -> detect_fillers：純フィラー / 高フィラー比率セグメント
  -> detect_stutters：文字レベルの吃音検出（4回以上の繰り返し）
  -> detect_false_starts：続きの前の短い言い間違いの冒頭
  -> [オプション] review_cuts_with_llm：意味レベルのレビュー
  -> generate_cut_list -> FFmpeg：カット実行 -> 連結
  -> クリーン動画を出力（H.264/AAC MP4）
```

## 検出アルゴリズム

### 繰り返し検出
SequenceMatcher による類似度比較。短いセグメント（25文字以下）は低い閾値（0.52）を使用します。テキストと時間の比率が異常に低いセグメント（Whisper の文字起こしエラーの可能性）はスキップされます。

### 孤立フラグメント検出
2段階マッチング：
1. **厳密な部分文字列**（任意の長さ）：セグメントテキストが近くの保持セグメントに含まれる
2. **ファジー部分文字列**（4文字以下のみ）：スライディングウィンドウ SequenceMatcher >= 0.6

### セグメント内やり直し検出
3つの戦略による単語レベル検出：
1. **ギャップベース**：0.6秒以上の単語ギャップで前後の内容が類似しているものを検出
2. **N単語繰り返し**：セグメント内で繰り返される単語シーケンスを検出
3. **長単語吸収**：Whisper が繰り返しフレーズを単一の長い単語に吸収することがある（3文字以下で5秒以上）

## 入力/出力

| 入力 | 備考 |
|------|------|
| .webm | OpenScreen 録画（AV1+Opus） |
| .mp4 | ネイティブサポート |
| .mkv/.mov | ネイティブサポート |

出力：常に H.264/AAC MP4 形式で、EDL ファイルを同梱。

## ライセンス

MIT
