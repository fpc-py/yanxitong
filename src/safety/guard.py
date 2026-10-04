"""LLM Guard v2.0 — comprehensive I/O safety filtering with PII, injection, harmful content detection."""

import re, logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GuardResult:
    passed: bool
    blocked_reason: str = ""
    sanitized_text: str = ""
    risk_level: str = "none"
    categories_triggered: list[str] = field(default_factory=list)


class InputGuard:
    PROMPT_INJECTION = [
        (r"(?i)ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|messages?)", "injection_ignore"),
        (r"(?i)(you\s+are\s+now|act\s+as|pretend\s+(you\s+are|to\s+be)|roleplay\s+as)", "injection_roleplay"),
        (r"(?i)(forget|disregard|override)\s+(your|all)\s+(instructions?|training|rules?)", "injection_forget"),
        (r"(?i)(system\s*(prompt|message|instruction)\s*[:：])", "injection_system"),
        (r"(?i)(<\|im_start\|>|<\|im_end\|>|<\|\S+\|>)", "injection_tokens"),
        (r"(?i)(reveal|show|display|print|output)\s+(your|the)\s+(system\s+)?(prompt|instructions?)", "injection_reveal"),
        (r"(?i)(jailbreak|dan\s+mode|developer\s+mode|god\s+mode)", "injection_jailbreak"),
    ]
    PII = {
        "email": (r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', "[EMAIL]"),
        "phone": (r'(?<!\d)1[3-9]\d{9}(?!\d)', "[PHONE]"),
        "id_card": (r'(?<!\d)\d{17}[\dXx](?!\d)', "[ID_CARD]"),
        "credit_card": (r'(?<!\d)\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)', "[CREDIT_CARD]"),
    }
    HARMFUL = [
        (r"(?i)\b(kill|murder|attack|bomb|terrorist|violence)\b.*\b(how\s+to|instructions?|guide)\b", "violence"),
        (r"(?i)\b(suicide|self-harm|self\s+harm|kill\s+myself)\b", "self_harm"),
        (r"(?i)\b(hack|crack|exploit|malware|ransomware|phishing)\b.*\b(how\s+to|create|write|build)\b", "cyber_attack"),
    ]

    @classmethod
    def check(cls, text: str) -> GuardResult:
        if not text or not text.strip():
            return GuardResult(passed=False, blocked_reason="Empty input", risk_level="low")
        cats = []
        for pat, cat in cls.PROMPT_INJECTION:
            if re.search(pat, text):
                return GuardResult(passed=False, blocked_reason=f"Injection: {cat}", risk_level="critical", categories_triggered=[cat])
        for pat, cat in cls.HARMFUL:
            if re.search(pat, text):
                return GuardResult(passed=False, blocked_reason=f"Harmful: {cat}", risk_level="critical", categories_triggered=[cat])
        sanitized = text
        for pii_type, (pat, repl) in cls.PII.items():
            if re.findall(pat, sanitized):
                cats.append(f"pii_{pii_type}")
                sanitized = re.sub(pat, repl, sanitized)
        if cats:
            return GuardResult(passed=True, sanitized_text=sanitized, risk_level="low", categories_triggered=cats)
        return GuardResult(passed=True, sanitized_text=text)

    @classmethod
    def sanitize(cls, text: str) -> str:
        r = cls.check(text)
        return r.sanitized_text if r.passed else ""


class OutputGuard:
    BLOCKED = [
        (r"(?i)(execute\s+(arbitrary\s+)?(code|command|script)\s*[:：(])", "code_execution"),
        (r"(?i)\b(DROP\s+TABLE|DELETE\s+FROM|TRUNCATE\s+TABLE)\b", "sql_destructive"),
        (r"(?i)\b(rm\s+-rf|del\s+/[fsq]|format\s+[a-z]:|shutdown\s+/[sr])\b", "system_destructive"),
        (r"(?i)\b(sudo|chmod\s+777|chown\s+root)\b", "privilege_escalation"),
    ]
    SENSITIVE = [
        (r"(?i)\b(password|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*\S+", "credential_leak"),
    ]

    @classmethod
    def check(cls, text: str) -> GuardResult:
        if not text:
            return GuardResult(passed=True, sanitized_text="")
        for pat, cat in cls.BLOCKED:
            if re.search(pat, text):
                return GuardResult(passed=False, blocked_reason=f"Dangerous: {cat}", risk_level="high", categories_triggered=[cat])
        cats = []
        sanitized = text
        for pat, cat in cls.SENSITIVE:
            if re.search(pat, sanitized):
                cats.append(cat)
                sanitized = re.sub(pat, f"[{cat.upper()}_REDACTED]", sanitized)
        if cats:
            return GuardResult(passed=True, sanitized_text=sanitized, risk_level="medium", categories_triggered=cats)
        return GuardResult(passed=True, sanitized_text=text)


_ig = None; _og = None

def get_input_guard() -> InputGuard:
    global _ig
    if _ig is None: _ig = InputGuard()
    return _ig

def get_output_guard() -> OutputGuard:
    global _og
    if _og is None: _og = OutputGuard()
    return _og