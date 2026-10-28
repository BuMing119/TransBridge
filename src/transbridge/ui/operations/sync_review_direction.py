"""Direction-specific language and decision keys shared by sync review surfaces."""

from dataclasses import dataclass

from .sync_review_view import KEEP_LOCAL


@dataclass(frozen=True, slots=True)
class SyncReviewDirection:
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
    def notice(self) -> str:
        return f"先比较内容，再决定这条是否{self.verb}。不确定的可以先放着，其余翻译可以继续{self.verb}。"

    @property
    def consequence(self) -> str:
        return "更新时，只替换工程中的译文和状态；原文和补充说明保持原样，不会写回 ESM/ESP。"


DOWNLOAD_REVIEW = SyncReviewDirection(KEEP_LOCAL, "本地", "更新", "采用这条云端译文", "已选择云端译文")
