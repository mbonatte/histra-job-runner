class RunnerError(Exception):
    pass
class PackageValidationError(RunnerError):
    pass
class BackendError(RunnerError):
    pass
class ServerError(RunnerError):
    pass
