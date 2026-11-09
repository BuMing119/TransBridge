# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Dispatch a state query through application-owned terminal receipts."""


def retrieve_state(binding, parsed):
    binding.control_results.start(parsed)
