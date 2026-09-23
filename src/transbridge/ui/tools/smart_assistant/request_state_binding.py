"""Dispatch a state query through application-owned terminal receipts."""


def retrieve_state(binding, parsed):
    binding.control_results.start(parsed)
