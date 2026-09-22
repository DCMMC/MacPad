# Unshipped WebKit launch-transport experiment

This subproject is deliberately **not** in the root aggregate build or default
libmachook routing. It is not a Safari fix. An explicit one-shot September 13
experiment preserved the native per-instance XPC launch context and started
macOS Safari's SandboxBroker and WebContent executables. WebContent then hit
JavaScriptCore's fatal Gigacage reservation failure. No allocator checks or
security features were disabled; default routing was restored after diagnosis.

See `docs/evidence/settings-system-apps-safari-20260913.md` for exact evidence
and remaining work before this transport can become a product dependency.
