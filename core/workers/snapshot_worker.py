from PyQt5.QtCore import QThread, pyqtSignal
import time
import os

class SnapshotWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, service, project_id, snapshot_name, working_dir, username):
        super().__init__()
        self.service = service
        self.project_id = project_id
        self.snapshot_name = snapshot_name
        self.working_dir = working_dir
        self.username = username
        self._is_running = True

    def run(self):
        try:
            files = [f for f in os.listdir(self.working_dir) if os.path.isfile(os.path.join(self.working_dir, f))]
            total = len(files)
            if total == 0:
                self.error.emit("No files found in working directory.")
                return

            # Step 1: Simulate progress during scanning
            for i, f in enumerate(files, 1):
                if not self._is_running:
                    return
                percent = int((i / total) * 90)  # scanning = first 50%
                self.progress.emit(percent)
                time.sleep(0.02)  # small delay so UI updates smoothly

            # Step 2: Generate and save snapshot (simulate heavier part)
            snapshot_id = self.service.create_snapshot(
                self.project_id,
                self.snapshot_name,
                "Auto snapshot from UI",
                self.working_dir,
                self.username
            )

            # Step 3: Finish progress
            for i in range(91, 101):
                if not self._is_running:
                    return
                self.progress.emit(i)
                time.sleep(0.01)

            self.finished.emit(snapshot_id)

        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self._is_running = False
