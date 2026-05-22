---
name: surge-middleware-stack
description: Add production-shaped middleware stack to app/services/backend — panic recovery, X-Request-ID injection, structured request logging, and Prometheus /metrics endpoint. Use when adding middleware chain to stdlib net/http service or wiring promhttp. Covers internal/middleware package creation and main.go integration.
---

# surge-middleware-stack

Adds Recover → RequestID → Logger middleware chain plus a `/metrics` endpoint. **Requires Steps 1–3 to be done first** (config, v2 tracer, slog).

## New dependencies

```bash
cd app/services/backend
go get github.com/prometheus/client_golang@v1.23.2
# google/uuid is already indirect — promote to direct usage
```

## Files to create

### internal/middleware/chain.go
```go
package middleware

import "net/http"

func Chain(h http.Handler, mw ...func(http.Handler) http.Handler) http.Handler {
    for i := len(mw) - 1; i >= 0; i-- {
        h = mw[i](h)
    }
    return h
}
```

### internal/middleware/recover.go
```go
package middleware

import (
    "log/slog"
    "net/http"
    "runtime"
)

func Recover(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        defer func() {
            if rec := recover(); rec != nil {
                buf := make([]byte, 64<<10)
                n := runtime.Stack(buf, false)
                slog.ErrorContext(r.Context(), "panic recovered",
                    "panic", rec,
                    "stack", string(buf[:n]),
                )
                http.Error(w, http.StatusText(http.StatusInternalServerError), http.StatusInternalServerError)
            }
        }()
        next.ServeHTTP(w, r)
    })
}
```

### internal/middleware/requestid.go
```go
package middleware

import (
    "context"
    "net/http"

    "github.com/google/uuid"
)

type contextKey string

const RequestIDKey contextKey = "request_id"

func RequestID(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        id := r.Header.Get("X-Request-ID")
        if id == "" {
            id = uuid.New().String()
        }
        ctx := context.WithValue(r.Context(), RequestIDKey, id)
        w.Header().Set("X-Request-ID", id)
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}
```

### internal/middleware/logger.go
```go
package middleware

import (
    "log/slog"
    "net/http"
    "time"
)

type statusRecorder struct {
    http.ResponseWriter
    status int
}

func (r *statusRecorder) WriteHeader(code int) {
    r.status = code
    r.ResponseWriter.WriteHeader(code)
}

func Logger(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if r.URL.Path == "/metrics" {
            next.ServeHTTP(w, r)
            return
        }
        start := time.Now()
        rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
        next.ServeHTTP(rec, r)
        slog.InfoContext(r.Context(), "request",
            "method", r.Method,
            "path", r.URL.Path,
            "status", rec.status,
            "duration_ms", time.Since(start).Milliseconds(),
            "request_id", r.Context().Value(RequestIDKey),
        )
    })
}
```

### internal/handler/metrics.go
```go
package handler

import (
    "net/http"

    "github.com/prometheus/client_golang/prometheus/promhttp"
)

func Metrics(w http.ResponseWriter, r *http.Request) {
    promhttp.Handler().ServeHTTP(w, r)
}
```

## Modify cmd/server/main.go

Replace the `mux` setup:
```go
innerMux := ddhttp.NewServeMux()
innerMux.Handle("GET /healthz", &handler.HealthHandler{Env: cfg.DDEnv, Version: cfg.DDVersion})
innerMux.HandleFunc("GET /metrics", handler.Metrics)

srv := &http.Server{
    Addr: ":" + cfg.Port,
    Handler: middleware.Chain(innerMux,
        middleware.Recover,
        middleware.RequestID,
        middleware.Logger,
    ),
}
```

## Verify

```bash
cd app/services/backend && make test

# Manual checks:
curl -v localhost:8080/healthz 2>&1 | grep X-Request-ID  # should be present
curl -s localhost:8080/metrics | grep go_goroutines       # Prometheus metrics
```
