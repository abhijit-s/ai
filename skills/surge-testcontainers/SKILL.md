---
name: surge-testcontainers
description: Add testcontainers-based integration test for app/services/backend that boots the service as a Docker container and asserts /healthz returns 200 with correct JSON. Use when establishing integration test patterns for Go services. Covers FromDockerfile container setup, wait strategies, and build tag gating.
---

# surge-testcontainers

Establishes the integration test pattern for `app/services/backend` using testcontainers-go. Can be implemented independently of Steps 1–4.

## New dependency

```bash
cd app/services/backend
go get github.com/testcontainers/testcontainers-go@v0.40.0
go mod tidy
```

## File to create: internal/integration/server_test.go

```go
//go:build integration

package integration_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
)

func TestServerHealthz(t *testing.T) {
	ctx := context.Background()

	req := testcontainers.ContainerRequest{
		FromDockerfile: testcontainers.FromDockerfile{
			Context:    "../../..",
			Dockerfile: "Dockerfile",
		},
		ExposedPorts: []string{"8080/tcp"},
		Env: map[string]string{
			"PORT":                 "8080",
			"LOG_LEVEL":            "info",
			"DD_ENV":               "test",
			"DD_SERVICE":           "backend",
			"DD_VERSION":           "0.0.0",
			"DD_AGENT_HOST":        "localhost",
			"DD_TRACE_AGENT_PORT":  "8126",
			"FEATURE_FLAG_DATADOG": "false",
		},
		WaitingFor: wait.ForHTTP("/healthz").
			WithPort("8080/tcp").
			WithStatusCodeMatcher(func(status int) bool { return status == 200 }).
			WithStartupTimeout(120 * time.Second),
	}

	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: req,
		Started:          true,
	})
	if err != nil {
		t.Fatalf("start container: %v", err)
	}
	t.Cleanup(func() { _ = container.Terminate(ctx) })

	host, err := container.Host(ctx)
	if err != nil {
		t.Fatalf("get host: %v", err)
	}
	port, err := container.MappedPort(ctx, "8080")
	if err != nil {
		t.Fatalf("get port: %v", err)
	}

	resp, err := http.Get(fmt.Sprintf("http://%s:%s/healthz", host, port.Port()))
	if err != nil {
		t.Fatalf("GET /healthz: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}

	var body map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode body: %v", err)
	}
	if body["status"] != "ok" {
		t.Errorf("expected status=ok, got %q", body["status"])
	}
	if body["service"] != "backend" {
		t.Errorf("expected service=backend, got %q", body["service"])
	}
	if body["env"] != "test" {
		t.Errorf("expected env=test, got %q", body["env"])
	}
}
```

## Run the test

```bash
cd app/services/backend
make test-integration
# Requires Docker running locally
```

## Note on Dockerfile context path

The `Context: "../../.."` path resolves from `internal/integration/` to the service root `app/services/backend/`. Verify the Dockerfile is at the service root (it is: `app/services/backend/Dockerfile`).

If testcontainers-go v0.40.0 has API changes, check the release notes for `GenericContainer` → `Run` migration.
