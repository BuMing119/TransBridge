from ..workers import ApiWorker


class OperationCoordinator:
    """Own one application-shell interaction slice."""

    def __init__(self, host) -> None:
        self._host = host

    def update_operation_menu_state(self):
        has_collection = self._host.context.collection is not None

        menu = self._host.operation_menu
        # Keep visible sync commands discoverable and hoverable. The canonical
        # intent router blocks unavailable operations and explains what is missing.
        menu.upload.setEnabled(True)
        menu.download.setEnabled(True)
        menu.write.setEnabled(has_collection)

        slots = self._host.context.slots
        multi = len(slots) > 1
        menu.batch_upload.setVisible(multi)
        menu.batch_upload.setEnabled(True)
        menu.batch_download.setVisible(multi)
        menu.batch_download.setEnabled(True)
        menu.batch_write.setVisible(multi)
        menu.batch_write.setEnabled(True)

    def upload(self):
        if not self._host.context.collection:
            return
        facade = getattr(self._host, "operation_plan_facade", None)
        if self._supports(facade, "upload"):
            return facade.begin_upload(self._host.context)
        self._host.upload_card.upload()

    def batch_upload(self):
        if len(self._host.context.slots) <= 1:
            return
        facade = getattr(self._host, "operation_plan_facade", None)
        if self._supports(facade, "upload", batch=True):
            return facade.begin_upload(self._host.context, batch=True)
        self._host.upload_card.batch_upload()

    def download(self):
        if not self._host.context.collection:
            return
        facade = getattr(self._host, "operation_plan_facade", None)
        if self._supports(facade, "download"):
            return facade.begin_download(self._host.context)
        self._host.download_card.download()

    def batch_download(self):
        if len(self._host.context.slots) <= 1:
            return
        facade = getattr(self._host, "operation_plan_facade", None)
        if self._supports(facade, "download", batch=True):
            return facade.begin_download(self._host.context, batch=True)
        self._host.download_card.batch_download()

    def write(self):
        if not self._host.context.collection:
            return
        facade = getattr(self._host, "operation_plan_facade", None)
        if self._supports(facade, "write"):
            return facade.begin_write(self._host.context)
        self._host.write_card.write()

    def batch_write(self):
        if len(self._host.context.slots) <= 1:
            return
        facade = getattr(self._host, "operation_plan_facade", None)
        if self._supports(facade, "write", batch=True):
            return facade.begin_write(self._host.context, batch=True)
        self._host.write_card.batch_write()

    def _supports(self, facade, kind: str, *, batch: bool = False) -> bool:
        if facade is None:
            return False
        supports = getattr(facade, "supports", None)
        return not callable(supports) or bool(supports(kind, self._host.context, batch=batch))

    def run_worker(
        self, fn=None, *, fn_factory=None, on_result, on_error, progress_total: int = 0, progress_msg: str = ""
    ):
        """Worker helper: disables menu items, shows Step2 progress, runs background task."""
        menu = self._host.operation_menu
        ops = [
            menu.upload,
            menu.batch_upload,
            menu.download,
            menu.batch_download,
            menu.write,
            menu.batch_write,
        ]
        saved = [(act, act.isEnabled()) for act in ops]
        for act in ops:
            act.setEnabled(False)

        self._host.workbench.show_step2_progress(progress_total, progress_msg)

        def _restore():
            self._host.workbench.hide_step2_progress()
            for act, state in saved:
                act.setEnabled(state)
            self.update_operation_menu_state()

        if fn_factory is not None:
            _cb_ref = [None]

            def _wrapped():
                return fn_factory(_cb_ref[0])

            w = ApiWorker(_wrapped)
            _cb_ref[0] = w.make_progress_callback()
        else:
            w = ApiWorker(fn)

        w.result.connect(on_result)
        w.error.connect(on_error)
        w.progress.connect(self._host.workbench.update_step2_progress)
        w.finished.connect(_restore)
        w.start()
        self._host.workers.append(w)
