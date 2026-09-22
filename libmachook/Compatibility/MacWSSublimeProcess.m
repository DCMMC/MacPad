@import Darwin;
@import Foundation;
@import MachO;

#include <crt_externs.h>
#include <dlfcn.h>
#include <errno.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <spawn.h>

// Sublime Text 4200's exec_process(path, argc, argv, cwd) builds a private
// argv copy and then uses fork -> chdir -> execvp. On this host the child does
// not return from fork: iPadOS 16.3's libSystem child-handler chain calls the
// macOS 13 Network.framework nw_settings_child_has_forked handler after the
// forked task's libxpc text mapping has lost execute permission. The exact
// runtime witness is:
//
//   EXC_CRASH/SIGBUS code=0x0a100002 (BUS_ADRERR)
//   KERN_PROTECTION_FAILURE pc=libxpc`xpc_dictionary_apply
//   Network`-[OS_nw_dictionary dealloc]+0x70
//   _pthread_atfork_child_handlers
//
// Steam has the same cross-version fork failure and already uses an atomic
// posix_spawn adapter. Reproduce Sublime's process-launch contract at the
// exact, UUID-checked exec_process fork call site. This creates the plugin
// host as a fresh task, so no incompatible Network/XPC state is inherited; it
// does not suppress the plugin host or report a failed launch as successful.

static bool MacWSSublimeHeaderHasUUID(const struct mach_header_64 *header,
                                      const uint8_t expected[16]) {
    if (!header || header->magic != MH_MAGIC_64) return false;
    const struct load_command *command =
        (const struct load_command *)(header + 1);
    for (uint32_t index = 0; index < header->ncmds; index++) {
        if (command->cmd == LC_UUID) {
            const struct uuid_command *uuid =
                (const struct uuid_command *)command;
            return memcmp(uuid->uuid, expected, 16) == 0;
        }
        command = (const struct load_command *)
            ((const uint8_t *)command + command->cmdsize);
    }
    return false;
}

__attribute__((used, noinline)) static pid_t MacWSSublimeSpawnFromExecProcess(
    const char *executable, const char *workingDirectory,
    char *const arguments[], const void *returnAddress) {
    Dl_info caller = {0};
    if (!returnAddress || !dladdr(returnAddress, &caller) ||
        !caller.dli_fname || !caller.dli_fbase ||
        strcmp(caller.dli_fname,
               "/Applications/Sublime Text.app/Contents/MacOS/sublime_text") ||
        (uintptr_t)returnAddress - (uintptr_t)caller.dli_fbase != 0x26d3bc) {
        return fork();
    }
    if (!executable || !*executable || !arguments || !arguments[0]) {
        errno = EINVAL;
        return -1;
    }

    posix_spawn_file_actions_t actions;
    int result = posix_spawn_file_actions_init(&actions);
    bool actionsReady = result == 0;
    if (result == 0 && workingDirectory && *workingDirectory) {
        typedef int (*MacWSAddChdirActionFunction)(
            posix_spawn_file_actions_t *, const char *);
        MacWSAddChdirActionFunction addChdir =
            (MacWSAddChdirActionFunction)dlsym(
                RTLD_DEFAULT, "posix_spawn_file_actions_addchdir_np");
        result = addChdir ? addChdir(&actions, workingDirectory) : ENOSYS;
    }

    char ***environmentPointer = _NSGetEnviron();
    char **environment = environmentPointer ? *environmentPointer : NULL;
    pid_t child = -1;
    if (result == 0)
        result = posix_spawnp(&child, executable, &actions, NULL,
                              arguments, environment);
    if (actionsReady) posix_spawn_file_actions_destroy(&actions);
    if (result != 0) {
        fprintf(stderr,
                "[MacWSSublimeProcess] plugin spawn failed executable=%s "
                "cwd=%s error=%d (%s)\n",
                executable, workingDirectory ?: "", result,
                strerror(result));
        fflush(stderr);
        errno = result;
        return -1;
    }
    fprintf(stderr,
            "[MacWSSublimeProcess] plugin spawn executable=%s cwd=%s "
            "child=%d\n",
            executable, workingDirectory ?: "", child);
    fflush(stderr);
    return child;
}

// At Sublime's RE-confirmed exec_process call site, x19 is the executable,
// x20 the working directory, x23 the completed argv, and x30 the instruction
// following BL _fork. Preserve that register contract before a C prologue can
// reuse the callee-saved registers.
__attribute__((naked, used)) static pid_t MacWSSublimeForkGateway(void) {
#if defined(__arm64__)
    __asm__ volatile(
        "mov x3, x30\n"
        "mov x2, x23\n"
        "mov x1, x20\n"
        "mov x0, x19\n"
        "b _MacWSSublimeSpawnFromExecProcess\n");
#else
    __asm__ volatile("brk #0x1\n");
#endif
}

static void MacWSRebindSublimeForkImport(const struct mach_header *header,
                                         intptr_t slide) {
    static const uint8_t sublime4200UUID[16] = {
        0x4c, 0x4c, 0x44, 0xc0, 0x55, 0x55, 0x31, 0x44,
        0xa1, 0x08, 0x43, 0x70, 0xe2, 0x34, 0x42, 0xe0,
    };
    const struct mach_header_64 *header64 =
        (const struct mach_header_64 *)header;
    if (!MacWSSublimeHeaderHasUUID(header64, sublime4200UUID)) return;

    Dl_info image = {0};
    if (!dladdr(header, &image) || !image.dli_fname ||
        strcmp(image.dli_fname,
               "/Applications/Sublime Text.app/Contents/MacOS/sublime_text"))
        return;

    const struct segment_command_64 *linkedit = NULL;
    const struct symtab_command *symbols = NULL;
    const struct dysymtab_command *dynamicSymbols = NULL;
    const struct load_command *command =
        (const struct load_command *)(header64 + 1);
    for (uint32_t index = 0; index < header64->ncmds; index++) {
        if (command->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *segment =
                (const struct segment_command_64 *)command;
            if (!strcmp(segment->segname, SEG_LINKEDIT)) linkedit = segment;
        } else if (command->cmd == LC_SYMTAB) {
            symbols = (const struct symtab_command *)command;
        } else if (command->cmd == LC_DYSYMTAB) {
            dynamicSymbols = (const struct dysymtab_command *)command;
        }
        command = (const struct load_command *)
            ((const uint8_t *)command + command->cmdsize);
    }
    if (!linkedit || !symbols || !dynamicSymbols) return;

    uintptr_t linkeditBase = (uintptr_t)slide + linkedit->vmaddr -
        linkedit->fileoff;
    const struct nlist_64 *symbolTable =
        (const struct nlist_64 *)(linkeditBase + symbols->symoff);
    const char *stringTable =
        (const char *)(linkeditBase + symbols->stroff);
    const uint32_t *indirectTable =
        (const uint32_t *)(linkeditBase + dynamicSymbols->indirectsymoff);

    command = (const struct load_command *)(header64 + 1);
    for (uint32_t commandIndex = 0;
         commandIndex < header64->ncmds; commandIndex++) {
        if (command->cmd != LC_SEGMENT_64) {
            command = (const struct load_command *)
                ((const uint8_t *)command + command->cmdsize);
            continue;
        }
        const struct segment_command_64 *segment =
            (const struct segment_command_64 *)command;
        const struct section_64 *section =
            (const struct section_64 *)(segment + 1);
        for (uint32_t sectionIndex = 0;
             sectionIndex < segment->nsects; sectionIndex++, section++) {
            if ((section->flags & SECTION_TYPE) != S_LAZY_SYMBOL_POINTERS)
                continue;
            uintptr_t *pointers = (uintptr_t *)
                ((uintptr_t)slide + section->addr);
            size_t count = (size_t)(section->size / sizeof(uintptr_t));
            for (size_t pointerIndex = 0;
                 pointerIndex < count; pointerIndex++) {
                uint32_t indirectIndex = section->reserved1 + pointerIndex;
                if (indirectIndex >= dynamicSymbols->nindirectsyms) break;
                uint32_t symbolIndex = indirectTable[indirectIndex];
                if (symbolIndex == INDIRECT_SYMBOL_ABS ||
                    symbolIndex == INDIRECT_SYMBOL_LOCAL ||
                    symbolIndex == (INDIRECT_SYMBOL_LOCAL |
                                    INDIRECT_SYMBOL_ABS) ||
                    symbolIndex >= symbols->nsyms) continue;
                const char *name = stringTable +
                    symbolTable[symbolIndex].n_un.n_strx;
                if (!name || strcmp(name, "_fork")) continue;
                uintptr_t replacement =
                    (uintptr_t)MacWSSublimeForkGateway;
                __atomic_store_n(&pointers[pointerIndex], replacement,
                                 __ATOMIC_RELEASE);
                fprintf(stderr,
                        "[MacWSSublimeProcess] rebound fork import image=%s "
                        "slot=%p replacement=%p readback=%p\n",
                        image.dli_fname, &pointers[pointerIndex],
                        (void *)replacement,
                        (void *)__atomic_load_n(&pointers[pointerIndex],
                                                __ATOMIC_ACQUIRE));
                fflush(stderr);
                return;
            }
        }
        command = (const struct load_command *)
            ((const uint8_t *)command + command->cmdsize);
    }
}

__attribute__((constructor))
static void MacWSInstallSublimeProcessCompatibility(void) {
    const char *program = getprogname();
    if (!program || strcmp(program, "sublime_text")) return;
    _dyld_register_func_for_add_image(MacWSRebindSublimeForkImport);
    for (uint32_t index = 0; index < _dyld_image_count(); index++)
        MacWSRebindSublimeForkImport(_dyld_get_image_header(index),
                                     _dyld_get_image_vmaddr_slide(index));
}
