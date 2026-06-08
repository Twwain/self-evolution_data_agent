"""drivers 共享异常类树."""


class DriverError(Exception):
    """所有 driver 错误的基类."""

    error_code: str = "driver_error"
    suggestion: str = ""
    transient: bool = False

    def __init__(self, message: str, *, suggestion: str = ""):
        super().__init__(message)
        if suggestion:
            self.suggestion = suggestion

    def to_dict(self) -> dict:
        sug = self.suggestion
        if self.transient and sug:
            sug = f"{sug}; 此为瞬时错误, 可直接重试"
        elif self.transient:
            sug = "此为瞬时错误, 可直接重试"
        return {"error": self.error_code, "message": str(self), "suggestion": sug}


class PayloadShapeMismatchError(DriverError):
    error_code = "payload_shape_mismatch"


class UnsupportedOperationError(DriverError):
    error_code = "unsupported_operation"


class UnsafeQueryError(DriverError):
    error_code = "unsafe_query"


class CostExceededError(DriverError):
    error_code = "cost_exceeded"


class ConnectionFailureError(DriverError):
    error_code = "connection_failure"
    transient = True


class QueryTimeoutError(DriverError):
    error_code = "query_timeout"
    transient = True


class UnsupportedDataSourceTypeError(DriverError):
    error_code = "unsupported_datasource_type"
