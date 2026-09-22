# MacWSHost test support

This directory contains explicit regression/profile entry points. Test code
must run only after a `macwshost://performance-*` action or a Control Center
test action. Production streaming, input routing and presentation must not
depend on test fixtures or diagnostic flags.

Production telemetry is implemented by `MacWSPerformanceMonitor`. Synthetic
gesture scheduling is implemented by `MacWSPerformanceGestureScenario`; the
view supplies small emit callbacks so every scenario still crosses the exact
production input boundary. New synthetic scenarios belong here rather than in
transport, renderer or application-launch modules.

`MacWSCatalystDrawableProbe` is likewise reachable only through the explicit
`macwshost://test-catalyst-drawable?pid=...` URL. It is the sole owner of the
read-only IOSurface scan and optional CPU copy used to prove third-party frame
contents. The normal Catalyst receiver/compositor never calls it, never scans
pixels, and never performs a readback.
