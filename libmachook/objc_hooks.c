// (Removed LAZY objc_addExceptionHandler / objc_removeExceptionHandler
// → no-op interposers. They were a "workaround for strange SIGTRAPs"
// without finding the cause. In the post-MACWS_AGX_REGISTER_CLASSES
// run no exception-handler-related SIGTRAP fired, so the no-op was
// dead code masking what the real exception handler does. If a SIGTRAP
// returns, fix the upstream — don't re-stub here. See AGENTS.md
// "Patch Discipline".)
