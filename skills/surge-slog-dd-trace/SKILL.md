---
name: surge-slog-dd-trace
description: Wire stdlib slog with Datadog trace ID injection for app/services/backend. Use when adding structured JSON logging with automatic dd.trace_id/dd.span_id correlation, or when activating LOG_LEVEL config. Covers dd-trace-go v2 slog contrib handler setup.
---

# surge-slog-dd-trace

Sets the default slog handler to emit JSON with automatic Datadog trace/span ID injection. **Requires Steps 1 (config) and 2 (dd-trace-go v2) to be done first.**

## What to implement in cmd/server/main.go

After config load, before any other setup:

```go
import (
    "log/slog"
    "os"
    "strings"
    ddlogslog "github.com/DataDog/dd-trace-go/contrib/log/slog/v2"
)

// Parse log level
var level slog.Level
switch strings.ToLower(cfg.LogLevel) {
case "debug":
    level = slog.LevelDebug
case "warn":
    level = slog.LevelWarn
case "error":
    level = slog.LevelError
default:
    level = slog.LevelInfo
}

opts := &slog.HandlerOptions{Level: level}

if cfg.FeatureFlagDatadog {
    // DD handler injects dd.trace_id + dd.span_id when a span is active in context
    slog.SetDefault(slog.New(ddlogslog.NewJSONHandler(os.Stdout, opts)))
} else {
    slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, opts)))
}
```

## Module path verification

Before adding the import, verify the module exists:
```bash
cd app/services/backend
go list -m github.com/DataDog/dd-trace-go/contrib/log/slog/v2 2>/dev/null || \
  go get github.com/DataDog/dd-trace-go/contrib/log/slog/v2
```

If the module path doesn't exist as a separate module, check if it's a sub-package of `github.com/DataDog/dd-trace-go/v2`. Fallback: use `slog.NewJSONHandler` for both paths and manually add `dd.trace_id` via `tracer.SpanFromContext(ctx)` in the request logger middleware.

## Verify

```bash
cd app/services/backend
LOG_LEVEL=info DD_ENV=test DD_SERVICE=backend DD_VERSION=0.0.0 DD_AGENT_HOST=localhost \
  go run ./cmd/server &
curl -s localhost:8080/healthz
# Logs should be JSON: {"time":"...","level":"INFO","msg":"request",...}

LOG_LEVEL=debug ... go run ./cmd/server
# Debug logs should appear

LOG_LEVEL=warn ... go run ./cmd/server  
# Info-level request logs should be suppressed
```
