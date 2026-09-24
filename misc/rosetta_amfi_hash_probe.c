#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern int __sandbox_ms(const char *policy, int operation, void *argument);

struct macws_sandbox_buffer {
    void *data;
    size_t size;
};

int main(void) {
    uint8_t hash[32];
    memset(hash, 0xa5, sizeof(hash));
    struct macws_sandbox_buffer argument = { hash, sizeof(hash) };
    errno = 0;
    int result = __sandbox_ms("AMFI", 0x5c, &argument);
    int saved_errno = errno;

    printf("result=%d errno=%d (%s) size=%zu hash=", result, saved_errno,
           strerror(saved_errno), argument.size);
    for (size_t index = 0; index < sizeof(hash); ++index)
        printf("%02x", hash[index]);
    putchar('\n');
    return result == 0 ? 0 : 1;
}
