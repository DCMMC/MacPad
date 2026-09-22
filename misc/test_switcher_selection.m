#import "../include/macws_switcher_selection.h"
#include <assert.h>

@interface SelectionTestItem : NSObject
@property NSString *uniqueIdentifier;
@property NSString *bundleIdentifier;
@end
@implementation SelectionTestItem
@end
@interface SelectionTestLayout : NSObject
@property NSArray *allItems;
@end
@implementation SelectionTestLayout
@end

static id item(NSString *scene, BOOL host) {
    SelectionTestItem *item = [SelectionTestItem new];
    item.uniqueIdentifier = scene;
    item.bundleIdentifier = host ? @"com.macwsguide.host" : @"com.apple.mobilenotes";
    return item;
}
static id layout(NSArray *items) {
    SelectionTestLayout *layout = [SelectionTestLayout new];
    layout.allItems = items;
    return layout;
}
int main(void) {
    @autoreleasepool {
        id a = item(@"A", YES), b = item(@"B", YES), c = item(@"C", NO);
        id old = layout(@[a, c]), resized = layout(@[a, c]);
        id expanded = layout(@[a, b, c]), different = layout(@[b]);
        assert(MacWSSwitcherReplacementSelection(old, @[resized]) == resized);
        assert(MacWSSwitcherReplacementSelection(old, @[different, expanded]) == expanded);
        assert(MacWSSwitcherReplacementSelection(old, @[old, expanded]) == nil);
        assert(MacWSSwitcherReplacementSelection(old, @[resized, expanded]) == nil);
        assert(MacWSSwitcherReplacementSelection(old, @[layout(@[a])]) == nil);
        assert(MacWSSwitcherReplacementSelection(old, @[different]) == nil);
        assert(MacWSSwitcherReplacementSelection(old, @[]) == nil);
        assert(MacWSSwitcherReplacementSelection(nil, @[expanded]) == nil);
        assert(MacWSSwitcherReplacementSelection(layout(@[c]), @[layout(@[c])]) == nil);
        assert(MacWSSwitcherReplacementSelection(layout(@[a]),
            @[layout(@[item(@"A", NO)])]) == nil);
        assert(MacWSSwitcherReplacementSelection(layout(@[item(@"", YES)]),
            @[expanded]) == nil);
        assert(MacWSSwitcherReplacementSelection(layout(@[item(@"A", YES)]),
            @[layout(@[item(@"A", YES)])]) != nil);
        puts("PASS: 12 selection identity / membership / ambiguity cases");
    }
}
