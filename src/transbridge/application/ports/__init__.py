"""Application port protocols.

These interfaces describe dependencies consumed by use cases. Concrete parser,
repository, GUI, network, and task implementations belong to adapters and are
assembled only by the composition root.
"""

from .paratranz_terms import (
    ParaTranzTerm,
    ParaTranzTerminologyPort,
    ParaTranzTermPage,
    ParaTranzTermSnapshot,
    ParaTranzTermWrite,
    ParaTranzTermWriteResult,
    TermWriteOperation,
    TermWriteStatus,
)
from .system import (
    ClockPort,
    ClosablePort,
    FileSystemPort,
    FormatPort,
    IdGeneratorPort,
    RepositoryPort,
    SecretPort,
    SecurityPort,
    TaskPort,
    closeables,
)

__all__ = [
    "ClockPort",
    "ClosablePort",
    "FileSystemPort",
    "FormatPort",
    "IdGeneratorPort",
    "ParaTranzTerm",
    "ParaTranzTermPage",
    "ParaTranzTermSnapshot",
    "ParaTranzTermWrite",
    "ParaTranzTermWriteResult",
    "ParaTranzTerminologyPort",
    "RepositoryPort",
    "SecretPort",
    "SecurityPort",
    "TaskPort",
    "TermWriteOperation",
    "TermWriteStatus",
    "closeables",
]
