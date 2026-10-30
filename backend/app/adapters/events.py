"""EventsAdapter（SPEC §17.2）：公開活動公告 → pub.events（待審），每 6 小時。

- 來源網址須符合 pub.source_whitelist（source_class = 'event_notice'）。
- normalize() 第一步是 strip_identity()（與輿情同一套 C-8 規則：發文者、電話、Email、@ 提及）。
- 欄位擷取：有 ANTHROPIC_API_KEY 用 Claude 結構化輸出；否則規則式擷取（[MOCK]）。
  日期或時間抽不出來的公告不收；一律寫成 pending_review，由機關人員審核後才推給民眾。
- 抓取：設定 EVENT_FEEDS（RSS／Atom 網址 JSON 陣列）時讀真實公告（未以真實來源驗證）；
  否則讀 fixtures/event_notices.json。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from fnmatch import fnmatch
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from app.adapters.base import FetchContext, RawRecord, mock_notice
from app.adapters.mocks.events import MockEventClient
from app.config import get_settings
from app.db.models_pub import EVENT_CATEGORIES
from app.deidentify.strip_identity import strip_identity

log = logging.getLogger(__name__)

SOURCE_CLASS = "event_notice"
DESCRIPTION_MAX = 400
DEFAULT_DURATION = timedelta(hours=2)
TAGS = ("bp", "glucose", "weight", "steps", "indoor", "outdoor", "gentle", "free")

# 依序比對（前面優先）：類別關鍵字
CATEGORY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("vaccine", ("疫苗", "接種", "流感", "打針")),
    ("walk", ("健走", "步行", "走路", "散步")),
    ("measure", ("量血壓", "血壓量測", "量測", "篩檢", "檢測", "血糖")),
    ("exercise", ("運動", "體操", "太極", "伸展", "有氧", "肌力", "瑜珈", "舞")),
    ("nutrition", ("共餐", "營養", "飲食", "料理", "烹飪", "食譜")),
    ("education", ("講座", "衛教", "課程", "說明會", "宣導")),
    ("social", ("聯誼", "唱歌", "手作", "桌遊", "茶會", "電影")),
)
TAG_WORDS: dict[str, tuple[str, ...]] = {
    "bp": ("血壓",),
    "glucose": ("血糖", "糖尿"),
    "weight": ("體重", "體脂", "減重"),
    "steps": ("健走", "走路", "步行", "散步", "萬步"),
    "indoor": ("室內", "活動中心", "教室", "禮堂", "會議室", "衛生所"),
    "outdoor": ("戶外", "公園", "河濱", "步道", "操場"),
    "gentle": ("長者", "長輩", "銀髮", "溫和", "坐姿", "椅子", "樂齡"),
    "free": ("免費", "免報名費", "不收費"),
}

_DATE = re.compile(r"(?:(\d{4})[/年.-])?(\d{1,2})\s*[/月.-]\s*(\d{1,2})\s*日?")
_TIME = re.compile(
    r"(上午|早上|中午|下午|晚上)?\s*(\d{1,2})\s*[:：點時]\s*(\d{2})?"
    r"(?:\s*分?\s*(?:至|到|~|～|-|–|—)\s*(上午|早上|中午|下午|晚上)?\s*(\d{1,2})\s*[:：點時]\s*(\d{2})?)?"
)
_VENUE = re.compile(r"(?:地點|地址|集合地點|集合)\s*[:：]\s*([^\n，。；]+)")
_POINTS = re.compile(r"(?:可獲|可得|贈|送|集點|健康點數|點數)[^\n\d]{0,6}(\d{1,3})\s*點")
_CAPACITY = re.compile(r"(?:名額|限額|限)\s*[:：]?\s*(\d{1,4})\s*(?:名|人|位)")
_BRACKET = re.compile(r"^[【\[]([^】\]]+)[】\]]\s*")


class EventClient(Protocol):
    def items(self, since: datetime) -> list[dict[str, Any]]: ...


@dataclass
class Extracted:
    title: str
    category: str
    start_at: datetime
    end_at: datetime
    venue: str
    organizer: str
    description: str
    capacity: int | None = None
    points_reward: int = 0
    tags: list[str] = field(default_factory=list)
    site_id: str | None = None
    village_code: str | None = None
    extracted_by: str = "rules"


@dataclass(frozen=True)
class Gazetteer:
    """據點與里的名稱（pub，非個資），用來把公告對到 site_id／village_code。"""

    sites: dict[str, str]  # 名稱 → site_id
    villages: dict[str, str]  # 名稱 → village_code
    site_village: dict[str, str]  # site_id → village_code


def _hour(period: str | None, hour: int) -> int:
    if period in ("下午", "晚上") and hour < 12:
        return hour + 12
    if period == "中午" and hour < 6:
        return hour + 12
    return hour


def _year_for(month: int, day: int, today: date) -> int:
    candidate = date(today.year, month, day)
    return today.year + 1 if candidate < today - timedelta(days=60) else today.year


def parse_when(text: str, today: date, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """「11/8（六）上午9:00至11:00」「11月8日 下午2點」→ (開始, 結束)。

    抽不出日期或時間就回 None。"""
    m_date = _DATE.search(text)
    if not m_date:
        return None
    year_s, month_s, day_s = m_date.groups()
    month, day = int(month_s), int(day_s)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        year = int(year_s) if year_s else _year_for(month, day, today)
        on = date(year, month, day)
    except ValueError:
        return None
    m_time = _TIME.search(text, m_date.end())
    if not m_time:
        return None
    p1, h1, m1, p2, h2, m2 = m_time.groups()
    start_h = _hour(p1, int(h1))
    if start_h > 23:
        return None
    start = datetime.combine(on, datetime.min.time(), tz) + timedelta(
        hours=start_h, minutes=int(m1 or 0)
    )
    if h2 is None:
        return start, start + DEFAULT_DURATION
    end_h = _hour(p2 or (p1 if int(h2) > int(h1) else "下午"), int(h2))
    end = datetime.combine(on, datetime.min.time(), tz) + timedelta(
        hours=min(end_h, 23), minutes=int(m2 or 0)
    )
    return (start, end) if end > start else (start, start + DEFAULT_DURATION)


def _category(text: str) -> str:
    for category, words in CATEGORY_WORDS:
        if any(w in text for w in words):
            return category
    return "social"


def _tags(text: str) -> list[str]:
    return [tag for tag, words in TAG_WORDS.items() if any(w in text for w in words)]


def _match(text: str, names: dict[str, str]) -> str | None:
    for name in sorted(names, key=len, reverse=True):
        if name and name in text:
            return names[name]
    return None


def extract_rules(
    clean: dict[str, Any], *, today: date, tz: ZoneInfo, gazetteer: Gazetteer
) -> Extracted | None:
    """規則式擷取（無 LLM 金鑰時；[MOCK]）。"""
    text = str(clean.get("text") or "")
    first_line = (str(clean.get("title") or "") or text.split("\n", 1)[0]).strip()
    bracket = _BRACKET.match(first_line)
    title = _BRACKET.sub("", first_line).strip() or first_line
    when = parse_when(text, today, tz)
    if not title or when is None:
        return None
    venue_m = _VENUE.search(text)
    venue = venue_m.group(1).strip() if venue_m else ""
    points_m = _POINTS.search(text)
    capacity_m = _CAPACITY.search(text)
    blob = f"{title}\n{text}"
    site_id = _match(venue, gazetteer.sites) or _match(blob, gazetteer.sites)
    village = _match(blob, gazetteer.villages) or (
        gazetteer.site_village.get(site_id) if site_id else None
    )
    organizer = (bracket.group(1) if bracket else "") or str(clean.get("organizer") or "")
    return Extracted(
        title=title[:80],
        category=_category(blob),
        start_at=when[0],
        end_at=when[1],
        venue=venue[:80] or organizer or "—",
        organizer=organizer[:60] or "—",
        description=text[:DESCRIPTION_MAX],
        capacity=int(capacity_m.group(1)) if capacity_m else None,
        points_reward=min(int(points_m.group(1)), 200) if points_m else 0,
        tags=_tags(blob),
        site_id=site_id,
        village_code=village,
    )


EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "is_event": {"type": "boolean"},
            "title": {"type": "string"},
            "organizer": {"type": "string"},
            "category": {"type": "string", "enum": list(EVENT_CATEGORIES)},
            "start_local": {"type": "string", "description": "YYYY-MM-DDTHH:MM（台灣時間）"},
            "end_local": {"type": "string", "description": "YYYY-MM-DDTHH:MM；未寫明則空字串"},
            "venue": {"type": "string"},
            "capacity": {"type": "integer", "description": "未寫明則 0"},
            "points_reward": {"type": "integer", "description": "參加可得點數；未寫明則 0"},
            "tags": {"type": "array", "items": {"type": "string", "enum": list(TAGS)}},
        },
        "required": [
            "is_event",
            "title",
            "organizer",
            "category",
            "start_local",
            "end_local",
            "venue",
            "capacity",
            "points_reward",
            "tags",
        ],
        "additionalProperties": False,
    },
}


def extract_llm(
    clean: dict[str, Any], *, today: date, tz: ZoneInfo, gazetteer: Gazetteer
) -> Extracted | None:
    """Claude 結構化擷取。輸入已去識別；日期一律再以程式驗證（抽錯日期比不收更糟）。"""
    from app.llm.client import create, text_of

    text = str(clean.get("text") or "")
    message = create(
        agent="nlp",
        system=(
            "你從社區公告擷取活動資訊。只有明確寫出日期與時間的健康、運動、衛教、共餐、"
            "疫苗或長者聯誼活動才算 is_event=true；不確定的欄位寧可留空，不要猜。"
        ),
        messages=[{"role": "user", "content": f"今天：{today.isoformat()}\n\n{text}"}],
        max_tokens=1500,
        output_format=EXTRACT_SCHEMA,
    )
    if message.stop_reason == "refusal":
        return None
    data = json.loads(text_of(message))
    if not data["is_event"] or not data["title"] or not data["start_local"]:
        return None
    start = datetime.fromisoformat(data["start_local"]).replace(tzinfo=tz)
    end = (
        datetime.fromisoformat(data["end_local"]).replace(tzinfo=tz)
        if data["end_local"]
        else start + DEFAULT_DURATION
    )
    if end <= start or start.date() < today - timedelta(days=1):
        return None
    blob = f"{data['title']}\n{data['venue']}\n{text}"
    site_id = _match(data["venue"], gazetteer.sites) or _match(blob, gazetteer.sites)
    return Extracted(
        title=data["title"][:80],
        category=data["category"],
        start_at=start,
        end_at=end,
        venue=(data["venue"] or data["organizer"] or "—")[:80],
        organizer=(data["organizer"] or "—")[:60],
        description=text[:DESCRIPTION_MAX],
        capacity=data["capacity"] or None,
        points_reward=max(0, min(int(data["points_reward"]), 200)),
        tags=[tag for tag in data["tags"] if tag in TAGS],
        site_id=site_id,
        village_code=_match(blob, gazetteer.villages)
        or (gazetteer.site_village.get(site_id) if site_id else None),
        extracted_by="claude",
    )


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
    """RSS 2.0 或 Atom → 原始公告（title、link、description、published）。"""
    root = ET.fromstring(xml_text)  # 只讀白名單內的公開公告來源
    atom = "{http://www.w3.org/2005/Atom}"
    out: list[dict[str, Any]] = []
    for item in root.iter("item"):
        out.append(
            {
                "source_class": SOURCE_CLASS,
                "source_url": (item.findtext("link") or "").strip(),
                "title": (item.findtext("title") or "").strip(),
                "text": f"{item.findtext('title') or ''}\n{item.findtext('description') or ''}",
                "published": item.findtext("pubDate"),
            }
        )
    for entry in root.iter(f"{atom}entry"):
        link = entry.find(f"{atom}link")
        title = entry.findtext(f"{atom}title") or ""
        body = entry.findtext(f"{atom}summary") or entry.findtext(f"{atom}content") or ""
        out.append(
            {
                "source_class": SOURCE_CLASS,
                "source_url": (link.get("href") if link is not None else "") or "",
                "title": title.strip(),
                "text": f"{title}\n{body}",
                "published": entry.findtext(f"{atom}updated"),
            }
        )
    return out


class FeedEventClient:
    def __init__(self, feeds: Sequence[str]) -> None:
        self.feeds = list(feeds)

    def items(self, since: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            for url in self.feeds:
                try:
                    out += parse_feed(client.get(url).text)
                except (httpx.HTTPError, ET.ParseError):
                    log.exception("活動公告來源讀取失敗：%s", url)
        return out


def default_event_client() -> EventClient:
    feeds = get_settings().event_feeds
    if feeds:
        return FeedEventClient(json.loads(feeds))
    mock_notice("EventsAdapter", "未設定 EVENT_FEEDS")
    return MockEventClient()


def dedupe_key(url: str, title: str, start: datetime) -> str:
    raw = f"{url}|{title}|{start.astimezone(UTC).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class EventsAdapter:
    source = "events"

    def __init__(
        self,
        whitelist: Sequence[str],
        gazetteer: Gazetteer,
        client: EventClient | None = None,
        *,
        use_llm: bool | None = None,
    ) -> None:
        self.whitelist = list(whitelist)
        self.gazetteer = gazetteer
        self._client = client
        self.use_llm = use_llm

    @property
    def client(self) -> EventClient:
        if self._client is None:
            self._client = default_event_client()
        return self._client

    def schedule(self) -> str:
        return "15 */6 * * *"

    def allowed(self, url: str) -> bool:
        return any(fnmatch(url, pattern) for pattern in self.whitelist)

    def fetch(self, since: datetime, ctx: FetchContext | None = None) -> list[RawRecord]:
        now = datetime.now(UTC)
        return [RawRecord(self.source, item, now) for item in self.client.items(since)]

    def normalize(self, raw: RawRecord) -> list[dict[str, Any]]:
        clean = strip_identity(raw.payload)
        url = str(clean.get("source_url") or "")
        if not url or not self.allowed(url):
            log.warning("活動公告來源不在白名單，略過：%s", url or "（無網址）")
            return []
        tz = ZoneInfo(get_settings().timezone)
        today = raw.fetched_at.astimezone(tz).date()
        use_llm = self.use_llm
        if use_llm is None:
            from app.llm.client import llm_available

            use_llm = llm_available()
        extracted: Extracted | None = None
        if use_llm:
            try:
                extracted = extract_llm(clean, today=today, tz=tz, gazetteer=self.gazetteer)
            except Exception:
                log.exception("LLM 擷取活動失敗，改用規則式")
        if extracted is None:
            if not use_llm:
                mock_notice("EventsAdapter.extract", "未設定 ANTHROPIC_API_KEY，活動欄位以規則擷取")
            extracted = extract_rules(clean, today=today, tz=tz, gazetteer=self.gazetteer)
        if extracted is None or extracted.end_at <= raw.fetched_at:
            return []
        key = dedupe_key(url, extracted.title, extracted.start_at)
        return [
            {
                "id": f"EV-C{key[:10].upper()}",
                "title": extracted.title,
                "category": extracted.category,
                "description": extracted.description,
                "organizer": extracted.organizer,
                "site_id": extracted.site_id,
                "village_code": extracted.village_code,
                "venue": extracted.venue,
                "start_at": extracted.start_at,
                "end_at": extracted.end_at,
                "capacity": extracted.capacity,
                "points_reward": extracted.points_reward,
                "tags": extracted.tags,
                "source": "crawl",
                "source_url": url,
                "dedupe_key": key,
                "extracted_by": extracted.extracted_by,
                "status": "pending_review",
                "created_by": "crawler",
            }
        ]
