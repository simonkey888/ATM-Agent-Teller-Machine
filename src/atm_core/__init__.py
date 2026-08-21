"""ATM deterministic core. Economic truth lives outside model output."""

# ORDER-018: Linux/GHA is the economic authority and uses the host-managed Docker
# sandbox. Windows remains a compatibility lane but must exercise the real
# AppContainer+Job backend before it may report backend unavailable.
import os

if os.name == "nt":
    from . import sandbox_boundary_v2 as _sandbox_v2
    from .windows_sandbox_fixed import WindowsStructuralSandbox as _WindowsStructuralSandbox

    _sandbox_v2.StructuralSandbox = _WindowsStructuralSandbox
