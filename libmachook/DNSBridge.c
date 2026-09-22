#include <arpa/inet.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <errno.h>
#include <netdb.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <xpc/xpc.h>

#include "macws_control_protocol.h"

#define MACWS_DNS_MAGIC UINT64_C(0x4d41435753444e53)

struct macws_addrinfo_node {
    uint64_t magic;
    struct addrinfo info;
    struct sockaddr_storage address;
    char canonname[1025];
};

static xpc_connection_t macws_dns_connection(void) {
    static xpc_connection_t connection;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        xpc_connection_t (*createMach)(const char *, dispatch_queue_t,
                                       uint64_t) = dlsym(
            RTLD_DEFAULT, "xpc_connection_create_mach_service");
        if (createMach)
            connection = createMach(MACWS_CONTROL_SERVICE, NULL, 0);
        if (connection) {
            xpc_connection_set_event_handler(connection,
                                              ^(xpc_object_t event) {
                (void)event;
            });
            xpc_connection_resume(connection);
        }
    });
    return connection;
}

static bool macws_is_bridge_node(const char *node) {
    if (!node || node[0] == '\0' || strnlen(node, 254) > 253) return false;
    // Numeric addresses and local-only names do not need the iOS resolver.
    struct in_addr address4;
    struct in6_addr address6;
    if (inet_pton(AF_INET, node, &address4) == 1 ||
        inet_pton(AF_INET6, node, &address6) == 1) return false;
    if (!strcmp(node, "localhost") || !strcmp(node, "localhost.localdomain"))
        return false;
    return true;
}

static void macws_free_bridge_list(struct addrinfo *list) {
    while (list) {
        struct addrinfo *next = list->ai_next;
        struct macws_addrinfo_node *node =
            (struct macws_addrinfo_node *)((char *)list -
                offsetof(struct macws_addrinfo_node, info));
        node->magic = 0;
        free(node);
        list = next;
    }
}

static int macws_bridge_getaddrinfo(const char *node, const char *service,
                                    const struct addrinfo *hints,
                                    struct addrinfo **result) {
    xpc_connection_t connection = macws_dns_connection();
    if (!connection) return EAI_AGAIN;

    xpc_object_t request = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_OP,
                              MACWS_CONTROL_OP_RESOLVE_HOST);
    xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_DNS_NODE, node);
    if (service)
        xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_DNS_SERVICE,
                                  service);
    xpc_dictionary_set_int64(request, MACWS_CONTROL_KEY_DNS_FLAGS,
                             hints ? hints->ai_flags : 0);
    xpc_dictionary_set_int64(request, MACWS_CONTROL_KEY_DNS_FAMILY,
                             hints ? hints->ai_family : AF_UNSPEC);
    xpc_dictionary_set_int64(request, MACWS_CONTROL_KEY_DNS_SOCKTYPE,
                             hints ? hints->ai_socktype : 0);
    xpc_dictionary_set_int64(request, MACWS_CONTROL_KEY_DNS_PROTOCOL,
                             hints ? hints->ai_protocol : 0);

    xpc_object_t reply = xpc_connection_send_message_with_reply_sync(
        connection, request);
    xpc_release(request);
    if (!reply || xpc_get_type(reply) != XPC_TYPE_DICTIONARY) {
        if (reply) xpc_release(reply);
        return EAI_AGAIN;
    }
    int error = (int)xpc_dictionary_get_int64(reply, "gai_error");
    if (error != 0) {
        xpc_release(reply);
        return error;
    }

    xpc_object_t entries = xpc_dictionary_get_value(reply, "results");
    if (!entries || xpc_get_type(entries) != XPC_TYPE_ARRAY) {
        xpc_release(reply);
        return EAI_FAIL;
    }

    struct addrinfo *head = NULL;
    struct addrinfo **tail = &head;
    size_t count = xpc_array_get_count(entries);
    for (size_t index = 0; index < count && index < 64; index++) {
        xpc_object_t entry = xpc_array_get_value(entries, index);
        if (!entry || xpc_get_type(entry) != XPC_TYPE_DICTIONARY) continue;
        size_t addressLength = 0;
        const void *address = xpc_dictionary_get_data(
            entry, "address", &addressLength);
        int family = (int)xpc_dictionary_get_int64(entry, "family");
        if (!address || addressLength == 0 ||
            addressLength > sizeof(struct sockaddr_storage) ||
            (family != AF_INET && family != AF_INET6)) continue;

        struct macws_addrinfo_node *allocated = calloc(1, sizeof(*allocated));
        if (!allocated) {
            macws_free_bridge_list(head);
            xpc_release(reply);
            return EAI_MEMORY;
        }
        allocated->magic = MACWS_DNS_MAGIC;
        allocated->info.ai_flags =
            (int)xpc_dictionary_get_int64(entry, "flags");
        allocated->info.ai_family = family;
        allocated->info.ai_socktype =
            (int)xpc_dictionary_get_int64(entry, "socktype");
        allocated->info.ai_protocol =
            (int)xpc_dictionary_get_int64(entry, "protocol");
        allocated->info.ai_addrlen = (socklen_t)addressLength;
        memcpy(&allocated->address, address, addressLength);
        allocated->info.ai_addr = (struct sockaddr *)&allocated->address;
        const char *canonname = xpc_dictionary_get_string(entry, "canonname");
        if (canonname) {
            strlcpy(allocated->canonname, canonname,
                    sizeof(allocated->canonname));
            allocated->info.ai_canonname = allocated->canonname;
        }
        *tail = &allocated->info;
        tail = &allocated->info.ai_next;
    }
    xpc_release(reply);
    if (!head) return EAI_NONAME;
    *result = head;
    return 0;
}

int macws_getaddrinfo(const char *node, const char *service,
                      const struct addrinfo *hints,
                      struct addrinfo **result) {
    if (!result) return EAI_FAIL;
    *result = NULL;
    // A call originating inside the interposing image binds to libSystem's
    // replacee; dyld applies the tuple to imports in the other images only.
    // This is the same non-recursive pattern used by mac_hooks.m's mach_msg
    // interpose and avoids fabricating an arm64e callable pointer from dlsym.
    int localError = getaddrinfo(node, service, hints, result);
    if (localError == 0 || !macws_is_bridge_node(node)) return localError;

    int bridged = macws_bridge_getaddrinfo(node, service, hints, result);
    if (getenv("MACWS_DNS_DEBUG"))
        fprintf(stderr, "#### DNS-BRIDGE node=%s local=%d bridged=%d\n",
                node ?: "(null)", localError, bridged);
    return bridged;
}

void macws_freeaddrinfo(struct addrinfo *list) {
    if (list) {
        struct macws_addrinfo_node *node =
            (struct macws_addrinfo_node *)((char *)list -
                offsetof(struct macws_addrinfo_node, info));
        if (node->magic == MACWS_DNS_MAGIC) {
            macws_free_bridge_list(list);
            return;
        }
    }
    freeaddrinfo(list);
}

struct hostent *macws_gethostbyname(const char *name) {
    struct hostent *local = gethostbyname(name);
    if (local || !macws_is_bridge_node(name)) return local;

    struct addrinfo hints = {
        .ai_family = AF_INET,
        .ai_socktype = SOCK_STREAM,
    };
    struct addrinfo *resolved = NULL;
    if (macws_bridge_getaddrinfo(name, NULL, &hints, &resolved) != 0)
        return NULL;

    static _Thread_local struct hostent entry;
    static _Thread_local char hostName[256];
    static _Thread_local struct in_addr addresses[16];
    static _Thread_local char *addressPointers[17];
    memset(&entry, 0, sizeof(entry));
    strlcpy(hostName, name, sizeof(hostName));
    size_t count = 0;
    for (struct addrinfo *item = resolved; item && count < 16;
         item = item->ai_next) {
        if (item->ai_family != AF_INET ||
            item->ai_addrlen < sizeof(struct sockaddr_in)) continue;
        addresses[count] = ((struct sockaddr_in *)item->ai_addr)->sin_addr;
        addressPointers[count] = (char *)&addresses[count];
        count++;
    }
    addressPointers[count] = NULL;
    macws_free_bridge_list(resolved);
    if (count == 0) return NULL;
    entry.h_name = hostName;
    entry.h_addrtype = AF_INET;
    entry.h_length = sizeof(struct in_addr);
    entry.h_addr_list = addressPointers;
    return &entry;
}

#define DYLD_INTERPOSE(_replacement, _replacee)                              \
    __attribute__((used)) static struct {                                    \
        const void *replacement;                                             \
        const void *replacee;                                                \
    } _interpose_##_replacee __attribute__((section("__DATA,__interpose"))) = { \
        (const void *)(uintptr_t)&_replacement,                              \
        (const void *)(uintptr_t)&_replacee                                  \
    }

DYLD_INTERPOSE(macws_getaddrinfo, getaddrinfo);
DYLD_INTERPOSE(macws_freeaddrinfo, freeaddrinfo);
DYLD_INTERPOSE(macws_gethostbyname, gethostbyname);
