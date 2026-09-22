//
//  os_log_hooks.m
//  
//
//  Created by Duy Tran on 4/8/25.
//

@import Darwin;
@import Foundation;
#import "interpose.h"
#import <os/log.h>

/*
 * Structure definition
 */
struct sbuf {
    char        *s_buf;        /* storage buffer */
    void        *s_unused;    /* binary compatibility. */
    int         s_size;    /* size of storage buffer */
    int         s_len;        /* current length of string */
#define    SBUF_FIXEDLEN    0x00000000    /* fixed length buffer (default) */
#define    SBUF_AUTOEXTEND    0x00000001    /* automatically extend buffer */
#define    SBUF_USRFLAGMSK 0x0000ffff    /* mask of flags the user may specify */
#define    SBUF_DYNAMIC    0x00010000    /* s_buf must be freed */
#define    SBUF_FINISHED    0x00020000    /* set by sbuf_finish() */
#define    SBUF_OVERFLOWED    0x00040000    /* sbuf overflowed */
#define    SBUF_DYNSTRUCT    0x00080000    /* sbuf must be freed */
    int         s_flags;    /* flags */
};

struct sbuf *sbuf_new_auto(void) {
    struct sbuf *sb = calloc(1, sizeof(*sb));
    sb->s_buf = malloc(1024);
    sb->s_size = 1024;
    sb->s_len = 0;
    return sb;
}

int sbuf_bcat(struct sbuf *sb, const void *ptr, size_t len) {
    if ((sb->s_len + len) > sb->s_size) {
        sb->s_size *= 2;
        sb->s_buf = realloc(sb->s_buf, sb->s_size);
    }

    memcpy(sb->s_buf + sb->s_len, ptr, len);
    sb->s_len += len;
    return sb->s_len;
}

int sbuf_cat(struct sbuf *sb, const char *str) {
    return sbuf_bcat(sb, str, (int)strlen(str) * sizeof(char));
}

int sbuf_vprintf(struct sbuf *sb, const char *fmt, va_list ap) {
    char *str; vasprintf(&str, fmt, ap);
    int ret = sbuf_cat(sb, str);
    free(str);
    return ret;
}

int sbuf_printf(struct sbuf *sb, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int ret = sbuf_vprintf(sb, fmt, ap);
    va_end(ap);
    return ret;
}

int sbuf_putc(struct sbuf *sb, int c) {
    return sbuf_bcat(sb, &c, sizeof(int));
}

char *sbuf_data(struct sbuf *sb) {
    bzero(sb->s_buf + sb->s_len, sb->s_size - sb->s_len);
    return sb->s_buf;
}

void sbuf_delete(struct sbuf *sb) {
    free(sb->s_buf);
    free(sb);
}

void _libtrace_assert_fail(const char *message, ...) {
    va_list args;
    va_start(args, message);
    fprintf(stderr, "%s\n", message);
    vfprintf(stderr, message, args);
    va_end(args);
    abort();
}
#define libtrace_assert(cond, message, ...) \
    do { \
        if (__builtin_expect(!(cond), 0)) \
            _libtrace_assert_fail("BUG IN LIBTRACE: " message, ##__VA_ARGS__); \
    } while (0)

typedef enum {
    privacy_setting_unset = 0,
    privacy_setting_private = 1,
    privacy_setting_public = 2
} privacy_setting_t;

char *os_log_decode_buffer(const char *formatString, uint8_t *buffer, uint32_t bufferSize) {
    uint32_t bufferIndex = 0;
    // uint8_t summaryByte = buffer[bufferIndex]; // not actually used, hence commented out
    bufferIndex++;

    uint8_t argsSeen = 0;
    uint8_t argCount = buffer[bufferIndex];
    bufferIndex++;

    struct sbuf *sbuf = sbuf_new_auto();
    while (formatString[bufferIndex++] != '\0') {
        if (formatString[bufferIndex] != '%') {
            sbuf_putc(sbuf, formatString[bufferIndex]);
            continue;
        }

        libtrace_assert(formatString[bufferIndex] == '%', "next char not % (this shouldn't happen)");
        libtrace_assert(argsSeen < argCount, "Too many format specifiers in os_log string (expected %d)", argCount);

        privacy_setting_t privacy = privacy_setting_public;
        if (formatString[bufferIndex] == '{') {
            const char *closingBracket = strchr(formatString + bufferIndex, '}');
            size_t brlen = closingBracket - (formatString + bufferIndex);
            char *attribute = (char *)malloc(brlen + 1);

            strlcpy(attribute, formatString + bufferIndex, brlen + 1);
            bufferIndex += strlen(attribute) + 1;

            while (formatString[bufferIndex] == '.' ||
                   formatString[bufferIndex] == '*' ||
                   isnumber(formatString[bufferIndex])) {
                bufferIndex++;
            }

            char *formattedArgument = NULL;
            if (formatString[bufferIndex] == 's') {
                uint8_t length = buffer[bufferIndex];
                bufferIndex++;
                formattedArgument = calloc(length + 1, sizeof(char));
                memcpy(formattedArgument, buffer + bufferIndex, length);
                bufferIndex += length * sizeof(char);

                if (privacy == privacy_setting_unset) privacy = privacy_setting_private;
            } else if (formatString[bufferIndex] == 'S') {
                uint8_t length = buffer[bufferIndex];
                bufferIndex++;

                wchar_t *wideArgument = calloc(length + 1, sizeof(wchar_t));
                memcpy(wideArgument, buffer + bufferIndex, length * sizeof(wchar_t));
                bufferIndex += length * sizeof(wchar_t);

                formattedArgument = calloc(length + 1, sizeof(char));
                wcstombs(formattedArgument, wideArgument, length);
                free(wideArgument);

                if (privacy == privacy_setting_unset) privacy = privacy_setting_private;
            } else if (formatString[bufferIndex] == 'P') {
                uint8_t length = buffer[bufferIndex];
                bufferIndex++;

                struct sbuf *dataHex = sbuf_new_auto();
                sbuf_putc(dataHex, '<');

                for (uint8_t i = 0; i < length; i++) {
                    sbuf_printf(dataHex, "%02X", buffer[bufferIndex]);
                    bufferIndex++;
                }

                sbuf_putc(dataHex, '>');
                formattedArgument = strdup(sbuf_data(dataHex));
                sbuf_delete(dataHex);

                if (privacy == privacy_setting_unset) privacy = privacy_setting_private;
            } else if (formatString[bufferIndex] == '@') {
                // FIXME: Correctly describe Objective-C objects
                formattedArgument = strdup("<ObjC object>");
                if (privacy == privacy_setting_unset) privacy = privacy_setting_private;
            } else if (formatString[bufferIndex] == 'm') {
                bufferIndex++; // skip zero "length"
                formattedArgument = strdup(strerror(errno));
                if (privacy == privacy_setting_unset) privacy = privacy_setting_public;
            } else if (formatString[bufferIndex] == 'd') {
                uint8_t length = buffer[bufferIndex];
                bufferIndex++;

                int64_t integer = 0;
                if (length == 1) integer = *(int8_t *)buffer;
                else if (length == 2) integer = *(int16_t *)buffer;
                else if (length == 4) integer = *(int32_t *)buffer;
                else if (length == 8) integer = *(int64_t *)buffer;
                else libtrace_assert(false, "Unexpected integer size %d", length);

                bufferIndex += length;
                asprintf(&formattedArgument, "%lld", integer);
                if (privacy == privacy_setting_unset) privacy = privacy_setting_public;
            } else if (formatString[bufferIndex] == 'u') {
                uint8_t length = buffer[bufferIndex];
                bufferIndex++;

                uint64_t integer = 0;
                if (length == 1) integer = *(uint8_t *)buffer;
                else if (length == 2) integer = *(uint16_t *)buffer;
                else if (length == 4) integer = *(uint32_t *)buffer;
                else if (length == 8) integer = *(uint64_t *)buffer;
                else libtrace_assert(false, "Unexpected integer size %d", length);

                bufferIndex += length;
                asprintf(&formattedArgument, "%llu", integer);
                if (privacy == privacy_setting_unset) privacy = privacy_setting_public;
            } else if (formatString[bufferIndex] == 'x') {
                uint8_t length = buffer[bufferIndex];
                bufferIndex++;

                uint64_t integer = 0;
                if (length == 1) integer = *(uint8_t *)buffer;
                else if (length == 2) integer = *(uint16_t *)buffer;
                else if (length == 4) integer = *(uint32_t *)buffer;
                else if (length == 8) integer = *(uint64_t *)buffer;
                else libtrace_assert(false, "Unexpected integer size %d", length);

                bufferIndex += length;
                asprintf(&formattedArgument, "0x%llx", integer);
                if (privacy == privacy_setting_unset) privacy = privacy_setting_public;
            } else if (formatString[bufferIndex] == 'X') {
                uint8_t length = buffer[bufferIndex];
                bufferIndex++;

                uint64_t integer = 0;
                if (length == 1) integer = *(uint8_t *)buffer;
                else if (length == 2) integer = *(uint16_t *)buffer;
                else if (length == 4) integer = *(uint32_t *)buffer;
                else if (length == 8) integer = *(uint64_t *)buffer;
                else libtrace_assert(false, "Unexpected integer size %d", length);

                bufferIndex += length;
                asprintf(&formattedArgument, "0x%llX", integer);
                if (privacy == privacy_setting_unset) privacy = privacy_setting_public;
            } else {
                libtrace_assert(false, "Unknown format argument %%%c in os_log() call", formatString[bufferIndex]);
            }

            if (privacy == privacy_setting_public) {
                sbuf_cat(sbuf, formattedArgument);
            } else {
                sbuf_cat(sbuf, "<private>");
            }

            free(formattedArgument);
            free(attribute);

            argsSeen++;
        }
    }

    char *retval = strdup(sbuf_data(sbuf));
    sbuf_delete(sbuf);
    return retval;
}

void _os_log_impl(void *dso, os_log_t log, os_log_type_t type, const char *format, uint8_t *buf, uint32_t size);
void _os_log_impl_new(void *dso, os_log_t log, os_log_type_t type, const char *format, uint8_t *buf, uint32_t size) {
    //char *decodedBuffer = os_log_decode_buffer(format, buf, size);
    NSLog(@"OS_LOG: %s", format);
    _os_log_impl(dso, log, type, format, buf, size);
}
DYLD_INTERPOSE(_os_log_impl_new, _os_log_impl);
