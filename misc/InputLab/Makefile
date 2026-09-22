OUTPUT := build/MacWSInputLab
MACBOOK_MOTION := build/macbook_input_motion
SOURCE := main.m

.PHONY: all clean

all: $(OUTPUT) $(MACBOOK_MOTION)

# InputLab is a macOS-chroot test executable, not an iOS/Theos target.  Using
# Theos here puts its iOS IOKit module headers ahead of the macOS SDK and makes
# Cocoa's transitive imports fail.  Build against the selected macOS SDK
# directly so the test environment has exactly the ABI of the applications it
# measures.
$(OUTPUT): $(SOURCE)
	mkdir -p build
	xcrun --sdk macosx clang -arch arm64 -mmacosx-version-min=13.0 \
		-O2 -fobjc-arc -framework Cocoa -framework QuartzCore \
		$(SOURCE) -o $(OUTPUT)

$(MACBOOK_MOTION): ../macbook_input_motion.m
	mkdir -p build
	xcrun --sdk macosx clang -arch arm64 -mmacosx-version-min=13.0 \
		-O2 -fobjc-arc -framework AppKit -framework Foundation \
		-framework ApplicationServices ../macbook_input_motion.m -o $@

clean:
	rm -f $(OUTPUT) $(MACBOOK_MOTION)
