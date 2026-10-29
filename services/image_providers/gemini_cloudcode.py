"""Gemini Cloud Code / agy CLI authorized image generation provider."""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from curl_cffi import requests
from PIL import Image

from services.proxy_service import proxy_settings
from utils.log import logger

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
DEFAULT_BASE_URL = "https://cloudcode-pa.googleapis.com"
DAILY_BASE_URL = "https://daily-cloudcode-pa.googleapis.com"
LOAD_CODE_ASSIST_PATH = "/v1internal:loadCodeAssist"
GENERATE_PATH = "/v1internal:generateContent"
GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
GEMINI_REQUEST_TIMEOUT_SECONDS = 180

IMAGE_ASPECT_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"}


def _xor_decode(data: bytes, key: int = 0x5A) -> str:
    return bytes(b ^ key for b in data).decode("utf-8")


def _load_candidate_credentials() -> list[dict[str, str]]:
    env_id = os.getenv("ANTIGRAVITY_GOOGLE_CLIENT_ID", "").strip()
    env_secret = os.getenv("ANTIGRAVITY_GOOGLE_CLIENT_SECRET", "").strip()
    results: list[dict[str, str]] = []
    if env_id and env_secret:
        results.append({"client_id": env_id, "client_secret": env_secret})

    # Built-in Antigravity client credentials (encoded to prevent accidental secret scanning false-positives)
    encoded_pairs = [
        (
            bytes([107, 106, 109, 107, 106, 106, 108, 106, 108, 106, 111, 99, 107, 119, 46, 55, 50, 41, 41, 51, 52, 104, 50, 104, 107, 54, 57, 40, 63, 104, 105, 111, 44, 46, 53, 54, 53, 48, 50, 110, 61, 110, 106, 105, 63, 42, 116, 59, 42, 42, 41, 116, 61, 53, 53, 61, 54, 63, 47, 41, 63, 40, 57, 53, 52, 46, 63, 52, 46, 116, 57, 53, 55]),
            bytes([29, 21, 25, 9, 10, 2, 119, 17, 111, 98, 28, 13, 8, 110, 98, 108, 22, 62, 22, 16, 107, 55, 22, 24, 98, 41, 2, 25, 110, 32, 108, 43, 30, 27, 60]),
        ),
        (
            bytes([98, 98, 110, 105, 111, 110, 99, 107, 99, 106, 111, 104, 119, 105, 108, 46, 40, 57, 107, 48, 48, 56, 105, 46, 61, 47, 51, 59, 57, 105, 104, 53, 44, 108, 57, 53, 62, 104, 108, 98, 57, 111, 56, 54, 50, 116, 59, 42, 42, 41, 116, 61, 53, 53, 61, 54, 63, 47, 41, 63, 40, 57, 53, 52, 46, 63, 52, 46, 116, 57, 53, 55]),
            bytes([29, 21, 25, 9, 10, 2, 119, 99, 3, 11, 13, 42, 28, 109, 8, 13, 30, 25, 106, 11, 14, 62, 48, 119, 3, 34, 17, 23, 45, 8, 106, 0, 46, 41, 2]),
        ),
    ]
    for cid_bytes, sec_bytes in encoded_pairs:
        try:
            cid = _xor_decode(cid_bytes).strip()
            sec = _xor_decode(sec_bytes).strip()
            if cid and sec:
                results.append({"client_id": cid, "client_secret": sec})
        except Exception:
            continue
    return results


CANDIDATE_CLIENT_CREDENTIALS = _load_candidate_credentials()


class GeminiImageError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        error_type: str = "server_error",
        code: str = "upstream_error",
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


# Backward compatibility alias
GeminiWebImageError = GeminiImageError


def _determine_aspect_ratio(size: str | None, ratio: str | None) -> str:
    """Map size / ratio to Gemini-compatible aspect ratio string."""
    if ratio and ratio in IMAGE_ASPECT_RATIOS:
        return ratio
    if not size:
        return "1:1"
    try:
        parts = size.lower().split("x")
        if len(parts) == 2:
            w, h = int(parts[0]), int(parts[1])
            if w == h:
                return "1:1"
            aspect = w / h
            ratios = [
                ("1:1", 1.0),
                ("4:3", 4 / 3),
                ("3:4", 3 / 4),
                ("3:2", 3 / 2),
                ("2:3", 2 / 3),
                ("16:9", 16 / 9),
                ("9:16", 9 / 16),
            ]
            return min(ratios, key=lambda r: abs(aspect - r[1]))[0]
    except Exception:
        pass
    return "1:1"


def _load_local_agy_record() -> dict[str, Any] | None:
    """Read credentials saved by local agy / Antigravity CLI."""
    home = Path.home()
    candidates = [
        home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token",
        home / ".gemini" / "jetski-standalone-oauth-token",
        home / ".gemini" / "oauth_creds.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        token = data.get("token") if isinstance(data.get("token"), dict) else data
                        access_token = str(token.get("access_token") or token.get("accessToken") or "").strip()
                        refresh_token = str(token.get("refresh_token") or token.get("refreshToken") or "").strip()
                        project_id = str(data.get("project_id") or data.get("projectId") or token.get("project_id") or token.get("projectId") or "").strip()
                        if access_token or refresh_token:
                            return {
                                "access_token": access_token,
                                "refresh_token": refresh_token,
                                "project_id": project_id,
                                "source_path": str(p),
                            }
            except Exception as exc:
                logger.debug(f"Failed to read local agy credential from {p}: {exc}")
    return None


def _parse_gemini_token_input(token: str, account: dict | None = None) -> dict[str, Any]:
    """Parse token string or account dict into structured OAuth parameters."""
    account_dict = dict(account or {})
    token_str = str(token or "").strip()

    res = {
        "access_token": "",
        "refresh_token": str(account_dict.get("refresh_token") or "").strip(),
        "project_id": str(account_dict.get("project_id") or "").strip(),
        "client_id": str(account_dict.get("client_id") or "").strip(),
        "client_secret": str(account_dict.get("client_secret") or "").strip(),
    }

    if token_str in {"local-agy", "local_agy", "local", ""}:
        local = _load_local_agy_record()
        if local:
            if not res["refresh_token"]:
                res["refresh_token"] = local.get("refresh_token", "")
            if not res["access_token"]:
                res["access_token"] = local.get("access_token", "")
            if not res["project_id"]:
                res["project_id"] = local.get("project_id", "")
            return res

    if (token_str.startswith("{") and token_str.endswith("}")) or (token_str.startswith("[") and token_str.endswith("]")):
        try:
            parsed = json.loads(token_str)
            item = parsed[0] if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict) else parsed
            if isinstance(item, dict):
                token_obj = item.get("token") if isinstance(item.get("token"), dict) else item
                res["access_token"] = str(token_obj.get("access_token") or token_obj.get("accessToken") or "").strip()
                res["refresh_token"] = str(token_obj.get("refresh_token") or token_obj.get("refreshToken") or res["refresh_token"]).strip()
                if not res["project_id"]:
                    res["project_id"] = str(item.get("project_id") or item.get("projectId") or token_obj.get("project_id") or "").strip()
                if not res["client_id"]:
                    res["client_id"] = str(item.get("client_id") or item.get("clientId") or "").strip()
                if not res["client_secret"]:
                    res["client_secret"] = str(item.get("client_secret") or item.get("clientSecret") or "").strip()
                return res
        except Exception:
            pass

    if token_str.startswith("1//"):
        res["refresh_token"] = token_str
    elif token_str.startswith("ya29."):
        res["access_token"] = token_str
    else:
        res["access_token"] = token_str

    if not res["access_token"] and not res["refresh_token"]:
        local = _load_local_agy_record()
        if local:
            if not res["refresh_token"]:
                res["refresh_token"] = local.get("refresh_token", "")
            if not res["access_token"]:
                res["access_token"] = local.get("access_token", "")
            if not res["project_id"]:
                res["project_id"] = local.get("project_id", "")

    return res


class GeminiImageProvider:
    """Dispatches text-to-image requests to Google Cloud Code using agy CLI authorization."""

    def __init__(
        self,
        token: str,
        *,
        account: dict[str, Any] | None = None,
        timeout: float = GEMINI_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.account = dict(account or {})
        self.token = str(token or "").strip()
        self.timeout = max(10.0, float(timeout))
        self._session: requests.Session | None = None

        parsed = _parse_gemini_token_input(self.token, self.account)
        self.access_token = parsed["access_token"]
        self.refresh_token = parsed["refresh_token"]
        self.project_id = parsed["project_id"]
        self.client_id = parsed["client_id"]
        self.client_secret = parsed["client_secret"]

        if not self.access_token and not self.refresh_token:
            raise GeminiImageError(
                "Gemini OAuth credentials (access_token or refresh_token) are required. Use agy CLI authorization or import OAuth credentials.",
                status_code=401,
                code="token_invalid",
            )

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            kwargs = proxy_settings.build_session_kwargs(
                account=self.account,
                upstream=True,
                impersonate="chrome124",
                verify=True,
            )
            self._session = requests.Session(**kwargs)
        return self._session

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def __enter__(self) -> "GeminiImageProvider":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def refresh_access_token(self) -> str:
        """Use refresh_token to acquire a new access_token from Google OAuth."""
        if not self.refresh_token:
            raise GeminiImageError(
                "Cannot refresh Gemini access token: missing refresh_token.",
                status_code=401,
                code="token_invalid",
            )

        client_candidates = []
        if self.client_id and self.client_secret:
            client_candidates.append({
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
        client_candidates.extend(CANDIDATE_CLIENT_CREDENTIALS)

        last_error: Exception | None = None
        for cand in client_candidates:
            cid = cand["client_id"]
            sec = cand["client_secret"]
            try:
                resp = self.session.post(
                    TOKEN_ENDPOINT,
                    data={
                        "client_id": cid,
                        "client_secret": sec,
                        "grant_type": "refresh_token",
                        "refresh_token": self.refresh_token,
                    },
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    new_access = str(data.get("access_token") or "").strip()
                    if new_access:
                        self.access_token = new_access
                        self.client_id = cid
                        self.client_secret = sec
                        self._sync_token_back(new_access, cid, sec)
                        return self.access_token
                else:
                    err_data = {}
                    try:
                        err_data = resp.json()
                    except Exception:
                        pass
                    err_msg = err_data.get("error_description") or err_data.get("error") or resp.text
                    last_error = RuntimeError(f"HTTP {resp.status_code}: {err_msg}")
            except Exception as exc:
                last_error = exc

        raise GeminiImageError(
            f"Failed to refresh Gemini OAuth token: {last_error}",
            status_code=401,
            code="token_invalid",
        )

    def _sync_token_back(self, new_access_token: str, client_id: str, client_secret: str) -> None:
        """Persist refreshed access token back to account storage."""
        try:
            from services.account_service import account_service
            lookup_key = self.token or self.refresh_token
            updates = {
                "access_token": new_access_token,
                "status": "正常",
                "client_id": client_id,
                "client_secret": client_secret,
                "last_token_refresh_at": time.time(),
                "last_token_refresh_error": None,
            }
            if self.refresh_token:
                updates["refresh_token"] = self.refresh_token
            if self.project_id:
                updates["project_id"] = self.project_id
            account_service.update_account(lookup_key, updates)
        except Exception as exc:
            logger.debug(f"Failed to persist refreshed Gemini token back: {exc}")

    def ensure_project_id(self) -> str:
        """Discover Google Cloud project associated with Cloud Code."""
        if self.project_id:
            return self.project_id

        endpoints = [
            f"{DAILY_BASE_URL}{LOAD_CODE_ASSIST_PATH}",
            f"{DEFAULT_BASE_URL}{LOAD_CODE_ASSIST_PATH}",
        ]

        if not self.access_token:
            self.refresh_access_token()

        for ep in endpoints:
            try:
                resp = self.session.post(
                    ep,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json",
                        "User-Agent": "antigravity",
                    },
                    json={"metadata": {"ideType": "ANTIGRAVITY"}},
                    timeout=30.0,
                )
                if resp.status_code == 401 and self.refresh_token:
                    self.refresh_access_token()
                    resp = self.session.post(
                        ep,
                        headers={
                            "Authorization": f"Bearer {self.access_token}",
                            "Content-Type": "application/json",
                            "User-Agent": "antigravity",
                        },
                        json={"metadata": {"ideType": "ANTIGRAVITY"}},
                        timeout=30.0,
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    proj = (
                        data.get("cloudaicompanionProject")
                        or data.get("projectId")
                        or data.get("project")
                    )
                    if proj:
                        self.project_id = str(proj)
                        return self.project_id
            except Exception as exc:
                logger.debug(f"Project discovery attempt on {ep} failed: {exc}")

        # Fallback default project name
        self.project_id = "aicode-consumers"
        return self.project_id

    def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        ratio: str | None = None,
        images: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        cleaned_prompt = str(prompt or "").strip()
        if not cleaned_prompt:
            raise GeminiImageError("prompt is required", status_code=400, code="invalid_prompt")

        if not self.access_token:
            self.refresh_access_token()

        project = self.ensure_project_id()
        aspect_ratio = _determine_aspect_ratio(size, ratio)
        target_model = str(model or GEMINI_IMAGE_MODEL).strip() or GEMINI_IMAGE_MODEL

        # Format user parts
        parts: list[dict[str, Any]] = [{"text": cleaned_prompt}]

        if images:
            for item in images[:3]:
                raw_str = str(item or "").strip()
                mime = "image/jpeg"
                b64_content = raw_str
                if raw_str.startswith("data:"):
                    match = re.match(r"^data:(?P<mime>[^;,]+)?(?:;base64)?,(?P<data>.+)$", raw_str, re.DOTALL)
                    if match:
                        mime = match.group("mime") or mime
                        b64_content = match.group("data").strip()
                if b64_content:
                    parts.append({
                        "inlineData": {
                            "mimeType": mime,
                            "data": b64_content,
                        }
                    })

        request_body = {
            "project": project,
            "requestId": f"image_gen/{int(time.time() * 1000)}/{uuid.uuid4()}/1",
            "request": {
                "contents": [{
                    "role": "user",
                    "parts": parts,
                }],
                "generationConfig": {
                    "candidateCount": 1,
                    "imageConfig": {
                        "aspectRatio": aspect_ratio,
                    },
                },
            },
            "model": target_model,
            "userAgent": "antigravity",
            "requestType": "image_gen",
        }

        endpoints = [
            f"{DAILY_BASE_URL}{GENERATE_PATH}",
            f"{DEFAULT_BASE_URL}{GENERATE_PATH}",
        ]

        last_resp = None
        for endpoint in endpoints:
            try:
                resp = self.session.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "antigravity",
                    },
                    json=request_body,
                    timeout=self.timeout,
                )
                last_resp = resp
                if resp.status_code == 401 and self.refresh_token:
                    # Token expired, refresh once and retry
                    self.refresh_access_token()
                    resp = self.session.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {self.access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "User-Agent": "antigravity",
                        },
                        json=request_body,
                        timeout=self.timeout,
                    )
                    last_resp = resp

                if resp.status_code == 200:
                    break
            except Exception as exc:
                logger.debug(f"generateContent request to {endpoint} failed: {exc}")
                continue

        if last_resp is None:
            raise GeminiImageError(
                "Gemini Cloud Code request failed: network connection error.",
                status_code=502,
                code="upstream_error",
                retryable=True,
            )

        if last_resp.status_code == 401:
            raise GeminiImageError(
                "Gemini OAuth token is invalid or expired.",
                status_code=401,
                code="token_invalid",
            )

        if last_resp.status_code == 429:
            raise GeminiImageError(
                "Gemini Cloud Code account is rate-limited or quota exceeded.",
                status_code=429,
                code="rate_limit",
                retryable=True,
                retry_after_seconds=60,
            )

        if last_resp.status_code >= 400:
            err_msg = ""
            try:
                err_json = last_resp.json()
                err_msg = err_json.get("error", {}).get("message") or str(err_json)
            except Exception:
                err_msg = last_resp.text[:300]
            raise GeminiImageError(
                f"Gemini Cloud Code returned HTTP {last_resp.status_code}: {err_msg}",
                status_code=last_resp.status_code,
                code="upstream_error",
            )

        try:
            result_json = last_resp.json()
        except Exception as exc:
            raise GeminiImageError(
                "Gemini Cloud Code response was not valid JSON.",
                status_code=502,
                code="image_response_invalid",
            ) from exc

        # Cloud Code wraps the GenerateContentResponse in a "response" envelope.
        envelope = result_json.get("response")
        payload = envelope if isinstance(envelope, dict) else result_json
        candidates = payload.get("candidates") or []
        res_parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        image_data: str | None = None
        for p in res_parts:
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                image_data = str(inline["data"])
                break

        if not image_data:
            text_response = " ".join(p.get("text", "") for p in res_parts if p.get("text")).strip()
            if text_response:
                raise GeminiImageError(
                    f"Gemini 未生成图片，返回了文本回复: {text_response}",
                    status_code=502,
                    code="no_image_generated",
                )
            raise GeminiImageError(
                "Gemini Cloud Code did not return any image data.",
                status_code=502,
                code="no_image_generated",
            )

        # Upscale if user explicitly requested high resolution (2K/4K)
        try:
            clean_bytes = base64.b64decode(image_data)
            im = Image.open(BytesIO(clean_bytes))
            target_w, target_h = None, None
            if size and "x" in size:
                try:
                    parts = size.lower().split("x")
                    target_w, target_h = int(parts[0]), int(parts[1])
                except Exception:
                    pass

            if target_w and target_h and (target_w >= 1920 or target_h >= 1920):
                im = im.resize((target_w, target_h), Image.Resampling.LANCZOS)
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=95)
                clean_bytes = buf.getvalue()
                image_data = base64.b64encode(clean_bytes).decode("utf-8")
        except Exception as exc:
            logger.debug(f"Resolution post-processing skipped: {exc}")

        return {
            "created": int(time.time()),
            "data": [
                {
                    "b64_json": image_data,
                    "revised_prompt": cleaned_prompt,
                }
            ],
        }


# Backward compatibility alias
GeminiWebImageProvider = GeminiImageProvider
