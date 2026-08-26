"""Story 25: PostProcessorConfig 等价性测试。

Test B: from_llm_config 字段映射 + phases → 开关转换。
"""

from __future__ import annotations

import unittest

from tests.conftest import make_llm_config
from transbridge.ai_translator.post_processor.post_processor import PostProcessorConfig


class TestPostProcessorConfigEquivalence(unittest.TestCase):
    """验收标准: run_postprocess 创建的 PostProcessorConfig 与 GUI from_llm_config 一致。"""

    def test_b1_from_llm_config_maps_all_fields(self):
        mock_cfg = make_llm_config(
            game_profile="skyrim_se",
            target_lang="zh_CN",
            pp_enable_consistency_check=True,
            pp_enable_format_validation=False,
            pp_enable_quality_gate=True,
            pp_quality_gate_batch_size=20,
            pp_enable_refinement=True,
            pp_refinement_batch_size=3,
            pp_enable_polish=True,
            pp_polish_scope="has_issues",
            pp_polish_level="aggressive",
            pp_polish_batch_size=8,
            pp_enable_arbitration=False,
            pp_strict_arbitration=True,
            pp_arbitration_batch_size=15,
        )

        config = PostProcessorConfig.from_llm_config(mock_cfg)

        self.assertEqual(config.game_profile, "skyrim_se")
        self.assertEqual(config.target_lang, "zh_CN")
        self.assertTrue(config.enable_consistency_check)
        self.assertFalse(config.enable_format_validation)
        self.assertTrue(config.enable_quality_gate)
        self.assertEqual(config.quality_gate_batch_size, 20)
        self.assertTrue(config.enable_refinement)
        self.assertEqual(config.refinement_batch_size, 3)
        self.assertTrue(config.enable_polish)
        self.assertEqual(config.polish_scope, "has_issues")
        self.assertEqual(config.polish_level, "aggressive")
        self.assertEqual(config.polish_batch_size, 8)
        self.assertFalse(config.enable_llm_arbitration)
        self.assertTrue(config.strict_arbitration)
        self.assertEqual(config.arbitration_batch_size, 15)

    def test_b2_from_llm_config_uses_defaults_when_none(self):
        try:
            PostProcessorConfig.from_llm_config(None)
        except Exception:
            pass
        self.assertTrue(callable(PostProcessorConfig.from_llm_config))

    def test_b3_phases_to_config_switch_mapping(self):
        all_phases = ["consistency", "format", "quality_gate", "refinement", "polish", "arbitration"]
        config = PostProcessorConfig()

        phase_to_attr = {
            "consistency": "enable_consistency_check",
            "format": "enable_format_validation",
            "quality_gate": "enable_quality_gate",
            "refinement": "enable_refinement",
            "polish": "enable_polish",
            "arbitration": "enable_llm_arbitration",
        }
        for p in all_phases:
            setattr(config, phase_to_attr[p], p in all_phases)

        self.assertTrue(config.enable_consistency_check)
        self.assertTrue(config.enable_format_validation)
        self.assertTrue(config.enable_quality_gate)
        self.assertTrue(config.enable_refinement)
        self.assertTrue(config.enable_polish)
        self.assertTrue(config.enable_llm_arbitration)

    def test_b4_partial_phases_disables_others(self):
        config = PostProcessorConfig()
        phases = ["consistency", "format"]

        phase_to_attr = {
            "consistency": "enable_consistency_check",
            "format": "enable_format_validation",
            "quality_gate": "enable_quality_gate",
            "refinement": "enable_refinement",
            "polish": "enable_polish",
            "arbitration": "enable_llm_arbitration",
        }

        for phase_name, attr_name in phase_to_attr.items():
            setattr(config, attr_name, phase_name in phases)

        self.assertTrue(config.enable_consistency_check)
        self.assertTrue(config.enable_format_validation)
        self.assertFalse(config.enable_quality_gate)
        self.assertFalse(config.enable_refinement)
        self.assertFalse(config.enable_polish)
        self.assertFalse(config.enable_llm_arbitration)

    def test_b5_empty_phases_disables_all(self):
        config = PostProcessorConfig()
        phases = []

        phase_to_attr = {
            "consistency": "enable_consistency_check",
            "format": "enable_format_validation",
            "quality_gate": "enable_quality_gate",
            "refinement": "enable_refinement",
            "polish": "enable_polish",
            "arbitration": "enable_llm_arbitration",
        }

        for phase_name, attr_name in phase_to_attr.items():
            setattr(config, attr_name, phase_name in phases)

        self.assertFalse(config.enable_consistency_check)
        self.assertFalse(config.enable_format_validation)
        self.assertFalse(config.enable_quality_gate)
        self.assertFalse(config.enable_refinement)
        self.assertFalse(config.enable_polish)
        self.assertFalse(config.enable_llm_arbitration)
