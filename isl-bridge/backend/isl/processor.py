"""
ISL linguistic processing: English sentence -> ISL gloss sequence.

Pipeline (matches the architecture diagram, step 4):
  1. Understand   spaCy parses the sentence (dependencies, lemmas, named entities).
  2. Tokenize + remove extra words
                  articles, auxiliary/copula verbs, most prepositions, conjunctions,
                  punctuation and the words in rules.json "drop_lemmas" are removed.
                  Every removed word is recorded with a reason (see result["dropped"]).
  3. ISL grammar  clause order  [time] [subject] [objects] [adverbs] [verb] [modal] [NOT]
                  question words at the start or end (rules.json), negation after the verb,
                  tense carried by time words, names fingerspelled, non-manual markers noted.
  4. Sign sequence each gloss is looked up in lexicon.json; the result says whether an
                  animation exists, so the website can handle missing signs.

IMPORTANT: the grammar rules are editable assumptions (rules.json). They are NOT a
substitute for ISL reference material or a fluent signer. Glosses are labels only.
"""
import json
import re
import threading
from pathlib import Path

HERE = Path(__file__).parent

WH_CAT = "wh"
TIME_ENTS = {"DATE", "TIME"}
NAME_ENTS = {"PERSON", "GPE", "ORG", "LOC", "FAC", "NORP"}
POS_DROP = {
    "DET": "article/determiner",
    "ADP": "preposition",
    "AUX": "auxiliary verb",
    "CCONJ": "conjunction",
    "SCONJ": "subordinator",
    "PART": "particle",
    "SYM": "symbol",
}
FUTURE_LEMMAS = {"will", "shall"}


def sanitize(gloss: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", gloss.upper()).strip("_")


class Lexicon:
    """English words / phrases -> gloss, loaded from lexicon.json."""

    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.signs = data["signs"]
        self.by_word = {}
        self.phrases = {}  # phrase -> gloss (2+ words)
        for gloss, e in self.signs.items():
            for w in e["words"]:
                w = w.lower()
                self.by_word[w] = gloss
                if " " in w:
                    self.phrases[w] = gloss
        self.max_phrase = max([len(p.split()) for p in self.phrases] or [1])

    def cat(self, gloss):
        return self.signs[gloss].get("cat")


class ISLProcessor:
    def __init__(self, model="en_core_web_sm", lexicon_path=None, rules_path=None):
        import spacy

        try:
            self.nlp = spacy.load(model)
        except OSError as exc:
            raise RuntimeError(
                f"spaCy model '{model}' is not installed. Run:  python -m spacy download {model}"
            ) from exc
        self.lex = Lexicon(lexicon_path or HERE / "lexicon.json")
        self.rules = json.loads(Path(rules_path or HERE / "rules.json").read_text(encoding="utf-8"))
        self._lock = threading.Lock()

    # ------------------------------------------------------------ lookups
    def lookup(self, tok):
        """Gloss for a token (exact word first, then lemma), or None."""
        for key in (tok.lower_, tok.lemma_.lower()):
            g = self.lex.by_word.get(key)
            if g:
                nt = self.lex.signs[g].get("not_tags")
                if nt and tok.tag_ in nt:
                    return None
                return g
        return None

    def is_wh(self, tok):
        g = self.lookup(tok)
        return bool(g and self.lex.cat(g) == WH_CAT)

    def is_time(self, tok):
        if tok.ent_type_ in TIME_ENTS:
            return True
        g = self.lookup(tok)
        return bool(g and self.lex.cat(g) == "time")

    def subtree_is_time(self, tok):
        return any(self.is_time(t) for t in tok.subtree)

    # ------------------------------------------------------------ units
    def sign_unit(self, gloss, tok, ctx):
        e = self.lex.signs[gloss]
        kind = "number" if e.get("cat") == "number" else "sign"
        return {"gloss": gloss, "kind": kind, "source": tok.text if tok is not None else gloss.lower()}

    def fingerspell_unit(self, text):
        # ISL fingerspelling here covers Latin letters and digits only.
        letters = [c for c in text.upper() if c.isascii() and c.isalnum()]
        if not letters:
            return {"gloss": text.upper(), "kind": "missing", "source": text,
                    "note": "cannot be fingerspelled (not Latin letters). Is the input English?"}
        return {"gloss": "FS:" + "".join(letters), "kind": "fingerspell", "letters": letters, "source": text}

    def gloss_tok(self, tok, ctx):
        """One token -> unit dict, or None (and a record of why it was dropped)."""
        if tok.is_punct or tok.is_space:
            return None
        lemma = tok.lemma_.lower()
        if lemma in self.rules["drop_lemmas"] or tok.lower_ in self.rules["drop_lemmas"]:
            ctx["dropped"].append({"word": tok.text, "reason": "extra word (rules.drop_lemmas)"})
            return None
        g = self.lookup(tok)
        if g:
            return self.sign_unit(g, tok, ctx)
        if tok.lower_ in self.rules["keep_prepositions"] and tok.pos_ == "ADP":
            return {"gloss": tok.lower_.upper(), "kind": "sign", "source": tok.text}
        if tok.like_num or (tok.pos_ == "NUM" and any(c.isdigit() for c in tok.text)):
            digits = [c for c in tok.text if c.isdigit()]
            if digits:
                return {"gloss": "".join(digits), "kind": "number", "digits": digits, "source": tok.text}
        if tok.pos_ == "PROPN" or tok.ent_type_ in NAME_ENTS:
            return self.fingerspell_unit(tok.text)
        if tok.pos_ in POS_DROP:
            ctx["dropped"].append({"word": tok.text, "reason": POS_DROP[tok.pos_]})
            return None
        if tok.pos_ == "PRON":
            ctx["dropped"].append({"word": tok.text, "reason": "pronoun with no sign mapped"})
            return None
        # content word with no lexicon entry
        # nouns can be fingerspelled; unknown verbs/adjectives/adverbs are flagged as missing signs
        policy = self.rules["unknown_word"] if tok.pos_ == "NOUN" else self.rules.get("unknown_other", "flag")
        word = tok.lemma_ if tok.pos_ in ("NOUN", "VERB", "ADJ") else tok.text
        if policy == "skip":
            ctx["dropped"].append({"word": tok.text, "reason": "no sign in lexicon (skipped)"})
            return None
        if policy == "flag":
            return {"gloss": word.upper(), "kind": "missing", "source": tok.text}
        u = self.fingerspell_unit(word)
        u["note"] = "no sign in lexicon: fingerspelled"
        return u

    # ------------------------------------------------------------ noun phrases
    def np_units(self, head, ctx):
        """Units of a phrase in English left-to-right order. Returns (units, contains_wh)."""
        wh = False
        units = []
        for t in head.subtree:
            if self.is_wh(t):
                wh = True
            u = self.gloss_tok(t, ctx)
            if u:
                units.append(u)
        return units, wh

    # ------------------------------------------------------------ clauses
    def build_clause(self, verb, ctx, inherited_subject=None, hoist_time=False):
        """Returns (units, slots). With hoist_time=True the clause's time units are left out of
        `units` (they are in slots['time']) so the parent clause can move them to its front."""
        S = {k: [] for k in ("pre", "wh", "time", "subject", "obj", "adv", "verb", "marker", "modal", "neg")}
        extra_clauses = []
        future = False

        def put(slot, units_wh):
            units, wh = units_wh
            S["wh" if wh else slot].extend(units)

        for c in verb.children:
            d = c.dep_
            if d == "punct":
                continue
            if d in ("aux", "auxpass"):
                lem = c.lemma_.lower()
                if c.tag_ == "MD" and lem in FUTURE_LEMMAS:
                    future = True
                    ctx["dropped"].append({"word": c.text, "reason": "future auxiliary (tense via time words)"})
                elif c.tag_ == "MD":
                    g = self.lookup(c)
                    if g:
                        S["modal"].append(self.sign_unit(g, c, ctx))
                    else:
                        ctx["dropped"].append({"word": c.text, "reason": "modal with no sign mapped"})
                else:
                    ctx["dropped"].append({"word": c.text, "reason": "auxiliary verb"})
            elif d == "neg":
                u = self.gloss_tok(c, ctx) or {"gloss": "NOT", "kind": "sign", "source": c.text}
                S["neg"].append(u)
            elif d in ("nsubj", "csubj"):
                put("subject", self.np_units(c, ctx))
            elif d == "nsubjpass":
                put("obj", self.np_units(c, ctx))
            elif d == "agent":
                for p in c.children:
                    if p.dep_ == "pobj":
                        put("subject", self.np_units(p, ctx))
                ctx["dropped"].append({"word": c.text, "reason": "preposition"})
            elif d == "expl":
                ctx["dropped"].append({"word": c.text, "reason": "dummy subject"})
            elif d == "cc":
                ctx["dropped"].append({"word": c.text, "reason": "conjunction"})
            elif d == "prt":
                ctx["dropped"].append({"word": c.text, "reason": "verb particle"})
            elif d == "conj" and c.pos_ in ("VERB", "AUX"):
                extra_clauses.append(c)
            elif d in ("xcomp", "ccomp"):
                sub, subS = self.build_clause(c, ctx, hoist_time=True)
                S["obj"].extend(sub)
                S["time"].extend(subS["time"])  # "want to pay ... tomorrow": TOMORROW moves to the front
            elif d == "advcl":
                sub, _ = self.build_clause(c, ctx)
                S["pre"].extend(sub)  # subordinate (if/because/when...) clause goes first
            elif d == "mark":
                u = self.gloss_tok(c, ctx)
                if u:
                    S["pre"].insert(0, u)
            elif d == "prep":
                if self.subtree_is_time(c):
                    for p in c.children:
                        if p.dep_ == "pobj":
                            units, _ = self.np_units(p, ctx)
                            S["time"].extend(units)
                    ctx["dropped"].append({"word": c.text, "reason": "preposition"})
                else:
                    kept = self.gloss_tok(c, ctx)  # kept only if in keep_prepositions / lexicon, else recorded as dropped
                    if kept:
                        S["obj"].append(kept)
                    for p in c.children:
                        if p.dep_ in ("pobj", "pcomp"):
                            put("obj", self.np_units(p, ctx))
            elif d == "advmod":
                if self.is_wh(c):
                    u = self.gloss_tok(c, ctx)
                    if u:
                        S["wh"].append(u)
                elif self.is_time(c):
                    u = self.gloss_tok(c, ctx)
                    if u:
                        S["time"].append(u)
                else:
                    u = self.gloss_tok(c, ctx)
                    if u:
                        (S["neg"] if u["gloss"] == "NOT" else S["adv"]).append(u)
            elif d == "npadvmod":
                units, wh = self.np_units(c, ctx)
                S["wh" if wh else ("time" if self.subtree_is_time(c) else "obj")].extend(units)
            elif d == "intj":
                u = self.gloss_tok(c, ctx)
                if u:
                    S["pre"].append(u)
            else:  # dobj, dative, attr, acomp, oprd, appos, ...
                put("obj", self.np_units(c, ctx))

        # the verb itself
        copula = verb.lemma_.lower() == "be" and verb.pos_ in ("AUX", "VERB")
        if copula:
            ctx["dropped"].append({"word": verb.text, "reason": "copula 'be' (not signed)"})
        else:
            u = self.gloss_tok(verb, ctx)
            if u:
                S["verb"].append(u)
                past = verb.tag_ == "VBD" or (verb.tag_ == "VBN" and any(
                    a.dep_ == "aux" and a.lemma_.lower() == "have" for a in verb.children))
                if past and self.rules["past_marker"] and not S["time"]:
                    S["marker"].append({"gloss": self.rules["past_marker"], "kind": "sign", "source": "(past tense)"})
        if future and self.rules["future_marker"] and not S["time"]:
            S["marker"].append({"gloss": self.rules["future_marker"], "kind": "sign", "source": "(future)"})

        if not S["subject"] and inherited_subject:
            S["subject"] = list(inherited_subject)

        units = list(S["pre"])
        if self.rules["wh_position"] == "start":
            units += S["wh"]
        if not hoist_time and self.rules["time_first"]:
            units += S["time"]
        units += S["subject"]
        units += S["obj"]
        if not hoist_time and not self.rules["time_first"]:
            units += S["time"]
        units += S["adv"] + S["verb"] + S["marker"] + S["modal"] + S["neg"]
        if self.rules["wh_position"] == "end":
            units += S["wh"]
        for cv in extra_clauses:
            sub, _ = self.build_clause(cv, ctx, inherited_subject=S["subject"])
            units += sub
        return units, S

    # ------------------------------------------------------------ sentence
    def sentence_type(self, sent):
        text = sent.text.strip()
        wh_present = any(self.is_wh(t) and t.dep_ != "mark" for t in sent)
        first = sent[0]
        if wh_present and (text.endswith("?") or self.is_wh(first)):
            return "wh_question"
        if text.endswith("?") or first.pos_ == "AUX" or first.tag_ == "MD":
            if text.endswith("?") or any(t.dep_ in ("nsubj", "nsubjpass") for t in sent.root.children):
                return "yes_no_question"
        root = sent.root
        if root.tag_ == "VB" and not any(t.dep_ in ("nsubj", "nsubjpass", "expl") for t in root.children):
            return "imperative"
        return "statement"

    def process_sentence(self, sent, ctx):
        stype = self.sentence_type(sent)
        root = sent.root
        if root.pos_ in ("VERB", "AUX"):
            units, S = self.build_clause(root, ctx)
        else:  # fragment ("Thank you", "Hello", "Ticket price?")
            units, wh = self.np_units(root, ctx)
            S = {"subject": [], "obj": units if not wh else [], "wh": units if wh else [],
                 "time": [], "verb": [], "neg": []}
            if wh and self.rules["wh_position"] == "start":
                pass
        negated = bool(S.get("neg"))
        g = lambda key: [u["gloss"] for u in S.get(key, [])]
        analysis = {
            "type": stype,
            "main_verb": root.lemma_.lower() if root.pos_ in ("VERB", "AUX") else None,
            "subject": g("subject"), "object": g("obj"), "time": g("time"),
            "question_word": g("wh"), "negated": negated,
            "entities": [{"text": e.text, "label": e.label_} for e in sent.ents],
        }
        nmm = {
            "question": {"wh_question": "wh", "yes_no_question": "yes_no"}.get(stype),
            "negation_headshake": negated,
            "note": "Non-manual markers (eyebrows, headshake) are passed to the avatar in the facial-expression phase; "
                    "their exact form should be verified against ISL reference.",
        }
        return units, analysis, nmm

    # ------------------------------------------------------------ public API
    def merge_phrases(self, doc):
        """Merge multi-word lexicon entries (how much, thank you, ID card...) into single tokens."""
        spans, taken = [], set()
        n = len(doc)
        for i in range(n):
            if i in taken:
                continue
            for size in range(min(self.lex.max_phrase, n - i), 1, -1):
                window = doc[i:i + size]
                cand = {" ".join(t.lower_ for t in window), " ".join(t.lemma_.lower() for t in window)}
                hit = next((c for c in cand if c in self.lex.phrases), None)
                if hit:
                    spans.append((window, hit))
                    taken.update(range(i, i + size))
                    break
        if spans:
            with doc.retokenize() as rt:
                for span, phrase in spans:
                    rt.merge(span, attrs={"LEMMA": phrase})
        return doc

    def finalize(self, unit):
        """Attach animation info so the website knows what can be played."""
        kind = unit["kind"]
        if kind == "sign":
            e = self.lex.signs.get(unit["gloss"], {})
            unit["animation"] = e.get("animation") or "ISL_" + sanitize(unit["gloss"])
            unit["status"] = e.get("status", "unverified")
            unit["available"] = unit["status"] == "animated"
        elif kind == "number":
            e = self.lex.signs.get(unit["gloss"], {})
            digits = unit.get("digits") or [unit["gloss"]]
            unit["animations"] = ["ISL_NUM_" + d for d in digits]
            unit["status"] = e.get("status", "unverified")
            unit["available"] = unit["status"] == "animated"
        elif kind == "fingerspell":
            unit["animations"] = ["ISL_FS_" + sanitize(c) for c in unit["letters"]]
            unit["status"] = "unverified"
            unit["available"] = False
        else:  # missing
            unit["animation"] = None
            unit["status"] = "missing"
            unit["available"] = False
        return unit

    def process(self, text):
        text = (text or "").strip()
        if not text:
            return {"input": "", "gloss": [], "gloss_string": "", "signs": [], "sentences": [],
                    "dropped": [], "missing": [], "unavailable": [], "warnings": []}
        text = text[: self.rules["max_chars"]]
        warnings = []
        if not re.search(r"[A-Za-z]", text):
            warnings.append("Input has no English letters. Translate Kannada to English first.")
        with self._lock:  # spaCy retokenize on a shared pipeline is safe, but keep results deterministic
            doc = self.nlp(text)
            self.merge_phrases(doc)
            ctx = {"dropped": []}
            sentences, all_units = [], []
            for sent in doc.sents:
                units, analysis, nmm = self.process_sentence(sent, ctx)
                units = [self.finalize(u) for u in units]
                sentences.append({"english": sent.text.strip(), "gloss": [u["gloss"] for u in units],
                                  "analysis": analysis, "nmm": nmm})
                all_units += units
        gloss = [u["gloss"] for u in all_units]
        return {
            "input": text,
            "gloss": gloss,
            "gloss_string": " ".join(gloss),
            "signs": all_units,
            "sentences": sentences,
            "dropped": ctx["dropped"],
            # no entry in lexicon.json at all (needs a new sign)
            "missing": [u["gloss"] for u in all_units if u["kind"] == "missing"],
            # in the lexicon but no finished animation yet (status != "animated")
            "unavailable": [u["gloss"] for u in all_units if not u["available"] and u["kind"] != "missing"],
            "warnings": warnings,
        }
