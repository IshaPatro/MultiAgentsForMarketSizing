"""System-1 client for TypeSafe's JEV (System One API): a classifier, not a text generator.

JEV answers typed questions about a "state" (choice, score, yes/no "noul") with calibrated probabilities. It cannot write
claims or arguments, so it serves two classification tasks here: the moderator's choice of which executive answers the VC's
question, and the first-pass fact-check verdict. Each claim becomes one verdict request:
a `choice` question for the verdict and one `noul` question per evidence chunk ("is this chunk about the claim?"), which
decides the chunks that get cited. Claim extraction stays on a generative model.
"""
from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel

from debate.config import TierConfig
from debate.llm.client import LLMError
from debate.routing import EXEC_ROLES
from debate.state import ClaimVerdict, RouteChoice, Usage, VerdictBatch

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
INPUT_USD_PER_M = 0.042   # JEV bills input tokens only; output tokens are free
CITE_THRESHOLD = 0.5  # a chunk is cited when JEV puts at least this probability on "this chunk is about the claim"

VERDICT_CRITERIA = {
    "supported": "The evidence states the claim or clearly implies it.",
    "contradicted": "The evidence states something incompatible with the claim: a different number, date or fact. "
                    "An exact date in the evidence for the same event makes a different asserted date contradicted.",
    "unverifiable": "The evidence does not cover the claim. Missing evidence is NOT a contradiction.",
}

_ITEM = re.compile(r"^(C\d+) \((?P<who>[^)]*)\): (?P<claim>.*?)\nEVIDENCE:\n(?P<evidence>.*?)(?=\n\nC\d+ \(|\Z)", re.S | re.M)
_CHUNK = re.compile(r"^ {2}\[([^\]\s]+)\] (.*?)(?=\n {2}\[[^\]\s]+\] |\Z)", re.S | re.M)


def parse_claims(user: str) -> list[dict]:
    """Invert `factcheck._prompt`: [{ref, speaker, claim, chunks: [(id, text)]}]."""
    items = []
    for m in _ITEM.finditer(user):
        chunks = [(cid, text.strip()) for cid, text in _CHUNK.findall(m["evidence"])]
        items.append({"ref": m.group(1), "speaker": m["who"], "claim": m["claim"].strip(), "chunks": chunks})
    return items


def build_request(model: str, item: dict) -> dict:
    evidence = "\n".join(f"[{cid}] {text}" for cid, text in item["chunks"]) or "(no evidence found)"
    questions = {"covered": {"type": "noul",
                             "instructions": "Does the evidence contain the information needed to check this claim, i.e. "
                                             "does it directly confirm or contradict it? Answer no if the evidence is "
                                             "only loosely related or silent on the claim."},
                 "verdict": {"type": "choice", "criteria": VERDICT_CRITERIA,
                             "instructions": "Judge ONLY against the evidence below: does it support, contradict or not "
                                             "cover the claim?"}}
    for n, (cid, _) in enumerate(item["chunks"]):
        questions[f"e{n}"] = {"type": "noul",
                              "instructions": f"Is evidence chunk [{cid}] directly about the same fact as the claim, "
                                              f"so that it either confirms or contradicts it?"}
    return {"model": model, "state": f"CLAIM ({item['speaker']}): {item['claim']}\n\nEVIDENCE:\n{evidence}",
            "questions": questions}


def build_route_request(model: str, question: str) -> dict:
    return {"model": model, "state": f"VC question. {question}",
            "questions": {"executive": {"type": "choice", "criteria": EXEC_ROLES,
                                        "instructions": "Which executive is best placed to answer this question?"}}}


class JevClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_URL, opener=urlopen):
        self.api_key, self.url, self.opener = api_key, base_url, opener

    def _post(self, payload: dict) -> dict:
        request = Request(self.url, data=json.dumps(payload).encode(), method="POST",
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with self.opener(request, timeout=60) as response:
            return json.load(response)

    def _route(self, role: str, model: str, question: str, started: float) -> tuple[RouteChoice, Usage]:
        try:
            data = self._post(build_route_request(model, question))
            answer = data["answers"]["executive"]
            usage = Usage(input_tokens=int(data["usage"]["input_tokens"]), output_tokens=int(data["usage"]["output_tokens"]),
                          latency_ms=int((time.monotonic() - started) * 1000), cost_usd=0.0)
            usage.cost_usd = usage.input_tokens * INPUT_USD_PER_M / 1e6
            return RouteChoice(executive=answer["choice"], confidence=float(answer["confidence"]),
                               reason=f"JEV {data.get('model', model)}"), usage
        except (HTTPError, URLError, OSError, KeyError, TypeError, ValueError) as exc:
            raise LLMError(f"{role}: JEV routing request failed: {exc}") from exc

    def generate(self, *, role: str, tier: TierConfig, system: str, user: str,
                 schema: type[BaseModel]) -> tuple[BaseModel, Usage]:
        if schema not in (VerdictBatch, RouteChoice):
            raise ValueError("JEV classifies; it can only produce fact-check verdicts (VerdictBatch) or routing choices (RouteChoice)")
        model = tier.model.removeprefix("jev:")
        started = time.monotonic()
        if schema is RouteChoice:
            return self._route(role, model, user, started)
        verdicts, input_tokens, output_tokens = [], 0, 0
        for item in parse_claims(user):
            try:
                data = self._post(build_request(model, item))
                answers = data["answers"]
                choice = answers["verdict"]
                input_tokens += int(data["usage"]["input_tokens"])
                output_tokens += int(data["usage"]["output_tokens"])
                covered = float(answers["covered"]["noul"])          # P(the corpus holds what is needed to check it)
                verdict = choice["choice"]
                if verdict == "unverifiable":
                    # Confident only when the evidence is also clearly silent; otherwise the two answers disagree and
                    # the low confidence sends the claim up to the stronger model.
                    confidence, cited = float(choice["confidence"]) * (1 - covered), []
                else:
                    relevance = [(answers[f"e{n}"]["noul"], cid) for n, (cid, _) in enumerate(item["chunks"])]
                    cited = [cid for p, cid in relevance if p >= CITE_THRESHOLD]
                    if not cited and relevance and covered >= 0.5:
                        cited = [max(relevance)[1]]
                    confidence = float(choice["confidence"]) * covered
                verdicts.append(ClaimVerdict(ref=item["ref"], verdict=verdict, confidence=round(confidence, 3),
                                             evidence_ids=cited,
                                             note=f"JEV {data.get('model', model)}: evidence covers it {covered:.0%}"))
            except (HTTPError, URLError, OSError, KeyError, TypeError, ValueError) as exc:
                raise LLMError(f"{role}: JEV request for {item['ref']} failed: {exc}") from exc
        usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens,
                      latency_ms=int((time.monotonic() - started) * 1000),
                      cost_usd=input_tokens * INPUT_USD_PER_M / 1e6)
        return VerdictBatch(verdicts=verdicts), usage
