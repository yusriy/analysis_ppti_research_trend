# PPTI Research Brief

This folder contains the automated short-form audio companion to the monthly
PPTI Research Publication Trends and Strategic Positioning report.

The generator reads only the validated analytical artifact produced by the report
builder. It creates a 115–210 word script, branded vertical cover, transcript,
social-media caption, and episode metadata. The installed local Voicebox server
uses the Yusri profile with the Qwen 1.7B engine to create:

- `ppti_research_brief.mp3`
- `captions.srt`
- `timing.json`
- `ppti_research_brief_vertical.mp4`

The current files are stored in `latest/`. Before a new reporting period is
generated, the previous edition is copied to `archive/YYYY-MM/`.

Cover backgrounds rotate by reporting month through a restrained palette of
institutional white, muted blues, sage, sand, blush, mint, and lavender. June
uses the original white background. Typography, navy-and-gold framing, logos,
captions, and the reports QR code remain consistent across editions.

## Latest edition

- [Listen to the MP3](latest/ppti_research_brief.mp3)
- [View the vertical captioned video](latest/ppti_research_brief_vertical.mp4)
- [Read the transcript](latest/transcript.txt)
- [Copy the social-media caption](latest/social_caption.txt)

## Voicebox configuration

Voicebox runs locally; no cloud API key or subscription is required. The default
configuration expects Voicebox 0.5 or later in `/Applications/Voicebox.app` and
uses the profile named `Yusri`.

To override the profile, engine, model, server path, or delivery instruction,
copy `voicebox.env.example` to `.secrets/voicebox.env` and edit the values.

The `.secrets/` directory is excluded by `.gitignore`. The generator parses its
configuration file directly; it does not execute shell syntax.

## Commands

Create or refresh only the script package:

```bash
$HOME/miniforge3/bin/conda run -n openai_ppti5 \
  python podcast/build_podcast.py --script-only
```

Generate the narration and social video:

```bash
$HOME/miniforge3/bin/conda run -n openai_ppti5 \
  python podcast/build_podcast.py
```

The script starts a private Voicebox server when needed and stops only the server
process it started. It avoids repeating local generation when the existing MP3
already matches the current script, profile, engine, and model. Use `--force`
only when narration must be regenerated deliberately.
