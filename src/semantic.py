"""
Semantic relevance pass.

Keyword rules cannot tell "A Look at U.S. Land-Based Deterrence Posture" from
"Nicaragua's Army Will Decide What Comes After Ortega" - both are defence
essays from the same publisher. Nor can they tell an AI story that is about
rad-hard silicon demand from one about chatbots doing paperwork. That judgement
needs reading, so borderline items are sent to the model in batches.

Design:
  * Only the AMBIGUOUS MIDDLE is adjudicated. Items that scored high on hard
    evidence (big dollar award, multi-source corroboration, tier-1 programme)
    are already unambiguous and skip the call. So does obvious noise below the
    floor. This keeps cost and latency proportional to the doubt, not volume.
  * The model returns a relevance score and a section. Relevance ADJUSTS the
    existing score rather than replacing it, so the deterministic signals
    (dollars, corroboration, milestones) still drive ranking and a model
    failure degrades gracefully instead of scrambling the page.
  * Every failure path returns the input unchanged. No API key, network error,
    bad JSON, missing id - the brief still builds. This runs unattended twice a
    day; it must never be able to break the build.
"""
import json, os, re, urllib.request

MODEL = "claude-sonnet-4-6"
API = "https://api.anthropic.com/v1/messages"


def _call(prompt, max_tokens=2000, timeout=90):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    req = urllib.request.Request(
        API,
        data=json.dumps({
            "model": MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    return "".join(b.get("text", "") for b in body.get("content", [])
                   if b.get("type") == "text")


def _parse_json(text):
    """Models sometimes wrap JSON in prose or fences. Extract the array."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def adjudicate(digest, cfg, log=print):
    """Score borderline items for thesis relevance. Returns digest, always."""
    sem = cfg.get("semantic", {})
    if not sem.get("enabled", True):
        return digest
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("  semantic: no ANTHROPIC_API_KEY, skipping")
        return digest

    lo, hi = sem.get("floor", 4.0), sem.get("ceiling", 20.0)
    batch_size = sem.get("batch_size", 25)
    max_items = sem.get("max_items", 120)

    # Only adjudicate the ambiguous middle.
    candidates = [d for d in digest if lo <= d["score"] <= hi][:max_items]
    if not candidates:
        return digest
    log(f"  semantic: adjudicating {len(candidates)} of {len(digest)} items")

    sections = {k: v["label"] for k, v in cfg["sections"].items()
                if isinstance(v, dict) and not k.startswith("_")}
    thesis = sem.get("thesis", "")
    # Prefer the richer per-section descriptions when configured: bare labels
    # led the model to treat "Analysis & Research" as a catch-all.
    desc = sem.get("section_guide", {})
    guide = "\n".join(f"  {k} ({sections[k]}): {desc.get(k, '')}".rstrip(": ")
                      for k in sections)

    by_id = {d["id"]: d for d in digest}
    adjusted = 0
    for i in range(0, len(candidates), batch_size):
        chunk = candidates[i:i + batch_size]
        listing = "\n".join(
            f'{n+1}. id={d["id"]} | {d["title"]} | {d["summary"][:180]}'
            for n, d in enumerate(chunk))
        prompt = f"""{thesis}

Rate each item below for relevance to that investment thesis, and assign the best section.

Sections:
{guide}

Relevance scale:
  3 = directly about the thesis (sub-tier suppliers, qualification barriers, industrial
      capacity, nuclear enterprise programmes, specialty materials, components)
  2 = useful context (programme decisions, budgets, primes, end-market demand that
      drives component demand)
  1 = tangential (general defence news, allied/foreign policy, technology applications
      with no hardware supply implication)
  0 = irrelevant (geopolitics, personnel, ceremonies, commentary with no industrial content)

Return ONLY a JSON array, no prose:
[{{"id":"<id>","relevance":<0-3>,"section":"<section key>"}}]

Items:
{listing}"""
        try:
            out = _parse_json(_call(prompt))
            if not out:
                log("  semantic: unparseable response for a batch, leaving unchanged")
                continue
            for row in out:
                d = by_id.get(str(row.get("id", "")))
                if not d:
                    continue
                rel = row.get("relevance")
                if isinstance(rel, (int, float)):
                    delta = sem.get("deltas", {}).get(str(int(rel)), 0)
                    d["score"] = round(d["score"] + delta, 2)
                    d["relevance"] = int(rel)
                    d["why"] = d.get("why", []) + [f"relevance {int(rel)} {delta:+g}"]
                    adjusted += 1
                sec = row.get("section")
                if sec in sections and sec != d["section"]:
                    if d.get("_locked"):
                        d["why"] = d.get("why", []) + [f"kept {d['section']} (locked)"]
                    else:
                        d["why"] = d.get("why", []) + [f"re-routed {d['section']}->{sec}"]
                        d["section"] = sec
        except Exception as ex:  # noqa: BLE001
            log(f"  semantic: batch failed ({type(ex).__name__}), leaving unchanged")
            continue

    log(f"  semantic: adjusted {adjusted} items")
    digest.sort(key=lambda x: x["score"], reverse=True)
    return digest
