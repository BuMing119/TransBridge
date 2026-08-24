"""Qt-free state-driven guidance foundation for FR26."""

from transbridge.config.ui_preferences import GuidanceMode

from .controller import (
    GuidanceController,
    GuidancePreferencePort,
    GuidanceReduction,
    GuidanceSubmission,
)
from .models import (
    GuidanceContextIdentity,
    GuidanceIntent,
    GuidanceIntentId,
    GuidanceKind,
    GuidanceProjection,
    GuidanceState,
)
from .presentation import (
    GuidancePresentation,
    GuidanceVisibility,
    present_guidance,
)
from .qt import GuidanceBanner, GuidanceBinding
from .state_machine import build_guidance_state

__all__ = [
    "GuidanceContextIdentity",
    "GuidanceController",
    "GuidanceIntent",
    "GuidanceIntentId",
    "GuidanceBanner",
    "GuidanceBinding",
    "GuidanceKind",
    "GuidanceMode",
    "GuidancePreferencePort",
    "GuidancePresentation",
    "GuidanceProjection",
    "GuidanceReduction",
    "GuidanceState",
    "GuidanceSubmission",
    "GuidanceVisibility",
    "build_guidance_state",
    "present_guidance",
]
