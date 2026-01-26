# Tests for Failure Handling Implementation

This folder contains tests for the failure handling and notification management system implemented on 2026-01-26.

## Test Files

### test_failure_handling.py
**Purpose:** Unit tests for core components

**What it tests:**
- Database schema validation (error_notifications_log, operational_state tables)
- Notification throttling logic (1 per hour per error type)
- Operational state transitions (STARTING → ACTIVE/WAITING/ERROR → SHUTDOWN)
- Configuration completeness (all required variables present)
- File structure validation (files exist with correct sizes)
- Code structure validation (all required methods present)

**Run:**
```bash
cd D:\nifty_options_agent
python tests/test_failure_handling.py
```

**Expected output:** All 6 tests PASS

---

### test_integration.py
**Purpose:** Integration tests for real-world scenarios

**What it tests:**
1. OpenAlgo Down - First Occurrence
2. Same Error Within Throttle Window
3. System Recovery
4. Broker Not Logged In (Permanent Error)
5. Multiple Errors - Aggregation
6. Graceful Shutdown (<10s)
7. Database Performance

**Run:**
```bash
cd D:\nifty_options_agent
python tests/test_integration.py
```

**Expected output:** All 7 scenarios PASS

---

## When to Run These Tests

### Before Deployment
Run both tests before deploying changes to EC2:
```bash
python tests/test_failure_handling.py
python tests/test_integration.py
```

### After Modifying Components
If you modify any of these files, run tests to ensure no regression:
- `baseline_v1_live/notification_manager.py`
- `baseline_v1_live/startup_health_check.py`
- `baseline_v1_live/state_manager.py` (operational_state or error_notifications_log)
- `baseline_v1_live/config.py` (failure handling variables)

### Onboarding New Developers
Run tests to validate development environment setup.

---

## Test Dependencies

**No external dependencies required!**

These tests are self-contained and only use:
- Standard library (sqlite3, datetime, os, sys)
- No OpenAlgo, no broker, no WebSocket
- Create temporary test databases (auto-cleaned)

This makes them fast, reliable, and portable.

---

## Test Coverage

| Component | Unit Test | Integration Test |
|-----------|-----------|------------------|
| Database schema | ✅ | ✅ |
| Notification throttling | ✅ | ✅ |
| Operational states | ✅ | ✅ |
| Error aggregation | ❌ | ✅ |
| Graceful shutdown | ❌ | ✅ |
| System recovery | ❌ | ✅ |
| Performance | ❌ | ✅ |

---

## Limitations

These tests **DO NOT** validate:
- Real OpenAlgo API integration (use manual testing)
- Real broker connections (use paper trading)
- WebSocket data feed (use live testing)
- Order placement/fills (use check_system.py)
- Full end-to-end workflow (use manual testing on EC2)

For full system validation, see:
- `baseline_v1_live/check_system.py` - Pre-flight system check
- `docs/DEPLOYMENT_GUIDE.md` - EC2 testing scenarios

---

## Adding New Tests

If you add new failure handling features:

1. Add unit tests to `test_failure_handling.py`
2. Add integration scenarios to `test_integration.py`
3. Update this README with new test coverage
4. Run both test files to ensure everything passes

---

## Test History

| Date | Test File | Purpose |
|------|-----------|---------|
| 2026-01-26 | test_failure_handling.py | Validate failure handling implementation |
| 2026-01-26 | test_integration.py | Validate 7 real-world scenarios |

---

## Quick Reference

```bash
# Run all tests
python tests/test_failure_handling.py && python tests/test_integration.py

# Run specific test
python tests/test_failure_handling.py
python tests/test_integration.py

# Both should show:
# [SUCCESS] All tests passed!
# ================================================================================
# READY FOR DEPLOYMENT
# ================================================================================
```
