"""Personal News Agent 的可注册斜杠命令 Skills。"""

from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.registry import SkillRegistry, build_default_registry

__all__ = [
    "SkillContext",
    "SkillRegistry",
    "SkillResult",
    "SkillSpec",
    "build_default_registry",
]
