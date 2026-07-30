class RunnerError(Exception):
    """Base runner exception."""


class PackageValidationError(RunnerError):
    """A downloaded package is unsafe, corrupt, or has mismatched provenance."""


class BackendError(RunnerError):
    """The configured analysis backend failed or violated its contract."""


class ServerError(RunnerError):
    """The runner could not complete a server protocol operation."""
