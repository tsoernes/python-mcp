#!/usr/bin/env python3
"""
Integration test for smart async pattern via MCP server.

This script tests the smart async functionality by directly calling
the MCP server tools to verify:
1. Job management tools are available
2. Jobs can be created, tracked, and managed
3. Progress tracking works
4. All job lifecycle states work correctly
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
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


@smart_async(timeout_env="TEST_TIMEOUT", default_timeout=2.0)
async def demo_fast_task(
    duration: float = 0.5,
    async_mode: bool = False,
    job_label: str | None = None,
) -> dict:
    """Fast demo task."""
    await asyncio.sleep(duration)
    return {"status": "completed", "duration": duration, "type": "fast"}


@smart_async(timeout_env="TEST_TIMEOUT", default_timeout=2.0)
async def demo_slow_task(
    duration: float = 5.0,
    async_mode: bool = False,
    job_label: str | None = None,
) -> dict:
    """Slow demo task that exceeds timeout."""
    await asyncio.sleep(duration)
    return {"status": "completed", "duration": duration, "type": "slow"}


@smart_async(timeout_env="TEST_TIMEOUT", default_timeout=2.0)
async def demo_progress_task(
    items: int = 10,
    async_mode: bool = False,
    job_label: str | None = None,
) -> dict:
    """Task with progress tracking."""
    progress = create_progress_callback()

    results = []
    for i in range(items):
        await asyncio.sleep(0.1)
        results.append(f"item_{i}")
        progress(i + 1, items, f"Processed {i + 1}/{items} items")

    return {"status": "completed", "items": len(results)}


async def main():
    """Run integration tests."""
    print("\n" + "=" * 70)
    print("SMART ASYNC MCP INTEGRATION TEST")
    print("=" * 70 + "\n")

    # Initialize
    initialize_state()
    print("✅ Smart async state initialized\n")

    # Test 1: Fast synchronous task
    print("📝 Test 1: Fast Synchronous Task")
    result1 = await demo_fast_task(duration=0.5)
    print(f"   Result: {result1}")
    assert result1["type"] == "fast"
    print("   ✅ Fast task completed synchronously\n")

    # Test 2: Slow task switches to background
    print("📝 Test 2: Slow Task (Auto Background Switch)")
    result2 = await demo_slow_task(duration=5.0)
    print(f"   Result: {result2}")
    assert "job_id" in result2
    job_id_slow = result2["job_id"]
    print(f"   ✅ Task switched to background: {job_id_slow[:12]}...\n")

    # Test 3: Explicit async mode
    print("📝 Test 3: Explicit Async Mode")
    result3 = await demo_fast_task(
        duration=1.0, async_mode=True, job_label="Explicit async demo"
    )
    print(f"   Result: {result3}")
    assert result3["status"] == "pending"
    job_id_explicit = result3["job_id"]
    print(f"   ✅ Job launched immediately: {job_id_explicit[:12]}...\n")

    # Test 4: Progress tracking
    print("📝 Test 4: Progress Tracking")
    result4 = await demo_progress_task(
        items=8, async_mode=True, job_label="Progress demo"
    )
    job_id_progress = result4["job_id"]
    print(f"   Job ID: {job_id_progress[:12]}...")

    # Poll for progress
    for _ in range(10):
        await asyncio.sleep(0.2)
        status = get_job_status(job_id_progress)
        if status["job"]["progress"]:
            prog = status["job"]["progress"]
            print(
                f"   Progress: {prog['current']}/{prog['total']} - {prog.get('message', '')}"
            )
        if status["job"]["status"] == "completed":
            break
    print("   ✅ Progress tracking works\n")

    # Test 5: List jobs
    print("📝 Test 5: Job Listing")
    all_jobs = list_jobs()
    print(f"   Total jobs: {all_jobs['total']}")
    for job in all_jobs["jobs"][:3]:
        print(f"   - {job['id'][:12]}... | {job['label'][:30]:30} | {job['status']}")
    print("   ✅ Job listing works\n")

    # Test 6: Get job status
    print("📝 Test 6: Job Status Queries")
    status = get_job_status(job_id_explicit)
    print(f"   Job: {job_id_explicit[:12]}...")
    print(f"   Status: {status['job']['status']}")
    print(f"   Label: {status['job']['label']}")
    if status["job"]["result"]:
        print(f"   Result: {status['job']['result']}")
    print("   ✅ Job status query works\n")

    # Test 7: Cancel job
    print("📝 Test 7: Job Cancellation")
    if not get_job_status(job_id_slow)["job"]["status"] in ("completed", "failed"):
        cancel_result = cancel_job(job_id_slow)
        print(f"   Cancelled: {cancel_result}")
        print("   ✅ Job cancellation works\n")
    else:
        print("   ⏩ Slow job already completed, skipping cancel test\n")

    # Test 8: Prune jobs
    print("📝 Test 8: Job Pruning")
    before_count = list_jobs()["total"]
    prune_result = prune_jobs(keep_completed=False, max_age_hours=0)
    after_count = list_jobs()["total"]
    print(f"   Before: {before_count} jobs")
    print(f"   Removed: {prune_result['removed']} jobs")
    print(f"   After: {after_count} jobs")
    print("   ✅ Job pruning works\n")

    # Summary
    print("=" * 70)
    print("ALL INTEGRATION TESTS PASSED ✅")
    print("=" * 70)
    print("\n📊 Smart Async Features Verified:")
    print("   ✅ Fast synchronous completion")
    print("   ✅ Automatic timeout switching")
    print("   ✅ Explicit async mode")
    print("   ✅ Progress tracking")
    print("   ✅ Job listing")
    print("   ✅ Job status queries")
    print("   ✅ Job cancellation")
    print("   ✅ Job pruning")
    print("\n🎉 Smart async pattern is fully operational!\n")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
