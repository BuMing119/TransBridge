"""Collect third-party license texts from the locked Windows build."""

from importlib import metadata
from pathlib import Path
from shutil import copyfile, copytree
import sys

ROOT = Path(__file__).resolve().parents[1]
LICENSES = ROOT / "LICENSES"
LEGAL_FILE_PREFIXES = ("license", "licence", "copying", "copyright", "notice", "third-party", "thirdparty")


def _copy_distribution(distribution: metadata.Distribution, destination: Path) -> None:
    files = sorted(
        path
        for path in (distribution.files or ())
        if not path.is_absolute()
        and ".." not in path.parts
        and (path.name.lower().startswith(LEGAL_FILE_PREFIXES) or "licenses" in path.parts)
        and distribution.locate_file(path).is_file()
    )
    name = distribution.name.lower()
    if not files and name in {"jiter", "tokenizers"}:
        # These locked wheels omit the license files present in their sources.
        copyfile(LICENSES / f"{name}-{distribution.version}.txt", destination / "LICENSE.txt")
        return
    if not files:
        raise RuntimeError(f"No license file found for {distribution.name} {distribution.version}")
    for path in files:
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(distribution.locate_file(path), target)


def collect_licenses(destination: Path, inno_compiler: Path | None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    copyfile(LICENSES / "Apache-2.0.txt", destination / "Apache-2.0.txt")
    copyfile(LICENSES / "LGPL-3.0.txt", destination / "LGPL-3.0.txt")
    copyfile(Path(sys.base_prefix) / "LICENSE.txt", destination / "Python-LICENSE.txt")
    qt_version = metadata.version("PyQt6-Qt6")
    qt_notices = LICENSES / f"qt-{qt_version}"
    copytree(qt_notices, destination / qt_notices.name, dirs_exist_ok=True)
    if inno_compiler is not None:
        copyfile(inno_compiler.with_name("license.txt"), destination / "Inno-Setup-LICENSE.txt")

    version = metadata.version("transbridge")
    index = [
        "TransBridge third-party licenses",
        "",
        f"This application uses Qt {qt_version} under the GNU LGPL version 3.",
        "The GNU GPL version 3 is in LICENSE.txt in the installation directory.",
        "Qt notices and the LGPL license text are in this licenses directory.",
        "Users may modify or replace interface-compatible Qt DLLs and reverse",
        "engineer the application to debug modifications to the LGPL library.",
        "Close TransBridge, replace the compatible DLLs in _internal/PyQt6/Qt6/bin,",
        "then launch TransBridge again.",
        "",
        "Corresponding source for TransBridge and covered bundled libraries:",
        f"https://github.com/BuMing119/TransBridge/releases/tag/v{version}",
        "",
        "This inventory covers the locked Windows build environment, including",
        "build tools and dependencies that may be excluded from the bundle.",
        "",
    ]
    for distribution in sorted(metadata.distributions(), key=lambda item: item.name.lower()):
        if distribution.name.lower() == "transbridge":
            continue
        folder = destination / f"{distribution.name}-{distribution.version}"
        folder.mkdir(exist_ok=True)
        _copy_distribution(distribution, folder)
        index.append(f"{distribution.name} {distribution.version}: {folder.name}/")
    (destination / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(index) + "\n", encoding="utf-8")


if __name__ == "__main__":
    compiler = Path(sys.argv[2]) if sys.argv[2] else None
    collect_licenses(Path(sys.argv[1]), compiler)
