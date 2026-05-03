from __future__ import annotations


def apply(app_mod):
    original_init = app_mod.MainWindow.__init__

    def patched_init(self):
        original_init(self)
        try:
            self.resize(1280, 900)
            self.library_list.setMaximumHeight(120)
            self.library_list.setMinimumHeight(90)
            self.log_box.setMinimumHeight(520)
            self.log_box.setLineWrapMode(app_mod.QTextEdit.NoWrap)
            self.log_box.setVerticalScrollBarPolicy(app_mod.Qt.ScrollBarAlwaysOn)
            self.log_box.setHorizontalScrollBarPolicy(app_mod.Qt.ScrollBarAsNeeded)
        except Exception:
            pass

    app_mod.MainWindow.__init__ = patched_init
