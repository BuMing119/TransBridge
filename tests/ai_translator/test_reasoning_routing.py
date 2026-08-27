from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import patch

from tests.conftest import make_llm_config
from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.infra.llm_reasoning import ReasoningIntent, ReasoningScopedLLMClient


class _FakeClient:
    def chat(self, _messages, _max_tokens=0) -> str:
        return "OK"

    def cancel(self) -> None:
        pass


def test_auto_translator_scopes_translation_calls_as_direct() -> None:
    config = make_llm_config()
    translator = AutoTranslator(
        TranslatorConfig(config, "fixture.esp"),
        llm_client=_FakeClient(),
    )

    assert translator._raw_llm.reasoning_intent is ReasoningIntent.PREFER_DIRECT


def test_strict_pipeline_routes_only_arbiter_to_optional_low_client() -> None:
    config = make_llm_config(pp_strategy="strict", pp_enable_arbitration=True)
    profile = AiExecutionProfile.from_config("polish", config)
    direct = _FakeClient()
    low = _FakeClient()

    pipeline = ProofreadPipeline.create(
        profile=profile,
        llm_client=direct,
        arbitration_llm_client=low,
    )

    assert pipeline._processor._llm_client is direct
    assert pipeline._processor._arbiter._llm is low


def test_proofread_pipeline_keeps_its_single_stage_on_direct_client() -> None:
    config = make_llm_config(pp_strategy="proofread")
    profile = AiExecutionProfile.from_config("polish", config)
    direct = _FakeClient()

    pipeline = ProofreadPipeline.create(
        profile=profile,
        llm_client=direct,
        arbitration_llm_client=_FakeClient(),
    )

    assert pipeline._proofread_stage._llm_client is direct


def test_smart_assistant_runtime_keeps_provider_client_unscoped(tmp_path: Path) -> None:
    from transbridge.smart_assistant.tools._workflow_llm_runtime import create_workflow_llm_runtime

    config = make_llm_config(max_concurrent=1)
    provider = _FakeClient()
    with patch("transbridge.infra.llm_client.create_llm_client", return_value=provider):
        runtime = create_workflow_llm_runtime(
            config,
            esp_path=str(tmp_path / "fixture.esp"),
            workflow="assistant-test",
            stop_event=threading.Event(),
        )

    try:
        limited = runtime.client._delegate
        assert limited.delegate is provider
        assert not isinstance(limited.delegate, ReasoningScopedLLMClient)
    finally:
        runtime.close()


def test_fomod_xml_ai_translation_uses_direct_scope(tmp_path: Path) -> None:
    from transbridge.fomod.pipeline import FomodPipeline
    from transbridge.fomod.xml_fidelity import XmlFidelityReport

    xml_path = tmp_path / "ModuleConfig.xml"
    xml_path.write_text("<config />", encoding="utf-8")
    provider = _FakeClient()
    received = []

    def process(path, **kwargs):
        received.append((path, kwargs["llm"]))
        return XmlFidelityReport("in", "out", "utf-8", "", (), (), (), ())

    pipeline = FomodPipeline(llm_config=make_llm_config())
    with (
        patch("transbridge.infra.llm_client.create_llm_client", return_value=provider),
        patch("transbridge.fomod.pipeline.find_fomod_xml_files", return_value=[xml_path]),
        patch("transbridge.fomod.pipeline.process_fomod_xml_file", side_effect=process),
    ):
        pipeline._translate_fomod_xml(
            tmp_path,
            None,
            "zh_CN",
            ai_enabled=True,
            cancellation=None,
        )

    scoped = received[0][1]
    assert isinstance(scoped, ReasoningScopedLLMClient)
    assert scoped.reasoning_intent is ReasoningIntent.PREFER_DIRECT
