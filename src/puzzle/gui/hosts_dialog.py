"""Project → Remote hosts… (ADR 0021): the user's hosts, added, edited, checked.

Hosts are user settings (`<user data dir>/hosts.toml`), not project data, so this
dialog writes them itself, as the Settings dialog writes settings.toml; no project
command is involved. A check reaches the host in the background and says what it has.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from puzzle import hosts
from puzzle.gui.theme import role

FIELDS = [  # (key, label, help)
    ("name", "Name", "What studies call it, e.g. lab or cluster"),
    ("address", "Address", "An alias from ~/.ssh/config, a host name or an IP address"),
    ("user", "User", "Empty: SSH's own (the config's, else yours)"),
    ("port", "Port", "0: SSH's own (22)"),
    ("runtime", "Solvers run", "Pixi environments there, or a container engine"),
    ("envs_dir", "Pixi environments", "Folder holding the solver environments there"),
    ("work_dir", "Runs go to", "Each run's case folder goes under this folder there"),
    ("cores", "Cores per run", "0: all the host has"),
    ("memory_gb", "Memory (GB)", "0: unknown"),
    ("scheduler", "Scheduler", "None runs at once; Slurm and PBS queue the run"),
    ("partition", "Partition or queue", "The scheduler's partition (Slurm) or queue (PBS)"),
    ("account", "Account", "The account to charge, where the scheduler wants one"),
    ("walltime", "Walltime", "The longest a run may take, HH:MM:SS"),
]
CHOICES = {"runtime": hosts.RUNTIMES, "scheduler": hosts.SCHEDULERS}


class HostsDialog(QDialog):
    def __init__(self, window=None) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Remote hosts")
        self.hosts = hosts.load()
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setFixedWidth(170)
        self.list.currentTextChanged.connect(self.show_host)
        left.addWidget(self.list, 1)
        add = QPushButton("New host")
        add.clicked.connect(self.new_host)
        left.addWidget(add)
        body.addLayout(left)
        right = QVBoxLayout()
        form = QFormLayout()
        self.fields: dict[str, QWidget] = {}
        for key, label, help_ in FIELDS:
            if key in CHOICES:
                w = QComboBox()
                for text, value in CHOICES[key]:
                    w.addItem(text, value)
            elif key in ("port", "cores"):
                w = QSpinBox()
                w.setRange(0, 65535 if key == "port" else 100000)
            elif key == "memory_gb":
                w = QDoubleSpinBox()
                w.setRange(0, 1e6)
                w.setDecimals(1)
            else:
                w = QLineEdit()
            w.setToolTip(help_)
            self.fields[key] = w
            form.addRow(label, w)
        self.fields["scheduler"].currentIndexChanged.connect(lambda _i: self._applies())
        self.fields["runtime"].currentIndexChanged.connect(lambda _i: self._applies())
        self.form = form
        right.addLayout(form)
        self.report = role(QLabel(""), "muted")
        self.report.setWordWrap(True)
        right.addWidget(self.report)
        row = QHBoxLayout()
        self.check_button = QPushButton("Check")
        self.check_button.setToolTip("Reach the host and list the solvers there")
        self.check_button.clicked.connect(self.check)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self.save)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (self.check_button, self.remove_button):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(save)
        row.addWidget(close)
        right.addLayout(row)
        body.addLayout(right, 1)
        outer = QVBoxLayout(self)
        intro = role(QLabel("Machines a study can run on, reached by SSH with your own keys. "
                            "They are yours, not the project's."), "muted")  # fmt: skip
        intro.setWordWrap(True)
        outer.addWidget(intro)
        outer.addLayout(body)
        self.resize(720, 520)
        self._fill_list()
        if not self.hosts:
            self.new_host()

    # --- list --------------------------------------------------------------------------

    def _fill_list(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for name in sorted(self.hosts):
            self.list.addItem(name)
        self.list.blockSignals(False)
        names = sorted(self.hosts)
        if names:
            pick = select if select in self.hosts else names[0]
            self.list.setCurrentRow(names.index(pick))
            self.show_host(pick)

    def new_host(self) -> None:
        self.list.clearSelection()
        self._load(hosts.Host(name="", address=""))
        self.fields["name"].setFocus()

    def show_host(self, name: str) -> None:
        if name in self.hosts:
            self._load(self.hosts[name])

    def _load(self, h: hosts.Host) -> None:
        for key, w in self.fields.items():
            value = getattr(h, key)
            if isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(value)))
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(value)
            else:
                w.setText(str(value))
        self.report.setText("")
        self._applies()

    def _applies(self) -> None:
        """Pixi environments only for native runs; queue fields only with a scheduler."""
        native = self.fields["runtime"].currentData() == "native"
        queued = self.fields["scheduler"].currentData() != "none"
        for key, shown in (("envs_dir", native), ("partition", queued), ("account", queued),
                           ("walltime", queued)):  # fmt: skip
            self.form.setRowVisible(self.fields[key], shown)

    def current(self) -> hosts.Host:
        values = {}
        for key, w in self.fields.items():
            if isinstance(w, QComboBox):
                values[key] = w.currentData()
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                values[key] = w.value()
            else:
                values[key] = w.text().strip()
        old = self.hosts.get(values["name"])
        if old is not None:  # what the form doesn't show (images, commands) is kept
            values.update(images=old.images, keep_case=old.keep_case, ssh=old.ssh,
                          rsync=old.rsync)  # fmt: skip
        return hosts.Host(**values)

    # --- actions -----------------------------------------------------------------------

    def save(self) -> hosts.Host | None:
        try:
            h = self.current()
        except Exception as exc:  # noqa: BLE001 - said in the dialog
            self.report.setText(str(getattr(exc, "message", exc)))
            return None
        if not h.name or not h.address:
            self.report.setText("A host needs a name and an address")
            return None
        self.hosts[h.name] = h
        hosts.save(self.hosts)
        self._fill_list(h.name)
        self.report.setText(f"Saved {h.name}")
        return h

    def remove(self) -> None:
        name = self.fields["name"].text().strip()
        if name not in self.hosts:
            return
        if (
            self.window is not None
            and QMessageBox.question(
                self, "Remove host", f"Remove {name}? Studies that run there will run here."
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        del self.hosts[name]
        hosts.save(self.hosts)
        self._fill_list()
        if not self.hosts:
            self.new_host()

    def check(self) -> None:
        h = self.save()
        if h is None:
            return
        self.report.setText(f"Reaching {h.name}…")
        self.check_button.setEnabled(False)

        def done(found) -> None:
            self.check_button.setEnabled(True)
            self.report.setText(found.words())
            role(self.report, "done" if found.reachable else "error")

        if self.window is not None and hasattr(self.window, "background"):
            again = self.check_button.setEnabled
            self.window.background(f"Checking {h.name}", lambda _emit: hosts.check(h), done,
                                   lock=False, failed=lambda _e: again(True))  # fmt: skip
        else:
            done(hosts.check(h))


def open_hosts(window) -> None:
    HostsDialog(window).exec()


def run_on(window, study) -> bool:
    """A study's Run on…: here, or a host with what to ask it for, as one Edit of the
    study's `remote` (out of its hashes: the results stay valid)."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui.quick_form import Ask

    known = hosts.load()
    now = study.remote
    choices = [("This computer", ""), *((f"{h.name} ({h.target})", h.name)
                                         for h in known.values())]  # fmt: skip
    queued = {name for name, h in known.items() if h.scheduler != "none"}
    asks = [
        Ask("host", "Run on", choices=choices, default=now.host if now else "",
            help="Project → Remote hosts… adds machines"),
        Ask("processes", "Processes", "text", default=str(now.processes if now else 1),
            help="MPI processes, where the solver splits its model",
            shown=lambda v: bool(v.get("host"))),
        Ask("threads", "Threads per process", "text", default=str(now.threads if now else 0),
            help="0 shares the host's cores out", shown=lambda v: bool(v.get("host"))),
        Ask("walltime", "Walltime", "text", default=now.walltime if now else "",
            help="HH:MM:SS; empty is the host's", shown=lambda v: v.get("host") in queued),
        Ask("partition", "Partition or queue", "text", default=now.partition if now else "",
            help="Empty is the host's", shown=lambda v: v.get("host") in queued),
    ]  # fmt: skip

    def check(v) -> str | None:
        if not v.get("host"):
            return None
        for key in ("processes", "threads"):
            if not str(v.get(key, "0")).strip().isdigit():
                return f"{key.capitalize()} is a whole number"
        wall = str(v.get("walltime") or "")
        if wall and not all(p.isdigit() for p in wall.split(":")):
            return "Walltime is HH:MM:SS"
        return None

    got = window.ask_form(f"Run {study.name} on", asks,
                          "Where the solver runs. The result and its run key are the same "
                          "wherever it runs.", check)  # fmt: skip
    if got is None:
        return False
    value = None
    if got["host"]:
        value = {
            "host": got["host"],
            "processes": int(got.get("processes") or 1),
            "threads": int(got.get("threads") or 0),
            "walltime": got.get("walltime") or "",
            "partition": got.get("partition") or "",
        }
    window.submit(Edit(object=str(study.id), ops=[EditOp(op="set", path="remote", value=value)]))
    return True
