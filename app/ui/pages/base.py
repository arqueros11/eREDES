from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget


class BasePage(QWidget):
    key = ""
    title = ""
    subtitle = ""

    def __init__(self):
        super().__init__()
        self.setObjectName("PageRoot")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 18, 24, 18)
        self.root.setSpacing(14)
        self._shown_once = False

    def on_show(self) -> None:
        """Se llama cada vez que la página se hace visible."""
        if not self._shown_once:
            self._shown_once = True
            self.first_show()

    def first_show(self) -> None:
        pass

    def on_hide(self) -> None:
        pass
