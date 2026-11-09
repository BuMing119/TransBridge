# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""File-level download progress, published only by the owning worker."""


class DownloadProgress:
    def __init__(self, callback, inventory, select_files):
        self.callback = callback
        self.select_files = select_files
        self.completed = set()
        self.refresh(inventory)

    def refresh(self, inventory):
        selected = {file_id for _source, file_id in self.select_files(inventory)}
        self.paths = {file_id: path for path, file_id in inventory.items() if file_id in selected}
        self.publish(f"本轮需下载 {len(selected)} 个云端文件，正在准备下载…")

    def downloaded(self, file_id):
        if file_id not in self.completed:
            self.completed.add(file_id)
            self.publish(self.paths.get(file_id, ""))

    def publish(self, message):
        if self.callback:
            current = len(self.completed.intersection(self.paths))
            total = len(self.paths)
            percent = current * 100 // total if total else 0
            self.callback(current, total, f"{percent}%（{current}/{total}）；{message}")


class ProgressFileReads:
    """Report sequential reads and inventory refreshes without changing transport."""

    def __init__(self, reader, progress):
        self.reader, self.progress = reader, progress

    def inventory(self, project_id):
        inventory = self.reader.inventory(project_id)
        self.progress.refresh(inventory)
        return inventory

    def get_file_translation(self, project_id, file_id):
        rows = self.reader.get_file_translation(project_id, file_id)
        self.progress.downloaded(file_id)
        return rows
