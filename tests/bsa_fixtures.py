"""Real SSE 105 archives for binary lookup and publication integration tests."""

from pathlib import Path
from tempfile import TemporaryDirectory

from sse_bsa import BSAArchive


def write_bsa(path: Path, members: dict[str, bytes]) -> Path:
    """Pack actual LZ4-compressed members using the installed BSA implementation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="bsa-input-", dir=path.parent) as directory:
        root = Path(directory)
        for name, content in members.items():
            member = root / name
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_bytes(content)
        BSAArchive.create_archive(root, path)
    archive = BSAArchive(path)
    assert {str(member).replace("\\", "/") for member in archive.files} == set(members)
    assert all(archive.get_file_stream(name).read() == content for name, content in members.items())
    return path
