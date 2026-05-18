import json
import re
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from base64 import b64encode

from core.config import ALI_BAICHUAN_API_KEY, BASE_URL, MODEL_TEXT, MODEL_VISION_FAST


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

# 翻译页常见示例句（与测试音频/真实识别易混淆的整段），仅作精确匹配过滤。
TRANSLATION_HINT_PHRASES = {
    "你好，很高兴认识你。",
    "很高兴认识你。",
    "Nice to meet you.",
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
    if text in TRANSLATION_HINT_PHRASES:
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


def _segment_single_language(text: str, source_lang: str, target_lang: str):
    """若同时明显命中源、目标语种（单行内混排），交给 split_mixed，不参与相邻块配对。"""
    sm = text_matches_language(text, source_lang)
    tm = text_matches_language(text, target_lang)
    if sm and tm:
        return None
    if sm:
        return source_lang
    if tm:
        return target_lang
    return None


def pick_adjacent_translation_pair(ordered_delta: list[str], source_lang: str, target_lang: str):
    """相邻两条 DOM 文本：常见布局为主行译文（较长）+ 次行/小字原文（较短）。

    顺序可与遍历一致：先源后译，或先译后源；按 len(译文)-len(原文) 优先选更像「大字+小字」的一对。
    """
    best = None
    best_key = None
    for i in range(len(ordered_delta) - 1):
        a, b = ordered_delta[i], ordered_delta[i + 1]
        la = _segment_single_language(a, source_lang, target_lang)
        lb = _segment_single_language(b, source_lang, target_lang)
        if la is None or lb is None:
            continue
        if la == source_lang and lb == target_lang:
            source_text, target_text = a, b
            score = len(target_text) - len(source_text)
        elif la == target_lang and lb == source_lang:
            source_text, target_text = b, a
            score = len(a) - len(b)
        else:
            continue
        key = (score, len(source_text) + len(target_text))
        if best is None or key > best_key:
            best = (source_text, target_text)
            best_key = key
    return best if best else (None, None)


def pick_translation_pair(texts: list[str], baseline_texts, source_lang: str, target_lang: str):
    """从相对 baseline 的新文案里取翻译对。

    优先同一节点内的双语混排；再尝试相邻两块、各为单一语种（主行译文+副行源文等）；
    最后用得分挑两条独立候选；不做全页无 baseline 回退。
    """
    meaningful_delta = filter_meaningful_texts(texts, baseline_texts=baseline_texts)

    for text in meaningful_delta:
        mixed_source, mixed_target = split_mixed_language_text(text, source_lang, target_lang)
        if mixed_source and mixed_target:
            return mixed_source, mixed_target

    adj_source, adj_target = pick_adjacent_translation_pair(meaningful_delta, source_lang, target_lang)
    if adj_source and adj_target:
        return adj_source, adj_target

    source_text = pick_best_candidate(meaningful_delta, source_lang)
    target_text = pick_best_candidate(meaningful_delta, target_lang, excluded={source_text})
    if source_text and target_text:
        return source_text, target_text

    return source_text, target_text


def extract_translation_display_snippets(
    ordered_delta: list[str],
    source_lang: str,
    target_lang: str,
    max_mono: int = 2,
) -> list[dict]:
    """先扫描所有「单行双语混排」，按总长降序；优先采用总长足够的对（避免 UI 残片盖住长句）。"""
    mixed_candidates: list[tuple[int, str, str]] = []
    for text in ordered_delta:
        mixed_source, mixed_target = split_mixed_language_text(text, source_lang, target_lang)
        if mixed_source and mixed_target:
            score = len(mixed_source) + len(mixed_target)
            mixed_candidates.append((score, mixed_source, mixed_target))
    min_substantial = 24
    if mixed_candidates:
        mixed_candidates.sort(key=lambda item: item[0], reverse=True)
        for score, ms, mt in mixed_candidates:
            if score >= min_substantial:
                return [{"type": "mixed", "source": ms, "target": mt}]
        # 仅存在很短的混排（如「その The.」）：不采信，改走单语片段 + 后续回退
        if len(mixed_candidates) == 1 and mixed_candidates[0][0] < min_substantial:
            pass
        else:
            score, ms, mt = mixed_candidates[0]
            return [{"type": "mixed", "source": ms, "target": mt}]

    snippets: list[dict] = []
    mono_count = 0
    for text in ordered_delta:
        if mono_count >= max_mono:
            break
        seg = _segment_single_language(text, source_lang, target_lang)
        if seg:
            snippets.append({"type": "mono", "lang": seg, "text": text})
            mono_count += 1
    return snippets


def derive_translation_pair_from_snippets(
    snippets: list[dict],
    source_lang: str,
    target_lang: str,
) -> tuple[str | None, str | None]:
    if not snippets:
        return None, None
    if snippets[0].get("type") == "mixed":
        return snippets[0]["source"], snippets[0]["target"]
    monos = [s for s in snippets if s.get("type") == "mono"]
    if len(monos) >= 2:
        a, b = monos[0], monos[1]
        if a["lang"] == source_lang and b["lang"] == target_lang:
            return a["text"], b["text"]
        if a["lang"] == target_lang and b["lang"] == source_lang:
            return b["text"], a["text"]
    return None, None


def resolve_translation_pair(
    texts: list[str],
    baseline_texts,
    source_lang: str,
    target_lang: str,
) -> tuple[str | None, str | None, list[dict]]:
    """先按 1～2 条展示句拼源/译文，失败则回退 pick_translation_pair。返回 (source, target, snippets)。"""
    meaningful_delta = filter_meaningful_texts(texts, baseline_texts=baseline_texts)
    snippets = extract_translation_display_snippets(meaningful_delta, source_lang, target_lang)
    source_text, target_text = derive_translation_pair_from_snippets(snippets, source_lang, target_lang)
    if source_text and target_text:
        return source_text, target_text, snippets
    fb_s, fb_t = pick_translation_pair(texts, baseline_texts, source_lang, target_lang)
    return fb_s, fb_t, snippets


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

    def validate_translation_with_screenshot(
        self,
        source_text: str,
        target_text: str,
        source_lang: str,
        target_lang: str,
        screenshot_base64: str,
        structured_snippets: list | None = None,
    ) -> dict:
        """结合截图做语义校验：用于 OCR/抓取可能不准时由视觉模型再确认。"""
        if not self.api_key:
            raise RuntimeError(
                "未配置阿里云模型密钥。请设置 DASHSCOPE_API_KEY，"
                "或继续使用当前环境里的兼容变量 ALI_BAICHUAN_API_KEY。"
            )

        source_lang_name = LANGUAGE_CODE_TO_NAME.get(source_lang, source_lang)
        target_lang_name = LANGUAGE_CODE_TO_NAME.get(target_lang, target_lang)
        vision_model = MODEL_VISION_FAST

        payload = {
            "model": vision_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是语音/对话翻译界面的自动化校验器。"
                        "你会收到：① 从页面 XML 解析出的候选源/译文与结构化片段；② 当前界面截图。"
                        "请先在截图中辨认与本次翻译相关的主文案（通常 1～2 句），区分 source_lang 与 target_lang，"
                        "再判断二者是否语义对应；若 OCR 候选与截图明显不符，以截图为准。"
                        "允许自然表达差异，但不允许关键事实、主语、时间、否定含义严重偏离。"
                        "只返回 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "source_lang": source_lang_name,
                                    "target_lang": target_lang_name,
                                    "candidate_source_text": source_text,
                                    "candidate_target_text": target_text,
                                    "structured_snippets": structured_snippets or [],
                                    "return_schema": {
                                        "pass": True,
                                        "reason": "string",
                                        "back_translation": "string",
                                        "source_read_from_screen": "string",
                                        "target_read_from_screen": "string",
                                    },
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{screenshot_base64}"
                            },
                        },
                    ],
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
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"阿里云视觉翻译校验失败: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            reason_text = str(exc.reason or exc)
            if "SSL" in reason_text or "EOF occurred in violation of protocol" in reason_text:
                body = self._request_via_powershell(payload)
            else:
                raise RuntimeError(f"阿里云视觉翻译校验失败: {exc}") from exc

        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))

        result = parse_json_object(content)
        return {
            "pass": bool(result.get("pass")),
            "reason": str(result.get("reason", "")).strip(),
            "back_translation": str(result.get("back_translation", "")).strip(),
            "source_read_from_screen": str(result.get("source_read_from_screen", "")).strip(),
            "target_read_from_screen": str(result.get("target_read_from_screen", "")).strip(),
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


class AliyunImageTranslationValidator(AliyunTranslationValidator):
    def __init__(self, api_key=None, base_url=None, model=None):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model or MODEL_VISION_FAST,
        )

    def validate_image_translation(
        self,
        screenshot_base64: str,
        source_lang: str | None = None,
        target_lang: str | None = None,
    ) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "未配置阿里云模型密钥。请设置 DASHSCOPE_API_KEY，"
                "或继续使用当前环境里的兼容变量 ALI_BAICHUAN_API_KEY。"
            )

        source_lang_name = LANGUAGE_CODE_TO_NAME.get(source_lang, source_lang or "未指定")
        target_lang_name = LANGUAGE_CODE_TO_NAME.get(target_lang, target_lang or "未指定")

        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 App 图片翻译功能的自动化校验器。"
                        "请只根据截图判断图片翻译是否真正完成。"
                        "只有当页面清晰展示图片翻译结果，且能看到源图片/源文字和对应译文时，pass 才能为 true。"
                        "如果截图还停留在相机/相册/加载中/权限页/报错页，或只有原图没有译文，必须判定为 false。"
                        "不要把普通按钮、标题、历史列表或旧页面当成翻译结果。只返回 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "expected_source_lang": source_lang_name,
                                    "expected_target_lang": target_lang_name,
                                    "return_schema": {
                                        "pass": True,
                                        "reason": "string",
                                        "source_text": "string",
                                        "translated_text": "string",
                                        "evidence": "string",
                                    },
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{screenshot_base64}"
                            },
                        },
                    ],
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
            raise RuntimeError(f"阿里云视觉模型调用失败: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            reason_text = str(exc.reason or exc)
            if "SSL" in reason_text or "EOF occurred in violation of protocol" in reason_text:
                body = self._request_via_powershell(payload)
            else:
                raise RuntimeError(f"阿里云视觉模型调用失败: {exc}") from exc

        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))

        result = parse_json_object(content)
        return {
            "pass": bool(result.get("pass")),
            "reason": str(result.get("reason", "")).strip(),
            "source_text": str(result.get("source_text", "")).strip(),
            "translated_text": str(result.get("translated_text", "")).strip(),
            "evidence": str(result.get("evidence", "")).strip(),
        }
