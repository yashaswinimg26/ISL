import { useEffect, useState } from "react";
import { useRecorder } from "./useRecorder.js";
import IslPanel from "./IslPanel.jsx";

const MIN_SECONDS = 0.6;

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    const detail = Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join("; ") : j.detail;
    throw new Error(detail || `Server error (${res.status})`);
  }
  return res.json();
}

function extFor(mime) {
  if (mime.includes("mp4")) return "m4a";
  if (mime.includes("ogg")) return "ogg";
  return "webm";
}

export default function App() {
  const { state, seconds, level, start, stop } = useRecorder();
  const [status, setStatus] = useState("idle"); // idle | recording | processing | done | error
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  // ISL stage: Kannada -> English -> sign sequence
  const [islView, setIslView] = useState({ kannada: "", english: "", isl: null });
  const [islLoading, setIslLoading] = useState(false);
  const [islError, setIslError] = useState("");
  const [englishInput, setEnglishInput] = useState("");
  const [server, setServer] = useState({
    ok: null,
    loaded: false,
    provider: "",
    model: "",
    error: null,
    maxSeconds: 30,
  });

  // Poll backend health until the speech engine is ready.
  useEffect(() => {
    let alive = true;
    let timer;
    const check = async () => {
      try {
        const r = await fetch("/api/health");
        const j = await r.json();
        if (!alive) return;
        setServer({
          ok: true,
          loaded: j.model_loaded,
          provider: j.provider,
          model: j.model,
          error: j.error,
          maxSeconds: j.max_seconds || 30,
        });
        if (!j.model_loaded) timer = setTimeout(check, 3000);
      } catch {
        if (!alive) return;
        setServer({ ok: false, loaded: false });
        timer = setTimeout(check, 3000);
      }
    };
    check();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  async function handleStart() {
    setError("");
    try {
      await start();
      setStatus("recording");
    } catch (e) {
      setError(
        e.name === "NotAllowedError"
          ? "Microphone permission denied. Allow mic access in your browser and try again."
          : e.message
      );
      setStatus("error");
    }
  }

  // Kannada text -> English (Sarvam) -> ISL sign sequence
  async function runPipeline(kannada) {
    setIslError("");
    setIslLoading(true);
    try {
      const d = await postJson("/api/pipeline", { text: kannada });
      setIslView({ kannada: d.kannada, english: d.english, isl: d.isl });
    } catch (e) {
      setIslView({ kannada: "", english: "", isl: null });
      setIslError(e.message);
    } finally {
      setIslLoading(false);
    }
  }

  // English text typed by the user -> ISL sign sequence (no Sarvam key needed)
  async function runEnglish(e) {
    e?.preventDefault();
    const text = englishInput.trim();
    if (!text) return;
    setIslError("");
    setIslLoading(true);
    try {
      const d = await postJson("/api/isl", { text });
      setIslView({ kannada: "", english: text, isl: d });
    } catch (err) {
      setIslView({ kannada: "", english: "", isl: null });
      setIslError(err.message);
    } finally {
      setIslLoading(false);
    }
  }

  async function handleStop() {
    try {
      const { blob, mimeType, seconds: secs } = await stop();
      if (secs < MIN_SECONDS) {
        setStatus("idle");
        setError("Recording too short. Please speak for a moment.");
        return;
      }
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      setAudioUrl(URL.createObjectURL(blob));
      setStatus("processing");

      const form = new FormData();
      form.append("audio", blob, `speech.${extFor(mimeType)}`);
      const res = await fetch("/api/transcribe", { method: "POST", body: form });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `Server error (${res.status})`);
      }
      const data = await res.json();
      setTranscript(data.text);
      setStatus("done");
      if (data.text) runPipeline(data.text);
    } catch (e) {
      setError(e.message);
      setStatus("error");
    }
  }

  const recording = state === "recording";
  const busy = status === "processing";
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");

  // Stop automatically just before the provider's per-request limit (Sarvam REST = 30 s).
  const autoStopAt = server.maxSeconds - 1;
  useEffect(() => {
    if (recording && seconds >= autoStopAt) handleStop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording, seconds, autoStopAt]);

  const engineName =
    server.provider === "sarvam"
      ? `Sarvam ${server.model}`
      : server.provider === "whisper"
      ? `Whisper ${server.model}`
      : "speech model";
  const notConfigured = server.ok && !server.loaded && server.error;

  return (
    <main className="app">
      <header>
        <h1>ISL Bridge</h1>
        <p className="sub">Kannada voice → English → ISL signs</p>
        <span
          className={`pill ${
            server.ok === false || notConfigured ? "bad" : server.loaded ? "good" : "wait"
          }`}
        >
          {server.ok === false
            ? "Backend offline"
            : notConfigured
            ? "Not configured"
            : server.loaded
            ? `${engineName} · ready`
            : `Loading ${engineName}…`}
        </span>
        {notConfigured && <div className="error">{server.error}</div>}
      </header>

      <section className="card">
        <button
          className={`mic ${recording ? "rec" : ""}`}
          onClick={recording ? handleStop : handleStart}
          disabled={busy || server.ok === false || notConfigured}
          aria-label={recording ? "Stop recording" : "Start recording"}
          style={{ "--level": level }}
        >
          <span className="ring" />
          <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor">
            {recording ? (
              <rect x="6" y="6" width="12" height="12" rx="2" />
            ) : (
              <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />
            )}
          </svg>
        </button>

        <p className="hint">
          {recording
            ? `Listening… ${mm}:${ss} / max ${autoStopAt}s — tap to stop`
            : busy
            ? "Converting speech to text…"
            : "Tap the mic and speak in Kannada"}
        </p>

        {error && <div className="error">{error}</div>}

        <div className="transcript" lang="kn">
          {busy ? (
            <span className="muted">Transcribing…</span>
          ) : transcript ? (
            transcript
          ) : status === "done" ? (
            <span className="muted">No speech detected. Try again.</span>
          ) : (
            <span className="muted">Your Kannada text will appear here.</span>
          )}
        </div>

        {audioUrl && <audio controls src={audioUrl} className="player" />}

        {transcript && (
          <div className="actions">
            <button onClick={() => navigator.clipboard.writeText(transcript)}>Copy</button>
            <button
              onClick={() => {
                setTranscript("");
                setStatus("idle");
                setIslView({ kannada: "", english: "", isl: null });
                setIslError("");
              }}
            >
              Clear
            </button>
          </div>
        )}
      </section>

      <IslPanel
        kannada={islView.kannada}
        english={islView.english}
        isl={islView.isl}
        loading={islLoading}
        error={islError}
      />

      <section className="card tester">
        <h2>Try an English sentence</h2>
        <p className="muted small">Test the ISL rules without speaking (no Sarvam key needed).</p>
        <form onSubmit={runEnglish} className="tester-form">
          <input
            value={englishInput}
            onChange={(e) => setEnglishInput(e.target.value)}
            placeholder="e.g. Where can I get a passport?"
            maxLength={500}
          />
          <button type="submit" disabled={islLoading || !englishInput.trim()}>
            Convert
          </button>
        </form>
      </section>

      <footer>Next: gloss sequence → 3D avatar animation</footer>
    </main>
  );
}
