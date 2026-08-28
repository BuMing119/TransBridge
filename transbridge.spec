# -*- mode: python ; coding: utf-8 -*-
"""TransBridge PyInstaller onedir build configuration."""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

block_cipher = None
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "src" / "transbridge" / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "src" / "transbridge" / "ui" / "assets" / "transbridge.ico"), "transbridge/ui/assets"),
        (
            str(ROOT / "src" / "transbridge" / "resources" / "embedding_models.toml"),
            "transbridge/resources",
        ),
        (str(ROOT / "data" / "prompts"), "data/prompts"),
        (str(ROOT / "data" / "skills"), "data/skills"),
        *copy_metadata("transbridge"),
    ],
    hiddenimports=[
        "sse_plugin_interface",
        "openpyxl",
        "pandas",
        "PyQt6.QtWidgets",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "torch",
        "torch._C",
        "torch._torch_docs",
        "sentence_transformers",
        "transformers",
        "huggingface_hub",
        "safetensors",
        "faiss",
        "faiss.loader",
        "rank_bm25",
        "py7zr",
        "rarfile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "ruff",
        "tkinter",
        "matplotlib",
        "scipy",
        "IPython",
        "torch.distributions",
        "torch.testing",
        "torch.ao",
        "torch._dynamo",
        "torch._inductor",
        "torch.fx",
        "torch.onnx",
        "torch.export",
        "torch.cuda",
        "torch.backends.cudnn",
        "torch.backends.cuda",
        "transformers.models",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TransBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "src" / "transbridge" / "ui" / "assets" / "transbridge.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TransBridge",
)
