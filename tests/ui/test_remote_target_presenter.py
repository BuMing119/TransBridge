from transbridge.application.projects import (
    ParaTranzTargetSource,
    ParaTranzTargetStatus,
    ResolvedParaTranzTarget,
)
from transbridge.ui.workbench.remote_target_presenter import RemoteTargetPresenter


def test_remote_target_presenter_distinguishes_unbound_and_mismatch() -> None:
    presenter = RemoteTargetPresenter()
    unbound = presenter.present(
        ResolvedParaTranzTarget(
            None,
            "",
            "https://paratranz.cn",
            7,
            ParaTranzTargetSource.UNBOUND,
            ParaTranzTargetStatus.UNBOUND,
            reason="尚未绑定",
        )
    )
    mismatch = presenter.present(
        ResolvedParaTranzTarget(
            42,
            "Cloud",
            "https://paratranz.cn",
            7,
            ParaTranzTargetSource.PROJECT_BINDING,
            ParaTranzTargetStatus.ACCOUNT_MISMATCH,
            5,
            "账号不一致",
        )
    )

    assert unbound.action_text == "选择…"
    assert not unbound.can_clear
    assert mismatch.title == "ParaTranz · Cloud"
    assert mismatch.can_clear
    assert mismatch.semantic_state == "error"
