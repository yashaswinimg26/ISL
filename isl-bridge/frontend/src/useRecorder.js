import { useCallback, useEffect, useRef, useState } from "react";

// Pick a MIME type the current browser can record.
function pickMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  return candidates.find((t) => window.MediaRecorder?.isTypeSupported?.(t)) || "";
}

/**
 * Microphone recording hook.
 * state: "idle" | "recording"
 * stop() resolves with { blob, mimeType, seconds }
 */
export function useRecorder() {
  const [state, setState] = useState("idle");
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0); // 0..1 live mic level

  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);
  const audioCtxRef = useRef(null);
  const rafRef = useRef(null);
  const startedAtRef = useRef(0);

  const cleanup = useCallback(() => {
    clearInterval(timerRef.current);
    cancelAnimationFrame(rafRef.current);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    audioCtxRef.current?.close().catch(() => {});
    streamRef.current = null;
    audioCtxRef.current = null;
    setLevel(0);
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const start = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(
        "Microphone not available. Use https:// or http://localhost and a modern browser."
      );
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    streamRef.current = stream;

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunksRef.current = [];
    recorder.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data);
    recorderRef.current = recorder;

    // Live level meter
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(analyser);
    audioCtxRef.current = ctx;
    const buf = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      setLevel(Math.min(1, peak / 64));
      rafRef.current = requestAnimationFrame(tick);
    };
    tick();

    recorder.start();
    startedAtRef.current = Date.now();
    setSeconds(0);
    timerRef.current = setInterval(
      () => setSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000)),
      250
    );
    setState("recording");
  }, []);

  const stop = useCallback(
    () =>
      new Promise((resolve, reject) => {
        const recorder = recorderRef.current;
        if (!recorder || recorder.state === "inactive") {
          return reject(new Error("Not recording."));
        }
        recorder.onstop = () => {
          const type = recorder.mimeType || "audio/webm";
          const blob = new Blob(chunksRef.current, { type });
          const secs = (Date.now() - startedAtRef.current) / 1000;
          cleanup();
          setState("idle");
          resolve({ blob, mimeType: type, seconds: secs });
        };
        recorder.stop();
      }),
    [cleanup]
  );

  return { state, seconds, level, start, stop };
}
