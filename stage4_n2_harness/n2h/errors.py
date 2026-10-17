"""STOP conditions and neutral fault injection."""


class N2Stop(Exception):
    """A STOP condition. `code` is a stable machine token."""

    def __init__(self, code, detail="", injected=None):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail
        self.injected = injected


class RefusedRun(N2Stop):
    pass


class LedgerStop(N2Stop):
    pass


class RootsStop(N2Stop):
    pass


class ContainmentStop(N2Stop):
    pass


class TelemetryStop(N2Stop):
    pass


class PolicyStop(N2Stop):
    pass


class CgroupStop(N2Stop):
    pass


class LaunchStop(N2Stop):
    pass


class InjectedFault(OSError):
    """Raised only by FaultPlan. Always carries a neutral INJECTED_E_* label."""

    def __init__(self, label):
        OSError.__init__(self, 0, label)
        self.label = label


class FaultPlan:
    """Set of neutral injection labels. Labels must start with INJECTED_E_."""

    def __init__(self, labels=()):
        self.labels = set(labels)
        for lab in self.labels:
            if not lab.startswith("INJECTED_E_"):
                raise ValueError("fault labels must start with INJECTED_E_: %r" % (lab,))
        self.hits = []

    def active(self, label):
        return label in self.labels

    def check(self, label):
        if label in self.labels:
            self.hits.append(label)
            raise InjectedFault(label)
