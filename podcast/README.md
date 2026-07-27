# PPTI Research Brief

This folder contains the automated short-form audio companion to the monthly
PPTI Research Publication Trends and Strategic Positioning report.

The generator reads only the validated analytical artifact produced by the report
builder. It creates a 115–210 word script, branded vertical cover, transcript,
social-media caption, and episode metadata. When ElevenLabs credentials are
available, it also creates:

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

## Credentials

Do not place an API key in this repository or send it through a chat message.

1. Copy `elevenlabs.env.example` to
   `.secrets/elevenlabs.env` at the repository root.
2. Replace the two placeholders with the ElevenLabs API key and voice ID.
3. Restrict the file to the current macOS account with `chmod 600`.

The `.secrets/` directory is excluded by `.gitignore`. The generator parses this
file directly; it does not execute it as shell code.

## Commands

Create or refresh the script package without using ElevenLabs credits:

```bash
$HOME/miniforge3/bin/conda run -n openai_ppti5 \
  python podcast/build_podcast.py --script-only
```

Generate the narration and social video:

```bash
$HOME/miniforge3/bin/conda run -n openai_ppti5 \
  python podcast/build_podcast.py --require-audio
```

The script avoids a repeated ElevenLabs call when the existing MP3 already matches
the current script, voice, and model. Use `--force` only when narration must be
regenerated deliberately.
