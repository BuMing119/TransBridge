# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Dispatch a history query through application-owned terminal receipts."""


def retrieve_history(binding, parsed):
    binding.control_results.start(parsed)
