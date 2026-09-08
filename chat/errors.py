"""Domain errors shared by chat transports."""


class ChatError(Exception):
    """Base class for expected chat failures."""

    code = "chat_error"
    status_code = 500
    retryable = False

    def __init__(self, message: str = ""):
        super().__init__(message or self.code)


class ThreadNotFound(ChatError):
    code = "thread_not_found"
    status_code = 404


class ThreadAccessDenied(ThreadNotFound):
    """Deliberately reported as not-found to avoid leaking thread existence."""


class ThreadBusy(ChatError):
    code = "thread_busy"
    status_code = 409
    retryable = True


class UnsafeCommand(ChatError):
    code = "unsafe_command"
    status_code = 403


class InvalidChatRequest(ChatError):
    code = "invalid_chat_request"
    status_code = 422


class PersistenceUnavailable(ChatError):
    code = "persistence_unavailable"
    status_code = 503
    retryable = True


class CommandUnavailable(ChatError):
    """A recognized PolyTrade command failed in its tool/backend."""

    code = "command_unavailable"
    status_code = 503
    retryable = True
