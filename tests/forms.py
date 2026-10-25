"""Filling the GUI's one-dialog forms (`gui.quick_form`) in tests, as a user would."""


def fill(window, **values):
    """The next form the window shows gets these values, then OK. The dialog is kept
    on `window.last_form` so a test can look at what it offered."""

    def run(dialog):
        for key, value in values.items():
            dialog.set(key, value)
        window.last_form = dialog
        return dialog.problem() is None

    window.run_dialog = run
