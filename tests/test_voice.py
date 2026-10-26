from capri.voice.lang import clean_for_speech, detect_lang
from capri.voice.tts import voice_url


def test_detect_lang():
    assert detect_lang("Привет, Игорь! Всё ок.") == "ru"
    assert detect_lang("Hallo Igor, ich bin bereit. Was machen wir?") == "de"
    assert detect_lang("Hey Igor, what's up?") == "en"
    assert detect_lang("שלום איגור, מה שלומך?") == "he"
    assert detect_lang("🚀", fallback="de") == "de"


def test_clean_for_speech():
    text = "**DHCP** выдаёт IP 🚀. Смотри https://example.com и `ipconfig`.\n```\ncode\n```"
    out = clean_for_speech(text)
    assert "**" not in out and "https" not in out and "🚀" not in out
    assert "ipconfig" in out and "код смотри в чате" in out


def test_voice_url():
    assert voice_url("ru_RU-irina-medium", ".onnx").endswith("/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx")
