"""Direction-specific language and decision keys shared by sync review surfaces."""

from dataclasses import dataclass

from .plan_view import OperationKind
from .sync_review_view import KEEP_LOCAL, KEEP_REMOTE


@dataclass(frozen=True, slots=True)
class SyncReviewDirection:
    decisions_field: str
    keep_choice: str
    retained_side: str
    verb: str
    candidate_action: str
    candidate_prefix: str

    @property
    def keep_label(self) -> str:
        return f"这条先不{self.verb}"

    @property
    def pending_label(self) -> str:
        return "还没决定"

    @property
    def safe_action(self) -> str:
        return "更新可确定的工程译文" if self.keep_choice == KEEP_LOCAL else "上传可确定的译文"

    @property
    def notice(self) -> str:
        return f"先比较内容，再决定这条是否{self.verb}。不确定的可以先放着，其余翻译可以继续{self.verb}。"

    @property
    def consequence(self) -> str:
        if self.keep_choice == KEEP_REMOTE:
            return "上传时，用我的译文和状态替换右边的内容；ParaTranz 的原文和补充说明保持原样。"
        return "更新时，只替换工程中的译文和状态；原文和补充说明保持原样，不会写回 ESM/ESP。"


_DOWNLOAD = SyncReviewDirection("download_decisions", KEEP_LOCAL, "本地", "更新", "采用这条云端译文", "已选择云端译文")
_UPLOAD = SyncReviewDirection(
    "upload_decisions",
    KEEP_REMOTE,
    "云端",
    "上传",
    "用我的译文更新这条",
    "已选择上传我的译文",
)


def sync_review_direction(kind: OperationKind) -> SyncReviewDirection:
    return _UPLOAD if kind is OperationKind.UPLOAD else _DOWNLOAD
