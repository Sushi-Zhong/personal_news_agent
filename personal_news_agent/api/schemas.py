from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from personal_news_agent.core.models import TimeRange


class SearchRequest(BaseModel):
    query: str
    category_scope: list[str] | None = None
    source_scope: list[str] | None = None
    time_range: str | None = "7d"
    max_results: int = Field(default=20, ge=1, le=100)


class DeepDiveRequest(BaseModel):
    query: str
    category_scope: list[str] | None = None
    source_scope: list[str] | None = None
    rounds: int = Field(default=2, ge=1, le=4)
    breadth: int = Field(default=4, ge=1, le=8)


class NativeSearchIngestRequest(BaseModel):
    query: str
    category_scope: list[str] | None = None
    source_scope: list[str] | None = None
    max_results: int = Field(default=20, ge=1, le=100)
    fetch_articles: int = Field(default=10, ge=0, le=50)
    follow_depth: int = Field(default=0, ge=0, le=1)
    follow_limit_per_article: int = Field(default=2, ge=0, le=5)


class TopicViewRequest(BaseModel):
    topic: str
    category_scope: list[str] | None = None
    source_scope: list[str] | None = None
    max_articles: int = Field(default=16, ge=1, le=50)


class TopicCreateRequest(BaseModel):
    user_id: str = "default"
    text: str | None = None
    title: str | None = None
    topic_type: str = "user"
    category_scope: list[str] | None = None
    schedule: str = "*/20 * * * *"
    refresh_now: bool = True


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    user_id: str = "default"
    message: str
    topic: str | None = None
    category_scope: list[str] | None = None
    use_llm: bool = False


class ReportRequest(BaseModel):
    user_id: str = "default"
    topic: str
    category_scope: list[str] = []
    time_range: str = "30d"
    report_type: str = "timeline_analysis"


class ProfileRequest(BaseModel):
    user_id: str = "default"
    interests: list[str] = []
    negative_interests: list[str] = []
    preferred_categories: list[str] = []
    preferred_sources: list[str] = []
    output_style: str = "concise"


class FeedbackRequest(BaseModel):
    user_id: str = "default"
    target_type: str
    target_id: str
    feedback_type: str


class TaskRequest(BaseModel):
    user_id: str = "default"
    task_type: str
    schedule: str
    category_scope: list[str] = []
    source_scope: list[str] = []
    topics: list[str] = []
    output_style: str | None = None
    delivery_channel: str = "in_app"


class DueTasksRequest(BaseModel):
    user_id: str = "default"
    limit: int = Field(default=10, ge=1, le=50)


class NotificationReadRequest(BaseModel):
    user_id: str = "default"


class DueCrawlRequest(BaseModel):
    category: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    per_section_limit: int = Field(default=10, ge=1, le=50)
    fetch_articles: int = Field(default=1, ge=0, le=10)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=6, max_length=128)
    confirm_password: str = Field(min_length=6, max_length=128)
    real_name: str = Field(min_length=2, max_length=40)
    mobile: str = Field(min_length=11, max_length=11)
    id_card: str | None = Field(default=None, min_length=15, max_length=18)


class LoginRequest(BaseModel):
    username: str
    password: str


class OnboardingRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    user_id: str
    display_name: str | None = None
    self_description: str = Field(default="", max_length=500)
    age: int | None = None
    gender: str = "不透露"
    zodiac: str = "不透露"
    preferred_categories: list[str] = []
    watch_keywords: list[str] = []
    negative_keywords: list[str] = []
    model_key: str = "yuanrong-personal-assistant"
    output_style: str = "简洁分析型"


def parse_range(value: str | None) -> TimeRange | None:
    if not value:
        return None
    if value.endswith("d") and value[:-1].isdigit():
        return TimeRange(days=int(value[:-1]))
    return TimeRange(days=7)


def mask_mobile(value: str) -> str:
    return value[:3] + "****" + value[-4:] if len(value) == 11 else value
