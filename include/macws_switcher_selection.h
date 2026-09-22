#import <Foundation/Foundation.h>
#import <objc/message.h>

// A Stage Manager group is an immutable layout, not the identity of a Scene.
// Rebind only a disappeared Host-containing group whose entire old membership
// survives in exactly one current group. Never guess by bundle or array index.
static id MacWSSwitcherObject(id object, NSString *selectorName) {
    SEL selector = NSSelectorFromString(selectorName);
    return [object respondsToSelector:selector]
        ? ((id (*)(id, SEL))objc_msgSend)(object, selector) : nil;
}

static NSSet<NSString *> *MacWSSwitcherSceneIdentities(id layout,
                                                     BOOL *containsHost) {
    NSArray *items = MacWSSwitcherObject(layout, @"allItems");
    if (![items isKindOfClass:NSArray.class] || !items.count) return nil;
    NSMutableSet *identities = [NSMutableSet set];
    BOOL host = NO;
    for (id item in items) {
        NSString *scene = MacWSSwitcherObject(item, @"uniqueIdentifier");
        NSString *bundle = MacWSSwitcherObject(item, @"bundleIdentifier");
        if (![scene isKindOfClass:NSString.class] || !scene.length ||
            ![bundle isKindOfClass:NSString.class] || !bundle.length) return nil;
        // Include both fields rather than relying on bundle-wide equality.
        [identities addObject:[NSString stringWithFormat:@"%lu:%@%@",
            (unsigned long)bundle.length, bundle, scene]];
        host |= [bundle isEqualToString:@"com.macwsguide.host"];
    }
    if (containsHost) *containsHost = host;
    return identities;
}

static id MacWSSwitcherReplacementSelection(id selected, NSArray *layouts) {
    if (!selected || ![layouts isKindOfClass:NSArray.class] ||
        [layouts containsObject:selected]) return nil;
    BOOL containsHost = NO;
    NSSet *oldScenes = MacWSSwitcherSceneIdentities(selected, &containsHost);
    if (!containsHost || !oldScenes.count) return nil;
    id replacement = nil;
    for (id candidate in layouts) {
        NSSet *scenes = MacWSSwitcherSceneIdentities(candidate, NULL);
        if (!scenes || ![oldScenes isSubsetOfSet:scenes]) continue;
        if (replacement) return nil; // Ambiguous: do not change selection.
        replacement = candidate;
    }
    return replacement;
}
