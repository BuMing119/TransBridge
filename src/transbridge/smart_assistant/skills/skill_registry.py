"""Skill 注册表：注册、查询、匹配。"""

from .skill_loader import SkillSpec


class SkillRegistry:
    """运行时 Skill 注册表（类级别单例）。"""

    _skills: dict[str, SkillSpec] = {}

    @classmethod
    def register(cls, spec: SkillSpec) -> None:
        cls._skills[spec.name] = spec

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._skills.pop(name, None)

    @classmethod
    def get(cls, name: str) -> SkillSpec | None:
        return cls._skills.get(name)

    @classmethod
    def match(cls, user_input: str) -> list[SkillSpec]:
        """按 trigger_keywords 匹配，返回按匹配度降序排列的列表。"""
        scored = []
        for spec in cls._skills.values():
            score = sum(1 for kw in spec.trigger_keywords if kw in user_input)
            if score > 0:
                scored.append((score, spec))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored]

    @classmethod
    def list_all(cls) -> list[SkillSpec]:
        return list(cls._skills.values())

    @classmethod
    def reload(cls, directory) -> None:
        """重新扫描目录，热加载 Skill。"""
        from .skill_loader import SkillLoader
        cls._skills.clear()
        for spec in SkillLoader.load_all(directory):
            cls.register(spec)
