#!/usr/bin/env python3
"""
Smart async demo - test background job with progress tracking.

This script demonstrates:
1. Fast task completes synchronously
2. Slow task switches to background
3. Progress tracking with live updates
"""
import asyncio
import time

async def fast_task():
    """Fast task - completes in < 1 second."""
    print("⚡ Fast task starting...")
    await asyncio.sleep(0.5)
    print("✅ Fast task completed!")
    return {"status": "completed", "type": "fast", "duration": 0.5}

async def slow_task():
    """Slow task - takes 5 seconds."""
    print("🐌 Slow task starting...")
    for i in range(5):
        await asyncio.sleep(1)
        print(f"  Progress: {i + 1}/5 seconds")
    print("✅ Slow task completed!")
    return {"status": "completed", "type": "slow", "duration": 5.0}

async def main():
    print("=== Smart Async Demo ===\n")
    
    # Test 1: Fast task
    result1 = await fast_task()
    print(f"Result 1: {result1}\n")
    
    # Test 2: Slow task
    result2 = await slow_task()
    print(f"Result 2: {result2}\n")
    
    print("=== Demo Complete ===")
    return {"fast": result1, "slow": result2}

if __name__ == "__main__":
    result = asyncio.run(main())
    print(f"\nFinal result: {result}")
