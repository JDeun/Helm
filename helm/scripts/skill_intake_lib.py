from __future__ import annotations


OFFENSIVE = (
    "phishing",
    "credential theft",
    "collect credentials",
    "exploit execution",
    "malware",
    "persistence",
    "evasion",
    "stealth",
    "bypass authorization",
    "privilege escalation",
)
DEFENSIVE_D1 = ("mcp", "tool poisoning", "prompt injection", "permission review", "untrusted content", "audit")
DEFENSIVE_D2 = ("incident response", "log review", "threat hunting", "dependency security", "suspicious command")
HIGH_IMPACT = ("cloud", "production", "credential store", "delete", "rotate", "revoke")
VALID_CLASSES = {"D0", "D1", "D2", "D3", "X"}


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def classify_candidate(name: str, description: str = "") -> dict:
    blob = f"{name} {description}".casefold()
    if _has_any(blob, OFFENSIVE):
        return {
            "name": name,
            "risk_class": "X",
            "default_action": "reject",
            "rationale": "offensive or unsafe dual-use behavior matched",
        }
    if "documentation" in blob or ("checklist" in blob and not _has_any(blob, HIGH_IMPACT)):
        return {
            "name": name,
            "risk_class": "D0",
            "default_action": "keep_reference",
            "rationale": "documentation-only defensive reference",
        }
    if _has_any(blob, DEFENSIVE_D1):
        return {
            "name": name,
            "risk_class": "D1",
            "default_action": "draft",
            "rationale": "defensive read-only analysis candidate",
        }
    if _has_any(blob, DEFENSIVE_D2):
        risk_class = "D3" if _has_any(blob, HIGH_IMPACT) else "D2"
        return {
            "name": name,
            "risk_class": risk_class,
            "default_action": "quarantine" if risk_class == "D3" else "draft_with_contract",
            "rationale": "defensive local inspection or operations candidate",
        }
    return {
        "name": name,
        "risk_class": "X",
        "default_action": "quarantine",
        "rationale": "unknown candidates default to quarantine",
    }


def validate_candidate(candidate: dict) -> dict:
    issues: list[str] = []
    if not candidate.get("name"):
        issues.append("missing `name`")
    if candidate.get("risk_class") not in VALID_CLASSES:
        issues.append("invalid `risk_class`")
    if not candidate.get("default_action"):
        issues.append("missing `default_action`")
    return {"ok": not issues, "issues": issues}
