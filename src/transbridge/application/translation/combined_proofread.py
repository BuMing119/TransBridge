# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compatibility imports for the former combined Proofread module."""

from .proofread_stage import ProofreadStage, TermResolver

CombinedProofreadStage = ProofreadStage

__all__ = ["CombinedProofreadStage", "ProofreadStage", "TermResolver"]
