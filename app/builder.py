"""Background corpus builder.

Building the corpus is a 20-60 s network job. Running it inline in a Streamlit
script blocks the whole UI, and because Streamlit re-executes the script on every
interaction, a blocked or slow scrape is re-attempted on every click -- which is
how an ingest turned into an eight-minute spinner.

Running it on a daemon thread keeps the UI responsive, lets the page show real
per-page progress, and survives the reruns: the thread object is cached, so the
work happens once per container rather than once per click.
"""

from __future__ import annotations

import threading

from app import config, store


class CorpusBuilder:
    """Idempotent, thread-safe wrapper around app.ingest.ingest."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.lines: list[str] = []
        self.error: str | None = None
        self.done = False

    # -- state ------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "done": self.done,
                "error": self.error,
                "lines": list(self.lines),
                "status": store.population_status(),
            }

    # -- control ----------------------------------------------------------

    def start(self) -> bool:
        """Kick off the build. Returns False if one is already under way."""
        if store.is_populated():
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self.lines = []
            self.error = None
            self.done = False
            self._thread = threading.Thread(
                target=self._run, name="corpus-build", daemon=True
            )
            self._thread.start()
            return True

    def _run(self) -> None:
        from app.ingest import ingest

        try:
            ingest(force=True, on_progress=self._progress)
        except Exception as exc:  # noqa: BLE001 - reported in the UI, not swallowed
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self.done = True

    def _progress(self, message: str) -> None:
        with self._lock:
            # Keep the tail only; the full log belongs in the build output.
            self.lines = (self.lines + [message.strip()])[-12:]


def deadline_note() -> str:
    return (
        f"An interactive build gives up after {config.INGEST_DEADLINE_SECONDS}s. "
        "A build step has no such ceiling."
    )
