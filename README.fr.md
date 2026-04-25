[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | **Français** | [한국어](README.ko.md)

# Smart Cut

Outil d'édition vidéo intelligent basé sur l'IA, conçu pour les enregistrements de cours. Détecte et supprime automatiquement les silences, les balbutiements, les erreurs, les mots de remplissage et les phrases répétées.

## Fonctionnalités

- **Détection multi-niveaux** : intervalles de silence, phrases répétées, mots de remplissage, balbutiements, faux démarrages, fragments orphelins
- **Précision au niveau des mots** : utilise les horodatages de mots de faster-whisper pour une détection précise des frontières
- **Détection des reprises intra-segment** : détecte les reprises de phrases au sein d'un seul segment Whisper (ex. « laissez-moi vous présenter, laissez-moi vous présenter l'ensemble... »)
- **Revue LLM optionnelle** : ajoute une revue sémantique pour les auto-répétitions et les fragments orphelins
- **Édition précise à l'image près** : découpage FFmpeg en deux passes avec alignement sur les images clés
- **Export EDL** : compatible avec Premiere Pro, DaVinci Resolve, Final Cut
- **Export PRPROJ** : génération de fichiers de projet Premiere Pro via `export_prproj.py`

## Prérequis

- Python 3.8+
- FFmpeg (doit être dans le PATH)

## Installation

```bash
# Dépendances principales
pip install faster-whisper torch

# Optionnel : pour la revue LLM
pip install anthropic    # pour Claude
pip install openai       # pour les API compatibles OpenAI
```

## Utilisation

### Mode une seule étape (analyse + découpe)

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4
```

### Étape par étape

```bash
# Étape 1 : Analyser
python smart_cut.py analyze input.mp4 --output-dir ./output

# Étape 2 : Examiner analysis.json, puis exécuter
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

### Avec Qwen3-ASR pour la transcription de référence (optionnel)

```bash
python smart_cut.py auto input.mp4 --output cleaned.mp4 \
  --use-qwen3 --qwen3-model-path /path/to/Qwen3-ASR-1.7B
```

## Paramètres

| Paramètre | Par défaut | Description |
|-----------|-----------|-------------|
| `--whisper-model` | small | Taille du modèle faster-whisper (base/small/medium/large) |
| `--silence-threshold` | -35dB | Seuil de bruit pour la détection du silence |
| `--min-silence` | 0.6s | Durée minimale de silence à détecter |
| `--padding` | 0.08s | Marge autour des segments conservés |
| `--repeat-threshold` | 0.6 | Seuil de similarité textuelle pour la détection des répétitions |
| `--llm-review` | false | Activer la revue sémantique LLM |
| `--llm-model` | gpt-4o-mini | Modèle LLM pour la revue |
| `--api-provider` | openai_compatible | Fournisseur d'API (anthropic/openai_compatible/auto) |
| `--language` | zh | Code de langue pour la transcription |

## Fonctionnement

```
Vidéo d'entrée
  -> FFmpeg : extraction audio (WAV 16kHz)
  -> FFmpeg silencedetect : détection des intervalles de silence
  -> faster-whisper : transcription avec horodatage des mots
  -> trim_fillers_from_segments : suppression des mots de remplissage aux frontières
  -> split_segments_by_clauses : découpage des segments longs aux frontières de propositions
  -> detect_intra_restarts : détection des reprises au niveau des mots
  -> build_utterances : fusion silence + ASR dans une timeline unifiée
  -> detect_repeats : détection des répétitions au niveau des phrases
  -> detect_orphan_fragments : sous-chaîne + correspondance floue
  -> detect_fillers : segments purs en mots de remplissage / à fort ratio
  -> detect_stutters : balbutiements au niveau des caractères (4+ répétitions)
  -> detect_false_starts : faux démarrages courts avant les continuations
  -> [optionnel] review_cuts_with_llm : revue au niveau sémantique
  -> generate_cut_list -> FFmpeg : exécution des découpes -> concaténation
  -> Sortie vidéo nettoyée (H.264/AAC MP4)
```

## Algorithmes de détection

### Détection des répétitions
Comparaison de similarité SequenceMatcher. Les segments courts (<=25 caractères) utilisent un seuil réduit (0.52). Les segments dont le ratio texte/durée est anormalement bas (probables échecs de transcription Whisper) sont ignorés.

### Détection des fragments orphelins
Correspondance en deux phases :
1. **Sous-chaîne exacte** (toute longueur) : le texte du segment apparaît dans un segment conservé voisin
2. **Sous-chaîne floue** (<=4 caractères uniquement) : fenêtre glissante SequenceMatcher >= 0.6

### Détection des reprises intra-segment
Détection au niveau des mots avec trois stratégies :
1. **Basée sur les écarts** : trouve des écarts entre mots > 0.6s avec un contenu similaire avant/après
2. **Répétition de N mots** : détecte les séquences de mots répétées dans un segment
3. **Absorption en mot long** : Whisper absorbe parfois les phrases répétées en un seul mot long (>5s pour <=3 caractères)

## Entrées/Sorties

| Entrée | Notes |
|--------|-------|
| .webm | Enregistrements OpenScreen (AV1+Opus) |
| .mp4 | Support natif |
| .mkv/.mov | Support natif |

Sortie : toujours au format H.264/AAC MP4 avec fichier EDL inclus.

## Licence

MIT
