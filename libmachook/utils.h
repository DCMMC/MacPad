@import Darwin;
void xpc_add_bundle(char *, int);

void ModifyExecutableRegion(void *addr, size_t size, void(^callback)(void));
