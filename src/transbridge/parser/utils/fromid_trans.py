def formid_bytes_to_hex(data: bytes) -> str:
    """
    Convert 4-byte FormID bytes to hex string (e.g., '00010000').

    Args:
        data: Raw bytes containing FormID (first 4 bytes will be used)

    Returns:
        Hex string representation of FormID (8 characters, uppercase)
    """
    formid_int = int.from_bytes(data[:4], byteorder="little")
    return hex(formid_int).removeprefix("0x").upper().zfill(8)


def formid_with_plugin_name(formid_hex: str, plugin_name: str) -> str:
    """
    Combine FormID hex and plugin name to create complete FormID.

    Args:
        formid_hex: FormID in hex format (e.g., '00010000')
        plugin_name: Plugin name (e.g., 'Test.esp')

    Returns:
        Complete FormID string (e.g., '00010000|Test.esp')
    """
    return f"{formid_hex}|{plugin_name}"


def formid_bytes_to_complete(
    data: bytes,
    masters: list[str],
    plugin_name: str,
) -> str:
    """
    Convert FormID bytes to complete FormID string with plugin name.

    This function extracts the FormID from bytes and determines the correct
    plugin name based on the master index (first byte of FormID).

    Args:
        data: Raw bytes containing FormID (first 4 bytes will be used)
        masters: List of master plugin names
        plugin_name: Current plugin name (used if FormID is from current plugin)

    Returns:
        Complete FormID string (e.g., '00010000|Test.esp')
    """
    formid_hex = formid_bytes_to_hex(data)
    master_index = int(formid_hex[:2], base=16)

    # Get plugin that first defines this FormID
    try:
        master = masters[master_index]
    except IndexError:
        master = plugin_name

    return formid_with_plugin_name(formid_hex, master)
