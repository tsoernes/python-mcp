#!/usr/bin/env python3
"""
Test suite for smart async decorator and job management.

This test suite validates:
1. Fast synchronous completion (under timeout)
2. Automatic timeout switching to background
3. Explicit async mode
4. Job status tracking
5. Job cancellation
6. Job listing and filtering
7. Progress tracking
"""

import asyncio
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from python_mcp_server.smart_async import (
    STATE,
    cancel_job,
    create_progress_callback,
    get_job_status,
    initialize_state,
    list_jobs,
    prune_jobs,
    smart_async,
)


# Test tools using smart_async decorator
@smart_async(timeout_env="TEST_TIMEOUT", default_timeout=2.0)
async def fast_tool(
    duration: float = 0.5,
    async_mode: bool = False,
    job_label: str | None = None,
) -> dict:
    """Fast tool that completes within timeout."""
    await asyncio.sleep(duration)
    return {"status": "completed", "duration": duration}


@smart_async(timeout_env="TEST_TIMEOUT", default_timeout=2.0)
async def slow_tool(
    duration: float = 5.0,
    async_mode: bool = False,
    job_label: str | None = None,
) -> dict:
    """Slow tool that exceeds timeout."""
    await asyncio.sleep(duration)
    return {"status": "completed", "duration": duration}


@smart_async(timeout_env="TEST_TIMEOUT", default_timeout=2.0)
async def progress_tool(
    items: int = 10,
    async_mode: bool = False,
    job_label: str | None = None,
) -> dict:
    """Tool with progress tracking."""
    progress_callback = create_progress_callback()

    results = []
    for i in range(items):
        await asyncio.sleep(0.1)
        results.append(f"item_{i}")
        progress_callback(i + 1, items, f"Processed {i + 1}/{items} items")

    return {"status": "completed", "items": len(results)}


@smart_async(timeout_env="TEST_TIMEOUT", default_timeout=2.0)
async def failing_tool(
    async_mode: bool = False,
    job_label: str | None = None,
) -> dict:
    """Tool that raises an exception."""
    await asyncio.sleep(0.1)
    raise ValueError("Intentional test failure")


async def test_fast_sync_completion():
    """Test 1: Fast task completes synchronously."""
    print("=" * 70)
    print("TEST 1: Fast Synchronous Completion")
    print("=" * 70)

    start = asyncio.get_event_loop().time()
    result = await fast_tool(duration=0.5)
    elapsed = asyncio.get_event_loop().time() - start

    print(f"Result: {result}")
    print(f"Elapsed: {elapsed:.2f}s")

    assert result["status"] == "completed"
    assert result["duration"] == 0.5
    assert elapsed < 1.0  # Should complete quickly
    assert "job_id" not in result  # Should not be a background job

    print("✅ PASSED: Fast task completed synchronously\n")


async def test_slow_timeout_switching():
    """Test 2: Slow task switches to background on timeout."""
    print("=" * 70)
    print("TEST 2: Timeout Switching to Background")
    print("=" * 70)

    start = asyncio.get_event_loop().time()
    result = await slow_tool(duration=5.0)
    elapsed = asyncio.get_event_loop().time() - start

    print(f"Result: {result}")
    print(f"Elapsed: {elapsed:.2f}s")

    # Should switch to background quickly (around timeout threshold)
    assert "job_id" in result
    assert result["status"] == "running"
    assert elapsed < 3.0  # Should switch quickly, not wait 5 seconds

    # Wait for job to complete
    job_id = result["job_id"]
    for _ in range(10):
        await asyncio.sleep(1)
        status = get_job_status(job_id)
        print(f"  Job status: {status['job']['status']}")
        if status["job"]["status"] == "completed":
            break

    # Verify job completed
    final_status = get_job_status(job_id)
    assert final_status["job"]["status"] == "completed"
    assert final_status["job"]["result"]["duration"] == 5.0

    print("✅ PASSED: Slow task switched to background and completed\n")


async def test_explicit_async_mode():
    """Test 3: Explicit async mode launches immediately."""
    print("=" * 70)
    print("TEST 3: Explicit Async Mode")
    print("=" * 70)

    start = asyncio.get_event_loop().time()
    result = await fast_tool(
        duration=0.5, async_mode=True, job_label="Explicit async test"
    )
    elapsed = asyncio.get_event_loop().time() - start

    print(f"Result: {result}")
    print(f"Elapsed: {elapsed:.2f}s")

    # Should return immediately with job_id
    assert "job_id" in result
    assert result["status"] == "pending"
    assert elapsed < 0.2  # Should launch very quickly

    # Wait for completion
    job_id = result["job_id"]
    await asyncio.sleep(1)

    status = get_job_status(job_id)
    assert status["job"]["status"] == "completed"
    assert status["job"]["label"] == "Explicit async test"

    print("✅ PASSED: Explicit async mode launched immediately\n")


async def test_progress_tracking():
    """Test 4: Progress tracking works correctly."""
    print("=" * 70)
    print("TEST 4: Progress Tracking")
    print("=" * 70)

    result = await progress_tool(items=10, async_mode=True, job_label="Progress test")
    job_id = result["job_id"]

    print(f"Job launched: {job_id}")

    # Poll for progress
    progress_seen = False
    for i in range(20):
        await asyncio.sleep(0.2)
        status = get_job_status(job_id)

        if status["job"]["progress"]:
            progress = status["job"]["progress"]
            print(
                f"  Progress: {progress['current']}/{progress['total']} - {progress.get('message', '')}"
            )
            progress_seen = True

        if status["job"]["status"] == "completed":
            break

    assert progress_seen, "Progress updates should be visible"

    final_status = get_job_status(job_id)
    assert final_status["job"]["status"] == "completed"
    assert final_status["job"]["result"]["items"] == 10

    print("✅ PASSED: Progress tracking works\n")


async def test_job_cancellation():
    """Test 5: Job cancellation works."""
    print("=" * 70)
    print("TEST 5: Job Cancellation")
    print("=" * 70)

    # Launch a slow job
    result = await slow_tool(
        duration=10.0, async_mode=True, job_label="Cancellation test"
    )
    job_id = result["job_id"]

    print(f"Job launched: {job_id}")

    # Wait a bit then cancel
    await asyncio.sleep(0.5)
    cancel_result = cancel_job(job_id)

    print(f"Cancel result: {cancel_result}")
    assert cancel_result["status"] == "cancelled"

    # Verify status
    status = get_job_status(job_id)
    assert status["job"]["status"] == "cancelled"

    print("✅ PASSED: Job cancellation works\n")


async def test_job_listing():
    """Test 6: Job listing and filtering works."""
    print("=" * 70)
    print("TEST 6: Job Listing and Filtering")
    print("=" * 70)

    # Launch several jobs
    job1 = await fast_tool(duration=0.1, async_mode=True, job_label="List test 1")
    job2 = await fast_tool(duration=0.1, async_mode=True, job_label="List test 2")
    job3 = await fast_tool(duration=0.1, async_mode=True, job_label="List test 3")

    await asyncio.sleep(0.3)  # Let them complete

    # List all jobs
    all_jobs = list_jobs()
    print(f"Total jobs: {all_jobs['total']}")
    assert all_jobs["total"] >= 3

    # List completed jobs
    completed = list_jobs(status_filter="completed")
    print(f"Completed jobs: {completed['total']}")
    assert completed["total"] >= 3

    # List with limit
    limited = list_jobs(limit=2)
    assert len(limited["jobs"]) == 2

    print("✅ PASSED: Job listing and filtering works\n")


async def test_error_handling():
    """Test 7: Error handling in async jobs."""
    print("=" * 70)
    print("TEST 7: Error Handling")
    print("=" * 70)

    result = await failing_tool(async_mode=True, job_label="Error test")
    job_id = result["job_id"]

    print(f"Job launched: {job_id}")

    # Wait for failure
    await asyncio.sleep(0.5)

    status = get_job_status(job_id)
    print(f"Job status: {status['job']['status']}")
    print(f"Error: {status['job']['error']}")

    assert status["job"]["status"] == "failed"
    assert "Intentional test failure" in status["job"]["error"]

    print("✅ PASSED: Error handling works\n")


async def test_job_pruning():
    """Test 8: Job pruning works."""
    print("=" * 70)
    print("TEST 8: Job Pruning")
    print("=" * 70)

    # Get current job count
    before = list_jobs()
    print(f"Jobs before pruning: {before['total']}")

    # Prune completed jobs
    result = prune_jobs(keep_completed=False, keep_failed=True, max_age_hours=0)
    print(f"Pruned {result['removed']} jobs, {result['remaining']} remaining")

    after = list_jobs()
    print(f"Jobs after pruning: {after['total']}")

    assert result["removed"] > 0 or before["total"] == 0
    assert after["total"] < before["total"] or before["total"] == 0

    print("✅ PASSED: Job pruning works\n")


async def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("SMART ASYNC TEST SUITE")
    print("=" * 70 + "\n")

    # Initialize state
    initialize_state()

    try:
        await test_fast_sync_completion()
        await test_slow_timeout_switching()
        await test_explicit_async_mode()
        await test_progress_tracking()
        await test_job_cancellation()
        await test_job_listing()
        await test_error_handling()
        await test_job_pruning()

        print("=" * 70)
        print("ALL TESTS PASSED ✅")
        print("=" * 70)
        return 0
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
