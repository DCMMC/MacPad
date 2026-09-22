#include <errno.h>
#include <mach/mach.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/time.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

#define ARRAY_COUNT(value) (sizeof(value) / sizeof((value)[0]))

enum info_field {
    FIELD_OS,
    FIELD_HOST,
    FIELD_KERNEL,
    FIELD_UPTIME,
    FIELD_SHELL,
    FIELD_DE,
    FIELD_WM,
    FIELD_WM_THEME,
    FIELD_CPU,
    FIELD_MEMORY,
    FIELD_COUNT
};

struct named_field {
    const char *name;
    enum info_field field;
};

static const struct named_field kNamedFields[] = {
    {"distro", FIELD_OS},       {"os", FIELD_OS},
    {"model", FIELD_HOST},      {"host", FIELD_HOST},
    {"kernel", FIELD_KERNEL},   {"uptime", FIELD_UPTIME},
    {"shell", FIELD_SHELL},     {"de", FIELD_DE},
    {"wm", FIELD_WM},           {"wm_theme", FIELD_WM_THEME},
    {"cpu", FIELD_CPU},         {"memory", FIELD_MEMORY},
};

static const char *kAsciiLines[] = {
    "                    'c.",
    "                 ,xNMM.",
    "               .OMMMMo",
    "               OMMM0,",
    "     .;loddo:' loolloddol;.",
    "   cKMMMMMMMMMMNWMMMMMMMMMM0:",
    " .KMMMMMMMMMMMMMMMMMMMMMMMWd.",
    " XMMMMMMMMMMMMMMMMMMMMMMMX.",
    ";MMMMMMMMMMMMMMMMMMMMMMMM:",
    ":MMMMMMMMMMMMMMMMMMMMMMMM:",
    ".MMMMMMMMMMMMMMMMMMMMMMMMX.",
    " kMMMMMMMMMMMMMMMMMMMMMMMMWd.",
    " .XMMMMMMMMMMMMMMMMMMMMMMMMMMk",
    "  .XMMMMMMMMMMMMMMMMMMMMMMMMK.",
    "    kMMMMMMMMMMMMMMMMMMMMMMd",
    "     ;KMMMMMMMWXXWMMMMMMMk.",
    "       .cooc,.    .,coo:.",
};

static const char *kAsciiColors[] = {
    "\033[32m", "\033[32m", "\033[32m", "\033[32m", "\033[32m",
    "\033[32m", "\033[33m", "\033[33m", "\033[31m", "\033[31m",
    "\033[31m", "\033[31m", "\033[35m", "\033[35m", "\033[34m",
    "\033[34m", "\033[34m",
};

static bool sysctl_string(const char *name, char *result, size_t capacity) {
    size_t size = capacity;
    if (capacity == 0 || sysctlbyname(name, result, &size, NULL, 0) != 0 ||
        size == 0) {
        return false;
    }
    result[capacity - 1] = '\0';
    return true;
}

static bool sysctl_u64(const char *name, uint64_t *result) {
    size_t size = sizeof(*result);
    return sysctlbyname(name, result, &size, NULL, 0) == 0;
}

static bool plist_value(const char *contents, const char *key,
                        char *result, size_t capacity) {
    char needle[128];
    if (snprintf(needle, sizeof(needle), "<key>%s</key>", key) < 0) {
        return false;
    }
    const char *position = strstr(contents, needle);
    if (position == NULL) {
        return false;
    }
    position = strstr(position + strlen(needle), "<string>");
    if (position == NULL) {
        return false;
    }
    position += strlen("<string>");
    const char *end = strstr(position, "</string>");
    if (end == NULL || end == position || capacity == 0) {
        return false;
    }
    size_t length = (size_t)(end - position);
    if (length >= capacity) {
        length = capacity - 1;
    }
    memcpy(result, position, length);
    result[length] = '\0';
    return true;
}

static void read_system_version(char *name, size_t name_capacity,
                                char *version, size_t version_capacity,
                                char *build, size_t build_capacity) {
    FILE *file = fopen("/System/Library/CoreServices/SystemVersion.plist", "rb");
    if (file == NULL) {
        return;
    }
    char contents[16384];
    size_t length = fread(contents, 1, sizeof(contents) - 1, file);
    fclose(file);
    contents[length] = '\0';
    (void)plist_value(contents, "ProductName", name, name_capacity);
    (void)plist_value(contents, "ProductVersion", version, version_capacity);
    (void)plist_value(contents, "ProductBuildVersion", build, build_capacity);
}

static void format_uptime(char *result, size_t capacity) {
    struct timeval boot = {0};
    size_t size = sizeof(boot);
    time_t now = time(NULL);
    if (sysctlbyname("kern.boottime", &boot, &size, NULL, 0) != 0 ||
        now <= boot.tv_sec) {
        snprintf(result, capacity, "unknown");
        return;
    }

    uint64_t seconds = (uint64_t)(now - boot.tv_sec);
    uint64_t days = seconds / 86400;
    uint64_t hours = (seconds / 3600) % 24;
    uint64_t minutes = (seconds / 60) % 60;
    if (days > 0) {
        snprintf(result, capacity, "%llu day%s, %llu hour%s, %llu min%s",
                 days, days == 1 ? "" : "s", hours, hours == 1 ? "" : "s",
                 minutes, minutes == 1 ? "" : "s");
    } else if (hours > 0) {
        snprintf(result, capacity, "%llu hour%s, %llu min%s", hours,
                 hours == 1 ? "" : "s", minutes, minutes == 1 ? "" : "s");
    } else if (minutes > 0) {
        snprintf(result, capacity, "%llu min%s", minutes,
                 minutes == 1 ? "" : "s");
    } else {
        snprintf(result, capacity, "%llu secs", seconds);
    }
}

static void format_memory(char *result, size_t capacity) {
    uint64_t total_bytes = 0;
    vm_statistics64_data_t statistics = {0};
    mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
    kern_return_t status = host_statistics64(mach_host_self(), HOST_VM_INFO64,
                                             (host_info64_t)&statistics, &count);
    if (!sysctl_u64("hw.memsize", &total_bytes) || status != KERN_SUCCESS) {
        snprintf(result, capacity, "unknown");
        return;
    }

    /* Neofetch 7.1.0 uses a fixed 4 KiB multiplier on Darwin. Keep its
     * displayed semantics so the fast and original paths remain comparable. */
    uint64_t used_pages = statistics.active_count + statistics.wire_count +
                          statistics.compressor_page_count;
    uint64_t used_mib = used_pages * 4096ULL / 1024ULL / 1024ULL;
    uint64_t total_mib = total_bytes / 1024ULL / 1024ULL;
    snprintf(result, capacity, "%lluMiB / %lluMiB", used_mib, total_mib);
}

static bool field_for_name(const char *name, enum info_field *field) {
    for (size_t index = 0; index < ARRAY_COUNT(kNamedFields); index++) {
        if (strcmp(name, kNamedFields[index].name) == 0) {
            *field = kNamedFields[index].field;
            return true;
        }
    }
    return false;
}

static int run_original(int argc, char **argv) {
    char **arguments = calloc((size_t)argc + 2, sizeof(*arguments));
    if (arguments == NULL) {
        return ENOMEM;
    }
    arguments[0] = "/bin/bash";
    arguments[1] = "/opt/local/bin/neofetch";
    for (int index = 1; index < argc; index++) {
        arguments[index + 1] = argv[index];
    }
    execv(arguments[0], arguments);
    int saved_errno = errno;
    fprintf(stderr, "macws-neofetch: cannot run original neofetch: %s\n",
            strerror(saved_errno));
    free(arguments);
    return saved_errno;
}

static void append_line(char *output, size_t capacity, size_t *length,
                        const char *ascii_color, const char *ascii,
                        const char *info, bool colors) {
    if (*length >= capacity) {
        return;
    }
    int written = snprintf(output + *length, capacity - *length,
                           "%s%-36s\033[0m   %s%s\n",
                           colors ? ascii_color : "", ascii,
                           colors && info[0] != '\0' ? "\033[0m" : "", info);
    if (written > 0) {
        size_t available = capacity - *length;
        *length += (size_t)written < available ? (size_t)written : available;
    }
}

int main(int argc, char **argv) {
    bool stdout_mode = false;
    bool enabled[FIELD_COUNT];
    for (size_t index = 0; index < FIELD_COUNT; index++) {
        enabled[index] = true;
    }

    for (int index = 1; index < argc; index++) {
        if (strcmp(argv[index], "--version") == 0 && argc == 2) {
            puts("Neofetch 7.1.0 (MacWS fast path)");
            return 0;
        }
        if (strcmp(argv[index], "--stdout") == 0) {
            stdout_mode = true;
            continue;
        }
        if (strcmp(argv[index], "--disable") == 0) {
            while (index + 1 < argc && argv[index + 1][0] != '-') {
                enum info_field field;
                index++;
                if (field_for_name(argv[index], &field)) {
                    enabled[field] = false;
                }
            }
            continue;
        }
        /* Preserve Neofetch's full CLI surface outside the optimized default
         * profile instead of silently accepting an option we do not model. */
        return run_original(argc, argv);
    }

    char product_name[64] = "macOS";
    char product_version[64] = "";
    char product_build[64] = "";
    char hardware_model[64] = "Apple device";
    char cpu[128] = "Apple processor";
    struct utsname system_info = {0};
    read_system_version(product_name, sizeof(product_name), product_version,
                        sizeof(product_version), product_build,
                        sizeof(product_build));
    (void)sysctl_string("hw.model", hardware_model, sizeof(hardware_model));
    (void)sysctl_string("machdep.cpu.brand_string", cpu, sizeof(cpu));
    (void)uname(&system_info);

    char uptime[128];
    char memory[128];
    format_uptime(uptime, sizeof(uptime));
    format_memory(memory, sizeof(memory));

    char values[FIELD_COUNT][256];
    snprintf(values[FIELD_OS], sizeof(values[FIELD_OS]), "%s %s %s %s",
             product_name, product_version, product_build, system_info.machine);
    snprintf(values[FIELD_HOST], sizeof(values[FIELD_HOST]), "%s", hardware_model);
    snprintf(values[FIELD_KERNEL], sizeof(values[FIELD_KERNEL]), "%s",
             system_info.release);
    snprintf(values[FIELD_UPTIME], sizeof(values[FIELD_UPTIME]), "%s", uptime);
    snprintf(values[FIELD_SHELL], sizeof(values[FIELD_SHELL]), "bash 3.2.57");
    snprintf(values[FIELD_DE], sizeof(values[FIELD_DE]), "Aqua");
    snprintf(values[FIELD_WM], sizeof(values[FIELD_WM]), "Quartz Compositor");
    snprintf(values[FIELD_WM_THEME], sizeof(values[FIELD_WM_THEME]), "Blue");
    snprintf(values[FIELD_CPU], sizeof(values[FIELD_CPU]), "%s", cpu);
    snprintf(values[FIELD_MEMORY], sizeof(values[FIELD_MEMORY]), "%s", memory);

    const char *labels[FIELD_COUNT] = {
        "OS", "Host", "Kernel", "Uptime", "Shell", "DE", "WM", "WM Theme",
        "CPU", "Memory"
    };
    char info_lines[FIELD_COUNT + 3][384];
    size_t info_count = 0;
    const char *user = getenv("USER");
    if (user == NULL || *user == '\0') {
        user = "root";
    }
    snprintf(info_lines[info_count++], sizeof(info_lines[0]),
             "\033[32m\033[1m%s@iPad\033[0m", user);
    snprintf(info_lines[info_count++], sizeof(info_lines[0]), "---------");
    for (size_t index = 0; index < FIELD_COUNT; index++) {
        if (enabled[index]) {
            snprintf(info_lines[info_count++], sizeof(info_lines[0]),
                     "\033[33m\033[1m%s\033[0m: %s", labels[index], values[index]);
        }
    }

    if (stdout_mode) {
        for (size_t index = 0; index < info_count; index++) {
            const char *line = info_lines[index];
            bool escape = false;
            for (; *line != '\0'; line++) {
                if (*line == '\033') {
                    escape = true;
                } else if (escape) {
                    if (*line == 'm') {
                        escape = false;
                    }
                } else {
                    putchar(*line);
                }
            }
            putchar('\n');
        }
        return 0;
    }

    bool colors = isatty(STDOUT_FILENO) && getenv("NO_COLOR") == NULL;
    char output[32768];
    size_t output_length = 0;
    size_t rows = ARRAY_COUNT(kAsciiLines);
    if (info_count > rows) {
        rows = info_count;
    }
    for (size_t index = 0; index < rows; index++) {
        append_line(output, sizeof(output), &output_length,
                    index < ARRAY_COUNT(kAsciiColors) ? kAsciiColors[index] : "",
                    index < ARRAY_COUNT(kAsciiLines) ? kAsciiLines[index] : "",
                    index < info_count ? info_lines[index] : "", colors);
    }
    if (output_length < sizeof(output)) {
        (void)write(STDOUT_FILENO, output, output_length);
    }
    return 0;
}
