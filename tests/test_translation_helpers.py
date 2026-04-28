from core.translation import extract_visible_texts, pick_translation_pair, split_mixed_language_text


SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy index="0" class="hierarchy">
  <android.view.View index="0" text="" content-desc="双耳机模式" />
  <android.view.View index="1" text="" content-desc="我的语言" />
  <android.view.View index="2" text="" content-desc="对方语言" />
  <android.view.View index="3" text="" content-desc="English" />
  <android.view.View index="4" text="" content-desc="中文" />
  <android.view.View index="5" text="你好，欢迎使用会议记录" content-desc="" />
  <android.view.View index="6" text="" content-desc="Hello, welcome to meeting notes" />
</hierarchy>
"""


def test_extract_visible_texts_collects_text_and_content_desc():
    texts = extract_visible_texts(SAMPLE_XML)
    assert "双耳机模式" in texts
    assert "你好，欢迎使用会议记录" in texts
    assert "Hello, welcome to meeting notes" in texts


def test_pick_translation_pair_filters_static_ui_labels():
    texts = extract_visible_texts(SAMPLE_XML)
    baseline = ["双耳机模式", "我的语言", "对方语言", "English", "中文"]
    source_text, target_text = pick_translation_pair(texts, baseline, "zh", "en")
    assert source_text == "你好，欢迎使用会议记录"
    assert target_text == "Hello, welcome to meeting notes"


def test_split_mixed_language_text_for_english_to_chinese():
    mixed = "Hello, nice to meet you. This is a translation test. 你好，很高兴见到你。这是一次翻译测试。"
    source_text, target_text = split_mixed_language_text(mixed, "en", "zh")
    assert source_text == "Hello, nice to meet you. This is a translation test."
    assert target_text == "你好，很高兴见到你。这是一次翻译测试。"


def test_pick_translation_pair_supports_single_bilingual_node():
    texts = [
        "双耳机模式",
        "未连接",
        "我的语言",
        "对方语言",
        "English",
        "中文",
        "Hello, nice to meet you. This is a translation test. 你好，很高兴见到你。这是一次翻译测试。",
    ]
    baseline = ["双耳机模式", "未连接", "我的语言", "对方语言", "English", "中文"]
    source_text, target_text = pick_translation_pair(texts, baseline, "en", "zh")
    assert source_text == "Hello, nice to meet you. This is a translation test."
    assert target_text == "你好，很高兴见到你。这是一次翻译测试。"


def test_pick_translation_pair_ignores_language_labels_and_translate_button():
    texts = [
        "单向模式",
        "English",
        "中文（简体）",
        "翻译",
        "Hello, nice to meet you.",
        "你好，很高兴见到你。",
    ]
    baseline = ["单向模式", "English", "中文（简体）", "翻译"]

    source_text, target_text = pick_translation_pair(texts, baseline, "en", "zh")

    assert source_text == "Hello, nice to meet you."
    assert target_text == "你好，很高兴见到你。"
