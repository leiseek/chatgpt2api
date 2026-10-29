"""Gemini Web compatibility wrapper, forwarding to Gemini Cloud Code provider."""

from __future__ import annotations

import re
from typing import Any

from services.image_providers.gemini_cloudcode import (
    GEMINI_IMAGE_MODEL,
    GeminiImageError,
    GeminiImageProvider,
    GeminiWebImageError,
    GeminiWebImageProvider,
    _determine_aspect_ratio,
    _parse_gemini_token_input,
)
from utils.gemini_watermark import remove_gemini_sparkle

__all__ = [
    "GEMINI_IMAGE_MODEL",
    "GeminiImageError",
    "GeminiImageProvider",
    "GeminiWebImageError",
    "GeminiWebImageProvider",
    "_determine_aspect_ratio",
    "_parse_cookie_input",
    "_high_res_image_url",
    "_extract_google_image_urls",
    "_extract_gemini_response_text",
]


def _parse_cookie_input(token: str, account: dict | None = None) -> tuple[dict[str, str], str]:
    """Compatibility stub for cookie input parser."""
    import json
    token_str = str(token or "").strip()
    cookies: dict[str, str] = {}
    snlm0e = ""

    if (token_str.startswith("{") and token_str.endswith("}")) or (token_str.startswith("[") and token_str.endswith("]")):
        try:
            parsed = json.loads(token_str)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        cookies[str(item["name"])] = str(item["value"])
            elif isinstance(parsed, dict):
                if "cookies" in parsed:
                    c_val = parsed["cookies"]
                    if isinstance(c_val, dict):
                        cookies.update({str(k): str(v) for k, v in c_val.items()})
                    elif isinstance(c_val, list):
                        for item in c_val:
                            if isinstance(item, dict) and "name" in item and "value" in item:
                                cookies[str(item["name"])] = str(item["value"])
                if "snlm0e" in parsed:
                    snlm0e = str(parsed["snlm0e"]).strip()
                for k, v in parsed.items():
                    if k.startswith("__Secure") or k in {"SID", "HSID", "SSID", "APISID", "SAPISID", "SIDCC"}:
                        cookies[k] = str(v)
        except Exception:
            pass

    if not cookies and token_str:
        if "=" in token_str:
            for part in token_str.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookies[k.strip()] = v.strip()
        elif not any(c in token_str for c in (":", '"', "{", "}", "[", "]", ",", "\n", "\r", " ")):
            if len(token_str) >= 20:
                cookies["__Secure-1PSID"] = token_str

    return cookies, snlm0e


def _high_res_image_url(url: str) -> str:
    """Legacy helper: Ensure Google LH3 image URL requests the original full master resolution (=s0)."""
    clean_url = re.sub(r"=(?:s|w|h)\d+.*$", "", url)
    return clean_url + "=s0"


def _extract_google_image_urls(text: str) -> list[str]:
    """Legacy helper: Find Google image URLs in response payload."""
    pattern = re.compile(
        r'https?://(?:lh\d+\.googleusercontent\.com|googleusercontent\.com|encrypted-tbn\d+\.gstatic\.com)/[^\s"\'\\<>]+'
    )
    urls: list[str] = []
    for match in pattern.finditer(text):
        url = match.group(0)
        cleaned = url.replace(r"\u003d", "=").replace(r"\u0026", "&").rstrip('"\')')
        if "=s32" in cleaned or "=s64" in cleaned or "profile" in cleaned or "avatar" in cleaned or "image_generation_content" in cleaned:
            continue
        if cleaned not in urls:
            urls.append(cleaned)
    return urls


def _extract_gemini_response_text(resp_text: str) -> str:
    """Legacy helper: Extract assistant text response or rejection explanation."""
    return ""
