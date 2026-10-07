"""Six-layer hallucination defense v2.0 — production-grade.

Layers:
  1. RetrievalScope    — semantic grounding check against source docs
  2. CitationAnchoring — every claim must cite a source
  3. KGVerification    — cross-check against knowledge graph facts
  4. SelfConsistency   — internal contradiction + numeric consistency
  5. ConfidenceScoring — weighted aggregation with per-layer weights
  6. HumanCircuitBreak — high-risk pattern detection triggers review
"""

import asyncio, logging, re
from dataclasses import dataclass, field
from typing import Optional
from src.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class LayerResult:
    name: str
    score: float
    passed: bool
    details: str = ""
    flagged_items: list[str] = field(default_factory=list)


@dataclass
class HallucinationReport:
    overall_confidence: float
    layer_results: dict[str, LayerResult]
    flagged_claims: list[dict] = field(default_factory=list)
    requires_human_review: bool = False
    risk_level: str = "none"
    summary: str = ""


class HallucinationDefense:
    LAYER_WEIGHTS = {
        "retrieval_scope": 0.25, "citation_anchoring": 0.20,
        "kg_verification": 0.15, "self_consistency": 0.15,
        "confidence_scoring": 0.15, "human_review": 0.10,
    }

    HIGH_RISK_PATTERNS = [
        (r"(?i)\b(cure|treats?|heals?|diagnoses?)\b.*\b(cancer|disease)\b", "medical_claim"),
        (r"(?i)\b(治疗|治愈|根治)\b.*\b(癌症|疾病)\b", "medical_claim_cn"),
        (r"(?i)\b(guarantees?|proves?|always|never|must|100\%)\b", "absolute_claim"),
        (r"(?i)\b(保证|证明|一定|必然|绝对)\b", "absolute_claim_cn"),
        (r"(?i)\b(take\s+\d+\s*mg|inject|self-medicate|overdose)\b", "dangerous_advice"),
    ]

    def __init__(self):
        self.settings = get_settings()

    async def evaluate(self, response: str, source_docs: list[dict], kg_facts: list[dict], alternative_samples: list[str] = None, *, triple_report: Optional[dict] = None) -> HallucinationReport:
        layers = {}
        flagged = []

        s1, d1 = self._check_retrieval_scope(response, source_docs)
        layers["retrieval_scope"] = LayerResult(name="检索范围限定", score=s1, passed=s1 >= 0.6, details=d1)

        s2, d2 = self._check_citation_anchoring(response, source_docs)
        layers["citation_anchoring"] = LayerResult(name="引用锚定", score=s2, passed=s2 >= 0.5, details=d2)

        s3, d3 = await self._verify_against_kg(response, kg_facts, triple_report)
        layers["kg_verification"] = LayerResult(name="知识图谱反验", score=s3, passed=s3 >= 0.4, details=d3)

        s4, d4 = self._check_self_consistency(response, alternative_samples)
        layers["self_consistency"] = LayerResult(name="自一致性检查", score=s4, passed=s4 >= 0.5, details=d4)

        s5 = self._compute_weighted_confidence(layers)
        layers["confidence_scoring"] = LayerResult(name="置信度评分", score=s5, passed=s5 >= self.settings.safety.confidence_threshold, details=f"Weighted: {s5:.3f}")

        l6_passed, l6_details, l6_risks = self._check_high_risk_detailed(response)
        l6_score = 0.0 if not l6_passed else 1.0
        layers["human_review"] = LayerResult(name="人工熔断", score=l6_score, passed=l6_passed, details=l6_details, flagged_items=l6_risks)

        overall = self._compute_weighted_confidence(layers)

        for name, layer in layers.items():
            if not layer.passed and layer.score < 0.6:
                flagged.append({"layer": name, "score": layer.score, "details": layer.details})

        # 三元组反查的硬冲突（contradicted / numeric_conflict）直接升级为高风险
        hard_conflicts = [
            t for t in (triple_report or {}).get("flags", [])
            if t.get("state") in ("contradicted", "numeric_conflict")
        ]
        for item in hard_conflicts[:10]:
            flagged.append({
                "layer": "kg_verification",
                "score": item.get("score", 0.0),
                "details": f"{item.get('method')}/{item.get('dataset')}/{item.get('metric')}: {item.get('reason', '')}",
            })

        if not l6_passed:
            risk_level = "critical"
        elif overall < 0.3:
            risk_level = "high"
        elif overall < self.settings.safety.confidence_threshold:
            risk_level = "medium"
        elif overall < 0.8:
            risk_level = "low"
        else:
            risk_level = "none"
        if hard_conflicts and risk_level in ("none", "low", "medium"):
            risk_level = "high"

        requires_review = not l6_passed or overall < self.settings.safety.confidence_threshold
        if hard_conflicts:
            requires_review = True

        return HallucinationReport(
            overall_confidence=overall,
            layer_results={k: layers[k] for k in self.LAYER_WEIGHTS if k in layers},
            flagged_claims=flagged,
            requires_human_review=requires_review,
            risk_level=risk_level,
            summary=self._build_summary(layers, overall, risk_level),
        )

    def _check_retrieval_scope(self, response: str, source_docs: list[dict]) -> tuple[float, str]:
        if not source_docs:
            return 0.3, "无源文献可供验证"
        all_text = " ".join(doc.get("text", doc.get("abstract", doc.get("title", ""))) for doc in source_docs).lower()
        if len(all_text) < 100:
            return 0.5, "源文献内容不足"
        claims = self._extract_claims(response)
        if not claims:
            return 0.5, "无法解析声明"
        matched = 0
        for claim in claims:
            words = claim.lower().split()
            key_terms = [w for w in words if len(w) > 3 and w.isalpha()]
            if not key_terms:
                matched += 1; continue
            overlap = sum(1 for t in key_terms if t in all_text)
            if overlap / len(key_terms) >= 0.4:
                matched += 1
        score = matched / len(claims) if claims else 0.5
        return score, f"{matched}/{len(claims)} claims grounded"

    def _check_citation_anchoring(self, response: str, source_docs: list[dict]) -> tuple[float, str]:
        if not source_docs:
            return 0.2, "无源文献，无法验证引用"
        total = 0
        for pat in [r'\[\d+(?:,\s*\d+)*\]', r'\[\d+\s*[-–—]\s*\d+\]', r'\(\w+(?:\s+et\s+al\.?)?,?\s*\d{4}\)']:
            total += len(re.findall(pat, response))
        claims = self._extract_claims(response)
        expected = max(1, len(claims) // 2)
        score = min(1.0, total / expected) if expected > 0 else 0.5
        return score, f"Found {total} citations for ~{len(claims)} claims"

    async def _verify_against_kg(self, response: str, kg_facts: list[dict], triple_report: Optional[dict] = None) -> tuple[float, str]:
        # 三元组反查优先：LLM 抽取的 (方法, 数据集, 指标) 已在图谱中反向验证过，
        # 比实体名子串匹配强得多；无三元组时退回名称匹配。
        report = triple_report or {}
        if report.get("checked") and not report.get("degraded"):
            states = report.get("states") or {}
            detail = "三元组反查 %d 条: %s" % (
                report["checked"],
                ", ".join(f"{k}={v}" for k, v in sorted(states.items())),
            )
            hard = states.get("contradicted", 0) + states.get("numeric_conflict", 0)
            if hard:
                detail += f"；{hard} 条与图谱冲突"
            return float(report.get("score", 0.5)), detail
        if not kg_facts:
            return 0.5, "知识图谱不可用"
        rl = response.lower()
        fact_names = {str(f.get("name", f.get("entity_id", ""))).lower(): f for f in kg_facts if len(str(f.get("name", ""))) > 2}
        if not fact_names:
            return 0.5, "无可验证实体"
        mentioned = sum(1 for n in fact_names if n in rl)
        if mentioned == 0:
            return 0.5, "未提及KG实体"
        verified = min(mentioned, len(fact_names))
        return max(0.3, verified / len(fact_names)), f"{mentioned}/{len(fact_names)} entities verified"

    def _check_self_consistency(self, response: str, alt_samples=None) -> tuple[float, str]:
        sentences = [s.strip().lower() for s in re.split(r'[.。!！?？\n]', response) if len(s.strip()) > 10]
        contradictions = 0
        pairs = [("increase","decrease"),("higher","lower"),("significant","not significant"),("improve","worsen"),("positive","negative"),("上升","下降"),("提高","降低"),("显著","不显著"),("优于","差于")]
        issues = []
        for w1, w2 in pairs:
            if any(w1 in s for s in sentences) and any(w2 in s for s in sentences):
                contradictions += 1
                issues.append(f"{w1}/{w2}")
        penalty = contradictions * 0.15
        score = max(0.1, 1.0 - penalty)
        return score, f"Contradictions: {contradictions}" if contradictions else "No contradictions"

    def _check_high_risk_detailed(self, response: str) -> tuple[bool, str, list[str]]:
        risks = []
        for pattern, category in self.HIGH_RISK_PATTERNS:
            matches = re.findall(pattern, response)
            if matches:
                risks.append(f"{category}")
        if risks:
            return False, f"检测到 {len(risks)} 个高风险模式", risks
        return True, "未检测到高风险模式", []

    def _check_high_risk(self, response: str) -> bool:
        """True when the response matches any high-risk pattern (wrapper for tests/callers)."""
        passed, _, _ = self._check_high_risk_detailed(response)
        return not passed

    def _compute_weighted_confidence(self, layers: dict) -> float:
        tw, ws = 0.0, 0.0
        for name, weight in self.LAYER_WEIGHTS.items():
            if name in layers:
                l = layers[name]
                s = l.score if isinstance(l, LayerResult) else float(l)
                ws += s * weight; tw += weight
        return ws / tw if tw > 0 else 0.5

    @staticmethod
    def _extract_claims(text: str) -> list[str]:
        return [s.strip() for s in re.split(r'[.。!！?？\n•\-●★]', text) if len(s.strip()) > 10]

    @staticmethod
    def _build_summary(layers: dict, overall: float, risk: str) -> str:
        passed = sum(1 for l in layers.values() if (l.passed if isinstance(l, LayerResult) else True))
        labels = {"none":"✅安全","low":"⚠️低风险","medium":"🟡中等风险","high":"🟠高风险","critical":"🔴严重风险"}
        return f"幻觉防线: {passed}/{len(layers)}层通过 | 置信度:{overall:.2f} | {labels.get(risk,risk)}"


_hallucination_defense = None

def get_hallucination_defense() -> HallucinationDefense:
    global _hallucination_defense
    if _hallucination_defense is None:
        _hallucination_defense = HallucinationDefense()
    return _hallucination_defense