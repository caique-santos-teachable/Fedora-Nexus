package client

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestCallTimeoutsWhenServerStalls(t *testing.T) {
	t.Setenv("FEDORA_NEXUS_HTTP_TIMEOUT_MS", "100")

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(500 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()

	start := time.Now()
	result := Call(srv.URL, "list_repos", map[string]any{})
	elapsed := time.Since(start)

	if result.Err == nil {
		t.Fatalf("expected timeout error, got nil")
	}
	if elapsed > 400*time.Millisecond {
		t.Fatalf("expected timeout before 400ms, got %s", elapsed)
	}
}

func TestIsHealthyTimeoutsWhenServerStalls(t *testing.T) {
	t.Setenv("FEDORA_NEXUS_HTTP_TIMEOUT_MS", "100")

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(500 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	start := time.Now()
	healthy := IsHealthy(srv.URL)
	elapsed := time.Since(start)

	if healthy {
		t.Fatalf("expected unhealthy result on timeout")
	}
	if elapsed > 400*time.Millisecond {
		t.Fatalf("expected timeout before 400ms, got %s", elapsed)
	}
}

