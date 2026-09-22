#ifndef MACWS_DISPLAY_GEOMETRY_H
#define MACWS_DISPLAY_GEOMETRY_H

#include <math.h>
#include <stdbool.h>
#include <stddef.h>

// SkyLight exposes desktop/window bounds in logical points while its window
// capture IOSurfaces and layer destinations use backing pixels. Derive both
// canvas axes from the same logical-bounds × backing-scale invariant.
static inline bool MacWSPhysicalDisplayExtent(double logicalWidth,
                                               double logicalHeight,
                                               double backingScale,
                                               size_t maximumDimension,
                                               size_t *widthOut,
                                               size_t *heightOut) {
    if (!widthOut || !heightOut || !isfinite(logicalWidth) ||
        !isfinite(logicalHeight) || !isfinite(backingScale) ||
        logicalWidth <= 0.0 || logicalHeight <= 0.0 ||
        backingScale < 0.5 || backingScale > 8.0 ||
        maximumDimension == 0) return false;
    double physicalWidth = round(logicalWidth * backingScale);
    double physicalHeight = round(logicalHeight * backingScale);
    if (!isfinite(physicalWidth) || !isfinite(physicalHeight) ||
        physicalWidth < 1.0 || physicalHeight < 1.0 ||
        physicalWidth > (double)maximumDimension ||
        physicalHeight > (double)maximumDimension) return false;
    *widthOut = (size_t)physicalWidth;
    *heightOut = (size_t)physicalHeight;
    return true;
}

// A compositor-owned layer whose logical bounds cover the complete display
// still owns the complete physical canvas even when its IOSurface is rendered
// at a lower material/effect resolution.  Mission Control is the concrete
// witness: Dock keeps the 1194x834 logical desktop bounds while temporarily
// publishing a 1194x834 wallpaper surface on a 2388x1668 Retina canvas.  The
// surface dimensions describe sampling resolution, not destination geometry.
static inline bool MacWSLayerCoversLogicalDisplay(
        double layerX, double layerY, double layerWidth, double layerHeight,
        double displayX, double displayY, double displayWidth,
        double displayHeight) {
    if (!isfinite(layerX) || !isfinite(layerY) ||
        !isfinite(layerWidth) || !isfinite(layerHeight) ||
        !isfinite(displayX) || !isfinite(displayY) ||
        !isfinite(displayWidth) || !isfinite(displayHeight) ||
        layerWidth <= 0.0 || layerHeight <= 0.0 ||
        displayWidth <= 0.0 || displayHeight <= 0.0) return false;
    const double tolerance = 0.5;
    return layerX <= displayX + tolerance &&
        layerY <= displayY + tolerance &&
        layerX + layerWidth >= displayX + displayWidth - tolerance &&
        layerY + layerHeight >= displayY + displayHeight - tolerance;
}

#endif
