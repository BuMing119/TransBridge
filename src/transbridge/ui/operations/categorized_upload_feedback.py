"""Worker-side progress stages and compact categorized-upload receipts."""


class UploadProgress:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.current = self.total = 0

    def __call__(self, current: int, total: int, message: str) -> None:
        self.current, self.total = current, total
        self.callback(current, total, message)

    def status(self, message: str) -> None:
        self.callback(self.current, self.total, message)
