# L3 ProcessSupervisor Validation Report

Date: 2026-07-12

Commit:
- c175543 fix: validate L3 supervisor SIGTERM escalation

## Summary

L3 ProcessSupervisor provides OS-level process isolation and hard-kill capability for workloads that cannot be safely terminated by in-process async cancellation.

Execution model:

Agent Process
|
v
ProcessSupervisor
|
+--> SIGTERM
|
+--> grace window
|
+--> SIGKILL escalation


## Motivation

L1/L2 cancellation cannot terminate workloads stuck in:

- CPU-bound infinite loops
- synchronous blocking code
- code paths that never reach await points

L3 moves execution into a separate child process so the supervisor can enforce termination externally.


## Validation Scope

Tested scenarios:

| Scenario | Result |
|---|---|
| Clean completion | PASS |
| Heartbeat loss termination | PASS |
| Wall clock timeout termination | PASS |
| SIGTERM ignored requiring SIGKILL | PASS |
| Cooperative SIGTERM shutdown | PASS |
| Pickle validation guard | PASS |
| Step recording callback | PASS |


## Test Results

Command:

uv run pytest packages/hermeskill-sdk/tests -q

Result:

218 passed in 8.84s


## Key Verification

SIGTERM ignored scenario:

Child process:
- installs SIGTERM ignore handler
- enters CPU-bound loop

Supervisor:
- detects heartbeat loss
- sends SIGTERM
- waits grace period
- escalates to SIGKILL


Verified:

SupervisorResult.sigkilled=True


## Files Changed

- packages/hermeskill-sdk/src/hermeskill/supervisor.py
- packages/hermeskill-sdk/tests/_supervisor_targets.py
- packages/hermeskill-sdk/tests/test_supervisor.py


## Status

L3 ProcessSupervisor is validated and ready for integration with higher-level Hermes runtime and control-plane workflows.
