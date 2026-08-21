"""ATM deterministic core. Economic truth lives outside model output."""

# ORDER-018: Linux/GHA is the economic authority and uses the host-managed Docker
# sandbox. Windows remains a compatibility lane but must exercise the real
# AppContainer+Job backend before it may report backend unavailable.
import os

_WINDOWS_ORIGINAL_RUN = None
if os.name == "nt":
    from . import sandbox_boundary_v2 as _sandbox_v2
    from .windows_sandbox_fixed import WINDOWS_ORIGINAL_RUN as _WINDOWS_ORIGINAL_RUN
    from .windows_sandbox_fixed import WindowsStructuralSandbox as _WindowsStructuralSandbox

    _sandbox_v2.StructuralSandbox = _WindowsStructuralSandbox

# Install ORDER-018 TaskMarket truth reconciliation into the existing single Cash
# Canon surfaces. This changes admission evidence, never economic authority.
from .order018_taskmarket import install as _install_order018_taskmarket

_install_order018_taskmarket()

# Linux broker/output hardening and exact hosted-runner fixes. On Windows the
# AppContainer class is restored immediately below so Linux decorators cannot
# become a parallel or accidental Windows authority.
from .order018_runtime_fix import install as _install_order018_runtime_fix

_install_order018_runtime_fix()

if os.name == "nt" and _WINDOWS_ORIGINAL_RUN is not None:
    _WindowsStructuralSandbox.run = _WINDOWS_ORIGINAL_RUN
    _sandbox_v2.StructuralSandbox = _WindowsStructuralSandbox

# Dynamic free-provider candidates remain outside economic authority. They can
# enter WORK/CHECK only after a fresh hard-zero-spend runtime proof.
from .order018_free_provider_directive import install as _install_order018_free_provider_directive

_install_order018_free_provider_directive()
