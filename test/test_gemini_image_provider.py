from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from services.account_service import account_service
from services.image_providers.gemini_web import (
    GeminiWebImageError,
    GeminiWebImageProvider,
    _determine_aspect_ratio,
    _extract_google_image_urls,
    _high_res_image_url,
    _parse_cookie_input,
)
from services.protocol.conversation import (
    ConversationRequest,
    ImageGenerationError,
    _generate_single_gemini_image,
    _generate_single_image,
)
from utils.gemini_watermark import remove_gemini_sparkle
from utils.helper import is_gemini_image_model, GEMINI_IMAGE_MODEL


def test_determine_aspect_ratio():
    assert _determine_aspect_ratio("1024x1024", None) == "1:1"
    assert _determine_aspect_ratio("1792x1024", None) == "16:9"
    assert _determine_aspect_ratio("1024x1792", None) == "9:16"
    assert _determine_aspect_ratio("1536x1024", None) == "3:2"
    assert _determine_aspect_ratio("1024x1536", None) == "2:3"
    assert _determine_aspect_ratio("1024x1024", "16:9") == "16:9"


def test_high_res_image_url():
    raw_url = "https://lh3.googleusercontent.com/gg-dl/AF1QipNabc12345"
    assert _high_res_image_url(raw_url) == "https://lh3.googleusercontent.com/gg-dl/AF1QipNabc12345=s0"
    preview_url = "https://lh3.googleusercontent.com/gg-dl/AF1QipNabc12345=s512"
    assert _high_res_image_url(preview_url) == "https://lh3.googleusercontent.com/gg-dl/AF1QipNabc12345=s0"


def test_gemini_model_detection():
    assert is_gemini_image_model("gemini-image-2.5-flash") is True
    assert is_gemini_image_model(GEMINI_IMAGE_MODEL) is True
    assert is_gemini_image_model("gpt-image-2") is False
    assert is_gemini_image_model("agnes-image-2.1-flash") is False


def test_parse_cookie_input_string():
    cookie_str = "__Secure-1PSID=abc12345; __Secure-1PSIDTS=ts999; SID=sid123"
    cookies, snlm0e = _parse_cookie_input(cookie_str)
    assert cookies["__Secure-1PSID"] == "abc12345"
    assert cookies["__Secure-1PSIDTS"] == "ts999"
    assert cookies["SID"] == "sid123"
    assert snlm0e == ""


def test_parse_cookie_input_json():
    json_str = '{"cookies": {"__Secure-1PSID": "test_psid"}, "snlm0e": "at_token_123"}'
    cookies, snlm0e = _parse_cookie_input(json_str)
    assert cookies["__Secure-1PSID"] == "test_psid"
    assert snlm0e == "at_token_123"


def test_extract_google_image_urls():
    payload = r'some data ["https://lh3.googleusercontent.com/ggms/AF1QipNabc12345=s1024", "https://googleusercontent.com/image_test_456"]'
    urls = _extract_google_image_urls(payload)
    assert len(urls) == 2
    assert "https://lh3.googleusercontent.com/ggms/AF1QipNabc12345=s1024" in urls
    assert "https://googleusercontent.com/image_test_456" in urls


def test_remove_gemini_sparkle_watermark():
    img = Image.new("RGB", (200, 200), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    cleaned = remove_gemini_sparkle(raw_bytes)
    assert isinstance(cleaned, bytes)
    assert len(cleaned) > 0

    with Image.open(io.BytesIO(cleaned)) as cleaned_img:
        assert cleaned_img.size == (200, 200)


def test_generate_single_gemini_image_success():
    fake_token = "__Secure-1PSID=test_cookie"
    test_img = Image.new("RGB", (64, 64), color=(200, 100, 50))
    buf = io.BytesIO()
    test_img.save(buf, format="PNG")
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

    with patch.object(account_service, "get_available_access_token", return_value=fake_token), \
         patch.object(account_service, "get_account", return_value={"access_token": fake_token, "provider": "gemini"}), \
         patch.object(account_service, "mark_image_result") as mock_mark, \
         patch.object(account_service, "release_image_slot") as mock_release, \
         patch.object(GeminiWebImageProvider, "generate", return_value={"data": [{"b64_json": b64_str}]}):

        req = ConversationRequest(model=GEMINI_IMAGE_MODEL, prompt="a cute cat")
        outputs = _generate_single_image(req, 1, 1)

        assert len(outputs) == 1
        assert outputs[0].kind == "result"
        assert len(outputs[0].data) == 1
        mock_mark.assert_called_once_with(fake_token, True, release_slot=False)
        mock_release.assert_called_once_with(fake_token)


from services.image_providers.gemini_cloudcode import (
    GeminiImageProvider,
    GeminiImageError,
    _parse_gemini_token_input,
)


def test_parse_gemini_token_input():
    # Raw refresh token
    res1 = _parse_gemini_token_input("1//test_refresh_token_123")
    assert res1["refresh_token"] == "1//test_refresh_token_123"

    # JSON input
    json_token = '{"refresh_token": "1//rt_val", "project_id": "proj-abc", "client_id": "cid", "client_secret": "csec"}'
    res2 = _parse_gemini_token_input(json_token)
    assert res2["refresh_token"] == "1//rt_val"
    assert res2["project_id"] == "proj-abc"
    assert res2["client_id"] == "cid"
    assert res2["client_secret"] == "csec"

    # local-agy keyword
    res3 = _parse_gemini_token_input("local-agy")
    assert isinstance(res3, dict)


def test_gemini_cloudcode_provider_generate_success():
    provider = GeminiImageProvider("1//test_refresh", account={"project_id": "test-project"})
    provider.access_token = "ya29.fake_access_token"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": "base64imagecontent123",
                            }
                        }
                    ]
                }
            }
        ]
    }

    with patch.object(provider.session, "post", return_value=mock_resp):
        res = provider.generate("A cute golden retriever", size="1024x1024", ratio="1:1")
        assert "data" in res
        assert len(res["data"]) == 1
        assert res["data"][0]["b64_json"] == "base64imagecontent123"
        assert res["data"][0]["revised_prompt"] == "A cute golden retriever"


def test_gemini_cloudcode_provider_auto_refresh_on_401():
    provider = GeminiImageProvider("1//test_refresh", account={"project_id": "test-project"})
    provider.access_token = "ya29.expired_token"

    resp_401 = MagicMock()
    resp_401.status_code = 401

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": "refreshed_image_data",
                            }
                        }
                    ]
                }
            }
        ]
    }

    with patch.object(provider, "refresh_access_token", return_value="ya29.new_token") as mock_refresh, \
         patch.object(provider.session, "post", side_effect=[resp_401, resp_200]):
        res = provider.generate("A cute cat")
        assert mock_refresh.called
        assert res["data"][0]["b64_json"] == "refreshed_image_data"


def test_gemini_cloudcode_provider_rate_limit_429():
    provider = GeminiImageProvider("1//test_refresh", account={"project_id": "test-project"})
    provider.access_token = "ya29.valid"

    resp_429 = MagicMock()
    resp_429.status_code = 429

    with patch.object(provider.session, "post", return_value=resp_429):
        with pytest.raises(GeminiImageError) as exc_info:
            provider.generate("test prompt")
        assert exc_info.value.status_code == 429
        assert exc_info.value.code == "rate_limit"
