# ISL Bridge

Bidirectional Kannada voice ↔ Indian Sign Language system for public service terminals.

## Stage 1 — Kannada voice input

Browser mic → FastAPI → **Sarvam Saaras** (`kn-IN`) → Kannada text on screen.
Sarvam is the engine behind sarvam.ai's Kannada speech-to-text page, so accuracy
(code-mixed Kannada-English, numbers, names) matches what you see there.
Local Whisper is kept as an optional offline fallback.

```
isl-bridge/
├── backend/    FastAPI (Sarvam API client, optional Whisper fallback)
└── frontend/   React (Vite) mic recorder UI
```

### 1. Get a Sarvam API key
Sign up at https://dashboard.sarvam.ai (free tier, no card). Then:

```bash
cd backend
cp .env.example .env      # Windows: copy .env.example .env
# edit .env and set SARVAM_API_KEY=your_key
```
Never commit `.env` (it is in `.gitignore`).

### 2. Run the backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 3. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The badge should read "Sarvam saaras:v4 · ready". Tap the mic
and speak Kannada (e.g. "ನಿಮ್ಮ ಹೆಸರು ಏನು?").

### Settings (`backend/.env`)

| Variable | Default | Notes |
|---|---|---|
| `SARVAM_API_KEY` | — | required for Sarvam |
| `SARVAM_MODEL` | `saaras:v4` | or `saaras:v3` |
| `SARVAM_LANGUAGE` | `kn-IN` | |
| `SARVAM_MODE` | blank | `transcribe`, `verbatim`, `codemix` |
| `STT_PROVIDER` | `sarvam` if a key is set, else `whisper` | |

### Notes

- Sarvam's REST endpoint accepts up to **30 seconds** per request; the UI auto-stops at 29 s.
  Longer audio needs Sarvam's Batch or WebSocket streaming API (a later upgrade for kiosks).
- Free tier has usage limits; after that it is billed per minute of audio.
- Browsers only allow the microphone on `http://localhost` or `https://`. A real kiosk must serve over HTTPS.
- The API key stays on the backend only; the browser never sees it.

### Optional: offline Whisper fallback

```bash
pip install -r requirements-whisper.txt
# in .env:  STT_PROVIDER=whisper   (WHISPER_MODEL=medium for better Kannada)
```
If you see `open() got an unexpected keyword argument 'metadata_errors'`, run
`pip install "av>=14,<19"` (already pinned in `requirements-whisper.txt`).

## Stage 2 — Kannada → English → ISL linguistic processing

```
Kannada text --Sarvam Translate--> English --spaCy + ISL rules--> gloss / sign sequence
```

New pieces (all in `backend/`):

| File | Purpose |
|---|---|
| `isl/processor.py` | the pipeline: parse → remove extra words → apply ISL grammar → sign sequence |
| `isl/lexicon.json` | **custom ISL mapping**: English words → gloss labels (≈200, all `unverified`) |
| `isl/rules.json` | **editable ISL grammar settings** (question-word position, tense markers, unknown words…) |
| `tests/test_isl.py` | 42 tests (`python -m pytest tests -q`) |

API: `POST /api/translate` (Kannada → English), `POST /api/isl` (English → signs, **no key needed**),
`POST /api/pipeline` (Kannada → English → signs). The page calls `/api/pipeline` after every recording
and has a box to type English sentences directly.

Try it without the web page (inside `backend/`):

```bash
pip install -r requirements.txt            # installs spaCy + the English model
python -m isl "What is your name?"         # -> GLOSS: WHAT YOUR NAME
python -m isl "I want to apply for a driving license tomorrow"
python -m pytest tests -q
```

### What the processor does

1. **Understands the sentence** with spaCy (dependency parse, lemmas, named entities).
2. **Removes extra words**: articles, `is/am/are`, auxiliaries, most prepositions, conjunctions,
   fillers (`very, really, just…`). The page lists every removed word and why.
3. **Applies ISL grammar rules** (editable): `[time] [subject] [objects] [adverbs] [verb] [modal] [NOT]`,
   question words at the start (default, as in your diagram) or end, negation after the verb,
   "if/because" clauses first, names fingerspelled, tense through time words.
4. **Generates the sign sequence**: each gloss gets an animation name (`ISL_WHAT`) and a status.
   Only signs with `status: "animated"` count as playable; the rest are reported so the website can
   handle them (see below).

| Output chip | Meaning |
|---|---|
| green | animation ready |
| amber | sign mapped, animation not made yet (all of them, at this stage) |
| purple | fingerspelled (names, unknown nouns) |
| red | no sign in the lexicon (unknown verbs/adjectives) |

### Important: what is and is not verified

- The glosses are **labels** for your sign database. They do not say how a sign looks.
- The word-order rules are **assumptions** built from general descriptions of ISL (subject-object-verb,
  time first, negation last). **Nobody has checked them against ISLRTC material or a fluent signer yet.**
  In particular, many ISL descriptions put question words at the *end*; set `"wh_position": "end"`
  in `isl/rules.json` to try that.
- Every lexicon entry is `unverified`. Check each sign against the ISLRTC dictionary/videos, then set
  `verified` (reference recorded) and later `animated` (avatar action exists).
- Plurals, adjective order inside noun phrases, relative clauses and long compound sentences are only
  handled simply. Test with real service-terminal sentences and tell me where it goes wrong.

### Customising

- Add or change signs: edit `isl/lexicon.json` (`words` = English words/phrases that map to the gloss).
- Change grammar behaviour: edit `isl/rules.json`, then update the expected values in `tests/test_isl.py`.
- Restart the backend after editing either file.

## Roadmap

1. ✅ Kannada voice → text (Sarvam Saaras)
2. ✅ Kannada → English (Sarvam) → ISL gloss / sign sequence (spaCy + custom lexicon + rules)
3. Avatar: see the separate `isl-avatar` project (rig, one verified sign, GLB export)
4. Website playback of a sign sequence on the avatar, with fallbacks for missing signs
5. Sign → speech (MediaPipe)
