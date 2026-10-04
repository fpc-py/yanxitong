"""Citation chain tracking v2.0 — per-claim evidence extraction + audit trail."""

import re, logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ClaimEvidence:
    claim: str
    source_title: str
    source_url: str = ""
    source_authors: list[str] = field(default_factory=list)
    source_year: int = 0
    excerpt: str = ""
    excerpt_similarity: float = 0.0
    page: str = ""
    confidence: float = 1.0


@dataclass
class CitationChain:
    claims: list[ClaimEvidence] = field(default_factory=list)
    response_text: str = ""

    def add_claim(self, evidence: ClaimEvidence):
        self.claims.append(evidence)

    def to_dict(self) -> dict:
        return {
            "response_text": self.response_text[:500],
            "total_claims": len(self.claims),
            "average_confidence": self.get_average_confidence(),
            "claims": [
                {
                    "claim": c.claim[:200],
                    "source": {"title": c.source_title, "url": c.source_url, "authors": c.source_authors, "year": c.source_year},
                    "evidence": {"excerpt": c.excerpt[:300], "similarity": round(c.excerpt_similarity, 3), "page": c.page},
                    "confidence": round(c.confidence, 3),
                }
                for c in self.claims
            ],
        }

    def get_claim_count(self) -> int:
        return len(self.claims)

    def get_average_confidence(self) -> float:
        if not self.claims: return 0.0
        return sum(c.confidence for c in self.claims) / len(self.claims)

    def get_unverified_claims(self) -> list[ClaimEvidence]:
        return [c for c in self.claims if c.confidence < 0.5]


class CitationTracker:
    @staticmethod
    def build_chain(response: str, source_docs: list[dict], claims: Optional[list[str]] = None) -> CitationChain:
        chain = CitationChain(response_text=response)
        if not source_docs: return chain
        if claims is None: claims = CitationTracker._extract_claims(response)
        for claim in claims:
            ev = CitationTracker._find_best_evidence(claim, source_docs)
            if ev: chain.add_claim(ev)
        return chain

    @staticmethod
    def _extract_claims(text: str) -> list[str]:
        return [s.strip() for s in re.split(r'[.。!！?？\n]', text) if len(s.strip()) > 15]

    @staticmethod
    def _find_best_evidence(claim: str, source_docs: list[dict]) -> Optional[ClaimEvidence]:
        if not source_docs: return None
        best_score, best_doc, best_excerpt = 0.0, None, ""
        cl = claim.lower(); cw = set(cl.split())
        for doc in source_docs:
            dt = " ".join([doc.get("title",""), doc.get("abstract",""), doc.get("text", doc.get("full_text_snippet",""))])
            if not dt: continue
            dl = dt.lower(); dw = set(dl.split())
            if not cw or not dw: continue
            overlap = cw & dw
            score = len(overlap) / len(cw) if cw else 0
            if score > best_score:
                best_score = score; best_doc = doc
                sents = re.split(r'[.。!！?？]', dt)
                bs, be = 0, ""
                for s in sents:
                    sl = s.lower(); so = sum(1 for w in cw if w in sl)
                    if so > bs: bs = so; be = s.strip()
                best_excerpt = be[:300]
        if best_doc and best_score > 0.1:
            return ClaimEvidence(claim=claim, source_title=best_doc.get("title","Unknown"), source_url=best_doc.get("url",""), source_authors=best_doc.get("authors",[]), source_year=best_doc.get("year",0), excerpt=best_excerpt, excerpt_similarity=best_score, confidence=min(1.0, best_score * 1.5))
        return None


_ct = None

def get_citation_tracker() -> CitationTracker:
    global _ct
    if _ct is None: _ct = CitationTracker()
    return _ct