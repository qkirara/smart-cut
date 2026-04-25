[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | **[Français](README.fr.md)** | [한국어](README.ko.md)

# Smart Cut

Outil d'edition video intelligent base sur l'IA, concu pour les enregistrements de cours. Detecte et supprime automatiquement les silences, les balbutiements, les erreurs, les mots de remplissage et les phrases repetees.

## Fonctionnalites

- **Detection multi-niveaux** : intervalles de silence, phrases repetees, mots de remplissage, balbutiements, faux demarrages, fragments orphelins
- **Precision au niveau des mots** : utilise les horodatages de mots de faster-whisper pour une detection precise des frontieres
- **Detection des reprises intra-segment** : detecte les reprises de phrases au sein d'un seul segment Whisper (ex. « laissez-moi vous presenter, laissez-moi vous presenter l'ensemble... »)
- **Revue LLM optionnelle** : ajoute une revue semantique pour les auto-repetitions et les fragments orphelins
- **Edition precise a l'image pres** : decoupage FFmpeg en deux passes avec alignement sur les images cles
- **Export EDL** : compatible avec Premiere Pro, DaVinci Resolve, Final Cut
- **Export PRPROJ** : generation de fichiers de projet Premiere Pro via `export_prproj.py`

## Prerequis

- Python 3.8+
- FFmpeg (doit etre dans le PATH)

## Installation

```bash
# Dependances principales
pip install faster-whisper torch

# Optionnel : pour la revue LLM
pip install anthropic    # pour Claude
pip install openai       # pour les API compatibles OpenAI
```

## Utilisation

### Mode une seule etape (analyse + decoupe)

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4
```

### Etape par etape

```bash
# Etape 1 : Analyser
python smart_cut.py analyze input.mp4 --output-dir ./output

# Etape 2 : Examiner analysis.json, puis executer
python smart_cut.py cut input.mp4 --cut-list ./output/analysis.json --output cleaned.mp4
```

### Avec revue LLM

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --llm-review \
  --llm-model gpt-4o-mini \
  --api-provider openai_compatible \
  --api-key YOUR_KEY \
  --base-url https://api.openai.com/v1
```

### Avec Qwen3-ASR pour la transcription de reference (optionnel)

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --use-qwen3 --qwen3-model-path /path/to/Qwen3-ASR-1.7B
```

## Parametres

| Parametre | Par defaut | Description |
|-----------|-----------|-------------|
| `--whisper-model` | small | Taille du modele faster-whisper (base/small/medium/large) |
| `--silence-threshold` | -35dB | Seuil de bruit pour la detection du silence |
| `--min-silence` | 0.6s | Duree minimale de silence a detecter |
| `--padding` | 0.08s | Marge autour des segments conserves |
| `--repeat-threshold` | 0.6 | Seuil de similarite textuelle pour la detection des repetitions |
| `--llm-review` | false | Activer la revue semantique LLM |
| `--llm-model` | gpt-4o-mini | Modele LLM pour la revue |
| `--api-provider` | openai_compatible | Fournisseur d'API (anthropic/openai_compatible/auto) |
| `--language` | zh | Code de langue pour la transcription |

## Fonctionnement

```
Video d'entree
  -> FFmpeg : extraction audio (WAV 16kHz)
  -> FFmpeg silencedetect : detection des intervalles de silence
  -> faster-whisper : transcription avec horodatage des mots
  -> trim_fillers_from_segments : suppression des mots de remplissage aux frontieres
  -> split_segments_by_clauses : decoupage des segments longs aux frontieres de propositions
  -> detect_intra_restarts : detection des reprises au niveau des mots
  -> build_utterances : fusion silence + ASR dans une timeline unifiee
  -> detect_repeats : detection des repetitions au niveau des phrases
  -> detect_orphan_fragments : sous-chaine + correspondance floue
  -> detect_fillers : segments purs en mots de remplissage / a fort ratio
  -> detect_stutters : balbutiements au niveau des caracteres (4+ repetitions)
  -> detect_false_starts : faux demarrages courts avant les continuations
  -> [optionnel] review_cuts_with_llm : revue au niveau semantique
  -> generate_cut_list -> FFmpeg : execution des decoupes -> concatenation
  -> Sortie video nettoyee (H.264/AAC MP4)
```

## Algorithmes de detection

### Detection des repetitions
Comparaison de similarite SequenceMatcher. Les segments courts (<=25 caracteres) utilisent un seuil reduit (0.52). Les segments dont le ratio texte/duree est anormalement bas (probables echecs de transcription Whisper) sont ignores.

### Detection des fragments orphelins
Correspondance en deux phases :
1. **Sous-chaine exacte** (toute longueur) : le texte du segment apparait dans un segment conserve voisin
2. **Sous-chaine floue** (<=4 caracteres uniquement) : fenetre glissante SequenceMatcher >= 0.6

### Detection des reprises intra-segment
Detection au niveau des mots avec trois strategies :
1. **Basee sur les ecarts** : trouve des ecarts entre mots > 0.6s avec un contenu similaire avant/apres
2. **Repetition de N mots** : detecte les sequences de mots repetees dans un segment
3. **Absorption en mot long** : Whisper absorbe parfois les phrases repetees en un seul mot long (>5s pour <=3 caracteres)

## Entrees/Sorties

| Entree | Notes |
|--------|-------|
| .webm | Enregistrements OpenScreen (AV1+Opus) |
| .mp4 | Support natif |
| .mkv/.mov | Support natif |

Sortie : toujours au format H.264/AAC MP4 avec fichier EDL inclus.

## Licence

MIT
