from __future__ import annotations

import hashlib
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from src.seo_agent.opportunities.models import OpportunityCandidate


class OpportunityCandidateNormalizer:
    """把诊断候选转换为可稳定去重的业务输入。"""

    @staticmethod
    def normalize_text(value: str, *, lowercase: bool = True) -> str:
        normalized = unicodedata.normalize("NFKC", value)
        normalized = " ".join(normalized.split())
        return normalized.lower() if lowercase else normalized

    @classmethod
    def normalize_url(cls, value: str) -> str:
        raw = unicodedata.normalize("NFKC", value.strip())
        parts = urlsplit(raw)
        if not parts.scheme or not parts.hostname:
            return raw.rstrip("/")
        scheme = parts.scheme.lower()
        hostname = parts.hostname.lower()
        port = parts.port
        if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
            netloc = f"{hostname}:{port}"
        else:
            netloc = hostname
        path = parts.path or ""
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((scheme, netloc, path, parts.query, ""))

    def normalize(self, candidate: OpportunityCandidate) -> OpportunityCandidate:
        keywords = [
            self.normalize_text(keyword)
            for keyword in candidate.keywords
            if keyword and keyword.strip()
        ]
        pages = [
            self.normalize_url(url) for url in candidate.competitor_pages if url and url.strip()
        ]
        return candidate.model_copy(
            update={
                "normalized_intent": self.normalize_text(candidate.normalized_intent),
                "opportunity_type": self.normalize_text(
                    candidate.opportunity_type,
                    lowercase=False,
                ).upper(),
                "target_country": self.normalize_text(
                    candidate.target_country,
                    lowercase=False,
                ).upper(),
                "target_language": self.normalize_text(candidate.target_language),
                "title": self.normalize_text(candidate.title, lowercase=False),
                "summary": self.normalize_text(candidate.summary, lowercase=False),
                "keywords": list(dict.fromkeys(keywords)),
                "competitor_pages": list(dict.fromkeys(pages)),
                "evidence_snapshot_ids": list(dict.fromkeys(candidate.evidence_snapshot_ids)),
                "major_change_reasons": list(dict.fromkeys(candidate.major_change_reasons)),
            }
        )

    def fingerprint(self, site_id: str, candidate: OpportunityCandidate) -> str:
        normalized = self.normalize(candidate)
        value = "|".join(
            [
                self.normalize_text(site_id),
                normalized.normalized_intent,
                normalized.target_country,
                normalized.target_language,
                normalized.opportunity_type,
            ]
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
