"""Skill 定义加载器：TOML 文件 → SkillSpec。"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# m39: prompt_template 最大长度限制，防止恶意 TOML 注入超长 prompt
MAX_PROMPT_TEMPLATE_LENGTH = 4096


@dataclass
class SkillSpec:
    """用户自定义 Skill 的运行时表示。"""
    name: str
    display_name: str
    description: str = ""
    version: str = "1.0"
    enabled: bool = True
    trigger_keywords: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    prompt_template: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @property
    def slug(self) -> str:
        return self.name.replace(" ", "_").lower()


class SkillLoader:
    """从 TOML 文件加载 Skill 定义。"""

    @staticmethod
    def load(path: Path) -> SkillSpec | None:
        """解析单个 TOML 文件 → SkillSpec，失败返回 None。"""
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError) as exc:
            import logging
            logging.getLogger("SkillLoader").warning(f"解析 Skill 文件失败: {path} — {exc}")
            return None

        try:
            meta = data.get("meta", {})
            trigger = data.get("trigger", {})
            prompt = data.get("prompt", {})
            tools = data.get("tools", {})

            # m39: 限制 prompt_template 长度，防止恶意 TOML 注入超长 prompt
            prompt_template = prompt.get("template", "")
            if len(prompt_template) > MAX_PROMPT_TEMPLATE_LENGTH:
                import logging
                logging.getLogger("SkillLoader").warning(
                    "Skill '%s' prompt_template 过长 (%d 字符)，已截断至 %d",
                    meta.get('name', path.stem), len(prompt_template),
                    MAX_PROMPT_TEMPLATE_LENGTH,
                )
                prompt_template = prompt_template[:MAX_PROMPT_TEMPLATE_LENGTH]

            return SkillSpec(
                name=meta.get("name", path.stem),
                display_name=meta.get("display_name", path.stem),
                description=meta.get("description", ""),
                version=meta.get("version", "1.0"),
                enabled=meta.get("enabled", True),
                trigger_keywords=trigger.get("keywords", []),
                required_tools=trigger.get("requires_tools", []),
                prompt_template=prompt_template,
                allowed_tools=tools.get("allowed", []),
                source_path=path,
            )
        except Exception as exc:
            import logging
            logging.getLogger("SkillLoader").warning(f"Skill 定义不完整: {path} — {exc}")
            return None

    @staticmethod
    def load_all(directory: Path) -> list[SkillSpec]:
        """扫描目录，加载所有 .toml 文件。"""
        skills = []
        if not directory.exists():
            return skills
        for f in sorted(directory.glob("*.toml")):
            spec = SkillLoader.load(f)
            if spec is not None and spec.enabled:
                skills.append(spec)
        return skills
