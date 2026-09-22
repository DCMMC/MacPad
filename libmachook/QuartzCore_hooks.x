@import CydiaSubstrate;
@import Darwin;
@import QuartzCore;
#import "interpose.h"

// Fix hangs
extern double NXClickTime();
extern void NXGetClickSpace();

double NXClickTime_new() {
    return 0.0;
}
void NXGetClickSpace_new() {}
DYLD_INTERPOSE(NXClickTime_new, NXClickTime)
DYLD_INTERPOSE(NXGetClickSpace_new, NXGetClickSpace)

// (Removed LAZY CA::OGL::BlurState::tile_downsample → return 0 stub.
// It was protecting against get_tile_pipeline → abort_with_payload when
// MTLSim couldn't build a tile pipeline. The real abort never fired in
// the post-MACWS_AGX_REGISTER_CLASSES run, so the stub was dead code
// hiding what the new code path actually does. If the abort returns,
// fix get_tile_pipeline upstream — don't re-stub here. See AGENTS.md
// "Patch Discipline".)

__attribute__((constructor)) static void InitQuartzCoreHooks() {
    const char *utility_process = getenv("MACWS_UTILITY_PROCESS");
    if (utility_process && strcmp(utility_process, "1") == 0) return;
    const char *shell_env = getenv("VSCODE_RESOLVING_ENVIRONMENT");
    if (shell_env && strcmp(shell_env, "1") == 0) return;

    const char *quartzCorePath = "/System/Library/Frameworks/QuartzCore.framework/Versions/A/QuartzCore";
    void *handle = dlopen(quartzCorePath, RTLD_GLOBAL);
    assert(handle);
    (void)handle;
}
