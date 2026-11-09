"""Exception types with user-facing messages."""


class CrushError(Exception):
    """Base class for all CRUSH errors."""


class BadMagic(CrushError):
    def __init__(self) -> None:
        super().__init__("not a .crush archive (bad magic bytes)")


class UnsupportedVersion(CrushError):
    def __init__(self, version: int) -> None:
        super().__init__(f"archive format version {version} is newer than this CRUSH build supports")


class UnknownCodec(CrushError):
    def __init__(self, codec: int) -> None:
        super().__init__(f"archive uses unknown codec id {codec}; upgrade CRUSH")


class CorruptArchive(CrushError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"archive is corrupt: {detail}")


class ChecksumMismatch(CrushError):
    def __init__(self, name: str) -> None:
        super().__init__(f"SHA-256 mismatch after decompressing {name!r}: output is not the original")


class ToolMissing(CrushError):
    """An optional external tool (cjxl/djxl) is not installed."""
