# Smart Async Pattern Consideration

## Current Status: ❌ NOT IMPLEMENTED

This project does **not** currently use the smart async pattern described in the `mcp-builder` skill.

## What is Smart Async?

The smart async pattern (from the mcp-builder skill) is a decorator-based approach for MCP tools that provides:

1. **Automatic timeout handling** - Tools try to complete synchronously, but automatically switch to background execution if they exceed a timeout threshold
2. **Shielded task execution** - Uses `asyncio.shield()` to prevent task cancellation
3. **Dual return types** - Returns either direct results (fast) or job metadata (slow)
4. **Progress tracking** - Optional progress updates for long-running tasks
5. **Job persistence** - Jobs are saved to disk and survive across status checks

## Current Implementation

This project has a **simpler async model**:

- **Explicit async variants** - Separate `_async` suffix functions (e.g., `py_run_script_in_dir_async`)
- **No automatic switching** - User must explicitly choose sync vs async
- **Manual job management** - Jobs tracked in memory, basic persistence
- **No timeout switching** - Sync tools have timeout parameter but don't auto-switch to background

## Comparison

| Feature | Current Implementation | Smart Async Pattern |
|---------|----------------------|---------------------|
| Timeout handling | Manual (sync only) | Automatic switch to background |
| API complexity | Two separate functions | Single function with mode flag |
| Return type | Always consistent per function | Dynamic based on execution time |
| Progress tracking | ❌ No | ✅ Yes (via contextvars) |
| Job persistence | Basic (in-memory + manual save) | Full (automatic saves) |
| Shielded execution | ❌ No | ✅ Yes (prevents cancellation) |
| Explicit async mode | Via separate `_async` functions | Via `async_mode=True` parameter |

## Should We Implement Smart Async?

### ✅ Reasons TO Implement

1. **Better UX** - Single API instead of sync/async pairs
2. **Automatic optimization** - Fast tasks complete inline, slow tasks background automatically
3. **Progress tracking** - Built-in progress updates for long operations
4. **More robust** - Shielded tasks can't be accidentally cancelled
5. **Consistent with best practices** - Matches mcp-builder skill recommendations

### ❌ Reasons NOT TO Implement

1. **Breaking change** - Would require API redesign or maintaining both
2. **Complexity** - More complex implementation to maintain
3. **Current model works** - Explicit sync/async is simple and predictable
4. **Not needed yet** - No user complaints about current approach
5. **Testing burden** - Would require comprehensive new test suite

## Recommendation

### For Now: Keep Current Implementation ✅

**Reasons:**
- Current model is working well
- API is stable and documented
- Users have explicit control (predictable behavior)
- Simpler implementation = easier to maintain

### Future Migration Path

If you decide to adopt smart async later:

1. **Add `@smart_async` decorator** alongside existing functions
2. **Create new tool variants** with `_v2` suffix to avoid breaking changes
3. **Deprecate old functions** gradually over time
4. **Provide migration guide** for users

Example migration:
```python
# Old (keep for compatibility)
@mcp.tool()
def py_run_script_in_dir(...) -> RunScriptResult:
    ...

@mcp.tool()
def py_run_script_in_dir_async(...) -> AsyncJobStart:
    ...

# New (smart async)
@mcp.tool()
@smart_async(timeout=50.0)
async def py_run_script_v2(
    ...,
    async_mode: bool = False,
    job_label: str | None = None
) -> RunScriptResult | AsyncJobStart:
    ...
```

## Example Implementation

If you want to implement smart async, see:
- **Skill**: `mcp-builder` skill notes on smart async
- **Example code**: In skill notes under `examples/smart_async_decorator.py`
- **Test patterns**: 6 comprehensive tests for the decorator

Key files to create:
1. `src/python_mcp_server/smart_async.py` - Decorator implementation
2. `src/python_mcp_server/job_manager.py` - Enhanced job tracking
3. `tests/test_smart_async.py` - Test suite

## Decision

**Current decision: Do not implement smart async yet**

Revisit this decision if:
- Users request automatic background switching
- Long-running operations become common
- Progress tracking becomes a requirement
- You want to align more closely with mcp-builder best practices

## See Also

- `mcp-builder` skill: Smart async decorator notes
- Current async implementation: `src/python_mcp_server/__init__.py` (lines 571-838)
- Job management: Lines 842-1000