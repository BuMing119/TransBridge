"""The quality pipeline inherits its stage configuration from LLMConfig."""

from __future__ import annotations

import unittest

from tests.conftest import make_llm_config
from transbridge.ai_translator.post_processor.post_processor import PostProcessorConfig


class TestPostProcessorConfigEquivalence(unittest.TestCase):
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
