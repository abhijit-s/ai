---
name: surge-dd-trace-v2
description: Upgrade dd-trace-go from v1 to v2 and gate tracer/profiler behind FEATURE_FLAG_DATADOG for app/services/backend. Use when migrating Datadog tracer imports, adding a feature flag guard to observability init, or running tracer.Flush() on graceful shutdown. Covers go.mod promotion, import rewrite, v2 error-returning API.
---

# surge-dd-trace-v2

Promotes dd-trace-go v2 from indirect → direct, removes v1, and gates the tracer/profiler behind `cfg.FeatureFlagDatadog`. **Requires Step 1 (typed config) to be done first.**

## go.mod changes

Remove:
```
gopkg.in/DataDog/dd-trace-go.v1 v1.74.8
```

Promote (move from indirect block to direct block):
```
github.com/DataDog/dd-trace-go/v2 v2.3.0
github.com/DataDog/dd-trace-go/contrib/net/http/v2 v2.3.0
```

Then run: `go mod tidy`

## Import rewrite in cmd/server/main.go

```go
// Before (v1):
ddhttp "gopkg.in/DataDog/dd-trace-go.v1/contrib/net/http"
"gopkg.in/DataDog/dd-trace-go.v1/ddtrace/tracer"
"gopkg.in/DataDog/dd-trace-go.v1/profiler"

// After (v2):
ddhttp "github.com/DataDog/dd-trace-go/contrib/net/http/v2"
"github.com/DataDog/dd-trace-go/v2/ddtrace/tracer"
"github.com/DataDog/dd-trace-go/v2/profiler"
```

## Tracer/profiler init — gated + v2 API

In v2, `tracer.Start()` returns `error`. Wrap in feature flag:

```go
if cfg.FeatureFlagDatadog {
    if err := tracer.Start(
        tracer.WithEnv(cfg.DDEnv),
        tracer.WithService(cfg.DDService),
        tracer.WithServiceVersion(cfg.DDVersion),
        tracer.WithAgentAddr(cfg.DDAgentHost + ":" + cfg.DDTraceAgentPort),
        tracer.WithAnalytics(true),
    ); err != nil {
        slog.Error("tracer start failed", "error", err)
    }
    defer tracer.Stop()

    if err := profiler.Start(
        profiler.WithService(cfg.DDService),
        profiler.WithEnv(cfg.DDEnv),
        profiler.WithVersion(cfg.DDVersion),
        profiler.WithProfileTypes(profiler.CPUProfile, profiler.HeapProfile),
    ); err != nil {
        slog.Error("profiler start failed", "error", err)
    }
    defer profiler.Stop()
}
```

## tracer.Flush() in shutdown sequence

Add inside the feature flag check, after `srv.Shutdown()` returns and before `slog.Info("server stopped")`:

```go
if cfg.FeatureFlagDatadog {
    tracer.Flush()
}
```

## Update internal/testutil/tracer.go

Update import paths from v1 to v2 (same pattern as main.go).

## Verify

```bash
cd app/services/backend
FEATURE_FLAG_DATADOG=false DD_ENV=test DD_SERVICE=backend DD_VERSION=0.0.0 DD_AGENT_HOST=localhost go run ./cmd/server &
# Confirm server starts, no DD init logs
make test
```
