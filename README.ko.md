[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [Français](README.fr.md) | **[한국어](README.ko.md)**

# Smart Cut

AI 기반 스마트 영상 편집 도구로, 강의 녹화용으로 설계되었습니다. 무음 구간, 말더듬, 실수, 불필요한 추임새, 반복 문장을 자동으로 감지하여 제거합니다.

## 기능

- **다층 감지**: 무음 구간, 반복 문장, 불필요한 추임새, 말더듬, 잘못 시작된 발화, 고립된 파편
- **단어 수준의 정밀도**: faster-whisper의 단어 타임스탬프를 활용하여 정확한 경계 감지
- **세그먼트 내 재시작 감지**: 단일 Whisper 세그먼트 내에서 문구 재시작을 감지 (예: "소개해 드리겠습니다, 소개해 드리겠습니다 전체를...")
- **선택적 LLM 검토**: 자가 반복 및 고립 파편에 대한 의미 수준의 검토 추가
- **프레임 정밀 편집**: 키프레임 정렬을 통한 2패스 FFmpeg 컷
- **EDL 내보내기**: Premiere Pro, DaVinci Resolve, Final Cut 호환
- **PRPROJ 내보내기**: `export_prproj.py`를 통해 Premiere Pro 프로젝트 파일 생성

## 요구 사항

- Python 3.8+
- FFmpeg (PATH에 포함되어야 함)

## 설치

```bash
# 핵심 의존성
pip install faster-whisper torch

# 선택사항: LLM 검토용
pip install anthropic    # Claude용
pip install openai       # OpenAI 호환 API용
```

## 사용법

### 원샷 모드 (분석 + 컷)

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4
```

### 단계별 실행

```bash
# 1단계: 분석
python smart_cut.py analyze input.mp4 --output-dir ./output

# 2단계: analysis.json 확인 후 실행
python smart_cut.py cut input.mp4 --cut-list ./output/analysis.json --output cleaned.mp4
```

### LLM 검토와 함께 사용

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --llm-review \
  --llm-model gpt-4o-mini \
  --api-provider openai_compatible \
  --api-key YOUR_KEY \
  --base-url https://api.openai.com/v1
```

### Qwen3-ASR를 참조 전사본으로 사용 (선택사항)

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --use-qwen3 --qwen3-model-path /path/to/Qwen3-ASR-1.7B
```

## 매개변수

| 매개변수 | 기본값 | 설명 |
|----------|--------|------|
| `--whisper-model` | small | faster-whisper 모델 크기 (base/small/medium/large) |
| `--silence-threshold` | -35dB | 무음 감지를 위한 노이즈 임계값 |
| `--min-silence` | 0.6s | 감지할 최소 무음 시간 |
| `--padding` | 0.08s | 유지 세그먼트 주변의 여백 |
| `--repeat-threshold` | 0.6 | 반복 감지를 위한 텍스트 유사도 임계값 |
| `--llm-review` | false | LLM 의미 수준 검토 활성화 |
| `--llm-model` | gpt-4o-mini | 검토에 사용할 LLM 모델 |
| `--api-provider` | openai_compatible | API 제공자 (anthropic/openai_compatible/auto) |
| `--language` | zh | 전사 언어 코드 |

## 작동 원리

```
입력 영상
  -> FFmpeg: 오디오 추출 (16kHz WAV)
  -> FFmpeg silencedetect: 무음 구간 감지
  -> faster-whisper: 단어 타임스탬프가 포함된 전사
  -> trim_fillers_from_segments: 경계부 불필요한 추임새 제거
  -> split_segments_by_clauses: 절 경계에서 긴 세그먼트 분할
  -> detect_intra_restarts: 단어 수준 재시작 감지
  -> build_utterances: 무음 + ASR을 통합 타임라인으로 병합
  -> detect_repeats: 문장 수준 반복 감지
  -> detect_orphan_fragments: 부분 문자열 + 퍼지 매칭
  -> detect_fillers: 순수 불필요 추임새 / 높은 추임새 비율 세그먼트
  -> detect_stutters: 문자 수준 말더듬 감지 (4회 이상 반복)
  -> detect_false_starts: 연속 발화 전의 짧은 잘못 시작
  -> [선택사항] review_cuts_with_llm: 의미 수준 검토
  -> generate_cut_list -> FFmpeg: 컷 실행 -> 연결
  -> 깨끗한 영상 출력 (H.264/AAC MP4)
```

## 감지 알고리즘

### 반복 감지
SequenceMatcher 유사도 비교입니다. 짧은 세그먼트(25자 이하)는 낮은 임계값(0.52)을 사용합니다. 텍스트 대비 시간 비율이 의심스럽게 낮은 세그먼트(Whisper 전사 실패 가능성)는 건너뜁니다.

### 고립 파편 감지
두 단계 매칭:
1. **정확한 부분 문자열** (임의 길이): 세그먼트 텍스트가 근처 유지 세그먼트에 포함됨
2. **퍼지 부분 문자열** (4자 이하만): 슬라이딩 윈도우 SequenceMatcher >= 0.6

### 세그먼트 내 재시작 감지
세 가지 전략을 통한 단어 수준 감지:
1. **간격 기반**: 0.6초 이상의 단어 간격에서 전후 내용이 유사한 것을 찾음
2. **N단어 반복**: 세그먼트 내에서 반복되는 단어 시퀀스를 감지
3. **긴 단어 흡수**: Whisper가 반복되는 문구를 단일 긴 단어로 흡수하는 경우가 있음 (3자 이하에서 5초 이상)

## 입력/출력

| 입력 | 비고 |
|------|------|
| .webm | OpenScreen 녹화 (AV1+Opus) |
| .mp4 | 기본 지원 |
| .mkv/.mov | 기본 지원 |

출력: 항상 H.264/AAC MP4 형식이며, EDL 파일이 함께 생성됩니다.

## 라이선스

MIT
