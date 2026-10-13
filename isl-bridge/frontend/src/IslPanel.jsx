// Shows the result of the ISL linguistic processing: sign sequence, removed words, analysis.

const QUESTION_LABEL = { wh: "question word (wh)", yes_no: "yes/no question" };
const TYPE_LABEL = {
  wh_question: "wh-question",
  yes_no_question: "yes/no question",
  imperative: "request / command",
  statement: "statement",
};

function chipInfo(s) {
  if (s.kind === "missing") return { cls: "missing", note: "no sign in lexicon yet" };
  if (s.kind === "fingerspell") return { cls: "fs", note: "fingerspelled" };
  if (s.available) return { cls: "ready", note: "animation ready" };
  return { cls: "unavail", note: `no animation yet (${s.status})` };
}

function chipText(s) {
  if (s.kind === "fingerspell") return s.letters.join("-");
  return s.gloss;
}

export default function IslPanel({ english, kannada, isl, loading, error }) {
  if (!loading && !error && !isl) return null;

  return (
    <section className="card isl">
      <h2>ISL sign sequence</h2>

      {loading && <p className="muted">Translating and processing…</p>}
      {error && <div className="error">{error}</div>}

      {isl && (
        <>
          {kannada && (
            <p className="row" lang="kn">
              <span className="label">Kannada</span> {kannada}
            </p>
          )}
          {english && (
            <p className="row">
              <span className="label">English</span> {english}
            </p>
          )}

          {isl.warnings?.map((w) => (
            <div className="error" key={w}>
              {w}
            </div>
          ))}

          <div className="chips" aria-label="ISL sign sequence">
            {isl.signs.length === 0 && <span className="muted">No signs produced.</span>}
            {isl.signs.map((s, i) => {
              const { cls, note } = chipInfo(s);
              return (
                <span key={i} className={`chip ${cls}`} title={`${s.animation || s.animations?.join(", ") || "—"} · ${note}`}>
                  <span className="n">{i + 1}</span>
                  {chipText(s)}
                </span>
              );
            })}
          </div>

          <div className="legend">
            <span className="chip ready">animation ready</span>
            <span className="chip unavail">sign mapped, no animation yet</span>
            <span className="chip fs">fingerspelled</span>
            <span className="chip missing">no sign in lexicon</span>
          </div>

          {isl.sentences.map((s, i) => (
            <div className="sentence" key={i}>
              <div className="badges">
                <span className="badge">{TYPE_LABEL[s.analysis.type] || s.analysis.type}</span>
                {s.nmm.question && <span className="badge">face: {QUESTION_LABEL[s.nmm.question]}</span>}
                {s.nmm.negation_headshake && <span className="badge">face: headshake</span>}
              </div>
              <div className="sentence-text">{s.english}</div>
              <div className="sentence-gloss">{s.gloss.join(" → ") || "—"}</div>
            </div>
          ))}

          <details>
            <summary>Words removed ({isl.dropped.length})</summary>
            {isl.dropped.length === 0 ? (
              <p className="muted">Nothing removed.</p>
            ) : (
              <ul>
                {isl.dropped.map((d, i) => (
                  <li key={i}>
                    <b>{d.word}</b> — {d.reason}
                  </li>
                ))}
              </ul>
            )}
          </details>

          <details>
            <summary>What was understood</summary>
            {isl.sentences.map((s, i) => {
              const a = s.analysis;
              return (
                <ul key={i}>
                  <li>Type: {TYPE_LABEL[a.type] || a.type}</li>
                  {a.main_verb && <li>Main verb: {a.main_verb}</li>}
                  {a.subject.length > 0 && <li>Subject: {a.subject.join(" ")}</li>}
                  {a.object.length > 0 && <li>Object / complement: {a.object.join(" ")}</li>}
                  {a.time.length > 0 && <li>Time: {a.time.join(" ")}</li>}
                  {a.question_word.length > 0 && <li>Question word: {a.question_word.join(" ")}</li>}
                  <li>Negated: {a.negated ? "yes" : "no"}</li>
                  {a.entities.length > 0 && <li>Names/entities: {a.entities.map((e) => `${e.text} (${e.label})`).join(", ")}</li>}
                </ul>
              );
            })}
          </details>

          <p className="muted small">
            Glosses are labels for the sign database. Word order follows the editable rules in <code>backend/isl/rules.json</code>{" "}
            and must be confirmed with ISL reference material or a signer.
          </p>
        </>
      )}
    </section>
  );
}
