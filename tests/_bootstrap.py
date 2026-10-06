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

_CONTRACT_PATH = os.environ.get("TA_CONTRACT") or os.path.join(os.path.dirname(_THIS_DIR), "contract.py")
_spec = importlib.util.spec_from_file_location("tradeanchor_contract", _CONTRACT_PATH)
_contract_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract_module)

TradeAnchor = _contract_module.TradeAnchor
gl = _contract_module.gl
Address = _contract_module.Address
u256 = _contract_module.u256

# Production floors are 1e15 wei; tests use tiny stakes.
TradeAnchor.MIN_STAKE = u256(1)
TradeAnchor.MIN_JUROR_STAKE = u256(1)
TradeAnchor.MIN_APPEAL_BOND = u256(1)


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


def submit_and_lock(contract, trade_id, urls):
    """Submit evidence as the current caller, then (if the counterparty
    has not submitted) move past the grace period and lock, mirroring
    what a real caller does with submit_evidence + lock_evidence."""
    import datetime as _dt
    import json as _json
    contract.submit_evidence(trade_id, urls)
    trade = _json.loads(contract.get_trade(trade_id))
    if trade["status"] == "open":
        trade["evidence_lock_at"] = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=5)
        ).isoformat()
        contract.trades[trade_id] = _json.dumps(trade, sort_keys=True)
        contract.lock_evidence(trade_id)


def pass_deadline(contract, trade_id):
    """Move the trade deadline into the past so non-completion outcomes
    are no longer treated as premature."""
    import datetime as _dt
    import json as _json
    t = _json.loads(contract.trades[trade_id])
    t["deadline"] = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=5)).isoformat()
    contract.trades[trade_id] = _json.dumps(t, sort_keys=True)


def age_jurors(contract):
    """Backdate every juror's registration so they were registered before
    any trade was accepted (jurors registered after acceptance are not
    eligible for that trade)."""
    import json as _json
    for key in list(contract.jurors.keys()):
        rec = _json.loads(contract.jurors[key])
        rec["registered_at"] = "2000-01-01T00:00:00+00:00"
        rec["stake_updated_at"] = "2000-01-01T00:00:00+00:00"
        contract.jurors[key] = _json.dumps(rec, sort_keys=True)


def draw_jury(contract, trade_id, beacon="fixed-test-beacon"):
    """Move an appeal past its window and draw the jury with a fixed
    beacon (the randomness round is mocked)."""
    import datetime as _dt
    import json as _json
    from unittest.mock import patch as _patch
    t = _json.loads(contract.trades[trade_id])
    now = _dt.datetime.now(_dt.timezone.utc)
    t["appeal_deadline"] = (now - _dt.timedelta(seconds=300)).isoformat()
    t["appeal"]["draw_round"] = int((now.timestamp() - 240 - 1595431050) // 30 + 1)
    contract.trades[trade_id] = _json.dumps(t, sort_keys=True)
    with _patch.object(gl.nondet.web, "render",
                       return_value=_json.dumps({"round": t["appeal"]["draw_round"], "randomness": beacon})):
        contract.draw_jury(trade_id)


def set_frozen(contract, trade_id, content, url="https://example.com/x"):
    """Replace the frozen evidence content of a trade with a consistent
    set (hash and root computed the way the contract does)."""
    import hashlib as _h
    import json as _json
    t = _json.loads(contract.trades[trade_id])
    page = {"url": url, "excerpt_sha256": _h.sha256(content.encode()).hexdigest(), "full_content_sha256": _h.sha256(content.encode()).hexdigest(), "tx": None, "content": content}
    t["evidence_content"] = [page]
    t["evidence_content_root"] = _contract_module._evidence_root([page])
    contract.trades[trade_id] = _json.dumps(t, sort_keys=True)
