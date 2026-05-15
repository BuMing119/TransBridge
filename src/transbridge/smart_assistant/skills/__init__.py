"""Skill 子包 — 用户自定义 Skill 的加载、注册与执行。

注册模式:
  SkillRegistry 集中管理，通过 SkillLoader.load_all() 扫描 TOML 文件显式注册。
  推荐调用 register_all(directory) 一次性完成加载与注册，无需手动操作内部类。
"""

from .skill_loader import SkillSpec
from .skill_registry import SkillRegistry
from .skill_executor import SkillExecutor


def register_all(directory: "Path") -> None:
    """显式注册所有 Skill（推荐 API）。

    扫描 directory 下所有 .toml 文件，加载并注册到 SkillRegistry。
    等价于 SkillRegistry.reload(directory)。
    """
    SkillRegistry.reload(directory)


__all__ = ["SkillSpec", "SkillRegistry", "SkillExecutor", "register_all"]
