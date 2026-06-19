from .auth.bearer import BearerAuth
from .auth.tempo import TempoAuth
from .dispatcher import AgentMintDispatcher
from .exceptions import (
    AgentMintError,
    DispatchInterrupted,
    DispatchTimeout,
    UnsupportedToolset,
)
from .hermes_patch import install_delegate_task_wrapper
from .models import AgentRecord, DispatchResult, Task
from .translation import (
    DEFAULT_TOOLSETS,
    ROLE_HINTS,
    TOOLSET_RESTRICTION_HINTS,
    UNSUPPORTED_TOOLSETS,
    compose_prompt,
)

__version__ = "0.4.0"

__all__ = [
    "AgentMintDispatcher",
    "BearerAuth",
    "TempoAuth",
    "AgentRecord",
    "DispatchResult",
    "Task",
    "AgentMintError",
    "DispatchTimeout",
    "DispatchInterrupted",
    "UnsupportedToolset",
    "compose_prompt",
    "DEFAULT_TOOLSETS",
    "ROLE_HINTS",
    "TOOLSET_RESTRICTION_HINTS",
    "UNSUPPORTED_TOOLSETS",
    "install_delegate_task_wrapper",
    "__version__",
]
