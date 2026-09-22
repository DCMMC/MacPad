#import <dlfcn.h>
#import <stdio.h>
#import <sys/mount.h>
#import <sys/param.h>
#import <sys/stat.h>
#import <unistd.h>
#import <rootless.h>

#define CS_CDHASH_LEN 20
typedef uint8_t cdhash_t[CS_CDHASH_LEN];

struct trust_cache_entry2 {
    cdhash_t cdhash;
    uint8_t hash_type;
    uint8_t flags;
    uint8_t constraintCategory;
    uint8_t reserved0;
} __attribute__((__packed__));

struct trust_cache_entry1 {
    cdhash_t cdhash;
    uint8_t hash_type;
    uint8_t flags;
} __attribute__((__packed__));

struct trust_cache {
    uint32_t version;
    uuid_t uuid;
    uint32_t num_entries;
    union {
        struct trust_cache_entry2 entries2;
        struct trust_cache_entry1 entries;
       cdhash_t hashes;
    };
} __attribute__((__packed__));

// flags
#define CS_TRUST_CACHE_AMFID 0x1

#define LIBJAILBREAK_PATH ROOT_PATH("/usr/lib/libjailbreak.dylib")

int (*jbclient_root_trustcache_add_cdhash)(cdhash_t cdhash, size_t size);


int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <trustcache file>\n", argv[0]);
        return 1;
    }

    if (getuid() != 0) {
        fprintf(stderr, "Must be run as root\n");
        return 1;
    }

    if (access(LIBJAILBREAK_PATH, F_OK) != 0) {
        fprintf(stderr, "error: libjailbreak not found\n");
        return 1;
    }

    void *libjailbreak = dlopen(LIBJAILBREAK_PATH, RTLD_NOW);
    jbclient_root_trustcache_add_cdhash = dlsym(libjailbreak, "jbclient_root_trustcache_add_cdhash");

    struct stat s;
    int fd = open(argv[1], O_RDONLY);
    fstat(fd, &s);
    size_t size = s.st_size;
    struct trust_cache *cache = (struct trust_cache *)mmap(0, size, PROT_READ, MAP_PRIVATE, fd, 0);

    if (cache->version < 0 || cache->version > 2) {
        printf("Invalid tc version\n");
        munmap(cache, size);
        close(fd);
        return 1;
    }

    printf("version: %d, num_entries: %d\n", cache->version, cache->num_entries);

    for (int i = 0; i < cache->num_entries; i++) {
        cdhash_t cdhash;
        switch (cache->version) {
            case 0:
                memcpy(cdhash, (&cache->hashes)[i], CS_CDHASH_LEN);
            case 1:
                memcpy(cdhash, (&cache->entries)[i].cdhash, CS_CDHASH_LEN);
                break;
            case 2:
                memcpy(cdhash, (&cache->entries2)[i].cdhash, CS_CDHASH_LEN);
                break;
        }
        for (int i2 = 0; i2 < 20; i2++) {
            printf("%02x", cdhash[i2]);
        }
        printf("\n");
        jbclient_root_trustcache_add_cdhash(cdhash, sizeof(cdhash));
    }

    munmap(cache, size);
    close(fd);

    return 0;
}
