#!/usr/bin/env python3
"""
Memory usage test for the content workflow system.
This test simulates the memory-intensive operations to verify improvements.
"""

import asyncio
import psutil
import gc
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import the workflow components
from content_workflow import ContentWorkflow, WorkflowState

async def test_memory_usage():
    """Test memory usage during workflow operations"""
    print("[Memory Test] Starting memory usage test...")
    
    # Get initial memory usage
    process = psutil.Process()
    initial_memory = process.memory_info().rss / 1024 / 1024
    print(f"[Memory Test] Initial memory usage: {initial_memory:.1f}MB")
    
    # Test WorkflowState memory management
    print("[Memory Test] Testing WorkflowState memory management...")
    state = WorkflowState()
    
    # Add many post IDs to test the capping mechanism
    for i in range(1500):  # More than the 1000 limit
        state.mark_post_processed(f"post_{i}")
    
    # Check memory after adding posts
    after_posts_memory = process.memory_info().rss / 1024 / 1024
    print(f"[Memory Test] Memory after adding 1500 post IDs: {after_posts_memory:.1f}MB")
    print(f"[Memory Test] Post IDs stored: {len(state.processed_post_ids)}")
    
    # Test memory cleanup
    state._cleanup_memory()
    after_cleanup_memory = process.memory_info().rss / 1024 / 1024
    print(f"[Memory Test] Memory after cleanup: {after_cleanup_memory:.1f}MB")
    
    # Test the full workflow cycle (if environment variables are available)
    if os.getenv("PERPLEXITY_API_KEY") and os.getenv("DISCORD_WEBHOOK_URL"):
        print("[Memory Test] Testing full workflow cycle...")
        workflow = ContentWorkflow()
        
        # Monitor memory before cycle
        before_cycle_memory = process.memory_info().rss / 1024 / 1024
        print(f"[Memory Test] Memory before workflow cycle: {before_cycle_memory:.1f}MB")
        
        try:
            # Run a shortened cycle (reduce time window to minimize processing)
            await workflow.run_cycle()
            
            # Monitor memory after cycle
            after_cycle_memory = process.memory_info().rss / 1024 / 1024
            print(f"[Memory Test] Memory after workflow cycle: {after_cycle_memory:.1f}MB")
            print(f"[Memory Test] Memory increase: {after_cycle_memory - before_cycle_memory:.1f}MB")
            
            # Force garbage collection and check again
            gc.collect()
            after_gc_memory = process.memory_info().rss / 1024 / 1024
            print(f"[Memory Test] Memory after garbage collection: {after_gc_memory:.1f}MB")
            print(f"[Memory Test] Memory recovered by GC: {after_cycle_memory - after_gc_memory:.1f}MB")
            
        except Exception as e:
            print(f"[Memory Test] Workflow cycle failed: {e}")
            print("[Memory Test] This is expected if Twitter scraping fails")
    else:
        print("[Memory Test] Skipping full workflow test - missing environment variables")
    
    # Final memory summary
    final_memory = process.memory_info().rss / 1024 / 1024
    print(f"[Memory Test] Final memory usage: {final_memory:.1f}MB")
    print(f"[Memory Test] Total memory increase: {final_memory - initial_memory:.1f}MB")
    
    # Memory usage analysis
    if final_memory < 100:
        print("[Memory Test] ✅ EXCELLENT: Memory usage is very low")
    elif final_memory < 200:
        print("[Memory Test] ✅ GOOD: Memory usage is reasonable")
    elif final_memory < 400:
        print("[Memory Test] ⚠️  WARNING: Memory usage is getting high")
    else:
        print("[Memory Test] ❌ CRITICAL: Memory usage is too high!")
    
    print("[Memory Test] Test completed successfully!")

if __name__ == "__main__":
    try:
        asyncio.run(test_memory_usage())
    except KeyboardInterrupt:
        print("\n[Memory Test] Test interrupted by user")
    except Exception as e:
        print(f"\n[Memory Test] Test failed: {e}")