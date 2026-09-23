"""Dispatch answer coverage and its receipt through the application boundary."""


def accept_answer(binding, parsed, turn):
    binding.control_results.start(parsed, stop_reason=turn.stop_reason)
