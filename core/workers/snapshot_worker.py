from PyQt5.QtCore import QThread, pyqtSignal


class SnapshotWorker(QThread):
    progress = pyqtSignal(int)
    phase = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, service, project_id, snapshot_name, working_dir, username, backup_directory):
        super().__init__()
        self.service, self.project_id = service, project_id
        self.snapshot_name, self.working_dir = snapshot_name, working_dir
        self.username, self.backup_directory = username, backup_directory
        self._is_running = True

    def run(self):
        try:
            self.phase.emit("Scanning project files and calculating checksums...")
            def update(done, total, label):
                self.phase.emit(label)
                self.progress.emit(min(99, int(done * 100 / total)) if total else 0)
            snapshot_id = self.service.create_backup_snapshot(
                self.project_id, self.snapshot_name, self.working_dir, self.username,
                self.backup_directory, progress=update, cancelled=lambda: not self._is_running)
            self.finished.emit(snapshot_id)
        except Exception as exc:
            self.error.emit(str(exc))

    def stop(self):
        self._is_running = False
