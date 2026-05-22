---
name: surge-typed-config
description: Implement typed config struct with startup validation for app/services/backend. Use when wiring env vars into a Go config struct, replacing os.Getenv calls, or adding fail-fast startup validation. Covers config loading, required field validation, health handler refactor.
---

# surge-typed-config

Implements a typed `Config` struct with `Load()` for `app/services/backend`, replacing scattered `os.Getenv` calls with a single validated struct at startup.

## What to implement

**Create** `app/services/backend/internal/config/config.go`:

```go
package config

import (
    "fmt"
    "os"
    "strings"
)

type Config struct {
    Port               string
    LogLevel           string
    DDEnv              string
    DDService          string
    DDVersion          string
    DDAgentHost        string
    DDTraceAgentPort   string
    FeatureFlagDatadog bool
}

func Load() (Config, error) {
    cfg := Config{
        Port:             envOr("PORT", "8080"),
        LogLevel:         envOr("LOG_LEVEL", "info"),
        DDEnv:            os.Getenv("DD_ENV"),
        DDService:        os.Getenv("DD_SERVICE"),
        DDVersion:        os.Getenv("DD_VERSION"),
        DDAgentHost:      os.Getenv("DD_AGENT_HOST"),
        DDTraceAgentPort: envOr("DD_TRACE_AGENT_PORT", "8126"),
        FeatureFlagDatadog: strings.EqualFold(os.Getenv("FEATURE_FLAG_DATADOG"), "true"),
    }

    var missing []string
    for _, pair := range []struct{ key, val string }{
        {"DD_ENV", cfg.DDEnv},
        {"DD_SERVICE", cfg.DDService},
        {"DD_VERSION", cfg.DDVersion},
        {"DD_AGENT_HOST", cfg.DDAgentHost},
    } {
        if pair.val == "" {
            missing = append(missing, pair.key)
        }
    }
    if len(missing) > 0 {
        return Config{}, fmt.Errorf("missing required env vars: %v", missing)
    }
    return cfg, nil
}

func envOr(key, fallback string) string {
    if v := os.Getenv(key); v != "" {
        return v
    }
    return fallback
}
```

**Modify** `internal/handler/health.go` — change to struct handler:

```go
type HealthHandler struct {
    Env     string
    Version string
}

func (h *HealthHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(map[string]string{
        "status":  "ok",
        "service": "backend",
        "env":     h.Env,
        "version": h.Version,
    })
}
```

**Modify** `internal/handler/health_test.go` — remove `t.Setenv`; construct handler directly:
```go
h := &handler.HealthHandler{Env: "test", Version: "0.0.0"}
h.ServeHTTP(rec, req)
```

**Modify** `cmd/server/main.go` — call `config.Load()` first; use `cfg.*` everywhere; remove `getEnvOr`.

**Modify** `k8s/base/backend/configmap.yaml` — add `FEATURE_FLAG_DATADOG: "false"`.

**Modify** `k8s/overlays/staging/kustomization.yaml` and `k8s/overlays/prod/kustomization.yaml` — add JSON patch:
```yaml
- target:
    kind: ConfigMap
    name: backend-config
  patch: |-
    - op: add
      path: /data/FEATURE_FLAG_DATADOG
      value: "true"
```

## Verify

```bash
cd app/services/backend && make test
```

Run without DD_ENV set → process exits with clear "missing required env vars" error.
