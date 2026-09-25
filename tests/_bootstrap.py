"""
Shared test bootstrap - wires up the offline genlayer SDK stub and
loads contract.py once. Standard pattern used across this project's
test files.
"""
import importlib.util
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

_CONTRACT_PATH = os.path.join(os.path.dirname(_THIS_DIR), "contract.py")
_spec = importlib.util.spec_from_file_location("tradeanchor_contract", _CONTRACT_PATH)
_contract_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract_module)

TradeAnchor = _contract_module.TradeAnchor
gl = _contract_module.gl
Address = _contract_module.Address
u256 = _contract_module.u256


def make_contract() -> "TradeAnchor":
    return TradeAnchor()


# Two fixed, valid, distinct addresses reused across test files so
# every test doesn't have to invent its own.
PARTY_A_ADDRESS = "0x" + "11" * 20
PARTY_B_ADDRESS = "0x" + "22" * 20
STRANGER_ADDRESS = "0x" + "33" * 20

# A pool of distinct juror addresses (0xaa.. through 0xaa+n), enough for
# any v1 jury (K=5) plus spares for pool-size/selection tests.
JUROR_ADDRESSES = ["0x" + hex(0xa0 + i)[2:].rjust(2, "0") * 20 for i in range(12)]


def set_caller(address_str: str):
    """Simulate a specific wallet calling the next contract method."""
    gl.message.sender_address = Address(address_str)


def reset_transfers():
    """
    Clear the offline `emit_transfer` ledger. Call this in `setUp()`
    for any test that inspects `gl.evm.transfers`, since the ledger is
    a module-level list shared across the whole stub (mirroring how
    `pending_withdrawals` is per-contract-instance but a *transfer*
    is, in real life, a chain-wide event - the stub keeps one global
    ledger for simplicity and relies on tests clearing it themselves).
    """
    gl.evm.transfers.clear()


def call_payable(contract, method_name: str, value: int, *args, **kwargs):
    """
    Invoke a `@gl.public.write.payable` method as if `value` wei of
    GEN were sent alongside the call, mirroring GenVM's atomicity:
    the value is credited to `contract.balance` BEFORE the method
    body runs (exactly like a real payable call, where
    `gl.message.value` is already sent when the method starts
    executing), and if the method raises, the credit is rolled back
    together with everything else the method would have changed -
    a reverted transaction never actually moves value on a real
    chain either.

    Resets `gl.message.value` back to the harness default afterwards
    (see the stub's `_Message.value` docstring) so a subsequent plain
    (non-payable-aware) call in the same test - e.g. one inherited
    from the settlement-only predecessor project - still gets a
    sane, matching default rather than an unexpected zero.
    """
    gl.message.value = u256(value)
    contract.balance = contract.balance + u256(value)
    try:
        result = getattr(contract, method_name)(*args, **kwargs)
    except Exception:
        contract.balance = contract.balance - u256(value)
        raise
    finally:
        gl.message.value = u256(10**18)
    return result
