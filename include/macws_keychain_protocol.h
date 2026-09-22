#pragma once

#include <stdint.h>

#define MACWS_KEYCHAIN_SERVICE "com.macwsguide.keychain"
#define MACWS_KEYCHAIN_VERSION 1u

#define MACWS_KEYCHAIN_KEY_VERSION "version"
#define MACWS_KEYCHAIN_KEY_OPERATION "operation"
#define MACWS_KEYCHAIN_KEY_QUERY "query"
#define MACWS_KEYCHAIN_KEY_ATTRIBUTES "attributes"
#define MACWS_KEYCHAIN_KEY_STATUS "status"
#define MACWS_KEYCHAIN_KEY_RESULT "result"

#define MACWS_KEYCHAIN_OP_COPY "copy"
#define MACWS_KEYCHAIN_OP_ADD "add"
#define MACWS_KEYCHAIN_OP_UPDATE "update"
#define MACWS_KEYCHAIN_OP_DELETE "delete"
