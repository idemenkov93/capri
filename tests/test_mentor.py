"""1.9: наставник — локальная модель + свои файлы + интернет только по теме."""
import pytest

from capri import mentor as M
from capri.live import glossary as G


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "Уроки"
    les = r / "2026-11-05 0900 — AD"
    les.mkdir(parents=True)
    (les / "конспект.md").write_text("# Active Directory und GPO\n\n## Главное\nGruppenrichtlinien (GPO) verknüpft man "
                                     "mit einer OU. Vorrang: LSDOU — Local, Site, Domain, OU.\n\nПро DNS ничего.\n",
                                     encoding="utf-8")
    mats = r / "Материалы" / "Kurs"
    mats.mkdir(parents=True)
    (mats / "Netzwerk.md").write_text("# Netzwerk\n\nDHCP vergibt IP-Adressen automatisch (DORA).\n", encoding="utf-8")
    monkeypatch.setattr(G.N, "lessons_dir", lambda: r)
    monkeypatch.setattr(G, "csv_dir", lambda: tmp_path / "gl")
    G._cache["key"] = None
    G.add("Gruppenrichtlinie (GPO)", "групповая политика", "настройки для OU", r)
    return r


def test_keywords_drop_stopwords():
    assert M.keywords("Что такое GPO и как она работает в AD?") == ["gpo", "ad"]


def test_retrieve_finds_glossary_and_lesson(root):
    hits, found = M.retrieve("Как работает GPO и LSDOU?", root)
    assert hits[0]["label"] == "словарь курса" and "групповая политика" in hits[0]["text"]
    assert any("LSDOU" in h["text"] for h in hits[1:]) and found >= 2


def test_safe_query_strips_personal():
    q = M.safe_query("наставник: у Igor ошибка на 192.168.1.15, пишет igor@firma.de, C:\\Users\\Igor\\x.txt, "
                     "тел +49 170 1234567 — DHCP не выдаёт адрес", names=["Igor"])
    assert "192.168" not in q and "@" not in q and "Igor" not in q and "Users" not in q and "1234567" not in q
    assert "DHCP" in q


class Agent:
    cfg = {"user_name": "Igor"}
    prefs = None

    def __init__(self):
        self.prompts = []
        agent = self

        class LLM:
            model = "qwen3:14b"

            def chat(self, messages, tools=None):
                agent.prompts.append(messages[0]["content"])
                return {"content": "<think>x</think>GPO — это групповая политика [M1]."}
        self.llm = LLM()

    def _text_llm(self):
        return self.llm

    def ready(self, p):
        return False


def test_answer_local_with_own_files_no_web_when_enough(root, monkeypatch):
    monkeypatch.setattr(M, "web_search", lambda q: pytest.fail("в интернет не ходим — хватает своих файлов"))
    monkeypatch.setattr(M, "youtube", lambda q, n=3: [{"title": "GPO erklärt", "url": "https://www.youtube.com/watch?v=abcdefghijk"}])
    a = Agent()
    text, info = M.answer(a, "Как работает GPO и LSDOU?", root=root, names=["Igor"])
    assert "групповая политика" in text and "<think>" not in text
    assert "[M1]" in a.prompts[0] and "LSDOU" in a.prompts[0]
    assert "watch?v=abcdefghijk" in text and "qwen3:14b" in text and info["web"] == 0


def test_answer_goes_online_when_files_have_nothing(root, monkeypatch):
    seen = []
    monkeypatch.setattr(M, "web_search", lambda q: seen.append(q) or [{"title": "Kubernetes", "url": "https://k8s.io",
                                                                       "snippet": "Container-Orchestrierung"}])
    monkeypatch.setattr(M, "page_text", lambda u: "Kubernetes orchestriert Container.")
    monkeypatch.setattr(M, "youtube", lambda q, n=3: [])
    a = Agent()
    text, info = M.answer(a, "Igor спрашивает: что такое Kubernetes?", root=root, names=["Igor"])
    assert seen and "Igor" not in seen[0] and "Kubernetes" in seen[0]
    assert "[W1]" in a.prompts[0] and "https://k8s.io" in text and info["web"] == 1


def test_offline_mode_never_touches_network(root, monkeypatch):
    monkeypatch.setattr(M, "web_search", lambda q: pytest.fail("без интернета"))
    monkeypatch.setattr(M, "youtube", lambda q, n=3: pytest.fail("без интернета"))
    text, info = M.answer(Agent(), "что такое Kubernetes", online=False, root=root)
    assert "без интернета" in text.lower()


def test_parse_ddg_and_bing_and_youtube():
    ddg = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fad&amp;rut=1">'
           'Active <b>Directory</b></a><a class="result__snippet" href="x">Was ist <b>AD</b>?</a>'
           '<a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad=1">Werbung</a>')
    assert M.parse_ddg(ddg) == [{"title": "Active Directory", "url": "https://example.com/ad", "snippet": "Was ist AD?"}]
    bing = '<li class="b_algo"><h2><a href="https://learn.microsoft.com/gpo">GPO &amp; AD</a></h2><p>Gruppen…</p></li>'
    assert M.parse_bing(bing)[0] == {"title": "GPO & AD", "url": "https://learn.microsoft.com/gpo", "snippet": "Gruppen…"}
    yt = ('"videoRenderer":{"videoId":"dQw4w9WgXcQ","thumbnail":{"x":1},"title":{"runs":[{"text":"AD einfach erkl\\u00e4rt"}]}'
          '"videoRenderer":{"videoId":"dQw4w9WgXcQ","title":{"runs":[{"text":"dup"}]}')
    assert M.parse_youtube(yt) == [{"title": "AD einfach erklärt", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]


@pytest.mark.parametrize("phrase,do", [
    ("наставник: как работает Kerberos?", "ask"),
    ("по учёбе: что такое LSDOU", "ask"),
    ("вопрос по работе: принтер не печатает в сети", "ask"),
    ("что мне подучить?", "plan"),
    ("план обучения", "plan"),
    ("видео про Docker", "video"),
    ("какая у меня локальная модель", "model"),
    ("да, ставь умную модель", "install"),
])
def test_phrases(phrase, do):
    r = M.route(Agent(), phrase)
    assert r.actions[0].kind == "mentor" and r.actions[0].params["do"] == do


def test_plan_adds_videos(root, monkeypatch):
    class A(Agent):
        def __init__(self):
            super().__init__()
            self.llm.chat = lambda messages, tools=None: {"content": "1. **Azure** — облако\nИщи: Azure AZ-900 deutsch"}
    monkeypatch.setattr(M, "youtube", lambda q, n=3: [{"title": "AZ-900 Kurs", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}])
    out = M.plan(A(), root=root)
    assert "AZ-900 Kurs" in out and "Active Directory und GPO" in M.topics(root)


def test_laptop_without_gpu_uses_fast_cloud_unless_private(root, monkeypatch):
    monkeypatch.setattr(M, "has_gpu", lambda: False)
    monkeypatch.setattr(M, "web_search", lambda q: [])
    monkeypatch.setattr(M, "youtube", lambda q, n=3: [])

    class Prefs(dict):
        def set(self, k, v):
            self[k] = v

    class Cloud(Agent):
        def __init__(self):
            super().__init__()
            self.prefs = Prefs()
            self.asked = []

        def ready(self, p):
            return p == "groq"

        def _ccfg(self, p):
            return {}

        def cloud_ask(self, p, prompt, cfg, images=None, system=""):
            self.asked.append(p)
            return "Ответ из облака"
    a = Cloud()
    text, info = M.answer(a, "Как работает GPO?", root=root)
    assert a.asked == ["groq"] and "Ответ из облака" in text and "нет видеокарты" in text and not a.prompts
    M.route(a, "наставник только локально")
    assert a.prefs["mentor_brain"] == "local"
    text, info = M.answer(a, "Как работает GPO?", root=root)
    assert a.prompts and "🔒" in text
    monkeypatch.setattr(M, "has_gpu", lambda: True)
    M.route(a, "наставник авто")
    assert M.local_ok(a)
