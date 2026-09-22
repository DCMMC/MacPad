#import "MacWSKeyMapping.h"

uint16_t MacWSMacKeyCodeForHIDUsage(NSInteger usage) {
    static const uint16_t letterCodes[] = {
        0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46,
        45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16, 6,
    };
    if (usage >= 4 && usage <= 29) return letterCodes[usage - 4];
    static const uint16_t digitCodes[] = {
        18, 19, 20, 21, 23, 22, 26, 28, 25, 29,
    };
    if (usage >= 30 && usage <= 39) return digitCodes[usage - 30];
    switch (usage) {
        case 40: return 36;  // Return
        case 41: return 53;  // Escape
        case 42: return 51;  // Delete backward
        case 43: return 48;  // Tab
        case 44: return 49;  // Space
        case 45: return 27;  // -
        case 46: return 24;  // =
        case 47: return 33;  // [
        case 48: return 30;  // ]
        case 49: return 42;  // backslash
        case 51: return 41;  // ;
        case 52: return 39;  // quote
        case 53: return 50;  // grave
        case 54: return 43;  // comma
        case 55: return 47;  // period
        case 56: return 44;  // slash
        case 57: return 57;  // Caps Lock
        case 58: return 122; // F1
        case 59: return 120; // F2
        case 60: return 99;  // F3
        case 61: return 118; // F4
        case 62: return 96;  // F5
        case 63: return 97;  // F6
        case 64: return 98;  // F7
        case 65: return 100; // F8
        case 66: return 101; // F9
        case 67: return 109; // F10
        case 68: return 103; // F11
        case 69: return 111; // F12
        case 74: return 115; // Home
        case 75: return 116; // Page Up
        case 76: return 117; // Delete forward
        case 77: return 119; // End
        case 78: return 121; // Page Down
        case 79: return 124; // Right
        case 80: return 123; // Left
        case 81: return 125; // Down
        case 82: return 126; // Up
        case 224: return 59; // Left Control
        case 225: return 56; // Left Shift
        case 226: return 58; // Left Option
        case 227: return 55; // Left Command
        case 228: return 62; // Right Control
        case 229: return 60; // Right Shift
        case 230: return 61; // Right Option
        case 231: return 54; // Right Command
        default: return UINT16_MAX;
    }
}
uint32_t MacWSKeySymForHIDUsage(NSInteger usage, NSString *characters,
                                UIKeyModifierFlags modifiers) {
    switch (usage) {
        case 40: return 0xff0d;
        case 41: return 0xff1b;
        case 42: return 0xff08;
        case 43: return 0xff09;
        case 57: return 0xffe5;
        case 58 ... 69: return 0xffbeu + (uint32_t)(usage - 58);
        case 74: return 0xff50;
        case 75: return 0xff55;
        case 76: return 0xffff;
        case 77: return 0xff57;
        case 78: return 0xff56;
        case 79: return 0xff53;
        case 80: return 0xff51;
        case 81: return 0xff54;
        case 82: return 0xff52;
        case 224: return 0xffe3;
        case 225: return 0xffe1;
        case 226: return 0xffe9;
        case 227: return 0xffe7;
        case 228: return 0xffe4;
        case 229: return 0xffe2;
        case 230: return 0xffea;
        case 231: return 0xffe8;
    }
    if (characters.length == 0) {
        BOOL shift = (modifiers & UIKeyModifierShift) != 0;
        BOOL caps = (modifiers & UIKeyModifierAlphaShift) != 0;
        if (usage >= 4 && usage <= 29) {
            uint32_t scalar = 'a' + (uint32_t)(usage - 4);
            return shift ^ caps ? scalar - ('a' - 'A') : scalar;
        }
        if (usage >= 30 && usage <= 39) {
            static const char ordinary[] = "1234567890";
            static const char shifted[] = "!@#$%^&*()";
            return (uint32_t)(shift ? shifted[usage - 30]
                                    : ordinary[usage - 30]);
        }
        switch (usage) {
            case 44: return ' ';
            case 45: return shift ? '_' : '-';
            case 46: return shift ? '+' : '=';
            case 47: return shift ? '{' : '[';
            case 48: return shift ? '}' : ']';
            case 49: return shift ? '|' : '\\';
            case 51: return shift ? ':' : ';';
            case 52: return shift ? '"' : '\'';
            case 53: return shift ? '~' : '`';
            case 54: return shift ? '<' : ',';
            case 55: return shift ? '>' : '.';
            case 56: return shift ? '?' : '/';
            default: return 0;
        }
    }
    __block uint32_t scalar = 0;
    [characters enumerateSubstringsInRange:NSMakeRange(0, characters.length)
        options:NSStringEnumerationByComposedCharacterSequences
        usingBlock:^(NSString *substring, NSRange substringRange,
                     NSRange enclosingRange, BOOL *stop) {
            (void)substringRange;
            (void)enclosingRange;
            NSData *data = [substring
                dataUsingEncoding:NSUTF32LittleEndianStringEncoding];
            if (data.length >= sizeof(scalar))
                memcpy(&scalar, data.bytes, sizeof(scalar));
            *stop = YES;
        }];
    return scalar;
}

NSInteger MacWSHIDUsageForASCII(uint32_t scalar) {
    uint32_t lower = scalar >= 'A' && scalar <= 'Z'
        ? scalar + ('a' - 'A') : scalar;
    if (lower >= 'a' && lower <= 'z') return 4 + (lower - 'a');
    if (lower >= '1' && lower <= '9') return 30 + (lower - '1');
    if (lower == '0') return 39;
    switch (lower) {
        case '\n': case '\r': return 40;
        case 0x1b: return 41;
        case '\b': return 42;
        case '\t': return 43;
        case ' ': return 44;
        case '-': case '_': return 45;
        case '=': case '+': return 46;
        case '[': case '{': return 47;
        case ']': case '}': return 48;
        case '\\': case '|': return 49;
        case ';': case ':': return 51;
        case '\'': case '"': return 52;
        case '`': case '~': return 53;
        case ',': case '<': return 54;
        case '.': case '>': return 55;
        case '/': case '?': return 56;
        default: return -1;
    }
}
