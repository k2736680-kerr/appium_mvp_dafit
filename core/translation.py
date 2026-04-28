import json
import re
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from base64 import b64encode

from core.config import ALI_BAICHUAN_API_KEY, BASE_URL, MODEL_TEXT


LANGUAGE_CODE_TO_NAME = {
    "zh": "中文",
    "en": "英文",
    "ja": "日语",
    "ko": "韩语",
}

UI_NOISE_TEXTS = {
    "双耳机模式",
    "开始双耳机模式",
    "开始手机模式",
    "翻译中心",
    "面对面，无缝交流",
    "我的语言",
    "对方语言",
    "连接中",
    "未连接",
    "已连接",
    "耳机未连接",
    "请连接蓝牙耳机以使用此功能",
    "无录音权限",
    "录音权限",
    "麦克风权限",
    "连接错误",
    "解析错误",
    "异常",
    "失败",
    "Auro AI",
    "翻译",
    "语言",
}

LANGUAGE_LABEL_NOISE = {
    "中文",
    "英文",
    "日语",
    "韩语",
    "Chinese",
    "English",
    "Japanese",
    "Korean",
    "日本語",
    "한국어",
    "简体中文",
    "中文（简体）",
    "Chinese (Simplified)",
    "中文（简体） Chinese (Simplified)",
    "中文 Chinese",
    "英文 English",
    "日本語 Japanese",
    "한국어 Korean",
}


def normalize_text_value(value: str) -> str:
    value = value.replace("\n", " ").replace("\r", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_visible_texts(page_source: str) -> list[str]:
    texts = []
    seen = set()

    try:
        root = ET.fromstring(page_source)
    except ET.ParseError:
        return texts

    for node in root.iter():
        for attr_name in ("text", "content-desc"):
            value = node.attrib.get(attr_name, "")
            value = normalize_text_value(value)
            if not value or value in seen:
                continue
            texts.append(value)
            seen.add(value)

    return texts


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def contains_kana(text: str) -> bool:
    return any("\u3040" <= char <= "\u30ff" for char in text)


def contains_hangul(text: str) -> bool:
    return any("\uac00" <= char <= "\ud7af" for char in text)


def contains_latin(text: str) -> bool:
    return any(("A" <= char <= "Z") or ("a" <= char <= "z") for char in text)


def text_matches_language(text: str, language_code: str) -> bool:
    if language_code == "zh":
        return contains_cjk(text) and not contains_kana(text) and not contains_hangul(text)
    if language_code == "en":
        return contains_latin(text)
    if language_code == "ja":
        return contains_kana(text)
    if language_code == "ko":
        return contains_hangul(text)
    return False


def char_matches_language(char: str, language_code: str) -> bool:
    if language_code == "zh":
        return "\u4e00" <= char <= "\u9fff"
    if language_code == "en":
        return ("A" <= char <= "Z") or ("a" <= char <= "z")
    if language_code == "ja":
        return ("\u3040" <= char <= "\u30ff") or ("\u4e00" <= char <= "\u9fff")
    if language_code == "ko":
        return "\uac00" <= char <= "\ud7af"
    return False


def is_noise_text(text: str) -> bool:
    if text in UI_NOISE_TEXTS or text in LANGUAGE_LABEL_NOISE:
        return True
    if len(text.strip()) < 2:
        return True
    if re.fullmatch(r"[\W_]+", text):
        return True
    return False


def filter_meaningful_texts(texts: list[str], baseline_texts=None) -> list[str]:
    baseline_texts = set(baseline_texts or [])
    result = []
    seen = set()

    for text in texts:
        if text in baseline_texts:
            continue
        if is_noise_text(text):
            continue
        if text in seen:
            continue
        result.append(text)
        seen.add(text)

    return result


def split_mixed_language_text(text: str, source_lang: str, target_lang: str):
    if not text:
        return None, None

    source_seen = False
    target_start = None

    for index, char in enumerate(text):
        if char_matches_language(char, source_lang):
            source_seen = True
            continue
        if source_seen and char_matches_language(char, target_lang):
            target_start = index
            break

    if target_start is None:
        return None, None

    source_text = text[:target_start].strip(" \n\r\t-:|")
    target_text = text[target_start:].strip(" \n\r\t-:|")

    if not source_text or not target_text:
        return None, None

    if not text_matches_language(source_text, source_lang):
        return None, None
    if not text_matches_language(target_text, target_lang):
        return None, None

    return source_text, target_text


def pick_best_candidate(candidates: list[str], language_code: str, excluded=None):
    excluded = set(excluded or [])
    ranked = []

    for text in candidates:
        if text in excluded:
            continue

        score = len(text)
        if text_matches_language(text, language_code):
            score += 100
        elif language_code in {"zh", "ja"} and contains_cjk(text):
            score += 20

        ranked.append((score, text))

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def pick_translation_pair(texts: list[str], baseline_texts, source_lang: str, target_lang: str):
    meaningful_delta = filter_meaningful_texts(texts, baseline_texts=baseline_texts)
    source_text = pick_best_candidate(meaningful_delta, source_lang)
    target_text = pick_best_candidate(meaningful_delta, target_lang, excluded={source_text})

    if source_text and target_text:
        return source_text, target_text

    for text in meaningful_delta:
        mixed_source, mixed_target = split_mixed_language_text(text, source_lang, target_lang)
        if mixed_source and mixed_target:
            return mixed_source, mixed_target

    fallback_candidates = filter_meaningful_texts(texts)
    source_text = source_text or pick_best_candidate(fallback_candidates, source_lang)
    target_text = target_text or pick_best_candidate(
        fallback_candidates,
        target_lang,
        excluded={source_text},
    )
    if source_text and target_text:
        return source_text, target_text

    for text in fallback_candidates:
        mixed_source, mixed_target = split_mixed_language_text(text, source_lang, target_lang)
        if mixed_source and mixed_target:
            return mixed_source, mixed_target

    return source_text, target_text


def build_chat_completions_url(base_url: str) -> str:
    if base_url.rstrip("/").endswith("/chat/completions"):
        return base_url
    return f"{base_url.rstrip('/')}/chat/completions"


def parse_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError(f"模型返回的内容不是合法 JSON: {text}")


def ps_single_quote(value: str) -> str:
    return value.replace("'", "''")


class AliyunTranslationValidator:
    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or ALI_BAICHUAN_API_KEY
        self.base_url = base_url or BASE_URL
        self.model = model or MODEL_TEXT

    def validate_translation(self, source_text: str, target_text: str, source_lang: str, target_lang: str) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "未配置阿里云模型密钥。请设置 DASHSCOPE_API_KEY，"
                "或继续使用当前环境里的兼容变量 ALI_BAICHUAN_API_KEY。"
            )

        source_lang_name = LANGUAGE_CODE_TO_NAME.get(source_lang, source_lang)
        target_lang_name = LANGUAGE_CODE_TO_NAME.get(target_lang, target_lang)

        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是翻译自动化校验器。"
                        "请把 target_text 反向翻译回 source_lang，"
                        "再判断它和 source_text 是否语义一致。"
                        "允许自然表达差异，但不允许关键事实、主语、时间、否定含义发生偏差。"
                        "只返回 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_lang": source_lang_name,
                            "target_lang": target_lang_name,
                            "source_text": source_text,
                            "target_text": target_text,
                            "return_schema": {
                                "pass": True,
                                "reason": "string",
                                "back_translation": "string",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }

        request = urllib.request.Request(
            build_chat_completions_url(self.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"阿里云模型调用失败: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            reason_text = str(exc.reason or exc)
            if "SSL" in reason_text or "EOF occurred in violation of protocol" in reason_text:
                body = self._request_via_powershell(payload)
            else:
                raise RuntimeError(f"阿里云模型调用失败: {exc}") from exc

        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))

        result = parse_json_object(content)
        return {
            "pass": bool(result.get("pass")),
            "reason": str(result.get("reason", "")).strip(),
            "back_translation": str(result.get("back_translation", "")).strip(),
        }

    def _request_via_powershell(self, payload: dict) -> dict:
        payload_json = json.dumps(payload, ensure_ascii=False)
        payload_b64 = b64encode(payload_json.encode("utf-8")).decode("ascii")
        url = ps_single_quote(build_chat_completions_url(self.base_url))
        api_key = ps_single_quote(self.api_key)
        script = f"""
$ErrorActionPreference = 'Stop'
$url = '{url}'
$apiKey = '{api_key}'
$payload = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{payload_b64}'))
$headers = @{{ Authorization = "Bearer $apiKey"; "Content-Type" = "application/json" }}
$response = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body $payload
$response | ConvertTo-Json -Depth 20 -Compress
""".strip()

        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"阿里云模型调用失败(PowerShell fallback): {stderr}")

        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "阿里云模型调用失败(PowerShell fallback): 返回内容不是合法 JSON "
                f"{completed.stdout}"
            ) from exc
